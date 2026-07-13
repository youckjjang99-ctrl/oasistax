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


def parse_registry_text(text: str) -> dict:
    compact = re.sub(r"\s+", " ", text or "")

    company_name = _first([
        r"(?:상호|회사명)\s*[:：]?\s*([^\n]+?)(?:\s+본점|\s+법인등록번호|\s+목적)",
        r"등기번호\s*[^\n]+\n\s*상호\s*[:：]?\s*([^\n]+)",
    ], text, re.S)

    corporate_no = _first([
        r"법인등록번호\s*[:：]?\s*([0-9\-]{13,14})",
        r"등록번호\s*[:：]?\s*([0-9]{6}-[0-9]{7})",
    ], compact)

    head_office = _first([
        r"본점\s*[:：]?\s*(.+?)(?:\s+공고방법|\s+목적|\s+회사성립연월일|\s+자본금의액)",
        r"본점소재지\s*[:：]?\s*(.+?)(?:\s+공고방법|\s+목적|\s+회사성립연월일)",
    ], compact, re.S)

    incorporation_date = _first([
        r"회사성립연월일\s*[:：]?\s*([0-9]{4}[.\-/년]\s*[0-9]{1,2}[.\-/월]\s*[0-9]{1,2}일?)",
        r"설립연월일\s*[:：]?\s*([0-9]{4}[.\-/년]\s*[0-9]{1,2}[.\-/월]\s*[0-9]{1,2}일?)",
    ], compact)

    date_parts = re.findall(r"[0-9]+", incorporation_date)
    if len(date_parts) >= 3:
        incorporation_date = (
            f"{int(date_parts[0]):04d}-"
            f"{int(date_parts[1]):02d}-"
            f"{int(date_parts[2]):02d}"
        )

    capital_text = _first([
        r"자본금의액\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
        r"자본금\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
    ], compact)

    authorized_shares_text = _first([
        r"회사가\s*발행할\s*주식의\s*총수\s*[:：]?\s*([0-9,]+)\s*주",
        r"발행할\s*주식의\s*총수\s*[:：]?\s*([0-9,]+)\s*주",
    ], compact)

    issued_shares_text = _first([
        r"발행주식의\s*총수와\s*그\s*종류\s*및\s*각각의\s*수\s*[:：]?\s*(.+?)(?:\s+자본금의액|\s+1주의\s*금액|\s+목적)",
        r"발행주식의\s*총수\s*[:：]?\s*([0-9,]+)\s*주",
    ], compact, re.S)

    issued_shares = None
    share_classes = []

    if issued_shares_text:
        direct = re.search(r"([0-9,]+)\s*주", issued_shares_text)
        if direct:
            issued_shares = _parse_share_count(direct.group(1))

        for class_name, count in re.findall(
            r"(보통주식|우선주식|종류주식|상환전환우선주식|전환우선주식)\s*([0-9,]+)\s*주",
            issued_shares_text,
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
        r"1주의\s*금액\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
        r"일주의\s*금액\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
    ], compact)

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
