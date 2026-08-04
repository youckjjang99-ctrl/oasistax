from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804220839_recover_growth_kakao_queue.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8").lower()
SOURCE_GUARD_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804221258_growth_kakao_snapshot_source_guard.sql"
)
SOURCE_GUARD_SQL = SOURCE_GUARD_MIGRATION.read_text(
    encoding="utf-8"
).lower()
STATUS_OPTIMIZATION_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804222809_optimize_growth_kakao_recovery_status.sql"
)
STATUS_OPTIMIZATION_SQL = STATUS_OPTIMIZATION_MIGRATION.read_text(
    encoding="utf-8"
).lower()
PHASED_VERIFICATION_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804233142_phase_growth_kakao_recovery_verification.sql"
)
PHASED_VERIFICATION_SQL = PHASED_VERIFICATION_MIGRATION.read_text(
    encoding="utf-8"
).lower()
LEASE_CONTINUITY_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804233559_require_continuous_growth_recovery_lease.sql"
)
LEASE_CONTINUITY_SQL = LEASE_CONTINUITY_MIGRATION.read_text(
    encoding="utf-8"
).lower()


def test_growth_recovery_uses_exact_inclusive_windows_and_counts():
    assert "20260805_growth_kakao_provider_error_recovery" in SQL
    assert "2026-07-29 01:30:18.222368+00" in SQL
    assert "2026-07-29 03:34:21.766558+00" in SQL
    assert "2026-07-31 18:40:57.54245+00" in SQL
    assert "2026-08-01 22:01:19.552288+00" in SQL
    assert "2026-08-01 23:52:49.510115+00" in SQL
    assert "2026-08-02 01:54:36.971516+00" in SQL
    assert "2026-08-03 01:20:27.086323+00" in SQL
    assert "2026-08-03 02:18:48.295961+00" in SQL
    for expected in ("77254", "33989", "98764", "43789", "253796"):
        assert expected in SQL
    assert "ec.phone_checked_at >= w.started_at" in SQL
    assert "ec.phone_checked_at <= w.ended_at" in SQL


def test_growth_recovery_target_signature_excludes_completed_naver_rows():
    prepare = SQL.split(
        "create or replace function "
        "oasis_private.oasis_prepare_growth_kakao_recovery"
    )[1].split("create or replace function", 1)[0]
    assert "ec.employee_growth > 0" in prepare
    assert "('comwel_annual', 'nps_monthly')" in prepare
    assert "ec.phone_provider_stage = 'naver'" in prepare
    assert "ec.phone_status = 'pending'" in prepare
    assert "ec.phone_attempt_count = 1" in prepare
    assert "ec.phone_next_check_at = ec.phone_checked_at" in prepare
    assert "coalesce(ec.phone_last_error, '') = ''" in prepare
    assert "coalesce(ec.mobile_phone, '') = ''" in prepare
    assert "coalesce(ec.landline_phone, '') = ''" in prepare


def test_growth_recovery_backs_up_full_rows_before_mutation():
    snapshot = SQL.split(
        "create or replace function "
        "oasis_private.oasis_snapshot_growth_kakao_batch"
    )[1].split("create or replace function", 1)[0]
    recovery = SQL.split(
        "create or replace function "
        "oasis_private.oasis_recover_growth_kakao_batch"
    )[1].split("create or replace function", 1)[0]
    assert "to_jsonb(ec) as prior_state" in snapshot
    assert "i.prior_state is null" in snapshot
    assert "i.prior_state is not null" in recovery
    assert "to_jsonb(ec) is not distinct from b.prior_state" in recovery


def test_growth_recovery_mutates_only_phone_fields_and_trigger_timestamp():
    recovery = SQL.split(
        "create or replace function "
        "oasis_private.oasis_recover_growth_kakao_batch"
    )[1].split("create or replace function", 1)[0]
    update_sql = recovery.split(
        "update public.oasis_employment_contacts ec"
    )[1].split("returning ec.contact_key", 1)[0]
    assert "phone_provider_stage = 'kakao'" in update_sql
    assert "phone_status = 'pending'" in update_sql
    assert "phone_checked_at = null" in update_sql
    assert "phone_next_check_at = null" in update_sql
    assert "phone_attempt_count = 0" in update_sql
    assert "phone_last_error = ''" in update_sql
    assigned_columns = set(
        re.findall(r"^\s*([a-z_]+)\s*=", update_sql, re.MULTILINE)
    )
    assert assigned_columns == {
        "phone_provider_stage",
        "phone_status",
        "phone_checked_at",
        "phone_next_check_at",
        "phone_attempt_count",
        "phone_last_error",
    }
    assert "'updated_at'" in recovery
    assert "current_state - v_mutated_columns" in recovery
    assert "prior_state - v_mutated_columns" in recovery


