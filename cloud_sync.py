from __future__ import annotations

from pathlib import Path
from typing import Any

from cloud_db import (
    CloudDatabase,
    TABLE_CRM,
    TABLE_CUSTOMERS,
    TABLE_FINANCIALS,
    TABLE_MATCHING_PREFERENCES,
    TABLE_REGISTRY,
    TABLE_STOCK,
    cloud_is_configured,
    normalize_business_no,
)
from utils import get_user_dirs
from sync_outbox import (
    cloud_outbox_status,
    durable_outbox_enabled,
    enqueue_outbox,
    load_local_outbox,
    local_outbox_status,
    retry_cloud_outbox,
    retry_local_outbox,
    save_local_outbox,
)


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


def retry_cloud_sync_queue(user_id: str) -> dict[str, int]:
    local_status = local_outbox_status(_queue_path(user_id))
    if not cloud_is_configured():
        return {
            "success": 0,
            "failed": int(local_status.get("queued", 0)),
            "dead_letter": int(local_status.get("dead_letter", 0)),
        }

    db = CloudDatabase()
    success = 0
    failed = 0
    dead_letter = 0

    if durable_outbox_enabled():
        try:
            durable_result = retry_cloud_outbox(
                db,
                owner_user_id=user_id,
                worker_id=f"app-{user_id}",
            )
            success += int(durable_result.get("success", 0))
            failed += int(durable_result.get("failed", 0))
        except Exception:
            # Migration/RPC availability must never destroy the local fallback.
            failed += 1

    local_result = retry_local_outbox(_queue_path(user_id), db.upsert)
    success += int(local_result.get("success", 0))
    failed += int(local_result.get("failed", 0))
    dead_letter += int(local_result.get("dead_letter", 0))
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


def sync_customer_snapshot(
    user_id: str,
    customer_data: dict[str, Any],
    source: str = "app",
    manager_name: str = "",
) -> tuple[bool, str]:
    data = dict(customer_data or {})
    business_no = normalize_business_no(
        data.get("사업자등록번호", data.get("사업자번호", ""))
    )
    if len(business_no.replace("-", "")) != 10:
        return False, "사업자등록번호가 없어 고객 동기화를 건너뛰었습니다."

    return _safe_upsert(
        user_id,
        "customer",
        TABLE_CUSTOMERS,
        [{
            "owner_user_id": user_id,
            "business_no": business_no,
            "company_name": data.get("업체명", data.get("기업명")),
            "representative_name": data.get("대표자명", data.get("대표자")),
            "industry_name": data.get("업종명", data.get("업종")),
            "address": data.get("사업장 소재지", data.get("주소")),
            "manager_name": manager_name or data.get("담당자"),
            "source": source,
            "customer_data": data,
        }],
        "owner_user_id,business_no",
    )


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

    return _safe_upsert(
        user_id,
        "financial",
        TABLE_FINANCIALS,
        [{
            "owner_user_id": user_id,
            "business_no": business_no,
            "financial_data": dict(financial_data or {}),
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

    return _safe_upsert(
        user_id,
        "registry",
        TABLE_REGISTRY,
        [{
            "owner_user_id": user_id,
            "business_no": business_no,
            "registry_data": dict(registry_data or {}),
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
        rows = CloudDatabase().select(
            TABLE_FINANCIALS,
            filters={"owner_user_id": user_id, "business_no": business_no},
            columns="financial_data",
            limit=1,
        )
        if not rows:
            return {}
        data = rows[0].get("financial_data", {})
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
        rows = CloudDatabase().select(
            TABLE_REGISTRY,
            filters={"owner_user_id": user_id, "business_no": business_no},
            columns="registry_data",
            limit=1,
        )
        if not rows:
            return {}
        data = rows[0].get("registry_data", {})
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
