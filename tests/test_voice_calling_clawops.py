import asyncio
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest

from voice_calling import VoiceSettings
from voice_calling_clawops import (
    CLAWOPS_ACCOUNT_ID_RE, CLAWOPS_CALL_ID_RE, CLAWOPS_STREAM_ID_RE,
    ClawOpsVoiceProvider, clawops_provider_call_id, clawops_raw_call_id,
    validate_clawops_signature,
)
from voice_calling_provider import CallRejected, UnknownCall, create_voice_provider
from voice_calling_worker import dispatch_once


JOB_ID = "11111111-1111-4111-8111-111111111111"
ACCOUNT = "AC" + "unit_test_00"
CALL_ID = "CA" + "unit_test_01"
# Format-only synthetic fixtures: every provider request uses MockTransport.
SYNTHETIC_MOBILE_PARTS = ("010", "0000", "0001")
SYNTHETIC_DESTINATION = "+82" + "".join(("10", *SYNTHETIC_MOBILE_PARTS[1:]))
SYNTHETIC_DOMESTIC_DESTINATION = "".join(SYNTHETIC_MOBILE_PARTS)
SYNTHETIC_FORMATTED_DESTINATION = "-".join(SYNTHETIC_MOBILE_PARTS)
SYNTHETIC_CALLER = "+82" + "".join(("70", "0000", "0000"))
SYNTHETIC_DOMESTIC_CALLER = "0" + SYNTHETIC_CALLER[3:]
SYNTHETIC_UNSUPPORTED_DESTINATION = "+1" + "".join(("202", "555", "0100"))
JOB = {"id": JOB_ID, "phone_e164": SYNTHETIC_DESTINATION}


def config(**changes):
    values = {
        "enabled": True, "provider": "clawops",
        "clawops_account_id": ACCOUNT,
        "clawops_api_key": "synthetic-test-" + "k" * 24,
        "clawops_signing_secret": "synthetic-signing-" + "s" * 32,
        "clawops_billing_confirmed": True,
        "clawops_live_verified": False,
        "clawops_test_numbers": (SYNTHETIC_DESTINATION,),
        "caller_id": SYNTHETIC_CALLER, "public_base_url": "https://voice.example.test",
        "request_timeout_seconds": 15, "daily_limit": 20, "readiness": lambda: {"ready": True},
    }
    values.update(changes)
    return SimpleNamespace(**values)


def provider_with_response(settings=None, *, status=201, payload=None):
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(status, json={"callId": CALL_ID} if payload is None else payload)
    client = httpx.Client(transport=httpx.MockTransport(respond))
    return ClawOpsVoiceProvider(settings or config(), client=client), seen


def test_dispatch_contract_only_documented_fields_no_extra_requests():
    provider, seen = provider_with_response()
    assert provider.start_call(JOB) == {"provider_call_id": "clawops:" + CALL_ID}
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == f"https://api.claw-ops.com/v1/accounts/{ACCOUNT}/calls"
    assert request.headers["authorization"] == "Bearer " + config().clawops_api_key
    assert request.headers["content-type"] == "application/json"
    assert json.loads(request.content) == {
        "To": SYNTHETIC_DOMESTIC_DESTINATION, "From": SYNTHETIC_DOMESTIC_CALLER,
        "Url": f"https://voice.example.test/voice/clawops/voiceml/{JOB_ID}",
        "StatusCallback": f"https://voice.example.test/voice/clawops/status/{JOB_ID}",
        "StatusCallbackEvent": "initiated ringing answered completed", "Timeout": 25,
    }


