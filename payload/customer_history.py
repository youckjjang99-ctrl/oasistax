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


def get_customer_history(
    user_id: str,
    business_no: str,
) -> list[dict[str, Any]]:
    all_history = _load_all(user_id)
    return all_history.get(
        _normalize_business_no(business_no),
        [],
    ) or []


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
