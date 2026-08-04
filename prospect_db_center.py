from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
import re
import secrets
import time

import pandas as pd
import streamlit as st
from runtime_error_log import safe_public_error

import company_sales_assignment as sales_assignments
import sales_outreach
import sales_outreach_repository
import localdata_contact_client
from korea_regions import (
    ALL_DISTRICTS,
    ALL_PROVINCES,
    district_label,
    district_options,
    province_options,
)
from licensed_business_repository import table_status as license_table_status
from licensed_business_sync import sync_services as sync_license_services
from contact_enrichment import api_statuses, enrich_company, test_connections
from contact_matching import is_mobile_phone, normalize_phone
from prospect_collection_service import (
    collect_contactable_growth_companies,
    collect_recent_opening_companies,
)
from public_data_api import (
    NPS_BASE_URL,
    REGION_CODES,
    fetch_nps_workplaces,
    service_key_status,
    test_nps_connection,
)
from prospect_db_repository import (
    company_contact_is_suppressed,
    contact_table_status,
    legacy_phone_contact_hash,
    legacy_phone_contact_is_suppressed,
    list_contacts_for_prospects,
    list_prospects,
    list_search_history,
    prospect_table_status,
    remove_existing_customers,
    remove_existing_prospects,
    save_prospect_memo,
    save_search_history,
    save_sales_analysis,
    save_prospect_contacts,
    save_assigned_prospects,
    save_prospects,
    search_history_table_status,
)
from sales_intelligence import analyze_sales_candidate, merge_analysis


BASE_DIR = Path(__file__).resolve().parent
STOCK_COMPANY_MARKERS = ("주식회사", "(주)", "㈜", "（주）")
EXCLUDED_LEGAL_MARKERS = (
    "농업회사법인",
    "유한회사",
    "합자회사",
    "합명회사",
    "영농조합법인",
    "사단법인",
    "재단법인",
)
OTHER_LEGAL_ENTITY_MARKERS = (
    "의료법인",
    "사회복지법인",
    "학교법인",
    "법무법인",
    "세무법인",
    "회계법인",
    "특허법인",
    "협동조합",
)
BUSINESS_TYPE_OPTIONS = {
    "주식회사": "stock",
    "개인사업자 후보": "individual",
    "전체": "all",
}
INDUSTRY_FILTER_OPTIONS = (
    "병원·의원",
    "음식점",
    "서비스업",
    "도소매업",
    "제조업",
    "건설업",
    "기타",
)
BUSINESS_TYPE_LABELS = {
    "stock": "주식회사",
    "individual": "개인사업자 후보",
    "all": "전체",
}
GROWTH_BASIS_LABELS = {
    "combined": "통합 고용 증가 신호",
    "none": "고용 증가 필터 미사용",
}
DATA_SOURCE_OPTIONS = {
    "통합 (국민연금 월별 + 근로복지공단 연간)": "combined",
    "국민연금 월별 (10명 이상)": "nps_monthly",
    "근로복지공단 연간 (1명 이상)": "comwel_annual",
}
DATA_SOURCE_LABELS = {
    value: label for label, value in DATA_SOURCE_OPTIONS.items()
}
CONTACT_CHANNEL_OPTIONS = {
    "휴대전화": "mobile_phone",
    "일반전화": "landline_phone",
    "이메일": "email",
    "인스타그램": "instagram",
}
CONTACT_CHANNEL_LABELS = {
    value: label for label, value in CONTACT_CHANNEL_OPTIONS.items()
}
DISCOVERY_TYPE_OPTIONS = {
    "고용증가기업": "growth",
    "신규개업기업": "recent_opening",
}
DISCOVERY_TYPE_LABELS = {
    value: label for label, value in DISCOVERY_TYPE_OPTIONS.items()
}
PROSPECT_RESULT_PAGE_SIZE_OPTIONS = (25, 50, 100)
_PROSPECT_SAVE_FLASH_KEY = "_prospect_save_flash_v989"
_OUTREACH_REQUEST_KEY = "_saved_prospect_outreach_request_v1040"
_OUTREACH_RESULT_KEY = "_saved_prospect_outreach_result_v1040"
_OUTREACH_ATTEMPTS_KEY = "_saved_prospect_outreach_attempts_v1040"
OUTREACH_ALLOWED_ASSIGNMENT_STATUSES = frozenset(
    {"assigned", "pending_contact", "contacted", "consulting", "follow_up"}
)
SAVED_PROSPECT_VISIBLE_COLUMNS = (
    "업체명",
    "사업자번호",
    "사업자유형",
    "연락처",
    "이메일",
    "인스타",
    "업종명",
    "가입자",
    "고용증가값",
    "이메일보내기",
    "문자보내기",
    "카카오톡보내기",
)
OUTREACH_COLUMN_CHANNELS = {
    "이메일보내기": "email",
    "문자보내기": "sms",
    "카카오톡보내기": "kakao",
}
OUTREACH_CHANNEL_LABELS = {
    "email": "이메일",
    "sms": "문자",
    "kakao": "카카오톡",
}
OUTREACH_HISTORY_STATUS_LABELS = {
    "reserved": "예약됨",
    "dispatching": "발송 처리 중",
    "provider_accepted": "공급자 접수",
    "provider_rejected": "공급자 미접수",
    "delivery_unknown": "접수 여부 확인 필요",
    "confirmed_not_sent": "관리자 미발송 확인",
    "cancelled_dnc": "수신거부로 취소",
    "cancelled_changed": "대상 변경으로 취소",
    "cancelled_stale": "예약 만료로 취소",
}
MEMBER_PROSPECT_TARGET_COUNT = 30
MEMBER_REQUIRED_CONTACT_LABELS = ("휴대전화", "일반전화")
MEMBER_OPTIONAL_CONTACT_LABELS = ("이메일", "인스타그램")

CONTACT_METHOD_OPTIONS = (
    "전화",
    "문자",
    "카카오톡",
    "상담",
)
CONTACT_RESULT_OPTIONS = (
    "부재중",
    "연결됨",
    "문자발송",
    "카카오톡 발송",
    "상담예약",
    "관심없음",
    "재연락 요청",
    "번호오류",
    "기존거래처",
    "계약진행",
    "계약완료",
)


MOBILE_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:(?:\+?82)[\s.\-]?(?:\(0\)[\s.\-]?)?|0)"
    r"(?:10|11|16|17|18|19)[\s.\-]?\d{3,4}[\s.\-]?\d{4}(?!\d)"
)


def _show_pending_prospect_save_notices() -> None:
    """Render save feedback once after the result table refreshes."""
    pending_notices = st.session_state.pop(_PROSPECT_SAVE_FLASH_KEY, [])
    if not isinstance(pending_notices, list):
        return
    for notice in pending_notices:
        if not isinstance(notice, dict):
            continue
        message = str(notice.get("message") or "").strip()
        if not message:
            continue
        level = str(notice.get("level") or "info").strip().lower()
        renderer = getattr(st, level, st.info)
        renderer(message)


def _effective_prospect_target_count(
    requested_count: object,
    *,
    is_admin_user: bool,
) -> int:
    """Enforce the ordinary-user result cap on the server-rendered path."""
    if not is_admin_user:
        return MEMBER_PROSPECT_TARGET_COUNT
    try:
        parsed_count = int(requested_count)
    except (TypeError, ValueError):
        parsed_count = 100
    return min(500, max(1, parsed_count))


def _effective_prospect_mobile_visibility(
    can_view_mobile: bool,
    *,
    is_admin_user: bool,
) -> bool:
    """Preserve the authenticated mobile-number permission without widening it."""
    del is_admin_user
    return bool(can_view_mobile)


def _limit_prospect_result_for_role(
    result: dict,
    *,
    is_admin_user: bool,
) -> dict:
    """Keep cached or unexpectedly oversized member results within 30 rows."""
    copied = dict(result or {})
    if is_admin_user:
        return copied
    visible_items = list(copied.get("items") or [])[
        :MEMBER_PROSPECT_TARGET_COUNT
    ]
    copied["items"] = visible_items
    copied["found_count"] = len(visible_items)
    return copied


def _effective_contact_filter_labels(
    selected_labels: object,
    *,
    is_admin_user: bool,
) -> list[str]:
    """Keep both phone channels mandatory for ordinary users."""
    if isinstance(selected_labels, (list, tuple, set)):
        requested = [str(label) for label in selected_labels]
    elif selected_labels:
        requested = [str(selected_labels)]
    else:
        requested = []
    valid_requested = [
        label
        for label in CONTACT_CHANNEL_OPTIONS
        if label in requested
    ]
    if is_admin_user:
        return valid_requested
    return [
        *MEMBER_REQUIRED_CONTACT_LABELS,
        *(
            label
            for label in MEMBER_OPTIONAL_CONTACT_LABELS
            if label in valid_requested
        ),
    ]


def _assignment_session_id() -> str:
    """Return an opaque per-login-session identifier without PII."""
    key = "company_sales_assignment_session_v989"
    if key not in st.session_state:
        st.session_state[key] = secrets.token_urlsafe(24)
    return str(st.session_state[key])


@st.cache_data(ttl=60, max_entries=2, show_spinner=False)
def _assignment_feature_status() -> tuple[bool, str]:
    return sales_assignments.assignment_feature_ready()


def _release_expired_assignments_if_due(owner_user_id: str) -> None:
    key = "company_sales_assignment_expiry_checked_v989"
    last_checked = float(st.session_state.get(key, 0.0) or 0.0)
    if time.monotonic() - last_checked < 60:
        return
    st.session_state[key] = time.monotonic()
    sales_assignments.release_expired_assignments(
        owner_user_id,
        session_id=_assignment_session_id(),
    )


def _filter_assignment_search_result(
    result: dict,
    owner_user_id: str,
    *,
    is_admin_user: bool,
) -> dict:
    """Apply company-wide assignment visibility without claiming on browse."""
    copied = dict(result or {})
    items = list(copied.get("items") or [])
    stats = dict(copied.get("stats") or {})
    prior_assignment_excluded = int(
        stats.pop("assignment_blocked_excluded", 0) or 0
    )
    prior_own_excluded = int(stats.pop("already_my_db_excluded", 0) or 0)
    stats["saved_prospect_excluded"] = max(
        0,
        int(stats.get("saved_prospect_excluded") or 0)
        - prior_assignment_excluded
        - prior_own_excluded,
    )
    if not items:
        return copied

    ready, ready_message = _assignment_feature_status()
    if not ready:
        filtered, excluded = remove_existing_prospects(items)
        copied["items"] = filtered
        copied["found_count"] = len(filtered)
        stats["saved_prospect_excluded"] = int(
            stats.get("saved_prospect_excluded") or 0
        ) + excluded
        copied["stats"] = stats
        copied["assignment_warning"] = ready_message
        copied["assignment_feature_ready"] = False
        return copied

    _release_expired_assignments_if_due(owner_user_id)
    availability = sales_assignments.filter_company_availability(
        items,
        owner_user_id,
        is_admin_user=is_admin_user,
    )
    if not availability.get("ready"):
        # Fail closed for new claims, while preserving the legacy global
        # exclusion on the read-only result screen.
        filtered, excluded = remove_existing_prospects(items)
        copied["items"] = filtered
        copied["found_count"] = len(filtered)
        stats["saved_prospect_excluded"] = int(
            stats.get("saved_prospect_excluded") or 0
        ) + excluded
        copied["stats"] = stats
        copied["assignment_warning"] = availability.get("warning", "")
        copied["assignment_feature_ready"] = False
        return copied

    visible = list(availability.get("items") or [])
    excluded_count = int(availability.get("excluded_count") or 0)
    own_count = int(availability.get("own_count") or 0)
    copied["items"] = visible
    copied["found_count"] = len(visible)
    stats["saved_prospect_excluded"] = int(
        stats.get("saved_prospect_excluded") or 0
    ) + excluded_count + (0 if is_admin_user else own_count)
    stats["assignment_blocked_excluded"] = excluded_count
    stats["already_my_db_excluded"] = own_count
    copied["stats"] = stats
    copied["assignment_warning"] = availability.get("warning", "")
    copied["assignment_feature_ready"] = True

    if is_admin_user and visible:
        admin_result = sales_assignments.list_admin_assignments(
            owner_user_id,
            limit=1000,
        )
        if admin_result.get("ok"):
            assignment_by_uid = {
                str(row.get("company_uid") or ""): row
                for row in admin_result.get("assignments") or []
                if str(row.get("company_uid") or "")
            }
            for item in visible:
                assignment = assignment_by_uid.get(
                    str(item.get("company_uid") or "")
                )
                if not assignment:
                    item["담당자"] = "미배정"
                    item["배정상태"] = "미배정"
                    item["최근연락일"] = ""
                    continue
                item["담당자"] = (
                    assignment.get("assigned_user_name")
                    or assignment.get("assigned_user_id")
                    or "미배정"
                )
                item["배정상태"] = sales_assignments.assignment_status_label(
                    assignment.get("status")
                )
                item["최근연락일"] = str(
                    assignment.get("last_contacted_at") or ""
                ).replace("T", " ")[:16]

    record_views = getattr(sales_assignments, "record_company_views", None)
    if callable(record_views) and visible:
        view_result = record_views(
            owner_user_id,
            visible,
            session_id=_assignment_session_id(),
        )
        if not view_result.get("ok"):
            copied["assignment_view_warning"] = view_result.get(
                "message", ""
            )
    return copied


