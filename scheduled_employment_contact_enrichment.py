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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _select_rows(
    *,
    status: str,
    limit: int,
    due_before: str | None = None,
    updated_before: str | None = None,
) -> list[dict[str, Any]]:
    db = CloudDatabase()
    params: dict[str, str] = {
        "select": (
            "contact_key,source_type,source_record_key,business_no,"
            "company_name,address,industry_name,status,attempt_count,"
            "mobile_phone,landline_phone,email,instagram_id,instagram_url"
        ),
        "status": f"eq.{status}",
        "limit": str(max(1, min(5000, int(limit)))),
    }
    if due_before:
        params["next_check_at"] = f"lte.{due_before}"
        params["order"] = "next_check_at.asc,updated_at.asc"
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


def _eligible_rows(limit: int) -> list[dict[str, Any]]:
    """월간 재검증 대상을 먼저 처리한 뒤 신규 대상을 채운다."""
    limit = max(1, min(5000, int(limit)))
    now = _now()
    due_before = _iso(now)
    stale_processing_before = _iso(now - timedelta(days=1))
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    queues = (
        ("matched", due_before, None),
        ("error", due_before, None),
        ("processing", None, stale_processing_before),
        ("pending", None, None),
        ("no_match", due_before, None),
    )
    for status, due, updated in queues:
        remaining = limit - len(selected)
        if remaining <= 0:
            break
        for row in _select_rows(
            status=status,
            limit=remaining,
            due_before=due,
            updated_before=updated,
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
) -> bool:
    db = CloudDatabase()
    headers = dict(db.headers)
    headers["Prefer"] = "return=representation"
    params = {"contact_key": f"eq.{contact_key}"}
    if expected_status:
        params["status"] = f"eq.{expected_status}"
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


def _claim(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "pending")
    return _patch(
        str(row.get("contact_key") or ""),
        {
            "status": "processing",
            "last_error": "",
            "updated_at": _iso(_now()),
        },
        expected_status=status,
    )


def _accepted_contacts(result: dict[str, Any]) -> list[dict[str, Any]]:
    accepted: list[dict[str, Any]] = []
    for raw in result.get("contacts") or []:
        row = dict(raw)
        contact_type = str(row.get("contact_type") or "")
        if contact_type not in CONTACT_TYPES:
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


def _enrich_one(row: dict[str, Any]) -> dict[str, Any]:
    contact_key = str(row.get("contact_key") or "")
    if not _claim(row):
        return {"status": "skipped", "contact_key": contact_key}

    checked_at = _now()
    attempt_count = int(row.get("attempt_count") or 0) + 1
    try:
        result = enrich_company(
            {
                "company_name": row.get("company_name"),
                "address": row.get("address"),
                "business_no": row.get("business_no"),
                "industry_name": row.get("industry_name"),
            },
            skip_localdata=True,
            bulk_mode=True,
            website_timeout=6,
        )
        if not result.get("ok"):
            raise RuntimeError(
                str(result.get("message") or result.get("status") or "")
            )
        accepted = _accepted_contacts(result)
        mobile = _best(accepted, "phone", mobile=True)
        landline = _best(accepted, "phone", mobile=False)
        email = _best(accepted, "email")
        instagram = _best(accepted, "instagram")
        matched = any((mobile, landline, email, instagram))
        next_check = checked_at + timedelta(days=30 if matched else 90)
        source_rows = {
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
        }
        _patch(
            contact_key,
            {
                "mobile_phone": (
                    normalize_phone(mobile.get("contact_value"))
                    if mobile
                    else ""
                ),
                "landline_phone": (
                    normalize_phone(landline.get("contact_value"))
                    if landline
                    else ""
                ),
                "email": str(
                    email.get("contact_value") if email else ""
                ),
                "instagram_id": str(
                    instagram.get("contact_value") if instagram else ""
                ),
                "instagram_url": str(
                    instagram.get("source_url") if instagram else ""
                ),
                "contact_sources": source_rows,
                "status": "matched" if matched else "no_match",
                "checked_at": _iso(checked_at),
                "next_check_at": _iso(next_check),
                "attempt_count": attempt_count,
                "last_error": "",
            },
            expected_status="processing",
        )
        return {
            "status": "matched" if matched else "no_match",
            "contact_key": contact_key,
        }
    except Exception as exc:
        _patch(
            contact_key,
            {
                "status": "error",
                "checked_at": _iso(checked_at),
                "next_check_at": _iso(checked_at + timedelta(days=1)),
                "attempt_count": attempt_count,
                "last_error": f"{type(exc).__name__}: {exc}"[:1000],
            },
            expected_status="processing",
        )
        return {"status": "error", "contact_key": contact_key}


def run_enrichment(
    *,
    workers: int = 4,
    batch_size: int = 200,
    max_records: int = 3000,
) -> int:
    if not (
        kakao_local_client.key_status()["configured"]
        or naver_web_search_client.key_status()["configured"]
    ):
        raise RuntimeError(
            "KAKAO_REST_API_KEY 또는 네이버 검색 API 키가 필요합니다."
        )

    workers = max(1, min(6, int(workers)))
    batch_size = max(1, min(1000, int(batch_size)))
    max_records = max(1, min(20000, int(max_records)))
    totals = {
        "matched": 0,
        "no_match": 0,
        "error": 0,
        "skipped": 0,
    }
    processed = 0
    while processed < max_records:
        rows = _eligible_rows(min(batch_size, max_records - processed))
        if not rows:
            break
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_enrich_one, row) for row in rows]
            for future in as_completed(futures):
                result = future.result()
                status = str(result.get("status") or "error")
                totals[status] = totals.get(status, 0) + 1
                processed += 1
        print(
            json.dumps(
                {
                    "job": "employment-contact-enrichment",
                    "processed": processed,
                    **totals,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(0.2)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("EMPLOYMENT_CONTACT_WORKERS", "4")),
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
            os.environ.get("EMPLOYMENT_CONTACT_MAX_RECORDS", "3000")
        ),
    )
    args = parser.parse_args()
    return run_enrichment(
        workers=args.workers,
        batch_size=args.batch_size,
        max_records=args.max_records,
    )


if __name__ == "__main__":
    sys.exit(main())
