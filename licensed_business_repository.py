from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timezone
from typing import Any

from cloud_db import CloudDatabase


TABLE_LICENSED_BUSINESSES = "oasis_licensed_businesses"
TABLE_LICENSE_SYNC_RUNS = "oasis_license_sync_runs"
TABLE_RECENT_LICENSE_SIGNALS = "oasis_recent_license_signals"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _months_ago(value: date, months: int) -> date:
    month_index = value.year * 12 + value.month - 1 - max(1, int(months))
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _license_date(value: Any) -> date | None:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) != 8:
        return None
    try:
        return datetime.strptime(digits, "%Y%m%d").date()
    except ValueError:
        return None


def table_status() -> tuple[bool, str]:
    try:
        CloudDatabase().select(
            TABLE_LICENSED_BUSINESSES,
            columns="id",
            limit=1,
        )
        return True, "인허가 원천업체 테이블 연결 완료"
    except Exception as exc:
        return False, str(exc)


def compact_table_status() -> tuple[bool, str]:
    try:
        CloudDatabase().select(
            TABLE_RECENT_LICENSE_SIGNALS,
            columns="signal_key",
            limit=1,
        )
        return True, "최근 인허가일 경량 신호 테이블 연결 완료"
    except Exception as exc:
        return False, str(exc)


def save_recent_license_signals(
    items: list[dict[str, Any]],
    *,
    retention_months: int = 12,
) -> int:
    """Persist only hashed match keys and recent licence dates.

    Company names, addresses, phone numbers, and raw API responses are sent to
    a service-role-only RPC for hashing and are never stored in the table.
    """
    cutoff = _months_ago(datetime.now(timezone.utc).date(), retention_months)
    rows: list[dict[str, Any]] = []
    for item in items:
        parsed_date = _license_date(item.get("license_date"))
        source_key = str(item.get("source_key") or "").strip()
        company_name = str(item.get("company_name") or "").strip()
        address = str(item.get("address") or "").strip()
        if (
            not parsed_date
            or parsed_date < cutoff
            or not source_key
            or not company_name
            or not address
        ):
            continue
        rows.append(
            {
                "source_key": source_key,
                "company_name": company_name,
                "address": address,
                "license_date": parsed_date.isoformat(),
                "is_active": bool(item.get("is_active")),
            }
        )
    if not rows:
        return 0

    database = CloudDatabase()
    saved = 0
    for start in range(0, len(rows), 500):
        result = database.rpc(
            "oasis_upsert_recent_license_signals",
            {
                "p_rows": rows[start : start + 500],
                "p_retention_months": max(1, int(retention_months)),
            },
        )
        saved += int(result or 0)
    return saved


def cleanup_recent_license_signals(*, retention_months: int = 12) -> int:
    result = CloudDatabase().rpc(
        "oasis_cleanup_recent_license_signals",
        {"p_retention_months": max(1, int(retention_months))},
    )
    return int(result or 0)


def latest_sync_watermark(
    *,
    service_key: str,
    province: str = "",
    district: str = "",
) -> str:
    rows = CloudDatabase().select(
        TABLE_LICENSE_SYNC_RUNS,
        filters={
            "service_key": service_key,
            "province": province,
            "district": district,
            "status": "SUCCESS",
            "is_complete": "true",
        },
        columns="window_end,created_at",
        order="created_at.desc",
        limit=1,
    )
    if not rows:
        return ""
    row = rows[0]
    return str(row.get("window_end") or row.get("created_at") or "").strip()


def save_businesses(items: list[dict[str, Any]]) -> int:
    now = _timestamp()
    rows = [
        {
            "source_key": str(item.get("source_key") or ""),
            "service_key": str(item.get("service_key") or ""),
            "category": str(item.get("category") or ""),
            "industry_name": str(item.get("industry_name") or ""),
            "management_no": str(item.get("management_no") or ""),
            "company_name": str(item.get("company_name") or ""),
            "address": str(item.get("address") or ""),
            "province": str(item.get("province") or ""),
            "district": str(item.get("district") or ""),
            "phone": str(item.get("phone") or ""),
            "business_status_code": str(
                item.get("business_status_code") or ""
            ),
            "business_status_name": str(
                item.get("business_status_name") or ""
            ),
            "is_active": bool(item.get("is_active")),
            "license_date": str(item.get("license_date") or ""),
            "close_date": str(item.get("close_date") or ""),
            "source_updated_at": str(item.get("source_updated_at") or ""),
            "source_data": item.get("raw") or {},
            "last_seen_at": now,
            "updated_at": now,
        }
        for item in items
        if str(item.get("source_key") or "").strip()
        and str(item.get("company_name") or "").strip()
    ]
    if not rows:
        return 0
    saved = CloudDatabase().upsert(
        TABLE_LICENSED_BUSINESSES,
        rows,
        on_conflict="source_key",
    )
    return len(saved) if isinstance(saved, list) else len(rows)


def save_sync_run(
    *,
    service_key: str,
    page_no: int,
    received_count: int,
    saved_count: int,
    status: str,
    message: str = "",
    province: str = "",
    district: str = "",
    sync_mode: str = "full",
    window_start: str = "",
    window_end: str = "",
    is_complete: bool = False,
) -> None:
    CloudDatabase().insert(
        TABLE_LICENSE_SYNC_RUNS,
        [
            {
                "service_key": service_key,
                "page_no": max(1, int(page_no)),
                "received_count": max(0, int(received_count)),
                "saved_count": max(0, int(saved_count)),
                "status": str(status or ""),
                "message": str(message or "")[:1000],
                "province": str(province or ""),
                "district": str(district or ""),
                "sync_mode": str(sync_mode or "full"),
                "window_start": (
                    str(window_start).strip() if window_start else None
                ),
                "window_end": (
                    str(window_end).strip() if window_end else None
                ),
                "is_complete": bool(is_complete),
                "created_at": _timestamp(),
            }
        ],
    )
