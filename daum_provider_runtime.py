from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import kakao_provider_runtime
from cloud_db import CloudDatabase


PROVIDER = "daum_web"
DEFAULT_DAILY_SAFE_REQUESTS = 28_500
MAX_DAILY_SAFE_REQUESTS = 30_000
LEASE_TTL_SECONDS = 600


def safe_error_code(value: Any) -> str:
    return kakao_provider_runtime.safe_error_code(value)


def new_lease_token() -> str:
    return str(uuid.uuid4())


def _rpc_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list) and value:
        return _rpc_bool(value[0])
    if isinstance(value, dict) and value:
        return _rpc_bool(next(iter(value.values())))
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _usage_row(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return dict(value[0])
    if isinstance(value, dict):
        return dict(value)
    return {}


def _normalized_usage(value: Any) -> dict[str, Any]:
    row = _usage_row(value)
    raw_count = row.get("request_count")
    if (
        not row
        or not isinstance(raw_count, int)
        or isinstance(raw_count, bool)
        or raw_count < 0
    ):
        raise RuntimeError("Daum provider usage state is unavailable")
    quota_date = str(row.get("quota_date") or "").strip()
    try:
        date.fromisoformat(quota_date)
    except ValueError as exc:
        raise RuntimeError("Daum provider quota date is invalid") from exc
    return {
        "request_count": raw_count,
        "blocked_until": str(row.get("blocked_until") or ""),
        "last_safe_error_code": (
            safe_error_code(row.get("last_safe_error_code"))
            if row.get("last_safe_error_code")
            else ""
        ),
        "quota_date": quota_date,
    }


def _kst_quota_date() -> str:
    return datetime.now(timezone(timedelta(hours=9))).date().isoformat()


def _validated_quota_date(value: Any) -> str:
    quota_date = str(value or "").strip()
    try:
        date.fromisoformat(quota_date)
    except ValueError as exc:
        raise ValueError("quota_date must be an ISO date") from exc
    return quota_date


def acquire_lease(
    lease_token: str,
    *,
    ttl_seconds: int = LEASE_TTL_SECONDS,
    database: CloudDatabase | None = None,
) -> bool:
    db = database or CloudDatabase()
    return _rpc_bool(db.rpc(
        "oasis_acquire_contact_provider_lease",
        {
            "p_provider": PROVIDER,
            "p_lease_token": str(uuid.UUID(lease_token)),
            "p_ttl_seconds": int(ttl_seconds),
        },
    ))


def renew_lease(
    lease_token: str,
    *,
    ttl_seconds: int = LEASE_TTL_SECONDS,
    database: CloudDatabase | None = None,
) -> bool:
    db = database or CloudDatabase()
    return _rpc_bool(db.rpc(
        "oasis_renew_contact_provider_lease",
        {
            "p_provider": PROVIDER,
            "p_lease_token": str(uuid.UUID(lease_token)),
            "p_ttl_seconds": int(ttl_seconds),
        },
    ))


def release_lease(
    lease_token: str,
    *,
    database: CloudDatabase | None = None,
) -> bool:
    db = database or CloudDatabase()
    return _rpc_bool(db.rpc(
        "oasis_release_contact_provider_lease",
        {
            "p_provider": PROVIDER,
            "p_lease_token": str(uuid.UUID(lease_token)),
        },
    ))


def get_daily_usage(
    *,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    db = database or CloudDatabase()
    return _normalized_usage(db.rpc(
        "oasis_get_contact_provider_daily_usage",
        {"p_provider": PROVIDER},
    ))


def reserve_quota(
    request_count: int,
    safe_limit: int = DEFAULT_DAILY_SAFE_REQUESTS,
    *,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    requested = int(request_count)
    limit = int(safe_limit)
    if requested <= 0 or requested > 10_000:
        raise ValueError("request_count must be between 1 and 10000")
    if limit <= 0 or limit > MAX_DAILY_SAFE_REQUESTS:
        raise ValueError("safe_limit must be between 1 and 30000")
    db = database or CloudDatabase()
    row = _usage_row(db.rpc(
        "oasis_reserve_contact_provider_quota",
        {
            "p_provider": PROVIDER,
            "p_request_count": requested,
            "p_safe_limit": limit,
        },
    ))
    return {
        **_normalized_usage(row),
        "reserved": _rpc_bool(row.get("reserved")),
    }


def record_usage(
    request_count: int,
    safe_code: str = "",
    *,
    quota_date: str | None = None,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    db = database or CloudDatabase()
    return _normalized_usage(db.rpc(
        "oasis_record_contact_provider_usage",
        {
            "p_provider": PROVIDER,
            "p_request_count": int(request_count),
            "p_safe_error_code": safe_error_code(safe_code) if safe_code else "",
            "p_quota_date": (
                _validated_quota_date(quota_date) if quota_date else None
            ),
        },
    ))


def reconcile_usage(
    reserved_count: int,
    actual_count: int,
    safe_code: str = "",
    *,
    reservation_date: str,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    reserved = int(reserved_count)
    actual = int(actual_count)
    if reserved <= 0 or reserved > 10_000:
        raise ValueError("reserved_count must be between 1 and 10000")
    if actual < 0 or actual > reserved:
        raise ValueError("actual_count must be inside the reservation")
    reserved_date = _validated_quota_date(reservation_date)
    current_date = _kst_quota_date()
    if reserved_date != current_date:
        return record_usage(
            actual,
            safe_code,
            quota_date=current_date,
            database=database,
        )
    return record_usage(
        actual - reserved,
        safe_code,
        quota_date=reserved_date,
        database=database,
    )


def is_quota_blocked(
    usage: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    return kakao_provider_runtime.is_quota_blocked(usage, now=now)


def next_kst_quota_reset(now: datetime | None = None) -> datetime:
    return kakao_provider_runtime.next_kst_quota_reset(now)
