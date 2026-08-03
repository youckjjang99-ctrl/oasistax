from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from cloud_db import CloudDatabase
from solapi_alimtalk_client import guidance_send_readiness as solapi_guidance_readiness


GUIDANCE_MESSAGE_TYPES = ("employment_support", "policy_funding", "tax_credit")
GUIDANCE_MESSAGE_LABELS = {
    "employment_support": "고용지원금 안내",
    "policy_funding": "정책자금 안내",
    "tax_credit": "누락된 세액공제 안내",
}
GUIDANCE_BUTTON_LABELS = {
    "employment_support": "고용지원금 검토 신청",
    "policy_funding": "정책자금 검토 신청",
    "tax_credit": "세액공제 검토 신청",
}
GUIDANCE_TEMPLATE_ENV_BY_TYPE = {
    "employment_support": "SOLAPI_TEMPLATE_GUIDANCE_EMPLOYMENT_SUPPORT_ID",
    "policy_funding": "SOLAPI_TEMPLATE_GUIDANCE_POLICY_FUNDING_ID",
    "tax_credit": "SOLAPI_TEMPLATE_GUIDANCE_TAX_CREDIT_ID",
}
_PREVIEW_URL = "{{secure_review_url}}"
GUIDANCE_MESSAGE_PREVIEWS = {
    "employment_support": (
        "안녕하세요. 오아시스 세무회계입니다.\n\n"
        "개인사업자 운영 과정에서 확인해볼 수 있는\n"
        "고용지원 제도와 세제 혜택을 안내드립니다.\n\n"
        "지원 가능 여부는 고객님의 동의와 본인인증 후\n"
        "필요 자료를 확인하여 세무사가 검토해드립니다.\n\n"
        "아래 버튼에서 검토 절차를 확인하실 수 있습니다.\n\n"
        f"버튼명: 고용지원금 검토 신청\n링크: {_PREVIEW_URL}"
    ),
    "policy_funding": (
        "안녕하세요. 오아시스 세무회계입니다.\n\n"
        "개인사업자가 검토할 수 있는 정책자금과 지원제도 정보를 안내드립니다.\n\n"
        "실제 가능 여부와 진행 방향은 고객님의 동의 및 자료 확인 후\n"
        "세무사가 검토하여 안내드립니다.\n\n"
        "아래 버튼에서 검토 절차를 확인하실 수 있습니다.\n\n"
        f"버튼명: 정책자금 검토 신청\n링크: {_PREVIEW_URL}"
    ),
    "tax_credit": (
        "안녕하세요. 오아시스 세무회계입니다.\n\n"
        "사업 운영 중 적용 여부를 확인해볼 수 있는\n"
        "세액공제와 세제 혜택을 안내드립니다.\n\n"
        "누락 여부와 적용 가능성은 고객님의 동의와 자료 확인 후\n"
        "세무사가 검토하여 안내드립니다.\n\n"
        "아래 버튼에서 검토 절차를 확인하실 수 있습니다.\n\n"
        f"버튼명: 세액공제 검토 신청\n링크: {_PREVIEW_URL}"
    ),
}

SEND_ENABLED_ENV = "OASIS_KAKAO_GUIDANCE_SEND_ENABLED"
PROVIDER_MODE_ENV = "OASIS_KAKAO_GUIDANCE_PROVIDER_MODE"
PHONE_HASH_KEY_ENV = "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY"
DEFAULT_TEMPLATE_VERSION = "v1"
GUIDANCE_LINK_TTL_DAYS = 7

RPC_CHECK_ELIGIBILITY = "oasis_check_company_kakao_guidance_eligibility"
RPC_RESOLVE_MOBILE = "oasis_resolve_company_kakao_guidance_mobile"
RPC_RESERVE = "oasis_reserve_company_kakao_guidance"
RPC_ATTACH_INVITE = "oasis_attach_company_kakao_guidance_invite"
RPC_FINALIZE = "oasis_finalize_company_kakao_guidance"
RPC_CANCEL = "oasis_cancel_company_kakao_guidance"
RPC_CANCEL_FOR_INVITE = "oasis_cancel_company_kakao_guidance_for_invite"
RPC_LIST = "oasis_list_company_kakao_guidance"
RPC_ADMIN_LIST = "oasis_admin_list_company_kakao_guidance"
RPC_SET_CONTROL = "oasis_set_company_kakao_contact_control"
RPC_GET_SETTINGS = "oasis_get_company_kakao_guidance_settings"
RPC_UPDATE_SETTINGS = "oasis_update_company_kakao_guidance_settings"
RPC_CHECK_SEND_READY = "oasis_check_company_kakao_guidance_send_ready"

