from __future__ import annotations

import re
from typing import Any

import pandas as pd
import streamlit as st
from runtime_error_log import safe_public_error

from artifact_cache import content_digest
from matching_preferences import save_temporary_advance_calculation
from temporary_advance_calculator import (
    LEGAL_BASIS,
    calculate_temporary_advance,
    company_burden_text,
    default_temporary_advance_inputs,
    extract_temporary_advance_balance,
    format_estimate_range,
    representative_burden_text,
)
from temporary_advance_pdf import build_temporary_advance_pdf


ADVANCE_PDF_CACHE_TTL_SECONDS = 30 * 60


@st.cache_data(
    ttl=ADVANCE_PDF_CACHE_TTL_SECONDS,
    max_entries=32,
    show_spinner=False,
)
def _cached_temporary_advance_pdf(
    owner_id: str,
    report_digest: str,
    company_name: str,
    business_no: str,
    consultant_name: str,
    _result: dict[str, Any],
) -> bytes:
    del owner_id, report_digest
    return build_temporary_advance_pdf(
        company_name=company_name,
        business_no=business_no,
        result=_result,
        consultant_name=consultant_name,
    )


EXPLICIT_ADVANCE_KEYS = {
    "가지급금",
    "주주임원종업원단기대여금",
    "대표자대여금",
    "임원대여금",
}


def _money(value: Any) -> str:
    try:
        return f"{int(round(float(value or 0))):,}원"
    except (TypeError, ValueError):
        return "0원"


def _key_suffix(business_no: str, company_name: str) -> str:
    value = re.sub(r"[^0-9A-Za-z가-힣]", "", business_no or company_name)
    return value[:40] or "company"


def _saved_inputs(
    preferences: dict[str, Any],
    detected_balance: float,
    detected_key: str,
) -> dict[str, Any]:
    defaults = default_temporary_advance_inputs(detected_balance)
    saved = preferences.get("가지급금계산", {}) or {}
    saved_values = (
        saved.get("inputs", {})
        if isinstance(saved, dict)
        else {}
    )
    if isinstance(saved_values, dict):
        defaults.update(saved_values)
    if float(defaults.get("balance", 0) or 0) <= 0 and detected_balance > 0:
        defaults["balance"] = detected_balance
    if not saved_values and detected_key in EXPLICIT_ADVANCE_KEYS:
        defaults["balance_confirmed"] = True
    return defaults


def _insurance_amount(row: dict[str, Any], key: str) -> str:
    if row.get("미확인"):
        return "미확인"
    if not row.get("적용"):
        return "미적용"
    return _money(row.get(key))


