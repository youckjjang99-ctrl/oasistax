from __future__ import annotations

import hashlib
import html
import re
import time
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import streamlit as st

from claim_correction_catalog import DOCUMENT_SPECS
from claim_correction_repository import (
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


CONSENT_VERSION = "claim-collection-v1-2026-07"
RETENTION_POLICY_VERSION = "claim-document-retention-v1-2026-07"
CONSENT_NOTICE_TEXT = (
    "고객에게 수집 항목·이용 목적·보유기간·제3자 제공 내용을 안내했고 "
    "유효한 동의를 확인했습니다."
)
COLLECTION_AUTHORITY_TEXT = (
    "민감정보 처리가 필요한 경우 적용되는 법적 근거와 위임 범위를 "
    "확인했습니다."
)
CONSENT_TEXT_SHA256 = hashlib.sha256(
    f"{CONSENT_NOTICE_TEXT}|{COLLECTION_AUTHORITY_TEXT}".encode("utf-8")
).hexdigest()
AUTH_TTL_SECONDS = 10 * 60
STATUS_LABELS = {
    "request_ready": "발송 준비",
    "auth_preparing": "인증 준비",
    "auth_requested": "고객 인증 대기",
    "auth_pending": "고객 인증 대기",
    "auth_complete": "인증 완료",
    "auth_partial": "일부 인증 완료 · 재요청 필요",
    "certificate_required": "공동인증서 대기",
    "collection_queued": "자료수집 대기",
    "auth_complete_collection_pending": "인증 완료 · 자료수집 연동 대기",
    "integration_required": "문서 API 연결 대기",
    "collecting": "자료수집 중",
    "collected": "수집 완료",
    "ready": "결과 확인 가능",
    "failed": "실패",
    "not_requested": "제외",
}


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return text


def _digits(value: Any) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


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


def _session_bucket(user_id: str) -> dict[str, dict[str, Any]]:
    owner = str(user_id or "").strip().lower()
    current_owner = str(
        st.session_state.get("_claim_auth_owner_v1", "")
    ).strip().lower()
    if current_owner != owner:
        st.session_state.pop("_claim_auth_sessions_v1", None)
        st.session_state["_claim_auth_owner_v1"] = owner
    bucket = st.session_state.setdefault("_claim_auth_sessions_v1", {})
    now = time.time()
    expired = [
        case_id
        for case_id, value in bucket.items()
        if float(value.get("expires_at", 0) or 0) <= now
    ]
    for case_id in expired:
        bucket.pop(case_id, None)
    return bucket


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


def _case_label(row: dict[str, Any]) -> str:
    company = _clean(row.get("company_name")) or "업체명 없음"
    requested = _clean(row.get("requested_at"))
    date_text = requested[:10] if requested else "-"
    return f"{company} · {date_text} · {str(row.get('id', ''))[:8]}"


def _selected_customer(
    user_id: str,
    input_mode: str,
) -> tuple[dict[str, Any], str]:
    if input_mode == "직접 입력":
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
                개인사업자는 카카오 인증을 홈택스와 근로복지공단에 각각
                요청하고, 법인사업자는 공동인증서 인증 완료 후 자료를
                수집합니다.
            </p>
        </div>
        <div class="claim-flow">
            <div class="claim-step"><span>01</span>고객정보 입력</div>
            <div class="claim-step"><span>02</span>인증 요청 발송</div>
            <div class="claim-step"><span>03</span>기관별 인증 확인</div>
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


def _render_personal_request(
    user_id: str,
    user_name: str,
    repository: ClaimRepository | None,
    provider_ready: bool,
) -> None:
    input_mode = st.radio(
        "고객정보 입력 방식",
        ["등록 고객 선택", "직접 입력"],
        horizontal=True,
        key="claim_personal_input_mode_v1",
    )
    row, suffix = _selected_customer(user_id, input_mode)
    company_default = _clean(row.get("업체명"))
    business_default = _format_business_no(row.get("사업자등록번호"))
    representative_default = _clean(row.get("대표자명"))
    phone_default = _format_phone(
        row.get("휴대전화")
        or row.get("휴대폰번호")
        or row.get("전화번호")
    )

    form_key = f"claim_personal_request_{suffix}"
    with st.form(form_key, clear_on_submit=True):
        st.markdown("#### 개인사업자 카카오 인증 요청")
        st.caption(
            "담당자가 고객정보를 한 번 입력해 두 기관의 인증 요청을 "
            "보냅니다. 고객 승인 후 10분 이내 같은 로그인 화면에서 "
            "완료 여부를 확인해주세요."
        )
        company_col, business_col = st.columns(2)
        with company_col:
            company_name = st.text_input(
                "상호명",
                value=company_default,
                key=f"claim_company_{suffix}",
            )
        with business_col:
            business_no = st.text_input(
                "사업자등록번호",
                value=business_default,
                placeholder="000-00-00000",
                key=f"claim_business_no_{suffix}",
            )

        name_col, phone_col = st.columns(2)
        with name_col:
            representative = st.text_input(
                "대표자 이름",
                value=representative_default,
                key=f"claim_representative_{suffix}",
            )
        with phone_col:
            cellphone = st.text_input(
                "대표자 휴대전화",
                value=phone_default,
                placeholder="010-0000-0000",
                key=f"claim_cellphone_{suffix}",
            )

        st.markdown("**인증·수집 기관**")
        st.info(
            "홈택스 카카오 인증과 근로복지공단 카카오 인증을 각각 "
            "1건씩 발송합니다. 고객은 카카오톡에서 두 요청을 모두 "
            "승인해야 자료수집 단계로 넘어갑니다."
        )
        needs_comwel_identity = True

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
                (
                    "주민등록번호 뒤 7자리"
                    if needs_comwel_identity
                    else "주민등록번호 구분값 1자리"
                ),
                max_chars=7 if needs_comwel_identity else 1,
                placeholder=(
                    "근로복지공단 인증에만 사용"
                    if needs_comwel_identity
                    else "생년대 구분값"
                ),
                type="password",
                key=f"claim_identity_rear_{suffix}",
                help="암호화 전송 후 서버 메모리에서 폐기합니다.",
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
            "카카오 인증 2건 발송",
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
    if not company_name.strip():
        errors.append("상호명을 입력해주세요.")
    if not _is_valid_business_no(business_digits):
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
    source_results: dict[str, str] = {}
    transient: dict[str, Any] = {
        "expires_at": time.time() + AUTH_TTL_SECONDS,
        "expected_sources": list(sources),
    }
    provider_failures: list[tuple[str, str]] = []
    try:
        case = repository.create_case(
            company_name=company_name,
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
            except ClaimProviderError as exc:
                source_results["hometax_status"] = "failed"
                provider_failures.append(("홈택스", str(exc)))
        if "comwel" in sources:
            try:
                identity_number = f"{front_digits}{rear_digits}"
                transient["comwel"] = client.request_comwel_kakao(
                    identity_number=identity_number,
                    user_name=representative.strip(),
                    cellphone=phone_digits,
                )
                source_results["comwel_status"] = "auth_requested"
            except ClaimProviderError as exc:
                source_results["comwel_status"] = "failed"
                provider_failures.append(("근로복지공단", str(exc)))

        requested_sources = [
            source
            for source in ("hometax", "comwel")
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
                "failed_source_count": len(provider_failures),
            },
        )
        if requested_sources:
            _session_bucket(user_id)[case["id"]] = transient
        source_labels = [
            "홈택스" if source == "hometax" else "근로복지공단"
            for source in requested_sources
        ]
        if source_labels:
            message = (
                f"{'·'.join(source_labels)} 카카오 인증 요청을 발송했습니다. "
                "고객이 인증을 마치면 ‘진행상황’에서 완료 여부를 확인하세요."
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
    transient = _session_bucket(user_id).get(case_id)
    can_check = bool(
        provider_ready
        and transient
        and selected_case.get("business_type") == "individual"
    )
    if selected_case.get("business_type") == "corporation":
        st.caption("법인 공동인증 완료 상태는 인증 모듈 콜백으로 갱신됩니다.")
    elif not transient:
        st.caption(
            "인증 요청값은 10분 동안 이 브라우저 세션에서만 유지됩니다. "
            "만료된 경우 인증을 다시 요청해야 합니다."
        )
    if not can_check:
        return

    assert transient is not None
    needs_hometax = "hometax" in transient
    needs_comwel = "comwel" in transient
    with st.form(
        f"claim_auth_check_{case_id}",
        clear_on_submit=True,
    ):
        st.caption(
            "완료 확인에 필요한 값은 이번 요청에만 암호화 전송되며 "
            "DB·로그·브라우저 세션에 저장하지 않습니다."
        )
        name_col, phone_col = st.columns(2)
        with name_col:
            representative = st.text_input(
                "대표자 이름",
                key=f"claim_check_name_{case_id}",
            )
        with phone_col:
            cellphone = st.text_input(
                "카카오 인증 휴대전화",
                placeholder="010-0000-0000",
                key=f"claim_check_phone_{case_id}",
            )
        id_front_col, id_rear_col = st.columns(2)
        with id_front_col:
            identity_front = st.text_input(
                "주민등록번호 앞 6자리",
                type="password",
                max_chars=6,
                key=f"claim_check_identity_front_{case_id}",
            )
        with id_rear_col:
            identity_rear = st.text_input(
                (
                    "주민등록번호 뒤 7자리"
                    if needs_comwel
                    else "주민등록번호 구분값 1자리"
                ),
                type="password",
                max_chars=7 if needs_comwel else 1,
                key=f"claim_check_identity_rear_{case_id}",
            )
        check_submitted = st.form_submit_button(
            "고객 인증 완료 확인",
            use_container_width=True,
            type="primary",
        )

    if not check_submitted:
        return

    phone_digits = _digits(cellphone)
    front_digits = _digits(identity_front)
    rear_digits = _digits(identity_rear)
    if not representative.strip():
        st.error("대표자 이름을 입력해주세요.")
        return
    if len(phone_digits) != 11 or not phone_digits.startswith("010"):
        st.error("카카오 인증을 받은 010 휴대전화 번호를 확인해주세요.")
        return
    if needs_hometax and not _birth_date_from_identity(
        front_digits,
        rear_digits,
    ):
        st.error("홈택스 인증용 주민등록번호 앞자리와 구분값을 확인해주세요.")
        return
    if needs_comwel and (
        len(front_digits) != 6 or len(rear_digits) != 7
    ):
        st.error("근로복지공단 인증에는 주민등록번호 13자리가 필요합니다.")
        return

    client = TilkoClaimClient()
    updates: dict[str, Any] = {}
    try:
        if needs_hometax:
            completed = client.check_hometax_kakao(
                birth_date=_birth_date_from_identity(
                    front_digits,
                    rear_digits,
                ),
                user_name=representative.strip(),
                cellphone=phone_digits,
                session=transient["hometax"],
            )
            updates["hometax_status"] = (
                "auth_complete" if completed else "auth_pending"
            )
        if needs_comwel:
            completed = client.check_comwel_kakao(
                identity_number=f"{front_digits}{rear_digits}",
                user_name=representative.strip(),
                cellphone=phone_digits,
                session=transient["comwel"],
            )
            updates["comwel_status"] = (
                "auth_complete" if completed else "auth_pending"
            )
        expected_sources = [
            source
            for source in transient.get("expected_sources", [])
            if source in {"hometax", "comwel"}
        ]
        combined_statuses = {
            source: updates.get(
                f"{source}_status",
                selected_case.get(f"{source}_status"),
            )
            for source in expected_sources
        }
        (
            updates["overall_status"],
            all_completed,
            any_failed,
        ) = _resolve_auth_progress(
            expected_sources,
            combined_statuses,
        )
        if all_completed:
            updates["auth_completed_at"] = datetime.now(
                timezone.utc
            ).isoformat()
        repository.update_case_status(case_id, **updates)
        for status_key, source in (
            ("hometax_status", "hometax"),
            ("comwel_status", "comwel"),
        ):
            if updates.get(status_key) == "auth_complete":
                repository.update_document_status(
                    case_id,
                    source=source,
                    status="integration_required",
                )
        repository.append_audit_event(
            case_id=case_id,
            action="auth_check",
            source="provider",
            outcome="success" if all_completed else "pending",
            metadata={"all_sources_complete": all_completed},
        )
        if all_completed:
            _session_bucket(user_id).pop(case_id, None)
            transient.clear()
            st.session_state["_claim_flash_v1"] = (
                "고객 인증을 확인했습니다. 외부 자료수집 API 계약과 "
                "문서별 연동이 완료되면 수집을 실행할 수 있습니다."
            )
            st.rerun()
        if any_failed:
            still_pending = any(
                updates.get(f"{source}_status") == "auth_pending"
                for source in ("hometax", "comwel")
                if source in transient
            )
            if not still_pending:
                _session_bucket(user_id).pop(case_id, None)
                transient.clear()
            st.warning(
                "일부 기관 인증 요청이 실패했습니다. 실패한 기관은 새 요청으로 "
                "다시 발송해야 하며 전체 수집 완료로 처리되지 않았습니다."
            )
            return
        st.info("아직 완료되지 않은 기관 인증이 있습니다.")
    except (ClaimProviderError, ClaimRepositoryError) as exc:
        repository.append_audit_event(
            case_id=case_id,
            action="auth_check",
            source="provider",
            outcome="failed",
            metadata={"safe_error_code": "AUTH_CHECK_FAILED"},
        )
        st.error(str(exc))


def _render_results_tab(repository: ClaimRepository | None) -> None:
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
            ["전체", "인증 대기", "인증 완료", "수집 완료", "실패"],
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

    try:
        documents = repository.list_documents(str(selected_case.get("id", "")))
    except ClaimRepositoryError as exc:
        st.error(str(exc))
        return
    if not documents:
        st.info("이 요청에 등록된 수집 항목이 없습니다.")
        return
    source_filter = st.segmented_control(
        "기관",
        ["전체", "홈택스", "근로복지공단"],
        default="전체",
        key="claim_result_source_filter_v1",
    )
    rows = []
    for document in documents:
        source = str(document.get("source", ""))
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
                "상태": _source_status(document.get("status")),
                "수집일": _clean(document.get("collected_at"))[:10] or "-",
                "출력": (
                    "준비"
                    if document.get("status") == "collected"
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
    st.caption(
        "현재는 인증 요청과 결과 목록 저장소까지 준비된 상태입니다. "
        "원문 수집·다운로드는 승인된 문서별 API 계약을 연결한 뒤 활성화됩니다."
    )


def _render_catalog_tab() -> None:
    rows = [
        {
            "자료명": spec.name,
            "기관": spec.source,
            "기간": spec.period,
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
        _render_results_tab(repository)
    with catalog_tab:
        _render_catalog_tab()
