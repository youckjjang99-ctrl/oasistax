from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260812040230_direct_sales_customer_registry.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8").lower()
FOLLOWUP_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260812051601_direct_customer_outbox_fk_index.sql"
)
FOLLOWUP_SQL = FOLLOWUP_MIGRATION.read_text(encoding="utf-8").lower()


def test_direct_registry_reuses_customer_and_crm_without_assignment_pool():
    assert "public.oasis_upsert_customer_profile(" in SQL
    assert "left join public.oasis_crm" in SQL
    assert "crm.crm_data ->> 'status'" in SQL
    assert "'계약완료'" in SQL
    assert "insert into public.oasis_company_sales_assignments" not in SQL
    assert "insert into public.oasis_sales_assignment_requests" not in SQL


def test_cross_user_business_number_conflicts_fail_closed_for_review():
    assert "unique (business_no)" in SQL
    assert "oasis_direct_sales_claim_conflicts" in SQL
    assert "v_existing.owner_user_id is distinct from v_actor" in SQL
    assert "'review_required'" in SQL
    assert "pg_advisory_xact_lock" in SQL


def test_every_rpc_checks_active_actor_and_owner_scope():
    assert SQL.count("public.oasis_sales_actor_is_active(v_actor)") == 7
    assert SQL.count("d.owner_user_id = v_actor") >= 5
    assert "o.requested_by_user_id = v_actor" in SQL


def test_tables_are_force_rls_and_service_role_rpc_only():
    for table in (
        "oasis_direct_sales_customers",
        "oasis_direct_sales_claim_conflicts",
        "oasis_direct_customer_outreach_outbox",
    ):
        assert f"alter table public.{table} enable row level security" in SQL
        assert f"alter table public.{table} force row level security" in SQL
        assert f"revoke all on table public.{table}" in SQL
    assert "from public, anon, authenticated, service_role" in SQL
    assert SQL.count("to service_role") == 7


def test_outreach_ledger_is_metadata_only_and_checks_consent_and_dnc_twice():
    for forbidden_column in (
        "recipient_value text",
        "message_body text",
        "subject text",
        "provider_response",
    ):
        assert forbidden_column not in SQL
    assert SQL.count("not v_direct.marketing_consent_confirmed") == 2
    assert SQL.count("oasis_company_kakao_contact_controls") == 2
    assert SQL.count("v_direct.updated_at is distinct from") == 2
    assert "do_not_contact" in SQL
    assert "duplicate_outreach" in SQL


def test_only_targeted_indexes_are_added():
    assert "idx_oasis_direct_sales_owner_active_updated" in SQL
    assert "idx_oasis_direct_outreach_customer_history" in SQL
    assert "idx_oasis_direct_outreach_duplicate_guard" in SQL
    assert "idx_oasis_direct_outreach_open_dispatch" in SQL
    assert "idx_oasis_direct_outreach_customer_fk" in FOLLOWUP_SQL
    assert "(direct_customer_id)" in FOLLOWUP_SQL
