from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", str(text or "")).strip()


def _first(patterns, text, flags=0):
    for pattern in patterns:
        match = re.search(pattern, text or "", flags)
        if match:
            return _clean(match.group(1))
    return ""


def _digits(value):
    return re.sub(r"[^0-9]", "", str(value or ""))


def _format_corporate_no(value):
    digits = _digits(value)
    if len(digits) == 13:
        return f"{digits[:6]}-{digits[6:]}"
    return str(value or "").strip()


def _parse_money(value):
    digits = _digits(value)
    return int(digits) if digits else None


def _parse_share_count(value):
    digits = _digits(value)
    return int(digits) if digits else None


def _spaced_label(label: str) -> str:
    """
    인터넷등기소 PDF는 '상  호', '본  점'처럼 글자 사이 공백이 들어갈 수 있다.
    각 글자 사이에 임의 공백을 허용하는 정규식을 만든다.
    """
    return r"\s*".join(re.escape(char) for char in label)


def parse_registry_text(text: str) -> dict:
    compact = re.sub(r"\s+", " ", text or "")
    flat = compact

    company_name = _first([
        rf"{_spaced_label('상호')}\s*[:：]?\s*(.+?)(?:\s+\d{{4}}[.\-/]\d{{2}}[.\-/]\d{{2}}\s+변경|\s+본\s*점|\s+공고방법)",
        r"(?:상호|회사명)\s*[:：]?\s*([^\n]+?)(?:\s+본점|\s+법인등록번호|\s+목적)",
    ], flat, re.S)

    corporate_no = _first([
        rf"{_spaced_label('등록번호')}\s*[:：]?\s*([0-9]{{6}}-[0-9]{{7}})",
        r"법인등록번호\s*[:：]?\s*([0-9\-]{13,14})",
    ], flat)

    # 본점은 말소선이 포함된 구주소 뒤에 최신 주소가 이어질 수 있으므로
    # 공고방법 직전까지 모두 잡은 뒤 날짜·등기문구를 제거하고 마지막 주소를 사용한다.
    head_office_block = _first([
        rf"{_spaced_label('본점')}\s*[:：]?\s*(.+?)\s+공고방법",
        r"본점소재지\s*[:：]?\s*(.+?)(?:\s+공고방법|\s+목적|\s+회사성립연월일)",
    ], flat, re.S)

    head_office = ""
    if head_office_block:
        cleaned = re.sub(
            r"\d{4}[.\-/년]\s*\d{1,2}[.\-/월]\s*\d{1,2}일?\s*(?:변경|등기)",
            " ",
            head_office_block,
        )
        cleaned = re.sub(r"\.\s*\.", " ", cleaned)
        address_candidates = re.findall(
            r"(?:서울특별시|서울|부산광역시|부산|대구광역시|대구|인천광역시|인천|"
            r"광주광역시|광주|대전광역시|대전|울산광역시|울산|세종특별자치시|세종|"
            r"경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|"
            r"전라남도|경상북도|경상남도|제주특별자치도|제주도)"
            r".+?(?=(?:서울특별시|서울|부산광역시|부산|대구광역시|대구|인천광역시|인천|"
            r"광주광역시|광주|대전광역시|대전|울산광역시|울산|세종특별자치시|세종|"
            r"경기도|강원특별자치도|강원도|충청북도|충청남도|전북특별자치도|전라북도|"
            r"전라남도|경상북도|경상남도|제주특별자치도|제주도)|$)",
            cleaned,
        )
        if address_candidates:
            head_office = _clean(address_candidates[-1])
        else:
            head_office = _clean(cleaned)

    incorporation_date = _first([
        rf"{_spaced_label('회사성립연월일')}\s*[:：]?\s*([0-9]{{4}}\s*년\s*[0-9]{{1,2}}\s*월\s*[0-9]{{1,2}}\s*일)",
        r"설립연월일\s*[:：]?\s*([0-9]{4}[.\-/년]\s*[0-9]{1,2}[.\-/월]\s*[0-9]{1,2}일?)",
    ], flat)

    date_parts = re.findall(r"[0-9]+", incorporation_date)
    if len(date_parts) >= 3:
        incorporation_date = (
            f"{int(date_parts[0]):04d}-"
            f"{int(date_parts[1]):02d}-"
            f"{int(date_parts[2]):02d}"
        )

    capital_text = _first([
        rf"{_spaced_label('자본금의액')}\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
        r"자본금\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
        # 예담건설처럼 표에서 '보통주식 2,000주 금 10,000,000원' 형태
        r"보통주식\s*[0-9,]+\s*주\s*금\s*([0-9,]+)\s*원",
    ], flat)

    authorized_shares_text = _first([
        rf"{_spaced_label('발행할주식의총수')}\s*[:：]?\s*([0-9,]+)\s*주",
        r"발행할\s*주식의\s*총수\s*[:：]?\s*([0-9,]+)\s*주",
    ], flat)

    # 핵심 수정: 표 머리글 뒤의 실제 '발행주식의 총수 2,000주' 행을 직접 찾는다.
    issued_shares_text = _first([
        rf"{_spaced_label('발행주식의총수')}\s*[:：]?\s*([0-9,]+)\s*주",
        r"발행주식의\s*총수와\s*그\s*종류\s*및\s*각각의\s*수.*?"
        r"발행주식의\s*총수\s*([0-9,]+)\s*주",
    ], flat, re.S)

    issued_shares = _parse_share_count(issued_shares_text)

    share_classes = []
    for class_name, count in re.findall(
        r"(보통주식|우선주식|종류주식|상환전환우선주식|전환우선주식)"
        r"\s*([0-9,]+)\s*주",
        flat,
    ):
        share_classes.append({
            "종류": class_name,
            "주식수": _parse_share_count(count),
        })

    if not issued_shares and share_classes:
        issued_shares = sum(
            item.get("주식수") or 0 for item in share_classes
        )

    par_value_text = _first([
        rf"{_spaced_label('1주의금액')}\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
        r"일주의\s*금액\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
    ], flat)

    return {
        "법인명": company_name,
        "법인등록번호": _format_corporate_no(corporate_no),
        "본점소재지": _clean(head_office),
        "법인설립일": incorporation_date,
        "자본금": _parse_money(capital_text),
        "발행할주식총수": _parse_share_count(authorized_shares_text),
        "발행주식총수": issued_shares,
        "1주당액면가액": _parse_money(par_value_text),
        "주식종류": share_classes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    from pypdf import PdfReader

    pdf_path = Path(args.pdf)
    output_path = Path(args.output)

    reader = PdfReader(str(pdf_path))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    result = parse_registry_text(text)

    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
