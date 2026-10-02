"""Separate, authenticated Twilio <-> OpenAI Realtime media service.

Run with ``uvicorn voice_calling_gateway:app --host 0.0.0.0 --port $PORT``.
It does not dial calls, expose transcripts, or modify the tax-claim gateway.
All media is transient; only validated structured call results reach storage.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qsl, quote

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from starlette.datastructures import FormData
from websockets.asyncio.client import connect

from voice_calling import VoiceSettings, build_realtime_session, opening_greeting, validate_outcome
from voice_calling_provider import (
    SID_RE, TwilioVoiceProvider, canonical_job_id, validate_twilio_signature,
    verify_stream_ticket,
)


logger = logging.getLogger(__name__)
# websockets DEBUG logs frame contents, including transient customer audio.
transport_logger = logging.getLogger("oasis.voice.websocket_transport")
transport_logger.setLevel(logging.WARNING)
PROVIDER_STATES = frozenset({"queued", "initiated", "ringing", "in-progress", "completed", "busy", "failed", "no-answer", "canceled"})
RESULT_KEYS = frozenset({"outcome", "visit_at", "address", "summary", "customer_confirmed"})
MAX_MESSAGE_BYTES = 131072


class BridgeProtocolError(RuntimeError):
    """Contains only a fixed, non-sensitive error code."""


async def _worker(repository: Any, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = await asyncio.to_thread(repository.worker, action, payload)
    if not isinstance(result, dict):
        raise BridgeProtocolError("STORAGE_RESPONSE_INVALID")
    return result


async def _bounded_text(websocket: WebSocket) -> dict[str, Any]:
    raw = await websocket.receive_text()
    if len(raw) > MAX_MESSAGE_BYTES:
        raise BridgeProtocolError("MEDIA_MESSAGE_TOO_LARGE")
    try:
        event = json.loads(raw)
    except (ValueError, TypeError):
        raise BridgeProtocolError("INVALID_MEDIA_MESSAGE") from None
    if not isinstance(event, dict):
        raise BridgeProtocolError("INVALID_MEDIA_MESSAGE")
    return event


def _audio_length(payload: Any) -> int:
    if not isinstance(payload, str) or not payload or len(payload) > 96000:
        raise BridgeProtocolError("INVALID_AUDIO_CHUNK")
    try:
        size = len(base64.b64decode(payload, validate=True))
    except (ValueError, TypeError):
        raise BridgeProtocolError("INVALID_AUDIO_CHUNK") from None
    if size == 0:
        raise BridgeProtocolError("INVALID_AUDIO_CHUNK")
    return size


@dataclass
class PlaybackTracker:
    """PCMU is 8000 bytes/second. Clear acknowledgements are not playback."""
    item_id: str | None = None
    start_timestamp: int | None = None
    sent_bytes: int = 0
    heard_ms: int = 0
    sequence: int = 0
    pending_marks: dict[str, tuple[str, int]] = field(default_factory=dict)

    def add(self, item_id: str, size: int, timestamp: int) -> str:
        if item_id != self.item_id:
            self.item_id = item_id
            self.start_timestamp = timestamp
            self.sent_bytes = 0
            self.heard_ms = 0
        self.sent_bytes += size
        self.sequence += 1
        mark = f"audio-{self.sequence}"
        if len(self.pending_marks) >= 1024:
            raise BridgeProtocolError("PLAYBACK_BACKPRESSURE")
        self.pending_marks[mark] = (item_id, self.sent_bytes // 8)
        return mark

    def acknowledge(self, mark: str) -> None:
        item = self.pending_marks.pop(mark, None)
        if item and item[0] == self.item_id:
            self.heard_ms = max(self.heard_ms, item[1])

    def interrupt(self, timestamp: int) -> dict[str, Any] | None:
        truncate = None
        if self.item_id is not None and self.start_timestamp is not None:
            elapsed = max(0, timestamp - self.start_timestamp)
            heard = min(self.sent_bytes // 8, max(self.heard_ms, elapsed))
            truncate = {"type": "conversation.item.truncate", "item_id": self.item_id,
                        "content_index": 0, "audio_end_ms": heard}
        # Marks emitted by clear describe discarded audio, not played audio.
        self.pending_marks.clear()
        self.item_id = None
        self.start_timestamp = None
        self.sent_bytes = 0
        self.heard_ms = 0
        return truncate


class CallBridge:
    def __init__(self, websocket: WebSocket, upstream: Any, repository: Any,
                 config: VoiceSettings, job: dict[str, Any], stream_sid: str,
                 provider_call_id: str) -> None:
        self.websocket, self.upstream, self.repository = websocket, upstream, repository
        self.config, self.job, self.stream_sid, self.provider_call_id = config, job, stream_sid, provider_call_id
        self.playback = PlaybackTracker()
        self.timestamp = 0
        self.session_ready = asyncio.Event()
        self.greeting_sent = False
        self.result_saved = False
        self.seen_tool_calls: dict[str, dict[str, Any]] = {}
        self.close_after_playback = False
        self.pending_response_instruction: str | None = None
        self.finished = asyncio.Event()

    async def _upstream(self, event: dict[str, Any]) -> None:
        await self.upstream.send(json.dumps(event, ensure_ascii=False))

    async def _downstream(self, event: dict[str, Any]) -> None:
        await self.websocket.send_json(event)

    async def _save_result(self, value: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        checked = validate_outcome(value)
        # Provider/job identity always comes from the authenticated stream.
        result = await _worker(self.repository, "result", {
            **checked, "job_id": self.job["id"],
            "provider_call_id": self.provider_call_id,
            "idempotency_key": idempotency_key,
        })
        if result.get("ok") is not True:
            return {"ok": False, "error": "RESULT_NOT_SAVED"}
        self.result_saved = True
        # Do not return internal CRM IDs, notes, or full result rows to the model.
        return {"ok": True, "outcome": checked["outcome"],
                "appointment_status": "pending_human_confirmation" if checked["outcome"] == "visit_requested" else "not_confirmed"}

    async def _tool(self, event: dict[str, Any]) -> None:
        call_id = str(event.get("call_id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", call_id):
            raise BridgeProtocolError("INVALID_TOOL_CALL")
        if call_id in self.seen_tool_calls:
            # Duplicate stream events must not create another visit/CRM record.
            return
        if len(self.seen_tool_calls) >= 8:
            raise BridgeProtocolError("TOOL_CALL_LIMIT")
        result: dict[str, Any]
        args = event.get("arguments", "")
        try:
            if (event.get("name") != "save_call_result" or not isinstance(args, str)
                    or len(args) > 4096):
                raise ValueError("INVALID_TOOL_ARGUMENTS")
            value = json.loads(args)
            if not isinstance(value, dict) or set(value) - RESULT_KEYS:
                raise ValueError("INVALID_TOOL_ARGUMENTS")
            # A withdrawal always supersedes an earlier visit or callback request.
            if self.result_saved and value.get("outcome") != "do_not_call":
                result = {"ok": False, "error": "RESULT_ALREADY_SAVED"}
            else:
                try:
                    result = await self._save_result(value, f"voice:{self.job['id']}:{call_id}")
                except (ValueError, TypeError):
                    raise
                except Exception:
                    if value.get("outcome") == "do_not_call":
                        raise BridgeProtocolError("DO_NOT_CALL_SAVE_FAILED") from None
                    raise
                if value.get("outcome") == "do_not_call" and result.get("ok") is not True:
                    raise BridgeProtocolError("DO_NOT_CALL_SAVE_FAILED")
        except (ValueError, TypeError):
            result = {"ok": False, "error": "CONFIRMED_DETAILS_REQUIRED"}
        self.seen_tool_calls[call_id] = result
        await self._upstream({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": call_id,
            "output": json.dumps(result, ensure_ascii=False),
        }})
        if result.get("ok"):
            self.close_after_playback = True
            instruction = (
                "고객의 수신거부를 처리했습니다. 짧게 수신거부 처리만 알리고 정중히 종료하세요. 추가 질문·권유를 하지 마세요."
                if result.get("outcome") == "do_not_call" else
                "결과 저장이 완료되었습니다. 방문/재연락 요청이면 담당자가 다시 연락해 일정을 확정함을 한 문장으로 알리세요. "
                "거절/잘못된 번호라면 짧게 사과 또는 인사만 하세요. 새 질문·추가 권유 없이 종료하세요."
            )
            self.pending_response_instruction = instruction
        else:
            self.pending_response_instruction = "접수 완료라고 말하지 마세요. 확인이 필요한 날짜·시간·장소 또는 의사를 짧게 다시 확인하세요."

    async def receive_customer(self) -> None:
        await asyncio.wait_for(self.session_ready.wait(), timeout=15)
        while not self.finished.is_set():
            event = await _bounded_text(self.websocket)
            kind = event.get("event")
            if event.get("streamSid") != self.stream_sid:
                raise BridgeProtocolError("STREAM_ID_MISMATCH")
            if kind == "media":
                media = event.get("media", {})
                if media.get("track") != "inbound":
                    continue
                _audio_length(media.get("payload"))
                try:
                    timestamp = int(media.get("timestamp", 0))
                except (ValueError, TypeError):
                    raise BridgeProtocolError("INVALID_MEDIA_TIMESTAMP") from None
                self.timestamp = max(self.timestamp, min(timestamp, self.config.max_call_seconds * 1000 + 10000))
                await self._upstream({"type": "input_audio_buffer.append", "audio": media["payload"]})
            elif kind == "mark":
                self.playback.acknowledge(str(event.get("mark", {}).get("name", "")))
                if self.close_after_playback and not self.playback.pending_marks:
                    # response.done sets closing readiness; audio generation may
                    # still be active while the first chunks are acknowledged.
                    if getattr(self, "closing_response_done", False):
                        self.finished.set()
                        return
            elif kind == "dtmf" and event.get("dtmf", {}).get("digit") == "9":
                # Keypad opt-out works independently of model understanding.
                await self._downstream({"event": "clear", "streamSid": self.stream_sid})
                try:
                    result = await self._save_result({"outcome": "do_not_call", "visit_at": "", "address": "",
                                                     "summary": "키패드 수신거부", "customer_confirmed": True},
                                                    f"voice:{self.job['id']}:dtmf-optout")
                    if result.get("ok") is not True:
                        raise BridgeProtocolError("DO_NOT_CALL_SAVE_FAILED")
                except Exception:
                    raise BridgeProtocolError("DO_NOT_CALL_SAVE_FAILED") from None
                self.finished.set()
                return
            elif kind == "stop":
                if event.get("stop", {}).get("callSid") not in (None, self.provider_call_id):
                    raise BridgeProtocolError("CALL_ID_MISMATCH")
                self.finished.set()
                return
            elif kind in {"start", "connected"}:
                raise BridgeProtocolError("DUPLICATE_STREAM_START")

    async def receive_model(self) -> None:
        async for raw in self.upstream:
            if len(raw) > MAX_MESSAGE_BYTES:
                raise BridgeProtocolError("UPSTREAM_MESSAGE_TOO_LARGE")
            try:
                event = json.loads(raw)
            except (ValueError, TypeError):
                raise BridgeProtocolError("INVALID_UPSTREAM_MESSAGE") from None
            if not isinstance(event, dict):
                raise BridgeProtocolError("INVALID_UPSTREAM_MESSAGE")
            kind = event.get("type")
            if kind == "session.updated" and not self.greeting_sent:
                self.greeting_sent = True
                self.session_ready.set()
                await self._upstream({"type": "response.create", "response": {
                    "instructions": "다음 첫 인사를 그대로 한 번만 말하고 고객 답을 기다리세요: " + opening_greeting(self.job)
                }})
            elif kind == "response.output_audio.delta":
                payload = event.get("delta")
                size = _audio_length(payload)
                item_id = event.get("item_id")
                if not isinstance(item_id, str) or len(item_id) > 200:
                    raise BridgeProtocolError("INVALID_AUDIO_ITEM")
                mark = self.playback.add(item_id, size, self.timestamp)
                await self._downstream({"event": "media", "streamSid": self.stream_sid,
                                        "media": {"payload": payload}})
                await self._downstream({"event": "mark", "streamSid": self.stream_sid,
                                        "mark": {"name": mark}})
            elif kind == "input_audio_buffer.speech_started":
                # Semantic VAD cancels generation; stop downstream queued audio
                # and tell the model only the part the customer actually heard.
                truncated = self.playback.interrupt(self.timestamp)
                await self._downstream({"event": "clear", "streamSid": self.stream_sid})
                if truncated:
                    await self._upstream(truncated)
                self.closing_response_done = False
            elif kind == "response.function_call_arguments.done":
                await self._tool(event)
            elif kind == "response.done":
                response = event.get("response", {})
                # The response containing the function call is not the closing
                # spoken response. Only finish after its successor's audio.
                tool_response = any(x.get("type") == "function_call" for x in response.get("output", []) if isinstance(x, dict))
                if self.pending_response_instruction and response.get("status") == "completed":
                    instruction = self.pending_response_instruction
                    self.pending_response_instruction = None
                    await self._upstream({"type": "response.create", "response": {"instructions": instruction}})
                    continue
                if self.close_after_playback and not tool_response and response.get("status") == "completed":
                    self.closing_response_done = True
                    if not self.playback.pending_marks:
                        self.finished.set()
                        return
                if response.get("status") == "failed":
                    raise BridgeProtocolError("UPSTREAM_RESPONSE_FAILED")
            elif kind == "error":
                # Deliberately never persist provider error text/transcripts.
                raise BridgeProtocolError("UPSTREAM_ERROR")
            # Transcript delta/done and other textual/audio events are ignored.
        self.finished.set()

    async def run(self) -> None:
        await self._upstream({"type": "session.update", "session": build_realtime_session(self.job, self.config)})
        tasks = [asyncio.create_task(self.receive_customer()),
                 asyncio.create_task(self.receive_model()),
                 asyncio.create_task(self.finished.wait())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def _default_upstream(config: VoiceSettings):
    # Fixed API destination and server-only authentication; never take either
    # from request data or model-generated tool arguments.
    return connect(
        "wss://api.openai.com/v1/realtime?model=" + quote(config.realtime_model, safe=""),
        additional_headers={"Authorization": "Bearer " + config.openai_api_key},
        open_timeout=config.request_timeout_seconds, close_timeout=3,
        max_size=MAX_MESSAGE_BYTES, max_queue=16, compression=None, proxy=None,
        logger=transport_logger,
    )


def create_app(config: VoiceSettings | None = None, repository: Any = None,
               provider: Any = None, upstream_connect: Callable | None = None) -> FastAPI:
    settings = config or VoiceSettings.from_environment()
    call_provider = provider or TwilioVoiceProvider(settings)
    connector = upstream_connect or _default_upstream
    active: set[asyncio.Task] = set()

    def get_repository():
        nonlocal repository
        if repository is None:
            from voice_calling_repository import VoiceRepository
            repository = VoiceRepository()
        return repository

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        for task in list(active):
            task.cancel()
        if active:
            await asyncio.gather(*list(active), return_exceptions=True)

    app = FastAPI(title="OASIS Voice Gateway", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"ok": True, "service": "voice-gateway"}

    @app.post("/voice/twilio/status/{job_id}")
    async def status(job_id: str, request: Request):
        try:
            canonical_job_id(job_id)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        if request.url.query or request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
            raise HTTPException(400, "Invalid request")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 16384:
                raise HTTPException(413, "Request too large")
        try:
            form = FormData(parse_qsl(content.decode("utf-8"), keep_blank_values=True, max_num_fields=100))
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid request") from None
        if not validate_twilio_signature(settings, f"/voice/twilio/status/{job_id}", form,
                                         request.headers.get("x-twilio-signature", "")):
            raise HTTPException(403, "Forbidden")
        for key in ("CallSid", "AccountSid", "CallStatus", "SequenceNumber", "CallDuration"):
            if len(form.getlist(key)) > 1:
                raise HTTPException(400, "Ambiguous request")
        sid, state = str(form.get("CallSid", "")), str(form.get("CallStatus", ""))
        if (form.get("AccountSid") != settings.twilio_account_sid
                or not SID_RE.fullmatch(sid) or state not in PROVIDER_STATES):
            raise HTTPException(400, "Invalid request")
        try:
            sequence = int(form.get("SequenceNumber", "0"))
            duration = int(form.get("CallDuration", "0"))
            if not 0 <= sequence <= 100000 or not 0 <= duration <= 86400:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid request") from None
        try:
            result = await _worker(get_repository(), "status", {
                "job_id": job_id, "provider_call_id": sid, "status": state,
                "sequence_number": sequence, "duration_seconds": duration,
            })
        except Exception:
            raise HTTPException(503, "Temporarily unavailable") from None
        if result.get("ok") is not True:
            raise HTTPException(409, "Call state not accepted")
        return Response(status_code=204)

    @app.websocket("/voice/media/{job_id}")
    async def media(job_id: str, websocket: WebSocket):
        try:
            canonical_job_id(job_id)
        except ValueError:
            await websocket.close(code=1008)
            return
        if (not settings.readiness().get("ready") or websocket.url.query
                or not validate_twilio_signature(settings, f"/voice/media/{job_id}", {},
                                                 websocket.headers.get("x-twilio-signature", ""), websocket=True)):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        task = asyncio.current_task()
        if task is not None:
            active.add(task)
        sid: str | None = None
        connected = False
        error_code: str | None = None
        try:
            async with asyncio.timeout(10):
                initial = await _bounded_text(websocket)
                if initial.get("event") == "connected":
                    if initial.get("protocol") != "Call" or initial.get("version") != "1.0.0":
                        raise BridgeProtocolError("INVALID_STREAM_PROTOCOL")
                    initial = await _bounded_text(websocket)
                if initial.get("event") != "start":
                    raise BridgeProtocolError("STREAM_START_REQUIRED")
                start = initial.get("start", {})
                if not isinstance(start, dict):
                    raise BridgeProtocolError("INVALID_STREAM_START")
                candidate = str(start.get("callSid", ""))
                stream_sid = str(start.get("streamSid", ""))
                if (start.get("accountSid") != settings.twilio_account_sid
                        or not SID_RE.fullmatch(candidate)
                        or not re.fullmatch(r"MZ[0-9a-fA-F]{32}", stream_sid)
                        or initial.get("streamSid") != stream_sid
                        or start.get("mediaFormat") != {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}):
                    raise BridgeProtocolError("INVALID_STREAM_START")
                nonce = verify_stream_ticket(settings, start.get("customParameters", {}).get("ticket"), job_id)
                result = await _worker(get_repository(), "connect", {
                    "job_id": job_id, "provider_call_id": candidate, "dispatch_nonce": nonce,
                })
                job = result.get("job")
                if (result.get("ok") is not True or not isinstance(job, dict) or job.get("id") != job_id
                        or job.get("provider_call_id") != candidate):
                    raise BridgeProtocolError("STREAM_NOT_AUTHORIZED")
                # Only now is this a trusted SID eligible for REST hangup.
                sid = candidate
                connected = True
            async with asyncio.timeout(settings.max_call_seconds):
                async with connector(settings) as upstream:
                    bridge = CallBridge(websocket, upstream, get_repository(), settings, job, stream_sid, sid)
                    await bridge.run()
        except asyncio.CancelledError:
            error_code = "GATEWAY_SHUTDOWN"
            raise
        except WebSocketDisconnect:
            pass
        except TimeoutError:
            error_code = "CALL_TIME_LIMIT"
        except BridgeProtocolError as error:
            error_code = "DO_NOT_CALL_SAVE_FAILED" if str(error) == "DO_NOT_CALL_SAVE_FAILED" else "MEDIA_PROTOCOL_ERROR"
        except ValueError:
            error_code = "MEDIA_PROTOCOL_ERROR"
        except Exception:
            error_code = "VOICE_BRIDGE_ERROR"
        finally:
            if connected and sid:
                if error_code:
                    try:
                        await asyncio.wait_for(_worker(get_repository(), "bridge_error", {
                            "job_id": job_id, "provider_call_id": sid, "error_code": error_code,
                        }), timeout=5)
                    except Exception:
                        logger.warning("voice_bridge_state_unconfirmed")
                try:
                    await asyncio.wait_for(asyncio.to_thread(call_provider.hangup, sid), timeout=20)
                except Exception:
                    logger.warning("voice_hangup_unconfirmed")
            try:
                await websocket.close(code=1000 if connected else 1008)
            except (RuntimeError, WebSocketDisconnect):
                pass
            if task is not None:
                active.discard(task)

    return app


app = create_app()
