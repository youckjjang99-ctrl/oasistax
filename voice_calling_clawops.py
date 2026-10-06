"""ClawOps REST transport. Importing this module never places a call.

The worker must atomically claim a job before dispatch. A failed or malformed
response can leave a real call ringing: never automatically retry dispatch.
Reference: https://docs.claw-ops.com/api-reference/claw-ops-api/calls/create-call
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
from typing import Any, Mapping

import httpx

from voice_calling import normalize_phone
from voice_calling_provider import (
    CallRejected, UnknownCall, canonical_job_id, public_endpoint,
)


CLAWOPS_CALL_ID_RE = re.compile(r"CA[A-Za-z0-9_-]{8,100}\Z")
CLAWOPS_ACCOUNT_ID_RE = re.compile(r"AC[A-Za-z0-9_-]{8,100}\Z")
CLAWOPS_STREAM_ID_RE = re.compile(r"MZ[A-Za-z0-9_-]{8,100}\Z")
_CALLER_RE = re.compile(r"\+8270[0-9]{8}\Z")
_PREFIX = "clawops:"
_API_BASE = "https://api.claw-ops.com/v1/accounts"


def clawops_provider_call_id(raw_call_id: str) -> str:
    """Namespace a validated raw ClawOps call ID for storage/provider routing."""
    if not isinstance(raw_call_id, str) or not CLAWOPS_CALL_ID_RE.fullmatch(raw_call_id):
        raise ValueError("INVALID_CALL_ID")
    return _PREFIX + raw_call_id


def clawops_raw_call_id(provider_call_id: str) -> str:
    """Reject unnamespaced and other-provider IDs, including plausible CA IDs."""
    if not isinstance(provider_call_id, str) or not provider_call_id.startswith(_PREFIX):
        raise ValueError("INVALID_CALL_ID")
    raw = provider_call_id[len(_PREFIX):]
    clawops_provider_call_id(raw)
    return raw


def validate_clawops_signature(config: Any, path: str, params: dict[str, str],
                               signature: str) -> bool:
    """Verify HTTP callbacks against the configured URL, never request headers.

