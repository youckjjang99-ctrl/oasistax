"""Trusted launch boundary for the simple admin screens; never dials a provider.

Configuration comes from the server environment. SQL independently verifies the
current administrator, consent, suppression and idempotency inside a transaction.
No request field can override readiness, actor identity or a daily calling limit.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from voice_calling import VoiceSettings, normalize_phone

_ERRORS = {
    "INVALID_INPUT": "입력 항목과 발신 승인 체크를 확인해 주세요.",
    "CONSENT_REQUIRED": "번호별 안내 동의 근거와 유효기간을 확인해 주세요.",
    "CALLING_DISABLED_OR_NOT_READY": "현재 실제 발신이 중지되어 있습니다. 전화 서비스와 필수 설정을 먼저 확인해 주세요.",
    "TEST_ONLY": "내부 시험 모드입니다. 개별 전화걸기에서 서버에 등록한 시험번호만 사용할 수 있습니다.",
    "NOT_READY": "발신 요청 결과를 확인하지 못했습니다. 동일한 요청으로 다시 확인해 주세요.",
}
_FILTERS = {
    "business_type": {"all", "individual", "corporate", "unknown"},
    "phone_type": {"all", "mobile", "landline"},
    "discovery_type": {"all", "employment_growth", "new", "other", "unknown"},
}


def _error(code: str) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": _ERRORS[code]}


def _text(value: Any, maximum: int, *, minimum: int = 0) -> str:
    if not isinstance(value, str) or re.search(r"[\x00-\x1f\x7f]", value):
        raise ValueError("INVALID_INPUT")
    value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise ValueError("INVALID_INPUT")
    return value


def _base(actor: str, payload: Mapping[str, Any]) -> str:
    _text(actor, 200, minimum=1)
    if not isinstance(payload, Mapping) or payload.get("approval_confirmed") is not True:
        raise ValueError("INVALID_INPUT")
    request_id = payload.get("request_id", "")
    if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{7,119}", request_id):
        raise ValueError("INVALID_INPUT")
    return request_id


def _instant(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError("CONSENT_REQUIRED")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ValueError("CONSENT_REQUIRED")
        return date.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("CONSENT_REQUIRED") from None


def _ready(settings: VoiceSettings | None) -> tuple[VoiceSettings, dict[str, Any]]:
    config = settings if settings is not None else VoiceSettings.from_environment()
    return config, config.readiness()


def _submit(repo: Any, actor: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = repo.action(actor, action, payload)
        if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
            return _error("NOT_READY")
        return result
    except Exception:
        # Provider/database exceptions may embed identifiers; never echo them.
        return _error("NOT_READY")


def launch_filtered_calls(repo: Any, actor: str, payload: Mapping[str, Any],
                          settings: VoiceSettings | None = None) -> dict[str, Any]:
    """Atomically select/enqueue/approve up to 1,000 eligible, distinct targets."""
    _, ready = _ready(settings)
    if not ready["ready"]:
        return _error("CALLING_DISABLED_OR_NOT_READY")
    if ready.get("test_only"):
        return _error("TEST_ONLY")
    try:
        request_id = _base(actor, payload)
        count = payload.get("requested_count")
        if type(count) is not int or not 1 <= count <= 1000:
            raise ValueError("INVALID_INPUT")
        filters = payload.get("filters")
        if not isinstance(filters, Mapping) or set(filters) - {*_FILTERS, "region", "industry"}:
            raise ValueError("INVALID_INPUT")
        clean = {}
        for key, allowed in _FILTERS.items():
            value = filters.get(key, "all")
            if not isinstance(value, str) or value not in allowed:
                raise ValueError("INVALID_INPUT")
            clean[key] = value
        clean["region"] = _text(filters.get("region", ""), 80)
        clean["industry"] = _text(filters.get("industry", ""), 120)
    except (TypeError, ValueError, AttributeError):
        return _error("INVALID_INPUT")
    return _submit(repo, actor, "filtered_campaign", {
        "request_id": request_id, "filters": clean, "requested_count": count,
        "approval_confirmed": True,
    })


def launch_manual_call(repo: Any, actor: str, payload: Mapping[str, Any],
                       settings: VoiceSettings | None = None) -> dict[str, Any]:
    """Create a separate immutable manual snapshot and one approved queue job."""
    config, ready = _ready(settings)
    if not ready["ready"]:
        return _error("CALLING_DISABLED_OR_NOT_READY")
    try:
        request_id = _base(actor, payload)
        name = _text(payload.get("company_name"), 160, minimum=1)
        business_type = payload.get("business_type")
        purpose = payload.get("purpose")
        if business_type not in ("individual", "corporate", "unknown") or purpose not in ("test", "customer_guidance"):
            raise ValueError("INVALID_INPUT")
        raw_phone = _text(payload.get("phone"), 32, minimum=8)
        phone = normalize_phone(raw_phone)
        if ready.get("test_only"):
            allowed = {normalize_phone(x) for x in config.clawops_test_numbers}
            if purpose != "test" or phone not in allowed:
                return _error("TEST_ONLY")
        clean = {key: _text(payload.get(key, ""), maximum) for key, maximum in (
            ("representative_name", 80), ("address", 300), ("region", 80), ("industry", 120),
        )}
        if payload.get("consent_confirmed") is not True or payload.get("consent_kind") not in ("explicit_consent", "callback_request"):
            return _error("CONSENT_REQUIRED")
        try:
            evidence = _text(payload.get("evidence_ref"), 500, minimum=3)
        except ValueError:
            return _error("CONSENT_REQUIRED")
        granted = _instant(payload.get("granted_at"))
        expires = _instant(payload.get("expires_at"))
        now = datetime.now(timezone.utc)
        if not granted <= now < expires or expires <= granted:
            return _error("CONSENT_REQUIRED")
        # Existing consent policy: at most two calendar years (callback: 31 days).
        try:
            maximum = now.replace(year=now.year + 2)
        except ValueError:
            maximum = now.replace(year=now.year + 2, day=28)
        if expires > maximum or (payload["consent_kind"] == "callback_request" and expires > now + timedelta(days=31)):
            return _error("CONSENT_REQUIRED")
    except (TypeError, ValueError, AttributeError) as exc:
        return _error("CONSENT_REQUIRED" if str(exc) == "CONSENT_REQUIRED" else "INVALID_INPUT")
    return _submit(repo, actor, "manual_call", {
        **clean, "request_id": request_id, "company_name": name,
        "business_type": business_type, "phone": phone, "purpose": purpose,
        "consent_confirmed": True, "consent_kind": payload["consent_kind"],
        "evidence_ref": evidence, "granted_at": granted.isoformat(),
        "expires_at": expires.isoformat(), "approval_confirmed": True,
    })
