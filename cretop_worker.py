from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path


PEER_HEADINGS = [
    r"동종\s*업계", r"동종\s*업종", r"업종\s*평균", r"업계\s*평균",
    r"산업\s*평균", r"산업분석", r"비교분석", r"기업순위",
]


def normalize_business_no(value):
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return str(value or "").strip()


def regex_first(pattern, text, flags=0):
    match = re.search(pattern, text or "", flags)
    return match.group(1).strip() if match else ""


def parse_latest_number_from_line(label, block):
    escaped = re.escape(label)
    patterns = [
        rf"(?m)^[ \t]*{escaped}(?:\([^\n]*?\))?[ \t]+([^\n]*)",
        rf"(?<![가-힣]){escaped}(?:\([^\n]*?\))?[ \t]+([^\n]*)",
    ]
    for pattern in patterns:
        match = re.search(pattern, block or "")
        if not match:
            continue
        # A blank financial row must never borrow a date/amount from the next row.
        raw_values = match.group(1).strip()
        if not re.fullmatch(r"(?:-?[ \t]*\d[\d,]*(?:\.\d+)?|[-–—])[ \t]*(?:(?:-?[ \t]*\d[\d,]*(?:\.\d+)?|[-–—])[ \t]*)*", raw_values):
            continue
        values = re.findall(r"-?[ \t]*[0-9][0-9,]*(?:\.[0-9]+)?|[-–—]", raw_values)
        if not values:
            continue
        if values[-1] in {"-", "–", "—"}:
            return None
        raw = values[-1].replace(" ", "").replace(",", "")
        try:
            return float(raw)
        except ValueError:
            continue
    return None


def extract_block(text, start_heading, end_headings):
    start = re.search(start_heading, text or "")
    if not start:
        return ""
    tail = text[start.end():]
    ends = []
    for heading in end_headings:
        match = re.search(heading, tail)
        if match:
            ends.append(match.start())
    end = min(ends) if ends else min(len(tail), 12000)
    return tail[:end]


def _find_amount_anywhere(labels, text, unit_multiplier):
    """문서 전체에서 후보 계정명의 마지막 숫자를 찾아 원 단위로 환산한다."""
    for label in labels:
        value = parse_latest_number_from_line(label, text)
        if value is not None:
            return int(round(value * unit_multiplier))
    return ""


