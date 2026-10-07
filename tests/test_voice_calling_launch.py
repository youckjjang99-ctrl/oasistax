"""Synthetic-only launch tests: no provider or database connections."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from voice_calling import VoiceSettings
from voice_calling_launch import launch_filtered_calls, launch_manual_call
from voice_calling_repository import VoiceRepository

PHONE = "010" + "0" * 7 + "1"
E164 = "+82" + PHONE[1:]


def ready_settings(**changes):
    config = VoiceSettings(
        enabled=True, twilio_account_sid="AC" + "1" * 32,
        twilio_auth_token="x" * 32, caller_id=E164,
        public_base_url="https://voice.example.com", openai_api_key="synthetic-key",
        stream_ticket_secret="s" * 32,
    )
    return replace(config, **changes)


def test_settings():
    return ready_settings(
        provider="clawops", caller_id="+82" + "70" + "0" * 8,
        clawops_account_id="AC" + "x" * 16, clawops_api_key="synthetic-key",
        clawops_signing_secret="s" * 32, clawops_billing_confirmed=True,
        clawops_test_numbers=(PHONE,),
    )


test_settings.__test__ = False


class Repo:
    def __init__(self):
        self.calls = []

    def action(self, actor, action, payload):
        self.calls.append((actor, action, payload))
        return {"ok": True, "code": "OK", "created_count": 1}


def bulk(**changes):
    return {"request_id": "bulk-request-0001", "filters": {}, "requested_count": 1000,
            "approval_confirmed": True, **changes}


def manual(**changes):
    now = datetime.now(timezone.utc)
    return {"request_id": "manual-request-0001", "company_name": "Synthetic company",
            "business_type": "corporate", "phone": PHONE, "purpose": "test",
            "consent_confirmed": True, "consent_kind": "explicit_consent",
            "evidence_ref": "synthetic-consent-reference", "granted_at": (now - timedelta(days=1)).isoformat(),
            "expires_at": (now + timedelta(days=1)).isoformat(), "approval_confirmed": True, **changes}


@pytest.mark.parametrize("launcher,payload", [(launch_filtered_calls, bulk), (launch_manual_call, manual)])
def test_disabled_server_never_contacts_repository(launcher, payload):
    repo = Repo()
    assert launcher(repo, "admin", payload(), VoiceSettings())["code"] == "CALLING_DISABLED_OR_NOT_READY"
    assert repo.calls == []


def test_environment_not_browser_controls_enabled(monkeypatch):
    monkeypatch.setenv("OASIS_VOICE_CALLS_ENABLED", "false")
    repo = Repo()
    assert not launch_filtered_calls(repo, "admin", bulk(enabled=True, ready=True))["ok"]
    assert not repo.calls


def test_bulk_defaults_canonical_payload_and_no_limit_override():
    repo = Repo()
    assert launch_filtered_calls(repo, "admin", bulk(daily_limit=9999), ready_settings())["ok"]
    sent = repo.calls[0][2]
    assert sent["requested_count"] == 1000
    assert sent["filters"] == {"business_type": "all", "phone_type": "all", "discovery_type": "all", "region": "", "industry": ""}
    assert "daily_limit" not in sent


@pytest.mark.parametrize("count", [True, False, 0, -1, 1001, 1.0, "1000", None])
def test_bulk_count_strict(count):
    repo = Repo()
    assert launch_filtered_calls(repo, "admin", bulk(requested_count=count), ready_settings())["code"] == "INVALID_INPUT"
    assert not repo.calls


@pytest.mark.parametrize("filters", [{"phone_type": "both"}, {"phone_type": []}, {"business_type": "invalid"}, {"discovery_type": 1}, {"region": "x" * 81}, {"industry": "x\n"}, {"company_uid": "forced"}, None])
def test_filters_reject_unknown_fields_and_bad_values(filters):
    repo = Repo()
    assert not launch_filtered_calls(repo, "admin", bulk(filters=filters), ready_settings())["ok"]
    assert not repo.calls


@pytest.mark.parametrize("launcher,payload", [(launch_filtered_calls, bulk), (launch_manual_call, manual)])
@pytest.mark.parametrize("changes", [{"approval_confirmed": False}, {"approval_confirmed": "true"}, {"request_id": "short"}, {"request_id": "bad id spaces"}])
def test_explicit_approval_and_request_identity(launcher, payload, changes):
    repo = Repo()
    assert not launcher(repo, "admin", payload(**changes), ready_settings())["ok"]
    assert not repo.calls


def test_bulk_disabled_in_test_only_even_when_ready():
    repo = Repo()
    assert launch_filtered_calls(repo, "admin", bulk(), test_settings())["code"] == "TEST_ONLY"
    assert not repo.calls


def test_manual_normalizes_and_preserves_semantics_without_fake_crm_id():
    repo = Repo()
    assert launch_manual_call(repo, "admin", manual(phone="+82 " + PHONE[1:]), test_settings())["ok"]
    sent = repo.calls[0][2]
    assert sent["phone"] == E164
    assert sent["purpose"] == "test"
    assert set(sent).isdisjoint({"company_uid", "assignment_id", "permission_id", "target_kind", "ready"})


@pytest.mark.parametrize("changes", [{"purpose": "customer_guidance"}, {"phone": PHONE[:-1] + "2"}])
def test_test_only_cannot_escape_allowlist(changes):
    repo = Repo()
    assert launch_manual_call(repo, "admin", manual(**changes), test_settings())["code"] == "TEST_ONLY"
    assert not repo.calls


@pytest.mark.parametrize("changes", [
    {"company_name": ""}, {"business_type": "all"}, {"phone": "invalid"},
    {"purpose": "anything"}, {"address": "x" * 501}, {"representative_name": "x\t"},
])
def test_manual_validation(changes):
    repo = Repo()
    assert launch_manual_call(repo, "admin", manual(**changes), ready_settings())["code"] == "INVALID_INPUT"
    assert not repo.calls


@pytest.mark.parametrize("changes", [
    {"consent_confirmed": False}, {"consent_confirmed": "true"}, {"consent_kind": "public_number"},
    {"evidence_ref": ""}, {"granted_at": "yesterday"}, {"expires_at": "2000-01-01T00:00:00+00:00"},
    {"granted_at": "2099-01-01T00:00:00+00:00"}, {"expires_at": "2099-01-01T00:00:00"},
])
def test_manual_consent_never_implied_by_test_purpose(changes):
    repo = Repo()
    assert launch_manual_call(repo, "admin", manual(**changes), ready_settings())["code"] == "CONSENT_REQUIRED"
    assert not repo.calls


def test_exception_not_echoed():
    class Broken:
        def action(self, *args):
            raise RuntimeError("SENSITIVE_PROVIDER_ERROR")
    result = launch_manual_call(Broken(), "admin", manual(), ready_settings())
    assert result["code"] == "NOT_READY"
    assert "SENSITIVE" not in str(result)


@pytest.mark.parametrize("action,rpc", [("filtered_campaign", "oasis_voice_filtered_campaign"), ("manual_call", "oasis_voice_manual_call")])
def test_repository_routes_with_authoritative_actor(action, rpc):
    class DB:
        def rpc(self, name, parameters):
            assert name == rpc
            assert parameters == {"p_current_user_id": "admin", "p_payload": {"request_id": "test-request"}}
            return {"ok": True}
    assert VoiceRepository(DB()).action("admin", action, {"request_id": "test-request"})["ok"]


@pytest.mark.parametrize("code", ["IDEMPOTENCY_CONFLICT", "NO_ELIGIBLE_TARGETS", "CAMPAIGN_CREATE_FAILED", "APPROVAL_EXPIRED"])
def test_new_codes_have_safe_messages(code):
    class DB:
        def rpc(self, *_):
            return {"ok": False, "code": code, "message": "UNTRUSTED"}
    result = VoiceRepository(DB()).action("admin", "manual_call", {})
    assert result["code"] == code and "UNTRUSTED" not in str(result)
