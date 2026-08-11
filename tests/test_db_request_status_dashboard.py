import inspect
from pathlib import Path

import prospect_db_center as prospect
from company_sales_assignment import get_assignable_db_inventory_dashboard


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260811194058_db_request_status_dashboard.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8").lower()


class _FakeDatabase:
    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def rpc(self, name, parameters):
        self.calls.append((name, parameters))
        response = self.responses.get(name)
        return response(parameters) if callable(response) else response


def test_inventory_dashboard_service_returns_count_only_allowlisted_metrics():
    database = _FakeDatabase(
        {
            "oasis_get_assignable_db_inventory_dashboard": [
                {
                    "total_db_count": 241_000,
                    "landline_db_count": 229_000,
                    "mobile_db_count": 12_000,
                    "total_individual_count": 165_000,
                    "total_corporate_count": 76_000,
                    "landline_individual_count": 154_000,
                    "landline_corporate_count": 75_000,
                    "mobile_individual_count": 11_000,
                    "mobile_corporate_count": 1_000,
                    "company_name": "노출되면 안 됨",
                }
            ]
        }
    )

    result = get_assignable_db_inventory_dashboard("Sales-A", db=database)

    assert result["ok"] is True
    assert result["metrics"]["total_db_count"] == 241_000
    assert result["metrics"]["mobile_corporate_count"] == 1_000
    assert "company_name" not in result["metrics"]
    assert database.calls == [
        (
            "oasis_get_assignable_db_inventory_dashboard",
            {"p_current_user_id": "sales-a"},
        )
    ]


def test_inventory_dashboard_is_global_but_requires_an_active_actor():
    assert "oasis_sales_actor_is_active(v_user_id)" in SQL
    assert "oasis_employment_contacts" in SQL
    assert "a.assigned_user_id = v_user_id" not in SQL
    assert "assignment.assigned_user_id is null" in SQL
    assert "coalesce(assignment.status, '') = 'unassigned'" in SQL
    assert "coalesce(assignment.permanently_excluded, false) is false" in SQL
    assert "coalesce(assignment.migration_conflict, false) is false" in SQL


def test_inventory_dashboard_uses_private_cached_candidates_and_live_blocking():
    assert "create materialized view oasis_private.oasis_assignable_db_inventory" in SQL
    assert "oasis_make_company_uid" in SQL
    assert "oasis_is_stock_company" in SQL
    assert "bool_or(has_landline)" in SQL
    assert "bool_or(has_mobile)" in SQL
    assert "left join public.oasis_company_sales_assignments" in SQL
    assert "refresh materialized view concurrently" in SQL
    assert "17 * * * *" in SQL


def test_inventory_dashboard_counts_phone_channels_and_business_classes():
    assert "count(*) filter (where has_landline)" in SQL
    assert "count(*) filter (where has_mobile)" in SQL
    assert "has_landline and not is_corporate" in SQL
    assert "has_landline and is_corporate" in SQL
    assert "has_mobile and not is_corporate" in SQL
    assert "has_mobile and is_corporate" in SQL


def test_inventory_dashboard_rpc_is_service_role_only_and_count_only():
    assert "from public, anon, authenticated" in SQL
    assert "to service_role" in SQL
    returned_columns = SQL.split("returns table", 1)[1].split(")", 1)[0]
    assert "company_name" not in returned_columns


def test_request_screen_places_responsive_global_dashboard_above_request_form():
    home_source = inspect.getsource(prospect._render_db_request_home)
    renderer_source = inspect.getsource(prospect._render_db_request_status_dashboard)

    assert home_source.index("_render_db_request_status_dashboard") < home_source.index(
        'st.markdown("### DB 신청")'
    )
    for label in (
        "총 배정가능 DB",
        "일반전화 DB",
        "핸드폰번호 DB",
        "개인사업자 후보",
        "법인사업자",
    ):
        assert label in renderer_source
    assert "모든 조직에 공통으로 표시" in renderer_source
    assert "현재 나에게 활성 배정된 DB" not in renderer_source
    assert "@media (max-width: 760px)" in renderer_source
    assert "grid-template-columns: 1fr" in renderer_source
