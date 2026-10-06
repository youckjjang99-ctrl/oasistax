from dataclasses import replace
from datetime import datetime, timezone

import pytest

from voice_calling import VoiceSettings, normalize_phone, validate_outcome, build_realtime_session, opening_greeting
from voice_calling_worker import dispatch_once
from voice_calling_repository import VoiceRepository
from voice_calling_provider import CallRejected, UnknownCall


# Synthetic parsing fixtures, assembled from parts like the other privacy tests.
SYNTHETIC_MOBILE_PARTS = ("010", "0000", "0001")
SYNTHETIC_MOBILE = "".join(SYNTHETIC_MOBILE_PARTS)
SYNTHETIC_E164 = "+82" + SYNTHETIC_MOBILE[1:]
SYNTHETIC_CALLER_ID = "+82" + "10" + "0" * 8


def settings():
    return VoiceSettings(enabled=True, twilio_account_sid="AC" + "1" * 32,
                         twilio_auth_token="x" * 32, caller_id=SYNTHETIC_CALLER_ID,
                         public_base_url="https://voice.example.com", openai_api_key="test-only",
                         stream_ticket_secret="s" * 32)


@pytest.mark.parametrize("raw", [
    "-".join(SYNTHETIC_MOBILE_PARTS),
    " ".join(("+82", "10", *SYNTHETIC_MOBILE_PARTS[1:])),
    "0082" + SYNTHETIC_MOBILE[1:],
    "82" + SYNTHETIC_MOBILE[1:],
])
def test_normalize_korean_phone(raw):
    assert normalize_phone(raw) == SYNTHETIC_E164


@pytest.mark.parametrize("raw", [
    "112", "+19" + "0" * 9, SYNTHETIC_MOBILE + ";DROP", "15880000",
    "060" + "0" * 8, SYNTHETIC_MOBILE[:-1],
])
def test_short_premium_or_non_korean_numbers_rejected(raw):
    with pytest.raises(ValueError):
        normalize_phone(raw)


def test_default_is_disabled_and_no_network_or_database():
    class NoCalls:
        def __getattr__(self, name):
            pytest.fail("Unexpected external access")
    assert dispatch_once(VoiceSettings(), NoCalls(), NoCalls())["code"] == "CALLING_DISABLED_OR_NOT_READY"


def test_readiness_does_not_expose_keys():
    config = settings()
    assert config.readiness()["ready"]
    assert config.openai_api_key not in str(config.readiness())
    assert not replace(config, public_base_url="http://voice.example.com").readiness()["ready"]
    assert not replace(config, stream_ticket_secret="").readiness()["ready"]


def test_env_limits_clamp_and_no_implicit_enable():
    config = VoiceSettings.from_environment({"OASIS_VOICE_CALLS_ENABLED": "yes", "OASIS_VOICE_DAILY_LIMIT": "100000", "OASIS_VOICE_MAX_CALL_SECONDS": "100000"})
    assert not config.enabled
    assert config.daily_limit == 100 and config.max_call_seconds == 300


def test_prompt_discloses_ai_and_does_not_send_phone_or_auth_data():
    session = build_realtime_session({"phone_e164": "SHOULD_NOT_SEND", "token": "SECRET", "company_name": "Synthetic"}, settings())
    assert "AI 상담원" in opening_greeting({})
    assert "9번" in opening_greeting({})
    assert "SHOULD_NOT_SEND" not in str(session) and "SECRET" not in str(session)
    assert session["audio"]["input"]["format"]["type"] == "audio/pcmu"
    assert session["tools"][0]["name"] == "save_call_result"


NOW = datetime(2030, 1, 7, 1, tzinfo=timezone.utc)
VISIT = {"outcome": "visit_requested", "visit_at": "2030-01-08T10:00:00+09:00", "address": "Synthetic office location", "summary": "visit request", "customer_confirmed": True}


@pytest.mark.parametrize("change", [{"customer_confirmed": False}, {"customer_confirmed": "true"}, {"visit_at": "2030-01-08T10:00:00"}, {"visit_at": "yesterday"}, {"visit_at": "2030-01-06T10:00:00+09:00"}, {"visit_at": "2030-01-08T21:00:00+09:00"}, {"visit_at": "2030-01-12T10:00:00+09:00"}, {"address": ""}, {"outcome": "approved_funding"}])
def test_result_requires_explicit_details(change):
    with pytest.raises(ValueError):
        validate_outcome({**VISIT, **change}, NOW)


