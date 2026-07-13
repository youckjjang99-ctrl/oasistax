from __future__ import annotations

import json
from datetime import datetime
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


def _queue_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "cloud_sync_queue.json"


def _load_queue(user_id: str) -> list[dict[str, Any]]:
    path = _queue_path(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_queue(user_id: str, items: list[dict[str, Any]]) -> None:
    path = _queue_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items[-500:], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _enqueue(
    user_id: str,
    operation: str,
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
    error: str,
) -> None:
    queue = _load_queue(user_id)
    queue.append({
        "operation": operation,
        "table": table,
        "rows": rows,
        "on_conflict": on_conflict,
        "error": error,
        "queued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    _save_queue(user_id, queue)


def retry_cloud_sync_queue(user_id: str) -> dict[str, int]:
    queue = _load_queue(user_id)
    if not queue or not cloud_is_configured():
        return {"success": 0, "failed": len(queue)}

    db = CloudDatabase()
    remaining = []
    success = 0

    for item in queue:
        try:
            db.upsert(
                item["table"],
                item["rows"],
                item["on_conflict"],
            )
            success += 1
        except Exception as exc:
            item["error"] = str(exc)
            item["last_retry_at"] = datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            remaining.append(item)

    _save_queue(user_id, remaining)
    return {"success": success, "failed": len(remaining)}


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
        return False, f"Supabase 저장 실패로 대기열에 보관: {exc}"


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
    queue = _load_queue(user_id)
    return {
        "configured": cloud_is_configured(),
        "queued": len(queue),
        "queue_path": str(_queue_path(user_id)),
    }
