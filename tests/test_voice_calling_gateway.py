import asyncio
import base64
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import FormData
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from voice_calling import VoiceSettings
from voice_calling_gateway import CallBridge, PlaybackTracker, create_app
from voice_calling_provider import issue_stream_ticket, public_endpoint

JOB_ID = "11111111-1111-4111-8111-111111111111"
NONCE = "22222222-2222-4222-8222-222222222222"
CALL_ID = "CA" + "1" * 32
STREAM_ID = "MZ" + "2" * 32
# Synthetic format-only caller ID; all provider traffic stays inside the fixture.
SYNTHETIC_CALLER_ID = "+82" + "".join(("10", "0000", "0000"))
CONFIG = VoiceSettings(enabled=True, twilio_account_sid="AC" + "0" * 32,
                       twilio_auth_token="test-" + "x" * 32,
                       caller_id=SYNTHETIC_CALLER_ID, public_base_url="https://voice.example.test",
                       openai_api_key="test-not-a-key", stream_ticket_secret="s" * 40)
JOB = {"id": JOB_ID, "dispatch_nonce": NONCE, "provider_call_id": CALL_ID,
       "company_name": "가상 테스트 업체", "representative_name": "", "address": ""}
AUDIO = base64.b64encode(b"\xff" * 800).decode()


def signature(path, params=None, *, websocket=False):
    return RequestValidator(CONFIG.twilio_auth_token).compute_signature(
        public_endpoint(CONFIG, path, websocket=websocket), params or {})


def start_message(config=CONFIG):
    return {"event": "start", "streamSid": STREAM_ID, "start": {
        "accountSid": CONFIG.twilio_account_sid, "callSid": CALL_ID,
        "streamSid": STREAM_ID, "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1},
        "customParameters": {"ticket": issue_stream_ticket(config, JOB_ID, NONCE)},
    }}


class FakeRepository:
    def __init__(self):
        self.calls = []
        self.connected = False
        self.fail_results = False

    def worker(self, action, payload):
        self.calls.append((action, payload))
        if action == "connect":
            if self.connected:
                return {"ok": False}
            assert payload == {"job_id": JOB_ID, "provider_call_id": CALL_ID, "dispatch_nonce": NONCE}
            self.connected = True
            return {"ok": True, "job": dict(JOB)}
        if action == "result" and self.fail_results:
            return {"ok": False}
        return {"ok": True}


class FakeProvider:
    def __init__(self):
        self.hung_up = []

    def hangup(self, sid):
        self.hung_up.append(sid)


class FakeUpstream:
    def __init__(self, *, respond_to_customer=False):
        self.events = asyncio.Queue()
        self.sent = []
        self.closed = False
        self.respond_to_customer = respond_to_customer
        self.response_count = 0

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
            self.events.put_nowait({"type": "response.output_audio.delta", "item_id": f"item{self.response_count}", "delta": AUDIO})
            self.events.put_nowait({"type": "response.done", "response": {"status": "completed", "output": [{"type": "message"}]}})
        elif event["type"] == "input_audio_buffer.append" and self.respond_to_customer:
            self.events.put_nowait({"type": "input_audio_buffer.speech_started"})
            self.events.put_nowait({"type": "response.function_call_arguments.done", "call_id": "call_result_1", "name": "save_call_result", "arguments": json.dumps({
                "outcome": "declined", "visit_at": "", "address": "", "summary": "관심 없음", "customer_confirmed": True,
            })})
            self.events.put_nowait({"type": "response.done", "response": {"status": "completed", "output": [{"type": "function_call"}]}})


class FakeConnector:
    def __init__(self, *, respond_to_customer=False):
        self.connections = []
        self.respond_to_customer = respond_to_customer

    @asynccontextmanager
    async def __call__(self, _config):
        upstream = FakeUpstream(respond_to_customer=self.respond_to_customer)
        self.connections.append(upstream)
        try:
            yield upstream
        finally:
            upstream.closed = True


def fixture_app(config=CONFIG, *, respond_to_customer=False):
    repo, provider, connector = FakeRepository(), FakeProvider(), FakeConnector(respond_to_customer=respond_to_customer)
    return create_app(config, repo, provider, connector), repo, provider, connector


def connect_socket(client):
    path = f"/voice/media/{JOB_ID}"
    return client.websocket_connect(path, headers={"x-twilio-signature": signature(path, websocket=True)})


def receive_audio_and_ack(ws):
    event = ws.receive_json()
    assert event["event"] == "media"
    assert event["media"]["payload"] == AUDIO
    mark = ws.receive_json()
    assert mark["event"] == "mark"
    ws.send_json({"event": "mark", "streamSid": STREAM_ID, "mark": mark["mark"]})


def test_health_no_keys_no_storage_and_no_public_api_docs():
    app, repo, _, _ = fixture_app()
    with TestClient(app) as client:
        assert client.get("/health").json() == {"ok": True, "service": "voice-gateway"}
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
    assert repo.calls == []