def _assignment_expiry_text(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "담당 확정"
    try:
        expires_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        remaining = int(
            (expires_at.astimezone(timezone.utc) - datetime.now(timezone.utc))
            .total_seconds()
        )
    except (TypeError, ValueError):
        return raw[:19].replace("T", " ")
    if remaining <= 0:
        return "만료 정리 대기"
    hours, remainder = divmod(remaining, 3600)
    minutes = remainder // 60
    return f"{hours}시간 {minutes}분"


def _redact_mobile_candidate(
    item: dict,
    can_view_mobile: bool,
) -> dict:
    copied = deepcopy(item)
    if can_view_mobile:
        return copied

    phone_value_keys = {
        "대표전화",
        "휴대전화",
        "일반전화",
        "phone",
        "mobile_phone",
        "landline_phone",
        "contact_value",
    }
    mobile_only_keys = {"휴대전화", "mobile_phone"}

    def _redact(value, key: str = ""):
        if isinstance(value, dict):
            return {
                str(child_key): _redact(child_value, str(child_key))
                for child_key, child_value in value.items()
            }
        if isinstance(value, list):
            return [_redact(child, key) for child in value]
        if isinstance(value, tuple):
            return tuple(_redact(child, key) for child in value)
        if key in mobile_only_keys:
            return ""
        if key in phone_value_keys and is_mobile_phone(value):
            return ""
        if isinstance(value, str):
            return MOBILE_PHONE_PATTERN.sub("[휴대전화 비공개]", value)
        return value

    redacted = _redact(copied)
    landline = normalize_phone(
        redacted.get("일반전화")
        or redacted.get("landline_phone")
        or ""
    )
    if is_mobile_phone(landline):
        landline = ""
    representative = normalize_phone(redacted.get("대표전화") or "")
    if is_mobile_phone(representative):
        representative = landline
    representative = representative or landline

    redacted["휴대전화"] = ""
    redacted["일반전화"] = landline
    redacted["대표전화"] = representative
    if "mobile_phone" in redacted:
        redacted["mobile_phone"] = ""
    if "landline_phone" in redacted:
        redacted["landline_phone"] = landline
    if redacted.get("전화유형") == "휴대전화":
        redacted["전화유형"] = "대표번호" if representative else ""
    return redacted


def _sanitize_search_result(
    result: dict,
    can_view_mobile: bool,
) -> dict:
    sanitized = deepcopy(result or {})
    if can_view_mobile:
        return sanitized

    items = [
        _redact_mobile_candidate(item, False)
        for item in list(sanitized.get("items") or [])
        if isinstance(item, dict)
    ]
    accessible_items = [
        item
        for item in items
        if (
            normalize_phone(item.get("일반전화"))
            or str(item.get("이메일") or "").strip()
            or str(item.get("인스타그램") or "").strip()
            or str(item.get("인스타그램URL") or "").strip()
        )
    ]
    sanitized["items"] = accessible_items
    sanitized["failures"] = [
        _redact_mobile_candidate(row, False)
        for row in list(sanitized.get("failures") or [])
        if isinstance(row, dict)
    ]
    sanitized["found_count"] = len(accessible_items)
    return sanitized


def _result_page_window(
    total_count: int,
    page_number: int,
    page_size: int,
) -> tuple[int, int, int, int]:
    """Return a clamped page number/count and its zero-based slice."""
    safe_total = max(0, int(total_count or 0))
    safe_size = max(1, int(page_size or 1))
    page_count = max(1, (safe_total + safe_size - 1) // safe_size)
    safe_page = min(max(1, int(page_number or 1)), page_count)
    start = (safe_page - 1) * safe_size
    return safe_page, page_count, start, min(safe_total, start + safe_size)


def _merge_result_page_selection(
    selected_keys: set[str],
    page_keys: set[str],
    checked_keys: set[str],
) -> set[str]:
    """Replace only the visible page's selections, preserving other pages."""
    normalized_selected = {
        str(key) for key in selected_keys if str(key or "").strip()
    }
    normalized_page = {
        str(key) for key in page_keys if str(key or "").strip()
    }
    normalized_checked = {
        str(key) for key in checked_keys if str(key or "").strip()
    }
    normalized_selected.difference_update(normalized_page)
    normalized_selected.update(normalized_checked & normalized_page)
    return normalized_selected


def _is_stock_company(value: object) -> bool:
    name = str(value or "").replace(" ", "")
    if any(marker in name for marker in EXCLUDED_LEGAL_MARKERS):
        return False
    return any(marker in name for marker in STOCK_COMPANY_MARKERS)


def _business_type_label(value: object) -> str:
    if _is_stock_company(value):
        return "주식회사"
    name = str(value or "").replace(" ", "")
    if any(
        marker in name
        for marker in EXCLUDED_LEGAL_MARKERS + OTHER_LEGAL_ENTITY_MARKERS
    ):
        return "기타 법인·단체"
    return "개인사업자 후보"


def _contact_status_label(value: object) -> str:
    status = str(value or "").upper()
    labels = {
        "FOUND": "대표전화 확인",
        "NOT_FOUND": "공개 대표전화 미확인",
        "ERROR": "조회 재시도 필요",
        "MATCHED": "공개 연락처 확인",
        "NO_MATCH": "공개 연락처 미확인",
        "PENDING": "수집 대기",
        "PROCESSING": "수집 중",
    }
    return labels.get(status, str(value or "분석 전"))


def _employment_value(value: object) -> int | str:
    if value in (None, ""):
        return "확인 불가"
    try:
        return int(value)
    except (TypeError, ValueError):
        return "확인 불가"


def _display_frame(
    items: list[dict],
    can_view_mobile: bool = False,
) -> pd.DataFrame:
    rows = []
    for raw_item in items:
        item = _redact_mobile_candidate(raw_item, can_view_mobile)
        row = {
            "선택": bool(item.get("선택", True)),
            "사업장명": item.get("사업장명", ""),
            "사업자유형": (
                item.get("사업자유형")
                or _business_type_label(item.get("사업장명"))
            ),
            "사업자등록번호": item.get("사업자등록번호", ""),
            "사업자번호상태": (
                item.get("사업자번호상태")
                or (
                    "확인"
                    if len(
                        re.sub(
                            r"[^0-9]",
                            "",
                            str(item.get("사업자등록번호") or ""),
                        )
                    )
                    == 10
                    else "미확인"
                )
            ),
            "지역": item.get("지역", ""),
            "주소": item.get("주소", ""),
            "대표전화": item.get("대표전화", ""),
            "휴대전화": item.get("휴대전화", ""),
            "일반전화": item.get("일반전화", ""),
            "전화유형": (
                item.get("전화유형")
                or (
                    "휴대전화"
                    if is_mobile_phone(item.get("대표전화", ""))
                    else (
                        "대표번호" if item.get("대표전화") else ""
                    )
                )
            ),
            "전화출처": item.get("전화출처", ""),
            "이메일": item.get("이메일", ""),
            "인스타그램": item.get("인스타그램", ""),
            "인스타그램URL": item.get("인스타그램URL", ""),
            "연락처조회일": str(item.get("연락처조회일") or "").replace(
                "T", " "
            )[:19],
            "연락처상태": _contact_status_label(
                item.get("연락처상태", "분석 전")
            ),
            "연락처조회이력": " · ".join(
                f"{row.get('stage', '')}:{row.get('status', '')}"
                for row in (item.get("연락처조회이력") or [])
            ),
            "업종분류": item.get("업종분류", ""),
            "업종명": item.get("업종명", ""),
            "자료생성년월": item.get("자료생성년월", ""),
            "가입자수": int(item.get("가입자수") or 0),
            "전월가입자수": _employment_value(
                item.get("전월가입자수")
            ),
            "전년가입자수": _employment_value(
                item.get("전년가입자수")
            ),
            "전월대비고용증가": _employment_value(
                item.get("전월대비고용증가")
            ),
            "전년대비고용증가": _employment_value(
                item.get("전년대비고용증가")
            ),
            "신규취득자수": _employment_value(
                item.get("신규취득자수")
            ),
            "상실가입자수": _employment_value(
                item.get("상실가입자수")
            ),
            "최근월순취득": _employment_value(
                item.get("순고용증가")
            ),
            "신규개업구분": item.get("신규개업구분", ""),
            "신규추정일": item.get("신규추정일", ""),
            "신규근거": item.get("신규근거", ""),
            "고용증가구분": item.get("고용증가구분", ""),
            "고용판정": item.get("고용증가판정", ""),
            "고용자료상태": item.get("고용자료상태", ""),
            "영업주제": item.get("영업주제", "분석 전"),
            "추천등급": item.get("추천등급", ""),
            "담당자": item.get("담당자", ""),
            "배정상태": item.get("배정상태", ""),
            "최근연락일": item.get("최근연락일", ""),
            "우선순위점수": int(item.get("우선순위점수") or 0),
            "추천사유": " · ".join(item.get("추천사유") or []),
            "초회전화스크립트": item.get("초회전화스크립트", ""),
            "source_key": item.get("source_key", ""),
        }
        rows.append(row)
    columns = [
        "선택",
        "사업장명",
        "사업자유형",
        "사업자등록번호",
        "사업자번호상태",
        "지역",
        "주소",
        "대표전화",
        "휴대전화",
        "일반전화",
        "전화유형",
        "전화출처",
        "이메일",
        "인스타그램",
        "인스타그램URL",
        "연락처조회일",
        "연락처상태",
        "연락처조회이력",
        "업종분류",
        "업종명",
        "자료생성년월",
        "가입자수",
        "전월가입자수",
        "전년가입자수",
        "전월대비고용증가",
        "전년대비고용증가",
        "신규취득자수",
        "상실가입자수",
        "최근월순취득",
        "신규개업구분",
        "신규추정일",
        "신규근거",
        "고용증가구분",
        "고용판정",
        "고용자료상태",
        "영업주제",
        "추천등급",
        "담당자",
        "배정상태",
        "최근연락일",
        "우선순위점수",
        "추천사유",
        "초회전화스크립트",
        "source_key",
    ]
    return pd.DataFrame(rows, columns=columns)


def _analyze_candidate_batch(
    items: list[dict],
    *,
    limit: int = 100,
) -> tuple[list[dict], list[dict]]:
    targets = items[: max(1, int(limit))]
    analysis_by_key: dict[str, dict] = {}
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {
            executor.submit(analyze_sales_candidate, item): item
            for item in targets
        }
        for future in as_completed(future_map):
            item = future_map[future]
            source_key = str(item.get("source_key") or "")
            try:
                analysis_by_key[source_key] = future.result()
            except Exception as exc:
                failures.append(
                    {
                        "사업장명": item.get("사업장명", ""),
                        "실패사유": f"{type(exc).__name__}: {exc}",
                    }
                )
    merged = [
        (
            merge_analysis(item, analysis_by_key[str(item.get("source_key") or "")])
            if str(item.get("source_key") or "") in analysis_by_key
            else item
        )
        for item in items
    ]
    return merged, failures


def _saved_sales_analysis(row: dict) -> dict:
    source_data = row.get("source_data") or {}
    if not isinstance(source_data, dict):
        return {}
    analysis = source_data.get("sales_intelligence_v971") or {}
    return analysis if isinstance(analysis, dict) else {}


def _saved_candidate_frame(
    rows: list[dict],
    contacts: list[dict],
    can_view_mobile: bool = False,
    *,
    canonical_contacts_only: bool = False,
) -> pd.DataFrame:
    phones_by_id: dict[str, list[dict]] = {}
    email_by_id: dict[str, dict] = {}
    instagram_by_id: dict[str, tuple[str, str]] = {}
    do_not_contact_ids: set[str] = set()
    for contact in contacts:
        prospect_id = str(contact.get("prospect_id") or "")
        if contact.get("do_not_contact") or contact.get("opt_out_at"):
            do_not_contact_ids.add(prospect_id)
            continue
        if str(contact.get("verification_status") or "").lower() == "rejected":
            continue
        if (
            contact.get("contact_type") == "phone"
            and (
                can_view_mobile
                or not is_mobile_phone(contact.get("contact_value", ""))
            )
        ):
            phones_by_id.setdefault(prospect_id, []).append(contact)
        if (
            contact.get("contact_type") == "email"
            and prospect_id not in email_by_id
        ):
            email_by_id[prospect_id] = contact
        if (
            contact.get("contact_type") == "instagram"
            and prospect_id not in instagram_by_id
        ):
            instagram_by_id[prospect_id] = (
                str(contact.get("contact_value") or ""),
                str(contact.get("source_url") or ""),
            )
    display: list[dict] = []
    for row in rows:
        prospect_id = str(row.get("id") or "")
        analysis = _saved_sales_analysis(row)
        source_data = (
            row.get("source_data")
            if isinstance(row.get("source_data"), dict)
            else {}
        )
        employment = (
            source_data.get("employment_growth")
            if isinstance(source_data.get("employment_growth"), dict)
            else {}
        )
        selected_growth = employment.get("selected_growth")
        phone_candidates = phones_by_id.get(prospect_id, [])
        phone_candidates.sort(
            key=lambda item: (
                is_mobile_phone(item.get("contact_value", "")),
                bool(item.get("is_primary")),
                int(item.get("confidence") or 0),
            ),
            reverse=True,
        )
        saved_phone = (
            str(phone_candidates[0].get("contact_value") or "")
            if phone_candidates
            else ""
        )
        canonical_mobile_available = bool(
            can_view_mobile and is_mobile_phone(saved_phone)
        )
        phone_record = phone_candidates[0] if phone_candidates else {}
        email_record = email_by_id.get(prospect_id, {})
        saved_email = str(email_record.get("contact_value") or "")
        canonical_email_available = bool(
            saved_email.strip()
        )
        analysis_phone = str(analysis.get("phone") or "")
        if canonical_contacts_only:
            analysis_phone = ""
        if not can_view_mobile and is_mobile_phone(analysis_phone):
            analysis_phone = ""
        preferred_phone = (
            saved_phone
            if (
                can_view_mobile
                and is_mobile_phone(saved_phone)
            )
            or not is_mobile_phone(analysis_phone)
            else analysis_phone
        ) or analysis_phone
        preferred_phone = normalize_phone(preferred_phone)
        mobile_phone = (
            preferred_phone
            if can_view_mobile and is_mobile_phone(preferred_phone)
            else ""
        )
        landline_phone = (
            preferred_phone
            if preferred_phone and not is_mobile_phone(preferred_phone)
            else ""
        )
        instagram, instagram_url = instagram_by_id.get(
            prospect_id,
            (
                str(analysis.get("instagram") or ""),
                str(analysis.get("instagram_url") or ""),
            ),
        )
        display.append(
            {
                "업체명": row.get("company_name", ""),
                "사업자번호": row.get("business_no", ""),
                "사업자유형": (
                    BUSINESS_TYPE_LABELS.get(
                        str(source_data.get("business_type") or "")
                    )
                )
                or _business_type_label(row.get("company_name")),
                "대표전화": preferred_phone,
                "휴대전화": mobile_phone,
                "일반전화": landline_phone,
                "전화유형": (
                    "휴대전화"
                    if is_mobile_phone(preferred_phone)
                    else ("대표번호" if preferred_phone else "")
                ),
                "전화출처": analysis.get("phone_source", ""),
                "연락처상태": _contact_status_label(
                    analysis.get("contact_status", "분석 전")
                ),
                "이메일": (
                    saved_email
                    or (
                        ""
                        if canonical_contacts_only
                        else analysis.get("email", "")
                    )
                ),
                "인스타그램": instagram,
                "인스타그램URL": instagram_url,
                "업종분류": (
                    source_data.get("industry_category")
                ),
                "업종명": row.get("industry_name", ""),
                "가입자": int(row.get("employee_count") or 0),
                "고용증가기준": GROWTH_BASIS_LABELS.get(
                    str(employment.get("basis") or ""),
                    "기존 저장자료",
                ),
                "고용증가값": _employment_value(selected_growth),
                "고용판정": str(employment.get("judgement") or ""),
                "_고용정렬": (
                    int(selected_growth)
                    if selected_growth not in (None, "")
                    else -1000000
                ),
                "영업주제": " · ".join(
                    analysis.get("sales_topics") or []
                ),
                "추천등급": analysis.get("recommendation_grade", ""),
                "초회전화스크립트": analysis.get(
                    "first_call_script",
                    "",
                ),
                "메모": str(row.get("memo") or ""),
                "배정상태": sales_assignments.assignment_status_label(
                    row.get("status")
                ),
                "배정만료": _assignment_expiry_text(
                    row.get("assignment_expires_at")
                ),
                "연락횟수": int(row.get("contact_count") or 0),
                "최근연락일": str(
                    row.get("last_contacted_at") or ""
                ).replace("T", " ")[:16],
                "다음연락일": str(
                    row.get("next_contact_at") or ""
                ).replace("T", " ")[:16],
                "_prospect_id": prospect_id,
                "_company_uid": str(row.get("company_uid") or ""),
                "_assignment_id": str(
                    row.get("_assignment_id")
                    or row.get("assignment_id")
                    or ""
                ),
                "_verified_business_type": str(
                    source_data.get("business_type") or ""
                ),
                "_assignment_status": str(row.get("status") or ""),
                "_assignment_expires_at": str(
                    row.get("assignment_expires_at") or ""
                ),
                "_do_not_contact": prospect_id in do_not_contact_ids,
                "_canonical_mobile_available": canonical_mobile_available,
                "_canonical_email_available": canonical_email_available,
                "_canonical_mobile_contact_id": str(
                    phone_record.get("id") or ""
                ),
                "_canonical_mobile_contact_updated_at": str(
                    phone_record.get("updated_at") or ""
                ),
                "_canonical_email_contact_id": str(
                    email_record.get("id") or ""
                ),
                "_canonical_email_contact_updated_at": str(
                    email_record.get("updated_at") or ""
                ),
            }
        )
    return pd.DataFrame(display)


def _saved_prospect_table_frame(
    frame: pd.DataFrame,
    *,
    can_view_mobile: bool,
) -> pd.DataFrame:
    """Return the compact saved-prospect view in the exact UI column order."""

    records: list[dict] = []
    for row in frame.to_dict("records"):
        blocked = (
            bool(row.get("_do_not_contact"))
            or str(row.get("_assignment_status") or "").strip().lower()
            not in OUTREACH_ALLOWED_ASSIGNMENT_STATUSES
            or not (
                str(row.get("_assignment_id") or "").strip()
                and str(row.get("_company_uid") or "").strip()
            )
        )
        email = str(row.get("이메일") or "").strip()
        mobile = normalize_phone(row.get("휴대전화") or "")
        instagram = str(
            row.get("인스타그램URL") or row.get("인스타그램") or ""
        ).strip()
        records.append(
            {
                "업체명": str(row.get("업체명") or ""),
                "사업자번호": str(row.get("사업자번호") or ""),
                "사업자유형": str(row.get("사업자유형") or ""),
                "연락처": normalize_phone(row.get("대표전화") or ""),
                "이메일": email,
                "인스타": instagram,
                "업종명": str(row.get("업종명") or ""),
                "가입자": int(row.get("가입자") or 0),
                "고용증가값": str(row.get("고용증가값") or ""),
                "이메일보내기": (
                    "📧"
                    if email
                    and bool(row.get("_canonical_email_available"))
                    and not blocked
                    else None
                ),
                "문자보내기": (
                    "💬"
                    if can_view_mobile
                    and is_mobile_phone(mobile)
                    and bool(row.get("_canonical_mobile_available"))
                    and not blocked
                    else None
                ),
                "카카오톡보내기": (
                    "🟡"
                    if can_view_mobile
                    and is_mobile_phone(mobile)
                    and bool(row.get("_canonical_mobile_available"))
                    and not blocked
                    else None
                ),
            }
        )
    return pd.DataFrame(records, columns=SAVED_PROSPECT_VISIBLE_COLUMNS)


def _outreach_action_rows(
    frame: pd.DataFrame,
    *,
    can_view_mobile: bool,
) -> list[dict]:
    """Keep callback state opaque; never place a recipient in widget state."""

    result: list[dict] = []
    for row in frame.to_dict("records"):
        available: list[str] = []
        sendable_assignment = bool(
            str(row.get("_assignment_id") or "").strip()
            and str(row.get("_company_uid") or "").strip()
            and str(row.get("_assignment_status") or "").strip().lower()
            in OUTREACH_ALLOWED_ASSIGNMENT_STATUSES
        )
        if (
            str(row.get("이메일") or "").strip()
            and bool(row.get("_canonical_email_available"))
            and not bool(row.get("_do_not_contact"))
            and sendable_assignment
        ):
            available.append("email")
        mobile = normalize_phone(row.get("휴대전화") or "")
        if (
            can_view_mobile
            and is_mobile_phone(mobile)
            and bool(row.get("_canonical_mobile_available"))
            and not bool(row.get("_do_not_contact"))
            and sendable_assignment
        ):
            available.extend(("sms", "kakao"))
        result.append(
            {
                "prospect_id": str(row.get("_prospect_id") or ""),
                "company_uid": str(row.get("_company_uid") or ""),
                "assignment_id": str(row.get("_assignment_id") or ""),
                "available_channels": tuple(available),
            }
        )
    return result


def _outreach_request_from_click(
    click: object,
    channel: str,
    action_rows: list[dict],
) -> dict:
    """Validate one ButtonColumn click without trusting browser-provided PII."""

    selected_channel = str(channel or "").strip().lower()
    if selected_channel not in OUTREACH_CHANNEL_LABELS:
        return {}
    try:
        if isinstance(click, dict):
            row_index = int(click.get("row"))
        else:
            row_index = int(getattr(click, "row"))
        if row_index < 0 or row_index >= len(action_rows):
            return {}
        selected = dict(action_rows[row_index])
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return {}
    if selected_channel not in tuple(
        selected.get("available_channels") or ()
    ):
        return {}
    return {
        "request_id": secrets.token_urlsafe(18),
        "channel": selected_channel,
        "prospect_id": str(selected.get("prospect_id") or ""),
        "company_uid": str(selected.get("company_uid") or ""),
        "assignment_id": str(selected.get("assignment_id") or ""),
    }


def _queue_outreach_from_button(
    click_key: str,
    channel: str,
    action_rows: list[dict],
) -> None:
    request = _outreach_request_from_click(
        st.session_state.get(click_key),
        channel,
        action_rows,
    )
    if request:
        st.session_state[_OUTREACH_REQUEST_KEY] = request


def _claim_outreach_attempt(state: object, request_id: object) -> bool:
    """Claim one browser-session attempt before making an external request."""

    if not hasattr(state, "get") or not hasattr(state, "__setitem__"):
        return False
    clean_id = str(request_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", clean_id):
        return False
    attempts = dict(state.get(_OUTREACH_ATTEMPTS_KEY) or {})
    if clean_id in attempts:
        return False
    attempts[clean_id] = "in_progress"
    if len(attempts) > 100:
        attempts = dict(list(attempts.items())[-100:])
    state[_OUTREACH_ATTEMPTS_KEY] = attempts
    return True


def _release_outreach_attempt(state: object, request_id: object) -> None:
    if not hasattr(state, "get") or not hasattr(state, "__setitem__"):
        return
    clean_id = str(request_id or "").strip()
    attempts = dict(state.get(_OUTREACH_ATTEMPTS_KEY) or {})
    attempts.pop(clean_id, None)
    state[_OUTREACH_ATTEMPTS_KEY] = attempts


def _mask_outreach_recipient(channel: str, recipient: object) -> str:
    value = str(recipient or "").strip()
    if channel == "email":
        local, separator, domain = value.partition("@")
        if not separator or not local or not domain:
            return "확인 불가"
        return f"{local[:1]}***@{domain}"
    digits = re.sub(r"\D", "", value)
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    if len(digits) == 11:
        return f"{digits[:3]}-****-{digits[-4:]}"
    if len(digits) == 10:
        return f"{digits[:3]}-***-{digits[-4:]}"
    return "확인 불가"


def _resolve_outreach_target(
    owner_user_id: str,
    request: dict,
    *,
    can_view_mobile: bool,
) -> dict:
    """Reload ownership, DNC state, and recipient immediately before send."""

    prospect_id = str(request.get("prospect_id") or "")
    company_uid = str(request.get("company_uid") or "")
    assignment_id = str(request.get("assignment_id") or "")
    channel = str(request.get("channel") or "").lower()
    if channel not in OUTREACH_CHANNEL_LABELS or not prospect_id:
        return {
            "ok": False,
            "code": "INVALID_TARGET",
            "message": "발송 대상을 다시 선택해 주세요.",
        }
    try:
        if assignment_id:
            assignment_result = sales_assignments.list_user_assignments(
                owner_user_id,
                statuses=sorted(OUTREACH_ALLOWED_ASSIGNMENT_STATUSES),
                limit=1000,
            )
            if not assignment_result.get("ok"):
                raise RuntimeError("assignment lookup failed")
            matching = next(
                (
                    dict(row)
                    for row in assignment_result.get("assignments") or []
                    if str(row.get("assignment_id") or row.get("id") or "")
                    == assignment_id
                    and str(row.get("company_uid") or "") == company_uid
                    and str(row.get("company_id") or "") == prospect_id
                    and str(row.get("status") or "").strip().lower()
                    in OUTREACH_ALLOWED_ASSIGNMENT_STATUSES
                    and not bool(row.get("permanently_excluded"))
                ),
                {},
            )
            if matching:
                matching["_assignment_id"] = assignment_id
                matching["id"] = prospect_id
                matching["memo"] = matching.get("own_memo") or ""
        else:
            matching = next(
                (
                    dict(row)
                    for row in list_prospects(owner_user_id, limit=1000)
                    if str(row.get("id") or "") == prospect_id
                ),
                {},
            )
        if not matching:
            return {
                "ok": False,
                "code": "TARGET_NOT_OWNED",
                "message": "현재 내게 배정된 업체가 아닙니다. 목록을 새로고침해 주세요.",
            }
        contact_ready, _ = contact_table_status()
        if not contact_ready:
            return {
                "ok": False,
                "code": "CONTACT_STATUS_UNAVAILABLE",
                "message": "수신거부 상태를 확인할 수 없어 발송을 중단했습니다.",
            }
        if company_contact_is_suppressed(company_uid, prospect_id=prospect_id):
            return {
                "ok": False,
                "code": "DO_NOT_CONTACT",
                "message": "수신거부가 등록된 업체라 발송할 수 없습니다.",
            }
        contacts = list_contacts_for_prospects(
            [prospect_id],
            owner_user_id,
        )
        current = _saved_candidate_frame(
            [matching],
            contacts,
            can_view_mobile=can_view_mobile,
            canonical_contacts_only=True,
        )
        if current.empty:
            raise RuntimeError("target frame missing")
        row = current.iloc[0].to_dict()
        if bool(row.get("_do_not_contact")):
            return {
                "ok": False,
                "code": "DO_NOT_CONTACT",
                "message": "수신거부가 등록된 업체라 발송할 수 없습니다.",
            }
        if channel == "email":
            if not bool(row.get("_canonical_email_available")):
                return {
                    "ok": False,
                    "code": "RECIPIENT_MISSING",
                    "message": "검증 가능한 현재 이메일 수신처가 없습니다.",
                }
            recipient = str(row.get("이메일") or "").strip()
            contact_id = str(
                row.get("_canonical_email_contact_id") or ""
            )
            contact_updated_at = str(
                row.get("_canonical_email_contact_updated_at") or ""
            )
        else:
            if not can_view_mobile:
                return {
                    "ok": False,
                    "code": "MOBILE_ACCESS_REQUIRED",
                    "message": "휴대전화 열람 권한이 없어 발송할 수 없습니다.",
                }
            if not bool(row.get("_canonical_mobile_available")):
                return {
                    "ok": False,
                    "code": "RECIPIENT_MISSING",
                    "message": "검증 가능한 현재 휴대전화 수신처가 없습니다.",
                }
            recipient = normalize_phone(row.get("휴대전화") or "")
            contact_id = str(
                row.get("_canonical_mobile_contact_id") or ""
            )
            contact_updated_at = str(
                row.get("_canonical_mobile_contact_updated_at") or ""
            )
        if not recipient or (
            channel in {"sms", "kakao"}
            and not is_mobile_phone(recipient)
        ):
            return {
                "ok": False,
                "code": "RECIPIENT_MISSING",
                "message": "이 채널로 발송할 수신처가 없습니다.",
            }
        if not contact_id or not contact_updated_at:
            return {
                "ok": False,
                "code": "CONTACT_VERSION_REQUIRED",
                "message": "현재 연락처 버전을 확인할 수 없어 발송을 중단했습니다.",
            }
        if channel in {"sms", "kakao"} and legacy_phone_contact_is_suppressed(
            str(row.get("_company_uid") or company_uid),
            recipient,
        ):
            return {
                "ok": False,
                "code": "DO_NOT_CONTACT",
                "message": "수신거부가 등록된 연락처라 발송할 수 없습니다.",
            }
        return {
            "ok": True,
            "code": "READY",
            "message": "발송 대상을 확인했습니다.",
            "channel": channel,
            "recipient": recipient,
            "company_name": str(row.get("업체명") or ""),
            "prospect_id": prospect_id,
            "company_uid": str(row.get("_company_uid") or company_uid),
            "assignment_id": str(row.get("_assignment_id") or assignment_id),
            "contact_id": contact_id,
            "contact_updated_at": contact_updated_at,
        }
    except Exception:
        return {
            "ok": False,
            "code": "TARGET_CHECK_FAILED",
            "message": "최신 배정·연락처 상태를 확인하지 못해 발송을 중단했습니다.",
        }


def _outreach_compliance_error(
    channel: str,
    subject: str,
    body: str,
    *,
    confirmed: bool,
) -> str:
    del channel, subject, body
    if not confirmed:
        return "수신 동의와 광고성 정보 발송 요건을 확인해 주세요."
    return ""


def _finish_outreach_dialog(level: str, message: str) -> None:
    """Close one composer through a single non-polling rerun path."""

    st.session_state[_OUTREACH_RESULT_KEY] = {
        "level": str(level or "info"),
        "message": str(message or ""),
    }
    st.session_state.pop(_OUTREACH_REQUEST_KEY, None)
    st.rerun()


@st.dialog("영업 메시지 보내기")
def _show_outreach_dialog(
    owner_user_id: str,
    request: dict,
    *,
    can_view_mobile: bool,
) -> None:
    channel = str(request.get("channel") or "").lower()
    channel_label = OUTREACH_CHANNEL_LABELS.get(channel, "메시지")
    target = _resolve_outreach_target(
        owner_user_id,
        request,
        can_view_mobile=can_view_mobile,
    )
    if not target.get("ok"):
        st.error(target.get("message") or "발송 대상을 확인하지 못했습니다.")
        return
    readiness = dict(sales_outreach.channel_readiness(channel) or {})
    st.markdown(f"**{channel_label} 작성**")
    st.caption(
        "수신처 "
        + _mask_outreach_recipient(channel, target.get("recipient"))
        + " · 한 업체에만 발송"
    )
    if channel == "kakao":
        st.info(
            "카카오 친구톡은 계약된 비즈메시지 발신 프로필의 채널 친구에게만 "
            "발송됩니다. 미친구·수신거부 대상에는 발송되지 않습니다."
        )
    elif channel == "sms":
        st.info(
            "담당자는 보낼 내용만 자유롭게 작성하면 됩니다. 시스템이 광고 표기, "
            "전송자 정보와 무료수신거부 안내를 앞뒤에 자동으로 붙입니다."
        )
    else:
        st.info(
            "제목과 본문은 담당자가 자유롭게 작성하면 됩니다. 시스템이 제목의 "
            "광고 표기와 본문의 전송자·수신거부 정보를 자동으로 붙입니다."
        )
    if not readiness.get("ready"):
        missing = [
            str(value)
            for value in readiness.get("missing_env_names") or []
            if str(value).strip()
        ]
        setup_message = "관리자 API 설정이 완료되지 않아 실제 발송은 차단됩니다."
        if missing:
            setup_message += " 필요한 설정: " + ", ".join(missing)
        st.warning(setup_message)

    request_id = re.sub(
        r"[^A-Za-z0-9]",
        "",
        str(request.get("request_id") or ""),
    )[:24] or "request"
    with st.form(f"saved_prospect_outreach_form_v1040_{request_id}"):
        subject = ""
        if channel in {"email", "sms"}:
            subject = st.text_input(
                "제목" if channel == "email" else "제목 (선택)",
                max_chars=200 if channel == "email" else 50,
            )
        body = st.text_area(
            "내용",
            height=260,
            max_chars={"email": 10_000, "sms": 2_000, "kakao": 1_000}.get(
                channel,
                2_000,
            ),
            placeholder="담당자가 보낼 내용을 직접 입력해 주세요.",
        )
        confirmed = st.checkbox(
            "이 대상의 광고 수신동의와 현재 수신거부 상태를 확인했습니다.",
            value=False,
        )
        send_col, cancel_col = st.columns(2)
        submitted = send_col.form_submit_button(
            "실제 발송",
            type="primary",
            use_container_width=True,
            disabled=not bool(readiness.get("ready")),
        )
        cancelled = cancel_col.form_submit_button(
            "취소",
            use_container_width=True,
        )
    if cancelled:
        st.session_state.pop(_OUTREACH_REQUEST_KEY, None)
        st.rerun()
    if not submitted:
        return
    compliance_error = _outreach_compliance_error(
        channel,
        subject,
        body,
        confirmed=confirmed,
    )
    if compliance_error:
        st.error(compliance_error)
        return
    validation = dict(
        sales_outreach.validate_message(
            channel,
            target.get("recipient"),
            subject,
            body,
        )
        or {}
    )
    if not validation.get("ok"):
        st.error(validation.get("message") or "입력한 발송 내용을 확인해 주세요.")
        return
    latest = _resolve_outreach_target(
        owner_user_id,
        request,
        can_view_mobile=can_view_mobile,
    )
    if (
        not latest.get("ok")
        or latest.get("recipient") != target.get("recipient")
        or latest.get("contact_id") != target.get("contact_id")
        or latest.get("contact_updated_at")
        != target.get("contact_updated_at")
    ):
        st.error("발송 직전 대상 상태가 변경되어 안전하게 중단했습니다. 목록에서 다시 선택해 주세요.")
        return
    request_id = str(request.get("request_id") or "")
    if not _claim_outreach_attempt(st.session_state, request_id):
        st.error(
            "같은 발송 요청이 이미 처리됐거나 처리 중입니다. 재발송하지 말고 "
            "공급자 발송내역과 CRM 연락이력을 먼저 확인해 주세요."
        )
        return
    try:
        content_hmac = sales_outreach_repository.message_fingerprint(
            channel,
            subject,
            body,
        )
        recipient_hmac = sales_outreach_repository.recipient_fingerprint(
            channel,
            latest.get("recipient"),
        )
        recipient_phone_hash = (
            legacy_phone_contact_hash(str(latest.get("recipient") or ""))
            if channel in {"sms", "kakao"}
            else ""
        )
    except Exception:
        _finish_outreach_dialog(
            "error",
            "발송 중복방지 보안설정을 확인할 수 없어 안전하게 중단했습니다.",
        )
        return

    reservation = sales_outreach_repository.reserve_outreach_attempt(
        owner_user_id,
        request_id,
        content_hmac,
        recipient_hmac,
        latest.get("assignment_id"),
        latest.get("prospect_id"),
        latest.get("company_uid"),
        latest.get("contact_id"),
        latest.get("contact_updated_at"),
        channel,
        recipient_phone_hash=recipient_phone_hash,
        consent_confirmed=confirmed,
    )
    if not reservation.get("ok") or not reservation.get("acquired"):
        _finish_outreach_dialog(
            "warning",
            str(
                reservation.get("message")
                or "발송 요청을 안전하게 예약하지 못해 중단했습니다."
            ),
        )
        return

    outbox_id = str(reservation.get("outbox_id") or "")
    reservation_token = str(reservation.get("reservation_token") or "")
    dispatch = sales_outreach_repository.begin_outreach_dispatch(
        owner_user_id,
        outbox_id,
        reservation_token,
        recipient_hmac=recipient_hmac,
        recipient_phone_hash=recipient_phone_hash,
    )
    if not dispatch.get("ok") or not dispatch.get("dispatch_started"):
        _finish_outreach_dialog(
            "warning",
            str(
                dispatch.get("message")
                or "발송 직전 안전 확인에 실패해 발송하지 않았습니다."
            ),
        )
        return

    try:
        result = dict(
            sales_outreach.send_outreach(
                channel,
                latest.get("recipient"),
                subject,
                body,
                request_id,
            )
            or {}
        )
    except Exception:
        result = {
            "ok": False,
            "code": "DELIVERY_UNKNOWN",
        }

    provider_code = str(result.get("code") or "").upper()
    if result.get("ok"):
        final_status = "provider_accepted"
    elif provider_code in {"DELIVERY_UNKNOWN", "PROVIDER_TIMEOUT"}:
        final_status = "delivery_unknown"
    else:
        final_status = "provider_rejected"
    finalized = sales_outreach_repository.finalize_outreach_attempt(
        owner_user_id,
        outbox_id,
        reservation_token,
        final_status,
        safe_result_code=provider_code,
    )
    if not finalized.get("ok"):
        _finish_outreach_dialog(
            "warning",
            (
                "공급자 처리 여부와 자동 이력 저장을 함께 확정하지 못했습니다. "
                "재발송하지 말고 관리자와 공급자 발송내역을 확인해 주세요."
            ),
        )
        return

    if final_status == "delivery_unknown":
        _finish_outreach_dialog(
            "warning",
            (
                "공급자 응답을 확정하지 못했습니다. 자동 재시도하지 않으며, "
                "재발송 전에 관리자가 공급자 발송내역을 확인해야 합니다."
            ),
        )
        return
    if final_status == "provider_rejected":
        _finish_outreach_dialog(
            "error",
            (
                "외부 발송 서비스가 요청을 접수하지 않았습니다. 설정을 확인한 뒤 "
                "목록에서 새 발송 요청을 만들어 주세요."
            ),
        )
        return

    if channel == "email":
        flash = (
            "이메일 발송 요청을 하이웍스가 접수했고 자동 발송 이력에 저장했습니다."
        )
        level = "success"
    else:
        contact_method, contact_result = {
            "sms": ("문자", "문자발송"),
            "kakao": ("카카오톡", "카카오톡 발송"),
        }[channel]
        crm_result = sales_assignments.record_contact(
            owner_user_id,
            latest.get("prospect_id"),
            latest.get("company_uid"),
            contact_method,
            contact_result,
            notes=f"OASIS에서 {channel_label} 발송 접수",
            session_id=_assignment_session_id(),
        )
        if crm_result.get("ok"):
            flash = (
                f"{channel_label} 발송 요청을 공급자가 접수했고 자동 발송 이력과 "
                "CRM 연락이력을 저장했습니다."
            )
            level = "success"
        else:
            flash = (
                f"{channel_label} 발송 요청과 자동 발송 이력은 저장됐지만 "
                "CRM 연락이력 저장에 실패했습니다. "
                "재발송하지 말고 관리자에게 알려주세요."
            )
            level = "warning"
    _finish_outreach_dialog(level, flash)


def _show_outreach_result_notice() -> None:
    result = st.session_state.pop(_OUTREACH_RESULT_KEY, None)
    if not isinstance(result, dict):
        return
    message = str(result.get("message") or "").strip()
    if not message:
        return
    renderer = getattr(st, str(result.get("level") or "info"), st.info)
    renderer(message)


@st.cache_data(ttl=300, max_entries=32, show_spinner=False)
def _excel_bytes(frame: pd.DataFrame, sheet_name: str) -> bytes:
    output = BytesIO()
    safe_sheet_name = str(sheet_name or "DB발굴")[:31]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name=safe_sheet_name, index=False)
    return output.getvalue()


def _render_search_history(owner_user_id: str) -> int:
    with st.expander("내 검색 이력", expanded=False):
        try:
            history_ok, history_message = search_history_table_status()
        except Exception as exc:
            history_ok = False
            history_message = f"검색 이력 연결 확인 실패: {exc}"
        if not history_ok:
            st.info(
                f"{history_message} 관리자 설정의 v9.8.4 SQL을 한 번 "
                "실행하면 이후 검색 구간이 사용자별로 자동 저장됩니다."
            )
            return 1
        try:
            rows = list_search_history(owner_user_id, limit=50)
        except Exception as exc:
            st.warning(safe_public_error(exc, "검색 이력을 불러오지 못했습니다."))
            return 1
        if not rows:
            st.caption(
                "아직 저장된 검색 이력이 없습니다. 검색을 완료하면 "
                "지역·업종·고용인원 조건이 자동 기록됩니다."
            )
            return 1
        display_rows = []
        for row in rows:
            categories = row.get("industry_categories") or []
            display_rows.append(
                {
                    "검색일시": str(row.get("searched_at") or "").replace(
                        "T", " "
                    )[:19],
                    "발굴 유형": DISCOVERY_TYPE_LABELS.get(
                        str(row.get("discovery_type") or "growth"),
                        "고용증가기업",
                    ),
                    "지역": " ".join(
                        value
                        for value in (
                            str(row.get("region") or ""),
                            str(row.get("district") or ""),
                        )
                        if value
                    ),
                    "사업자 유형": BUSINESS_TYPE_LABELS.get(
                        str(row.get("business_type") or ""),
                        row.get("business_type", ""),
                    ),
                    "연락처": " · ".join(
                        CONTACT_CHANNEL_LABELS.get(
                            str(value or ""),
                            str(value or ""),
                        )
                        for value in (row.get("contact_channels") or [])
                    ) or "전체",
                    "고용인원": (
                        f"{int(row.get('minimum_employees') or 1):,}"
                        f"~{int(row.get('maximum_employees') or 300):,}명"
                    ),
                    "업종 필터": (
                        " · ".join(categories) if categories else "전체 업종"
                    ),
                    "고용 기준": GROWTH_BASIS_LABELS.get(
                        str(row.get("growth_basis") or "combined"),
                        (
                            f"최근 {int(row.get('recent_months') or 6)}개월"
                            if row.get("discovery_type") == "recent_opening"
                            else str(row.get("growth_basis") or "")
                        ),
                    ),
                    "발굴": f"{int(row.get('found_count') or 0):,}건",
                    "검색시간": (
                        f"{float(row.get('elapsed_seconds') or 0):,.1f}초"
                    ),
                }
            )
        st.dataframe(
            pd.DataFrame(display_rows),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "같은 조건은 Supabase 사전계산 목록과 전용 인덱스로 "
            "다시 빠르게 조회됩니다."
        )
        return 1


def _contact_result_row(result: dict) -> dict:
    contacts = result.get("contacts") or []
    return {
        "업체명": result.get("company_name", ""),
        "판정": result.get("status", ""),
        "전화": " · ".join(
            row.get("contact_value", "")
            for row in contacts
            if row.get("contact_type") == "phone"
        ),
        "이메일": " · ".join(
            row.get("contact_value", "")
            for row in contacts
            if row.get("contact_type") == "email"
        ),
        "인스타그램": " · ".join(
            row.get("contact_value", "")
            for row in contacts
            if row.get("contact_type") == "instagram"
        ),
        "홈페이지": result.get("website_url", ""),
        "저장": int(result.get("saved_count") or 0),
    }


def _render_prospect_db_center_legacy(owner_user_id: str = "") -> None:
    st.markdown("## 영업후보DB")
    st.caption(
        "전국 주식회사를 찾고 연락처·고용변화를 분석해 "
        "전화할 이유와 초회 스크립트까지 준비합니다."
    )
    guide_cols = st.columns(3)
    guide_cols[0].info("① 전국 주식회사 수집")
    guide_cols[1].info("② 연락처·고용 자동분석")
    guide_cols[2].info("③ 후보 저장 후 초회전화")

    st.markdown("### 1. 데이터 연결 상태")
    st.info(
        "인증키와 응답구조를 확인한 뒤 전국 사업장을 "
        "최대 100건씩 미리보기로 수집합니다."
    )

    key_status = service_key_status()
    col1, col2, col3 = st.columns(3)
    col1.metric(
        "Railway 인증키",
        "등록됨" if key_status["configured"] else "미등록",
    )
    col2.metric("인증키 마스킹", key_status["masked"])
    col3.metric("대상 API", "국민연금 사업장")

    with st.expander("국민연금 연결 설정", expanded=False):
        st.code("DATA_GO_KR_SERVICE_KEY", language="text")
        st.caption(
            "호출주소: "
            f"{NPS_BASE_URL.replace('https://', '')}"
        )
        region_name = st.selectbox(
            "테스트 지역",
            list(REGION_CODES.keys()),
            key="prospect_db_test_region_v950",
        )
        test_clicked = st.button(
            "국민연금 API 연결 테스트",
            type="primary",
            use_container_width=True,
            disabled=not key_status["configured"],
            key="prospect_db_connection_test_v950",
        )

    if not key_status["configured"]:
        st.warning(
            "Railway의 앱 서비스 Variables에 "
            "DATA_GO_KR_SERVICE_KEY를 등록하고 재배포해 주세요."
        )

    if test_clicked:
        with st.spinner("국민연금 사업장 API 연결을 확인하고 있습니다..."):
            result = test_nps_connection(REGION_CODES[region_name])
        st.session_state["prospect_db_api_test_result_v950"] = result

    result = st.session_state.get("prospect_db_api_test_result_v950")
    if result:
        if result.get("ok"):
            st.success(result.get("message", "연결 성공"))
        else:
            st.error(result.get("message", "연결 실패"))

        result_cols = st.columns(4)
        result_cols[0].metric("상태", result.get("status", "-"))
        result_cols[1].metric("HTTP", result.get("http_status", "-"))
        result_cols[2].metric("전체 건수", f"{result.get('total_count', 0):,}")
        result_cols[3].metric("응답형식", result.get("response_format", "-"))
        st.caption(
            f"마지막 점검: {result.get('checked_at', '-')} · "
            f"호출 시도: {result.get('attempt_count', 1)}회"
        )

        samples = result.get("sample") or []
        if samples:
            st.markdown("#### 응답 샘플 1건")
            st.dataframe(
                pd.DataFrame(samples),
                use_container_width=True,
                hide_index=True,
            )

        if not result.get("ok"):
            status = result.get("status", "")
            if status in {"SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "30"}:
                st.warning(
                    "인증키가 아직 API 게이트웨이에 반영되지 않았거나 "
                    "해당 API 활용승인이 완료되지 않은 상태일 수 있습니다."
                )
            elif status in {"LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR", "22"}:
                st.warning("개발계정의 일일 호출한도를 확인해 주세요.")

    st.markdown("#### 영업정보 데이터")
    contact_api_status = api_statuses()
    api_cols = st.columns(3)
    api_cols[0].metric(
        "카카오 로컬",
        "등록됨" if contact_api_status["kakao"]["configured"] else "미등록",
    )
    api_cols[1].metric(
        "네이버 검색",
        "등록됨" if contact_api_status["naver"]["configured"] else "미등록",
    )
    api_cols[2].metric(
        "승인 인허가 API",
        (
            f"{contact_api_status['localdata']['service_count']}종"
            if contact_api_status["localdata"]["configured"]
            else (
                "사용 안 함"
                if contact_api_status["localdata"].get("disabled")
                else "미등록"
            )
        ),
    )
    with st.expander("외부 데이터 연결 설정"):
        st.code(
            "KAKAO_REST_API_KEY\n"
            "NAVER_CLIENT_ID\n"
            "NAVER_CLIENT_SECRET\n"
            "DATA_GO_KR_SERVICE_KEY",
            language="text",
        )
        contact_test_clicked = st.button(
            "외부 데이터 연결 점검",
            use_container_width=True,
            disabled=not (
                contact_api_status["kakao"].get("configured")
                and contact_api_status["naver"].get("configured")
            ),
            key="contact_api_connection_test_v970",
        )
    if contact_test_clicked:
        with st.spinner(
            "카카오·네이버·인허가 API를 점검하고 있습니다..."
        ):
            connection_result = test_connections()
            st.session_state["contact_api_test_result_v970"] = (
                connection_result
            )

    contact_test = st.session_state.get("contact_api_test_result_v970")
    if contact_test:
        sources = contact_test.get("sources") or {}
        test_rows = []
        for key, label in (
            ("kakao", "카카오 로컬"),
            ("naver", "네이버 검색"),
            ("localdata", "승인 인허가 API"),
        ):
            source = sources.get(key) or {}
            test_rows.append(
                {
                    "연결": label,
                    "상태": source.get("status", "-"),
                    "결과": source.get("message", "-"),
                }
            )
        if contact_test.get("ok"):
            st.success("연락처 보강 API 연결 점검을 완료했습니다.")
        else:
            st.warning("일부 API 연결을 확인하지 못했습니다.")
        st.dataframe(pd.DataFrame(test_rows), use_container_width=True, hide_index=True)
        local_services = (sources.get("localdata") or {}).get("services") or []
        if local_services:
            with st.expander(
                "전국 인허가 API 대표 업종 연결 상세"
            ):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "서비스": row.get("label", ""),
                                "상태": row.get("status", ""),
                                "응답": row.get("message", ""),
                            }
                            for row in local_services
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

    st.divider()
    st.markdown("### 2. 전국 사업장 수집 미리보기")
    st.caption(
        "기본조회에서 사업장 순번을 받은 뒤 상세조회를 자동 실행합니다. "
        "조회 50건은 기본조회 1회와 상세조회 최대 50회가 사용됩니다."
    )

    with st.form("prospect_collection_form_v960"):
        collect_col1, collect_col2, collect_col3 = st.columns(3)
        collect_region = collect_col1.selectbox(
            "수집 지역",
            list(REGION_CODES.keys()),
            key="prospect_collect_region_v960",
        )
        collect_page = collect_col2.number_input(
            "조회 페이지",
            min_value=1,
            max_value=100000,
            value=1,
            step=1,
        )
        collect_rows = collect_col3.selectbox(
            "조회 건수",
            [10, 30, 50, 100],
            index=1,
        )
        filter_col1, filter_col2, filter_col3 = st.columns(3)
        minimum_employees = filter_col1.number_input(
            "최소 가입자 수",
            min_value=0,
            max_value=10000,
            value=1,
            step=1,
        )
        sigungu_code = filter_col2.text_input(
            "시·군·구 법정동 코드",
            placeholder="선택사항",
            help="공공데이터포털 명세의 시군구 코드를 알고 있을 때만 입력합니다.",
        )
        emd_code = filter_col3.text_input(
            "읍·면·동 법정동 코드",
            placeholder="선택사항",
            help="공공데이터포털 명세의 읍면동 코드를 알고 있을 때만 입력합니다.",
        )
        auto_sales_analysis = st.checkbox(
            "대표전화 확인 후 고용변화·영업주제 자동생성",
            value=True,
            disabled=True,
            help=(
                "대표전화가 확인되지 않은 업체는 영업후보로 표시하거나 저장하지 않습니다."
            ),
        )
        collect_clicked = st.form_submit_button(
            "사업장 수집 및 영업정보 자동완성",
            type="primary",
            use_container_width=True,
            disabled=not key_status["configured"],
        )

    if collect_clicked:
        with st.spinner(
            "기본 사업장을 조회하고 상세정보를 확인하고 있습니다. "
            "조회 건수에 따라 시간이 걸릴 수 있습니다..."
        ):
            collection = fetch_nps_workplaces(
                REGION_CODES[collect_region],
                page_no=int(collect_page),
                rows=int(collect_rows),
                sigungu_code=sigungu_code,
                emd_code=emd_code,
            )
            if collection.get("ok"):
                employee_filtered_items = [
                    item for item in collection.get("items", [])
                    if int(item.get("가입자수") or 0)
                    >= int(minimum_employees)
                ]
                items = [
                    item
                    for item in employee_filtered_items
                    if _is_stock_company(item.get("사업장명"))
                ]
                collection["non_stock_company_count"] = (
                    len(employee_filtered_items) - len(items)
                )
                duplicate_count = 0
                duplicate_warning = ""
                try:
                    items, duplicate_count = remove_existing_customers(items)
                except Exception as exc:
                    duplicate_warning = str(exc)
                analysis_failures: list[dict] = []
                if items:
                    items, analysis_failures = _analyze_candidate_batch(
                        items,
                        limit=len(items),
                    )
                contact_ready_items = [
                    item for item in items
                    if str(item.get("대표전화") or "").strip()
                ]
                collection["contact_missing_count"] = (
                    len(items) - len(contact_ready_items)
                )
                collection["contact_analysis_attempted_count"] = len(items)
                collection["contact_missing_items"] = [
                    item
                    for item in items
                    if not str(item.get("대표전화") or "").strip()
                ]
                items = contact_ready_items
                collection["items"] = items
                collection["existing_customer_count"] = duplicate_count
                collection["duplicate_warning"] = duplicate_warning
                collection["sales_analysis_count"] = sum(
                    1 for item in items if item.get("영업분석")
                )
                collection["sales_analysis_failures"] = analysis_failures
            st.session_state["prospect_collection_v960"] = collection

    collection = st.session_state.get("prospect_collection_v960")
    if collection:
        if collection.get("ok"):
            summary_cols = st.columns(6)
            summary_cols[0].metric(
                "기본조회",
                f"{collection.get('basic_received_count', 0):,}건",
            )
            summary_cols[1].metric(
                "상세조회 성공",
                f"{collection.get('detail_success_count', 0):,}건",
            )
            summary_cols[2].metric(
                "상세조회 실패",
                f"{collection.get('detail_failed_count', 0):,}건",
            )
            summary_cols[3].metric(
                "지역 외 제외",
                f"{collection.get('filtered_out_count', 0):,}건",
            )
            summary_cols[4].metric(
                "기존 고객 제외",
                f"{collection.get('existing_customer_count', 0):,}건",
            )
            summary_cols[5].metric(
                "최종 후보",
                f"{len(collection.get('items', [])):,}건",
            )
            st.caption(
                f"페이지 {collection.get('page_no', 1):,} · "
                f"실제 API 호출 시도 {collection.get('api_attempt_count', 1):,}회 · "
                f"주식회사 외 제외 {collection.get('non_stock_company_count', 0):,}건 · "
                f"연락처 분석 {collection.get('contact_analysis_attempted_count', 0):,}건 · "
                f"번호 확인 {collection.get('sales_analysis_count', 0):,}건 · "
                f"연락처 미확인 제외 {collection.get('contact_missing_count', 0):,}건"
            )
            if collection.get("duplicate_warning"):
                st.warning(
                    "기존 고객DB 중복확인을 완료하지 못했습니다: "
                    f"{collection['duplicate_warning']}"
                )
        else:
            st.error(collection.get("message", "사업장 조회 실패"))

        detail_failures = collection.get("detail_failures", [])
        if detail_failures:
            st.warning(
                f"상세조회에 실패한 사업장 {len(detail_failures):,}건은 "
                "가입자 수 필터를 적용하지 않고 저장대상에서 제외했습니다. "
                "같은 페이지를 다시 조회하면 자동으로 재시도합니다."
            )
            failure_rows = [
                {
                    "사업장명": item.get("사업장명", ""),
                    "지역코드": item.get("지역코드", ""),
                    "사업장순번": item.get("source_key", ""),
                    "실패사유": item.get("상세조회메시지", ""),
                }
                for item in detail_failures
            ]
            with st.expander("상세조회 실패 사업장 보기"):
                st.dataframe(
                    pd.DataFrame(failure_rows),
                    use_container_width=True,
                    hide_index=True,
                )

        prospects = collection.get("items", [])
        contact_missing_items = collection.get("contact_missing_items") or []
        if contact_missing_items:
            with st.expander(
                f"대표전화 미확인 제외 사유 {len(contact_missing_items):,}건"
            ):
                st.dataframe(
                    _display_frame(contact_missing_items)[
                        [
                            "사업장명",
                            "주소",
                            "업종명",
                            "연락처상태",
                            "연락처조회이력",
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
        if collection.get("ok") and not prospects:
            if detail_failures:
                st.warning(
                    "상세조회 성공 사업장 중 현재 조건에 맞는 후보가 없습니다. "
                    "같은 페이지를 재시도하거나 최소 가입자 수를 확인해 주세요."
                )
            else:
                st.warning(
                    "현재 페이지에서 대표전화까지 확인된 주식회사가 없습니다. "
                    "다음 페이지를 조회하면 다른 사업장을 확인합니다."
                )
        elif prospects:
            action_col1, action_col2 = st.columns([2, 1])
            action_col1.info(
                "아래 표에서 대표전화·전화출처·연락처상태·순고용증가·영업주제와 "
                "초회 전화 스크립트를 한 번에 확인할 수 있습니다."
            )
            reanalyze_clicked = action_col2.button(
                "영업정보 다시 분석",
                use_container_width=True,
                key="reanalyze_candidates_v971",
            )
            if reanalyze_clicked:
                with st.spinner(
                    "대표전화·공식홈페이지·고용변화와 영업주제를 분석하고 있습니다..."
                ):
                    analyzed_items, analysis_failures = (
                        _analyze_candidate_batch(prospects, limit=len(prospects))
                    )
                    contact_ready_items = [
                        item for item in analyzed_items
                        if str(item.get("대표전화") or "").strip()
                    ]
                    collection["contact_missing_count"] = (
                        collection.get("contact_missing_count", 0)
                        + len(analyzed_items) - len(contact_ready_items)
                    )
                    collection["contact_analysis_attempted_count"] = len(
                        analyzed_items
                    )
                    collection["contact_missing_items"] = [
                        item
                        for item in analyzed_items
                        if not str(item.get("대표전화") or "").strip()
                    ]
                    collection["items"] = contact_ready_items
                    collection["sales_analysis_count"] = sum(
                        1 for item in analyzed_items if item.get("영업분석")
                    )
                    collection["sales_analysis_failures"] = analysis_failures
                    st.session_state["prospect_collection_v960"] = collection
                    prospects = contact_ready_items

            analysis_failures = collection.get("sales_analysis_failures") or []
            if analysis_failures:
                with st.expander(
                    f"영업정보를 확인하지 못한 업체 {len(analysis_failures)}건"
                ):
                    st.dataframe(
                        pd.DataFrame(analysis_failures),
                        use_container_width=True,
                        hide_index=True,
                    )

            with st.form("prospect_sales_filter_v971"):
                filter_view_col1, filter_view_col2 = st.columns(2)
                hiring_only = filter_view_col1.checkbox(
                    "순고용 증가업체만",
                    key="prospect_hiring_only_v971",
                )
                grade_filter = filter_view_col2.selectbox(
                    "추천등급",
                    ["전체", "A", "B", "C"],
                    key="prospect_grade_filter_v971",
                )
                st.form_submit_button(
                    "조건 적용",
                    use_container_width=True,
                )

            visible_prospects = [
                item
                for item in prospects
                if (
                    not hiring_only
                    or int(item.get("순고용증가") or 0) > 0
                )
                and (
                    grade_filter == "전체"
                    or item.get("추천등급") == grade_filter
                )
            ]
            display = _display_frame(visible_prospects)
            if display.empty:
                st.warning("선택한 조건에 맞는 영업후보가 없습니다.")
            edited = st.data_editor(
                display,
                use_container_width=True,
                hide_index=True,
                disabled=[
                    column for column in display.columns
                    if column not in {"선택"}
                ],
                column_config={
                    "선택": st.column_config.CheckboxColumn(
                        "저장",
                        help="영업후보DB에 저장할 업체를 선택합니다.",
                    ),
                    "대표전화": st.column_config.TextColumn(
                        "대표전화",
                        width="medium",
                    ),
                    "영업주제": st.column_config.TextColumn(
                        "영업주제",
                        width="large",
                    ),
                    "초회전화스크립트": st.column_config.TextColumn(
                        "초회 전화 스크립트",
                        width="large",
                    ),
                    "source_key": None,
                },
                key=(
                    "prospect_editor_v971_"
                    f"{int(hiring_only)}_{grade_filter}"
                ),
            )
            selected_keys = set(
                edited.loc[edited["선택"] == True, "source_key"].tolist()
            )
            selected_items = [
                item for item in visible_prospects
                if item.get("source_key") in selected_keys
            ]
            st.caption(f"저장 선택: {len(selected_items):,}건")

            script_options = {
                str(item.get("사업장명") or item.get("source_key")): item
                for item in visible_prospects
                if item.get("초회전화스크립트")
            }
            if script_options:
                with st.expander("초회 영업전화 스크립트 크게 보기"):
                    script_company = st.selectbox(
                        "업체 선택",
                        list(script_options.keys()),
                        key="preview_call_script_company_v971",
                    )
                    selected_script_item = script_options[script_company]
                    st.text_area(
                        "전화 스크립트",
                        value=selected_script_item.get(
                            "초회전화스크립트",
                            "",
                        ),
                        height=180,
                        disabled=True,
                        key="preview_call_script_v971",
                    )

            if st.button(
                "선택한 업체를 영업후보DB에 저장",
                type="primary",
                use_container_width=True,
                disabled=not selected_items,
                key="save_selected_prospects_v960",
            ):
                table_ok, table_message = prospect_table_status()
                if not table_ok:
                    st.error(table_message)
                else:
                    try:
                        saved_count = save_prospects(
                            selected_items,
                            owner_user_id,
                        )
                        st.success(
                            f"영업후보DB에 {saved_count:,}건을 저장했습니다."
                        )
                        st.session_state.pop("prospect_saved_list_v960", None)
                    except Exception as exc:
                        st.error(str(exc))

    st.divider()
    st.markdown("### 3. 저장된 영업후보 관리")
    st.caption(
        "기술적인 DB 설정은 아래 관리자 설정 안에 모았습니다. "
        "평소에는 업체를 선택하고 분석 또는 정밀 연락처 보강만 누르면 됩니다."
    )

    if "prospect_table_status_v960" not in st.session_state:
        st.session_state["prospect_table_status_v960"] = prospect_table_status()
    if "contact_table_status_v970" not in st.session_state:
        st.session_state["contact_table_status_v970"] = contact_table_status()
    table_status = st.session_state["prospect_table_status_v960"]
    saved_contact_status = st.session_state["contact_table_status_v970"]

    setup_ok = bool(table_status[0] and saved_contact_status[0])
    with st.expander(
        "관리자 설정 · DB 연결 상태",
        expanded=not setup_ok,
    ):
        setup_col1, setup_col2 = st.columns(2)
        if table_status[0]:
            setup_col1.success("영업후보 저장 준비 완료")
        else:
            setup_col1.warning("영업후보 테이블 설정 필요")
        if saved_contact_status[0]:
            setup_col2.success("연락처 저장 준비 완료")
        else:
            setup_col2.warning("연락처 테이블 설정 필요")

        if st.button(
            "DB 연결상태 새로 확인",
            use_container_width=True,
            key="refresh_db_status_v971",
        ):
            st.session_state["prospect_table_status_v960"] = (
                prospect_table_status()
            )
            st.session_state["contact_table_status_v970"] = (
                contact_table_status()
            )
            table_status = st.session_state["prospect_table_status_v960"]
            saved_contact_status = st.session_state[
                "contact_table_status_v970"
            ]

        sql_path = BASE_DIR / "supabase_v960_prospect_db.sql"
        contact_sql_path = BASE_DIR / "supabase_v970_contact_enrichment.sql"
        if not table_status[0] and sql_path.exists():
            st.info(
                "① 아래 영업후보DB SQL을 Supabase SQL Editor에서 "
                "먼저 한 번 실행합니다."
            )
            st.download_button(
                "① 영업후보DB 설정파일 다운로드",
                data=sql_path.read_bytes(),
                file_name="supabase_v960_prospect_db.sql",
                mime="text/plain",
                use_container_width=True,
                key="download_prospect_sql_v971",
            )
        if not saved_contact_status[0] and contact_sql_path.exists():
            st.info(
                "② 영업후보DB 설정 후 아래 연락처 SQL을 "
                "Supabase SQL Editor에서 한 번 실행합니다."
            )
            st.download_button(
                "② 연락처DB 설정파일 다운로드",
                data=contact_sql_path.read_bytes(),
                file_name="supabase_v970_contact_enrichment.sql",
                mime="text/plain",
                use_container_width=True,
                key="download_contact_sql_v971",
            )
        st.caption(
            "두 SQL은 최초 1회만 필요합니다. v9.7.2도 기존 "
            "source_data에 영업분석을 추가하므로 새 SQL이 없습니다."
        )

    if not table_status[0]:
        st.warning(
            "관리자 설정에서 ① 영업후보DB 설정을 완료하면 "
            "저장된 영업후보 관리 화면이 열립니다."
        )
        return

    load_col1, load_col2 = st.columns([3, 1])
    load_col1.success("영업후보DB 연결 완료")
    refresh_saved = load_col2.button(
        "저장목록 새로고침",
        use_container_width=True,
        key="refresh_saved_prospects_v971",
    )
    if refresh_saved or "prospect_saved_list_v960" not in st.session_state:
        try:
            st.session_state["prospect_saved_list_v960"] = list_prospects(
                owner_user_id
            )
        except Exception as exc:
            st.error(str(exc))
            st.session_state["prospect_saved_list_v960"] = []
    all_saved_rows = st.session_state.get("prospect_saved_list_v960", [])
    stock_company_rows = [
        row
        for row in all_saved_rows
        if _is_stock_company(row.get("company_name"))
    ]
    hidden_non_stock_count = len(all_saved_rows) - len(stock_company_rows)
    if hidden_non_stock_count:
        st.info(
            f"기존 저장자료 중 주식회사 외 {hidden_non_stock_count:,}건은 "
            "삭제하지 않고 이 영업후보 화면에서만 숨겼습니다."
        )
    contact_rows: list[dict] = []
    if stock_company_rows and saved_contact_status[0]:
        try:
            contact_rows = list_contacts_for_prospects(
                [str(row.get("id")) for row in stock_company_rows],
                owner_user_id,
            )
            st.session_state["prospect_contacts_v970"] = contact_rows
        except Exception as exc:
            st.warning(safe_public_error(exc, "연락처 목록 확인에 실패했습니다."))
            contact_rows = st.session_state.get("prospect_contacts_v970", [])

    phone_prospect_ids = {
        str(row.get("prospect_id") or "")
        for row in contact_rows
        if row.get("contact_type") == "phone"
        and row.get("contact_value")
        and not row.get("do_not_contact")
    }
    saved_rows = [
        row
        for row in stock_company_rows
        if str(_saved_sales_analysis(row).get("phone") or "").strip()
        or str(row.get("id") or "") in phone_prospect_ids
    ]
    hidden_no_phone_count = len(stock_company_rows) - len(saved_rows)
    if hidden_no_phone_count:
        st.info(
            f"대표전화가 확인되지 않은 저장자료 {hidden_no_phone_count:,}건은 "
            "삭제하지 않고 영업 목록에서만 숨겼습니다."
        )

    if not saved_rows:
        st.info(
            "저장된 주식회사 영업후보가 없습니다. 위 후보표에서 업체를 선택해 "
            "영업후보DB에 저장해 주세요."
        )
        return

    saved_frame = _saved_candidate_frame(saved_rows, contact_rows)
    st.dataframe(
        saved_frame,
        use_container_width=True,
        hide_index=True,
        column_config={
            "인스타그램URL": st.column_config.LinkColumn(
                "인스타그램 링크"
            ),
            "초회전화스크립트": st.column_config.TextColumn(
                "초회 전화 스크립트",
                width="large",
            )
        },
    )
    with st.expander("기존 영업후보 원본데이터 보기"):
        st.dataframe(
            pd.DataFrame(saved_rows),
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("저장된 연락처 상세보기"):
        if not saved_contact_status[0]:
            st.info(
                "관리자 설정에서 연락처 테이블 연결을 먼저 완료해 주세요."
            )
        else:
            if st.button(
                "저장된 연락처 새로고침",
                use_container_width=True,
                key="refresh_prospect_contacts_v970",
            ):
                try:
                    contact_rows = list_contacts_for_prospects(
                        [str(row.get("id")) for row in saved_rows],
                        owner_user_id,
                    )
                    st.session_state["prospect_contacts_v970"] = contact_rows
                except Exception as exc:
                    st.error(str(exc))
            if contact_rows:
                company_by_id = {
                    str(row.get("id")): row.get("company_name", "")
                    for row in saved_rows
                }
                display_contacts = [
                    {
                        "업체명": company_by_id.get(
                            str(row.get("prospect_id")),
                            "",
                        ),
                        "구분": row.get("contact_type", ""),
                        "연락처": row.get("contact_value", ""),
                        "설명": row.get("contact_label", ""),
                        "출처": row.get("source_type", ""),
                        "신뢰도": row.get("confidence", 0),
                        "검증상태": row.get("verification_status", ""),
                        "수신거부": row.get("do_not_contact", False),
                    }
                    for row in contact_rows
                ]
                st.dataframe(
                    pd.DataFrame(display_contacts),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("아직 저장된 연락처가 없습니다.")

    label_to_id = {
        (
            f"{row.get('company_name', '(업체명 없음)')} | "
            f"{row.get('address', '')} | {str(row.get('id', ''))[-8:]}"
        ): str(row.get("id") or "")
        for row in saved_rows
        if row.get("id")
    }
    row_by_id = {
        str(row.get("id")): row
        for row in saved_rows
        if row.get("id")
    }
    with st.form("saved_sales_action_form_v971"):
        selected_labels = st.multiselect(
            "작업할 영업후보 선택",
            list(label_to_id.keys()),
            max_selections=10,
            help="한 번에 최대 10개 업체를 선택합니다.",
        )
        button_col1, button_col2 = st.columns(2)
        analyze_saved_clicked = button_col1.form_submit_button(
            "대표전화·고용주제 분석",
            type="primary",
            use_container_width=True,
            disabled=not selected_labels,
        )
        enrich_saved_clicked = button_col2.form_submit_button(
            "이메일·홈페이지 정밀 보강",
            use_container_width=True,
            disabled=(
                not selected_labels or not saved_contact_status[0]
            ),
        )
        st.caption(
            "첫 번째 버튼은 카카오·인허가 API에서 번호를 찾지 못하면 "
            "네이버와 공식 홈페이지까지 자동 확인해 전화할 이유와 스크립트를 만듭니다. "
            "두 번째 버튼은 네이버와 공식 홈페이지까지 확인해 "
            "이메일·홈페이지를 연락처DB에 저장합니다. 공개된 사업용 "
            "연락처만 사용하며 실제 연락 전에는 수신거부 여부를 확인합니다."
        )

    selected_ids = [
        label_to_id[label]
        for label in selected_labels
        if label in label_to_id
    ]
    if analyze_saved_clicked:
        sales_results: list[dict] = []
        progress = st.progress(0, text="영업정보 분석을 시작합니다.")
        for index, prospect_id in enumerate(selected_ids, start=1):
            prospect = row_by_id[prospect_id]
            try:
                analysis = analyze_sales_candidate(prospect)
                save_sales_analysis(prospect_id, analysis)
                sales_results.append(
                    {
                        "업체명": prospect.get("company_name", ""),
                        "대표전화": analysis.get("phone", ""),
                        "전화출처": analysis.get("phone_source", ""),
                        "연락처상태": _contact_status_label(
                            analysis.get("contact_status", "")
                        ),
                        "순고용증가": analysis.get("net_hiring", 0),
                        "영업주제": " · ".join(
                            analysis.get("sales_topics") or []
                        ),
                        "추천등급": analysis.get(
                            "recommendation_grade",
                            "",
                        ),
                        "결과": "저장 완료",
                    }
                )
            except Exception as exc:
                sales_results.append(
                    {
                        "업체명": prospect.get("company_name", ""),
                        "결과": f"실패: {exc}",
                    }
                )
            progress.progress(
                index / max(1, len(selected_ids)),
                text=f"{index}/{len(selected_ids)} 업체 분석 완료",
            )
        st.session_state["sales_analysis_results_v971"] = sales_results
        try:
            st.session_state["prospect_saved_list_v960"] = list_prospects(
                owner_user_id
            )
            saved_rows = st.session_state["prospect_saved_list_v960"]
        except Exception as exc:
            st.warning(
                safe_public_error(exc, "분석 후 목록을 새로고치지 못했습니다.")
            )

    if enrich_saved_clicked:
        enrichment_results: list[dict] = []
        progress = st.progress(0, text="정밀 연락처 보강을 시작합니다.")
        for index, prospect_id in enumerate(selected_ids, start=1):
            prospect = row_by_id[prospect_id]
            try:
                result = enrich_company(prospect)
                result["prospect_id"] = prospect_id
                result["saved_count"] = save_prospect_contacts(
                    prospect_id,
                    result.get("contacts") or [],
                    owner_user_id,
                )
            except Exception as exc:
                result = {
                    "ok": False,
                    "prospect_id": prospect_id,
                    "company_name": prospect.get("company_name", ""),
                    "status": "error",
                    "contacts": [],
                    "saved_count": 0,
                    "trace": [
                        {
                            "stage": "error",
                            "status": type(exc).__name__,
                            "message": str(exc),
                        }
                    ],
                }
            enrichment_results.append(result)
            progress.progress(
                index / max(1, len(selected_ids)),
                text=f"{index}/{len(selected_ids)} 업체 보강 완료",
            )
        st.session_state["contact_enrichment_results_v970"] = (
            enrichment_results
        )
        st.session_state.pop("prospect_contacts_v970", None)
        # Contact tables were rendered before enrichment in this legacy view.
        # Refresh once so every table uses the newly saved contact records.
        st.rerun()

    sales_results = st.session_state.get("sales_analysis_results_v971", [])
    if sales_results:
        st.markdown("#### 최근 영업정보 분석 결과")
        st.dataframe(
            pd.DataFrame(sales_results),
            use_container_width=True,
            hide_index=True,
        )
    enrichment_results = st.session_state.get(
        "contact_enrichment_results_v970",
        [],
    )
    if enrichment_results:
        st.markdown("#### 최근 정밀 연락처 보강 결과")
        st.dataframe(
            pd.DataFrame(
                [_contact_result_row(row) for row in enrichment_results]
            ),
            use_container_width=True,
            hide_index=True,
        )
        with st.expander("업체별 연락처 수집 경로 보기"):
            for result in enrichment_results:
                st.markdown(f"**{result.get('company_name', '')}**")
                st.dataframe(
                    pd.DataFrame(result.get("trace") or []),
                    use_container_width=True,
                    hide_index=True,
                )

    script_rows = [
        row for row in saved_rows if _saved_sales_analysis(row).get(
            "first_call_script"
        )
    ]
    if script_rows:
        st.markdown("#### 초회 영업전화 준비")
        script_labels = {
            (
                f"{row.get('company_name', '')} | "
                f"{str(row.get('id', ''))[-8:]}"
            ): row
            for row in script_rows
        }
        selected_script_label = st.selectbox(
            "전화할 업체",
            list(script_labels.keys()),
            key="saved_call_script_company_v971",
        )
        script_analysis = _saved_sales_analysis(
            script_labels[selected_script_label]
        )
        script_col1, script_col2 = st.columns([1, 2])
        script_col1.metric(
            "추천등급",
            script_analysis.get("recommendation_grade", "-"),
        )
        script_col1.write(
            "영업주제: "
            + " · ".join(script_analysis.get("sales_topics") or [])
        )
        script_col2.text_area(
            "초회 전화 스크립트",
            value=script_analysis.get("first_call_script", ""),
            height=200,
            disabled=True,
            key="saved_call_script_v971",
        )


def _render_clean_saved_prospects(
    owner_user_id: str,
    can_view_mobile: bool = False,
    is_admin_user: bool = False,
) -> None:
    st.markdown("### 저장된 영업후보")
    st.caption(
        "내가 저장한 영업후보만 표시합니다. 다른 사용자가 저장한 업체는 "
        "보이지 않지만, 전사 중복 제외 기준에는 계속 반영됩니다. "
        "전사 배정 기능 적용 후에는 공개 연락처가 아직 없는 업체도 "
        "배정 해제·연락결과 관리를 위해 함께 표시합니다."
    )
    _show_outreach_result_notice()
    assignment_mode = False
    try:
        ready, _ready_message = _assignment_feature_status()
        if ready:
            _release_expired_assignments_if_due(owner_user_id)
            assignment_result = sales_assignments.list_user_assignments(
                owner_user_id,
                limit=1000,
            )
        else:
            assignment_result = {"ok": False}
        if assignment_result.get("ok"):
            rows = []
            for assignment in assignment_result.get("assignments") or []:
                row = dict(assignment)
                row["_assignment_id"] = (
                    row.get("assignment_id") or row.get("id") or ""
                )
                row["id"] = row.get("company_id") or row.get("id")
                row["memo"] = row.get("own_memo") or row.get("memo") or ""
                rows.append(row)
            assignment_mode = True
        else:
            rows = list_prospects(owner_user_id, limit=1000)
    except Exception as exc:
        st.warning(safe_public_error(exc, "저장목록을 불러오지 못했습니다."))
        return
    if not rows:
        st.info("내가 저장한 영업후보가 없습니다.")
        return

    contacts: list[dict] = []
    canonical_contacts_ready = False
    try:
        if contact_table_status()[0]:
            canonical_contacts_ready = True
            contacts = list_contacts_for_prospects(
                [str(row.get("id") or "") for row in rows],
                owner_user_id,
            )
    except Exception:
        contacts = []

    frame = _saved_candidate_frame(
        rows,
        contacts,
        can_view_mobile=can_view_mobile,
        canonical_contacts_only=canonical_contacts_ready,
    )
    if not frame.empty:
        frame["대표전화"] = frame["대표전화"].map(normalize_phone)
        frame["휴대전화"] = frame["휴대전화"].map(normalize_phone)
        frame["일반전화"] = frame["일반전화"].map(normalize_phone)
        accessible = (
            (frame["일반전화"] != "")
            | (frame["이메일"].fillna("").astype(str).str.strip() != "")
            | (frame["인스타그램"].fillna("").astype(str).str.strip() != "")
            | (
                frame["인스타그램URL"]
                .fillna("")
                .astype(str)
                .str.strip()
                != ""
            )
        )
        if can_view_mobile:
            accessible = accessible | (frame["휴대전화"] != "")
        if assignment_mode:
            # Central assignments must remain manageable even when a public
            # phone/email/Instagram address has not been enriched yet.
            accessible = pd.Series(True, index=frame.index)
        frame = frame[accessible].reset_index(drop=True)
        frame = frame.sort_values(
            by=["_고용정렬", "가입자"],
            ascending=[False, False],
            kind="stable",
        ).reset_index(drop=True)
    if frame.empty:
        st.info("내 저장 업체 중 표시 가능한 공개 연락처가 없습니다.")
        return

    compact_frame = _saved_prospect_table_frame(
        frame,
        can_view_mobile=can_view_mobile,
    )
    export_frame = compact_frame[
        [
            column
            for column in SAVED_PROSPECT_VISIBLE_COLUMNS
            if column not in OUTREACH_COLUMN_CHANNELS
        ]
    ]
    st.download_button(
        "저장된 영업후보 엑셀 다운로드",
        data=_excel_bytes(export_frame, "저장된 영업후보"),
        file_name="OASIS_저장된_영업후보.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True,
        key="saved_prospect_excel_v984",
    )
    st.caption(
        "오른쪽의 📧·💬·🟡 버튼을 누르면 선택한 업체 1곳에 보낼 내용을 "
        "직접 작성할 수 있습니다. 수신거부 또는 수신처가 없는 채널은 버튼이 표시되지 않습니다."
    )
    action_rows = _outreach_action_rows(
        frame,
        can_view_mobile=can_view_mobile,
    )
    email_click_key = "saved_prospect_email_click_v1040"
    sms_click_key = "saved_prospect_sms_click_v1040"
    kakao_click_key = "saved_prospect_kakao_click_v1040"
    st.dataframe(
        compact_frame,
        use_container_width=True,
        hide_index=True,
        column_config={
            "이메일보내기": st.column_config.ButtonColumn(
                "이메일보내기",
                width="small",
                type="tertiary",
                key=email_click_key,
                on_click=_queue_outreach_from_button,
                args=(email_click_key, "email", action_rows),
            ),
            "문자보내기": st.column_config.ButtonColumn(
                "문자보내기",
                width="small",
                type="tertiary",
                key=sms_click_key,
                on_click=_queue_outreach_from_button,
                args=(sms_click_key, "sms", action_rows),
            ),
            "카카오톡보내기": st.column_config.ButtonColumn(
                "카카오톡보내기",
                width="small",
                type="tertiary",
                key=kakao_click_key,
                on_click=_queue_outreach_from_button,
                args=(kakao_click_key, "kakao", action_rows),
            ),
        },
        key="saved_prospect_compact_table_v1040",
    )

    pending_request = st.session_state.get(_OUTREACH_REQUEST_KEY)
    if isinstance(pending_request, dict):
        _show_outreach_dialog(
            owner_user_id,
            pending_request,
            can_view_mobile=can_view_mobile,
        )

    with st.expander("업체 메모 관리", expanded=False):
        memo_rows = {
            (
                f"{row.get('업체명', '')} · "
                f"{str(row.get('_company_uid') or row.get('_prospect_id') or '')[-10:]}"
            ): row
            for row in frame.to_dict("records")
        }
        memo_label = st.selectbox(
            "메모를 관리할 업체",
            list(memo_rows),
            key="saved_prospect_memo_company_v1040",
        )
        memo_row = memo_rows.get(memo_label, {})
        with st.form("saved_prospect_memo_form_v1040"):
            memo_value = st.text_area(
                "업체 메모",
                value=str(memo_row.get("메모") or ""),
                max_chars=10_000,
                placeholder="담당자·통화 결과·다음 조치 등을 기록해 주세요.",
            )
            memo_submitted = st.form_submit_button(
                "메모 저장",
                use_container_width=True,
            )
        if memo_submitted:
            try:
                memo_prospect_id = str(memo_row.get("_prospect_id") or "")
                memo_company_uid = str(memo_row.get("_company_uid") or "")
                if assignment_mode and memo_company_uid:
                    memo_result = sales_assignments.save_user_note(
                        owner_user_id,
                        memo_company_uid,
                        memo_value,
                        company_id=memo_prospect_id,
                    )
                    if not memo_result.get("ok"):
                        raise RuntimeError("memo save failed")
                else:
                    save_prospect_memo(
                        memo_prospect_id,
                        memo_value,
                        owner_user_id,
                    )
                st.success("업체 메모를 저장했습니다.")
                st.rerun()
            except Exception:
                st.error("메모를 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.")

    if assignment_mode:
        st.markdown("#### 연락결과 기록")
        st.caption(
            "통화·이메일·문자·카카오톡·상담 결과를 저장하면 담당자가 "
            "확정되고 전사 중복연락 방지 상태가 함께 갱신됩니다."
        )
        assignment_rows = {
            (
                f"{row.get('업체명', '')} · "
                f"{row.get('배정상태', '')} · "
                f"{str(row.get('_company_uid') or '')[-10:]}"
            ): row
            for row in frame.to_dict("records")
            if str(row.get("_company_uid") or "")
        }
        selected_label = st.selectbox(
            "연락결과를 기록할 업체",
            list(assignment_rows),
            key="sales_contact_company_v989",
        )
        selected_assignment = assignment_rows.get(selected_label, {})
        selected_company_uid = str(
            selected_assignment.get("_company_uid") or ""
        )
        with st.form("sales_contact_record_form_v989"):
            contact_col1, contact_col2 = st.columns(2)
            contact_method = contact_col1.selectbox(
                "연락방식",
                CONTACT_METHOD_OPTIONS,
            )
            contact_result = contact_col2.selectbox(
                "연락결과",
                CONTACT_RESULT_OPTIONS,
            )
            schedule_next_contact = st.checkbox(
                "다음 연락예정일 지정",
                value=False,
            )
            next_contact_date = st.date_input(
                "다음 연락예정일",
                disabled=not schedule_next_contact,
                help="‘재연락 요청’ 선택 시 반드시 지정합니다.",
            )
            contact_notes = st.text_area(
                "상담내용",
                max_chars=10_000,
                placeholder="고객 반응과 후속조치 내용을 기록해 주세요.",
            )
            contact_submitted = st.form_submit_button(
                "연락결과 저장",
                type="primary",
                use_container_width=True,
            )
        if contact_submitted:
            if contact_result == "재연락 요청" and not schedule_next_contact:
                st.error("재연락 요청은 다음 연락예정일을 입력해 주세요.")
            else:
                contact_save_result = sales_assignments.record_contact(
                    owner_user_id,
                    selected_assignment.get("_prospect_id"),
                    selected_assignment.get("_company_uid"),
                    contact_method,
                    contact_result,
                    notes=contact_notes,
                    next_contact_at=(
                        next_contact_date.isoformat()
                        if schedule_next_contact
                        else None
                    ),
                    session_id=_assignment_session_id(),
                )
                if contact_save_result.get("ok"):
                    st.success(contact_save_result.get("message"))
                else:
                    st.error(contact_save_result.get("message"))

        with st.expander("내 연락이력", expanded=False):
            contact_history_result = sales_assignments.list_company_contacts(
                owner_user_id,
                selected_assignment.get("_company_uid"),
                limit=200,
            )
            if contact_history_result.get("ok") and contact_history_result.get(
                "contacts"
            ):
                contact_history_frame = pd.DataFrame(
                    contact_history_result["contacts"]
                ).rename(
                    columns={
                        "contact_method": "연락방식",
                        "contact_result": "연락결과",
                        "notes": "상담내용",
                        "contacted_at": "연락일시",
                        "next_contact_at": "다음 연락예정일",
                    }
                )
                st.dataframe(
                    contact_history_frame[
                        [
                            column
                            for column in [
                                "연락방식",
                                "연락결과",
                                "상담내용",
                                "연락일시",
                                "다음 연락예정일",
                            ]
                            if column in contact_history_frame.columns
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
            elif contact_history_result.get("ok"):
                st.info("기록된 연락이력이 없습니다.")
            else:
                st.error(contact_history_result.get("message"))

        with st.expander("자동 발송 이력", expanded=False):
            outreach_history_result = (
                sales_outreach_repository.list_outreach_history(
                    owner_user_id,
                    selected_assignment.get("_company_uid"),
                    limit=200,
                )
            )
            outreach_rows = list(
                outreach_history_result.get("history") or []
            )
            if outreach_history_result.get("ok") and outreach_rows:
                outreach_history_frame = pd.DataFrame(
                    [
                        {
                            "채널": OUTREACH_CHANNEL_LABELS.get(
                                str(row.get("channel") or ""),
                                str(row.get("channel") or ""),
                            ),
                            "처리상태": OUTREACH_HISTORY_STATUS_LABELS.get(
                                str(row.get("status") or ""),
                                str(row.get("status") or ""),
                            ),
                            "안전결과코드": str(
                                row.get("safe_result_code") or ""
                            ),
                            "요청일시": str(
                                row.get("reserved_at") or ""
                            ).replace("T", " ")[:19],
                            "종료일시": str(
                                row.get("finalized_at") or ""
                            ).replace("T", " ")[:19],
                        }
                        for row in outreach_rows
                    ]
                )
                st.dataframe(
                    outreach_history_frame,
                    use_container_width=True,
                    hide_index=True,
                )
                st.caption(
                    "수신처·메시지 본문·녹음파일·증빙 경로는 저장하지 않습니다."
                )
            elif outreach_history_result.get("ok"):
                st.info("자동 발송 이력이 없습니다.")
            else:
                st.warning(
                    outreach_history_result.get("message")
                    or "자동 발송 이력을 불러오지 못했습니다."
                )

        selected_contact_count = int(
            selected_assignment.get("연락횟수") or 0
        )
        selected_status = str(
            selected_assignment.get("_assignment_status") or ""
        )
        if selected_contact_count == 0 and selected_status == "assigned":
            with st.expander("미접촉 임시 배정 해제", expanded=False):
                with st.form("sales_assignment_release_form_v989"):
                    release_reason = st.text_input(
                        "해제 사유",
                        max_chars=500,
                    )
                    release_submitted = st.form_submit_button(
                        "이 업체 배정 해제",
                        use_container_width=True,
                    )
                if release_submitted:
                    if not release_reason.strip():
                        st.error("배정 해제 사유를 입력해 주세요.")
                    else:
                        release_result = sales_assignments.release_assignment(
                            owner_user_id,
                            selected_assignment.get("_prospect_id"),
                            selected_assignment.get("_company_uid"),
                            reason=release_reason,
                            session_id=_assignment_session_id(),
                        )
                        if release_result.get("ok"):
                            st.success(release_result.get("message"))
                        else:
                            st.error(release_result.get("message"))


def render_prospect_db_center(
    owner_user_id: str = "",
    *,
    can_view_mobile: bool = False,
    is_admin_user: bool = False,
) -> None:
    can_view_mobile = _effective_prospect_mobile_visibility(
        can_view_mobile,
        is_admin_user=is_admin_user,
    )
    _show_pending_prospect_save_notices()
    pending_workflow_step = st.session_state.pop(
        "_prospect_workflow_step_pending_v1020",
        None,
    )
    if pending_workflow_step in {
        "① 조건 설정",
        "② 검색 결과",
        "③ 저장된 영업후보",
    }:
        st.session_state["prospect_workflow_step_v1020"] = (
            pending_workflow_step
        )
    if "prospect_workflow_step_v1020" not in st.session_state:
        st.session_state["prospect_workflow_step_v1020"] = "① 조건 설정"
    workflow_step = st.pills(
        "DB발굴 작업 단계",
        ["① 조건 설정", "② 검색 결과", "③ 저장된 영업후보"],
        key="prospect_workflow_step_v1020",
        label_visibility="collapsed",
    )
    workflow_step = workflow_step or "① 조건 설정"
    if workflow_step == "③ 저장된 영업후보":
        _render_clean_saved_prospects(
            owner_user_id,
            can_view_mobile=can_view_mobile,
            is_admin_user=is_admin_user,
        )
        return
    if workflow_step == "① 조건 설정":
        _render_search_history(owner_user_id)

    with st.expander(
        "조회 조건",
        expanded=workflow_step == "① 조건 설정",
    ):
        discovery_type_name = st.radio(
            "발굴 유형",
            list(DISCOVERY_TYPE_OPTIONS.keys()),
            horizontal=True,
            key="prospect_discovery_type_v1012",
        )
        discovery_type = DISCOVERY_TYPE_OPTIONS[discovery_type_name]
        col1, col2, col3, col4 = st.columns(4)
        region_name = col1.selectbox(
            "도·광역시",
            province_options()[1:],
            key="prospect_region_v1002",
        )
        district_name = col2.selectbox(
            district_label(region_name),
            district_options(region_name),
            key=f"prospect_district_v1002_{region_name}",
        )
        business_type_name = col3.selectbox(
            "사업자 유형",
            list(BUSINESS_TYPE_OPTIONS.keys()),
            key="prospect_business_type_v1002",
        )
        target_count = col4.selectbox(
            "조회 단위",
            [50, 100, 300, 500] if is_admin_user else [30],
            index=1 if is_admin_user else 0,
            disabled=not is_admin_user,
            key=(
                "prospect_target_v1002"
                if is_admin_user
                else "prospect_target_v1033_member"
            ),
        )
        target_count = _effective_prospect_target_count(
            target_count,
            is_admin_user=is_admin_user,
        )
        with st.expander("검색 범위 조정", expanded=True):
            if is_admin_user:
                contact_channel_options = {
                    label: value
                    for label, value in CONTACT_CHANNEL_OPTIONS.items()
                    if can_view_mobile or value != "mobile_phone"
                }
                default_contact_labels = (
                    ["휴대전화", "일반전화"]
                    if can_view_mobile
                    else ["일반전화", "이메일", "인스타그램"]
                )
                selected_contact_labels = st.pills(
                    "연락처 필터",
                    list(contact_channel_options.keys()),
                    default=default_contact_labels,
                    selection_mode="multi",
                    key=(
                        "prospect_contact_channels_v1021_"
                        + ("owner" if can_view_mobile else "member_admin")
                    ),
                    help=(
                        "선택한 연락수단 중 하나라도 저장된 업체만 "
                        "조회합니다. 여러 항목을 함께 선택할 수 있습니다."
                    ),
                )
            else:
                contact_channel_options = dict(CONTACT_CHANNEL_OPTIONS)
                st.pills(
                    "연락처 필터",
                    list(MEMBER_REQUIRED_CONTACT_LABELS),
                    default=list(MEMBER_REQUIRED_CONTACT_LABELS),
                    selection_mode="multi",
                    disabled=True,
                    key="prospect_required_phone_channels_v1033_member",
                    help=(
                        "일반 계정은 휴대전화와 일반전화를 항상 함께 "
                        "조회하고 표시합니다."
                    ),
                )
                optional_contact_labels = st.pills(
                    "추가 연락처 필터 (선택)",
                    list(MEMBER_OPTIONAL_CONTACT_LABELS),
                    default=[],
                    selection_mode="multi",
                    key="prospect_optional_contact_channels_v1033_member",
                )
                selected_contact_labels = optional_contact_labels
            selected_contact_labels = _effective_contact_filter_labels(
                selected_contact_labels,
                is_admin_user=is_admin_user,
            )
            if discovery_type == "recent_opening":
                recent_col1, recent_col2 = st.columns(2)
                recent_months = recent_col1.selectbox(
                    "국민연금 신규 적용 기간",
                    [3, 6, 12],
                    index=1,
                    format_func=lambda value: f"최근 {value}개월",
                    key="prospect_recent_months_v1012",
                )
                include_comwel_annual = recent_col2.checkbox(
                    "근로복지공단 2025년 신규 추정 포함",
                    value=True,
                    key="prospect_include_comwel_v1012",
                    help=(
                        "근로복지공단 자료는 정확한 개업일이 아니라 "
                        "2025년 연간 자료에 처음 등장한 사업장입니다."
                    ),
                )
                st.caption(
                    "국민연금 사업장 적용일은 법적 개업일과 다를 수 "
                    "있으므로 화면에는 ‘신규개업 추정’으로 표시합니다."
                )
            else:
                recent_months = 6
                include_comwel_annual = True
            filter_col1, filter_col2 = st.columns(2)
            minimum_employees = filter_col1.number_input(
                "최소 고용인원",
                min_value=1,
                max_value=10000,
                value=1,
                step=1,
                key="prospect_min_employee_v1002",
            )
            maximum_employees = filter_col2.number_input(
                "최대 고용인원",
                min_value=1,
                max_value=10000,
                value=300,
                step=1,
                key="prospect_max_employee_v1002",
            )
            industry_categories = st.multiselect(
                "업종 필터",
                list(INDUSTRY_FILTER_OPTIONS),
                default=[],
                help=(
                    "선택하지 않으면 전체 업종을 검색합니다. 개인사업자 "
                    "후보에서 병원·음식점·서비스업 등을 골라 검색할 수 "
                    "있습니다."
                ),
                key="prospect_industry_categories_v1002",
            )
        search_clicked = st.button(
            (
                f"신규개업 추정기업 {target_count}개 조회"
                if discovery_type == "recent_opening"
                else f"성장기업 {target_count}개 조회"
            ),
            type="primary",
            use_container_width=True,
            key="prospect_search_button_v1002",
        )
        st.caption(
            (
                "국민연금 사업장 적용일과 근로복지공단 연간 최초 등장 "
                "신호를 사업자번호로 중복 제거해 조회합니다."
                if discovery_type == "recent_opening"
                else (
                    "Supabase에 미리 저장된 국민연금 월별·근로복지공단 "
                    "연간 고용증가와 공개 연락처를 즉시 조회합니다."
                )
            )
        )

    business_type = BUSINESS_TYPE_OPTIONS[business_type_name]
    contact_channels = [
        contact_channel_options[label]
        for label in (selected_contact_labels or [])
        if label in contact_channel_options
    ]
    data_source = "combined"
    minimum_growth = 1
    growth_basis = (
        "recent_opening"
        if discovery_type == "recent_opening"
        else "combined"
    )
    effective_growth_only = discovery_type == "growth"
    start_page = 1
    end_page = 1
    page_state_key = (
        "prospect_next_page_v1002_"
        f"{owner_user_id}_{discovery_type}_"
        f"{region_name}_{district_name}_{business_type}"
    )
    page_count = 1
    result_state_key = f"prospect_result_v1012_{discovery_type}"
    selection_state_key = (
        f"prospect_selected_keys_v1013_{discovery_type}"
    )
    result_revision_key = (
        f"prospect_result_revision_v1013_{discovery_type}"
    )
    result_page_key = f"prospect_result_page_v1013_{discovery_type}"
    if search_clicked and int(maximum_employees) < int(minimum_employees):
        st.error("최대 고용인원은 최소 고용인원보다 크거나 같아야 합니다.")
        search_clicked = False
    if search_clicked:
        progress_bar = st.progress(
            0,
            text="검색 준비 중입니다.",
        )
        status_box = st.empty()
        progress_state = {"value": 0.0}

        def _progress(event: dict) -> None:
            stage = event.get("stage")
            if stage == "recent_opening":
                progress_state["value"] = max(progress_state["value"], 0.25)
                progress_bar.progress(
                    progress_state["value"],
                    text="Supabase 신규개업 추정기업 불러오는 중",
                )
            elif stage == "recent_opening_complete":
                progress_state["value"] = max(progress_state["value"], 0.8)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        "신규개업 추정기업 "
                        f"{event.get('checked', 0)}개 확인 완료"
                    ),
                )
            elif stage == "precomputed":
                progress_state["value"] = max(progress_state["value"], 0.15)
                progress_bar.progress(
                    progress_state["value"],
                    text="Supabase 저장 성장기업 불러오는 중",
                )
            elif stage == "precomputed_complete":
                progress_state["value"] = max(progress_state["value"], 0.35)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        "Supabase 성장기업 "
                        f"{event.get('checked', 0)}개 확인 완료"
                    ),
                )
            elif stage == "nps":
                current = int(event.get("pages_scanned") or 0)
                ratio = min(
                    0.55,
                    (current + 1) / max(1, page_count) * 0.55,
                )
                progress_state["value"] = max(progress_state["value"], ratio)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        f"국민연금 {event.get('page', '')}페이지 기본·상세조회 중"
                    ),
                )
            elif stage == "nps_complete":
                current = int(event.get("pages_scanned") or 0)
                ratio = min(
                    0.60,
                    current / max(1, page_count) * 0.60,
                )
                progress_state["value"] = max(progress_state["value"], ratio)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        f"국민연금 {event.get('page', '')}페이지 확인 완료"
                    ),
                )
            elif stage == "employment":
                progress_state["value"] = max(progress_state["value"], 0.62)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        "최근 월 고용 증가 신호 확인 중 "
                        f"({event.get('page', '')}페이지)"
                    ),
                )
            elif stage == "employment_complete":
                progress_state["value"] = max(progress_state["value"], 0.68)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        f"고용자료 확인 {event.get('checked', 0)}건 · "
                        f"확인 불가 {event.get('unavailable', 0)}건"
                    ),
                )
            elif stage == "quick_contact":
                progress_state["value"] = max(progress_state["value"], 0.72)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        "카카오·네이버 공개 장소·웹 검색 "
                        f"{event.get('checked', 0)}건"
                    ),
                )
            elif stage == "full_contact":
                progress_state["value"] = max(progress_state["value"], 0.88)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        "공식 홈페이지 정밀 확인 "
                        f"{event.get('checked', 0)}건"
                    ),
                )
            status_box.caption(
                f"현재 확인된 연락 가능 업체: {event.get('found', 0)}건"
            )

        with st.spinner(
            (
                "기존 고객·저장 영업후보를 제외하고 신규개업 "
                "추정기업을 찾고 있습니다."
                if discovery_type == "recent_opening"
                else (
                    "기존 고객·저장 영업후보를 제외하고 성장기업을 "
                    "찾고 있습니다."
                )
            )
        ):
            if discovery_type == "recent_opening":
                result = collect_recent_opening_companies(
                    REGION_CODES[region_name],
                    target_count=int(target_count),
                    minimum_employees=int(minimum_employees),
                    maximum_employees=int(maximum_employees),
                    recent_months=int(recent_months),
                    include_comwel_annual=bool(include_comwel_annual),
                    business_type=business_type,
                    industry_categories=list(industry_categories),
                    contact_channels=contact_channels,
                    district_name=(
                        ""
                        if district_name == ALL_DISTRICTS
                        else district_name
                    ),
                    progress=_progress,
                    exclude_saved_prospects=False,
                )
            else:
                result = collect_contactable_growth_companies(
                    REGION_CODES[region_name],
                    target_count=int(target_count),
                    start_page=int(start_page),
                    max_pages=page_count,
                    minimum_employees=int(minimum_employees),
                    maximum_employees=int(maximum_employees),
                    minimum_growth=int(minimum_growth),
                    business_type=business_type,
                    growth_only=effective_growth_only,
                    growth_basis=growth_basis,
                    industry_categories=list(industry_categories),
                    contact_channels=contact_channels,
                    district_name=(
                        ""
                        if district_name == ALL_DISTRICTS
                        else district_name
                    ),
                    data_source=data_source,
                    progress=_progress,
                    exclude_saved_prospects=False,
                )
        result = _sanitize_search_result(result, can_view_mobile)
        if result.get("ok"):
            result = _filter_assignment_search_result(
                result,
                owner_user_id,
                is_admin_user=is_admin_user,
            )
        result = _limit_prospect_result_for_role(
            result,
            is_admin_user=is_admin_user,
        )
        if result.get("ok"):
            progress_bar.progress(1.0, text="검색을 완료했습니다.")
        else:
            progress_bar.progress(
                1.0,
                text=f"Supabase {discovery_type_name} 조회에 실패했습니다.",
            )
        status_box.empty()
        try:
            result_stats = result.get("stats") or {}
            save_search_history(
                owner_user_id,
                region=region_name,
                region_code=REGION_CODES[region_name],
                district=(
                    ""
                    if district_name == ALL_DISTRICTS
                    else district_name
                ),
                business_type=business_type,
                data_source=data_source,
                start_page=int(
                    result.get("searched_start_page") or start_page
                ),
                end_page=int(
                    result.get("searched_end_page") or end_page
                ),
                target_count=int(target_count),
                minimum_employees=int(minimum_employees),
                maximum_employees=int(maximum_employees),
                minimum_growth=int(minimum_growth),
                growth_only=effective_growth_only,
                growth_basis=growth_basis,
                industry_categories=list(industry_categories),
                contact_channels=contact_channels,
                discovery_type=discovery_type,
                recent_months=int(recent_months),
                include_comwel_annual=bool(include_comwel_annual),
                found_count=int(result.get("found_count") or 0),
                pages_scanned=int(result_stats.get("pages_scanned") or 0),
                elapsed_seconds=float(
                    result_stats.get("elapsed_seconds") or 0
                ),
            )
        except Exception as exc:
            result["history_warning"] = str(exc)
        st.session_state[result_state_key] = result
        st.session_state[selection_state_key] = [
            str(item.get("source_key") or "")
            for item in list(result.get("items") or [])
            if bool(item.get("선택", True))
            and str(item.get("source_key") or "").strip()
        ]
        st.session_state[result_revision_key] = (
            int(st.session_state.get(result_revision_key, 0) or 0) + 1
        )
        st.session_state[result_page_key] = 1
        st.session_state[page_state_key] = int(
            result.get("next_page") or int(end_page) + 1
        )
        st.session_state["_prospect_workflow_step_pending_v1020"] = (
            "② 검색 결과"
        )
        # Render the completed result immediately in this run. The pending
        # value synchronizes the selector on the next natural interaction,
        # avoiding a second full-app repaint at the end of a long search.
        workflow_step = "② 검색 결과"

    if workflow_step != "② 검색 결과":
        st.info(
            "조건을 선택해 조회하면 결과가 ‘② 검색 결과’ 단계에 "
            "표시됩니다. 저장한 업체는 ‘③ 저장된 영업후보’에서 "
            "관리할 수 있습니다."
        )
        return
    result = _sanitize_search_result(
        st.session_state.get(result_state_key) or {},
        can_view_mobile,
    )
    result = _limit_prospect_result_for_role(
        result,
        is_admin_user=is_admin_user,
    )
    st.session_state[result_state_key] = result
    if not result:
        st.info(
            "아직 표시할 검색 결과가 없습니다. ‘① 조건 설정’에서 "
            "조회 조건을 선택하고 검색을 실행해 주세요."
        )
        return
    if result:
        if not result.get("ok", True):
            if result.get("error_code") == "GROWTH_SEARCH_TIMEOUT":
                st.error("성장기업 검색 시간이 초과되었습니다.")
                st.info(
                    "지역이나 업종 등 조회 조건을 선택한 뒤 다시 "
                    "시도해 주세요. 문제가 계속되면 관리자에게 "
                    "검색 성능 확인을 요청해 주세요."
                )
            else:
                st.error("검색 자료를 불러오지 못했습니다.")
                st.info(
                    "잠시 후 다시 조회해 주세요. 같은 문제가 계속되면 "
                    "관리자에게 데이터 연결 상태 확인을 요청해 주세요."
                )
            with st.expander("오류 상세", expanded=False):
                st.caption(
                    result.get(
                        "message",
                        f"{discovery_type_name} 저장자료 조회 실패",
                    )
                )
            return
        stats = result.get("stats") or {}
        signal_count = stats.get(
            (
                "recent_candidates"
                if discovery_type == "recent_opening"
                else "growth_candidates"
            ),
            0,
        )
        metric_cols = st.columns(5)
        metric_cols[0].metric(
            "확인한 사업장",
            f"{stats.get('basic_received', 0):,}건",
        )
        metric_cols[1].metric(
            (
                "신규개업 추정"
                if discovery_type == "recent_opening"
                else "고용 증가 신호"
            ),
            f"{signal_count:,}건",
        )
        metric_cols[2].metric(
            "기존 DB 제외",
            f"{stats.get('saved_prospect_excluded', 0):,}건",
        )
        metric_cols[3].metric(
            "조회 결과",
            f"{result.get('found_count', 0):,}건",
        )
        if stats.get("source_mode") == "precomputed":
            metric_cols[4].metric("조회 방식", "사전 계산")
            search_range_text = " · ".join(
                (
                    (
                        f"{region_name} {district_name}"
                        if district_name != ALL_DISTRICTS
                        else f"{region_name} 전체"
                    ),
                    (
                        f"고용 {int(minimum_employees):,}"
                        f"~{int(maximum_employees):,}명"
                    ),
                    (
                        "연락처 "
                        + "·".join(selected_contact_labels or ["전체"])
                    ),
                )
            )
        else:
            metric_cols[4].metric(
                "다음 검색 페이지",
                f"{result.get('next_page', 1):,}",
            )
            search_range_text = (
                f"조회 페이지 "
                f"{result.get('searched_start_page', start_page)}"
                f"~{result.get('searched_end_page', end_page)}"
            )
        st.caption(
            f"우선순위: {result.get('priority_basis', '')} · "
            f"{search_range_text} · "
            f"사전 수집 연락처 결과 "
            f"{stats.get('contact_checked', 0):,}건 · "
            f"검색시간 {stats.get('elapsed_seconds', 0):,.1f}초"
        )
        if stats.get("source_mode") == "precomputed":
            st.info(
                (
                    "국민연금·근로복지공단 신규 신호와 사업자번호 "
                    "중복제거 인덱스를 사용했습니다. 사업자번호가 "
                    "없는 국민연금 사업장은 공단 사업장 식별키와 "
                    "상호·주소로 중복을 방지합니다. 행안부 자료는 "
                    "이 조회에 포함되지 않습니다."
                    if discovery_type == "recent_opening"
                    else (
                        "Supabase 사전 계산 목록과 지역·고용·업종 전용 "
                        "인덱스를 사용했습니다. 다른 담당자가 배정·"
                        "연락 중인 업체는 전사 상태 기준으로 제외됩니다."
                    )
                )
            )
        else:
            st.info(
                f"다음 검색 권장 시작 페이지는 "
                f"{result.get('next_page', int(end_page) + 1):,}입니다. "
                "다른 담당자가 배정·연락 중인 업체는 전사 상태 "
                "기준으로 제외됩니다."
            )
        if result.get("duplicate_warning"):
            st.warning(
                "기존 DB 중복확인 일부를 완료하지 못했습니다: "
                f"{result['duplicate_warning']}"
            )
        if result.get("assignment_warning"):
            st.warning(
                "전사 중복연락 방지 상태를 완전히 확인하지 못했습니다. "
                f"{result['assignment_warning']}"
            )
        own_excluded = int(stats.get("already_my_db_excluded") or 0)
        if own_excluded:
            st.info(
                f"이미 내 영업DB에 저장된 업체 {own_excluded:,}건은 "
                "신규 결과에서 제외했습니다. ③ 저장된 영업후보에서 "
                "기존 담당 상태와 연락이력을 확인할 수 있습니다."
            )
        if result.get("assignment_view_warning") and is_admin_user:
            st.caption(
                "업체 조회이력 기록 안내: "
                f"{result['assignment_view_warning']}"
            )
        if result.get("snapshot_warning"):
            st.warning(
                "월별 가입자 스냅샷 저장을 완료하지 못했습니다. 이번 "
                "발굴 결과와 기존 DB 제외에는 영향이 없습니다: "
                f"{result['snapshot_warning']}"
            )
        if result.get("history_warning"):
            st.warning(
                "검색 결과는 정상이며 검색이력 저장만 완료되지 "
                "않았습니다."
            )
            if is_admin_user:
                with st.expander("관리자용 오류 상세", expanded=False):
                    st.caption(result["history_warning"])

        items = list(result.get("items") or [])
        if not items:
            st.info(
                (
                    "선택한 지역·기간·고용인원·업종 범위에서 신규개업 "
                    "추정 신호와 연락처 조건을 모두 충족한 업체가 없습니다."
                    if discovery_type == "recent_opening"
                    else (
                        "선택한 지역·고용인원·업종 범위에서 고용 증가 "
                        "신호와 공개 연락처 조건을 모두 충족한 업체가 없습니다."
                    )
                )
                + " 필터 범위를 넓혀 다시 조회해 주세요."
            )
        else:
            st.markdown("### 이번에 찾은 영업후보")
            display = _display_frame(
                items,
                can_view_mobile=can_view_mobile,
            )
            display["대표전화"] = display["대표전화"].map(normalize_phone)
            display["휴대전화"] = display["휴대전화"].map(normalize_phone)
            display["일반전화"] = display["일반전화"].map(normalize_phone)
            excel_columns = [
                column
                for column in display.columns
                if column not in {"선택", "source_key"}
                and (
                    can_view_mobile
                    or column not in {"대표전화", "휴대전화"}
                )
            ]
            st.download_button(
                "이번 발굴결과 엑셀 다운로드",
                data=_excel_bytes(
                    display[excel_columns],
                    "DB발굴 결과",
                ),
                file_name=(
                    f"OASIS_DB발굴_{region_name}_"
                    f"{district_name}_연락처.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                use_container_width=True,
                key=(
                    "prospect_result_excel_v1012_"
                    f"{discovery_type}_{region_name}_{district_name}"
                ),
            )
            if selection_state_key not in st.session_state:
                st.session_state[selection_state_key] = [
                    str(value)
                    for value in display.loc[
                        display["선택"] == True,
                        "source_key",
                    ].tolist()
                    if str(value or "").strip()
                ]
            selected_keys = {
                str(value)
                for value in st.session_state.get(
                    selection_state_key,
                    [],
                )
                if str(value or "").strip()
            }
            visible_result_keys = {
                str(value)
                for value in display["source_key"].tolist()
                if str(value or "").strip()
            }
            selected_keys.intersection_update(visible_result_keys)
            st.session_state[selection_state_key] = sorted(selected_keys)
            page_size_key = (
                f"prospect_result_page_size_v1013_{discovery_type}"
            )
            page_control_cols = st.columns([1, 2])
            with page_control_cols[0]:
                page_size = st.selectbox(
                    "페이지당 표시",
                    list(PROSPECT_RESULT_PAGE_SIZE_OPTIONS),
                    index=1,
                    key=page_size_key,
                )
            current_page, page_total, _, _ = _result_page_window(
                len(display),
                int(st.session_state.get(result_page_key, 1) or 1),
                int(page_size),
            )
            if int(st.session_state.get(result_page_key, 1) or 1) != (
                current_page
            ):
                st.session_state[result_page_key] = current_page
            with page_control_cols[1]:
                page_number = st.selectbox(
                    "결과 페이지",
                    list(range(1, page_total + 1)),
                    format_func=lambda value: (
                        f"{value:,} / {page_total:,} 페이지"
                    ),
                    key=result_page_key,
                )
            page_number, page_total, page_start, page_end = (
                _result_page_window(
                    len(display),
                    int(page_number),
                    int(page_size),
                )
            )
            page_display = display.iloc[page_start:page_end].copy()
            page_display["선택"] = (
                page_display["source_key"].astype(str).isin(selected_keys)
            )
            st.caption(
                f"전체 {len(display):,}건 중 "
                f"{page_start + 1:,}~{page_end:,}건을 표시합니다. "
                "다른 페이지로 이동해도 선택은 유지됩니다."
            )
            visible_columns = [
                column
                for column in [
                    "선택",
                    "사업장명",
                    *(["휴대전화"] if can_view_mobile else []),
                    "일반전화",
                    "이메일",
                    "인스타그램URL",
                    "지역",
                    "가입자수",
                    *(
                        ["신규개업구분", "신규추정일"]
                        if discovery_type == "recent_opening"
                        else ["고용증가구분"]
                    ),
                    "업종분류",
                    "추천등급",
                    *((["담당자", "배정상태", "최근연락일"])
                      if is_admin_user else []),
                    "source_key",
                ]
                if column in display.columns
            ]
            edited = st.data_editor(
                page_display[visible_columns],
                use_container_width=True,
                hide_index=True,
                disabled=[
                    column
                    for column in visible_columns
                    if column != "선택"
                ],
                column_config={
                    "선택": st.column_config.CheckboxColumn("저장"),
                    **(
                        {
                            "휴대전화": st.column_config.TextColumn(
                                "휴대전화"
                            )
                        }
                        if can_view_mobile
                        else {}
                    ),
                    "일반전화": st.column_config.TextColumn("일반전화"),
                    "인스타그램URL": st.column_config.LinkColumn(
                        "인스타그램 링크"
                    ),
                    "초회전화스크립트": st.column_config.TextColumn(
                        "초회 전화 스크립트",
                        width="large",
                    ),
                    "source_key": None,
                },
                key=(
                    "prospect_editor_v1012_"
                    f"{discovery_type}_{region_name}_{district_name}_"
                    f"{int(st.session_state.get(result_revision_key, 0) or 0)}_"
                    f"{int(page_size)}_{page_number}"
                ),
            )
            page_keys = {
                str(value)
                for value in page_display["source_key"].tolist()
                if str(value or "").strip()
            }
            checked_page_keys = {
                str(value)
                for value in edited.loc[
                    edited["선택"] == True,
                    "source_key",
                ].tolist()
                if str(value or "").strip()
            }
            selected_keys = _merge_result_page_selection(
                selected_keys,
                page_keys,
                checked_page_keys,
            )
            st.session_state[selection_state_key] = sorted(selected_keys)
            if selected_keys:
                show_selected_detail = st.toggle(
                    f"선택 업체 {len(selected_keys):,}개의 상세정보 보기",
                    value=False,
                    key=(
                        "prospect_selected_detail_v1013_"
                        f"{discovery_type}"
                    ),
                )
                if show_selected_detail:
                    detail_columns = [
                        column
                        for column in [
                            "사업장명",
                            "사업자유형",
                            "사업자번호상태",
                            "이메일",
                            "인스타그램",
                            "인스타그램URL",
                            "주소",
                            "업종명",
                            "자료생성년월",
                            "신규근거",
                            "영업주제",
                            "초회전화스크립트",
                        ]
                        if column in display.columns
                    ]
                    selected_detail = display[
                        display["source_key"].astype(str).isin(selected_keys)
                    ]
                    detail_column_config = {}
                    if "인스타그램URL" in detail_columns:
                        detail_column_config["인스타그램URL"] = (
                            st.column_config.LinkColumn("인스타그램 링크")
                        )
                    st.dataframe(
                        selected_detail[detail_columns],
                        hide_index=True,
                        use_container_width=True,
                        column_config=detail_column_config,
                    )
            selected_items = [
                item
                for item in items
                if str(item.get("source_key") or "") in selected_keys
                and (
                    normalize_phone(item.get("대표전화"))
                    or str(item.get("이메일") or "").strip()
                    or str(item.get("인스타그램") or "").strip()
                )
            ]
            assignment_ready = bool(
                result.get("assignment_feature_ready")
            )
            if "assignment_feature_ready" not in result:
                assignment_ready = _assignment_feature_status()[0]
            if st.button(
                f"선택한 {len(selected_items):,}개 업체 내 영업DB에 담기",
                type="primary",
                use_container_width=True,
                disabled=not selected_items or not assignment_ready,
                key=f"save_prospects_v1012_{discovery_type}",
            ):
                try:
                    save_result = save_assigned_prospects(
                        selected_items,
                        owner_user_id,
                        session_id=_assignment_session_id(),
                    )
                    saved_count = int(save_result.get("saved_count") or 0)
                    already_owned_count = int(
                        save_result.get("already_owned_count") or 0
                    )
                    newly_saved_count = max(
                        0,
                        saved_count - already_owned_count,
                    )
                    failures = [
                        row
                        for row in (save_result.get("results") or [])
                        if not row.get("ok")
                    ]
                    save_notices: list[dict[str, str]] = []
                    if newly_saved_count:
                        save_notices.append(
                            {
                                "level": "success",
                                "message": (
                                    "저장 완료: "
                                    f"{newly_saved_count:,}개 업체를 내 "
                                    "영업DB에 담았습니다. 24시간 안에 "
                                    "연락결과를 기록해 주세요."
                                ),
                            }
                        )
                    if already_owned_count:
                        save_notices.append(
                            {
                                "level": "info",
                                "message": (
                                    f"{already_owned_count:,}개 업체는 이미 "
                                    "내 영업DB에 있어 중복 저장하지 "
                                    "않았습니다. ③ 저장된 영업후보에서 "
                                    "기존 상세정보를 확인할 수 있습니다."
                                ),
                            }
                        )
                    failure_codes = {
                        str(row.get("code") or "") for row in failures
                    }
                    if failure_codes & {
                        "ASSIGNMENT_CONFLICT",
                        "ALREADY_ASSIGNED",
                    }:
                        save_notices.append(
                            {
                                "level": "warning",
                                "message": (
                                    "다른 담당자가 먼저 배정받은 "
                                    "업체입니다. 검색 결과를 "
                                    "새로고침합니다."
                                ),
                            }
                        )
                    if failure_codes & {
                        "MAX_UNCONTACTED_REACHED",
                        "LIMIT_REACHED",
                        "UNCONTACTED_LIMIT_REACHED",
                    }:
                        save_notices.append(
                            {
                                "level": "warning",
                                "message": (
                                    "미접촉 배정 DB는 최대 30개까지 "
                                    "보유할 수 있습니다. 기존 DB의 "
                                    "연락결과를 기록하거나 배정을 "
                                    "해제한 후 다시 시도해 주세요."
                                ),
                            }
                        )
                    for row in failures:
                        if str(row.get("code") or "") not in {
                            "ASSIGNMENT_CONFLICT",
                            "ALREADY_ASSIGNED",
                            "MAX_UNCONTACTED_REACHED",
                            "LIMIT_REACHED",
                            "UNCONTACTED_LIMIT_REACHED",
                        }:
                            save_notices.append(
                                {
                                    "level": "warning",
                                    "message": str(
                                        row.get("message") or "저장 실패"
                                    ),
                                }
                            )

                    saved_uids = {
                        str((row.get("assignment") or {}).get("company_uid") or "")
                        for row in (save_result.get("results") or [])
                        if row.get("ok")
                    }
                    result["items"] = [
                        item
                        for item in items
                        if str(item.get("company_uid") or "") not in saved_uids
                    ]
                    result["found_count"] = len(result["items"])
                    # A fresh save result is already shown once through the
                    # flash notice.  Do not repeat a stale assignment warning
                    # from the cached search result below it after rerun.
                    result.pop("assignment_warning", None)
                    st.session_state[result_state_key] = result
                    st.session_state[selection_state_key] = []
                    st.session_state[result_revision_key] = int(
                        st.session_state.get(result_revision_key, 0) or 0
                    ) + 1
                    if save_notices:
                        st.session_state[_PROSPECT_SAVE_FLASH_KEY] = (
                            save_notices
                        )
                    st.rerun()
                except Exception as exc:
                    st.error(safe_public_error(exc, "영업후보 저장에 실패했습니다."))

            if assignment_ready and len(selected_items) == 1:
                with st.expander(
                    "선택 업체 연락결과 바로 기록",
                    expanded=False,
                ):
                    st.caption(
                        "저장과 동시에 임시 배정한 뒤 연락결과를 기록합니다. "
                        "다른 담당자가 먼저 배정받았다면 저장되지 않습니다."
                    )
                    with st.form(
                        f"search_contact_record_v989_{discovery_type}"
                    ):
                        quick_col1, quick_col2 = st.columns(2)
                        quick_method = quick_col1.selectbox(
                            "연락방식",
                            CONTACT_METHOD_OPTIONS,
                            key=f"quick_method_v989_{discovery_type}",
                        )
                        quick_result = quick_col2.selectbox(
                            "연락결과",
                            CONTACT_RESULT_OPTIONS,
                            key=f"quick_result_v989_{discovery_type}",
                        )
                        quick_schedule = st.checkbox(
                            "다음 연락예정일 지정",
                            value=False,
                            key=f"quick_schedule_v989_{discovery_type}",
                        )
                        quick_next_date = st.date_input(
                            "다음 연락예정일",
                            disabled=not quick_schedule,
                            help="‘재연락 요청’ 선택 시 반드시 지정합니다.",
                            key=f"quick_date_v989_{discovery_type}",
                        )
                        quick_notes = st.text_area(
                            "상담내용",
                            max_chars=10_000,
                            key=f"quick_notes_v989_{discovery_type}",
                        )
                        quick_submitted = st.form_submit_button(
                            "내 영업DB에 담고 연락결과 기록",
                            type="primary",
                            use_container_width=True,
                        )
                    if quick_submitted:
                        if (
                            quick_result == "재연락 요청"
                            and not quick_schedule
                        ):
                            st.error(
                                "재연락 요청은 다음 연락예정일을 입력해 주세요."
                            )
                        else:
                            quick_item = selected_items[0]
                            quick_claim = save_assigned_prospects(
                                [quick_item],
                                owner_user_id,
                                session_id=_assignment_session_id(),
                            )
                            quick_rows = list(
                                quick_claim.get("results") or []
                            )
                            quick_row = quick_rows[0] if quick_rows else {}
                            if not quick_row.get("ok"):
                                st.warning(
                                    quick_row.get("message")
                                    or quick_claim.get("message")
                                    or "업체를 배정하지 못했습니다."
                                )
                            else:
                                quick_assignment = dict(
                                    quick_row.get("assignment") or {}
                                )
                                quick_uid = str(
                                    quick_assignment.get("company_uid")
                                    or quick_item.get("company_uid")
                                    or sales_assignments.build_company_uid(
                                        quick_item
                                    )
                                )
                                quick_contact = (
                                    sales_assignments.record_contact(
                                        owner_user_id,
                                        quick_assignment.get("company_id"),
                                        quick_uid,
                                        quick_method,
                                        quick_result,
                                        notes=quick_notes,
                                        next_contact_at=(
                                            quick_next_date.isoformat()
                                            if quick_schedule
                                            else None
                                        ),
                                        session_id=_assignment_session_id(),
                                    )
                                )
                                if quick_contact.get("ok"):
                                    st.success(quick_contact.get("message"))
                                    result["items"] = [
                                        item
                                        for item in items
                                        if str(item.get("company_uid") or "")
                                        != quick_uid
                                    ]
                                    result["found_count"] = len(
                                        result["items"]
                                    )
                                    st.session_state[result_state_key] = result
                                    st.session_state[selection_state_key] = []
                                    st.session_state[result_revision_key] = int(
                                        st.session_state.get(
                                            result_revision_key, 0
                                        )
                                        or 0
                                    ) + 1
                                else:
                                    st.error(quick_contact.get("message"))

        failures = result.get("failures") or []
        if failures:
            with st.expander("검색 중 확인하지 못한 항목"):
                st.dataframe(
                    pd.DataFrame(failures),
                    use_container_width=True,
                    hide_index=True,
                )

def render_company_assignment_admin(current_user_id: str = "") -> None:
    """Admin-only company assignment, limit and audit management screen."""
    from auth import is_admin, list_all_users_for_admin

    if not is_admin(current_user_id):
        st.error("관리자 권한이 필요합니다.")
        return

    st.markdown("## 전사 영업배정 관리")
    st.caption(
        "DB발굴 업체의 현재 담당자·연락상태를 확인하고, 담당자 변경·"
        "강제 회수·재활성화·영구 제외를 처리합니다. 모든 변경은 "
        "감사로그에 남습니다."
    )
    ready, ready_message = _assignment_feature_status()
    if not ready:
        st.warning(ready_message)
        st.info(
            "supabase_v1032_company_sales_assignments.sql을 적용한 뒤 "
            "이 화면을 다시 열어 주세요. 적용 전에는 기존 DB발굴 "
            "중복 제외 기능이 그대로 유지됩니다."
        )
        return

    _release_expired_assignments_if_due(current_user_id)
    metrics_result = sales_assignments.list_admin_assignment_metrics(
        current_user_id
    )
    metric_rows = (
        list(metrics_result.get("metrics") or [])
        if metrics_result.get("ok")
        else []
    )
    global_total = max(
        (int(row.get("total_assignment_count") or 0) for row in metric_rows),
        default=0,
    )
    page_control_col1, page_control_col2 = st.columns([1, 2])
    page_size = page_control_col1.selectbox(
        "페이지당 업체",
        [100, 200, 500],
        index=1,
        key="assignment_admin_page_size_v989",
    )
    page_count = max(1, (global_total + int(page_size) - 1) // int(page_size))
    page_state_key = "assignment_admin_page_v989"
    if page_state_key not in st.session_state:
        st.session_state[page_state_key] = 1
    if int(st.session_state.get(page_state_key, 1) or 1) > page_count:
        st.session_state[page_state_key] = page_count
    page_number = page_control_col2.number_input(
        "페이지",
        min_value=1,
        max_value=page_count,
        step=1,
        key=page_state_key,
    )
    assignment_result = sales_assignments.list_admin_assignments(
        current_user_id,
        limit=int(page_size),
        offset=(int(page_number) - 1) * int(page_size),
    )
    if not assignment_result.get("ok"):
        st.error(assignment_result.get("message") or "배정현황 조회 실패")
        return
    rows = list(assignment_result.get("assignments") or [])

    total_count = int(
        assignment_result.get("total_count") or global_total or len(rows)
    )
    uncontacted_count = sum(
        int(row.get("uncontacted_count") or 0) for row in metric_rows
    )
    contacted_count = sum(
        int(row.get("contacted_count") or 0) for row in metric_rows
    )
    long_unprocessed_count = sum(
        int(row.get("long_unprocessed_count") or 0) for row in metric_rows
    )
    duplicate_attempt_count = max(
        (
            int(row.get("global_duplicate_assignment_attempt_count") or 0)
            for row in metric_rows
        ),
        default=sum(
            int(row.get("duplicate_attempt_count") or 0)
            for row in metric_rows
        ),
    )
    migration_conflict_count = max(
        (
            int(row.get("global_migration_conflict_count") or 0)
            for row in metric_rows
        ),
        default=sum(1 for row in rows if row.get("migration_conflict")),
    )
    metric_cols = st.columns(4)
    metric_cols[0].metric("전체 배정상태", f"{total_count:,}건")
    metric_cols[1].metric("미접촉 임시배정", f"{uncontacted_count:,}건")
    metric_cols[2].metric("연락기록 보유", f"{contacted_count:,}건")
    metric_cols[3].metric(
        "장기 미처리 / 중복시도",
        f"{long_unprocessed_count:,}건 / {duplicate_attempt_count:,}회",
    )
    st.caption(
        f"전체 {total_count:,}건 중 {int(page_number):,}/{page_count:,}페이지 · "
        f"기존 이관 충돌 {migration_conflict_count:,}건"
    )

    salesperson_metrics = [
        {
            "영업사원": row.get("assigned_user_name")
            or row.get("assigned_user_id"),
            "미접촉 배정 DB": int(row.get("uncontacted_count") or 0),
            "연락완료 DB": int(row.get("contacted_count") or 0),
            "장기 미처리 DB": int(
                row.get("long_unprocessed_count") or 0
            ),
            "중복 배정 시도": int(
                row.get("duplicate_attempt_count") or 0
            ),
        }
        for row in metric_rows
        if row.get("assigned_user_id")
    ]
    if salesperson_metrics:
        st.dataframe(
            pd.DataFrame(salesperson_metrics),
            use_container_width=True,
            hide_index=True,
        )
    elif not metrics_result.get("ok"):
        st.warning(
            metrics_result.get("message")
            or "전사 전체 통계를 불러오지 못했습니다."
        )

    if rows:
        admin_frame = pd.DataFrame(
            [
                {
                    "업체": row.get("company_name") or row.get("company_uid"),
                    "담당자": row.get("assigned_user_name")
                    or row.get("assigned_user_id")
                    or "미배정",
                    "상태": sales_assignments.assignment_status_label(
                        row.get("status")
                    ),
                    "최초 조회자": row.get("first_viewer_user_name")
                    or row.get("first_viewer_user_id")
                    or "-",
                    "최초 배정자": row.get("first_assigned_user_name")
                    or row.get("first_assigned_user_id")
                    or "-",
                    "최초 연락자": row.get("first_contacted_user_name")
                    or row.get("first_contacted_user_id")
                    or "-",
                    "최초 연락일": str(row.get("first_contacted_at") or "")
                    .replace("T", " ")[:16],
                    "최근 연락일": str(row.get("last_contacted_at") or "")
                    .replace("T", " ")[:16],
                    "연락횟수": int(row.get("contact_count") or 0),
                    "다음 연락일": str(row.get("next_contact_at") or "")
                    .replace("T", " ")[:16],
                    "임시배정 만료": str(
                        row.get("assignment_expires_at") or ""
                    ).replace("T", " ")[:16],
                    "중복배정 시도": int(
                        row.get("duplicate_attempt_count") or 0
                    ),
                    "이관충돌": (
                        "담당자 확인 필요"
                        if row.get("migration_conflict")
                        else ""
                    ),
                    "기존 저장 사용자": ", ".join(
                        str(value)
                        for value in (row.get("conflicting_user_ids") or [])
                    ),
                }
                for row in rows
            ]
        )
        st.dataframe(
            admin_frame,
            use_container_width=True,
            hide_index=True,
            height=min(620, 72 + len(admin_frame) * 36),
        )
        conflict_rows = [
            row for row in rows if bool(row.get("migration_conflict"))
        ]
        if conflict_rows:
            with st.expander(
                f"현재 페이지 이관 충돌 {len(conflict_rows):,}건",
                expanded=False,
            ):
                st.caption(
                    "기존에 여러 사용자에게 저장된 업체입니다. 아래 담당 "
                    "상태 변경에서 최종 담당자를 선택하면 원본 자료는 "
                    "삭제하지 않고 충돌만 해결됩니다."
                )
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "업체": row.get("company_name")
                                or row.get("company_uid"),
                                "기존 저장 사용자": ", ".join(
                                    str(value)
                                    for value in (
                                        row.get("conflicting_user_ids") or []
                                    )
                                ),
                                "상태": row.get(
                                    "conflict_resolution_status"
                                )
                                or "pending",
                            }
                            for row in conflict_rows
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )
    else:
        st.info("현재 전사 영업배정 상태가 등록된 업체가 없습니다.")

    users = [
        row
        for row in list_all_users_for_admin(current_user_id)
        if str(row.get("상태") or "approved") == "approved"
    ]
    user_labels = {
        f"{row.get('이름') or row.get('아이디')} · {row.get('아이디')}": str(
            row.get("아이디") or ""
        )
        for row in users
        if str(row.get("아이디") or "")
    }

    if rows:
        st.markdown("### 담당 상태 변경")
        company_labels: dict[str, dict] = {}
        for index, row in enumerate(rows, start=1):
            company_uid = str(row.get("company_uid") or "")
            label = (
                f"{row.get('company_name') or '업체명 미확인'} · "
                f"{sales_assignments.assignment_status_label(row.get('status'))} · "
                f"{company_uid[-10:]} · {index}"
            )
            company_labels[label] = row
        selected_company_label = st.selectbox(
            "대상 업체",
            list(company_labels),
            key="assignment_admin_company_v989",
        )
        selected_company = company_labels[selected_company_label]
        with st.form("assignment_admin_action_form_v989"):
            action = st.selectbox(
                "관리 작업",
                [
                    "담당자 변경",
                    "임시 배정 강제 해제",
                    "담당자 강제 회수",
                    "재활성화",
                    "영구 제외",
                ],
            )
            selected_target_label = st.selectbox(
                "변경할 담당자",
                list(user_labels) or ["승인된 사용자가 없습니다"],
                disabled=action != "담당자 변경" or not user_labels,
            )
            reason = st.text_input(
                "변경 사유",
                max_chars=500,
                placeholder="감사로그에 기록할 사유를 입력하세요.",
            )
            action_submitted = st.form_submit_button(
                "변경 적용",
                type="primary",
                use_container_width=True,
            )
        if action_submitted:
            if not reason.strip():
                st.error("변경 사유를 입력해 주세요.")
            elif action == "담당자 변경" and not user_labels:
                st.error("변경할 승인 사용자가 없습니다.")
            else:
                company_id = selected_company.get("company_id") or ""
                company_uid = selected_company.get("company_uid") or ""
                session_id = _assignment_session_id()
                if action == "담당자 변경":
                    action_result = sales_assignments.admin_change_assignee(
                        current_user_id,
                        company_id,
                        company_uid,
                        user_labels[selected_target_label],
                        reason=reason,
                        session_id=session_id,
                    )
                elif action in {"임시 배정 강제 해제", "담당자 강제 회수"}:
                    action_result = sales_assignments.admin_release_assignment(
                        current_user_id,
                        company_id,
                        company_uid,
                        reason=f"{action}: {reason}",
                        session_id=session_id,
                    )
                elif action == "재활성화":
                    action_result = sales_assignments.admin_reactivate(
                        current_user_id,
                        company_id,
                        company_uid,
                        reason=reason,
                        session_id=session_id,
                    )
                else:
                    action_result = sales_assignments.admin_permanent_exclude(
                        current_user_id,
                        company_id,
                        company_uid,
                        reason=reason,
                        session_id=session_id,
                    )
                if action_result.get("ok"):
                    st.success(action_result.get("message"))
                else:
                    st.error(action_result.get("message"))

    st.markdown("### 영업사원별 미접촉 배정 한도")
    if user_labels:
        with st.form("assignment_user_limit_form_v989"):
            limit_user_label = st.selectbox(
                "영업사원",
                list(user_labels),
            )
            max_uncontacted = st.number_input(
                "미접촉 임시배정 최대 건수",
                min_value=1,
                max_value=1000,
                value=30,
                step=1,
            )
            limit_reason = st.text_input(
                "한도 변경 사유",
                max_chars=500,
            )
            limit_submitted = st.form_submit_button(
                "한도 저장",
                use_container_width=True,
            )
        if limit_submitted:
            if not limit_reason.strip():
                st.error("한도 변경 사유를 입력해 주세요.")
            else:
                limit_result = sales_assignments.admin_set_user_limit(
                    current_user_id,
                    user_labels[limit_user_label],
                    int(max_uncontacted),
                    limit_reason,
                    _assignment_session_id(),
                )
                if limit_result.get("ok"):
                    st.success(limit_result.get("message"))
                else:
                    st.error(limit_result.get("message"))
    else:
        st.info("한도를 설정할 승인 사용자가 없습니다.")

    with st.expander("배정·연락 감사로그", expanded=False):
        audit_result = sales_assignments.list_admin_assignment_audit(
            current_user_id,
            limit=500,
        )
        if audit_result.get("ok") and audit_result.get("audit"):
            audit_frame = pd.DataFrame(audit_result["audit"])
            st.dataframe(
                audit_frame,
                use_container_width=True,
                hide_index=True,
            )
        elif audit_result.get("ok"):
            st.info("기록된 감사로그가 없습니다.")
        else:
            st.error(audit_result.get("message") or "감사로그 조회 실패")

    with st.expander("전체 연락이력", expanded=False):
        admin_contact_result = sales_assignments.list_company_contacts(
            current_user_id,
            limit=1000,
        )
        if admin_contact_result.get("ok") and admin_contact_result.get(
            "contacts"
        ):
            st.dataframe(
                pd.DataFrame(admin_contact_result["contacts"]),
                use_container_width=True,
                hide_index=True,
            )
        elif admin_contact_result.get("ok"):
            st.info("기록된 연락이력이 없습니다.")
        else:
            st.error(admin_contact_result.get("message"))


