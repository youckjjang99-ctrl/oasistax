from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from cloud_db import CloudDatabase, cloud_is_configured
from utils import get_user_dirs


TABLE_HISTORY = "oasis_customer_history"
TRACKED_FIELDS = [
    "매출액",
    "영업이익",
    "당기순이익",
    "자산총계",
    "부채총계",
    "자본총계",
    "종업원수",
    "사업장 소재지",
]


def _normalize_business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return str(value or "").strip()


def _path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "customer_history.json"


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
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def save_customer_snapshot(
    user_id: str,
    extracted_data: dict[str, Any],
    source: str = "cretop",
) -> dict[str, Any]:
    data = dict(extracted_data or {})
    business_no = _normalize_business_no(
        data.get("사업자등록번호", "")
    )
    if not business_no:
        return {}

    all_history = _load_all(user_id)
    items = all_history.get(business_no, [])
    if not isinstance(items, list):
        items = []

    snapshot_data = {
        field: data.get(field)
        for field in TRACKED_FIELDS
    }
    snapshot = {
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "company_name": data.get("업체명", ""),
        "business_no": business_no,
        "data": snapshot_data,
    }

    # 같은 값이 연속으로 들어오면 중복 스냅샷을 만들지 않는다.
    if items and items[0].get("data") == snapshot_data:
        return items[0]

    items.insert(0, snapshot)
    all_history[business_no] = items[:50]
    _save_all(user_id, all_history)

    if cloud_is_configured():
        try:
            CloudDatabase().insert(
                TABLE_HISTORY,
                [{
                    "owner_user_id": user_id,
                    "business_no": business_no,
                    "company_name": data.get("업체명", ""),
                    "source": source,
                    "snapshot_data": snapshot_data,
                    "captured_at": snapshot["captured_at"],
                }],
            )
        except Exception:
            pass

    return snapshot



def save_customer_event(
    user_id: str,
    business_no: str,
    company_name: str,
    event_id: str,
    event_title: str,
    event_detail: str,
    occurred_at: str = "",
    source: str = "consultation",
) -> dict[str, Any]:
    normalized = _normalize_business_no(business_no)
    if not normalized:
        return {}
    all_history = _load_all(user_id)
    items = all_history.get(normalized, [])
    if not isinstance(items, list):
        items = []
    event_id = str(event_id or "").strip()
    for item in items:
        data = item.get("data", {}) if isinstance(item, dict) else {}
        if isinstance(data, dict) and str(data.get("이벤트ID", "")) == event_id:
            return item
    captured_at = occurred_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    event_data = {
        "히스토리유형": "상담",
        "이벤트ID": event_id,
        "상담제목": event_title,
        "상담내용": event_detail,
    }
    snapshot = {
        "captured_at": captured_at,
        "source": source,
        "company_name": company_name,
        "business_no": normalized,
        "data": event_data,
    }
    items.insert(0, snapshot)
    all_history[normalized] = items[:200]
    _save_all(user_id, all_history)
    if cloud_is_configured():
        try:
            CloudDatabase().insert(
                TABLE_HISTORY,
                [{
                    "owner_user_id": user_id,
                    "business_no": normalized,
                    "company_name": company_name,
                    "source": source,
                    "snapshot_data": event_data,
                    "captured_at": captured_at,
                }],
            )
        except Exception:
            pass
    return snapshot

def get_customer_history(
    user_id: str,
    business_no: str,
) -> list[dict[str, Any]]:
    normalized = _normalize_business_no(business_no)
    all_history = _load_all(user_id)
    local_items = all_history.get(normalized, []) or []
    if not isinstance(local_items, list):
        local_items = []
    cloud_items = []
    if normalized and cloud_is_configured():
        try:
            rows = CloudDatabase().select(
                TABLE_HISTORY,
                filters={"owner_user_id": user_id, "business_no": normalized},
                order="captured_at.desc",
                limit=200,
            )
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                data = row.get("snapshot_data", {})
                if isinstance(data, str):
                    data = json.loads(data)
                cloud_items.append({
                    "captured_at": row.get("captured_at", ""),
                    "source": row.get("source", ""),
                    "company_name": row.get("company_name", ""),
                    "business_no": row.get("business_no", normalized),
                    "data": data if isinstance(data, dict) else {},
                })
        except Exception:
            cloud_items = []
    merged = []
    seen = set()
    for item in cloud_items + local_items:
        if not isinstance(item, dict):
            continue
        data = item.get("data", {}) if isinstance(item.get("data"), dict) else {}
        unique_key = str(data.get("이벤트ID") or (
            str(item.get("captured_at", "")) + "|" + str(item.get("source", "")) + "|" +
            json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
        ))
        if unique_key in seen:
            continue
        seen.add(unique_key)
        merged.append(item)
    merged.sort(key=lambda item: str(item.get("captured_at", "")), reverse=True)
    if merged:
        all_history[normalized] = merged[:200]
        _save_all(user_id, all_history)
    return merged[:200]


def build_history_table(history: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for item in history:
        data = item.get("data", {})
        rows.append({
            "수집일시": item.get("captured_at", ""),
            "출처": item.get("source", ""),
            **data,
        })
    return pd.DataFrame(rows)


def build_change_summary(
    history: list[dict[str, Any]],
) -> list[str]:
    if len(history) < 2:
        return ["비교할 이전 데이터가 아직 없습니다."]

    current = history[0].get("data", {})
    previous = history[1].get("data", {})
    messages = []

    for field in [
        "매출액",
        "영업이익",
        "당기순이익",
        "자산총계",
        "부채총계",
        "자본총계",
        "종업원수",
    ]:
        try:
            current_value = float(
                str(current.get(field, "")).replace(",", "")
            )
            previous_value = float(
                str(previous.get(field, "")).replace(",", "")
            )
        except Exception:
            continue

        difference = current_value - previous_value
        if difference == 0:
            continue

        direction = "증가" if difference > 0 else "감소"
        messages.append(
            f"{field}: {abs(difference):,.0f} {direction}"
        )

    if current.get("사업장 소재지") != previous.get("사업장 소재지"):
        messages.append("사업장 소재지가 변경되었습니다.")

    return messages or ["직전 스냅샷과 주요 수치가 동일합니다."]
