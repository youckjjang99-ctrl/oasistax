from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804020000_v911_customer_information_integration.sql"
)
SQL = MIGRATION_PATH.read_text(encoding="utf-8")
SQL_LOWER = SQL.lower()
SERVICE_GRANT_MIGRATION_PATH = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804133529_v911_normalizer_acl.sql"
)
SERVICE_GRANT_SQL_LOWER = SERVICE_GRANT_MIGRATION_PATH.read_text(
    encoding="utf-8"
).lower()


def _function_sql(name: str) -> str:
    marker = re.search(
        rf"create\s+or\s+replace\s+function\s+public[.]{re.escape(name)}\s*[(]",
        SQL,
        flags=re.IGNORECASE,
    )
    if marker is None:
        raise AssertionError(f"missing SQL function: {name}")
    end = SQL.find("\n$$;", marker.start())
    if end < 0:
        raise AssertionError(f"unterminated SQL function: {name}")
    return SQL[marker.start() : end + len("\n$$;")]


def _create_table_sql(name: str) -> str:
    marker = SQL_LOWER.find(f"create table if not exists public.{name}")
    if marker < 0:
        raise AssertionError(f"missing SQL table: {name}")
    end = SQL_LOWER.find("\n);", marker)
    if end < 0:
        raise AssertionError(f"unterminated SQL table: {name}")
    return SQL[marker : end + len("\n);")]


