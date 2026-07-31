from __future__ import annotations

import hashlib
import hmac
import html
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

import pandas as pd
import streamlit as st
from cryptography.fernet import Fernet, InvalidToken

from claim_correction_catalog import (
    AUTOMATIC_COLLECTION_CODES,
    DOCUMENT_SPECS,
    automatic_collection_supported,
)
from claim_correction_repository import (
    CLAIM_DOWNLOAD_EXTENSION_BY_CONTENT_TYPE,
    CLAIM_DOWNLOAD_URL_TTL_SECONDS,
    CLAIM_STORAGE_BUCKET,
    ClaimRepository,
    ClaimRepositoryError,
)
from registered_policy_match import (
    build_customer_labels,
    load_registered_customers,
)
from tilko_claim_client import (
    ClaimProviderError,
    TilkoClaimClient,
    provider_readiness,
)
from utils import get_user_cumulative_db_path


CONSENT_VERSION = "claim-collection-v2-2026-07"
RETENTION_POLICY_VERSION = "claim-document-retention-v1-2026-07"
CONSENT_NOTICE_TEXT = (
    "고객에게 수집 항목·이용 목적·보유기간·제3자 제공 내용을 안내했고 "
    "유효한 동의를 확인했습니다. 주민등록번호는 DB·로그에 저장하지 않고 "
    "인증 및 자료수집 중 암호화된 서버 메모리에 최대 45분 보관한 뒤 "
    "즉시 삭제합니다."
)
COLLECTION_AUTHORITY_TEXT = (
    "민감정보 처리가 필요한 경우 적용되는 법적 근거와 위임 범위를 "
    "확인했습니다."
)
CONSENT_TEXT_SHA256 = hashlib.sha256(
    f"{CONSENT_NOTICE_TEXT}|{COLLECTION_AUTHORITY_TEXT}".encode("utf-8")
).hexdigest()
AUTH_TTL_SECONDS = 10 * 60
COLLECTION_TTL_SECONDS = 45 * 60
AUTH_POLL_SECONDS = 1.0
AUTH_MEDIUM_POLL_SECONDS = 3.0
AUTH_SLOW_POLL_SECONDS = 10.0
AUTH_FAST_POLL_WINDOW_SECONDS = 30.0
AUTH_MEDIUM_POLL_WINDOW_SECONDS = 90.0
TRANSIENT_AUTH_RETRY_SECONDS = 3.0
COMWEL_DISPATCH_DELAY_SECONDS = 1.0
_CLAIM_JOB_CIPHER = Fernet(Fernet.generate_key())
_CLAIM_BUSINESS_TOKEN_KEY = Fernet.generate_key()
_CLAIM_JOB_LOCK = threading.RLock()
_CLAIM_JOBS: dict[str, dict[str, Any]] = {}
_CLAIM_JOB_EXECUTOR = ThreadPoolExecutor(
    max_workers=4,
    thread_name_prefix="claim-auth",
)
_CLAIM_SWEEPER_STARTED = False
STATUS_LABELS = {
    "request_ready": "발송 준비",
    "auth_preparing": "인증 준비",
    "auth_requested": "고객 인증 대기",
    "auth_pending": "고객 인증 대기",
    "auth_complete": "인증 완료",
    "auth_partial": "일부 인증 완료 · 재요청 필요",
    "certificate_required": "공동인증서 대기",
    "collection_queued": "자료수집 대기",
    "auth_complete_collection_pending": "인증 완료 · 일부 자료 재수집 필요",
    "integration_required": "자동수집 연동 예정",
    "collecting": "자료수집 중",
    "collected": "수집 완료",
    "ready": "결과 확인 가능",
    "failed": "실패",
    "not_requested": "제외",
}

_REMOTE_INVITE_NOTICE_KEY = "claim_remote_invite_notice_v1"
_REMOTE_INVITE_NAME_PATTERN = re.compile(r"^[가-힣]+(?:[ ·][가-힣]+)*$")
_REMOTE_INVITE_PHONE_PATTERN = re.compile(r"^010\d{8}$")
_KOREA_TIMEZONE = timezone(timedelta(hours=9))
_REMOTE_INVITE_ERROR_MESSAGES = {
    "PUBLIC_BASE_URL_REQUIRED": (
        "고객 인증 주소 설정이 완료되지 않았습니다. 관리자에게 문의해주세요."
    ),
    "REMOTE_STORAGE_NOT_CONFIGURED": (
        "원격 인증 저장소 연결이 준비되지 않았습니다. 관리자에게 문의해주세요."
    ),
    "REMOTE_CRYPTO_NOT_CONFIGURED": (
        "원격 인증 보안 설정이 준비되지 않았습니다. 관리자에게 문의해주세요."
    ),
    "REMOTE_REPOSITORY_UNAVAILABLE": (
        "원격 인증 저장소에 연결하지 못했습니다. 잠시 후 다시 시도해주세요."
    ),
    "REMOTE_INVITE_CREATE_FAILED": (
        "고객 인증 요청을 저장하지 못했습니다. 잠시 후 다시 시도해주세요."
    ),
    "REMOTE_INVITE_NOT_READY": (
        "카카오톡 원격 인증 발송 설정이 완료되지 않았습니다. "
        "관리자에게 문의해주세요."
    ),
    "REMOTE_OWNER_REQUIRED": "로그인 정보를 다시 확인해주세요.",
    "INVALID_CUSTOMER_NAME": "고객 이름을 한글로 정확히 입력해주세요.",
    "INVALID_CUSTOMER_PHONE": "010으로 시작하는 휴대전화번호를 확인해주세요.",
}


class RemoteInviteUIError(RuntimeError):
    """A redacted remote-invite error that is safe to render."""


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return text


def _claim_document_is_downloadable(
    document: dict[str, Any],
    *,
    now: datetime | None = None,
) -> bool:
    facts = document.get("facts")
    if isinstance(facts, dict) and facts.get("no_data") is True:
        return False
    if (
        str(document.get("status", "") or "").strip().lower() != "ready"
        or not _clean(document.get("storage_path"))
        or bool(document.get("deleted_at"))
        or _clean(document.get("storage_bucket")) != CLAIM_STORAGE_BUCKET
    ):
        return False
    content_type = _clean(document.get("content_type")).lower()
    expected_extension = CLAIM_DOWNLOAD_EXTENSION_BY_CONTENT_TYPE.get(
        content_type,
        "",
    )
    if (
        not expected_extension
        or not _clean(document.get("storage_path"))
        .lower()
        .endswith(expected_extension)
    ):
        return False
    retention_until = _clean(document.get("retention_until"))
    if not retention_until:
        return False
    try:
        retention_deadline = datetime.fromisoformat(
            retention_until.replace("Z", "+00:00")
        )
        if retention_deadline.tzinfo is None:
            retention_deadline = retention_deadline.replace(
                tzinfo=timezone.utc
            )
    except ValueError:
        return False
    reference_now = now or datetime.now(timezone.utc)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=timezone.utc)
    return retention_deadline > reference_now


def _digits(value: Any) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def _validate_remote_invite_input(
    customer_name: Any,
    customer_phone: Any,
) -> tuple[str, str, list[str]]:
    name = re.sub(r"\s+", " ", str(customer_name or "")).strip()
    phone = _digits(customer_phone)
    errors: list[str] = []
    hangul_count = len(re.sub(r"[^가-힣]", "", name))
    if (
        not _REMOTE_INVITE_NAME_PATTERN.fullmatch(name)
        or not 2 <= hangul_count <= 20
    ):
        errors.append("고객 이름은 한글 2~20자로 입력해주세요.")
    if not _REMOTE_INVITE_PHONE_PATTERN.fullmatch(phone):
        errors.append("010으로 시작하는 휴대전화번호 11자리를 입력해주세요.")
    return name, phone, errors


def _remote_invite_safe_error(exc: BaseException) -> str:
    error_code = str(
        getattr(exc, "error_code", "")
        or getattr(exc, "code", "")
        or ""
    ).strip().upper()
    return _REMOTE_INVITE_ERROR_MESSAGES.get(
        error_code,
        "인증 링크 발송 준비 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
    )


def _remote_invite_runtime_readiness(
    checker: Callable[[], Mapping[str, Any]] | None = None,
) -> tuple[bool, str]:
    selected_checker = checker
    if selected_checker is None:
        try:
            from claim_remote_service import (  # noqa: PLC0415
                remote_invite_environment_readiness,
            )
        except (ImportError, ModuleNotFoundError):
            return (
                False,
                "카카오톡 원격 인증 발송 기능을 준비하지 못했습니다. "
                "관리자에게 문의해주세요.",
            )
        selected_checker = remote_invite_environment_readiness
    try:
        readiness = dict(selected_checker() or {})
    except Exception:
        return (
            False,
            "카카오톡 원격 인증 발송 설정을 확인하지 못했습니다. "
            "관리자에게 문의해주세요.",
        )
    if bool(readiness.get("ready")):
        return True, ""
    return (
        False,
        "카카오톡 원격 인증 발송 설정이 완료되지 않았습니다. "
        "관리자에게 문의해주세요.",
    )