def test_unsigned_status_cannot_write_and_signed_form_all_parameters_verified():
    app, repo, _, _ = fixture_app()
    path = f"/voice/twilio/status/{JOB_ID}"
    data = {"CallSid": CALL_ID, "AccountSid": CONFIG.twilio_account_sid,
            "CallStatus": "completed", "SequenceNumber": "3", "CallDuration": "25",
            "FutureTwilioField": "supported"}
    with TestClient(app) as client:
        assert client.post(path, data=data).status_code == 403
        assert repo.calls == []
        headers = {"x-twilio-signature": signature(path, data), "host": "untrusted.example"}
        assert client.post(path, data=data, headers=headers).status_code == 204
        assert repo.calls[-1] == ("status", {"job_id": JOB_ID, "provider_call_id": CALL_ID,
                                           "status": "completed", "sequence_number": 3, "duration_seconds": 25})
        data["FutureTwilioField"] = "tampered"
        assert client.post(path, data=data, headers=headers).status_code == 403
        assert len(repo.calls) == 1


def test_callback_rejects_wrong_account_duplicates_json_and_query():
    app, repo, _, _ = fixture_app()
    path = f"/voice/twilio/status/{JOB_ID}"
    with TestClient(app) as client:
        data = {"CallSid": CALL_ID, "AccountSid": "AC" + "f" * 32, "CallStatus": "completed"}
        assert client.post(path, data=data, headers={"x-twilio-signature": signature(path, data)}).status_code == 400
        assert client.post(path, json=data).status_code == 400
        assert client.post(path + "?extra=1", data=data).status_code == 400
        pairs = [("CallSid", CALL_ID), ("CallSid", "CA" + "3" * 32), ("AccountSid", CONFIG.twilio_account_sid), ("CallStatus", "completed")]
        headers = {"content-type": "application/x-www-form-urlencoded", "x-twilio-signature": signature(path, FormData(pairs))}
        assert client.post(path, content=urlencode(pairs), headers=headers).status_code == 400
    assert repo.calls == []


@pytest.mark.parametrize("enabled,bad_signature", [(False, False), (True, True)])
def test_disabled_or_unsigned_socket_never_opens_model(enabled, bad_signature):
    app, repo, provider, connector = fixture_app(replace(CONFIG, enabled=enabled))
    path = f"/voice/media/{JOB_ID}"
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path, headers={"x-twilio-signature": "invalid" if bad_signature else signature(path, websocket=True)}):
                pass
    assert not repo.calls and not connector.connections and not provider.hung_up


@pytest.mark.parametrize("field", ["ticket", "account", "codec"])
def test_invalid_start_cannot_claim_or_open_model(field):
    app, repo, provider, connector = fixture_app()
    event = start_message()
    if field == "ticket":
        event["start"]["customParameters"]["ticket"] = "invalid"
    elif field == "account":
        event["start"]["accountSid"] = "AC" + "f" * 32
    else:
        event["start"]["mediaFormat"]["sampleRate"] = 24000
    with TestClient(app) as client:
        with connect_socket(client) as ws:
            ws.send_json(event)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert not repo.calls and not connector.connections and not provider.hung_up


