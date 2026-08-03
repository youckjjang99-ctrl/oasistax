from __future__ import annotations

import json
from pathlib import Path

import pytest

import sync_outbox
from sync_outbox import (
    LocalOutboxCorruptionError,
    cloud_outbox_status,
    enqueue_local_outbox,
    load_local_outbox,
    make_outbox_job,
    retry_cloud_outbox,
    retry_local_outbox,
    sanitize_error_summary,
    save_local_outbox,
)

SYNTHETIC_BUSINESS_NO = "-".join(("123", "45", "67890"))
SYNTHETIC_PHONE = "-".join(("010", "1234", "5678"))
SYNTHETIC_PHONE_DIGITS = "".join(("010", "1234", "5678"))
SYNTHETIC_RRN = "-".join(("900101", "1234567"))
SYNTHETIC_EMAIL = "person" + "@example.invalid"
SYNTHETIC_070_PHONE = "-".join(("070", "2345", "6789"))
SYNTHETIC_050_PHONE = "-".join(("0505", "345", "6789"))
SYNTHETIC_PATH = "C:" + "\\Users\\Example\\customer.pdf"


def _job(*, error: str = "") -> dict:
    return make_outbox_job(
        "user-1",
        "customer",
        "oasis_customers",
        [{"owner_user_id": "user-1", "business_no": SYNTHETIC_BUSINESS_NO}],
        "owner_user_id,business_no",
        error=error,
    )


