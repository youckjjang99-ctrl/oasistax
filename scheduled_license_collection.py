from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

import localdata_contact_client
from cloud_db import CloudDatabase
from korea_regions import ALL_DISTRICTS, ALL_PROVINCES
from licensed_business_repository import save_businesses, save_sync_run


TABLE_RUNS = "oasis_license_collection_runs"
TABLE_PROGRESS = "oasis_license_collection_progress"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _monthly_run_key() -> str:
    return datetime.now(timezone.utc).strftime("monthly-%Y-%m")


def _upsert_run(run_key: str, values: dict[str, Any]) -> None:
    CloudDatabase().upsert(
        TABLE_RUNS,
        [{"run_key": run_key, **values, "updated_at": _now()}],
        on_conflict="run_key",
    )


def _progress_rows(run_key: str) -> dict[str, dict[str, Any]]:
    rows = CloudDatabase().select(
        TABLE_PROGRESS,
        filters={"run_key": run_key},
        columns=(
            "run_key,service_key,status,next_page,pages_processed,"
            "received_count,saved_count,last_error"
        ),
        limit=1000,
    )
    return {str(row.get("service_key") or ""): row for row in rows}


def _upsert_progress(run_key: str, service_key: str, **values: Any) -> None:
    CloudDatabase().upsert(
        TABLE_PROGRESS,
        [
            {
                "run_key": run_key,
                "service_key": service_key,
                **values,
                "updated_at": _now(),
            }
        ],
        on_conflict="run_key,service_key",
    )


def _collect_service(
    run_key: str,
    service_key: str,
    *,
    start_page: int,
    max_pages: int,
    rows_per_page: int,
) -> dict[str, Any]:
    pages = 0
    received_total = 0
    saved_total = 0
    _upsert_progress(
        run_key,
        service_key,
        status="running",
        next_page=start_page,
        last_error="",
    )
    for page_no in range(start_page, max_pages + 1):
        result = localdata_contact_client.fetch_business_page(
            service_key,
            page_no=page_no,
            rows=rows_per_page,
            province=ALL_PROVINCES,
            district=ALL_DISTRICTS,
            timeout=30,
        )
        pages += 1
        raw_received = int(
            result.get("raw_received_count")
            if result.get("raw_received_count") is not None
            else len(result.get("items") or [])
        )
        received = len(result.get("items") or [])
        if not result.get("ok"):
            message = str(result.get("message") or result.get("status") or "")
            _upsert_progress(
                run_key,
                service_key,
                status="failed",
                next_page=page_no,
                pages_processed=pages,
                received_count=received_total,
                saved_count=saved_total,
                last_error=message[:1000],
            )
            save_sync_run(
                service_key=service_key,
                page_no=page_no,
                received_count=0,
                saved_count=0,
                status=str(result.get("status") or "FAILED"),
                message=message,
                province=ALL_PROVINCES,
                district=ALL_DISTRICTS,
                sync_mode="full",
                window_end=_now(),
                is_complete=False,
            )
            return {
                "service_key": service_key,
                "status": "failed",
                "pages": pages,
                "received": received_total,
                "saved": saved_total,
                "error": message,
            }

        saved = save_businesses(result.get("items") or [])
        received_total += received
        saved_total += saved
        complete = raw_received < rows_per_page
        save_sync_run(
            service_key=service_key,
            page_no=page_no,
            received_count=received,
            saved_count=saved,
            status="SUCCESS",
            message=str(result.get("message") or ""),
            province=ALL_PROVINCES,
            district=ALL_DISTRICTS,
            sync_mode="full",
            window_end=_now(),
            is_complete=complete,
        )
        _upsert_progress(
            run_key,
            service_key,
            status="completed" if complete else "running",
            next_page=page_no + 1,
            pages_processed=pages,
            received_count=received_total,
            saved_count=saved_total,
            last_error="",
        )
        print(
            f"[{service_key}] page={page_no} received={received} "
            f"saved={saved} complete={complete}",
            flush=True,
        )
        if complete:
            return {
                "service_key": service_key,
                "status": "completed",
                "pages": pages,
                "received": received_total,
                "saved": saved_total,
                "error": "",
            }

    message = f"최대 {max_pages}페이지에 도달했습니다."
    _upsert_progress(
        run_key,
        service_key,
        status="failed",
        next_page=max_pages + 1,
        pages_processed=pages,
        received_count=received_total,
        saved_count=saved_total,
        last_error=message,
    )
    return {
        "service_key": service_key,
        "status": "failed",
        "pages": pages,
        "received": received_total,
        "saved": saved_total,
        "error": message,
    }


