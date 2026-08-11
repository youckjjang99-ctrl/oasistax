from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from cloud_db import CloudDatabase


RPC_FEATURE_READY = "oasis_company_sales_assignment_feature_ready"
RPC_RELEASE_EXPIRED = "oasis_release_expired_company_assignments"
RPC_CLAIM_COMPANY = "oasis_claim_company_sales_assignment"
RPC_CLAIM_AND_SAVE_COMPANY = "oasis_claim_and_save_company_sales_assignment"
RPC_CLAIM_SAVE_PROMOTE_CONTACTS = (
    "oasis_claim_save_and_promote_prospect_contacts"
)
RPC_LIST_USER = "oasis_list_user_company_assignments"
RPC_GET_USER_DB_DASHBOARD = "oasis_get_user_db_dashboard"
RPC_LIST_USER_DB_ASSIGNMENTS = "oasis_list_user_db_assignments"
RPC_FILTER_BLOCKED = "oasis_filter_blocked_company_uids"
RPC_RESOLVE_CANDIDATE_UIDS = "oasis_resolve_candidate_company_uids"
RPC_RECORD_CONTACT = "oasis_record_company_sales_contact"
RPC_LIST_CONTACTS = "oasis_list_company_sales_contacts"
RPC_RELEASE_ASSIGNMENT = "oasis_release_company_sales_assignment"
RPC_LIST_ADMIN = "oasis_list_admin_company_assignments"
RPC_LIST_ADMIN_METRICS = "oasis_list_company_assignment_admin_metrics"
RPC_ADMIN_CHANGE_ASSIGNEE = "oasis_admin_change_company_assignee"
RPC_ADMIN_RELEASE = "oasis_admin_release_company_assignment"
RPC_ADMIN_REACTIVATE = "oasis_admin_reactivate_company_assignment"
RPC_ADMIN_PERMANENT_EXCLUDE = "oasis_admin_permanent_exclude_company"
RPC_ADMIN_REVIEW_RETURNED_BATCH = "oasis_admin_review_returned_companies_batch"
RPC_SAVE_USER_NOTE = "oasis_save_user_prospect_note"
RPC_RECORD_COMPANY_VIEWS = "oasis_record_company_views"
RPC_ADMIN_SET_USER_LIMIT = "oasis_admin_set_sales_user_limit"
RPC_GET_USER_LIMITS = "oasis_get_sales_user_limits"
RPC_LIST_ADMIN_AUDIT = "oasis_list_company_assignment_audit"
RPC_SUBMIT_MOBILE_DB_REQUEST = "oasis_submit_mobile_db_request"
RPC_LIST_USER_MOBILE_DB_REQUESTS = "oasis_list_user_mobile_db_requests"
RPC_LIST_ADMIN_MOBILE_DB_REQUESTS = "oasis_list_admin_mobile_db_requests"
RPC_ADMIN_UPDATE_MOBILE_DB_REQUEST = "oasis_admin_update_mobile_db_request"


_UID_PATTERN = re.compile(
    r"^(?:business:[0-9]{10}|corporate:[0-9]{13}|"
    r"nps:[A-Z0-9]+|fallback:[0-9a-f]{64}|source:[0-9a-f]{64})$"
)
_SAFE_CODE_PATTERN = re.compile(r"^[A-Z0-9_]{1,80}$")
_LEGAL_NAME_MARKERS = re.compile(
    r"(?:\(주\)|\(유\)|㈜|주식회사|유한회사|합자회사|합명회사)",
    re.IGNORECASE,
)

_BUSINESS_NO_KEYS = (
    "business_no",
    "business_number",
    "사업자등록번호",
)
_CORPORATE_NO_KEYS = (
    "corporate_registration_no",
    "corporate_no",
    "corporation_no",
    "법인등록번호",
)
_NPS_MANAGEMENT_NO_KEYS = (
    "nps_workplace_management_no",
    "nps_management_no",
    "workplace_management_no",
    "국민연금사업장관리번호",
    "사업장관리번호",
)
_COMPANY_NAME_KEYS = (
    "company_name",
    "business_name",
    "사업장명",
    "업체명",
    "상호명",
)
_ADDRESS_KEYS = (
    "address",
    "road_address",
    "lot_address",
    "주소",
    "도로명주소",
)
_PHONE_KEYS = (
    "mobile_phone",
    "landline_phone",
    "phone",
    "primary_phone",
    "휴대전화",
    "휴대전화번호",
    "일반전화",
    "전화번호",
    "대표전화",
)
_SOURCE_KEYS = ("source", "source_type", "원천", "조회기관")
_SOURCE_RECORD_KEYS = (
    "source_key",
    "source_record_key",
    "record_key",
    "원천키",
)

_STATUS_LABELS = {
    "unassigned": "미배정",
    "assigned": "배정됨",
    "pending_contact": "연락대기",
    "contacted": "연락완료",
    "consulting": "상담진행",
    "follow_up": "재연락예정",
    "rejected": "거절",
    "contracted": "계약완료",
    "long_hold": "장기보류",
    "unreachable": "연락불가",
    "wrong_number": "번호오류",
    "closed": "폐업",
    "permanently_excluded": "영구제외",
    "미배정": "미배정",
    "배정됨": "배정됨",
    "연락대기": "연락대기",
    "연락완료": "연락완료",
    "상담진행": "상담진행",
    "재연락예정": "재연락예정",
    "거절": "거절",
    "계약완료": "계약완료",
    "장기보류": "장기보류",
    "연락불가": "연락불가",
    "번호오류": "번호오류",
    "폐업": "폐업",
    "영구제외": "영구제외",
}

_CONTACT_RESULTS_REQUIRING_NEXT_DATE = {
    "재연락 요청",
    "follow_up_requested",
    "follow_up",
}

_FAILURE_MESSAGES = {
    "ASSIGNMENT_CONFLICT": (
        "다른 담당자가 먼저 배정받은 업체입니다. "
        "검색 결과를 새로고침합니다."
    ),
    "ALREADY_ASSIGNED": (
        "다른 담당자가 먼저 배정받은 업체입니다. "
        "검색 결과를 새로고침합니다."
    ),
    "MAX_UNCONTACTED_REACHED": "관리자가 설정한 미접촉 배정 한도에 도달했습니다.",
    "LIMIT_REACHED": "관리자가 설정한 미접촉 배정 한도에 도달했습니다.",
    "UNCONTACTED_LIMIT_REACHED": "관리자가 설정한 미접촉 배정 한도에 도달했습니다.",
    "TOTAL_DB_LIMIT_REACHED": "관리자가 설정한 전체 DB 보유 한도에 도달했습니다.",
    "LANDLINE_LIMIT_REACHED": "관리자가 설정한 일반전화 DB 한도에 도달했습니다.",
    "MOBILE_LIMIT_REACHED": "관리자가 설정한 핸드폰번호 DB 한도에 도달했습니다.",
    "RETURN_REASON_REQUIRED": "반납사유를 입력해 주세요.",
    "MIGRATION_CONFLICT": (
        "기존 저장자료의 담당자가 서로 달라 관리자의 담당자 지정이 "
        "필요한 업체입니다."
    ),
    "PERMISSION_DENIED": "이 작업을 수행할 권한이 없습니다.",
    "ADMIN_REQUIRED": "관리자 권한이 필요한 작업입니다.",
    "NOT_OWNER": "현재 담당자만 이 작업을 수행할 수 있습니다.",
    "NOT_FOUND": "업체 배정 정보를 찾을 수 없습니다.",
    "PERMANENTLY_EXCLUDED": "영구 제외된 업체입니다.",
    "NEXT_CONTACT_REQUIRED": "재연락 예정일을 입력해 주세요.",
    "INVALID_INPUT": "입력값을 확인해 주세요.",
    "FEATURE_NOT_READY": (
        "중복 연락 방지 기능이 아직 데이터베이스에 적용되지 "
        "않았습니다. 기존 중복 제외 방식을 사용해 주세요."
    ),
    "ASSIGNMENT_SERVICE_UNAVAILABLE": (
        "배정 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."
    ),
    "MALFORMED_RESPONSE": (
        "배정 처리 결과를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."
    ),
}

