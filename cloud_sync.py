from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from cloud_db import (
    CloudDatabase,
    TABLE_CRM,
    TABLE_FINANCIALS,
    TABLE_MATCHING_PREFERENCES,
    TABLE_REGISTRY,
    TABLE_STOCK,
    cloud_is_configured,
    normalize_business_no,
)
from performance_cache import invalidate_cache
from utils import get_user_dirs
from sync_outbox import (
    cloud_outbox_status,
    durable_outbox_enabled,
    enqueue_outbox,
    enqueue_rpc_outbox,
    load_local_outbox,
    local_outbox_status,
    retry_cloud_outbox,
    retry_local_outbox,
    save_local_outbox,
)


def _business_no_variants(value: Any) -> list[str]:
    raw = str(value or "").strip()
    normalized = normalize_business_no(raw)
    digits = "".join(character for character in raw if character.isdigit())
    return list(
        dict.fromkeys(
            candidate
            for candidate in (normalized, digits, raw)
            if candidate
        )
    )


def _usable_snapshot_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {
            "", "-", "nan", "none", "nat", "<na>"
        }
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _merge_snapshot_data(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(existing or {})
    for key, value in dict(incoming or {}).items():
        if _usable_snapshot_value(value):
            merged[key] = value
    return merged


def _select_snapshot_row(
    table: str,
    owner_user_id: str,
    business_no: Any,
    columns: str,
) -> dict[str, Any]:
    if not cloud_is_configured():
        return {}
    database = CloudDatabase()
    for candidate in _business_no_variants(business_no):
        rows = database.select(
            table,
            filters={
                "owner_user_id": owner_user_id,
                "business_no": candidate,
            },
            columns=columns,
            limit=1,
        )
        if rows:
            return rows[0] if isinstance(rows[0], dict) else {}
    return {}


def _queue_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "cloud_sync_queue.json"


def _load_queue(user_id: str) -> list[dict[str, Any]]:
    return load_local_outbox(_queue_path(user_id))


def _save_queue(user_id: str, items: list[dict[str, Any]]) -> None:
    save_local_outbox(_queue_path(user_id), items)


def _enqueue(
    user_id: str,
    operation: str,
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
    error: str,
) -> None:
    enqueue_outbox(
        _queue_path(user_id),
        user_id,
        operation,
        table,
        rows,
        on_conflict,
        error=error,
    )


def retry_cloud_sync_queue(
    user_id: str,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, int]:
    local_status = local_outbox_status(_queue_path(user_id))
    if db is None and not cloud_is_configured():
        return {
            "success": 0,
            "failed": int(local_status.get("queued", 0)),
            "dead_letter": int(local_status.get("dead_letter", 0)),
        }

    try:
        database = db or CloudDatabase()
    except Exception:
        return {
            "success": 0,
            "failed": max(1, int(local_status.get("queued", 0))),
            "dead_letter": int(local_status.get("dead_letter", 0)),
        }
    success = 0
    failed = 0
    dead_letter = 0

    if durable_outbox_enabled():
        try:
            durable_result = retry_cloud_outbox(
                database,
                owner_user_id=user_id,
                worker_id=f"app-{user_id}",
            )
            success += int(durable_result.get("success", 0))
            failed += int(durable_result.get("failed", 0))
        except Exception:
            # Migration/RPC availability must never destroy the local fallback.
            failed += 1

    try:
        local_result = retry_local_outbox(
            _queue_path(user_id),
            database.upsert,
            rpc=database.rpc,
        )
        success += int(local_result.get("success", 0))
        failed += int(local_result.get("failed", 0))
        dead_letter += int(local_result.get("dead_letter", 0))
    except Exception:
        failed += max(1, int(local_status.get("queued", 0)))
    return {"success": success, "failed": failed, "dead_letter": dead_letter}


def _safe_upsert(
    user_id: str,
    operation: str,
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
) -> tuple[bool, str]:
    if not rows:
        return True, "저장할 데이터가 없습니다."

    if not cloud_is_configured():
        _enqueue(
            user_id, operation, table, rows, on_conflict,
            "Supabase Secrets 미설정",
        )
        return False, "Supabase 미설정으로 동기화 대기열에 저장했습니다."

    try:
        retry_cloud_sync_queue(user_id)
        CloudDatabase().upsert(table, rows, on_conflict)
        return True, "Supabase 동기화 완료"
    except Exception as exc:
        _enqueue(
            user_id, operation, table, rows, on_conflict, str(exc)
        )
        return False, "클라우드 저장에 실패하여 안전한 재시도 대기열에 보관했습니다."


def _exact_business_no(value: Any) -> str:
    raw = "" if value is None else str(value)
    digits = re.sub(r"[^0-9]", "", raw)
    if len(digits) != 10:
        return ""
    return normalize_business_no(digits)


def _rpc_function_unavailable(exc: Exception, function_name: str) -> bool:
    message = str(exc or "").lower()
    function_name = str(function_name or "").lower()
    return (
        "pgrst202" in message
        or "could not find the function" in message
        or (
            function_name in message
            and (
                "does not exist" in message
                or "undefined function" in message
                or "42883" in message
            )
        )
    )


def _first_nonblank_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text.lower() not in {"", "nan", "none", "nat", "<na>"}:
            return text
    return ""


def _customer_sync_data(
    customer_data: dict[str, Any],
    *,
    customer_id: str = "",
    previous_business_no: str = "",
) -> tuple[dict[str, Any], str, str]:
    data = dict(customer_data) if isinstance(customer_data, dict) else {}
    resolved_customer_id = _first_nonblank_text(
        customer_id,
        data.get("_customer_id"),
    )
    resolved_previous_business_no = _first_nonblank_text(
        previous_business_no,
        data.get("_previous_business_no"),
    )
    for field in (
        "_customer_id",
        "_company_uid",
        "_lifecycle_status",
        "_cloud_updated_at",
        "_previous_business_no",
    ):
        data.pop(field, None)
    return data, resolved_customer_id, resolved_previous_business_no


def _customer_profile_parameters(
    user_id: str,
    data: dict[str, Any],
    *,
    business_no: str,
    source: str,
    manager_name: str,
    customer_id: str,
    previous_business_no: str,
) -> dict[str, Any]:
    previous_value = (
        normalize_business_no(previous_business_no)
        if previous_business_no
        else None
    )
    return {
        "p_owner_user_id": user_id,
        "p_business_no": business_no,
        "p_company_name": data.get("업체명", data.get("기업명")),
        "p_representative_name": data.get("대표자명", data.get("대표자")),
        "p_industry_name": data.get("업종명", data.get("업종")),
        "p_address": data.get("사업장 소재지", data.get("주소")),
        "p_manager_name": manager_name or data.get("담당자"),
        "p_source": source,
        "p_customer_data": data,
        "p_customer_id": customer_id or None,
        "p_previous_business_no": previous_value,
    }


_PROFILE_SUCCESS_LINK_STATUSES = {
    "linked",
    "linked_review_required",
    "unlinked",
    "ambiguous_review",
}


def _customer_profile_rpc_succeeded(value: Any) -> bool:
    if isinstance(value, list):
        row = next((item for item in value if isinstance(item, dict)), {})
    elif isinstance(value, dict):
        row = value
    else:
        row = {}
    customer_id = _first_nonblank_text(row.get("customer_id"))
    link_status = str(row.get("link_status") or "").strip().lower()
    return bool(
        customer_id
        and link_status in _PROFILE_SUCCESS_LINK_STATUSES
    )


def _enqueue_customer_profile(
    user_id: str,
    parameters: dict[str, Any],
    error: Any,
    *,
    db: CloudDatabase | None,
) -> bool:
    try:
        enqueue_rpc_outbox(
            _queue_path(user_id),
            user_id,
            "customer",
            "oasis_upsert_customer_profile",
            parameters,
            error=error,
            db=db,
        )
        return True
    except Exception:
        # A malformed/corrupted existing queue is intentionally never replaced.
        return False


def _sync_customer_snapshot_once(
    user_id: str,
    customer_data: dict[str, Any],
    *,
    source: str,
    manager_name: str,
    customer_id: str,
    previous_business_no: str,
    db: CloudDatabase | None,
    retry_queue: bool,
) -> tuple[bool, str, str]:
    data, resolved_customer_id, resolved_previous_business_no = (
        _customer_sync_data(
            customer_data,
            customer_id=customer_id,
            previous_business_no=previous_business_no,
        )
    )
    business_no = _exact_business_no(
        data.get("사업자등록번호", data.get("사업자번호", ""))
    )
    if not business_no:
        return (
            False,
            "사업자등록번호가 없어 고객 동기화를 건너뛰었습니다.",
            "skipped",
        )

    parameters = _customer_profile_parameters(
        user_id,
        data,
        business_no=business_no,
        source=source,
        manager_name=manager_name,
        customer_id=resolved_customer_id,
        previous_business_no=resolved_previous_business_no,
    )
    if db is None:
        queued = _enqueue_customer_profile(
            user_id,
            parameters,
            "Supabase 설정 없음",
            db=None,
        )
        return (
            False,
            (
                "Supabase 미설정으로 동기화 대기열에 저장했습니다."
                if queued
                else "동기화 대기열을 보존하지 못해 저장을 중단했습니다."
            ),
            "queued" if queued else "failed",
        )

    if retry_queue:
        try:
            retry_cloud_sync_queue(user_id, db=db)
        except Exception:
            # A stale/corrupted prior queue must not block this direct attempt.
            pass

    try:
        rpc_result = db.rpc("oasis_upsert_customer_profile", parameters)
    except Exception as exc:
        if _rpc_function_unavailable(
            exc,
            "oasis_upsert_customer_profile",
        ):
            queued = _enqueue_customer_profile(
                user_id,
                parameters,
                exc,
                db=db,
            )
            return (
                False,
                (
                    "고객 원본을 보존해 통합 RPC 재시도 대기열에 보관했습니다."
                    if queued
                    else "동기화 대기열을 보존하지 못해 저장을 중단했습니다."
                ),
                "queued" if queued else "failed",
            )
        else:
            queued = _enqueue_customer_profile(
                user_id,
                parameters,
                exc,
                db=db,
            )
            return (
                False,
                (
                    "고객 통합 저장이 거부되어 재시도 대기열에 보관했습니다."
                    if queued
                    else "동기화 대기열을 보존하지 못해 저장을 중단했습니다."
                ),
                "queued" if queued else "failed",
            )
    else:
        if not _customer_profile_rpc_succeeded(rpc_result):
            queued = _enqueue_customer_profile(
                user_id,
                parameters,
                "customer_profile_rpc_rejected",
                db=db,
            )
            return (
                False,
                (
                    "고객 통합 검증이 완료되지 않아 재시도 대기열에 보관했습니다."
                    if queued
                    else "동기화 대기열을 보존하지 못해 저장을 중단했습니다."
                ),
                "queued" if queued else "failed",
            )

    invalidate_cache("registered_customers", str(user_id).strip().lower())
    return True, "Supabase 동기화 완료", "synced"


def sync_customer_snapshot(
    user_id: str,
    customer_data: dict[str, Any],
    source: str = "app",
    manager_name: str = "",
    *,
    customer_id: str = "",
    previous_business_no: str = "",
    db: CloudDatabase | None = None,
) -> tuple[bool, str]:
    database = db
    try:
        configured = cloud_is_configured()
    except Exception:
        configured = False
    if database is None and configured:
        try:
            database = CloudDatabase()
        except Exception:
            database = None
    try:
        success, message, _status = _sync_customer_snapshot_once(
            user_id,
            customer_data,
            source=source,
            manager_name=manager_name,
            customer_id=customer_id,
            previous_business_no=previous_business_no,
            db=database,
            retry_queue=True,
        )
    except Exception:
        return False, "고객 동기화를 안전하게 완료하지 못했습니다."
    return success, message


def sync_customer_snapshots(
    user_id: str,
    customer_rows: list[dict[str, Any]],
    source: str = "app",
    manager_name: str = "",
    *,
    db: CloudDatabase | None = None,
) -> dict[str, int]:
    rows = list(customer_rows or [])
    summary = {
        "attempted": len(rows),
        "synced": 0,
        "queued": 0,
        "skipped": 0,
        "failed": 0,
    }
    database = db
    try:
        configured = cloud_is_configured()
    except Exception:
        configured = False
    if database is None and configured:
        try:
            database = CloudDatabase()
        except Exception:
            database = None
    if database is not None:
        try:
            retry_cloud_sync_queue(user_id, db=database)
        except Exception:
            pass

    for row in rows:
        if not isinstance(row, dict):
            summary["skipped"] += 1
            continue
        try:
            success, _message, status = _sync_customer_snapshot_once(
                user_id,
                row,
                source=source,
                manager_name=manager_name,
                customer_id="",
                previous_business_no="",
                db=database,
                retry_queue=False,
            )
        except Exception:
            summary["failed"] += 1
            continue
        if success:
            summary["synced"] += 1
        elif status in summary:
            summary[status] += 1
        else:
            summary["failed"] += 1
    return summary


def sync_crm_record(
    user_id: str,
    business_no: Any,
    crm_data: dict[str, Any],
) -> tuple[bool, str]:
    business_no = normalize_business_no(business_no)
    if not business_no:
        return False, "사업자등록번호가 없어 CRM 동기화를 건너뛰었습니다."

    return _safe_upsert(
        user_id,
        "crm",
        TABLE_CRM,
        [{
            "owner_user_id": user_id,
            "business_no": business_no,
            "crm_data": dict(crm_data or {}),
        }],
        "owner_user_id,business_no",
    )


def sync_financial_snapshot(
    user_id: str,
    business_no: Any,
    financial_data: dict[str, Any],
) -> tuple[bool, str]:
    business_no = normalize_business_no(business_no)
    if not business_no:
        return False, "사업자등록번호가 없어 재무 동기화를 건너뛰었습니다."

    data = dict(financial_data or {})
    storage_business_no = business_no
    if cloud_is_configured():
        try:
            existing_row = _select_snapshot_row(
                TABLE_FINANCIALS,
                user_id,
                business_no,
                "business_no,financial_data",
            )
        except Exception:
            existing_row = {}
        if existing_row:
            storage_business_no = str(
                existing_row.get("business_no") or business_no
            )
            existing_data = existing_row.get("financial_data", {})
            if isinstance(existing_data, dict):
                data = _merge_snapshot_data(existing_data, data)
    data["사업자등록번호"] = business_no

    return _safe_upsert(
        user_id,
        "financial",
        TABLE_FINANCIALS,
        [{
            "owner_user_id": user_id,
            "business_no": storage_business_no,
            "financial_data": data,
        }],
        "owner_user_id,business_no",
    )


def sync_registry_snapshot(
    user_id: str,
    business_no: Any,
    registry_data: dict[str, Any],
) -> tuple[bool, str]:
    business_no = normalize_business_no(business_no)
    if not business_no:
        return False, "사업자등록번호가 없어 등기 동기화를 건너뛰었습니다."

    data = dict(registry_data or {})
    storage_business_no = business_no
    if cloud_is_configured():
        try:
            existing_row = _select_snapshot_row(
                TABLE_REGISTRY,
                user_id,
                business_no,
                "business_no,registry_data",
            )
        except Exception:
            existing_row = {}
        if existing_row:
            storage_business_no = str(
                existing_row.get("business_no") or business_no
            )
            existing_data = existing_row.get("registry_data", {})
            if isinstance(existing_data, dict):
                data = _merge_snapshot_data(existing_data, data)
    data["사업자등록번호"] = business_no

    return _safe_upsert(
        user_id,
        "registry",
        TABLE_REGISTRY,
        [{
            "owner_user_id": user_id,
            "business_no": storage_business_no,
            "registry_data": data,
        }],
        "owner_user_id,business_no",
    )


def load_financial_snapshot(
    user_id: str,
    business_no: Any,
) -> dict[str, Any]:
    # Supabase에 저장된 최신 크레탑 재무 스냅샷을 읽습니다.
    business_no = normalize_business_no(business_no)
    if not business_no or not cloud_is_configured():
        return {}
    try:
        row = _select_snapshot_row(
            TABLE_FINANCIALS,
            user_id,
            business_no,
            "financial_data",
        )
        if not row:
            return {}
        data = row.get("financial_data", {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_registry_snapshot(
    user_id: str,
    business_no: Any,
) -> dict[str, Any]:
    # Supabase에 저장된 최신 법인 등기 스냅샷을 읽습니다.
    business_no = normalize_business_no(business_no)
    if not business_no or not cloud_is_configured():
        return {}
    try:
        row = _select_snapshot_row(
            TABLE_REGISTRY,
            user_id,
            business_no,
            "registry_data",
        )
        if not row:
            return {}
        data = row.get("registry_data", {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def sync_stock_valuation(
    user_id: str,
    record: dict[str, Any],
) -> tuple[bool, str]:
    data = dict(record or {})
    record_id = str(data.get("record_id", "") or "").strip()
    if not record_id:
        return False, "record_id가 없어 주가평가 동기화를 건너뛰었습니다."

    return _safe_upsert(
        user_id,
        "stock_valuation",
        TABLE_STOCK,
        [{
            "owner_user_id": user_id,
            "record_id": record_id,
            "business_no": normalize_business_no(
                data.get("business_no", "")
            ),
            "company_name": data.get("company_name"),
            "valuation_date": data.get("valuation_date") or None,
            "valuation_data": data,
        }],
        "owner_user_id,record_id",
    )


def load_stock_valuations(
    user_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Supabase에 저장된 사용자의 주가평가 기록을 최신순으로 읽습니다."""
    if not user_id or not cloud_is_configured():
        return []

    try:
        rows = CloudDatabase().select(
            TABLE_STOCK,
            filters={"owner_user_id": user_id},
            columns="record_id,valuation_data",
            order="created_at.desc",
            limit=limit,
        )
    except Exception:
        return []

    records: list[dict[str, Any]] = []
    for row in rows:
        data = row.get("valuation_data", {})
        if not isinstance(data, dict):
            continue
        record = dict(data)
        if not record.get("record_id"):
            record["record_id"] = str(row.get("record_id", "") or "")
        if record.get("record_id"):
            records.append(record)
    return records


def sync_matching_preferences(
    user_id: str,
    business_no: Any,
    preferences: dict[str, Any],
) -> tuple[bool, str]:
    business_no = normalize_business_no(business_no)
    if not business_no:
        return False, "사업자등록번호가 없어 매칭설정 동기화를 건너뛰었습니다."

    return _safe_upsert(
        user_id,
        "matching_preferences",
        TABLE_MATCHING_PREFERENCES,
        [{
            "owner_user_id": user_id,
            "business_no": business_no,
            "preference_data": dict(preferences or {}),
        }],
        "owner_user_id,business_no",
    )


def get_cloud_sync_status(user_id: str) -> dict[str, Any]:
    local_status = local_outbox_status(_queue_path(user_id))
    try:
        durable_status = cloud_outbox_status(user_id)
    except Exception:
        durable_status = {
            "enabled": durable_outbox_enabled(),
            "queued": 0,
            "dead_letter": 0,
            "total": 0,
            "unavailable": True,
        }
    return {
        "configured": cloud_is_configured(),
        "durable_enabled": durable_outbox_enabled(),
        "queued": int(local_status.get("queued", 0))
        + int(durable_status.get("queued", 0)),
        "dead_letter": int(local_status.get("dead_letter", 0))
        + int(durable_status.get("dead_letter", 0)),
        "local": local_status,
        "cloud": durable_status,
        "queue_path": str(_queue_path(user_id)),
    }
