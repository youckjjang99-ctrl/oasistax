"""Offline ClawOps HTTP/media security and shared Realtime bridge regressions."""
import asyncio
import base64
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from voice_calling import VoiceSettings
from voice_calling_gateway import create_app
from voice_calling_provider import issue_stream_ticket, verify_stream_ticket


JOB_ID = "11111111-1111-4111-8111-111111111111"
OTHER_JOB_ID = "33333333-3333-4333-8333-333333333333"
NONCE = "22222222-2222-4222-8222-222222222222"
CALL_ID = "CAoffline_test_call_01"
PROVIDER_CALL_ID = "clawops:" + CALL_ID
STREAM_ID = "MZoffline_test_stream_01"
ACCOUNT_ID = "ACoffline_test_account"
# Synthetic, format-only numbers. Nothing in this file dials a phone.
SYNTHETIC_DESTINATION = "+82" + "".join(("10", "0000", "0001"))
SYNTHETIC_CALLER_ID = "+82" + "".join(("70", "0000", "0000"))
SYNTHETIC_OTHER_DESTINATION = "+82" + "".join(("10", "0000", "0002"))
SYNTHETIC_OTHER_CALLER_ID = "+82" + "".join(("70", "0000", "0002"))
AUDIO = base64.b64encode(b"\xff" * 800).decode()
CONFIG = VoiceSettings(
    enabled=True, provider="clawops", caller_id=SYNTHETIC_CALLER_ID,
    public_base_url="https://voice.example.test", openai_api_key="not-a-real-key",
    stream_ticket_secret="offline-ticket-" + "s" * 40,
    clawops_account_id=ACCOUNT_ID, clawops_api_key="offline-api-" + "k" * 32,
    clawops_signing_secret="offline-signing-" + "s" * 32,
    clawops_billing_confirmed=True, clawops_test_numbers=(SYNTHETIC_DESTINATION,),
)
VOICE_PATH = f"/voice/clawops/voiceml/{JOB_ID}"
STATUS_PATH = f"/voice/clawops/status/{JOB_ID}"
MEDIA_PATH = f"/voice/clawops/media/{JOB_ID}"
SCOPE = {"job_id": JOB_ID, "provider_call_id": PROVIDER_CALL_ID, "provider": "clawops"}


