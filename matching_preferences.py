from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from utils import get_user_dirs
from cloud_sync import sync_matching_preferences


INTEREST_OPTIONS = [
    "운전자금",
    "시설자금",
    "기계·설비 구입",
    "차량 구입",
    "공장 신축·증축",
    "온라인 마케팅",
    "판로·유통",
    "수출",
    "연구개발",
    "특허·인증",
    "신규채용",
    "고용유지",
    "창업·사업화",
]


def normalize_business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return str(value or "").strip()


def _storage_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "customer_matching_preferences.json"


def _load_all(user_id: str) -> dict[str, Any]:
    path = _storage_path(user_id)
    if not path.exists():
        return {}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(user_id: str, data: dict[str, Any]) -> None:
    path = _storage_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def split_keywords(value: Any) -> list[str]:
    if isinstance(value, list):
        source = value
    else:
        source = re.split(r"[,/\n;|]+", str(value or ""))

    result = []
    for item in source:
        keyword = str(item or "").strip()
        if keyword and keyword not in result:
            result.append(keyword)
    return result


def get_matching_preferences(
    user_id: str,
    business_no: Any,
) -> dict[str, Any]:
    key = normalize_business_no(business_no)
    data = _load_all(user_id)
    value = data.get(key, {}) if key else {}
    if isinstance(value, dict) and value:
        return value

    if key:
        try:
            from cloud_db import CloudDatabase, TABLE_MATCHING_PREFERENCES, cloud_is_configured
            if cloud_is_configured():
                rows = CloudDatabase().select(
                    TABLE_MATCHING_PREFERENCES,
                    filters={"owner_user_id": user_id, "business_no": key},
                    limit=1,
                )
                if rows and isinstance(rows[0], dict):
                    cloud_value = rows[0].get("preference_data", {})
                    if isinstance(cloud_value, str):
                        cloud_value = json.loads(cloud_value)
                    if isinstance(cloud_value, dict) and cloud_value:
                        data[key] = cloud_value
                        _save_all(user_id, data)
                        return cloud_value
        except Exception:
            pass
    return {}


def save_matching_preferences(
    user_id: str,
    business_no: Any,
    company_name: str = "",
    matching_keywords: Any = None,
    interest_fields: Any = None,
    exclusion_keywords: Any = None,
    fund_purpose: str = "",
    planned_amount: Any = "",
    planned_timing: str = "",
) -> dict[str, Any]:
    key = normalize_business_no(business_no)
    if len(re.sub(r"[^0-9]", "", key)) != 10:
        raise ValueError("사업자등록번호가 확인되어야 매칭설정을 저장할 수 있습니다.")

    data = _load_all(user_id)
    record = {
        "사업자등록번호": key,
        "업체명": str(company_name or "").strip(),
        "매칭키워드": split_keywords(matching_keywords),
        "관심지원분야": split_keywords(interest_fields),
        "제외키워드": split_keywords(exclusion_keywords),
        "자금사용목적": str(fund_purpose or "").strip(),
        "투자예정금액": str(planned_amount or "").strip(),
        "투자예정시기": str(planned_timing or "").strip(),
        "수정일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    data[key] = record
    _save_all(user_id, data)
    sync_matching_preferences(user_id, key, record)
    return record


def preference_summary(preferences: dict[str, Any]) -> str:
    parts = []

    keywords = split_keywords(preferences.get("매칭키워드", []))
    interests = split_keywords(preferences.get("관심지원분야", []))
    exclusions = split_keywords(preferences.get("제외키워드", []))

    if keywords:
        parts.append("매칭키워드: " + ", ".join(keywords))
    if interests:
        parts.append("관심분야: " + ", ".join(interests))
    if exclusions:
        parts.append("제외키워드: " + ", ".join(exclusions))

    purpose = str(preferences.get("자금사용목적", "") or "").strip()
    if purpose:
        parts.append("자금목적: " + purpose)

    amount = str(preferences.get("투자예정금액", "") or "").strip()
    if amount:
        parts.append("투자예정금액: " + amount)

    timing = str(preferences.get("투자예정시기", "") or "").strip()
    if timing:
        parts.append("투자예정시기: " + timing)

    return " / ".join(parts)
