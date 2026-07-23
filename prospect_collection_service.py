from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from contact_matching import normalize_phone
from prospect_db_repository import (
    _snapshot_identity,
    existing_prospect_identities,
    load_prior_employee_snapshots,
    remove_existing_customers,
    remove_existing_prospects,
    save_employee_snapshots,
)
from public_data_api import (
    enrich_employment_growth,
    fetch_nps_workplaces,
    industry_category,
)
from sales_intelligence import analyze_sales_candidate, merge_analysis


ProgressCallback = Callable[[dict[str, Any]], None]


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _growth_sort_key(item: dict[str, Any]) -> tuple[int, int, int]:
    selected_growth = _optional_int(item.get("선택고용증가"))
    recent_new = _optional_int(item.get("신규취득자수"))
    return (
        selected_growth if selected_growth is not None else -1000000,
        recent_new if recent_new is not None else -1000000,
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
    run_quick: bool = True,
    run_full: bool = True,
    found_offset: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if needed <= 0 or not items:
        return [], [], 0

    ordered = sorted(items, key=_growth_sort_key, reverse=True)
    quick_found: list[dict[str, Any]] = []
    quick_failures: list[dict[str, Any]] = []
    if run_quick:
        quick_found, quick_failures = _analyze_parallel(
            ordered,
            contact_mode="quick",
            workers=8,
        )
    selected = quick_found[:needed]
    selected_keys = {
        str(row.get("source_key") or "") for row in quick_found
    }
    _notify(
        progress,
        stage="quick_contact",
        checked=len(ordered),
        found=found_offset + len(selected),
    )

    remaining_needed = needed - len(selected)
    full_checked = 0
    failures = list(quick_failures)
    if run_full and remaining_needed > 0:
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
                found=found_offset + len(selected),
            )

    selected.sort(key=_growth_sort_key, reverse=True)
    checked = (len(ordered) if run_quick else 0) + full_checked
    return selected[:needed], failures, checked


