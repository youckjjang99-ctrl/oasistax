"""ClawOps VoiceML and Id-based media protocol; no customer dialing here.

HTTP callbacks are signed. Stream handshakes are NOT signed by ClawOps, so
media is rejected until a short-lived ticket and one-use database nonce bind
the stream to an approved, still-authorized job/account/call.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl
from xml.etree import ElementTree as ET

from fastapi import HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from voice_calling import normalize_phone
from voice_calling_clawops import (
    CLAWOPS_STREAM_ID_RE, clawops_provider_call_id,
    validate_clawops_signature,
)
from voice_calling_provider import canonical_job_id, public_endpoint, issue_stream_ticket, verify_stream_ticket

logger = logging.getLogger(__name__)
_FORMAT = {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}
_STATES = {"initiated": ("initiated", 0), "ringing": ("ringing", 1),
           "answered": ("in-progress", 2), "completed": ("completed", 3),
           "busy": ("busy", 3), "no-answer": ("no-answer", 3),
           "failed": ("failed", 3), "canceled": ("canceled", 3), "rejected": ("failed", 3)}


def _fresh_timestamp(value: str, now: datetime | None = None) -> bool:
    try:
        sent = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if sent.tzinfo is None:
            return False
        age = ((now or datetime.now(timezone.utc)) - sent).total_seconds()
        return -30 <= age <= 300
    except (ValueError, TypeError, OverflowError):
        return False


def _test_target_allowed(config: Any, phone: str) -> bool:
    if config.clawops_live_verified:
        return True
    try:
        return normalize_phone(phone) in {normalize_phone(x) for x in config.clawops_test_numbers}
    except ValueError:
        return False


def _recent_dispatch(value: Any) -> bool:
    try:
        dispatched = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dispatched.tzinfo is not None and 0 <= (datetime.now(timezone.utc) - dispatched).total_seconds() <= 600
    except (ValueError, TypeError, OverflowError):
        return False


def _hangup_xml() -> Response:
    return Response('<Response><Hangup/></Response>', media_type="application/xml",
                    headers={"Cache-Control": "no-store"})


class ClawOpsMediaSocket:
    """Adapt only a previously authenticated socket to the shared bridge."""
    def __init__(self, websocket: WebSocket, stream_id: str, call_id: str, account_id: str):
        self.websocket, self.stream_id = websocket, stream_id
        self.call_id, self.account_id = call_id, account_id

    async def receive_text(self) -> str:
        raw = await self.websocket.receive_text()
        if len(raw) > 131072:
            raise ValueError("INVALID_MEDIA_MESSAGE")
        event = json.loads(raw)
        if not isinstance(event, dict) or event.get("event") not in {"media", "mark", "dtmf", "stop"}:
            raise ValueError("INVALID_MEDIA_MESSAGE")
        # Official messages omit a top-level stream ID. If one is present,
        # never let it override the ID bound to this authenticated socket.
        if event.get("streamId", self.stream_id) != self.stream_id or "streamSid" in event:
            raise ValueError("STREAM_ID_MISMATCH")
        kind = event["event"]
        body = event.get(kind)
        if not isinstance(body, dict):
            raise ValueError("INVALID_MEDIA_MESSAGE")
        if kind == "stop":
            if body.get("callId") != self.call_id or body.get("accountId") != self.account_id:
                raise ValueError("CALL_ID_MISMATCH")
            event["stop"] = {"callSid": clawops_provider_call_id(self.call_id)}
        event["streamSid"] = self.stream_id
        return json.dumps(event)

    async def send_json(self, event: dict[str, Any]) -> None:
        # ClawOps media/mark/clear do not carry Twilio streamSid.
        await self.websocket.send_json({k: v for k, v in event.items() if k != "streamSid"})


async def _signed_form(request: Request, config: Any, path: str) -> dict[str, str]:
    if config.provider != "clawops":
        raise HTTPException(403, "Forbidden")
    if request.url.query or request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
        raise HTTPException(400, "Invalid request")
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 16384:
            raise HTTPException(413, "Request too large")
    try:
        pairs = parse_qsl(content.decode("utf-8"), keep_blank_values=True, max_num_fields=100)
        form = dict(pairs)
        if len(form) != len(pairs):
            raise ValueError
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Invalid request") from None
    if not validate_clawops_signature(config, path, form, request.headers.get("x-signature", "")):
        raise HTTPException(403, "Forbidden")
    try:
        if (form.get("AccountId") != config.clawops_account_id or form.get("Direction") != "outbound"
                or normalize_phone(form.get("From")) != normalize_phone(config.caller_id)):
            raise ValueError
        clawops_provider_call_id(form.get("CallId"))
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid request") from None
    return form


def register_clawops_routes(app, settings, get_repository, call_provider, connector, active):
    from voice_calling_gateway import CallBridge, BridgeProtocolError, _worker, _bounded_text

    @app.post("/voice/clawops/voiceml/{job_id}")
    async def voiceml(job_id: str, request: Request):
        try:
            canonical_job_id(job_id)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        form = await _signed_form(request, settings, f"/voice/clawops/voiceml/{job_id}")
        if not settings.readiness().get("ready") or form.get("CallStatus") not in {"in-progress", "answered"}:
            return _hangup_xml()
        call_id = clawops_provider_call_id(form["CallId"])
        scope = {"job_id": job_id, "provider_call_id": call_id, "provider": "clawops"}
        try:
            result = await _worker(get_repository(), "get_job", scope)
            job = result.get("job")
            if (result.get("ok") is not True or not isinstance(job, dict) or job.get("id") != job_id
                    or job.get("provider") != "clawops" or job.get("connected_at") or not _recent_dispatch(job.get("dispatch_at"))
                    or normalize_phone(form.get("To")) != normalize_phone(job.get("phone_e164"))
                    or not _test_target_allowed(settings, job.get("phone_e164"))):
                return _hangup_xml()
            bound = await _worker(get_repository(), "mark_dispatched", scope)
            if bound.get("ok") is not True:
                return _hangup_xml()
            ticket = issue_stream_ticket(settings, job_id, job["dispatch_nonce"], ttl_seconds=90)
            root = ET.Element("Response")
            # No Connect.action: provider terminates PSTN when the stream closes.
            stream = ET.SubElement(ET.SubElement(root, "Connect"), "Stream", {
                "url": public_endpoint(settings, f"/voice/clawops/media/{job_id}", websocket=True), "track": "inbound"})
            ET.SubElement(stream, "Parameter", {"name": "ticket", "value": ticket})
            ET.SubElement(root, "Hangup")
            return Response(ET.tostring(root, encoding="unicode"), media_type="application/xml",
                            headers={"Cache-Control": "no-store"})
        except Exception:
            # Fail closed without disclosing fields or replaying a dialing request.
            return _hangup_xml()

    @app.post("/voice/clawops/status/{job_id}")
    async def status(job_id: str, request: Request):
        try:
            canonical_job_id(job_id)
        except ValueError:
            raise HTTPException(404, "Not found") from None
        form = await _signed_form(request, settings, f"/voice/clawops/status/{job_id}")
        if not _fresh_timestamp(form.get("Timestamp", "")):
            raise HTTPException(403, "Expired callback")
        if form.get("CallStatus") not in _STATES:
            raise HTTPException(400, "Invalid request")
        state, sequence = _STATES[form["CallStatus"]]
        try:
            duration = int(form.get("Duration", "0"))
            if not 0 <= duration <= 86400:
                raise ValueError
        except (ValueError, TypeError):
            raise HTTPException(400, "Invalid request") from None
        try:
            result = await _worker(get_repository(), "status", {
                "job_id": job_id, "provider_call_id": clawops_provider_call_id(form["CallId"]),
                "provider": "clawops", "status": state,
                "sequence_number": sequence, "duration_seconds": duration,
            })
        except Exception:
            raise HTTPException(503, "Temporarily unavailable") from None
        if result.get("ok") is not True:
            raise HTTPException(409, "Call state not accepted")
        return Response(status_code=204)

    @app.websocket("/voice/clawops/media/{job_id}")
    async def media(job_id: str, websocket: WebSocket):
        try:
            canonical_job_id(job_id)
        except ValueError:
            await websocket.close(code=1008)
            return
        if (settings.provider != "clawops" or not settings.readiness().get("ready")
                or websocket.url.query or len(active) >= 8):
            await websocket.close(code=1008)
            return
        # Vendor sends no handshake signature. No AI/storage access until ticket.
        await websocket.accept()
        task = asyncio.current_task()
        if task is not None:
            active.add(task)
        trusted_call = None
        error_code = None
        try:
            async with asyncio.timeout(10):
                initial = await _bounded_text(websocket)
                if initial.get("event") == "connected":
                    if initial.get("protocol") != "Call" or initial.get("version") != "1.0.0":
                        raise ValueError("INVALID_STREAM_PROTOCOL")
                    initial = await _bounded_text(websocket)
                start = initial.get("start")
                if initial.get("event") != "start" or not isinstance(start, dict):
                    raise ValueError("INVALID_STREAM_START")
                raw_call, stream_id = start.get("callId"), start.get("streamId", "")
                candidate = clawops_provider_call_id(raw_call)
                if (start.get("accountId") != settings.clawops_account_id
                        or not isinstance(stream_id, str) or not CLAWOPS_STREAM_ID_RE.fullmatch(stream_id)
                        or start.get("mediaFormat") != _FORMAT or start.get("tracks") != ["inbound"]):
                    raise ValueError("INVALID_STREAM_START")
                nonce = verify_stream_ticket(settings, start.get("customParameters", {}).get("ticket"), job_id)
                scope = {"job_id": job_id, "provider_call_id": candidate, "provider": "clawops"}
                # VoiceML must already have bound this call; the WS cannot be
                # used to bind an arbitrary candidate ID with a stolen ticket.
                checked = await _worker(get_repository(), "get_job", scope)
                job = checked.get("job")
                if (checked.get("ok") is not True or not isinstance(job, dict) or job.get("id") != job_id
                        or job.get("provider") != "clawops" or job.get("provider_call_id") != candidate
                        or not _test_target_allowed(settings, job.get("phone_e164"))):
                    raise ValueError("STREAM_NOT_AUTHORIZED")
                result = await _worker(get_repository(), "connect", {**scope, "dispatch_nonce": nonce})
                connected_job = result.get("job")
                if (result.get("ok") is not True or not isinstance(connected_job, dict)
                        or connected_job.get("id") != job_id or connected_job.get("provider_call_id") != candidate
                        or connected_job.get("provider") != "clawops"):
                    raise ValueError("STREAM_NOT_AUTHORIZED")
                job = connected_job
                trusted_call = candidate
            async with asyncio.timeout(settings.max_call_seconds):
                async with connector(settings) as upstream:
                    socket = ClawOpsMediaSocket(websocket, stream_id, raw_call, settings.clawops_account_id)
                    await CallBridge(socket, upstream, get_repository(), settings, job, stream_id, trusted_call).run()
        except asyncio.CancelledError:
            error_code = "GATEWAY_SHUTDOWN"
            raise
        except WebSocketDisconnect:
            pass
        except TimeoutError:
            error_code = "CALL_TIME_LIMIT"
        except BridgeProtocolError as error:
            error_code = "DO_NOT_CALL_SAVE_FAILED" if str(error) == "DO_NOT_CALL_SAVE_FAILED" else "MEDIA_PROTOCOL_ERROR"
        except (ValueError, TypeError, AttributeError):
            error_code = "MEDIA_PROTOCOL_ERROR"
        except Exception:
            error_code = "VOICE_BRIDGE_ERROR"
        finally:
            if trusted_call:
                if error_code:
                    try:
                        await asyncio.wait_for(_worker(get_repository(), "bridge_error", {
                            "job_id": job_id, "provider_call_id": trusted_call, "error_code": error_code,
                        }), timeout=5)
                    except Exception:
                        logger.warning("voice_bridge_state_unconfirmed")
            # Close WS first: Connect without action also ends the PSTN leg.
            try:
                await websocket.close(code=1000 if trusted_call else 1008)
            except (RuntimeError, WebSocketDisconnect):
                pass
            if trusted_call:
                try:
                    await asyncio.wait_for(asyncio.to_thread(call_provider.hangup, trusted_call), timeout=20)
                except Exception:
                    logger.warning("voice_hangup_unconfirmed")
            if task is not None:
                active.discard(task)
