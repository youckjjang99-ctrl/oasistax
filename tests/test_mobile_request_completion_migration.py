from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / (
    "20260811174123_complete_mobile_request_on_first_allocation.sql"
)
SQL = " ".join(MIGRATION.read_text(encoding="utf-8").lower().split())


def test_existing_partial_mobile_requests_are_closed_after_any_allocation() -> None:
    assert SQL.startswith("begin;")
    assert SQL.endswith("commit;")
    assert "where status in ('pending', 'partially_approved')" in SQL
    assert "and allocated_count > 0" in SQL
    assert "status = 'approved'" in SQL


def test_first_successful_allocation_completes_the_request() -> None:
    function_sql = SQL[SQL.index("create or replace function") :]

    assert "if v_request.status <> 'pending'" in function_sql
    assert "allocated_count = r.allocated_count + v_add_count" in function_sql
    assert "status = 'approved'" in function_sql
    assert "else 'partially_approved'" not in function_sql
    assert "decided_at = now()" in function_sql


def test_mobile_request_update_rpc_remains_service_role_only() -> None:
    signature = (
        "function public.oasis_admin_update_mobile_db_request( "
        "text, uuid, text, integer, text, text )"
    )
    assert f"revoke all on {signature} from public, anon, authenticated" in SQL
    assert f"grant execute on {signature} to service_role" in SQL
