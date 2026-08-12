from __future__ import annotations

import re
from typing import Any

from cloud_db import CloudDatabase


RPC_REGISTER = "oasis_register_direct_sales_customer"
RPC_SUMMARY = "oasis_get_direct_sales_customer_summary"
RPC_LIST = "oasis_list_direct_sales_customers_v2"
RPC_RESERVE = "oasis_reserve_direct_customer_outreach"
RPC_BEGIN = "oasis_begin_direct_customer_outreach"
RPC_FINALIZE = "oasis_finalize_direct_customer_outreach"
RPC_HISTORY = "oasis_list_direct_customer_outreach_history"

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,200}$")
_CHANNELS = frozenset({"sms", "kakao"})
_FINAL_STATUSES = frozenset(
    {"provider_accepted", "provider_rejected", "delivery_unknown"}
)

_SAFE_MESSAGES = {
    "REGISTERED": "등록 DB에 업체를 추가했습니다.",
    "UPDATED": "기존 등록 DB 업체정보를 갱신했습니다.",
    "REVIEW_REQUIRED": (
        "다른 담당자가 먼저 등록한 사업자번호여서 관리자 검토를 요청했습니다."
    ),
    "INVALID_REQUEST": "입력값을 다시 확인해 주세요.",
    "CUSTOMER_SAVE_FAILED": "고객 원장에 안전하게 연결하지 못했습니다.",
    "DIRECT_DB_UNAVAILABLE": "계약/등록 DB에 안전하게 연결하지 못했습니다.",
    "RESERVED": "발송 요청을 안전하게 예약했습니다.",
    "DISPATCH_STARTED": "발송 직전 안전 확인을 완료했습니다.",
    "FINALIZED": "발송 결과를 자동 이력에 저장했습니다.",
    "ALREADY_RESERVED": "이미 접수된 요청입니다. 중복 발송하지 않았습니다.",
    "IDEMPOTENCY_CONFLICT": "같은 요청번호의 대상이 달라 발송을 중단했습니다.",
    "DUPLICATE_OUTREACH": (
        "같은 연락처와 채널의 요청이 처리 중이거나 최근 처리되었습니다."
    ),
    "TARGET_CHANGED": "업체 연락처 또는 수신동의 상태가 변경되었습니다.",
    "DO_NOT_CONTACT": "수신거부 또는 연락제외 업체라 발송할 수 없습니다.",
    "DNC_CANCELLED": "발송 직전 수신거부가 확인되어 자동 취소했습니다.",
    "RESERVATION_NOT_FOUND": "발송 예약정보가 없거나 권한이 없습니다.",
    "RESERVATION_EXPIRED": "발송 예약 시간이 지나 새 요청이 필요합니다.",
    "RECIPIENT_BINDING_CHANGED": "발송 수신처 확인정보가 변경되었습니다.",
    "OUTBOX_UNAVAILABLE": "자동 발송 이력에 연결하지 못해 안전하게 중단했습니다.",
}


def _actor(value: Any) -> str:
    return str(value or "").strip().lower()


def _single_row(payload: Any) -> dict[str, Any]:
    if isinstance(payload, list):
        return dict(payload[0]) if payload and isinstance(payload[0], dict) else {}
    return dict(payload) if isinstance(payload, dict) else {}


def _safe_code(value: Any, fallback: str = "INVALID_REQUEST") -> str:
    code = re.sub(r"[^A-Z0-9_-]", "_", str(value or fallback).upper())[:80]
    return code or fallback


def _safe_result(payload: Any, *, fallback: str) -> dict[str, Any]:
    row = _single_row(payload)
    code = _safe_code(row.get("code"), fallback)
    return {
        "ok": bool(row.get("success")),
        "code": code,
        "message": _SAFE_MESSAGES.get(
            code,
            str(row.get("message") or _SAFE_MESSAGES.get(fallback, "처리하지 못했습니다.")),
        ),
        "direct_customer_id": str(row.get("direct_customer_id") or ""),
        "customer_id": str(row.get("customer_id") or ""),
        "outbox_id": str(row.get("outbox_id") or ""),
        "status": str(row.get("status") or "").strip().lower(),
        "acquired": bool(row.get("acquired")),
        "dispatch_started": bool(row.get("dispatch_started")),
        "reservation_token": str(row.get("reservation_token") or ""),
        "reserved_at": str(row.get("reserved_at") or ""),
        "finalized_at": str(row.get("finalized_at") or ""),
    }


def _error(code: str = "INVALID_REQUEST") -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "message": _SAFE_MESSAGES.get(code, _SAFE_MESSAGES["INVALID_REQUEST"]),
        "direct_customer_id": "",
        "customer_id": "",
        "outbox_id": "",
        "status": "",
        "acquired": False,
        "dispatch_started": False,
        "reservation_token": "",
    }