def test_local_outbox_is_atomic_and_does_not_truncate(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    jobs = []
    for index in range(620):
        job = _job()
        job["id"] = f"job-{index}"
        job["idempotency_key"] = f"key-{index}"
        jobs.append(job)

    save_local_outbox(path, jobs)

    assert len(load_local_outbox(path)) == 620
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupted_queue_is_preserved_instead_of_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(LocalOutboxCorruptionError):
        enqueue_local_outbox(path, _job())

    assert path.read_text(encoding="utf-8") == "{broken"


def test_enqueue_is_idempotent_for_active_job(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    job = _job()

    enqueue_local_outbox(path, job)
    enqueue_local_outbox(path, dict(job))

    assert len(load_local_outbox(path)) == 1


def test_successful_retry_keeps_a_completed_history(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    enqueue_local_outbox(path, _job())
    calls = []

    def upsert(table, rows, on_conflict):
        calls.append((table, rows, on_conflict))

    result = retry_local_outbox(path, upsert)
    stored = load_local_outbox(path)

    assert result == {"success": 1, "failed": 0, "dead_letter": 0}
    assert len(calls) == 1
    assert stored[0]["status"] == "complete"
    assert stored[0]["completed_at"]


def test_legacy_queue_row_without_status_is_retried(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    legacy_job = {
        "operation": "customer",
        "table": "oasis_customers",
        "rows": [{"owner_user_id": "user-1", "business_no": "masked"}],
        "on_conflict": "owner_user_id,business_no",
        "queued_at": "2026-01-01 00:00:00",
    }
    path.write_text(json.dumps([legacy_job]), encoding="utf-8")
    calls = []

    def upsert(table, rows, on_conflict):
        calls.append((table, rows, on_conflict))

    result = retry_local_outbox(path, upsert)
    stored = load_local_outbox(path)

    assert result == {"success": 1, "failed": 0, "dead_letter": 0}
    assert len(calls) == 1
    assert stored[0]["status"] == "complete"
    assert stored[0]["completed_at"]


def test_retry_failure_is_sanitized_and_not_lost(tmp_path: Path) -> None:
    path = tmp_path / "queue.json"
    enqueue_local_outbox(path, _job())

    def fail(*_args):
        raise RuntimeError(
            f"token=plain-secret phone={SYNTHETIC_PHONE} "
            f"business={SYNTHETIC_BUSINESS_NO}"
        )

    result = retry_local_outbox(path, fail)
    stored = load_local_outbox(path)[0]

    assert result["failed"] == 1
    assert stored["status"] == "retry"
    assert "plain-secret" not in stored["last_error_summary"]
    assert SYNTHETIC_PHONE not in stored["last_error_summary"]
    assert SYNTHETIC_BUSINESS_NO not in stored["last_error_summary"]


def test_error_sanitizer_masks_sensitive_values() -> None:
    safe = sanitize_error_summary(
        f"Authorization: BearerABC api_key=abc123 {SYNTHETIC_RRN} "
        f"{SYNTHETIC_PHONE_DIGITS}"
    )
    assert "BearerABC" not in safe
    assert "abc123" not in safe
    assert SYNTHETIC_RRN not in safe
    assert SYNTHETIC_PHONE_DIGITS not in safe


def test_error_sanitizer_uses_shared_redaction_for_extended_pii() -> None:
    safe = sanitize_error_summary(
        " ".join(
            (
                f"email={SYNTHETIC_EMAIL}",
                SYNTHETIC_070_PHONE,
                SYNTHETIC_050_PHONE,
                f"path={SYNTHETIC_PATH}",
                "customer_name=SampleCustomer",
                "address=SampleAddress",
            )
        )
    )

    for private_value in (
        SYNTHETIC_EMAIL,
        SYNTHETIC_070_PHONE,
        SYNTHETIC_050_PHONE,
        SYNTHETIC_PATH,
        "SampleCustomer",
        "SampleAddress",
    ):
        assert private_value not in safe


def test_error_sanitizer_redacts_multiword_identity_values_without_tail_leaks() -> None:
    private_name = "Synthetic First Last"
    private_address = "Synthetic District Building 101"
    safe = sanitize_error_summary(
        f'customer_name="{private_name}", address={private_address}; retryable'
    )

    assert private_name not in safe
    assert "First Last" not in safe
    assert private_address not in safe
    assert "District Building 101" not in safe


class _FakeCloudOutboxDatabase:
    def __init__(self, claimed_rows, *, fail_upsert: bool = False) -> None:
        self.claimed_rows = claimed_rows
        self.fail_upsert = fail_upsert
        self.rpc_calls = []
        self.upsert_calls = []

    def rpc(self, name, parameters):
        self.rpc_calls.append((name, parameters))
        if name == "oasis_claim_sync_outbox":
            return self.claimed_rows
        if name in {
            "oasis_complete_sync_outbox",
            "oasis_fail_sync_outbox",
        }:
            return True
        raise AssertionError(f"unexpected RPC: {name}")

    def upsert(self, table, rows, on_conflict):
        self.upsert_calls.append((table, rows, on_conflict))
        if self.fail_upsert:
            raise RuntimeError(
                f"token=plain-secret phone={SYNTHETIC_PHONE}"
            )
        return rows


def _claimed_cloud_job(
    *,
    lease_token: str | None = "lease-1",
    owner_user_id: str = "owner-1",
) -> dict:
    return {
        "id": "job-1",
        "owner_user_id": owner_user_id,
        "lease_token": lease_token,
        "payload": {
            "table": "oasis_customers",
            "rows": [{"owner_user_id": "owner-1", "business_no": "masked"}],
            "on_conflict": "owner_user_id,business_no",
        },
    }


def test_cloud_retry_claims_only_owner_and_completes_with_lease_token() -> None:
    db = _FakeCloudOutboxDatabase([_claimed_cloud_job()])

    result = retry_cloud_outbox(
        db,
        owner_user_id="owner-1",
        worker_id="worker-1",
    )

    assert result == {"success": 1, "failed": 0}
    claim_name, claim_params = db.rpc_calls[0]
    assert claim_name == "oasis_claim_sync_outbox"
    assert claim_params["p_owner_user_id"] == "owner-1"
    complete_name, complete_params = db.rpc_calls[-1]
    assert complete_name == "oasis_complete_sync_outbox"
    assert complete_params["p_job_id"] == "job-1"
    assert complete_params["p_worker_id"] == "worker-1"
    assert complete_params["p_lease_token"] == "lease-1"


def test_cloud_retry_failure_is_fenced_and_sanitized() -> None:
    db = _FakeCloudOutboxDatabase(
        [_claimed_cloud_job(lease_token="lease-failure")],
        fail_upsert=True,
    )

    result = retry_cloud_outbox(
        db,
        owner_user_id="owner-1",
        worker_id="worker-1",
    )

    assert result == {"success": 0, "failed": 1}
    fail_name, fail_params = db.rpc_calls[-1]
    assert fail_name == "oasis_fail_sync_outbox"
    assert fail_params["p_lease_token"] == "lease-failure"
    assert "plain-secret" not in fail_params["p_error_summary"]
    assert SYNTHETIC_PHONE not in fail_params["p_error_summary"]


def test_cloud_retry_refuses_unfenced_claim() -> None:
    db = _FakeCloudOutboxDatabase(
        [_claimed_cloud_job(lease_token=None)]
    )

    result = retry_cloud_outbox(
        db,
        owner_user_id="owner-1",
        worker_id="worker-1",
    )

    assert result == {"success": 0, "failed": 1}
    assert db.upsert_calls == []
    assert [name for name, _params in db.rpc_calls] == [
        "oasis_claim_sync_outbox"
    ]


def test_cloud_retry_refuses_cross_owner_claim() -> None:
    db = _FakeCloudOutboxDatabase(
        [_claimed_cloud_job(owner_user_id="another-owner")]
    )

    result = retry_cloud_outbox(
        db,
        owner_user_id="owner-1",
        worker_id="worker-1",
    )

    assert result == {"success": 0, "failed": 1}
    assert db.upsert_calls == []
    assert [name for name, _params in db.rpc_calls] == [
        "oasis_claim_sync_outbox"
    ]


class _PagedStatusDatabase:
    def __init__(self, rows, *, ignore_offset: bool = False) -> None:
        self.rows = rows
        self.ignore_offset = ignore_offset
        self.select_calls = []

    def select(
        self,
        table,
        filters=None,
        columns="*",
        order=None,
        limit=None,
        offset=None,
    ):
        self.select_calls.append(
            {
                "table": table,
                "filters": filters,
                "columns": columns,
                "order": order,
                "limit": limit,
                "offset": offset,
            }
        )
        start = 0 if self.ignore_offset else int(offset or 0)
        end = start + int(limit or len(self.rows))
        return self.rows[start:end]


def _enable_cloud_status(monkeypatch, db) -> None:
    monkeypatch.setattr(sync_outbox, "durable_outbox_enabled", lambda: True)
    monkeypatch.setattr(sync_outbox, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(sync_outbox, "CloudDatabase", lambda: db)


def test_global_cloud_status_pages_past_postgrest_limit(monkeypatch) -> None:
    rows = (
        [{"id": f"p-{index}", "status": "pending"} for index in range(1001)]
        + [{"id": f"c-{index}", "status": "completed"} for index in range(750)]
        + [{"id": f"d-{index}", "status": "dead_letter"} for index in range(500)]
        + [{"id": f"x-{index}", "status": "processing"} for index in range(254)]
    )
    db = _PagedStatusDatabase(rows)
    _enable_cloud_status(monkeypatch, db)

    result = cloud_outbox_status(None)

    assert result == {
        "enabled": True,
        "queued": 1255,
        "dead_letter": 500,
        "total": 2505,
    }
    assert [call["offset"] for call in db.select_calls] == [0, 1000, 2000]
    assert all(call["filters"] is None for call in db.select_calls)
    assert all(call["limit"] == 1000 for call in db.select_calls)


def test_owner_cloud_status_keeps_filter_and_also_pages(monkeypatch) -> None:
    rows = (
        [{"id": f"r-{index}", "status": "retry"} for index in range(1000)]
        + [{"id": f"c-{index}", "status": "completed"} for index in range(201)]
    )
    db = _PagedStatusDatabase(rows)
    _enable_cloud_status(monkeypatch, db)

    result = cloud_outbox_status("owner-1")

    assert result == {
        "enabled": True,
        "queued": 1000,
        "dead_letter": 0,
        "total": 1201,
    }
    assert [call["offset"] for call in db.select_calls] == [0, 1000]
    assert all(
        call["filters"] == {"owner_user_id": "owner-1"}
        for call in db.select_calls
    )


def test_cloud_status_stops_if_backend_ignores_offset(monkeypatch) -> None:
    rows = [
        {"id": f"p-{index}", "status": "pending"}
        for index in range(2000)
    ]
    db = _PagedStatusDatabase(rows, ignore_offset=True)
    _enable_cloud_status(monkeypatch, db)

    with pytest.raises(RuntimeError, match="outbox_status_pagination_stalled"):
        cloud_outbox_status(None)

    assert len(db.select_calls) == 2
