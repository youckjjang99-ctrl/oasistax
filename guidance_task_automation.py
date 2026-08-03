"""Durable, PII-free consumer for DB-discovery Kakao follow-up tasks.

The claim tax-review task is materialized by a database trigger because the
claim collector already commits its terminal state in one transaction.  This
module consumes only the existing Kakao follow-up outbox.  It never receives
or logs customer names, phone numbers, authentication data, or provider
payloads.

Railway wiring:

The public claim gateway starts one in-process daemon through
``start_guidance_task_automation_worker``.  A dedicated Railway worker may
instead run:

    python -m guidance_task_automation

The worker only calls task RPCs and never changes Tilko/authentication logic.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import threading
import uuid
from typing import Any, Callable, Mapping, Protocol

from cloud_db import CloudDatabase


DEFAULT_BATCH_SIZE = 25
DEFAULT_LEASE_SECONDS = 90
DEFAULT_POLL_SECONDS = 30


class TaskAutomationDatabase(Protocol):
    def rpc(self, function_name: str, parameters: dict[str, Any]) -> Any: ...


@dataclass(frozen=True)
class TaskAutomationStats:
    leased: int = 0
    created: int = 0
    already_created: int = 0
    cancelled: int = 0
    failed: int = 0


def _rows(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        return [value]
    return []


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        selected = int(value)
    except (TypeError, ValueError):
        selected = default
    return max(minimum, min(selected, maximum))


def _new_worker_id() -> str:
    return f"oasis-task-{uuid.uuid4().hex[:20]}"


def run_guidance_task_automation_once(
    database: TaskAutomationDatabase | None = None,
    *,
    worker_id: str | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> TaskAutomationStats:
    """Lease and materialize a bounded batch of due follow-up tasks.

    Database RPCs own all eligibility and idempotency decisions.  The Python
    consumer only coordinates leases and submits a fixed safe error code when
    a transient RPC failure occurs.
    """

    selected_database = database or CloudDatabase()
    selected_worker = str(worker_id or _new_worker_id()).strip()
    selected_batch = _bounded_int(batch_size, DEFAULT_BATCH_SIZE, 1, 100)
    selected_lease = _bounded_int(
        lease_seconds,
        DEFAULT_LEASE_SECONDS,
        30,
        900,
    )

    leased_rows = _rows(
        selected_database.rpc(
            "oasis_lease_company_kakao_followups",
            {
                "p_worker_id": selected_worker,
                "p_limit": selected_batch,
                "p_lease_seconds": selected_lease,
            },
        )
    )
    created = 0
    already_created = 0
    cancelled = 0
    failed = 0

    for row in leased_rows:
        outbox_id = str(row.get("id") or "").strip()
        if not outbox_id:
            failed += 1
            continue
        try:
            result_rows = _rows(
                selected_database.rpc(
                    "oasis_materialize_company_kakao_followup",
                    {
                        "p_worker_id": selected_worker,
                        "p_outbox_id": outbox_id,
                    },
                )
            )
            result = result_rows[0] if result_rows else {}
            code = str(result.get("code") or "").strip().upper()
            if bool(result.get("success")) and code == "CREATED":
                created += 1
            elif bool(result.get("success")) and code == "ALREADY_CREATED":
                already_created += 1
            elif code == "NO_LONGER_ELIGIBLE":
                cancelled += 1
            else:
                failed += 1
        except Exception:
            # Never forward exception text: HTTP/provider errors may contain
            # request data.  Only a fixed allow-listed safe code reaches DB.
            try:
                selected_database.rpc(
                    "oasis_fail_company_kakao_followup",
                    {
                        "p_worker_id": selected_worker,
                        "p_outbox_id": outbox_id,
                        "p_error_code": "TASK_RPC_FAILED",
                        "p_retry_after_seconds": 60,
                    },
                )
            except Exception:
                # The lease expires and can be safely reclaimed.  Suppressing
                # raw exceptions here prevents accidental PII in process logs.
                pass
            failed += 1

    return TaskAutomationStats(
        leased=len(leased_rows),
        created=created,
        already_created=already_created,
        cancelled=cancelled,
        failed=failed,
    )


def run_guidance_task_automation_forever(
    stop_event: threading.Event,
    *,
    database_factory: Callable[[], TaskAutomationDatabase] = CloudDatabase,
    worker_id: str | None = None,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
) -> None:
    """Poll without exposing errors or customer data to stdout/stderr."""

    selected_worker = str(worker_id or _new_worker_id()).strip()
    selected_poll = _bounded_int(
        poll_seconds,
        DEFAULT_POLL_SECONDS,
        5,
        300,
    )
    selected_batch = _bounded_int(
        batch_size,
        DEFAULT_BATCH_SIZE,
        1,
        100,
    )
    database: TaskAutomationDatabase | None = None
    while not stop_event.is_set():
        try:
            database = database or database_factory()
            stats = run_guidance_task_automation_once(
                database,
                worker_id=selected_worker,
                batch_size=selected_batch,
                lease_seconds=lease_seconds,
            )
            # Drain a backlog promptly while keeping idle polling inexpensive.
            wait_seconds = 1 if stats.leased >= selected_batch else selected_poll
        except Exception:
            database = None
            wait_seconds = selected_poll
        stop_event.wait(wait_seconds)


_START_LOCK = threading.Lock()
_WORKER_THREAD: threading.Thread | None = None
_WORKER_STOP_EVENT: threading.Event | None = None


def start_guidance_task_automation_worker(
    *,
    database_factory: Callable[[], TaskAutomationDatabase] = CloudDatabase,
    poll_seconds: int = DEFAULT_POLL_SECONDS,
) -> threading.Thread:
    """Start at most one daemon consumer in the current process."""

    global _WORKER_THREAD, _WORKER_STOP_EVENT
    with _START_LOCK:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            return _WORKER_THREAD
        _WORKER_STOP_EVENT = threading.Event()
        _WORKER_THREAD = threading.Thread(
            target=run_guidance_task_automation_forever,
            kwargs={
                "stop_event": _WORKER_STOP_EVENT,
                "database_factory": database_factory,
                "poll_seconds": poll_seconds,
            },
            name="oasis-guidance-task-automation",
            daemon=True,
        )
        _WORKER_THREAD.start()
        return _WORKER_THREAD


def stop_guidance_task_automation_worker() -> None:
    """Request a clean stop for the in-process daemon, if one exists."""

    with _START_LOCK:
        if _WORKER_STOP_EVENT is not None:
            _WORKER_STOP_EVENT.set()


def _main() -> None:
    stop_event = threading.Event()

    def _request_stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    for signal_name in ("SIGINT", "SIGTERM"):
        selected_signal = getattr(signal, signal_name, None)
        if selected_signal is not None:
            signal.signal(selected_signal, _request_stop)

    poll_seconds = _bounded_int(
        os.environ.get("OASIS_TASK_AUTOMATION_POLL_SECONDS"),
        DEFAULT_POLL_SECONDS,
        5,
        300,
    )
    run_guidance_task_automation_forever(
        stop_event,
        poll_seconds=poll_seconds,
    )


if __name__ == "__main__":
    _main()
