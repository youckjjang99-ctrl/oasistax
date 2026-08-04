from __future__ import annotations

from typing import Any

import kakao_local_client
import kakao_provider_runtime


def _provider_error(
    safe_error_code: str = "PROVIDER_ERROR",
    *,
    request_count: int = 0,
) -> dict[str, Any]:
    return {
        "ok": False,
        "outcome": "error",
        "status": kakao_provider_runtime.safe_error_code(safe_error_code),
        "safe_error_code": kakao_provider_runtime.safe_error_code(
            safe_error_code
        ),
        "request_count": max(0, int(request_count)),
        "candidates": [],
    }


def _connection_error(
    safe_error_code: str = "PROVIDER_ERROR",
) -> dict[str, Any]:
    code = kakao_provider_runtime.safe_error_code(safe_error_code)
    category = kakao_provider_runtime.safe_connection_category("", ok=False)
    if code in {"KEY_MISSING", "HTTP_401"}:
        category = "AUTH_ERROR"
    elif code == "HTTP_403":
        category = "PERMISSION_ERROR"
    elif code == "HTTP_429":
        category = "QUOTA_ERROR"
    return {
        "ok": False,
        "status": category,
        "category": category,
        "safe_error_code": code,
        "request_count": 0,
    }


def test_connection() -> dict[str, Any]:
    """Run the UI connection check under the shared guard and quota ledger."""
    lease_token = kakao_provider_runtime.new_lease_token()
    try:
        acquired = kakao_provider_runtime.acquire_lease(lease_token)
    except Exception:
        return _connection_error()
    if not acquired:
        return _connection_error()
    try:
        guard = kakao_provider_runtime.get_guard_state()
        if (
            str(guard.get("state") or "")
            != kakao_provider_runtime.GUARD_STATE_READY
        ):
            return _connection_error()
        result = kakao_provider_runtime.test_connection_and_record()
        return {
            "ok": bool(result.get("ok")),
            "status": kakao_provider_runtime.safe_connection_category(
                result.get("category"),
                ok=bool(result.get("ok")),
            ),
            "category": kakao_provider_runtime.safe_connection_category(
                result.get("category"),
                ok=bool(result.get("ok")),
            ),
            "safe_error_code": (
                ""
                if result.get("ok")
                else kakao_provider_runtime.safe_error_code(
                    result.get("safe_error_code")
                )
            ),
            "request_count": max(
                0,
                int(result.get("request_count") or 0),
            ),
        }
    except Exception:
        return _connection_error()
    finally:
        try:
            kakao_provider_runtime.release_lease(lease_token)
        except Exception:
            pass


def search_company(
    company_name: str,
    address: str,
    *,
    timeout: int = 5,
    size: int = 10,
    managed_externally: bool = False,
) -> dict[str, Any]:
    """Run a one-off Kakao lookup under the shared lease and quota ledger."""
    if managed_externally:
        return kakao_local_client.search_company(
            company_name,
            address,
            timeout=timeout,
            size=size,
        )

    lease_token = kakao_provider_runtime.new_lease_token()
    try:
        acquired = kakao_provider_runtime.acquire_lease(lease_token)
    except Exception:
        return _provider_error()
    if not acquired:
        return _provider_error()

    result: dict[str, Any] = _provider_error()
    try:
        guard = kakao_provider_runtime.get_guard_state()
        if (
            str(guard.get("state") or "")
            != kakao_provider_runtime.GUARD_STATE_READY
        ):
            return _provider_error()

        usage = kakao_provider_runtime.get_daily_usage()
        if kakao_provider_runtime.is_quota_blocked(usage):
            return _provider_error(
                usage.get("last_safe_error_code") or "HTTP_429"
            )
        reservation = kakao_provider_runtime.reserve_quota(
            2,
            kakao_provider_runtime.DEFAULT_DAILY_SAFE_REQUESTS,
        )
        if not reservation.get("reserved"):
            return _provider_error(
                reservation.get("last_safe_error_code")
                or (
                    "HTTP_429"
                    if kakao_provider_runtime.is_quota_blocked(reservation)
                    else "PROVIDER_ERROR"
                )
            )

        try:
            raw_result = kakao_local_client.search_company(
                company_name,
                address,
                timeout=timeout,
                size=size,
            )
            result = dict(raw_result or {})
            actual_count = int(result.get("request_count") or 0)
            if actual_count < 0 or actual_count > 2:
                actual_count = 2
                result = _provider_error(
                    "INVALID_JSON",
                    request_count=actual_count,
                )
        except Exception:
            actual_count = 2
            result = _provider_error(
                "PROVIDER_ERROR",
                request_count=actual_count,
            )

        safe_code = (
            ""
            if result.get("ok")
            else kakao_provider_runtime.safe_error_code(
                result.get("safe_error_code") or result.get("status")
            )
        )
        kakao_provider_runtime.reconcile_usage(
            2,
            actual_count,
            safe_code,
            reservation_date=str(reservation.get("quota_date") or ""),
        )
        return result
    except Exception:
        return _provider_error(
            "PROVIDER_ERROR",
            request_count=int(result.get("request_count") or 0),
        )
    finally:
        try:
            kakao_provider_runtime.release_lease(lease_token)
        except Exception:
            pass
