from __future__ import annotations

import argparse
import os
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import kakao_local_client
import kakao_provider_runtime
from cloud_db import CloudDatabase
from contact_enrichment import AUTO_CONFIRM_SCORE
from contact_matching import normalize_phone
from licensed_business_repository import TABLE_LICENSED_BUSINESSES


MAX_CONSECUTIVE_PROVIDER_ERRORS = 10
KAKAO_INITIAL_ZERO_MATCH_LIMIT = 100
KAKAO_ROLLING_ZERO_MATCH_LIMIT = 500
KAKAO_NO_MATCH_HELD = "KAKAO_NO_MATCH_HELD"
EXIT_PROVIDER_GUARD = 3
LEASE_RENEW_INTERVAL_SECONDS = 120
SOURCE_JOB = kakao_provider_runtime.GUARD_SOURCE_LICENSE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eligible_rows(limit: int, retry_days: int) -> list[dict[str, Any]]:
    """Fetch a bounded, index-friendly phone enrichment batch."""
    db = CloudDatabase()
    retry_before = (
        datetime.now(timezone.utc) - timedelta(days=max(1, retry_days))
    ).isoformat()
    response = requests.get(
        db._url(TABLE_LICENSED_BUSINESSES),
        headers=db.headers,
        params={
            "select": "id,source_key,company_name,address",
            "phone": "eq.",
            "phone_enrichment_status": "eq.pending",
            "phone_checked_at": "is.null",
            "company_name": "neq.",
            "address": "neq.",
            "order": "created_at.asc,id.asc",
            "limit": str(max(1, min(1000, limit))),
        },
        timeout=db.config.timeout,
    )
    if not response.ok:
        raise RuntimeError("LICENSE_PHONE_QUEUE_READ_FAILED")
    data = response.json() if response.text else []
    if isinstance(data, list) and data:
        return data

    response = requests.get(
        db._url(TABLE_LICENSED_BUSINESSES),
        headers=db.headers,
        params={
            "select": "id,source_key,company_name,address",
            "phone": "eq.",
            "phone_enrichment_status": "in.(no_match,error)",
            "phone_checked_at": f"lt.{retry_before}",
            "company_name": "neq.",
            "address": "neq.",
            "order": "phone_checked_at.asc",
            "limit": str(max(1, min(1000, limit))),
        },
        timeout=db.config.timeout,
    )
    if not response.ok:
        raise RuntimeError("LICENSE_PHONE_RETRY_QUEUE_READ_FAILED")
    data = response.json() if response.text else []
    return data if isinstance(data, list) else []


def _patch_if_phone_empty(
    source_key: str,
    values: dict[str, Any],
    *,
    expected_status: str | None = None,
    expected_error: str | None = None,
) -> bool:
    db = CloudDatabase()
    headers = dict(db.headers)
    headers["Prefer"] = "return=representation"
    params = {"source_key": f"eq.{source_key}", "phone": "eq."}
    if expected_status is not None:
        params["phone_enrichment_status"] = f"eq.{expected_status}"
    if expected_error is not None:
        params["phone_enrichment_error"] = f"eq.{expected_error}"
    response = requests.patch(
        db._url(TABLE_LICENSED_BUSINESSES),
        headers=headers,
        params=params,
        json=values,
        timeout=db.config.timeout,
    )
    if not response.ok:
        raise RuntimeError("LICENSE_PHONE_SAVE_FAILED")
    rows = response.json() if response.text else []
    return bool(rows)


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _runtime_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, list) and value:
        return _runtime_bool(value[0])
    if isinstance(value, dict) and value:
        if "consumed" in value:
            return _runtime_bool(value["consumed"])
        return _runtime_bool(next(iter(value.values())))
    return False


def _fatal_result(*, request_count: int, provider_error: bool) -> dict[str, Any]:
    return {
        "status": "error",
        "outcome": "error",
        "provider_error": provider_error,
        "fatal": True,
        "safe_error_code": (
            "PROVIDER_ERROR" if provider_error else ""
        ),
        "request_count": max(0, request_count),
    }


def _invalid_provider_result(request_count: int = 0) -> dict[str, Any]:
    return {
        "outcome": "error",
        "safe_error_code": "INVALID_JSON",
        "request_count": max(0, request_count),
    }


