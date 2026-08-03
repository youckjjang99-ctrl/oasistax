from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIR = ROOT / "supabase" / "migrations"
MIGRATION_PATHS = sorted(MIGRATION_DIR.glob("*_v990_data_safety.sql"))


def _migration_sql() -> str:
    if len(MIGRATION_PATHS) != 1:
        raise AssertionError(
            "expected exactly one timestamped v990 data-safety migration, "
            f"found {len(MIGRATION_PATHS)}"
        )
    return MIGRATION_PATHS[0].read_text(encoding="utf-8")


SQL = _migration_sql()
SQL_LOWER = SQL.lower()


def _function_sql(name: str) -> str:
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


class DataSafetyMigrationStaticTests(unittest.TestCase):
    """Schema/security contracts only; this suite never contacts Supabase."""

    NEW_TABLES = (
        "oasis_sync_outbox",
        "oasis_sync_outbox_events",
        "oasis_customer_assets",
        "oasis_customer_asset_links",
        "oasis_copilot_assets",
        "oasis_copilot_company_memory",
        "oasis_copilot_success_cases",
        "oasis_copilot_checklists",
        "oasis_backup_runs",
        "oasis_restore_drills",
        "oasis_customer_archive_events",
    )

    OUTBOX_RPCS = (
        "oasis_enqueue_sync_outbox",
        "oasis_claim_sync_outbox",
        "oasis_complete_sync_outbox",
        "oasis_fail_sync_outbox",
        "oasis_retry_sync_outbox",
    )

    CUSTOMER_RETENTION_RPCS = (
        "oasis_archive_customer",
        "oasis_reactivate_customer",
    )

    def test_migration_is_timestamped_additive_and_non_destructive(self):
        self.assertRegex(
            MIGRATION_PATHS[0].name,
            r"^\d{14}_v990_data_safety[.]sql$",
        )
        self.assertIn("begin;", SQL_LOWER)
        self.assertIn("commit;", SQL_LOWER)
        self.assertNotRegex(SQL_LOWER, r"\bdrop\s+(table|schema|column)\b")
        self.assertNotRegex(SQL_LOWER, r"\btruncate\b")
        self.assertNotRegex(SQL_LOWER, r"\bdelete\s+from\b")
        self.assertNotIn("tilko", SQL_LOWER)
        self.assertNotIn("claim_case", SQL_LOWER)

        for table in self.NEW_TABLES:
            self.assertIn(
                f"create table if not exists public.{table}",
                SQL_LOWER,
            )

    def test_existing_customer_and_audio_changes_are_archive_metadata_only(self):
        customer_alter = re.search(
            r"alter table(?: if exists)? public[.]oasis_customers\s+(.*?);",
            SQL,
            flags=re.IGNORECASE | re.DOTALL,
        )
        self.assertIsNotNone(customer_alter)
        customer_sql = customer_alter.group(1).lower()
        for column in (
            "lifecycle_status",
            "archived_at",
            "archived_by_user_id",
            "archive_reason",
            "retention_class",
            "merged_into_customer_id",
        ):
            self.assertIn(f"add column if not exists {column}", customer_sql)
        self.assertNotIn("not null", customer_sql)
        self.assertNotIn(" default ", customer_sql)

        audio_alter = re.search(
            r"alter table if exists public[.]oasis_consultation_audio\s+(.*?);",
            SQL,
            flags=re.IGNORECASE | re.DOTALL,
        )
        self.assertIsNotNone(audio_alter)
        audio_sql = audio_alter.group(1).lower()
        self.assertIn("add column if not exists status text default 'active'", audio_sql)
        self.assertIn("add column if not exists archived_at timestamptz", audio_sql)
        self.assertIn("add column if not exists archived_by text", audio_sql)
        self.assertIn("add column if not exists archive_reason text", audio_sql)

    def test_required_customer_table_fails_clearly_and_audio_index_is_optional(self):
        self.assertIn(
            "OASIS_V990_REQUIRES_PUBLIC_OASIS_CUSTOMERS",
            SQL,
        )
        self.assertIn(
            "OASIS_V990_CUSTOMER_IDENTITY_SCHEMA_INCOMPATIBLE",
            SQL,
        )
        self.assertIn(
            "OASIS_V990_CUSTOMER_IDENTITY_DUPLICATE_OR_NULL",
            SQL,
        )
        self.assertRegex(
            SQL_LOWER,
            r"if\s+to_regclass\('public[.]oasis_customers'\)\s+is\s+null",
        )
        self.assertRegex(
            SQL_LOWER,
            r"(?s)if\s+to_regclass\('public[.]oasis_consultation_audio'\)\s+"
            r"is\s+not\s+null\s+then.*?"
            r"create\s+index\s+if\s+not\s+exists\s+"
            r"idx_oasis_consultation_audio_archive_status",
        )

    def test_every_new_public_table_is_force_rls_and_browser_locked(self):
        for table in self.NEW_TABLES:
            self.assertIn(
                f"alter table public.{table} enable row level security",
                SQL_LOWER,
            )
            self.assertIn(
                f"alter table public.{table} force row level security",
                SQL_LOWER,
            )
            self.assertRegex(
                SQL_LOWER,
                rf"revoke\s+all\s+on\s+table\s+public[.]{table}\s+"
                rf"from\s+public,\s*anon,\s*authenticated",
            )
            self.assertRegex(
                SQL_LOWER,
                rf"revoke\s+all\s+on\s+table\s+public[.]{table}\s+"
                rf"from\s+service_role",
            )
            self.assertRegex(
                SQL_LOWER,
                rf"grant\s+(?:select(?:,\s*insert)?(?:,\s*update)?)\s+on\s+"
                rf"table\s+public[.]{table}\s+to\s+service_role",
            )

        self.assertNotRegex(
            SQL_LOWER,
            r"grant\s+[^;]*\bdelete\b[^;]*\bto\s+service_role",
        )
        self.assertNotRegex(
            SQL_LOWER,
            r"grant\s+[^;]*\bon\s+table\s+public[.]oasis_[^;]*\bto\s+"
            r"(?:anon|authenticated)",
        )
        self.assertNotIn("auth.uid", SQL_LOWER)
        self.assertNotRegex(SQL_LOWER, r"create\s+policy\b")

    def test_outbox_has_idempotency_leases_retry_and_dead_letter_columns(self):
        table_start = SQL_LOWER.index(
            "create table if not exists public.oasis_sync_outbox"
        )
        table_end = SQL_LOWER.index(
            "create index if not exists idx_oasis_sync_outbox_claimable",
            table_start,
        )
        table_sql = SQL_LOWER[table_start:table_end]
        for column in (
            "idempotency_key text not null",
            "attempt_count integer not null default 0",
            "total_attempt_count bigint not null default 0",
            "max_attempts integer not null default 8",
            "next_retry_at timestamptz not null default now()",
            "leased_by text",
            "lease_expires_at timestamptz",
            "last_error_code text",
            "last_error_summary text",
            "manual_retry_count integer not null default 0",
            "completed_at timestamptz",
        ):
            self.assertIn(column, table_sql)
        self.assertIn("unique (owner_user_id, idempotency_key)", table_sql)
        self.assertIn("'dead_letter'", table_sql)
        self.assertIn("char_length(last_error_summary) <= 500", table_sql)

    def test_copilot_compatibility_asset_contract_is_durable_and_idempotent(self):
        table_start = SQL_LOWER.index(
            "create table if not exists public.oasis_copilot_assets"
        )
        table_end = SQL_LOWER.index(
            "create index if not exists idx_oasis_copilot_assets_owner_type_updated",
            table_start,
        )
        table_sql = SQL_LOWER[table_start:table_end]
        for column in (
            "owner_user_id text not null",
            "asset_type text not null",
            "asset_key text not null",
            "payload jsonb not null default '{}'::jsonb",
            "source_updated_at timestamptz",
        ):
            self.assertIn(column, table_sql)
        self.assertIn(
            "unique (owner_user_id, asset_type, asset_key)",
            table_sql,
        )
        self.assertIn(
            "asset_type in ('memory', 'success_case', 'checklist')",
            table_sql,
        )
        self.assertIn("jsonb_typeof(payload) = 'object'", table_sql)

    def test_enqueue_rpc_matches_python_contract_and_is_idempotent(self):
        function_sql = _function_sql("oasis_enqueue_sync_outbox").lower()
        for parameter in (
            "p_owner_user_id text",
            "p_job_type text",
            "p_entity_type text",
            "p_entity_id text",
            "p_payload jsonb",
            "p_idempotency_key text",
            "p_max_attempts integer default 8",
        ):
            self.assertIn(parameter, function_sql)
        self.assertIn("security definer", function_sql)
        self.assertIn("set search_path = public, pg_temp", function_sql)
        self.assertIn(
            "on conflict (owner_user_id, idempotency_key) do nothing",
            function_sql,
        )
        self.assertIn("oasis_outbox_idempotency_conflict", function_sql)
        for request_field in (
            "v_job.job_type is distinct from btrim(p_job_type)",
            "v_job.entity_type is distinct from btrim(p_entity_type)",
            "v_job.entity_id is distinct from nullif(btrim(p_entity_id), '')",
            "v_job.payload is distinct from coalesce(p_payload, '{}'::jsonb)",
            "v_job.max_attempts is distinct from greatest",
        ):
            self.assertIn(request_field, function_sql)

    def test_claim_rpc_is_atomic_nonblocking_and_leased(self):
        function_sql = _function_sql("oasis_claim_sync_outbox").lower()
        self.assertIn("p_owner_user_id text", function_sql)
        self.assertIn("p_worker_id text", function_sql)
        self.assertIn("p_limit integer default 25", function_sql)
        self.assertIn("p_lease_seconds integer default 300", function_sql)
        self.assertIn("for update skip locked", function_sql)
        self.assertIn("status = 'processing'", function_sql)
        self.assertIn("attempt_count = q.attempt_count + 1", function_sql)
        self.assertIn("total_attempt_count = q.total_attempt_count + 1", function_sql)
        self.assertIn("leased_by = btrim(p_worker_id)", function_sql)
        self.assertIn("lease_expires_at = now() + make_interval", function_sql)
        self.assertIn("status = 'dead_letter'", function_sql)
        self.assertGreaterEqual(
            function_sql.count("q.owner_user_id = btrim(p_owner_user_id)"),
            2,
        )
        self.assertIn("insert into public.oasis_sync_outbox_events", function_sql)
        self.assertIn("'claim'", function_sql)

    def test_complete_fail_and_manual_retry_are_fenced_and_audited(self):
        complete_sql = _function_sql("oasis_complete_sync_outbox").lower()
        fail_sql = _function_sql("oasis_fail_sync_outbox").lower()
        retry_sql = _function_sql("oasis_retry_sync_outbox").lower()

        for function_sql in (complete_sql, fail_sql):
            self.assertIn("p_job_id uuid", function_sql)
            self.assertIn("p_worker_id text", function_sql)
            self.assertIn("p_lease_token uuid", function_sql)
            self.assertIn("q.leased_by = btrim(p_worker_id)", function_sql)
            self.assertIn("q.lease_token = p_lease_token", function_sql)
            self.assertIn("q.status = 'processing'", function_sql)
            self.assertIn(
                "insert into public.oasis_sync_outbox_events",
                function_sql,
            )

        self.assertIn("oasis_v990_safe_error_summary", fail_sql)
        self.assertIn("power(2.0", fail_sql)
        self.assertIn("'dead_letter'", fail_sql)
        self.assertIn("p_actor_user_id text", retry_sql)
        self.assertIn("manual_retry_count = q.manual_retry_count + 1", retry_sql)
        self.assertIn("q.status in ('retry', 'dead_letter')", retry_sql)
        self.assertIn("'manual_retry'", retry_sql)

    def test_outbox_events_are_append_only_and_indexed(self):
        table_start = SQL_LOWER.index(
            "create table if not exists public.oasis_sync_outbox_events"
        )
        table_end = SQL_LOWER.index(
            "create index if not exists idx_oasis_sync_outbox_events_job_created",
            table_start,
        )
        table_sql = SQL_LOWER[table_start:table_end]
        self.assertIn(
            "foreign key (job_id, owner_user_id)\n"
            "        references public.oasis_sync_outbox(id, owner_user_id)",
            table_sql,
        )
        self.assertIn(
            "event_type in ('claim', 'complete', 'fail', 'manual_retry')",
            table_sql,
        )
        self.assertRegex(
            SQL_LOWER,
            r"grant\s+select\s+on\s+table\s+"
            r"public[.]oasis_sync_outbox_events\s+to\s+service_role",
        )
        self.assertRegex(
            SQL_LOWER,
            r"grant\s+select\s+on\s+table\s+"
            r"public[.]oasis_sync_outbox\s+to\s+service_role",
        )
        self.assertNotRegex(
            SQL_LOWER,
            r"grant\s+[^;]*(?:insert|update|delete)[^;]*on\s+table\s+"
            r"public[.]oasis_sync_outbox(?:_events)?\s+to\s+service_role",
        )
        self.assertNotRegex(
            SQL_LOWER,
            r"grant\s+[^;]*update[^;]*public[.]oasis_sync_outbox_events",
        )

    def test_all_outbox_rpcs_are_fixed_path_and_service_role_only(self):
        for name in self.OUTBOX_RPCS:
            function_sql = _function_sql(name).lower()
            self.assertIn("security definer", function_sql)
            self.assertIn("set search_path = public, pg_temp", function_sql)
            self.assertRegex(
                SQL_LOWER,
                rf"revoke\s+all\s+on\s+function\s+public[.]{name}\([^;]+\)\s+"
                rf"from\s+public,\s*anon,\s*authenticated",
            )
            self.assertRegex(
                SQL_LOWER,
                rf"grant\s+execute\s+on\s+function\s+public[.]{name}\([^;]+\)\s+"
                rf"to\s+service_role",
            )

    def test_customer_archive_transitions_are_atomic_non_destructive_and_audited(self):
        for name, action, status in (
            ("oasis_archive_customer", "archive", "archived"),
            ("oasis_reactivate_customer", "reactivate", "active"),
        ):
            function_sql = _function_sql(name).lower()
            for parameter in (
                "p_customer_id uuid",
                "p_owner_user_id text",
                "p_actor_user_id text",
                "p_reason text",
                "p_idempotency_key text",
            ):
                self.assertIn(parameter, function_sql)
            self.assertIn("security definer", function_sql)
            self.assertIn("set search_path = public, pg_temp", function_sql)
            self.assertIn("for update", function_sql)
            self.assertIn("update public.oasis_customers", function_sql)
            self.assertIn(
                "insert into public.oasis_customer_archive_events",
                function_sql,
            )
            self.assertIn(f"'{action}'", function_sql)
            self.assertIn(f"'{status}'", function_sql)
            self.assertIn("v_existing_actor_user_id", function_sql)
            self.assertIn("v_existing_reason", function_sql)
            self.assertIn(
                "v_existing_actor_user_id = v_actor_user_id",
                function_sql,
            )
            self.assertIn("v_existing_reason = v_reason", function_sql)
            advisory_lock = function_sql.index("pg_catalog.pg_advisory_xact_lock")
            event_lookup = function_sql.index(
                "from public.oasis_customer_archive_events"
            )
            customer_lock = function_sql.index("for update")
            self.assertLess(advisory_lock, event_lookup)
            self.assertLess(event_lookup, customer_lock)
            self.assertIn("pg_catalog.hashtextextended", function_sql)
            self.assertIn("'oasis:customer-lifecycle:'", function_sql)
            self.assertIn("'state_changed', v_state_changed", function_sql)
            self.assertIn("and v_idempotency_key is null then", function_sql)
            self.assertNotRegex(function_sql, r"\bdelete\s+from\b")
            self.assertRegex(
                SQL_LOWER,
                rf"revoke\s+all\s+on\s+function\s+public[.]{name}"
                rf"\(uuid,\s*text,\s*text,\s*text,\s*text\)\s+"
                rf"from\s+public,\s*anon,\s*authenticated",
            )
            self.assertRegex(
                SQL_LOWER,
                rf"grant\s+execute\s+on\s+function\s+public[.]{name}"
                rf"\(uuid,\s*text,\s*text,\s*text,\s*text\)\s+"
                rf"to\s+service_role",
            )

    def test_sql_error_summary_redacts_extended_identifiers_and_paths(self):
        function_sql = _function_sql("oasis_v990_safe_error_summary").lower()
        for marker in (
            "customer[_-]?name",
            "50[2-8]",
            "[redacted_email]",
            "[redacted_business_no]",
            "[redacted_path]",
            "[^,;|}]+",
        ):
            self.assertIn(marker, function_sql)
        self.assertNotIn("[^,;[:space:]]+", function_sql)

    def test_private_storage_bucket_is_registered_without_storage_ddl(self):
        self.assertIn("insert into storage.buckets", SQL_LOWER)
        self.assertIn("'oasis-customer-assets'", SQL_LOWER)
        self.assertRegex(
            SQL_LOWER,
            r"values\s*\(\s*'oasis-customer-assets'\s*,\s*"
            r"'oasis-customer-assets'\s*,\s*false\s*\)",
        )
        self.assertIn("on conflict (id) do update", SQL_LOWER)
        self.assertIn("set public = false", SQL_LOWER)
        self.assertNotRegex(
            SQL_LOWER,
            r"create\s+(?:table|schema|function)\s+(?:if\s+not\s+exists\s+)?storage[.]",
        )

    def test_customer_asset_links_allow_one_blob_to_have_many_associations(self):
        table_start = SQL_LOWER.index(
            "create table if not exists public.oasis_customer_asset_links"
        )
        table_end = SQL_LOWER.index(
            "create index if not exists idx_oasis_customer_asset_links_asset_id",
            table_start,
        )
        table_sql = SQL_LOWER[table_start:table_end]
        self.assertIn(
            "unique (owner_user_id, asset_id, association_key)",
            table_sql,
        )
        self.assertIn(
            "foreign key (asset_id, owner_user_id)\n"
            "        references public.oasis_customer_assets(id, owner_user_id)",
            table_sql,
        )
        self.assertIn(
            "foreign key (customer_id, owner_user_id)\n"
            "        references public.oasis_customers(id, owner_user_id)",
            table_sql,
        )

    def test_foreign_keys_have_supporting_indexes(self):
        required_composite_indexes = (
            (
                "idx_oasis_customers_merged_into_owner",
                "(merged_into_customer_id, owner_user_id)",
            ),
            (
                "idx_oasis_sync_outbox_events_job_owner",
                "(job_id, owner_user_id)",
            ),
            (
                "idx_oasis_customer_assets_customer_owner",
                "(customer_id, owner_user_id)",
            ),
            (
                "idx_oasis_customer_assets_duplicate_owner",
                "(duplicate_of_asset_id, owner_user_id)",
            ),
            (
                "idx_oasis_customer_asset_links_asset_owner",
                "(asset_id, owner_user_id)",
            ),
            (
                "idx_oasis_customer_asset_links_customer_owner",
                "(customer_id, owner_user_id)",
            ),
            (
                "idx_oasis_copilot_company_memory_customer_owner",
                "(customer_id, owner_user_id)",
            ),
            (
                "idx_oasis_copilot_success_cases_customer_owner",
                "(customer_id, owner_user_id)",
            ),
            (
                "idx_oasis_copilot_checklists_customer_owner",
                "(customer_id, owner_user_id)",
            ),
            (
                "idx_oasis_customer_archive_events_customer_owner",
                "(customer_id, owner_user_id)",
            ),
        )
        for index, columns in required_composite_indexes:
            declaration = (
                f"create index if not exists {index}\n"
                f"    on public."
            )
            self.assertIn(declaration, SQL_LOWER)
            index_start = SQL_LOWER.index(declaration)
            self.assertIn(columns, SQL_LOWER[index_start : index_start + 240])

        self.assertIn(
            "create index if not exists idx_oasis_restore_drills_backup_run_id",
            SQL_LOWER,
        )

    def test_self_referential_links_cannot_point_to_the_same_row(self):
        self.assertIn(
            "oasis_customers_merged_into_customer_id_not_self_check",
            SQL_LOWER,
        )
        self.assertIn("merged_into_customer_id <> id", SQL_LOWER)
        self.assertIn(
            "oasis_customer_assets_duplicate_of_asset_id_not_self_check",
            SQL_LOWER,
        )
        self.assertIn("duplicate_of_asset_id <> id", SQL_LOWER)

    def test_owner_scoped_links_cannot_cross_tenants(self):
        self.assertIn(
            "constraint oasis_customers_id_owner_user_id_unique",
            SQL_LOWER,
        )
        self.assertIn("unique (id, owner_user_id)", SQL_LOWER)
        for link in (
            "foreign key (merged_into_customer_id, owner_user_id)\n"
            "            references public.oasis_customers(id, owner_user_id)",
            "foreign key (job_id, owner_user_id)\n"
            "        references public.oasis_sync_outbox(id, owner_user_id)",
            "foreign key (duplicate_of_asset_id, owner_user_id)\n"
            "        references public.oasis_customer_assets(id, owner_user_id)",
        ):
            self.assertIn(link, SQL_LOWER)
        self.assertGreaterEqual(
            SQL_LOWER.count(
                "foreign key (customer_id, owner_user_id)\n"
                "        references public.oasis_customers(id, owner_user_id)"
            ),
            6,
        )

    def test_every_new_table_has_timestamps_and_idempotent_updated_trigger(self):
        for table in self.NEW_TABLES:
            table_start = SQL_LOWER.index(
                f"create table if not exists public.{table}"
            )
            next_create = SQL_LOWER.find(
                "create table if not exists public.",
                table_start + 1,
            )
            table_sql = SQL_LOWER[
                table_start : next_create if next_create >= 0 else len(SQL_LOWER)
            ]
            self.assertIn("created_at timestamptz not null default now()", table_sql)
            self.assertIn("updated_at timestamptz not null default now()", table_sql)

        trigger_sql = _function_sql("oasis_v990_touch_updated_at").lower()
        self.assertIn("new.updated_at = now()", trigger_sql)
        self.assertIn("set search_path = public, pg_temp", trigger_sql)
        self.assertIn("if not exists (", SQL_LOWER)
        self.assertIn("create trigger %i before update on public.%i", SQL_LOWER)


if __name__ == "__main__":
    unittest.main()
