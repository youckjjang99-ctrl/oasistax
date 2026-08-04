from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804171835_v912_central_work_inbox.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
SQL_LOWER = SQL.lower()

RPC_NAMES = (
    "oasis_work_inbox_feature_ready",
    "oasis_list_my_work_tasks",
    "oasis_list_my_sales_followups",
    "oasis_get_my_work_task_summary",
    "oasis_transition_my_work_task",
)


def _function_sql(name: str) -> str:
    marker = re.search(
        rf"create\s+or\s+replace\s+function\s+public[.]"
        rf"{re.escape(name)}\s*[(]",
        SQL,
        flags=re.IGNORECASE,
    )
    if marker is None:
        raise AssertionError(f"missing SQL function: {name}")
    next_function = re.search(
        r"\ncreate\s+or\s+replace\s+function\s+public[.]",
        SQL[marker.end() :],
        flags=re.IGNORECASE,
    )
    end = (
        marker.end() + next_function.start()
        if next_function is not None
        else len(SQL)
    )
    return SQL[marker.start() : end]


def _create_table_sql(name: str) -> str:
    marker = SQL_LOWER.find(f"create table if not exists public.{name}")
    if marker < 0:
        raise AssertionError(f"missing SQL table: {name}")
    end = SQL_LOWER.find("\n);", marker)
    if end < 0:
        raise AssertionError(f"unterminated SQL table: {name}")
    return SQL[marker : end + len("\n);")]


def test_migration_is_transactional_additive_idempotent_and_guarded():
    assert MIGRATION.is_file()
    assert re.fullmatch(
        r"\d{14}_v912_central_work_inbox[.]sql",
        MIGRATION.name,
    )
    assert SQL_LOWER.lstrip().startswith("--")
    assert "begin;" in SQL_LOWER
    assert SQL_LOWER.rstrip().endswith("commit;")

    for destructive in (
        r"\bdrop\s+(?:table|schema|column|index)\b",
        r"\btruncate\b",
        r"\bdelete\s+from\b",
    ):
        assert not re.search(destructive, SQL_LOWER)

    assert "to_regclass('public.oasis_work_tasks')" in SQL_LOWER
    first_mutation = min(
        position
        for position in (
            SQL_LOWER.find("alter table public.oasis_work_tasks"),
            SQL_LOWER.find("create table if not exists"),
        )
        if position >= 0
    )
    assert SQL_LOWER.index("to_regclass('public.oasis_work_tasks')") < first_mutation
    assert re.search(
        r"alter\s+table\s+public[.]oasis_work_tasks\s+"
        r"add\s+column\s+if\s+not\s+exists\s+task_version\s+bigint",
        SQL_LOWER,
    )
    assert re.search(
        r"check\s*[(][^;)]*task_version\s*(?:>=\s*1|>\s*0)",
        SQL_LOWER,
    )
    assert "pg_catalog.pg_attrdef" in SQL_LOWER
    assert "oasis_v912_task_version_schema_incompatible" in SQL_LOWER
    assert "oasis_v912_task_version_check_incompatible" in SQL_LOWER

    assert "create table if not exists public.oasis_work_task_events" in SQL_LOWER
    assert "create table public.oasis_work_task_events" not in SQL_LOWER
    assert "create or replace function" in SQL_LOWER
    assert not re.search(r"\bcreate\s+index\s+(?!if\s+not\s+exists)", SQL_LOWER)


def test_migration_does_not_touch_protected_queues_phone_jobs_or_tilko():
    protected_markers = (
        "oasis_company_kakao_followup_outbox",
        "oasis_prospect_outreach_outbox",
        "oasis_sync_outbox",
        "oasis_claim_remote_jobs",
        "oasis_claim_remote_outbox",
        "oasis_contact_provider_",
        "oasis_employment_contacts",
        "oasis_licensed_businesses",
        "phone_enrichment",
        "phone_provider",
        "tilko",
    )
    for marker in protected_markers:
        assert marker not in SQL_LOWER

    updated_tables = set(
        re.findall(r"\bupdate\s+public[.]([a-z0-9_]+)", SQL_LOWER)
    )
    assert updated_tables <= {"oasis_work_tasks"}


def test_event_ledger_is_versioned_force_rls_and_append_only():
    table_sql = _create_table_sql("oasis_work_task_events").lower()
    for field in (
        "task_id uuid not null",
        "task_version bigint not null",
        "actor_user_id text not null",
        "event_type text not null",
        "from_status text not null",
        "to_status text not null",
        "from_due_at timestamptz not null",
        "to_due_at timestamptz not null",
    ):
        assert field in table_sql
    assert "references public.oasis_work_tasks(id)" in table_sql
    assert "on delete restrict" in table_sql
    assert "oasis_v912_work_task_event_pk_incompatible" in SQL_LOWER
    assert "c.confdeltype = 'r'" in SQL_LOWER
    assert "oasis_v912_work_task_event_default_incompatible" in SQL_LOWER
    assert "oasis_v912_work_task_event_check_incompatible" in SQL_LOWER
    assert "oasis_v912_work_task_event_extra_column_incompatible" in SQL_LOWER

    assert (
        "alter table public.oasis_work_task_events enable row level security"
        in SQL_LOWER
    )
    assert (
        "alter table public.oasis_work_task_events force row level security"
        in SQL_LOWER
    )
    assert re.search(
        r"revoke\s+all\s+on\s+table\s+public[.]oasis_work_task_events\s+"
        r"from\s+public\s*,\s*anon\s*,\s*authenticated\s*,\s*service_role",
        SQL_LOWER,
    )

    event_grants = re.findall(
        r"grant\s+([^;]+?)\s+on\s+table\s+"
        r"public[.]oasis_work_task_events\s+to\s+service_role",
        SQL_LOWER,
    )
    assert event_grants
    for privileges in event_grants:
        assert "update" not in privileges
        assert "delete" not in privileges
        assert "all" not in privileges
    assert not re.search(
        r"create\s+policy[^;]+on\s+public[.]oasis_work_task_events[^;]+"
        r"for\s+(?:update|delete)",
        SQL_LOWER,
    )
    immutable = _function_sql("oasis_work_task_event_is_immutable").lower()
    assert "oasis_work_task_events_are_append_only" in immutable
    assert "before update or delete on public.oasis_work_task_events" in SQL_LOWER
    assert "execute function public.oasis_work_task_event_is_immutable()" in SQL_LOWER


