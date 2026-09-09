"""Small editor guard shared by the two existing CRM editing screens."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
from datetime import date, datetime, timezone
import uuid

from crm import get_customer_record, load_crm_data, mutate_crm_data
from crm_sync_protocol import cloud_content, event_signature, merge_events
from crm_cloud_merge import valid_snapshot
from crm_file_store import CrmStorageError


def _preserve_draft(user_id, customer_key, changes=None):
    def preserve(data):
        current = data["customers"].get(customer_key, {})
        draft = cloud_content(current)
        if changes is not None:
            changes_copy = deepcopy(changes)
            if isinstance(changes_copy.get("_v44_profile"), dict):
                profile = deepcopy(draft.get("_v44_profile", {}))
                if not isinstance(profile, dict):
                    raise CrmStorageError("기존 CRM 확장정보 형식이 올바르지 않아 원본을 보존했습니다.")
                profile.update(changes_copy["_v44_profile"])
                changes_copy["_v44_profile"] = profile
            draft.update(changes_copy)
        data.setdefault("_local_conflicts", []).append({
            "id": uuid.uuid4().hex, "at": datetime.now(timezone.utc).isoformat(),
            "customer_key": customer_key, "attempted_changes": draft,
            "event_title": "최신본 조회 전 보관한 편집 초안",
        })
    mutate_crm_data(user_id, preserve)


def _enterprise_widget_draft(st, scope, epoch, record):
    suffix = f"{scope}:{epoch}"
    changes = {}
    for prefix, field in (("status", "status"), ("action", "next_action"),
                          ("next_date", "next_date"), ("memo", "memo")):
        key = f"enterprise_{prefix}:{suffix}"
        if key in st.session_state:
            value = st.session_state[key]
            changes[field] = value.isoformat() if isinstance(value, (date, datetime)) else value
    profile = deepcopy(record.get("_v44_profile", {}))
    for prefix, field in (("pipeline", "pipeline_stage"), ("priority", "priority"), ("manager", "assigned_manager")):
        key = f"enterprise_{prefix}:{suffix}"
        if key in st.session_state:
            profile[field] = st.session_state[key]
    if profile:
        changes["_v44_profile"] = profile
    return changes


def _adopt_latest(user_id, customer_key):
    def adopt(data):
        current = data["customers"].get(customer_key, {})
        latest = current.get("_cloud_latest")
        version = current.get("_cloud_latest_version")
        if (not valid_snapshot(latest) or type(version) is not int or version < 1
                or version < int(current.get("_cloud_version", 0) or 0)):
            return False
        restored = deepcopy(latest)
        remote_events = merge_events(latest.get("timeline"), latest.get("timelines"))
        restored["timeline"] = merge_events(remote_events, current.get("timeline"), current.get("timelines"))
        pending_events = Counter(event_signature(event) for event in restored["timeline"]) - Counter(
            event_signature(event) for event in remote_events)
        restored.update({"_cloud_base": deepcopy(latest), "_cloud_version": version,
                         "_sync_state": "pending" if pending_events else "synced",
                         "_local_revision": int(current.get("_local_revision", 0) or 0) + 1})
        data["customers"][customer_key] = restored
        return True
    return mutate_crm_data(user_id, adopt)


def reload_editor(st, user_id, customer_key, namespace, *, changes=None, adopt=False):
    """Preserve submitted/server-known input, then explicitly pull and reload."""
    scope = f"crm-edit:{namespace}:{user_id}:{customer_key}"
    try:
        _preserve_draft(user_id, customer_key, changes)
    except CrmStorageError as exc:
        st.warning(str(exc))
        return
    if namespace in {"enterprise", "customer"}:
        from cloud_crm_restore import restore_crm_from_cloud
        result = restore_crm_from_cloud(user_id, force=True)
        if not result.get("ok"):
            st.warning("편집 초안은 보관했습니다. 클라우드 최신본 조회에 실패하여 현재 입력을 유지합니다.")
            return
    current = get_customer_record(user_id, customer_key)
    if adopt or (namespace == "customer" and current.get("_sync_state") == "conflict"):
        if not _adopt_latest(user_id, customer_key):
            st.warning("확인된 클라우드 최신본이 없어 현재 초안을 유지합니다.")
            return
    st.session_state.pop(scope + ":revision", None)
    st.session_state[scope + ":epoch"] = int(st.session_state.get(scope + ":epoch", 0)) + 1
    st.rerun()


def editor_guard(st, user_id: str, customer_key: str, record: dict, namespace: str) -> tuple[str, int]:
    scope = f"crm-edit:{namespace}:{user_id}:{customer_key}"
    epoch = int(st.session_state.get(scope + ":epoch", 0))
    revision_key = scope + ":revision"
    revision = int(st.session_state.setdefault(revision_key, int(record.get("_local_revision", 0) or 0)))
    if record.get("_sync_state") == "conflict":
        st.warning("다른 기기의 CRM 변경과 충돌했습니다. 내 입력 초안은 보존되어 있습니다.")
        if namespace != "customer" and st.button("클라우드 최신본 불러오기 · 내 초안 보관", key=scope + ":cloud"):
            changes = _enterprise_widget_draft(st, scope, epoch, record) if namespace == "enterprise" else None
            reload_editor(st, user_id, customer_key, namespace, changes=changes, adopt=True)
    if namespace == "customer":
        st.caption("입력 중인 내용까지 보관하려면 아래 양식의 ‘초안 보관 후 최신본 불러오기’를 누르세요.")
    elif st.button("최신 CRM 다시 불러오기", key=scope + ":reload"):
        changes = _enterprise_widget_draft(st, scope, epoch, record) if namespace == "enterprise" else None
        reload_editor(st, user_id, customer_key, namespace, changes=changes)
    drafts = [d for d in load_crm_data(user_id).get("_local_conflicts", [])
              if isinstance(d, dict) and d.get("customer_key") == customer_key]
    if drafts:
        with st.expander(f"저장 충돌로 보관된 내 초안 {len(drafts)}건", expanded=False):
            for draft in reversed(drafts):
                changes = draft.get("attempted_changes", {})
                st.caption(draft.get("at", ""))
                # Only this owner/customer's editable fields; never cloud auth metadata.
                st.write({k: v for k, v in changes.items()
                          if k in {"status", "next_action", "next_date", "memo", "_v44_profile"}})
    return f"{scope}:{epoch}", revision


def editor_saved(st, user_id: str, customer_key: str, namespace: str) -> None:
    scope = f"crm-edit:{namespace}:{user_id}:{customer_key}"
    record = get_customer_record(user_id, customer_key)
    st.session_state[scope + ":revision"] = int(record.get("_local_revision", 0) or 0)


def show_sync_result(st, result: tuple[bool, str]) -> None:
    ok, message = result
    (st.success if ok else st.warning)(message)
