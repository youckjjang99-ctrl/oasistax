from __future__ import annotations

import argparse
import json
import re
from datetime import date
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


_DATE_PART = r"((?:19|20)\d{2})\s*[.년/\-]\s*(\d{1,2})\s*[.월/\-]\s*(\d{1,2})\s*일?\s*\.?"
_DATED_ACTION = re.compile(_DATE_PART + r"\s*(변경|등기|설립|증자|감자|경정)(?![가-힣])")
_PREFIXED_DATE = re.compile(r"(변경일|등기일|원인일)\s*[:：]?\s*" + _DATE_PART)


def _capital_dates(text: str) -> list[dict]:
    result = []
    for pattern, prefixed in ((_DATED_ACTION, False), (_PREFIXED_DATE, True)):
        for match in pattern.finditer(text):
            parts = match.groups()[1:] if prefixed else match.groups()[:3]
            action = match.group(1) if prefixed else match.group(4)
            try:
                value = date(*map(int, parts)).isoformat()
            except (ValueError, TypeError):
                continue
            result.append({"start": match.start(), "end": match.end(), "value": value,
                           "field": "registered_at" if action.startswith("등기") else "effective_date"})
    return sorted(result, key=lambda item: item["start"])


def parse_capital_history(text: str) -> dict:
    """Keep dated capital candidates; never infer subscription price or cash paid.

    PDF text cannot reliably retain strike-throughs. Repeated numeric rows are
    retained as evidence, and ambiguous/undated changes require original review.
    """
    source = str(text or "")
    patterns = {
        "shares": re.compile(_spaced_label("발행주식의총수") + r"\s*[:：]?\s*([0-9][0-9,]*)\s*주"),
        # The capital-column heading often occurs only once. Its actual rows
        # read '보통주식 N주 금 M원', so retain each amount next to that row.
        # Never treat an arbitrary money amount elsewhere as registry capital.
        "capital": re.compile(r"(?:" + _spaced_label("자본금")
                              + r"(?:\s*의\s*액)?\s*[:：]?\s*금?"
                              + r"|(?:보통주식|상환전환우선주식|전환우선주식|우선주식|종류주식)"
                              + r"\s*[0-9][0-9,]*\s*주[\s|│┃]*금)\s*([0-9][0-9,]*)\s*원"),
        "par_value": re.compile(r"(?:" + _spaced_label("1주의금액") + "|" + _spaced_label("일주의금액") + r")\s*[:：]?\s*금?\s*([0-9][0-9,]*)\s*원"),
    }
    tokens = sorted(({"field": field, "value": _parse_money(match.group(1)),
                      "start": match.start(), "end": match.end()}
                     for field, pattern in patterns.items() for match in pattern.finditer(source)),
                    key=lambda item: item["start"])
    actions = _capital_dates(source)
    grouped = []
    current = []
    section = re.compile(r"목\s*적|임\s*원\s*에\s*관\s*한\s*사\s*항|회\s*사\s*성\s*립\s*연\s*월\s*일|본\s*점|공\s*고\s*방\s*법")
    for token in tokens:
        # PDF column extraction can put the change date between a row's share
        # count and capital amount. A date alone must not split complementary
        # fields. A repeated field (next row) or unrelated section does.
        separated = bool(current and section.search(source, current[-1]["end"], token["start"]))
        if current and (token["field"] in {item["field"] for item in current} or separated):
            grouped.append(current)
            current = []
        current.append(token)
    if current:
        grouped.append(current)
    history, warnings = [], []
    # A following, unrelated registry section cannot supply a capital date.
    for index, group in enumerate(grouped):
        end = grouped[index + 1][0]["start"] if index + 1 < len(grouped) else len(source)
        boundary = section.search(source, group[-1]["end"], end)
        if boundary:
            end = boundary.start()
        dates = [item for item in actions if group[0]["start"] <= item["start"] < end]
        row = {"shares": None, "capital": None, "par_value": None,
               "effective_date": "", "registered_at": "", "source_index": index,
               "review_required": False}
        row.update({item["field"]: item["value"] for item in group})
        for field in ("effective_date", "registered_at"):
            values = {item["value"] for item in dates if item["field"] == field}
            if len(values) == 1:
                row[field] = next(iter(values))
            elif len(values) > 1:
                row["review_required"] = True
                warnings.append("동일 자본행에 서로 다른 날짜가 있어 원문 확인이 필요합니다.")
        if not row["effective_date"]:
            row["review_required"] = True
        if row["effective_date"] and row["registered_at"] and row["registered_at"] < row["effective_date"]:
            row["review_required"] = True
            warnings.append("변경일과 등기일의 선후관계를 원문에서 확인해주세요.")
        # Separate PDF columns can repeat the same dated row for shares/capital.
        matching = next((item for item in history if row["effective_date"]
                         and item["effective_date"] == row["effective_date"]
                         and item["registered_at"] == row["registered_at"]
                         and all(item.get(field) in (None, row[field]) or row[field] is None
                                 for field in patterns)), None)
        if matching:
            for field in patterns:
                if row[field] is not None:
                    matching[field] = row[field]
            matching["review_required"] |= row["review_required"]
        else:
            history.append(row)
    for field in patterns:
        by_date = {}
        for row in history:
            if row["effective_date"] and row[field] is not None:
                by_date.setdefault(row["effective_date"], set()).add(row[field])
        for stamp, values in by_date.items():
            if len(values) > 1:
                for row in history:
                    if row["effective_date"] == stamp:
                        row["review_required"] = True
                warnings.append("같은 변경일의 자본·주식수 값이 상충하여 최신 값을 자동 확정하지 않았습니다.")
    if any(row["review_required"] for row in history):
        warnings.append("변경일 미확인 또는 상충 자본행은 원문 확인 후 반영해야 합니다. 등기일을 증자 납입일로 간주하지 않습니다.")
    latest = {}
    for field in patterns:
        observed = [row for row in history if row[field] is not None]
        values = {row[field] for row in observed}
        if len(values) == 1:
            latest[field] = next(iter(values))
        elif observed:
            dated = [row for row in observed if row["effective_date"]]
            last_dated = max(dated, key=lambda row: row["effective_date"], default=None)
            unknown_after = last_dated and any(not row["effective_date"] and row["source_index"] > last_dated["source_index"] for row in observed)
            same_date = [row for row in dated if last_dated and row["effective_date"] == last_dated["effective_date"]]
            if last_dated and not unknown_after and not last_dated["review_required"] and len({row[field] for row in same_date}) == 1:
                latest[field] = last_dated[field]
            else:
                latest[field] = None
    # A first snapshot proves a count, not an increase from zero or its date.
    share_rows = [row for row in history if row["shares"] is not None]
    # With undated rows, preserve documentary order as an unconfirmed candidate.
    # Sorting an undated *last* row to the beginning would reverse before/after.
    dated_order = (sorted(history, key=lambda row: (row["effective_date"] or "", row["source_index"]))
                   if all(row["effective_date"] for row in share_rows) else history)
    events, previous = [], None
    for row in dated_order:
        if row["shares"] is None:
            continue
        if previous and any(row.get(field) is not None and previous.get(field) is not None
                            and row[field] != previous[field] for field in ("shares", "capital", "par_value")):
            events.append({"before_shares": previous["shares"], "after_shares": row["shares"],
                           "before_capital": previous.get("capital"), "after_capital": row.get("capital"),
                           "par_value": row.get("par_value"), "effective_date": row["effective_date"],
                           "registered_at": row["registered_at"], "source": "registry", "kind": "unknown",
                           "issue_price": None, "review_required": bool(row["review_required"] or previous["review_required"])})
        previous = row
    return {"capital_history": history, "capital_events": events,
            "capital_history_warnings": list(dict.fromkeys(warnings)),
            "capital_history_review_required": any(row["review_required"] for row in history),
            "latest": latest}


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

    repeated_classes = len({item["종류"] for item in share_classes}) != len(share_classes)
    if not issued_shares and share_classes and not repeated_classes:
        issued_shares = sum(item.get("주식수") or 0 for item in share_classes)

    par_value_text = _first([
        rf"{_spaced_label('1주의금액')}\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
        r"일주의\s*금액\s*[:：]?\s*금?\s*([0-9,]+)\s*원",
    ], flat)

    capital_history = parse_capital_history(text)
    latest = capital_history.pop("latest")
    if repeated_classes and not issued_shares:
        capital_history["capital_history_review_required"] = True
        capital_history["capital_history_warnings"].append("반복된 종류주식 행을 합산하지 않았습니다. 현재 발행주식총수를 원문에서 확인해주세요.")
    return {
        "법인명": company_name,
        "법인등록번호": _format_corporate_no(corporate_no),
        "본점소재지": _clean(head_office),
        "법인설립일": incorporation_date,
        "자본금": latest.get("capital", _parse_money(capital_text)),
        "발행할주식총수": _parse_share_count(authorized_shares_text),
        "발행주식총수": latest.get("shares", issued_shares),
        "1주당액면가액": latest.get("par_value", _parse_money(par_value_text)),
        "주식종류": share_classes,
        **capital_history,
        "registry_parser_version": "capital-history-v1",
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
