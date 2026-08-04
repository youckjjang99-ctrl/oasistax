from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import sales_outreach_repository as repository


ASSIGNMENT_ID = "10000000-0000-4000-8000-000000000001"
PROSPECT_ID = "20000000-0000-4000-8000-000000000002"
CONTACT_ID = "30000000-0000-4000-8000-000000000003"
OUTBOX_ID = "40000000-0000-4000-8000-000000000004"
TOKEN = "50000000-0000-4000-8000-000000000005"
HMAC_KEY = "test-outreach-hmac-key-with-at-least-32-characters"
HEX_A = "a" * 64
HEX_B = "b" * 64
MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260804082841_v1040_sales_outreach_outbox.sql"
)


def _email(local_part: str) -> str:
    return "@".join((local_part, "example.invalid"))


class _Database:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, parameters: dict):
        self.calls.append((name, dict(parameters)))
        if self.error:
            raise self.error
        return self.payload


def _reserve(db: _Database, *, confirmed: bool = True):
    return repository.reserve_outreach_attempt(
        _email("owner"),
        "request-12345678",
        HEX_A,
        HEX_B,
        ASSIGNMENT_ID,
        PROSPECT_ID,
        "source:" + ("c" * 64),
        CONTACT_ID,
        "2026-08-04T00:00:00+00:00",
        "email",
        consent_confirmed=confirmed,
        db=db,
    )


def test_hmac_fingerprints_are_domain_separated_and_do_not_expose_values():
    with patch.dict(
        repository.os.environ,
        {repository.OUTREACH_HMAC_KEY_ENV: HMAC_KEY},
        clear=True,
    ):
        content = repository.message_fingerprint(
            "email",
            "private subject",
            "private body",
        )
        recipient = repository.recipient_fingerprint(
            "email",
            _email("private"),
        )

    assert len(content) == 64
    assert len(recipient) == 64
    assert content != recipient
    assert "private" not in content
    assert "example" not in recipient


def test_missing_hmac_key_fails_closed():
    with patch.dict(repository.os.environ, {}, clear=True):
        try:
            repository.message_fingerprint("sms", "", "hello")
        except RuntimeError:
            pass
        else:
            raise AssertionError("missing HMAC key must fail closed")


def test_reserve_requires_ui_confirmation_before_rpc():
    db = _Database()

    result = _reserve(db, confirmed=False)

    assert result["ok"] is False
    assert result["code"] == "CONSENT_CONFIRMATION_REQUIRED"
    assert db.calls == []


def test_reserve_sends_only_opaque_bindings_to_rpc():
    db = _Database(
        [
            {
                "success": True,
                "code": "RESERVED",
                "outbox_id": OUTBOX_ID,
                "status": "reserved",
                "acquired": True,
                "reservation_token": TOKEN,
            }
        ]
    )

    result = _reserve(db)

    assert result["ok"] is True
    assert result["acquired"] is True
    name, parameters = db.calls[0]
    assert name == repository.RPC_RESERVE
    serialized = repr(parameters)
    assert "recipient" not in serialized.lower() or "hmac" in serialized.lower()
    for forbidden in (
        _email("private"),
        "private subject",
        "private body",
        "recording",
        "evidence",
    ):
        assert forbidden not in serialized
    assert parameters["p_content_hmac"] == HEX_A
    assert parameters["p_recipient_hmac"] == HEX_B
    assert "p_consent_confirmed" not in parameters


def test_rpc_exception_is_redacted_and_never_echoed():
    db = _Database(error=RuntimeError("secret-token " + _email("private")))

    result = _reserve(db)

    assert result["ok"] is False
    assert result["code"] == "OUTBOX_UNAVAILABLE"
    assert "secret-token" not in result["message"]
    assert "example.invalid" not in result["message"]


def test_begin_binds_recipient_hmac_and_reservation_token():
    db = _Database(
        [
            {
                "success": True,
                "code": "DISPATCH_STARTED",
                "outbox_id": OUTBOX_ID,
                "status": "dispatching",
                "dispatch_started": True,
            }
        ]
    )

    result = repository.begin_outreach_dispatch(
        _email("owner"),
        OUTBOX_ID,
        TOKEN,
        recipient_hmac=HEX_B,
        db=db,
    )

    assert result["ok"] is True
    assert result["dispatch_started"] is True
    name, parameters = db.calls[0]
    assert name == repository.RPC_BEGIN
    assert parameters["p_recipient_hmac"] == HEX_B
    assert parameters["p_reservation_token"] == TOKEN


def test_finalize_accepts_only_one_of_the_safe_terminal_states():
    invalid_db = _Database()
    invalid = repository.finalize_outreach_attempt(
        _email("owner"),
        OUTBOX_ID,
        TOKEN,
        "retry",
        db=invalid_db,
    )
    assert invalid["ok"] is False
    assert invalid_db.calls == []

    db = _Database(
        [
            {
                "success": True,
                "code": "FINALIZED",
                "outbox_id": OUTBOX_ID,
                "status": "delivery_unknown",
            }
        ]
    )
    result = repository.finalize_outreach_attempt(
        _email("owner"),
        OUTBOX_ID,
        TOKEN,
        "delivery_unknown",
        safe_result_code="provider timeout: private detail",
        db=db,
    )

    assert result["ok"] is True
    parameters = db.calls[0][1]
    assert parameters["p_safe_result_code"] == (
        "PROVIDER_TIMEOUT__PRIVATE_DETAIL"
    )


def test_history_allowlist_drops_unexpected_or_sensitive_fields():
    db = _Database(
        [
            {
                "outbox_id": OUTBOX_ID,
                "channel": "email",
                "status": "provider_accepted",
                "safe_result_code": "ACCEPTED",
                "reserved_at": "2026-08-04T00:00:00+00:00",
                "recipient": _email("private"),
                "message_body": "private body",
            }
        ]
    )

    result = repository.list_outreach_history(
        _email("owner"),
        db=db,
    )

    assert result["ok"] is True
    assert "recipient" not in result["history"][0]
    assert "message_body" not in result["history"][0]


def test_migration_is_metadata_only_fail_closed_and_service_role_only():
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    for forbidden_column in (
        "recipient_value text",
        "subject text",
        "message_body text",
        "provider_response",
        "recording_path",
        "evidence_path",
    ):
        assert forbidden_column not in sql
    assert "oasis-outreach-recipient:" in sql
    assert "or o.status = 'delivery_unknown'" in sql
    assert "confirmed_not_sent" in sql
    assert "oasis_admin_reconcile_prospect_outreach" in sql
    assert "enable row level security" in sql
    assert "force row level security" in sql
    assert "from public, anon, authenticated, service_role" in sql
    assert "grant execute on function" in sql
    assert "to service_role" in sql
    assert "create policy" not in sql
    assert "create trigger oasis_outreach" not in sql
