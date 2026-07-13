from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path


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
        rf"(?m)^\s*{escaped}(?:\(손실\)|\(순손실\)|\(\*\)|\(.*?\))?\s+((?:-?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s+)+)",
        rf"{escaped}(?:\(손실\)|\(순손실\)|\(\*\)|\(.*?\))?\s+((?:-?\s*[0-9][0-9,]*(?:\.[0-9]+)?\s+)+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, block or "")
        if not match:
            continue
        values = re.findall(r"-?\s*[0-9][0-9,]*(?:\.[0-9]+)?", match.group(1))
        if not values:
            continue
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
            [r"요약\s*현금흐름", r"요약\s*재무비율", r"연혁", r"재무상태표"],
        )
        for alias in income_aliases[label]:
            value = parse_latest_number_from_line(alias, summary)
            if value is not None:
                return int(round(value * 1_000_000))

        detail = extract_block(
            text,
            r"(?:상세\s*)?손익계산서\s+단위\s*:?\s*천원",
            [r"현금흐름표", r"자본변동표", r"이익잉여금처분계산서", r"재무비율"],
        )
        for alias in income_aliases[label]:
            value = parse_latest_number_from_line(alias, detail)
            if value is not None:
                return int(round(value * 1_000))

        # 표 제목이 달라 블록을 못 잡는 보고서의 최종 보완.
        # 백만원 단위 표를 우선하고, 없으면 천원 단위 표를 찾는다.
        million_sections = re.findall(
            r"(?:단위\s*:?\s*백만원)(.{0,8000})",
            text,
            flags=re.S,
        )
        for section in million_sections:
            result = _find_amount_anywhere(income_aliases[label], section, 1_000_000)
            if result != "":
                return result

        thousand_sections = re.findall(
            r"(?:단위\s*:?\s*천원)(.{0,12000})",
            text,
            flags=re.S,
        )
        for section in thousand_sections:
            result = _find_amount_anywhere(income_aliases[label], section, 1_000)
            if result != "":
                return result

    elif label in balance_aliases:
        summary = extract_block(
            text,
            r"요약\s*재무상태표",
            [r"요약\s*손익계산서", r"요약\s*현금흐름", r"요약\s*재무비율"],
        )
        for alias in balance_aliases[label]:
            value = parse_latest_number_from_line(alias, summary)
            if value is not None:
                return int(round(value * 1_000_000))

        detail = extract_block(
            text,
            r"재무상태표\s+단위\s*:?\s*천원",
            [r"손익계산서", r"현금흐름표"],
        )
        for alias in balance_aliases[label]:
            value = parse_latest_number_from_line(alias, detail)
            if value is not None:
                return int(round(value * 1_000))

    return ""


def extract_certifications(text):
    compact = re.sub(r"\s+", " ", text or "")
    result = {
        "벤처": "N",
        "이노비즈": "N",
        "메인비즈": "N",
        "연구개발전담부서": "N",
        "기업부설연구소": "N",
        "특허보유": "N",
        "상표": "N",
    }

    for key in ["벤처", "이노비즈", "메인비즈", "연구개발전담부서", "부설연구소"]:
        pattern = rf"{key}\s+(인증|보유|유|Y|미인증)"
        match = re.search(pattern, compact, re.I)
        if match and match.group(1) not in {"미인증"}:
            target = "기업부설연구소" if key == "부설연구소" else key
            result[target] = "Y"

    # 산업재산권 표에서 '-' 또는 '조회된 자료가 없습니다'면 N
    property_block = extract_block(
        text,
        r"산업재산권",
        [r"주요\s*주주", r"관계회사", r"주요\s*구매처"],
    )
    if property_block:
        patent_line = re.search(r"특허\s+실용신안\s+디자인\s+상표권\s+(.+)", property_block)
        if patent_line:
            values = patent_line.group(1)
            tokens = re.findall(r"[0-9]+|[Yy]|보유", values)
            if tokens:
                result["특허보유"] = "Y"
                result["상표"] = "Y"
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
        pattern = rf"(?m)^\s*{re.escape(alias)}(?:\(.*?\))?\s+(.+)$"
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
        [r"요약\s*현금흐름", r"요약\s*재무비율", r"연혁", r"재무상태표"],
    )
    balance_block = extract_block(
        text,
        r"요약\s*재무상태표",
        [r"요약\s*손익계산서", r"요약\s*현금흐름", r"요약\s*재무비율"],
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
        history.append({
            "연도": int(year),
            "매출액": rows["매출액"].get(year),
            "당기순이익": rows["당기순이익"].get(year),
            "자산총계": rows["자산총계"].get(year),
            "부채총계": rows["부채총계"].get(year),
            "자본총계": rows["자본총계"].get(year),
        })
    return history

def parse_document_text(text):
    data = {}
    data["업체명"] = regex_first(r"기업명\s+(.+?)\s+영문기업명", text)
    if not data["업체명"]:
        data["업체명"] = regex_first(r"기업명\s*[:：]\s*(.+?)\s+사업자번호", text)

    business_no = regex_first(r"사업자번호\s+([0-9]{3}-[0-9]{2}-[0-9]{5})", text)
    if not business_no:
        business_no = regex_first(r"([0-9]{3}-[0-9]{2}-[0-9]{5})", text)
    data["사업자등록번호"] = normalize_business_no(business_no)

    data["법인등록번호"] = regex_first(r"법인\(주민\)번호\s+([0-9\-]+)", text)
    data["대표자명"] = regex_first(r"대표자명\s+(.+?)\s+종업원수", text)
    data["종업원수"] = regex_first(r"종업원수\s+([0-9,]+)\s*명", text)

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

    data["기업유형"] = regex_first(r"기업유형\s+(.+?)\s+기업규모", text)
    data["기업규모"] = regex_first(r"기업규모\s+(.+?)(?:\n|전화번호|팩스번호)", text)

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=["identity", "full"], default="full")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    output_path = Path(args.output)

    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    total = len(reader.pages)
    page_texts = []

    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        page_texts.append(text)

        if args.mode == "identity":
            joined = "\n".join(page_texts)
            business_no = regex_first(
                r"사업자번호\s+([0-9]{3}-[0-9]{2}-[0-9]{5})",
                joined,
            )
            if not business_no:
                business_no = regex_first(
                    r"([0-9]{3}-[0-9]{2}-[0-9]{5})",
                    joined,
                )

            company_name = regex_first(r"기업명\s+(.+?)\s+영문기업명", joined)
            if not company_name:
                company_name = regex_first(
                    r"기업명\s*[:：]\s*(.+?)\s+사업자번호",
                    joined,
                )

            emit_progress(index, total, "사업자정보 탐색 중")
            if business_no:
                representative_name = regex_first(
                    r"대표자명\s+(.+?)\s+종업원수",
                    joined,
                )
                result = {
                    "업체명": company_name,
                    "대표자명": representative_name,
                    "사업자등록번호": normalize_business_no(business_no),
                }
                output_path.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                return 0

        else:
            emit_progress(index, total, "문서 섹션 탐색 중")

    joined = "\n".join(page_texts)
    if args.mode == "identity":
        result = parse_document_text(joined)
        result = {
            "업체명": result.get("업체명", ""),
            "대표자명": result.get("대표자명", ""),
            "사업자등록번호": result.get("사업자등록번호", ""),
        }
    else:
        result = parse_document_text(joined)

    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
