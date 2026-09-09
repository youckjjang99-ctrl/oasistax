"""
OASIS CRM utilities (v3.2.0)
회원별 고객 상태, 상담메모, 다음 액션, 타임라인을 JSON 파일로 관리한다.
기존 고객DB 엑셀 구조는 변경하지 않고 CRM 보조 데이터만 별도 저장한다.
"""
from __future__ import annotations

import json
import hashlib
import uuid
from copy import deepcopy
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from crm_file_store import (
    CrmConflictError, CrmStorageError, atomic_write_json, locked_json,
    read_json_object,
)

from performance_cache import cache_generation, invalidate_cache

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
    result = "".join(ch for ch in safe if ch.isalnum() or ch in ("-", "_", "."))
    if result in {"", ".", ".."}:
        raise CrmStorageError("사용자 ID를 확인해 주세요.")
    return result


def get_crm_file_path(user_id: str) -> Path:
    user_dir = USER_DATA_DIR / _safe_user_id(user_id)
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise CrmStorageError("CRM 저장 공간에 접근하지 못했습니다. 원본은 변경하지 않았습니다.") from None
    return user_dir / "crm_data.json"


@lru_cache(maxsize=256)
def _load_crm_data_cached(
    path_str: str,
    mtime_ns: int,
    file_size: int,
    generation: int,
) -> Dict[str, Any]:
    """Read one immutable CRM snapshot keyed by the exact file revision."""
    del mtime_ns, file_size, generation
    return _CrmSnapshot(_read_crm(Path(path_str)))


class _CrmSnapshot(dict):
    """Optimistic whole-file token kept off disk and out of cloud payloads."""

    def __init__(self, value: dict[str, Any]):
        super().__init__(value)
        self.base_revision = _document_revision(value)


def _document_revision(data: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    ).hexdigest()


def _record_revision(record: dict[str, Any]) -> int:
    value = record.get("_local_revision", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CrmStorageError("CRM 저장 버전 형식이 올바르지 않아 원본을 보존했습니다.")
    return value


def _merge_profile_fields(existing: Any, changes: Any) -> dict[str, Any]:
    """Edit the supported profile fields without erasing extension data."""
    if not isinstance(existing, dict) or not isinstance(changes, dict):
        raise CrmStorageError("CRM 확장정보 형식이 올바르지 않아 원본을 보존하고 저장을 중단했습니다.")
    merged = deepcopy(existing)
    editable = {"pipeline_stage", "priority", "assigned_manager", "updated_at"}
    for key, value in changes.items():
        if key in editable or key not in merged:
            merged[key] = deepcopy(value)
    return merged


def _read_crm(path: Path) -> dict[str, Any]:
    data = read_json_object(path)
    data.setdefault("customers", {})
    if not isinstance(data["customers"], dict):
        raise CrmStorageError("CRM 고객 자료 형식이 올바르지 않아 저장을 중단했습니다.")
    return data


def load_crm_data(user_id: str) -> Dict[str, Any]:
    path = get_crm_file_path(user_id)
    with locked_json(path):
        if not path.exists():
            return _CrmSnapshot({"customers": {}})
        stat = path.stat()
        cached = _load_crm_data_cached(
            str(path),
            int(stat.st_mtime_ns),
            int(stat.st_size),
            cache_generation("crm", _safe_user_id(user_id)),
        )
        # Callers update nested records, so never expose the cached object.
        return deepcopy(cached)


def save_crm_data(user_id: str, data: Dict[str, Any]) -> None:
    path = get_crm_file_path(user_id)
    with locked_json(path):
        current = _read_crm(path)
        expected = getattr(data, "base_revision", None)
        if expected is None:
            # Plain dictionaries may initialize an empty store, never replace
            # an existing document without having read its revision.
            if current != {"customers": {}}:
                raise CrmConflictError("최신 CRM 자료를 다시 불러온 후 저장해 주세요. 기존 자료는 보존했습니다.")
        elif expected != _document_revision(current):
            raise CrmConflictError("다른 작업에서 CRM 자료가 변경되었습니다. 새로 불러온 후 저장해 주세요.")
        if not isinstance(data.get("customers"), dict):
            raise CrmStorageError("CRM 고객 자료 형식이 올바르지 않습니다.")
        atomic_write_json(path, dict(data))
        if isinstance(data, _CrmSnapshot):
            data.base_revision = _document_revision(data)
        invalidate_cache("crm", _safe_user_id(user_id))


def mutate_crm_data(user_id: str, callback: Callable[[dict[str, Any]], Any]) -> Any:
    """Run an in-place mutation against the latest file and return its result.

    A callback exception aborts the transaction. Network calls do not belong
    inside this bounded local-file transaction.
    """
    path = get_crm_file_path(user_id)
    with locked_json(path):
        data = _read_crm(path)
        before = _document_revision(data)
        result = callback(data)
        if not isinstance(data.get("customers"), dict):
            raise CrmStorageError("CRM 고객 자료 형식이 올바르지 않습니다.")
        if _document_revision(data) != before:
            atomic_write_json(path, data)
            invalidate_cache("crm", _safe_user_id(user_id))
        return result


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
    record.setdefault("_local_revision", 0)
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
    *,
    expected_revision: int | None = None,
    profile: dict[str, Any] | None = None,
) -> Tuple[bool, str]:
    changes = {
        "company_name": company_name,
        "business_no": business_no,
        "status": status or "신규",
        "next_action": next_action or "없음",
        "next_date": str(next_date or ""),
        "memo": memo or "",
    }
    if profile is not None:
        changes["_v44_profile"] = deepcopy(profile)

    def apply(data: dict[str, Any]) -> Tuple[bool, str]:
        customers = data["customers"]
        record = customers.get(customer_key, {})
        if not isinstance(record, dict):
            raise CrmStorageError("CRM 고객 자료 형식이 올바르지 않아 원본을 보존했습니다.")
        revision = _record_revision(record)
        if expected_revision is not None and revision != expected_revision:
            conflicts = data.setdefault("_local_conflicts", [])
            if not isinstance(conflicts, list):
                raise CrmStorageError("CRM 충돌 이력 형식이 올바르지 않아 저장을 중단했습니다.")
            conflicts.append({
                "id": uuid.uuid4().hex, "at": _now(),
                "customer_key": customer_key, "expected_revision": expected_revision,
                "actual_revision": revision, "attempted_changes": deepcopy(changes),
                "event_title": event_title, "event_detail": event_detail,
            })
            return False, "다른 작업에서 고객 정보가 변경되었습니다. 입력 내용은 충돌 이력에 보관했습니다. 최신 정보를 확인한 후 다시 저장해 주세요."
        timeline = record.setdefault("timeline", [])
        if not isinstance(timeline, list):
            raise CrmStorageError("CRM 상담 이력 형식이 올바르지 않아 원본을 보존했습니다.")
        safe_changes = deepcopy(changes)
        if "_v44_profile" in safe_changes:
            safe_changes["_v44_profile"] = _merge_profile_fields(
                record.get("_v44_profile", {}), safe_changes["_v44_profile"],
            )
        if not record.get("created_at"):
            record["created_at"] = _now()
        record.update(safe_changes)
        record["updated_at"] = _now()
        record["_local_revision"] = revision + 1
        record["_sync_state"] = "pending"
        if event_detail:
            timeline.insert(0, {"id": uuid.uuid4().hex, "at": _now(), "title": event_title, "detail": event_detail})
        customers[customer_key] = record
        return True, "CRM 정보가 저장되었습니다."

    try:
        return mutate_crm_data(user_id, apply)
    except CrmStorageError as exc:
        return False, str(exc)