_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_COMPANY_UID = re.compile(
    r"^(?:business:[0-9]{10}|corporate:[0-9]{13}|nps:[A-Z0-9]+|"
    r"fallback:[0-9a-f]{64}|source:[0-9a-f]{64})$"
)
_MOBILE = re.compile(r"^01(?:0\d{8}|[16789]\d{7,8})$")
_BLOCKING_ASSIGNMENT_STATUSES = {
    "unassigned", "permanently_excluded", "closed", "wrong_number", "long_hold"
}


class CompanyKakaoGuidanceError(RuntimeError):
    """화면에 노출해도 개인정보가 섞이지 않는 안내 오류입니다."""

    def __init__(self, code: str, safe_message: str) -> None:
        clean = re.sub(r"[^A-Z0-9_]", "_", str(code or "").upper())
        self.code = re.sub(r"_+", "_", clean).strip("_")[:80] or "GUIDANCE_ERROR"
        super().__init__(str(safe_message or "안내 발송 요청을 처리하지 못했습니다."))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _digits(value: Any) -> str:
    digits = re.sub(r"\D", "", _text(value))
    return "0" + digits[2:] if digits.startswith("82") else digits


def _truthy(value: Any) -> bool:
    return _text(value).lower() in {"1", "true", "yes", "y", "on", "enabled"}


