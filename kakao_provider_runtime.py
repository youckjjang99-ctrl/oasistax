from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import kakao_local_client
from cloud_db import CloudDatabase


PROVIDER = "kakao_local"
DEFAULT_DAILY_SAFE_REQUESTS = 85_000
MAX_DAILY_SAFE_REQUESTS = 90_000
LEASE_TTL_SECONDS = 600
GUARD_APPROVAL_CONFIRMATION = "KAKAO_RESTART_APPROVED"
GUARD_STATE_READY = "ready"
GUARD_STATE_BLOCKED = "blocked"
GUARD_STATE_RESUME_APPROVED = "resume_approved"
GUARD_REASON_INITIAL_ZERO_MATCH_RATE = "INITIAL_ZERO_MATCH_RATE"
GUARD_REASON_ROLLING_ZERO_MATCH_RATE = "ROLLING_ZERO_MATCH_RATE"
GUARD_REASON_CONSECUTIVE_PROVIDER_ERRORS = "CONSECUTIVE_PROVIDER_ERRORS"
GUARD_REASON_ORPHANED_HOLDS = "ORPHANED_HOLDS"
GUARD_REASON_PROVIDER_GUARD = "PROVIDER_GUARD"
GUARD_SOURCE_EMPLOYMENT = "employment"
GUARD_SOURCE_LICENSE = "license"
GUARD_REASONS = frozenset(
    {
        GUARD_REASON_INITIAL_ZERO_MATCH_RATE,
        GUARD_REASON_ROLLING_ZERO_MATCH_RATE,
        GUARD_REASON_CONSECUTIVE_PROVIDER_ERRORS,
        GUARD_REASON_ORPHANED_HOLDS,
        GUARD_REASON_PROVIDER_GUARD,
    }
)
GUARD_SOURCE_JOBS = frozenset(
    {GUARD_SOURCE_EMPLOYMENT, GUARD_SOURCE_LICENSE}
)
_SAFE_ERROR_CODE = re.compile(
    r"^(?:KEY_MISSING|TIMEOUT|NETWORK_ERROR|INVALID_JSON|"
    r"HTTP_[0-9]{3}|HTTP_ERROR|PROVIDER_ERROR)$"
)
_CONNECTION_CATEGORIES = {
    "CONNECTED",
    "AUTH_ERROR",
    "PERMISSION_ERROR",
    "QUOTA_ERROR",
    "NETWORK_ERROR",
}


def safe_error_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    return code if _SAFE_ERROR_CODE.fullmatch(code) else "PROVIDER_ERROR"


def safe_connection_category(value: Any, *, ok: bool = False) -> str:
    category = str(value or "").strip().upper()
    if ok:
        return "CONNECTED"
    if category not in _CONNECTION_CATEGORIES or category == "CONNECTED":
        return "NETWORK_ERROR"
    return category


def new_lease_token() -> str:
    return str(uuid.uuid4())


def new_guard_incident_token() -> str:
    return str(uuid.uuid4())


def _rpc_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, bool):
            return first
        if isinstance(first, dict) and first:
            return _rpc_bool(next(iter(first.values())))
    if isinstance(value, dict) and value:
        return _rpc_bool(next(iter(value.values())))
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def acquire_lease(
    lease_token: str,
    *,
    ttl_seconds: int = LEASE_TTL_SECONDS,
    database: CloudDatabase | None = None,
) -> bool:
    db = database or CloudDatabase()
    return _rpc_bool(
        db.rpc(
            "oasis_acquire_contact_provider_lease",
            {
                "p_provider": PROVIDER,
                "p_lease_token": lease_token,
                "p_ttl_seconds": int(ttl_seconds),
            },
        )
    )


def renew_lease(
    lease_token: str,
    *,
    ttl_seconds: int = LEASE_TTL_SECONDS,
    database: CloudDatabase | None = None,
) -> bool:
    db = database or CloudDatabase()
    return _rpc_bool(
        db.rpc(
            "oasis_renew_contact_provider_lease",
            {
                "p_provider": PROVIDER,
                "p_lease_token": lease_token,
                "p_ttl_seconds": int(ttl_seconds),
            },
        )
    )


def release_lease(
    lease_token: str,
    *,
    database: CloudDatabase | None = None,
) -> bool:
    db = database or CloudDatabase()
    return _rpc_bool(
        db.rpc(
            "oasis_release_contact_provider_lease",
            {
                "p_provider": PROVIDER,
                "p_lease_token": lease_token,
            },
        )
    )