def test_optout_does_not_require_visit_or_schedule():
    assert validate_outcome({"outcome": "do_not_call"}, NOW)["outcome"] == "do_not_call"


def test_summary_redacts_incidental_phone():
    checked = validate_outcome({**VISIT, "summary": "Call " + "-".join(SYNTHETIC_MOBILE_PARTS)}, NOW)
    assert "0001" not in checked["summary"]


class Repo:
    def __init__(self, *, available=True, accepted=True):
        self.calls = []
        self.available, self.accepted = available, accepted
    def worker(self, action, payload):
        self.calls.append((action, payload))
        if action == "claim":
            return {"ok": True, "job": {"id": "job-fixture"}}
        if action == "get_job":
            return {"ok": self.available}
        if action == "mark_dispatched":
            return {"ok": self.accepted}
        return {"ok": True}


class Provider:
    def __init__(self, error=None):
        self.count, self.hangups, self.error = 0, [], error
    def start_call(self, job):
        self.count += 1
        if self.error:
            raise self.error
        return {"provider_call_id": "CA" + "1" * 32}
    def hangup(self, sid):
        self.hangups.append(sid)


def test_recheck_prevents_dispatch_after_contact_or_permission_changed():
    repo, provider = Repo(available=False), Provider()
    assert dispatch_once(settings(), repo, provider)["code"] == "TARGET_CHANGED"
    assert provider.count == 0


@pytest.mark.parametrize("error,code,final_action", [(CallRejected("safe"), "PROVIDER_REJECTED", "mark_failed"), (UnknownCall("safe"), "DISPATCH_UNKNOWN", "mark_unknown"), (RuntimeError("private payload must not leak"), "DISPATCH_UNKNOWN", "mark_unknown")])
def test_dispatch_never_retries_provider(error, code, final_action):
    repo, provider = Repo(), Provider(error)
    assert dispatch_once(settings(), repo, provider) == {"ok": False, "code": code}
    assert provider.count == 1
    assert repo.calls[-1][0] == final_action


def test_unconfirmed_storage_stops_known_call():
    repo, provider = Repo(accepted=False), Provider()
    assert dispatch_once(settings(), repo, provider)["code"] == "DISPATCH_UNKNOWN"
    assert provider.count == 1 and len(provider.hangups) == 1


def test_accepted_not_claimed_as_completed():
    assert dispatch_once(settings(), Repo(), Provider()) == {"ok": True, "code": "PROVIDER_ACCEPTED"}


def test_repository_masks_database_exception_and_never_retries():
    class BrokenDB:
        calls = 0
        def rpc(self, function, params):
            self.calls += 1
            raise RuntimeError("private customer data and secret")
    db = BrokenDB()
    result = VoiceRepository(db).action("alice", "enqueue", {})
    assert result["code"] == "NOT_READY" and not result["ok"]
    assert "private" not in str(result) and db.calls == 1


def test_repository_preserves_actor_for_authoritative_rpc_check():
    class DB:
        def rpc(self, function, params):
            assert function == "oasis_voice_action"
            assert params["p_current_user_id"] == "alice"
            return {"ok": True, "rows": []}
    assert VoiceRepository(DB()).action("alice", "candidates")["ok"]


@pytest.mark.parametrize("action", ["create_campaign", "list_campaigns", "campaign_jobs", "campaign_stats", "start_campaign", "pause_campaign", "cancel_campaign", "legacy_jobs"])
def test_campaign_repository_routes_to_authorized_campaign_rpc(action):
    class DB:
        def rpc(self, function, params):
            assert function == "oasis_voice_campaign_action"
            assert params == {"p_current_user_id": "alice", "p_action": action, "p_payload": {"request_id": "synthetic-request"}}
            return {"ok": True, "rows": []}
    assert VoiceRepository(DB()).action("alice", action, {"request_id": "synthetic-request"})["ok"]


def test_campaign_rpc_failure_does_not_fall_back_to_legacy_mutation():
    class DB:
        calls = 0
        def rpc(self, function, params):
            self.calls += 1
            assert function == "oasis_voice_campaign_action"
            raise RuntimeError("private provider details")
    db = DB()
    result = VoiceRepository(db).action("alice", "start_campaign", {})
    assert result["code"] == "NOT_READY" and db.calls == 1
    assert "private provider" not in str(result)


def test_repository_rejects_unknown_campaign_action_before_cloud_access():
    class DB:
        def rpc(self, *args):
            raise AssertionError("unknown action must not reach RPC")
    assert VoiceRepository(DB()).action("alice", "auto_retry_all_calls")["code"] == "INVALID_INPUT"
