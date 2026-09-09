"""Synthetic contract and UI tests; never connect to a customer database."""

import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

import company_sales_assignment as service
import prospect_db_center as prospect


class FakeDatabase:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def rpc(self, name, parameters):
        self.calls.append((name, parameters))
        return self.response


def summary_row(**changes):
    return {
        "assignment_id": "assignment-one",
        "company_id": "company-one",
        "company_uid": "source:" + "a" * 64,
        "company_name": "합성 테스트 업체",
        "contact_summary_loaded": True,
        "current_assignment_contact_count": 1,
        "latest_contact_result": "connected",
        "latest_contacted_at": "2026-08-01T01:00:00+00:00",
        "latest_next_contact_at": None,
        "status": "contacted",
        "total_count": 1,
        **changes,
    }


@pytest.mark.parametrize("bad", [None, {}, "error", [None], [{"status": "contacted"}]])
def test_list_does_not_turn_invalid_payload_into_an_empty_success(bad):
    result = service.list_user_db_assignments("owner-a", db=FakeDatabase(bad))
    assert result["ok"] is False
    assert result["code"] == "MALFORMED_RESPONSE"
    assert result["assignments"] == []


@pytest.mark.parametrize("field", sorted(service._CONTACT_SUMMARY_FIELDS))
def test_every_summary_field_must_exist_even_when_no_contact_was_made(field):
    row = summary_row()
    del row[field]
    result = service.list_user_db_assignments("owner-a", db=FakeDatabase([row]))
    assert result["ok"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"contact_summary_loaded": False},
        {"contact_summary_loaded": 1},
        {"current_assignment_contact_count": True},
        {"current_assignment_contact_count": -1},
        {"total_count": "1"},
        {"total_count": -1},
        {"assignment_id": None},
        {"company_uid": {"private": "never echo this"}},
        {"latest_contact_result": {"notes": "never echo this"}},
        {"latest_contact_result": ""},
        {"latest_contacted_at": "not a timestamp"},
        {"latest_contacted_at": "2026-08-01T10:00:00"},
        {"latest_next_contact_at": "2026-08-01"},
        {"latest_contacted_at": None},
        {"latest_contact_result": None},
    ],
)
def test_invalid_summary_fields_are_safe_errors(changes):
    result = service.list_user_db_assignments(
        "owner-a", db=FakeDatabase([summary_row(**changes)])
    )
    assert result["ok"] is False
    assert "never echo this" not in str(result)


def test_verified_no_contact_is_different_from_missing_summary():
    row = summary_row(
        latest_contact_result=None,
        latest_contacted_at=None,
        current_assignment_contact_count=0,
    )
    db = FakeDatabase([row])
    result = service.list_user_db_assignments("OWNER-A", db=db)
    assert result["ok"] is True
    assert result["assignments"][0]["latest_contact_result"] is None
    assert result["assignments"][0]["contact_summary_loaded"] is True
    assert db.calls[0][0] == "oasis_list_user_db_assignments_v2"
    assert db.calls[0][1]["p_current_user_id"] == "owner-a"


def test_empty_verified_page_is_allowed_and_v2_does_not_leak_actor_or_notes():
    assert service.list_user_db_assignments("owner-a", db=FakeDatabase([]))["ok"]
    row = summary_row(
        created_by_user_id="other-owner", notes="another person's note",
        assigned_user_name="another person", own_memo="my note",
    )
    result = service.list_user_db_assignments("owner-a", db=FakeDatabase([row]))
    saved = result["assignments"][0]
    assert saved["memo"] == "my note"
    assert not {"created_by_user_id", "notes", "assigned_user_name"} & saved.keys()


@pytest.mark.parametrize("field", sorted(service._USER_DB_DASHBOARD_FIELDS))
@pytest.mark.parametrize("bad", [None, True, -1, "1", "unavailable", 1.5])
def test_dashboard_requires_all_six_nonnegative_integer_counts(field, bad):
    metrics = {name: 0 for name in service._USER_DB_DASHBOARD_FIELDS}
    metrics[field] = bad
    result = service.get_user_db_dashboard("owner-a", db=FakeDatabase([metrics]))
    assert result["ok"] is False
    assert result["metrics"] == {}


def test_dashboard_missing_count_is_not_zero():
    result = service.get_user_db_dashboard("owner-a", db=FakeDatabase([{}]))
    assert result["ok"] is False
    zero = {name: 0 for name in service._USER_DB_DASHBOARD_FIELDS}
    result = service.get_user_db_dashboard("owner-a", db=FakeDatabase([zero]))
    assert result["ok"] is True
    assert result["metrics"] == zero


def test_current_page_summary_does_not_depend_on_global_contact_history():
    rows = [summary_row()]
    latest = prospect._assignment_contact_summaries(rows)
    assert prospect._contact_progress_label(rows[0], latest[rows[0]["company_uid"]]) == prospect.CONTACT_PROGRESS_LABELS["connected"]
    source = inspect.getsource(prospect._render_clean_saved_prospects)
    assert "sales_assignments.list_company_contacts" not in source
    contact_source = inspect.getsource(prospect._render_contact_results)
    assert "limit=1000" not in contact_source
    assert "selected_company_uid,\n        limit=200" in contact_source


