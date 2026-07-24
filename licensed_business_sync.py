from __future__ import annotations

from typing import Any, Callable

import localdata_contact_client
from korea_regions import ALL_DISTRICTS, ALL_PROVINCES
from licensed_business_repository import save_businesses, save_sync_run


ProgressCallback = Callable[[dict[str, Any]], None]


def sync_services(
    service_keys: list[str],
    *,
    max_pages_per_service: int = 1,
    rows_per_page: int = 100,
    province: str = ALL_PROVINCES,
    district: str = ALL_DISTRICTS,
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
        "raw_received": 0,
        "received": 0,
        "region_filtered": 0,
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
                province=province,
                district=district,
            )
            stats["pages"] += 1
            raw_received = int(
                result.get("raw_received_count")
                if result.get("raw_received_count") is not None
                else len(result.get("items") or [])
            )
            received = len(result.get("items") or [])
            stats["raw_received"] += raw_received
            stats["received"] += received
            stats["region_filtered"] += max(0, raw_received - received)
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
                province=province,
                district=district,
            )
            if progress:
                progress(
                    {
                        **stats,
                        "service_key": service_key,
                        "service_index": service_index,
                    }
                )
            # 지역 후처리로 저장 건수가 0이어도 원본 페이지가 가득 찼다면
            # 다음 페이지를 계속 조회해야 누락이 생기지 않는다.
            if raw_received < rows:
                break
    return stats
