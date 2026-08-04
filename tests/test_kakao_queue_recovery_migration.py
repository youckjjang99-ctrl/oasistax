from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804165657_recover_20260804_kakao_queue.sql"
).read_text(encoding="utf-8")
SQL = " ".join(MIGRATION.lower().split())


def test_recovery_is_transactional_serialized_and_repeat_safe() -> None:
    assert MIGRATION.lower().lstrip().startswith("begin;")
    assert MIGRATION.lower().rstrip().endswith("commit;")
    assert "pg_advisory_xact_lock" in SQL
    assert "20260804_kakao_provider_error_queue_recovery" in SQL
    assert "v_existing_status = 'completed'" in SQL
    assert "a completed, internally consistent run is a safe no-op" in SQL
    assert "incomplete or inconsistent" in SQL
    assert "v_run_id := gen_random_uuid()" in SQL
    assert "20260804-1411-4121-a5a0-000000054250" not in SQL


def test_recovery_refuses_to_race_an_active_kakao_collector() -> None:
    assert "oasis_acquire_contact_provider_lease" in SQL
    assert "v_recovery_lease_token" in SQL
    assert "'kakao_local'" in SQL
    assert "kakao provider job is active; recovery aborted safely" in SQL
    assert "oasis_release_contact_provider_lease" in SQL
    assert "kakao recovery lease release failed; recovery aborted" in SQL


def test_recovery_uses_the_exact_audited_cohorts_and_counts() -> None:
    assert "v_expected_moved constant integer := 54238" in SQL
    assert "v_expected_moved_comwel constant integer := 54235" in SQL
    assert "v_expected_moved_nps constant integer := 3" in SQL
    assert "v_expected_stuck constant integer := 12" in SQL
    assert "v_expected_total constant integer := 54250" in SQL

    assert "ec.source_type in ('comwel_all_employers', 'nps_monthly')" in SQL
    assert "ec.phone_provider_stage = 'naver'" in SQL
    assert "ec.phone_attempt_count = 1" in SQL
    assert "ec.attempt_count = 1" in SQL
    assert "ec.phone_checked_at >= timestamptz '2026-08-03 17:44:51+00'" in SQL
    assert "ec.phone_checked_at < timestamptz '2026-08-04 04:30:32+00'" in SQL

    assert "ec.source_type = 'comwel_all_employers'" in SQL
    assert "ec.created_at = timestamptz '2026-08-04 03:05:22.347848+00'" in SQL
    assert "ec.phone_provider_stage = 'kakao'" in SQL
    assert "ec.phone_status = 'processing'" in SQL
    assert "ec.updated_at >= timestamptz '2026-08-04 04:30:32+00'" in SQL
    assert "ec.updated_at < timestamptz '2026-08-04 04:30:33+00'" in SQL

    assert "recheck every locked row immediately before the backup and update" in SQL
    assert "v_signature_count <> v_expected_total" in SQL


def test_full_row_backups_are_private_and_service_role_read_only() -> None:
    assert "create schema if not exists oasis_private" in SQL
    assert "oasis_private.oasis_kakao_queue_recovery_runs" in SQL
    assert "oasis_private.oasis_kakao_queue_recovery_items" in SQL
    assert SQL.count("enable row level security") >= 2
    assert SQL.count("force row level security") >= 2
    assert "revoke all on schema oasis_private from public, anon, authenticated" in SQL
    assert "revoke all on schema oasis_private from service_role" in SQL
    assert "from public, anon, authenticated" in SQL
    assert "from service_role" in SQL
    assert "grant select on table" in SQL
    assert "to service_role" in SQL
    assert "grant insert" not in SQL
    assert "grant update" not in SQL
    assert "grant delete" not in SQL
    assert "prior_state jsonb not null" in SQL
    assert "to_jsonb(ec)" in SQL
    assert "private full-row snapshots retained for an exact rollback" in SQL


def test_update_is_driven_only_by_persisted_backup_keys() -> None:
    update_start = SQL.index("update public.oasis_employment_contacts ec set")
    update_end = SQL.index("get diagnostics v_updated_count", update_start)
    update_sql = SQL[update_start:update_end]

    assert "from oasis_private.oasis_kakao_queue_recovery_items backup" in update_sql
    assert "backup.run_id = v_run_id" in update_sql
    assert "backup.contact_key = ec.contact_key" in update_sql
    assert "oasis_kakao_queue_recovery_targets" not in update_sql

    assert "phone_provider_stage = 'kakao'" in update_sql
    assert "phone_status = 'pending'" in update_sql
    assert "phone_checked_at = null" in update_sql
    assert "phone_next_check_at = null" in update_sql
    assert "phone_attempt_count = 0" in update_sql
    assert "phone_last_error = ''" in update_sql
    assert "status = 'pending'" in update_sql
    assert "checked_at = null" in update_sql
    assert "next_check_at = null" in update_sql
    assert "attempt_count = 0" in update_sql
    assert "last_error = ''" in update_sql

    for protected_column in (
        "mobile_phone",
        "landline_phone",
        "email",
        "instagram_id",
        "instagram_url",
        "contact_sources",
        "digital_status",
        "digital_checked_at",
        "digital_next_check_at",
        "digital_attempt_count",
        "digital_last_error",
    ):
        assert f"{protected_column} =" not in update_sql


def test_post_update_verification_preserves_contact_and_digital_state() -> None:
    assert "v_updated_count <> v_expected_total" in SQL
    assert "v_missing_count <> 0" in SQL
    assert "v_invalid_state_count <> 0" in SQL
    assert "v_preserved_difference_count <> 0" in SQL
    assert "to_jsonb(ec) - v_mutated_columns" in SQL
    assert "backup.prior_state - v_mutated_columns" in SQL
    assert "v_remaining_moved_count <> 0" in SQL
    assert "'preserved_difference_count'" in SQL
    assert "'invalid_state_count'" in SQL


def test_recovery_avoids_multi_million_row_global_invariant_scans() -> None:
    assert "from public.oasis_employment_contacts;" not in SQL
    remaining_start = SQL.index("into v_remaining_moved_count")
    remaining_end = SQL.index("v_invariants_after :=", remaining_start)
    remaining_sql = SQL[remaining_start:remaining_end]
    assert "oasis_kakao_queue_recovery_items backup" in remaining_sql
    assert "backup.run_id = v_run_id" in remaining_sql
    assert "'target_count', v_target_count" in SQL
    assert "'signature_count', v_signature_count" in SQL
    assert "'updated_count', v_updated_count" in SQL


def test_migration_never_emits_rows_or_sensitive_payloads() -> None:
    assert "raise notice" not in SQL
    assert "returning" not in SQL
    assert "api_key" not in SQL
    assert "response_body" not in SQL