_ASSIGNMENT_FIELDS = {
    "id",
    "assignment_id",
    "company_id",
    "prospect_id",
    "company_uid",
    "status",
    "assigned_at",
    "assignment_expires_at",
    "first_contacted_at",
    "last_contacted_at",
    "next_contact_at",
    "contact_count",
    "released_at",
    "released_reason",
    "promoted_contact_count",
    "permanently_excluded",
    "created_at",
    "updated_at",
}
_USER_ASSIGNMENT_FIELDS = _ASSIGNMENT_FIELDS | {
    "company_name",
    "business_no",
    "corporate_registration_no",
    "nps_workplace_management_no",
    "address",
    "region",
    "industry_name",
    "own_memo",
    "source",
    "source_key",
    "employee_count",
    "new_employee_count",
    "lost_employee_count",
    "priority_score",
    "priority_reasons",
    "data_created_ym",
    "source_data",
    "total_count",
}
_USER_DB_DASHBOARD_FIELDS = {
    "total_db_count",
    "landline_db_count",
    "mobile_db_count",
    "new_db_count",
    "in_progress_db_count",
    "completed_db_count",
}
_USER_DB_FILTERS = {
    "all",
    "landline",
    "mobile",
    "new",
    "in_progress",
    "completed",
}
_ADMIN_ASSIGNMENT_FIELDS = _USER_ASSIGNMENT_FIELDS | {
    "assigned_user_id",
    "assigned_user_name",
    "first_viewer_user_id",
    "first_viewer_user_name",
    "first_assigned_user_id",
    "first_assigned_user_name",
    "first_contacted_user_id",
    "first_contacted_user_name",
    "first_viewed_by_user_id",
    "first_viewed_by_user_name",
    "first_assigned_by_user_id",
    "first_assigned_by_user_name",
    "first_contacted_by_user_id",
    "first_contacted_by_user_name",
    "duplicate_attempt_count",
    "current_uncontacted_count",
    "contacted_assignment_count",
    "long_unprocessed_count",
    "legacy_hold",
    "migration_conflict",
    "conflicting_user_ids",
    "conflict_details",
    "conflict_resolution_status",
    "effective_max_uncontacted",
    "assignee_uncontacted_count",
    "target_user_id",
    "max_uncontacted",
    "total_count",
}
_MOBILE_DB_REQUEST_FIELDS = {
    "request_id",
    "requested_user_id",
    "requested_user_name",
    "region",
    "district",
    "business_type",
    "minimum_employees",
    "maximum_employees",
    "requested_count",
    "allocated_count",
    "status",
    "decision_reason",
    "requested_at",
    "decided_at",
}