@pytest.mark.parametrize("changes,error", [
    ({"enabled": False}, "CALLING_DISABLED"),
    ({"enabled": "true"}, "CALLING_DISABLED"),
    ({"provider": "twilio"}, "PROVIDER_NOT_CONFIGURED"),
    ({"clawops_account_id": "../ACotheraccount"}, "PROVIDER_NOT_CONFIGURED"),
    ({"clawops_account_id": "ACshort"}, "PROVIDER_NOT_CONFIGURED"),
    ({"clawops_api_key": ""}, "PROVIDER_NOT_CONFIGURED"),
    ({"clawops_api_key": "k" * 32 + "\r\nInjected: header"}, "PROVIDER_NOT_CONFIGURED"),
    ({"readiness": lambda: {"ready": False}}, "PROVIDER_NOT_READY"),
    ({"readiness": lambda: {"ready": "true"}}, "PROVIDER_NOT_READY"),
    ({"clawops_billing_confirmed": False}, "PROVIDER_NOT_READY"),
    ({"clawops_billing_confirmed": "true"}, "PROVIDER_NOT_READY"),
    ({"caller_id": SYNTHETIC_DESTINATION}, "INVALID_PHONE_CONFIGURATION"),
    ({"caller_id": SYNTHETIC_UNSUPPORTED_DESTINATION}, "INVALID_PHONE_CONFIGURATION"),
    ({"clawops_test_numbers": ()}, "TEST_DESTINATION_REQUIRED"),
    ({"clawops_test_numbers": (SYNTHETIC_DESTINATION + "2",)}, "TEST_DESTINATION_REQUIRED"),
    ({"clawops_test_numbers": SYNTHETIC_DESTINATION}, "TEST_DESTINATION_REQUIRED"),
    ({"clawops_test_numbers": (SYNTHETIC_DESTINATION, "invalid")}, "TEST_DESTINATION_REQUIRED"),
    ({"clawops_test_numbers": (SYNTHETIC_DESTINATION,) * 6}, "TEST_DESTINATION_REQUIRED"),
    ({"clawops_test_numbers": (), "clawops_live_verified": "true"}, "TEST_DESTINATION_REQUIRED"),
    ({"public_base_url": "http://voice.example.test"}, "INVALID_JOB_CONFIGURATION"),
    ({"public_base_url": "https://voice.example.test/path"}, "INVALID_JOB_CONFIGURATION"),
])
def test_dispatch_gates_make_no_requests(changes, error):
    provider, seen = provider_with_response(config(**changes))
    with pytest.raises(CallRejected, match="^" + error + "$"):
        provider.start_call(JOB)
    assert seen == []


def test_destination_normalized_before_exact_allowlist_comparison():
    provider, seen = provider_with_response()
    provider.start_call({**JOB, "phone_e164": SYNTHETIC_FORMATTED_DESTINATION})
    assert len(seen) == 1
    provider, seen = provider_with_response(config(clawops_test_numbers=(SYNTHETIC_FORMATTED_DESTINATION,)))
    provider.start_call(JOB)
    assert len(seen) == 1


def test_live_verified_removes_test_allowlist_gate_but_not_readiness():
    provider, seen = provider_with_response(config(clawops_live_verified=True, clawops_test_numbers=()))
    provider.start_call(JOB)
    assert len(seen) == 1
    provider, seen = provider_with_response(config(clawops_live_verified=True, readiness=lambda: {"ready": False}))
    with pytest.raises(CallRejected):
        provider.start_call(JOB)
    assert not seen


@pytest.mark.parametrize("job", [{**JOB, "id": "../../outside"}, {**JOB, "phone_e164": SYNTHETIC_UNSUPPORTED_DESTINATION}, {**JOB, "phone_e164": ""}])
def test_bad_job_does_not_dispatch(job):
    provider, seen = provider_with_response()
    with pytest.raises(CallRejected):
        provider.start_call(job)
    assert not seen


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 410, 422, 429])
def test_explicit_rejections_sanitized_and_not_retried(status):
    provider, seen = provider_with_response(status=status, payload={"error": "private customer data"})
    with pytest.raises(CallRejected, match="^PROVIDER_REJECTED$"):
        provider.start_call(JOB)
    assert len(seen) == 1


@pytest.mark.parametrize("status,payload", [
    (500, {"error": "private"}), (503, {}), (302, {}), (200, {}), (408, {}), (499, {}),
    (201, {}), (201, []), (201, {"callId": None}), (201, {"callId": "CAshort"}),
    (201, {"callId": "clawops:" + CALL_ID}), (201, {"callId": "CA" + "a" * 101}),
    (201, {"callId": "CA12345678/../../other"}),
])
def test_ambiguous_dispatch_never_retried(status, payload):
    provider, seen = provider_with_response(status=status, payload=payload)
    with pytest.raises(UnknownCall, match="^PROVIDER_RESPONSE_UNKNOWN$"):
        provider.start_call(JOB)
    assert len(seen) == 1