ClawOps uses Base64 HMAC-SHA256(URL + sorted key/value pairs). Its WSS
handshake is UNSIGNED; this helper must not be used to authenticate WSS.
Duplicate form keys must be rejected by the caller before building ``params``.
"""
    try:
        secret = config.clawops_signing_secret
        account = config.clawops_account_id
        if (config.provider != "clawops" or not isinstance(account, str)
                or not CLAWOPS_ACCOUNT_ID_RE.fullmatch(account)
                or not isinstance(secret, str) or not 32 <= len(secret) <= 4096
                or not isinstance(path, str) or not path.startswith("/voice/clawops/")
                or not isinstance(params, dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in params.items())
                or ("AccountId" in params and params["AccountId"] != account)
                or not isinstance(signature, str) or len(signature) != 44):
            return False
        url = public_endpoint(config, path)
        data = url + "".join(k + params[k] for k in sorted(params))
        digest = hmac.new(secret.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).digest()
        expected = base64.b64encode(digest)
        return hmac.compare_digest(expected, signature.encode("ascii"))
    except (AttributeError, ValueError, TypeError, UnicodeError):
        return False


class ClawOpsVoiceProvider:
    def __init__(self, config: Any, *, client: httpx.Client | None = None) -> None:
        self.config = config
        self._client = client

    def _credentials(self) -> None:
        account = getattr(self.config, "clawops_account_id", "")
        key = getattr(self.config, "clawops_api_key", "")
        if (self.config.provider != "clawops" or not isinstance(account, str)
                or not CLAWOPS_ACCOUNT_ID_RE.fullmatch(account)
                or not isinstance(key, str) or not 1 <= len(key) <= 4096
                or any(ord(char) < 33 or ord(char) > 126 for char in key)):
            raise CallRejected("PROVIDER_NOT_CONFIGURED")

    def _post(self, suffix: str, data: Mapping[str, Any]) -> httpx.Response:
        self._credentials()
        url = f"{_API_BASE}/{self.config.clawops_account_id}/calls{suffix}"
        kwargs = {
            "json": dict(data),
            "headers": {"Authorization": f"Bearer {self.config.clawops_api_key}"},
            "timeout": min(30, max(1, self.config.request_timeout_seconds)),
            "follow_redirects": False,
        }
        try:
            if self._client is not None:
                return self._client.post(url, **kwargs)
            with httpx.Client(trust_env=False) as client:
                return client.post(url, **kwargs)
        except httpx.HTTPError:
            # Exceptions can contain credentials, destinations and request URLs.
            raise UnknownCall("PROVIDER_RESPONSE_UNKNOWN") from None

    def start_call(self, job: Mapping[str, Any]) -> dict[str, str]:
        if self.config.enabled is not True:
            raise CallRejected("CALLING_DISABLED")
        self._credentials()
        if (self.config.readiness().get("ready") is not True
                or getattr(self.config, "clawops_billing_confirmed", False) is not True):
            raise CallRejected("PROVIDER_NOT_READY")
        try:
            destination = normalize_phone(job.get("phone_e164", ""))
            caller = normalize_phone(self.config.caller_id)
        except (ValueError, TypeError):
            raise CallRejected("INVALID_PHONE_CONFIGURATION") from None
        if not _CALLER_RE.fullmatch(caller):
            raise CallRejected("INVALID_PHONE_CONFIGURATION")
        # Ownership is enforced by ClawOps (From must belong to this account).
        # Before real-call acceptance tests pass, only exact E.164 test numbers
        # are permitted, regardless of other approval flags on a queued job.
        if getattr(self.config, "clawops_live_verified", False) is not True:
            allowlist = getattr(self.config, "clawops_test_numbers", ())
            try:
                allowed = (isinstance(allowlist, tuple) and 1 <= len(allowlist) <= 5
                           and destination in {normalize_phone(value) for value in allowlist})
            except (ValueError, TypeError):
                allowed = False
            if not allowed:
                raise CallRejected("TEST_DESTINATION_REQUIRED")
        try:
            job_id = canonical_job_id(job.get("id"))
            voice_url = public_endpoint(self.config, f"/voice/clawops/voiceml/{job_id}")
            status_url = public_endpoint(self.config, f"/voice/clawops/status/{job_id}")
        except ValueError:
            raise CallRejected("INVALID_JOB_CONFIGURATION") from None
        response = self._post("", {
            "To": "0" + destination[3:],
            "From": "0" + caller[3:],
            "Url": voice_url,
            "StatusCallback": status_url,
            "StatusCallbackEvent": "initiated ringing answered completed",
            "Timeout": 25,
        })
        # Explicit client errors are refusals. Timeout/client-closed responses
        # are ambiguous; never classify them as safe to redial.
        if 400 <= response.status_code < 500 and response.status_code not in {408, 499}:
            raise CallRejected("PROVIDER_REJECTED")
        if response.status_code != 201:
            raise UnknownCall("PROVIDER_RESPONSE_UNKNOWN")
        try:
            call_id = clawops_provider_call_id(response.json()["callId"])
        except (ValueError, KeyError, TypeError):
            raise UnknownCall("PROVIDER_RESPONSE_UNKNOWN") from None
        return {"provider_call_id": call_id}

    def hangup(self, provider_call_id: str) -> None:
        try:
            raw = clawops_raw_call_id(provider_call_id)
        except ValueError:
            raise CallRejected("INVALID_CALL_ID") from None
        # Emergency disable and incomplete readiness must never prevent cleanup.
        response = self._post(f"/{raw}", {"Status": "completed"})
        if response.status_code != 200:
            raise UnknownCall("PROVIDER_HANGUP_UNCONFIRMED")
