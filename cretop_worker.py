from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime
from functools import lru_cache
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


_FINANCIAL_NUMBER = r"[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_FINANCIAL_CELL = rf"(?:{_FINANCIAL_NUMBER}|\([ \t]*{_FINANCIAL_NUMBER}[ \t]*\)|[-–—])"


def _financial_cells(raw):
    """A separated dash is an empty cell, never the next cell's minus sign."""
    raw = raw.strip()
    if not raw:
        return []
    if not re.fullmatch(rf"{_FINANCIAL_CELL}(?:[ \t]+{_FINANCIAL_CELL})*", raw):
        return None
    cells = []
    for token in re.findall(_FINANCIAL_CELL, raw):
        if token in {"-", "–", "—"}:
            cells.append(None)
            continue
        parenthesized = token.startswith("(")
        normalized = token.strip("() \t").replace(",", "").replace("−", "-")
        value = float(normalized)
        if not math.isfinite(value):
            return None
        cells.append(-abs(value) if parenthesized else value)
    return cells


def _financial_row_evidence(label, block):
    escaped = re.escape(label)
    # Account-name annotations are not numeric parentheses denoting losses.
    suffix = r"(?:\([^()\n]*[가-힣A-Za-z][^()\n]*\))?"
    patterns = [
        rf"(?m)^[ \t]*{escaped}{suffix}(?:[ \t]+([^\n]*)|(?=\n|$))",
        rf"(?<![가-힣]){escaped}{suffix}(?:[ \t]+([^\n]*)|(?=\n|$))",
    ]
    valid = []
    for pattern in patterns:
        for match in re.finditer(pattern, block or ""):
            # Ignore unreadable rows, but retain explicit blanks as evidence.
            values = _financial_cells(match.group(1) or "")
            if values is not None and values not in valid:
                valid.append(values)
    return {"recognized": bool(valid), "conflict": len(valid) > 1,
            "cells": valid[0] if len(valid) == 1 else None}


def _financial_row_cells(label, block):
    return _financial_row_evidence(label, block)["cells"]


def _financial_account_evidence(labels, block):
    evidence = [_financial_row_evidence(label, block) for label in labels]
    valid = []
    for item in evidence:
        if item["cells"] is not None and item["cells"] not in valid:
            valid.append(item["cells"])
    conflict = any(item["conflict"] for item in evidence) or len(valid) > 1
    return {"recognized": any(item["recognized"] for item in evidence),
            "conflict": conflict, "cells": valid[0] if len(valid) == 1 and not conflict else None}


def parse_latest_number_from_line(label, block):
    values = _financial_row_cells(label, block)
    return values[-1] if values else None


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