@pytest.mark.parametrize("kind", ["timeout", "bad_json", "redirect"])
def test_network_errors_invalid_json_and_redirect_are_not_retried(kind):
    seen = []
    def respond(request):
        seen.append(request)
        if kind == "timeout":
            raise httpx.ReadTimeout("private headers and customer", request=request)
        if kind == "redirect":
            return httpx.Response(307, headers={"Location": "https://unexpected.example.test"})
        return httpx.Response(201, content=b"not-json-private")
    client = httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True)
    provider = ClawOpsVoiceProvider(config(), client=client)
    with pytest.raises(UnknownCall, match="^PROVIDER_RESPONSE_UNKNOWN$"):
        provider.start_call(JOB)
    assert len(seen) == 1


def test_hangup_works_when_disabled_or_not_ready():
    provider, seen = provider_with_response(config(enabled=False, clawops_billing_confirmed=False, readiness=lambda: {"ready": False}), status=200)
    provider.hangup("clawops:" + CALL_ID)
    assert len(seen) == 1
    assert str(seen[0].url).endswith("/calls/" + CALL_ID)
    assert json.loads(seen[0].content) == {"Status": "completed"}


@pytest.mark.parametrize("value", [CALL_ID, "twilio:" + CALL_ID, "clawops:../call", "clawops:CAshort", "clawops:" + CALL_ID + "\n", None])
def test_hangup_requires_valid_namespaced_id(value):
    provider, seen = provider_with_response(status=200)
    with pytest.raises(CallRejected, match="^INVALID_CALL_ID$"):
        provider.hangup(value)
    assert not seen


@pytest.mark.parametrize("status", [201, 204, 302, 400, 404, 503])
def test_hangup_requires_200_without_retries(status):
    provider, seen = provider_with_response(status=status)
    with pytest.raises(UnknownCall, match="^PROVIDER_HANGUP_UNCONFIRMED$"):
        provider.hangup("clawops:" + CALL_ID)
    assert len(seen) == 1


@pytest.mark.parametrize("pattern,prefix", [(CLAWOPS_CALL_ID_RE, "CA"), (CLAWOPS_ACCOUNT_ID_RE, "AC"), (CLAWOPS_STREAM_ID_RE, "MZ")])
def test_identifier_bounds(pattern, prefix):
    for length in (8, 100):
        assert pattern.fullmatch(prefix + "a" * length)
    for suffix in ("a" * 7, "a" * 101, "12345678/", "12345678\n", "12345678?"):
        assert not pattern.fullmatch(prefix + suffix)
    assert pattern.fullmatch(prefix + "Az_09-xX")


def test_namespaced_id_round_trip():
    assert clawops_raw_call_id(clawops_provider_call_id(CALL_ID)) == CALL_ID
    with pytest.raises(ValueError):
        clawops_provider_call_id("clawops:" + CALL_ID)


PATH = f"/voice/clawops/status/{JOB_ID}"
PARAMS = {"CallId": CALL_ID, "AccountId": ACCOUNT, "CallStatus": "answered", "Timestamp": "2026-10-03T01:00:00Z", "FutureField": "한글 값"}


def signed(settings=None, path=PATH, params=None):
    settings = settings or config()
    params = PARAMS if params is None else params
    data = settings.public_base_url + path + "".join(k + params[k] for k in sorted(params))
    return base64.b64encode(hmac.new(settings.clawops_signing_secret.encode(), data.encode(), hashlib.sha256).digest()).decode()