def test_rpc_contracts_scope_to_approved_assignee_and_use_safe_search_paths():
    assert (
        "to_regprocedure(\n        'public.oasis_sales_actor_is_active(text)'"
        in SQL_LOWER
    )

    for name in RPC_NAMES:
        body = _function_sql(name).lower()
        assert "set search_path" in body

    for name in RPC_NAMES[1:]:
        body = _function_sql(name).lower()
        assert "p_current_user_id text" in body
        assert "public.oasis_sales_actor_is_active" in body
        assert "assigned_user_id" in body

    summary = _function_sql("oasis_get_my_work_task_summary").lower()
    assert "asia/seoul" in summary
    for metric in ("overdue", "today", "week"):
        assert metric in summary


def test_sales_followup_rpc_is_stable_read_only_and_keyset_paginated():
    sales = _function_sql("oasis_list_my_sales_followups").lower()

    assert "stable" in sales
    assert "security definer" in sales
    assert "set search_path = ''" in sales
    assert "public.oasis_sales_actor_is_active" in sales
    assert "a.assigned_user_id = v_actor" in sales
    assert "a.status = 'follow_up'" in sales
    assert "order by a.next_contact_at, a.id" in sales
    assert "(a.next_contact_at, a.id) >" in sales
    assert "p_after_assignment_id" in sales
    assert "oasis_release_expired_company_assignments" not in sales
    assert not re.search(r"\b(?:insert|update|delete)\b", sales)
    assert "oasis_sales_followup_inbox_idx" in SQL_LOWER


def test_transition_is_optimistic_versioned_and_records_every_action():
    transition = _function_sql("oasis_transition_my_work_task").lower()
    for parameter in (
        "p_current_user_id text",
        "p_task_id uuid",
        "p_action text",
        "p_expected_version bigint",
        "p_defer_until timestamptz",
    ):
        assert parameter in transition
    for action in ("'start'", "'complete'", "'defer'"):
        assert action in transition

    assert "for update" in transition
    assert re.search(
        r"task_version\s*(?:=|<>|!=)\s*p_expected_version|"
        r"p_expected_version\s*(?:=|<>|!=)\s*[^;\n]*task_version",
        transition,
    )
    version_trigger = _function_sql("oasis_work_task_touch_updated_at").lower()
    assert "new.task_version := old.task_version + 1" in version_trigger
    assert re.search(r"p_expected_version\s*(?:<\s*1|<=\s*0)", transition)
    assert "insert into public.oasis_work_task_events" in transition
    assert "due_at" in transition

    stale_check = transition.index("if v_task.task_version <> p_expected_version")
    for retry_code in (
        "'already_in_progress'",
        "'already_completed'",
        "'already_deferred'",
    ):
        assert transition.index(retry_code) < stale_check
    defer_retry = transition.index("'already_deferred'")
    defer_time_guard = transition.index(
        "p_defer_until < pg_catalog.clock_timestamp() + interval '1 minute'"
    )
    assert defer_retry < defer_time_guard < stale_check
    assert "lost-response retries are idempotent" in transition
    assert "drop trigger if exists oasis_work_tasks_updated_at" in SQL_LOWER


def test_indexes_cover_inbox_reads_and_event_history():
    index_statements = re.findall(
        r"create\s+index\s+if\s+not\s+exists\s+[^;]+;",
        SQL_LOWER,
    )
    assert len(index_statements) >= 2
    assert any("on public.oasis_work_tasks" in statement for statement in index_statements)
    assert any(
        "on public.oasis_work_task_events" in statement
        and "task_id" in statement
        and "created_at" in statement
        for statement in index_statements
    )
    inherited_migration = (
        ROOT / "supabase" / "migrations" / "20260803092000_v910_task_automation.sql"
    ).read_text(encoding="utf-8").lower()
    assert "create index if not exists oasis_work_tasks_assignee_due_idx" in inherited_migration
    assert "assigned_user_id, status, due_at, created_at" in inherited_migration


def test_all_public_rpcs_are_service_role_only():
    acl_start = SQL_LOWER.index("do $v912_function_acl$")
    acl_end = SQL_LOWER.index("$v912_function_acl$;", acl_start + 1)
    acl = SQL_LOWER[acl_start:acl_end]
    for name in RPC_NAMES:
        assert f"'{name}'" in acl
    assert (
        "revoke all on function public.%i(%s) from public, anon, authenticated, service_role"
        in acl
    )
    assert "grant execute on function public.%i(%s) to service_role" in acl
    assert "grant execute on function public.%i(%s) to authenticated" not in acl