def latest_financial_amount(label, text):
    """
    요약표 → 상세표 → 문서 전체 후보계정 순으로 최신 값을 탐색한다.
    업체별 크레탑 표 제목이나 계정명이 달라도 대응한다.
    """
    income_aliases = {
        "매출액": ["매출액", "매출", "영업수익", "수익"],
        "영업이익": ["영업이익", "영업이익(손실)", "영업손익"],
        "당기순이익": [
            "당기순이익",
            "당기순이익(순손실)",
            "당기순손익",
            "당기순손실",
            "법인세차감후순이익",
            "법인세비용차감후순이익",
            "순이익",
        ],
    }
    balance_aliases = {
        "자산총계": ["자산총계", "자산"],
        "부채총계": ["부채총계", "부채"],
        "자본총계": ["자본총계", "자본"],
    }

    if label in income_aliases:
        summary = extract_block(
            text,
            r"요약\s*손익계산서",
            [r"요약\s*현금흐름", r"요약\s*재무비율", r"연혁", r"재무상태표"] + PEER_HEADINGS,
        )
        for alias in income_aliases[label]:
            value = parse_latest_number_from_line(alias, summary)
            if value is not None:
                return int(round(value * 1_000_000))

        detail = extract_block(
            text,
            r"(?:상세\s*)?손익계산서\s+단위\s*:?\s*천원",
            [r"현금흐름표", r"자본변동표", r"이익잉여금처분계산서", r"재무비율"] + PEER_HEADINGS,
        )
        for alias in income_aliases[label]:
            value = parse_latest_number_from_line(alias, detail)
            if value is not None:
                return int(round(value * 1_000))

        # CRETOP also includes peers' sales tables. An explicit missing company
        # statement must not fall through to those unrelated industry amounts.
        if re.search(r"조회된\s*자료가\s*없습니다|해당\s*자료가\s*없습니다", summary):
            return ""

        # Alternate statement names are supported, but never search all amounts
        # in the report: industry/peer tables are not this company's financials.
        for heading in re.finditer(r"(?:포괄\s*)?손익(?:계산서|현황|내역)", text):
            tail = text[heading.end():heading.end() + 12000]
            section = re.split(
                r"(?:요약\s*)?(?:현금흐름|재무비율|재무상태표)|자본변동|연혁|"
                r"동종\s*업계|동종\s*업종|업종\s*평균|업계\s*평균|산업\s*평균|"
                r"산업분석|비교분석|기업순위",
                tail, maxsplit=1,
            )[0]
            unit = re.search(r"단위\s*[:：]?\s*(백만원|천원)", section[:160])
            if not unit:
                continue
            result = _find_amount_anywhere(
                income_aliases[label], section[unit.end():],
                1_000_000 if unit.group(1) == "백만원" else 1_000,
            )
            if result != "":
                return result

    elif label in balance_aliases:
        summary = extract_block(
            text,
            r"요약\s*재무상태표",
            [r"요약\s*손익계산서", r"요약\s*현금흐름", r"요약\s*재무비율"] + PEER_HEADINGS,
        )
        for alias in balance_aliases[label]:
            value = parse_latest_number_from_line(alias, summary)
            if value is not None:
                return int(round(value * 1_000_000))

        detail = extract_block(
            text,
            r"재무상태표\s+단위\s*:?\s*천원",
            [r"손익계산서", r"현금흐름표"] + PEER_HEADINGS,
        )
        for alias in balance_aliases[label]:
            value = parse_latest_number_from_line(alias, detail)
            if value is not None:
                return int(round(value * 1_000))

    return ""


CERTIFICATION_LABELS = {
    "연구개발전담부서": "연구개발전담부서", "기업부설연구소": "기업부설연구소",
    "부설연구소": "기업부설연구소", "이노비즈": "이노비즈", "메인비즈": "메인비즈",
    "INNO-BIZ": "이노비즈", "MAIN-BIZ": "메인비즈", "벤처": "벤처",
}
CERTIFICATION_STATUS = r"미인증|미보유|해당없음|없음|인증|보유|유|무|Y|N"


def _certification_text_values(text):
    """Read explicit pairs or complete header/value rows, never shift columns."""
    label_pattern = "|".join(re.escape(key) for key in CERTIFICATION_LABELS)
    token_pattern = rf"(?P<label>{label_pattern})|(?<![가-힣A-Za-z])(?P<status>{CERTIFICATION_STATUS})(?![가-힣A-Za-z])"
    tokens = list(re.finditer(token_pattern, text, re.I))
    values = {}
    index = 0
    while index < len(tokens):
        if not tokens[index].group("label"):
            index += 1
            continue
        headers = []
        while index < len(tokens) and tokens[index].group("label"):
            headers.append(tokens[index])
            index += 1
        statuses = []
        while index < len(tokens) and tokens[index].group("status"):
            statuses.append(tokens[index])
            index += 1
        # A missing/unreadable heading or value invalidates the entire group.
        if len(headers) != len(statuses):
            continue
        between = text[headers[-1].end():statuses[0].start()]
        # Only whitespace and table decoration may separate header and value.
        if re.search(r"[가-힣A-Za-z0-9]", between):
            continue
        if len(headers) > 1 and any(
            re.search(r"[가-힣A-Za-z0-9]", text[left.end():right.start()])
            for left, right in zip(headers, headers[1:])
        ):
            continue
        if any(re.search(r"[가-힣A-Za-z0-9]", text[left.end():right.start()])
               for left, right in zip(statuses, statuses[1:])):
            continue
        for header, status in zip(headers, statuses):
            label = header.group("label")
            target = CERTIFICATION_LABELS.get(label, CERTIFICATION_LABELS.get(label.upper()))
            value = "Y" if status.group("status").upper() in {"인증", "보유", "유", "Y"} else "N"
            # Conflicting explicit values are unconfirmed, not silently overwritten.
            values.setdefault(target, set()).add(value)
    return {key: next(iter(found)) if len(found) == 1 else "" for key, found in values.items()}


