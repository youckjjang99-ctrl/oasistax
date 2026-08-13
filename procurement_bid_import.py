from __future__ import annotations

import argparse
import csv
import os
import tempfile
from datetime import datetime, timezone
from collections.abc import Iterator
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlparse

import requests

from cloud_db import CloudDatabase, TABLE_PROCUREMENT_SYNC_RUNS
from procurement_discovery import UPSERT_RPC, business_digits


BUSINESS_NO_HEADERS = (
    "업체사업자등록번호",
    "사업자등록번호",
    "사업자번호",
)
BID_DATE_HEADERS = ("투찰일자", "개찰일자", "공고일자")
WINNER_HEADERS = ("낙찰자선정여부", "낙찰여부")
CATEGORY_HEADERS = ("업무구분", "공공조달분류", "조달방식")
TRUE_VALUES = {"y", "yes", "true", "1", "예", "선정", "낙찰"}


def _first_value(row: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = str(row.get(name) or "").strip()
        if value:
            return value
    return ""


def _date_text(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return ""


def _open_csv(path: Path) -> TextIO:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        handle = path.open("r", encoding=encoding, newline="")
        try:
            handle.read(4096)
            handle.seek(0)
            return handle
        except UnicodeDecodeError:
            handle.close()
    raise UnicodeError("나라장터 CSV 문자 인코딩을 확인해 주세요.")


def iter_bidder_rows(path: str | Path) -> Iterator[dict[str, Any]]:
    source = Path(path)
    with _open_csv(source) as handle:
        reader = csv.DictReader(handle)
        headers = {str(name or "").strip() for name in (reader.fieldnames or [])}
        if not headers.intersection(BUSINESS_NO_HEADERS):
            raise ValueError("CSV에서 업체사업자등록번호 열을 찾지 못했습니다.")
        for row in reader:
            normalized = {
                str(key or "").strip(): value
                for key, value in row.items()
            }
            business_no = business_digits(
                _first_value(normalized, BUSINESS_NO_HEADERS)
            )
            if not business_no:
                continue
            winner = _first_value(normalized, WINNER_HEADERS).casefold()
            yield {
                "business_no": business_no,
                "bid_date": _date_text(
                    _first_value(normalized, BID_DATE_HEADERS)
                ),
                "has_won": winner in TRUE_VALUES,
                "business_category": _first_value(
                    normalized,
                    CATEGORY_HEADERS,
                )[:30],
            }


def _merge_activity(
    target: dict[str, dict[str, Any]],
    row: dict[str, Any],
) -> None:
    business_no = str(row["business_no"])
    current = target.get(business_no)
    if current is None:
        target[business_no] = dict(row)
        return
    dates = [
        value
        for value in (current.get("bid_date"), row.get("bid_date"))
        if value
    ]
    current["bid_date"] = max(dates) if dates else ""
    current["has_won"] = bool(current.get("has_won") or row.get("has_won"))
    if row.get("business_category") and row.get("bid_date") == current.get("bid_date"):
        current["business_category"] = row["business_category"]


def import_bidder_csv(
    path: str | Path,
    *,
    database: CloudDatabase | None = None,
    batch_size: int = 1000,
) -> dict[str, int]:
    db = database or CloudDatabase()
    safe_batch_size = max(100, min(int(batch_size), 2000))
    pending: dict[str, dict[str, Any]] = {}
    source_rows = 0
    signal_rows = 0
    matched_contacts = 0

    def flush() -> None:
        nonlocal signal_rows, matched_contacts
        if not pending:
            return
        result = db.rpc(UPSERT_RPC, {"p_rows": list(pending.values())}) or []
        summary = result[0] if isinstance(result, list) and result else {}
        signal_rows += int(summary.get("signal_count") or 0)
        matched_contacts += int(summary.get("matched_contact_count") or 0)
        pending.clear()

    for row in iter_bidder_rows(path):
        source_rows += 1
        _merge_activity(pending, row)
        if len(pending) >= safe_batch_size:
            flush()
    flush()
    return {
        "source_rows": source_rows,
        "signal_rows": signal_rows,
        "matched_contacts": matched_contacts,
    }


def _download_csv(url: str) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("HTTPS 나라장터 CSV 주소만 사용할 수 있습니다.")
    response = requests.get(
        url,
        timeout=120,
        stream=True,
        headers={"User-Agent": "OASIS-CRM/procurement-bid-import"},
    )
    response.raise_for_status()
    descriptor, name = tempfile.mkstemp(prefix="oasis_procurement_", suffix=".csv")
    os.close(descriptor)
    target = Path(name)
    with target.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="나라장터 투찰 CSV를 비식별 요약 신호로 반영합니다."
    )
    parser.add_argument("--file", default="")
    parser.add_argument("--url", default=os.environ.get("PROCUREMENT_BID_CSV_URL", ""))
    parser.add_argument("--batch-size", type=int, default=1000)
    args = parser.parse_args()
    if not args.file and not args.url:
        raise SystemExit("--file 또는 PROCUREMENT_BID_CSV_URL이 필요합니다.")

    temporary: Path | None = None
    source = Path(args.file) if args.file else _download_csv(args.url)
    if not args.file:
        temporary = source
    db = CloudDatabase()
    sync_key = "procurement-bid-csv"
    db.upsert(
        TABLE_PROCUREMENT_SYNC_RUNS,
        [{
            "sync_key": sync_key,
            "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }],
        on_conflict="sync_key",
    )
    try:
        result = import_bidder_csv(source, database=db, batch_size=args.batch_size)
        db.upsert(
            TABLE_PROCUREMENT_SYNC_RUNS,
            [{
                "sync_key": sync_key,
                "status": "completed",
                "source_item_count": result["source_rows"],
                "bidder_signal_count": result["signal_rows"],
                "matched_contact_count": result["matched_contacts"],
            }],
            on_conflict="sync_key",
        )
        print(
            "나라장터 투찰 분류 완료: "
            f"원본 {result['source_rows']:,}건, "
            f"비식별 신호 {result['signal_rows']:,}건, "
            f"연락처 업체 매칭 {result['matched_contacts']:,}건"
        )
        return 0
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
