from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from consulting_report import (
    build_consulting_analysis,
    build_consulting_excel_report,
)
from articles_review import (
    get_latest_articles_review,
    render_articles_review,
)
from employee_status import render_employee_status
from enterprise_consulting_engine import (
    reconcile_enterprise_consulting_context,
)
from consultation_journal import (
    get_company_consultation_context,
    render_audio_consultation_journal,
    render_saved_consultation_journals,
)

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
from multi_source_policy import render_multi_source_match
from stock_valuation import render_stock_valuation_page
from registered_policy_match import (
    build_customer_labels,
    load_registered_customers,
)
from enterprise_customer_management import (
    confirm_delete_dialog,
    filter_active_customers,
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



def _render_enterprise_dashboard_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.4rem; max-width: 1320px;}
        .oasis-hero {
            padding: 24px 28px; border: 1px solid #e7edf7; border-radius: 22px;
            background: radial-gradient(circle at 88% 14%, rgba(99,102,241,.13), transparent 34%),
                        linear-gradient(135deg, #ffffff 0%, #f6f9ff 100%);
            box-shadow: 0 12px 34px rgba(31,64,124,.08); margin: 8px 0 18px;
        }
        .oasis-title {color:#172033; font-size:1.72rem; font-weight:850; letter-spacing:-.03em;}
        .oasis-meta {color:#6b768c; font-size:.93rem; margin-top:8px;}
        .oasis-metric-grid {
            display:grid; grid-template-columns:repeat(4,minmax(0,1fr));
            gap:14px; margin:8px 0 20px;
        }
        .oasis-metric-card {
            min-height:132px; padding:18px 20px; border-radius:18px;
            border:1px solid #e1e9f6; box-shadow:0 7px 22px rgba(42,70,120,.06);
            background:#fff;
        }
        .oasis-metric-card.status {background:linear-gradient(145deg,#f5f9ff,#edf5ff); border-color:#cfe0ff;}
        .oasis-metric-card.stage {background:linear-gradient(145deg,#f8f8ff,#f1f0ff); border-color:#dcd8ff;}
        .oasis-metric-card.sales {background:linear-gradient(145deg,#f4fff9,#edf9f3); border-color:#ceeadd;}
        .oasis-metric-card.profit {background:linear-gradient(145deg,#fff9f2,#fff4e8); border-color:#f3ddc1;}
        .oasis-metric-label {color:#69758c; font-size:.86rem; font-weight:700; margin-bottom:15px;}
        .oasis-metric-value {color:#182338; font-size:1.56rem; font-weight:850; line-height:1.15;}
        .oasis-badge {
            display:inline-block; margin-top:11px; padding:5px 10px; border-radius:999px;
            color:#3568c8; background:rgba(76,132,237,.11); font-size:.78rem; font-weight:750;
        }
        .oasis-section-card {
            height:100%; min-height:245px; padding:20px 22px; border:1px solid #e4eaf3;
            border-radius:18px; background:#fff; box-shadow:0 7px 22px rgba(42,70,120,.055);
        }
        .oasis-section-card.blue {background:linear-gradient(145deg,#fff,#f5f9ff); border-color:#d7e4fb;}
        .oasis-section-card.amber {background:linear-gradient(145deg,#fffefa,#fff9ef); border-color:#f0dfbf;}
        .oasis-section-card.violet {background:linear-gradient(145deg,#fff,#f9f7ff); border-color:#e2dcfb;}
        .oasis-section-title {color:#1f2c43; font-size:1.13rem; font-weight:850; margin-bottom:14px;}
        .oasis-item {position:relative; padding:8px 0 8px 22px; color:#344158; line-height:1.5;}
        .oasis-item:before {content:"✓"; position:absolute; left:0; top:8px; color:#3478e5; font-weight:900;}
        .oasis-question-card {
            padding:20px 22px; border-radius:18px; border:1px solid #e4eaf3;
            background:linear-gradient(145deg,#fff,#f8faff);
            box-shadow:0 7px 22px rgba(42,70,120,.055);
        }
        .oasis-question {display:flex; gap:12px; align-items:flex-start; padding:9px 0; color:#354158;}
        .oasis-question-number {
            min-width:25px; height:25px; border-radius:8px; display:inline-flex;
            align-items:center; justify-content:center; background:#eaf2ff;
            color:#2867cf; font-size:.82rem; font-weight:850;
        }
        div[data-baseweb="tab-list"] {gap:10px; border-bottom:1px solid #e6ebf3;}
        button[data-baseweb="tab"] {padding:10px 12px 12px; font-weight:750;}
        @media (max-width:900px) {.oasis-metric-grid {grid-template-columns:repeat(2,minmax(0,1fr));}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_metric_dashboard(crm_status: str, pipeline_stage: str, sales_text: str, profit_text: str) -> None:
    st.markdown(
        f"""
        <div class="oasis-metric-grid">
            <div class="oasis-metric-card status">
                <div class="oasis-metric-label">CRM 상태</div>
                <div class="oasis-metric-value">{crm_status or '신규'}</div>
                <div class="oasis-badge">고객 상태</div>
            </div>
            <div class="oasis-metric-card stage">
                <div class="oasis-metric-label">상담 진행단계</div>
                <div class="oasis-metric-value">{pipeline_stage or '신규'}</div>
                <div class="oasis-badge">영업 파이프라인</div>
            </div>
            <div class="oasis-metric-card sales">
                <div class="oasis-metric-label">최근 매출액</div>
                <div class="oasis-metric-value">{sales_text}</div>
                <div class="oasis-badge">재무 규모</div>
            </div>
            <div class="oasis-metric-card profit">
                <div class="oasis-metric-label">최근 당기순이익</div>
                <div class="oasis-metric-value">{profit_text}</div>
                <div class="oasis-badge">수익성</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_list_card(title: str, items: list[str], theme: str, fallback: str) -> None:
    safe_items = items or [fallback]
    item_html = "".join(f'<div class="oasis-item">{item}</div>' for item in safe_items)
    st.markdown(
        f"""
        <div class="oasis-section-card {theme}">
            <div class="oasis-section-title">{title}</div>
            {item_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_enterprise_management_center(
    user_id: str,
    user_name: str = "",
) -> None:
    st.markdown("## 기업 컨설팅")
    st.caption(
        "고객 한 곳을 선택해 기업정보·CRM·정책자금 설정·주가평가·"
        "기업 히스토리를 한 화면에서 관리합니다."
    )

    all_customers = load_registered_customers(
        get_user_cumulative_db_path(user_id),
        owner_user_id=user_id,
    )
    if all_customers.empty:
        st.info(
            "등록된 고객이 없습니다. 크레탑 자동등록으로 고객을 먼저 등록해주세요."
        )
        return

    st.markdown(
        """
        <style>
        .enterprise-selector-title {
            font-size: 1.28rem;
            font-weight: 850;
            color: #182235;
            margin: 0.2rem 0 0.25rem 0;
        }
        div[data-testid="stButton"] button[kind="secondary"] {
            min-height: 40px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    title_col, search_col = st.columns([1.05, 2.2], vertical_alignment="bottom")
    with title_col:
        st.markdown(
            '<div class="enterprise-selector-title">관리할 기업</div>',
            unsafe_allow_html=True,
        )
    with search_col:
        search_text = st.text_input(
            "기업 검색",
            placeholder="회사명·대표자·사업자번호 검색",
            key="enterprise_customer_search_v720",
            label_visibility="collapsed",
        )

    customers = filter_active_customers(
        user_id=user_id,
        customers=all_customers,
        search_text=search_text,
    )
    if customers.empty:
        st.info("검색 결과가 없습니다.")
        return

    labels, row_map = build_customer_labels(customers)
    current_selected = st.session_state.get("enterprise_center_customer")
    if current_selected not in labels:
        st.session_state.pop("enterprise_center_customer", None)

    select_col, delete_col = st.columns([12, 0.65], vertical_alignment="bottom")
    with select_col:
        selected_label = st.selectbox(
            "관리할 기업 선택",
            labels,
            key="enterprise_center_customer",
            label_visibility="collapsed",
        )
    selected_row = customers.loc[row_map[selected_label]]
    with delete_col:
        if st.button(
            "×",
            help="선택 회사 삭제",
            key=f"enterprise_delete_x_v720_{selected_label}",
            use_container_width=True,
        ):
            confirm_delete_dialog(
                user_id=user_id,
                user_name=user_name,
                selected_row=selected_row,
            )

    company_name = _clean(selected_row.get("업체명", ""))
    business_no = _normalize_business_no(
        selected_row.get("사업자등록번호", "")
    )
    customer_key = make_customer_key(company_name, business_no)

    integration = reconcile_enterprise_consulting_context(
        user_id=user_id,
        business_no=business_no,
        company_name=company_name,
    )
    financial = _financial_snapshot(user_id, business_no)
    registry = _registry_snapshot(user_id, business_no)
    stock = _stock_record(user_id, business_no)
    preferences = integration.get("preferences", {}) or {}
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

    _render_enterprise_dashboard_styles()

    importance = str(crm_profile.get("priority", "3") or "3")
    try:
        importance_stars = "★" * int(importance) + "☆" * (5 - int(importance))
    except Exception:
        importance_stars = "★★★☆☆"

    st.markdown(
        f"""
        <div class="oasis-hero">
            <div class="oasis-title">{company_name or '기업명 미확인'}</div>
            <div class="oasis-meta">
                사업자번호 {business_no or '-'} &nbsp;·&nbsp;
                대표자 {_clean(selected_row.get('대표자명', '')) or '-'} &nbsp;·&nbsp;
                업종 {_clean(selected_row.get('업종명', '')) or '-'} &nbsp;·&nbsp;
                중요도 {importance_stars} &nbsp;·&nbsp;
                담당자 {crm_profile.get('assigned_manager') or user_name or '-'}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button(
        "이 기업 AI 코파일럿으로 분석하기",
        type="primary",
        use_container_width=True,
        key=f"enterprise_open_copilot_{business_no or company_name}",
    ):
        st.session_state["_oasis_copilot_business_no"] = business_no
        st.session_state["_oasis_copilot_company_name"] = company_name
        st.session_state["_oasis_pending_main_menu"] = "AI 코파일럿"
        st.rerun()

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

    _render_metric_dashboard(
        crm_record.get("status", "신규"),
        crm_profile.get("pipeline_stage", "신규"),
        _format_number(sales, "원"),
        _format_number(net_income, "원"),
    )

    (
        tab_overview,
        tab_crm,
        tab_policy,
        tab_stock,
        tab_articles,
        tab_history,
        tab_employees,
    ) = st.tabs(
        [
            "기업정보",
            "CRM",
            "정책자금",
            "주가평가·등기",
            "정관검토",
            "기업히스토리",
            "직원현황",
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
        crm_manage_tab, journal_view_tab = st.tabs(
            ["CRM 관리", "녹음파일 상담일지 보기"]
        )

        with crm_manage_tab:
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

            st.divider()
            render_audio_consultation_journal(
                user_id=user_id,
                customer_key=customer_key,
                company_name=company_name,
                business_no=business_no,
                consultant_name=(
                    crm_profile.get("assigned_manager")
                    or user_name
                    or ""
                ),
                current_crm=crm_record,
            )


        with journal_view_tab:
            render_saved_consultation_journals(
                user_id=user_id,
                business_no=business_no,
                company_name=company_name,
            )
            st.caption(
                "재연동 후 기업컨설팅의 정책자금 탭을 다시 열면 "
                "최신 키워드와 추천 결과가 반영됩니다."
            )

    with tab_policy:
        st.markdown("#### 고객별 정책자금 매칭설정")
        added_keywords = integration.get("added_keywords", []) or []
        added_interests = integration.get("added_interests", []) or []
        consultation_count = int(
            integration.get("consultation_context", {}).get("count", 0) or 0
        )
        st.caption(
            f"상담일지 {consultation_count}건 연동 · "
            f"자동추가 키워드 {len(added_keywords)}개 · "
            f"자동추가 관심분야 {len(added_interests)}개"
        )

        widget_suffix = (
            business_no.replace("-", "")
            if business_no
            else company_name
        )
        matching_keywords = st.text_area(
            "매칭키워드",
            value=", ".join(
                preferences.get("매칭키워드", []) or []
            ),
            key=f"enterprise_match_keywords_v650_{widget_suffix}",
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
            key=f"enterprise_interest_fields_v650_{widget_suffix}",
        )
        exclusion_keywords = st.text_area(
            "제외키워드",
            value=", ".join(
                preferences.get("제외키워드", []) or []
            ),
            key=f"enterprise_exclusion_keywords_v650_{widget_suffix}",
        )

        p1, p2, p3 = st.columns(3)
        with p1:
            fund_purpose = st.text_input(
                "자금사용목적",
                value=_clean(
                    preferences.get("자금사용목적", "")
                ),
                key=f"enterprise_fund_purpose_v650_{widget_suffix}",
            )
        with p2:
            planned_amount = st.text_input(
                "투자예정금액",
                value=_clean(
                    preferences.get("투자예정금액", "")
                ),
                key=f"enterprise_planned_amount_v650_{widget_suffix}",
            )
        with p3:
            planned_timing = st.text_input(
                "투자예정시기",
                value=_clean(
                    preferences.get("투자예정시기", "")
                ),
                key=f"enterprise_planned_timing_v650_{widget_suffix}",
            )

        if st.button(
            "정책자금 매칭설정 저장",
            use_container_width=True,
            key=f"enterprise_save_preferences_v650_{widget_suffix}",
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

        current_policy_preferences = {
            "매칭키워드": [
                item.strip()
                for item in matching_keywords.split(",")
                if item.strip()
            ],
            "관심지원분야": interest_fields,
            "제외키워드": [
                item.strip()
                for item in exclusion_keywords.split(",")
                if item.strip()
            ],
            "자금사용목적": fund_purpose,
            "투자예정금액": planned_amount,
            "투자예정시기": planned_timing,
        }

        st.divider()
        st.markdown("#### 다중소스 정책자금·고용지원금 매칭")
        render_multi_source_match(
            user_id,
            selected_row,
            current_policy_preferences,
        )

    with tab_stock:
        selected_stock_label = (
            f"{company_name} · {business_no}"
            if business_no
            else company_name
        )
        if selected_stock_label:
            st.session_state["stock_customer_selector"] = (
                selected_stock_label
            )

        render_stock_valuation_page(
            user_id=user_id,
            user_name=user_name,
        )

    with tab_articles:
        render_articles_review(
            user_id=user_id,
            business_no=business_no,
            company_name=company_name,
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

    with tab_employees:
        render_employee_status(
            user_id=user_id,
            business_no=business_no,
            company_name=company_name,
        )
