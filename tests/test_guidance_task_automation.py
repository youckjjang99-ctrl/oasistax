from __future__ import annotations

from pathlib import Path

from guidance_task_automation import (
    TaskAutomationStats,
    run_guidance_task_automation_once,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260803092000_v910_task_automation.sql"
)


class _FakeDatabase:
    def __init__(self, leased=None, materialized=None, failure=None):
        self.leased = list(leased or [])
        self.materialized = dict(materialized or {})
        self.failure = failure
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, parameters: dict):
        self.calls.append((name, dict(parameters)))
        if name == "oasis_lease_company_kakao_followups":
            return self.leased
        if name == "oasis_materialize_company_kakao_followup":
            outbox_id = parameters["p_outbox_id"]
            selected = self.materialized.get(outbox_id)
            if isinstance(selected, Exception):
                raise selected
            return selected or [
                {
                    "success": True,
                    "code": "CREATED",
                    "task_id": "00000000-0000-0000-0000-000000000101",
                    "task_status": "pending",
                }
            ]
        if name == "oasis_fail_company_kakao_followup":
            if isinstance(self.failure, Exception):
                raise self.failure
            return [{"success": True, "code": "RECORDED", "status": "retry"}]
        raise AssertionError(f"unexpected RPC: {name}")


def test_empty_batch_does_not_make_followup_calls():
    database = _FakeDatabase()

    result = run_guidance_task_automation_once(
        database,
        worker_id="worker-1",
    )

    assert result == TaskAutomationStats()
    assert [name for name, _ in database.calls] == [
        "oasis_lease_company_kakao_followups"
    ]


def test_created_already_created_and_cancelled_are_counted_separately():
    ids = [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000003",
    ]
    database = _FakeDatabase(
        leased=[{"id": selected} for selected in ids],
        materialized={
            ids[0]: [{"success": True, "code": "CREATED"}],
            ids[1]: [{"success": True, "code": "ALREADY_CREATED"}],
            ids[2]: [{"success": False, "code": "NO_LONGER_ELIGIBLE"}],
        },
    )

    result = run_guidance_task_automation_once(
        database,
        worker_id="worker-2",
        batch_size=500,
        lease_seconds=1,
    )

    assert result == TaskAutomationStats(
        leased=3,
        created=1,
        already_created=1,
        cancelled=1,
        failed=0,
    )
    lease_call = database.calls[0][1]
    assert lease_call["p_limit"] == 100
    assert lease_call["p_lease_seconds"] == 30


def test_exception_text_is_never_forwarded_to_database():
    outbox_id = "00000000-0000-0000-0000-000000000004"
    synthetic_phone = "-".join(("010", "1234", "5678"))
    sensitive_exception = RuntimeError(
        f"customer-name {synthetic_phone} resident-number provider-payload"
    )
    database = _FakeDatabase(
        leased=[{"id": outbox_id}],
        materialized={outbox_id: sensitive_exception},
    )

    result = run_guidance_task_automation_once(
        database,
        worker_id="worker-3",
    )

    assert result.failed == 1
    failure_calls = [
        params
        for name, params in database.calls
        if name == "oasis_fail_company_kakao_followup"
    ]
    assert failure_calls == [
        {
            "p_worker_id": "worker-3",
            "p_outbox_id": outbox_id,
            "p_error_code": "TASK_RPC_FAILED",
            "p_retry_after_seconds": 60,
        }
    ]
    serialized_calls = repr(database.calls)
    assert synthetic_phone not in serialized_calls
    assert "resident-number" not in serialized_calls
    assert "provider-payload" not in serialized_calls


def test_failure_recording_failure_leaves_lease_for_safe_reclaim():
    outbox_id = "00000000-0000-0000-0000-000000000005"
    database = _FakeDatabase(
        leased=[{"id": outbox_id}],
        materialized={outbox_id: RuntimeError("private upstream body")},
        failure=RuntimeError("private database response"),
    )

    result = run_guidance_task_automation_once(
        database,
        worker_id="worker-4",
    )

    assert result.failed == 1
    assert len(database.calls) == 3