def register_direct_customer(
    current_user_id: Any,
    customer: dict[str, Any],
    *,
    mobile_phone_hash: str = "",
    manager_name: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = _actor(current_user_id)
    if not actor or not isinstance(customer, dict):
        return _error()
    try:
        employee_count = max(0, int(customer.get("employee_count") or 0))
    except (TypeError, ValueError):
        return _error()
    parameters = {
        "p_current_user_id": actor,
        "p_company_name": str(customer.get("company_name") or "").strip(),
        "p_business_no": str(customer.get("business_no") or "").strip(),
        "p_business_type": str(customer.get("business_type") or "").strip(),
        "p_representative_name": str(
            customer.get("representative_name") or ""
        ).strip(),
        "p_landline_phone": str(customer.get("landline_phone") or "").strip(),
        "p_mobile_phone": str(customer.get("mobile_phone") or "").strip(),
        "p_mobile_phone_hash": str(mobile_phone_hash or "").strip().lower(),
        "p_industry_name": str(customer.get("industry_name") or "").strip(),
        "p_address": str(customer.get("address") or "").strip(),
        "p_employee_count": employee_count,
        "p_acquisition_source": str(
            customer.get("acquisition_source") or ""
        ).strip(),
        "p_registration_memo": str(
            customer.get("registration_memo") or ""
        ).strip(),
        "p_marketing_consent_confirmed": bool(
            customer.get("marketing_consent_confirmed")
        ),
        "p_marketing_consent_method": str(
            customer.get("marketing_consent_method") or ""
        ).strip(),
        "p_manager_name": str(manager_name or "").strip(),
    }
    try:
        raw = (db or CloudDatabase()).rpc(RPC_REGISTER, parameters)
    except Exception:
        return _error("DIRECT_DB_UNAVAILABLE")
    return _safe_result(raw, fallback="CUSTOMER_SAVE_FAILED")


def get_direct_customer_summary(
    current_user_id: Any,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = _actor(current_user_id)
    if not actor:
        return {"ok": False, "total": 0, "registered": 0, "contracted": 0}
    try:
        row = _single_row(
            (db or CloudDatabase()).rpc(
                RPC_SUMMARY,
                {"p_current_user_id": actor},
            )
        )
    except Exception:
        return {"ok": False, "total": 0, "registered": 0, "contracted": 0}
    return {
        "ok": True,
        "total": int(row.get("total_count") or 0),
        "registered": int(row.get("registered_count") or 0),
        "contracted": int(row.get("contracted_count") or 0),
    }


def list_direct_customers(
    current_user_id: Any,
    *,
    category: str = "all",
    direct_customer_id: str = "",
    limit: int = 500,
    offset: int = 0,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = _actor(current_user_id)
    clean_id = str(direct_customer_id or "").strip()
    clean_category = str(category or "all").strip().lower()
    if clean_category not in {"all", "registered", "contracted"}:
        clean_category = "all"
    if not actor or (clean_id and not _UUID_PATTERN.fullmatch(clean_id)):
        return {"ok": False, "customers": [], "total_count": 0}
    try:
        raw = (db or CloudDatabase()).rpc(
            RPC_LIST,
            {
                "p_current_user_id": actor,
                "p_filter": clean_category,
                "p_direct_customer_id": clean_id or None,
                "p_limit": max(1, min(int(limit), 5000)),
                "p_offset": max(0, int(offset)),
            },
        )
    except Exception:
        return {
            "ok": False,
            "message": "계약/등록 DB를 안전하게 불러오지 못했습니다.",
            "customers": [],
            "total_count": 0,
        }
    allowed_fields = (
        "direct_customer_id",
        "customer_id",
        "company_uid",
        "company_name",
        "business_no",
        "representative_name",
        "business_type",
        "discovery_type",
        "landline_phone",
        "mobile_phone",
        "industry_name",
        "address",
        "employee_count",
        "acquisition_source",
        "registration_memo",
        "marketing_consent_confirmed",
        "marketing_consent_at",
        "marketing_consent_method",
        "crm_status",
        "sales_category",
        "created_at",
        "updated_at",
        "total_count",
    )
    rows = (
        [
            {field: row.get(field) for field in allowed_fields}
            for row in raw
            if isinstance(row, dict)
        ]
        if isinstance(raw, list)
        else []
    )
    return {
        "ok": True,
        "customers": rows,
        "total_count": int(rows[0].get("total_count") or 0) if rows else 0,
    }


def reserve_outreach_attempt(
    current_user_id: Any,
    request_id: Any,
    content_hmac: Any,
    recipient_hmac: Any,
    recipient_phone_hash: Any,
    direct_customer_id: Any,
    direct_customer_updated_at: Any,
    channel: Any,
    *,
    consent_confirmed: bool = False,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = _actor(current_user_id)
    request = str(request_id or "").strip()
    content = str(content_hmac or "").strip().lower()
    recipient = str(recipient_hmac or "").strip().lower()
    phone_hash = str(recipient_phone_hash or "").strip().lower()
    customer_id = str(direct_customer_id or "").strip()
    channel_value = str(channel or "").strip().lower()
    if (
        not consent_confirmed
        or not actor
        or not _REQUEST_PATTERN.fullmatch(request)
        or not _HEX_PATTERN.fullmatch(content)
        or not _HEX_PATTERN.fullmatch(recipient)
        or not _HEX_PATTERN.fullmatch(phone_hash)
        or not _UUID_PATTERN.fullmatch(customer_id)
        or not str(direct_customer_updated_at or "").strip()
        or channel_value not in _CHANNELS
    ):
        return _error()
    try:
        raw = (db or CloudDatabase()).rpc(
            RPC_RESERVE,
            {
                "p_current_user_id": actor,
                "p_request_id": request,
                "p_content_hmac": content,
                "p_recipient_hmac": recipient,
                "p_recipient_phone_hash": phone_hash,
                "p_direct_customer_id": customer_id,
                "p_direct_customer_updated_at": str(direct_customer_updated_at),
                "p_channel": channel_value,
            },
        )
    except Exception:
        return _error("OUTBOX_UNAVAILABLE")
    return _safe_result(raw, fallback="OUTBOX_UNAVAILABLE")


def begin_outreach_dispatch(
    current_user_id: Any,
    outbox_id: Any,
    reservation_token: Any,
    recipient_hmac: Any,
    recipient_phone_hash: Any,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = _actor(current_user_id)
    outbox = str(outbox_id or "").strip()
    token = str(reservation_token or "").strip()
    recipient = str(recipient_hmac or "").strip().lower()
    phone_hash = str(recipient_phone_hash or "").strip().lower()
    if (
        not actor
        or not _UUID_PATTERN.fullmatch(outbox)
        or not _UUID_PATTERN.fullmatch(token)
        or not _HEX_PATTERN.fullmatch(recipient)
        or not _HEX_PATTERN.fullmatch(phone_hash)
    ):
        return _error()
    try:
        raw = (db or CloudDatabase()).rpc(
            RPC_BEGIN,
            {
                "p_current_user_id": actor,
                "p_outbox_id": outbox,
                "p_reservation_token": token,
                "p_recipient_hmac": recipient,
                "p_recipient_phone_hash": phone_hash,
            },
        )
    except Exception:
        return _error("OUTBOX_UNAVAILABLE")
    return _safe_result(raw, fallback="OUTBOX_UNAVAILABLE")


def finalize_outreach_attempt(
    current_user_id: Any,
    outbox_id: Any,
    reservation_token: Any,
    status: Any,
    *,
    safe_result_code: Any = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = _actor(current_user_id)
    outbox = str(outbox_id or "").strip()
    token = str(reservation_token or "").strip()
    status_value = str(status or "").strip().lower()
    if (
        not actor
        or not _UUID_PATTERN.fullmatch(outbox)
        or not _UUID_PATTERN.fullmatch(token)
        or status_value not in _FINAL_STATUSES
    ):
        return _error()
    try:
        raw = (db or CloudDatabase()).rpc(
            RPC_FINALIZE,
            {
                "p_current_user_id": actor,
                "p_outbox_id": outbox,
                "p_reservation_token": token,
                "p_status": status_value,
                "p_safe_result_code": _safe_code(safe_result_code, ""),
            },
        )
    except Exception:
        return _error("OUTBOX_UNAVAILABLE")
    return _safe_result(raw, fallback="OUTBOX_UNAVAILABLE")


def list_outreach_history(
    current_user_id: Any,
    direct_customer_id: Any,
    *,
    limit: int = 100,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    actor = _actor(current_user_id)
    customer_id = str(direct_customer_id or "").strip()
    if not actor or not _UUID_PATTERN.fullmatch(customer_id):
        return {"ok": False, "history": []}
    try:
        raw = (db or CloudDatabase()).rpc(
            RPC_HISTORY,
            {
                "p_current_user_id": actor,
                "p_direct_customer_id": customer_id,
                "p_limit": max(1, min(int(limit), 500)),
            },
        )
    except Exception:
        return {"ok": False, "history": []}
    allowed_fields = (
        "outbox_id",
        "channel",
        "status",
        "safe_result_code",
        "reserved_at",
        "dispatch_started_at",
        "finalized_at",
    )
    rows = (
        [
            {field: row.get(field) for field in allowed_fields}
            for row in raw
            if isinstance(row, dict)
        ]
        if isinstance(raw, list)
        else []
    )
    return {"ok": True, "history": rows}


__all__ = [
    "begin_outreach_dispatch",
    "finalize_outreach_attempt",
    "get_direct_customer_summary",
    "list_direct_customers",
    "list_outreach_history",
    "register_direct_customer",
    "reserve_outreach_attempt",
]
