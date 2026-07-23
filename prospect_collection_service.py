from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from contact_matching import normalize_phone
from prospect_db_repository import (
    existing_prospect_identities,
    remove_existing_customers,
    remove_existing_prospects,
)
from public_data_api import fetch_nps_workplaces
from sales_intelligence import analyze_sales_candidate, merge_analysis


ProgressCallback = Callable[[dict[str, Any]], None]


def _net_growth(item: dict[str, Any]) -> int:
    return int(item.get("신규취득자수") or 0) - int(
        item.get("상실가입자수") or 0
    )


def _growth_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    return (
        _net_growth(item),
        int(item.get("신규취득자수") or 0),
        int(item.get("가입자수") or 0),
    )


def _notify(
    callback: ProgressCallback | None,
    **payload: Any,
) -> None:
    if callback:
        callback(payload)


def _analyze_parallel(
    items: list[dict[str, Any]],
    *,
    contact_mode: str,
    workers: int = 6,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not items:
        return [], []
    analyzed: list[tuple[int, dict[str, Any]]] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(
        max_workers=min(max(1, workers), len(items))
    ) as executor:
        future_map = {
            executor.submit(
                analyze_sales_candidate,
                item,
                contact_mode=contact_mode,
            ): (index, item)
            for index, item in enumerate(items)
        }
        for future in as_completed(future_map):
            index, item = future_map[future]
            try:
                analysis = future.result()
                merged = merge_analysis(item, analysis)
                normalized = normalize_phone(merged.get("대표전화"))
                merged["대표전화"] = normalized
                if normalized:
                    analyzed.append((index, merged))
            except Exception as exc:
                failures.append(
                    {
                        "사업장명": item.get("사업장명", ""),
                        "단계": contact_mode,
                        "실패사유": f"{type(exc).__name__}: {exc}",
                    }
                )
    analyzed.sort(key=lambda row: row[0])
    return [row for _index, row in analyzed], failures


def _find_contactable(
    items: list[dict[str, Any]],
    *,
    needed: int,
    progress: ProgressCallback | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if needed <= 0 or not items:
        return [], [], 0

    ordered = sorted(items, key=_growth_sort_key, reverse=True)
    quick_found, quick_failures = _analyze_parallel(
        ordered,
        contact_mode="quick",
    )
    selected = quick_found[:needed]
    selected_keys = {
        str(row.get("source_key") or "") for row in quick_found
    }
    _notify(
        progress,
        stage="quick_contact",
        checked=len(ordered),
        found=len(quick_found),
    )

    remaining_needed = needed - len(selected)
    full_checked = 0
    failures = list(quick_failures)
    if remaining_needed > 0:
        no_quick_phone = [
            item
            for item in ordered
            if str(item.get("source_key") or "") not in selected_keys
        ]
        for start in range(0, len(no_quick_phone), 8):
            if remaining_needed <= 0:
                break
            batch = no_quick_phone[start : start + 8]
            full_found, full_failures = _analyze_parallel(
                batch,
                contact_mode="full",
                workers=4,
            )
            full_checked += len(batch)
            failures.extend(full_failures)
            selected.extend(full_found[:remaining_needed])
            remaining_needed = needed - len(selected)
            _notify(
                progress,
                stage="full_contact",
                checked=full_checked,
                found=len(selected),
            )

    selected.sort(key=_growth_sort_key, reverse=True)
    return selected[:needed], failures, len(ordered)


def collect_contactable_growth_companies(
    region_code: str,
    *,
    target_count: int = 30,
    start_page: int = 1,
    max_pages: int = 10,
    minimum_employees: int = 3,
    sigungu_code: str = "",
    emd_code: str = "",
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    target_count = min(100, max(1, int(target_count)))
    max_pages = min(30, max(1, int(max_pages)))
    start_page = max(1, int(start_page))

    try:
        saved_source_keys, saved_business_nos = (
            existing_prospect_identities()
        )
        duplicate_warning = ""
    except Exception as exc:
        saved_source_keys, saved_business_nos = set(), set()
        duplicate_warning = str(exc)

    seen_source_keys = set(saved_source_keys)
    growth_pool: list[dict[str, Any]] = []
    fallback_pool: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    stats = {
        "basic_received": 0,
        "detail_targets": 0,
        "detail_success": 0,
        "detail_failed": 0,
        "existing_customer_excluded": 0,
        "saved_prospect_excluded": 0,
        "under_minimum_excluded": 0,
        "growth_candidates": 0,
        "contact_checked": 0,
        "pages_scanned": 0,
    }

    for offset in range(max_pages):
        if len(selected) >= target_count:
            break
        page_no = start_page + offset
        _notify(
            progress,
            stage="nps",
            page=page_no,
            pages_scanned=stats["pages_scanned"],
            found=len(selected),
        )
        page_result = fetch_nps_workplaces(
            region_code,
            page_no=page_no,
            rows=100,
            sigungu_code=sigungu_code,
            emd_code=emd_code,
            detail_workers=8,
            stock_company_only=True,
            exclude_source_keys=seen_source_keys,
        )
        stats["pages_scanned"] += 1
        if not page_result.get("ok"):
            failures.append(
                {
                    "페이지": page_no,
                    "단계": "국민연금",
                    "실패사유": page_result.get("message", "조회 실패"),
                }
            )
            continue

        stats["basic_received"] += int(
            page_result.get("basic_received_count") or 0
        )
        stats["detail_targets"] += int(
            page_result.get("basic_detail_target_count") or 0
        )
        stats["detail_success"] += int(
            page_result.get("detail_success_count") or 0
        )
        stats["detail_failed"] += int(
            page_result.get("detail_failed_count") or 0
        )
        items = list(page_result.get("items") or [])
        for item in items:
            source_key = str(item.get("source_key") or "").strip()
            if source_key:
                seen_source_keys.add(source_key)

        minimum_filtered = [
            item
            for item in items
            if int(item.get("가입자수") or 0) >= int(minimum_employees)
        ]
        stats["under_minimum_excluded"] += (
            len(items) - len(minimum_filtered)
        )
        try:
            minimum_filtered, customer_count = remove_existing_customers(
                minimum_filtered
            )
            stats["existing_customer_excluded"] += customer_count
        except Exception as exc:
            duplicate_warning = duplicate_warning or str(exc)

        minimum_filtered, prospect_count = remove_existing_prospects(
            minimum_filtered,
            source_keys=saved_source_keys,
            business_nos=saved_business_nos,
        )
        stats["saved_prospect_excluded"] += prospect_count

        page_growth = []
        page_fallback = []
        for item in minimum_filtered:
            net_growth = _net_growth(item)
            item["순고용증가"] = net_growth
            item["고용증가판정"] = (
                "최근 순고용 증가" if net_growth > 0 else "증가 미확인"
            )
            if net_growth > 0:
                page_growth.append(item)
            else:
                page_fallback.append(item)
        growth_pool.extend(page_growth)
        fallback_pool.extend(page_fallback)
        stats["growth_candidates"] += len(page_growth)

        growth_found, contact_failures, checked = _find_contactable(
            page_growth,
            needed=target_count - len(selected),
            progress=progress,
        )
        selected.extend(growth_found)
        failures.extend(contact_failures)
        stats["contact_checked"] += checked

    if len(selected) < target_count and fallback_pool:
        fallback_found, contact_failures, checked = _find_contactable(
            fallback_pool,
            needed=target_count - len(selected),
            progress=progress,
        )
        selected.extend(fallback_found)
        failures.extend(contact_failures)
        stats["contact_checked"] += checked

    selected = [
        row for row in selected if normalize_phone(row.get("대표전화"))
    ][:target_count]
    selected.sort(key=_growth_sort_key, reverse=True)
    return {
        "ok": True,
        "items": selected,
        "target_count": target_count,
        "found_count": len(selected),
        "next_page": start_page + stats["pages_scanned"],
        "stats": stats,
        "failures": failures,
        "duplicate_warning": duplicate_warning,
        "priority_basis": (
            "최근 월간 순고용 증가(신규취득자수-상실가입자수) 우선"
        ),
    }