def test_current_contact_time_never_borrows_previous_assignment_time():
    row = summary_row(
        id="company-one", latest_contact_result=None, latest_contacted_at=None,
        latest_next_contact_at=None, current_assignment_contact_count=0,
        status="assigned", last_contacted_at="2026-07-01T09:00:00+09:00",
        next_contact_at="2026-07-02T09:00:00+09:00",
    )
    with patch.object(prospect, "_procurement_activity_map", return_value={}):
        frame = prospect._saved_candidate_frame([row], [])
    assert frame.iloc[0]["최근연락일"] == "-"
    assert frame.iloc[0]["다음연락일"] == "-"
    assert frame.iloc[0]["업체별 진행상황"] == "신규 배정"


def test_common_state_remains_visible_when_own_summary_is_null():
    row = summary_row(latest_contact_result=None, latest_contacted_at=None, status="consulting")
    assert prospect._contact_progress_label(row, None) == "상담진행"
    row["contact_summary_loaded"] = False
    assert prospect._contact_progress_label(row, None) == "상태 확인 불가"


def test_contact_ties_are_deterministic_by_created_time_then_id():
    records = [
        {"company_uid": "one", "id": "b", "contact_result": "connected",
         "contacted_at": "2026-08-01T01:00:00Z", "created_at": "2026-08-02T01:00:00Z"},
        {"company_uid": "one", "id": "c", "contact_result": "contracted",
         "contacted_at": "2026-08-01T01:00:00Z", "created_at": "2026-08-02T01:00:00Z"},
        {"company_uid": "one", "id": "z", "contact_result": "no_answer",
         "contacted_at": "2026-08-01T01:00:00Z", "created_at": "2026-08-01T01:00:00Z"},
    ]
    assert prospect._latest_contact_by_company(records)["one"]["id"] == "c"
    assert prospect._contact_activity_rows(records)[0]["연락결과"] == "계약완료"


def test_standalone_selector_pages_beyond_first_thousand_assignments():
    first = [summary_row(assignment_id=f"a-{i}") for i in range(1000)]
    last = summary_row(assignment_id="a-last")
    with (
        patch.object(prospect, "_assignment_feature_status", return_value=(True, "")),
        patch.object(prospect, "_release_expired_assignments_if_due"),
        patch.object(service, "list_user_db_assignments", side_effect=[
            {"ok": True, "assignments": first, "total_count": 1001},
            {"ok": True, "assignments": [last], "total_count": 1001},
        ]) as fetch,
    ):
        result = prospect._load_user_assignment_rows("owner-a")
    assert result["ok"]
    assert len(result["rows"]) == 1001
    assert fetch.call_args_list[1].kwargs["offset"] == 1000


def test_selector_does_not_report_partial_page_failure_as_complete():
    with (
        patch.object(prospect, "_assignment_feature_status", return_value=(True, "")),
        patch.object(prospect, "_release_expired_assignments_if_due"),
        patch.object(service, "list_user_db_assignments", side_effect=[
            {"ok": True, "assignments": [summary_row()], "total_count": 2},
            {"ok": False, "assignments": [], "message": "retry"},
        ]),
    ):
        result = prospect._load_user_assignment_rows("owner-a")
    assert result["ok"] is False
    assert result["rows"] == []


@pytest.mark.parametrize("second", [
    {"ok": True, "assignments": [], "total_count": 0},
    {"ok": True, "assignments": [], "total_count": 2},
    {"ok": True, "assignments": [summary_row(assignment_id="second")], "total_count": 3},
    {"ok": True, "assignments": [summary_row()], "total_count": 2},
    {"ok": True, "assignments": [summary_row(assignment_id="second"), summary_row(assignment_id="third")], "total_count": 2},
])
def test_selector_rejects_missing_changed_duplicate_or_excess_pages(second):
    with (
        patch.object(prospect, "_assignment_feature_status", return_value=(True, "")),
        patch.object(prospect, "_release_expired_assignments_if_due"),
        patch.object(service, "list_user_db_assignments", side_effect=[
            {"ok": True, "assignments": [summary_row()], "total_count": 2},
            second,
        ]),
    ):
        result = prospect._load_user_assignment_rows("owner-a")
    assert result["ok"] is False
    assert result["rows"] == []


def test_selector_initial_empty_page_is_a_valid_empty_list():
    with (
        patch.object(prospect, "_assignment_feature_status", return_value=(True, "")),
        patch.object(prospect, "_release_expired_assignments_if_due"),
        patch.object(service, "list_user_db_assignments", return_value={
            "ok": True, "assignments": [], "total_count": 0,
        }),
    ):
        assert prospect._load_user_assignment_rows("owner-a") == {"ok": True, "message": "", "rows": []}


def test_migration_keeps_v1_contract_and_enforces_same_current_cycle_predicates():
    sql = (Path(__file__).parents[1] / "supabase/migrations/20260909080959_contact_status_accuracy.sql").read_text(encoding="utf-8").lower()
    assert "function public.oasis_list_user_db_assignments_v2(" in sql
    assert "function public.oasis_list_user_db_assignments(" not in sql
    assert "drop " not in sql
    assert "delete " not in sql
    for predicate in (
        "l.assigned_user_id = a.assigned_user_id",
        "v_is_admin or l.created_by_user_id = v_user_id",
        "l.created_at >= a.assigned_at",
        "or a.assigned_at is null",
        "coalesce(a.current_assignment_contact_count, 0) > 0",
        "a.assigned_user_id = v_user_id",
        "l.id desc",
    ):
        assert sql.count(predicate) == 2
    assert sql.count("security invoker") == 2
    assert sql.count("from public, anon, authenticated") == 2
    assert sql.count("to service_role") == 2
    assert "security definer" not in sql
