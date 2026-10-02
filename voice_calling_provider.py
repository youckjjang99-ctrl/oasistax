"""Optional Twilio adapter. Nothing is dialled at import time.

Only the dispatcher may call ``start_call`` after an atomic database claim.
An ambiguous create-call response is NEVER retried automatically: Twilio may
already be ringing the customer even if this process did not receive its SID.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import time
import uuid
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import httpx
from twilio.request_validator import RequestValidator


class CallRejected(RuntimeError):
    """The provider explicitly refused a request, or local validation failed."""


class UnknownCall(RuntimeError):
    """A call may exist. Require provider reconciliation, never redial."""


SID_RE = re.compile(r"CA[0-9a-fA-F]{32}\Z")
ACCOUNT_RE = re.compile(r"AC[0-9a-fA-F]{32}\Z")
E164_RE = re.compile(r"\+[1-9]\d{7,14}\Z")


def canonical_job_id(value: Any) -> str:
    try:
        parsed = str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("INVALID_JOB_ID") from None
    if str(value).lower() != parsed:
        raise ValueError("INVALID_JOB_ID")
    return parsed


def public_endpoint(config: Any, path: str, *, websocket: bool = False) -> str:
    """Do not build signing URLs from Host/Forwarded headers sent by a client."""
    parts = urlsplit(str(config.public_base_url))
    try:
        port = parts.port
    except ValueError:
        raise ValueError("INVALID_PUBLIC_BASE_URL") from None
    if (parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment
            or parts.path not in ("", "/") or port not in (None, 443)
            or parts.hostname in {"localhost", "127.0.0.1", "::1"}
            or not path.startswith("/voice/") or "?" in path or "#" in path):
        raise ValueError("INVALID_PUBLIC_BASE_URL")
    # The API receives exactly this URL; do not infer a scheme or public host.
    return urlunsplit(("wss" if websocket else "https", parts.netloc, path, "", ""))


def _signature_equal(token: str, url: str, params: Any, signature: str) -> bool:
    if not token or not signature or len(signature) > 128:
        return False
    try:
        expected = RequestValidator(token).compute_signature(url, params)
        return hmac.compare_digest(expected.encode("ascii"), signature.encode("ascii"))
    except (ValueError, TypeError, UnicodeError):
        return False


def validate_twilio_signature(config: Any, path: str, params: Any,
                              signature: str, *, websocket: bool = False) -> bool:
    try:
        url = public_endpoint(config, path, websocket=websocket)
    except ValueError:
        return False
    valid = _signature_equal(config.twilio_auth_token, url, params, signature)
    if websocket:
        # Twilio documents a trailing slash variant for Voice WSS handshakes.
        # Both forms remain bound to the same configured host and exact job.
        valid |= _signature_equal(config.twilio_auth_token, url + "/", params, signature)
    return valid


def issue_stream_ticket(config: Any, job_id: str, dispatch_nonce: str,
                        *, now: int | None = None, ttl_seconds: int = 600) -> str:
    job_id = canonical_job_id(job_id)
    nonce = canonical_job_id(dispatch_nonce)
    if len(config.stream_ticket_secret) < 32 or not 30 <= ttl_seconds <= 900:
        raise ValueError("INVALID_STREAM_TICKET_CONFIGURATION")
    expires = int(time.time() if now is None else now) + ttl_seconds
    payload = f"v1.{job_id}.{nonce}.{expires}"
    digest = hmac.new(config.stream_ticket_secret.encode(), payload.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return payload + "." + signature


def verify_stream_ticket(config: Any, ticket: Any, job_id: str,
                         *, now: int | None = None) -> str:
    """Return a signed nonce. The DB must atomically consume it once per job."""
    if not isinstance(ticket, str) or len(ticket) > 256 or len(config.stream_ticket_secret) < 32:
        raise ValueError("INVALID_STREAM_TICKET")
    try:
        version, target, nonce, expires, signature = ticket.split(".")
        timestamp = int(time.time() if now is None else now)
        if (version != "v1" or target != canonical_job_id(job_id)
                or not timestamp < int(expires) <= timestamp + 900):
            raise ValueError
        canonical_job_id(nonce)
        payload = ".".join((version, target, nonce, expires))
        digest = hmac.new(config.stream_ticket_secret.encode(), payload.encode(), hashlib.sha256).digest()
        expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        if not hmac.compare_digest(expected.encode("ascii"), signature.encode("ascii")):
            raise ValueError
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("INVALID_STREAM_TICKET") from None
    return nonce


def build_call_twiml(config: Any, job: Mapping[str, Any]) -> str:
    job_id = canonical_job_id(job.get("id"))
    ticket = issue_stream_ticket(config, job_id, str(job.get("dispatch_nonce", "")))
    response = ET.Element("Response")
    connection = ET.SubElement(response, "Connect")
    stream = ET.SubElement(connection, "Stream", {
        "url": public_endpoint(config, f"/voice/media/{job_id}", websocket=True),
    })
    ET.SubElement(stream, "Parameter", {"name": "ticket", "value": ticket})
    # Closing the WS must end the PSTN leg, even if the process cannot call REST.
    ET.SubElement(response, "Hangup")
    return ET.tostring(response, encoding="unicode")


class TwilioVoiceProvider:
    def __init__(self, config: Any, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client

    def _credentials(self) -> None:
        if (self.config.provider != "twilio"
                or not ACCOUNT_RE.fullmatch(self.config.twilio_account_sid)
                or len(self.config.twilio_auth_token) < 20):
            raise CallRejected("PROVIDER_NOT_CONFIGURED")

    def _post(self, suffix: str, data: Mapping[str, Any]) -> httpx.Response:
        self._credentials()
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.config.twilio_account_sid}/{suffix}"
        kwargs = {
            "data": dict(data),
            "auth": (self.config.twilio_account_sid, self.config.twilio_auth_token),
            "timeout": min(30, max(1, self.config.request_timeout_seconds)),
            "follow_redirects": False,
        }
        try:
            if self._client is not None:
                return self._client.post(url, **kwargs)
            with httpx.Client(trust_env=False) as client:
                return client.post(url, **kwargs)
        except httpx.HTTPError:
            # Never expose the exception's request URL/headers/body to UI/logs.
            raise UnknownCall("PROVIDER_RESPONSE_UNKNOWN") from None

    def start_call(self, job: Mapping[str, Any]) -> dict[str, str]:
        if not self.config.enabled:
            raise CallRejected("CALLING_DISABLED")
        self._credentials()
        if (not E164_RE.fullmatch(str(job.get("phone_e164", "")))
                or not E164_RE.fullmatch(self.config.caller_id)):
            raise CallRejected("INVALID_PHONE_CONFIGURATION")
        try:
            job_id = canonical_job_id(job.get("id"))
            twiml = build_call_twiml(self.config, job)
            callback = public_endpoint(self.config, f"/voice/twilio/status/{job_id}")
        except ValueError:
            raise CallRejected("INVALID_JOB_CONFIGURATION") from None
        result = self._post("Calls.json", {
            "To": job["phone_e164"], "From": self.config.caller_id,
            "Twiml": twiml, "StatusCallback": callback,
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            "Timeout": "25", "TimeLimit": str(min(300, max(60, self.config.max_call_seconds))),
            "Record": "false",
        })
        if result.status_code in {400, 401, 403, 404, 422, 429}:
            raise CallRejected("PROVIDER_REJECTED")
        if result.status_code != 201:
            raise UnknownCall("PROVIDER_RESPONSE_UNKNOWN")
        try:
            sid = result.json()["sid"]
            if not isinstance(sid, str) or not SID_RE.fullmatch(sid):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise UnknownCall("PROVIDER_RESPONSE_UNKNOWN") from None
        return {"provider_call_id": sid}

    def hangup(self, provider_call_id: str) -> None:
        if not SID_RE.fullmatch(provider_call_id):
            raise CallRejected("INVALID_CALL_ID")
        # Allowed with calls disabled: an emergency switch must not block cleanup.
        response = self._post(f"Calls/{provider_call_id}.json", {"Status": "completed"})
        if response.status_code not in {200, 204}:
            raise UnknownCall("PROVIDER_HANGUP_UNCONFIRMED")
