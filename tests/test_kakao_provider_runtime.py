from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import kakao_provider_runtime as runtime


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / (
    "20260804161208_kakao_enrichment_runtime_guards.sql"
)
QUOTA_FIX_MIGRATION = ROOT / "supabase" / "migrations" / (
    "20260804161836_fix_kakao_quota_reservation_ambiguity.sql"
)
USAGE_FIX_MIGRATION = ROOT / "supabase" / "migrations" / (
    "20260804162407_fix_kakao_usage_reconciliation_ambiguity.sql"
)


class FakeDatabase:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def rpc(self, name, parameters):
        self.calls.append((name, parameters))
        return self.responses.pop(0)


def test_safe_error_code_never_forwards_arbitrary_text():
    assert runtime.safe_error_code("http_500") == "HTTP_500"
    assert runtime.safe_error_code("timeout") == "TIMEOUT"
    assert runtime.safe_error_code("upstream raw response") == "PROVIDER_ERROR"


def test_connection_category_is_restricted_to_public_categories():
    assert runtime.safe_connection_category("AUTH_ERROR") == "AUTH_ERROR"
    assert runtime.safe_connection_category("secret detail") == "NETWORK_ERROR"
    assert runtime.safe_connection_category("AUTH_ERROR", ok=True) == "CONNECTED"


def test_lease_rpcs_use_provider_and_token():
    database = FakeDatabase([True, [{"renewed": True}], {"released": "true"}])
    token = runtime.new_lease_token()
    assert runtime.acquire_lease(token, database=database)
    assert runtime.renew_lease(token, database=database)
    assert runtime.release_lease(token, database=database)
    assert [name for name, _parameters in database.calls] == [
        "oasis_acquire_contact_provider_lease",
        "oasis_renew_contact_provider_lease",
        "oasis_release_contact_provider_lease",
    ]
    assert all(
        parameters["p_provider"] == "kakao_local"
        for _name, parameters in database.calls
    )
    assert all(
        parameters["p_lease_token"] == token
        for _name, parameters in database.calls
    )


def test_guard_state_requires_declared_state_to_match_generation_counters():
    database = FakeDatabase(
        [
            [
                {
                    "guard_state": "blocked",
                    "guard_generation": 4,
                    "approved_generation": 3,
                    "consumed_generation": 3,
                    "guard_reason": "INITIAL_ZERO_MATCH_RATE",
                    "source_job": "employment",
                    "observed_count": 100,
                    "matched_count": 0,
                    "tripped_at": "2026-08-04T15:00:00Z",
                }
            ]
        ]
    )

    state = runtime.get_guard_state(database=database)

    assert state == {
        "state": "blocked",
        "guard_generation": 4,
        "approved_generation": 3,
        "consumed_generation": 3,
        "guard_reason": "INITIAL_ZERO_MATCH_RATE",
        "source_job": "employment",
        "observed_count": 100,
        "matched_count": 0,
        "tripped_at": "2026-08-04T15:00:00Z",
        "approved_at": "",
        "resumed_at": "",
    }
    assert database.calls == [
        (
            "oasis_get_contact_provider_guard",
            {"p_provider": "kakao_local"},
        )
    ]


def test_guard_state_rejects_untrusted_database_values():
    database = FakeDatabase(
        [
            {
                "guard_generation": 2,
                "approved_generation": 99,
                "consumed_generation": 99,
                "guard_reason": "raw provider response",
                "source_job": "private-company-name",
                "observed_count": 3,
                "matched_count": 99,
            }
        ]
    )

    with pytest.raises(RuntimeError):
        runtime.get_guard_state(database=database)


def test_guard_state_rejects_missing_control_row():
    with pytest.raises(RuntimeError):
        runtime.get_guard_state(database=FakeDatabase([[]]))


def test_guard_transition_rpcs_use_exact_tokens_and_generation():
    database = FakeDatabase([True, True, True])
    lease_token = runtime.new_lease_token()
    incident_token = runtime.new_guard_incident_token()

    assert runtime.trip_guard(
        lease_token,
        incident_token,
        "initial_zero_match_rate",
        "employment",
        observed_count=100,
        matched_count=0,
        database=database,
    )
    assert runtime.approve_guard(
        7,
        runtime.GUARD_APPROVAL_CONFIRMATION,
        database=database,
    )
    assert runtime.consume_guard_resume(
        lease_token,
        7,
        database=database,
    )

    assert database.calls == [
        (
            "oasis_trip_contact_provider_guard",
            {
                "p_provider": "kakao_local",
                "p_lease_token": lease_token,
                "p_incident_token": incident_token,
                "p_guard_reason": "INITIAL_ZERO_MATCH_RATE",
                "p_source_job": "employment",
                "p_observed_count": 100,
                "p_matched_count": 0,
            },
        ),
        (
            "oasis_approve_contact_provider_guard",
            {
                "p_provider": "kakao_local",
                "p_expected_generation": 7,
                "p_confirmation": "KAKAO_RESTART_APPROVED",
            },
        ),
        (
            "oasis_consume_contact_provider_resume",
            {
                "p_provider": "kakao_local",
                "p_lease_token": lease_token,
                "p_expected_generation": 7,
            },
        ),
    ]