def _certification_block(text):
    heading = re.search(r"(?m)^[ \t]*기업[ \t]*인증[ \t]*(?:\n|[:：])", text or "")
    block = (text or "")[heading.end():] if heading else (text or "")
    return re.split(r"산업재산권|주요\s*주주|관계회사|주요\s*구매처|주요\s*판매처", block)[0]


def extract_certifications(text):
    result = {
        "벤처": "",
        "이노비즈": "",
        "메인비즈": "",
        "연구개발전담부서": "",
        "기업부설연구소": "",
        "특허보유": "",
        "상표": "",
    }

    # The overview panel prints all labels first, then all statuses beneath them.
    # Keep shareholder/customer/peer sections out of the certification evidence.
    result.update(_certification_text_values(_certification_block(text)))

    # Missing/OCR-unreadable certification sections remain unknown.
    property_block = extract_block(
        text,
        r"산업재산권",
        [r"주요\s*주주", r"관계회사", r"주요\s*구매처"],
    )
    if property_block:
        patent_line = re.search(r"특허\s+실용신안\s+디자인\s+상표권\s+(.+)", property_block)
        if patent_line:
            values = patent_line.group(1)
            tokens = re.findall(r"[0-9,]+|[YyNn]|보유|미보유|(?<!\S)-(?!\S)", values)
            if len(tokens) == 4:
                for target, token in [("특허보유", tokens[0]), ("상표", tokens[3])]:
                    if token not in {"-"}:
                        result[target] = "N" if token in {"0", "N", "n", "미보유"} else "Y"
        else:
            if re.search(r"특허.{0,20}(?:[1-9][0-9]*|보유|Y)", property_block, re.S):
                result["특허보유"] = "Y"
            if re.search(r"상표(?:권)?.{0,20}(?:[1-9][0-9]*|보유|Y)", property_block, re.S):
                result["상표"] = "Y"
    return result



def _extract_row_values(block, aliases, years):
    """요약 재무표에서 계정별 연도 값을 추출한다."""
    if not block or not years:
        return {}

    for alias in aliases:
        pattern = rf"(?m)^[ \t]*{re.escape(alias)}(?:\([^\n]*?\))?[ \t]+([^\n]+)$"
        match = re.search(pattern, block)
        if not match:
            continue

        tokens = re.findall(r"-?\d[\d,]*(?:\.\d+)?|(?<!\S)-(?!\S)", match.group(1))
        if len(tokens) < len(years):
            continue

        tokens = tokens[-len(years):]
        result = {}
        for year, token in zip(years, tokens):
            if token.strip() == "-":
                result[str(year)] = None
                continue
            try:
                result[str(year)] = int(round(float(token.replace(",", "")) * 1_000_000))
            except ValueError:
                result[str(year)] = None
        return result

    return {}