def collect_contactable_growth_companies(
    region_code: str,
    *,
    target_count: int = 30,
    start_page: int = 1,
    max_pages: int = 10,
    minimum_employees: int = 3,
    business_type: str = "stock",
    growth_only: bool = True,
    growth_basis: str = "combined",
    industry_categories: list[str] | None = None,
    sigungu_code: str = "",
    emd_code: str = "",
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    target_count = min(100, max(1, int(target_count)))
    max_pages = min(100, max(1, int(max_pages)))
    start_page = max(1, int(start_page))
    started_at = time.monotonic()
    business_type = str(business_type or "stock").strip().lower()
    if business_type not in {"stock", "individual", "all"}:
        business_type = "stock"
    growth_basis = str(growth_basis or "combined").strip().lower()
    if growth_basis not in {"combined", "none"}:
        growth_basis = "combined"
    if growth_basis == "none":
        growth_only = False
    selected_industries = {
        str(value or "").strip()
        for value in (industry_categories or [])
        if str(value or "").strip()
    }

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
        "industry_excluded": 0,
        "growth_candidates": 0,
        "employment_checked": 0,
        "employment_unavailable": 0,
        "employment_failed": 0,
        "employment_api_attempts": 0,
        "year_snapshot_found": 0,
        "year_snapshot_saved": 0,
        "contact_checked": 0,
        "pages_scanned": 0,
        "elapsed_seconds": 0.0,
        "growth_only": bool(growth_only),
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
            detail_workers=8,
            timeout=30,
            retries=2,
            business_type=business_type,
            exclude_source_keys=seen_source_keys,
        )
        stats["pages_scanned"] += 1
        _notify(
            progress,
            stage="nps_complete",
            page=page_no,
            pages_scanned=stats["pages_scanned"],
            found=len(selected),
        )
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
        if selected_industries:
            industry_filtered = []
            for item in minimum_filtered:
                category = str(
                    item.get("업종분류")
                    or industry_category(item.get("업종명"))
                )
                item["업종분류"] = category
                if category in selected_industries:
                    industry_filtered.append(item)
            stats["industry_excluded"] += (
                len(minimum_filtered) - len(industry_filtered)
            )
            minimum_filtered = industry_filtered
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

        try:
            previous_snapshots = load_prior_employee_snapshots(
                minimum_filtered
            )
            for item in minimum_filtered:
                identity = _snapshot_identity(item)
                previous = previous_snapshots.get(identity)
                if previous:
                    previous_count = int(
                        previous.get("employee_count") or 0
                    )
                    item["전년가입자수"] = previous_count
                    item["전년대비고용증가"] = int(
                        item.get("가입자수") or 0
                    ) - previous_count
                    item["전년자료생성년월"] = previous.get(
                        "data_created_ym", ""
                    )
                    stats["year_snapshot_found"] += 1
            stats["year_snapshot_saved"] += save_employee_snapshots(
                minimum_filtered
            )
        except Exception as exc:
            duplicate_warning = duplicate_warning or str(exc)

        _notify(
            progress,
            stage="employment",
            page=page_no,
            checked=stats["employment_checked"],
            found=len(selected),
        )
        minimum_filtered, employment_stats = enrich_employment_growth(
            minimum_filtered,
            basis=growth_basis,
            timeout=15,
            retries=1,
            workers=8,
        )
        for name in (
            "employment_checked",
            "employment_unavailable",
            "employment_failed",
            "employment_api_attempts",
        ):
            stats[name] += int(employment_stats.get(name) or 0)
        _notify(
            progress,
            stage="employment_complete",
            page=page_no,
            checked=stats["employment_checked"],
            unavailable=stats["employment_unavailable"],
            found=len(selected),
        )

        page_growth = []
        page_fallback = []
        for item in minimum_filtered:
            growth_value = _optional_int(item.get("선택고용증가"))
            if growth_basis == "none":
                page_growth.append(item)
            elif bool(item.get("고용증가신호")):
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
            run_quick=True,
            run_full=True,
            found_offset=len(selected),
        )
        selected.extend(growth_found)
        failures.extend(contact_failures)
        stats["contact_checked"] += checked

    if (
        len(selected) < target_count
        and fallback_pool
        and not growth_only
    ):
        fallback_found, contact_failures, checked = _find_contactable(
            sorted(fallback_pool, key=_growth_sort_key, reverse=True),
            needed=target_count - len(selected),
            progress=progress,
            run_quick=True,
            run_full=True,
            found_offset=len(selected),
        )
        selected.extend(fallback_found)
        failures.extend(contact_failures)
        stats["contact_checked"] += checked

    selected = [
        row for row in selected if normalize_phone(row.get("대표전화"))
    ][:target_count]
    selected.sort(key=_growth_sort_key, reverse=True)
    stats["elapsed_seconds"] = round(time.monotonic() - started_at, 1)
    return {
        "ok": True,
        "items": selected,
        "target_count": target_count,
        "found_count": len(selected),
        "next_page": start_page + stats["pages_scanned"],
        "stats": stats,
        "failures": failures,
        "duplicate_warning": duplicate_warning,
        "business_type": business_type,
        "growth_only": bool(growth_only),
        "growth_basis": growth_basis,
        "industry_categories": sorted(selected_industries),
        "searched_start_page": start_page,
        "searched_end_page": (
            start_page + max(0, stats["pages_scanned"] - 1)
        ),
        "priority_basis": (
            {
                "combined": (
                    "전년 동월 가입자 증감(스냅샷 축적 시) 또는 "
                    "최근 월 신규취득자수-상실가입자수 증가"
                ),
                "none": "고용 증가 필터 미사용",
            }[growth_basis]
            + (
                " 사업장만"
                if growth_only
                else (
                    " 사업장 우선"
                    if growth_basis != "none"
                    else ""
                )
            )
        ),
    }