_INCOME_ALIASES = {
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
_BALANCE_ALIASES = {
        "자산총계": ["자산총계", "자산"],
        "부채총계": ["부채총계", "부채"],
        "자본총계": ["자본총계", "자본"],
}
_STATEMENT_HEADING = r"(?:(?P<prefix>요약|상세|포괄)\s*)?[ \t]*(?P<kind>손익(?:계산서|현황|내역)|재무상태표)"
_FINANCIAL_END = (
    r"(?:요약\s*)?(?:현금흐름|재무비율)|자본변동|이익잉여금처분|연혁|"
    + "|".join(PEER_HEADINGS)
)


def _financial_unit(block, default=None):
    unit = re.search(r"단위\s*[:：]?\s*([가-힣]+)", block[:160])
    if unit:
        return {"백만원": 1_000_000, "천원": 1_000, "원": 1}.get(unit.group(1))
    return default


def _financial_statement_selection(text, income=True):
    """Choose one company statement for all accounts; never fill gaps from MY."""
    aliases = _INCOME_ALIASES if income else _BALANCE_ALIASES
    headings = list(re.finditer(_STATEMENT_HEADING, text or ""))
    candidates = []
    for index, heading in enumerate(headings):
        if heading.group("kind").startswith("손익") != income:
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        block = re.split(_FINANCIAL_END, text[heading.end():min(end, heading.end() + 12000)], maxsplit=1)[0]
        summary = heading.group("prefix") == "요약"
        unit = _financial_unit(block, 1_000_000 if summary else None)
        if unit is None:
            continue
        priority = 0 if summary else (1 if heading.group("prefix") == "상세" or unit == 1_000 else 2)
        candidates.append((priority, heading.start(), block, unit))
    for priority, _, block, unit in sorted(candidates):
        source = {"block": block, "unit_multiplier": unit, "kind": ("summary", "detail", "alternate")[priority]}
        if re.search(r"조회된\s*자료가\s*없습니다|해당\s*자료가\s*없습니다", block):
            return source
        if any(_financial_account_evidence(names, block)["recognized"] for names in aliases.values()):
            return source
    return {"block": "", "unit_multiplier": 1_000_000, "kind": "unavailable"}


def _financial_statement_source(text, income=True):
    source = _financial_statement_selection(text, income)
    return source["block"], source["unit_multiplier"]


def _financial_statement_metadata(text, income=True):
    """Safe provenance for the extraction UI; never include account amounts."""
    source = _financial_statement_selection(text, income)
    columns = _financial_year_columns(source["block"])
    aliases = _INCOME_ALIASES if income else _BALANCE_ALIASES
    return {"kind": source["kind"], "unit_multiplier": source["unit_multiplier"],
            "years": [year for year in (columns or []) if year is not None],
            "year_columns": columns or [], "year_header_valid": columns is not None,
            "conflicting_accounts": [key for key, names in aliases.items()
                                     if _financial_account_evidence(names, source["block"])["conflict"]]}


def _financial_year_columns(block):
    # Only table headers may supply years; amounts such as 2024 are not years.
    labels = [alias for names in (*_INCOME_ALIASES.values(), *_BALANCE_ALIASES.values()) for alias in names]
    first_row = re.search(r"(?m)^[ \tⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩIVX0-9.·()]*(?:" + "|".join(map(re.escape, labels)) + r")(?:\(|[ \t\n]|$)", block)
    header = block[:first_row.start()] if first_row else ""
    columns = []
    cell_pattern = r"(?:\d{3,4}(?:[./-]\d{1,2}(?:[./-]\d{1,2})?)?년?|[-–—]|미상|미확인|확인불가|N/A)"
    for line in header.splitlines():
        raw = line.strip()
        label = re.match(r"^(?:구분|계정[ \t]*과목|계정|과목|결산(?:년도|연도|년월일|일)?|기준일|연도|년도)[ \t:：|]*", raw)
        if label:
            raw = raw[label.end():]
        # Preserve explicit empty header columns instead of shifting later years.
        if not raw or not (label or re.search(r"(?<!\d)20\d{2}(?!\d)", raw)):
            continue
        if not re.fullmatch(rf"{cell_pattern}(?:[ \t|]+{cell_pattern})*", raw):
            return None
        for token in re.findall(cell_pattern, raw):
            year = re.match(r"(20\d{2})(?!\d)", token)
            value = year.group(1) if year and int(year.group(1)) <= datetime.now().year + 1 else None
            columns.append(value)
    known = [year for year in columns if year is not None]
    return columns if len(columns) <= 10 and len(known) == len(set(known)) else None


def _financial_years(block):
    columns = _financial_year_columns(block)
    return [year for year in columns if year is not None] if columns is not None else None


def latest_financial_amount(label, text):
    aliases = _INCOME_ALIASES if label in _INCOME_ALIASES else _BALANCE_ALIASES
    if label not in aliases:
        return ""
    block, unit = _financial_statement_source(text, income=label in _INCOME_ALIASES)
    columns = _financial_year_columns(block)
    if columns is None:
        return ""
    years = [year for year in columns if year is not None]
    values = _financial_account_evidence(aliases[label], block)["cells"]
    if not values or (columns and (not years or len(values) != len(columns))):
        return ""
    value = values[columns.index(max(years))] if years else values[-1]
    amount = value * unit if value is not None else None
    return int(round(amount)) if amount is not None and math.isfinite(amount) else ""


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



def _extract_row_values(block, aliases, years, unit_multiplier=1_000_000):
    """요약 재무표에서 계정별 연도 값을 추출한다."""
    if not block or not years:
        return {}

    tokens = _financial_account_evidence(aliases, block)["cells"]
    # Extra chart values or a missing column must not shift the year mapping.
    if tokens is None or len(tokens) != len(years):
        return {}
    result = {}
    for year, token in zip(years, tokens):
        if year is None:
            continue
        if token is None:
            result[str(year)] = None
            continue
        amount = token * unit_multiplier
        result[str(year)] = int(round(amount)) if math.isfinite(amount) else None
    return result


def extract_annual_financial_history(text):
    """
    크레탑 요약 재무상태표·손익계산서의 최근 연도별 값을 반환한다.
    단위는 원이다.
    """
    income_block, income_unit = _financial_statement_source(text, income=True)
    balance_block, balance_unit = _financial_statement_source(text, income=False)
    income_years = _financial_year_columns(income_block) or []
    balance_years = _financial_year_columns(balance_block) or []

    rows = {
        "매출액": _extract_row_values(
            income_block,
            ["매출액", "매출", "영업수익"],
            income_years,
            income_unit,
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
            income_unit,
        ),
        "자산총계": _extract_row_values(
            balance_block,
            ["자산총계", "자산"],
            balance_years,
            balance_unit,
        ),
        "부채총계": _extract_row_values(
            balance_block,
            ["부채총계", "부채"],
            balance_years,
            balance_unit,
        ),
        "자본총계": _extract_row_values(
            balance_block,
            ["자본총계", "자본"],
            balance_years,
            balance_unit,
        ),
    }

    years = sorted(
        {year for year in income_years + balance_years if year is not None},
        reverse=True,
    )

    history = []
    for year in years[:3]:
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


def _representative_resolution(text):
    """Compare subject cover/overview evidence, never a later trading-party row."""
    text = normalize_document_text(text)
    scope = re.split(
        r"(?m)^[ \t]*(?:주요\s*(?:주주|구매처|판매처)|관계\s*회사|경영진\s*현황|"
        r"주주\s*현황|요약\s*(?:재무|손익)|MY\s*재무)", text, maxsplit=1,
    )[0]
    candidates = {}
    for match in re.finditer(r"(?<![가-힣A-Za-z])대표자(?:명)?[ \t]*[:：]?[ \t]*(?:\n[ \t]*)?[^\n]*", scope):
        value = _identity_value(match.group(), "대표자명", "대표자", validator=_valid_representative)
        if value:
            candidates.setdefault(re.sub(r"\s+", "", value).casefold(), value)
    if len(candidates) > 1:
        return "", "representative_conflict"
    return next(iter(candidates.values()), ""), ""


def _representative_needs_ocr_review(value):
    # A short isolated Latin token can be damaged Hangul. Do not ban real
    # international names in text PDFs; require verification only for OCR.
    return bool(re.fullmatch(r"[A-Z]{2,4}", str(value or "").strip()))


def extract_identity(text):
    text = normalize_document_text(text)
    company_name = _identity_value(text, "기업명", "회사명", "업체명")
    representative, _ = _representative_resolution(text)
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

def _financial_evidence_text(text, evidence):
    # Supplemental OCR is isolated from identity/certification extraction.
    blocks = [str((evidence or {}).get(kind) or "") for kind in ("income", "balance")]
    return "\n요약 현금흐름\n".join(block for block in (*blocks, text) if block)


def parse_document_text(text, *, certification_evidence=None, financial_evidence=None):
    text = normalize_document_text(text)
    financial_text = _financial_evidence_text(text, financial_evidence)
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

    data["매출액"] = latest_financial_amount("매출액", financial_text)
    data["연매출"] = data["매출액"]
    data["전년도매출"] = data["매출액"]
    data["영업이익"] = latest_financial_amount("영업이익", financial_text)
    data["당기순이익"] = latest_financial_amount("당기순이익", financial_text)
    data["자산총계"] = latest_financial_amount("자산총계", financial_text)
    data["부채총계"] = latest_financial_amount("부채총계", financial_text)
    data["자본총계"] = latest_financial_amount("자본총계", financial_text)

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
    data["재무연도별"] = extract_annual_financial_history(financial_text)
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


@lru_cache(maxsize=1)
def _require_korean_ocr_languages():
    import pytesseract
    try:
        languages = set(pytesseract.get_languages(config=""))
    except UnicodeDecodeError:
        # Windows may localize the banner/path in CP949. Language IDs are ASCII;
        # never disable the required-language check because that banner differs.
        import subprocess
        probe = subprocess.run([pytesseract.pytesseract.tesseract_cmd, "--list-langs"],
                               capture_output=True, timeout=10, check=False)
        languages = set(probe.stdout.decode("ascii", errors="ignore").splitlines()) if probe.returncode == 0 else set()
    if not {"kor", "eng"}.issubset(languages):
        # Tesseract can otherwise silently run English-only after Korean fails.
        raise pytesseract.TesseractError(1, "required_ocr_language_unavailable")


def _ocr_pdf_page(document, index, *, psm=6, timeout=15, remove_table_borders=False):
    import fitz
    import pytesseract
    from PIL import Image, ImageOps

    _require_korean_ocr_languages()
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


def _improve_identity_ocr(document, index, text, *, mode, deadline, review=None):
    """A readable cover must not suppress retries for an unreadable overview."""
    is_overview = bool(re.search(r"기업\s*개요", text)) or (mode == "full" and index == 1)
    score = _overview_quality if is_overview else lambda value: sum(bool(v) for v in extract_identity(value).values())
    target = 8 if is_overview else 3
    original_identity = extract_identity(text)
    uncertain_name = _representative_needs_ocr_review(original_identity.get("대표자명"))
    if not (is_overview or index < 2) or (score(text) >= target and not uncertain_name):
        return text
    best, best_score = text, score(text)
    name_evidence = [(text, original_identity.get("대표자명", ""))]
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
        name_evidence.append((candidate, candidate_identity.get("대표자명", "")))
        candidate_score = score(candidate)
        if candidate_score > best_score:
            best, best_score = candidate, candidate_score
        if best_score >= target and not uncertain_name:
            break
    if uncertain_name:
        counts = {}
        for _, name in name_evidence:
            if name:
                normalized = re.sub(r"\s+", "", name).casefold()
                counts[normalized] = counts.get(normalized, 0) + 1
        supported = [candidate for candidate, name in name_evidence
                     if name and not _representative_needs_ocr_review(name)
                     and counts.get(re.sub(r"\s+", "", name).casefold(), 0) >= 2]
        if supported:
            best = max(supported, key=score)
        elif review is not None:
            review["representative_warning"] = "representative_ocr_uncertain"
    elif review is not None and len({re.sub(r"\s+", "", name).casefold()
                                     for _, name in name_evidence if name}) > 1:
        review["representative_warning"] = "representative_conflict"
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
    financial_evidence = {}
    financial_conflicts = set()
    identity_reviews = []
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
                        identity_review = {}
                        ocr_text = _improve_identity_ocr(
                            document, index, ocr_text, mode=mode, deadline=deadline, review=identity_review,
                        )
                        if identity_review.get("representative_warning"):
                            identity_reviews.append(identity_review["representative_warning"])
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
                            if re.search(r"요약\s*(?:재무상태표|손익계산서)", ocr_text):
                                try:
                                    from cretop_ocr_tables import get_verified_financial_tables
                                    tables, table_warnings = get_verified_financial_tables(
                                        document, index, ocr_text, deadline=deadline,
                                    )
                                    warnings.extend(table_warnings)
                                    for kind, block in tables.items():
                                        if kind not in {"income", "balance"} or kind in financial_conflicts:
                                            continue
                                        if kind in financial_evidence and financial_evidence[kind] != block:
                                            financial_conflicts.add(kind)
                                            financial_evidence.pop(kind)
                                            warnings.append("financial_source_conflict")
                                        else:
                                            financial_evidence[kind] = block
                                except Exception:
                                    warnings.append("financial_table_ocr_incomplete")
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
                    _, identity_warning = _representative_resolution("\n".join(page_texts))
                    if identity_reviews or identity_warning:
                        identity["대표자명"] = ""
                        warnings.extend(identity_reviews + ([identity_warning] if identity_warning else []))
                    identity["_extraction"] = {
                        "method": "ocr" if ocr_pages else "text", "ocr_pages": ocr_pages,
                        "page_count": total, "processed_pages": processed_pages, "warnings": warnings,
                    }
                    return identity
    finally:
        if document is not None:
            document.close()

    joined = "\n".join(page_texts)
    result = extract_identity(joined) if mode == "identity" else parse_document_text(
        joined, certification_evidence=certification_evidence, financial_evidence=financial_evidence,
    )
    _, identity_warning = _representative_resolution(joined)
    if identity_reviews or identity_warning:
        result["대표자명"] = ""
        warnings.extend(identity_reviews + ([identity_warning] if identity_warning else []))
    financial_sources = {}
    if mode == "full":
        for name, income in (("income", True), ("balance", False)):
            source = _financial_statement_metadata(_financial_evidence_text(joined, financial_evidence), income=income)
            source["verified_table_ocr"] = name in financial_evidence
            financial_sources[name] = source
            if source.get("conflicting_accounts"):
                warnings.append("financial_source_conflict")
            if name in financial_conflicts:
                source["kind"] = "conflict"
                for field in (_INCOME_ALIASES if income else _BALANCE_ALIASES):
                    result[field] = ""
                    for row in result.get("재무연도별", []):
                        if field in row:
                            row[field] = None
                if income:
                    result["연매출"] = result["전년도매출"] = ""
            if source["kind"] == "alternate":
                warnings.append("financial_alternate_source")
            if source["kind"] != "unavailable" and not source["years"]:
                warnings.append("financial_year_unconfirmed")
    if not has_company_identity(result):
        raise CretopExtractionError("ocr_unavailable" if "ocr_unavailable" in warnings else "identity_not_found")
    result["_extraction"] = {
        "method": "ocr" if ocr_pages else "text", "ocr_pages": ocr_pages,
        "page_count": total, "processed_pages": processed_pages, "warnings": list(dict.fromkeys(warnings)),
        "financial_sources": financial_sources,
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