def append_timeline_event(user_id: str, customer_key: str, title: str, detail: str) -> Tuple[bool, str]:
    def apply(data: dict[str, Any]) -> Tuple[bool, str]:
        customers = data["customers"]
        record = customers.get(customer_key, {})
        if not isinstance(record, dict):
            raise CrmStorageError("CRM 고객 자료 형식이 올바르지 않아 원본을 보존했습니다.")
        record.setdefault("status", "신규")
        timeline = record.setdefault("timeline", [])
        if not isinstance(timeline, list):
            raise CrmStorageError("CRM 상담 이력 형식이 올바르지 않아 원본을 보존했습니다.")
        timeline.insert(0, {"id": uuid.uuid4().hex, "at": _now(), "title": title, "detail": detail})
        record["updated_at"] = _now()
        record["_local_revision"] = _record_revision(record) + 1
        record["_sync_state"] = "pending"
        customers[customer_key] = record
        return True, "타임라인이 추가되었습니다."

    try:
        return mutate_crm_data(user_id, apply)
    except CrmStorageError as exc:
        return False, str(exc)


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


def get_due_action_summary(user_id: str, today: str | None = None) -> Dict[str, Any]:
    """오늘 예정, 기한 경과, 향후 7일 고객을 집계한다."""
    from datetime import date, datetime, timedelta

    base_date = date.today()
    if today:
        try:
            base_date = datetime.strptime(today, "%Y-%m-%d").date()
        except ValueError:
            pass

    result = {
        "today": [],
        "overdue": [],
        "week": [],
    }

    data = load_crm_data(user_id)
    for customer_key, record in data.get("customers", {}).items():
        if not isinstance(record, dict):
            continue

        raw_date = str(record.get("next_date", "") or "").strip()
        if not raw_date:
            continue

        try:
            due = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        item = {
            "customer_key": customer_key,
            "company_name": record.get("company_name", ""),
            "business_no": record.get("business_no", ""),
            "next_action": record.get("next_action", "없음"),
            "next_date": raw_date[:10],
            "status": record.get("status", "신규"),
        }

        if due == base_date:
            result["today"].append(item)
        elif due < base_date:
            result["overdue"].append(item)
        elif base_date < due <= base_date + timedelta(days=7):
            result["week"].append(item)

    return result


def get_home_dashboard_summary(
    user_id: str,
    today: str | None = None,
) -> tuple[Dict[str, int], Dict[str, Any]]:
    """Build both home summaries from one CRM snapshot."""
    from datetime import date, datetime, timedelta

    data = load_crm_data(user_id)
    summary: Dict[str, int] = {status: 0 for status in STATUS_OPTIONS}
    due_result: Dict[str, Any] = {"today": [], "overdue": [], "week": []}

    base_date = date.today()
    if today:
        try:
            base_date = datetime.strptime(today, "%Y-%m-%d").date()
        except ValueError:
            pass

    for customer_key, record in data.get("customers", {}).items():
        if not isinstance(record, dict):
            continue
        status = record.get("status", "신규") or "신규"
        summary[status] = summary.get(status, 0) + 1

        raw_date = str(record.get("next_date", "") or "").strip()
        if not raw_date:
            continue
        try:
            due = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        except ValueError:
            continue

        item = {
            "customer_key": customer_key,
            "company_name": record.get("company_name", ""),
            "business_no": record.get("business_no", ""),
            "next_action": record.get("next_action", "없음"),
            "next_date": raw_date[:10],
            "status": status,
        }
        if due == base_date:
            due_result["today"].append(item)
        elif due < base_date:
            due_result["overdue"].append(item)
        elif due <= base_date + timedelta(days=7):
            due_result["week"].append(item)

    return summary, due_result
