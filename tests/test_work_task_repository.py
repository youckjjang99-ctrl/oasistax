from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import work_task_repository as repository


TASK_ID = "10000000-0000-4000-8000-000000000001"
USER_ID = "owner" + "\x40" + "example.invalid"


class _Database:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, parameters: dict):
        self.calls.append((name, dict(parameters)))
        if self.error is not None:
            raise self.error
        return self.payload


def _task_row(**overrides):
    row = {
        "task_id": TASK_ID,
        "task_type": "guidance_followup",
        "title": "fixed safe title",
        "priority": "high",
        "status": "pending",
        "due_at": "2026-08-05T02:00:00Z",
        "completed_at": None,
        "task_version": 3,
        "updated_at": "2026-08-05T01:00:00Z",
        "total_count": 1,
    }
    row.update(overrides)
    return row


def test_rpc_names_are_the_exact_stage3_contract():
    assert repository.RPC_FEATURE_READY == "oasis_work_inbox_feature_ready"
    assert repository.RPC_LIST == "oasis_list_my_work_tasks"
    assert repository.RPC_SALES_FOLLOWUPS == "oasis_list_my_sales_followups"
    assert repository.RPC_SUMMARY == "oasis_get_my_work_task_summary"
    assert repository.RPC_TRANSITION == "oasis_transition_my_work_task"


def test_feature_ready_accepts_scalar_or_named_row():
    scalar = _Database(True)
    assert repository.work_inbox_feature_ready(db=scalar) == (True, "")
    assert scalar.calls == [(repository.RPC_FEATURE_READY, {})]

    named = _Database([{"ready": True, "raw_detail": "must not escape"}])
    assert repository.work_inbox_feature_ready(db=named) == (True, "")


def test_list_normalizes_inputs_clamps_page_and_allowlists_task_fields():
    database = _Database(
        [
            _task_row(
                customer_name="private customer",
                phone="010" + "-0000" + "-0000",
                idempotency_key="private-key",
                source_id="20000000-0000-4000-8000-000000000002",
                future_backend_field="unexpected",
            )
        ]
    )

    result = repository.list_my_work_tasks(
        f"  {USER_ID.upper()}  ",
        statuses=["PENDING", "in_progress"],
        limit=9999,
        offset=-5,
        db=database,
    )

    assert result["ok"] is True
    assert result["total_count"] == 1
    assert result["tasks"][0]["task_id"] == TASK_ID
    assert result["tasks"][0]["task_version"] == 3
    for forbidden in (
        "customer_name",
        "phone",
        "idempotency_key",
        "source_id",
        "future_backend_field",
    ):
        assert forbidden not in result["tasks"][0]
    assert database.calls == [
        (
            repository.RPC_LIST,
            {
                "p_current_user_id": USER_ID,
                "p_statuses": ["pending", "in_progress"],
                "p_limit": 500,
                "p_offset": 0,
            },
        )
    ]


