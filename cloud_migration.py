from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from cloud_db import (
    CloudDatabase,
    TABLE_CRM,
    TABLE_FINANCIALS,
    TABLE_MIGRATIONS,
    TABLE_REGISTRY,
    TABLE_STOCK,
    normalize_business_no,
)
from cloud_sync import sync_customer_snapshots
from runtime_error_log import safe_public_error
from utils import get_user_cumulative_db_path, get_user_dirs


MIGRATION_VERSION = "v9.11.0"


def _migration_error(label: str, exc: Exception) -> str:
    """Return a bounded, PII-free migration failure for UI and audit storage."""
    return f"{label}: {safe_public_error(exc, '데이터 복사에 실패했습니다.')}"


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return value


def _clean_record(record: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _clean_value(value) for key, value in record.items()}


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def collect_migration_preview(user_id: str) -> dict[str, int]:
    dirs = get_user_dirs(user_id)
    customer_path = get_user_cumulative_db_path(user_id)

    customer_count = 0
    if customer_path.exists():
        try:
            df = pd.read_excel(customer_path, sheet_name="고객DB")
            customer_count = len(df.dropna(how="all"))
        except Exception:
            customer_count = 0

    crm_data = _load_json(dirs["base"] / "crm_data.json", {})
    financial_data = _load_json(dirs["base"] / "stock_financial_cache.json", {})
    registry_data = _load_json(dirs["base"] / "registry_cache.json", {})
    stock_data = _load_json(dirs["base"] / "stock_valuations.json", [])

    crm_customers = crm_data.get("customers", {}) if isinstance(crm_data, dict) else {}

    return {
        "customers": customer_count,
        "crm": len(crm_customers) if isinstance(crm_customers, dict) else 0,
        "financials": len(financial_data) if isinstance(financial_data, dict) else 0,
        "registry": len(registry_data) if isinstance(registry_data, dict) else 0,
        "stock_valuations": len(stock_data) if isinstance(stock_data, list) else 0,
    }


def migrate_user_data(
    user_id: str,
    manager_name: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    db = db or CloudDatabase()
    dirs = get_user_dirs(user_id)
    result = {
        "customers": 0,
        "customer_unresolved": 0,
        "customer_queued": 0,
        "crm": 0,
        "financials": 0,
        "registry": 0,
        "stock_valuations": 0,
        "errors": [],
    }

    customer_path = get_user_cumulative_db_path(user_id)
    if customer_path.exists():
        try:
            df = pd.read_excel(customer_path, sheet_name="고객DB").dropna(how="all")
            customer_rows = [
                _clean_record(row.to_dict())
                for _, row in df.iterrows()
            ]
            sync_result = sync_customer_snapshots(
                user_id,
                customer_rows,
                source="excel_migration",
                manager_name=manager_name,
                db=db,
            )
            result["customers"] = int(sync_result.get("synced", 0))
            result["customer_unresolved"] = int(
                sync_result.get("skipped", 0)
            )
            result["customer_queued"] = int(sync_result.get("queued", 0))
            if result["customer_unresolved"]:
                result["errors"].append(
                    "고객DB: 식별 불가 행 "
                    f"{result['customer_unresolved']}건은 원본에 보존했습니다."
                )
            if result["customer_queued"]:
                result["errors"].append(
                    "고객DB: 클라우드 통합 재시도 대기 "
                    f"{result['customer_queued']}건"
                )
            if sync_result.get("failed"):
                result["errors"].append(
                    "고객DB: 일부 고객 통합 저장을 재확인해야 합니다."
                )
        except Exception as exc:
            result["errors"].append(_migration_error("고객DB", exc))

    crm_data = _load_json(dirs["base"] / "crm_data.json", {})
    crm_customers = crm_data.get("customers", {}) if isinstance(crm_data, dict) else {}
    if isinstance(crm_customers, dict):
        rows = []
        for customer_key, data in crm_customers.items():
            if not isinstance(data, dict):
                continue
            business_no = normalize_business_no(data.get("business_no", ""))
            if len(business_no.replace("-", "")) != 10:
                business_no = str(customer_key)
            rows.append({
                "owner_user_id": user_id,
                "business_no": business_no,
                "crm_data": data,
            })
        try:
            db.upsert(TABLE_CRM, rows, "owner_user_id,business_no")
            result["crm"] = len(rows)
        except Exception as exc:
            result["errors"].append(_migration_error("CRM", exc))

    for filename, table, key_name, result_key in [
        ("stock_financial_cache.json", TABLE_FINANCIALS, "financial_data", "financials"),
        ("registry_cache.json", TABLE_REGISTRY, "registry_data", "registry"),
    ]:
        cached = _load_json(dirs["base"] / filename, {})
        if isinstance(cached, dict):
            rows = [
                {
                    "owner_user_id": user_id,
                    "business_no": normalize_business_no(business_no),
                    key_name: data,
                }
                for business_no, data in cached.items()
                if isinstance(data, dict)
            ]
            try:
                db.upsert(table, rows, "owner_user_id,business_no")
                result[result_key] = len(rows)
            except Exception as exc:
                result["errors"].append(_migration_error(filename, exc))

    stock_data = _load_json(dirs["base"] / "stock_valuations.json", [])
    if isinstance(stock_data, list):
        rows = []
        for index, data in enumerate(stock_data):
            if not isinstance(data, dict):
                continue
            rows.append({
                "owner_user_id": user_id,
                "record_id": str(data.get("record_id") or f"legacy-{user_id}-{index}"),
                "business_no": normalize_business_no(data.get("business_no", "")),
                "company_name": data.get("company_name"),
                "valuation_date": data.get("valuation_date") or None,
                "valuation_data": data,
            })
        try:
            db.upsert(TABLE_STOCK, rows, "owner_user_id,record_id")
            result["stock_valuations"] = len(rows)
        except Exception as exc:
            result["errors"].append(_migration_error("주가평가", exc))

    try:
        db.insert(TABLE_MIGRATIONS, [{
            "owner_user_id": user_id,
            "migration_version": MIGRATION_VERSION,
            "result_data": {
                **result,
                "migrated_at": datetime.now().isoformat(timespec="seconds"),
            },
        }])
    except Exception as exc:
        result["errors"].append(_migration_error("이관이력", exc))

    return result
