from dataclasses import replace
from urllib.parse import parse_qs
from xml.etree import ElementTree as ET

import httpx
import pytest
from starlette.datastructures import FormData
from twilio.request_validator import RequestValidator

from voice_calling import VoiceSettings
from voice_calling_provider import (
    CallRejected, UnknownCall, TwilioVoiceProvider, issue_stream_ticket,
    verify_stream_ticket, validate_twilio_signature, public_endpoint,
)

JOB_ID = "11111111-1111-4111-8111-111111111111"
NONCE = "22222222-2222-4222-8222-222222222222"
CALL_ID = "CA" + "1" * 32
# Synthetic format-only endpoints; requests are handled by MockTransport below.
SYNTHETIC_CALLER_ID = "+82" + "".join(("10", "0000", "0000"))
SYNTHETIC_DESTINATION = "+82" + "".join(("10", "0000", "0001"))
SYNTHETIC_USERINFO_ORIGIN = "https://" + "u:p" + "@" + "example.test"
CONFIG = VoiceSettings(enabled=True, twilio_account_sid="AC" + "0" * 32,
                       twilio_auth_token="test-" + "x" * 32,
                       caller_id=SYNTHETIC_CALLER_ID, public_base_url="https://voice.example.test",
                       openai_api_key="test-not-a-key", stream_ticket_secret="s" * 40,
                       )
JOB = {"id": JOB_ID, "dispatch_nonce": NONCE, "phone_e164": SYNTHETIC_DESTINATION}


def test_ticket_scope_expiry_tamper():
    token = issue_stream_ticket(CONFIG, JOB_ID, NONCE, now=1000)
    assert verify_stream_ticket(CONFIG, token, JOB_ID, now=1001) == NONCE
    for bad_token, bad_job, now in [(token, NONCE, 1001), (token, JOB_ID, 1600),
                                    (token[:-1] + "Z", JOB_ID, 1001), (token, JOB_ID, 0)]:
        with pytest.raises(ValueError, match="INVALID_STREAM_TICKET"):
            verify_stream_ticket(CONFIG, bad_token, bad_job, now=now)


def test_signatures_cover_all_form_fields_and_duplicates():
    path = f"/voice/twilio/status/{JOB_ID}"
    data = FormData([("CallSid", CALL_ID), ("AccountSid", CONFIG.twilio_account_sid),
                     ("FutureParameter", "alpha"), ("FutureParameter", "beta")])
    signature = RequestValidator(CONFIG.twilio_auth_token).compute_signature(public_endpoint(CONFIG, path), data)
    assert validate_twilio_signature(CONFIG, path, data, signature)
    assert not validate_twilio_signature(CONFIG, path, FormData({"CallSid": CALL_ID}), signature)
    assert not validate_twilio_signature(CONFIG, path, data, "bad-signature")
    assert not validate_twilio_signature(replace(CONFIG, public_base_url="https://other.example.test"), path, data, signature)


def test_websocket_signature_canonical_trailing_variant():
    path = f"/voice/media/{JOB_ID}"
    for suffix in ("", "/"):
        signature = RequestValidator(CONFIG.twilio_auth_token).compute_signature(public_endpoint(CONFIG, path, websocket=True) + suffix, {})
        assert validate_twilio_signature(CONFIG, path, {}, signature, websocket=True)
    assert not validate_twilio_signature(CONFIG, path, {}, signature)


@pytest.mark.parametrize("url", ["http://voice.example.test", "https://localhost", SYNTHETIC_USERINFO_ORIGIN, "https://example.test/?x=y", "https://example.test:8443", "https://example.test/path"])
def test_disallow_ambiguous_signing_origin(url):
    with pytest.raises(ValueError):
        public_endpoint(replace(CONFIG, public_base_url=url), f"/voice/media/{JOB_ID}")


def test_disabled_never_issues_a_provider_request():
    def unexpected(_request):
        pytest.fail("No network permitted while disabled")
    provider = TwilioVoiceProvider(replace(CONFIG, enabled=False), client=httpx.Client(transport=httpx.MockTransport(unexpected)))
    with pytest.raises(CallRejected, match="CALLING_DISABLED"):
        provider.start_call(JOB)


def test_provider_request_has_bounded_call_signed_stream_no_recording():
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(201, json={"sid": CALL_ID})
    provider = TwilioVoiceProvider(CONFIG, client=httpx.Client(transport=httpx.MockTransport(respond)))
    assert provider.start_call(JOB) == {"provider_call_id": CALL_ID}
    assert len(seen) == 1
    assert str(seen[0].url) == f"https://api.twilio.com/2010-04-01/Accounts/{CONFIG.twilio_account_sid}/Calls.json"
    data = parse_qs(seen[0].content.decode())
    assert data["TimeLimit"] == ["180"] and data["Record"] == ["false"]
    assert data["StatusCallbackEvent"] == ["initiated", "ringing", "answered", "completed"]
    assert data["StatusCallback"] == [f"https://voice.example.test/voice/twilio/status/{JOB_ID}"]
    root = ET.fromstring(data["Twiml"][0])
    stream = root.find("Connect/Stream")
    assert stream.attrib["url"] == f"wss://voice.example.test/voice/media/{JOB_ID}"
    assert "?" not in stream.attrib["url"]
    assert verify_stream_ticket(CONFIG, stream.find("Parameter").attrib["value"], JOB_ID) == NONCE
    assert root.find("Hangup") is not None


@pytest.mark.parametrize("status,payload", [(500, {"message": "sensitive"}), (302, {}), (201, {"sid": "not-a-sid"})])
def test_ambiguous_responses_are_not_retried_or_echoed(status, payload):
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(status, json=payload)
    provider = TwilioVoiceProvider(CONFIG, client=httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(UnknownCall) as error:
        provider.start_call(JOB)
    assert len(seen) == 1
    assert "sensitive" not in str(error.value)


def test_network_timeout_is_ambiguous_and_never_retried():
    seen = []
    def respond(request):
        seen.append(request)
        raise httpx.ReadTimeout("private request must not be surfaced")
    provider = TwilioVoiceProvider(CONFIG, client=httpx.Client(transport=httpx.MockTransport(respond)))
    with pytest.raises(UnknownCall, match="^PROVIDER_RESPONSE_UNKNOWN$"):
        provider.start_call(JOB)
    assert len(seen) == 1


def test_hangup_still_works_after_emergency_disable():
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"sid": CALL_ID})
    provider = TwilioVoiceProvider(replace(CONFIG, enabled=False), client=httpx.Client(transport=httpx.MockTransport(respond)))
    provider.hangup(CALL_ID)
    assert parse_qs(seen[0].content.decode()) == {"Status": ["completed"]}
    with pytest.raises(CallRejected, match="INVALID_CALL_ID"):
        provider.hangup("../../other-account")
    assert len(seen) == 1