def _mapping_value(data: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return ""


def _normalized_business_type(company: Mapping[str, Any]) -> str:
    raw = _text(
        _mapping_value(company, ("business_type", "사업자유형", "사업자 유형", "사업자구분"))
    ).lower()
    # 후보/추정 값은 발송하지 않는다. 확인된 개인사업자 값만 허용한다.
    if raw in {"individual", "sole", "sole_proprietor", "개인", "개인사업자"}:
        return "individual"
    if raw in {"stock", "corporation", "corporate", "법인", "법인사업자", "주식회사"}:
        return "corporation"
    return "unknown"


def _mobile_from_company(company: Mapping[str, Any]) -> str:
    for key in ("mobile_phone", "휴대전화", "휴대전화번호", "대표전화", "phone"):
        phone = _digits(company.get(key))
        if _MOBILE.fullmatch(phone):
            return phone
    return ""


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _company_uid(company: Mapping[str, Any], assignment: Mapping[str, Any]) -> str:
    existing = _text(company.get("company_uid") or assignment.get("company_uid"))
    if _COMPANY_UID.fullmatch(existing):
        return existing
    try:
        from company_sales_assignment import build_company_uid

        return build_company_uid(company)
    except Exception:
        return ""


def _message_type(value: str) -> str:
    selected = _text(value).lower()
    if selected not in GUIDANCE_MESSAGE_TYPES:
        raise CompanyKakaoGuidanceError("INVALID_MESSAGE_TYPE", "안내 유형을 확인해 주세요.")
    return selected


def evaluate_guidance_eligibility(
    company: Mapping[str, Any],
    *,
    current_user_id: str,
    is_admin_user: bool = False,
    assignment: Mapping[str, Any] | None = None,
    contact_control: Mapping[str, Any] | None = None,
    recent_success_at: datetime | str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """DB 호출 없는 1차 검사입니다. 반환값에 전화번호를 넣지 않습니다."""

    company_row, assignment_row = dict(company or {}), dict(assignment or {})
    control = dict(contact_control or {})
    uid = _company_uid(company_row, assignment_row)
    assignment_id = _text(assignment_row.get("id") or assignment_row.get("assignment_id"))
    result: dict[str, Any] = {
        "eligible": False, "code": "NOT_ELIGIBLE",
        "message": "현재 안내 발송 대상이 아닙니다.",
        "company_uid": uid, "assignment_id": assignment_id, "retry_at": None,
    }
    if _normalized_business_type(company_row) != "individual":
        result.update(code="INDIVIDUAL_ONLY", message="확인된 개인사업자만 안내할 수 있습니다.")
        return result
    if not _mobile_from_company(company_row):
        result.update(code="MOBILE_REQUIRED", message="정상 형식의 휴대전화 번호가 필요합니다.")
        return result
    if not uid:
        result.update(code="COMPANY_UID_REQUIRED", message="업체 공통 식별값을 확인할 수 없습니다.")
        return result
    if any(bool(company_row.get(k)) for k in ("do_not_contact", "contact_excluded", "opted_out", "수신거부", "연락제외")):
        result.update(code="DO_NOT_CONTACT", message="수신거부 또는 연락제외 업체입니다.")
        return result
    if _text(control.get("status")).lower() in {"opted_out", "admin_blocked"}:
        result.update(code="DO_NOT_CONTACT", message="수신거부 또는 연락제외 업체입니다.")
        return result

    actor = _text(current_user_id).lower()
    assigned_user = _text(assignment_row.get("assigned_user_id")).lower()
    assignment_status = _text(assignment_row.get("status")).lower()
    if not is_admin_user:
        if not assignment_id or not assigned_user:
            result.update(code="ASSIGNMENT_REQUIRED", message="내 영업DB에 배정된 업체만 안내할 수 있습니다.")
            return result
        if assigned_user != actor:
            result.update(code="ASSIGNED_TO_OTHER", message="다른 담당자에게 배정된 업체입니다.")
            return result
        if assignment_status in _BLOCKING_ASSIGNMENT_STATUSES:
            result.update(code="ASSIGNMENT_BLOCKED", message="현재 배정 상태에서는 안내할 수 없습니다.")
            return result
    elif assigned_user and assigned_user != actor and assignment_status not in {"", "unassigned"}:
        result.update(code="ASSIGNED_TO_OTHER", message="다른 담당자에게 배정된 업체입니다.")
        return result

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    recent = _parse_time(recent_success_at)
    if recent and recent > current.astimezone(timezone.utc) - timedelta(days=7):
        result.update(
            code="DUPLICATE_WITHIN_7_DAYS",
            message="최근 7일 이내 같은 안내가 발송되어 중복 발송할 수 없습니다.",
            retry_at=(recent + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
        )
        return result
    result.update(eligible=True, code="ELIGIBLE", message="안내 발송이 가능합니다.")
    return result


def guidance_environment_readiness(environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """비밀값을 반환하지 않는 fail-closed 환경 점검입니다."""

    source = os.environ if environ is None else environ
    mode = _text(source.get(PROVIDER_MODE_ENV)).lower()
    if mode not in {"mock", "live"}:
        mode = "invalid"
    provider = solapi_guidance_readiness(
        source,
        required_template_env_names=tuple(
            GUIDANCE_TEMPLATE_ENV_BY_TYPE.values()
        ),
    )
    send_enabled = bool(provider.get("send_enabled"))
    hash_ready = len(_text(source.get(PHONE_HASH_KEY_ENV))) >= 32
    mock_mode = bool(mode == "mock" and provider.get("mock_mode"))
    external_ready = bool(
        mode == "live"
        and provider.get("external_send_allowed")
        and hash_ready
    )
    missing = list(provider.get("missing_env_names") or []) if mode == "live" else []
    if mode == "invalid":
        missing.append(PROVIDER_MODE_ENV)
    if mode == "mock" and not mock_mode:
        missing.append("OASIS_KAKAO_GUIDANCE_MOCK_MODE")
    if not hash_ready:
        missing.append(PHONE_HASH_KEY_ENV)
    if mode == "live" and not send_enabled:
        missing.append(SEND_ENABLED_ENV)
    ready = bool(hash_ready and (mock_mode or external_ready))
    return {
        "ready": ready, "provider_mode": mode, "send_enabled": send_enabled,
        "external_send_ready": external_ready, "mock_mode": mock_mode,
        "mock_mode_blocked_in_production": bool(
            provider.get("mock_mode_blocked_in_production")
        ),
        "phone_hash_key_configured": hash_ready,
        "templates_configured": bool(provider.get("template_ids_configured")),
        "missing_env_names": list(dict.fromkeys(missing)),
    }


def _phone_hash(phone: str, environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    key = _text(source.get(PHONE_HASH_KEY_ENV))
    if len(key) < 32:
        raise CompanyKakaoGuidanceError("PHONE_HASH_KEY_MISSING", "안내 발송 보안키가 설정되지 않았습니다.")
    normalized = _digits(phone)
    if not _MOBILE.fullmatch(normalized):
        raise CompanyKakaoGuidanceError("MOBILE_REQUIRED", "정상 형식의 휴대전화 번호가 필요합니다.")
    return hmac.new(key.encode("utf-8"), normalized.encode("ascii"), hashlib.sha256).hexdigest()


def _safe_rpc_failure(exc: Exception) -> CompanyKakaoGuidanceError:
    raw = str(exc or "").upper()
    messages = {
        "DUPLICATE_WITHIN_7_DAYS": "최근 7일 이내 같은 안내가 발송되어 중복 발송할 수 없습니다.",
        "DO_NOT_CONTACT": "수신거부 또는 연락제외 업체입니다.",
        "ASSIGNED_TO_OTHER": "다른 담당자에게 배정된 업체입니다.",
        "ASSIGNMENT_REQUIRED": "내 영업DB에 배정된 업체만 안내할 수 있습니다.",
        "PERMISSION_DENIED": "안내 발송 권한이 없습니다.",
        "DAILY_LIMIT_REACHED": "오늘 발송 한도에 도달했습니다.",
        "GUIDANCE_DISABLED": "관리자가 안내 발송을 비활성화했습니다.",
        "MESSAGE_NOT_FOUND": "안내 발송 이력을 찾을 수 없습니다.",
    }
    for code, message in messages.items():
        if code in raw:
            return CompanyKakaoGuidanceError(code, message)
    if "PGRST202" in raw or "SCHEMA CACHE" in raw:
        return CompanyKakaoGuidanceError("FEATURE_NOT_READY", "안내 발송 데이터베이스 설정이 필요합니다.")
    return CompanyKakaoGuidanceError("GUIDANCE_SERVICE_UNAVAILABLE", "안내 발송 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.")


def _rpc(name: str, parameters: dict[str, Any], db: CloudDatabase | None = None) -> Any:
    try:
        return (db or CloudDatabase()).rpc(name, parameters)
    except Exception as exc:
        raise _safe_rpc_failure(exc) from None


def _first_row(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, list) and raw and isinstance(raw[0], Mapping):
        return dict(raw[0])
    raise CompanyKakaoGuidanceError("MALFORMED_RESPONSE", "안내 발송 결과를 확인하지 못했습니다.")


def check_guidance_eligibility(*, current_user_id: str, company_uid: str, message_type: str, recipient_phone_hash: str, db: CloudDatabase | None = None) -> dict[str, Any]:
    return _first_row(_rpc(RPC_CHECK_ELIGIBILITY, {
        "p_current_user_id": _text(current_user_id).lower(), "p_company_uid": _text(company_uid),
        "p_message_type": _message_type(message_type), "p_recipient_phone_hash": _text(recipient_phone_hash).lower(),
    }, db))


def resolve_canonical_guidance_mobile(
    *, current_user_id: str, company_uid: str,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """서버가 선택한 발송 연락처를 반환합니다.

    호출자가 화면 행에 넣은 전화번호나 연락처 ID는 RPC 입력으로 사용하지
    않습니다. 서버는 현재 담당자, 업체 식별키, 수신거부 상태를 확인한 뒤
    실제 ``oasis_prospect_contacts`` 레코드와 휴대전화 번호를 함께 돌려줘야
    합니다. 원문 번호가 포함될 수 있으므로 이 함수의 결과는 UI/로그에
    출력하지 않고 발송 예약 내부에서만 사용합니다.
    """

    uid = _text(company_uid)
    if not _COMPANY_UID.fullmatch(uid):
        raise CompanyKakaoGuidanceError(
            "COMPANY_UID_REQUIRED", "업체 공통 식별값을 확인할 수 없습니다."
        )
    row = _first_row(_rpc(RPC_RESOLVE_MOBILE, {
        "p_current_user_id": _text(current_user_id).lower(),
        "p_company_uid": uid,
        # 현재 UI에서는 연락처를 사용자가 고르지 않습니다. 항상 서버가
        # 신뢰 가능한 대표 휴대전화를 결정하도록 NULL을 전달합니다.
        "p_contact_id": None,
    }, db))
    if not bool(row.get("success", row.get("ok", False))):
        code = _text(row.get("code") or "CANONICAL_CONTACT_NOT_FOUND").upper()
        safe_messages = {
            "ASSIGNMENT_REQUIRED": "내 영업DB에 배정된 업체만 안내할 수 있습니다.",
            "ASSIGNED_TO_OTHER": "다른 담당자에게 배정된 업체입니다.",
            "DO_NOT_CONTACT": "수신거부 또는 연락제외 업체입니다.",
            "INDIVIDUAL_ONLY": "확인된 개인사업자만 안내할 수 있습니다.",
            "MOBILE_REQUIRED": "정상 형식의 휴대전화 번호가 필요합니다.",
            "CONTACT_NOT_FOUND": "발송 가능한 휴대전화 연락처를 확인하지 못했습니다.",
            "CANONICAL_CONTACT_NOT_FOUND": "발송 가능한 휴대전화 연락처를 확인하지 못했습니다.",
            "PERMISSION_DENIED": "안내 발송 권한이 없습니다.",
        }
        raise CompanyKakaoGuidanceError(
            code, safe_messages.get(code, "발송 가능한 휴대전화 연락처를 확인하지 못했습니다.")
        )

    company_id = _text(row.get("company_id"))
    assignment_id = _text(row.get("assignment_id"))
    contact_id = _text(row.get("contact_id"))
    mobile_phone = _digits(row.get("mobile_phone"))
    contact_updated_at = _parse_time(row.get("contact_updated_at"))
    if not _UUID.fullmatch(company_id):
        raise CompanyKakaoGuidanceError(
            "MALFORMED_RESPONSE", "업체 연락처 확인 결과가 올바르지 않습니다."
        )
    if assignment_id and not _UUID.fullmatch(assignment_id):
        raise CompanyKakaoGuidanceError(
            "MALFORMED_RESPONSE", "업체 연락처 확인 결과가 올바르지 않습니다."
        )
    if (
        not _UUID.fullmatch(contact_id)
        or not _MOBILE.fullmatch(mobile_phone)
        or contact_updated_at is None
    ):
        raise CompanyKakaoGuidanceError(
            "MALFORMED_RESPONSE", "발송 가능한 휴대전화 연락처를 확인하지 못했습니다."
        )
    return {
        "company_id": company_id,
        "assignment_id": assignment_id,
        "contact_id": contact_id,
        # This non-PII row-version snapshot lets the database reject a live
        # send when the public delivery contact changes after reservation.
        "contact_updated_at": contact_updated_at.isoformat(),
        "mobile_phone": mobile_phone,
    }


def check_guidance_send_ready(
    guidance_message_id: str,
    canonical_contact_id: str,
    recipient_phone_hash: str,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """외부 제공자 호출 직전의 서버 상태를 fail-closed로 확인합니다."""

    message_id = _text(guidance_message_id)
    if not _UUID.fullmatch(message_id):
        raise CompanyKakaoGuidanceError(
            "INVALID_MESSAGE_ID", "안내 발송 이력을 확인하지 못했습니다."
        )
    contact_id = _text(canonical_contact_id)
    phone_hash = _text(recipient_phone_hash).lower()
    if not _UUID.fullmatch(contact_id) or not re.fullmatch(r"[0-9a-f]{64}", phone_hash):
        raise CompanyKakaoGuidanceError(
            "INVALID_DELIVERY_BINDING", "안내 발송 연락처 결속을 확인하지 못했습니다."
        )
    row = _first_row(_rpc(
        RPC_CHECK_SEND_READY,
        {
            "p_message_id": message_id,
            "p_contact_id": contact_id,
            "p_recipient_phone_hash": phone_hash,
        },
        db,
    ))
    # 문자열 "false" 같은 비정상 응답이 참으로 평가되는 일을 막습니다.
    if not isinstance(row.get("allowed"), bool):
        raise CompanyKakaoGuidanceError(
            "MALFORMED_RESPONSE", "안내 발송 상태를 확인하지 못했습니다."
        )
    return row


def evaluate_send_eligibility(
    company: Mapping[str, Any], *, current_user_id: str, assignment: Mapping[str, Any],
    message_type: str, is_admin_user: bool = False, db: CloudDatabase | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """UI용 최종 검사. HMAC 전화 지문도 반환하지 않습니다."""

    pure = evaluate_guidance_eligibility(
        company, current_user_id=current_user_id, assignment=assignment, is_admin_user=is_admin_user
    )
    if not pure["eligible"]:
        return pure
    server = check_guidance_eligibility(
        current_user_id=current_user_id, company_uid=pure["company_uid"],
        message_type=message_type, recipient_phone_hash=_phone_hash(_mobile_from_company(company), environ), db=db,
    )
    return {
        "eligible": bool(server.get("eligible", server.get("success", False))),
        "code": _text(server.get("code") or ("ELIGIBLE" if server.get("eligible") else "NOT_ELIGIBLE")),
        "message": _text(server.get("message") or pure["message"]),
        "company_uid": pure["company_uid"], "assignment_id": pure["assignment_id"],
        "retry_at": server.get("retry_at"),
    }


def reserve_guidance_message(
    *, current_user_id: str, company_id: str | None, company_uid: str, assignment_id: str | None,
    recipient_phone_hash: str, message_type: str, idempotency_key: str,
    contact_id: str | None = None,
    contact_updated_at: str | datetime | None = None,
    template_version: str = DEFAULT_TEMPLATE_VERSION, delivery_mode: str = "mock",
    session_id: str = "", db: CloudDatabase | None = None,
) -> dict[str, Any]:
    return _first_row(_rpc(RPC_RESERVE, {
        "p_current_user_id": _text(current_user_id).lower(), "p_company_id": _text(company_id) or None,
        "p_company_uid": _text(company_uid), "p_assignment_id": _text(assignment_id) or None,
        "p_contact_id": _text(contact_id) or None,
        "p_recipient_contact_updated_at": (
            _parse_time(contact_updated_at).isoformat()
            if _parse_time(contact_updated_at) is not None
            else None
        ),
        "p_recipient_phone_hash": _text(recipient_phone_hash).lower(), "p_message_type": _message_type(message_type),
        "p_template_key": _message_type(message_type), "p_template_version": _text(template_version)[:40] or DEFAULT_TEMPLATE_VERSION,
        "p_delivery_mode": "live" if _text(delivery_mode).lower() == "live" else "mock",
        "p_idempotency_key": _text(idempotency_key)[:200], "p_session_id": _text(session_id)[:200],
    }, db))


def attach_secure_review_invite(*, current_user_id: str, guidance_message_id: str, invite_id: str, db: CloudDatabase | None = None) -> dict[str, Any]:
    return _first_row(_rpc(RPC_ATTACH_INVITE, {
        "p_current_user_id": _text(current_user_id).lower(), "p_message_id": _text(guidance_message_id),
        "p_invite_id": _text(invite_id),
    }, db))


def finalize_guidance_message(
    *, current_user_id: str, guidance_message_id: str, status: str, provider_message_id: str = "",
    provider_group_id: str = "", error_code: str = "", error_summary: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    return _first_row(_rpc(RPC_FINALIZE, {
        "p_current_user_id": _text(current_user_id).lower(), "p_message_id": _text(guidance_message_id),
        "p_status": _text(status).lower(), "p_provider_message_id": _text(provider_message_id)[:200],
        "p_provider_group_id": _text(provider_group_id)[:200],
        "p_failure_code": re.sub(r"[^A-Z0-9_]", "_", _text(error_code).upper())[:80],
        "p_failure_summary": _text(error_summary)[:300],
    }, db))


def cancel_guidance_message(*, current_user_id: str, guidance_message_id: str, opt_out: bool = False, reason: str = "user_cancelled", db: CloudDatabase | None = None) -> dict[str, Any]:
    return _first_row(_rpc(RPC_CANCEL, {
        "p_current_user_id": _text(current_user_id).lower(), "p_message_id": _text(guidance_message_id),
        "p_opt_out": bool(opt_out), "p_reason": _text(reason)[:200],
    }, db))


def list_guidance_history(*, current_user_id: str, company_uid: str = "", limit: int = 100, offset: int = 0, db: CloudDatabase | None = None) -> list[dict[str, Any]]:
    raw = _rpc(RPC_LIST, {"p_current_user_id": _text(current_user_id).lower(), "p_company_uid": _text(company_uid), "p_limit": max(1, min(int(limit), 500)), "p_offset": max(0, int(offset))}, db)
    return [dict(row) for row in (raw or []) if isinstance(row, Mapping)]


def admin_list_guidance_history(*, current_user_id: str, status: str = "", message_type: str = "", limit: int = 200, offset: int = 0, db: CloudDatabase | None = None) -> list[dict[str, Any]]:
    raw = _rpc(RPC_ADMIN_LIST, {"p_current_user_id": _text(current_user_id).lower(), "p_status": _text(status).lower(), "p_message_type": _text(message_type).lower(), "p_limit": max(1, min(int(limit), 1000)), "p_offset": max(0, int(offset))}, db)
    return [dict(row) for row in (raw or []) if isinstance(row, Mapping)]


def set_guidance_contact_control(*, current_user_id: str, company_uid: str, recipient_phone_hash: str, status: str, reason: str = "", db: CloudDatabase | None = None) -> dict[str, Any]:
    return _first_row(_rpc(RPC_SET_CONTROL, {"p_current_user_id": _text(current_user_id).lower(), "p_company_uid": _text(company_uid), "p_recipient_phone_hash": _text(recipient_phone_hash).lower(), "p_status": _text(status).lower(), "p_reason": _text(reason)[:200]}, db))


def get_guidance_admin_settings(*, current_user_id: str, db: CloudDatabase | None = None) -> dict[str, Any]:
    return _first_row(_rpc(RPC_GET_SETTINGS, {"p_current_user_id": _text(current_user_id).lower()}, db))


def update_guidance_admin_settings(*, current_user_id: str, enabled: bool, daily_limit: int, reason: str, db: CloudDatabase | None = None) -> dict[str, Any]:
    return _first_row(_rpc(RPC_UPDATE_SETTINGS, {
        "p_current_user_id": _text(current_user_id).lower(), "p_enabled": bool(enabled),
        "p_daily_limit": max(0, min(int(daily_limit), 100000)), "p_reason": _text(reason)[:200],
    }, db))


def request_guidance_send(
    *, current_user_id: str, requested_by: str, company: Mapping[str, Any], assignment: Mapping[str, Any],
    message_type: str, is_admin_user: bool = False, idempotency_key: str = "", session_id: str = "",
    environ: Mapping[str, str] | None = None, db: CloudDatabase | None = None,
    invite_factory: Callable[..., Mapping[str, Any]] | None = None,
    outbox_repository_factory: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """원격 신청링크를 만들고 live에서만 암호화 발송 대기열을 생성합니다."""

    selected = _message_type(message_type)
    requested_company_uid = _company_uid(dict(company or {}), dict(assignment or {}))
    canonical = resolve_canonical_guidance_mobile(
        current_user_id=current_user_id,
        company_uid=requested_company_uid,
        db=db,
    )
    requested_assignment_id = _text(
        assignment.get("id") or assignment.get("assignment_id")
    )
    canonical_assignment_id = _text(canonical.get("assignment_id"))
    if requested_assignment_id and canonical_assignment_id != requested_assignment_id:
        raise CompanyKakaoGuidanceError(
            "ASSIGNMENT_CHANGED",
            "업체 배정 상태가 변경되었습니다. 검색 결과를 새로고침해 주세요.",
        )
    if not is_admin_user and not canonical_assignment_id:
        raise CompanyKakaoGuidanceError(
            "ASSIGNMENT_REQUIRED", "내 영업DB에 배정된 업체만 안내할 수 있습니다."
        )

    # 화면/검색 결과에 남아 있는 전화번호는 오래됐거나 조작될 수 있습니다.
    # 모든 전화번호 후보를 버리고 서버가 방금 검증한 번호 하나만 사용합니다.
    phone_keys = {
        "mobile_phone", "휴대전화", "휴대전화번호", "대표전화", "phone",
    }
    trusted_company = {
        key: value for key, value in dict(company or {}).items()
        if key not in phone_keys
    }
    trusted_company.update({
        "id": canonical["company_id"],
        "company_id": canonical["company_id"],
        "company_uid": requested_company_uid,
        "mobile_phone": canonical["mobile_phone"],
    })
    trusted_assignment = dict(assignment or {})
    if canonical_assignment_id:
        trusted_assignment["id"] = canonical_assignment_id
        trusted_assignment["assignment_id"] = canonical_assignment_id
    trusted_assignment["company_uid"] = requested_company_uid

    supplied_message_key = _text(idempotency_key)
    message_key = supplied_message_key or secrets.token_urlsafe(32)
    eligibility = evaluate_send_eligibility(
        trusted_company, current_user_id=current_user_id,
        assignment=trusted_assignment, message_type=selected,
        is_admin_user=is_admin_user, db=db, environ=environ,
    )
    duplicate_code = _text(eligibility.get("code")).upper()
    replay_candidate = bool(supplied_message_key) and duplicate_code in {
        "DUPLICATE_IN_PROGRESS",
        "DUPLICATE_WITHIN_7_DAYS",
    }
    if not eligibility["eligible"] and not replay_candidate:
        raise CompanyKakaoGuidanceError(eligibility["code"], eligibility["message"])
    readiness = guidance_environment_readiness(environ)
    if not readiness["ready"]:
        raise CompanyKakaoGuidanceError("GUIDANCE_NOT_READY", "안내 발송 설정이 완료되지 않았습니다.")
    phone = canonical["mobile_phone"]
    reserved = reserve_guidance_message(
        current_user_id=current_user_id, company_id=canonical["company_id"],
        company_uid=eligibility["company_uid"],
        assignment_id=canonical_assignment_id or None,
        contact_id=canonical["contact_id"],
        contact_updated_at=canonical["contact_updated_at"],
        recipient_phone_hash=_phone_hash(phone, environ), message_type=selected,
        idempotency_key=message_key, delivery_mode="mock" if readiness["mock_mode"] else "live",
        session_id=session_id, db=db,
    )
    if not bool(reserved.get("success", reserved.get("ok", False))):
        raise CompanyKakaoGuidanceError(_text(reserved.get("code") or "RESERVATION_FAILED"), _text(reserved.get("message") or "안내 발송을 예약하지 못했습니다."))
    message_id = _text(reserved.get("message_id") or reserved.get("id"))
    if not _UUID.fullmatch(message_id):
        raise CompanyKakaoGuidanceError("MALFORMED_RESPONSE", "안내 발송 예약을 확인하지 못했습니다.")

    if _text(reserved.get("code")).upper() == "IDEMPOTENT_REPLAY":
        replay_status = _text(reserved.get("status")).lower()
        if replay_status not in {
            "queued", "sending", "sent", "delivered", "failed", "blocked",
            "cancelled", "simulated",
        }:
            raise CompanyKakaoGuidanceError(
                "MALFORMED_RESPONSE", "기존 안내 발송 상태를 확인하지 못했습니다."
            )
        # The reserve RPC intentionally returns no invite identifier or URL.
        # Reusing the existing message state is safe; trying to recreate its
        # invite could cancel an otherwise healthy message/outbox on failure.
        return {
            "ok": True,
            "code": "IDEMPOTENT_REPLAY",
            "message": "이미 처리된 안내 발송 요청입니다.",
            "guidance_message_id": message_id,
            "invite_id": "",
            "status": replay_status,
            "external_send_enabled": readiness["external_send_ready"],
        }

    if invite_factory is None:
        from claim_remote_service import create_prospect_self_input_invite
        invite_factory = create_prospect_self_input_invite
    if outbox_repository_factory is None:
        from claim_remote_repository import ClaimRemoteRepository
        outbox_repository_factory = ClaimRemoteRepository
    try:
        invite = dict(invite_factory(
            owner_user_id=_text(current_user_id).lower(), requested_by=_text(requested_by)[:120] or _text(current_user_id).lower(),
            company_uid=eligibility["company_uid"], guidance_type=selected, guidance_message_id=message_id,
        ) or {})
        invite_id, invite_url = _text(invite.get("invite_id")), _text(invite.get("invite_url"))
        if not _UUID.fullmatch(invite_id) or not invite_url.startswith("https://"):
            raise CompanyKakaoGuidanceError("INVITE_CREATION_FAILED", "검토신청 링크를 만들지 못했습니다.")
        attach_secure_review_invite(current_user_id=current_user_id, guidance_message_id=message_id, invite_id=invite_id, db=db)
        if readiness["mock_mode"]:
            # 외부 전송/발송 대기열을 절대 만들지 않는 성공 시뮬레이션입니다.
            finalize_guidance_message(
                current_user_id=current_user_id, guidance_message_id=message_id,
                status="simulated", provider_message_id="", provider_group_id="", db=db,
            )
        else:
            template_code = f"GUIDANCE_{selected.upper()}"
            outbox_repository_factory(_text(current_user_id).lower()).enqueue_message(
                idempotency_key=f"guidance:{message_id}", event_type=template_code, template_code=template_code,
                secure_payload={
                    "to": phone,
                    "variables": {"#{검토신청링크}": invite_url},
                    "guidance_message_id": message_id,
                    "canonical_contact_id": canonical["contact_id"],
                },
                invite_id=invite_id,
                guidance_message_id=message_id,
                expires_at=_parse_time(invite.get("expires_at")) or datetime.now(timezone.utc) + timedelta(days=GUIDANCE_LINK_TTL_DAYS),
            )
    except Exception as exc:
        try:
            cancel_guidance_message(current_user_id=current_user_id, guidance_message_id=message_id, reason="invite_or_outbox_failed", db=db)
        except Exception:
            pass
        if isinstance(exc, CompanyKakaoGuidanceError):
            raise
        raise CompanyKakaoGuidanceError("GUIDANCE_QUEUE_FAILED", "안내 발송 대기열을 만들지 못했습니다. 외부 메시지는 발송되지 않았습니다.") from None
    return {
        "ok": True, "code": "SIMULATED" if readiness["mock_mode"] else "QUEUED",
        "message": "테스트 모드에서 발송을 모의 처리했습니다. 외부 메시지는 발송되지 않았습니다." if readiness["mock_mode"] else "카카오톡 안내 발송을 예약했습니다.",
        "guidance_message_id": message_id, "invite_id": invite_id,
        "status": "simulated" if readiness["mock_mode"] else "queued",
        "external_send_enabled": readiness["external_send_ready"],
    }


def notify_guidance_outbox_status(*, guidance_message_id: str, status: str, provider_message_id: str = "", error_code: str = "") -> dict[str, Any]:
    """워커 콜백. retry는 queued, cancelled는 cancelled로 안전하게 정규화합니다."""

    raw = _text(status).lower()
    mapped = {"retry": "queued", "cancelled": "cancelled"}.get(raw, raw)
    if mapped not in {
        "queued", "blocked", "sent", "delivered", "failed",
        "cancelled", "simulated",
    }:
        raise CompanyKakaoGuidanceError("INVALID_STATUS", "안내 발송 상태를 확인해 주세요.")
    return _first_row(_rpc(RPC_FINALIZE, {
        "p_current_user_id": "", "p_message_id": _text(guidance_message_id), "p_status": mapped,
        "p_provider_message_id": _text(provider_message_id)[:200], "p_provider_group_id": "",
        "p_failure_code": re.sub(r"[^A-Z0-9_]", "_", _text(error_code).upper())[:80], "p_failure_summary": "",
    }))


def cancel_guidance_for_invite(*, invite_id: str, owner_user_id: str, opt_out: bool = True, reason: str = "customer_opt_out") -> dict[str, Any]:
    return _first_row(_rpc(RPC_CANCEL_FOR_INVITE, {
        "p_owner_user_id": _text(owner_user_id).lower(), "p_invite_id": _text(invite_id),
        "p_opt_out": bool(opt_out), "p_reason": _text(reason)[:200],
    }))


__all__ = [
    "CompanyKakaoGuidanceError", "GUIDANCE_BUTTON_LABELS", "GUIDANCE_MESSAGE_LABELS",
    "GUIDANCE_MESSAGE_PREVIEWS", "GUIDANCE_MESSAGE_TYPES", "GUIDANCE_TEMPLATE_ENV_BY_TYPE",
    "admin_list_guidance_history", "attach_secure_review_invite", "cancel_guidance_for_invite",
    "cancel_guidance_message", "check_guidance_eligibility", "check_guidance_send_ready",
    "evaluate_guidance_eligibility",
    "evaluate_send_eligibility", "finalize_guidance_message", "get_guidance_admin_settings",
    "guidance_environment_readiness", "list_guidance_history", "notify_guidance_outbox_status",
    "request_guidance_send", "reserve_guidance_message", "resolve_canonical_guidance_mobile",
    "set_guidance_contact_control",
    "update_guidance_admin_settings",
]