def _strict_kakao_result(
    result: Any,
    min_score: int,
) -> dict[str, Any]:
    """Validate the provider tri-state without retaining upstream payloads."""
    if not isinstance(result, dict) or type(result.get("ok")) is not bool:
        return _invalid_provider_result()

    request_count = max(0, _int_value(result.get("request_count")))
    outcome = str(result.get("outcome") or "").strip().lower()
    status = str(result.get("status") or "").strip().upper()
    raw_safe_code = str(result.get("safe_error_code") or "").strip().upper()

    if not result["ok"]:
        if outcome != "error" or not raw_safe_code:
            return _invalid_provider_result(request_count)
        safe_code = kakao_provider_runtime.safe_error_code(raw_safe_code)
        if safe_code != raw_safe_code:
            return _invalid_provider_result(request_count)
        if status and status not in {"ERROR", safe_code}:
            return _invalid_provider_result(request_count)
        candidates = result.get("candidates", [])
        if not isinstance(candidates, list) or candidates:
            return _invalid_provider_result(request_count)
        return {
            "outcome": "error",
            "safe_error_code": safe_code,
            "request_count": request_count,
        }

    if outcome not in {"matched", "no_match"} or raw_safe_code:
        return _invalid_provider_result(request_count)
    expected_status = "MATCHED" if outcome == "matched" else "NO_MATCH"
    if status != expected_status:
        return _invalid_provider_result(request_count)

    candidates = result.get("candidates")
    if not isinstance(candidates, list) or any(
        not isinstance(item, dict) for item in candidates
    ):
        return _invalid_provider_result(request_count)
    provider_matches = [
        item
        for item in candidates
        if normalize_phone(item.get("phone"))
        and _int_value(item.get("confidence")) >= AUTO_CONFIRM_SCORE
    ]
    if (outcome == "matched") != bool(provider_matches):
        return _invalid_provider_result(request_count)

    accepted = next(
        (
            item
            for item in candidates
            if normalize_phone(item.get("phone"))
            and _int_value(item.get("confidence")) >= min_score
        ),
        None,
    )
    return {
        "outcome": "matched" if accepted else "no_match",
        "accepted": accepted,
        "safe_error_code": "",
        "request_count": request_count,
    }


def _release_kakao_no_match_holds(source_keys: list[str]) -> None:
    """Finalize only no-matches held by this clean collector run."""
    for source_key in dict.fromkeys(source_keys):
        updated = _patch_if_phone_empty(
            source_key,
            {
                "phone_enrichment_status": "no_match",
                "phone_enrichment_error": "",
            },
            expected_status="pending",
            expected_error=KAKAO_NO_MATCH_HELD,
        )
        if not updated:
            raise RuntimeError("LICENSE_PHONE_HELD_RELEASE_CONFLICT")


def _has_kakao_no_match_holds(*, database: CloudDatabase) -> bool:
    """Check for held work without reading contact or company values."""
    response = requests.get(
        database._url(TABLE_LICENSED_BUSINESSES),
        headers=database.headers,
        params={
            "select": "id",
            "phone": "eq.",
            "phone_enrichment_status": "eq.pending",
            "phone_enrichment_error": f"eq.{KAKAO_NO_MATCH_HELD}",
            "limit": "1",
        },
        timeout=database.config.timeout,
    )
    if not response.ok:
        raise RuntimeError("LICENSE_PHONE_HELD_CHECK_FAILED")
    rows = response.json() if response.text else []
    if not isinstance(rows, list):
        raise RuntimeError("LICENSE_PHONE_HELD_CHECK_INVALID")
    return bool(rows)


def _reset_kakao_no_match_holds(*, database: CloudDatabase) -> None:
    """Make held rows retryable only after explicit administrator approval."""
    headers = dict(database.headers)
    headers["Prefer"] = "return=minimal"
    response = requests.patch(
        database._url(TABLE_LICENSED_BUSINESSES),
        headers=headers,
        params={
            "phone": "eq.",
            "phone_enrichment_status": "eq.pending",
            "phone_enrichment_error": f"eq.{KAKAO_NO_MATCH_HELD}",
        },
        json={
            "phone_checked_at": None,
            "phone_enrichment_status": "pending",
            "phone_enrichment_error": "",
        },
        timeout=database.config.timeout,
    )
    if not response.ok:
        raise RuntimeError("LICENSE_PHONE_HELD_RESET_FAILED")


