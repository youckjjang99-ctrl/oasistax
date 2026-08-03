from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from cloud_db import CloudDatabase, TABLE_SYNC_OUTBOX, cloud_is_configured
from runtime_error_log import sanitize_public_text


OUTBOX_FEATURE_FLAG = "OASIS_DURABLE_OUTBOX_V1"
DEFAULT_MAX_ATTEMPTS = 8
MAX_ERROR_LENGTH = 500
_RPC_FUNCTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_OUTBOX_RPC_FUNCTIONS = {"oasis_upsert_customer_profile"}
_CUSTOMER_PROFILE_SUCCESS_STATUSES = {
    "linked",
    "linked_review_required",
    "unlinked",
    "ambiguous_review",
}


class LocalOutboxCorruptionError(RuntimeError):
    """Raised without overwriting a malformed local recovery queue."""


def durable_outbox_enabled() -> bool:
    return str(os.environ.get(OUTBOX_FEATURE_FLAG, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def sanitize_error_summary(value: Any) -> str:
    """Keep bounded operational context with the shared PII/secret redactor."""
    return sanitize_public_text(value)[:MAX_ERROR_LENGTH]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def build_idempotency_key(
    owner_user_id: str,
    job_type: str,
    payload: dict[str, Any],
) -> str:
    material = f"{owner_user_id}|{job_type}|{_canonical_json(payload)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _entity_fingerprint(payload: dict[str, Any]) -> str:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    first_row = rows[0] if isinstance(rows, list) and rows else None
    fingerprint_value = (
        first_row
        if isinstance(first_row, dict)
        else payload.get("parameters", {})
    )
    material = _canonical_json(
        fingerprint_value if isinstance(fingerprint_value, dict) else {}
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def load_local_outbox(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LocalOutboxCorruptionError(
            "로컬 동기화 대기열을 읽지 못했습니다. 원본 파일은 보존했습니다."
        ) from exc
    if not isinstance(data, list):
        raise LocalOutboxCorruptionError(
            "로컬 동기화 대기열 형식이 올바르지 않습니다. 원본 파일은 보존했습니다."
        )
    return [item for item in data if isinstance(item, dict)]


def save_local_outbox(path: Path, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def make_outbox_job(
    owner_user_id: str,
    job_type: str,
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
    *,
    error: Any = "",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    payload = {
        "operation": "upsert",
        "table": str(table),
        "rows": list(rows),
        "on_conflict": str(on_conflict),
    }
    now = _iso()
    return {
        "id": str(uuid.uuid4()),
        "owner_user_id": str(owner_user_id),
        "job_type": str(job_type),
        "entity_type": str(table),
        "entity_id": _entity_fingerprint(payload),
        "payload": payload,
        "idempotency_key": build_idempotency_key(
            str(owner_user_id), str(job_type), payload
        ),
        "status": "pending",
        "attempt_count": 0,
        "max_attempts": max(1, int(max_attempts)),
        "next_retry_at": now,
        "last_error_code": "initial_sync_failed" if error else "",
        "last_error_summary": sanitize_error_summary(error),
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }


def _validate_rpc_function_name(value: Any) -> str:
    function_name = str(value or "").strip()
    if not _RPC_FUNCTION_NAME.fullmatch(function_name):
        raise ValueError("올바르지 않은 RPC 함수명입니다.")
    if function_name not in _ALLOWED_OUTBOX_RPC_FUNCTIONS:
        raise ValueError("허용되지 않은 동기화 RPC 함수입니다.")
    return function_name


def _normalized_owner(value: Any) -> str:
    # PostgreSQL text equality is case-sensitive; do not broaden owner scope.
    return str(value or "").strip()


def _validate_rpc_owner(
    parameters: dict[str, Any],
    expected_owner_user_id: str,
) -> None:
    expected_owner = _normalized_owner(expected_owner_user_id)
    payload_owner = _normalized_owner(parameters.get("p_owner_user_id"))
    if not expected_owner or payload_owner != expected_owner:
        raise ValueError("동기화 RPC 소유자 범위가 일치하지 않습니다.")


def make_rpc_outbox_job(
    owner_user_id: str,
    job_type: str,
    function_name: str,
    parameters: dict[str, Any],
    *,
    error: Any = "",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Build a durable RPC job without projecting parameters into table rows."""
    safe_function_name = _validate_rpc_function_name(function_name)
    safe_parameters = dict(parameters or {})
    _validate_rpc_owner(safe_parameters, owner_user_id)
    payload = {
        "operation": "rpc",
        "function_name": safe_function_name,
        "parameters": safe_parameters,
    }
    now = _iso()
    return {
        "id": str(uuid.uuid4()),
        "owner_user_id": str(owner_user_id),
        "job_type": str(job_type),
        "entity_type": f"rpc:{payload['function_name']}",
        "entity_id": _entity_fingerprint(payload),
        "payload": payload,
        "idempotency_key": build_idempotency_key(
            str(owner_user_id), str(job_type), payload
        ),
        "status": "pending",
        "attempt_count": 0,
        "max_attempts": max(1, int(max_attempts)),
        "next_retry_at": now,
        "last_error_code": "initial_sync_failed" if error else "",
        "last_error_summary": sanitize_error_summary(error),
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }


def enqueue_local_outbox(path: Path, job: dict[str, Any]) -> dict[str, Any]:
    queue = load_local_outbox(path)
    key = str(job.get("idempotency_key") or "")
    for existing in queue:
        if str(existing.get("idempotency_key") or "") != key:
            continue
        if existing.get("status") in {"pending", "retry", "processing"}:
            return existing
    queue.append(dict(job))
    save_local_outbox(path, queue)
    return job


def enqueue_cloud_outbox(db: CloudDatabase, job: dict[str, Any]) -> Any:
    return db.rpc(
        "oasis_enqueue_sync_outbox",
        {
            "p_owner_user_id": job["owner_user_id"],
            "p_job_type": job["job_type"],
            "p_entity_type": job["entity_type"],
            "p_entity_id": job["entity_id"],
            "p_payload": job["payload"],
            "p_idempotency_key": job["idempotency_key"],
            "p_max_attempts": job["max_attempts"],
        },
    )


def enqueue_outbox(
    path: Path,
    owner_user_id: str,
    job_type: str,
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
    *,
    error: Any = "",
) -> tuple[str, dict[str, Any]]:
    job = make_outbox_job(
        owner_user_id,
        job_type,
        table,
        rows,
        on_conflict,
        error=error,
    )
    if durable_outbox_enabled() and cloud_is_configured():
        try:
            enqueue_cloud_outbox(CloudDatabase(), job)
            return "cloud", job
        except Exception as exc:
            job["last_error_code"] = "cloud_outbox_unavailable"
            job["last_error_summary"] = sanitize_error_summary(exc)
    enqueue_local_outbox(path, job)
    return "local", job


def enqueue_rpc_outbox(
    path: Path,
    owner_user_id: str,
    job_type: str,
    function_name: str,
    parameters: dict[str, Any],
    *,
    error: Any = "",
    db: CloudDatabase | None = None,
) -> tuple[str, dict[str, Any]]:
    """Queue an RPC call while retaining its full validated parameter set."""
    job = make_rpc_outbox_job(
        owner_user_id,
        job_type,
        function_name,
        parameters,
        error=error,
    )
    if durable_outbox_enabled() and (db is not None or cloud_is_configured()):
        try:
            enqueue_cloud_outbox(db or CloudDatabase(), job)
            return "cloud", job
        except Exception as exc:
            job["last_error_code"] = "cloud_outbox_unavailable"
            job["last_error_summary"] = sanitize_error_summary(exc)
    enqueue_local_outbox(path, job)
    return "local", job


def _dispatch_outbox_payload(
    payload: dict[str, Any],
    upsert: Callable[[str, list[dict[str, Any]], str], Any],
    rpc: Callable[[str, dict[str, Any]], Any] | None = None,
    *,
    expected_owner_user_id: str = "",
) -> Any:
    operation = str(payload.get("operation") or "upsert").strip().lower()
    if operation == "upsert":
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ValueError("동기화 upsert 행 형식이 올바르지 않습니다.")
        return upsert(
            str(payload.get("table") or ""),
            list(rows),
            str(payload.get("on_conflict") or ""),
        )
    if operation == "rpc":
        if rpc is None:
            raise RuntimeError("RPC 동기화 실행기가 준비되지 않았습니다.")
        parameters = payload.get("parameters")
        if not isinstance(parameters, dict):
            raise ValueError("동기화 RPC 매개변수 형식이 올바르지 않습니다.")
        _validate_rpc_owner(parameters, expected_owner_user_id)
        function_name = _validate_rpc_function_name(
            payload.get("function_name")
        )
        result = rpc(
            function_name,
            dict(parameters),
        )
        if function_name == "oasis_upsert_customer_profile":
            if isinstance(result, list):
                response = next(
                    (item for item in result if isinstance(item, dict)),
                    {},
                )
            elif isinstance(result, dict):
                response = result
            else:
                response = {}
            customer_id = str(response.get("customer_id") or "").strip()
            link_status = str(
                response.get("link_status") or ""
            ).strip().lower()
            if (
                not customer_id
                or link_status not in _CUSTOMER_PROFILE_SUCCESS_STATUSES
            ):
                raise RuntimeError("customer_profile_sync_rejected")
        return result
    raise ValueError("지원하지 않는 동기화 작업 형식입니다.")


def _is_due(item: dict[str, Any], now: datetime) -> bool:
    value = str(item.get("next_retry_at") or "")
    if not value:
        return True
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed <= now
    except ValueError:
        return True


def retry_local_outbox(
    path: Path,
    upsert: Callable[[str, list[dict[str, Any]], str], Any],
    *,
    rpc: Callable[[str, dict[str, Any]], Any] | None = None,
) -> dict[str, int]:
    queue = load_local_outbox(path)
    now = _utc_now()
    success = 0
    failed = 0
    dead_letter = 0
    changed = False
    for item in queue:
        # Legacy queue rows did not have an explicit status.  Treat them as
        # pending so the Stage 1 upgrade cannot strand pre-existing work.
        current_status = str(item.get("status") or "pending")
        if current_status not in {"pending", "retry"} or not _is_due(item, now):
            if item.get("status") == "dead_letter":
                dead_letter += 1
            continue
        payload = item.get("payload") or {
            "table": item.get("table"),
            "rows": item.get("rows"),
            "on_conflict": item.get("on_conflict"),
        }
        try:
            _dispatch_outbox_payload(
                payload,
                upsert,
                rpc,
                expected_owner_user_id=str(item.get("owner_user_id") or ""),
            )
            item["status"] = "complete"
            item["completed_at"] = _iso()
            item["last_error_code"] = ""
            item["last_error_summary"] = ""
            success += 1
        except Exception as exc:
            attempts = int(item.get("attempt_count") or 0) + 1
            maximum = max(1, int(item.get("max_attempts") or DEFAULT_MAX_ATTEMPTS))
            item["attempt_count"] = attempts
            item["last_error_code"] = "sync_retry_failed"
            item["last_error_summary"] = sanitize_error_summary(exc)
            if attempts >= maximum:
                item["status"] = "dead_letter"
                dead_letter += 1
            else:
                item["status"] = "retry"
                delay_seconds = min(3600, 15 * (2 ** max(0, attempts - 1)))
                item["next_retry_at"] = _iso(now + timedelta(seconds=delay_seconds))
                failed += 1
        item["updated_at"] = _iso()
        changed = True
    if changed:
        save_local_outbox(path, queue)
    return {
        "success": success,
        "failed": failed,
        "dead_letter": dead_letter,
    }


def _coerce_claimed_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def retry_cloud_outbox(
    db: CloudDatabase,
    *,
    owner_user_id: str,
    worker_id: str,
    limit: int = 20,
    lease_seconds: int = 90,
) -> dict[str, int]:
    claimed = _coerce_claimed_rows(
        db.rpc(
            "oasis_claim_sync_outbox",
            {
                "p_owner_user_id": str(owner_user_id),
                "p_worker_id": worker_id,
                "p_limit": max(1, min(int(limit), 100)),
                "p_lease_seconds": max(30, min(int(lease_seconds), 900)),
            },
        )
    )
    success = 0
    failed = 0
    expected_owner = str(owner_user_id or "").strip()
    for item in claimed:
        payload = item.get("payload") or {}
        job_id = str(item.get("id") or "")
        lease_token = str(item.get("lease_token") or "")
        claimed_owner = str(item.get("owner_user_id") or "").strip()
        if (
            not job_id
            or not lease_token
            or not expected_owner
            or claimed_owner != expected_owner
        ):
            failed += 1
            continue
        try:
            _dispatch_outbox_payload(
                payload,
                db.upsert,
                db.rpc,
                expected_owner_user_id=claimed_owner,
            )
            completed = db.rpc(
                "oasis_complete_sync_outbox",
                {
                    "p_job_id": job_id,
                    "p_worker_id": worker_id,
                    "p_lease_token": lease_token,
                },
            )
            if completed is not True:
                raise RuntimeError("outbox_completion_rejected")
            success += 1
        except Exception as exc:
            db.rpc(
                "oasis_fail_sync_outbox",
                {
                    "p_job_id": job_id,
                    "p_worker_id": worker_id,
                    "p_lease_token": lease_token,
                    "p_error_code": "sync_retry_failed",
                    "p_error_summary": sanitize_error_summary(exc),
                },
            )
            failed += 1
    return {"success": success, "failed": failed}


def manual_retry_cloud_outbox(job_id: str, actor_user_id: str) -> Any:
    if not durable_outbox_enabled() or not cloud_is_configured():
        raise RuntimeError("영속 동기화 대기열이 아직 활성화되지 않았습니다.")
    return CloudDatabase().rpc(
        "oasis_retry_sync_outbox",
        {"p_job_id": str(job_id), "p_actor_user_id": str(actor_user_id)},
    )


def local_outbox_status(path: Path) -> dict[str, Any]:
    try:
        queue = load_local_outbox(path)
    except LocalOutboxCorruptionError as exc:
        return {
            "queued": 0,
            "complete": 0,
            "dead_letter": 0,
            "total": 0,
            "corrupted": True,
            "error": str(exc),
        }
    counts = {"pending": 0, "retry": 0, "processing": 0, "complete": 0, "dead_letter": 0}
    for item in queue:
        status = str(item.get("status") or "pending")
        if status in counts:
            counts[status] += 1
    return {
        "queued": counts["pending"] + counts["retry"] + counts["processing"],
        "complete": counts["complete"],
        "dead_letter": counts["dead_letter"],
        "total": len(queue),
        "corrupted": False,
    }


def _aggregate_cloud_outbox_status(
    db: CloudDatabase,
    owner_user_id: str | None,
    *,
    page_size: int = 1000,
) -> dict[str, int]:
    """Aggregate every status row in stable, bounded REST pages.

    The admin view passes ``None`` and therefore aggregates every owner's
    service-role-visible row.  Only counters and the prior page signature are
    retained, so a large queue does not need to be held in process memory.
    """

    safe_page_size = max(1, min(int(page_size), 1000))
    filters = (
        None
        if owner_user_id is None
        else {"owner_user_id": str(owner_user_id)}
    )
    queued = 0
    dead_letter = 0
    total = 0
    offset = 0
    previous_signature: tuple[str, str, int] | None = None
    while True:
        batch = db.select(
            TABLE_SYNC_OUTBOX,
            filters=filters,
            columns="id,status",
            order="created_at.asc,id.asc",
            limit=safe_page_size,
            offset=offset,
        )
        if not batch:
            break

        signature = (
            str(batch[0].get("id") or ""),
            str(batch[-1].get("id") or ""),
            len(batch),
        )
        if previous_signature == signature:
            raise RuntimeError("outbox_status_pagination_stalled")

        for row in batch:
            status = str(row.get("status") or "")
            if status in {"pending", "retry", "processing"}:
                queued += 1
            elif status == "dead_letter":
                dead_letter += 1
            total += 1

        if len(batch) < safe_page_size:
            break
        previous_signature = signature
        offset += len(batch)
    return {
        "queued": queued,
        "dead_letter": dead_letter,
        "total": total,
    }


def cloud_outbox_status(
    owner_user_id: str | None = None,
) -> dict[str, Any]:
    if not durable_outbox_enabled() or not cloud_is_configured():
        return {"enabled": False, "queued": 0, "dead_letter": 0, "total": 0}
    counts = _aggregate_cloud_outbox_status(
        CloudDatabase(),
        owner_user_id,
    )
    return {
        "enabled": True,
        **counts,
    }