def test_signature_exact_public_url_all_fields_and_utf8():
    signature = signed()
    assert validate_clawops_signature(config(), PATH, dict(reversed(list(PARAMS.items()))), signature)
    assert not validate_clawops_signature(config(), PATH, {**PARAMS, "CallStatus": "completed"}, signature)
    assert not validate_clawops_signature(config(), PATH + "/", PARAMS, signature)
    assert not validate_clawops_signature(config(public_base_url="https://other.example.test"), PATH, PARAMS, signature)
    assert not validate_clawops_signature(config(), PATH, PARAMS, signature[:-2] + "A=")


@pytest.mark.parametrize("changes", [
    {"provider": "twilio"}, {"clawops_account_id": "ACshort"},
    {"clawops_signing_secret": "x" * 31}, {"clawops_signing_secret": None},
    {"public_base_url": "http://voice.example.test"},
])
def test_signature_rejects_invalid_configuration(changes):
    assert not validate_clawops_signature(config(**changes), PATH, PARAMS, signed())


@pytest.mark.parametrize("params,signature", [(PARAMS, "é" * 44), (PARAMS, None), (PARAMS, "x" * 1000), ({"CallId": 1}, "x" * 44), ([], "x" * 44)])
def test_signature_malformed_inputs_fail_closed(params, signature):
    assert not validate_clawops_signature(config(), PATH, params, signature)


def test_signature_rejects_other_account_even_if_correctly_signed():
    params = {**PARAMS, "AccountId": "ACdifferent_account"}
    assert not validate_clawops_signature(config(), PATH, params, signed(params=params))


def test_voiceml_signature_does_not_require_timestamp():
    path = f"/voice/clawops/voiceml/{JOB_ID}"
    params = {"CallId": CALL_ID, "From": SYNTHETIC_DOMESTIC_CALLER, "To": SYNTHETIC_DOMESTIC_DESTINATION}
    assert validate_clawops_signature(config(), path, params, signed(path=path, params=params))
    assert not validate_clawops_signature(config(), "/voice/media/" + JOB_ID, params, signed(path=path, params=params))


def test_factory_and_real_settings_support_clawops_without_twilio_credentials():
    values = vars(config()).copy()
    values.pop("readiness")
    settings = VoiceSettings(**values, openai_api_key="synthetic-openai-no-network", stream_ticket_secret="t" * 32)
    assert settings.readiness()["ready"]
    assert isinstance(create_voice_provider(settings), ClawOpsVoiceProvider)
    provider, seen = provider_with_response(settings)
    assert provider.start_call(JOB)["provider_call_id"] == "clawops:" + CALL_ID
    assert len(seen) == 1


class WorkerRepository:
    def __init__(self, accepted=True):
        self.requests = []
        self.accepted = accepted

    def worker(self, action, payload):
        self.requests.append((action, payload))
        if action == "claim":
            return {"ok": True, "job": {**JOB, "provider": "clawops"}}
        if action == "mark_dispatched":
            return {"ok": self.accepted}
        return {"ok": True}


class WorkerCarrier:
    def __init__(self, error=None):
        self.started = 0
        self.hung_up = []
        self.error = error

    def start_call(self, job):
        self.started += 1
        if self.error:
            raise self.error
        return {"provider_call_id": "clawops:" + CALL_ID}

    def hangup(self, call_id):
        self.hung_up.append(call_id)


@pytest.mark.parametrize("error,code,last_action", [
    (None, "PROVIDER_ACCEPTED", "mark_dispatched"),
    (CallRejected("PROVIDER_REJECTED"), "PROVIDER_REJECTED", "mark_failed"),
    (UnknownCall("PROVIDER_RESPONSE_UNKNOWN"), "DISPATCH_UNKNOWN", "mark_unknown"),
])
def test_worker_scopes_all_clawops_operations_and_does_not_redial(error, code, last_action):
    repo, carrier = WorkerRepository(), WorkerCarrier(error)
    assert dispatch_once(config(), repo, carrier)["code"] == code
    assert carrier.started == 1
    assert repo.requests[-1][0] == last_action
    assert all(payload["provider"] == "clawops" for _, payload in repo.requests)
    if error is None:
        assert repo.requests[-1][1]["provider_call_id"] == "clawops:" + CALL_ID