def _enrich_one(row: dict[str, Any], min_score: int) -> dict[str, Any]:
    source_key = str(row.get("source_key") or "")
    company_name = str(row.get("company_name") or "").strip()
    address = str(row.get("address") or "").strip()
    checked_at = _now()
    try:
        raw_result = kakao_local_client.search_company(company_name, address)
    except Exception:
        return _fatal_result(request_count=0, provider_error=True)

    result = _strict_kakao_result(raw_result, min_score)
    request_count = max(0, _int_value(result.get("request_count")))
    outcome = str(result.get("outcome") or "error")
    if outcome == "error":
        safe_code = kakao_provider_runtime.safe_error_code(
            result.get("safe_error_code") or "INVALID_JSON"
        )
        try:
            _patch_if_phone_empty(
                source_key,
                {
                    "phone_enrichment_status": "error",
                    "phone_checked_at": checked_at,
                    "phone_enrichment_error": safe_code,
                },
            )
        except Exception:
            fatal = _fatal_result(
                request_count=request_count,
                provider_error=True,
            )
            fatal["safe_error_code"] = safe_code
            return fatal
        return {
            "status": "error",
            "outcome": "error",
            "provider_error": True,
            "fatal": False,
            "safe_error_code": safe_code,
            "request_count": request_count,
        }

    accepted = result.get("accepted")
    if outcome == "no_match":
        try:
            _patch_if_phone_empty(
                source_key,
                {
                    "phone_enrichment_status": "pending",
                    "phone_checked_at": checked_at,
                    "phone_enrichment_error": KAKAO_NO_MATCH_HELD,
                },
            )
        except Exception:
            return _fatal_result(
                request_count=request_count,
                provider_error=False,
            )
        return {
            "status": "no_match",
            "outcome": "no_match",
            "provider_error": False,
            "fatal": False,
            "safe_error_code": "",
            "request_count": request_count,
            "source_key": source_key,
            "held": True,
        }

    if not isinstance(accepted, dict):
        return _fatal_result(
            request_count=request_count,
            provider_error=True,
        )
    phone = normalize_phone(accepted.get("phone"))
    try:
        saved = _patch_if_phone_empty(
            source_key,
            {
                "phone": phone,
                "phone_source": "kakao_local",
                "phone_source_url": str(accepted.get("source_url") or ""),
                "phone_confidence": _int_value(accepted.get("confidence")),
                "phone_enrichment_status": "matched",
                "phone_checked_at": checked_at,
                "phone_enrichment_error": "",
                "updated_at": checked_at,
            },
        )
    except Exception:
        return _fatal_result(
            request_count=request_count,
            provider_error=False,
        )
    return {
        "status": "matched" if saved else "skipped",
        "outcome": "matched",
        "provider_error": False,
        "fatal": False,
        "safe_error_code": "",
        "request_count": request_count,
    }


def _daily_safe_request_limit(value: int) -> int:
    return max(
        kakao_provider_runtime.DEFAULT_DAILY_SAFE_REQUESTS,
        min(int(value), kakao_provider_runtime.MAX_DAILY_SAFE_REQUESTS),
    )


