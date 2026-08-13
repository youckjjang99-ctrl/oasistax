from __future__ import annotations

import re
from typing import Any, Iterable

from cloud_db import CloudDatabase


LOOKUP_RPC = "oasis_lookup_procurement_activity"
UPSERT_RPC = "oasis_upsert_procurement_bidder_signals"
REFRESH_TODAY_RPC = "oasis_refresh_today_procurement_contacts"


def business_digits(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return digits if len(digits) == 10 else ""


def activity_label(row: dict[str, Any] | None) -> str:
    status = str((row or {}).get("activity_status") or "").lower()
    if status == "winner":
        return "나라장터 낙찰 이력"
    if status == "bidder":
        return "나라장터 투찰 이력"
    return ""


def load_activity_map(
    business_nos: Iterable[Any],
    *,
    database: CloudDatabase | None = None,
) -> dict[str, dict[str, Any]]:
    normalized = sorted(
        {
            digits
            for value in business_nos
            if (digits := business_digits(value))
        }
    )
    if not normalized:
        return {}
    db = database or CloudDatabase()
    rows = db.rpc(LOOKUP_RPC, {"p_business_nos": normalized}) or []
    if not isinstance(rows, list):
        return {}
    return {
        digits: dict(row)
        for row in rows
        if isinstance(row, dict)
        and (digits := business_digits(row.get("business_no")))
    }


def attach_activity_labels(
    records: list[dict[str, Any]],
    *,
    business_no_key: str,
    output_key: str = "나라장터활동",
    database: CloudDatabase | None = None,
) -> list[dict[str, Any]]:
    if not records:
        return records
    activity = load_activity_map(
        [row.get(business_no_key) for row in records],
        database=database,
    )
    for row in records:
        matched = activity.get(business_digits(row.get(business_no_key)))
        row[output_key] = activity_label(matched)
    return records