def test_media_roundtrip_barge_in_result_and_cleanup():
    app, repo, provider, connector = fixture_app(respond_to_customer=True)
    with TestClient(app) as client:
        with connect_socket(client) as ws:
            ws.send_json({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            ws.send_json(start_message())
            receive_audio_and_ack(ws)
            ws.send_json({"event": "media", "streamSid": STREAM_ID, "media": {
                "track": "inbound", "timestamp": "200", "payload": AUDIO,
            }})
            assert ws.receive_json()["event"] == "clear"
            receive_audio_and_ack(ws)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert provider.hung_up == [CALL_ID]
    assert connector.connections[0].closed
    sent = connector.connections[0].sent
    assert sum(x["type"] == "session.update" for x in sent) == 1
    assert sum(x["type"] == "response.create" for x in sent) == 2
    assert any(x["type"] == "conversation.item.truncate" and x["audio_end_ms"] == 100 for x in sent)
    result_calls = [payload for action, payload in repo.calls if action == "result"]
    assert len(result_calls) == 1
    assert result_calls[0]["outcome"] == "declined"
    assert result_calls[0]["provider_call_id"] == CALL_ID
    assert not any(action == "bridge_error" for action, _ in repo.calls)


def test_keypad_optout_and_replayed_stream_cannot_connect_twice():
    app, repo, provider, connector = fixture_app()
    with TestClient(app) as client:
        with connect_socket(client) as ws:
            ws.send_json(start_message())
            receive_audio_and_ack(ws)
            ws.send_json({"event": "dtmf", "streamSid": STREAM_ID, "dtmf": {"track": "inbound_track", "digit": "9"}})
            assert ws.receive_json()["event"] == "clear"
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
        with connect_socket(client) as ws:
            ws.send_json(start_message())
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert len(connector.connections) == 1
    assert provider.hung_up == [CALL_ID]
    results = [payload for action, payload in repo.calls if action == "result"]
    assert results[0]["outcome"] == "do_not_call"
    assert results[0]["idempotency_key"].endswith(":dtmf-optout")


def test_duration_limit_hangs_up_and_closes_upstream():
    app, repo, provider, connector = fixture_app(replace(CONFIG, max_call_seconds=0.1))
    with TestClient(app) as client:
        with connect_socket(client) as ws:
            ws.send_json(start_message())
            receive_audio_and_ack(ws)
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert provider.hung_up == [CALL_ID]
    assert connector.connections[0].closed
    assert any(action == "bridge_error" and payload["error_code"] == "CALL_TIME_LIMIT" for action, payload in repo.calls)


def test_disconnect_cancels_both_pumps_and_hangs_up():
    app, _, provider, connector = fixture_app()
    with TestClient(app) as client:
        with connect_socket(client) as ws:
            ws.send_json(start_message())
            receive_audio_and_ack(ws)
            ws.close()
    assert provider.hung_up == [CALL_ID]
    assert connector.connections[0].closed


def test_failed_optout_is_quarantined_and_ends_call_without_further_pitch():
    app, repo, provider, connector = fixture_app()
    repo.fail_results = True
    with TestClient(app) as client:
        with connect_socket(client) as ws:
            ws.send_json(start_message())
            receive_audio_and_ack(ws)
            ws.send_json({"event": "dtmf", "streamSid": STREAM_ID, "dtmf": {"digit": "9"}})
            assert ws.receive_json()["event"] == "clear"
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert provider.hung_up == [CALL_ID]
    assert any(action == "bridge_error" and payload["error_code"] == "DO_NOT_CALL_SAVE_FAILED" for action, payload in repo.calls)
    assert connector.connections[0].response_count == 1


def test_model_connection_failure_still_ends_authenticated_phone_leg():
    repo, provider = FakeRepository(), FakeProvider()
    @asynccontextmanager
    async def fail_to_connect(_config):
        raise RuntimeError("Sensitive upstream detail must not be persisted")
        yield  # pragma: no cover
    app = create_app(CONFIG, repo, provider, fail_to_connect)
    with TestClient(app) as client:
        with connect_socket(client) as ws:
            ws.send_json(start_message())
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()
    assert provider.hung_up == [CALL_ID]
    assert any(action == "bridge_error" and payload["error_code"] == "VOICE_BRIDGE_ERROR" for action, payload in repo.calls)
    assert "Sensitive" not in str(repo.calls)


def test_clear_marks_never_count_as_played_and_truncation_is_bounded():
    tracker = PlaybackTracker()
    mark = tracker.add("item", 8000, 100)
    assert tracker.interrupt(600)["audio_end_ms"] == 500
    tracker.acknowledge(mark)  # Twilio acks even discarded buffers after clear.
    assert tracker.heard_ms == 0
    tracker.add("other", 160, 0)
    assert tracker.interrupt(9999)["audio_end_ms"] == 20


def test_model_cannot_inject_another_job_or_store_unconfirmed_visit():
    class DummyWebSocket:
        async def send_json(self, event):
            pass
    async def run():
        repo, upstream = FakeRepository(), FakeUpstream()
        bridge = CallBridge(DummyWebSocket(), upstream, repo, CONFIG, JOB, STREAM_ID, CALL_ID)
        for call_id, value in [("bad_job", {"outcome": "declined", "job_id": NONCE}),
                               ("unconfirmed", {"outcome": "visit_requested", "customer_confirmed": False})]:
            await bridge._tool({"call_id": call_id, "name": "save_call_result", "arguments": json.dumps(value)})
        assert not repo.calls
        assert all(not json.loads(x["item"]["output"])["ok"] for x in upstream.sent)
    asyncio.run(run())


def test_duplicate_tool_event_only_persists_once_and_waits_for_response_done():
    async def run():
        repo, upstream = FakeRepository(), FakeUpstream()
        bridge = CallBridge(None, upstream, repo, CONFIG, JOB, STREAM_ID, CALL_ID)
        event = {"call_id": "repeat", "name": "save_call_result", "arguments": json.dumps({
            "outcome": "declined", "visit_at": "", "address": "", "summary": "", "customer_confirmed": True,
        })}
        await bridge._tool(event)
        await bridge._tool(event)
        assert len(repo.calls) == 1
        assert len(upstream.sent) == 1
        assert upstream.sent[0]["type"] == "conversation.item.create"
        assert bridge.pending_response_instruction
    asyncio.run(run())
