"""
OASIS CRM utilities (v3.2.0)
회원별 고객 상태, 상담메모, 다음 액션, 타임라인을 JSON 파일로 관리한다.
기존 고객DB 엑셀 구조는 변경하지 않고 CRM 보조 데이터만 별도 저장한다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT_DIR = Path(__file__).parent
USER_DATA_DIR = ROOT_DIR / "user_data"

STATUS_OPTIONS = [
    "신규",
    "상담중",
    "자료요청",
    "제안서 발송",
    "신청준비",
    "신청완료",
    "계약완료",
    "보류",
]

ACTION_OPTIONS = [
    "전화",
    "방문",
    "카톡/문자",
    "자료요청",
    "제안서 작성",
    "신청서 준비",
    "후속관리",
    "없음",
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_user_id(user_id: str) -> str:
    safe = str(user_id or "default").strip() or "default"
    return "".join(ch for ch in safe if ch.isalnum() or ch in ("-", "_", "."))


def get_crm_file_path(user_id: str) -> Path:
    user_dir = USER_DATA_DIR / _safe_user_id(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir / "crm_data.json"


def load_crm_data(user_id: str) -> Dict[str, Any]:
    path = get_crm_file_path(user_id)
    if not path.exists():
        return {"customers": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"customers": {}}
        data.setdefault("customers", {})
        return data
    except Exception:
        return {"customers": {}}


def save_crm_data(user_id: str, data: Dict[str, Any]) -> None:
    path = get_crm_file_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def make_customer_key(company_name: Any = "", business_no: Any = "") -> str:
    biz = str(business_no or "").strip().replace("-", "")
    company = str(company_name or "").strip()
    if biz and biz.lower() != "nan":
        return f"biz:{biz}"
    if company and company.lower() != "nan":
        return f"company:{company}"
    return "unknown"


def get_customer_record(user_id: str, customer_key: str) -> Dict[str, Any]:
    data = load_crm_data(user_id)
    customers = data.setdefault("customers", {})
    record = customers.get(customer_key, {})
    if not isinstance(record, dict):
        record = {}
    record.setdefault("status", "신규")
    record.setdefault("next_action", "없음")
    record.setdefault("next_date", "")
    record.setdefault("memo", "")
    record.setdefault("timeline", [])
    return record


def upsert_customer_record(
    user_id: str,
    customer_key: str,
    company_name: str = "",
    business_no: str = "",
    status: str = "신규",
    next_action: str = "없음",
    next_date: str = "",
    memo: str = "",
    event_title: str = "CRM 정보 수정",
    event_detail: str = "",
) -> Tuple[bool, str]:
    data = load_crm_data(user_id)
    customers = data.setdefault("customers", {})
    record = customers.get(customer_key, {})
    if not isinstance(record, dict):
        record = {}

    is_new = not bool(record)
    record.update({
        "company_name": company_name,
        "business_no": business_no,
        "status": status or "신규",
        "next_action": next_action or "없음",
        "next_date": str(next_date or ""),
        "memo": memo or "",
        "updated_at": _now(),
    })
    if is_new:
        record["created_at"] = _now()

    timeline = record.setdefault("timeline", [])
    if event_detail:
        timeline.insert(0, {
            "at": _now(),
            "title": event_title,
            "detail": event_detail,
        })
        record["timeline"] = timeline[:80]

    customers[customer_key] = record
    save_crm_data(user_id, data)
    return True, "CRM 정보가 저장되었습니다."


def append_timeline_event(user_id: str, customer_key: str, title: str, detail: str) -> Tuple[bool, str]:
    data = load_crm_data(user_id)
    customers = data.setdefault("customers", {})
    record = customers.get(customer_key, {})
    if not isinstance(record, dict):
        record = {}
    record.setdefault("status", "신규")
    timeline = record.setdefault("timeline", [])
    timeline.insert(0, {"at": _now(), "title": title, "detail": detail})
    record["timeline"] = timeline[:80]
    record["updated_at"] = _now()
    customers[customer_key] = record
    save_crm_data(user_id, data)
    return True, "타임라인이 추가되었습니다."


def get_status_for_customer(user_id: str, customer_key: str) -> str:
    return get_customer_record(user_id, customer_key).get("status", "신규")


def get_crm_summary(user_id: str) -> Dict[str, int]:
    data = load_crm_data(user_id)
    summary: Dict[str, int] = {status: 0 for status in STATUS_OPTIONS}
    for record in data.get("customers", {}).values():
        if isinstance(record, dict):
            status = record.get("status", "신규") or "신규"
            summary[status] = summary.get(status, 0) + 1
    return summary


def delete_customer_record(user_id: str, customer_key: str) -> Tuple[bool, str]:
    """CRM 보조 데이터에서 특정 고객 기록을 삭제한다.

    고객DB 엑셀 행 삭제 시 상담메모/타임라인이 고아 데이터로 남지 않도록 함께 정리한다.
    """
    data = load_crm_data(user_id)
    customers = data.setdefault("customers", {})
    if customer_key in customers:
        del customers[customer_key]
        save_crm_data(user_id, data)
        return True, "CRM 보조 기록도 함께 삭제되었습니다."
    return True, "삭제할 CRM 보조 기록이 없습니다."