def render_prospect_admin_settings(current_user_id: str = "") -> None:
    from auth import is_admin

    if not is_admin(current_user_id):
        st.error("관리자 권한이 필요합니다.")
        return
    st.markdown("## 데이터 연결 관리")
    st.caption(
        "국민연금·카카오·네이버·인허가 API와 Supabase 테이블을 "
        "관리자가 점검하는 화면입니다."
    )
    nps_status = service_key_status()
    contact_status = api_statuses()
    status_cols = st.columns(4)
    status_cols[0].metric(
        "국민연금",
        "키 등록" if nps_status["configured"] else "미등록",
    )
    status_cols[1].metric(
        "카카오",
        "키 등록" if contact_status["kakao"]["configured"] else "미등록",
    )
    status_cols[2].metric(
        "네이버",
        "키 등록" if contact_status["naver"]["configured"] else "미등록",
    )
    status_cols[3].metric(
        "인허가",
        (
            f"{contact_status['localdata'].get('service_count', 0)}종"
            if contact_status["localdata"]["configured"]
            else (
                "사용 안 함"
                if contact_status["localdata"].get("disabled")
                else "미등록"
            )
        ),
    )

    test_col1, test_col2, test_col3 = st.columns(3)
    nps_test = test_col1.button(
        "국민연금 연결 점검",
        use_container_width=True,
        disabled=not nps_status["configured"],
        key="admin_nps_test_v980",
    )
    contact_test = test_col2.button(
        "연락처 API 연결 점검",
        use_container_width=True,
        disabled=not (
            contact_status["kakao"].get("configured")
            and contact_status["naver"].get("configured")
        ),
        key="admin_contact_test_v980",
    )
    all_license_test = test_col3.button(
        "인허가 195종 승인 점검",
        use_container_width=True,
        disabled=not contact_status["localdata"]["configured"],
        key="admin_localdata_all_test_v990",
        help=(
            "공공데이터포털 195개 인허가 API를 병렬 점검합니다. "
            "완료까지 약 1분이 걸릴 수 있습니다."
        ),
    )
    if nps_test:
        st.session_state["admin_nps_result_v980"] = test_nps_connection("11")
    if contact_test:
        st.session_state["admin_contact_result_v980"] = test_connections()
    if all_license_test:
        with st.spinner("인허가 API 195종의 승인 상태를 확인하고 있습니다..."):
            st.session_state["admin_localdata_all_result_v990"] = (
                localdata_contact_client.test_services(
                    timeout=8,
                    workers=20,
                )
            )

    nps_result = st.session_state.get("admin_nps_result_v980")
    if nps_result:
        st.success(nps_result.get("message", "연결 완료")) if nps_result.get(
            "ok"
        ) else st.error(nps_result.get("message", "연결 실패"))
    contact_result = st.session_state.get("admin_contact_result_v980")
    if contact_result:
        rows = []
        for key, label in (
            ("kakao", "카카오 로컬"),
            ("naver", "네이버 검색"),
            ("localdata", "승인 인허가 API"),
        ):
            source = (contact_result.get("sources") or {}).get(key) or {}
            rows.append(
                {
                    "연결": label,
                    "상태": source.get("status", "-"),
                    "결과": source.get("message", "-"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    license_result = st.session_state.get("admin_localdata_all_result_v990")
    if license_result:
        license_rows = [
            {
                "분류": localdata_contact_client.SERVICES.get(
                    str(row.get("service") or ""), {}
                ).get("category", ""),
                "업종": row.get("label", ""),
                "상태": row.get("status", ""),
                "결과": row.get("message", ""),
            }
            for row in license_result.get("services", [])
        ]
        connected_count = sum(
            1 for row in license_result.get("services", []) if row.get("ok")
        )
        st.info(
            f"전체 195종 중 {connected_count:,}종 연결 가능 · "
            f"{len(license_rows) - connected_count:,}종 확인 필요"
        )
        st.dataframe(
            pd.DataFrame(license_rows),
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "인허가 API 승인 점검결과 다운로드",
            data=pd.DataFrame(license_rows).to_csv(
                index=False,
                encoding="utf-8-sig",
            ),
            file_name="인허가_API_195종_승인점검.csv",
            mime="text/csv",
            use_container_width=True,
            key="admin_localdata_all_csv_v990",
        )

    st.markdown("### 인허가 원천업체 수집")
    license_categories = sorted(
        {
            str(row.get("category") or "")
            for row in localdata_contact_client.SERVICES.values()
            if str(row.get("category") or "")
        }
    )
    selected_license_categories = st.multiselect(
        "수집할 인허가 분류",
        license_categories,
        default=[],
        help="선택한 분류에 포함된 업종을 Supabase 원천업체DB로 수집합니다.",
        key="admin_license_categories_v990",
    )
    selected_license_services = [
        key
        for key, row in localdata_contact_client.SERVICES.items()
        if str(row.get("category") or "") in selected_license_categories
    ]
    st.markdown("#### 수집 지역")
    region_col1, region_col2 = st.columns(2)
    selected_license_province = region_col1.selectbox(
        "시·도",
        province_options(),
        key="admin_license_province_v991",
    )
    license_district_options = district_options(selected_license_province)
    selected_license_district = region_col2.selectbox(
        district_label(selected_license_province),
        license_district_options,
        key=f"admin_license_district_v991_{selected_license_province}",
        disabled=(
            selected_license_province == ALL_PROVINCES
            or len(license_district_options) == 1
        ),
    )
    if selected_license_province == ALL_PROVINCES:
        selected_license_district = ALL_DISTRICTS
    selected_region_label = (
        "전국"
        if selected_license_province == ALL_PROVINCES
        else " ".join(
            value
            for value in (
                selected_license_province,
                (
                    selected_license_district
                    if selected_license_district != ALL_DISTRICTS
                    else ""
                ),
            )
            if value
        )
    )
    st.caption(
        "선택 지역을 API 주소검색 조건에 적용하고, 응답 주소를 다시 검증해 "
        "다른 지역 업체는 저장하지 않습니다."
    )

    license_sync_mode_label = st.radio(
        "수집 방식",
        ["최초·정기 전수 수집", "변경분만 빠른 갱신"],
        horizontal=True,
        key="admin_license_sync_mode_v992",
        help=(
            "처음에는 전수 수집으로 오래 영업한 업체까지 확보하세요. "
            "그다음부터는 변경분 갱신을 사용하고, 월 1회 전수 수집을 권장합니다."
        ),
    )
    license_sync_mode = (
        "incremental"
        if license_sync_mode_label == "변경분만 빠른 갱신"
        else "full"
    )
    if license_sync_mode == "incremental":
        st.info(
            "이 지역·업종의 이전 수집 기록이 없으면 자동으로 전수 수집부터 "
            "실행합니다. 기존 DB에 들어간 업체는 삭제되지 않습니다."
        )

    sync_col1, sync_col2 = st.columns(2)
    license_pages = sync_col1.number_input(
        "업종별 최대 페이지",
        min_value=1,
        max_value=100,
        value=10 if selected_license_province != ALL_PROVINCES else 1,
        step=1,
        key=f"admin_license_pages_v991_{selected_license_province}",
        help=(
            "지역 검색을 지원하지 않는 일부 업종은 페이지를 순서대로 확인한 뒤 "
            "주소로 걸러냅니다. 지역 수집은 10페이지 이상을 권장합니다."
        ),
    )
    estimated_license_calls = (
        len(selected_license_services) * int(license_pages)
    )
    if selected_license_services:
        st.caption(
            f"최대 API 호출량: {len(selected_license_services):,}개 업종 × "
            f"{int(license_pages):,}페이지 = "
            f"{estimated_license_calls:,}회 (업종 8개씩 동시 수집, "
            "데이터가 끝난 업종은 즉시 종료)"
        )
    start_license_sync = sync_col2.button(
        (
            f"{selected_region_label} · {license_sync_mode_label} · "
            f"{len(selected_license_services):,}개 업종 수집"
        ),
        type="primary",
        use_container_width=True,
        disabled=not selected_license_services,
        key="admin_license_sync_v990",
    )
    if start_license_sync:
        status_ok, status_message = license_table_status()
        if not status_ok:
            st.error(
                "Supabase에 인허가 원천업체 테이블을 먼저 생성해 주세요. "
                f"{status_message}"
            )
        else:
            progress_bar = st.progress(0.0, text="인허가 업체 수집 준비 중")

            def update_license_progress(stats: dict) -> None:
                total = max(1, int(stats.get("service_count") or 1))
                current = int(stats.get("service_index") or 0)
                progress_bar.progress(
                    min(1.0, current / total),
                    text=(
                        f"{current:,}/{total:,}개 업종 · "
                        f"{int(stats.get('saved') or 0):,}건 저장"
                    ),
                )

            result = sync_license_services(
                selected_license_services,
                max_pages_per_service=int(license_pages),
                rows_per_page=1000,
                province=selected_license_province,
                district=selected_license_district,
                sync_mode=license_sync_mode,
                workers=8,
                progress=update_license_progress,
            )
            progress_bar.progress(1.0, text="인허가 업체 수집 완료")
            st.success(
                f"{selected_region_label} · "
                f"{result['service_count']:,}개 업종 · "
                f"{result['received']:,}건 수신 · "
                f"{result['saved']:,}건 저장"
            )
            if result.get("full_fallback_services"):
                st.info(
                    f"기준 데이터가 없던 "
                    f"{int(result['full_fallback_services']):,}개 업종은 "
                    "누락 방지를 위해 전수 수집으로 자동 전환했습니다."
                )
            if result.get("incomplete_services"):
                st.warning(
                    f"{int(result['incomplete_services']):,}개 업종은 설정한 최대 "
                    "페이지까지 데이터가 계속 있어 전수 수집이 아직 끝나지 "
                    "않았습니다. 최대 페이지를 늘려 다시 실행해 주세요."
                )
            if result.get("region_filtered"):
                st.caption(
                    f"다른 지역으로 확인된 "
                    f"{int(result['region_filtered']):,}건은 저장에서 제외했습니다."
                )
            if result["failures"]:
                st.warning(
                    f"{len(result['failures']):,}개 호출은 승인 또는 연결 확인이 "
                    "필요합니다."
                )
                st.dataframe(
                    pd.DataFrame(result["failures"]),
                    use_container_width=True,
                    hide_index=True,
                )

    st.markdown("### Supabase 영업후보 테이블")
    prospect_status = prospect_table_status()
    contacts_status = contact_table_status()
    history_status = search_history_table_status()
    db_cols = st.columns(3)
    db_cols[0].success(prospect_status[1]) if prospect_status[0] else db_cols[
        0
    ].warning(prospect_status[1])
    db_cols[1].success(contacts_status[1]) if contacts_status[0] else db_cols[
        1
    ].warning(contacts_status[1])
    db_cols[2].success(history_status[1]) if history_status[0] else db_cols[
        2
    ].warning(history_status[1])
    license_status = license_table_status()
    st.success(license_status[1]) if license_status[0] else st.warning(
        "인허가 원천업체 테이블 준비 필요: "
        "아래 v9.9.0 SQL을 Supabase에서 실행해 주세요."
    )

    for path_name, label in (
        ("supabase_v960_prospect_db.sql", "영업후보DB SQL 다운로드"),
        (
            "supabase_v970_contact_enrichment.sql",
            "연락처DB SQL 다운로드",
        ),
        (
            "supabase_v984_db_discovery.sql",
            "DB발굴 검색이력·메모 SQL 다운로드",
        ),
        (
            "supabase_v990_licensed_businesses.sql",
            "인허가 195종 원천업체DB SQL 다운로드",
        ),
    ):
        path = BASE_DIR / path_name
        if path.exists():
            st.download_button(
                label,
                data=path.read_bytes(),
                file_name=path_name,
                mime="text/plain",
                use_container_width=True,
                key=f"admin_download_{path_name}",
            )
