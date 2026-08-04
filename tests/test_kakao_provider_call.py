from __future__ import annotations

from unittest.mock import Mock, patch

import kakao_provider_call as provider_call


def _ready_guard() -> dict[str, object]:
    return {"state": provider_call.kakao_provider_runtime.GUARD_STATE_READY}


def test_externally_managed_call_does_not_double_count_or_lock():
    expected = {
        "ok": True,
        "outcome": "no_match",
        "request_count": 2,
        "candidates": [],
    }
    with patch.object(
        provider_call.kakao_local_client,
        "search_company",
        return_value=expected,
    ) as search, patch.object(
        provider_call.kakao_provider_runtime,
        "acquire_lease",
    ) as acquire:
        result = provider_call.search_company(
            "sample",
            "address",
            managed_externally=True,
        )

    assert result == expected
    search.assert_called_once()
    acquire.assert_not_called()


def test_ui_connection_check_uses_shared_lease_guard_and_ledger():
    release = Mock(return_value=True)
    recorded = Mock(
        return_value={
            "ok": True,
            "category": "CONNECTED",
            "safe_error_code": "",
            "request_count": 1,
            "message": "must not be forwarded",
        }
    )
    with patch.multiple(
        provider_call.kakao_provider_runtime,
        new_lease_token=Mock(return_value="lease-token"),
        acquire_lease=Mock(return_value=True),
        release_lease=release,
        get_guard_state=Mock(return_value=_ready_guard()),
        test_connection_and_record=recorded,
    ):
        result = provider_call.test_connection()

    assert result == {
        "ok": True,
        "status": "CONNECTED",
        "category": "CONNECTED",
        "safe_error_code": "",
        "request_count": 1,
    }
    assert "must not be forwarded" not in repr(result)
    recorded.assert_called_once_with()
    release.assert_called_once_with("lease-token")


def test_ui_connection_check_respects_persistent_guard():
    recorded = Mock()
    with patch.multiple(
        provider_call.kakao_provider_runtime,
        acquire_lease=Mock(return_value=True),
        release_lease=Mock(return_value=True),
        get_guard_state=Mock(return_value={"state": "blocked"}),
        test_connection_and_record=recorded,
    ):
        result = provider_call.test_connection()

    assert result["ok"] is False
    assert result["safe_error_code"] == "PROVIDER_ERROR"
    recorded.assert_not_called()


def test_direct_call_uses_shared_lease_and_actual_request_ledger():
    result = {
        "ok": True,
        "outcome": "no_match",
        "status": "NO_MATCH",
        "safe_error_code": "",
        "request_count": 1,
        "candidates": [],
    }
    reserve = Mock(
        return_value={
            "reserved": True,
            "request_count": 12,
            "blocked_until": "",
            "quota_date": "2026-08-05",
        }
    )
    reconcile = Mock(return_value={"request_count": 11})
    release = Mock(return_value=True)
    with patch.multiple(
        provider_call.kakao_provider_runtime,
        new_lease_token=Mock(return_value="lease-token"),
        acquire_lease=Mock(return_value=True),
        release_lease=release,
        get_guard_state=Mock(return_value=_ready_guard()),
        get_daily_usage=Mock(
            return_value={
                "request_count": 10,
                "blocked_until": "",
                "quota_date": "2026-08-05",
            }
        ),
        reserve_quota=reserve,
        reconcile_usage=reconcile,
    ), patch.object(
        provider_call.kakao_local_client,
        "search_company",
        return_value=result,
    ):
        actual = provider_call.search_company("sample", "address")

    assert actual == result
    reserve.assert_called_once_with(2, 85_000)
    reconcile.assert_called_once_with(
        2,
        1,
        "",
        reservation_date="2026-08-05",
    )
    release.assert_called_once_with("lease-token")


def test_direct_call_keeps_provider_error_and_records_safe_code():
    result = {
        "ok": False,
        "outcome": "error",
        "status": "HTTP_401",
        "safe_error_code": "HTTP_401",
        "request_count": 1,
        "candidates": [],
    }
    reconcile = Mock(return_value={})
    with patch.multiple(
        provider_call.kakao_provider_runtime,
        acquire_lease=Mock(return_value=True),
        release_lease=Mock(return_value=True),
        get_guard_state=Mock(return_value=_ready_guard()),
        get_daily_usage=Mock(
            return_value={"request_count": 0, "blocked_until": ""}
        ),
        reserve_quota=Mock(
            return_value={
                "reserved": True,
                "quota_date": "2026-08-05",
            }
        ),
        reconcile_usage=reconcile,
    ), patch.object(
        provider_call.kakao_local_client,
        "search_company",
        return_value=result,
    ):
        actual = provider_call.search_company("sample", "address")

    assert actual["safe_error_code"] == "HTTP_401"
    reconcile.assert_called_once_with(
        2,
        1,
        "HTTP_401",
        reservation_date="2026-08-05",
    )


def test_blocked_guard_never_calls_provider():
    release = Mock(return_value=True)
    with patch.multiple(
        provider_call.kakao_provider_runtime,
        acquire_lease=Mock(return_value=True),
        release_lease=release,
        get_guard_state=Mock(return_value={"state": "blocked"}),
    ), patch.object(
        provider_call.kakao_local_client,
        "search_company",
    ) as search:
        result = provider_call.search_company("sample", "address")

    assert result["outcome"] == "error"
    search.assert_not_called()
    release.assert_called_once()


def test_lease_conflict_never_calls_provider():
    with patch.object(
        provider_call.kakao_provider_runtime,
        "acquire_lease",
        return_value=False,
    ), patch.object(
        provider_call.kakao_local_client,
        "search_company",
    ) as search:
        result = provider_call.search_company("sample", "address")

    assert result["outcome"] == "error"
    search.assert_not_called()