def extract_annual_financial_history(text):
    """
    크레탑 요약 재무상태표·손익계산서의 최근 연도별 값을 반환한다.
    단위는 원이다.
    """
    income_block = extract_block(
        text,
        r"요약\s*손익계산서",
        [r"요약\s*현금흐름", r"요약\s*재무비율", r"연혁", r"재무상태표"] + PEER_HEADINGS,
    )
    balance_block = extract_block(
        text,
        r"요약\s*재무상태표",
        [r"요약\s*손익계산서", r"요약\s*현금흐름", r"요약\s*재무비율"] + PEER_HEADINGS,
    )

    income_years = []
    balance_years = []

    for value in re.findall(r"\b(20\d{2})\b", income_block):
        if value not in income_years:
            income_years.append(value)
    for value in re.findall(r"\b(20\d{2})\b", balance_block):
        if value not in balance_years:
            balance_years.append(value)

    income_years = income_years[-3:]
    balance_years = balance_years[-3:]

    rows = {
        "매출액": _extract_row_values(
            income_block,
            ["매출액", "매출", "영업수익"],
            income_years,
        ),
        "당기순이익": _extract_row_values(
            income_block,
            [
                "당기순이익",
                "당기순이익(순손실)",
                "당기순손익",
                "법인세차감후순이익",
                "법인세비용차감후순이익",
                "순이익",
            ],
            income_years,
        ),
        "자산총계": _extract_row_values(
            balance_block,
            ["자산총계", "자산"],
            balance_years,
        ),
        "부채총계": _extract_row_values(
            balance_block,
            ["부채총계", "부채"],
            balance_years,
        ),
        "자본총계": _extract_row_values(
            balance_block,
            ["자본총계", "자본"],
            balance_years,
        ),
    }

    years = sorted(
        set(income_years + balance_years),
        reverse=True,
    )

    history = []
    for year in years:
        if not any(values.get(year) is not None for values in rows.values()):
            continue
        history.append({
            "연도": int(year),
            "매출액": rows["매출액"].get(year),
            "당기순이익": rows["당기순이익"].get(year),
            "자산총계": rows["자산총계"].get(year),
            "부채총계": rows["부채총계"].get(year),
            "자본총계": rows["자본총계"].get(year),
        })
    return history


IDENTITY_LABELS = (
    "영문기업명", "사업자등록번호", "사업자번호", "법인(주민)번호", "법인등록번호",
    "대표자명", "대표자", "종업원수", "설립형태", "설립년월일", "설립년월",
    "회사설립일", "법인설립일", "창업일", "기업유형", "기업규모", "전화번호",
    "팩스번호", "홈페이지", "이메일", "결산월", "기업공개일자", "사업장 소재지",
    "본사 소재지", "소재지", "주소", "표준산업분류(10차)", "표준산업분류(11차)",
    "주요제품(상품)", "기업명", "회사명", "업체명", "휴폐업정보", "법인등기정보",
)
IDENTITY_TABLE_HEADINGS = (
    "거래비중", "결산년도", "결산연도", "자본금", "자산총계", "매출액", "순이익",
    "주요 주주", "주요 구매처", "주요 판매처", "조회된 자료", "기업신용등급",
)


def normalize_document_text(text):
    text = str(text or "").replace("\x0c", "\n").replace("\r\n", "\n")
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    # Repair spaces in field labels only. Names and addresses keep their spacing.
    for label in sorted(IDENTITY_LABELS, key=len, reverse=True):
        pattern = r"[ \t]*".join(re.escape(char) for char in label if char != " ")
        text = re.sub(pattern, lambda _: label, text)
    return text


def _identity_value(text, *labels, validator=None):
    is_representative = any(label in {"대표자명", "대표자"} for label in labels)
    boundaries = IDENTITY_LABELS + (IDENTITY_TABLE_HEADINGS if is_representative else ())
    stop = "|".join(re.escape(label) for label in boundaries)
    for label in labels:
        pattern = rf"(?<![가-힣A-Za-z]){re.escape(label)}[ \t]*[:：]?[ \t]*(?:\n[ \t]*)?([^\n]*)"
        for match in re.finditer(pattern, text):
            value = re.split(rf"(?:^|[ \t]+)(?:{stop})(?=[ \t:：]|$)", match.group(1), maxsplit=1)[0]
            value = value.strip(" \t|:")
            if is_representative and any(heading.replace(" ", "") in re.sub(r"\s+", "", value) for heading in IDENTITY_TABLE_HEADINGS):
                continue
            if value and value not in {"-", "조회된 자료가 없습니다.", "조회된 자료가 없습니다"} and (validator is None or validator(value)):
                return value
    return ""


def _valid_representative(value):
    # Preserve international names and joint representatives, but not table data.
    name = re.sub(r"\s*(?:외|등)\s*\d+\s*명$", "", value).strip()
    return (2 <= len(name) <= 70 and any(char.isalpha() for char in name)
            and all(char.isalpha() or char in " .·,()-'’" for char in name))


