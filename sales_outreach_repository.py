from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any

from cloud_db import CloudDatabase


RPC_RESERVE = "oasis_reserve_prospect_outreach"
RPC_BEGIN = "oasis_begin_prospect_outreach_dispatch"
RPC_FINALIZE = "oasis_finalize_prospect_outreach"
RPC_LIST_HISTORY = "oasis_list_prospect_outreach_history"

_REQUEST_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
_HEX_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_CHANNELS = frozenset({"email", "sms", "kakao"})
_FINAL_STATUSES = frozenset(
    {"provider_accepted", "provider_rejected", "delivery_unknown"}
)
OUTREACH_HMAC_KEY_ENV = "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY"

_SAFE_MESSAGES = {
    "RESERVED": "발송 요청을 안전하게 예약했습니다.",
    "DISPATCH_STARTED": "발송 직전 안전 확인을 완료했습니다.",
    "FINALIZED": "발송 결과를 자동 이력에 저장했습니다.",
    "IDEMPOTENT_FINALIZE": "이미 같은 결과로 처리되었습니다.",
    "CONSENT_CONFIRMATION_REQUIRED": "수신동의와 수신거부 상태를 확인해 주세요.",
    "INVALID_REQUEST": "발송 요청값을 다시 확인해 주세요.",
    "IDEMPOTENCY_CONFLICT": (
        "같은 요청번호의 내용이 달라 안전하게 중단했습니다. "
        "창을 닫고 새로 작성해 주세요."
    ),
    "ALREADY_RESERVED": "이미 접수된 요청이 처리 중입니다. 중복 발송하지 않았습니다.",
    "ALREADY_DISPATCHING": (
        "이미 외부 발송 처리가 시작되었습니다. 중복 발송하지 않았습니다."
    ),
    "ALREADY_ACCEPTED": "이미 공급자가 접수한 요청입니다. 중복 발송하지 않았습니다.",
    "ALREADY_REJECTED": "이미 종료된 요청입니다. 새 발송은 목록에서 다시 선택해 주세요.",
    "DELIVERY_UNKNOWN": (
        "공급자 응답을 확정하지 못한 요청입니다. 재발송하지 말고 "
        "공급자 발송내역을 확인해 주세요."
    ),
    "DUPLICATE_OUTREACH": (
        "같은 업체와 채널의 요청이 처리 중이거나 최근 처리되었습니다. "
        "중복 발송하지 않았습니다."
    ),
    "ASSIGNMENT_CHANGED": "업체 배정 상태가 변경되어 발송을 중단했습니다.",
    "TARGET_NOT_OWNED": "현재 담당 중인 영업후보가 아닙니다.",
    "CONTACT_CHANGED": "발송 연락처가 변경되었거나 사용할 수 없습니다.",
    "RECIPIENT_BINDING_CHANGED": "발송 수신처 결속정보가 변경되어 중단했습니다.",
    "TARGET_CHANGED": "배정 또는 연락처가 변경되어 발송을 취소했습니다.",
    "DO_NOT_CONTACT": "수신거부 또는 연락제외 업체라 발송할 수 없습니다.",
    "DNC_CANCELLED": "발송 직전 수신거부가 확인되어 자동 취소했습니다.",
    "RESERVATION_EXPIRED": "발송 예약 시간이 지나 새 요청이 필요합니다.",
    "RESERVATION_NOT_FOUND": "발송 예약정보가 없거나 권한이 없습니다.",
    "TERMINAL_STATE": "이미 종료되거나 취소된 요청이라 결과를 변경하지 않았습니다.",
    "INVALID_STATUS": "발송 결과 상태를 확인할 수 없습니다.",
    "OUTBOX_UNAVAILABLE": (
        "자동 발송 이력에 연결하지 못해 안전하게 중단했습니다. "
        "같은 요청을 다시 보내지 마세요."
    ),
}


def _outreach_hmac(domain: str, *values: Any) -> str:
    key = str(os.environ.get(OUTREACH_HMAC_KEY_ENV, "") or "").strip()
    if len(key) < 32:
        raise RuntimeError("발송 중복방지 보안키가 설정되지 않았습니다.")
    normalized = "\x00".join(
        str(value or "").replace("\r\n", "\n").strip()
        for value in values
    )
    payload = f"oasis-outreach:{domain}:v1\x00{normalized}".encode("utf-8")
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def message_fingerprint(channel: Any, subject: Any, body: Any) -> str:
    """Build a secret-key content HMAC without retaining message text."""

    return _outreach_hmac(
        "content",
        str(channel or "").strip().lower(),
        subject,
        body,
    )


