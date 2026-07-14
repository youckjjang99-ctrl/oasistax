from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from cloud_sync import sync_crm_record
from crm import (
    ACTION_OPTIONS,
    STATUS_OPTIONS,
    get_customer_record,
    make_customer_key,
    upsert_customer_record,
)
from crm_enhancements import (
    PIPELINE_OPTIONS,
    PRIORITY_OPTIONS,
    get_crm_profile,
    merge_profile_into_crm_record,
    save_crm_profile,
)
from customer_history import (
    build_change_summary,
    build_history_table,
    get_customer_history,
)
from matching_preferences import (
    INTEREST_OPTIONS,
    get_matching_preferences,
    save_matching_preferences,
)
from registered_policy_match import (
    build_customer_labels,
    load_registered_customers,
)
from utils import get_user_cumulative_db_path, get_user_dirs


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return text


def _number(value: Any) -> float | None:
    text = _clean(value).replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _format_number(value: Any, suffix: str = "") -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{int(round(number)):,}{suffix}"


def _normalize_business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return _clean(value)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _financial_snapshot(
    user_id: str,
    business_no: str,
) -> dict[str, Any]:
    base = get_user_dirs(user_id)["base"]
    cache = _load_json(
        base / "stock_financial_cache.json",
        {},
    )
    if not isinstance(cache, dict):
        return {}
    return cache.get(_normalize_business_no(business_no), {}) or {}


def _registry_snapshot(
    user_id: str,
    business_no: str,
) -> dict[str, Any]:
    base = get_user_dirs(user_id)["base"]
    cache = _load_json(
        base / "registry_cache.json",
        {},
    )
    if not isinstance(cache, dict):
        return {}
    normalized = _normalize_business_no(business_no)
    return cache.get(normalized, {}) or {}


def _stock_record(
    user_id: str,
    business_no: str,
) -> dict[str, Any]:
    base = get_user_dirs(user_id)["base"]
    records = _load_json(
        base / "stock_valuations.json",
        [],
    )
    if not isinstance(records, list):
        return {}

    normalized = _normalize_business_no(business_no)
    matching = [
        record
        for record in records
        if isinstance(record, dict)
        and _normalize_business_no(record.get("business_no", ""))
        == normalized
    ]
    if not matching:
        return {}

    return sorted(
        matching,
        key=lambda record: str(record.get("saved_at", "")),
        reverse=True,
    )[0]


