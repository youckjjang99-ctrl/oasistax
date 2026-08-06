from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import kakao_provider_runtime
import naver_web_search_client
from cloud_db import CloudDatabase
from contact_enrichment import AUTO_CONFIRM_SCORE, enrich_company
from contact_matching import is_mobile_phone, normalize_phone


TABLE_CONTACTS = "oasis_employment_contacts"
CONTACT_TYPES = {"phone", "email", "instagram"}
CONTACT_STAGES = {"phone", "digital"}
PHONE_PROVIDERS = {"auto", "kakao", "naver"}
PHONE_PROVIDER_FIELD = "phone_provider_stage"
PHONE_ONLY_SOURCE_TYPES = {"comwel_all_employers"}
KAKAO_DAILY_SAFE_REQUESTS = (
    kakao_provider_runtime.DEFAULT_DAILY_SAFE_REQUESTS
)
NAVER_DAILY_SAFE_RECORDS = 12000
KAKAO_NO_MATCH_HELD = "KAKAO_NO_MATCH_HELD"
KAKAO_CONSECUTIVE_ERROR_LIMIT = 10
KAKAO_INITIAL_ZERO_MATCH_LIMIT = 100
KAKAO_ROLLING_ZERO_MATCH_LIMIT = 500
EXIT_PROVIDER_GUARD = 3
EXIT_DAILY_QUOTA = 4
EXIT_LEASE_UNAVAILABLE = 5
EXIT_PREFLIGHT_FAILED = 6
EXIT_RUNTIME_ERROR = 7
EXIT_PROVIDER_QUOTA = 8
DB_PATCH_RETRY_ATTEMPTS = 4
DB_PATCH_RETRY_DELAYS = (0.4, 1.0, 2.0)
STAGE_FIELDS = {
    "phone": {
        "status": "phone_status",
        "checked_at": "phone_checked_at",
        "next_check_at": "phone_next_check_at",
        "attempt_count": "phone_attempt_count",
        "last_error": "phone_last_error",
    },
    "digital": {
        "status": "digital_status",
        "checked_at": "digital_checked_at",
        "next_check_at": "digital_next_check_at",
        "attempt_count": "digital_attempt_count",
        "last_error": "digital_last_error",
    },
}
RATE_LIMIT_MARKERS = {
    "HTTP_403",
    "HTTP_429",
    "QUOTA_EXCEEDED",
    "RATE_LIMIT",
    "RATE_LIMITED",
}


class UpstreamLimitError(RuntimeError):
    """Raised when a contact provider reports a quota or rate limit."""