class CompanyIdentityError(ValueError):
    """Raised when a prospect does not contain a safe common identity."""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def _first_value(data: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        value = _text(data.get(key))
        if value:
            return value
    return ""


def _digits(value: Any) -> str:
    return re.sub(r"[^0-9]", "", _text(value))


def _normalize_company_name(value: Any) -> str:
    name = _LEGAL_NAME_MARKERS.sub("", _text(value).casefold())
    return re.sub(r"[^0-9a-z가-힣]", "", name)


def _normalize_address(value: Any) -> str:
    address = _text(value).casefold()
    return re.sub(r"[^0-9a-z가-힣]", "", address)


def _normalize_phone(value: Any) -> str:
    raw = re.split(
        r"(?:ext(?:ension)?\.?|내선)",
        _text(value),
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    digits = re.sub(r"[^0-9]", "", raw)
    if digits.startswith("0082"):
        digits = digits[4:]
        if not digits.startswith("0"):
            digits = f"0{digits}"
    elif digits.startswith("82") and len(digits) >= 10:
        digits = digits[2:]
        if not digits.startswith("0"):
            digits = f"0{digits}"
    return digits


def _normalize_management_no(value: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", _text(value).upper())


def _hash_uid(prefix: str, values: Sequence[str]) -> str:
    payload = "|".join(values).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()}"


def build_company_uid(
    company: Mapping[str, Any] | None = None,
    *,
    business_no: Any = "",
    corporate_registration_no: Any = "",
    nps_workplace_management_no: Any = "",
    company_name: Any = "",
    address: Any = "",
    phone: Any = "",
) -> str:
    """Build the deterministic company identity used by assignment RPCs.

    Strong public identifiers are preferred.  A place hash is generated only
    when normalized company name, address, and phone are all present.  If one
    of those three values is missing, a source-specific identity is used so
    that similarly named companies are never merged by name alone.
    """

    data = dict(company or {})

    business_digits = _digits(
        business_no or _first_value(data, _BUSINESS_NO_KEYS)
    )
    if len(business_digits) == 10:
        return f"business:{business_digits}"

    corporate_digits = _digits(
        corporate_registration_no
        or _first_value(data, _CORPORATE_NO_KEYS)
    )
    if len(corporate_digits) == 13:
        return f"corporate:{corporate_digits}"

    management_no = _normalize_management_no(
        nps_workplace_management_no
        or _first_value(data, _NPS_MANAGEMENT_NO_KEYS)
    )
    if management_no:
        return f"nps:{management_no}"

    # Once a row has a valid common identity, keep it stable even if public
    # contact enrichment later adds or changes a phone number.
    existing_uid = _text(data.get("company_uid"))
    if _UID_PATTERN.fullmatch(existing_uid):
        return existing_uid

    normalized_name = _normalize_company_name(
        company_name or _first_value(data, _COMPANY_NAME_KEYS)
    )
    normalized_address = _normalize_address(
        address or _first_value(data, _ADDRESS_KEYS)
    )
    normalized_phone = _normalize_phone(
        phone or _first_value(data, _PHONE_KEYS)
    )
    if normalized_name and normalized_address and normalized_phone:
        return _hash_uid(
            "fallback",
            (normalized_name, normalized_address, normalized_phone),
        )

    source = _text(_first_value(data, _SOURCE_KEYS)).casefold()
    source_key = _text(_first_value(data, _SOURCE_RECORD_KEYS))
    if source and source_key:
        return _hash_uid("source", (source, source_key))

    raise CompanyIdentityError(
        "업체 공통 식별키를 만들 수 없습니다. 사업자번호, 법인번호, "
        "국민연금 관리번호 또는 원천 식별정보를 확인해 주세요."
    )


def assignment_status_label(status: Any) -> str:
    value = _text(status)
    return _STATUS_LABELS.get(value, value or "미배정")


def _user_id(value: Any) -> str:
    user_id = _text(value).lower()
    if not user_id or len(user_id) > 200:
        raise ValueError("현재 로그인 사용자를 확인할 수 없습니다.")
    return user_id


def _company_uid(value: Any) -> str:
    uid = _text(value)
    if not _UID_PATTERN.fullmatch(uid):
        raise ValueError("올바르지 않은 업체 공통 식별키입니다.")
    return uid


def _bounded(value: Any, limit: int) -> str:
    return _text(value)[: max(0, int(limit))]


def _nullable_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _utc_iso(value: datetime | str | None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = _text(value)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("날짜와 시간 형식을 확인해 주세요.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_code(value: Any, default: str) -> str:
    code = _text(value).upper()
    return code if _SAFE_CODE_PATTERN.fullmatch(code) else default


def _safe_message(code: str, *, success_message: str, ok: bool) -> str:
    if ok:
        return success_message
    return _FAILURE_MESSAGES.get(
        code,
        "요청을 처리하지 못했습니다. 상태를 확인한 후 다시 시도해 주세요.",
    )


def _safe_rpc_failure(exc: Exception) -> dict[str, Any]:
    raw = str(exc or "").upper()
    if (
        "PGRST202" in raw
        or "COULD NOT FIND THE FUNCTION" in raw
        or "SCHEMA CACHE" in raw
    ):
        code = "FEATURE_NOT_READY"
    elif "42501" in raw or "PERMISSION DENIED" in raw:
        code = "PERMISSION_DENIED"
    else:
        code = "ASSIGNMENT_SERVICE_UNAVAILABLE"
    return {
        "ok": False,
        "code": code,
        "message": _FAILURE_MESSAGES[code],
        "assignment": {},
        "warning": _FAILURE_MESSAGES[code],
        "fallback_required": code == "FEATURE_NOT_READY",
    }


def _rpc(
    function_name: str,
    parameters: dict[str, Any],
    *,
    db: CloudDatabase | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    try:
        return (db or CloudDatabase()).rpc(function_name, parameters), None
    except Exception as exc:  # CloudDatabase deliberately normalizes transport errors.
        return None, _safe_rpc_failure(exc)


def _first_row(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, list) and raw and isinstance(raw[0], dict):
        return dict(raw[0])
    return None


def _rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [dict(row) for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        for key in ("rows", "items", "assignments", "results"):
            nested = raw.get(key)
            if isinstance(nested, list):
                return [dict(row) for row in nested if isinstance(row, dict)]
        return [dict(raw)]
    return []


def _mutation_result(
    raw: Any,
    *,
    success_message: str,
    admin: bool = False,
) -> dict[str, Any]:
    row = _first_row(raw)
    if row is None:
        code = "MALFORMED_RESPONSE"
        return {
            "ok": False,
            "code": code,
            "message": _FAILURE_MESSAGES[code],
            "assignment": {},
            "warning": _FAILURE_MESSAGES[code],
            "fallback_required": False,
        }

    success_value = row.get("success", row.get("ok"))
    ok = success_value is True or _text(success_value).lower() in {
        "true",
        "t",
        "1",
    }
    code = _safe_code(row.get("code"), "OK" if ok else "REQUEST_FAILED")
    allowed = _ADMIN_ASSIGNMENT_FIELDS if admin else _ASSIGNMENT_FIELDS
    source_assignment = (
        row.get("assignment")
        if isinstance(row.get("assignment"), dict)
        else row
    )
    assignment = {
        key: value
        for key, value in source_assignment.items()
        if key in allowed
    }
    return {
        "ok": ok,
        "code": code,
        "message": _safe_message(
            code,
            success_message=success_message,
            ok=ok,
        ),
        "assignment": assignment,
        "warning": "" if ok else _safe_message(
            code,
            success_message=success_message,
            ok=False,
        ),
        "fallback_required": False,
    }


def assignment_feature_ready(
    *,
    db: CloudDatabase | None = None,
) -> tuple[bool, str]:
    raw, error = _rpc(RPC_FEATURE_READY, {}, db=db)
    if error:
        return False, str(error["message"])
    if isinstance(raw, bool):
        return raw, "" if raw else _FAILURE_MESSAGES["FEATURE_NOT_READY"]
    row = _first_row(raw)
    ready = bool(row and row.get("ready") is True)
    return ready, "" if ready else _FAILURE_MESSAGES["FEATURE_NOT_READY"]


def release_expired_assignments(
    current_user_id: str,
    *,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    raw, error = _rpc(
        RPC_RELEASE_EXPIRED,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_session_id": _bounded(session_id, 200),
        },
        db=db,
    )
    if error:
        return {**error, "released_count": 0}
    try:
        released_count = int(raw or 0)
    except (TypeError, ValueError):
        row = _first_row(raw)
        try:
            released_count = int((row or {}).get("released_count", -1))
        except (TypeError, ValueError):
            released_count = -1
    if released_count < 0:
        failure = _safe_rpc_failure(RuntimeError("malformed response"))
        failure["code"] = "MALFORMED_RESPONSE"
        failure["message"] = _FAILURE_MESSAGES["MALFORMED_RESPONSE"]
        failure["warning"] = failure["message"]
        return {**failure, "released_count": 0}
    return {
        "ok": True,
        "code": "OK",
        "message": "만료된 임시 배정을 정리했습니다.",
        "assignment": {},
        "released_count": released_count,
        "warning": "",
        "fallback_required": False,
    }


def record_company_views(
    current_user_id: str,
    companies: Iterable[Mapping[str, Any]],
    *,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """Record per-company views without assigning or changing sales state."""

    payload: list[dict[str, str]] = []
    invalid_count = 0
    for company in companies:
        try:
            uid = build_company_uid(company)
        except CompanyIdentityError:
            invalid_count += 1
            continue
        payload.append(
            {
                "company_id": _nullable_text(
                    company.get("id") or company.get("company_id") or ""
                ),
                "company_uid": uid,
            }
        )
    payload = list(
        {
            row["company_uid"]: row
            for row in payload
        }.values()
    )
    if not payload:
        return {
            "ok": invalid_count == 0,
            "code": "OK" if invalid_count == 0 else "INVALID_INPUT",
            "message": (
                "기록할 업체 조회가 없습니다."
                if invalid_count == 0
                else "업체 식별정보가 부족하여 조회이력을 기록하지 못했습니다."
            ),
            "assignment": {},
            "recorded_count": 0,
            "invalid_count": invalid_count,
            "warning": "" if invalid_count == 0 else _FAILURE_MESSAGES["INVALID_INPUT"],
            "fallback_required": False,
        }
    raw, error = _rpc(
        RPC_RECORD_COMPANY_VIEWS,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_companies": payload,
            "p_session_id": _bounded(session_id, 200),
        },
        db=db,
    )
    if error:
        return {**error, "recorded_count": 0, "invalid_count": invalid_count}
    if isinstance(raw, (int, float, str)):
        try:
            recorded_count = max(0, int(raw))
        except (TypeError, ValueError):
            recorded_count = -1
    else:
        row = _first_row(raw)
        try:
            recorded_count = int((row or {}).get("recorded_count", -1))
        except (TypeError, ValueError):
            recorded_count = -1
    if recorded_count < 0:
        return {
            "ok": False,
            "code": "MALFORMED_RESPONSE",
            "message": _FAILURE_MESSAGES["MALFORMED_RESPONSE"],
            "assignment": {},
            "recorded_count": 0,
            "invalid_count": invalid_count,
            "warning": _FAILURE_MESSAGES["MALFORMED_RESPONSE"],
            "fallback_required": False,
        }
    return {
        "ok": True,
        "code": "OK",
        "message": "업체 조회이력을 기록했습니다.",
        "assignment": {},
        "recorded_count": recorded_count,
        "invalid_count": invalid_count,
        "warning": (
            "일부 업체는 식별정보가 부족하여 조회이력에서 제외했습니다."
            if invalid_count
            else ""
        ),
        "fallback_required": False,
    }


def claim_company(
    current_user_id: str,
    company_id: Any,
    company_uid: Any,
    *,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    raw, error = _rpc(
        RPC_CLAIM_COMPANY,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_company_id": _nullable_text(company_id),
            "p_company_uid": _company_uid(company_uid),
            "p_session_id": _bounded(session_id, 200),
        },
        db=db,
    )
    if error:
        return error
    result = _mutation_result(raw, success_message="내 영업DB에 배정했습니다.")
    assignment = result.get("assignment") or {}
    if assignment.get("prospect_id") and not assignment.get("company_id"):
        assignment["company_id"] = assignment["prospect_id"]
    return result


def claim_and_save_company(
    current_user_id: str,
    company_uid: Any,
    company_payload: Mapping[str, Any],
    *,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """Atomically claim and mirror one public company snapshot."""
    source_payload = dict(company_payload or {})
    contact_candidates = source_payload.pop("contact_candidates", [])
    if not isinstance(contact_candidates, list):
        contact_candidates = []
    allowed_payload_fields = {
        "source",
        "source_key",
        "business_no",
        "company_name",
        "address",
        "region",
        "industry_code",
        "industry_name",
        "employee_count",
        "new_employee_count",
        "lost_employee_count",
        "monthly_notice_amount",
        "data_created_ym",
        "priority_score",
        "priority_reasons",
        "status",
        "source_data",
        "collected_at",
        "updated_at",
        "company_uid",
        "corporate_registration_no",
        "nps_workplace_management_no",
    }
    payload = {
        key: value
        for key, value in source_payload.items()
        if key in allowed_payload_fields
    }
    payload["company_uid"] = _company_uid(company_uid)
    rpc_name = RPC_CLAIM_AND_SAVE_COMPANY
    parameters = {
        "p_current_user_id": _user_id(current_user_id),
        "p_company_uid": payload["company_uid"],
        "p_company_payload": payload,
        "p_session_id": _bounded(session_id, 200) or None,
    }
    if contact_candidates:
        rpc_name = RPC_CLAIM_SAVE_PROMOTE_CONTACTS
        parameters["p_contact_candidates"] = contact_candidates[:8]
    raw, error = _rpc(
        rpc_name,
        parameters,
        db=db,
    )
    if error:
        return error
    result = _mutation_result(raw, success_message="내 영업DB에 배정했습니다.")
    assignment = result.get("assignment") or {}
    if assignment.get("prospect_id") and not assignment.get("company_id"):
        assignment["company_id"] = assignment["prospect_id"]
    return result


def claim_and_save_companies(
    current_user_id: str,
    company_payloads: Iterable[Mapping[str, Any]],
    *,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    database = db or CloudDatabase()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for company_payload in company_payloads:
        payload = dict(company_payload)
        try:
            uid = build_company_uid(payload)
        except CompanyIdentityError as exc:
            results.append(
                {
                    "ok": False,
                    "code": "INVALID_INPUT",
                    "message": str(exc),
                    "assignment": {},
                }
            )
            continue
        if uid in seen:
            continue
        seen.add(uid)
        results.append(
            claim_and_save_company(
                current_user_id,
                uid,
                payload,
                session_id=session_id,
                db=database,
            )
        )
    successful = [result for result in results if result.get("ok")]
    assignments = [
        result["assignment"]
        for result in successful
        if result.get("assignment")
    ]
    all_ok = bool(results) and len(successful) == len(results)
    return {
        "ok": all_ok,
        "code": "OK" if all_ok else "PARTIAL_SUCCESS",
        "message": (
            f"{len(successful)}개 업체를 내 영업DB에 배정했습니다."
            if results
            else "선택된 업체가 없습니다."
        ),
        "assignment": assignments[0] if len(assignments) == 1 else {},
        "assignments": assignments,
        "results": results,
        "success_count": len(successful),
        "failure_count": len(results) - len(successful),
    }


def claim_companies(
    current_user_id: str,
    companies: Iterable[Mapping[str, Any]],
    *,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    database = db or CloudDatabase()
    results: list[dict[str, Any]] = []
    for company in companies:
        try:
            uid = build_company_uid(company)
        except CompanyIdentityError as exc:
            results.append(
                {
                    "ok": False,
                    "code": "INVALID_INPUT",
                    "message": str(exc),
                    "assignment": {},
                }
            )
            continue
        results.append(
            claim_company(
                current_user_id,
                company.get("id") or company.get("company_id") or "",
                uid,
                session_id=session_id,
                db=database,
            )
        )

    successful = [result for result in results if result.get("ok")]
    assignments = [
        result["assignment"]
        for result in successful
        if result.get("assignment")
    ]
    all_ok = len(successful) == len(results)
    return {
        "ok": all_ok,
        "code": "OK" if all_ok else "PARTIAL_SUCCESS",
        "message": (
            f"{len(successful)}개 업체를 내 영업DB에 배정했습니다."
            if results
            else "선택된 업체가 없습니다."
        ),
        "assignment": assignments[0] if len(assignments) == 1 else {},
        "assignments": assignments,
        "results": results,
        "success_count": len(successful),
        "failure_count": len(results) - len(successful),
    }


def list_user_assignments(
    current_user_id: str,
    *,
    statuses: Sequence[str] | None = None,
    limit: int = 1000,
    offset: int = 0,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    raw, error = _rpc(
        RPC_LIST_USER,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_statuses": [_text(status) for status in (statuses or []) if _text(status)],
            "p_limit": max(1, min(int(limit), 1000)),
            "p_offset": max(0, int(offset)),
        },
        db=db,
    )
    if error:
        return {**error, "assignments": []}
    assignments = []
    for row in _rows(raw):
        assignment = {
            key: value
            for key, value in row.items()
            if key in _USER_ASSIGNMENT_FIELDS
        }
        assignment["memo"] = _text(assignment.get("own_memo"))
        assignments.append(assignment)
    return {
        "ok": True,
        "code": "OK",
        "message": "내 영업DB를 불러왔습니다.",
        "assignment": {},
        "assignments": assignments,
        "warning": "",
        "fallback_required": False,
    }


def get_user_db_dashboard(
    current_user_id: str,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """Return count-only metrics for one user's active assignments."""

    raw, error = _rpc(
        RPC_GET_USER_DB_DASHBOARD,
        {"p_current_user_id": _user_id(current_user_id)},
        db=db,
    )
    if error:
        return {**error, "metrics": {}}
    row = _first_row(raw)
    if row is None:
        return {
            "ok": False,
            "code": "MALFORMED_RESPONSE",
            "message": _FAILURE_MESSAGES["MALFORMED_RESPONSE"],
            "assignment": {},
            "metrics": {},
            "warning": _FAILURE_MESSAGES["MALFORMED_RESPONSE"],
            "fallback_required": False,
        }
    metrics: dict[str, int] = {}
    for field in _USER_DB_DASHBOARD_FIELDS:
        try:
            metrics[field] = max(0, int(row.get(field) or 0))
        except (TypeError, ValueError):
            metrics[field] = 0
    return {
        "ok": True,
        "code": "OK",
        "message": "내 DB 현황을 불러왔습니다.",
        "assignment": {},
        "metrics": metrics,
        "warning": "",
        "fallback_required": False,
    }


def list_user_db_assignments(
    current_user_id: str,
    *,
    dashboard_filter: str = "all",
    limit: int = 100,
    offset: int = 0,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """Return one server-filtered page of the user's active assignments."""

    safe_filter = _text(dashboard_filter).lower() or "all"
    if safe_filter not in _USER_DB_FILTERS:
        safe_filter = "all"
    raw, error = _rpc(
        RPC_LIST_USER_DB_ASSIGNMENTS,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_filter": safe_filter,
            "p_limit": max(1, min(int(limit), 1000)),
            "p_offset": max(0, int(offset)),
        },
        db=db,
    )
    if error:
        return {**error, "assignments": [], "total_count": 0}
    assignments = []
    for row in _rows(raw):
        assignment = {
            key: value
            for key, value in row.items()
            if key in _USER_ASSIGNMENT_FIELDS
        }
        assignment["memo"] = _text(assignment.get("own_memo"))
        assignments.append(assignment)
    total_count = max(
        (int(row.get("total_count") or 0) for row in assignments),
        default=0,
    )
    return {
        "ok": True,
        "code": "OK",
        "message": "내 DB 목록을 불러왔습니다.",
        "assignment": {},
        "assignments": assignments,
        "total_count": total_count,
        "filter": safe_filter,
        "warning": "",
        "fallback_required": False,
    }


def list_blocked_company_uids(
    current_user_id: str,
    company_uids: Iterable[str],
    *,
    include_own: bool = True,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    unique_uids = list(
        dict.fromkeys(_company_uid(value) for value in company_uids if _text(value))
    )
    if not unique_uids:
        return {
            "ok": True,
            "code": "OK",
            "message": "확인할 업체가 없습니다.",
            "assignment": {},
            "company_uids": [],
            "blocked_company_uids": [],
            "own_company_uids": [],
            "relations": {},
            "warning": "",
            "fallback_required": False,
        }
    raw, error = _rpc(
        RPC_FILTER_BLOCKED,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_company_uids": unique_uids,
            "p_include_own": bool(include_own),
        },
        db=db,
    )
    if error:
        return {
            **error,
            "company_uids": unique_uids,
            "blocked_company_uids": [],
            "own_company_uids": [],
            "relations": {},
        }
    relations: dict[str, str] = {}
    for row in _rows(raw):
        uid = _text(row.get("company_uid"))
        relation = _text(row.get("relation")).lower()
        if uid in unique_uids and relation in {"available", "own", "blocked"}:
            relations[uid] = relation
    blocked = [uid for uid in unique_uids if relations.get(uid) == "blocked"]
    own = [uid for uid in unique_uids if relations.get(uid) == "own"]
    return {
        "ok": True,
        "code": "OK",
        "message": "업체 배정 가능 여부를 확인했습니다.",
        "assignment": {},
        "company_uids": unique_uids,
        "blocked_company_uids": blocked,
        "own_company_uids": own,
        "relations": relations,
        "warning": "",
        "fallback_required": False,
    }


def resolve_candidate_company_uids(
    current_user_id: str,
    items: Sequence[Mapping[str, Any]],
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """Resolve source-backed search rows to the UID already stored in CRM.

    Public phone enrichment can change a locally calculated fallback UID.  The
    database remains the authority for rows that already have a source/source
    key, so the search exclusion check and the atomic save RPC always address
    the same company.
    """

    prepared = [dict(item) for item in items]
    if not prepared:
        return {
            "ok": True,
            "code": "OK",
            "items": [],
            "conflict_items": [],
            "warning": "",
            "fallback_required": False,
        }

    candidates: list[dict[str, Any]] = []
    for row in prepared[:1000]:
        candidates.append(
            {
                "company_uid": _company_uid(row.get("company_uid")),
                "source": _first_value(row, _SOURCE_KEYS),
                "source_key": _first_value(row, _SOURCE_RECORD_KEYS),
                "business_no": _first_value(row, _BUSINESS_NO_KEYS),
                "corporate_registration_no": _first_value(
                    row, _CORPORATE_NO_KEYS
                ),
                "nps_workplace_management_no": _first_value(
                    row, _NPS_MANAGEMENT_NO_KEYS
                ),
            }
        )

    raw, error = _rpc(
        RPC_RESOLVE_CANDIDATE_UIDS,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_candidates": candidates,
        },
        db=db,
    )
    if error:
        return {
            **error,
            "items": prepared,
            "conflict_items": [],
        }

    resolved_rows = _rows(raw)
    if len(resolved_rows) != len(candidates):
        message = (
            "업체 공통 식별키 확인 결과가 완전하지 않아 안전하게 검색을 중단했습니다. "
            "잠시 후 다시 시도해 주세요."
        )
        return {
            "ok": False,
            "code": "MALFORMED_RESPONSE",
            "message": message,
            "warning": message,
            "items": prepared,
            "conflict_items": [],
            "fallback_required": True,
        }

    conflicts: list[dict[str, Any]] = []
    seen_indexes: set[int] = set()
    conflict_codes = {
        "strong_identifier_conflict",
        "source_strong_identifier_conflict",
        "unresolved",
    }
    for result in resolved_rows:
        try:
            index = int(result.get("candidate_index"))
        except (TypeError, ValueError):
            index = -1
        if index < 0 or index >= len(candidates) or index in seen_indexes:
            continue
        seen_indexes.add(index)
        code = _text(result.get("resolution_code")).lower()
        prepared[index]["assignment_resolution_code"] = code
        if code in conflict_codes:
            prepared[index]["assignment_relation"] = "unresolved"
            conflicts.append(prepared[index])
            continue
        try:
            prepared[index]["company_uid"] = _company_uid(
                result.get("canonical_company_uid")
            )
        except ValueError:
            prepared[index]["assignment_relation"] = "unresolved"
            conflicts.append(prepared[index])

    if len(seen_indexes) != len(candidates):
        message = (
            "일부 업체의 공통 식별키를 확인하지 못해 안전하게 검색을 중단했습니다. "
            "잠시 후 다시 시도해 주세요."
        )
        return {
            "ok": False,
            "code": "MALFORMED_RESPONSE",
            "message": message,
            "warning": message,
            "items": prepared,
            "conflict_items": conflicts,
            "fallback_required": True,
        }

    conflict_ids = {id(row) for row in conflicts}
    return {
        "ok": True,
        "code": "OK",
        "items": [row for row in prepared if id(row) not in conflict_ids],
        "conflict_items": conflicts,
        "warning": "",
        "fallback_required": False,
    }


def filter_company_availability(
    items: Iterable[Mapping[str, Any]],
    current_user_id: str,
    is_admin_user: bool = False,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    prepared: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        try:
            row["company_uid"] = build_company_uid(row)
        except CompanyIdentityError:
            row["assignment_relation"] = "unresolved"
            unresolved.append(row)
            continue
        prepared.append(row)

    # Rows backed by a stable source identity are resolved against the CRM
    # before availability filtering.  This keeps a phone update performed by
    # the nightly Kakao/Naver collector from changing the identity seen by the
    # sales assignment layer.
    if any(
        _first_value(row, _SOURCE_KEYS)
        and _first_value(row, _SOURCE_RECORD_KEYS)
        for row in prepared
    ):
        identity = resolve_candidate_company_uids(
            current_user_id,
            prepared,
            db=db,
        )
        if not identity.get("ok"):
            return {
                "items": [*prepared, *unresolved],
                "blocked_items": [],
                "own_items": [],
                "excluded_count": 0,
                "own_count": 0,
                "warning": identity.get("message", ""),
                "ready": False,
                "fallback_required": True,
            }
        prepared = list(identity.get("items") or [])
        unresolved.extend(identity.get("conflict_items") or [])

    availability = list_blocked_company_uids(
        current_user_id,
        [row["company_uid"] for row in prepared],
        include_own=True,
        db=db,
    )
    if not availability.get("ok"):
        return {
            "items": [*prepared, *unresolved],
            "blocked_items": [],
            "own_items": [],
            "excluded_count": 0,
            "own_count": 0,
            "warning": availability.get("message", ""),
            "ready": False,
            "fallback_required": True,
        }

    relations = availability.get("relations", {})
    visible: list[dict[str, Any]] = []
    blocked_items: list[dict[str, Any]] = []
    own_items: list[dict[str, Any]] = []
    for row in prepared:
        relation = relations.get(row["company_uid"], "available")
        row["assignment_relation"] = relation
        if relation == "blocked":
            blocked_items.append(row)
        elif relation == "own":
            own_items.append(row)
        else:
            visible.append(row)
    # A row without a deterministic common identity cannot be safely assigned
    # or excluded for other users.  Keep it visible only to administrators;
    # normal sales search fails closed instead of offering a button that can
    # never create a safe company-wide assignment.
    if is_admin_user:
        visible.extend(unresolved)

    warning = ""
    if unresolved:
        warning = (
            "일부 업체의 공통 식별정보가 부족하여 일반 영업사원의 "
            "신규 저장 대상에서 제외했습니다."
        )
    return {
        "items": visible,
        "blocked_items": blocked_items,
        "own_items": own_items,
        "excluded_count": len(blocked_items) + (
            0 if is_admin_user else len(unresolved)
        ),
        "own_count": len(own_items),
        "warning": warning,
        "ready": True,
        "fallback_required": bool(unresolved),
    }


def record_contact(
    current_user_id: str,
    company_id: Any,
    company_uid: Any,
    contact_method: Any,
    contact_result: Any,
    *,
    notes: Any = "",
    next_contact_at: datetime | str | None = None,
    contacted_at: datetime | str | None = None,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    result_value = _text(contact_result)
    if not _text(contact_method) or not result_value:
        return {
            "ok": False,
            "code": "INVALID_INPUT",
            "message": _FAILURE_MESSAGES["INVALID_INPUT"],
            "assignment": {},
        }
    if result_value in _CONTACT_RESULTS_REQUIRING_NEXT_DATE and not next_contact_at:
        return {
            "ok": False,
            "code": "NEXT_CONTACT_REQUIRED",
            "message": _FAILURE_MESSAGES["NEXT_CONTACT_REQUIRED"],
            "assignment": {},
        }
    raw, error = _rpc(
        RPC_RECORD_CONTACT,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_company_id": _nullable_text(company_id),
            "p_company_uid": _company_uid(company_uid),
            "p_contact_method": _text(contact_method),
            "p_contact_result": result_value,
            "p_notes": _bounded(notes, 10_000),
            "p_next_contact_at": _utc_iso(next_contact_at),
            "p_contacted_at": _utc_iso(contacted_at) or _utc_iso(
                datetime.now(timezone.utc)
            ),
            "p_session_id": _bounded(session_id, 200),
        },
        db=db,
    )
    if error:
        return error
    return _mutation_result(raw, success_message="연락결과를 저장했습니다.")


def list_company_contacts(
    current_user_id: str,
    company_uid: Any = "",
    *,
    limit: int = 200,
    offset: int = 0,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    uid = _text(company_uid)
    if uid:
        uid = _company_uid(uid)
    raw, error = _rpc(
        RPC_LIST_CONTACTS,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_company_uid": uid,
            "p_limit": max(1, min(int(limit), 1000)),
            "p_offset": max(0, int(offset)),
        },
        db=db,
    )
    if error:
        return {**error, "contacts": []}
    allowed = {
        "id",
        "company_id",
        "company_uid",
        "contact_method",
        "contact_result",
        "notes",
        "contacted_at",
        "next_contact_at",
        "assigned_user_id",
        "created_by_user_id",
        "created_at",
    }
    contacts = [
        {key: value for key, value in row.items() if key in allowed}
        for row in _rows(raw)
    ]
    return {
        "ok": True,
        "code": "OK",
        "message": "연락이력을 불러왔습니다.",
        "assignment": {},
        "contacts": contacts,
        "warning": "",
        "fallback_required": False,
    }


def release_assignment(
    current_user_id: str,
    company_id: Any,
    company_uid: Any,
    *,
    reason: Any,
    return_reason: Any = "",
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    raw, error = _rpc(
        RPC_RELEASE_ASSIGNMENT,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_company_uid": _company_uid(company_uid),
            "p_reason": _bounded(reason, 500),
            "p_session_id": _bounded(session_id, 200),
            "p_return_reason": _bounded(return_reason, 500),
        },
        db=db,
    )
    if error:
        return error
    return _mutation_result(raw, success_message="업체 배정을 해제했습니다.")


def save_user_note(
    current_user_id: str,
    company_uid: Any,
    memo: Any,
    company_id: Any = "",
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    raw, error = _rpc(
        RPC_SAVE_USER_NOTE,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_company_uid": _company_uid(company_uid),
            "p_company_id": _nullable_text(company_id),
            "p_memo": _bounded(memo, 20_000),
        },
        db=db,
    )
    if error:
        return error
    if isinstance(raw, bool):
        return {
            "ok": raw,
            "code": "UPDATED" if raw else "REQUEST_FAILED",
            "message": (
                "메모를 저장했습니다."
                if raw
                else "메모를 저장하지 못했습니다."
            ),
            "assignment": {
                "company_id": _nullable_text(company_id),
                "company_uid": _company_uid(company_uid),
            },
            "warning": "" if raw else "메모를 저장하지 못했습니다.",
            "fallback_required": False,
        }
    return _mutation_result(raw, success_message="메모를 저장했습니다.")


def list_admin_assignments(
    current_user_id: str,
    *,
    statuses: Sequence[str] | None = None,
    assigned_user_id: str = "",
    limit: int = 1000,
    offset: int = 0,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    raw, error = _rpc(
        RPC_LIST_ADMIN,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_statuses": [_text(status) for status in (statuses or []) if _text(status)],
            "p_assigned_user_id": _text(assigned_user_id).lower(),
            "p_limit": max(1, min(int(limit), 1000)),
            "p_offset": max(0, int(offset)),
        },
        db=db,
    )
    if error:
        return {**error, "assignments": []}
    assignments: list[dict[str, Any]] = []
    for raw_row in _rows(raw):
        assignment = {
            key: value
            for key, value in raw_row.items()
            if key in _ADMIN_ASSIGNMENT_FIELDS
        }
        # Keep the application compatible with the descriptive PostgreSQL
        # column names while exposing one stable UI contract.
        assignment["first_viewer_user_id"] = (
            assignment.get("first_viewer_user_id")
            or assignment.get("first_viewed_by_user_id")
        )
        assignment["first_viewer_user_name"] = (
            assignment.get("first_viewer_user_name")
            or assignment.get("first_viewed_by_user_name")
        )
        assignment["first_assigned_user_id"] = (
            assignment.get("first_assigned_user_id")
            or assignment.get("first_assigned_by_user_id")
        )
        assignment["first_assigned_user_name"] = (
            assignment.get("first_assigned_user_name")
            or assignment.get("first_assigned_by_user_name")
        )
        assignment["first_contacted_user_id"] = (
            assignment.get("first_contacted_user_id")
            or assignment.get("first_contacted_by_user_id")
        )
        assignment["first_contacted_user_name"] = (
            assignment.get("first_contacted_user_name")
            or assignment.get("first_contacted_by_user_name")
        )
        assignments.append(assignment)
    total_count = max(
        (int(row.get("total_count") or 0) for row in assignments),
        default=0,
    )
    return {
        "ok": True,
        "code": "OK",
        "message": "전체 업체 배정현황을 불러왔습니다.",
        "assignment": {},
        "assignments": assignments,
        "total_count": total_count,
        "warning": "",
        "fallback_required": False,
    }


def list_admin_assignment_metrics(
    current_user_id: str,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """Return full-dataset metrics without loading every assignment row."""
    raw, error = _rpc(
        RPC_LIST_ADMIN_METRICS,
        {"p_current_user_id": _user_id(current_user_id)},
        db=db,
    )
    if error:
        return {**error, "metrics": []}
    allowed = {
        "user_id",
        "user_name",
        "assigned_user_id",
        "assigned_user_name",
        "uncontacted_assignment_count",
        "contacted_assignment_count",
        "long_unprocessed_assignment_count",
        "total_assigned_count",
        "duplicate_assignment_attempt_count",
        "global_assignment_count",
        "global_duplicate_assignment_attempt_count",
        "global_migration_conflict_count",
        "uncontacted_count",
        "contacted_count",
        "long_unprocessed_count",
        "duplicate_attempt_count",
        "total_assignment_count",
    }
    metrics = []
    for raw_row in _rows(raw):
        metric = {key: value for key, value in raw_row.items() if key in allowed}
        metric["assigned_user_id"] = (
            metric.get("assigned_user_id") or metric.get("user_id")
        )
        metric["assigned_user_name"] = (
            metric.get("assigned_user_name") or metric.get("user_name")
        )
        metric["uncontacted_count"] = (
            metric.get("uncontacted_count")
            if metric.get("uncontacted_count") is not None
            else metric.get("uncontacted_assignment_count")
        )
        metric["contacted_count"] = (
            metric.get("contacted_count")
            if metric.get("contacted_count") is not None
            else metric.get("contacted_assignment_count")
        )
        metric["long_unprocessed_count"] = (
            metric.get("long_unprocessed_count")
            if metric.get("long_unprocessed_count") is not None
            else metric.get("long_unprocessed_assignment_count")
        )
        metric["duplicate_attempt_count"] = (
            metric.get("duplicate_attempt_count")
            if metric.get("duplicate_attempt_count") is not None
            else metric.get("duplicate_assignment_attempt_count")
        )
        metric["total_assignment_count"] = (
            metric.get("total_assignment_count")
            if metric.get("total_assignment_count") is not None
            else metric.get("global_assignment_count")
        )
        metrics.append(metric)
    return {
        "ok": True,
        "code": "OK",
        "message": "전사 영업배정 통계를 불러왔습니다.",
        "assignment": {},
        "metrics": metrics,
        "warning": "",
        "fallback_required": False,
    }


def list_admin_assignment_audit(
    current_user_id: str,
    company_uid: Any = "",
    *,
    limit: int = 500,
    offset: int = 0,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    uid = _text(company_uid)
    if uid:
        uid = _company_uid(uid)
    raw, error = _rpc(
        RPC_LIST_ADMIN_AUDIT,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_company_uid": uid,
            "p_limit": max(1, min(int(limit), 1000)),
            "p_offset": max(0, int(offset)),
        },
        db=db,
    )
    if error:
        return {**error, "audit": []}
    allowed = {
        "id",
        "user_id",
        "user_name",
        "company_id",
        "company_uid",
        "action",
        "previous_value",
        "new_value",
        "session_identifier",
        "session_fingerprint",
        "ip_address",
        "created_at",
    }
    audit = [
        {key: value for key, value in row.items() if key in allowed}
        for row in _rows(raw)
    ]
    return {
        "ok": True,
        "code": "OK",
        "message": "배정 감사로그를 불러왔습니다.",
        "assignment": {},
        "audit": audit,
        "warning": "",
        "fallback_required": False,
    }


def _admin_mutation(
    rpc_name: str,
    current_user_id: str,
    company_id: Any,
    company_uid: Any,
    *,
    reason: Any,
    session_id: str,
    success_message: str,
    extra: Mapping[str, Any] | None = None,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    parameters = {
        "p_current_user_id": _user_id(current_user_id),
        "p_company_uid": _company_uid(company_uid),
        "p_reason": _bounded(reason, 500),
        "p_session_id": _bounded(session_id, 200),
    }
    parameters.update(dict(extra or {}))
    raw, error = _rpc(rpc_name, parameters, db=db)
    if error:
        return error
    return _mutation_result(raw, success_message=success_message, admin=True)


def admin_change_assignee(
    current_user_id: str,
    company_id: Any,
    company_uid: Any,
    new_assigned_user_id: Any,
    *,
    reason: Any,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    return _admin_mutation(
        RPC_ADMIN_CHANGE_ASSIGNEE,
        current_user_id,
        company_id,
        company_uid,
        reason=reason,
        session_id=session_id,
        success_message="업체 담당자를 변경했습니다.",
        extra={"p_new_assigned_user_id": _user_id(new_assigned_user_id)},
        db=db,
    )


def admin_release_assignment(
    current_user_id: str,
    company_id: Any,
    company_uid: Any,
    *,
    reason: Any,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    return _admin_mutation(
        RPC_ADMIN_RELEASE,
        current_user_id,
        company_id,
        company_uid,
        reason=reason,
        session_id=session_id,
        success_message="업체 배정을 강제로 회수했습니다.",
        db=db,
    )


def admin_reactivate(
    current_user_id: str,
    company_id: Any,
    company_uid: Any,
    *,
    reason: Any,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    return _admin_mutation(
        RPC_ADMIN_REACTIVATE,
        current_user_id,
        company_id,
        company_uid,
        reason=reason,
        session_id=session_id,
        success_message="업체를 재활성화했습니다.",
        db=db,
    )


def admin_permanent_exclude(
    current_user_id: str,
    company_id: Any,
    company_uid: Any,
    *,
    reason: Any,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    return _admin_mutation(
        RPC_ADMIN_PERMANENT_EXCLUDE,
        current_user_id,
        company_id,
        company_uid,
        reason=reason,
        session_id=session_id,
        success_message="업체를 영구 제외했습니다.",
        db=db,
    )


def admin_review_returned_batch(
    current_user_id: str,
    company_uids: Sequence[Any],
    *,
    action: str,
    reason: Any,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    clean_uids = list(
        dict.fromkeys(_company_uid(value) for value in (company_uids or []))
    )
    if not clean_uids or len(clean_uids) > 100:
        return {
            "ok": False,
            "code": "INVALID_BATCH_SIZE",
            "message": "한 번에 처리할 반납 DB를 1개 이상 100개 이하로 선택해 주세요.",
            "processed_count": 0,
        }
    clean_action = _text(action).lower()
    if clean_action not in {"reactivate", "permanent_exclude"}:
        return {
            "ok": False,
            "code": "INVALID_ACTION",
            "message": "지원하지 않는 반납 DB 처리 방식입니다.",
            "processed_count": 0,
        }
    clean_reason = _bounded(reason, 400)
    if not clean_reason:
        return {
            "ok": False,
            "code": "REASON_REQUIRED",
            "message": "검토 사유를 입력해 주세요.",
            "processed_count": 0,
        }

    raw, error = _rpc(
        RPC_ADMIN_REVIEW_RETURNED_BATCH,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_company_uids": clean_uids,
            "p_action": clean_action,
            "p_reason": clean_reason,
            "p_session_id": _nullable_text(session_id),
        },
        db=db,
    )
    if error:
        return {**error, "processed_count": 0}
    row = _first_row(raw)
    if row is None:
        return {
            "ok": False,
            "code": "MALFORMED_RESPONSE",
            "message": _FAILURE_MESSAGES["MALFORMED_RESPONSE"],
            "processed_count": 0,
        }
    success_value = row.get("success", row.get("ok"))
    ok = success_value is True or _text(success_value).lower() in {
        "true",
        "t",
        "1",
    }
    processed_count = max(0, int(row.get("processed_count") or 0))
    return {
        "ok": ok,
        "code": _safe_code(row.get("code"), "OK" if ok else "REQUEST_FAILED"),
        "message": (
            "선택한 반납 DB를 일괄 처리했습니다."
            if ok
            else "반납 DB 일괄 처리를 완료하지 못했습니다."
        ),
        "processed_count": processed_count if ok else 0,
    }


def get_user_limits(
    admin_user_id: str,
    target_user_id: str,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    raw, error = _rpc(
        RPC_GET_USER_LIMITS,
        {
            "p_current_user_id": _user_id(admin_user_id),
            "p_target_user_id": _user_id(target_user_id),
        },
        db=db,
    )
    if error:
        return {**error, "limits": {}}
    row = _first_row(raw)
    if row is None:
        return {
            "ok": False,
            "code": "MALFORMED_RESPONSE",
            "message": _FAILURE_MESSAGES["MALFORMED_RESPONSE"],
            "limits": {},
        }
    try:
        limits = {
            "max_uncontacted": int(row.get("max_uncontacted") or 0),
            "max_landline_db": int(row.get("max_landline_db") or 0),
            "max_mobile_db": int(row.get("max_mobile_db") or 0),
        }
    except (TypeError, ValueError):
        limits = {}
    if not limits or any(value < 1 or value > 1000 for value in limits.values()):
        return {
            "ok": False,
            "code": "MALFORMED_RESPONSE",
            "message": _FAILURE_MESSAGES["MALFORMED_RESPONSE"],
            "limits": {},
        }
    return {
        "ok": True,
        "code": "OK",
        "message": "사용자별 DB 한도를 불러왔습니다.",
        "limits": limits,
    }


def admin_set_user_limit(
    admin_user_id: str,
    target_user_id: str,
    max_uncontacted: int,
    max_landline_db: int,
    max_mobile_db: int,
    reason: Any,
    session_id: str = "",
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    try:
        safe_limit = int(max_uncontacted)
        safe_landline_limit = int(max_landline_db)
        safe_mobile_limit = int(max_mobile_db)
    except (TypeError, ValueError):
        safe_limit = -1
        safe_landline_limit = -1
        safe_mobile_limit = -1
    if any(
        value < 1 or value > 1000
        for value in (safe_limit, safe_landline_limit, safe_mobile_limit)
    ):
        return {
            "ok": False,
            "code": "INVALID_INPUT",
            "message": "각 DB 한도는 1~1000 사이로 입력해 주세요.",
            "assignment": {},
        }
    raw, error = _rpc(
        RPC_ADMIN_SET_USER_LIMIT,
        {
            "p_admin_user_id": _user_id(admin_user_id),
            "p_target_user_id": _user_id(target_user_id),
            "p_max_uncontacted": safe_limit,
            "p_max_landline_db": safe_landline_limit,
            "p_max_mobile_db": safe_mobile_limit,
            "p_reason": _bounded(reason, 500),
            "p_session_id": _bounded(session_id, 200),
        },
        db=db,
    )
    if error:
        return error
    if isinstance(raw, bool):
        return {
            "ok": raw,
            "code": "UPDATED" if raw else "REQUEST_FAILED",
            "message": (
                "영업사원의 DB 한도를 변경했습니다."
                if raw
                else "DB 한도를 변경하지 못했습니다."
            ),
            "assignment": {
                "target_user_id": _user_id(target_user_id),
                "max_uncontacted": safe_limit,
                "max_landline_db": safe_landline_limit,
                "max_mobile_db": safe_mobile_limit,
            },
            "warning": "" if raw else "DB 한도를 변경하지 못했습니다.",
            "fallback_required": False,
        }
    return _mutation_result(
        raw,
        success_message="영업사원의 DB 한도를 변경했습니다.",
        admin=True,
    )


def submit_mobile_db_request(
    current_user_id: str,
    region: Any,
    district: Any = "",
    business_type: Any = "all",
    *,
    minimum_employees: int = 1,
    maximum_employees: int = 300,
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    try:
        minimum_count = max(1, min(int(minimum_employees), 10000))
        maximum_count = max(1, min(int(maximum_employees), 10000))
    except (TypeError, ValueError):
        minimum_count = 1
        maximum_count = 300
    if maximum_count < minimum_count:
        return {
            "ok": False,
            "code": "INVALID_INPUT",
            "message": "최대 고용인원은 최소 고용인원보다 크거나 같아야 합니다.",
            "assignment": {},
            "warning": "고용인원 범위를 확인해 주세요.",
            "fallback_required": False,
        }
    raw, error = _rpc(
        RPC_SUBMIT_MOBILE_DB_REQUEST,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_region": _bounded(region, 100),
            "p_district": _bounded(district, 100),
            "p_business_type": _bounded(business_type, 20).lower() or "all",
            "p_minimum_employees": minimum_count,
            "p_maximum_employees": maximum_count,
            "p_session_id": _bounded(session_id, 200) or None,
        },
        db=db,
    )
    if error:
        return error
    return _mobile_request_mutation_result(
        raw,
        success_message="핸드폰 DB 배정 신청을 접수했습니다.",
    )


def list_user_mobile_db_requests(
    current_user_id: str,
    *,
    limit: int = 20,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    raw, error = _rpc(
        RPC_LIST_USER_MOBILE_DB_REQUESTS,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_limit": max(1, min(int(limit), 100)),
        },
        db=db,
    )
    if error:
        return {**error, "requests": []}
    requests = [
        {
            key: value
            for key, value in row.items()
            if key in _MOBILE_DB_REQUEST_FIELDS
        }
        for row in _rows(raw)
    ]
    return {
        "ok": True,
        "code": "OK",
        "message": "핸드폰 DB 신청내역을 불러왔습니다.",
        "assignment": {},
        "requests": requests,
        "warning": "",
        "fallback_required": False,
    }


def _mobile_request_mutation_result(
    raw: Any,
    *,
    success_message: str,
) -> dict[str, Any]:
    row = _first_row(raw)
    if row is None:
        return _mutation_result(raw, success_message=success_message)
    success_value = row.get("success", row.get("ok"))
    ok = success_value is True or _text(success_value).lower() in {
        "true",
        "t",
        "1",
    }
    code = _safe_code(row.get("code"), "OK" if ok else "REQUEST_FAILED")
    request = {
        key: value
        for key, value in row.items()
        if key in _MOBILE_DB_REQUEST_FIELDS
    }
    return {
        "ok": ok,
        "code": code,
        "message": _safe_message(
            code,
            success_message=_text(row.get("message")) or success_message,
            ok=ok,
        ),
        "assignment": {},
        "request": request,
        "warning": (
            ""
            if ok
            else _FAILURE_MESSAGES.get(code, "요청을 처리하지 못했습니다.")
        ),
        "fallback_required": False,
    }


def list_admin_mobile_db_requests(
    current_user_id: str,
    *,
    statuses: Sequence[str] | None = None,
    limit: int = 200,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    raw, error = _rpc(
        RPC_LIST_ADMIN_MOBILE_DB_REQUESTS,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_statuses": [
                _text(status).lower()
                for status in (statuses or [])
                if _text(status)
            ],
            "p_limit": max(1, min(int(limit), 1000)),
        },
        db=db,
    )
    if error:
        return {**error, "requests": []}
    requests = [
        {
            key: value
            for key, value in row.items()
            if key in _MOBILE_DB_REQUEST_FIELDS
        }
        for row in _rows(raw)
    ]
    return {
        "ok": True,
        "code": "OK",
        "message": "핸드폰 DB 신청현황을 불러왔습니다.",
        "assignment": {},
        "requests": requests,
        "warning": "",
        "fallback_required": False,
    }


def admin_update_mobile_db_request(
    current_user_id: str,
    request_id: Any,
    action: Any,
    *,
    allocated_count: int = 0,
    reason: Any = "",
    session_id: str = "",
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    try:
        safe_count = max(0, min(int(allocated_count), 100))
    except (TypeError, ValueError):
        safe_count = 0
    raw, error = _rpc(
        RPC_ADMIN_UPDATE_MOBILE_DB_REQUEST,
        {
            "p_current_user_id": _user_id(current_user_id),
            "p_request_id": _text(request_id),
            "p_action": _bounded(action, 20).lower(),
            "p_allocated_count": safe_count,
            "p_reason": _bounded(reason, 500),
            "p_session_id": _bounded(session_id, 200) or None,
        },
        db=db,
    )
    if error:
        return error
    return _mobile_request_mutation_result(
        raw,
        success_message="핸드폰 DB 신청을 처리했습니다.",
    )


__all__ = [
    "CompanyIdentityError",
    "admin_change_assignee",
    "admin_permanent_exclude",
    "admin_review_returned_batch",
    "admin_release_assignment",
    "admin_reactivate",
    "admin_set_user_limit",
    "admin_update_mobile_db_request",
    "assignment_feature_ready",
    "assignment_status_label",
    "build_company_uid",
    "claim_and_save_companies",
    "claim_and_save_company",
    "claim_companies",
    "claim_company",
    "filter_company_availability",
    "get_user_limits",
    "get_user_db_dashboard",
    "list_admin_assignments",
    "list_admin_assignment_metrics",
    "list_admin_assignment_audit",
    "list_admin_mobile_db_requests",
    "list_blocked_company_uids",
    "list_company_contacts",
    "list_user_assignments",
    "list_user_db_assignments",
    "list_user_mobile_db_requests",
    "record_contact",
    "record_company_views",
    "resolve_candidate_company_uids",
    "release_assignment",
    "release_expired_assignments",
    "save_user_note",
    "submit_mobile_db_request",
]