def _run_with_lease(
    *,
    database: CloudDatabase,
    lease_token: str,
    workers: int,
    batch_size: int,
    retry_days: int,
    min_score: int,
    max_records: int,
    safe_limit: int,
    resume_generation: int | None = None,
) -> int:
    totals = {"matched": 0, "no_match": 0, "error": 0, "skipped": 0}
    processed = 0
    consecutive_provider_errors = 0
    exit_code = 0
    held_no_match_keys: list[str] = []
    first_outcomes: list[str] = []
    recent_outcomes: deque[str] = deque(
        maxlen=KAKAO_ROLLING_ZERO_MATCH_LIMIT
    )

    usage = kakao_provider_runtime.get_daily_usage(database=database)
    if (
        kakao_provider_runtime.is_quota_blocked(usage)
        or usage["request_count"] >= safe_limit
    ):
        print("kakao-quota status=blocked", flush=True)
        return 2

    connection = kakao_provider_runtime.test_connection_and_record(
        safe_limit=safe_limit,
        database=database,
    )
    print(
        "kakao-preflight "
        f"status={connection['category']} "
        f"code={connection['safe_error_code'] or 'OK'}",
        flush=True,
    )
    if not connection["ok"]:
        return 2

    if resume_generation is not None:
        try:
            _reset_kakao_no_match_holds(database=database)
            consumed = kakao_provider_runtime.consume_guard_resume(
                lease_token,
                resume_generation,
                database=database,
            )
        except Exception:
            print("kakao-guard status=resume-failed", flush=True)
            return 2
        if not _runtime_bool(consumed):
            print("kakao-guard status=resume-conflict", flush=True)
            return 2

    while max_records <= 0 or processed < max_records:
        usage = kakao_provider_runtime.get_daily_usage(database=database)
        if kakao_provider_runtime.is_quota_blocked(usage):
            print("kakao-quota status=blocked", flush=True)
            exit_code = 2
            break
        available_requests = safe_limit - usage["request_count"]
        if available_requests < 2:
            print("kakao-quota status=safe-limit", flush=True)
            break
        if not kakao_provider_runtime.renew_lease(
            lease_token,
            database=database,
        ):
            print("kakao-runtime status=lease-lost", flush=True)
            exit_code = 2
            break

        remaining = min(
            max(1, int(batch_size)),
            available_requests // 2,
            max(
                1,
                MAX_CONSECUTIVE_PROVIDER_ERRORS
                - consecutive_provider_errors,
            ),
        )
        if len(first_outcomes) < KAKAO_INITIAL_ZERO_MATCH_LIMIT:
            remaining = min(
                remaining,
                KAKAO_INITIAL_ZERO_MATCH_LIMIT - len(first_outcomes),
            )
        if len(recent_outcomes) < KAKAO_ROLLING_ZERO_MATCH_LIMIT:
            remaining = min(
                remaining,
                KAKAO_ROLLING_ZERO_MATCH_LIMIT - len(recent_outcomes),
            )
        if max_records > 0:
            remaining = min(remaining, max_records - processed)
        try:
            rows = _eligible_rows(remaining, retry_days)
        except Exception:
            print("phone-enrichment status=queue-read-failed", flush=True)
            exit_code = 2
            break
        if not rows:
            break

        reserved_requests = 2 * len(rows)
        try:
            reservation = kakao_provider_runtime.reserve_quota(
                reserved_requests,
                safe_limit,
                database=database,
            )
        except Exception:
            print("kakao-runtime status=quota-reservation-failed", flush=True)
            exit_code = 2
            break
        if not reservation.get("reserved"):
            if (
                kakao_provider_runtime.is_quota_blocked(reservation)
                or str(
                    reservation.get("last_safe_error_code") or ""
                ) == "HTTP_429"
            ):
                print("kakao-quota status=blocked", flush=True)
                exit_code = 2
            else:
                print("kakao-quota status=safe-limit", flush=True)
            break
        reservation_quota_date = str(
            reservation.get("quota_date") or ""
        )

        batch_requests = 0
        batch_safe_codes: list[str] = []
        batch_fatal = False
        batch_usage_uncertain = False
        lease_lost = False
        guard_reason = ""
        guard_observed_count = 0
        guard_matched_count = 0
        last_renewed = time.monotonic()
        with ThreadPoolExecutor(
            max_workers=max(1, min(6, workers))
        ) as executor:
            futures = [
                executor.submit(_enrich_one, row, min_score) for row in rows
            ]
            for future in as_completed(futures):
                if future.cancelled():
                    continue
                try:
                    result = future.result()
                except Exception:
                    batch_usage_uncertain = True
                    result = _fatal_result(
                        request_count=0,
                        provider_error=False,
                    )
                status = str(result.get("status") or "error")
                outcome = str(result.get("outcome") or status).lower()
                if outcome not in {"matched", "no_match", "error"}:
                    outcome = "error"
                    status = "error"
                    result = {
                        **result,
                        "provider_error": True,
                        "safe_error_code": "INVALID_JSON",
                    }
                totals[status] = totals.get(status, 0) + 1
                processed += 1
                batch_requests += max(
                    0,
                    _int_value(result.get("request_count")),
                )
                safe_code = str(result.get("safe_error_code") or "")
                if safe_code:
                    batch_safe_codes.append(
                        kakao_provider_runtime.safe_error_code(safe_code)
                    )
                if result.get("provider_error"):
                    consecutive_provider_errors += 1
                else:
                    consecutive_provider_errors = 0
                if len(first_outcomes) < KAKAO_INITIAL_ZERO_MATCH_LIMIT:
                    first_outcomes.append(outcome)
                recent_outcomes.append(outcome)

                if result.get("held"):
                    source_key = str(result.get("source_key") or "")
                    if source_key:
                        held_no_match_keys.append(source_key)
                batch_fatal = batch_fatal or bool(result.get("fatal"))
                if (
                    outcome == "matched"
                    and held_no_match_keys
                    and not guard_reason
                    and not batch_fatal
                ):
                    try:
                        _release_kakao_no_match_holds(held_no_match_keys)
                    except Exception:
                        batch_fatal = True
                    else:
                        held_no_match_keys.clear()

                if (
                    not guard_reason
                    and consecutive_provider_errors
                    >= MAX_CONSECUTIVE_PROVIDER_ERRORS
                ):
                    guard_reason = (
                        kakao_provider_runtime
                        .GUARD_REASON_CONSECUTIVE_PROVIDER_ERRORS
                    )
                    guard_observed_count = consecutive_provider_errors
                elif (
                    not guard_reason
                    and len(first_outcomes)
                    == KAKAO_INITIAL_ZERO_MATCH_LIMIT
                    and all(item == "no_match" for item in first_outcomes)
                ):
                    guard_reason = (
                        kakao_provider_runtime
                        .GUARD_REASON_INITIAL_ZERO_MATCH_RATE
                    )
                    guard_observed_count = len(first_outcomes)
                elif (
                    not guard_reason
                    and len(recent_outcomes)
                    == KAKAO_ROLLING_ZERO_MATCH_LIMIT
                    and not any(
                        item == "matched" for item in recent_outcomes
                    )
                ):
                    guard_reason = (
                        kakao_provider_runtime
                        .GUARD_REASON_ROLLING_ZERO_MATCH_RATE
                    )
                    guard_observed_count = len(recent_outcomes)
                if guard_reason:
                    guard_matched_count = 0
                stop_pending = (
                    safe_code == "HTTP_429"
                    or bool(guard_reason)
                    or batch_fatal
                )
                if (
                    time.monotonic() - last_renewed
                    >= LEASE_RENEW_INTERVAL_SECONDS
                ):
                    try:
                        renewed = kakao_provider_runtime.renew_lease(
                            lease_token,
                            database=database,
                        )
                    except Exception:
                        renewed = False
                    if not renewed:
                        lease_lost = True
                    last_renewed = time.monotonic()
                if stop_pending or lease_lost:
                    for pending in futures:
                        if pending is not future:
                            pending.cancel()

        usage_code = (
            "HTTP_429"
            if "HTTP_429" in batch_safe_codes
            else (batch_safe_codes[-1] if batch_safe_codes else "")
        )
        try:
            kakao_provider_runtime.reconcile_usage(
                reserved_requests,
                (
                    reserved_requests
                    if batch_usage_uncertain
                    else batch_requests
                ),
                usage_code,
                reservation_date=reservation_quota_date,
                database=database,
            )
        except Exception:
            print("kakao-runtime status=usage-write-failed", flush=True)
            exit_code = 2
            break

        if guard_reason:
            try:
                tripped = kakao_provider_runtime.trip_guard(
                    lease_token,
                    kakao_provider_runtime.new_lease_token(),
                    guard_reason,
                    SOURCE_JOB,
                    observed_count=guard_observed_count,
                    matched_count=guard_matched_count,
                    database=database,
                )
            except Exception:
                print("kakao-guard status=persistence-failed", flush=True)
                exit_code = 2
            else:
                if not _runtime_bool(tripped):
                    print(
                        "kakao-guard status=persistence-conflict",
                        flush=True,
                    )
                    exit_code = 2
                    break
                print(
                    f"kakao-guard status=blocked reason={guard_reason}",
                    flush=True,
                )
                exit_code = EXIT_PROVIDER_GUARD
            break

        print(
            f"phone-enrichment processed={processed} "
            f"matched={totals['matched']} "
            f"no_match={totals['no_match']} "
            f"errors={totals['error']} "
            f"requests={batch_requests}",
            flush=True,
        )
        if "HTTP_429" in batch_safe_codes:
            print("kakao-quota status=blocked", flush=True)
            exit_code = 2
            break
        if batch_fatal or lease_lost:
            print("kakao-runtime status=safe-stop", flush=True)
            exit_code = 2
            break
        time.sleep(0.2)

    if totals["error"] and exit_code == 0:
        exit_code = 2
    if held_no_match_keys and exit_code == 0:
        try:
            _release_kakao_no_match_holds(held_no_match_keys)
        except Exception:
            print("kakao-runtime status=held-release-failed", flush=True)
            exit_code = 2
    return exit_code


