from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = ROOT / "supabase_v1032_company_sales_assignments.sql"
RLS_PATH = ROOT / "supabase_v1032_company_sales_assignments_rls.sql"
ASSIGNMENT_PATH = ROOT / "company_sales_assignment.py"
REPOSITORY_PATH = ROOT / "prospect_db_repository.py"
CENTER_PATH = ROOT / "prospect_db_center.py"
EXPIRY_72_MIGRATION_PATH = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260824053418_extend_assignment_expiry_to_72_hours.sql"
)

SQL = SQL_PATH.read_text(encoding="utf-8")
SQL_LOWER = SQL.lower()
RLS_LOWER = RLS_PATH.read_text(encoding="utf-8").lower()
ASSIGNMENT_SOURCE = ASSIGNMENT_PATH.read_text(encoding="utf-8")
REPOSITORY_SOURCE = REPOSITORY_PATH.read_text(encoding="utf-8")
CENTER_SOURCE = CENTER_PATH.read_text(encoding="utf-8")
EXPIRY_72_MIGRATION = EXPIRY_72_MIGRATION_PATH.read_text(
    encoding="utf-8"
).lower()


def _function_sql(name: str) -> str:
    """Return one complete SQL function definition without executing it."""

    marker = re.search(
        rf"create\s+or\s+replace\s+function\s+public\.{re.escape(name)}\s*\(",
        SQL,
        flags=re.IGNORECASE,
    )
    if marker is None:
        raise AssertionError(f"missing SQL function: {name}")
    end = SQL.find("\n$$;", marker.start())
    if end < 0:
        raise AssertionError(f"unterminated SQL function: {name}")
    return SQL[marker.start() : end + len("\n$$;")]


def _python_function(source: str, name: str) -> str:
    marker = re.search(rf"^def\s+{re.escape(name)}\s*\(", source, re.MULTILINE)
    if marker is None:
        raise AssertionError(f"missing Python function: {name}")
    next_function = re.search(r"^def\s+", source[marker.end() :], re.MULTILINE)
    end = (
        marker.end() + next_function.start()
        if next_function is not None
        else len(source)
    )
    return source[marker.start() : end]


