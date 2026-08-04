from __future__ import annotations

import json
from unittest.mock import patch

import kakao_provider_admin as admin


BLOCKED_STATE = {
    "state": "blocked",
    "guard_generation": 3,
    "approved_generation": 2,
    "consumed_generation": 2,
    "guard_reason": "INITIAL_ZERO_MATCH_RATE",
    "source_job": "employment",
    "observed_count": 100,
    "matched_count": 0,
    "tripped_at": "2026-08-04T15:00:00Z",
    "approved_at": "",
    "resumed_at": "",
}


def test_status_prints_only_safe_guard_metadata(capsys):
    with patch.object(
        admin.kakao_provider_runtime,
        "get_guard_state",
        return_value=BLOCKED_STATE,
    ):
        exit_code = admin.main(["status"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "job": "kakao-provider-admin",
        "action": "status",
        "provider": "kakao",
        "state": "blocked",
        "generation": 3,
        "reason": "INITIAL_ZERO_MATCH_RATE",
        "source_job": "employment",
        "observed_count": 100,
        "matched_count": 0,
        "tripped_at": "2026-08-04T15:00:00+00:00",
        "approved_at": "",
        "resumed_at": "",
    }


def test_approve_requires_generation_and_exact_confirmation(capsys):
    approved_state = {
        **BLOCKED_STATE,
        "state": "resume_approved",
        "approved_generation": 3,
        "approved_at": "2026-08-04T15:05:00Z",
    }
    with patch.object(
        admin.kakao_provider_runtime,
        "approve_guard",
        return_value=True,
    ) as approve, patch.object(
        admin.kakao_provider_runtime,
        "get_guard_state",
        return_value=approved_state,
    ):
        exit_code = admin.main(
            [
                "approve",
                "--generation",
                "3",
                "--confirm",
                "KAKAO_RESTART_APPROVED",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    approve.assert_called_once_with(3, "KAKAO_RESTART_APPROVED")
    assert payload["action"] == "approve"
    assert payload["state"] == "resume_approved"
    assert payload["generation"] == 3


def test_rejected_generation_returns_nonzero(capsys):
    with patch.object(
        admin.kakao_provider_runtime,
        "approve_guard",
        return_value=False,
    ), patch.object(
        admin.kakao_provider_runtime,
        "get_guard_state",
        return_value=BLOCKED_STATE,
    ):
        exit_code = admin.main(
            [
                "approve",
                "--generation",
                "2",
                "--confirm",
                "KAKAO_RESTART_APPROVED",
            ]
        )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["action"] == "approve_rejected"
    assert payload["state"] == "blocked"


def test_preflight_uses_lease_and_prints_only_safe_result(capsys):
    with patch.object(
        admin.kakao_provider_runtime,
        "new_lease_token",
        return_value="lease-token",
    ), patch.object(
        admin.kakao_provider_runtime,
        "acquire_lease",
        return_value=True,
    ) as acquire, patch.object(
        admin.kakao_provider_runtime,
        "release_lease",
        return_value=True,
    ) as release, patch.object(
        admin.kakao_provider_runtime,
        "test_connection_and_record",
        return_value={
            "ok": True,
            "category": "CONNECTED",
            "safe_error_code": "",
            "request_count": 1,
            "message": "must not be printed",
        },
    ):
        exit_code = admin.main(["preflight"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert "must not be printed" not in output
    assert payload == {
        "job": "kakao-provider-admin",
        "action": "preflight",
        "provider": "kakao",
        "ok": True,
        "category": "CONNECTED",
        "safe_error_code": "",
        "request_count": 1,
    }
    acquire.assert_called_once_with("lease-token")
    release.assert_called_once_with("lease-token")


def test_preflight_lease_conflict_stops_before_http(capsys):
    with patch.object(
        admin.kakao_provider_runtime,
        "acquire_lease",
        return_value=False,
    ), patch.object(
        admin.kakao_provider_runtime,
        "test_connection_and_record",
    ) as connection:
        exit_code = admin.main(["preflight"])

    assert exit_code == 2
    assert capsys.readouterr().out.strip() == (
        "kakao-provider-admin preflight=lease-unavailable"
    )
    connection.assert_not_called()


def test_invalid_confirmation_never_prints_submitted_value(capsys):
    submitted = "not-approved-sensitive-text"
    exit_code = admin.main(
        [
            "approve",
            "--generation",
            "3",
            "--confirm",
            submitted,
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert output.strip() == "kakao-provider-admin status=invalid-request"
    assert submitted not in output


def test_runtime_failure_never_prints_exception_detail(capsys):
    detail = "secret response body"
    with patch.object(
        admin.kakao_provider_runtime,
        "get_guard_state",
        side_effect=RuntimeError(detail),
    ):
        exit_code = admin.main(["status"])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert output.strip() == "kakao-provider-admin status=unavailable"
    assert detail not in output


def test_status_sanitizes_unexpected_database_strings(capsys):
    secret = "private raw provider response"
    with patch.object(
        admin.kakao_provider_runtime,
        "get_guard_state",
        return_value={
            **BLOCKED_STATE,
            "state": secret,
            "guard_reason": secret,
            "source_job": secret,
            "tripped_at": secret,
        },
    ):
        exit_code = admin.main(["status"])

    output = capsys.readouterr().out
    payload = json.loads(output)
    assert exit_code == 0
    assert secret not in output
    assert payload["state"] == "unavailable"
    assert payload["reason"] == "PROVIDER_GUARD"
    assert payload["source_job"] == ""
    assert payload["tripped_at"] == ""