def test_worker_failed_persistence_hangs_up_namespaced_call_and_marks_unknown():
    repo, carrier = WorkerRepository(accepted=False), WorkerCarrier()
    assert dispatch_once(config(), repo, carrier)["code"] == "DISPATCH_UNKNOWN"
    assert carrier.started == 1 and carrier.hung_up == ["clawops:" + CALL_ID]
    assert repo.requests[-1] == ("mark_unknown", {
        "job_id": JOB_ID, "provider": "clawops", "provider_call_id": "clawops:" + CALL_ID,
    })


def test_worker_not_ready_does_not_claim_or_dispatch():
    repo, carrier = WorkerRepository(), WorkerCarrier()
    assert dispatch_once(config(readiness=lambda: {"ready": False}), repo, carrier)["code"] == "CALLING_DISABLED_OR_NOT_READY"
    assert not repo.requests and carrier.started == 0


def test_worker_internal_mode_claim_uses_normalized_test_allowlist():
    repo, carrier = WorkerRepository(), WorkerCarrier()
    dispatch_once(config(clawops_test_numbers=(SYNTHETIC_FORMATTED_DESTINATION,)), repo, carrier)
    assert repo.requests[0] == ("claim", {
        "daily_limit": 20, "provider": "clawops", "test_phone_allowlist": [SYNTHETIC_DESTINATION],
    })


def test_worker_verified_mode_does_not_add_test_claim_filter():
    repo, carrier = WorkerRepository(), WorkerCarrier()
    dispatch_once(config(clawops_live_verified=True), repo, carrier)
    assert repo.requests[0] == ("claim", {"daily_limit": 20, "provider": "clawops"})


@pytest.mark.parametrize("legacy_provider", [None, "twilio"])
def test_worker_missing_carrier_migration_never_dispatches(legacy_provider):
    class LegacyRepository(WorkerRepository):
        def worker(self, action, payload):
            result = super().worker(action, payload)
            if action == "claim":
                result["job"].pop("provider")
                if legacy_provider is not None:
                    result["job"]["provider"] = legacy_provider
            return result

    repo, carrier = LegacyRepository(), WorkerCarrier()
    assert dispatch_once(config(), repo, carrier)["code"] == "CARRIER_MIGRATION_REQUIRED"
    assert carrier.started == 0
    assert [action for action, _ in repo.requests] == ["claim", "mark_failed"]


@pytest.mark.parametrize("storage_failure", [None, "rejected", "exception"])
def test_keypad_optout_survives_clear_write_failure_and_reports_storage_failure(storage_failure):
    from voice_calling_gateway import BridgeProtocolError, CallBridge

    class DisconnectedSocket:
        def __init__(self):
            self.clear_attempts = 0

        async def receive_text(self):
            return json.dumps({"event": "dtmf", "streamSid": "MZsynthetic_01", "dtmf": {"digit": "9"}})

        async def send_json(self, event):
            assert event["event"] == "clear"
            self.clear_attempts += 1
            raise RuntimeError("synthetic disconnected socket")

    class OptoutRepository:
        def __init__(self):
            self.results = []

        def worker(self, action, payload):
            assert action == "result"
            self.results.append(payload)
            if storage_failure == "exception":
                raise RuntimeError("synthetic storage unavailable")
            return {"ok": storage_failure is None}

    async def check():
        socket, repo = DisconnectedSocket(), OptoutRepository()
        bridge = CallBridge(socket, None, repo, config(), JOB, "MZsynthetic_01", "clawops:" + CALL_ID)
        bridge.session_ready.set()
        if storage_failure:
            with pytest.raises(BridgeProtocolError, match="^DO_NOT_CALL_SAVE_FAILED$"):
                await bridge.receive_customer()
        else:
            await bridge.receive_customer()
            assert bridge.finished.is_set()
            assert socket.clear_attempts == 1
        assert len(repo.results) == 1
        assert repo.results[0]["outcome"] == "do_not_call"
        assert repo.results[0]["provider_call_id"] == "clawops:" + CALL_ID
        assert repo.results[0]["idempotency_key"].endswith(":dtmf-optout")

    asyncio.run(check())