@pytest.fixture(autouse=True)
def forbid_external_transports(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("This suite must never use an external transport")

    # TestClient uses its own ASGI transport, not HTTPTransport.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr("voice_calling_gateway._default_upstream", forbidden)


def domestic_number(synthetic_e164):
    return "0" + synthetic_e164[3:]


def callback_form(**changes):
    form = {
        "AccountId": ACCOUNT_ID, "CallId": CALL_ID, "Direction": "outbound",
        "From": domestic_number(SYNTHETIC_CALLER_ID), "To": domestic_number(SYNTHETIC_DESTINATION),
        "CallStatus": "answered", "Timestamp": datetime.now(timezone.utc).isoformat(),
        "Duration": "25", "FutureVendorField": "also-signed",
    }
    form.update(changes)
    return form


def signature(path, form, config=CONFIG, *, base_url=None):
    # Independent implementation of the vendor's documented HMAC wire format.
    message = (base_url or config.public_base_url) + path
    message += "".join(key + form[key] for key in sorted(form))
    return base64.b64encode(hmac.new(
        config.clawops_signing_secret.encode(), message.encode(), hashlib.sha256,
    ).digest()).decode()


def post_signed(client, path, form=None, *, config=CONFIG, headers=None):
    form = callback_form() if form is None else form
    return client.post(path, data=form, headers={
        "x-signature": signature(path, form, config), **(headers or {}),
    })


def start_message(*, ticket=None):
    return {"event": "start", "start": {
        "accountId": ACCOUNT_ID, "callId": CALL_ID, "streamId": STREAM_ID,
        "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        "customParameters": {"ticket": ticket or issue_stream_ticket(CONFIG, JOB_ID, NONCE)},
    }}


class FakeRepository:
    def __init__(self, *, bound=True):
        self.calls = []
        self.fail_actions = set()
        self.raise_actions = set()
        self.connected = False
        self.connect_job_override = None
        self.job = {
            "id": JOB_ID, "provider": "clawops", "dispatch_nonce": NONCE,
            "dispatch_at": datetime.now(timezone.utc).isoformat(), "connected_at": None,
            "provider_call_id": PROVIDER_CALL_ID if bound else None,
            "phone_e164": SYNTHETIC_DESTINATION, "company_name": "Synthetic offline company",
            "representative_name": "", "address": "",
        }

    def worker(self, action, payload):
        self.calls.append((action, deepcopy(payload)))
        if action in self.raise_actions:
            raise RuntimeError("Sensitive repository detail")
        if action in self.fail_actions:
            return {"ok": False}
        if action == "get_job":
            assert payload == SCOPE
            return {"ok": True, "job": deepcopy(self.job)}
        if action == "mark_dispatched":
            assert payload == SCOPE
            if self.job["provider_call_id"] not in (None, PROVIDER_CALL_ID):
                return {"ok": False}
            self.job["provider_call_id"] = PROVIDER_CALL_ID
            return {"ok": True, "job": deepcopy(self.job)}
        if action == "connect":
            assert payload == {**SCOPE, "dispatch_nonce": NONCE}
            if self.connected:
                return {"ok": False}
            self.connected = True
            self.job["connected_at"] = datetime.now(timezone.utc).isoformat()
            job = self.connect_job_override if self.connect_job_override is not None else self.job
            return {"ok": True, "job": deepcopy(job)}
        return {"ok": True}


class FakeProvider:
    def __init__(self):
        self.hung_up = []

    def hangup(self, call_id):
        self.hung_up.append(call_id)


class FakeUpstream:
    def __init__(self, *, respond_to_customer=False):
        self.events = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.response_count = 0
        self.respond_to_customer = respond_to_customer

    def __aiter__(self):
        return self

    async def __anext__(self):
        return json.dumps(await self.events.get())

    async def send(self, raw):
        event = json.loads(raw)
        self.sent.append(event)
        if event["type"] == "session.update":
            self.events.put_nowait({"type": "session.updated"})
        elif event["type"] == "response.create":
            self.response_count += 1
            self.events.put_nowait({"type": "response.output_audio.delta",
                                   "item_id": f"item{self.response_count}", "delta": AUDIO})
            self.events.put_nowait({"type": "response.done", "response": {
                "status": "completed", "output": [{"type": "message"}],
            }})
        elif event["type"] == "input_audio_buffer.append" and self.respond_to_customer:
            self.events.put_nowait({"type": "input_audio_buffer.speech_started"})
            self.events.put_nowait({"type": "response.function_call_arguments.done",
                "call_id": "tool_result_01", "name": "save_call_result", "arguments": json.dumps({
                    "outcome": "declined", "visit_at": "", "address": "",
                    "summary": "No interest", "customer_confirmed": True,
                })})
            self.events.put_nowait({"type": "response.done", "response": {
                "status": "completed", "output": [{"type": "function_call"}],
            }})


class FakeConnector:
    def __init__(self, repo, *, respond_to_customer=False):
        self.repo = repo
        self.connections = []
        self.respond_to_customer = respond_to_customer
        self.connect_snapshots = []

    @asynccontextmanager
    async def __call__(self, _config):
        # Authorization and atomic one-use connect must precede any AI access.
        assert self.repo.connected
        assert self.repo.calls[-1] == ("connect", {**SCOPE, "dispatch_nonce": NONCE})
        self.connect_snapshots.append(deepcopy(self.repo.calls))
        upstream = FakeUpstream(respond_to_customer=self.respond_to_customer)
        self.connections.append(upstream)
        try:
            yield upstream
        finally:
            upstream.closed = True


def fixture_app(config=CONFIG, *, bound=True, respond_to_customer=False):
    repo, provider = FakeRepository(bound=bound), FakeProvider()
    connector = FakeConnector(repo, respond_to_customer=respond_to_customer)
    return create_app(config, repo, provider, connector), repo, provider, connector


def assert_no_ai(provider, connector):
    assert connector.connections == []
    assert provider.hung_up == []


def assert_hangup_only(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(response.text)
    assert root.tag == "Response"
    assert [child.tag for child in root] == ["Hangup"]
    assert "ticket" not in response.text


def receive_audio_and_ack(ws):
    audio, mark = ws.receive_json(), ws.receive_json()
    assert audio == {"event": "media", "media": {"payload": AUDIO}}
    assert set(mark) == {"event", "mark"}
    assert mark["event"] == "mark"
    ws.send_json(mark)


def stop_message():
    return {"event": "stop", "stop": {"callId": CALL_ID, "accountId": ACCOUNT_ID}}


def test_signed_voiceml_binds_job_before_short_lived_ticket_and_stream_xml():
    app, repo, provider, connector = fixture_app(bound=False)
    with TestClient(app) as client:
        before = int(time.time())
        response = post_signed(client, VOICE_PATH, headers={"host": "untrusted.example"})
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        root = ET.fromstring(response.text)
        assert [child.tag for child in root] == ["Connect", "Hangup"]
        assert root.find("Connect").attrib == {}
        stream = root.find("Connect/Stream")
        assert stream.attrib == {"url": CONFIG.public_base_url.replace("https:", "wss:") + MEDIA_PATH,
                                 "track": "inbound"}
        parameter = stream.find("Parameter")
        assert parameter.attrib["name"] == "ticket"
        ticket = parameter.attrib["value"]
        assert verify_stream_ticket(CONFIG, ticket, JOB_ID) == NONCE
        assert before + 90 <= int(ticket.split(".")[3]) <= int(time.time()) + 90
    assert repo.calls == [("get_job", SCOPE), ("mark_dispatched", SCOPE)]
    assert repo.job["provider_call_id"] == PROVIDER_CALL_ID
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("case", ["unsigned", "forged", "wrong_account", "wrong_from", "inbound", "bad_call_id"])
def test_untrusted_voiceml_never_reads_database_or_opens_ai(case):
    app, repo, provider, connector = fixture_app(bound=False)
    form = callback_form()
    if case == "wrong_account":
        form["AccountId"] = "ACanother_test_account"
    elif case == "wrong_from":
        form["From"] = domestic_number(SYNTHETIC_OTHER_CALLER_ID)
    elif case == "inbound":
        form["Direction"] = "inbound"
    elif case == "bad_call_id":
        form["CallId"] = "../not-a-call"
    headers = {"x-signature": signature(VOICE_PATH, form)}
    if case == "unsigned":
        headers = {}
    elif case == "forged":
        form["FutureVendorField"] = "changed-after-signing"
    with TestClient(app) as client:
        response = client.post(VOICE_PATH, data=form, headers=headers)
        assert response.status_code in (400, 403)
    assert repo.calls == []
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("route", [VOICE_PATH, STATUS_PATH])
@pytest.mark.parametrize("case", ["duplicates", "query", "json", "oversized", "invalid_utf8"])
def test_malformed_callbacks_never_touch_storage(route, case):
    app, repo, provider, connector = fixture_app()
    form = callback_form()
    headers = {"content-type": "application/x-www-form-urlencoded", "x-signature": signature(route, form)}
    with TestClient(app) as client:
        if case == "duplicates":
            pairs = list(form.items()) + [("FutureVendorField", "second-value")]
            response = client.post(route, content=urlencode(pairs), headers=headers)
        elif case == "query":
            response = client.post(route + "?unexpected=1", data=form, headers=headers)
        elif case == "json":
            response = client.post(route, json=form)
        elif case == "oversized":
            response = client.post(route, content="x=" + "a" * 16385, headers=headers)
        else:
            response = client.post(route, content=b"x=\xff", headers=headers)
        assert response.status_code == (413 if case == "oversized" else 400)
    assert repo.calls == []
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("case", ["wrong_to", "wrong_job", "wrong_provider", "already_connected",
                                  "missing_job", "get_job_error", "bind_refused", "bind_error",
                                  "not_test_target", "stale_dispatch", "future_dispatch", "naive_dispatch"])
def test_signed_voiceml_fails_closed_before_issuing_ticket(case):
    app, repo, provider, connector = fixture_app(bound=False)
    form = callback_form()
    if case == "wrong_to":
        form["To"] = domestic_number(SYNTHETIC_OTHER_DESTINATION)
    elif case == "wrong_job":
        repo.job["id"] = OTHER_JOB_ID
    elif case == "wrong_provider":
        repo.job["provider"] = "twilio"
    elif case == "already_connected":
        repo.job["connected_at"] = datetime.now(timezone.utc).isoformat()
    elif case == "missing_job":
        repo.fail_actions.add("get_job")
    elif case == "get_job_error":
        repo.raise_actions.add("get_job")
    elif case == "bind_refused":
        repo.fail_actions.add("mark_dispatched")
    elif case == "bind_error":
        repo.raise_actions.add("mark_dispatched")
    elif case == "not_test_target":
        repo.job["phone_e164"] = SYNTHETIC_OTHER_DESTINATION
        form["To"] = domestic_number(SYNTHETIC_OTHER_DESTINATION)
    elif case == "stale_dispatch":
        repo.job["dispatch_at"] = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    elif case == "future_dispatch":
        repo.job["dispatch_at"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    else:
        repo.job["dispatch_at"] = datetime.now().isoformat()
    with TestClient(app) as client:
        assert_hangup_only(post_signed(client, VOICE_PATH, form))
    assert [action for action, _ in repo.calls] == (
        ["get_job", "mark_dispatched"] if case in ("bind_refused", "bind_error") else ["get_job"])
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("changes", [{"enabled": False}, {"clawops_billing_confirmed": False},
                                    {"clawops_signing_secret": ""}])
def test_disabled_or_unready_voiceml_cannot_read_job(changes):
    config = replace(CONFIG, **changes)
    app, repo, provider, connector = fixture_app(config)
    with TestClient(app) as client:
        response = post_signed(client, VOICE_PATH, config=config)
        if changes.get("clawops_signing_secret") == "":
            assert response.status_code == 403
        else:
            assert_hangup_only(response)
    assert repo.calls == []
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("state,mapped,sequence", [("answered", "in-progress", 2), ("rejected", "failed", 3),
                                                  ("completed", "completed", 3), ("ringing", "ringing", 1)])
def test_signed_fresh_status_maps_provider_state_and_namespace(state, mapped, sequence):
    app, repo, provider, connector = fixture_app()
    form = callback_form(CallStatus=state)
    with TestClient(app) as client:
        assert post_signed(client, STATUS_PATH, form, headers={"host": "untrusted.example"}).status_code == 204
    assert repo.calls == [("status", {**SCOPE, "status": mapped,
                                      "sequence_number": sequence, "duration_seconds": 25})]
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("case", ["missing", "invalid", "naive", "stale", "future", "tampered", "wrong_host_signature"])
def test_status_requires_fresh_signed_timestamp(case):
    app, repo, provider, connector = fixture_app()
    form = callback_form()
    if case == "missing":
        form.pop("Timestamp")
    elif case == "invalid":
        form["Timestamp"] = "not-a-timestamp"
    elif case == "naive":
        form["Timestamp"] = datetime.now().isoformat()
    elif case == "stale":
        form["Timestamp"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
    elif case == "future":
        form["Timestamp"] = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    signed = signature(STATUS_PATH, form, base_url="https://evil.example" if case == "wrong_host_signature" else None)
    if case == "tampered":
        form["Duration"] = "26"
    with TestClient(app) as client:
        assert client.post(STATUS_PATH, data=form, headers={"x-signature": signed}).status_code == 403
    assert repo.calls == []
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("changes", [{"CallStatus": "unknown"}, {"Duration": "-1"},
                                    {"Duration": "86401"}, {"Duration": "NaN"}])
def test_status_rejects_invalid_fields_without_storage(changes):
    app, repo, provider, connector = fixture_app()
    with TestClient(app) as client:
        assert post_signed(client, STATUS_PATH, callback_form(**changes)).status_code == 400
    assert repo.calls == []
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("operation,status", [("refuse", 409), ("raise", 503)])
def test_status_database_failure_is_not_acknowledged(operation, status):
    app, repo, _, _ = fixture_app()
    (repo.fail_actions if operation == "refuse" else repo.raise_actions).add("status")
    with TestClient(app) as client:
        response = post_signed(client, STATUS_PATH)
        assert response.status_code == status
        assert "Sensitive" not in response.text


@pytest.mark.parametrize("route", [VOICE_PATH, STATUS_PATH])
def test_other_provider_refuses_clawops_callbacks(route):
    app, repo, provider, connector = fixture_app(replace(CONFIG, provider="twilio"))
    with TestClient(app) as client:
        assert post_signed(client, route).status_code == 403
    assert repo.calls == []
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("case", ["disabled", "provider", "query", "invalid_job"])
def test_media_rejected_before_accept_without_authorization(case):
    config = replace(CONFIG, **({"enabled": False} if case == "disabled" else
                               {"provider": "twilio"} if case == "provider" else {}))
    app, repo, provider, connector = fixture_app(config)
    path = MEDIA_PATH + "?ticket=ignored" if case == "query" else MEDIA_PATH
    if case == "invalid_job":
        path = "/voice/clawops/media/not-a-uuid"
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(path):
                pass
        assert caught.value.code == 1008
    assert repo.calls == []
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("case", ["invalid_ticket", "expired_ticket", "other_job_ticket", "wrong_account",
                                  "bad_call_id", "bad_stream_id", "wrong_codec", "wrong_track", "no_start"])
def test_invalid_start_has_no_database_or_ai_access(case):
    app, repo, provider, connector = fixture_app()
    event = start_message()
    start = event["start"]
    if case == "invalid_ticket":
        start["customParameters"]["ticket"] = "forged"
    elif case == "expired_ticket":
        start["customParameters"]["ticket"] = issue_stream_ticket(CONFIG, JOB_ID, NONCE, now=int(time.time()) - 901)
    elif case == "other_job_ticket":
        start["customParameters"]["ticket"] = issue_stream_ticket(CONFIG, OTHER_JOB_ID, NONCE)
    elif case == "wrong_account":
        start["accountId"] = "ACother_test_account"
    elif case == "bad_call_id":
        start["callId"] = "twilio:" + CALL_ID
    elif case == "bad_stream_id":
        start["streamId"] = "invalid-stream"
    elif case == "wrong_codec":
        start["mediaFormat"]["sampleRate"] = 24000
    elif case == "wrong_track":
        start["tracks"] = ["outbound"]
    else:
        event = {"event": "media", "media": {"payload": AUDIO}}
    with TestClient(app) as client:
        with client.websocket_connect(MEDIA_PATH) as ws:
            ws.send_json(event)
            with pytest.raises(WebSocketDisconnect) as caught:
                ws.receive_json()
            assert caught.value.code == 1008
    assert repo.calls == []
    assert_no_ai(provider, connector)


@pytest.mark.parametrize("case", ["unbound", "other_call", "other_job", "other_provider", "not_test_target",
                                  "job_refused", "connect_refused", "connect_wrong_call",
                                  "connect_wrong_job", "connect_wrong_provider", "connect_non_object"])
def test_valid_ticket_still_requires_database_call_binding_and_atomic_connect(case):
    app, repo, provider, connector = fixture_app()
    if case == "unbound":
        repo.job["provider_call_id"] = None
    elif case == "other_call":
        repo.job["provider_call_id"] = "clawops:CAanother_test_call"
    elif case == "other_job":
        repo.job["id"] = OTHER_JOB_ID
    elif case == "other_provider":
        repo.job["provider"] = "twilio"
    elif case == "not_test_target":
        repo.job["phone_e164"] = SYNTHETIC_OTHER_DESTINATION
    elif case == "job_refused":
        repo.fail_actions.add("get_job")
    elif case == "connect_refused":
        repo.fail_actions.add("connect")
    elif case == "connect_wrong_call":
        repo.connect_job_override = {**repo.job, "provider_call_id": "clawops:CAanother_test_call"}
    elif case == "connect_wrong_job":
        repo.connect_job_override = {**repo.job, "id": OTHER_JOB_ID}
    elif case == "connect_wrong_provider":
        repo.connect_job_override = {**repo.job, "provider": "twilio"}
    else:
        repo.connect_job_override = []
    with TestClient(app) as client:
        with client.websocket_connect(MEDIA_PATH) as ws:
            ws.send_json(start_message())
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert [action for action, _ in repo.calls] == (
        ["get_job", "connect"] if case.startswith("connect_") else ["get_job"])
    assert_no_ai(provider, connector)


def test_voiceml_ticket_then_unsigned_socket_roundtrip_uses_clawops_wire_contract():
    app, repo, provider, connector = fixture_app(bound=False, respond_to_customer=True)
    with TestClient(app) as client:
        xml = post_signed(client, VOICE_PATH)
        ticket = ET.fromstring(xml.text).find("Connect/Stream/Parameter").attrib["value"]
        # ClawOps does not sign the WebSocket handshake: no signature header.
        with client.websocket_connect(MEDIA_PATH) as ws:
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json(start_message(ticket=ticket))
            receive_audio_and_ack(ws)
            ws.send_json({"event": "media", "media": {
                "track": "inbound", "timestamp": "200", "payload": AUDIO,
            }})
            assert ws.receive_json() == {"event": "clear"}
            receive_audio_and_ack(ws)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert provider.hung_up == [PROVIDER_CALL_ID]
    assert len(connector.connections) == 1
    upstream = connector.connections[0]
    assert upstream.closed
    assert [action for action, _ in connector.connect_snapshots[0]] == [
        "get_job", "mark_dispatched", "get_job", "connect"]
    assert any(event == {"type": "input_audio_buffer.append", "audio": AUDIO} for event in upstream.sent)
    assert any(event["type"] == "conversation.item.truncate" and event["audio_end_ms"] == 100
               for event in upstream.sent)
    results = [payload for action, payload in repo.calls if action == "result"]
    assert len(results) == 1
    assert results[0]["provider_call_id"] == PROVIDER_CALL_ID
    assert results[0]["outcome"] == "declined"
    assert not any(action == "bridge_error" for action, _ in repo.calls)


def test_dtmf_nine_persists_optout_then_hangs_up_and_replay_cannot_open_ai():
    app, repo, provider, connector = fixture_app()
    ticket = issue_stream_ticket(CONFIG, JOB_ID, NONCE)
    with TestClient(app) as client:
        with client.websocket_connect(MEDIA_PATH) as ws:
            ws.send_json(start_message(ticket=ticket))
            receive_audio_and_ack(ws)
            ws.send_json({"event": "dtmf", "dtmf": {"digit": "9", "track": "inbound_track"}})
            assert ws.receive_json() == {"event": "clear"}
            # Persistence must finish before a fallible socket write/close.
            assert any(action == "result" and payload["outcome"] == "do_not_call"
                       for action, payload in repo.calls)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        with client.websocket_connect(MEDIA_PATH) as ws:
            ws.send_json(start_message(ticket=ticket))
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert len(connector.connections) == 1
    assert connector.connections[0].response_count == 1
    assert connector.connections[0].closed
    assert provider.hung_up == [PROVIDER_CALL_ID]
    results = [payload for action, payload in repo.calls if action == "result"]
    assert len(results) == 1
    assert results[0]["outcome"] == "do_not_call"
    assert results[0]["customer_confirmed"] is True
    assert results[0]["provider_call_id"] == PROVIDER_CALL_ID
    assert results[0]["idempotency_key"] == f"voice:{JOB_ID}:dtmf-optout"


def test_failed_optout_quarantines_and_never_resumes_pitch():
    app, repo, provider, connector = fixture_app()
    repo.fail_actions.add("result")
    with TestClient(app) as client:
        with client.websocket_connect(MEDIA_PATH) as ws:
            ws.send_json(start_message())
            receive_audio_and_ack(ws)
            ws.send_json({"event": "dtmf", "dtmf": {"digit": "9"}})
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert provider.hung_up == [PROVIDER_CALL_ID]
    assert connector.connections[0].response_count == 1
    assert any(action == "bridge_error" and payload["error_code"] == "DO_NOT_CALL_SAVE_FAILED"
               for action, payload in repo.calls)


@pytest.mark.parametrize("case", ["stream_id", "twilio_sid", "stop_call", "stop_account", "duplicate_start"])
def test_authenticated_media_cannot_change_identity_or_restart_stream(case):
    app, repo, provider, connector = fixture_app()
    event = {"event": "media", "media": {"track": "inbound", "payload": AUDIO, "timestamp": "1"}}
    if case == "stream_id":
        event["streamId"] = "MZanother_test_stream"
    elif case == "twilio_sid":
        event["streamSid"] = STREAM_ID
    elif case.startswith("stop_"):
        event = stop_message()
        event["stop"]["callId" if case == "stop_call" else "accountId"] = "wrong-identity"
    else:
        event = start_message()
    with TestClient(app) as client:
        with client.websocket_connect(MEDIA_PATH) as ws:
            ws.send_json(start_message())
            receive_audio_and_ack(ws)
            ws.send_json(event)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert provider.hung_up == [PROVIDER_CALL_ID]
    assert connector.connections[0].closed
    assert any(action == "bridge_error" and payload["error_code"] == "MEDIA_PROTOCOL_ERROR"
               for action, payload in repo.calls)


def test_valid_stop_closes_model_and_hangs_up_bound_phone_call():
    app, repo, provider, connector = fixture_app()
    with TestClient(app) as client:
        with client.websocket_connect(MEDIA_PATH) as ws:
            ws.send_json(start_message())
            receive_audio_and_ack(ws)
            ws.send_json(stop_message())
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert provider.hung_up == [PROVIDER_CALL_ID]
    assert connector.connections[0].closed
    assert not any(action == "bridge_error" for action, _ in repo.calls)
