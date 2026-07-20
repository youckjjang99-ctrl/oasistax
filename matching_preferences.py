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


def _safe_text(value: Any, max_length: int = 300) -> str:
    """Normalize user text before local JSON/Supabase persistence."""
    text = str(value or "")
    # Remove control characters that can break JSON, Excel, logs or reruns.
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text[:max_length]


def split_keywords(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        source = value
    else:
        source = re.split(r"[,/\n\r;|]+", str(value or ""))

    result: list[str] = []
    seen: set[str] = set()
    for item in source:
        keyword = _safe_text(item, max_length=80)
        normalized = keyword.casefold()
        if keyword and normalized not in seen:
            result.append(keyword)
            seen.add(normalized)
        if len(result) >= 30:
            break
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
        "업체명": _safe_text(company_name, 120),
        "매칭키워드": split_keywords(matching_keywords),
        "관심지원분야": split_keywords(interest_fields),
        "제외키워드": split_keywords(exclusion_keywords),
        "자금사용목적": _safe_text(fund_purpose, 200),
        "투자예정금액": _safe_text(planned_amount, 80),
        "투자예정시기": _safe_text(planned_timing, 80),
        "수정일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    data[key] = record
    _save_all(user_id, data)

    # Local persistence is the primary success condition. A temporary cloud
    # failure must not cancel customer registration or keyword saving.
    try:
        sync_matching_preferences(user_id, key, record)
    except Exception as sync_error:
        record["_cloud_sync_warning"] = str(sync_error)

    return record


def save_policy_recommendations(
    user_id: str,
    business_no: Any,
    company_name: str,
    minimum_score: int,
    recommendations: list[dict[str, Any]],
) -> dict[str, Any]:
    key = normalize_business_no(business_no)
    if len(re.sub(r"[^0-9]", "", key)) != 10:
        raise ValueError(
            "사업자등록번호가 확인되어야 정책자금 추천을 저장할 수 있습니다."
        )

    data = _load_all(user_id)
    current = data.get(key, {})
    if not isinstance(current, dict):
        current = {}

    saved_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in recommendations:
        if not isinstance(item, dict):
            continue
        title = _safe_text(item.get("title", ""), 200)
        if not title:
            continue
        record_id = _safe_text(item.get("id", ""), 80) or title.casefold()
        if record_id in seen:
            continue
        seen.add(record_id)
        saved_items.append(
            {
                "id": record_id,
                "title": title,
                "score": int(item.get("score", 0) or 0),
                "grade": _safe_text(item.get("grade", ""), 40),
                "category": _safe_text(
                    item.get("category")
                    or item.get("classification")
                    or item.get("분류"),
                    80,
                ),
                "agency": _safe_text(item.get("agency", ""), 120),
                "summary": _safe_text(item.get("summary", ""), 500),
                "target": _safe_text(item.get("target", ""), 300),
                "end_date": _safe_text(item.get("end_date", ""), 40),
                "url": _safe_text(item.get("url", ""), 500),
                "evidence": [
                    _safe_text(value, 200)
                    for value in (item.get("evidence", []) or [])[:5]
                    if _safe_text(value, 200)
                ],
            }
        )
        if len(saved_items) >= 50:
            break

    record = dict(current)
    record.update(
        {
            "사업자등록번호": key,
            "업체명": _safe_text(
                company_name or current.get("업체명", ""),
                120,
            ),
            "저장정책자금_최소점수": max(
                0,
                min(int(minimum_score), 100),
            ),
            "저장정책자금": saved_items,
            "저장정책자금_건수": len(saved_items),
            "저장정책자금_저장일시": datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "수정일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )

    data[key] = record
    _save_all(user_id, data)

    try:
        sync_matching_preferences(user_id, key, record)
    except Exception as sync_error:
        record["_cloud_sync_warning"] = str(sync_error)

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