def test_growth_recovery_is_batched_leased_and_idempotent():
    assert "least(coalesce(p_batch_size, 5000), 5000)" in SQL
    assert "for update of i skip locked" in SQL
    assert SQL.count("oasis_acquire_contact_provider_lease") >= 4
    assert "oasis_release_contact_provider_lease" in SQL
    assert "if v_status = 'completed'" in SQL
    assert SQL.count(
        "return oasis_private.oasis_growth_kakao_recovery_status()"
    ) >= 4
    assert "pg_advisory_xact_lock" in SQL


def test_growth_recovery_private_tables_force_rls_and_deny_app_roles():
    for table in (
        "oasis_growth_kakao_recovery_runs",
        "oasis_growth_kakao_recovery_windows",
        "oasis_growth_kakao_recovery_items",
        "oasis_growth_kakao_recovery_control",
    ):
        assert f"alter table oasis_private.{table}\n    enable row level security" in SQL
        assert f"alter table oasis_private.{table}\n    force row level security" in SQL
    assert "grant select on table" not in SQL
    assert "grant usage on schema oasis_private" not in SQL
    assert "from public, anon, authenticated, service_role" in SQL


def test_growth_recovery_checks_exact_window_source_split_and_naver_idle():
    for expected in (
        "76154",
        "1100",
        "33257",
        "732",
        "97066",
        "1698",
        "42879",
        "910",
    ):
        assert expected in SQL
    assert SQL.count("phone_provider_stage = 'naver'") >= 6
    assert SQL.count("phone_status = 'processing'") >= 4
    assert "for update of ec" in SQL
    assert "to_jsonb(ec) is not distinct from v.prior_state" in SQL
    assert "prior_state->>'source_type' = source_type" in SOURCE_GUARD_SQL
    assert "validate constraint" in SOURCE_GUARD_SQL


def test_growth_recovery_helpers_are_private_security_invokers():
    assert SQL.count("security invoker") == 5
    assert SQL.count("revoke all on function") == 5
    assert "security definer" not in SQL
    assert "grant execute" not in SQL


def test_growth_recovery_migration_does_not_mutate_contacts_on_apply():
    top_level = SQL.split(
        "create or replace function "
        "oasis_private.oasis_prepare_growth_kakao_recovery"
    )[0]
    assert "update public.oasis_employment_contacts" not in top_level
    assert "delete from public.oasis_employment_contacts" not in SQL
    assert "truncate" not in SQL


def test_growth_recovery_status_uses_guarded_run_counters():
    assert "recovered_count <= snapshot_count" in STATUS_OPTIMIZATION_SQL
    assert "snapshot_count <= selected_count" in STATUS_OPTIMIZATION_SQL
    assert "selected_count <= expected_count" in STATUS_OPTIMIZATION_SQL
    assert "r.selected_count - r.snapshot_count" in STATUS_OPTIMIZATION_SQL
    assert "r.snapshot_count - r.recovered_count" in STATUS_OPTIMIZATION_SQL
    assert "oasis_growth_kakao_recovery_items" not in STATUS_OPTIMIZATION_SQL


def test_growth_recovery_final_verification_is_phased_and_guarded():
    sql = PHASED_VERIFICATION_SQL
    assert "verified_count <= recovered_count" in sql
    assert "p_batch_size integer default 5000" in sql
    assert "for update of i skip locked" in sql
    assert "for share of ec" in sql
    assert "order by b.contact_key" in sql
    assert "verified_at >= recovered_at" in sql
    assert "ec.phone_provider_stage = 'kakao'" in sql
    assert "ec.phone_status = 'pending'" in sql
    assert "to_jsonb(ec) - v_mutated_columns" in sql
    assert "b.prior_state - v_mutated_columns" in sql
    assert "verification batch invariant mismatch" in sql
    assert "v_verified_count <> v_expected_total" in sql
    assert "v_actual_verified_count <> v_expected_total" in sql
    assert "oasis_acquire_contact_provider_lease" not in sql
    assert sql.count("oasis_renew_contact_provider_lease") == 2
    assert "order by b.contact_key" in sql
    assert "verified_at >= recovered_at" in sql
    assert sql.count("phone_status = 'processing'") == 2
    assert "security definer" not in sql
    assert "grant execute" not in sql


def test_phased_verification_migration_never_updates_contacts():
    sql = PHASED_VERIFICATION_SQL
    assert "update public.oasis_employment_contacts" not in sql
    assert "delete from public.oasis_employment_contacts" not in sql


def test_growth_recovery_verification_requires_continuous_lease():
    sql = LEASE_CONTINUITY_SQL
    assert "oasis_acquire_contact_provider_lease" not in sql
    assert sql.count("oasis_renew_contact_provider_lease") == 2
    assert "oasis_verify_growth_kakao_batch" in sql
    assert "oasis_finalize_growth_kakao_recovery" in sql
    assert "update public.oasis_employment_contacts" not in sql