class CustomerInformationIntegrationMigrationTests(unittest.TestCase):
    NEW_TABLES = (
        "oasis_customer_company_links",
        "oasis_customer_identity_reviews",
    )
    DEPENDENT_TABLES = (
        "oasis_crm",
        "oasis_financials",
        "oasis_registry",
        "oasis_matching_preferences",
        "oasis_customer_history",
        "oasis_consultation_journals",
        "oasis_customer_trash",
        "oasis_stock_valuations",
    )

    def test_timestamped_migration_is_transactional_additive_and_non_destructive(self):
        self.assertTrue(MIGRATION_PATH.is_file())
        self.assertRegex(
            MIGRATION_PATH.name,
            r"^\d{14}_v911_customer_information_integration[.]sql$",
        )
        self.assertIn("begin;", SQL_LOWER)
        self.assertTrue(SQL_LOWER.rstrip().endswith("commit;"))
        self.assertNotRegex(SQL_LOWER, r"\bdrop\s+(?:table|schema|column)\b")
        self.assertNotRegex(SQL_LOWER, r"\btruncate\b")
        self.assertNotRegex(SQL_LOWER, r"\bdelete\s+from\b")
        self.assertNotRegex(SQL_LOWER, r"\balter\s+column\b")

        for marker in (
            "tilko",
            "claim_case",
            "claim_correction",
            "phone_enrichment_queue",
            "phone_provider_queue",
        ):
            self.assertNotIn(marker, SQL_LOWER)

    def test_prerequisite_catalog_guards_fail_before_mutation(self):
        first_create = SQL_LOWER.index("create or replace function")
        guard_sql = SQL_LOWER[:first_create]
        for code in (
            "oasis_v911_requires_public_oasis_customers",
            "oasis_v911_customer_profile_schema_incompatible",
            "oasis_v911_requires_v990_customer_identity_guard",
            "oasis_v911_requires_company_uid_validator",
            "oasis_v911_prospect_identity_schema_incompatible",
            "oasis_v911_sales_identity_schema_incompatible",
            "oasis_v911_dependent_identity_schema_incompatible",
            "oasis_v911_existing_customer_link_schema_incompatible",
        ):
            self.assertIn(code, guard_sql)
        self.assertIn("to_regclass('public.oasis_customers')", guard_sql)
        self.assertIn(
            "to_regprocedure('public.oasis_is_valid_company_uid(text)')",
            guard_sql,
        )
        self.assertIn("pg_catalog.pg_attribute", guard_sql)
        self.assertIn("pg_catalog.pg_constraint", guard_sql)

    def test_business_number_matching_is_exact_owner_scoped_and_never_name_based(self):
        normalize_sql = _function_sql(
            "oasis_v911_normalize_business_no"
        ).lower()
        candidate_sql = _function_sql(
            "oasis_v911_company_uid_candidates"
        ).lower()
        ensure_sql = _function_sql(
            "oasis_v911_ensure_customer_company_link"
        ).lower()

        self.assertIn("regexp_replace", normalize_sql)
        self.assertIn("'[^0-9]'", normalize_sql)
        self.assertIn("'^[0-9]{10}$'", normalize_sql)
        self.assertIn("p.owner_user_id = $1", candidate_sql)
        self.assertIn(
            "oasis_v911_normalize_business_no(p.business_no) = $2",
            candidate_sql,
        )
        self.assertIn("a.assigned_user_id = $1", candidate_sql)
        assignment_branch = candidate_sql[
            candidate_sql.index("from public.oasis_company_sales_assignments a") :
        ]
        self.assertIn("p.owner_user_id = $1", assignment_branch)
        self.assertIn("public.oasis_is_valid_company_uid", candidate_sql)
        self.assertNotIn("company_name", candidate_sql)
        self.assertNotIn("company_name", ensure_sql)
        self.assertNotRegex(
            candidate_sql,
            r"(?:similarity|soundex|levenshtein|\blike\b|\bilike\b)",
        )

    def test_assignment_candidates_cannot_borrow_another_tenants_prospect(self):
        candidate_sql = _function_sql(
            "oasis_v911_company_uid_candidates"
        ).lower()
        assignment_branch = candidate_sql[
            candidate_sql.index("from public.oasis_company_sales_assignments a") :
            candidate_sql.index("$assigned_candidate$", candidate_sql.index("from public.oasis_company_sales_assignments a"))
        ]
        self.assertIn("a.assigned_user_id = $1", assignment_branch)
        self.assertIn("p.owner_user_id = $1", assignment_branch)
        self.assertIn("on p.company_uid = a.company_uid", assignment_branch)
        self.assertIn(
            "oasis_v911_normalize_business_no(p.business_no) = $2",
            assignment_branch,
        )

    def test_crosswalk_is_owner_scoped_immutable_and_preserves_company_uids(self):
        table_sql = _create_table_sql("oasis_customer_company_links").lower()
        self.assertIn("customer_id uuid not null", table_sql)
        self.assertIn("company_uid text not null", table_sql)
        self.assertIn("unique (owner_user_id, customer_id)", table_sql)
        self.assertIn(
            "foreign key (customer_id, owner_user_id)", table_sql
        )
        self.assertIn(
            "references public.oasis_customers(id, owner_user_id)", table_sql
        )
        self.assertIn("match_method = 'exact_normalized_business_no'", table_sql)

        self.assertNotRegex(
            SQL_LOWER,
            r"update\s+public[.]oasis_prospect_companies\b",
        )
        self.assertNotRegex(
            SQL_LOWER,
            r"update\s+public[.]oasis_company_sales_assignments\b",
        )
        self.assertNotRegex(
            SQL_LOWER,
            r"update\s+public[.]oasis_customer_company_links\b",
        )
        upsert_sql = _function_sql("oasis_upsert_customer_profile").lower()
        customer_update = upsert_sql[
            upsert_sql.index("update public.oasis_customers c") :
            upsert_sql.index("where c.id = v_existing.id")
        ]
        self.assertNotRegex(customer_update, r"(?:^|,)\s*id\s*=")
        self.assertIn(
            "on conflict (owner_user_id, customer_id) do nothing",
            SQL_LOWER,
        )

    def test_ambiguous_matches_stay_unlinked_and_review_rows_are_pii_free(self):
        review_table = _create_table_sql(
            "oasis_customer_identity_reviews"
        ).lower()
        ensure_sql = _function_sql(
            "oasis_v911_ensure_customer_company_link"
        ).lower()

        for reason in (
            "duplicate_customer_business_number",
            "multiple_company_uid_candidates",
            "dependent_record_ambiguous",
            "business_number_changed_link_unchanged",
        ):
            self.assertIn(reason, review_table)

        for pii_field in (
            "business_no",
            "company_name",
            "representative_name",
            "manager_name",
            "address",
            "phone",
            "email",
            "company_uid text",
        ):
            self.assertNotIn(pii_field, review_table)

        duplicate_pos = ensure_sql.index("if v_customer_count <> 1")
        duplicate_return = ensure_sql.index(
            "'ambiguous_review'::text", duplicate_pos
        )
        duplicate_link = ensure_sql.find(
            "insert into public.oasis_customer_company_links", duplicate_pos
        )
        self.assertTrue(duplicate_link < 0 or duplicate_return < duplicate_link)

        multi_pos = ensure_sql.index("cardinality(v_company_uids) > 1")
        multi_return = ensure_sql.index("'ambiguous_review'::text", multi_pos)
        multi_link = ensure_sql.index(
            "insert into public.oasis_customer_company_links", multi_pos
        )
        self.assertLess(multi_return, multi_link)
        self.assertIn("on conflict (", ensure_sql)
        self.assertIn(") do nothing", ensure_sql)

    def test_dependent_customer_links_are_nullable_owner_safe_and_fill_nulls_only(self):
        loop_sql = SQL_LOWER[
            SQL_LOWER.index("do $v911_dependent_links$") :
            SQL_LOWER.index("$v911_dependent_links$;", SQL_LOWER.index("do $v911_dependent_links$") + 1)
        ]
        for table in self.DEPENDENT_TABLES:
            self.assertIn(f"'{table}'", loop_sql)

        self.assertIn(
            "add column if not exists customer_id uuid", loop_sql
        )
        self.assertNotIn("customer_id uuid not null", loop_sql)
        self.assertIn(
            "foreign key (customer_id, owner_user_id)", loop_sql
        )
        self.assertIn(
            "references public.oasis_customers(id, owner_user_id) not valid",
            loop_sql,
        )
        self.assertIn("validate constraint", loop_sql)
        self.assertIn("(customer_id, owner_user_id)", loop_sql)
        self.assertGreaterEqual(loop_sql.count("d.customer_id is null"), 3)
        self.assertIn("m.candidate_count = 1", loop_sql)
        self.assertIn("oasis_v911_existing_customer_link_owner_mismatch", loop_sql)

    def test_future_dependent_writes_fill_only_null_links_on_one_exact_match(self):
        trigger_sql = _function_sql(
            "oasis_v911_fill_dependent_customer_id"
        ).lower()
        loop_sql = SQL_LOWER[
            SQL_LOWER.index("do $v911_dependent_links$") :
            SQL_LOWER.index(
                "$v911_dependent_links$;",
                SQL_LOWER.index("do $v911_dependent_links$") + 1,
            )
        ]

        self.assertIn("returns trigger", trigger_sql)
        self.assertIn("security definer", trigger_sql)
        self.assertIn("set search_path = public, pg_temp", trigger_sql)
        nonnull_guard = trigger_sql.index("if new.customer_id is not null")
        early_return = trigger_sql.index("return new", nonnull_guard)
        assignment = trigger_sql.index("new.customer_id := v_candidate_ids[1]")
        self.assertLess(nonnull_guard, early_return)
        self.assertLess(early_return, assignment)
        self.assertIn("cardinality(v_candidate_ids) = 1", trigger_sql)
        self.assertIn("c.owner_user_id = new.owner_user_id", trigger_sql)
        self.assertIn(
            "oasis_v911_normalize_business_no(c.business_no) = v_business_no",
            trigger_sql,
        )
        self.assertNotIn("company_name", trigger_sql)
        self.assertIn("cardinality(v_candidate_ids) > 1", trigger_sql)
        self.assertIn("'dependent_record_ambiguous'", trigger_sql)
        self.assertIn("tg_table_name", trigger_sql)

        self.assertIn("pg_catalog.pg_trigger", loop_sql)
        self.assertIn("not t.tgisinternal", loop_sql)
        self.assertIn(
            "before insert or update of owner_user_id, business_no", loop_sql
        )
        self.assertIn(
            "execute function public.oasis_v911_fill_dependent_customer_id()",
            loop_sql,
        )
        self.assertIn("oasis_v911_dependent_trigger_incompatible", loop_sql)

    def test_new_tables_are_force_rls_and_service_role_has_read_only_table_access(self):
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
            self.assertIn(
                f"revoke all on table public.{table} from service_role",
                SQL_LOWER,
            )
            self.assertIn(
                f"grant select on table public.{table} to service_role",
                SQL_LOWER,
            )

        self.assertNotRegex(
            SQL_LOWER,
            r"grant\s+(?:insert|update|delete|all)[^;]*on\s+table\s+"
            r"public[.]oasis_customer_(?:company_links|identity_reviews)",
        )
        self.assertNotRegex(SQL_LOWER, r"create\s+policy\b")

    def test_upsert_rpc_has_exact_contract_and_preserves_uuid_on_correction(self):
        upsert_sql = _function_sql("oasis_upsert_customer_profile").lower()
        expected_parameters = (
            "p_owner_user_id text",
            "p_business_no text",
            "p_company_name text default null",
            "p_representative_name text default null",
            "p_industry_name text default null",
            "p_address text default null",
            "p_manager_name text default null",
            "p_source text default 'app'",
            "p_customer_data jsonb default '{}'::jsonb",
            "p_customer_id uuid default null",
            "p_previous_business_no text default null",
        )
        for parameter in expected_parameters:
            self.assertIn(parameter, upsert_sql)
        for output in (
            "customer_id uuid",
            "company_uid text",
            "created boolean",
            "link_status text",
        ):
            self.assertIn(output, upsert_sql)

        self.assertIn("security definer", upsert_sql)
        self.assertIn("set search_path = public, pg_temp", upsert_sql)
        self.assertIn("pg_advisory_xact_lock", upsert_sql)
        self.assertGreaterEqual(upsert_sql.count("for update"), 2)
        self.assertIn("where c.id = p_customer_id", upsert_sql)
        self.assertIn("where c.id = v_existing.id", upsert_sql)
        self.assertIn("returning c.id into v_target_id", upsert_sql)
        self.assertNotRegex(upsert_sql, r"\bset\s+id\s*=")

        for conflict_code in (
            "customer_not_found",
            "previous_business_not_found",
            "previous_business_ambiguous",
            "customer_reference_conflict",
            "business_number_conflict",
        ):
            self.assertIn(f"'{conflict_code}'", upsert_sql)

    def test_business_number_correction_never_reassigns_existing_company_link(self):
        upsert_sql = _function_sql("oasis_upsert_customer_profile").lower()
        linked_lookup = upsert_sql.index(
            "from public.oasis_customer_company_links l"
        )
        changed_guard = upsert_sql.index(
            "v_old_business_no is distinct from v_business_no",
            linked_lookup,
        )
        review_insert = upsert_sql.index(
            "'business_number_changed_link_unchanged'", changed_guard
        )
        review_return = upsert_sql.index(
            "'linked_review_required'::text", review_insert
        )
        relink_call = upsert_sql.index(
            "from public.oasis_v911_ensure_customer_company_link", review_return
        )
        self.assertLess(changed_guard, review_insert)
        self.assertLess(review_insert, review_return)
        self.assertLess(review_return, relink_call)
        self.assertNotIn("update public.oasis_customer_company_links", upsert_sql)
        self.assertNotRegex(upsert_sql, r"set\s+company_uid\s*=")

    def test_scalar_and_json_updates_do_not_blank_or_remove_existing_values(self):
        upsert_sql = _function_sql("oasis_upsert_customer_profile").lower()
        merge_sql = _function_sql(
            "oasis_v911_lossless_jsonb_merge"
        ).lower()

        for field in (
            "company_name",
            "representative_name",
            "industry_name",
            "address",
            "manager_name",
            "source",
        ):
            self.assertRegex(
                upsert_sql,
                rf"{field}\s*=\s*coalesce\s*[(]\s*nullif\s*[(]"
                rf"pg_catalog[.]btrim\(p_{field}\),\s*''\),\s*c[.]{field}",
            )

        self.assertIn("p_incoming is null", merge_sql)
        self.assertIn("return p_existing", merge_sql)
        self.assertIn("jsonb_typeof(p_existing) <> 'object'", merge_sql)
        self.assertIn("v_value = 'null'::jsonb", merge_sql)
        self.assertIn("v_kind = 'string'", merge_sql)
        self.assertIn("v_value = '{}'::jsonb", merge_sql)
        self.assertIn("v_value = '[]'::jsonb", merge_sql)
        self.assertIn("continue;", merge_sql)
        self.assertIn("oasis_v911_lossless_jsonb_merge(", merge_sql)
        self.assertNotRegex(merge_sql, r"\s-\s*['\"]")

    def test_list_rpc_contract_is_owner_scoped_and_exposes_identity_status(self):
        list_sql = _function_sql("oasis_list_unified_customers").lower()
        for output in (
            "id uuid",
            "owner_user_id text",
            "business_no text",
            "company_name text",
            "representative_name text",
            "industry_name text",
            "address text",
            "manager_name text",
            "source text",
            "customer_data jsonb",
            "lifecycle_status text",
            "created_at timestamptz",
            "updated_at timestamptz",
            "company_uid text",
            "identity_status text",
        ):
            self.assertIn(output, list_sql)
        self.assertIn("security definer", list_sql)
        self.assertIn("set search_path = public, pg_temp", list_sql)
        self.assertIn(
            "where c.owner_user_id = nullif(pg_catalog.btrim(p_owner_user_id), '')",
            list_sql,
        )
        for status in (
            "linked_review_required",
            "linked",
            "ambiguous_review",
            "unlinked",
        ):
            self.assertIn(f"'{status}'", list_sql)

    def test_only_service_role_can_execute_public_rpcs(self):
        upsert_identity = (
            "text, text, text, text, text, text, text, text, jsonb, uuid, text"
        )
        self.assertRegex(
            SQL_LOWER,
            rf"revoke\s+all\s+on\s+function\s+"
            rf"public[.]oasis_upsert_customer_profile\(\s*{upsert_identity}\s*\)\s+"
            rf"from\s+public,\s*anon,\s*authenticated,\s*service_role",
        )
        self.assertRegex(
            SQL_LOWER,
            rf"grant\s+execute\s+on\s+function\s+"
            rf"public[.]oasis_upsert_customer_profile\(\s*{upsert_identity}\s*\)\s+"
            rf"to\s+service_role",
        )
        self.assertRegex(
            SQL_LOWER,
            r"revoke\s+all\s+on\s+function\s+"
            r"public[.]oasis_list_unified_customers\(text\)\s+"
            r"from\s+public,\s*anon,\s*authenticated,\s*service_role",
        )
        self.assertRegex(
            SQL_LOWER,
            r"grant\s+execute\s+on\s+function\s+"
            r"public[.]oasis_list_unified_customers\(text\)\s+to\s+service_role",
        )

        self.assertRegex(
            SERVICE_GRANT_SQL_LOWER,
            r"revoke\s+all\s+on\s+function\s+"
            r"public[.]oasis_v911_normalize_business_no\(text\)\s+"
            r"from\s+public,\s*anon,\s*authenticated,\s*service_role",
        )
        self.assertRegex(
            SERVICE_GRANT_SQL_LOWER,
            r"grant\s+execute\s+on\s+function\s+"
            r"public[.]oasis_v911_normalize_business_no\(text\)\s+"
            r"to\s+service_role",
        )
        self.assertNotRegex(
            SERVICE_GRANT_SQL_LOWER,
            r"grant\s+execute[^;]*"
            r"oasis_v911_normalize_business_no\(text\)[^;]*"
            r"to\s+(?:public|anon|authenticated)",
        )

        for helper in (
            "oasis_v911_lossless_jsonb_merge",
            "oasis_v911_company_uid_candidates",
            "oasis_v911_ensure_customer_company_link",
            "oasis_v911_fill_dependent_customer_id",
        ):
            helper_grants = re.findall(
                rf"grant\s+execute[^;]*public[.]{helper}\b[^;]*;",
                SQL_LOWER,
            )
            self.assertEqual(helper_grants, [])

    def test_replay_guards_cover_every_created_or_altered_object(self):
        for table in self.NEW_TABLES:
            self.assertIn(
                f"create table if not exists public.{table}", SQL_LOWER
            )
        self.assertNotRegex(SQL_LOWER, r"\bcreate\s+table\s+public[.]")
        self.assertNotRegex(SQL_LOWER, r"\bcreate\s+index\s+(?!if\s+not\s+exists)")
        self.assertIn("add column if not exists customer_id uuid", SQL_LOWER)
        self.assertIn("if not exists (", SQL_LOWER)
        self.assertGreaterEqual(SQL_LOWER.count("on conflict ("), 5)
        self.assertNotIn("on conflict (owner_user_id, customer_id) do update", SQL_LOWER)

        for delimiter in set(re.findall(r"\$[a-z0-9_]*\$", SQL_LOWER)):
            self.assertEqual(
                SQL_LOWER.count(delimiter) % 2,
                0,
                f"unbalanced dollar-quote delimiter {delimiter}",
            )


if __name__ == "__main__":
    unittest.main()
