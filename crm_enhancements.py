from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from cloud_db import CloudDatabase, TABLE_CRM, cloud_is_configured
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
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(user_id: str, data: dict[str, Any]) -> None:
    path = _path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_crm_profile(
    user_id: str,
    customer_key: str,
    business_no: str = "",
) -> dict[str, Any]:
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
                    data[customer_key] = cloud_profile
                    _save_all(user_id, data)
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
    data = _load_all(user_id)
    record = {
        "pipeline_stage": pipeline_stage,
        "priority": str(priority),
        "assigned_manager": str(assigned_manager or "").strip(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    data[customer_key] = record
    _save_all(user_id, data)
    return record


def merge_profile_into_crm_record(
    crm_record: dict[str, Any],
    profile: dict[str, Any],
) -> dict[str, Any]:
    result = dict(crm_record or {})
    result["_v44_profile"] = dict(profile or {})
    return result


def get_profile_summary(user_id: str) -> dict[str, int]:
    data = _load_all(user_id)
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
