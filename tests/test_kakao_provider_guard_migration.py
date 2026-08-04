from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / (
    "20260804161208_kakao_enrichment_runtime_guards.sql"
)


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8").lower()


def test_guard_state_is_durable_and_not_bound_to_quota_date():
    sql = _sql()
    table = sql.split(
        "create table if not exists "
        "public.oasis_contact_provider_guard_state",
        1,
    )[1].split(");", 1)[0]

    assert "guard_generation bigint not null default 0" in table
    assert "approved_generation bigint not null default 0" in table
    assert "consumed_generation bigint not null default 0" in table
    assert "quota_date" not in table
    assert "consumed_generation <= approved_generation" in table
    assert "approved_generation <= guard_generation" in table


def test_guard_rpcs_are_service_role_only_security_invokers():
    sql = _sql()
    functions = (
        "oasis_get_contact_provider_guard",
        "oasis_trip_contact_provider_guard",
        "oasis_approve_contact_provider_guard",
        "oasis_consume_contact_provider_resume",
    )

    assert "alter table public.oasis_contact_provider_guard_state\n" in sql
    assert "force row level security" in sql
    assert "security definer" not in sql
    for function in functions:
        assert f"create or replace function public.{function}" in sql
        assert f"grant execute on function public.{function}" in sql
    assert sql.count("from public, anon, authenticated") >= 13


def test_approval_is_generation_bound_explicit_and_rejects_active_lease():
    sql = _sql()
    acquire = sql.split(
        "create or replace function "
        "public.oasis_acquire_contact_provider_lease",
        1,
    )[1].split("create or replace function", 1)[0]
    approve = sql.split(
        "create or replace function "
        "public.oasis_approve_contact_provider_guard",
        1,
    )[1].split("create or replace function", 1)[0]

    assert "p_confirmation" in approve
    assert "kakao_restart_approved" in approve
    assert "g.guard_generation = p_expected_generation" in approve
    assert "g.guard_generation > g.approved_generation" in approve
    assert "oasis_contact_provider_job_leases" in approve
    assert "l.expires_at > v_now" in approve
    assert "return false" in approve
    advisory_lock = (
        "pg_advisory_xact_lock(\n"
        "        hashtextextended('oasis_contact_provider:kakao_local', 0)"
    )
    assert advisory_lock in acquire
    assert advisory_lock in approve
    assert acquire.index(advisory_lock) < acquire.index(
        "v_now := clock_timestamp()"
    )
    assert approve.index(advisory_lock) < approve.index(
        "v_now := clock_timestamp()"
    )


def test_trip_is_idempotent_and_consume_requires_the_active_lease():
    sql = _sql()
    trip = sql.split(
        "create or replace function public.oasis_trip_contact_provider_guard",
        1,
    )[1].split("create or replace function", 1)[0]
    consume = sql.split(
        "create or replace function "
        "public.oasis_consume_contact_provider_resume",
        1,
    )[1].split("revoke all on function", 1)[0]

    assert "p_incident_token" in trip
    assert "if v_guard_generation > v_approved_generation" in trip
    assert "if v_incident_token = p_incident_token" in trip
    assert "l.lease_token = p_lease_token" in trip
    assert "l.lease_token = p_lease_token" in consume
    assert "g.guard_generation = p_expected_generation" in consume
    assert "g.approved_generation = g.guard_generation" in consume
    assert "g.consumed_generation < g.approved_generation" in consume
    for function in (trip, consume):
        assert function.index("pg_advisory_xact_lock") < function.index(
            "v_now := clock_timestamp()"
        )