def run_collection(
    *,
    run_key: str,
    workers: int = 8,
    max_pages: int = 1000,
    rows_per_page: int = 1000,
) -> int:
    if not localdata_contact_client.key_status()["configured"]:
        raise RuntimeError("DATA_GO_KR_SERVICE_KEY가 설정되지 않았습니다.")
    services = list(localdata_contact_client.SERVICES)
    existing = _progress_rows(run_key)
    targets: list[tuple[str, int]] = []
    for service_key in services:
        current = existing.get(service_key, {})
        if current.get("status") == "completed":
            continue
        targets.append(
            (service_key, max(1, int(current.get("next_page") or 1)))
        )

    _upsert_run(
        run_key,
        {
            "status": "running",
            "total_services": len(services),
            "completed_services": len(services) - len(targets),
            "failed_services": 0,
            "started_at": _now(),
            "completed_at": None,
            "last_error": "",
        },
    )
    completed = len(services) - len(targets)
    failed = 0
    received = 0
    saved = 0
    with ThreadPoolExecutor(
        max_workers=max(1, min(12, int(workers)))
    ) as executor:
        futures = {
            executor.submit(
                _collect_service,
                run_key,
                service_key,
                start_page=start_page,
                max_pages=max_pages,
                rows_per_page=rows_per_page,
            ): service_key
            for service_key, start_page in targets
        }
        for future in as_completed(futures):
            service_key = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "service_key": service_key,
                    "status": "failed",
                    "received": 0,
                    "saved": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                _upsert_progress(
                    run_key,
                    service_key,
                    status="failed",
                    next_page=1,
                    last_error=result["error"][:1000],
                )
            if result["status"] == "completed":
                completed += 1
            else:
                failed += 1
            received += int(result.get("received") or 0)
            saved += int(result.get("saved") or 0)
            _upsert_run(
                run_key,
                {
                    "status": "running",
                    "total_services": len(services),
                    "completed_services": completed,
                    "failed_services": failed,
                    "received_count": received,
                    "saved_count": saved,
                    "heartbeat_at": _now(),
                    "last_error": str(result.get("error") or "")[:1000],
                },
            )
            print(
                f"progress {completed + failed}/{len(services)} "
                f"completed={completed} failed={failed} saved={saved}",
                flush=True,
            )

    final_status = "completed" if failed == 0 else "partial"
    _upsert_run(
        run_key,
        {
            "status": final_status,
            "total_services": len(services),
            "completed_services": completed,
            "failed_services": failed,
            "received_count": received,
            "saved_count": saved,
            "heartbeat_at": _now(),
            "completed_at": _now(),
        },
    )
    return 0 if failed == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-key",
        default=os.environ.get("LICENSE_COLLECTION_RUN_KEY") or _monthly_run_key(),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("LICENSE_COLLECTION_WORKERS", "8")),
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=int(os.environ.get("LICENSE_COLLECTION_MAX_PAGES", "1000")),
    )
    args = parser.parse_args()
    return run_collection(
        run_key=args.run_key,
        workers=args.workers,
        max_pages=args.max_pages,
    )


if __name__ == "__main__":
    sys.exit(main())
