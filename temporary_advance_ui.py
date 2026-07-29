from __future__ import annotations

import re
from typing import Any

import pandas as pd
import streamlit as st

from matching_preferences import save_temporary_advance_calculation
from temporary_advance_calculator import (
    LEGAL_BASIS,
    calculate_temporary_advance,
    default_temporary_advance_inputs,
    extract_temporary_advance_balance,
)
from temporary_advance_pdf import build_temporary_advance_pdf


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
    return defaults


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
    initial = _saved_inputs(preferences, detected_balance)
    suffix = _key_suffix(business_no, company_name)

    st.markdown("### 가지급금 세무·4대보험 계산기")
    st.caption(
        "크레탑 재무계정의 가지급금·임원대여금을 불러와 대표자와 법인의 "
        "세금·보험 부담을 분리해서 계산합니다."
    )

    if is_individual:
        st.warning(
            "가지급금 인정이자와 대표자 상여처분은 법인세 구조입니다. "
            "현재 고객은 개인사업자로 표시되어 계산결과를 법인 신고에 사용할 수 없습니다."
        )

    if detected_balance > 0:
        st.success(
            f"크레탑에서 '{detected_key}' {_money(detected_balance)}을 확인했습니다."
        )
    else:
        st.info(
            "크레탑에서 가지급금 계정을 찾지 못했습니다. 계정별원장을 확인한 뒤 직접 입력하세요."
        )

    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("#### 가지급금·세금 가정")
        balance = st.number_input(
            "가지급금 잔액",
            min_value=0,
            value=int(float(initial.get("balance", 0) or 0)),
            step=1_000_000,
            help="크레탑 계정값을 자동 입력하며 계정별원장 확인 후 수정할 수 있습니다.",
            key=f"advance_balance_{suffix}",
        )
        days = st.number_input(
            "가지급금 유지일수",
            min_value=0,
            max_value=366,
            value=int(initial.get("days", 365) or 365),
            step=1,
            key=f"advance_days_{suffix}",
        )
        recognized_interest_rate_pct = st.number_input(
            "인정이자율(%)",
            min_value=0.0,
            max_value=30.0,
            value=float(
                initial.get("recognized_interest_rate_pct", 4.6) or 4.6
            ),
            step=0.1,
            format="%.2f",
            help=(
                "원칙은 가중평균차입이자율입니다. 4.6%는 법정 당좌대출이자율을 "
                "선택·적용할 수 있는 경우의 기본값입니다."
            ),
            key=f"advance_interest_rate_{suffix}",
        )
        received_interest = st.number_input(
            "실제로 회수·계상한 이자",
            min_value=0,
            value=int(float(initial.get("received_interest", 0) or 0)),
            step=100_000,
            key=f"advance_received_interest_{suffix}",
        )
        disposition_options = [
            "미회수·대표자 상여처분 가정",
            "인정이자 실제 회수·상여 없음",
            "소득처분 미확정·세무대리인 확인",
        ]
        saved_mode = str(
            initial.get(
                "disposition_mode",
                "미회수·대표자 상여처분 가정",
            )
        )
        disposition_mode = st.selectbox(
            "미회수 인정이자 처리",
            disposition_options,
            index=(
                disposition_options.index(saved_mode)
                if saved_mode in disposition_options
                else 0
            ),
            help=(
                "상여처분은 자동 확정이 아닙니다. 실제 회수 여부, 계약, 장부와 "
                "소득 귀속을 세무대리인이 확인해야 합니다."
            ),
            key=f"advance_disposition_{suffix}",
        )
        representative_tax_base = st.number_input(
            "대표자 현재 종합소득 과세표준",
            min_value=0,
            value=int(
                float(initial.get("representative_tax_base", 0) or 0)
            ),
            step=1_000_000,
            help=(
                "상여처분 전 예상 종합소득 과세표준을 입력하면 누진세율 구간을 "
                "반영합니다. 급여총액이 아니라 과세표준입니다."
            ),
            key=f"advance_representative_tax_base_{suffix}",
        )
        corporate_tax_base = st.number_input(
            "법인 현재 과세표준",
            min_value=0,
            value=int(float(initial.get("corporate_tax_base", 0) or 0)),
            step=10_000_000,
            help="가지급금 세무조정 전 예상 법인세 과세표준입니다.",
            key=f"advance_corporate_tax_base_{suffix}",
        )

    with right:
        st.markdown("#### 차입금·4대보험 가정")
        total_borrowings = st.number_input(
            "법인 총차입금",
            min_value=0,
            value=int(float(initial.get("total_borrowings", 0) or 0)),
            step=10_000_000,
            help="지급이자 손금불산입의 단순 비례 추정에 사용합니다.",
            key=f"advance_total_borrowings_{suffix}",
        )
        annual_interest_expense = st.number_input(
            "법인 연간 지급이자",
            min_value=0,
            value=int(
                float(initial.get("annual_interest_expense", 0) or 0)
            ),
            step=1_000_000,
            key=f"advance_annual_interest_{suffix}",
        )
        current_monthly_standard_income = st.number_input(
            "대표자 현재 월 기준소득·보수월액",
            min_value=0,
            value=int(
                float(
                    initial.get("current_monthly_standard_income", 0) or 0
                )
            ),
            step=100_000,
            help="국민연금 추가부담 계산 시 2026년 상·하한을 적용합니다.",
            key=f"advance_monthly_income_{suffix}",
        )
        nps_health_covered = st.checkbox(
            "국민연금·건강보험 직장가입자로 반영",
            value=bool(initial.get("nps_health_covered", True)),
            key=f"advance_nps_health_{suffix}",
        )
        employment_covered = st.checkbox(
            "대표자가 고용보험 피보험자에 해당",
            value=bool(initial.get("employment_covered", False)),
            help=(
                "대표이사는 통상 근로자성이 없어 제외됩니다. 실질적으로 근로자성이 "
                "인정되고 피보험자격이 있을 때만 선택하세요."
            ),
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
            help="150인 미만 0.25%가 기본이며 기업 규모에 따라 달라집니다.",
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
            help="1.47%는 2026년 전체 평균이며 실제 업종요율을 입력해야 합니다.",
            key=f"advance_industrial_rate_{suffix}",
        )

    inputs = {
        "balance": balance,
        "days": days,
        "recognized_interest_rate_pct": recognized_interest_rate_pct,
        "received_interest": received_interest,
        "disposition_mode": disposition_mode,
        "representative_tax_base": representative_tax_base,
        "corporate_tax_base": corporate_tax_base,
        "total_borrowings": total_borrowings,
        "annual_interest_expense": annual_interest_expense,
        "current_monthly_standard_income": current_monthly_standard_income,
        "nps_health_covered": nps_health_covered,
        "employment_covered": employment_covered,
        "industrial_accident_covered": industrial_accident_covered,
        "employment_stability_rate_pct": employment_stability_rate_pct,
        "industrial_accident_rate_pct": industrial_accident_rate_pct,
    }
    result = calculate_temporary_advance(inputs)
    representative = result["representative"]
    company = result["company"]

    st.markdown("---")
    st.markdown("#### 계산 결과")
    kpi_columns = st.columns(4)
    kpi_columns[0].metric(
        "연간 인정이자",
        _money(company["recognized_interest"]),
        f"{recognized_interest_rate_pct:.2f}% · {days}일",
    )
    kpi_columns[1].metric(
        "대표자 세금·보험",
        _money(representative["total_burden"]),
        "상여처분 시나리오",
    )
    kpi_columns[2].metric(
        "법인 세금·보험",
        _money(company["tax_and_insurance_burden"]),
        "추가 현금부담",
    )
    kpi_columns[3].metric(
        "미회수이자 포함 법인노출",
        _money(company["cash_exposure_including_uncollected_interest"]),
        "세금·보험 + 미회수이자",
    )

    representative_table = pd.DataFrame(
        [
            ["대표자 상여처분 소득", _money(representative["bonus_disposition"]), "미회수 인정이자 상여 가정"],
            ["종합소득세", _money(representative["income_tax"]), "기존 과세표준 포함 누진세율"],
            ["개인지방소득세", _money(representative["local_income_tax"]), "종합소득세의 10%"],
            ["대표자 부담 4대보험", _money(representative["insurance_total"]), "선택한 피보험자격 기준"],
            ["대표자 합계", _money(representative["total_burden"]), "세금 + 보험료"],
        ],
        columns=["대표자 관점", "추정금액", "설명"],
    )
    company_table = pd.DataFrame(
        [
            ["연간 인정이자", _money(company["recognized_interest"]), "회사에 귀속되어야 할 이자"],
            ["인정이자 세무조정", _money(company["tax_adjustment_interest"]), "시가와 실제 회수이자 차이"],
            ["지급이자 손금불산입", _money(company["disallowed_interest"]), "차입금·지급이자 단순 적수 가정"],
            ["법인세·지방소득세", _money(company["tax_total"]), "세무조정으로 증가한 추정세액"],
            ["법인 부담 4대보험", _money(company["employer_insurance_total"]), "상여 보수반영 가정"],
            ["법인 세금·보험 합계", _money(company["tax_and_insurance_burden"]), "추가 세금 + 회사 보험료"],
        ],
        columns=["법인 관점", "추정금액", "설명"],
    )
    table_left, table_right = st.columns(2, gap="large")
    with table_left:
        st.markdown("##### 대표자 부담")
        st.dataframe(
            representative_table,
            hide_index=True,
            use_container_width=True,
        )
    with table_right:
        st.markdown("##### 법인 부담")
        st.dataframe(
            company_table,
            hide_index=True,
            use_container_width=True,
        )

    insurance_table = pd.DataFrame(
        [
            {
                "보험": row["구분"],
                "대표자 부담": _money(row["대표자 부담"]),
                "법인 부담": _money(row["법인 부담"]),
                "적용여부": "적용" if row["적용"] else "미적용",
                "기준": row["기준"],
            }
            for row in result["insurance_rows"]
        ]
    )
    with st.expander("4대보험 상세 보기", expanded=True):
        st.dataframe(
            insurance_table,
            hide_index=True,
            use_container_width=True,
        )

    for warning in result["warnings"]:
        st.caption(f"• {warning}")

    try:
        pdf_bytes = build_temporary_advance_pdf(
            company_name=company_name,
            business_no=business_no,
            result=result,
            consultant_name=user_name,
        )
    except Exception as exc:
        pdf_bytes = b""
        st.warning(f"1페이지 리포트 생성 중 오류가 발생했습니다: {exc}")

    safe_company = re.sub(r'[\\/:*?"<>|]', "_", company_name or "기업")
    save_col, download_col = st.columns(2)
    business_digits = re.sub(r"[^0-9]", "", business_no or "")
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
                        "계산결과를 고객별로 저장했습니다. 종합컨설팅 리포트에도 반영됩니다."
                    )
            except Exception as exc:
                st.error(f"계산결과 저장에 실패했습니다: {exc}")
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
            "사업자등록번호가 없어 고객별 저장은 비활성화되었습니다. 계산과 PDF 출력은 사용할 수 있습니다."
        )

    with st.expander("적용 법령·2026년 보험요율 확인"):
        for item in LEGAL_BASIS:
            st.markdown(
                f"- [{item['title']}]({item['url']}) — {item['summary']}"
            )
        st.warning(
            "이 계산기는 상담용 사전 시뮬레이션입니다. 계정별원장, 금전소비대차계약, "
            "인정이자 실제 회수, 차입금 적수, 대표자 보수총액과 피보험자격을 확인한 뒤 "
            "세무사·노무사·각 공단의 최종 판단으로 신고해야 합니다."
        )
