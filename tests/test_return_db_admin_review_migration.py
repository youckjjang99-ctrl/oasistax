from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = next(
    (ROOT / "supabase" / "migrations").glob(
        "*_return_db_admin_review.sql"
    )
)
SQL = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_contact_results_return_is_quarantined_for_admin_review() -> None:
    assert "update public.oasis_company_sales_assignments" in SQL
    assert "where status = 'unassigned'" in SQL
    assert "and assigned_user_id is null" in SQL
    assert "and released_reason = 'contact_results_return'" in SQL
    assert "and permanently_excluded is false" in SQL
    assert (
        "create or replace function "
        "public.oasis_release_company_sales_assignment" in SQL
    )
    assert "not v_is_admin" in SQL
    assert "v_release_reason = 'contact_results_return'" in SQL
    assert "then 'long_hold'" in SQL
    assert "assigned_user_id = null" in SQL
    assert "released_reason = v_release_reason" in SQL


def test_other_release_paths_keep_existing_unassigned_behavior() -> None:
    status_case = SQL[SQL.index("status = case") : SQL.index(
        "assigned_at = null"
    )]

    assert "then 'long_hold'" in status_case
    assert "else 'unassigned'" in status_case


def test_return_quarantine_remains_audited() -> None:
    assert "public.oasis_write_company_assignment_audit" in SQL
    assert "'assignment_released'" in SQL
    assert "'status', v_saved.status" in SQL
    assert "'reason', v_saved.released_reason" in SQL


def test_release_rpc_remains_service_role_only() -> None:
    assert (
        "from public, anon, authenticated" in SQL
    )
    assert "to service_role" in SQL
