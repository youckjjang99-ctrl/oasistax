"""Owner-scoped, conflict-safe CRM pull synchronization (no cloud writes)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
import time
from typing import Any
import uuid

from cloud_db import CloudDatabase, TABLE_CUSTOMERS, cloud_is_configured
from crm import load_crm_data, make_customer_key, mutate_crm_data
from crm_cloud_merge import merge_cloud_record, valid_snapshot
from crm_enhancements import _load_all as load_legacy_profiles
from crm_file_store import CrmStorageError
from crm_sync_protocol import merge_events

PULL_TTL_SECONDS = 60
FULL_PULL_SECONDS = 86400
OVERLAP_SECONDS = 300
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _timestamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("crm_sync_timestamp_timezone_required")
    return parsed.astimezone(timezone.utc)


def _cursor(row: dict) -> tuple[datetime, int]:
    return _timestamp(row["updated_at"]), uuid.UUID(str(row["id"])).int


def _pull_state(data: dict) -> dict:
    state = data.get("_cloud_pull", {})
    if not isinstance(state, dict):
        raise CrmStorageError("CRM 동기화 상태 형식이 올바르지 않아 원본을 보존했습니다.")
    return state


def _valid_row(row: dict, owner: str) -> None:
    if (not isinstance(row, dict) or row.get("owner_user_id") != owner
            or not isinstance(row.get("business_no"), str) or not row["business_no"].strip()
            or type(row.get("crm_version")) is not int or row["crm_version"] < 1
            or not valid_snapshot(row.get("crm_data"))):
        raise ValueError("crm_sync_unconfirmed_row")
    _cursor(row)


def restore_crm_from_cloud(user_id: str, *, force: bool = False,
                           page_size: int = 500, max_rows: int = 50000) -> dict[str, Any]:
    """Pull fresh CRM rows, preserving pending edits and all original history.

    Checkpoints advance only after strict EOF and local commit. An overlap
    handles late commits; daily full pulls recover older transaction timestamps.
    Existing customer workbooks and customer-table rows are never mutated.
    """
    result = {"ok": False, "status": "unavailable", "restored": 0, "profiles": 0,
              "timelines": 0, "conflicts": 0, "message": ""}
    if not isinstance(user_id, str) or not user_id.strip():
        result["message"] = "사용자 ID가 없습니다."
        return result
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(user_id, threading.Lock())
    if not lock.acquire(blocking=False):
        result.update(status="busy", message="CRM 동기화가 진행 중입니다.")
        return result
    now = time.time()
    try:
        local = load_crm_data(user_id)
        state = deepcopy(_pull_state(local))
        attempted_at = float(state.get("attempted_at", 0) or 0)
        if not force and 0 <= now - attempted_at < PULL_TTL_SECONDS:
            result.update(ok=bool(state.get("ok")), status="cached", message=state.get("message", ""))
            return result
        if not cloud_is_configured():
            result["message"] = "Supabase가 설정되지 않아 CRM 동기화를 진행하지 못했습니다."
            return result

        def attempted(data):
            latest = _pull_state(data)
            latest["attempted_at"] = now
            data["_cloud_pull"] = latest

        mutate_crm_data(user_id, attempted)
        database = CloudDatabase()
        previous_checkpoint = state.get("checkpoint")
        full = force or not previous_checkpoint or now - float(state.get("full_completed_at", 0) or 0) >= FULL_PULL_SECONDS
        since = None if full else (
            _timestamp(previous_checkpoint["updated_at"]) - timedelta(seconds=OVERLAP_SECONDS)
        ).isoformat()
        rows, seen, cursor = [], set(), None
        page_size, max_rows = max(1, min(int(page_size), 1000)), max(1, int(max_rows))
        while True:
            requested = min(page_size, max_rows - len(rows) + 1)
            page = database.select_crm_sync_page(user_id, since=since, after=cursor, limit=requested)
            if not isinstance(page, list) or len(page) > requested:
                raise ValueError("crm_sync_invalid_page")
            if not page:
                break
            for row in page:
                _valid_row(row, user_id)
                if str(row["id"]) in seen or (rows and _cursor(row) <= _cursor(rows[-1])):
                    raise ValueError("crm_sync_invalid_page_order")
                seen.add(str(row["id"]))
                rows.append(row)
                if len(rows) > max_rows:
                    result["status"] = "partial"
                    raise ValueError("crm_sync_row_limit")
            cursor = (str(rows[-1]["updated_at"]), str(rows[-1]["id"]))
            # A server-imposed smaller row limit means short pages are not EOF.

        profiles = load_legacy_profiles(user_id)
        prepared, keys = [], set()
        for row in rows:
            remote = deepcopy(row["crm_data"])
            key = make_customer_key(business_no=row["business_no"])
            if key in keys:
                raise ValueError("crm_sync_ambiguous_identity")
            keys.add(key)
            existing = local["customers"].get(key, {})
            remote.setdefault("business_no", row["business_no"])
            if not remote.get("company_name"):
                name = existing.get("company_name", "") if isinstance(existing, dict) else ""
                if not name:
                    names = database.select(TABLE_CUSTOMERS,
                        filters={"owner_user_id": user_id, "business_no": row["business_no"]},
                        columns="company_name", limit=1)
                    name = str(names[0].get("company_name") or "") if names else ""
                if name:
                    remote["company_name"] = name
            remote.setdefault("updated_at", row["updated_at"])
            prepared.append((key, remote, row["crm_version"]))

        def apply(data):
            latest_state = _pull_state(data)
            for key, remote, version in prepared:
                current = data["customers"].get(key, {})
                if not isinstance(current, dict):
                    raise CrmStorageError("CRM 자료 형식이 올바르지 않아 원본을 보존했습니다.")
                current = deepcopy(current)
                if not current.get("_v44_profile") and isinstance(profiles.get(key), dict) and profiles[key]:
                    current["_v44_profile"] = deepcopy(profiles[key])
                merged, outcome = merge_cloud_record(current, remote, version)
                if outcome != "unchanged":
                    data["customers"][key] = merged
                    result["restored"] += 1
                    result["profiles"] += int(isinstance(merged.get("_v44_profile"), dict))
                    result["timelines"] += max(0, len(merge_events(merged.get("timeline"))) -
                                                len(merge_events(current.get("timeline"), current.get("timelines"))))
                result["conflicts"] += int(merged.get("_sync_state") == "conflict")
            latest_checkpoint = latest_state.get("checkpoint")
            checkpoint = {"updated_at": rows[-1]["updated_at"], "id": str(rows[-1]["id"])} if rows else None
            if checkpoint and (not latest_checkpoint or _cursor(checkpoint) > _cursor(latest_checkpoint)):
                latest_state["checkpoint"] = checkpoint
            result.update(ok=True, status="conflict" if result["conflicts"] else "synced",
                          message="CRM 변경사항을 확인했습니다. 충돌 초안은 보존했습니다." if result["conflicts"]
                          else "CRM 최신 변경사항을 동기화했습니다.")
            latest_state.update(ok=True, completed_at=now,
                                attempted_at=max(now, float(latest_state.get("attempted_at", 0))),
                                message=result["message"])
            if full:
                latest_state["full_completed_at"] = now
            data["_cloud_pull"] = latest_state

        mutate_crm_data(user_id, apply)
        return result
    except Exception:
        result.update(ok=False, status="partial" if result["status"] == "partial" else "error",
                      restored=0, profiles=0, timelines=0, conflicts=0,
                      message="CRM 조회 범위가 커서 동기화를 완료하지 못했습니다. 기존 자료는 보존했습니다."
                      if result["status"] == "partial" else "Supabase CRM 조회 실패")
        try:
            def failed(data):
                state = _pull_state(data)
                state.update(ok=False, attempted_at=now, message=result["message"])
                data["_cloud_pull"] = state
            mutate_crm_data(user_id, failed)
        except Exception:
            pass
        return result
    finally:
        lock.release()
