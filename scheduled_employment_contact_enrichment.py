from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import kakao_local_client
import naver_web_search_client
from cloud_db import CloudDatabase
from contact_enrichment import AUTO_CONFIRM_SCORE, enrich_company
from contact_matching import is_mobile_phone, normalize_phone


TABLE_CONTACTS = "oasis_employment_contacts"
CONTACT_TYPES = {"phone", "email", "instagram"}
CONTACT_STAGES = {"phone", "digital"}
PHONE_PROVIDERS = {"auto", "kakao", "naver"}
PHONE_PROVIDER_FIELD = "phone_provider_stage"
KAKAO_DAILY_SAFE_RECORDS = 90000
NAVER_DAILY_SAFE_RECORDS = 12000
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
            f"HTTP {response.status_code}: {response.text[:500]}"
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


def _patch_legacy(
    contact_key: str,
    values: dict[str, Any],
    *,
    expected_status: str | None = None,
    status_field: str = "status",
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
    response = requests.patch(
        db._url(TABLE_CONTACTS),
        headers=headers,
        params=params,
        data=json.dumps(values, ensure_ascii=False, default=str),
        timeout=max(30, db.config.timeout),
    )
    if not response.ok:
        raise RuntimeError(
            "고용기업 연락처 저장 실패 "
            f"HTTP {response.status_code}: {response.text[:500]}"
        )
    rows = response.json() if response.text else []
    return bool(rows)


def _patch(
    contact_key: str,
    values: dict[str, Any],
    *,
    expected_status: str | None = None,
    status_field: str = "status",
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
            last_error = (
                f"HTTP {response.status_code}: {response.text[:500]}"
            )
            retryable = (
                response.status_code in {429, 500, 502, 503, 504}
                or "57014" in response.text
                or "statement timeout" in response.text.lower()
            )
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
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


def _claim(row: dict[str, Any], stage: str) -> bool:
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
) -> dict[str, Any]:
    if stage not in CONTACT_STAGES:
        raise ValueError("stage must be phone or digital")
    if stage == "phone" and phone_provider not in {"kakao", "naver"}:
        raise ValueError("phone_provider must be kakao or naver")
    fields = STAGE_FIELDS[stage]
    status_field = fields["status"]
    contact_key = str(row.get("contact_key") or "")
    if not _claim(row, stage):
        return {"status": "skipped", "contact_key": contact_key}

    checked_at = _now()
    attempt_count = int(row.get("attempt_count") or 0) + 1
    stage_attempt_count = int(row.get(fields["attempt_count"]) or 0) + 1
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
            bulk_mode=stage == "phone",
            contact_stage=stage,
            website_timeout=6 if stage == "phone" else 8,
            max_website_candidates=2,
            website_max_pages=2,
        )
        if not result.get("ok"):
            raise RuntimeError(
                str(result.get("message") or result.get("status") or "")
            )
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
        phase_matched = (
            any((mobile_phone, landline_phone))
            if stage == "phone"
            else any((email_value, instagram_id, instagram_url))
        )
        kakao_fallback = (
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
            if kakao_fallback
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
                if kakao_fallback
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
                if kakao_fallback
                else "no_match"
            ),
            fields["checked_at"]: _iso(checked_at),
            fields["next_check_at"]: _iso(next_check),
            fields["attempt_count"]: stage_attempt_count,
            fields["last_error"]: "",
        }
        if stage == "phone":
            values[PHONE_PROVIDER_FIELD] = (
                "complete"
                if phase_matched or phone_provider == "naver"
                else "naver"
            )
        _patch(
            contact_key,
            values,
            expected_status="processing",
            status_field=status_field,
        )
        return {
            "status": (
                "matched"
                if phase_matched
                else "fallback"
                if kakao_fallback
                else "no_match"
            ),
            "contact_key": contact_key,
            "stage": stage,
            "provider": phone_provider if stage == "phone" else "",
        }
    except Exception as exc:
        is_limit = isinstance(exc, UpstreamLimitError)
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
                "next_check_at": _iso(checked_at + timedelta(days=1)),
                "attempt_count": attempt_count,
                "last_error": f"{type(exc).__name__}: {exc}"[:1000],
                status_field: "error",
                fields["checked_at"]: _iso(checked_at),
                fields["next_check_at"]: _iso(
                    checked_at + timedelta(days=1)
                ),
                fields["attempt_count"]: stage_attempt_count,
                fields["last_error"]: (
                    f"{type(exc).__name__}: {exc}"[:1000]
                ),
                **(
                    {PHONE_PROVIDER_FIELD: phone_provider}
                    if stage == "phone"
                    else {}
                ),
            },
            expected_status="processing",
            status_field=status_field,
        )
        return {
            "status": "error",
            "contact_key": contact_key,
            "stage": stage,
            "provider": phone_provider if stage == "phone" else "",
            "halt": is_limit,
        }