def recipient_fingerprint(channel: Any, recipient: Any) -> str:
    """Bind an attempt to a recipient while keeping the value out of storage."""

    return _outreach_hmac(
        "recipient",
        str(channel or "").strip().lower(),
        str(recipient or "").strip().lower(),
    )


def _single_row(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return dict(payload[0]) if payload and isinstance(payload[0], dict) else {}
    return dict(payload) if isinstance(payload, dict) else {}


def _safe_result(row: dict[str, Any], *, fallback_code: str) -> dict[str, Any]:
    code = re.sub(
        r"[^A-Z0-9_-]",
        "_",
        str(row.get("code") or fallback_code).upper(),
    )[:80]
    result = {
        "ok": bool(row.get("success")),
        "code": code,
        "message": _SAFE_MESSAGES.get(code, _SAFE_MESSAGES[fallback_code]),
        "outbox_id": str(row.get("outbox_id") or ""),
        "status": str(row.get("status") or "").strip().lower(),
        "acquired": bool(row.get("acquired")),
        "dispatch_started": bool(row.get("dispatch_started")),
        "reservation_token": str(row.get("reservation_token") or ""),
        "reserved_at": str(row.get("reserved_at") or ""),
        "finalized_at": str(row.get("finalized_at") or ""),
    }
    return result


def _invalid(message: str = "발송 요청값을 다시 확인해 주세요.") -> dict[str, Any]:
    return {
        "ok": False,
        "code": "INVALID_REQUEST",
        "message": message,
        "outbox_id": "",
        "status": "",
        "acquired": False,
        "dispatch_started": False,
        "reservation_token": "",
    }


def reserve_outreach_attempt(
    current_user_id: Any,
    request_id: Any,
    content_hmac: Any,
    recipient_hmac: Any,
    assignment_id: Any,
    prospect_id: Any,
    company_uid: Any,
    contact_id: Any,
    contact_updated_at: Any,
    channel: Any,
    *,
    recipient_phone_hash: Any = "",
    consent_confirmed: bool = False,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = str(current_user_id or "").strip().lower()
    clean_request = str(request_id or "").strip()
    clean_content_hmac = str(content_hmac or "").strip().lower()
    clean_recipient_hmac = str(recipient_hmac or "").strip().lower()
    clean_channel = str(channel or "").strip().lower()
    clean_phone_hash = str(recipient_phone_hash or "").strip().lower()
    ids = tuple(
        str(value or "").strip()
        for value in (assignment_id, prospect_id, contact_id)
    )
    if (
        not actor
        or not _REQUEST_PATTERN.fullmatch(clean_request)
        or not _HEX_DIGEST_PATTERN.fullmatch(clean_content_hmac)
        or not _HEX_DIGEST_PATTERN.fullmatch(clean_recipient_hmac)
        or clean_channel not in _CHANNELS
        or not all(_UUID_PATTERN.fullmatch(value) for value in ids)
        or not str(company_uid or "").strip()
        or not str(contact_updated_at or "").strip()
        or (
            clean_channel in {"sms", "kakao"}
            and not _HEX_DIGEST_PATTERN.fullmatch(clean_phone_hash)
        )
        or (clean_channel == "email" and bool(clean_phone_hash))
    ):
        return _invalid()
    if not consent_confirmed:
        return {
            **_invalid(_SAFE_MESSAGES["CONSENT_CONFIRMATION_REQUIRED"]),
            "code": "CONSENT_CONFIRMATION_REQUIRED",
        }
    try:
        raw = (db or CloudDatabase()).rpc(
            RPC_RESERVE,
            {
                "p_current_user_id": actor,
                "p_request_id": clean_request,
                "p_content_hmac": clean_content_hmac,
                "p_recipient_hmac": clean_recipient_hmac,
                "p_assignment_id": ids[0],
                "p_prospect_id": ids[1],
                "p_company_uid": str(company_uid or "").strip(),
                "p_contact_id": ids[2],
                "p_contact_updated_at": str(contact_updated_at or "").strip(),
                "p_channel": clean_channel,
                "p_recipient_phone_hash": clean_phone_hash,
            },
        )
    except Exception:
        return {
            **_invalid(_SAFE_MESSAGES["OUTBOX_UNAVAILABLE"]),
            "code": "OUTBOX_UNAVAILABLE",
        }
    return _safe_result(_single_row(raw), fallback_code="OUTBOX_UNAVAILABLE")


def begin_outreach_dispatch(
    current_user_id: Any,
    outbox_id: Any,
    reservation_token: Any,
    *,
    recipient_hmac: Any,
    recipient_phone_hash: Any = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = str(current_user_id or "").strip().lower()
    clean_outbox_id = str(outbox_id or "").strip()
    clean_token = str(reservation_token or "").strip()
    clean_recipient_hmac = str(recipient_hmac or "").strip().lower()
    clean_phone_hash = str(recipient_phone_hash or "").strip().lower()
    if (
        not actor
        or not _UUID_PATTERN.fullmatch(clean_outbox_id)
        or not _UUID_PATTERN.fullmatch(clean_token)
        or not _HEX_DIGEST_PATTERN.fullmatch(clean_recipient_hmac)
        or (clean_phone_hash and not _HEX_DIGEST_PATTERN.fullmatch(clean_phone_hash))
    ):
        return _invalid("발송 예약정보를 확인할 수 없습니다.")
    try:
        raw = (db or CloudDatabase()).rpc(
            RPC_BEGIN,
            {
                "p_current_user_id": actor,
                "p_outbox_id": clean_outbox_id,
                "p_reservation_token": clean_token,
                "p_recipient_hmac": clean_recipient_hmac,
                "p_recipient_phone_hash": clean_phone_hash,
            },
        )
    except Exception:
        return {
            **_invalid(_SAFE_MESSAGES["OUTBOX_UNAVAILABLE"]),
            "code": "OUTBOX_UNAVAILABLE",
        }
    return _safe_result(_single_row(raw), fallback_code="OUTBOX_UNAVAILABLE")


def finalize_outreach_attempt(
    current_user_id: Any,
    outbox_id: Any,
    reservation_token: Any,
    status: Any,
    *,
    safe_result_code: Any = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = str(current_user_id or "").strip().lower()
    clean_outbox_id = str(outbox_id or "").strip()
    clean_token = str(reservation_token or "").strip()
    clean_status = str(status or "").strip().lower()
    clean_code = re.sub(
        r"[^A-Z0-9_-]",
        "_",
        str(safe_result_code or "").upper(),
    )[:80]
    if (
        not actor
        or not _UUID_PATTERN.fullmatch(clean_outbox_id)
        or not _UUID_PATTERN.fullmatch(clean_token)
        or clean_status not in _FINAL_STATUSES
    ):
        return _invalid("발송 결과 상태를 확인할 수 없습니다.")
    try:
        raw = (db or CloudDatabase()).rpc(
            RPC_FINALIZE,
            {
                "p_current_user_id": actor,
                "p_outbox_id": clean_outbox_id,
                "p_reservation_token": clean_token,
                "p_status": clean_status,
                "p_safe_result_code": clean_code,
            },
        )
    except Exception:
        return {
            **_invalid(_SAFE_MESSAGES["OUTBOX_UNAVAILABLE"]),
            "code": "OUTBOX_UNAVAILABLE",
        }
    return _safe_result(_single_row(raw), fallback_code="OUTBOX_UNAVAILABLE")


def list_outreach_history(
    current_user_id: Any,
    company_uid: Any = "",
    *,
    limit: int = 100,
    offset: int = 0,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = str(current_user_id or "").strip().lower()
    if not actor:
        return {"ok": False, "code": "INVALID_REQUEST", "history": []}
    try:
        raw = (db or CloudDatabase()).rpc(
            RPC_LIST_HISTORY,
            {
                "p_current_user_id": actor,
                "p_company_uid": str(company_uid or "").strip(),
                "p_limit": max(1, min(int(limit), 500)),
                "p_offset": max(0, int(offset)),
            },
        )
    except Exception:
        return {
            "ok": False,
            "code": "OUTBOX_UNAVAILABLE",
            "message": _SAFE_MESSAGES["OUTBOX_UNAVAILABLE"],
            "history": [],
        }
    rows = raw if isinstance(raw, list) else []
    allowed = {
        "outbox_id",
        "channel",
        "status",
        "safe_result_code",
        "reserved_at",
        "dispatch_started_at",
        "finalized_at",
    }
    history = [
        {key: row.get(key) for key in allowed}
        for row in rows
        if isinstance(row, dict)
    ]
    return {"ok": True, "code": "READY", "history": history}


__all__ = [
    "begin_outreach_dispatch",
    "finalize_outreach_attempt",
    "list_outreach_history",
    "message_fingerprint",
    "recipient_fingerprint",
    "reserve_outreach_attempt",
]
