from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cloud_db import CloudDatabase


TABLE_LICENSED_BUSINESSES = "oasis_licensed_businesses"
TABLE_LICENSE_SYNC_RUNS = "oasis_license_sync_runs"


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


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
                "created_at": _timestamp(),
            }
        ],
    )
