from company_sales_assignment import (
    get_user_db_dashboard,
    list_user_db_assignments,
)


class _FakeDatabase:
    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def rpc(self, name, parameters):
        self.calls.append((name, parameters))
        response = self.responses.get(name)
        return response(parameters) if callable(response) else response


def test_user_db_dashboard_returns_count_only_allowlisted_metrics():
    database = _FakeDatabase(
        {
            "oasis_get_user_db_dashboard": [
                {
                    "total_db_count": 10,
                    "landline_db_count": 7,
                    "mobile_db_count": 4,
                    "new_db_count": 3,
                    "in_progress_db_count": 5,
                    "completed_db_count": 2,
                    "company_name": "노출되면 안 됨",
                }
            ]
        }
    )

    result = get_user_db_dashboard("Sales-A", db=database)

    assert result["ok"] is True
    assert result["metrics"]["total_db_count"] == 10
    assert result["metrics"]["mobile_db_count"] == 4
    assert "company_name" not in result["metrics"]
    assert database.calls[0] == (
        "oasis_get_user_db_dashboard",
        {"p_current_user_id": "sales-a"},
    )


def test_user_db_list_applies_server_filter_and_pagination():
    database = _FakeDatabase(
        {
            "oasis_list_user_db_assignments": [
                {
                    "assignment_id": "assignment-one",
                    "company_id": "company-one",
                    "company_uid": "source:" + ("a" * 64),
                    "company_name": "테스트 업체",
                    "own_memo": "후속 연락",
                    "total_count": 121,
                    "assigned_user_id": "다른 사용자 노출 금지",
                }
            ]
        }
    )

    result = list_user_db_assignments(
        "sales-a",
        dashboard_filter="in_progress",
        limit=100,
        offset=100,
        db=database,
    )

    assert result["ok"] is True
    assert result["total_count"] == 121
    assert result["assignments"][0]["memo"] == "후속 연락"
    assert "assigned_user_id" not in result["assignments"][0]
    assert database.calls[0][1] == {
        "p_current_user_id": "sales-a",
        "p_filter": "in_progress",
        "p_limit": 100,
        "p_offset": 100,
    }


def test_user_db_list_rejects_unknown_client_filter():
    database = _FakeDatabase({"oasis_list_user_db_assignments": []})

    result = list_user_db_assignments(
        "sales-a",
        dashboard_filter="another-user",
        db=database,
    )

    assert result["ok"] is True
    assert database.calls[0][1]["p_filter"] == "all"
