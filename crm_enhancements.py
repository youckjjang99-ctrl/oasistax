from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from cloud_db import CloudDatabase, TABLE_CRM, cloud_is_configured
from crm_file_store import CrmStorageError, atomic_write_json, locked_json, read_json_object
from utils import get_user_dirs


PIPELINE_OPTIONS = [
    "신규",
    "초기상담",
    "자료수집",
    "제안준비",
    "제안완료",
    "정책자금 진행",
    "고용지원금 진행",
    "주가평가 진행",
    "계약완료",
    "보류",
]

PRIORITY_OPTIONS = ["1", "2", "3", "4", "5"]


def _path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "customer_crm_profiles.json"


def _load_all(user_id: str) -> dict[str, Any]:
    path = _path(user_id)
    with locked_json(path):
        return read_json_object(path)


def _save_all(user_id: str, data: dict[str, Any]) -> None:
    path = _path(user_id)
    with locked_json(path):
        current = read_json_object(path)
        current.update(data)
        atomic_write_json(path, current)


def get_crm_profile(
    user_id: str,
    customer_key: str,
    business_no: str = "",
) -> dict[str, Any]:
    from crm import get_customer_record

    customer = get_customer_record(user_id, customer_key)
    canonical = customer.get("_v44_profile")
    if "_v44_profile" in customer and not isinstance(canonical, dict):
        raise CrmStorageError("CRM 확장정보 형식이 올바르지 않아 원본을 보존했습니다.")
    if isinstance(canonical, dict) and canonical:
        return deepcopy(canonical)
    data = _load_all(user_id)
    profile = data.get(customer_key, {})
    if isinstance(profile, dict) and profile:
        return profile

    if business_no and cloud_is_configured():
        try:
            rows = CloudDatabase().select(
                TABLE_CRM,
                filters={
                    "owner_user_id": user_id,
                    "business_no": business_no,
                },
                columns="crm_data",
                limit=1,
            )
            if rows:
                cloud_data = rows[0].get("crm_data", {})
                cloud_profile = (
                    cloud_data.get("_v44_profile", {})
                    if isinstance(cloud_data, dict)
                    else {}
                )
                if isinstance(cloud_profile, dict) and cloud_profile:
                    _save_all(user_id, {customer_key: cloud_profile})
                    return cloud_profile
        except Exception:
            pass

    return {
        "pipeline_stage": "신규",
        "priority": "3",
        "assigned_manager": "",
    }


def save_crm_profile(
    user_id: str,
    customer_key: str,
    pipeline_stage: str,
    priority: str,
    assigned_manager: str,
) -> dict[str, Any]:
    from crm import _merge_profile_fields, _record_revision, mutate_crm_data

    record = {
        "pipeline_stage": pipeline_stage,
        "priority": str(priority),
        "assigned_manager": str(assigned_manager or "").strip(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    def apply(data: dict[str, Any]) -> dict[str, Any]:
        customer = data["customers"].setdefault(customer_key, {})
        if not isinstance(customer, dict):
            raise CrmStorageError("CRM 고객 자료 형식이 올바르지 않아 원본을 보존했습니다.")
        customer["_v44_profile"] = _merge_profile_fields(customer.get("_v44_profile", {}), record)
        customer["_local_revision"] = _record_revision(customer) + 1
        customer["_sync_state"] = "pending"
        customer["updated_at"] = record["updated_at"]
        return deepcopy(customer["_v44_profile"])

    return mutate_crm_data(user_id, apply)


def save_crm_profiles_bulk(
    user_id: str,
    profiles: dict[str, dict[str, Any]],
) -> int:
    """Merge several profile updates and write the local file once."""
    if not profiles:
        return 0
    data: dict[str, Any] = {}
    updated = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for customer_key, profile in profiles.items():
        if not customer_key or not isinstance(profile, dict):
            continue
        record = dict(profile)
        record.setdefault("pipeline_stage", "신규")
        record.setdefault("priority", "3")
        record.setdefault("assigned_manager", "")
        record.setdefault("updated_at", now)
        data[customer_key] = record
        updated += 1
    if updated:
        _save_all(user_id, data)
    return updated


def merge_profile_into_crm_record(
    crm_record: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    from crm import _merge_profile_fields

    if not isinstance(crm_record, dict):
        raise CrmStorageError("CRM 고객 자료 형식이 올바르지 않아 원본을 보존했습니다.")
    result = deepcopy(crm_record)
    result["_v44_profile"] = _merge_profile_fields(result.get("_v44_profile", {}), profile)
    return result


def get_profile_summary(user_id: str) -> dict[str, int]:
    from crm import load_crm_data

    data = _load_all(user_id)
    for key, record in load_crm_data(user_id).get("customers", {}).items():
        if isinstance(record, dict) and isinstance(record.get("_v44_profile"), dict):
            data[key] = record["_v44_profile"]
    result = {
        "high_priority": 0,
        "active_pipeline": 0,
        "completed": 0,
    }
    for profile in data.values():
        if not isinstance(profile, dict):
            continue
        try:
            if int(profile.get("priority", 0)) >= 4:
                result["high_priority"] += 1
        except Exception:
            pass

        stage = profile.get("pipeline_stage", "")
        if stage == "계약완료":
            result["completed"] += 1
        elif stage not in {"", "신규", "보류"}:
            result["active_pipeline"] += 1
    return result