class CompanySalesAssignmentMigrationStaticTests(unittest.TestCase):
    """Static contracts only; this suite never opens a Supabase connection."""

    def test_feature_ready_requires_compatibility_rpcs(self):
        ready_sql = _function_sql(
            "oasis_company_sales_assignment_feature_ready"
        ).lower()
        self.assertIn("oasis_resolve_candidate_company_uids", ready_sql)
        self.assertIn(
            "oasis_claim_and_save_company_sales_assignment", ready_sql
        )
        self.assertIn("oasis_resolve_candidate_company_uids", RLS_LOWER)

    def test_company_uid_priority_and_normalization_match_application(self):
        sql_uid = _function_sql("oasis_make_company_uid").lower()
        python_uid = _python_function(ASSIGNMENT_SOURCE, "build_company_uid")
        sql_phone = _function_sql("oasis_normalize_sales_phone").lower()
        python_phone = _python_function(ASSIGNMENT_SOURCE, "_normalize_phone")

        for source in (sql_uid, python_uid):
            positions = [
                source.index("business:"),
                source.index("corporate:"),
                source.index("nps:"),
                source.index("fallback"),
            ]
            self.assertEqual(
                positions,
                sorted(positions),
                "company UID priority must be business > corporate > NPS > fallback",
            )

        self.assertIn(
            "v_name is not null and v_address is not null and v_phone is not null",
            sql_uid,
        )
        self.assertIn(
            "normalized_name and normalized_address and normalized_phone",
            python_uid,
        )
        self.assertIn("v_source is not null and v_source_key is not null", sql_uid)
        self.assertIn("if source and source_key", python_uid)
        for source in (sql_phone, python_phone):
            self.assertIn("0082", source)
            self.assertIn("82", source)
        self.assertIn("[^0-9a-z]", SQL_LOWER)
        self.assertIn("nfkc", SQL_LOWER)
        self.assertIn("unicodedata.normalize", ASSIGNMENT_SOURCE)

    def test_scenario_1_view_only_records_history_and_never_claims(self):
        view_sql = _function_sql("oasis_record_company_views").lower()
        self.assertIn("insert into public.oasis_company_view_history", view_sql)
        self.assertNotIn("insert into public.oasis_company_sales_assignments", view_sql)
        self.assertNotIn("assignment_expires_at", view_sql)
        self.assertNotIn("assigned_user_id", view_sql)
        self.assertIn("record_company_views", CENTER_SOURCE)
        self.assertNotIn("claim_company(", _python_function(ASSIGNMENT_SOURCE, "record_company_views"))

    def test_scenarios_2_and_3_claim_is_temporary_limited_and_atomic(self):
        claim_sql = _function_sql("oasis_claim_company_sales_assignment").lower()
        assignment_table = SQL_LOWER[
            SQL_LOWER.index("create table if not exists public.oasis_company_sales_assignments") :
            SQL_LOWER.index("create table if not exists public.oasis_sales_assignment_settings")
        ]

        self.assertIn("company_uid text not null unique", assignment_table)
        self.assertIn("pg_advisory_xact_lock", claim_sql)
        self.assertIn("'oasis-company:' || v_uid", claim_sql)
        self.assertIn("'oasis-user:' || p_current_user_id", claim_sql)
        self.assertIn("for update", claim_sql)
        self.assertIn(
            "on conflict on constraint "
            "oasis_company_sales_assignments_company_uid_key",
            claim_sql,
        )
        self.assertNotIn("on conflict (company_uid) do nothing", claim_sql)
        self.assertIn("'duplicate_assignment_attempt'", claim_sql)
        self.assertIn("'already_assigned'", claim_sql)
        self.assertIn("status = 'assigned'", claim_sql)
        self.assertIn("make_interval(hours => v_hours)", claim_sql)
        self.assertRegex(
            SQL_LOWER,
            r"assignment_hours\s+integer\s+not\s+null\s+default\s+72",
        )
        self.assertRegex(
            SQL_LOWER,
            r"max_uncontacted\s+integer\s+not\s+null\s+default\s+30",
        )
        self.assertIn("v_uncontacted >= v_limit", claim_sql)
        self.assertIn("save_assigned_prospects", REPOSITORY_SOURCE)
        self.assertIn("claim_and_save_companies(", REPOSITORY_SOURCE)
        self.assertIn("내 영업db에 담기", CENTER_SOURCE.lower())

    def test_scenario_4_expired_uncontacted_assignment_is_released(self):
        expiry_sql = _function_sql("oasis_release_expired_company_assignments").lower()
        filter_sql = _function_sql("oasis_filter_blocked_company_uids").lower()

        self.assertIn("a.assignment_expires_at <= now()", expiry_sql)
        self.assertIn("a.current_assignment_contact_count = 0", expiry_sql)
        self.assertIn("a.current_assignment_first_contacted_at is null", expiry_sql)
        self.assertIn("a.legacy_hold is false", expiry_sql)
        self.assertIn("assigned_user_id = null", expiry_sql)
        self.assertIn("status = 'unassigned'", expiry_sql)
        self.assertIn("released_reason = 'assignment_expired'", expiry_sql)
        self.assertIn("'assignment_expired'", expiry_sql)
        self.assertIn("oasis_release_expired_company_assignments", filter_sql)

    def test_72_hour_migration_updates_defaults_and_active_assignments(self):
        self.assertIn(
            "alter column assignment_hours set default 72",
            EXPIRY_72_MIGRATION,
        )
        self.assertIn("assignment_hours = 72", EXPIRY_72_MIGRATION)
        self.assertIn("assigned_at + interval '72 hours'", EXPIRY_72_MIGRATION)
        self.assertIn(
            "current_assignment_contact_count = 0",
            EXPIRY_72_MIGRATION,
        )
        self.assertIn(
            "current_assignment_first_contacted_at is null",
            EXPIRY_72_MIGRATION,
        )

    def test_scenario_5_contact_finalizes_owner_and_counts_every_attempt(self):
        contact_sql = _function_sql("oasis_record_company_sales_contact").lower()

        self.assertIn("v_assignment.assigned_user_id <> p_current_user_id", contact_sql)
        for result in ("missed", "connected", "sms_sent", "kakao_sent"):
            self.assertIn(f"'{result}'", contact_sql)
        self.assertIn("assignment_expires_at = null", contact_sql)
        self.assertIn("first_contacted_at = coalesce", contact_sql)
        self.assertIn("last_contacted_at = greatest", contact_sql)
        self.assertIn("contact_count = a.contact_count + 1", contact_sql)
        self.assertIn("insert into public.oasis_company_sales_contact_logs", contact_sql)
        self.assertIn("'contact_result_recorded'", contact_sql)
        self.assertIn("when v_result = 'follow_up_requested' then 'follow_up'", contact_sql)
        self.assertIn("p_next_contact_at is null", contact_sql)

    def test_scenario_6_rejection_blocks_then_reactivates_after_default_period(self):
        contact_sql = _function_sql("oasis_record_company_sales_contact").lower()
        expiry_sql = _function_sql("oasis_release_expired_company_assignments").lower()

        self.assertIn("when v_result = 'not_interested' then 'rejected'", contact_sql)
        self.assertRegex(
            SQL_LOWER,
            r"rejected_reactivation_days\s+integer\s+not\s+null\s+default\s+180",
        )
        self.assertIn("make_interval(days => v_rejected_days)", contact_sql)
        self.assertIn("a.status in ('rejected', 'unreachable')", expiry_sql)
        self.assertIn("a.reactivate_at <= now()", expiry_sql)
        self.assertIn("'assignment_reactivated_automatically'", expiry_sql)

    def test_scenario_7_wrong_number_waits_for_real_phone_change_and_is_audited(self):
        contact_sql = _function_sql("oasis_record_company_sales_contact").lower()
        expiry_sql = _function_sql("oasis_release_expired_company_assignments").lower()
        fingerprint_sql = _function_sql(
            "oasis_company_sales_phone_fingerprint"
        ).lower()

        self.assertIn("when v_result = 'bad_number' then 'wrong_number'", contact_sql)
        self.assertIn("wrong_number_phone_fingerprint", contact_sql)
        self.assertIn("a.status = 'wrong_number'", expiry_sql)
        self.assertIn("is distinct from a.wrong_number_phone_fingerprint", expiry_sql)
        self.assertIn("released_reason = 'valid_phone_changed_after_wrong_number'", expiry_sql)
        self.assertIn("'wrong_number_reactivated_after_phone_change'", expiry_sql)
        self.assertIn("'phone_fingerprint'", expiry_sql)
        self.assertIn("language plpgsql", fingerprint_sql)
        self.assertIn(
            "to_regclass('public.oasis_employment_contacts') is not null",
            fingerprint_sql,
        )
        self.assertIn("execute $employment$", fingerprint_sql)
        self.assertIn("c.mobile_phone", fingerprint_sql)
        self.assertIn("c.landline_phone", fingerprint_sql)
        self.assertIn("c.source_record_key", fingerprint_sql)
        self.assertIn("c.contact_key = p.company_uid", fingerprint_sql)

    def test_candidate_uid_resolver_is_batched_canonical_and_service_only(self):
        resolver_sql = _function_sql(
            "oasis_resolve_candidate_company_uids"
        ).lower()

        self.assertIn("p_current_user_id text", resolver_sql)
        self.assertIn("p_candidates jsonb", resolver_sql)
        self.assertIn("candidate_index integer", resolver_sql)
        self.assertIn("input_company_uid text", resolver_sql)
        self.assertIn("canonical_company_uid text", resolver_sql)
        self.assertIn("resolution_code text", resolver_sql)
        self.assertIn("oasis_sales_actor_is_active", resolver_sql)
        self.assertIn("jsonb_array_elements(p_candidates) with ordinality", resolver_sql)
        self.assertIn("entry.ordinality <= 1000", resolver_sql)
        self.assertIn("'strong_identifier'", resolver_sql)
        self.assertIn("'source_identity'", resolver_sql)
        self.assertIn("'strong_identifier_conflict'", resolver_sql)
        self.assertIn("p.source_key", resolver_sql)
        self.assertIn("p.business_no", resolver_sql)
        self.assertIn("p.corporate_registration_no", resolver_sql)
        self.assertIn("p.nps_workplace_management_no", resolver_sql)
        self.assertIn("'oasis_resolve_candidate_company_uids'", SQL_LOWER)

    def test_atomic_save_canonicalizes_source_uid_without_lock_order_inversion(self):
        atomic_sql = _function_sql(
            "oasis_claim_and_save_company_sales_assignment"
        ).lower()

        cleanup_pos = atomic_sql.index("oasis_release_expired_company_assignments")
        source_lookup_pos = atomic_sql.index("where p.source = v_source")
        claim_pos = atomic_sql.index("from public.oasis_claim_company_sales_assignment")
        self.assertLess(cleanup_pos, source_lookup_pos)
        self.assertLess(source_lookup_pos, claim_pos)
        self.assertNotIn("for update", atomic_sql[:claim_pos])
        self.assertIn("v_uid := v_existing_uid", atomic_sql)
        self.assertIn("v_strong_identity_conflict", atomic_sql)
        self.assertIn("v_business_no <> v_existing_business_no", atomic_sql)
        self.assertIn("v_corporate_no <> v_existing_corporate_no", atomic_sql)
        self.assertIn("v_nps_no <> v_existing_nps_no", atomic_sql)
        self.assertIn("'source_identity_conflict'", atomic_sql)
        self.assertIn("v_uid_matches_strong_identity", atomic_sql)
        self.assertIn("'company_uid_mismatch'", atomic_sql)

    def test_scenario_8_rls_and_server_authorization_prevent_cross_user_access(self):
        protected_tables = (
            "oasis_company_sales_assignments",
            "oasis_sales_assignment_settings",
            "oasis_user_prospect_notes",
            "oasis_company_sales_contact_logs",
            "oasis_company_view_history",
            "oasis_company_assignment_audit_logs",
            "oasis_company_assignment_conflicts",
        )
        for table in protected_tables:
            self.assertIn(
                f"alter table public.{table} enable row level security",
                SQL_LOWER,
            )
            self.assertRegex(
                SQL_LOWER,
                rf"revoke\s+all\s+on\s+table\s+public\.{table}\s+from\s+public,\s*anon,\s*authenticated",
            )
            self.assertRegex(
                SQL_LOWER,
                rf"grant\s+select,\s*insert,\s*update,\s*delete\s+on\s+table\s+public\.{table}\s+to\s+service_role",
            )

        self.assertNotRegex(
            SQL_LOWER,
            r"grant\s+.*\s+on\s+table\s+public\.oasis_company_.*\s+to\s+(anon|authenticated)",
        )
        self.assertNotRegex(
            SQL_LOWER,
            r"(?:revoke|grant)\s+[^;]*on\s+all\s+sequences\s+in\s+schema\s+public",
            "the patch must never change privileges on unrelated public sequences",
        )

        contact_list_sql = _function_sql("oasis_list_company_sales_contacts").lower()
        self.assertIn("oasis_sales_actor_is_active", contact_list_sql)
        self.assertIn("oasis_sales_actor_is_admin", contact_list_sql)
        self.assertIn("assigned_user_id = p_current_user_id", contact_list_sql)
        self.assertIn("from public.oasis_users", SQL_LOWER)
        self.assertIn("u.status = 'approved'", SQL_LOWER)

    def test_scenario_9_admin_reassignment_requires_admin_reason_and_audit(self):
        admin_sql = _function_sql("oasis_admin_change_company_assignee").lower()
        audit_sql = _function_sql("oasis_list_company_assignment_audit").lower()

        self.assertIn("oasis_sales_actor_is_admin", admin_sql)
        self.assertIn("nullif(btrim(p_reason), '') is null", admin_sql)
        self.assertIn("for update", admin_sql)
        self.assertIn("assigned_user_id = p_new_assigned_user_id", admin_sql)
        self.assertIn("owner_user_id = p_new_assigned_user_id", admin_sql)
        self.assertIn("'assignee_changed'", admin_sql)
        self.assertIn("previous_value", SQL_LOWER)
        self.assertIn("new_value", SQL_LOWER)
        self.assertIn("oasis_sales_actor_is_admin", audit_sql)
        self.assertIn("order by l.created_at desc", audit_sql)

    def test_scenario_10_legacy_migration_is_additive_idempotent_and_non_destructive(self):
        self.assertIn("begin;", SQL_LOWER)
        self.assertIn("commit;", SQL_LOWER)
        self.assertNotRegex(SQL_LOWER, r"\bdrop\s+table\b")
        self.assertNotRegex(SQL_LOWER, r"\btruncate\b")
        for legacy_table in ("oasis_prospect_companies", "oasis_crm"):
            self.assertNotRegex(
                SQL_LOWER,
                rf"delete\s+from\s+public\.{legacy_table}\b",
            )

        self.assertIn(
            "alter table public.oasis_prospect_companies\n    add column if not exists company_uid",
            SQL_LOWER,
        )
        for table in (
            "oasis_company_sales_assignments",
            "oasis_user_prospect_notes",
            "oasis_company_assignment_conflicts",
        ):
            self.assertIn(f"create table if not exists public.{table}", SQL_LOWER)
        self.assertIn("create index if not exists", SQL_LOWER)
        self.assertIn("create or replace function", SQL_LOWER)
        self.assertIn("on conflict (company_uid, user_id) do nothing", SQL_LOWER)
        self.assertIn("string_agg", SQL_LOWER)
        self.assertIn("legacy_multiple_owners", SQL_LOWER)
        self.assertIn("conflicting_user_ids", SQL_LOWER)
        self.assertIn("migration_conflict", SQL_LOWER)
        self.assertRegex(SQL_LOWER, r"\btrue\s*,\s*true\s*\nfrom public\.oasis_company_assignment_conflicts")
        self.assertIn("legacy_hold", SQL_LOWER)

    def test_all_privileged_rpcs_have_fixed_search_path_and_service_role_only(self):
        privileged_rpcs = (
            "oasis_claim_company_sales_assignment",
            "oasis_record_company_sales_contact",
            "oasis_release_company_sales_assignment",
            "oasis_release_expired_company_assignments",
            "oasis_admin_change_company_assignee",
            "oasis_admin_release_company_assignment",
            "oasis_admin_reactivate_company_assignment",
            "oasis_admin_permanent_exclude_company",
        )
        for name in privileged_rpcs:
            function_sql = _function_sql(name).lower()
            self.assertIn("set search_path = public, pg_temp", function_sql)
            self.assertTrue(
                "oasis_sales_actor_is_active" in function_sql
                or "oasis_sales_actor_is_admin" in function_sql,
                f"{name} must validate the custom OASIS actor server-side",
            )
        self.assertIn("from public, anon, authenticated", SQL_LOWER)
        self.assertIn("to service_role", SQL_LOWER)


if __name__ == "__main__":
    unittest.main()