@pytest.mark.parametrize(
    ("user_id", "statuses"),
    (
        ("", None),
        (USER_ID, ["pending", "made_up_status"]),
    ),
)
def test_invalid_list_inputs_fail_before_rpc(user_id, statuses):
    database = _Database([])

    result = repository.list_my_work_tasks(
        user_id,
        statuses=statuses,
        db=database,
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_INPUT"
    assert result["tasks"] == []
    assert database.calls == []


def test_list_drops_malformed_task_rows_instead_of_exposing_them():
    database = _Database(
        [
            _task_row(task_id="not-a-uuid"),
            _task_row(task_version=0),
            _task_row(status="unknown"),
            _task_row(),
        ]
    )

    result = repository.list_my_work_tasks(USER_ID, db=database)

    assert result["ok"] is True
    assert result["tasks"] == [_task_row()]


def test_summary_has_fixed_nonnegative_integer_fields_only():
    database = _Database(
        [
            {
                "open_count": "5",
                "overdue_count": -2,
                "today_count": 1,
                "week_count": 2,
                "in_progress_count": None,
                "completed_today_count": "bad-value",
                "customer_name": "private customer",
                "raw_error": "private database detail",
            }
        ]
    )

    result = repository.get_my_work_task_summary(
        f" {USER_ID.upper()} ",
        db=database,
    )

    assert result["ok"] is True
    assert result["summary"] == {
        "open_count": 5,
        "overdue_count": 0,
        "today_count": 1,
        "week_count": 2,
        "in_progress_count": 0,
        "completed_today_count": 0,
    }
    assert database.calls == [
        (
            repository.RPC_SUMMARY,
            {"p_current_user_id": USER_ID},
        )
    ]


def test_sales_followups_use_keyset_rpc_and_allowlist_fields():
    assignment_id = "50000000-0000-4000-8000-000000000001"
    database = _Database(
        [
            {
                "assignment_id": assignment_id,
                "company_name": "synthetic company",
                "next_contact_at": "2026-08-06T00:00:00Z",
                "company_uid": "private-source-key",
                "own_memo": "private memo",
            }
        ]
    )

    result = repository.list_my_sales_followups(
        f" {USER_ID.upper()} ",
        limit=5000,
        after_next_contact_at="2026-08-05T00:00:00+00:00",
        after_assignment_id=assignment_id,
        db=database,
    )

    assert result["ok"] is True
    assert result["assignments"] == [
        {
            "assignment_id": assignment_id,
            "company_name": "synthetic company",
            "next_contact_at": "2026-08-06T00:00:00Z",
        }
    ]
    assert database.calls == [
        (
            repository.RPC_SALES_FOLLOWUPS,
            {
                "p_current_user_id": USER_ID,
                "p_limit": 1000,
                "p_after_next_contact_at": "2026-08-05T00:00:00Z",
                "p_after_assignment_id": assignment_id,
            },
        )
    ]


@pytest.mark.parametrize(
    ("after_at", "after_id"),
    (
        ("2026-08-05T00:00:00Z", None),
        (None, "50000000-0000-4000-8000-000000000001"),
        ("not-a-date", "50000000-0000-4000-8000-000000000001"),
        ("2026-08-05T00:00:00Z", "not-a-uuid"),
    ),
)
def test_invalid_sales_followup_cursor_never_reaches_rpc(after_at, after_id):
    database = _Database([])

    result = repository.list_my_sales_followups(
        USER_ID,
        after_next_contact_at=after_at,
        after_assignment_id=after_id,
        db=database,
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_INPUT"
    assert result["assignments"] == []
    assert database.calls == []


def test_transport_exceptions_map_to_fixed_safe_results():
    private_phone = "010" + "-1111" + "-2222"
    first_secret = f"service-role-token customer-name {private_phone}"
    second_secret = "different-private-database-payload"
    first = repository.list_my_work_tasks(
        USER_ID,
        db=_Database(error=RuntimeError(first_secret)),
    )
    second = repository.list_my_work_tasks(
        USER_ID,
        db=_Database(error=RuntimeError(second_secret)),
    )

    assert first["ok"] is False
    assert first["code"] == second["code"] == "SERVICE_UNAVAILABLE"
    assert first["message"] == second["message"]
    serialized = repr((first, second))
    assert first_secret not in serialized
    assert second_secret not in serialized
    assert private_phone not in serialized


@pytest.mark.parametrize(
    ("action", "defer_until", "code", "status"),
    (
        ("start", None, "STARTED", "in_progress"),
        ("complete", None, "COMPLETED", "completed"),
        (
            "defer",
            datetime(
                2026,
                8,
                6,
                9,
                0,
                tzinfo=timezone(timedelta(hours=9)),
            ),
            "DEFERRED",
            "scheduled",
        ),
    ),
)
def test_transition_supports_start_complete_and_defer(
    action,
    defer_until,
    code,
    status,
):
    database = _Database(
        [
            {
                "success": True,
                "code": code,
                "task_id": TASK_ID,
                "status": status,
                "due_at": "2026-08-06T00:00:00Z",
                "task_version": 4,
                "updated_at": "2026-08-05T02:00:00Z",
                "assigned_user_id": "another-private-user",
                "raw_detail": "private database response",
            }
        ]
    )

    result = repository.transition_my_work_task(
        f" {USER_ID.upper()} ",
        TASK_ID,
        action.upper(),
        3,
        defer_until=defer_until,
        db=database,
    )

    assert result["ok"] is True
    assert result["code"] == code
    assert result["task"]["task_id"] == TASK_ID
    assert result["task"]["task_version"] == 4
    assert "assigned_user_id" not in result["task"]
    assert "raw_detail" not in result["task"]
    parameters = database.calls[0][1]
    assert database.calls[0][0] == repository.RPC_TRANSITION
    assert parameters["p_current_user_id"] == USER_ID
    assert parameters["p_task_id"] == TASK_ID
    assert parameters["p_action"] == action
    assert parameters["p_expected_version"] == 3
    assert parameters["p_defer_until"] == (
        "2026-08-06T00:00:00Z" if action == "defer" else None
    )


@pytest.mark.parametrize(
    ("user_id", "task_id", "action", "version", "defer_until"),
    (
        ("", TASK_ID, "start", 1, None),
        (USER_ID, "not-a-uuid", "start", 1, None),
        (USER_ID, TASK_ID, "reopen", 1, None),
        (USER_ID, TASK_ID, "start", 0, None),
        (USER_ID, TASK_ID, "start", True, None),
        (USER_ID, TASK_ID, "start", 1.5, None),
        (
            USER_ID,
            TASK_ID,
            "start",
            1,
            "2026-08-06T00:00:00Z",
        ),
        (USER_ID, TASK_ID, "defer", 1, None),
        (USER_ID, TASK_ID, "defer", 1, "not-a-date"),
    ),
)
def test_invalid_transition_inputs_never_reach_rpc(
    user_id,
    task_id,
    action,
    version,
    defer_until,
):
    database = _Database([])

    result = repository.transition_my_work_task(
        user_id,
        task_id,
        action,
        version,
        defer_until=defer_until,
        db=database,
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_INPUT"
    assert result["task"] == {}
    assert database.calls == []


def test_convenience_transitions_bind_only_the_fixed_actions():
    responses = {
        "start": (repository.start_work_task, "STARTED", "in_progress"),
        "complete": (repository.complete_work_task, "COMPLETED", "completed"),
    }
    for action, (function, code, status) in responses.items():
        database = _Database(
            [
                {
                    "success": True,
                    "code": code,
                    "task_id": TASK_ID,
                    "status": status,
                    "task_version": 2,
                }
            ]
        )
        result = function(USER_ID, TASK_ID, 1, db=database)
        assert result["ok"] is True
        assert database.calls[0][1]["p_action"] == action
        assert database.calls[0][1]["p_defer_until"] is None

    defer_database = _Database(
        [
            {
                "success": True,
                "code": "DEFERRED",
                "task_id": TASK_ID,
                "status": "scheduled",
                "task_version": 2,
            }
        ]
    )
    result = repository.defer_work_task(
        USER_ID,
        TASK_ID,
        1,
        "2026-08-06T00:00:00Z",
        db=defer_database,
    )
    assert result["ok"] is True
    assert defer_database.calls[0][1]["p_action"] == "defer"
    assert defer_database.calls[0][1]["p_defer_until"] == (
        "2026-08-06T00:00:00Z"
    )