def _create_remote_claim_invite(
    *,
    owner_user_id: str,
    requested_by: str,
    customer_name: str,
    customer_phone: str,
    invite_creator: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    name, phone, errors = _validate_remote_invite_input(
        customer_name,
        customer_phone,
    )
    if errors:
        raise RemoteInviteUIError(errors[0])

    creator = invite_creator
    if creator is None:
        try:
            from claim_remote_service import (  # noqa: PLC0415
                create_staff_claim_invite,
            )
        except (ImportError, ModuleNotFoundError) as exc:
            raise RemoteInviteUIError(
                "카카오톡 인증 발송 기능을 불러오지 못했습니다. "
                "관리자에게 문의해주세요."
            ) from exc
        creator = create_staff_claim_invite

    try:
        result = creator(
            owner_user_id=str(owner_user_id or "").strip().lower(),
            requested_by=str(requested_by or owner_user_id).strip(),
            customer_name=name,
            customer_phone=phone,
        )
    except Exception as exc:
        raise RemoteInviteUIError(_remote_invite_safe_error(exc)) from None

    if not isinstance(result, dict) or not result.get("invite_id"):
        raise RemoteInviteUIError(
            "고객 인증 요청의 저장 결과를 확인하지 못했습니다. "
            "잠시 후 다시 시도해주세요."
        )
    if result.get("message_queued") is not True:
        raise RemoteInviteUIError(
            "카카오톡 발송 대기열 등록 결과를 확인하지 못했습니다. "
            "잠시 후 다시 시도해주세요."
        )
    return dict(result)


def _format_remote_invite_expiry(value: Any) -> str:
    selected: datetime | None = None
    if isinstance(value, datetime):
        selected = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            selected = datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            selected = None
    else:
        text = str(value or "").strip()
        if text:
            try:
                selected = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                selected = None
    if selected is None:
        return ""
    if selected.tzinfo is None:
        selected = selected.replace(tzinfo=timezone.utc)
    return selected.astimezone(_KOREA_TIMEZONE).strftime(
        "%Y년 %m월 %d일 %H:%M"
    )


def _format_business_no(value: Any) -> str:
    digits = _digits(value)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return _clean(value)


def _format_phone(value: Any) -> str:
    digits = _digits(value)
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return _clean(value)


def _birth_date_from_identity(front: str, rear: str) -> str:
    front_digits = _digits(front)
    rear_digits = _digits(rear)
    if len(front_digits) != 6:
        return ""
    century_code = rear_digits[:1]
    if century_code in {"1", "2", "5", "6"}:
        century = "19"
    elif century_code in {"3", "4", "7", "8"}:
        century = "20"
    else:
        return ""
    birth_date = f"{century}{front_digits}"
    try:
        datetime.strptime(birth_date, "%Y%m%d")
    except ValueError:
        return ""
    return birth_date


def _is_valid_business_no(value: Any) -> bool:
    digits = _digits(value)
    if len(digits) != 10:
        return False
    weights = (1, 3, 7, 1, 3, 7, 1, 3, 5)
    checksum = sum(
        int(digit) * weight
        for digit, weight in zip(digits[:9], weights)
    )
    checksum += (int(digits[8]) * 5) // 10
    expected = (10 - (checksum % 10)) % 10
    return expected == int(digits[-1])


def _repository(user_id: str) -> tuple[ClaimRepository | None, str]:
    try:
        repository = ClaimRepository(user_id)
        status = repository.status()
        if not status.available:
            return None, status.message
        return repository, ""
    except ClaimRepositoryError as exc:
        return None, str(exc)


def _source_status(value: Any) -> str:
    key = str(value or "").strip()
    return STATUS_LABELS.get(key, key or "-")


def _claim_result_document_status(
    document: dict[str, Any],
    selected_case: dict[str, Any],
    *,
    multiple_management_numbers: bool = False,
    no_management_workplaces: bool = False,
) -> str:
    document_code = str(document.get("document_code", ""))
    status = str(document.get("status", ""))
    facts = document.get("facts")
    document_safe_error_code = (
        str(facts.get("safe_error_code", ""))
        if isinstance(facts, dict)
        else ""
    )
    case_safe_error_code = str(
        selected_case.get("last_safe_error_code", "")
    )
    business_dependent_codes = {
        "hometax_business_registration_certificate",
        "comwel_management_number_list",
        "comwel_workplace_rate",
    }

    if isinstance(facts, dict) and facts.get("no_data") is True:
        return "조회된 신고내역 없음"
    if (
        document_code == "comwel_workplace_rate"
        and no_management_workplaces
    ):
        return "조회된 가입 사업장 없음"
    if (
        document_code == "comwel_worker_status"
        and status == "integration_required"
    ):
        return "공동인증서 필요"
    if (
        document_code == "comwel_workplace_rate"
        and status == "integration_required"
        and multiple_management_numbers
    ):
        return "사업장 선택 필요"
    if (
        document_code in business_dependent_codes
        and (
            document_safe_error_code == "BUSINESS_NUMBER_NOT_FOUND"
            or (
                status in {"auth_pending", "integration_required"}
                and case_safe_error_code == "BUSINESS_NUMBER_NOT_FOUND"
            )
        )
    ):
        return "홈택스 사업자번호 확인 필요"
    if (
        document_code == "hometax_business_registration_list"
        and status == "ready"
        and str(
            (facts or {}).get("record_count", "")
            if isinstance(facts, dict)
            else ""
        )
        == "0"
        and case_safe_error_code == "BUSINESS_NUMBER_NOT_FOUND"
    ):
        return "사업자번호 미확인"
    if (
        status == "integration_required"
        and automatic_collection_supported(document_code)
    ):
        return "재수집 필요"
    if (
        status == "auth_pending"
        and not automatic_collection_supported(document_code)
    ):
        return _source_status("integration_required")
    return _source_status(status)


def _resolve_auth_progress(
    expected_sources: list[str],
    source_statuses: dict[str, Any],
) -> tuple[str, bool, bool]:
    expected = [
        str(source_statuses.get(source, "") or "").strip()
        for source in expected_sources
        if source in {"hometax", "comwel"}
    ]
    all_completed = bool(expected) and all(
        status == "auth_complete" for status in expected
    )
    any_failed = any(status == "failed" for status in expected)
    overall_status = (
        "auth_complete_collection_pending"
        if all_completed
        else "auth_partial"
        if any_failed
        else "auth_pending"
    )
    return overall_status, all_completed, any_failed


CLAIM_AUTH_STAGE_MESSAGES = (
    "국세청 홈택스 인증요청을 보냈습니다.",
    "홈택스 인증이 완료되어 근로복지공단 인증을 발송했습니다.",
    "최종 인증이 모두 완료되었습니다.",
    "자료를 수집하겠습니다.",
)


def _claim_auth_stage(case: dict[str, Any]) -> tuple[int, str]:
    overall_status = str(case.get("overall_status", "") or "").strip()
    hometax_status = str(case.get("hometax_status", "") or "").strip()
    comwel_status = str(case.get("comwel_status", "") or "").strip()

    both_complete = (
        hometax_status == "auth_complete"
        and comwel_status == "auth_complete"
    )
    if hometax_status == "failed":
        return 1, "국세청 홈택스 인증을 완료하지 못했습니다."
    if hometax_status == "auth_complete" and comwel_status == "failed":
        return 2, "근로복지공단 인증을 완료하지 못했습니다."
    if both_complete and overall_status in {
        "auth_complete_collection_pending",
        "collecting",
        "collected",
        "ready",
    }:
        return 4, CLAIM_AUTH_STAGE_MESSAGES[3]
    if both_complete:
        return 3, CLAIM_AUTH_STAGE_MESSAGES[2]
    if (
        hometax_status == "auth_complete"
        and comwel_status in {
            "request_ready",
            "auth_requested",
            "auth_pending",
            "auth_complete",
        }
    ):
        return 2, CLAIM_AUTH_STAGE_MESSAGES[1]
    if hometax_status in {
        "auth_requested",
        "auth_pending",
        "auth_complete",
        "failed",
    }:
        return 1, CLAIM_AUTH_STAGE_MESSAGES[0]
    return 0, "국세청 홈택스 인증요청을 준비하고 있습니다."


def _claim_collection_progress(
    documents: list[dict[str, Any]],
) -> tuple[int, str, int, int]:
    management_document = next(
        (
            document
            for document in documents
            if str(document.get("document_code", "")).strip()
            == "comwel_management_number_list"
            and str(document.get("status", "")).strip().lower() == "ready"
        ),
        {},
    )
    management_facts = (
        management_document.get("facts")
        if isinstance(management_document, dict)
        else {}
    )
    no_management_workplaces = bool(
        isinstance(management_facts, dict)
        and (
            str(management_facts.get("record_count", "")) == "0"
            or management_facts.get("management_numbers") == []
        )
    )
    targets = [
        document
        for document in documents
        if automatic_collection_supported(
            str(document.get("document_code", "")).strip()
        )
        and not (
            no_management_workplaces
            and str(document.get("document_code", "")).strip()
            == "comwel_workplace_rate"
        )
    ]
    target_count = len(targets)
    ready_count = sum(
        1
        for document in targets
        if str(document.get("status", "")).strip().lower() == "ready"
    )
    if target_count:
        calculated = round((ready_count / target_count) * 100)
        percentage = (
            100
            if ready_count >= target_count
            else min(99, calculated)
        )
    else:
        percentage = 0
    return (
        percentage,
        (
            f"{percentage}% · 자동수집 대상 {target_count}건 중 "
            f"{ready_count}건 수집 완료"
            if target_count
            else "0% · 수집할 자동연동 자료를 확인하고 있습니다."
        ),
        ready_count,
        target_count,
    )


def _claim_collection_progress_from_repository(
    repository: Any,
    case_id: str,
) -> tuple[int, str, int, int, bool]:
    try:
        documents = repository.list_documents(case_id)
        if isinstance(documents, list):
            percentage, text, ready_count, target_count = (
                _claim_collection_progress(documents)
            )
            return (
                percentage,
                text,
                ready_count,
                target_count,
                True,
            )
    except Exception:
        # 진행률 조회 실패가 실제 서류 수집을 중단시키면 안 된다.
        pass
    return (
        0,
        "0% · Supabase의 실제 수집 자료를 확인하고 있습니다.",
        0,
        0,
        False,
    )


def _claim_progress(
    case: dict[str, Any],
    documents: list[dict[str, Any]] | None = None,
) -> tuple[int, str]:
    if documents is not None:
        percentage, text, _, _ = _claim_collection_progress(documents)
        return percentage, text
    return 0, "0% · 실제 수집 자료를 확인한 뒤 진행률을 표시합니다."


def _render_claim_auth_stage(case: dict[str, Any]) -> None:
    stage, _ = _claim_auth_stage(case)
    overall_status = str(case.get("overall_status", "") or "").strip()
    hometax_status = str(case.get("hometax_status", "") or "").strip()
    comwel_status = str(case.get("comwel_status", "") or "").strip()
    failed_stage = (
        1
        if hometax_status == "failed"
        else 2
        if hometax_status == "auth_complete" and comwel_status == "failed"
        else 0
    )
    collection_finished = overall_status in {"ready", "collected"}
    rows: list[str] = []
    for index, message in enumerate(CLAIM_AUTH_STAGE_MESSAGES, start=1):
        if index == failed_stage:
            state = "실패"
            background = "#fff1f1"
            border = "#f2b8b8"
            color = "#b42318"
        elif index < stage or (
            index == 4 and stage == 4 and collection_finished
        ):
            state = "완료"
            background = "#eef8f2"
            border = "#b9e2c8"
            color = "#18733d"
        elif index == stage:
            state = "진행"
            background = "#eef5ff"
            border = "#b9d3ff"
            color = "#155dcc"
        else:
            state = "대기"
            background = "#f7f8fa"
            border = "#e1e5eb"
            color = "#758096"
        rows.append(
            (
                f'<div style="display:flex;align-items:center;gap:.7rem;'
                f'padding:.62rem .75rem;border:1px solid {border};'
                f'border-radius:10px;background:{background};">'
                f'<span style="font-weight:800;color:{color};'
                f'min-width:1.8rem;">{index:02d}</span>'
                f'<span style="flex:1;color:#17335f;">'
                f'{html.escape(message)}</span>'
                f'<span style="font-size:.78rem;font-weight:700;'
                f'color:{color};">{state}</span></div>'
            )
        )
    st.markdown(
        '<div style="display:grid;gap:.45rem;margin:.2rem 0 1rem;">'
        + "".join(rows)
        + "</div>",
        unsafe_allow_html=True,
    )


def _next_auth_action(
    case: dict[str, Any],
    transient: dict[str, Any],
) -> tuple[str, str]:
    hometax_status = str(case.get("hometax_status", "") or "")
    comwel_status = str(case.get("comwel_status", "") or "")
    has_hometax_session = bool(transient.get("hometax"))
    has_comwel_session = bool(transient.get("comwel"))

    if hometax_status != "auth_complete" and has_hometax_session:
        return (
            "check_hometax",
            "홈택스 인증 확인 후 근로복지공단 발송",
        )
    if hometax_status == "auth_complete" and comwel_status != "auth_complete":
        if has_comwel_session:
            return (
                "check_comwel",
                "근로복지공단 인증 확인 및 자료수집",
            )
        return "request_comwel", "근로복지공단 카카오 인증 발송"
    if (
        hometax_status == "auth_complete"
        and comwel_status == "auth_complete"
        and has_hometax_session
    ):
        return "collect", "홈택스 서류 다시 수집"
    return "", ""


def _safe_provider_error_code(
    exc: Exception,
    source: str = "HOMETAX",
) -> str:
    prefix = re.sub(r"[^A-Z0-9_]", "", str(source or "").upper())
    if not prefix:
        prefix = "DOCUMENT"
    match = re.search(
        r"(?:오류코드|TargetCode|ErrorCode)\s*[:：]\s*([A-Za-z0-9_-]+)",
        str(exc),
        flags=re.IGNORECASE,
    )
    if match:
        return f"{prefix}_{match.group(1).upper()}"[:80]
    return f"{prefix}_DOCUMENT_COLLECTION_FAILED"[:80]


def _provider_error_code(exc: Exception) -> str:
    explicit = str(getattr(exc, "error_code", "") or "").strip().upper()
    if explicit:
        return explicit
    match = re.search(
        r"(?:오류코드|TargetCode|ErrorCode)\s*[:：]\s*([A-Za-z0-9_-]+)",
        str(exc),
        flags=re.IGNORECASE,
    )
    return match.group(1).upper() if match else ""


def _is_transient_auth_error(exc: Exception) -> bool:
    return _provider_error_code(exc) in {"OACX_NO_USER"}


def _document_label(document_code: str) -> str:
    code = str(document_code or "").strip()
    for spec in DOCUMENT_SPECS:
        if spec.code == code:
            return spec.name
    return code or "서류"


def _auth_poll_delay(transient: dict[str, Any]) -> float:
    stage_started_at = float(
        transient.get("stage_started_at", time.time()) or time.time()
    )
    elapsed = time.time() - stage_started_at
    if elapsed <= AUTH_FAST_POLL_WINDOW_SECONDS:
        return AUTH_POLL_SECONDS
    if elapsed <= AUTH_MEDIUM_POLL_WINDOW_SECONDS:
        return AUTH_MEDIUM_POLL_SECONDS
    return AUTH_SLOW_POLL_SECONDS


def _claim_absolute_expiry(transient: dict[str, Any]) -> float:
    absolute_expires_at = float(
        transient.get("absolute_expires_at", 0) or 0
    )
    if absolute_expires_at > 0:
        return absolute_expires_at
    request_started_at = float(
        transient.get("request_started_at", time.time()) or time.time()
    )
    absolute_expires_at = request_started_at + COLLECTION_TTL_SECONDS
    transient["request_started_at"] = request_started_at
    transient["absolute_expires_at"] = absolute_expires_at
    return absolute_expires_at


def _set_claim_expiry(
    transient: dict[str, Any],
    ttl_seconds: float,
) -> float:
    expires_at = min(
        time.time() + max(float(ttl_seconds), 0.0),
        _claim_absolute_expiry(transient),
    )
    transient["expires_at"] = expires_at
    return expires_at


def _ensure_claim_operation_active(
    should_continue: Any | None,
) -> None:
    if callable(should_continue) and not bool(should_continue()):
        raise ClaimProviderError(
            "인증 유효시간이 지나 임시 인증정보를 삭제했습니다.",
            error_code="AUTH_SESSION_EXPIRED",
        )


def _masked_business_no(value: Any) -> str:
    digits = _digits(value)
    if len(digits) != 10:
        return ""
    return f"{digits[:3]}-**-*****"


def _masked_business_choice_no(value: Any) -> str:
    digits = _digits(value)
    if len(digits) != 10:
        return ""
    return f"{digits[:3]}-**-***{digits[-2:]}"


def _business_candidate_token(case_id: str, business_number: str) -> str:
    payload = (
        f"{str(case_id or '').strip()}|{_digits(business_number)}"
    ).encode("utf-8")
    return hmac.new(
        _CLAIM_BUSINESS_TOKEN_KEY,
        payload,
        hashlib.sha256,
    ).hexdigest()[:24]


def _discover_hometax_business_number(
    repository: ClaimRepository,
    client: TilkoClaimClient,
    *,
    case_id: str,
    birth_date: str,
    representative: str,
    cellphone: str,
    session: dict[str, str],
    transient: dict[str, Any],
    on_progress: Any | None = None,
    should_continue: Any | None = None,
) -> dict[str, Any]:
    documents = [
        document
        for document in repository.list_documents(case_id)
        if str(document.get("source", "")) == "hometax"
        and str(document.get("document_code", ""))
        == "hometax_business_registration_list"
    ]
    if not documents:
        return {
            "target": 0,
            "ready": 0,
            "failed": 0,
            "errors": [],
            "business_number": _digits(transient.get("business_number")),
            "candidates": [],
            "selection_required": False,
        }

    existing_ready = any(
        str(document.get("status", "")) == "ready" for document in documents
    )
    raw_candidates = transient.get("business_candidates")
    candidates = (
        [dict(candidate) for candidate in raw_candidates]
        if isinstance(raw_candidates, list)
        else []
    )
    errors: list[dict[str, str]] = []
    ready_count = 1 if existing_ready else 0
    failed_count = 0
    discovery_already_attempted = bool(
        transient.get("hometax_business_discovery_attempted")
    )

    if not candidates and not discovery_already_attempted:
        if on_progress:
            on_progress(0, 1, "hometax_business_registration_list")
        try:
            _ensure_claim_operation_active(should_continue)
            discovery = client.discover_hometax_businesses(
                birth_date=birth_date,
                user_name=representative,
                cellphone=cellphone,
                session=session,
            )
            _ensure_claim_operation_active(should_continue)
            candidates = [
                {
                    "business_number": _digits(candidate.business_number),
                    "business_name": _clean(candidate.business_name),
                    "business_status": _clean(candidate.business_status),
                }
                for candidate in discovery.candidates
                if _is_valid_business_no(candidate.business_number)
            ]
            transient["business_candidates"] = candidates
            if not existing_ready:
                repository.store_collected_document(
                    case_id,
                    document_code="hometax_business_registration_list",
                    document=discovery.document,
                )
                ready_count = 1
            # An empty successful response is also cached for the lifetime of
            # this authenticated job. Otherwise every retry would purchase the
            # same MyBizInfo lookup again.
            transient["hometax_business_discovery_attempted"] = True
        except (ClaimProviderError, ClaimRepositoryError) as exc:
            if isinstance(exc, ClaimProviderError) and (
                _is_transient_auth_error(exc)
                or _provider_error_code(exc) == "AUTH_SESSION_EXPIRED"
            ):
                raise
            # A previous successful, downloadable snapshot must not be
            # downgraded just because the short-lived auth session had to
            # rediscover the raw number and that retry failed.
            failed_count = 0 if existing_ready else 1
            ready_count = 1 if existing_ready else 0
            safe_error_code = _safe_provider_error_code(
                exc,
                "HOMETAX_BUSINESS_DISCOVERY",
            )
            errors.append(
                {
                    "document_code": "hometax_business_registration_list",
                    "safe_error_code": safe_error_code,
                    "message": str(exc)[:240],
                }
            )
            if not existing_ready:
                try:
                    repository.fail_document(
                        case_id,
                        document_code="hometax_business_registration_list",
                        safe_error_code=safe_error_code,
                    )
                except ClaimRepositoryError:
                    pass
        if on_progress:
            on_progress(1, 1, "hometax_business_registration_list")

    allowed_numbers = {
        _digits(candidate.get("business_number"))
        for candidate in candidates
        if _is_valid_business_no(candidate.get("business_number"))
    }
    requested_number = _digits(
        transient.get("selected_business_number")
        or transient.get("business_number")
    )
    business_number = (
        requested_number
        if _is_valid_business_no(requested_number)
        and (
            not allowed_numbers
            or requested_number in allowed_numbers
        )
        else next(iter(allowed_numbers))
        if len(allowed_numbers) == 1
        else ""
    )
    if business_number:
        transient["business_number"] = business_number

    return {
        "target": 1,
        "ready": ready_count,
        "failed": failed_count,
        "errors": errors,
        "business_number": business_number,
        "candidates": candidates,
        "selection_required": bool(
            len(allowed_numbers) > 1 and not business_number
        ),
    }


def _collect_supported_hometax_documents(
    repository: ClaimRepository,
    client: TilkoClaimClient,
    *,
    case_id: str,
    birth_date: str,
    representative: str,
    cellphone: str,
    business_number: str,
    session: dict[str, str],
    force_tax_number_discovery: bool = False,
    known_ready_keys: set[tuple[str, int]] | None = None,
    on_progress: Any | None = None,
    should_continue: Any | None = None,
) -> dict[str, Any]:
    _ensure_claim_operation_active(should_continue)
    hometax_documents = [
        document
        for document in repository.list_documents(case_id)
        if str(document.get("source", "")) == "hometax"
    ]
    existing = {
        (
            str(document.get("document_code", "")),
            int(document.get("period_year") or 0),
        ): document
        for document in hometax_documents
    }
    jobs: list[tuple[str, int, Any]] = []
    skipped_codes: list[str] = [
        (
            f"{document.get('document_code')}:{document.get('period_year')}"
            if document.get("period_year")
            else str(document.get("document_code", ""))
        )
        for document in hometax_documents
        if not automatic_collection_supported(
            str(document.get("document_code", ""))
        )
    ]
    business_valid = _is_valid_business_no(business_number)

    if (
        "hometax_business_registration_certificate",
        0,
    ) in existing and business_valid:
        jobs.append(
            (
                "hometax_business_registration_certificate",
                0,
                lambda: client.collect_hometax_business_registration_certificate(
                    birth_date=birth_date,
                    user_name=representative,
                    cellphone=cellphone,
                    business_number=business_number,
                    session=session,
                ),
            )
        )
    elif (
        "hometax_business_registration_certificate",
        0,
    ) in existing:
        skipped_codes.append("hometax_business_registration_certificate")

    if ("hometax_tax_payment_certificate", 0) in existing:
        jobs.append(
            (
                "hometax_tax_payment_certificate",
                0,
                lambda: client.collect_hometax_tax_payment_certificate(
                    birth_date=birth_date,
                    user_name=representative,
                    cellphone=cellphone,
                    session=session,
                ),
            )
        )

    for document in hometax_documents:
        document_code = str(document.get("document_code", ""))
        period_year = int(document.get("period_year") or 0)
        if document_code == "hometax_income_tax_help" and period_year:
            jobs.append(
                (
                    document_code,
                    period_year,
                    lambda year=period_year: client.collect_hometax_income_tax_help(
                        year=year,
                        birth_date=birth_date,
                        user_name=representative,
                        cellphone=cellphone,
                        session=session,
                    ),
                )
            )
        elif document_code == "hometax_income_tax_return" and period_year:
            if business_valid:
                jobs.append(
                    (
                        document_code,
                        period_year,
                        lambda year=period_year: client.collect_hometax_income_tax_return(
                            year=year,
                            birth_date=birth_date,
                            user_name=representative,
                            cellphone=cellphone,
                            business_number=business_number,
                            session=session,
                        ),
                    )
                )
            else:
                skipped_codes.append(f"{document_code}:{period_year}")

    if ("hometax_closure_certificate", 0) in existing and business_valid:
        jobs.append(
            (
                "hometax_closure_certificate",
                0,
                lambda: client.collect_hometax_closure_certificate(
                    birth_date=birth_date,
                    user_name=representative,
                    cellphone=cellphone,
                    business_number=business_number,
                    session=session,
                ),
            )
        )
    elif ("hometax_closure_certificate", 0) in existing:
        skipped_codes.append("hometax_closure_certificate")

    jobs.sort(key=lambda item: (item[0], -item[1]))
    completed_count = 0
    failed_count = 0
    errors: list[dict[str, str]] = []
    transient_business_numbers: list[str] = []
    ready_keys = set(known_ready_keys or set())
    tax_number_discovery_attempted = False
    target_count = len(jobs)
    for document_code, period_year, collector in jobs:
        key = (document_code, period_year)
        current = existing.get(key)
        preexisting_ready = bool(
            key in ready_keys
            or (
                current
                and str(current.get("status", "")) == "ready"
            )
        )
        force_refresh = bool(
            force_tax_number_discovery
            and document_code == "hometax_tax_payment_certificate"
        )
        if preexisting_ready and not force_refresh:
            completed_count += 1
            ready_keys.add(key)
            if on_progress:
                on_progress(completed_count, target_count, document_code)
            continue
        if on_progress:
            on_progress(
                completed_count + failed_count,
                target_count,
                document_code,
            )
        try:
            _ensure_claim_operation_active(should_continue)
            collected = collector()
            _ensure_claim_operation_active(should_continue)
            if document_code == "hometax_tax_payment_certificate":
                business_numbers = collected.transient_facts.get(
                    "business_numbers"
                )
                if isinstance(business_numbers, list):
                    for candidate in business_numbers:
                        digits = _digits(candidate)
                        if (
                            _is_valid_business_no(digits)
                            and digits not in transient_business_numbers
                        ):
                            transient_business_numbers.append(digits)
                tax_number_discovery_attempted = True
            # A forced number-only refresh must never overwrite a previously
            # downloadable certificate. It is used only for transient facts.
            if not (force_refresh and preexisting_ready):
                store_kwargs: dict[str, Any] = {
                    "document_code": document_code,
                    "document": collected,
                }
                if period_year:
                    store_kwargs["period_year"] = period_year
                repository.store_collected_document(case_id, **store_kwargs)
            ready_keys.add(key)
            completed_count += 1
        except (ClaimProviderError, ClaimRepositoryError) as exc:
            if isinstance(exc, ClaimProviderError):
                if (
                    _is_transient_auth_error(exc)
                    or _provider_error_code(exc) == "AUTH_SESSION_EXPIRED"
                ):
                    raise
            safe_error_code = _safe_provider_error_code(exc)
            errors.append(
                {
                    "document_code": document_code,
                    "period_year": str(period_year or ""),
                    "safe_error_code": safe_error_code,
                    "message": str(exc)[:240],
                }
            )
            if force_refresh and preexisting_ready:
                # The refresh failed, but the previously stored document is
                # still valid and must remain downloadable.
                completed_count += 1
                ready_keys.add(key)
            else:
                failed_count += 1
                try:
                    fail_kwargs: dict[str, Any] = {
                        "document_code": document_code,
                        "safe_error_code": safe_error_code,
                    }
                    if period_year:
                        fail_kwargs["period_year"] = period_year
                    repository.fail_document(case_id, **fail_kwargs)
                except ClaimRepositoryError:
                    pass
        if on_progress:
            on_progress(completed_count + failed_count, target_count, document_code)
    return {
        "target": target_count,
        "ready": completed_count,
        "failed": failed_count,
        "skipped": skipped_codes,
        "errors": errors,
        "business_numbers": transient_business_numbers,
        "ready_codes": sorted({code for code, _ in ready_keys}),
        "ready_keys": sorted(ready_keys),
        "tax_number_discovery_attempted": tax_number_discovery_attempted,
    }


def _collect_supported_comwel_documents(
    repository: ClaimRepository,
    client: TilkoClaimClient,
    *,
    case_id: str,
    identity_number: str,
    representative: str,
    cellphone: str,
    business_number: str,
    session: dict[str, str],
    selected_management_number: str = "",
    on_progress: Any | None = None,
    should_continue: Any | None = None,
) -> dict[str, Any]:
    _ensure_claim_operation_active(should_continue)
    documents = [
        document
        for document in repository.list_documents(case_id)
        if str(document.get("source", "")) == "comwel"
    ]
    existing = {
        (
            str(document.get("document_code", "")),
            int(document.get("period_year") or 0),
        ): document
        for document in documents
    }
    remuneration_years = sorted(
        {
            int(document.get("period_year") or 0)
            for document in documents
            if str(document.get("document_code", ""))
            == "comwel_total_remuneration"
            and int(document.get("period_year") or 0) > 0
        },
        reverse=True,
    )
    rate_years = sorted(
        {
            int(document.get("period_year") or 0)
            for document in documents
            if str(document.get("document_code", ""))
            == "comwel_workplace_rate"
            and int(document.get("period_year") or 0) > 0
        },
        reverse=True,
    )
    business_valid = _is_valid_business_no(business_number)
    has_management_document = (
        "comwel_management_number_list",
        0,
    ) in existing
    management_numbers: list[str] = []
    completed_count = 0
    failed_count = 0
    errors: list[dict[str, str]] = []
    skipped_codes: list[str] = []
    target_count = len(remuneration_years)
    if business_valid and has_management_document:
        target_count += 1 + len(rate_years)
    else:
        skipped_codes.extend(
            (
                ["comwel_management_number_list"]
                if has_management_document
                else []
            )
            + [
                f"comwel_workplace_rate:{year}"
                for year in rate_years
            ]
        )

    def report(document_code: str) -> None:
        if on_progress:
            on_progress(
                completed_count + failed_count,
                max(target_count, 1),
                document_code,
            )

    def store_one(
        document_code: str,
        collector: Any,
        *,
        period_year: int | None = None,
    ) -> Any | None:
        nonlocal completed_count, failed_count
        key = (document_code, int(period_year or 0))
        current = existing.get(key)
        if current and str(current.get("status", "")) == "ready":
            completed_count += 1
            report(document_code)
            return current
        report(document_code)
        try:
            _ensure_claim_operation_active(should_continue)
            collected = collector()
            _ensure_claim_operation_active(should_continue)
            stored = repository.store_collected_document(
                case_id,
                document_code=document_code,
                document=collected,
                period_year=period_year,
            )
            completed_count += 1
            report(document_code)
            return {
                **dict(stored or {}),
                "facts": dict(collected.facts or {}),
            }
        except (ClaimProviderError, ClaimRepositoryError) as exc:
            if isinstance(exc, ClaimProviderError):
                if (
                    _is_transient_auth_error(exc)
                    or _provider_error_code(exc) == "AUTH_SESSION_EXPIRED"
                ):
                    raise
            failed_count += 1
            safe_error_code = _safe_provider_error_code(exc, "COMWEL")
            errors.append(
                {
                    "document_code": document_code,
                    "period_year": str(period_year or ""),
                    "safe_error_code": safe_error_code,
                    "message": str(exc)[:240],
                }
            )
            try:
                repository.fail_document(
                    case_id,
                    document_code=document_code,
                    period_year=period_year,
                    safe_error_code=safe_error_code,
                )
            except ClaimRepositoryError:
                pass
            report(document_code)
            return None

    if business_valid and has_management_document:
        management_document = store_one(
            "comwel_management_number_list",
            lambda: client.collect_comwel_management_numbers(
                identity_number=identity_number,
                user_name=representative,
                cellphone=cellphone,
                business_number=business_number,
                session=session,
            ),
        )
        management_facts = (
            management_document.get("facts")
            if isinstance(management_document, dict)
            else {}
        )
        if isinstance(management_facts, dict):
            numbers = management_facts.get("management_numbers")
            if isinstance(numbers, list):
                management_numbers = [
                    _digits(number)
                    for number in numbers
                    if _digits(number)
                ]
    requested_management_number = _digits(selected_management_number)
    management_number = (
        requested_management_number
        if requested_management_number in {
            _digits(number) for number in management_numbers
        }
        else management_numbers[0]
        if len(management_numbers) == 1
        else ""
    )
    selection_required = bool(
        business_valid
        and has_management_document
        and len(management_numbers) > 1
        and not management_number
        and rate_years
    )

    for year in remuneration_years:
        store_one(
            "comwel_total_remuneration",
            lambda year=year: client.collect_comwel_total_remuneration(
                year=year,
                identity_number=identity_number,
                user_name=representative,
                cellphone=cellphone,
                business_number=business_number,
                management_number=management_number,
                session=session,
            ),
            period_year=year,
        )

    if business_valid and has_management_document and management_number:
        for year in rate_years:
            store_one(
                "comwel_workplace_rate",
                lambda year=year: client.collect_comwel_workplace_rate(
                    year=year,
                    identity_number=identity_number,
                    user_name=representative,
                    cellphone=cellphone,
                    management_number=management_number,
                    session=session,
                ),
                period_year=year,
            )
    elif business_valid and has_management_document:
        skipped_codes.extend(
            [
                (
                    f"comwel_workplace_rate:{year}:"
                    "management_number_selection_required"
                    if selection_required
                    else f"comwel_workplace_rate:{year}:"
                    "management_number_missing"
                )
                for year in rate_years
            ]
        )
        target_count -= len(rate_years)

    skipped_codes.append("comwel_worker_status:certificate_required")
    return {
        "target": max(0, target_count),
        "ready": completed_count,
        "failed": failed_count,
        "skipped": skipped_codes,
        "errors": errors,
        "management_numbers": management_numbers,
        "selection_required": selection_required,
    }


def _collect_case_documents(
    repository: ClaimRepository,
    client: TilkoClaimClient,
    *,
    case_id: str,
    birth_date: str,
    identity_number: str,
    representative: str,
    cellphone: str,
    transient: dict[str, Any],
    on_progress: Any | None = None,
    should_continue: Any | None = None,
) -> dict[str, Any]:
    _ensure_claim_operation_active(should_continue)
    planned_documents = repository.list_documents(case_id)
    estimated_target = max(
        1,
        sum(
            1
            for document in planned_documents
            if automatic_collection_supported(
                str(document.get("document_code", ""))
            )
        ),
    )

    def discovery_progress(
        processed: int,
        _total: int,
        document_code: str,
    ) -> None:
        if on_progress:
            on_progress(processed, estimated_target, document_code)

    business_discovery = _discover_hometax_business_number(
        repository,
        client,
        case_id=case_id,
        birth_date=birth_date,
        representative=representative,
        cellphone=cellphone,
        session=transient["hometax"],
        transient=transient,
        on_progress=discovery_progress,
        should_continue=should_continue,
    )
    business_number = _digits(business_discovery.get("business_number"))
    if business_number:
        transient["business_number"] = business_number
    discovery_target = int(business_discovery.get("target", 0) or 0)
    business_valid = _is_valid_business_no(business_number)
    planned_target = estimated_target

    def hometax_progress(
        processed: int,
        _total: int,
        document_code: str,
    ) -> None:
        if on_progress:
            on_progress(
                discovery_target + processed,
                planned_target,
                document_code,
            )

    hometax_summary = _collect_supported_hometax_documents(
        repository,
        client,
        case_id=case_id,
        birth_date=birth_date,
        representative=representative,
        cellphone=cellphone,
        business_number=business_number,
        session=transient["hometax"],
        force_tax_number_discovery=bool(
            not business_valid
            and not business_discovery.get("candidates")
            and not transient.get(
                "hometax_tax_number_discovery_attempted"
            )
        ),
        on_progress=hometax_progress,
        should_continue=should_continue,
    )
    _ensure_claim_operation_active(should_continue)
    if hometax_summary.get("tax_number_discovery_attempted"):
        transient["hometax_tax_number_discovery_attempted"] = True
        transient["hometax_tax_business_numbers"] = list(
            hometax_summary.get("business_numbers", [])
        )

    # MyBizInfo가 개인 납세자 정보만 돌려주는 경우에도, 이미 수집하는
    # 국세납세증명서의 공식 JsonData에서 사업자번호를 메모리 내에서만
    # 보조 확인합니다. 원문 번호는 문서 facts·감사로그에 저장하지 않습니다.
    if (
        not business_valid
        and not business_discovery.get("candidates")
    ):
        fallback_numbers = [
            _digits(candidate)
            for candidate in (
                list(
                    transient.get("hometax_tax_business_numbers", [])
                )
                + list(hometax_summary.get("business_numbers", []))
            )
            if _is_valid_business_no(candidate)
        ]
        fallback_numbers = list(dict.fromkeys(fallback_numbers))
        fallback_candidates = [
            {
                "business_number": candidate,
                "business_name": "",
                "business_status": "",
            }
            for candidate in fallback_numbers
        ]
        if fallback_candidates:
            business_discovery["candidates"] = fallback_candidates
            transient["business_candidates"] = fallback_candidates
            requested_number = _digits(
                transient.get("selected_business_number")
                or transient.get("business_number")
            )
            if (
                _is_valid_business_no(requested_number)
                and requested_number in fallback_numbers
            ):
                business_number = requested_number
            elif len(fallback_numbers) == 1:
                business_number = fallback_numbers[0]
            business_discovery["business_number"] = business_number
            business_discovery["selection_required"] = bool(
                len(fallback_numbers) > 1 and not business_number
            )
            business_valid = _is_valid_business_no(business_number)
            if business_valid:
                transient["business_number"] = business_number
                # 첫 호출에서 저장한 국세납세증명서는 재사용하고,
                # 사업자번호가 필요한 사업자등록증명원만 이어서 수집합니다.
                hometax_summary = _collect_supported_hometax_documents(
                    repository,
                    client,
                    case_id=case_id,
                    birth_date=birth_date,
                    representative=representative,
                    cellphone=cellphone,
                    business_number=business_number,
                    session=transient["hometax"],
                    known_ready_keys={
                        (str(code), int(year or 0))
                        for code, year in hometax_summary.get(
                            "ready_keys",
                            [],
                        )
                        if str(code)
                    },
                    on_progress=hometax_progress,
                    should_continue=should_continue,
                )
                _ensure_claim_operation_active(should_continue)

    business_selection_required = bool(
        business_discovery.get("selection_required")
    )
    business_dependent_documents = [
        document
        for document in planned_documents
        if str(document.get("document_code", ""))
        in {
            "hometax_business_registration_certificate",
            "hometax_income_tax_return",
            "hometax_closure_certificate",
            "comwel_management_number_list",
            "comwel_workplace_rate",
        }
        and str(document.get("status", "")) != "ready"
    ]
    comwel_business_dependent_documents = [
        document
        for document in business_dependent_documents
        if str(document.get("source", "")) == "comwel"
    ]
    business_number_missing = bool(
        not business_valid
        and business_dependent_documents
        and not business_selection_required
    )
    business_blocked_count = (
        len(comwel_business_dependent_documents)
        if business_number_missing
        else 0
    )
    blocked_status_errors: list[dict[str, str]] = []
    if business_number_missing:
        for document in business_dependent_documents:
            status = str(document.get("status", ""))
            facts = document.get("facts")
            safe_error_code = (
                str(facts.get("safe_error_code", ""))
                if isinstance(facts, dict)
                else ""
            )
            if (
                status == "failed"
                and safe_error_code == "BUSINESS_NUMBER_NOT_FOUND"
            ):
                continue
            # Preserve an earlier provider failure rather than rewriting its
            # diagnostic. Only pending/planned rows are converted to an
            # explicit compatible failed status.
            if status not in {"auth_pending", "integration_required"}:
                continue
            try:
                failure_kwargs: dict[str, Any] = {
                    "document_code": str(
                        document.get("document_code", "")
                    ),
                    "safe_error_code": "BUSINESS_NUMBER_NOT_FOUND",
                }
                if document.get("period_year"):
                    failure_kwargs["period_year"] = int(
                        document.get("period_year")
                    )
                repository.fail_document(
                    case_id,
                    **failure_kwargs,
                )
            except ClaimRepositoryError:
                blocked_status_errors.append(
                    {
                        "document_code": str(
                            document.get("document_code", "")
                        ),
                        "safe_error_code": (
                            "BUSINESS_BLOCK_STATUS_SAVE_FAILED"
                        ),
                        "message": (
                            "사업자번호 필요 상태를 저장하지 못했습니다."
                        ),
                    }
                )

    hometax_target = int(hometax_summary.get("target", 0) or 0)

    def comwel_progress(
        processed: int,
        _total: int,
        document_code: str,
    ) -> None:
        if on_progress:
            on_progress(
                discovery_target + hometax_target + processed,
                planned_target,
                document_code,
            )

    if not business_selection_required:
        comwel_summary = _collect_supported_comwel_documents(
            repository,
            client,
            case_id=case_id,
            identity_number=identity_number,
            representative=representative,
            cellphone=cellphone,
            business_number=business_number,
            session=transient["comwel"],
            selected_management_number=str(
                transient.get("selected_management_number", "")
            ),
            on_progress=comwel_progress,
            should_continue=should_continue,
        )
    else:
        comwel_summary = {
            "target": 0,
            "ready": 0,
            "failed": 0,
            "skipped": [
                "comwel_documents:business_number_selection_required"
            ],
            "errors": [],
            "management_numbers": [],
            "selection_required": False,
        }
    _ensure_claim_operation_active(should_continue)
    summary = {
        "target": discovery_target
        + int(hometax_summary["target"])
        + int(comwel_summary["target"]),
        "ready": int(business_discovery.get("ready", 0) or 0)
        + int(hometax_summary["ready"])
        + int(comwel_summary["ready"]),
        "failed": int(business_discovery.get("failed", 0) or 0)
        + int(hometax_summary["failed"])
        + int(comwel_summary["failed"]),
        "skipped": list(hometax_summary["skipped"])
        + list(comwel_summary["skipped"]),
        "errors": list(business_discovery.get("errors", []))
        + list(hometax_summary["errors"])
        + list(comwel_summary["errors"])
        + blocked_status_errors,
        "sources": {
            "hometax_business_discovery": business_discovery,
            "hometax": hometax_summary,
            "comwel": comwel_summary,
        },
        "management_numbers": list(
            comwel_summary.get("management_numbers", [])
        ),
        "selection_required": bool(
            comwel_summary.get("selection_required")
        ),
        "business_candidates": list(
            business_discovery.get("candidates", [])
        ),
        "business_selection_required": business_selection_required,
        "business_number_missing": business_number_missing,
        "business_blocked_count": business_blocked_count,
    }
    if business_number_missing:
        summary["errors"].append(
            {
                "document_code": "hometax_business_registration_list",
                "safe_error_code": "BUSINESS_NUMBER_NOT_FOUND",
                "message": "홈택스에서 유효한 사업자등록번호를 확인하지 못했습니다.",
            }
        )
    collection_complete = bool(
        summary["target"] > 0
        and summary["ready"] == summary["target"]
        and summary["failed"] == 0
        and not summary["selection_required"]
        and not business_selection_required
        and not business_number_missing
    )
    summary["complete"] = collection_complete
    _ensure_claim_operation_active(should_continue)
    if summary["business_selection_required"]:
        repository.update_case_status(
            case_id,
            overall_status="auth_complete_collection_pending",
            last_safe_error_code=None,
        )
        repository.append_audit_event(
            case_id=case_id,
            action="business_number_selection_required",
            source="hometax",
            outcome="pending",
            metadata={
                "business_candidate_count": len(
                    summary["business_candidates"]
                ),
                "ready_document_count": summary["ready"],
            },
        )
        return summary
    if summary["selection_required"]:
        repository.update_case_status(
            case_id,
            overall_status="auth_complete_collection_pending",
            last_safe_error_code=None,
        )
        repository.append_audit_event(
            case_id=case_id,
            action="management_number_selection_required",
            source="comwel",
            outcome="pending",
            metadata={
                "management_number_count": len(
                    summary["management_numbers"]
                ),
                "ready_document_count": summary["ready"],
            },
        )
        return summary
    if collection_complete:
        repository.update_case_status(
            case_id,
            overall_status="ready",
            last_safe_error_code=None,
        )
        repository.append_audit_event(
            case_id=case_id,
            action="collection_complete",
            source="provider",
            outcome="success",
            metadata={
                "supported_document_count": summary["target"],
                "ready_document_count": summary["ready"],
                "skipped_document_codes": summary["skipped"],
                "scope": "currently_connected_documents",
            },
        )
        return summary

    first_error = (
        summary["errors"][0]["safe_error_code"]
        if summary["errors"]
        else "DOCUMENT_COLLECTION_FAILED"
    )
    repository.update_case_status(
        case_id,
        overall_status="auth_complete_collection_pending",
        last_safe_error_code=first_error,
    )
    repository.append_audit_event(
        case_id=case_id,
        action="collection_partial",
        source="provider",
        outcome="failed",
        metadata={
            "supported_document_count": summary["target"],
            "ready_document_count": summary["ready"],
            "failed_document_count": summary["failed"],
            "safe_error_codes": [
                item["safe_error_code"] for item in summary["errors"]
            ],
        },
    )
    return summary


def _advance_personal_case(
    repository: ClaimRepository,
    client: TilkoClaimClient,
    *,
    case: dict[str, Any],
    transient: dict[str, Any],
    representative: str,
    cellphone: str,
    birth_date: str,
    identity_number: str,
    on_progress: Any | None = None,
    should_continue: Any | None = None,
) -> dict[str, Any]:
    _ensure_claim_operation_active(should_continue)
    case_id = str(case.get("id", ""))
    action, _ = _next_auth_action(case, transient)
    if not action:
        return {"event": "idle", "action": ""}

    if action == "check_hometax":
        hometax_complete = client.check_hometax_kakao(
            birth_date=birth_date,
            user_name=representative,
            cellphone=cellphone,
            session=transient["hometax"],
        )
        _ensure_claim_operation_active(should_continue)
        if not hometax_complete:
            repository.update_case_status(
                case_id,
                hometax_status="auth_pending",
                overall_status="auth_pending",
            )
            repository.append_audit_event(
                case_id=case_id,
                action="auth_check",
                source="hometax",
                outcome="pending",
                metadata={"next_source_requested": False},
            )
            return {"event": "hometax_pending", "action": action}

        repository.update_case_status(
            case_id,
            hometax_status="auth_complete",
            overall_status="auth_pending",
            last_safe_error_code=None,
        )
        try:
            if not transient.get("comwel"):
                _ensure_claim_operation_active(should_continue)
                if COMWEL_DISPATCH_DELAY_SECONDS > 0:
                    time.sleep(COMWEL_DISPATCH_DELAY_SECONDS)
                _ensure_claim_operation_active(should_continue)
                comwel_session = client.request_comwel_kakao(
                    identity_number=identity_number,
                    user_name=representative,
                    cellphone=cellphone,
                )
                _ensure_claim_operation_active(should_continue)
                transient["comwel"] = comwel_session
        except ClaimProviderError as exc:
            if (
                _is_transient_auth_error(exc)
                or _provider_error_code(exc) == "AUTH_SESSION_EXPIRED"
            ):
                raise
            repository.update_case_status(
                case_id,
                hometax_status="auth_complete",
                comwel_status="failed",
                overall_status="auth_partial",
                last_safe_error_code="COMWEL_AUTH_REQUEST_FAILED",
            )
            repository.append_audit_event(
                case_id=case_id,
                action="auth_request",
                source="comwel",
                outcome="failed",
                metadata={"safe_error_code": "COMWEL_AUTH_REQUEST_FAILED"},
            )
            raise
        _set_claim_expiry(transient, AUTH_TTL_SECONDS)
        transient["stage_started_at"] = time.time()
        repository.update_case_status(
            case_id,
            hometax_status="auth_complete",
            comwel_status="auth_requested",
            overall_status="auth_pending",
            auth_requested_at=datetime.now(timezone.utc).isoformat(),
            last_safe_error_code=None,
        )
        repository.append_audit_event(
            case_id=case_id,
            action="auth_check",
            source="hometax",
            outcome="success",
            metadata={"next_source_requested": True},
        )
        return {"event": "comwel_requested", "action": action}

    if action == "request_comwel":
        _ensure_claim_operation_active(should_continue)
        comwel_session = client.request_comwel_kakao(
            identity_number=identity_number,
            user_name=representative,
            cellphone=cellphone,
        )
        _ensure_claim_operation_active(should_continue)
        transient["comwel"] = comwel_session
        _set_claim_expiry(transient, AUTH_TTL_SECONDS)
        transient["stage_started_at"] = time.time()
        repository.update_case_status(
            case_id,
            comwel_status="auth_requested",
            overall_status="auth_pending",
            auth_requested_at=datetime.now(timezone.utc).isoformat(),
            last_safe_error_code=None,
        )
        repository.append_audit_event(
            case_id=case_id,
            action="auth_request",
            source="comwel",
            outcome="success",
            metadata={"sequential_after_hometax": True},
        )
        return {"event": "comwel_requested", "action": action}

    if action == "check_comwel":
        comwel_complete = client.check_comwel_kakao(
            identity_number=identity_number,
            user_name=representative,
            cellphone=cellphone,
            session=transient["comwel"],
        )
        _ensure_claim_operation_active(should_continue)
        if not comwel_complete:
            repository.update_case_status(
                case_id,
                hometax_status="auth_complete",
                comwel_status="auth_pending",
                overall_status="auth_pending",
            )
            repository.append_audit_event(
                case_id=case_id,
                action="auth_check",
                source="comwel",
                outcome="pending",
                metadata={"collection_started": False},
            )
            return {"event": "comwel_pending", "action": action}
        repository.update_case_status(
            case_id,
            hometax_status="auth_complete",
            comwel_status="auth_complete",
            overall_status="collecting",
            auth_completed_at=datetime.now(timezone.utc).isoformat(),
            last_safe_error_code=None,
        )
        repository.append_audit_event(
            case_id=case_id,
            action="auth_check",
            source="comwel",
            outcome="success",
            metadata={"collection_started": True},
        )
        _set_claim_expiry(transient, COLLECTION_TTL_SECONDS)
    elif action == "collect":
        _ensure_claim_operation_active(should_continue)
        repository.update_case_status(
            case_id,
            overall_status="collecting",
            last_safe_error_code=None,
        )
        _set_claim_expiry(transient, COLLECTION_TTL_SECONDS)

    if on_progress:
        on_progress(0, 1, "collection_preparing")
    _ensure_claim_operation_active(should_continue)
    summary = _collect_case_documents(
        repository,
        client,
        case_id=case_id,
        birth_date=birth_date,
        identity_number=identity_number,
        representative=representative,
        cellphone=cellphone,
        transient=transient,
        on_progress=on_progress,
        should_continue=should_continue,
    )
    event = (
        "business_selection_required"
        if summary.get("business_selection_required")
        else "management_selection_required"
        if summary.get("selection_required")
        else "collection_complete"
        if summary["complete"]
        else "collection_partial"
    )
    return {
        "event": event,
        "action": action,
        "summary": summary,
    }


def _claim_job_owner_ref(user_id: str) -> str:
    return hashlib.sha256(
        str(user_id or "").strip().lower().encode("utf-8")
    ).hexdigest()


def _seal_claim_job_payload(payload: dict[str, Any]) -> bytes:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _CLAIM_JOB_CIPHER.encrypt(serialized)


def _unseal_claim_job_payload(value: bytes) -> dict[str, Any]:
    try:
        decoded = _CLAIM_JOB_CIPHER.decrypt(value)
        payload = json.loads(decoded.decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ClaimProviderError(
            "임시 인증정보를 복구하지 못했습니다. 새 인증 요청을 시작해 주세요."
        ) from exc
    if not isinstance(payload, dict):
        raise ClaimProviderError(
            "임시 인증정보 형식을 확인하지 못했습니다."
        )
    return payload


def _sync_interrupted_claim_case(
    user_id: str,
    case_id: str,
    *,
    active_action: str,
    safe_error_code: str,
    outcome: str,
) -> None:
    owner_user_id = str(user_id or "").strip().lower()
    if not owner_user_id:
        return
    try:
        repository = ClaimRepository(owner_user_id)
        case = repository.get_case(case_id)
        if not case:
            return
        hometax_status = str(case.get("hometax_status", "") or "")
        comwel_status = str(case.get("comwel_status", "") or "")
        updates: dict[str, Any] = {
            "last_safe_error_code": safe_error_code,
        }
        if safe_error_code == "AUTH_SESSION_EXPIRED":
            if hometax_status != "auth_complete":
                updates["hometax_status"] = "failed"
            elif comwel_status != "auth_complete":
                updates["comwel_status"] = "failed"
        elif (
            active_action == "check_hometax"
            and hometax_status != "auth_complete"
        ):
            updates["hometax_status"] = "failed"
        elif (
            active_action in {"request_comwel", "check_comwel"}
            and comwel_status != "auth_complete"
        ):
            updates["comwel_status"] = "failed"

        authentication_complete = (
            hometax_status == "auth_complete"
            and comwel_status == "auth_complete"
        )
        collection_started = (
            active_action == "collect"
            or str(case.get("overall_status", "") or "")
            in {
                "collecting",
                "collection_queued",
                "auth_complete_collection_pending",
            }
        )
        updates["overall_status"] = (
            "auth_complete_collection_pending"
            if authentication_complete or collection_started
            else "auth_partial"
        )
        repository.update_case_status(case_id, **updates)
        repository.append_audit_event(
            case_id=case_id,
            action="background_auth_or_collection",
            source="provider",
            outcome=outcome,
            metadata={"safe_error_code": safe_error_code},
        )
    except ClaimRepositoryError:
        return


def _expire_claim_job(
    case_id: str,
    owner_ref: str,
    user_id: str = "",
) -> None:
    owner_user_id = str(user_id or "").strip().lower()
    previous_status = ""
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(case_id)
        if not job or job.get("owner_ref") != owner_ref:
            return
        previous_status = str(job.get("status", "") or "")
        owner_user_id = (
            owner_user_id
            or str(job.get("owner_user_id", "") or "").strip().lower()
        )
        job["sealed_payload"] = b""
        if previous_status != "complete":
            job["status"] = "expired"
            job["safe_message"] = (
                (
                    "일부 서류는 저장했지만 재시도 유효시간이 지나 "
                    "임시 인증정보를 삭제했습니다. 새 인증 요청을 시작해 주세요."
                )
                if previous_status == "collection_partial"
                else (
                    "인증 유효시간이 지나 임시 인증정보를 삭제했습니다. "
                    "새 인증 요청을 시작해 주세요."
                )
            )
        wake_event = job.get("wake_event")
        if isinstance(wake_event, threading.Event):
            wake_event.set()
    if previous_status != "complete":
        _sync_interrupted_claim_case(
            owner_user_id,
            case_id,
            active_action=(
                "collect"
                if previous_status
                in {
                    "collection_partial",
                    "awaiting_business_selection",
                    "awaiting_management_selection",
                }
                else ""
            ),
            safe_error_code="AUTH_SESSION_EXPIRED",
            outcome="expired",
        )


def _update_claim_job(
    case_id: str,
    owner_ref: str,
    **updates: Any,
) -> bool:
    expired_owner_user_id = ""
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(case_id)
        if not job or job.get("owner_ref") != owner_ref:
            return False
        current_status = str(job.get("status", "") or "")
        current_expires_at = float(job.get("expires_at", 0) or 0)
        if current_status in {"complete", "expired"}:
            return False
        if (
            current_expires_at <= time.time()
            or not job.get("sealed_payload")
        ):
            job["sealed_payload"] = b""
            job["status"] = "expired"
            job["safe_message"] = (
                "인증 유효시간이 지나 임시 인증정보를 삭제했습니다. "
                "새 인증 요청을 시작해 주세요."
            )
            job["updated_at"] = time.time()
            expired_owner_user_id = str(
                job.get("owner_user_id", "") or ""
            ).strip().lower()
            wake_event = job.get("wake_event")
            if isinstance(wake_event, threading.Event):
                wake_event.set()
        else:
            job.update(updates)
            job["updated_at"] = time.time()
            return True
    _sync_interrupted_claim_case(
        expired_owner_user_id,
        case_id,
        active_action="",
        safe_error_code="AUTH_SESSION_EXPIRED",
        outcome="expired",
    )
    return False


def _claim_job_can_continue(case_id: str, owner_ref: str) -> bool:
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(case_id)
        return bool(
            job
            and job.get("owner_ref") == owner_ref
            and str(job.get("status", "") or "")
            not in {"complete", "expired"}
            and float(job.get("expires_at", 0) or 0) > time.time()
            and job.get("sealed_payload")
        )


def _run_background_claim_job(
    user_id: str,
    case_id: str,
    owner_ref: str,
    *,
    initial_delay: float = 0,
) -> None:
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(case_id)
        wake_event = job.get("wake_event") if job else None
    if not isinstance(wake_event, threading.Event):
        return
    if initial_delay > 0 and wake_event.wait(initial_delay):
        wake_event.clear()

    transient: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    active_action = ""
    try:
        repository = ClaimRepository(user_id)
        client = TilkoClaimClient()
        while True:
            with _CLAIM_JOB_LOCK:
                job = _CLAIM_JOBS.get(case_id)
                if not job or job.get("owner_ref") != owner_ref:
                    return
                expires_at = float(job.get("expires_at", 0) or 0)
                sealed_payload = job.get("sealed_payload")
            if time.time() >= expires_at or not isinstance(
                sealed_payload,
                bytes,
            ) or not sealed_payload:
                _expire_claim_job(case_id, owner_ref, user_id)
                return

            transient = _unseal_claim_job_payload(sealed_payload)
            context = transient.get("auth_context")
            if not isinstance(context, dict):
                raise ClaimProviderError(
                    "순차 인증에 필요한 고객정보를 확인하지 못했습니다."
                )
            case = repository.get_case(case_id)
            if not case:
                raise ClaimRepositoryError(
                    "경정청구 요청 건을 찾지 못했습니다."
                )

            def update_collection_progress(
                _processed: int,
                _total: int,
                document_code: str,
            ) -> None:
                with _CLAIM_JOB_LOCK:
                    current_job = dict(_CLAIM_JOBS.get(case_id) or {})
                current_summary = dict(current_job.get("summary") or {})
                (
                    percentage,
                    _progress_text,
                    ready_count,
                    target_count,
                    progress_verified,
                ) = _claim_collection_progress_from_repository(
                    repository,
                    case_id,
                )
                if (
                    not progress_verified
                    and current_summary.get("progress_verified") is True
                ):
                    percentage = int(current_job.get("progress", 0) or 0)
                    ready_count = int(
                        current_summary.get("ready", 0) or 0
                    )
                    target_count = int(
                        current_summary.get("target", 0) or 0
                    )
                _set_claim_expiry(
                    transient,
                    COLLECTION_TTL_SECONDS,
                )
                if transient["expires_at"] <= time.time():
                    raise ClaimProviderError(
                        "인증 유효시간이 지나 임시 인증정보를 삭제했습니다.",
                        error_code="AUTH_SESSION_EXPIRED",
                    )
                updated = _update_claim_job(
                    case_id,
                    owner_ref,
                    sealed_payload=_seal_claim_job_payload(transient),
                    expires_at=transient["expires_at"],
                    progress=percentage,
                    status="running",
                    safe_message=(
                        f"{_document_label(document_code)} 수집 중 · "
                        f"{ready_count}/{target_count}건 수집 완료"
                        if target_count
                        else f"{_document_label(document_code)} 수집 중"
                    ),
                    summary={
                        "ready": ready_count,
                        "target": target_count,
                        "progress_verified": progress_verified,
                    },
                )
                if not updated:
                    transient.clear()
                    context.clear()
                    raise ClaimProviderError(
                        "인증 유효시간이 지나 임시 인증정보를 삭제했습니다.",
                        error_code="AUTH_SESSION_EXPIRED",
                    )

            try:
                active_action = _next_auth_action(
                    case,
                    transient,
                )[0]
                result = _advance_personal_case(
                    repository,
                    client,
                    case=case,
                    transient=transient,
                    representative=str(
                        context.get("representative", "")
                    ).strip(),
                    cellphone=_digits(context.get("cellphone")),
                    birth_date=_digits(context.get("birth_date")),
                    identity_number=_digits(context.get("identity_number")),
                    on_progress=update_collection_progress,
                    should_continue=lambda: _claim_job_can_continue(
                        case_id,
                        owner_ref,
                    ),
                )
            except ClaimProviderError as exc:
                if not _is_transient_auth_error(exc):
                    raise
                if not _claim_job_can_continue(case_id, owner_ref):
                    _sync_interrupted_claim_case(
                        user_id,
                        case_id,
                        active_action=active_action,
                        safe_error_code="AUTH_SESSION_EXPIRED",
                        outcome="expired",
                    )
                    transient.clear()
                    context.clear()
                    return
                retry_count = int(
                    transient.get("provider_session_retry_count", 0) or 0
                ) + 1
                transient["provider_session_retry_count"] = retry_count
                updated = _update_claim_job(
                    case_id,
                    owner_ref,
                    sealed_payload=_seal_claim_job_payload(transient),
                    expires_at=float(
                        transient.get("expires_at", time.time()) or time.time()
                    ),
                    status="running",
                    progress=int(job.get("progress", 0) or 0),
                    safe_message=(
                        "인증정보가 중계 서버에 반영되는 중입니다. "
                        "자동으로 다시 확인합니다."
                    ),
                )
                if not updated:
                    transient.clear()
                    context.clear()
                    return
                if retry_count == 1:
                    repository.append_audit_event(
                        case_id=case_id,
                        action="auth_check",
                        source="provider",
                        outcome="pending",
                        metadata={
                            "safe_error_code": _provider_error_code(exc),
                            "automatic_retry": True,
                        },
                    )
                wake_event.wait(
                    max(
                        TRANSIENT_AUTH_RETRY_SECONDS,
                        _auth_poll_delay(transient),
                    )
                )
                wake_event.clear()
                continue
            if not _claim_job_can_continue(case_id, owner_ref):
                _sync_interrupted_claim_case(
                    user_id,
                    case_id,
                    active_action=active_action,
                    safe_error_code="AUTH_SESSION_EXPIRED",
                    outcome="expired",
                )
                transient.clear()
                context.clear()
                return
            event = str(result.get("event", "") or "")
            updated_case = repository.get_case(case_id) or case
            (
                percentage,
                _progress_text,
                ready_count,
                target_count,
                progress_verified,
            ) = _claim_collection_progress_from_repository(
                repository,
                case_id,
            )
            _, auth_stage_message = _claim_auth_stage(updated_case)

            if event == "business_selection_required":
                summary = dict(result.get("summary") or {})
                business_choices = []
                for candidate in summary.get("business_candidates", []):
                    if not isinstance(candidate, dict):
                        continue
                    business_number = _digits(
                        candidate.get("business_number")
                    )
                    if not _is_valid_business_no(business_number):
                        continue
                    business_name = _clean(
                        candidate.get("business_name")
                    )
                    business_status = _clean(
                        candidate.get("business_status")
                    )
                    label_parts = [
                        part
                        for part in (
                            business_name,
                            _masked_business_choice_no(business_number),
                            business_status,
                        )
                        if part
                    ]
                    business_choices.append(
                        {
                            "token": _business_candidate_token(
                                case_id,
                                business_number,
                            ),
                            "label": " · ".join(label_parts),
                        }
                    )
                updated = _update_claim_job(
                    case_id,
                    owner_ref,
                    sealed_payload=_seal_claim_job_payload(transient),
                    expires_at=float(
                        transient.get("expires_at", time.time())
                        or time.time()
                    ),
                    status="awaiting_business_selection",
                    progress=percentage,
                    safe_message=(
                        "홈택스에서 여러 사업자가 확인됐습니다. "
                        "자료를 수집할 사업자를 선택해 주세요."
                    ),
                    summary={
                        "ready": ready_count,
                        "target": target_count,
                        "progress_verified": progress_verified,
                        "failed": int(summary.get("failed", 0) or 0),
                        "business_choices": business_choices,
                    },
                )
                if not updated:
                    transient.clear()
                    context.clear()
                return
            if event == "management_selection_required":
                summary = dict(result.get("summary") or {})
                management_numbers = [
                    str(number)
                    for number in summary.get("management_numbers", [])
                    if str(number).strip()
                ]
                updated = _update_claim_job(
                    case_id,
                    owner_ref,
                    sealed_payload=_seal_claim_job_payload(transient),
                    expires_at=float(
                        transient.get("expires_at", time.time()) or time.time()
                    ),
                    status="awaiting_management_selection",
                    progress=percentage,
                    safe_message=(
                        "사업장관리번호를 선택하면 사업장요율 수집을 "
                        "계속합니다."
                    ),
                    summary={
                        "ready": ready_count,
                        "target": target_count,
                        "progress_verified": progress_verified,
                        "failed": int(summary.get("failed", 0) or 0),
                        "management_numbers": management_numbers,
                    },
                )
                if not updated:
                    transient.clear()
                    context.clear()
                return
            if event == "collection_complete":
                summary = dict(result.get("summary") or {})
                (
                    percentage,
                    _progress_text,
                    ready_count,
                    target_count,
                    progress_verified,
                ) = _claim_collection_progress_from_repository(
                    repository,
                    case_id,
                )
                if (
                    not progress_verified
                    or target_count <= 0
                    or ready_count != target_count
                ):
                    verification_error_code = (
                        "COLLECTION_PROGRESS_UNVERIFIED"
                        if not progress_verified
                        else "COLLECTION_PROGRESS_INCOMPLETE"
                    )
                    try:
                        repository.update_case_status(
                            case_id,
                            overall_status=(
                                "auth_complete_collection_pending"
                            ),
                            last_safe_error_code=verification_error_code,
                        )
                        repository.append_audit_event(
                            case_id=case_id,
                            action="collection_progress_verification",
                            source="supabase",
                            outcome="pending",
                            metadata={
                                "safe_error_code": (
                                    verification_error_code
                                ),
                                "ready_document_count": ready_count,
                                "target_document_count": target_count,
                            },
                        )
                    except ClaimRepositoryError:
                        pass
                    safe_message = (
                        "Supabase의 실제 수집 상태를 확인하지 못했습니다. "
                        "완료로 처리하지 않고 다시 확인합니다."
                        if not progress_verified
                        else (
                            f"자동수집 대상 {target_count}건 중 "
                            f"{ready_count}건만 확인되어 완료로 처리하지 "
                            "않았습니다."
                        )
                    )
                    _update_claim_job(
                        case_id,
                        owner_ref,
                        sealed_payload=_seal_claim_job_payload(transient),
                        expires_at=float(
                            transient.get("expires_at", time.time())
                            or time.time()
                        ),
                        status="paused",
                        progress=min(99, percentage),
                        safe_message=safe_message,
                        summary={
                            "ready": ready_count,
                            "target": target_count,
                            "progress_verified": progress_verified,
                            "failed": int(
                                summary.get("failed", 0) or 0
                            ),
                        },
                    )
                    return
                _update_claim_job(
                    case_id,
                    owner_ref,
                    sealed_payload=b"",
                    status="complete",
                    progress=percentage,
                    safe_message=(
                        f"현재 연결된 서류 "
                        f"{ready_count}건 수집 완료"
                        + (
                            f", 추가 정보·미지원 "
                            f"{len(summary.get('skipped', []))}건"
                            if summary.get("skipped")
                            else ""
                        )
                    ),
                    summary={
                        "ready": ready_count,
                        "target": target_count,
                        "progress_verified": True,
                        "failed": int(summary.get("failed", 0) or 0),
                        "skipped_count": len(summary.get("skipped", [])),
                    },
                )
                transient.clear()
                context.clear()
                return
            if event == "collection_partial":
                summary = dict(result.get("summary") or {})
                business_number_missing = bool(
                    summary.get("business_number_missing")
                )
                blocked_count = int(
                    summary.get("business_blocked_count", 0) or 0
                )
                (
                    percentage,
                    _progress_text,
                    ready_count,
                    target_count,
                    progress_verified,
                ) = _claim_collection_progress_from_repository(
                    repository,
                    case_id,
                )
                percentage = min(99, percentage)
                safe_message = (
                    "홈택스에서 사업자등록번호를 자동 확인하지 못했습니다. "
                    "사업자번호 없이 조회 가능한 보수총액은 수집을 시도했고, "
                    f"사업자번호가 필요한 관리번호·요율 {blocked_count}건은 "
                    "보류했습니다."
                    if business_number_missing
                    else (
                        f"서류 {int(summary.get('ready', 0) or 0)}건 저장, "
                        f"{int(summary.get('failed', 0) or 0)}건 실패"
                    )
                )
                updated = _update_claim_job(
                    case_id,
                    owner_ref,
                    sealed_payload=_seal_claim_job_payload(transient),
                    expires_at=float(
                        transient.get("expires_at", time.time()) or time.time()
                    ),
                    status="collection_partial",
                    progress=percentage,
                    safe_message=safe_message,
                    summary={
                        "ready": ready_count,
                        "target": target_count,
                        "progress_verified": progress_verified,
                        "failed": int(summary.get("failed", 0) or 0),
                        "skipped_count": len(summary.get("skipped", [])),
                        "blocked_count": blocked_count,
                        "business_number_missing": business_number_missing,
                    },
                )
                if not updated:
                    transient.clear()
                    context.clear()
                return

            updated = _update_claim_job(
                case_id,
                owner_ref,
                sealed_payload=_seal_claim_job_payload(transient),
                expires_at=float(
                    transient.get("expires_at", time.time()) or time.time()
                ),
                status="running",
                progress=percentage,
                safe_message=auth_stage_message,
                summary={
                    "ready": ready_count,
                    "target": target_count,
                    "progress_verified": progress_verified,
                },
            )
            if not updated:
                transient.clear()
                context.clear()
                return
            wake_event.wait(_auth_poll_delay(transient))
            wake_event.clear()
    except (ClaimProviderError, ClaimRepositoryError) as exc:
        if isinstance(transient, dict) and not _claim_job_can_continue(
            case_id,
            owner_ref,
        ):
            _sync_interrupted_claim_case(
                user_id,
                case_id,
                active_action=active_action,
                safe_error_code="AUTH_SESSION_EXPIRED",
                outcome="expired",
            )
            transient.clear()
            if isinstance(context, dict):
                context.clear()
            return
        safe_error_code = (
            _safe_provider_error_code(exc, "BACKGROUND")
            if isinstance(exc, ClaimProviderError)
            else "BACKGROUND_REPOSITORY_FAILED"
        )
        resealed = (
            _seal_claim_job_payload(transient)
            if isinstance(transient, dict)
            else None
        )
        _update_claim_job(
            case_id,
            owner_ref,
            **(
                {
                    "sealed_payload": resealed,
                    "expires_at": float(
                        transient.get("expires_at", time.time()) or time.time()
                    ),
                }
                if resealed is not None
                else {}
            ),
            status="paused",
            safe_message=(
                "인증 또는 자료수집 연결이 중단되었습니다. "
                "잠시 후 다시 시도해 주세요."
            ),
        )
        _sync_interrupted_claim_case(
            user_id,
            case_id,
            active_action=active_action,
            safe_error_code=safe_error_code,
            outcome="failed",
        )
    except Exception:
        if isinstance(transient, dict) and not _claim_job_can_continue(
            case_id,
            owner_ref,
        ):
            _sync_interrupted_claim_case(
                user_id,
                case_id,
                active_action=active_action,
                safe_error_code="AUTH_SESSION_EXPIRED",
                outcome="expired",
            )
            transient.clear()
            if isinstance(context, dict):
                context.clear()
            return
        resealed = (
            _seal_claim_job_payload(transient)
            if isinstance(transient, dict)
            else None
        )
        _update_claim_job(
            case_id,
            owner_ref,
            **(
                {
                    "sealed_payload": resealed,
                    "expires_at": float(
                        transient.get("expires_at", time.time()) or time.time()
                    ),
                }
                if resealed is not None
                else {}
            ),
            status="paused",
            safe_message=(
                "자동 인증 확인 중 오류가 발생했습니다. "
                "잠시 후 다시 시도해 주세요."
            ),
        )
        _sync_interrupted_claim_case(
            user_id,
            case_id,
            active_action=active_action,
            safe_error_code="BACKGROUND_UNEXPECTED_ERROR",
            outcome="failed",
        )


def _claim_job_sweeper() -> None:
    while True:
        time.sleep(15)
        now = time.time()
        with _CLAIM_JOB_LOCK:
            jobs = [
                (
                    case_id,
                    str(job.get("owner_ref", "")),
                    str(job.get("owner_user_id", "") or ""),
                    float(job.get("expires_at", 0) or 0),
                    str(job.get("status", "") or ""),
                    float(job.get("updated_at", 0) or 0),
                )
                for case_id, job in _CLAIM_JOBS.items()
            ]
        for (
            case_id,
            owner_ref,
            owner_user_id,
            expires_at,
            status,
            updated_at,
        ) in jobs:
            if status != "complete" and expires_at <= now:
                _expire_claim_job(
                    case_id,
                    owner_ref,
                    owner_user_id,
                )
            if status in {"complete", "collection_partial", "expired"}:
                if updated_at and now - updated_at > 30 * 60:
                    with _CLAIM_JOB_LOCK:
                        current = _CLAIM_JOBS.get(case_id)
                        if (
                            current
                            and current.get("owner_ref") == owner_ref
                            and current.get("status") == status
                        ):
                            _CLAIM_JOBS.pop(case_id, None)


def _ensure_claim_job_sweeper() -> None:
    global _CLAIM_SWEEPER_STARTED
    with _CLAIM_JOB_LOCK:
        if _CLAIM_SWEEPER_STARTED:
            return
        _CLAIM_SWEEPER_STARTED = True
    sweeper = threading.Thread(
        target=_claim_job_sweeper,
        daemon=True,
        name="claim-auth-sweeper",
    )
    sweeper.start()


def _register_background_claim_job(
    user_id: str,
    case_id: str,
    transient: dict[str, Any],
) -> None:
    _ensure_claim_job_sweeper()
    owner_ref = _claim_job_owner_ref(user_id)
    absolute_expires_at = _claim_absolute_expiry(transient)
    expires_at = min(
        float(transient.get("expires_at", 0) or 0),
        time.time() + AUTH_TTL_SECONDS,
        absolute_expires_at,
    )
    transient["expires_at"] = expires_at
    wake_event = threading.Event()
    with _CLAIM_JOB_LOCK:
        existing = _CLAIM_JOBS.get(case_id)
        if existing and existing.get("status") == "running":
            return
        _CLAIM_JOBS[case_id] = {
            "owner_ref": owner_ref,
            "owner_user_id": str(user_id or "").strip().lower(),
            "sealed_payload": _seal_claim_job_payload(transient),
            "expires_at": expires_at,
            "status": "queued",
            "progress": 0,
            "safe_message": CLAIM_AUTH_STAGE_MESSAGES[0],
            "summary": {
                "ready": 0,
                "target": 0,
            },
            "updated_at": time.time(),
            "wake_event": wake_event,
        }


def _activate_background_claim_job(
    user_id: str,
    case_id: str,
    *,
    initial_delay: float = AUTH_POLL_SECONDS,
) -> bool:
    owner_ref = _claim_job_owner_ref(user_id)
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(case_id)
        if (
            not job
            or job.get("owner_ref") != owner_ref
            or float(job.get("expires_at", 0) or 0) <= time.time()
            or not job.get("sealed_payload")
        ):
            return False
        if job.get("status") == "running":
            wake_event = job.get("wake_event")
            if isinstance(wake_event, threading.Event):
                wake_event.set()
            return True
        job["status"] = "running"
        job["safe_message"] = CLAIM_AUTH_STAGE_MESSAGES[0]
        job["updated_at"] = time.time()
    _CLAIM_JOB_EXECUTOR.submit(
        _run_background_claim_job,
        user_id,
        case_id,
        owner_ref,
        initial_delay=initial_delay,
    )
    return True


def _claim_job_snapshot(
    user_id: str,
    case_id: str,
) -> dict[str, Any] | None:
    owner_ref = _claim_job_owner_ref(user_id)
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(case_id)
        if not job or job.get("owner_ref") != owner_ref:
            return None
        return {
            "status": str(job.get("status", "") or ""),
            "progress": int(job.get("progress", 0) or 0),
            "safe_message": str(job.get("safe_message", "") or ""),
            "summary": dict(job.get("summary") or {}),
            "expires_at": float(job.get("expires_at", 0) or 0),
        }


def _retry_or_wake_claim_job(user_id: str, case_id: str) -> bool:
    owner_ref = _claim_job_owner_ref(user_id)
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(case_id)
        if (
            not job
            or job.get("owner_ref") != owner_ref
            or float(job.get("expires_at", 0) or 0) <= time.time()
            or not job.get("sealed_payload")
        ):
            return False
        wake_event = job.get("wake_event")
        if not isinstance(wake_event, threading.Event):
            return False
        if job.get("status") == "running":
            wake_event.set()
            return True
    return _activate_background_claim_job(
        user_id,
        case_id,
        initial_delay=0,
    )


def _retry_authenticated_claim_collection(
    user_id: str,
    case_id: str,
) -> tuple[bool, str]:
    owner_user_id = str(user_id or "").strip().lower()
    normalized_case_id = str(case_id or "").strip()
    if not owner_user_id or not normalized_case_id:
        return False, "재수집할 요청을 확인하지 못했습니다."
    if not bool(provider_readiness().get("simple_auth_ready")):
        return False, "자료수집 API 설정을 먼저 확인해 주세요."

    try:
        repository = ClaimRepository(owner_user_id)
        case = repository.get_case(normalized_case_id)
    except ClaimRepositoryError:
        return False, "저장된 요청 상태를 확인하지 못했습니다."
    if not case:
        return False, "재수집할 요청을 찾지 못했습니다."
    if (
        str(case.get("hometax_status", "") or "") != "auth_complete"
        or str(case.get("comwel_status", "") or "") != "auth_complete"
    ):
        return False, "홈택스와 근로복지공단 인증을 모두 완료해야 재수집할 수 있습니다."

    owner_ref = _claim_job_owner_ref(owner_user_id)
    previous_status = ""
    should_expire = False
    now = time.time()
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(normalized_case_id)
        if not job or job.get("owner_ref") != owner_ref:
            return (
                False,
                "보안상 임시 인증정보가 남아 있지 않아 새 인증 요청이 필요합니다.",
            )
        previous_status = str(job.get("status", "") or "")
        sealed_payload = job.get("sealed_payload")
        expires_at = float(job.get("expires_at", 0) or 0)
        if previous_status == "complete":
            return False, "현재 연결된 자료수집이 이미 완료되었습니다."
        if (
            previous_status == "expired"
            or expires_at <= now
            or not isinstance(sealed_payload, bytes)
            or not sealed_payload
        ):
            should_expire = True
        elif previous_status in {"running", "queued"}:
            wake_event = job.get("wake_event")
            if isinstance(wake_event, threading.Event):
                wake_event.set()
            return True, "자료수집이 이미 진행 중입니다."
        elif previous_status in {
            "awaiting_business_selection",
            "awaiting_management_selection",
        }:
            return (
                False,
                "화면에 표시된 사업장 선택을 먼저 완료해야 자료수집을 계속할 수 있습니다.",
            )
        elif previous_status not in {"paused", "collection_partial"}:
            return False, "현재 상태에서는 자료 재수집을 시작할 수 없습니다."
        else:
            transient: dict[str, Any] | None = None
            try:
                transient = _unseal_claim_job_payload(sealed_payload)
                context = transient.get("auth_context")
                if (
                    not isinstance(context, dict)
                    or not isinstance(transient.get("hometax"), dict)
                    or not isinstance(transient.get("comwel"), dict)
                ):
                    should_expire = True
            except ClaimProviderError:
                should_expire = True
            finally:
                if isinstance(transient, dict):
                    transient.clear()
            if not should_expire:
                job["status"] = "queued"
                job["safe_message"] = "인증 완료 자료의 재수집을 준비합니다."
                job["updated_at"] = now

    if should_expire:
        _expire_claim_job(
            normalized_case_id,
            owner_ref,
            owner_user_id,
        )
        return (
            False,
            "임시 인증정보가 만료되어 고객의 새 인증 요청이 필요합니다.",
        )

    try:
        repository.update_case_status(
            normalized_case_id,
            overall_status="collecting",
            last_safe_error_code=None,
        )
        repository.append_audit_event(
            case_id=normalized_case_id,
            action="collection_retry_requested",
            source="provider",
            outcome="requested",
            metadata={
                "previous_job_status": previous_status,
                "reuses_authenticated_session": True,
            },
        )
    except ClaimRepositoryError:
        with _CLAIM_JOB_LOCK:
            job = _CLAIM_JOBS.get(normalized_case_id)
            if (
                job
                and job.get("owner_ref") == owner_ref
                and str(job.get("status", "") or "") == "queued"
            ):
                job["status"] = previous_status
                job["safe_message"] = "일부 자료를 다시 수집할 수 있습니다."
                job["updated_at"] = time.time()
        try:
            repository.update_case_status(
                normalized_case_id,
                overall_status="auth_complete_collection_pending",
                last_safe_error_code="COLLECTION_RETRY_STATE_SAVE_FAILED",
            )
        except ClaimRepositoryError:
            pass
        return False, "재수집 상태를 저장하지 못했습니다. 잠시 후 다시 시도해 주세요."

    try:
        activated = _activate_background_claim_job(
            owner_user_id,
            normalized_case_id,
            initial_delay=0,
        )
    except Exception:
        activated = False
    if activated:
        _update_claim_job(
            normalized_case_id,
            owner_ref,
            safe_message="인증 완료 자료를 다시 수집하고 있습니다.",
        )
        return True, "인증 완료 상태를 유지한 채 자료 재수집을 시작했습니다."

    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(normalized_case_id)
        if (
            job
            and job.get("owner_ref") == owner_ref
            and str(job.get("status", "") or "") in {"queued", "running"}
        ):
            job["status"] = previous_status
            job["safe_message"] = "일부 자료를 다시 수집할 수 있습니다."
            job["updated_at"] = time.time()
    try:
        repository.update_case_status(
            normalized_case_id,
            overall_status="auth_complete_collection_pending",
            last_safe_error_code="COLLECTION_RETRY_START_FAILED",
        )
        repository.append_audit_event(
            case_id=normalized_case_id,
            action="collection_retry_requested",
            source="provider",
            outcome="failed",
            metadata={"safe_error_code": "COLLECTION_RETRY_START_FAILED"},
        )
    except ClaimRepositoryError:
        pass
    return False, "재수집 작업을 시작하지 못했습니다. 잠시 후 다시 시도해 주세요."


def _claim_collection_retry_state(
    case: dict[str, Any],
    job_snapshot: dict[str, Any] | None,
    *,
    provider_ready: bool,
) -> str:
    authentication_complete = (
        str(case.get("hometax_status", "") or "") == "auth_complete"
        and str(case.get("comwel_status", "") or "") == "auth_complete"
    )
    if not authentication_complete:
        return "hidden"

    if not job_snapshot:
        overall_status = str(case.get("overall_status", "") or "")
        if overall_status in {"collected", "ready"}:
            return "complete"
        return (
            "reauth_required"
            if overall_status
            in {
                "auth_complete_collection_pending",
                "collection_queued",
                "collecting",
            }
            else "hidden"
        )

    job_status = str(job_snapshot.get("status", "") or "")
    if job_status in {"paused", "collection_partial"}:
        return "retryable" if provider_ready else "provider_unavailable"
    if job_status in {"running", "queued"}:
        return "running"
    if job_status in {
        "awaiting_business_selection",
        "awaiting_management_selection",
    }:
        return "selection_required"
    if job_status == "complete":
        return "complete"
    if (
        job_status == "expired"
        or float(job_snapshot.get("expires_at", 0) or 0) <= time.time()
    ):
        return "reauth_required"
    return "hidden"


def _render_claim_collection_retry_action(
    user_id: str,
    case: dict[str, Any],
    job_snapshot: dict[str, Any] | None,
    *,
    provider_ready: bool,
    key_prefix: str,
) -> str:
    state = _claim_collection_retry_state(
        case,
        job_snapshot,
        provider_ready=provider_ready,
    )
    case_id = str(case.get("id", "") or "")
    if state == "retryable":
        st.warning(
            "인증은 완료됐지만 일부 자료를 수집하지 못했습니다. "
            "완료된 자료는 그대로 두고 실패한 자료만 다시 수집할 수 있습니다."
        )
        if st.button(
            "실패 자료 재수집",
            type="primary",
            use_container_width=True,
            key=f"{key_prefix}_{case_id}",
        ):
            retried, message = _retry_authenticated_claim_collection(
                user_id,
                case_id,
            )
            if retried:
                st.session_state["_claim_active_case_v1"] = case_id
                st.session_state.pop(
                    f"_claim_collection_notified_{case_id}",
                    None,
                )
                st.toast(message)
                st.rerun(scope="app")
            else:
                st.error(message)
    elif state == "running":
        st.info(
            "자료 수집 또는 재수집이 진행 중입니다. "
            "완료된 자료는 다시 내려받지 않습니다."
        )
    elif state == "provider_unavailable":
        st.error("자료수집 API 설정을 확인한 뒤 재수집할 수 있습니다.")
    elif state == "reauth_required":
        st.warning(
            "재수집에 필요한 임시 인증정보가 만료됐거나 서버 재시작으로 "
            "삭제되었습니다. 개인정보 보호를 위해 복구하지 않으므로 "
            "고객에게 새 인증 요청을 보내 주세요."
        )
    return state


def _select_claim_business_number(
    user_id: str,
    case_id: str,
    selection_token: str,
) -> bool:
    owner_ref = _claim_job_owner_ref(user_id)
    requested_token = str(selection_token or "").strip()
    expired_after_unseal = False
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(case_id)
        if (
            not job
            or job.get("owner_ref") != owner_ref
            or job.get("status") != "awaiting_business_selection"
            or float(job.get("expires_at", 0) or 0) <= time.time()
            or not job.get("sealed_payload")
        ):
            return False
        transient = _unseal_claim_job_payload(job["sealed_payload"])
        raw_candidates = transient.get("business_candidates")
        candidates = (
            raw_candidates if isinstance(raw_candidates, list) else []
        )
        selected_number = next(
            (
                _digits(candidate.get("business_number"))
                for candidate in candidates
                if isinstance(candidate, dict)
                and _is_valid_business_no(candidate.get("business_number"))
                and hmac.compare_digest(
                    _business_candidate_token(
                        case_id,
                        _digits(candidate.get("business_number")),
                    ),
                    requested_token,
                )
            ),
            "",
        )
        if not selected_number:
            transient.clear()
            return False
        transient["selected_business_number"] = selected_number
        transient["business_number"] = selected_number
        _set_claim_expiry(transient, COLLECTION_TTL_SECONDS)
        if transient["expires_at"] <= time.time():
            job["sealed_payload"] = b""
            job["status"] = "expired"
            job["safe_message"] = (
                "인증 유효시간이 지나 임시 인증정보를 삭제했습니다. "
                "새 인증 요청을 시작해 주세요."
            )
            job["updated_at"] = time.time()
            transient.clear()
            expired_after_unseal = True
        else:
            previous_summary = dict(job.get("summary") or {})
            job["sealed_payload"] = _seal_claim_job_payload(transient)
            job["expires_at"] = transient["expires_at"]
            job["status"] = "queued"
            job["progress"] = int(job.get("progress", 0) or 0)
            job["safe_message"] = (
                "선택한 사업자번호로 근로복지공단 자료수집을 준비합니다."
            )
            job["summary"] = {
                "ready": int(previous_summary.get("ready", 0) or 0),
                "target": int(previous_summary.get("target", 0) or 0),
            }
            job["updated_at"] = time.time()
    if expired_after_unseal:
        _sync_interrupted_claim_case(
            user_id,
            case_id,
            active_action="collect",
            safe_error_code="AUTH_SESSION_EXPIRED",
            outcome="expired",
        )
        return False
    return _activate_background_claim_job(
        user_id,
        case_id,
        initial_delay=0,
    )


def _select_claim_management_number(
    user_id: str,
    case_id: str,
    management_number: str,
) -> bool:
    owner_ref = _claim_job_owner_ref(user_id)
    selected_digits = _digits(management_number)
    expired_after_unseal = False
    with _CLAIM_JOB_LOCK:
        job = _CLAIM_JOBS.get(case_id)
        if (
            not job
            or job.get("owner_ref") != owner_ref
            or job.get("status") != "awaiting_management_selection"
            or float(job.get("expires_at", 0) or 0) <= time.time()
            or not job.get("sealed_payload")
        ):
            return False
        summary = dict(job.get("summary") or {})
        allowed_numbers = {
            _digits(number)
            for number in summary.get("management_numbers", [])
            if _digits(number)
        }
        if selected_digits not in allowed_numbers:
            return False
        transient = _unseal_claim_job_payload(job["sealed_payload"])
        transient["selected_management_number"] = selected_digits
        _set_claim_expiry(transient, COLLECTION_TTL_SECONDS)
        if transient["expires_at"] <= time.time():
            job["sealed_payload"] = b""
            job["status"] = "expired"
            job["safe_message"] = (
                "인증 유효시간이 지나 임시 인증정보를 삭제했습니다. "
                "새 인증 요청을 시작해 주세요."
            )
            job["updated_at"] = time.time()
            transient.clear()
            expired_after_unseal = True
        else:
            previous_summary = dict(job.get("summary") or {})
            job["sealed_payload"] = _seal_claim_job_payload(transient)
            job["expires_at"] = transient["expires_at"]
            job["status"] = "queued"
            job["progress"] = int(job.get("progress", 0) or 0)
            job["safe_message"] = "선택한 사업장의 요율 수집을 준비합니다."
            job["summary"] = {
                "ready": int(previous_summary.get("ready", 0) or 0),
                "target": int(previous_summary.get("target", 0) or 0),
            }
            job["updated_at"] = time.time()
    if expired_after_unseal:
        _sync_interrupted_claim_case(
            user_id,
            case_id,
            active_action="collect",
            safe_error_code="AUTH_SESSION_EXPIRED",
            outcome="expired",
        )
        return False
    return _activate_background_claim_job(
        user_id,
        case_id,
        initial_delay=0,
    )


def _case_label(row: dict[str, Any]) -> str:
    company = _clean(row.get("company_name")) or "업체명 없음"
    requested = _clean(row.get("requested_at"))
    date_text = requested[:10] if requested else "-"
    return f"{company} · {date_text} · {str(row.get('id', ''))[:8]}"


def _selected_customer(
    user_id: str,
    input_mode: str,
) -> tuple[dict[str, Any], str]:
    if input_mode == "직접입력":
        return {}, "manual"

    customers = load_registered_customers(
        get_user_cumulative_db_path(user_id),
        owner_user_id=user_id,
    )
    if customers.empty:
        st.info("등록된 고객이 없어 직접 입력 방식으로 전환했습니다.")
        return {}, "manual"

    labels, row_map = build_customer_labels(customers)
    selected = st.selectbox(
        "고객 선택",
        labels,
        key="claim_customer_selector_v1",
    )
    row = customers.loc[row_map[selected]].to_dict()
    suffix = hashlib.sha256(selected.encode("utf-8")).hexdigest()[:8]
    return row, suffix


def _render_intro(
    repository_ready: bool,
    repository_message: str,
    readiness: dict[str, object],
) -> None:
    st.markdown(
        """
        <style>
        .claim-hero {
            border: 1px solid #dce5f3;
            border-radius: 18px;
            padding: 1.15rem 1.25rem;
            margin-bottom: 0.9rem;
            background:
                linear-gradient(135deg, #f8fbff 0%, #eef5ff 58%, #f7fbff 100%);
        }
        .claim-hero h2 {
            margin: 0 0 0.38rem 0;
            color: #102d5c;
            font-size: 1.5rem;
            letter-spacing: -0.04em;
        }
        .claim-hero p {
            margin: 0;
            color: #4c607c;
            line-height: 1.65;
        }
        .claim-flow {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.55rem;
            margin: 0.75rem 0 1rem;
        }
        .claim-step {
            border: 1px solid #dce5f3;
            border-radius: 12px;
            padding: 0.72rem 0.78rem;
            background: #ffffff;
            color: #18345f;
            font-size: 0.91rem;
            font-weight: 700;
        }
        .claim-step span {
            display: block;
            color: #6c7d96;
            font-size: 0.72rem;
            margin-bottom: 0.22rem;
            font-weight: 700;
        }
        @media (max-width: 700px) {
            .claim-flow { grid-template-columns: 1fr 1fr; }
            .claim-hero { padding: 0.95rem; }
            .claim-hero h2 { font-size: 1.28rem; }
        }
        </style>
        <div class="claim-hero">
            <h2>경정청구 자료수집</h2>
            <p>
                개인사업자는 홈택스 인증 완료 후 근로복지공단 인증을
                순서대로 요청하고, 법인사업자는 공동인증서 인증 완료 후
                자료를 수집합니다.
            </p>
        </div>
        <div class="claim-flow">
            <div class="claim-step"><span>01</span>고객정보 입력</div>
            <div class="claim-step"><span>02</span>홈택스 인증</div>
            <div class="claim-step"><span>03</span>근로복지공단 인증</div>
            <div class="claim-step"><span>04</span>수집결과 확인</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not repository_ready:
        st.warning(repository_message)
    if not bool(readiness.get("simple_auth_ready")):
        st.info(
            "화면과 안전한 저장 구조는 준비됐습니다. 실제 카카오 인증 발송은 "
            "승인된 중계 API 설정 후 활성화됩니다."
        )


def _render_remote_personal_invite(
    *,
    user_id: str,
    user_name: str,
    repository_ready: bool,
    provider_ready: bool,
) -> None:
    st.markdown("#### 고객 카카오톡 인증 링크 발송")
    st.caption(
        "고객에게 안전한 입력 링크를 보냅니다. 고객이 링크에서 본인정보와 "
        "동의 내용을 직접 입력한 뒤 홈택스·근로복지공단 인증을 진행합니다."
    )

    notice = st.session_state.get(_REMOTE_INVITE_NOTICE_KEY)
    if isinstance(notice, dict):
        expiry_text = _format_remote_invite_expiry(notice.get("expires_at"))
        success_message = "카카오톡 인증 링크 발송 요청을 등록했습니다."
        if expiry_text:
            success_message += f" 링크 유효시간: {expiry_text}까지"
        st.success(success_message)

    readiness_messages: list[str] = []
    if not repository_ready:
        readiness_messages.append(
            "원격 인증 저장소 연결이 준비되지 않았습니다. 관리자에게 문의해주세요."
        )
    if not provider_ready:
        readiness_messages.append(
            "홈택스·근로복지공단 인증 연동 설정이 완료되지 않았습니다. "
            "관리자에게 문의해주세요."
        )
    remote_runtime_ready, remote_runtime_message = (
        _remote_invite_runtime_readiness()
    )
    if not remote_runtime_ready:
        readiness_messages.append(remote_runtime_message)
    for message in readiness_messages:
        st.warning(message)

    with st.form("claim_remote_personal_invite_v1", clear_on_submit=True):
        name_col, phone_col = st.columns(2)
        with name_col:
            customer_name = st.text_input(
                "고객 이름",
                placeholder="홍길동",
                key="claim_remote_customer_name_v1",
            )
        with phone_col:
            customer_phone = st.text_input(
                "고객 휴대전화",
                placeholder="010-0000-0000",
                key="claim_remote_customer_phone_v1",
            )
        submitted = st.form_submit_button(
            "카카오톡 인증 링크 발송",
            use_container_width=True,
            type="primary",
            disabled=bool(readiness_messages),
        )

    if not submitted:
        return

    name, phone, errors = _validate_remote_invite_input(
        customer_name,
        customer_phone,
    )
    if errors:
        for error in errors:
            st.error(error)
        return

    try:
        result = _create_remote_claim_invite(
            owner_user_id=user_id,
            requested_by=user_name or user_id,
            customer_name=name,
            customer_phone=phone,
        )
    except RemoteInviteUIError as exc:
        st.error(str(exc))
        return

    notice = {
        "expires_at": result.get("expires_at"),
        "status": result.get("status"),
    }
    st.session_state[_REMOTE_INVITE_NOTICE_KEY] = notice
    expiry_text = _format_remote_invite_expiry(notice.get("expires_at"))
    success_message = "카카오톡 인증 링크 발송 요청을 등록했습니다."
    if expiry_text:
        success_message += f" 링크 유효시간: {expiry_text}까지"
    st.success(success_message)


def _render_personal_request(
    user_id: str,
    user_name: str,
    repository: ClaimRepository | None,
    provider_ready: bool,
) -> None:
    input_mode = st.radio(
        "고객정보 입력 방식",
        ["카카오톡 발송", "직접입력"],
        horizontal=True,
        key="claim_personal_input_mode_v2",
        help=(
            "카카오톡 발송은 고객 이름과 휴대전화만 입력해 안전한 본인정보 "
            "입력 링크를 보내는 방식입니다. 직접입력은 담당자가 고객의 "
            "인증정보를 입력해 바로 기관 인증을 요청하는 방식입니다."
        ),
    )
    remote_input = input_mode == "카카오톡 발송"
    if remote_input:
        _render_remote_personal_invite(
            user_id=user_id,
            user_name=user_name,
            repository_ready=repository is not None,
            provider_ready=provider_ready,
        )
        return

    suffix = "kakao" if remote_input else "manual"
    company_name = ""
    business_no = ""
    identity_front = ""
    identity_rear = ""

    form_key = f"claim_personal_request_{suffix}"
    with st.form(form_key, clear_on_submit=True):
        st.markdown(
            "#### 개인사업자 카카오 인증 직접발송"
            if remote_input
            else "#### 개인사업자 카카오 인증 요청"
        )
        if remote_input:
            st.caption(
                "담당자가 고객정보를 입력해 홈택스 인증을 먼저 발송합니다. "
                "고객은 카카오톡에서 인증만 승인하면 됩니다."
            )
            with st.expander(
                "사업장관리번호·사업장요율까지 수집하려면 (선택)"
            ):
                business_no = st.text_input(
                    "사업자등록번호 (선택)",
                    placeholder="000-00-00000",
                    key=f"claim_business_no_{suffix}",
                    help=(
                        "인증 발송에는 필요하지 않습니다. 입력하지 않아도 "
                        "보수총액신고내역은 수집되지만, 사업장관리번호와 "
                        "사업장요율은 수집할 수 없습니다."
                    ),
                )
        else:
            st.caption(
                "홈택스 인증을 먼저 발송합니다. 고객이 홈택스 인증을 마치면 "
                "약 1초 후 근로복지공단 인증 발송을 시작합니다."
            )
            company_col, business_col = st.columns(2)
            with company_col:
                company_name = st.text_input(
                    "상호명 (선택)",
                    key=f"claim_company_{suffix}",
                )
            with business_col:
                business_no = st.text_input(
                    "사업자등록번호 (선택)",
                    placeholder="000-00-00000",
                    key=f"claim_business_no_{suffix}",
                    help=(
                        "인증 자체에는 선택값이지만, 사업장관리번호와 "
                        "사업장요율 수집에는 필요합니다."
                    ),
                )

        name_col, phone_col = st.columns(2)
        with name_col:
            representative = st.text_input(
                "대표자 이름",
                key=f"claim_representative_{suffix}",
            )
        with phone_col:
            cellphone = st.text_input(
                "대표자 휴대전화",
                placeholder="010-0000-0000",
                key=f"claim_cellphone_{suffix}",
            )

        st.markdown("**인증·수집 기관**")
        st.info(
            "① 홈택스 인증 발송 → ② 홈택스 승인 자동 확인 → "
            "③ 약 1초 후 근로복지공단 인증 발송 → "
            "④ 두 기관 인증 완료 후 자료수집"
        )
        id_front_col, id_rear_col = st.columns(2)
        with id_front_col:
            identity_front = st.text_input(
                "주민등록번호 앞 6자리",
                max_chars=6,
                placeholder="생년월일 6자리",
                type="password",
                key=f"claim_identity_front_{suffix}",
                help=(
                    "홈택스에는 생년월일로 변환해 전송합니다. "
                    "입력값은 DB와 로그에 저장하지 않습니다."
                ),
            )
        with id_rear_col:
            identity_rear = st.text_input(
                "주민등록번호 뒤 7자리",
                max_chars=7,
                placeholder="근로복지공단 인증에만 사용",
                type="password",
                key=f"claim_identity_rear_{suffix}",
                    help=(
                        "순차 인증 자동 확인을 위해 Railway 서버 메모리에 "
                        "인증 및 자료수집 중 최대 45분만 암호화해 보관하며 "
                        "DB·로그에는 저장하지 않습니다."
                    ),
                )

        consent_confirmed = st.checkbox(
            CONSENT_NOTICE_TEXT,
            key=f"claim_consent_{suffix}",
        )
        legal_basis_confirmed = st.checkbox(
            COLLECTION_AUTHORITY_TEXT,
            key=f"claim_legal_basis_{suffix}",
        )
        submitted = st.form_submit_button(
            (
                "홈택스 카카오 인증 직접발송"
                if remote_input
                else "홈택스 카카오 인증 발송"
            ),
            use_container_width=True,
            type="primary",
            disabled=not (repository and provider_ready),
        )

    if not submitted:
        return

    sources = ["hometax", "comwel"]
    front_digits = _digits(identity_front)
    rear_digits = _digits(identity_rear)
    phone_digits = _digits(cellphone)
    business_digits = _digits(business_no)
    errors: list[str] = []
    if business_digits and not _is_valid_business_no(business_digits):
        errors.append("유효한 사업자등록번호인지 확인해주세요.")
    if not representative.strip():
        errors.append("대표자 이름을 입력해주세요.")
    if len(phone_digits) != 11 or not phone_digits.startswith("010"):
        errors.append("카카오 인증을 받을 010 휴대전화 번호를 확인해주세요.")
    if "hometax" in sources and not _birth_date_from_identity(
        front_digits,
        rear_digits,
    ):
        errors.append("홈택스 인증용 주민등록번호 앞자리와 구분값을 확인해주세요.")
    if "comwel" in sources and (
        len(front_digits) != 6 or len(rear_digits) != 7
    ):
        errors.append("근로복지공단 인증에는 주민등록번호 13자리가 필요합니다.")
    if not consent_confirmed or not legal_basis_confirmed:
        errors.append("동의와 법적 근거 확인 항목을 모두 확인해주세요.")
    if errors:
        for error in errors:
            st.error(error)
        return

    assert repository is not None
    case: dict[str, Any] | None = None
    source_results: dict[str, str] = {
        "comwel_status": "request_ready",
    }
    request_started_at = time.time()
    transient: dict[str, Any] = {
        "request_started_at": request_started_at,
        "absolute_expires_at": (
            request_started_at + COLLECTION_TTL_SECONDS
        ),
        "expires_at": min(
            request_started_at + AUTH_TTL_SECONDS,
            request_started_at + COLLECTION_TTL_SECONDS,
        ),
        "stage_started_at": request_started_at,
        "expected_sources": list(sources),
        "business_number": business_digits,
        "auth_context": {
            "representative": representative.strip(),
            "cellphone": phone_digits,
            "birth_date": _birth_date_from_identity(front_digits, rear_digits),
            "identity_number": f"{front_digits}{rear_digits}",
        },
    }
    provider_failures: list[tuple[str, str]] = []
    try:
        case = repository.create_case(
            company_name=company_name.strip() or "상호명 미입력",
            business_no=business_digits,
            business_type="individual",
            representative_name=representative,
            cellphone=phone_digits,
            requested_by=user_name or user_id,
            selected_sources=sources,
            consent_version=CONSENT_VERSION,
            consent_text_sha256=CONSENT_TEXT_SHA256,
            consent_channel="staff_attestation",
            retention_policy_version=RETENTION_POLICY_VERSION,
            collection_authority_confirmed=legal_basis_confirmed,
        )
        client = TilkoClaimClient()
        if "hometax" in sources:
            try:
                birth_date = _birth_date_from_identity(front_digits, rear_digits)
                transient["hometax"] = client.request_hometax_kakao(
                    birth_date=birth_date,
                    user_name=representative.strip(),
                    cellphone=phone_digits,
                )
                source_results["hometax_status"] = "auth_requested"
                _register_background_claim_job(
                    user_id,
                    str(case["id"]),
                    transient,
                )
                st.session_state["_claim_active_case_v1"] = case["id"]
            except ClaimProviderError as exc:
                source_results["hometax_status"] = "failed"
                provider_failures.append(("홈택스", str(exc)))
        requested_sources = [
            source
            for source in ("hometax",)
            if source in transient
        ]
        repository.update_case_status(
            case["id"],
            **source_results,
            overall_status=(
                "auth_partial"
                if provider_failures and requested_sources
                else "auth_pending"
                if requested_sources
                else "failed"
            ),
            auth_requested_at=(
                datetime.now(timezone.utc).isoformat()
                if requested_sources
                else None
            ),
            last_safe_error_code=(
                "AUTH_REQUEST_PARTIAL"
                if provider_failures and requested_sources
                else "AUTH_REQUEST_FAILED"
                if provider_failures
                else None
            ),
        )
        repository.append_audit_event(
            case_id=case["id"],
            action="auth_request",
            source="provider",
            outcome=(
                "partial"
                if provider_failures and requested_sources
                else "failed"
                if provider_failures
                else "success"
            ),
            metadata={
                "requested_sources": requested_sources,
                "planned_sources": ["comwel"],
                "failed_source_count": len(provider_failures),
            },
        )
        if requested_sources and not _activate_background_claim_job(
            user_id,
            str(case["id"]),
        ):
            raise ClaimProviderError(
                "자동 인증 확인 작업을 시작하지 못했습니다. "
                "새 인증 요청을 시작해 주세요."
            )
        source_labels = [
            "홈택스"
            for source in requested_sources
        ]
        if source_labels:
            message = (
                f"{'·'.join(source_labels)} 카카오 인증 요청을 발송했습니다. "
                "화면을 이동하거나 닫아도 Railway가 고객 인증을 자동으로 "
                "확인합니다. "
                "홈택스 인증이 확인되는 즉시 근로복지공단 인증을 이어서 "
                "발송합니다."
            )
            if provider_failures:
                failed_labels = "·".join(
                    label for label, _ in provider_failures
                )
                message += f" {failed_labels} 요청은 실패해 다시 요청해야 합니다."
            st.session_state["_claim_flash_v1"] = message
            st.rerun()
        transient.clear()
        for label, message in provider_failures:
            st.error(f"{label}: {message}")
    except (ClaimProviderError, ClaimRepositoryError) as exc:
        if case is not None:
            _update_claim_job(
                str(case["id"]),
                _claim_job_owner_ref(user_id),
                status="paused",
                safe_message=(
                    "초기 상태 저장에 실패했습니다. "
                    "‘지금 인증 상태 확인’을 눌러 다시 시도해 주세요."
                ),
            )
            try:
                repository.update_case_status(
                    case["id"],
                    overall_status="failed",
                    last_safe_error_code="AUTH_REQUEST_FAILED",
                )
                repository.append_audit_event(
                    case_id=case["id"],
                    action="auth_request",
                    source="provider",
                    outcome="failed",
                    metadata={"safe_error_code": "AUTH_REQUEST_FAILED"},
                )
            except ClaimRepositoryError:
                pass
        transient.clear()
        st.error(str(exc))


def _render_corporate_request(
    user_id: str,
    user_name: str,
    repository: ClaimRepository | None,
    corporate_ready: bool,
) -> None:
    st.markdown("#### 법인사업자 공동인증서 인증")
    st.caption(
        "공동인증서는 고객 PC의 로컬 인증 모듈에서 사용합니다. "
        "인증서 파일·개인키·비밀번호는 OASIS와 Supabase에 저장하지 않습니다."
    )
    input_mode = st.radio(
        "고객정보 입력 방식",
        ["등록 고객 선택", "직접 입력"],
        horizontal=True,
        key="claim_corporate_input_mode_v1",
    )
    row, suffix = _selected_customer(user_id, input_mode)
    with st.form(f"claim_corporate_request_{suffix}"):
        company_col, business_col = st.columns(2)
        with company_col:
            company_name = st.text_input(
                "법인명",
                value=_clean(row.get("업체명")),
                key=f"claim_corp_company_{suffix}",
            )
        with business_col:
            business_no = st.text_input(
                "사업자등록번호",
                value=_format_business_no(row.get("사업자등록번호")),
                key=f"claim_corp_business_no_{suffix}",
            )
        representative = st.text_input(
            "대표자 이름",
            value=_clean(row.get("대표자명")),
            key=f"claim_corp_representative_{suffix}",
        )
        consent_confirmed = st.checkbox(
            "법인의 자료조회 위임과 수집 범위를 확인했습니다.",
            key=f"claim_corp_consent_{suffix}",
        )
        submitted = st.form_submit_button(
            "공동인증서 인증 준비 건 등록",
            use_container_width=True,
            disabled=not (repository and corporate_ready),
        )

    if submitted:
        if not company_name.strip():
            st.error("법인명을 입력해주세요.")
            return
        if not _is_valid_business_no(business_no):
            st.error("유효한 사업자등록번호인지 확인해주세요.")
            return
        if not representative.strip():
            st.error("대표자 이름을 입력해주세요.")
            return
        if not consent_confirmed:
            st.error("법인의 자료조회 위임과 수집 범위를 먼저 확인해주세요.")
            return
        assert repository is not None
        try:
            case = repository.create_case(
                company_name=company_name,
                business_no=business_no,
                business_type="corporation",
                representative_name=representative,
                cellphone="",
                requested_by=user_name or user_id,
                selected_sources=["hometax", "comwel"],
                consent_version=CONSENT_VERSION,
                consent_text_sha256=CONSENT_TEXT_SHA256,
                consent_channel="staff_attestation",
                retention_policy_version=RETENTION_POLICY_VERSION,
                collection_authority_confirmed=consent_confirmed,
            )
            repository.update_case_status(
                case["id"],
                hometax_status="certificate_required",
                comwel_status="certificate_required",
                overall_status="auth_preparing",
            )
            st.success("공동인증서 인증 준비 건을 등록했습니다.")
        except ClaimRepositoryError as exc:
            st.error(str(exc))

    if not corporate_ready:
        st.info(
            "법인 인증은 고객 PC용 공동인증서 모듈 계약, 요청 건별 인증 "
            "연결, 결과 콜백 검증까지 설정한 뒤 활성화됩니다. 인증서 "
            "파일·개인키·비밀번호는 OASIS 서버로 전송하지 않습니다."
        )


def _render_request_tab(
    user_id: str,
    user_name: str,
    repository: ClaimRepository | None,
    readiness: dict[str, object],
) -> None:
    business_type = st.radio(
        "사업자 구분",
        ["개인사업자", "법인사업자"],
        horizontal=True,
        key="claim_business_type_v1",
    )
    if business_type == "개인사업자":
        _render_personal_request(
            user_id,
            user_name,
            repository,
            bool(readiness.get("simple_auth_ready")),
        )
    else:
        _render_corporate_request(
            user_id,
            user_name,
            repository,
            bool(readiness.get("corporate_auth_ready")),
        )


def _cases_dataframe(cases: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for case in cases:
        rows.append(
            {
                "요청일": _clean(case.get("requested_at"))[:16].replace("T", " "),
                "상호명": _clean(case.get("company_name")),
                "사업자번호": _clean(case.get("business_no_masked")),
                "구분": (
                    "개인"
                    if case.get("business_type") == "individual"
                    else "법인"
                ),
                "홈택스": _source_status(case.get("hometax_status")),
                "근로복지공단": _source_status(case.get("comwel_status")),
                "전체상태": _source_status(case.get("overall_status")),
            }
        )
    return pd.DataFrame(rows)


@st.fragment(run_every="2s")
def _render_auto_claim_monitor(
    user_id: str,
    case_id: str,
    repository: ClaimRepository,
    provider_ready: bool,
) -> None:
    job_snapshot = _claim_job_snapshot(user_id, case_id)
    try:
        current_case = repository.get_case(case_id)
    except ClaimRepositoryError as exc:
        st.error(str(exc))
        return
    if not current_case:
        if st.session_state.get("_claim_active_case_v1") == case_id:
            st.session_state.pop("_claim_active_case_v1", None)
        st.caption("경정청구 요청 건을 찾지 못했습니다.")
        return

    st.markdown("#### 인증 진행")
    _render_claim_auth_stage(current_case)
    st.markdown("#### 자료수집 진행률")
    (
        progress_percent,
        progress_text,
        ready_count,
        target_count,
        progress_verified,
    ) = _claim_collection_progress_from_repository(
        repository,
        case_id,
    )
    st.progress(progress_percent, text=progress_text)
    if not progress_verified:
        st.caption(
            "Supabase의 실제 수집 상태가 확인되면 진행률을 표시합니다."
        )

    if job_snapshot:
        job_status = str(job_snapshot.get("status", "") or "")
        summary = dict(job_snapshot.get("summary") or {})
        if job_status == "awaiting_business_selection":
            business_choices = [
                dict(choice)
                for choice in summary.get("business_choices", [])
                if isinstance(choice, dict)
                and _clean(choice.get("token"))
                and _clean(choice.get("label"))
            ]
            st.info(
                "홈택스에서 여러 사업자가 확인됐습니다. "
                "근로복지공단 자료를 수집할 사업자를 선택해 주세요."
            )
            if not business_choices:
                st.warning(
                    "선택 가능한 사업자정보를 확인하지 못했습니다. "
                    "새 인증 요청을 시작해 주세요."
                )
                return
            labels = {
                str(choice["token"]): str(choice["label"])
                for choice in business_choices
            }
            selected_business_token = st.selectbox(
                "사업자 선택",
                list(labels),
                format_func=lambda token: labels.get(token, "사업자"),
                key=f"claim_business_number_{case_id}",
            )
            if st.button(
                "선택 사업자로 자료수집 계속",
                type="primary",
                use_container_width=True,
                key=f"claim_business_number_continue_{case_id}",
            ):
                if _select_claim_business_number(
                    user_id,
                    case_id,
                    selected_business_token,
                ):
                    st.toast(
                        "선택한 사업자로 근로복지공단 자료수집을 시작합니다."
                    )
                    st.rerun(scope="app")
                else:
                    st.warning(
                        "임시 인증정보가 만료됐습니다. "
                        "새 인증 요청을 시작해 주세요."
                    )
            return
        if job_status == "awaiting_management_selection":
            management_numbers = [
                str(number)
                for number in summary.get("management_numbers", [])
                if str(number).strip()
            ]
            st.info(
                "여러 사업장이 확인됐습니다. 사업장요율을 수집할 "
                "사업장관리번호를 선택해 주세요."
            )
            if not management_numbers:
                st.warning(
                    "선택 가능한 사업장관리번호를 확인하지 못했습니다. "
                    "새 인증 요청을 시작해 주세요."
                )
                return
            selected_management_number = st.selectbox(
                "사업장관리번호",
                management_numbers,
                key=f"claim_management_number_{case_id}",
            )
            if st.button(
                "선택 사업장 요율 수집 계속",
                type="primary",
                use_container_width=True,
                key=f"claim_management_number_continue_{case_id}",
            ):
                if _select_claim_management_number(
                    user_id,
                    case_id,
                    selected_management_number,
                ):
                    st.toast("선택한 사업장의 요율 수집을 시작합니다.")
                    st.rerun(scope="app")
                else:
                    st.warning(
                        "임시 인증정보가 만료됐습니다. "
                        "새 인증 요청을 시작해 주세요."
                    )
            return
        verified_complete = bool(
            progress_verified
            and target_count > 0
            and ready_count == target_count
        )
        if job_status == "complete" and verified_complete:
            skipped_count = int(summary.get("skipped_count", 0) or 0)
            completion_message = (
                f"현재 연결된 서류 {ready_count}건 수집이 "
                "완료되었습니다."
                + (
                    f" 현재 인증방식 또는 필수 식별정보 제한으로 "
                    f"{skipped_count}건은 제외했습니다."
                    if skipped_count
                    else ""
                )
            )
            notified_key = f"_claim_collection_notified_{case_id}"
            if not st.session_state.get(notified_key):
                st.session_state[notified_key] = True
                st.session_state["_claim_collection_completed_v1"] = (
                    completion_message
                )
                st.session_state["_claim_flash_v1"] = completion_message
                if st.session_state.get("_claim_active_case_v1") == case_id:
                    st.session_state.pop("_claim_active_case_v1", None)
                st.rerun(scope="app")
            st.success(
                f"{completion_message} ‘수집결과’에서 확인해 주세요."
            )
            return
        if job_status == "complete" and not verified_complete:
            st.warning(
                "작업 종료 신호는 받았지만 Supabase의 실제 수집 자료가 "
                "모두 확인되지 않아 완료로 표시하지 않았습니다."
            )
            return
        if job_status == "expired":
            if st.session_state.get("_claim_active_case_v1") == case_id:
                st.session_state.pop("_claim_active_case_v1", None)
            st.warning(str(job_snapshot.get("safe_message", "") or "인증정보가 만료되었습니다."))
            return
        retry_state = _render_claim_collection_retry_action(
            user_id,
            current_case,
            job_snapshot,
            provider_ready=provider_ready,
            key_prefix="claim_monitor_collection_retry",
        )
        if retry_state in {
            "retryable",
            "running",
            "provider_unavailable",
            "reauth_required",
        }:
            return
        manual_check = st.button(
            "지금 인증 상태 확인",
            use_container_width=True,
            key=f"claim_auto_check_{case_id}",
        )
        if manual_check:
            if _retry_or_wake_claim_job(user_id, case_id):
                st.toast("인증 상태를 바로 다시 확인합니다.", icon="🔄")
            else:
                st.warning("임시 인증정보가 만료되어 새 인증 요청이 필요합니다.")
        if job_status in {"paused", "collection_partial"}:
            st.warning(
                str(job_snapshot.get("safe_message", "") or "")
                or "자동 확인을 잠시 멈췄습니다."
            )
        else:
            remaining = max(
                0,
                int(float(job_snapshot.get("expires_at", 0) or 0) - time.time()),
            )
            st.caption(
                "화면을 이동하거나 닫아도 Railway 서버가 처음 30초는 약 1초마다, "
                "다음 60초는 약 3초마다, 이후에는 약 10초마다 자동으로 "
                f"확인합니다. 임시 인증정보는 {remaining // 60}분 "
                f"{remaining % 60}초 뒤 자동 삭제됩니다."
            )
        return

    if (
        progress_verified
        and target_count > 0
        and ready_count == target_count
        and str(current_case.get("overall_status", "") or "")
        in {"ready", "collected"}
    ):
        if st.session_state.get("_claim_active_case_v1") == case_id:
            st.session_state.pop("_claim_active_case_v1", None)
        st.success(
            "현재 연결된 서류 수집이 완료되었습니다. "
            "‘수집결과’에서 확인해 주세요."
        )
        return
    if st.session_state.get("_claim_active_case_v1") == case_id:
        st.session_state.pop("_claim_active_case_v1", None)
    st.caption(
        "이 요청의 임시 인증정보가 없거나 Railway가 재시작되었습니다. "
        "개인정보 보호를 위해 복구하지 않으므로 새 인증 요청을 시작해 주세요."
    )


def _render_status_tab(
    user_id: str,
    repository: ClaimRepository | None,
    provider_ready: bool,
) -> None:
    if repository is None:
        st.info("전용 저장소 설치 후 인증 진행상황이 표시됩니다.")
        return
    try:
        cases = repository.list_cases()
    except ClaimRepositoryError as exc:
        st.error(str(exc))
        return
    if not cases:
        st.info("아직 등록된 경정청구 요청이 없습니다.")
        return

    st.dataframe(
        _cases_dataframe(cases),
        use_container_width=True,
        hide_index=True,
    )
    labels = [_case_label(case) for case in cases]
    selected_label = st.selectbox(
        "완료 여부를 확인할 요청",
        labels,
        key="claim_status_case_selector_v1",
    )
    selected_case = cases[labels.index(selected_label)]
    case_id = str(selected_case.get("id", ""))
    job_snapshot = _claim_job_snapshot(user_id, case_id)
    if job_snapshot:
        if st.session_state.get("_claim_active_case_v1") == case_id:
            st.info("이 요청은 화면 상단의 파란 진행창에서 자동 확인 중입니다.")
        else:
            _render_auto_claim_monitor(
                user_id,
                case_id,
                repository,
                provider_ready,
            )
        return

    st.markdown("#### 인증 진행")
    _render_claim_auth_stage(selected_case)
    st.markdown("#### 자료수집 진행률")
    (
        progress_percent,
        progress_text,
        ready_count,
        target_count,
        progress_verified,
    ) = _claim_collection_progress_from_repository(
        repository,
        case_id,
    )
    st.progress(progress_percent, text=progress_text)
    if not progress_verified:
        st.caption(
            "Supabase의 실제 수집 상태가 확인되면 진행률을 표시합니다."
        )
    if (
        progress_verified
        and target_count > 0
        and ready_count == target_count
        and str(selected_case.get("overall_status", "") or "")
        in {"ready", "collected"}
    ):
        st.success(
            "현재 연결된 서류 수집이 완료되었습니다. "
            "‘수집결과’에서 확인해 주세요."
        )
    elif selected_case.get("business_type") == "corporation":
        st.caption("법인 공동인증 완료 상태는 인증 모듈 콜백으로 갱신됩니다.")
    else:
        st.caption(
            "이 요청은 현재 Railway 자동 작업에 연결되어 있지 않습니다. "
            "배포 전 요청이거나 인증 유효시간이 지난 건은 새로 요청해 주세요."
        )


def _render_results_tab(
    user_id: str,
    repository: ClaimRepository | None,
    provider_ready: bool,
) -> None:
    if repository is None:
        st.info("전용 저장소 설치 후 수집결과가 표시됩니다.")
        return
    try:
        cases = repository.list_cases()
    except ClaimRepositoryError as exc:
        st.error(str(exc))
        return
    if not cases:
        st.info("수집결과를 확인할 경정청구 요청이 없습니다.")
        return

    filter_cols = st.columns([1.4, 1, 1])
    with filter_cols[0]:
        search_text = st.text_input(
            "상호명 검색",
            placeholder="성명 또는 상호명을 입력하세요",
            key="claim_result_search_v1",
        )
    with filter_cols[1]:
        business_filter = st.selectbox(
            "사업자 구분",
            ["전체", "개인사업자", "법인사업자"],
            key="claim_result_business_filter_v1",
        )
    with filter_cols[2]:
        status_filter = st.selectbox(
            "진행 상태",
            [
                "전체",
                "인증 대기",
                "인증 완료",
                "일부 수집 실패",
                "수집 완료",
                "실패",
            ],
            key="claim_result_status_filter_v1",
        )

    filtered_cases = []
    search_key = search_text.strip().lower()
    for case in cases:
        company_name = _clean(case.get("company_name"))
        business_type = str(case.get("business_type", ""))
        overall_status = str(case.get("overall_status", ""))
        if search_key and search_key not in company_name.lower():
            continue
        if (
            business_filter == "개인사업자"
            and business_type != "individual"
        ):
            continue
        if (
            business_filter == "법인사업자"
            and business_type != "corporation"
        ):
            continue
        status_group = (
            "수집 완료"
            if overall_status in {"collected", "ready"}
            else "일부 수집 실패"
            if (
                overall_status == "auth_complete_collection_pending"
                and bool(case.get("last_safe_error_code"))
            )
            else "인증 완료"
            if overall_status
            in {
                "auth_complete",
                "auth_complete_collection_pending",
                "collection_queued",
                "collecting",
            }
            else "실패"
            if overall_status in {"failed", "auth_partial"}
            else "인증 대기"
        )
        if status_filter != "전체" and status_group != status_filter:
            continue
        filtered_cases.append(case)

    st.caption(f"TOTAL {len(filtered_cases):,}건")
    if not filtered_cases:
        st.info("조건에 맞는 경정청구 요청이 없습니다.")
        return

    st.dataframe(
        _cases_dataframe(filtered_cases),
        use_container_width=True,
        hide_index=True,
    )
    labels = [_case_label(case) for case in filtered_cases]
    selected_label = st.selectbox(
        "서류를 확인할 고객",
        labels,
        key="claim_result_case_selector_v1",
    )
    selected_case = filtered_cases[labels.index(selected_label)]
    st.markdown(
        f"#### {html.escape(_clean(selected_case.get('company_name')))} 자료 목록"
    )
    metric_cols = st.columns(3)
    with metric_cols[0]:
        st.metric("홈택스", _source_status(selected_case.get("hometax_status")))
    with metric_cols[1]:
        st.metric("근로복지공단", _source_status(selected_case.get("comwel_status")))
    with metric_cols[2]:
        st.metric("전체 상태", _source_status(selected_case.get("overall_status")))

    selected_case_id = str(selected_case.get("id", "") or "")
    selected_job_snapshot = _claim_job_snapshot(user_id, selected_case_id)
    _render_claim_collection_retry_action(
        user_id,
        selected_case,
        selected_job_snapshot,
        provider_ready=provider_ready,
        key_prefix="claim_result_collection_retry",
    )

    try:
        documents = repository.list_documents(selected_case_id)
    except ClaimRepositoryError as exc:
        st.error(str(exc))
        return
    if not documents:
        st.info("이 요청에 등록된 수집 항목이 없습니다.")
        return
    management_document = next(
        (
            document
            for document in documents
            if str(document.get("document_code", ""))
            == "comwel_management_number_list"
            and str(document.get("status", "")) == "ready"
        ),
        {},
    )
    management_facts = (
        management_document.get("facts")
        if isinstance(management_document, dict)
        else {}
    )
    management_numbers = (
        management_facts.get("management_numbers")
        if isinstance(management_facts, dict)
        else []
    )
    multiple_management_numbers = (
        isinstance(management_numbers, list)
        and len(management_numbers) > 1
    )
    no_management_workplaces = bool(
        isinstance(management_document, dict)
        and str(management_document.get("status", "")) == "ready"
        and isinstance(management_facts, dict)
        and (
            str(management_facts.get("record_count", "")) == "0"
            or management_facts.get("management_numbers") == []
        )
    )
    source_filter = st.segmented_control(
        "기관",
        ["전체", "홈택스", "근로복지공단"],
        default="전체",
        key="claim_result_source_filter_v1",
    )
    rows = []
    for document in documents:
        source = str(document.get("source", ""))
        document_code = str(document.get("document_code", ""))
        status = str(document.get("status", ""))
        source_label = (
            "홈택스"
            if source in {"hometax", "홈택스"}
            else "근로복지공단"
        )
        if source_filter not in {None, "전체", source_label}:
            continue
        rows.append(
            {
                "자료명": _clean(document.get("document_name")),
                "기관": source_label,
                "연도": document.get("period_year") or "-",
                "상태": _claim_result_document_status(
                    document,
                    selected_case,
                    multiple_management_numbers=(
                        multiple_management_numbers
                    ),
                    no_management_workplaces=no_management_workplaces,
                ),
                "수집일": _clean(document.get("collected_at"))[:10] or "-",
                "출력": (
                    "준비"
                    if _claim_document_is_downloadable(document)
                    else "보관기간 만료"
                    if status == "ready"
                    and document.get("storage_path")
                    else "-"
                ),
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )
    ready_documents = [
        document
        for document in documents
        if _claim_document_is_downloadable(document)
        and (
            source_filter in {None, "전체"}
            or (
                source_filter == "홈택스"
                and str(document.get("source", "")) in {"hometax", "홈택스"}
            )
            or (
                source_filter == "근로복지공단"
                and str(document.get("source", ""))
                not in {"hometax", "홈택스"}
            )
        )
    ]
    if ready_documents:
        st.markdown("#### 수집자료 다운로드")
        st.caption(
            f"현재 선택한 기관의 수집 완료 자료 {len(ready_documents):,}건입니다. "
            "링크는 현재 로그인 계정에서 생성되며 1분간 유효합니다. "
            "민감한 자료이므로 링크를 공유하지 마세요."
        )
        selected_case_id = str(selected_case.get("id", ""))
        for document in ready_documents:
            document_id = str(document.get("id", ""))
            source = str(document.get("source", ""))
            source_label = (
                "홈택스"
                if source in {"hometax", "홈택스"}
                else "근로복지공단"
            )
            content_type = str(
                document.get("content_type", "") or ""
            ).lower()
            file_label = (
                "PDF"
                if content_type == "application/pdf"
                else "엑셀"
                if "spreadsheet" in content_type or "excel" in content_type
                else "JSON"
                if content_type == "application/json"
                else "파일"
            )
            size_bytes = int(document.get("size_bytes") or 0)
            size_label = (
                f"{size_bytes / (1024 * 1024):.1f}MB"
                if size_bytes >= 1024 * 1024
                else f"{max(1, round(size_bytes / 1024)):,}KB"
                if size_bytes
                else "크기 미확인"
            )
            document_name = (
                _clean(document.get("document_name"))
                or _clean(document.get("document_code"))
                or "수집자료"
            )
            year_label = str(document.get("period_year") or "공통")
            state_key = (
                f"_claim_download_link_{selected_case_id}_{document_id}"
            )
            cached_link = st.session_state.get(state_key)
            if not (
                isinstance(cached_link, dict)
                and _clean(cached_link.get("url"))
                and float(cached_link.get("expires_at") or 0) > time.time()
            ):
                st.session_state.pop(state_key, None)
                cached_link = None

            with st.container(border=True):
                info_col, action_col = st.columns([4.6, 1.4])
                with info_col:
                    st.markdown(f"**{html.escape(document_name)}**")
                    st.caption(
                        f"{source_label} · {year_label} · "
                        f"{file_label} · {size_label} · "
                        f"수집일 {_clean(document.get('collected_at'))[:10] or '-'}"
                    )
                with action_col:
                    if cached_link:
                        st.link_button(
                            "파일 다운로드",
                            str(cached_link["url"]),
                            use_container_width=True,
                        )
                        if st.button(
                            "링크 다시 생성",
                            key=(
                                "claim_download_refresh_"
                                f"{selected_case_id}_{document_id}"
                            ),
                            use_container_width=True,
                        ):
                            st.session_state.pop(state_key, None)
                            st.rerun()
                    elif st.button(
                        "다운로드 준비",
                        key=(
                            "claim_download_prepare_"
                            f"{selected_case_id}_{document_id}"
                        ),
                        use_container_width=True,
                        type="primary",
                    ):
                        try:
                            download_url = repository.document_download_url(
                                selected_case_id,
                                document_id,
                            )
                            st.session_state[state_key] = {
                                "url": download_url,
                                "expires_at": (
                                    time.time()
                                    + CLAIM_DOWNLOAD_URL_TTL_SECONDS
                                    - 5
                                ),
                            }
                            st.rerun()
                        except ClaimRepositoryError as exc:
                            st.error(str(exc))
    else:
        st.info(
            "현재 선택한 기관에는 다운로드 가능한 수집 완료 자료가 없습니다. "
            "자료수집이 끝나면 이곳에 다운로드 버튼이 표시됩니다."
        )
    st.caption(
        "현재 자동수집 지원: 홈택스 사업자정보 조회·사업자등록증명원·"
        "국세납세증명서·종합소득세 신고도움·종합소득세 신고서·"
        "폐업사실증명, 근로복지공단 보수총액·사업장관리번호·사업장요율. "
        "환급금은 세무대리인 공동인증서 API가 필요해 별도 연동 예정입니다."
    )


def _render_catalog_tab() -> None:
    rows = [
        {
            "자료명": spec.name,
            "기관": spec.source,
            "기간": spec.period,
            "자동수집": (
                "지원"
                if spec.code in AUTOMATIC_COLLECTION_CODES
                else "연동 예정"
            ),
            "설명": spec.description,
        }
        for spec in DOCUMENT_SPECS
    ]
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )
    st.warning(
        "기관 또는 중계 API가 실제로 제공하는 조회 기간이 7개년보다 짧으면 "
        "가능한 연도만 수집하고, 누락 연도를 결과 화면에 명확히 표시합니다."
    )


@st.dialog("자료수집 완료")
def _show_collection_complete_dialog(message: str) -> None:
    st.success(message)
    st.caption("수집된 문서는 ‘수집결과’ 탭에서 확인할 수 있습니다.")
    if st.button(
        "확인",
        use_container_width=True,
        type="primary",
        key="claim_collection_complete_dialog_close",
    ):
        st.rerun()


def render_claim_correction_center(
    user_id: str,
    user_name: str = "",
) -> None:
    repository, repository_message = _repository(user_id)
    readiness = provider_readiness()
    _render_intro(
        repository is not None,
        repository_message,
        readiness,
    )
    flash = st.session_state.pop("_claim_flash_v1", "")
    if flash:
        st.success(flash)
    completion_message = st.session_state.pop(
        "_claim_collection_completed_v1",
        "",
    )
    if completion_message:
        _show_collection_complete_dialog(str(completion_message))

    active_case_id = str(
        st.session_state.get("_claim_active_case_v1", "") or ""
    )
    if repository is not None and active_case_id:
        st.markdown("### 인증 및 자료수집 진행")
        _render_auto_claim_monitor(
            user_id,
            active_case_id,
            repository,
            bool(readiness.get("simple_auth_ready")),
        )

    request_tab, status_tab, result_tab, catalog_tab = st.tabs(
        ["인증 요청", "진행상황", "수집결과", "수집 항목"]
    )
    with request_tab:
        _render_request_tab(
            user_id,
            user_name,
            repository,
            readiness,
        )
    with status_tab:
        _render_status_tab(
            user_id,
            repository,
            bool(readiness.get("simple_auth_ready")),
        )
    with result_tab:
        _render_results_tab(
            user_id,
            repository,
            bool(readiness.get("simple_auth_ready")),
        )
    with catalog_tab:
        _render_catalog_tab()
