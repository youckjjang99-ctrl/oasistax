"""Pure, lossless CRM snapshot/operation helpers (no network or file access)."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from copy import deepcopy
from typing import Any

CRM_SAVE_RPC = "oasis_save_crm_versioned"
LOCAL_KEYS = {
    "_local_revision", "_cloud_base", "_cloud_version", "_sync_state",
    "_cloud_latest", "_cloud_latest_version", "_sync_error", "_sync_request_id",
}
VOLATILE_KEYS = {"timeline", "timelines", "updated_at", "created_at"}


def cloud_content(record: dict[str, Any]) -> dict[str, Any]:
    return deepcopy({k: v for k, v in record.items() if k not in LOCAL_KEYS})


def editable_content(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in cloud_content(record).items() if k not in VOLATILE_KEYS}


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def event_signature(event: dict[str, Any]) -> str:
    # Legacy events have no UUID. Deduplicate by original content without
    # discarding any fields from the event retained in the merged result.
    return hashlib.sha256(canonical(event).encode("utf-8")).hexdigest()


def merge_events(*groups: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    counts: Counter = Counter()
    for group in groups:
        group_counts: Counter = Counter()
        for event in group if isinstance(group, list) else []:
            if not isinstance(event, dict):
                continue
            signature = event_signature(event)
            group_counts[signature] += 1
            if group_counts[signature] > counts[signature]:
                found.append(deepcopy(event))
        counts |= group_counts
    return sorted(found, key=lambda e: str(e.get("at", e.get("created_at", ""))), reverse=True)


def make_save_parameters(owner: str, business_no: str, record: dict[str, Any]) -> dict[str, Any]:
    base = record.get("_cloud_base", {})
    base = base if isinstance(base, dict) else {}
    content = editable_content(record)
    patch = {k: v for k, v in content.items() if k not in base or base[k] != v}
    # New fields and intentional empty strings are retained. Omitted fields
    # are never a request to erase data from a different/older client.
    events = merge_events(record.get("timeline"), record.get("timelines"))
    base_signatures = Counter(event_signature(e) for e in merge_events(base.get("timeline"), base.get("timelines")))
    additions = []
    for event in events:
        signature = event_signature(event)
        if base_signatures[signature]:
            base_signatures[signature] -= 1
        else:
            additions.append(event)
    events = additions
    version = max(0, int(record.get("_cloud_version", 0) or 0))
    material = canonical([owner, business_no, version, patch, events])
    return {
        "p_owner_user_id": owner,
        "p_business_no": business_no,
        "p_request_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "oasis-crm:" + material)),
        "p_expected_version": version,
        "p_patch": patch,
        "p_events": events,
    }


def response_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return value[0]
    return {}


def validate_save_response(value: Any, parameters: dict) -> dict:
    response = response_object(value)
    version = response.get("version")
    content = response.get("crm_data")
    valid_content = isinstance(content, dict) and all(
        key not in content or (
            isinstance(content[key], list)
            and all(isinstance(event, dict) for event in content[key])
        )
        for key in ("timeline", "timelines")
    )
    if (response.get("status") not in {"applied", "conflict"}
        or type(version) is not int or version < (1 if response.get("status") == "applied" else 0)
        or not valid_content
        or response.get("owner_user_id") != parameters.get("p_owner_user_id")
        or response.get("request_id") != parameters.get("p_request_id")):
        raise RuntimeError("crm_cloud_response_unconfirmed")
    return response


def known_cloud_version(record: dict[str, Any]) -> int:
    """A conflict response is also an observed server version, not just an ACK."""
    return max(
        int(record.get("_cloud_version", 0) or 0),
        int(record.get("_cloud_latest_version", 0) or 0),
    )


def acknowledge_record(current: dict[str, Any], sent: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """Apply a server ACK while preserving edits made during the HTTP request."""
    if known_cloud_version(current) > int(response.get("version", 0) or 0):
        return deepcopy(current)
    remote = cloud_content(response.get("crm_data") or {})
    current_fields = editable_content(current)
    sent_fields = editable_content(sent)
    remote_fields = editable_content(remote)
    edited_since_send = int(current.get("_local_revision", 0) or 0) > int(
        sent.get("_local_revision", 0) or 0
    )
    # A -> B -> A is indistinguishable from an untouched field if we compare
    # only values. A replay can return newer cloud content, so do not discard
    # a revision-confirmed local edit merely because its final value matches
    # the old request. Keep the entire draft for explicit reconciliation.
    ambiguous_fields = {
        key for key, value in current_fields.items()
        if key in sent_fields and value == sent_fields[key]
        and key in remote_fields and remote_fields[key] != sent_fields[key]
    }
    unresolved_conflict = current.get("_sync_state") == "conflict" and any(
        key in remote_fields and value != remote_fields[key]
        for key, value in current_fields.items()
    )
    if (edited_since_send and ambiguous_fields) or unresolved_conflict:
        preserved = deepcopy(current)
        preserved.update({
            "_sync_state": "conflict",
            "_cloud_latest": remote,
            "_cloud_latest_version": int(response["version"]),
            "_local_revision": int(current.get("_local_revision", 0) or 0) + 1,
        })
        return preserved
    merged = deepcopy(remote)
    later_changes = {
        k: v for k, v in current_fields.items()
        if k not in sent_fields or sent_fields[k] != v
    }
    merged.update(later_changes)
    merged["timeline"] = merge_events(remote.get("timeline"), remote.get("timelines"), current.get("timeline"), current.get("timelines"))
    pending_events = Counter(event_signature(e) for e in merged["timeline"]) - Counter(
        event_signature(e) for e in merge_events(remote.get("timeline"), remote.get("timelines"))
    )
    merged.update({
        "_cloud_base": remote,
        "_cloud_version": int(response.get("version", 0) or 0),
        "_local_revision": int(current.get("_local_revision", 0) or 0) + 1,
        "_sync_state": "pending" if later_changes or pending_events else "synced",
    })
    return merged