def test_migration_reuses_existing_followup_outbox_and_enforces_idempotency():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "create table if not exists public.oasis_work_tasks" in sql
    assert "create table if not exists public.oasis_company_kakao_followup_outbox" not in sql
    assert "unique (task_type, source_id)" in sql
    assert "idempotency_key text not null unique" in sql
    assert "add constraint oasis_guidance_followup_task_fkey" in sql
    assert "foreign key (task_id)" in sql
    assert "references public.oasis_work_tasks(id)" in sql
    assert "on delete set null" in sql
    assert "for update skip locked" in sql.lower()
    assert "oasis_lease_company_kakao_followups" in sql
    assert "oasis_materialize_company_kakao_followup" in sql
    assert "oasis_fail_company_kakao_followup" in sql


def test_followup_reconciliation_uses_canonical_task_type_and_source_id():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "t.source_id = o.guidance_message_id" in sql
    assert "t.source_id = v_outbox.guidance_message_id" in sql
    assert "on conflict (task_type, source_id) do update" in sql
    assert "and t.status = 'cancelled'" in sql
    assert "different idempotency-key format" in sql
    assert "where t.idempotency_key = o.idempotency_key" not in sql
    assert "where t.idempotency_key = v_outbox.idempotency_key" not in sql


def test_migration_rekeys_legacy_rows_before_exact_source_constraint():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "oasis_work_tasks_rekey" in sql
    assert "oasis_guidance_followup_rekey" in sql
    assert "oasis_work_task_source_duplicates" in sql
    assert "drop constraint if exists oasis_work_tasks_source_unique" in sql
    assert "add constraint oasis_work_tasks_source_unique" in sql
    assert "unique (task_type, source_id)" in sql
    assert sql.index("v910-rekey-") < sql.index(
        "add constraint oasis_work_tasks_source_unique"
    )
    assert sql.index("v910-followup-rekey-") < sql.index(
        "add constraint oasis_guidance_followup_canonical_idempotency_check"
    )


def test_migration_creates_claim_review_and_cancels_terminal_or_opted_out_work():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert "oasis_sync_claim_review_task" in sql
    assert "new.status in ('complete', 'partial')" in sql
    assert "CLAIM_COLLECTION_TERMINAL" in sql
    assert "new.status in ('cancelled', 'expired', 'failed')" in sql
    assert "new.status in ('opted_out', 'admin_blocked')" in sql
    assert "t.status in ('scheduled', 'pending', 'in_progress')" in sql
    assert "t.status <> 'cancelled'" in sql


def test_task_table_is_service_role_only_and_uses_fixed_safe_titles():
    sql = MIGRATION.read_text(encoding="utf-8")
    table_definition = sql.split(
        "create table if not exists public.oasis_work_tasks",
        1,
    )[1].split(";", 1)[0]

    assert "enable row level security" in sql
    assert "from PUBLIC, anon, authenticated" in sql
    assert "to service_role" in sql
    assert "create policy oasis_work_tasks_service_role_all" in sql
    assert "카카오톡 검토신청 후속 확인" in table_definition
    assert "경정청구 수집자료 세무사 검토" in table_definition
    assert "phone" not in table_definition.lower()
    assert "name" not in table_definition.lower()
    assert "business_no" not in table_definition.lower()
    assert "auth" not in table_definition.lower()


def test_public_gateway_bootstrap_starts_durable_task_consumer():
    source = (ROOT / "claim_remote_service.py").read_text(encoding="utf-8")

    assert "OASIS_TASK_AUTOMATION_ENABLED" in source
    assert "start_guidance_task_automation_worker" in source
    assert "if task_automation_enabled:" in source
    assert "if enabled and task_automation_enabled" not in source
