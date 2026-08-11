from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / (
    "20260811165310_fix_return_review_batch.sql"
)
SQL = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_admin_owned_returns_are_backfilled_and_future_returns_are_held() -> None:
    assert SQL.startswith("begin;")
    assert SQL.endswith("commit;")
    assert "where status = 'unassigned'" in SQL
    assert "and released_reason = 'contact_results_return'" in SQL
    assert "create or replace function public.oasis_release_company_sales_assignment" in SQL
    status_case = SQL[SQL.index("status = case") : SQL.index("assigned_at = null")]
    assert "when v_release_reason = 'contact_results_return' then 'long_hold'" in status_case
    assert "not v_is_admin and v_release_reason = 'contact_results_return'" not in SQL


def test_batch_review_is_atomic_bounded_and_admin_only() -> None:
    assert "public.oasis_admin_review_returned_companies_batch" in SQL
    assert "public.oasis_sales_actor_is_admin(p_current_user_id)" in SQL
    assert "cardinality(v_uids) > 100" in SQL
    assert "a.status = 'long_hold'" in SQL
    assert "a.released_reason = 'contact_results_return'" in SQL
    assert "foreach v_uid in array v_uids loop" in SQL
    assert "oasis_admin_reactivate_company_assignment" in SQL
    assert "oasis_admin_permanent_exclude_company" in SQL
    assert "raise exception" in SQL


def test_batch_rpc_is_service_role_only() -> None:
    signature = (
        "function public.oasis_admin_review_returned_companies_batch( "
        "text, text[], text, text, text )"
    )
    assert f"revoke all on {signature} from public, anon, authenticated" in SQL
    assert f"grant execute on {signature} to service_role" in SQL