def test_guard_transition_inputs_fail_before_rpc():
    database = FakeDatabase([])
    lease_token = runtime.new_lease_token()
    incident_token = runtime.new_guard_incident_token()

    with pytest.raises(ValueError):
        runtime.trip_guard(
            lease_token,
            incident_token,
            "response body",
            "employment",
            database=database,
        )
    with pytest.raises(ValueError):
        runtime.trip_guard(
            lease_token,
            incident_token,
            "ROLLING_ZERO_MATCH_RATE",
            "other",
            database=database,
        )
    with pytest.raises(ValueError):
        runtime.approve_guard(2, "yes", database=database)
    with pytest.raises(ValueError):
        runtime.consume_guard_resume("not-a-uuid", 2, database=database)

    assert database.calls == []


def test_daily_usage_is_normalized_without_raw_error_values():
    quota_date = runtime._kst_quota_date()
    database = FakeDatabase(
        [
            [
                {
                    "request_count": 12,
                    "blocked_until": "2026-08-04T15:00:00Z",
                    "last_safe_error_code": "raw response",
                    "quota_date": quota_date,
                }
            ]
        ]
    )
    usage = runtime.get_daily_usage(database=database)
    assert usage == {
        "request_count": 12,
        "blocked_until": "2026-08-04T15:00:00Z",
        "last_safe_error_code": "PROVIDER_ERROR",
        "quota_date": quota_date,
    }


def test_record_usage_normalizes_error_before_rpc():
    quota_date = runtime._kst_quota_date()
    database = FakeDatabase(
        [
            [
                {
                    "request_count": 7,
                    "blocked_until": None,
                    "last_safe_error_code": "PROVIDER_ERROR",
                    "quota_date": quota_date,
                }
            ]
        ]
    )
    result = runtime.record_usage(
        2,
        "raw upstream response",
        database=database,
    )
    assert result["request_count"] == 7
    assert database.calls[0][1]["p_request_count"] == 2
    assert database.calls[0][1]["p_safe_error_code"] == "PROVIDER_ERROR"


def test_quota_reservation_is_atomic_and_uses_safe_limit():
    quota_date = runtime._kst_quota_date()
    database = FakeDatabase(
        [
            [
                {
                    "request_count": 85_000,
                    "reserved": True,
                    "blocked_until": None,
                    "last_safe_error_code": "",
                    "quota_date": quota_date,
                }
            ]
        ]
    )

    result = runtime.reserve_quota(10, 85_000, database=database)

    assert result == {
        "request_count": 85_000,
        "reserved": True,
        "blocked_until": "",
        "last_safe_error_code": "",
        "quota_date": quota_date,
    }
    assert database.calls == [
        (
            "oasis_reserve_contact_provider_quota",
            {
                "p_provider": "kakao_local",
                "p_request_count": 10,
                "p_safe_limit": 85_000,
            },
        )
    ]


def test_reconciliation_refunds_only_unused_reservation_and_keeps_429():
    quota_date = runtime._kst_quota_date()
    database = FakeDatabase(
        [
            [
                {
                    "request_count": 12,
                    "blocked_until": "2026-08-04T15:00:00Z",
                    "last_safe_error_code": "HTTP_429",
                    "quota_date": quota_date,
                }
            ]
        ]
    )

    result = runtime.reconcile_usage(
        4,
        3,
        "HTTP_429",
        reservation_date=quota_date,
        database=database,
    )

    assert result["request_count"] == 12
    assert result["last_safe_error_code"] == "HTTP_429"
    assert database.calls[0][0] == "oasis_record_contact_provider_usage"
    assert database.calls[0][1]["p_request_count"] == -1
    assert database.calls[0][1]["p_safe_error_code"] == "HTTP_429"
    assert database.calls[0][1]["p_quota_date"] == quota_date


def test_reconciliation_never_refunds_prior_day_into_new_quota():
    database = FakeDatabase(
        [
            [
                {
                    "request_count": 3,
                    "blocked_until": None,
                    "last_safe_error_code": "",
                    "quota_date": "2026-08-05",
                }
            ]
        ]
    )

    with patch.object(runtime, "_kst_quota_date", return_value="2026-08-05"):
        runtime.reconcile_usage(
            4,
            3,
            reservation_date="2026-08-04",
            database=database,
        )

    assert database.calls[0][1]["p_request_count"] == 3
    assert database.calls[0][1]["p_quota_date"] == "2026-08-05"