def run_enrichment(
    *,
    workers: int = 4,
    batch_size: int = 200,
    retry_days: int = 30,
    min_score: int = AUTO_CONFIRM_SCORE,
    max_records: int = 0,
    daily_safe_requests: int = (
        kakao_provider_runtime.DEFAULT_DAILY_SAFE_REQUESTS
    ),
) -> int:
    safe_limit = _daily_safe_request_limit(daily_safe_requests)
    lease_token = kakao_provider_runtime.new_lease_token()
    try:
        database = CloudDatabase()
        acquired = kakao_provider_runtime.acquire_lease(
            lease_token,
            database=database,
        )
    except Exception:
        print("kakao-runtime status=unavailable", flush=True)
        return 2
    if not acquired:
        print("kakao-runtime status=already-running", flush=True)
        return 2

    try:
        try:
            guard = kakao_provider_runtime.get_guard_state(
                database=database
            )
            if not isinstance(guard, dict):
                print("kakao-guard status=state-invalid", flush=True)
                return 2
            guard_state = str(guard.get("state") or "").strip().lower()
            if guard_state == kakao_provider_runtime.GUARD_STATE_BLOCKED:
                print(
                    "kakao-guard status=administrator-review-required",
                    flush=True,
                )
                return EXIT_PROVIDER_GUARD
            if guard_state not in {
                kakao_provider_runtime.GUARD_STATE_READY,
                kakao_provider_runtime.GUARD_STATE_RESUME_APPROVED,
            }:
                print("kakao-guard status=state-invalid", flush=True)
                return 2
            has_held_work = (
                _has_kakao_no_match_holds(database=database)
                if guard_state
                == kakao_provider_runtime.GUARD_STATE_READY
                else False
            )
            resume_generation = None
            if (
                guard_state
                == kakao_provider_runtime.GUARD_STATE_RESUME_APPROVED
            ):
                try:
                    resume_generation = int(
                        guard.get("guard_generation")
                    )
                except (TypeError, ValueError):
                    print(
                        "kakao-guard status=generation-invalid",
                        flush=True,
                    )
                    return 2
                if resume_generation < 1:
                    print(
                        "kakao-guard status=generation-invalid",
                        flush=True,
                    )
                    return 2
            if (
                guard_state == kakao_provider_runtime.GUARD_STATE_READY
                and has_held_work
            ):
                tripped = kakao_provider_runtime.trip_guard(
                    lease_token,
                    kakao_provider_runtime.new_lease_token(),
                    kakao_provider_runtime.GUARD_REASON_ORPHANED_HOLDS,
                    SOURCE_JOB,
                    observed_count=0,
                    matched_count=0,
                    database=database,
                )
                if not _runtime_bool(tripped):
                    print(
                        "kakao-guard status=persistence-conflict",
                        flush=True,
                    )
                    return 2
                print("kakao-guard status=orphaned-holds", flush=True)
                return EXIT_PROVIDER_GUARD
            return _run_with_lease(
                database=database,
                lease_token=lease_token,
                workers=workers,
                batch_size=batch_size,
                retry_days=retry_days,
                min_score=min_score,
                max_records=max_records,
                safe_limit=safe_limit,
                resume_generation=resume_generation,
            )
        except Exception:
            print("kakao-runtime status=safe-stop", flush=True)
            return 2
    finally:
        try:
            released = kakao_provider_runtime.release_lease(
                lease_token,
                database=database,
            )
        except Exception:
            released = False
        if not released:
            print("kakao-runtime status=lease-release-pending", flush=True)


