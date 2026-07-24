from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import localdata_contact_client
from korea_regions import ALL_DISTRICTS, ALL_PROVINCES
from licensed_business_repository import (
    latest_sync_watermark,
    save_businesses,
    save_sync_run,
)


ProgressCallback = Callable[[dict[str, Any]], None]


def _parse_timestamp(value: str) -> datetime | None:
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


def _api_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def sync_services(
    service_keys: list[str],
    *,
    max_pages_per_service: int = 1,
    rows_per_page: int = 100,
    province: str = ALL_PROVINCES,
    district: str = ALL_DISTRICTS,
    sync_mode: str = "full",
    workers: int = 8,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    keys = [
        key
        for key in dict.fromkeys(service_keys)
        if key in localdata_contact_client.SERVICES
    ]
    max_pages = min(100, max(1, int(max_pages_per_service)))
    rows = min(1000, max(1, int(rows_per_page)))
    requested_mode = (
        "incremental" if str(sync_mode).lower() == "incremental" else "full"
    )
    run_window_end = datetime.now(timezone.utc)
    run_window_end_iso = run_window_end.isoformat()
    stats = {
        "sync_mode": requested_mode,
        "service_count": len(keys),
        "pages": 0,
        "raw_received": 0,
        "received": 0,
        "region_filtered": 0,
        "saved": 0,
        "failed": 0,
        "full_fallback_services": 0,
        "complete_services": 0,
        "incomplete_services": 0,
        "failures": [],
    }
    def sync_one(service_key: str) -> dict[str, Any]:
        service_stats: dict[str, Any] = {
            "service_key": service_key,
            "pages": 0,
            "raw_received": 0,
            "received": 0,
            "region_filtered": 0,
            "saved": 0,
            "failed": 0,
            "full_fallback_services": 0,
            "complete_services": 0,
            "incomplete_services": 0,
            "failures": [],
        }
        service_mode = requested_mode
        window_start_iso = ""
        updated_since = ""
        updated_before = ""
        if requested_mode == "incremental":
            watermark = latest_sync_watermark(
                service_key=service_key,
                province=province,
                district=district,
            )
            parsed_watermark = _parse_timestamp(watermark)
            if parsed_watermark:
                # 경계 시각의 수정 건을 놓치지 않도록 5분을 겹쳐 조회한다.
                window_start = parsed_watermark - timedelta(minutes=5)
                window_start_iso = window_start.isoformat()
                updated_since = _api_timestamp(window_start)
                updated_before = _api_timestamp(run_window_end)
            else:
                # 해당 지역·업종의 기준 데이터가 없으면 전수 수집부터 수행한다.
                service_mode = "full"
                service_stats["full_fallback_services"] += 1
        service_stats["service_mode"] = service_mode
        service_complete = False
        for page_no in range(1, max_pages + 1):
            try:
                result = localdata_contact_client.fetch_business_page(
                    service_key,
                    page_no=page_no,
                    rows=rows,
                    province=province,
                    district=district,
                    updated_since=updated_since,
                    updated_before=updated_before,
                )
            except Exception as exc:
                result = {
                    "ok": False,
                    "status": type(exc).__name__,
                    "message": str(exc),
                    "items": [],
                    "raw_received_count": 0,
                }
            service_stats["pages"] += 1
            raw_received = int(
                result.get("raw_received_count")
                if result.get("raw_received_count") is not None
                else len(result.get("items") or [])
            )
            received = len(result.get("items") or [])
            service_stats["raw_received"] += raw_received
            service_stats["received"] += received
            service_stats["region_filtered"] += max(0, raw_received - received)
            page_complete = bool(result.get("ok")) and raw_received < rows
            service_complete = page_complete
            if result.get("ok"):
                try:
                    saved = save_businesses(result.get("items") or [])
                    service_stats["saved"] += saved
                    status = "SUCCESS"
                except Exception as exc:
                    saved = 0
                    status = type(exc).__name__
                    result["ok"] = False
                    page_complete = False
                    service_complete = False
                    result["message"] = f"Supabase 저장 실패: {exc}"
                    service_stats["failed"] += 1
                    service_stats["failures"].append(
                        {
                            "service_key": service_key,
                            "page_no": page_no,
                            "status": status,
                            "message": result["message"],
                        }
                    )
            else:
                saved = 0
                status = str(result.get("status") or "FAILED")
                service_stats["failed"] += 1
                service_stats["failures"].append(
                    {
                        "service_key": service_key,
                        "page_no": page_no,
                        "status": status,
                        "message": result.get("message", ""),
                    }
                )
            try:
                save_sync_run(
                    service_key=service_key,
                    page_no=page_no,
                    received_count=received,
                    saved_count=saved,
                    status=status,
                    message=str(result.get("message") or ""),
                    province=province,
                    district=district,
                    sync_mode=service_mode,
                    window_start=window_start_iso,
                    window_end=run_window_end_iso,
                    is_complete=page_complete,
                )
            except Exception as exc:
                service_stats["failures"].append(
                    {
                        "service_key": service_key,
                        "page_no": page_no,
                        "status": type(exc).__name__,
                        "message": f"수집이력 저장 실패: {exc}",
                    }
                )
            # 지역 후처리로 저장 건수가 0이어도 원본 페이지가 가득 찼다면
            # 다음 페이지를 계속 조회해야 누락이 생기지 않는다.
            if not result.get("ok") or raw_received < rows:
                break
        if service_complete:
            service_stats["complete_services"] += 1
        else:
            service_stats["incomplete_services"] += 1
        return service_stats

    worker_count = max(1, min(12, int(workers), len(keys) or 1))
    completed = 0
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(sync_one, service_key): service_key
            for service_key in keys
        }
        for future in as_completed(future_map):
            service_key = future_map[future]
            try:
                service_stats = future.result()
            except Exception as exc:
                service_stats = {
                    "service_key": service_key,
                    "pages": 0,
                    "raw_received": 0,
                    "received": 0,
                    "region_filtered": 0,
                    "saved": 0,
                    "failed": 1,
                    "full_fallback_services": 0,
                    "complete_services": 0,
                    "incomplete_services": 1,
                    "failures": [
                        {
                            "service_key": service_key,
                            "page_no": 1,
                            "status": type(exc).__name__,
                            "message": str(exc),
                        }
                    ],
                    "service_mode": requested_mode,
                }
            for field in (
                "pages",
                "raw_received",
                "received",
                "region_filtered",
                "saved",
                "failed",
                "full_fallback_services",
                "complete_services",
                "incomplete_services",
            ):
                stats[field] += int(service_stats.get(field) or 0)
            stats["failures"].extend(service_stats.get("failures") or [])
            completed += 1
            if progress:
                progress(
                    {
                        **stats,
                        "service_key": service_key,
                        "service_index": completed,
                        "service_mode": service_stats.get(
                            "service_mode", requested_mode
                        ),
                    }
                )
    return stats