def render_temporary_advance_calculator(
    user_id: str,
    user_name: str,
    business_no: str,
    company_name: str,
    selected_row: Any,
    financial: dict[str, Any],
    preferences: dict[str, Any] | None = None,
    is_individual: bool = False,
) -> None:
    preferences = preferences if isinstance(preferences, dict) else {}
    detected_balance, detected_key = extract_temporary_advance_balance(
        selected_row,
        financial,
    )
    initial = _saved_inputs(
        preferences,
        detected_balance,
        detected_key,
    )
    suffix = _key_suffix(business_no, company_name)

    st.markdown("### 가지급금 세무·4대보험 사전진단")
    st.caption(
        "입력값을 바꾸면 결과가 즉시 갱신됩니다. 크레탑 계정은 자동으로 "
        "불러오되 대표자·특수관계인 관련 거래인지 확인한 뒤 계산합니다."
    )

    if is_individual:
        st.warning(
            "이 계산기는 법인의 가지급금 인정이자와 대표자 상여처분을 "
            "대상으로 합니다. 개인사업자 고객에게는 적용할 수 없습니다."
        )
        return

    mode_options = ["간편 진단", "상세 계산"]
    saved_mode = str(initial.get("ui_mode", "간편 진단"))
    ui_mode = st.radio(
        "계산 방식",
        mode_options,
        index=mode_options.index(saved_mode) if saved_mode in mode_options else 0,
        horizontal=True,
        help=(
            "간편 진단은 미확인 세금을 범위로 표시합니다. 상세 계산은 "
            "과세표준·차입금·보수 정보를 입력해 금액을 좁힙니다."
        ),
        key=f"advance_mode_{suffix}",
    )

    if detected_balance > 0:
        account_note = (
            "대표자 관련성이 높은 계정"
            if detected_key in EXPLICIT_ADVANCE_KEYS
            else "대표자 관련 여부 확인이 필요한 일반 대여금 계정"
        )
        st.success(
            f"크레탑에서 '{detected_key}' {_money(detected_balance)}을 "
            f"확인했습니다. ({account_note})"
        )
    else:
        st.info(
            "크레탑에서 관련 계정을 찾지 못했습니다. 계정별원장을 확인한 뒤 "
            "가지급금 잔액을 직접 입력하세요."
        )

    input_columns = st.columns(3)
    with input_columns[0]:
        balance = st.number_input(
            "가지급금 잔액",
            min_value=0,
            value=int(float(initial.get("balance", 0) or 0)),
            step=1_000_000,
            help="크레탑 자동입력값은 계정별원장 확인 후 수정할 수 있습니다.",
            key=f"advance_balance_{suffix}",
        )
    with input_columns[1]:
        days = st.number_input(
            "가지급금 유지일수",
            min_value=0,
            max_value=366,
            value=int(initial.get("days", 365) or 365),
            step=1,
            key=f"advance_days_{suffix}",
        )
    with input_columns[2]:
        received_interest = st.number_input(
            "실제로 회수·계상한 이자",
            min_value=0,
            value=int(float(initial.get("received_interest", 0) or 0)),
            step=100_000,
            key=f"advance_received_interest_{suffix}",
        )

    balance_confirmed = st.checkbox(
        "계정별원장에서 대표자·특수관계인 관련 가지급금 또는 대여금임을 확인했습니다.",
        value=bool(initial.get("balance_confirmed", False)),
        help=(
            "일반 단기대여금은 거래처 대여금일 수 있으므로 자동으로 대표자 "
            "상여처분 대상이라고 단정하지 않습니다."
        ),
        key=f"advance_balance_confirmed_{suffix}",
    )

    if balance <= 0:
        st.info("가지급금 잔액을 입력하면 사전진단 결과가 표시됩니다.")
        return
    if not balance_confirmed:
        st.warning(
            "대표자 관련 계정 확인 전에는 세금·보험 결과를 표시하지 않습니다. "
            "계정별원장을 확인한 뒤 위 확인란을 선택하세요."
        )
        return

    recognized_interest_rate_pct = float(
        initial.get("recognized_interest_rate_pct", 4.6) or 4.6
    )
    disposition_mode = str(
        initial.get(
            "disposition_mode",
            "미회수·대표자 상여처분 가정",
        )
    )
    representative_tax_base = float(
        initial.get("representative_tax_base", 0) or 0
    )
    representative_tax_base_known = False
    corporate_tax_base = float(initial.get("corporate_tax_base", 0) or 0)
    corporate_tax_base_known = False
    total_borrowings = float(initial.get("total_borrowings", 0) or 0)
    annual_interest_expense = float(
        initial.get("annual_interest_expense", 0) or 0
    )
    insurance_status = "미확인"
    current_monthly_standard_income = float(
        initial.get("current_monthly_standard_income", 0) or 0
    )
    current_monthly_standard_income_known = False
    employment_covered = False
    industrial_accident_covered = False
    employment_stability_rate_pct = 0.25
    industrial_accident_rate_pct = 1.47

    if ui_mode == "간편 진단":
        st.info(
            "현재 과세표준과 대표자 보수정보를 입력하지 않았으므로 세금은 "
            "최저~최고 예상 범위로 표시하고 4대보험은 별도 확인으로 분리합니다."
        )
    else:
        left, right = st.columns(2, gap="large")
        with left:
            st.markdown("#### 세금 계산 기준")
            recognized_interest_rate_pct = st.number_input(
                "인정이자율(%)",
                min_value=0.0,
                max_value=30.0,
                value=recognized_interest_rate_pct,
                step=0.1,
                format="%.2f",
                help=(
                    "원칙은 회사의 가중평균차입이자율입니다. 4.6%는 "
                    "당좌대출이자율을 적용할 수 있는 경우의 기본값입니다."
                ),
                key=f"advance_interest_rate_{suffix}",
            )
            disposition_options = [
                "미회수·대표자 상여처분 가정",
                "인정이자 실제 회수·상여 없음",
                "소득처분 미확정·세무대리인 확인",
            ]
            disposition_mode = st.selectbox(
                "미회수 인정이자 처리",
                disposition_options,
                index=(
                    disposition_options.index(disposition_mode)
                    if disposition_mode in disposition_options
                    else 0
                ),
                help=(
                    "상여처분은 자동 확정이 아닙니다. 실제 회수 여부·계약·장부와 "
                    "소득 귀속을 세무대리인이 확인해야 합니다."
                ),
                key=f"advance_disposition_{suffix}",
            )
            representative_tax_base_known = st.checkbox(
                "대표자 종합소득 과세표준을 확인했습니다.",
                value=bool(
                    initial.get("representative_tax_base_known", False)
                ),
                key=f"advance_representative_known_{suffix}",
            )
            representative_tax_base = st.number_input(
                "대표자 현재 종합소득 과세표준",
                min_value=0,
                value=int(representative_tax_base),
                step=1_000_000,
                disabled=not representative_tax_base_known,
                help="급여총액이 아니라 상여처분 전 예상 종합소득 과세표준입니다.",
                key=f"advance_representative_tax_base_{suffix}",
            )
            corporate_tax_base_known = st.checkbox(
                "법인 과세표준을 확인했습니다.",
                value=bool(initial.get("corporate_tax_base_known", False)),
                key=f"advance_corporate_known_{suffix}",
            )
            corporate_tax_base = st.number_input(
                "법인 현재 과세표준",
                min_value=0,
                value=int(corporate_tax_base),
                step=10_000_000,
                disabled=not corporate_tax_base_known,
                help="가지급금 세무조정 전 예상 법인세 과세표준입니다.",
                key=f"advance_corporate_tax_base_{suffix}",
            )

        with right:
            st.markdown("#### 법인·보험 계산 기준")
            total_borrowings = st.number_input(
                "법인 총차입금",
                min_value=0,
                value=int(total_borrowings),
                step=10_000_000,
                help="지급이자 손금불산입의 단순 비례 추정에 사용합니다.",
                key=f"advance_total_borrowings_{suffix}",
            )
            annual_interest_expense = st.number_input(
                "법인 연간 지급이자",
                min_value=0,
                value=int(annual_interest_expense),
                step=1_000_000,
                key=f"advance_annual_interest_{suffix}",
            )
            insurance_options = ["미확인", "직장가입", "미가입"]
            saved_insurance_status = str(
                initial.get("insurance_status", "미확인")
            )
            insurance_status = st.selectbox(
                "대표자 국민연금·건강보험 상태",
                insurance_options,
                index=(
                    insurance_options.index(saved_insurance_status)
                    if saved_insurance_status in insurance_options
                    else 0
                ),
                help=(
                    "미확인은 보험료를 합계에서 분리합니다. 직장가입을 선택하면 "
                    "월 기준소득·보수월액을 입력해야 합니다."
                ),
                key=f"advance_insurance_status_{suffix}",
            )
            if insurance_status == "직장가입":
                current_monthly_standard_income = st.number_input(
                    "대표자 현재 월 기준소득·보수월액",
                    min_value=0,
                    value=int(current_monthly_standard_income),
                    step=100_000,
                    help="국민연금 계산 시 2026년 상·하한을 반영합니다.",
                    key=f"advance_monthly_income_{suffix}",
                )
                current_monthly_standard_income_known = bool(
                    current_monthly_standard_income > 0
                )
                if not current_monthly_standard_income_known:
                    st.caption("월 보수액 입력 전에는 국민연금이 미확인으로 남습니다.")
            elif insurance_status == "미가입":
                current_monthly_standard_income = 0
                current_monthly_standard_income_known = True

            with st.expander("고용·산재보험 상세 설정"):
                employment_covered = st.checkbox(
                    "대표자가 고용보험 피보험자에 해당",
                    value=bool(initial.get("employment_covered", False)),
                    help="실질적인 근로자성이 인정될 때만 선택하세요.",
                    key=f"advance_employment_{suffix}",
                )
                industrial_accident_covered = st.checkbox(
                    "대표자가 산재보험 적용 대상에 해당",
                    value=bool(
                        initial.get("industrial_accident_covered", False)
                    ),
                    help="중소기업 사업주 특례가입 등 실제 적용 대상일 때만 선택하세요.",
                    key=f"advance_industrial_{suffix}",
                )
                employment_stability_rate_pct = st.number_input(
                    "법인 고용안정·직업능력개발 요율(%)",
                    min_value=0.0,
                    max_value=3.0,
                    value=float(
                        initial.get("employment_stability_rate_pct", 0.25)
                        or 0.25
                    ),
                    step=0.05,
                    format="%.2f",
                    disabled=not employment_covered,
                    key=f"advance_employment_stability_{suffix}",
                )
                industrial_accident_rate_pct = st.number_input(
                    "산재보험 업종요율(%)",
                    min_value=0.0,
                    max_value=30.0,
                    value=float(
                        initial.get("industrial_accident_rate_pct", 1.47)
                        or 1.47
                    ),
                    step=0.01,
                    format="%.3f",
                    disabled=not industrial_accident_covered,
                    key=f"advance_industrial_rate_{suffix}",
                )

    inputs = {
        "ui_mode": ui_mode,
        "balance": balance,
        "balance_confirmed": balance_confirmed,
        "days": days,
        "recognized_interest_rate_pct": recognized_interest_rate_pct,
        "received_interest": received_interest,
        "disposition_mode": disposition_mode,
        "representative_tax_base": representative_tax_base,
        "representative_tax_base_known": representative_tax_base_known,
        "corporate_tax_base": corporate_tax_base,
        "corporate_tax_base_known": corporate_tax_base_known,
        "total_borrowings": total_borrowings,
        "annual_interest_expense": annual_interest_expense,
        "insurance_status": insurance_status,
        "current_monthly_standard_income": current_monthly_standard_income,
        "current_monthly_standard_income_known": (
            current_monthly_standard_income_known
        ),
        "nps_health_covered": insurance_status == "직장가입",
        "employment_covered": employment_covered,
        "industrial_accident_covered": industrial_accident_covered,
        "employment_stability_rate_pct": employment_stability_rate_pct,
        "industrial_accident_rate_pct": industrial_accident_rate_pct,
    }
    result = calculate_temporary_advance(inputs)
    representative = result["representative"]
    company = result["company"]

    st.markdown("---")
    st.markdown("#### 사전진단 결과")
    kpi_columns = st.columns(3)
    kpi_columns[0].metric(
        "연간 인정이자",
        _money(company["recognized_interest"]),
        f"{recognized_interest_rate_pct:.2f}% / {days}일",
    )
    kpi_columns[1].metric(
        "대표자 예상 부담",
        representative_burden_text(result),
        "세금 + 확인된 보험료",
    )
    kpi_columns[2].metric(
        "법인 추가 세금·보험",
        company_burden_text(result),
        "미회수이자 제외",
    )

    with st.container(border=True):
        st.markdown(
            "##### 법인 총 재무 노출  "
            + company_burden_text(
                result,
                include_uncollected_interest=True,
            )
        )
        st.caption(
            "세금·보험과 아직 회수하지 못한 인정이자의 합계입니다. "
            "회수 가능한 채권을 포함하므로 확정 손실액을 의미하지 않습니다."
        )

    representative_tab, company_tab, insurance_tab = st.tabs(
        ["대표자 부담", "법인 부담", "4대보험"]
    )
    with representative_tab:
        st.dataframe(
            pd.DataFrame(
                [
                    [
                        "상여처분 소득",
                        _money(representative["bonus_disposition"]),
                        "미회수 인정이자 상여 가정",
                    ],
                    [
                        "소득세·지방소득세",
                        format_estimate_range(
                            representative["tax_total_min"],
                            representative["tax_total_max"],
                        ),
                        (
                            "과세표준 확인"
                            if representative["tax_base_known"]
                            else "과세표준 미확인 범위"
                        ),
                    ],
                    [
                        "대표자 보험료",
                        (
                            "미확인"
                            if representative["insurance_pending"]
                            else _money(representative["insurance_total"])
                        ),
                        "직장가입·월 보수와 피보험자격 기준",
                    ],
                    [
                        "대표자 예상 부담",
                        representative_burden_text(result),
                        "세금 + 확인된 보험료",
                    ],
                ],
                columns=["대표자 관점", "추정금액", "설명"],
            ),
            hide_index=True,
            use_container_width=True,
        )

    with company_tab:
        st.dataframe(
            pd.DataFrame(
                [
                    [
                        "연간 인정이자",
                        _money(company["recognized_interest"]),
                        "회사에 귀속되어야 할 이자",
                    ],
                    [
                        "인정이자 세무조정",
                        _money(company["tax_adjustment_interest"]),
                        "시가와 실제 회수이자의 차이",
                    ],
                    [
                        "지급이자 손금불산입",
                        _money(company["disallowed_interest"]),
                        "차입금·지급이자 단순 적수 가정",
                    ],
                    [
                        "법인세·지방소득세",
                        format_estimate_range(
                            company["tax_total_min"],
                            company["tax_total_max"],
                        ),
                        (
                            "과세표준 확인"
                            if company["tax_base_known"]
                            else "과세표준 미확인 범위"
                        ),
                    ],
                    [
                        "법인 부담 보험료",
                        (
                            "미확인"
                            if company["insurance_pending"]
                            else _money(company["employer_insurance_total"])
                        ),
                        "상여의 보수반영과 피보험자격 기준",
                    ],
                    [
                        "법인 추가 세금·보험",
                        company_burden_text(result),
                        "미회수 인정이자 제외",
                    ],
                    [
                        "법인 총 재무 노출",
                        company_burden_text(
                            result,
                            include_uncollected_interest=True,
                        ),
                        "확정 손실이 아닌 사전 노출액",
                    ],
                ],
                columns=["법인 관점", "추정금액", "설명"],
            ),
            hide_index=True,
            use_container_width=True,
        )

    with insurance_tab:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "보험": row["구분"],
                        "대표자 부담": _insurance_amount(
                            row,
                            "대표자 부담",
                        ),
                        "법인 부담": _insurance_amount(
                            row,
                            "법인 부담",
                        ),
                        "기준": (
                            row["기준"]
                            if row.get("적용") or row.get("미확인")
                            else "미적용"
                        ),
                    }
                    for row in result["insurance_rows"]
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )

    with st.expander("계산 전제와 확인이 필요한 항목"):
        for warning in result["warnings"]:
            st.markdown(f"- {warning}")

    try:
        report_digest = content_digest(
            company_name,
            business_no,
            result,
            user_name,
        )
        pdf_bytes = _cached_temporary_advance_pdf(
            user_id,
            report_digest,
            company_name=company_name,
            business_no=business_no,
            _result=result,
            consultant_name=user_name,
        )
    except Exception as exc:
        pdf_bytes = b""
        st.warning(
            safe_public_error(exc, "1페이지 리포트 생성 중 오류가 발생했습니다.")
        )

    safe_company = re.sub(r'[\\/:*?"<>|]', "_", company_name or "기업")
    business_digits = re.sub(r"[^0-9]", "", business_no or "")
    save_col, download_col = st.columns(2)
    with save_col:
        if st.button(
            "계산결과 저장",
            type="primary",
            use_container_width=True,
            disabled=len(business_digits) != 10,
            key=f"advance_save_{suffix}",
        ):
            try:
                saved = save_temporary_advance_calculation(
                    user_id=user_id,
                    business_no=business_no,
                    company_name=company_name,
                    calculation=result,
                )
                if saved.get("_cloud_sync_warning"):
                    st.warning(
                        "로컬 저장은 완료했지만 Supabase 동기화는 대기 중입니다: "
                        + str(saved["_cloud_sync_warning"])
                    )
                else:
                    st.success(
                        "고객별 계산결과를 저장했습니다. 종합컨설팅 리포트에도 "
                        "같은 범위·미확인 상태가 반영됩니다."
                    )
            except Exception as exc:
                st.error(safe_public_error(exc, "계산결과 저장에 실패했습니다."))
    with download_col:
        st.download_button(
            "1페이지 리포트 다운로드",
            data=pdf_bytes,
            file_name=f"가지급금_사전진단_{safe_company}.pdf",
            mime="application/pdf",
            use_container_width=True,
            disabled=not bool(pdf_bytes),
            key=f"advance_pdf_{suffix}",
        )

    if len(business_digits) != 10:
        st.info(
            "사업자등록번호가 없어 고객별 저장은 비활성화되었습니다. "
            "계산과 PDF 출력은 사용할 수 있습니다."
        )

    with st.expander("적용 법령·2026년 보험요율 확인"):
        for item in LEGAL_BASIS:
            st.markdown(
                f"- [{item['title']}]({item['url']}) — {item['summary']}"
            )
        st.warning(
            "이 화면은 상담용 사전 시뮬레이션입니다. 계정별원장, "
            "금전소비대차계약, 인정이자 실제 회수, 차입금 적수, 대표자 "
            "보수총액과 피보험자격을 확인한 뒤 세무·노무 전문가와 각 공단의 "
            "최종 판단으로 신고해야 합니다."
        )
