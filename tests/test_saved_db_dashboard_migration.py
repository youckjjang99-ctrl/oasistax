from pathlib import Path


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260811133456_saved_db_dashboard.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8").lower()


def test_dashboard_uses_separate_count_and_server_filtered_list_rpcs():
    assert "function public.oasis_get_user_db_dashboard" in SQL
    assert "function public.oasis_list_user_db_assignments" in SQL
    assert "p_filter text default 'all'" in SQL
    for value in (
        "'all'",
        "'landline'",
        "'mobile'",
        "'new'",
        "'in_progress'",
        "'completed'",
    ):
        assert value in SQL


def test_every_dashboard_query_is_fail_closed_to_active_owner_rows():
    assert SQL.count("a.assigned_user_id = v_user_id") == 2
    assert SQL.count("a.released_at is null") == 2
    assert SQL.count("coalesce(a.permanently_excluded, false) is false") == 2
    assert SQL.count("a.assignment_expires_at > now()") == 2
    assert "'unassigned', 'long_hold', 'permanently_excluded'" in SQL


def test_phone_counts_are_distinct_company_flags_and_allow_overlap():
    assert SQL.count("bool_or(") >= 4
    assert SQL.count("normalized.phone_digits ~ '^010[0-9]{8}$'") == 2
    assert SQL.count("normalized.phone_digits ~ '^0[0-9]{8,10}$'") == 2
    assert "count(*) filter (where has_landline)" in SQL
    assert "count(*) filter (where has_mobile)" in SQL


def test_current_assignment_latest_contact_drives_stage_classification():
    assert SQL.count("where l.assignment_id = a.id") == 2
    assert SQL.count("l.contacted_at desc") == 2
    assert SQL.count("current_assignment_contact_count") >= 4
    assert SQL.count("latest_contact_result = 'connected'") == 2
    assert SQL.count("latest_contact_result is null") >= 4
    assert SQL.count("then 'completed'") >= 6
    assert SQL.count("then 'new'") == 2
    assert SQL.count("else 'in_progress'") == 2


def test_dashboard_rpcs_are_service_role_only():
    assert "from public, anon, authenticated" in SQL
    assert SQL.count("to service_role") == 2


def test_only_targeted_indexes_are_added():
    assert "oasis_company_sales_assignments_active_owner_idx" not in SQL
    assert "oasis_company_sales_contact_logs_assignment_latest_idx" in SQL
    assert SQL.count("create index if not exists") == 1