def test_preflight_records_actual_request_count_with_same_database():
    quota_date = runtime._kst_quota_date()
    database = FakeDatabase(
        [
            [
                {
                    "request_count": 3,
                    "reserved": True,
                    "blocked_until": None,
                    "last_safe_error_code": "",
                    "quota_date": quota_date,
                }
            ],
            [
                {
                    "request_count": 3,
                    "blocked_until": None,
                    "last_safe_error_code": "HTTP_403",
                    "quota_date": quota_date,
                }
            ]
        ]
    )
    with patch.object(
        runtime.kakao_local_client,
        "test_connection",
        return_value={
            "ok": False,
            "category": "PERMISSION_ERROR",
            "safe_error_code": "HTTP_403",
            "request_count": 1,
            "message": "must not be forwarded",
        },
    ):
        result = runtime.test_connection_and_record(database=database)
    assert result == {
        "ok": False,
        "category": "PERMISSION_ERROR",
        "safe_error_code": "HTTP_403",
        "request_count": 1,
    }
    assert database.calls[0][0] == "oasis_reserve_contact_provider_quota"
    assert database.calls[0][1]["p_request_count"] == 1
    assert database.calls[0][1]["p_safe_limit"] == 85_000
    assert database.calls[1][0] == "oasis_record_contact_provider_usage"
    assert database.calls[1][1]["p_request_count"] == 0
    assert database.calls[1][1]["p_safe_error_code"] == "HTTP_403"


def test_preflight_quota_denial_stops_before_api_call():
    quota_date = runtime._kst_quota_date()
    database = FakeDatabase(
        [
            [
                {
                    "request_count": 85_000,
                    "reserved": False,
                    "blocked_until": None,
                    "last_safe_error_code": "",
                    "quota_date": quota_date,
                }
            ]
        ]
    )
    with patch.object(
        runtime.kakao_local_client,
        "test_connection",
    ) as connection:
        result = runtime.test_connection_and_record(database=database)

    assert result == {
        "ok": False,
        "category": "QUOTA_ERROR",
        "safe_error_code": "PROVIDER_ERROR",
        "request_count": 0,
    }
    connection.assert_not_called()


def test_quota_block_and_next_kst_reset():
    now = datetime(2026, 8, 4, 14, 59, tzinfo=timezone.utc)
    assert runtime.is_quota_blocked(
        {"blocked_until": "2026-08-04T15:00:00Z"},
        now=now,
    )
    assert runtime.next_kst_quota_reset(now).isoformat() == (
        "2026-08-04T15:00:00+00:00"
    )
    assert runtime.next_kst_quota_reset(
        datetime(2026, 8, 4, 15, 0, tzinfo=timezone.utc)
    ).isoformat() == "2026-08-05T15:00:00+00:00"


def test_runtime_migration_is_service_role_only_and_forces_rls():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert sql.count("force row level security") == 3
    assert "security definer" not in sql
    assert sql.count("security invoker") == 10
    assert sql.count("to service_role") >= 13
    assert sql.count(
        "from public, anon, authenticated, service_role"
    ) >= 3
    assert "grant execute" in sql
    assert "grant select, insert, update, delete" in sql
    assert sql.count(
        "from public, anon, authenticated, service_role"
    ) == 3


def test_runtime_migration_counts_kst_requests_and_blocks_429():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "asia/seoul" in sql
    assert "request_count = u.request_count + excluded.request_count" in sql
    assert "v_code = 'http_429'" in sql
    assert "(v_quota_date + 1)::timestamp" in sql
    assert "at time zone 'asia/seoul'" in sql


def test_runtime_migration_reserves_quota_atomically_and_refunds_safely():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "oasis_reserve_contact_provider_quota" in sql
    assert "u.request_count + excluded.request_count <= v_safe_limit" in sql
    assert "v_safe_limit not between 1 and 90000" in sql
    assert "u.blocked_until <= v_now" in sql
    assert "v_count not between -10000 and 10000" in sql
    assert "request_count = greatest(0, u.request_count + v_count)" in sql
    assert "p_quota_date date default null" in sql
    assert "v_current_quota_date - 1" in sql
    assert (
        "on conflict on constraint "
        "oasis_contact_provider_daily_usage_pkey"
    ) in sql


def test_quota_followup_migration_keeps_the_unambiguous_conflict_target():
    sql = QUOTA_FIX_MIGRATION.read_text(encoding="utf-8").lower()
    assert "create or replace function" in sql
    assert (
        "on conflict on constraint "
        "oasis_contact_provider_daily_usage_pkey"
    ) in sql
    assert "grant execute" in sql


def test_usage_followup_migration_keeps_the_unambiguous_conflict_target():
    sql = USAGE_FIX_MIGRATION.read_text(encoding="utf-8").lower()
    assert "create or replace function" in sql
    assert "oasis_record_contact_provider_usage" in sql
    assert (
        "on conflict on constraint "
        "oasis_contact_provider_daily_usage_pkey"
    ) in sql
    assert (
        "from public, anon, authenticated" in sql
    )
    assert "to service_role" in sql


def test_runtime_migration_rejects_arbitrary_error_text():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "last_safe_error_code ~ '^[A-Z0-9_]{1,64}$'" not in sql
    assert "last_safe_error_code ~ '^HTTP_[0-9]{3}$'" in sql
    assert "v_code !~ '^HTTP_[0-9]{3}$'" in sql
