from __future__ import annotations

from typing import Any, Callable

import localdata_contact_client
from licensed_business_repository import save_businesses, save_sync_run


ProgressCallback = Callable[[dict[str, Any]], None]


def sync_services(
    service_keys: list[str],
    *,
    max_pages_per_service: int = 1,
    rows_per_page: int = 100,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    keys = [
        key
        for key in dict.fromkeys(service_keys)
        if key in localdata_contact_client.SERVICES
    ]
    max_pages = min(100, max(1, int(max_pages_per_service)))
    rows = min(1000, max(1, int(rows_per_page)))
    stats = {
        "service_count": len(keys),
        "pages": 0,
        "received": 0,
        "saved": 0,
        "failed": 0,
        "failures": [],
    }
    for service_index, service_key in enumerate(keys, start=1):
        for page_no in range(1, max_pages + 1):
            result = localdata_contact_client.fetch_business_page(
                service_key,
                page_no=page_no,
                rows=rows,
            )
            stats["pages"] += 1
            received = len(result.get("items") or [])
            stats["received"] += received
            if result.get("ok"):
                saved = save_businesses(result.get("items") or [])
                stats["saved"] += saved
                status = "SUCCESS"
            else:
                saved = 0
                status = str(result.get("status") or "FAILED")
                stats["failed"] += 1
                stats["failures"].append(
                    {
                        "service_key": service_key,
                        "page_no": page_no,
                        "status": status,
                        "message": result.get("message", ""),
                    }
                )
            save_sync_run(
                service_key=service_key,
                page_no=page_no,
                received_count=received,
                saved_count=saved,
                status=status,
                message=str(result.get("message") or ""),
            )
            if progress:
                progress(
                    {
                        **stats,
                        "service_key": service_key,
                        "service_index": service_index,
                    }
                )
            if received < rows:
                break
    return stats