def main() -> int:
    enabled = os.environ.get("OASIS_ENABLE_LOCALDATA", "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        print("license-phone-enrichment status=disabled", flush=True)
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("PHONE_ENRICHMENT_WORKERS", "4")),
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=int(os.environ.get("PHONE_ENRICHMENT_BATCH_SIZE", "200")),
    )
    parser.add_argument(
        "--retry-days",
        type=int,
        default=int(os.environ.get("PHONE_ENRICHMENT_RETRY_DAYS", "30")),
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=int(
            os.environ.get(
                "PHONE_ENRICHMENT_MIN_SCORE",
                str(AUTO_CONFIRM_SCORE),
            )
        ),
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=int(os.environ.get("PHONE_ENRICHMENT_MAX_RECORDS", "0")),
    )
    parser.add_argument(
        "--daily-safe-requests",
        type=int,
        default=int(
            os.environ.get(
                "KAKAO_DAILY_SAFE_REQUESTS",
                str(kakao_provider_runtime.DEFAULT_DAILY_SAFE_REQUESTS),
            )
        ),
    )
    args = parser.parse_args()
    return run_enrichment(
        workers=args.workers,
        batch_size=args.batch_size,
        retry_days=args.retry_days,
        min_score=max(AUTO_CONFIRM_SCORE, args.min_score),
        max_records=args.max_records,
        daily_safe_requests=args.daily_safe_requests,
    )


if __name__ == "__main__":
    sys.exit(main())