def _first_value(
    customer: pd.Series,
    financial: dict[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        value = customer.get(key, "")
        if _clean(value):
            return value
    for key in keys:
        value = financial.get(key, "")
        if _clean(value):
            return value
    return ""


def _quick_diagnosis(
    customer: pd.Series,
    financial: dict[str, Any],
    preferences: dict[str, Any],
) -> tuple[list[str], list[str], list[str]]:
    strengths: list[str] = []
    checks: list[str] = []
    actions: list[str] = []

    sales = _first_value(
        customer,
        financial,
        "매출액",
        "연매출",
        "전년도매출",
    )
    operating = _first_value(
        customer,
        financial,
        "영업이익",
    )
    net_income = _first_value(
        customer,
        financial,
        "당기순이익",
    )
    employees = _first_value(
        customer,
        financial,
        "종업원수",
        "상시근로자수",
    )

    if _number(sales):
        strengths.append(f"매출액 {_format_number(sales, '원')} 확인")
    if (_number(operating) or 0) > 0:
        strengths.append(f"영업이익 {_format_number(operating, '원')} 흑자")
    if (_number(net_income) or 0) > 0:
        strengths.append(f"당기순이익 {_format_number(net_income, '원')} 흑자")
    if _number(employees):
        strengths.append(f"종업원수 {_format_number(employees, '명')} 확인")

    if not _clean(customer.get("사업장 소재지", "")):
        checks.append("사업장 소재지 확인 필요")
    if not _clean(
        customer.get("설립일", customer.get("설립년도", ""))
    ):
        checks.append("설립일 확인 필요")
    if _number(net_income) is None:
        checks.append("당기순이익 자료 보완 필요")
    elif (_number(net_income) or 0) < 0:
        checks.append("당기순손실 원인 확인 필요")

    interests = preferences.get("관심지원분야", []) or []
    if "운전자금" in interests:
        actions.append("운전자금 및 보증기관 연계 검토")
    if any(
        value in interests
        for value in ["시설자금", "기계·설비 구입", "차량 구입"]
    ):
        actions.append("시설·설비 투자 견적 및 자금계획 확인")
    if any(
        value in interests
        for value in ["신규채용", "고용유지"]
    ):
        actions.append("고용지원금 및 고용세액공제 검토")
    if not actions:
        actions.append("자금수요·채용계획·투자계획 우선 확인")

    return strengths, checks, actions


def render_enterprise_management_center(
    user_id: str,
    user_name: str = "",
) -> None:
    st.markdown("## 기업 컨설팅")
    st.caption(
        "고객 한 곳을 선택해 기업정보·CRM·정책자금 설정·주가평가·"
        "기업 히스토리를 한 화면에서 관리합니다."
    )

    customers = load_registered_customers(
        get_user_cumulative_db_path(user_id)
    )
    if customers.empty:
        st.info(
            "등록된 고객이 없습니다. 크레탑 자동등록으로 고객을 먼저 등록해주세요."
        )
        return

    labels, row_map = build_customer_labels(customers)
    selected_label = st.selectbox(
        "관리할 기업",
        labels,
        key="enterprise_center_customer",
    )
    selected_row = customers.loc[row_map[selected_label]]

    company_name = _clean(selected_row.get("업체명", ""))
    business_no = _normalize_business_no(
        selected_row.get("사업자등록번호", "")
    )
    customer_key = make_customer_key(company_name, business_no)

    financial = _financial_snapshot(user_id, business_no)
    registry = _registry_snapshot(user_id, business_no)
    stock = _stock_record(user_id, business_no)
    preferences = get_matching_preferences(
        user_id,
        business_no,
    )
    crm_record = get_customer_record(
        user_id,
        customer_key,
    )
    crm_profile = get_crm_profile(
        user_id,
        customer_key,
        business_no,
    )
    history = get_customer_history(
        user_id,
        business_no,
    )

    st.markdown(
        f"""
        <div style="
            padding:20px 22px;
            border-radius:18px;
            background:linear-gradient(135deg,#113b73,#2563eb);
            color:white;
            margin:6px 0 16px 0;
            box-shadow:0 10px 24px rgba(37,99,235,.16);
        ">
            <div style="font-size:1.45rem;font-weight:800;">
                {company_name or '기업명 미확인'}
            </div>
            <div style="margin-top:6px;opacity:.9;">
                사업자번호 {business_no or '-'} · 담당자
                {crm_profile.get('assigned_manager') or user_name or '-'}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    sales = _first_value(
        selected_row,
        financial,
        "매출액",
        "연매출",
        "전년도매출",
    )
    operating = _first_value(
        selected_row,
        financial,
        "영업이익",
    )
    net_income = _first_value(
        selected_row,
        financial,
        "당기순이익",
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("CRM 상태", crm_record.get("status", "신규"))
    m2.metric(
        "진행단계",
        crm_profile.get("pipeline_stage", "신규"),
    )
    m3.metric("매출액", _format_number(sales, "원"))
    m4.metric("당기순이익", _format_number(net_income, "원"))

    tab_overview, tab_crm, tab_policy, tab_stock, tab_history, tab_ai = st.tabs(
        [
            "기업정보",
            "CRM",
            "정책자금",
            "주가평가·등기",
            "기업히스토리",
            "AI 진단",
        ]
    )

    with tab_overview:
        left, right = st.columns(2)
        with left:
            st.markdown("#### 기본정보")
            basic_rows = [
                ["업체명", company_name],
                ["대표자명", _clean(selected_row.get("대표자명", ""))],
                ["사업자등록번호", business_no],
                ["업종명", _clean(selected_row.get("업종명", ""))],
                [
                    "사업장 소재지",
                    _clean(selected_row.get("사업장 소재지", "")),
                ],
                [
                    "설립일",
                    _clean(
                        selected_row.get(
                            "설립일",
                            selected_row.get("설립년도", ""),
                        )
                    ),
                ],
            ]
            st.dataframe(
                pd.DataFrame(basic_rows, columns=["항목", "내용"]),
                hide_index=True,
                use_container_width=True,
            )

        with right:
            st.markdown("#### 재무정보")
            financial_rows = [
                ["매출액", _format_number(sales, "원")],
                ["영업이익", _format_number(operating, "원")],
                ["당기순이익", _format_number(net_income, "원")],
                [
                    "자산총계",
                    _format_number(
                        _first_value(
                            selected_row,
                            financial,
                            "자산총계",
                        ),
                        "원",
                    ),
                ],
                [
                    "부채총계",
                    _format_number(
                        _first_value(
                            selected_row,
                            financial,
                            "부채총계",
                        ),
                        "원",
                    ),
                ],
                [
                    "자본총계",
                    _format_number(
                        _first_value(
                            selected_row,
                            financial,
                            "자본총계",
                        ),
                        "원",
                    ),
                ],
            ]
            st.dataframe(
                pd.DataFrame(financial_rows, columns=["항목", "내용"]),
                hide_index=True,
                use_container_width=True,
            )

    with tab_crm:
        c1, c2, c3 = st.columns(3)
        with c1:
            current_status = crm_record.get("status", "신규")
            status_index = (
                STATUS_OPTIONS.index(current_status)
                if current_status in STATUS_OPTIONS
                else 0
            )
            status = st.selectbox(
                "고객 상태",
                STATUS_OPTIONS,
                index=status_index,
                key="enterprise_status",
            )
        with c2:
            current_stage = crm_profile.get(
                "pipeline_stage",
                "신규",
            )
            stage_index = (
                PIPELINE_OPTIONS.index(current_stage)
                if current_stage in PIPELINE_OPTIONS
                else 0
            )
            pipeline_stage = st.selectbox(
                "상담 진행단계",
                PIPELINE_OPTIONS,
                index=stage_index,
                key="enterprise_pipeline",
            )
        with c3:
            current_priority = str(
                crm_profile.get("priority", "3")
            )
            priority_index = (
                PRIORITY_OPTIONS.index(current_priority)
                if current_priority in PRIORITY_OPTIONS
                else 2
            )
            priority = st.selectbox(
                "중요도",
                PRIORITY_OPTIONS,
                index=priority_index,
                format_func=lambda value: "★" * int(value),
                key="enterprise_priority",
            )

        d1, d2, d3 = st.columns(3)
        with d1:
            current_action = crm_record.get(
                "next_action",
                "없음",
            )
            action_index = (
                ACTION_OPTIONS.index(current_action)
                if current_action in ACTION_OPTIONS
                else len(ACTION_OPTIONS) - 1
            )
            next_action = st.selectbox(
                "다음 액션",
                ACTION_OPTIONS,
                index=action_index,
                key="enterprise_action",
            )
        with d2:
            current_date = _clean(
                crm_record.get(
                    "next_date",
                    crm_record.get("next_action_date", ""),
                )
            )
            try:
                default_date = datetime.strptime(
                    current_date,
                    "%Y-%m-%d",
                ).date()
            except Exception:
                default_date = date.today()
            next_date_value = st.date_input(
                "다음 예정일",
                value=default_date,
                key="enterprise_next_date",
            )
        with d3:
            assigned_manager = st.text_input(
                "담당자",
                value=(
                    crm_profile.get("assigned_manager")
                    or user_name
                    or ""
                ),
                key="enterprise_manager",
            )

        memo = st.text_area(
            "상담 메모",
            value=_clean(crm_record.get("memo", "")),
            height=150,
            key="enterprise_memo",
        )

        if st.button(
            "CRM 저장",
            type="primary",
            use_container_width=True,
            key="enterprise_save_crm",
        ):
            profile = save_crm_profile(
                user_id,
                customer_key,
                pipeline_stage,
                priority,
                assigned_manager,
            )
            ok, message = upsert_customer_record(
                user_id,
                customer_key,
                company_name,
                business_no,
                status,
                next_action,
                next_date_value.strftime("%Y-%m-%d"),
                memo,
            )
            if ok:
                updated_crm = get_customer_record(
                    user_id,
                    customer_key,
                )
                updated_crm = merge_profile_into_crm_record(
                    updated_crm,
                    profile,
                )
                sync_crm_record(
                    user_id,
                    business_no,
                    updated_crm,
                )
                st.success(
                    "CRM 내용을 로컬과 Supabase에 저장했습니다."
                )
                st.rerun()
            else:
                st.error(message)

    with tab_policy:
        st.markdown("#### 고객별 정책자금 매칭설정")
        matching_keywords = st.text_area(
            "매칭키워드",
            value=", ".join(
                preferences.get("매칭키워드", []) or []
            ),
            key="enterprise_match_keywords",
        )
        interest_fields = st.multiselect(
            "관심지원분야",
            INTEREST_OPTIONS,
            default=[
                item
                for item in (
                    preferences.get("관심지원분야", []) or []
                )
                if item in INTEREST_OPTIONS
            ],
            key="enterprise_interest_fields",
        )
        exclusion_keywords = st.text_area(
            "제외키워드",
            value=", ".join(
                preferences.get("제외키워드", []) or []
            ),
            key="enterprise_exclusion_keywords",
        )

        p1, p2, p3 = st.columns(3)
        with p1:
            fund_purpose = st.text_input(
                "자금사용목적",
                value=_clean(
                    preferences.get("자금사용목적", "")
                ),
                key="enterprise_fund_purpose",
            )
        with p2:
            planned_amount = st.text_input(
                "투자예정금액",
                value=_clean(
                    preferences.get("투자예정금액", "")
                ),
                key="enterprise_planned_amount",
            )
        with p3:
            planned_timing = st.text_input(
                "투자예정시기",
                value=_clean(
                    preferences.get("투자예정시기", "")
                ),
                key="enterprise_planned_timing",
            )

        if st.button(
            "정책자금 매칭설정 저장",
            use_container_width=True,
            key="enterprise_save_preferences",
        ):
            save_matching_preferences(
                user_id,
                business_no,
                company_name=company_name,
                matching_keywords=matching_keywords,
                interest_fields=interest_fields,
                exclusion_keywords=exclusion_keywords,
                fund_purpose=fund_purpose,
                planned_amount=planned_amount,
                planned_timing=planned_timing,
            )
            st.success(
                "정책자금 매칭설정을 로컬과 Supabase에 저장했습니다."
            )

    with tab_stock:
        left, right = st.columns(2)
        with left:
            st.markdown("#### 등기정보")
            if registry:
                registry_rows = [
                    ["법인등록번호", registry.get("법인등록번호", "")],
                    ["본점소재지", registry.get("본점소재지", "")],
                    ["법인설립일", registry.get("법인설립일", "")],
                    [
                        "자본금",
                        _format_number(registry.get("자본금"), "원"),
                    ],
                    [
                        "발행주식총수",
                        _format_number(
                            registry.get("발행주식총수"),
                            "주",
                        ),
                    ],
                    [
                        "1주당 액면가액",
                        _format_number(
                            registry.get("1주당액면가액"),
                            "원",
                        ),
                    ],
                ]
                st.dataframe(
                    pd.DataFrame(
                        registry_rows,
                        columns=["항목", "내용"],
                    ),
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.info(
                    "저장된 등기정보가 없습니다. 주가평가 메뉴에서 등기를 분석해주세요."
                )

        with right:
            st.markdown("#### 주가평가")
            if stock:
                result = stock.get("result", {})
                stock_rows = [
                    ["평가기준일", stock.get("valuation_date", "")],
                    [
                        "발행주식총수",
                        _format_number(
                            stock.get("current_shares"),
                            "주",
                        ),
                    ],
                    [
                        "1주당 평가액",
                        _format_number(
                            result.get("final_value_per_share"),
                            "원",
                        ),
                    ],
                    [
                        "기업 전체 주식가치",
                        _format_number(
                            result.get("total_equity_value"),
                            "원",
                        ),
                    ],
                ]
                st.dataframe(
                    pd.DataFrame(
                        stock_rows,
                        columns=["항목", "내용"],
                    ),
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.info(
                    "저장된 주가평가 결과가 없습니다."
                )

    with tab_history:
        if history:
            st.markdown("#### 직전 자료 대비 변화")
            for message in build_change_summary(history):
                st.write(f"- {message}")

            st.markdown("#### 전체 스냅샷")
            st.dataframe(
                build_history_table(history),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info(
                "기업 히스토리가 없습니다. 크레탑 PDF를 다시 분석하면 기록됩니다."
            )

    with tab_ai:
        strengths, checks, actions = _quick_diagnosis(
            selected_row,
            financial,
            preferences,
        )
        a1, a2, a3 = st.columns(3)
        with a1:
            with st.container(border=True):
                st.markdown("#### 강점")
                for item in strengths or ["추가 정보 확인 필요"]:
                    st.markdown(f"- {item}")
        with a2:
            with st.container(border=True):
                st.markdown("#### 확인사항")
                for item in checks or ["주요 누락정보 없음"]:
                    st.markdown(f"- {item}")
        with a3:
            with st.container(border=True):
                st.markdown("#### 우선 실행")
                for item in actions:
                    st.markdown(f"- {item}")

        st.markdown("#### 대표 미팅 질문")
        questions = [
            "최근 1년 이내 시설·기계·차량 투자계획이 있습니까?",
            "향후 6개월 내 신규채용 또는 고용유지 계획이 있습니까?",
            "현재 정책자금·보증기관 대출 잔액과 만기는 어떻게 됩니까?",
            "국세·지방세 체납이나 최근 연체 이력이 있습니까?",
            "올해 예상 매출과 주요 거래처 변화는 어떻게 됩니까?",
        ]
        for index, question in enumerate(questions, start=1):
            st.markdown(f"**{index}.** {question}")
