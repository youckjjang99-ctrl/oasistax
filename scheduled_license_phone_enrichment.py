from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

import kakao_local_client
from cloud_db import CloudDatabase
from contact_enrichment import AUTO_CONFIRM_SCORE
from contact_matching import normalize_phone
from licensed_business_repository import TABLE_LICENSED_BUSINESSES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eligible_rows(limit: int, retry_days: int) -> list[dict[str, Any]]:
    db = CloudDatabase()
    retry_before = (
        datetime.now(timezone.utc) - timedelta(days=max(1, retry_days))
    ).isoformat()
    response = requests.get(
        db._url(TABLE_LICENSED_BUSINESSES),
        headers=db.headers,
        params={
            "select": "id,source_key,company_name,address",
            "and": (
                "(or(phone.eq.,phone.is.null),"
                "or(phone_enrichment_status.eq.pending,"
                f"phone_checked_at.lt.{retry_before}))"
            ),
            "company_name": "neq.",
            "address": "neq.",
            "order": "phone_checked_at.asc.nullsfirst,created_at.asc",
            "limit": str(max(1, min(1000, limit))),
        },
        timeout=db.config.timeout,
    )
    if not response.ok:
        raise RuntimeError(
            "전화번호 보강 대상 조회 실패 "
            f"HTTP {response.status_code}: {response.text[:500]}"
        )
    data = response.json() if response.text else []
    return data if isinstance(data, list) else []


def _patch_if_phone_empty(
    source_key: str,
    values: dict[str, Any],
) -> bool:
    db = CloudDatabase()
    headers = dict(db.headers)
    headers["Prefer"] = "return=representation"
    response = requests.patch(
        db._url(TABLE_LICENSED_BUSINESSES),
        headers=headers,
        params={"source_key": f"eq.{source_key}", "phone": "eq."},
        json=values,
        timeout=db.config.timeout,
    )
    if not response.ok:
        raise RuntimeError(
            "전화번호 저장 실패 "
            f"HTTP {response.status_code}: {response.text[:500]}"
        )
    rows = response.json() if response.text else []
    return bool(rows)


def _enrich_one(row: dict[str, Any], min_score: int) -> dict[str, Any]:
    source_key = str(row.get("source_key") or "")
    company_name = str(row.get("company_name") or "").strip()
    address = str(row.get("address") or "").strip()
    checked_at = _now()
    result = kakao_local_client.search_company(company_name, address)
    if not result.get("ok"):
        _patch_if_phone_empty(
            source_key,
            {
                "phone_enrichment_status": "error",
                "phone_checked_at": checked_at,
                "phone_enrichment_error": str(
                    result.get("message") or result.get("status") or ""
                )[:500],
            },
        )
        return {"status": "error", "source_key": source_key}

    accepted = next(
        (
            item
            for item in (result.get("candidates") or [])
            if normalize_phone(item.get("phone"))
            and int(item.get("confidence") or 0) >= min_score
        ),
        None,
    )
    if not accepted:
        _patch_if_phone_empty(
            source_key,
            {
                "phone_enrichment_status": "no_match",
                "phone_checked_at": checked_at,
                "phone_enrichment_error": "",
            },
        )
        return {"status": "no_match", "source_key": source_key}

    phone = normalize_phone(accepted.get("phone"))
    saved = _patch_if_phone_empty(
        source_key,
        {
            "phone": phone,
            "phone_source": "kakao_local",
            "phone_source_url": str(accepted.get("source_url") or ""),
            "phone_confidence": int(accepted.get("confidence") or 0),
            "phone_enrichment_status": "matched",
            "phone_checked_at": checked_at,
            "phone_enrichment_error": "",
            "updated_at": checked_at,
        },
    )
    return {
        "status": "matched" if saved else "skipped",
        "source_key": source_key,
    }


def run_enrichment(
    *,
    workers: int = 4,
    batch_size: int = 200,
    retry_days: int = 30,
    min_score: int = AUTO_CONFIRM_SCORE,
    max_records: int = 0,
) -> int:
    if not kakao_local_client.key_status()["configured"]:
        raise RuntimeError("KAKAO_REST_API_KEY가 설정되지 않았습니다.")

    totals = {"matched": 0, "no_match": 0, "error": 0, "skipped": 0}
    processed = 0
    while max_records <= 0 or processed < max_records:
        remaining = batch_size
        if max_records > 0:
            remaining = min(remaining, max_records - processed)
        rows = _eligible_rows(remaining, retry_days)
        if not rows:
            break
        with ThreadPoolExecutor(
            max_workers=max(1, min(6, workers))
        ) as executor:
            futures = [
                executor.submit(_enrich_one, row, min_score) for row in rows
            ]
            for future in as_completed(futures):
                result = future.result()
                status = str(result.get("status") or "error")
                totals[status] = totals.get(status, 0) + 1
                processed += 1
        print(
            f"phone-enrichment processed={processed} "
            f"matched={totals['matched']} no_match={totals['no_match']} "
            f"errors={totals['error']}",
            flush=True,
        )
        time.sleep(0.2)
    return 0 if totals["error"] == 0 else 2


def main() -> int:
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
                "PHONE_ENRICHMENT_MIN_SCORE", str(AUTO_CONFIRM_SCORE)
            )
        ),
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=int(os.environ.get("PHONE_ENRICHMENT_MAX_RECORDS", "0")),
    )
    args = parser.parse_args()
    return run_enrichment(
        workers=args.workers,
        batch_size=args.batch_size,
        retry_days=args.retry_days,
        min_score=max(AUTO_CONFIRM_SCORE, args.min_score),
        max_records=args.max_records,
    )


if __name__ == "__main__":
    sys.exit(main())