class KakaoProviderError(RuntimeError):
    """Safe, structured Kakao failure propagated from the provider client."""

    def __init__(self, safe_error_code: str, request_count: int = 0) -> None:
        self.safe_error_code = kakao_provider_runtime.safe_error_code(
            safe_error_code
        )
        self.request_count = max(0, int(request_count))
        super().__init__(self.safe_error_code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _select_rows(
    *,
    stage: str,
    status: str,
    limit: int,
    due_before: str | None = None,
    updated_before: str | None = None,
    phone_provider: str | None = None,
) -> list[dict[str, Any]]:
    fields = STAGE_FIELDS[stage]
    status_field = fields["status"]
    next_check_field = fields["next_check_at"]
    db = CloudDatabase()
    params: dict[str, str] = {
        "select": (
            "contact_key,source_type,source_record_key,business_no,"
            "company_name,address,industry_name,status,attempt_count,"
            "mobile_phone,landline_phone,email,instagram_id,instagram_url,"
            "contact_sources,phone_status,phone_checked_at,"
            "phone_next_check_at,phone_attempt_count,phone_last_error,"
            "phone_provider_stage,"
            "digital_status,digital_checked_at,digital_next_check_at,"
            "digital_attempt_count,digital_last_error"
        ),
        status_field: f"eq.{status}",
        "limit": str(max(1, min(5000, int(limit)))),
    }
    if stage == "phone" and phone_provider:
        params[PHONE_PROVIDER_FIELD] = f"eq.{phone_provider}"
        if phone_provider == "kakao" and status == "pending":
            # A no-match stays in Kakao while zero-match guards are evaluated.
            # Excluding the marker prevents it being selected again in-run.
            params[fields["last_error"]] = (
                f"neq.{KAKAO_NO_MATCH_HELD}"
            )
    if stage == "digital":
        params["source_type"] = (
            "not.in.("
            + ",".join(sorted(PHONE_ONLY_SOURCE_TYPES))
            + ")"
        )
    if due_before:
        params[next_check_field] = f"lte.{due_before}"
        params["order"] = f"{next_check_field}.asc,updated_at.asc"
    elif updated_before:
        params["updated_at"] = f"lt.{updated_before}"
        params["order"] = "updated_at.asc"
    else:
        params["order"] = "created_at.asc,contact_key.asc"
    response = requests.get(
        db._url(TABLE_CONTACTS),
        headers=db.headers,
        params=params,
        timeout=max(30, db.config.timeout),
    )
    if not response.ok:
        raise RuntimeError(
            "고용기업 연락처 보강 대상 조회 실패 "
            f"HTTP_{int(response.status_code)}"
        )
    rows = response.json() if response.text else []
    return rows if isinstance(rows, list) else []


def _eligible_rows(
    limit: int,
    stage: str = "phone",
    phone_provider: str | None = None,
) -> list[dict[str, Any]]:
    """단계별 재검증 대상을 처리한 뒤 신규 대상을 채운다."""
    if stage not in CONTACT_STAGES:
        raise ValueError("stage must be phone or digital")
    if (
        stage == "phone"
        and phone_provider not in {"kakao", "naver"}
    ):
        raise ValueError("phone_provider must be kakao or naver")
    limit = max(1, min(5000, int(limit)))
    now = _now()
    due_before = _iso(now)
    stale_processing_before = _iso(now - timedelta(days=1))
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    # The initial provider pass is the overwhelmingly common queue. Selecting
    # it first also lets Postgres use the small provider-specific pending index
    # instead of checking the full contact table before work can begin.
    queues = (
        ("pending", None, None),
        ("error", due_before, None),
        ("processing", None, stale_processing_before),
        ("matched", due_before, None),
        ("no_match", due_before, None),
    )
    for status, due, updated in queues:
        remaining = limit - len(selected)
        if remaining <= 0:
            break
        for row in _select_rows(
            stage=stage,
            status=status,
            limit=remaining,
            due_before=due,
            updated_before=updated,
            phone_provider=phone_provider,
        ):
            contact_key = str(row.get("contact_key") or "")
            if not contact_key or contact_key in seen:
                continue
            seen.add(contact_key)
            selected.append(row)
            if len(selected) >= limit:
                break
    return selected


def _patch(
    contact_key: str,
    values: dict[str, Any],
    *,
    expected_status: str | None = None,
    status_field: str = "status",
    expected_phone_provider_stage: str | None = None,
    expected_last_error: str | None = None,
) -> bool:
    db = CloudDatabase()
    headers = dict(db.headers)
    headers["Prefer"] = "return=representation"
    params = {
        "contact_key": f"eq.{contact_key}",
        "select": "contact_key",
    }
    if expected_status:
        params[status_field] = f"eq.{expected_status}"
    if expected_phone_provider_stage:
        params[PHONE_PROVIDER_FIELD] = (
            f"eq.{expected_phone_provider_stage}"
        )
    if expected_last_error is not None:
        params["phone_last_error"] = f"eq.{expected_last_error}"

    last_error = ""
    for attempt in range(DB_PATCH_RETRY_ATTEMPTS):
        try:
            response = requests.patch(
                db._url(TABLE_CONTACTS),
                headers=headers,
                params=params,
                data=json.dumps(values, ensure_ascii=False, default=str),
                timeout=max(30, db.config.timeout),
            )
            if response.ok:
                rows = response.json() if response.text else []
                return bool(rows)
            last_error = f"HTTP_{int(response.status_code)}"
            retryable = (
                response.status_code in {429, 500, 502, 503, 504}
                or "57014" in response.text
                or "statement timeout" in response.text.lower()
            )
        except requests.RequestException:
            last_error = "NETWORK_ERROR"
            retryable = True
        if not retryable or attempt + 1 >= DB_PATCH_RETRY_ATTEMPTS:
            break
        time.sleep(
            DB_PATCH_RETRY_DELAYS[
                min(attempt, len(DB_PATCH_RETRY_DELAYS) - 1)
            ]
        )
    raise RuntimeError(
        "employment contact update failed "
        f"{last_error}"
    )


def _claim(
    row: dict[str, Any],
    stage: str,
    phone_provider: str | None = None,
) -> bool:
    fields = STAGE_FIELDS[stage]
    status_field = fields["status"]
    status = str(row.get(status_field) or "pending")
    return _patch(
        str(row.get("contact_key") or ""),
        {
            status_field: "processing",
            fields["last_error"]: "",
            "updated_at": _iso(_now()),
        },
        expected_status=status,
        status_field=status_field,
        expected_phone_provider_stage=(
            phone_provider if stage == "phone" else None
        ),
    )


def _accepted_contacts(
    result: dict[str, Any],
    stage: str = "phone",
) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    allowed_types = (
        {"phone"} if stage == "phone" else {"email", "instagram"}
    )
    for raw in result.get("contacts") or []:
        row = dict(raw)
        contact_type = str(row.get("contact_type") or "")
        if (
            contact_type not in CONTACT_TYPES
            or contact_type not in allowed_types
        ):
            continue
        confidence = int(row.get("confidence") or 0)
        verification = str(row.get("verification_status") or "")
        if contact_type == "phone":
            if not normalize_phone(row.get("contact_value")):
                continue
            if (
                verification != "auto_verified"
                and confidence < AUTO_CONFIRM_SCORE
            ):
                continue
        elif confidence < 80:
            continue
        accepted.append(row)
    return accepted


def _provider_limit_message(result: dict[str, Any]) -> str:
    for row in result.get("trace") or []:
        status = str((row or {}).get("status") or "").upper()
        if any(marker in status for marker in RATE_LIMIT_MARKERS):
            stage = str((row or {}).get("stage") or "provider")
            return f"{stage}:{status}"
    return ""


def _kakao_provider_result(result: dict[str, Any]) -> dict[str, Any]:
    providers = result.get("provider_results") or {}
    provider = providers.get("kakao") if isinstance(providers, dict) else {}
    return dict(provider) if isinstance(provider, dict) else {}


def _has_kakao_no_match_holds() -> bool:
    """Return whether a previous guarded run left retryable Kakao holds."""
    db = CloudDatabase()
    result = db.rpc("oasis_has_kakao_no_match_holds", {})
    return _rpc_boolean(result, "Kakao held-row check")


def _clear_stale_kakao_no_match_holds() -> None:
    """Reset held rows only after an approved guarded restart preflight."""
    db = CloudDatabase()
    result = db.rpc("oasis_clear_kakao_no_match_holds", {})
    _rpc_integer(result, "Kakao held-row reset")


def _rpc_boolean(value: Any, operation: str) -> bool:
    """Read scalar PostgREST RPC values across supported response shapes."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    if isinstance(value, list) and len(value) == 1:
        return _rpc_boolean(value[0], operation)
    if isinstance(value, dict) and len(value) == 1:
        return _rpc_boolean(next(iter(value.values())), operation)
    raise RuntimeError(f"{operation} returned invalid result")


def _rpc_integer(value: Any, operation: str) -> int:
    """Validate a scalar integer result without coupling to REST response form."""
    if isinstance(value, bool):
        raise RuntimeError(f"{operation} returned invalid result")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    if isinstance(value, list) and len(value) == 1:
        return _rpc_integer(value[0], operation)
    if isinstance(value, dict) and len(value) == 1:
        return _rpc_integer(next(iter(value.values())), operation)
    raise RuntimeError(f"{operation} returned invalid result")


def _release_kakao_no_match_holds(contact_keys: list[str]) -> None:
    """Advance only confirmed normal no-matches to the Naver queue."""
    now = _iso(_now())
    for contact_key in dict.fromkeys(contact_keys):
        updated = _patch(
            contact_key,
            {
                "status": "pending",
                "phone_status": "pending",
                PHONE_PROVIDER_FIELD: "naver",
                "last_error": "",
                "phone_last_error": "",
                "next_check_at": now,
                "phone_next_check_at": now,
                "updated_at": now,
            },
            expected_status="pending",
            status_field="phone_status",
            expected_phone_provider_stage="kakao",
            expected_last_error=KAKAO_NO_MATCH_HELD,
        )
        if not updated:
            raise RuntimeError("Kakao held-row release conflict")


def _safe_runtime_event(
    status: str,
    *,
    category: str = "",
    safe_error_code: str = "",
    **values: Any,
) -> None:
    payload: dict[str, Any] = {
        "job": "employment-phone-enrichment",
        "provider": "kakao",
        "status": status,
    }
    if category:
        payload["category"] = str(category)
    if safe_error_code:
        payload["safe_error_code"] = (
            kakao_provider_runtime.safe_error_code(safe_error_code)
        )
    payload.update(values)
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _best(
    rows: list[dict[str, Any]],
    contact_type: str,
    *,
    mobile: bool | None = None,
) -> dict[str, Any] | None:
    candidates = [
        row
        for row in rows
        if str(row.get("contact_type") or "") == contact_type
        and (
            mobile is None
            or is_mobile_phone(row.get("contact_value", "")) is mobile
        )
    ]
    candidates.sort(
        key=lambda row: (
            str(row.get("verification_status") or "") == "auto_verified",
            int(row.get("confidence") or 0),
            bool(row.get("is_primary")),
        ),
        reverse=True,
    )
    return candidates[0] if candidates else None


def _enrich_one(
    row: dict[str, Any],
    stage: str = "phone",
    phone_provider: str | None = None,
    *,
    hold_kakao_no_match: bool = False,
) -> dict[str, Any]:
    if stage not in CONTACT_STAGES:
        raise ValueError("stage must be phone or digital")
    if stage == "phone" and phone_provider not in {"kakao", "naver"}:
        raise ValueError("phone_provider must be kakao or naver")
    fields = STAGE_FIELDS[stage]
    status_field = fields["status"]
    contact_key = str(row.get("contact_key") or "")
    if not _claim(row, stage, phone_provider):
        return {
            "status": "skipped",
            "contact_key": contact_key,
            "request_count": 0,
        }

    checked_at = _now()
    attempt_count = int(row.get("attempt_count") or 0) + 1
    stage_attempt_count = int(row.get(fields["attempt_count"]) or 0) + 1
    request_count = 0
    try:
        result = enrich_company(
            {
                "company_name": row.get("company_name"),
                "address": row.get("address"),
                "business_no": row.get("business_no"),
                "industry_name": row.get("industry_name"),
            },
            skip_kakao=stage == "phone" and phone_provider == "naver",
            skip_naver=stage == "phone" and phone_provider == "kakao",
            skip_localdata=True,
            kakao_runtime_managed=True,
            bulk_mode=stage == "phone",
            contact_stage=stage,
            website_timeout=6 if stage == "phone" else 8,
            max_website_candidates=2,
            website_max_pages=2,
        )
        kakao_provider = _kakao_provider_result(result)
        request_count = (
            0
            if result.get("cache_hit")
            else max(
                0,
                int(kakao_provider.get("request_count") or 0),
            )
        )
        if stage == "phone" and phone_provider == "kakao":
            kakao_outcome = str(
                kakao_provider.get("outcome")
                or result.get("outcome")
                or ""
            ).lower()
            if (
                not result.get("ok")
                or kakao_outcome not in {"matched", "no_match"}
            ):
                raise KakaoProviderError(
                    kakao_provider.get("safe_error_code")
                    or result.get("safe_error_code")
                    or (
                        "INVALID_JSON"
                        if result.get("ok")
                        else "PROVIDER_ERROR"
                    ),
                    request_count,
                )
        elif not result.get("ok"):
            raise RuntimeError("PROVIDER_ERROR")
        accepted = _accepted_contacts(result, stage)
        provider_limit = _provider_limit_message(result)
        if provider_limit and not accepted:
            raise UpstreamLimitError(provider_limit)

        mobile = _best(accepted, "phone", mobile=True)
        landline = _best(accepted, "phone", mobile=False)
        email = _best(accepted, "email")
        instagram = _best(accepted, "instagram")

        mobile_phone = (
            normalize_phone(mobile.get("contact_value"))
            if mobile
            else normalize_phone(row.get("mobile_phone"))
        )
        landline_phone = (
            normalize_phone(landline.get("contact_value"))
            if landline
            else normalize_phone(row.get("landline_phone"))
        )
        email_value = str(
            email.get("contact_value")
            if email
            else row.get("email") or ""
        )
        instagram_id = str(
            instagram.get("contact_value")
            if instagram
            else row.get("instagram_id") or ""
        )
        instagram_url = str(
            instagram.get("source_url")
            if instagram
            else row.get("instagram_url") or ""
        )
        if stage == "phone" and phone_provider == "kakao":
            # The Kakao transition reflects this provider call only. An older
            # phone already stored on the row must not turn a fresh no-match
            # into a provider match.
            phase_matched = any((mobile, landline))
            if phase_matched != (kakao_outcome == "matched"):
                raise KakaoProviderError("INVALID_JSON", request_count)
        else:
            phase_matched = (
                any((mobile_phone, landline_phone))
                if stage == "phone"
                else any((email_value, instagram_id, instagram_url))
            )
        kakao_no_match = (
            stage == "phone"
            and phone_provider == "kakao"
            and not phase_matched
        )
        overall_matched = any(
            (
                mobile_phone,
                landline_phone,
                email_value,
                instagram_id,
                instagram_url,
            )
        )
        next_check = (
            checked_at
            if kakao_no_match
            else checked_at + timedelta(
                days=30 if phase_matched else 90
            )
        )
        source_rows = dict(row.get("contact_sources") or {})
        source_rows.update({
            key: {
                "source_type": value.get("source_type"),
                "source_url": value.get("source_url"),
                "confidence": int(value.get("confidence") or 0),
            }
            for key, value in {
                "mobile_phone": mobile,
                "landline_phone": landline,
                "email": email,
                "instagram": instagram,
            }.items()
            if value
        })
        values = {
            "mobile_phone": mobile_phone,
            "landline_phone": landline_phone,
            "email": email_value,
            "instagram_id": instagram_id,
            "instagram_url": instagram_url,
            "contact_sources": source_rows,
            "status": (
                "matched"
                if overall_matched
                else "pending"
                if kakao_no_match
                else "no_match"
            ),
            "checked_at": _iso(checked_at),
            "next_check_at": _iso(next_check),
            "attempt_count": attempt_count,
            "last_error": "",
            status_field: (
                "matched"
                if phase_matched
                else "pending"
                if kakao_no_match
                else "no_match"
            ),
            fields["checked_at"]: _iso(checked_at),
            fields["next_check_at"]: _iso(next_check),
            fields["attempt_count"]: stage_attempt_count,
            fields["last_error"]: "",
        }
        if stage == "phone":
            if phase_matched or phone_provider == "naver":
                values[PHONE_PROVIDER_FIELD] = "complete"
            elif hold_kakao_no_match:
                values[PHONE_PROVIDER_FIELD] = "kakao"
                values["last_error"] = KAKAO_NO_MATCH_HELD
                values[fields["last_error"]] = KAKAO_NO_MATCH_HELD
            else:
                values[PHONE_PROVIDER_FIELD] = "naver"
        _patch(
            contact_key,
            values,
            expected_status="processing",
            status_field=status_field,
            expected_phone_provider_stage=(
                phone_provider if stage == "phone" else None
            ),
        )
        return {
            "status": (
                "matched"
                if phase_matched
                else "no_match"
            ),
            "contact_key": contact_key,
            "stage": stage,
            "provider": phone_provider if stage == "phone" else "",
            "outcome": "matched" if phase_matched else "no_match",
            "request_count": request_count,
            "held": bool(kakao_no_match and hold_kakao_no_match),
        }
    except Exception as exc:
        is_kakao_error = (
            stage == "phone" and phone_provider == "kakao"
        )
        if isinstance(exc, KakaoProviderError):
            request_count = exc.request_count
        if isinstance(exc, KakaoProviderError):
            safe_error_code = exc.safe_error_code
        elif isinstance(exc, UpstreamLimitError):
            message = str(exc).upper()
            safe_error_code = next(
                (
                    marker
                    for marker in RATE_LIMIT_MARKERS
                    if marker.startswith("HTTP_") and marker in message
                ),
                "PROVIDER_ERROR",
            )
        else:
            safe_error_code = "PROVIDER_ERROR"
        safe_error_code = kakao_provider_runtime.safe_error_code(
            safe_error_code
        )
        is_quota = safe_error_code == "HTTP_429"
        next_retry = (
            kakao_provider_runtime.next_kst_quota_reset(checked_at)
            if is_quota
            else checked_at + timedelta(minutes=5)
        )
        persistence_failed = False
        try:
            _patch(
                contact_key,
                {
                    "status": (
                        "matched"
                        if any(
                            (
                                row.get("mobile_phone"),
                                row.get("landline_phone"),
                                row.get("email"),
                                row.get("instagram_id"),
                                row.get("instagram_url"),
                            )
                        )
                        else "error"
                    ),
                    "checked_at": _iso(checked_at),
                    "next_check_at": _iso(next_retry),
                    "attempt_count": attempt_count,
                    "last_error": safe_error_code,
                    status_field: "error",
                    fields["checked_at"]: _iso(checked_at),
                    fields["next_check_at"]: _iso(next_retry),
                    fields["attempt_count"]: stage_attempt_count,
                    fields["last_error"]: safe_error_code,
                    **(
                        {PHONE_PROVIDER_FIELD: phone_provider}
                        if stage == "phone"
                        else {}
                    ),
                },
                expected_status="processing",
                status_field=status_field,
                expected_phone_provider_stage=(
                    phone_provider if stage == "phone" else None
                ),
            )
        except Exception:
            # Preserve the provider's safe code/request count for quota and
            # guard decisions even when the row-state PATCH itself failed.
            persistence_failed = True
        return {
            "status": "error",
            "contact_key": contact_key,
            "stage": stage,
            "provider": phone_provider if stage == "phone" else "",
            "outcome": "error",
            "safe_error_code": safe_error_code,
            "request_count": request_count,
            "provider_error": is_kakao_error,
            "halt": is_quota,
            "fatal": persistence_failed,
        }


def _run_provider_batches(
    *,
    stage: str,
    phone_provider: str,
    workers: int,
    batch_size: int,
    max_records: int,
    max_requests: int,
    daily_request_count: int,
    lease_token: str,
) -> int:
    totals = {
        "matched": 0,
        "no_match": 0,
        "error": 0,
        "skipped": 0,
    }
    processed = 0
    exit_code = 0
    held_no_match_keys: list[str] = []
    first_outcomes: list[str] = []
    recent_outcomes: deque[str] = deque(
        maxlen=KAKAO_ROLLING_ZERO_MATCH_LIMIT
    )
    consecutive_provider_errors = 0
    quota_provider_error = False

    try:
        while processed < max_records:
            select_limit = min(batch_size, max_records - processed)
            if phone_provider == "kakao":
                remaining_requests = max_requests - daily_request_count
                if remaining_requests < 2:
                    exit_code = EXIT_DAILY_QUOTA
                    _safe_runtime_event(
                        "daily_request_limit_reached",
                        request_count=daily_request_count,
                        request_limit=max_requests,
                    )
                    break
                # A maximum of ten provider calls can be concurrently in
                # flight. The consecutive-error guard therefore cannot be
                # hidden behind a pre-submitted batch of hundreds of calls.
                select_limit = min(
                    select_limit,
                    max(
                        1,
                        KAKAO_CONSECUTIVE_ERROR_LIMIT
                        - consecutive_provider_errors,
                    ),
                    remaining_requests // 2,
                )
                if len(first_outcomes) < KAKAO_INITIAL_ZERO_MATCH_LIMIT:
                    select_limit = min(
                        select_limit,
                        KAKAO_INITIAL_ZERO_MATCH_LIMIT
                        - len(first_outcomes),
                    )

            rows = _eligible_rows(
                select_limit,
                stage,
                phone_provider if stage == "phone" else None,
            )
            if not rows:
                break

            reserved_request_count = 0
            reservation_quota_date = ""
            if phone_provider == "kakao":
                reserved_request_count = 2 * len(rows)
                try:
                    reservation = kakao_provider_runtime.reserve_quota(
                        reserved_request_count,
                        max_requests,
                    )
                    daily_request_count = int(
                        reservation.get("request_count") or 0
                    )
                    reservation_quota_date = str(
                        reservation.get("quota_date") or ""
                    )
                except Exception:
                    exit_code = EXIT_RUNTIME_ERROR
                    _safe_runtime_event("quota_reservation_failed")
                    break
                if not reservation.get("reserved"):
                    quota_provider_error = (
                        kakao_provider_runtime.is_quota_blocked(
                            reservation
                        )
                        or str(
                            reservation.get("last_safe_error_code") or ""
                        ) == "HTTP_429"
                    )
                    exit_code = (
                        EXIT_PROVIDER_QUOTA
                        if quota_provider_error
                        else EXIT_DAILY_QUOTA
                    )
                    _safe_runtime_event(
                        "daily_request_limit_reached",
                        request_count=daily_request_count,
                        request_limit=max_requests,
                    )
                    break

            batch_request_count = 0
            batch_error_code = ""
            batch_usage_uncertain = False
            halted_reason = ""
            guard_trip_reason = ""
            guard_observed_count = 0
            guard_matched_count = 0
            with ThreadPoolExecutor(
                max_workers=min(workers, len(rows))
            ) as executor:
                futures = [
                    executor.submit(
                        _enrich_one,
                        row,
                        stage,
                        phone_provider if stage == "phone" else None,
                        hold_kakao_no_match=(phone_provider == "kakao"),
                    )
                    for row in rows
                ]
                for future in as_completed(futures):
                    try:
                        result = future.result()
                    except Exception:
                        batch_usage_uncertain = True
                        result = {
                            "status": "error",
                            "outcome": "error",
                            "request_count": 0,
                            "provider_error": phone_provider == "kakao",
                            "safe_error_code": "PROVIDER_ERROR",
                        }

                    status = str(result.get("status") or "error")
                    if status not in totals:
                        status = "error"
                    totals[status] += 1
                    processed += 1
                    batch_request_count += max(
                        0,
                        int(result.get("request_count") or 0),
                    )

                    if phone_provider != "kakao":
                        if result.get("halt"):
                            halted_reason = "provider_limit"
                            exit_code = EXIT_PROVIDER_GUARD
                        continue

                    outcome = str(
                        result.get("outcome") or status
                    ).lower()
                    if outcome not in {"matched", "no_match", "error"}:
                        continue
                    if len(first_outcomes) < KAKAO_INITIAL_ZERO_MATCH_LIMIT:
                        first_outcomes.append(outcome)
                    recent_outcomes.append(outcome)

                    if result.get("held"):
                        contact_key = str(result.get("contact_key") or "")
                        if contact_key:
                            held_no_match_keys.append(contact_key)

                    if outcome == "matched":
                        consecutive_provider_errors = 0
                        if held_no_match_keys and not halted_reason:
                            _release_kakao_no_match_holds(
                                held_no_match_keys
                            )
                            held_no_match_keys.clear()
                    elif outcome == "no_match":
                        consecutive_provider_errors = 0
                    elif result.get("provider_error"):
                        consecutive_provider_errors += 1
                        code = kakao_provider_runtime.safe_error_code(
                            result.get("safe_error_code")
                        )
                        if code == "HTTP_429" or not batch_error_code:
                            batch_error_code = code

                    if result.get("fatal") and not halted_reason:
                        halted_reason = "row_state_persistence_failed"
                        exit_code = EXIT_RUNTIME_ERROR

                    if (
                        str(result.get("safe_error_code") or "")
                        == "HTTP_429"
                    ):
                        quota_provider_error = True
                        halted_reason = "quota_error"
                        exit_code = EXIT_PROVIDER_QUOTA
                    elif (
                        not halted_reason
                        and consecutive_provider_errors
                        >= KAKAO_CONSECUTIVE_ERROR_LIMIT
                    ):
                        halted_reason = "consecutive_provider_errors"
                        exit_code = EXIT_PROVIDER_GUARD
                        guard_trip_reason = (
                            kakao_provider_runtime.GUARD_REASON_CONSECUTIVE_PROVIDER_ERRORS
                        )
                        guard_observed_count = consecutive_provider_errors
                    elif (
                        not halted_reason
                        and len(first_outcomes)
                        == KAKAO_INITIAL_ZERO_MATCH_LIMIT
                        and all(
                            item == "no_match"
                            for item in first_outcomes
                        )
                    ):
                        halted_reason = "initial_zero_match_rate"
                        exit_code = EXIT_PROVIDER_GUARD
                        guard_trip_reason = (
                            kakao_provider_runtime.GUARD_REASON_INITIAL_ZERO_MATCH_RATE
                        )
                        guard_observed_count = len(first_outcomes)
                        guard_matched_count = first_outcomes.count("matched")
                    elif (
                        not halted_reason
                        and len(recent_outcomes)
                        == KAKAO_ROLLING_ZERO_MATCH_LIMIT
                        and not any(
                            item == "matched"
                            for item in recent_outcomes
                        )
                    ):
                        halted_reason = "rolling_zero_match_rate"
                        exit_code = EXIT_PROVIDER_GUARD
                        guard_trip_reason = (
                            kakao_provider_runtime.GUARD_REASON_ROLLING_ZERO_MATCH_RATE
                        )
                        guard_observed_count = len(recent_outcomes)
                        guard_matched_count = recent_outcomes.count("matched")

                    if halted_reason:
                        for pending in futures:
                            if pending is not future:
                                pending.cancel()

            if phone_provider == "kakao":
                try:
                    updated_usage = kakao_provider_runtime.reconcile_usage(
                        reserved_request_count,
                        (
                            reserved_request_count
                            if batch_usage_uncertain
                            else batch_request_count
                        ),
                        batch_error_code,
                        reservation_date=reservation_quota_date,
                    )
                    daily_request_count = int(
                        updated_usage.get("request_count") or 0
                    )
                    if not kakao_provider_runtime.renew_lease(lease_token):
                        halted_reason = "lease_lost"
                        exit_code = EXIT_LEASE_UNAVAILABLE
                except Exception:
                    halted_reason = "runtime_persistence_failed"
                    exit_code = EXIT_RUNTIME_ERROR

                if guard_trip_reason and exit_code == EXIT_PROVIDER_GUARD:
                    try:
                        tripped = kakao_provider_runtime.trip_guard(
                            lease_token,
                            kakao_provider_runtime.new_guard_incident_token(),
                            guard_trip_reason,
                            kakao_provider_runtime.GUARD_SOURCE_EMPLOYMENT,
                            observed_count=guard_observed_count,
                            matched_count=guard_matched_count,
                        )
                    except Exception:
                        tripped = False
                    if not tripped:
                        halted_reason = "guard_persistence_failed"
                        exit_code = EXIT_RUNTIME_ERROR

            print(
                json.dumps(
                    {
                        "job": f"employment-{stage}-enrichment",
                        "stage": stage,
                        "provider": (
                            phone_provider if stage == "phone" else ""
                        ),
                        "processed": processed,
                        **(
                            {"request_count": daily_request_count}
                            if phone_provider == "kakao"
                            else {}
                        ),
                        **totals,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if halted_reason:
                _safe_runtime_event(
                    "stopped_by_guard",
                    safe_error_code=batch_error_code,
                    reason=halted_reason,
                    processed=processed,
                    request_count=daily_request_count,
                )
                break
            if phone_provider != "kakao":
                time.sleep(0.2)

        # Confirmed normal no-matches may advance on a clean finish or a
        # deliberate request-cap stop. Guard/error stops retain their holds in
        # Kakao so the same rows can be retried after administrator review.
        if (
            held_no_match_keys
            and exit_code in {0, EXIT_DAILY_QUOTA}
            and not quota_provider_error
        ):
            _release_kakao_no_match_holds(held_no_match_keys)
        return exit_code
    except Exception:
        _safe_runtime_event(
            "runtime_processing_failed",
            processed=processed,
        )
        return EXIT_RUNTIME_ERROR


def run_enrichment(
    *,
    stage: str = "phone",
    phone_provider: str = "auto",
    workers: int = 0,
    batch_size: int = 200,
    max_records: int = 0,
    max_requests: int = 0,
) -> int:
    if stage not in CONTACT_STAGES:
        raise ValueError("stage must be phone or digital")
    phone_provider = str(phone_provider or "auto").strip().lower()
    if phone_provider not in PHONE_PROVIDERS:
        raise ValueError("phone_provider must be auto, kakao, or naver")
    requested_auto = stage == "phone" and phone_provider == "auto"
    if requested_auto:
        phone_provider = "kakao"
    if stage == "digital":
        phone_provider = "auto"

    if int(workers) <= 0:
        workers = 12
    workers = max(1, min(20, int(workers)))
    batch_size = max(1, min(1000, int(batch_size)))

    if phone_provider == "kakao":
        max_records = (
            max(1, int(max_records))
            if int(max_records) > 0
            else 2_147_483_647
        )
        max_requests = (
            int(max_requests)
            if int(max_requests) > 0
            else KAKAO_DAILY_SAFE_REQUESTS
        )
        max_requests = max(
            1,
            min(
                kakao_provider_runtime.MAX_DAILY_SAFE_REQUESTS,
                max_requests,
            ),
        )
    else:
        naver_configured = naver_web_search_client.key_status()[
            "configured"
        ]
        if not naver_configured:
            raise RuntimeError("Naver provider key is required")
        if int(max_records) <= 0:
            max_records = NAVER_DAILY_SAFE_RECORDS
        max_records = max(1, min(25000, int(max_records)))
        max_requests = 0

    lease_token = ""
    daily_request_count = 0
    resume_guard_generation = 0
    try:
        if phone_provider == "kakao":
            lease_token = kakao_provider_runtime.new_lease_token()
            try:
                acquired = kakao_provider_runtime.acquire_lease(
                    lease_token
                )
            except Exception:
                _safe_runtime_event("lease_check_failed")
                return EXIT_RUNTIME_ERROR
            if not acquired:
                _safe_runtime_event("lease_unavailable")
                return EXIT_LEASE_UNAVAILABLE

            try:
                guard = kakao_provider_runtime.get_guard_state()
            except Exception:
                _safe_runtime_event("guard_state_check_failed")
                return EXIT_RUNTIME_ERROR
            guard_state = str(guard.get("state") or "")
            guard_generation = max(
                0,
                int(guard.get("guard_generation") or 0),
            )
            if guard_state == kakao_provider_runtime.GUARD_STATE_BLOCKED:
                _safe_runtime_event(
                    "guard_blocked",
                    reason=str(guard.get("guard_reason") or ""),
                    guard_generation=guard_generation,
                )
                return EXIT_PROVIDER_GUARD
            if guard_state == kakao_provider_runtime.GUARD_STATE_READY:
                try:
                    has_held_work = _has_kakao_no_match_holds()
                except Exception:
                    _safe_runtime_event("held_row_check_failed")
                    return EXIT_RUNTIME_ERROR
                if has_held_work:
                    try:
                        tripped = kakao_provider_runtime.trip_guard(
                            lease_token,
                            kakao_provider_runtime.new_guard_incident_token(),
                            kakao_provider_runtime.GUARD_REASON_ORPHANED_HOLDS,
                            kakao_provider_runtime.GUARD_SOURCE_EMPLOYMENT,
                            observed_count=1,
                            matched_count=0,
                        )
                    except Exception:
                        tripped = False
                    _safe_runtime_event("orphaned_holds_detected")
                    return (
                        EXIT_PROVIDER_GUARD
                        if tripped
                        else EXIT_RUNTIME_ERROR
                    )
            elif (
                guard_state
                == kakao_provider_runtime.GUARD_STATE_RESUME_APPROVED
            ):
                if guard_generation <= 0:
                    _safe_runtime_event("guard_state_invalid")
                    return EXIT_RUNTIME_ERROR
                resume_guard_generation = guard_generation
            else:
                _safe_runtime_event("guard_state_invalid")
                return EXIT_RUNTIME_ERROR

            try:
                usage = kakao_provider_runtime.get_daily_usage()
                daily_request_count = int(
                    usage.get("request_count") or 0
                )
            except Exception:
                _safe_runtime_event("usage_check_failed")
                return EXIT_RUNTIME_ERROR
            if kakao_provider_runtime.is_quota_blocked(usage):
                _safe_runtime_event(
                    "daily_quota_blocked",
                    safe_error_code=(
                        usage.get("last_safe_error_code") or "HTTP_429"
                    ),
                    request_count=daily_request_count,
                    request_limit=max_requests,
                )
                return EXIT_PROVIDER_QUOTA
            if daily_request_count >= max_requests:
                _safe_runtime_event(
                    "daily_request_limit_reached",
                    request_count=daily_request_count,
                    request_limit=max_requests,
                )
                return EXIT_DAILY_QUOTA

            try:
                preflight = (
                    kakao_provider_runtime.test_connection_and_record(
                        safe_limit=max_requests,
                    )
                )
            except Exception:
                _safe_runtime_event("preflight_runtime_failed")
                return EXIT_RUNTIME_ERROR
            if not preflight.get("ok"):
                _safe_runtime_event(
                    "preflight_failed",
                    category=str(preflight.get("category") or ""),
                    safe_error_code=str(
                        preflight.get("safe_error_code")
                        or "PROVIDER_ERROR"
                    ),
                )
                return (
                    EXIT_PROVIDER_QUOTA
                    if str(preflight.get("safe_error_code") or "")
                    == "HTTP_429"
                    else EXIT_PREFLIGHT_FAILED
                )
            _safe_runtime_event(
                "preflight_succeeded",
                category=str(preflight.get("category") or "CONNECTED"),
            )
            try:
                usage = kakao_provider_runtime.get_daily_usage()
                daily_request_count = int(
                    usage.get("request_count") or 0
                )
                if daily_request_count >= max_requests:
                    _safe_runtime_event(
                        "daily_request_limit_reached",
                        request_count=daily_request_count,
                        request_limit=max_requests,
                    )
                    return EXIT_DAILY_QUOTA
                if resume_guard_generation:
                    _clear_stale_kakao_no_match_holds()
                    consumed = (
                        kakao_provider_runtime.consume_guard_resume(
                            lease_token,
                            resume_guard_generation,
                        )
                    )
                    if not consumed:
                        _safe_runtime_event("guard_resume_consume_failed")
                        return EXIT_RUNTIME_ERROR
                    _safe_runtime_event(
                        "guard_resume_consumed",
                        guard_generation=resume_guard_generation,
                    )
            except Exception:
                _safe_runtime_event("runtime_setup_failed")
                return EXIT_RUNTIME_ERROR

            if requested_auto and not _eligible_rows(
                1,
                "phone",
                "kakao",
            ):
                try:
                    released = kakao_provider_runtime.release_lease(
                        lease_token
                    )
                except Exception:
                    _safe_runtime_event("lease_release_failed")
                    return EXIT_RUNTIME_ERROR
                finally:
                    lease_token = ""
                if not released:
                    _safe_runtime_event("lease_release_failed")
                    return EXIT_RUNTIME_ERROR
                return run_enrichment(
                    stage="phone",
                    phone_provider="naver",
                    workers=workers,
                    batch_size=batch_size,
                    max_records=(
                        0 if max_records == 2_147_483_647 else max_records
                    ),
                )

        print(
            json.dumps(
                {
                    "job": f"employment-{stage}-enrichment",
                    "status": "started",
                    "provider": (
                        phone_provider if stage == "phone" else ""
                    ),
                    "workers": workers,
                    "batch_size": batch_size,
                    "max_records": max_records,
                    **(
                        {"daily_request_limit": max_requests}
                        if phone_provider == "kakao"
                        else {}
                    ),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return _run_provider_batches(
            stage=stage,
            phone_provider=phone_provider,
            workers=workers,
            batch_size=batch_size,
            max_records=max_records,
            max_requests=max_requests,
            daily_request_count=daily_request_count,
            lease_token=lease_token,
        )
    finally:
        if lease_token:
            try:
                kakao_provider_runtime.release_lease(lease_token)
            except Exception:
                _safe_runtime_event("lease_release_failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=sorted(CONTACT_STAGES),
        default=os.environ.get("EMPLOYMENT_CONTACT_STAGE", "phone"),
    )
    parser.add_argument(
        "--phone-provider",
        choices=sorted(PHONE_PROVIDERS),
        default=os.environ.get(
            "EMPLOYMENT_PHONE_PROVIDER",
            "auto",
        ),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("EMPLOYMENT_CONTACT_WORKERS", "0")),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(
            os.environ.get("EMPLOYMENT_CONTACT_BATCH_SIZE", "200")
        ),
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=int(
            os.environ.get("EMPLOYMENT_CONTACT_MAX_RECORDS", "0")
        ),
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=int(
            os.environ.get("EMPLOYMENT_KAKAO_MAX_REQUESTS", "0")
        ),
    )
    args = parser.parse_args()
    return run_enrichment(
        stage=args.stage,
        phone_provider=args.phone_provider,
        workers=args.workers,
        batch_size=args.batch_size,
        max_records=args.max_records,
        max_requests=args.max_requests,
    )


if __name__ == "__main__":
    sys.exit(main())