def run_enrichment(
    *,
    stage: str = "phone",
    phone_provider: str = "auto",
    workers: int = 0,
    batch_size: int = 200,
    max_records: int = 0,
) -> int:
    if stage not in CONTACT_STAGES:
        raise ValueError("stage must be phone or digital")
    phone_provider = str(phone_provider or "auto").strip().lower()
    if phone_provider not in PHONE_PROVIDERS:
        raise ValueError("phone_provider must be auto, kakao, or naver")
    print(
        json.dumps(
            {
                "job": f"employment-{stage}-enrichment",
                "status": "selecting_provider",
                "requested_provider": phone_provider,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if stage == "phone" and phone_provider == "auto":
        phone_provider = (
            "kakao"
            if _eligible_rows(1, "phone", "kakao")
            else "naver"
        )
    if stage == "digital":
        phone_provider = "auto"
    kakao_configured = kakao_local_client.key_status()["configured"]
    naver_configured = naver_web_search_client.key_status()["configured"]
    if (
        stage == "phone"
        and phone_provider == "kakao"
        and not kakao_configured
    ):
        raise RuntimeError(
            "카카오 전화번호 선조회에는 KAKAO_REST_API_KEY가 필요합니다."
        )
    if (
        stage == "phone"
        and phone_provider == "naver"
        and not naver_configured
    ):
        raise RuntimeError(
            "네이버 전화번호 후조회에는 네이버 검색 API 키가 필요합니다."
        )
    if stage == "digital" and not naver_configured:
        raise RuntimeError(
            "이메일·인스타그램 수집에는 네이버 검색 API 키가 필요합니다."
        )

    if int(workers) <= 0:
        workers = 12
    workers = max(1, min(20, int(workers)))
    batch_size = max(1, min(1000, int(batch_size)))
    if int(max_records) <= 0:
        max_records = (
            KAKAO_DAILY_SAFE_RECORDS
            if phone_provider == "kakao"
            else NAVER_DAILY_SAFE_RECORDS
        )
    provider_cap = (
        KAKAO_DAILY_SAFE_RECORDS
        if phone_provider == "kakao"
        else 25000
    )
    max_records = max(1, min(provider_cap, int(max_records)))
    print(
        json.dumps(
            {
                "job": f"employment-{stage}-enrichment",
                "status": "started",
                "provider": phone_provider if stage == "phone" else "",
                "workers": workers,
                "batch_size": batch_size,
                "max_records": max_records,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    totals = {
        "matched": 0,
        "fallback": 0,
        "no_match": 0,
        "error": 0,
        "skipped": 0,
    }
    processed = 0
    halted = False
    while processed < max_records:
        rows = _eligible_rows(
            min(batch_size, max_records - processed),
            stage,
            phone_provider if stage == "phone" else None,
        )
        if not rows:
            break
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _enrich_one,
                    row,
                    stage,
                    phone_provider if stage == "phone" else None,
                )
                for row in rows
            ]
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    totals["error"] += 1
                    processed += 1
                    print(
                        json.dumps(
                            {
                                "job": f"employment-{stage}-enrichment",
                                "stage": stage,
                                "provider": (
                                    phone_provider
                                    if stage == "phone"
                                    else ""
                                ),
                                "status": "row_error",
                                "error": (
                                    f"{type(exc).__name__}: {exc}"[:500]
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    continue
                status = str(result.get("status") or "error")
                totals[status] = totals.get(status, 0) + 1
                halted = halted or bool(result.get("halt"))
                processed += 1
        print(
            json.dumps(
                {
                    "job": f"employment-{stage}-enrichment",
                    "stage": stage,
                    "provider": (
                        phone_provider if stage == "phone" else ""
                    ),
                    "processed": processed,
                    "halted": halted,
                    **totals,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if halted:
            print(
                json.dumps(
                    {
                        "job": f"employment-{stage}-enrichment",
                        "provider": (
                            phone_provider if stage == "phone" else ""
                        ),
                        "status": "paused_by_provider_limit",
                        "processed": processed,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            break
        time.sleep(0.2)
    return 0


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
    args = parser.parse_args()
    return run_enrichment(
        stage=args.stage,
        phone_provider=args.phone_provider,
        workers=args.workers,
        batch_size=args.batch_size,
        max_records=args.max_records,
    )


if __name__ == "__main__":
    sys.exit(main())