def extract_identity(text):
    text = normalize_document_text(text)
    company_name = _identity_value(text, "기업명", "회사명", "업체명")
    representative = _identity_value(
        text, "대표자명", "대표자",
        validator=_valid_representative,
    )
    business_raw = _identity_value(text, "사업자등록번호", "사업자번호")
    business_no = regex_first(r"(?<!\d)(\d{3}[ \t]*-[ \t]*\d{2}[ \t]*-[ \t]*\d{5}|\d{10})(?!\d)", business_raw)
    if not business_no:
        # Legacy reports sometimes omit the label, but still print the formatted ID.
        business_no = regex_first(r"(?<!\d)(\d{3}-\d{2}-\d{5})(?!\d)", text)
    return {
        "업체명": company_name,
        "대표자명": representative,
        "사업자등록번호": normalize_business_no(business_no),
    }


def has_company_identity(data):
    return bool(data.get("업체명") or re.fullmatch(r"\d{10}", re.sub(r"\D", "", str(data.get("사업자등록번호", "")))))

def parse_document_text(text, *, certification_evidence=None):
    text = normalize_document_text(text)
    data = extract_identity(text)
    corporate_raw = _identity_value(text, "법인(주민)번호", "법인등록번호")
    data["법인등록번호"] = regex_first(r"(?<!\d)(\d{6}[ \t]*-[ \t]*\d{7}|\d{13})(?!\d)", corporate_raw).replace(" ", "")
    data["종업원수"] = regex_first(r"^([0-9,]+)(?:[ \t]*명)?$", _identity_value(text, "종업원수"))

    establishment_patterns = [
        r"설립년월(?:일)?\s*[:：]?\s*([0-9]{4}[-./년]\s*[0-9]{1,2}[-./월]\s*[0-9]{1,2}일?)",
        r"회사설립일\s*[:：]?\s*([0-9]{4}[-./]\s*[0-9]{1,2}[-./]\s*[0-9]{1,2})",
        r"법인설립일\s*[:：]?\s*([0-9]{4}[-./]\s*[0-9]{1,2}[-./]\s*[0-9]{1,2})",
        r"창업일\s*[:：]?\s*([0-9]{4}[-./]\s*[0-9]{1,2}[-./]\s*[0-9]{1,2})",
    ]
    establishment = ""
    for pattern in establishment_patterns:
        establishment = regex_first(pattern, text)
        if establishment:
            break
    establishment_digits = re.findall(r"[0-9]+", establishment)
    if len(establishment_digits) >= 3:
        data["설립일"] = (
            f"{int(establishment_digits[0]):04d}-"
            f"{int(establishment_digits[1]):02d}-"
            f"{int(establishment_digits[2]):02d}"
        )
    else:
        data["설립일"] = establishment
    data["설립년도"] = data["설립일"][:4] if data.get("설립일") else ""

    data["기업유형"] = _identity_value(text, "기업유형")
    if not data["기업유형"]:
        overview = extract_block(text, r"기업개요", [r"기업신용등급", r"요약\s*재무"])
        known_types = set(re.findall(
            r"(?<![가-힣])(일반법인|외감법인|상장법인|비상장법인|개인사업자|개인기업)(?![가-힣])",
            overview,
        ))
        if len(known_types) == 1:
            data["기업유형"] = known_types.pop()
    data["기업규모"] = _identity_value(text, "기업규모")

    address_patterns = [
        r"주소\s*[:：]?\s*(.+?)\s+표준산업분류\(10차\)",
        r"사업장\s*소재지\s*[:：]?\s*(.+?)(?:\n|전화번호|팩스번호|표준산업분류)",
        r"본사\s*소재지\s*[:：]?\s*(.+?)(?:\n|전화번호|팩스번호|표준산업분류)",
        r"소재지\s*[:：]?\s*(.+?)(?:\n|전화번호|팩스번호|표준산업분류)",
    ]
    address = ""
    for pattern in address_patterns:
        address = regex_first(pattern, text, flags=re.S)
        if address:
            break
    if not address:
        address = _identity_value(text, "주소", "사업장 소재지", "본사 소재지", "소재지")

    address = " ".join(address.split())
    # 다음 항목의 텍스트가 주소에 붙는 경우 잘라낸다.
    address = re.split(
        r"\s+(?:표준산업분류|기업유형|기업규모|전화번호|팩스번호|홈페이지)\b",
        address,
        maxsplit=1,
    )[0].strip()
    data["사업장 소재지"] = address

    industry = regex_first(
        r"표준산업분류\(10차\)\s+\([A-Z0-9]+\)\s*(.+?)\s+표준산업분류\(11차\)",
        text,
        re.S,
    )
    if not industry:
        industry = re.sub(r"^\([A-Z0-9]+\)[ \t]*", "", _identity_value(text, "표준산업분류(10차)", "표준산업분류(11차)"))
    data["업종명"] = " ".join(industry.split())

    data["매출액"] = latest_financial_amount("매출액", text)
    data["연매출"] = data["매출액"]
    data["전년도매출"] = data["매출액"]
    data["영업이익"] = latest_financial_amount("영업이익", text)
    data["당기순이익"] = latest_financial_amount("당기순이익", text)
    data["자산총계"] = latest_financial_amount("자산총계", text)
    data["부채총계"] = latest_financial_amount("부채총계", text)
    data["자본총계"] = latest_financial_amount("자본총계", text)

    data.update(extract_certifications(text))
    # A supplemental OCR pass contributes only a verified certification panel,
    # never a replacement of the company/financial text from the primary pass.
    if certification_evidence:
        explicit_values = _certification_text_values(_certification_block(text))
        for key, value in certification_evidence.items():
            if key not in set(CERTIFICATION_LABELS.values()) or value not in {"Y", "N", ""}:
                continue
            original = data.get(key, "")
            conflicted = key in explicit_values and explicit_values[key] == ""
            data[key] = "" if conflicted or (original and value and original != value) else value

    purpose = regex_first(r"사업목적\s+내용\s+(.+?)\s+종합의견", text, re.S)
    if purpose:
        lines = [line.strip() for line in purpose.splitlines() if line.strip()]
        data["주요 사업내용"] = " / ".join(lines[:8])[:500]
    else:
        data["주요 사업내용"] = data.get("업종명", "")

    keywords = [data.get("업종명", "")]
    if data.get("벤처") == "Y":
        keywords.append("벤처")
    if data.get("이노비즈") == "Y":
        keywords.append("이노비즈")
    if data.get("메인비즈") == "Y":
        keywords.append("메인비즈")
    if data.get("기업부설연구소") == "Y" or data.get("연구개발전담부서") == "Y":
        keywords.append("연구소")
    if data.get("특허보유") == "Y":
        keywords.append("특허")
    data["키워드메모"] = " / ".join(item for item in keywords if item)
    data["재무연도별"] = extract_annual_financial_history(text)
    data["PDF추출일시"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


def emit_progress(page, total, message):
    print(json.dumps({
        "type": "progress",
        "page": page,
        "total": total,
        "message": message,
    }, ensure_ascii=False), flush=True)


class CretopExtractionError(RuntimeError):
    """Safe code only; never put document text or customer paths in errors."""


def _remove_blue_table_outlines(image):
    """Remove large blue capsule outlines only from a disposable OCR image."""
    import numpy as np
    from PIL import Image

    pixels = np.array(image)
    channels = pixels.astype(np.int16)
    blue = ((channels[:, :, 2] > 150)
            & (channels[:, :, 2] - channels[:, :, 0] > 40)
            & (channels[:, :, 2] - channels[:, :, 1] > 20))
    height, width = blue.shape
    # Bound the flood-fill work on pages with large coloured backgrounds.
    if int(blue.sum()) > width * height // 5:
        return image
    for start_y, start_x in zip(*np.where(blue)):
        if not blue[start_y, start_x]:
            continue
        stack = [(int(start_y), int(start_x))]
        component = []
        blue[start_y, start_x] = False
        while stack:
            y, x = stack.pop()
            component.append((y, x))
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < height and 0 <= nx < width and blue[ny, nx]:
                    blue[ny, nx] = False
                    stack.append((ny, nx))
        ys, xs = zip(*component)
        box_width, box_height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if (width * .025 < box_width < width * .25
                and height * .007 < box_height < height * .06
                and box_width / box_height > 2
                and len(component) / (box_width * box_height) < .35):
            pixels[np.array(ys), np.array(xs)] = 255
    return Image.fromarray(pixels)


def _ocr_pdf_page(document, index, *, psm=6, timeout=15, remove_table_borders=False):
    import fitz
    import pytesseract
    from PIL import Image, ImageOps

    # 180 dpi keeps table text readable while bounding CPU and image memory.
    pixmap = document[index].get_pixmap(matrix=fitz.Matrix(2.5, 2.5), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    if remove_table_borders:
        image = _remove_blue_table_outlines(image)
    image = ImageOps.autocontrast(ImageOps.grayscale(image))
    return pytesseract.image_to_string(
        image, lang="kor+eng", config=f"--oem 1 --psm {psm}", timeout=timeout,
    ) or ""


def _overview_quality(text):
    data = parse_document_text(text)
    return sum(bool(data.get(key)) * weight for key, weight in (
        ("업체명", 1), ("사업자등록번호", 1), ("대표자명", 2),
        ("법인등록번호", 2), ("설립일", 2), ("기업유형", 1), ("기업규모", 1),
    ))


def _improve_identity_ocr(document, index, text, *, mode, deadline):
    """A readable cover must not suppress retries for an unreadable overview."""
    is_overview = bool(re.search(r"기업\s*개요", text)) or (mode == "full" and index == 1)
    score = _overview_quality if is_overview else lambda value: sum(bool(v) for v in extract_identity(value).values())
    target = 8 if is_overview else 3
    if not (is_overview or index < 2) or score(text) >= target:
        return text
    best, best_score = text, score(text)
    # PSM4 preserves table rows; PSM3 remains a fallback for cover/column layouts.
    for psm in (4, 3):
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            break
        try:
            candidate = _ocr_pdf_page(document, index, psm=psm, timeout=min(15, remaining))
        except Exception:
            # An optional retry must not discard a successfully read page.
            continue
        original_identity = extract_identity(text)
        candidate_identity = extract_identity(candidate)
        def comparable_name(name):
            return re.sub(r"주식회사|㈜|\(주\)|[\s().·]", "", name).casefold()
        if any(
            original_identity.get(key)
            and (not candidate_identity.get(key)
                 or normalizer(original_identity[key]) != normalizer(candidate_identity[key]))
            for key, normalizer in (("사업자등록번호", lambda value: re.sub(r"\D", "", value)),
                                    ("업체명", comparable_name))
        ):
            continue
        candidate_score = score(candidate)
        if candidate_score > best_score:
            best, best_score = candidate, candidate_score
        if best_score >= target:
            break
    return best


def _certification_ocr_evidence(document, index, text, *, deadline):
    if not re.search(r"기업\s*인증", text):
        return {}, ""
    fields = {"벤처", "이노비즈", "메인비즈", "연구개발전담부서", "기업부설연구소"}
    existing = _certification_text_values(_certification_block(text))
    if all(existing.get(key) in {"Y", "N"} for key in fields):
        return existing, ""
    remaining = deadline - time.monotonic()
    if remaining > 1:
        try:
            candidate = _ocr_pdf_page(
                document, index, psm=4, timeout=min(15, remaining), remove_table_borders=True,
            )
            values = _certification_text_values(_certification_block(candidate))
            if all(values.get(key) in {"Y", "N"} for key in fields):
                conflicts = {key for key in fields if key in existing and existing[key] != values[key]}
                for key in conflicts:
                    values[key] = ""
                return values, "certification_evidence_conflict" if conflicts else ""
        except Exception:
            pass
    return {}, "certification_ocr_incomplete"


def extract_document(pdf_path, mode="full", *, timeout_seconds=230, progress=emit_progress):
    """Text first, then bounded per-page OCR; all processing stays in this worker."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    page_texts = []
    warnings = []
    certification_evidence = {}
    ocr_pages = 0
    processed_pages = 0
    document = None
    ocr_disabled = False
    deadline = time.monotonic() + max(1, timeout_seconds)
    try:
        for index, page in enumerate(reader.pages):
            if time.monotonic() >= deadline:
                warnings.append("analysis_time_limit")
                break
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if len(re.sub(r"\s+", "", text)) < 40 and not ocr_disabled:
                try:
                    if document is None:
                        import fitz
                        document = fitz.open(str(pdf_path))
                    alternative = document[index].get_text("text") or ""
                    if len(alternative.strip()) > len(text.strip()):
                        text = alternative
                    if len(re.sub(r"\s+", "", text)) < 40:
                        progress(index + 1, total, "이미지형 PDF의 한글을 인식하고 있습니다.")
                        remaining = max(1, min(15, deadline - time.monotonic()))
                        original_text = text
                        ocr_text = _ocr_pdf_page(document, index, timeout=remaining)
                        ocr_pages += 1
                        ocr_text = _improve_identity_ocr(
                            document, index, ocr_text, mode=mode, deadline=deadline,
                        )
                        if mode == "full":
                            evidence, warning = _certification_ocr_evidence(
                                document, index, ocr_text, deadline=deadline,
                            )
                            if warning:
                                warnings.append(warning)
                            for key, value in evidence.items():
                                if key in certification_evidence and certification_evidence[key] != value:
                                    certification_evidence[key] = ""
                                    warnings.append("certification_evidence_conflict")
                                else:
                                    certification_evidence[key] = value
                        # Keep any usable embedded text even if OCR is empty or noisy.
                        text = "\n".join(part for part in (original_text, ocr_text) if part.strip())
                except (ImportError, ModuleNotFoundError):
                    warnings.append("ocr_unavailable")
                    ocr_disabled = True
                except Exception as exc:
                    # Missing binary/language data should fail once, not once per page.
                    if type(exc).__name__ in {"TesseractNotFoundError", "TesseractError"}:
                        warnings.append("ocr_unavailable")
                        ocr_disabled = True
                    else:
                        warnings.append(f"page_{index + 1}_ocr_failed")
            page_texts.append(text)
            processed_pages += 1
            progress(index + 1, total, "사업자정보 탐색 중" if mode == "identity" else "문서 섹션 탐색 중")
            if mode == "identity":
                identity = extract_identity("\n".join(page_texts))
                if identity.get("업체명") and identity.get("사업자등록번호"):
                    identity["_extraction"] = {
                        "method": "ocr" if ocr_pages else "text", "ocr_pages": ocr_pages,
                        "page_count": total, "processed_pages": processed_pages, "warnings": warnings,
                    }
                    return identity
    finally:
        if document is not None:
            document.close()

    joined = "\n".join(page_texts)
    result = extract_identity(joined) if mode == "identity" else parse_document_text(joined, certification_evidence=certification_evidence)
    if not has_company_identity(result):
        raise CretopExtractionError("ocr_unavailable" if "ocr_unavailable" in warnings else "identity_not_found")
    result["_extraction"] = {
        "method": "ocr" if ocr_pages else "text", "ocr_pages": ocr_pages,
        "page_count": total, "processed_pages": processed_pages, "warnings": warnings,
    }
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=["identity", "full"], default="full")
    parser.add_argument("--timeout-seconds", type=float, default=230)
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    output_path = Path(args.output)

    try:
        result = extract_document(pdf_path, args.mode, timeout_seconds=args.timeout_seconds)
    except CretopExtractionError as exc:
        print(json.dumps({"type": "error", "code": str(exc)}), flush=True)
        return 2
    except Exception:
        print(json.dumps({"type": "error", "code": "document_unreadable"}), flush=True)
        return 2

    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