def _usage_row(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return dict(value[0])
    if isinstance(value, dict):
        return dict(value)
    return {}


def _guard_row(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return dict(value[0])
    if isinstance(value, dict):
        return dict(value)
    return {}


def _required_nonnegative_int(row: dict[str, Any], key: str) -> int:
    value = row.get(key)
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise RuntimeError(f"provider guard {key} is invalid")
    return value


def _normalized_guard(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        raise RuntimeError("provider guard state is unavailable")
    generation = _required_nonnegative_int(row, "guard_generation")
    approved = _required_nonnegative_int(row, "approved_generation")
    consumed = _required_nonnegative_int(row, "consumed_generation")
    if consumed > approved or approved > generation:
        raise RuntimeError("provider guard generation order is invalid")
    if generation > approved:
        state = GUARD_STATE_BLOCKED
    elif approved > consumed:
        state = GUARD_STATE_RESUME_APPROVED
    else:
        state = GUARD_STATE_READY

    declared_state = str(row.get("guard_state") or "").strip().lower()
    if declared_state != state:
        raise RuntimeError("provider guard state is inconsistent")

    reason = str(row.get("guard_reason") or "").strip().upper()
    if reason and reason not in GUARD_REASONS:
        raise RuntimeError("provider guard reason is invalid")
    source_job = str(row.get("source_job") or "").strip().lower()
    if source_job and source_job not in GUARD_SOURCE_JOBS:
        raise RuntimeError("provider guard source is invalid")
    if state == GUARD_STATE_BLOCKED and (not reason or not source_job):
        raise RuntimeError("blocked provider guard metadata is missing")

    observed_count = _required_nonnegative_int(row, "observed_count")
    matched_count = _required_nonnegative_int(row, "matched_count")
    if matched_count > observed_count:
        raise RuntimeError("provider guard counts are invalid")
    return {
        "state": state,
        "guard_generation": generation,
        "approved_generation": approved,
        "consumed_generation": consumed,
        "guard_reason": reason,
        "source_job": source_job,
        "observed_count": observed_count,
        "matched_count": matched_count,
        "tripped_at": str(row.get("tripped_at") or ""),
        "approved_at": str(row.get("approved_at") or ""),
        "resumed_at": str(row.get("resumed_at") or ""),
    }


def get_guard_state(
    *,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    db = database or CloudDatabase()
    return _normalized_guard(
        _guard_row(
            db.rpc(
                "oasis_get_contact_provider_guard",
                {"p_provider": PROVIDER},
            )
        )
    )


def _validated_uuid(value: str, *, field: str) -> str:
    try:
        return str(uuid.UUID(str(value or "")))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def trip_guard(
    lease_token: str,
    incident_token: str,
    reason: str,
    source_job: str,
    *,
    observed_count: int = 0,
    matched_count: int = 0,
    database: CloudDatabase | None = None,
) -> bool:
    normalized_reason = str(reason or "").strip().upper()
    normalized_source = str(source_job or "").strip().lower()
    observed = int(observed_count)
    matched = int(matched_count)
    if normalized_reason not in GUARD_REASONS:
        raise ValueError("reason must be an approved guard reason")
    if normalized_source not in GUARD_SOURCE_JOBS:
        raise ValueError("source_job must be employment or license")
    if observed < 0 or matched < 0 or matched > observed:
        raise ValueError("guard counts are invalid")

    db = database or CloudDatabase()
    return _rpc_bool(
        db.rpc(
            "oasis_trip_contact_provider_guard",
            {
                "p_provider": PROVIDER,
                "p_lease_token": _validated_uuid(
                    lease_token,
                    field="lease_token",
                ),
                "p_incident_token": _validated_uuid(
                    incident_token,
                    field="incident_token",
                ),
                "p_guard_reason": normalized_reason,
                "p_source_job": normalized_source,
                "p_observed_count": observed,
                "p_matched_count": matched,
            },
        )
    )


def approve_guard(
    expected_generation: int,
    confirmation: str,
    *,
    database: CloudDatabase | None = None,
) -> bool:
    generation = int(expected_generation)
    if generation <= 0:
        raise ValueError("expected_generation must be positive")
    if str(confirmation or "") != GUARD_APPROVAL_CONFIRMATION:
        raise ValueError("confirmation phrase does not match")

    db = database or CloudDatabase()
    return _rpc_bool(
        db.rpc(
            "oasis_approve_contact_provider_guard",
            {
                "p_provider": PROVIDER,
                "p_expected_generation": generation,
                "p_confirmation": GUARD_APPROVAL_CONFIRMATION,
            },
        )
    )


def consume_guard_resume(
    lease_token: str,
    expected_generation: int,
    *,
    database: CloudDatabase | None = None,
) -> bool:
    generation = int(expected_generation)
    if generation <= 0:
        raise ValueError("expected_generation must be positive")

    db = database or CloudDatabase()
    return _rpc_bool(
        db.rpc(
            "oasis_consume_contact_provider_resume",
            {
                "p_provider": PROVIDER,
                "p_lease_token": _validated_uuid(
                    lease_token,
                    field="lease_token",
                ),
                "p_expected_generation": generation,
            },
        )
    )


def _normalized_usage(row: dict[str, Any]) -> dict[str, Any]:
    if not row:
        raise RuntimeError("provider usage state is unavailable")
    raw_count = row.get("request_count")
    if (
        not isinstance(raw_count, int)
        or isinstance(raw_count, bool)
        or raw_count < 0
    ):
        raise RuntimeError("provider usage count is invalid")
    quota_date = str(row.get("quota_date") or "").strip()
    try:
        date.fromisoformat(quota_date)
    except ValueError as exc:
        raise RuntimeError("provider quota date is invalid") from exc
    return {
        "request_count": raw_count,
        "blocked_until": str(row.get("blocked_until") or ""),
        "last_safe_error_code": safe_error_code(
            row.get("last_safe_error_code")
        ) if row.get("last_safe_error_code") else "",
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


def get_daily_usage(
    *,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    db = database or CloudDatabase()
    row = _usage_row(
        db.rpc(
            "oasis_get_contact_provider_daily_usage",
            {"p_provider": PROVIDER},
        )
    )
    return _normalized_usage(row)


def reserve_quota(
    request_count: int,
    safe_limit: int,
    *,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    requested = int(request_count)
    limit = int(safe_limit)
    if requested <= 0 or requested > 10_000:
        raise ValueError("request_count must be between 1 and 10000")
    if limit <= 0 or limit > MAX_DAILY_SAFE_REQUESTS:
        raise ValueError("safe_limit must be between 1 and 90000")

    db = database or CloudDatabase()
    row = _usage_row(
        db.rpc(
            "oasis_reserve_contact_provider_quota",
            {
                "p_provider": PROVIDER,
                "p_request_count": requested,
                "p_safe_limit": limit,
            },
        )
    )
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
    normalized_code = safe_error_code(safe_code) if safe_code else ""
    row = _usage_row(
        db.rpc(
            "oasis_record_contact_provider_usage",
            {
                "p_provider": PROVIDER,
                "p_request_count": int(request_count),
                "p_safe_error_code": normalized_code,
                "p_quota_date": (
                    _validated_quota_date(quota_date)
                    if quota_date
                    else None
                ),
            },
        )
    )
    return _normalized_usage(row)


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
    reserved_quota_date = _validated_quota_date(reservation_date)
    current_quota_date = _kst_quota_date()
    if reserved_quota_date != current_quota_date:
        # Never refund a prior-day reservation into the new quota day. The
        # actual calls are conservatively recorded on the current day so a
        # midnight-spanning batch cannot undercount the fresh quota.
        return record_usage(
            actual,
            safe_code,
            quota_date=current_quota_date,
            database=database,
        )
    return record_usage(
        actual - reserved,
        safe_code,
        quota_date=reserved_quota_date,
        database=database,
    )


def test_connection_and_record(
    *,
    safe_limit: int = DEFAULT_DAILY_SAFE_REQUESTS,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    reservation = reserve_quota(1, safe_limit, database=database)
    if not reservation.get("reserved"):
        existing_code = str(
            reservation.get("last_safe_error_code") or ""
        )
        return {
            "ok": False,
            "category": "QUOTA_ERROR",
            "safe_error_code": (
                safe_error_code(existing_code)
                if existing_code
                else "PROVIDER_ERROR"
            ),
            "request_count": 0,
        }

    result = dict(kakao_local_client.test_connection() or {})
    ok = bool(result.get("ok"))
    request_count = max(0, int(result.get("request_count") or 0))
    code = safe_error_code(result.get("safe_error_code")) if not ok else ""
    reconcile_usage(
        1,
        request_count,
        code,
        reservation_date=str(reservation.get("quota_date") or ""),
        database=database,
    )
    return {
        "ok": ok,
        "category": safe_connection_category(
            result.get("category"),
            ok=ok,
        ),
        "safe_error_code": code,
        "request_count": request_count,
    }


def parse_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def is_quota_blocked(
    usage: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    blocked_until = parse_timestamp(usage.get("blocked_until"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return bool(blocked_until and blocked_until > current)


def next_kst_quota_reset(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone(timedelta(hours=9)))
    tomorrow = current.date() + timedelta(days=1)
    return datetime.combine(
        tomorrow,
        datetime.min.time(),
        tzinfo=timezone(timedelta(hours=9)),
    ).astimezone(timezone.utc)
