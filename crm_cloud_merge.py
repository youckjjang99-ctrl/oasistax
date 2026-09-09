"""Lossless three-way CRM pulls; no file, network, or UI side effects."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from crm_file_store import CrmStorageError
from crm_sync_protocol import cloud_content, editable_content, event_signature, merge_events

_MISSING = object()


def valid_snapshot(value: Any) -> bool:
    return isinstance(value, dict) and all(
        name not in value or (isinstance(value[name], list)
                              and all(isinstance(event, dict) for event in value[name]))
        for name in ("timeline", "timelines")
    )


def _version(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise CrmStorageError("CRM 동기화 버전 형식이 올바르지 않아 원본을 보존했습니다.")
    return value


def _fields(base: dict, local: dict, remote: dict) -> tuple[dict, bool]:
    """Missing fields never mean deletion; merge independent nested changes."""
    merged = {}
    conflict = False
    for key in local.keys() | remote.keys():
        before, own, other = base.get(key, _MISSING), local.get(key, _MISSING), remote.get(key, _MISSING)
        if own is _MISSING:
            merged[key] = deepcopy(other)
        elif other is _MISSING:
            merged[key] = deepcopy(own)
        elif own == other:
            merged[key] = deepcopy(own)
        elif isinstance(own, dict) and isinstance(other, dict) and isinstance(before, dict):
            merged[key], nested_conflict = _fields(before, own, other)
            conflict |= nested_conflict
        elif own == before:
            merged[key] = deepcopy(other)
        elif other == before:
            merged[key] = deepcopy(own)
        else:
            merged[key] = deepcopy(own)
            conflict = True
    return merged, conflict


def _possible_aba(base: dict, local: dict, remote: dict) -> bool:
    for key in base.keys() & local.keys() & remote.keys():
        before, own, other = base[key], local[key], remote[key]
        if isinstance(before, dict) and isinstance(own, dict) and isinstance(other, dict):
            if _possible_aba(before, own, other):
                return True
        elif own == before and other != before:
            return True
    return False


def merge_cloud_record(current: dict, remote: dict, remote_version: int) -> tuple[dict, str]:
    """Return a preserved record and outcome: updated, conflict, or unchanged."""
    if not valid_snapshot(current) or not valid_snapshot(remote):
        raise CrmStorageError("CRM 자료 형식이 올바르지 않아 원본을 보존했습니다.")
    version = _version(remote_version)
    if not version:
        raise CrmStorageError("CRM 서버 저장 버전을 확인하지 못해 원본을 보존했습니다.")
    own_version = _version(current.get("_cloud_version", 0))
    latest_version = _version(current.get("_cloud_latest_version", 0))
    revision = _version(current.get("_local_revision", 0))
    if version < max(own_version, latest_version):
        return deepcopy(current), "unchanged"
    remote = cloud_content(remote)
    remote["timeline"] = merge_events(remote.get("timeline"), remote.get("timelines"))
    local_content = cloud_content(current)
    local_fields, remote_fields = editable_content(current), editable_content(remote)
    base = current.get("_cloud_base")
    if base is not None and not valid_snapshot(base):
        raise CrmStorageError("CRM 동기화 기준본이 올바르지 않아 원본을 보존했습니다.")
    if not current:
        merged_fields, conflict = deepcopy(remote_fields), False
    elif base is None:
        # Legacy local data has no trustworthy common ancestor. Equal fields
        # can acknowledge a prior save; different fields require review.
        merged_fields = deepcopy(local_fields)
        conflict = local_fields != remote_fields
    else:
        merged_fields, conflict = _fields(editable_content(base), local_fields, remote_fields)
        if (current.get("_sync_state") == "pending" and revision > 0
                and _possible_aba(editable_content(base), local_fields, remote_fields)):
            # A->B->A editing is indistinguishable from a timeline-only pending
            # snapshot without field revisions. Keep the draft conservatively.
            conflict = True
        if version == own_version and editable_content(base) != remote_fields:
            conflict = True  # Equal server versions must not describe different values.
    if current.get("_sync_state") == "conflict" and local_fields != remote_fields:
        conflict = True  # Only explicit adoption or real convergence resolves a draft.
    events = merge_events(remote.get("timeline"), current.get("timeline"), current.get("timelines"))
    if conflict:
        merged = deepcopy(current)
        merged["timeline"] = events
        merged.update({"_sync_state": "conflict", "_cloud_latest": deepcopy(remote),
                       "_cloud_latest_version": version, "_sync_error": "crm_pull_conflict"})
    else:
        merged = deepcopy(remote)
        merged.update(merged_fields)
        # Preserve a local creation marker even if older remote JSON lacks it.
        if "created_at" not in merged and "created_at" in local_content:
            merged["created_at"] = local_content["created_at"]
        merged["timeline"] = events
        pending_events = Counter(event_signature(event) for event in events) - Counter(
            event_signature(event) for event in remote["timeline"]
        )
        merged.update({"_cloud_base": deepcopy(remote), "_cloud_version": version,
                       "_sync_state": "pending" if merged_fields != remote_fields or pending_events else "synced"})
    merged["_local_revision"] = revision
    if merged == current:
        return merged, "unchanged"
    merged["_local_revision"] = revision + 1
    return merged, "conflict" if conflict else "updated"
