import streamlit as st
import subprocess
import sys
import os
import glob
import shutil
import pandas as pd
from pathlib import Path

from ui import apply_oasis_ui
from maintenance import render_system_management_page
from cretop_runner import run_cretop_worker
from cloud_admin import render_cloud_database_page
from ai_usage import render_ai_usage_page
from cloud_restore import restore_customer_db_if_needed
from cloud_crm_restore import restore_crm_from_cloud
from enterprise_center import render_enterprise_management_center
from enterprise_customer_management import render_customer_trash_page
from address_tools import (
    enrich_address_fields,
    repair_user_customer_addresses,
)
from crm_enhancements import (
    PIPELINE_OPTIONS,
    PRIORITY_OPTIONS,
    get_crm_profile,
    save_crm_profile,
    merge_profile_into_crm_record,
    get_profile_summary,
)
from customer_history import (
    save_customer_snapshot,
    get_customer_history,
    build_history_table,
    build_change_summary,
)
from cloud_sync import (
    get_cloud_sync_status,
    retry_cloud_sync_queue,
    sync_customer_snapshot,
    sync_crm_record,
)
from registered_policy_match import (
    load_registered_customers,
    build_customer_labels,
    customer_preview,
    create_single_customer_workbook,
)
from matching_preferences import (
    INTEREST_OPTIONS,
    get_matching_preferences,
    save_matching_preferences,
    preference_summary,
)
from multi_source_policy import render_multi_source_match
from consulting_copilot import render_copilot_page
from stock_valuation import (
    render_stock_valuation_page,
    save_cretop_financial_snapshot,
)
from crm import (
    STATUS_OPTIONS, ACTION_OPTIONS, make_customer_key, get_customer_record,
    upsert_customer_record, append_timeline_event, get_crm_summary,
    get_due_action_summary
)

from auth import (
    apply_env_secrets, check_login, login_form, logout_button,
    is_admin, list_pending_users, list_all_users_for_admin,
    approve_user, reject_user, render_password_change
)
from history import append_run_history, read_run_history, get_manager_stats
from utils import (
    ROOT_DIR, TEMPLATE_DIR, UPLOAD_DIR, RESULT_DIR,
    logo_html, make_upload_filename, find_customer_template,
    make_basic_customer_template_bytes, run_cleanup,
    move_result_files_to_results, extract_company_previews,
    get_user_dirs, get_user_cumulative_db_path, append_user_customer_db,
    append_cretop_to_user_customer_db,
    check_user_customer_duplicate, link_business_no_to_legacy_customer,
    refresh_existing_customer_from_cretop,
    ensure_user_cumulative_db_format, update_user_customer_record,
    count_user_cumulative_rows
)

apply_env_secrets()


# =====================================================
# 고객DB 다운로드/검증 안정화 유틸
# =====================================================
from io import BytesIO

BASE_DIR = Path(__file__).parent
TEMPLATE_DIR = BASE_DIR / "templates"
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
HISTORY_DIR = BASE_DIR / "history"

for _folder in [TEMPLATE_DIR, UPLOAD_DIR, RESULT_DIR, HISTORY_DIR]:
    _folder.mkdir(exist_ok=True)

def get_customer_template_download():
    candidates = [
        TEMPLATE_DIR / "고객DB_양식.xlsx",
        TEMPLATE_DIR / "고객DB(11).xlsx",
        BASE_DIR / "고객DB_양식.xlsx",
        BASE_DIR / "고객DB.xlsx",
    ]

    for path in candidates:
        if path.exists():
            with open(path, "rb") as f:
                return f.read(), "고객DB_양식.xlsx"

    columns = [
        "업체명", "대표자명", "사업자등록번호", "업종명", "사업장 소재지",
        "전년도매출", "올해예상매출", "매출감소여부", "상시근로자수",
        "고용보험가입인원", "희망상담주제1", "희망자금용도1",
        "키워드메모", "주요 사업내용", "비고"
    ]
    sample = pd.DataFrame([{"업체명": "예시기업", "업종명": "제조업"}], columns=columns)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        sample.to_excel(writer, sheet_name="고객DB", index=False)
        pd.DataFrame(columns=["상품명", "기관명", "대상지역", "대상업종", "추천키워드"]).to_excel(writer, sheet_name="상시정책자금DB", index=False)
        pd.DataFrame(columns=["제도명", "기관명", "대상", "추천키워드"]).to_excel(writer, sheet_name="고용지원금DB", index=False)
        pd.DataFrame({"안내": ["파일명은 변경 가능하지만 시트명과 컬럼명은 변경하지 마세요."]}).to_excel(writer, sheet_name="사용가이드", index=False)
    output.seek(0)
    return output.getvalue(), "고객DB_기본양식.xlsx"

def validate_customer_workbook(file_path):
    errors, warnings = [], []
    try:
        xls = pd.ExcelFile(file_path)
    except Exception as e:
        return False, [f"엑셀 파일을 읽을 수 없습니다: {e}"], warnings

    if "고객DB" not in xls.sheet_names:
        errors.append("필수 시트가 없습니다: 고객DB")

    if "고객DB" in xls.sheet_names:
        try:
            df = pd.read_excel(file_path, sheet_name="고객DB", nrows=3)
            cols = [str(c).strip() for c in df.columns]
            for col in ["업체명", "업종명"]:
                if col not in cols:
                    warnings.append(f"권장 컬럼이 없습니다: {col}")
        except Exception as e:
            errors.append(f"고객DB 시트를 읽을 수 없습니다: {e}")

    return len(errors) == 0, errors, warnings

st.set_page_config(
    page_title="OASIS 내부 CRM",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
.stApp {
    background: linear-gradient(135deg, #f6f9ff 0%, #ffffff 44%, #edf4ff 100%);
}
.block-container {
    padding-top: 1.1rem;
    padding-bottom: 3rem;
    max-width: 1180px;
}
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
/* v2.3.6: 메뉴 전환 시 Streamlit 기본 재실행 페이드 효과가 화면을 반투명하게 보이게 하는 현상 완화 */
.stApp,
div[data-testid="stAppViewContainer"],
section[data-testid="stSidebar"] {
    opacity: 1 !important;
    transition: none !important;
}
div[data-testid="stStatusWidget"] {
    visibility: hidden;
    height: 0px;
}

/* v3.2.1: Streamlit rerun 중 화면이 오래 반투명해 보이는 체감 완화 */
[data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"],
[data-testid="stSidebar"],
[data-testid="stHeader"],
.main,
.block-container {
    opacity: 1 !important;
    transition: none !important;
}
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
.stDeployButton {
    display: none !important;
    visibility: hidden !important;
}
.oasis-topbar {
    display:flex;
    justify-content:center;
    align-items:center;
    margin-bottom: 6px;
}
.login-wrap {
    max-width: 980px;
    margin: 0 auto;
    padding-top: 12px;
}
.login-logo {
    display:flex;
    justify-content:center;
    align-items:center;
    margin-bottom: 22px;
}
.login-panel {
    background: rgba(255, 255, 255, 0.96);
    border: 1px solid #dce7f7;
    border-radius: 24px;
    padding: 40px 42px 36px 42px;
    box-shadow: 0 18px 44px rgba(10, 42, 96, 0.08);
    margin-bottom: 24px;
}
.login-title {
    font-size: 34px;
    font-weight: 850;
    color: #082e68;
    letter-spacing: -1.0px;
    line-height: 1.2;
    margin-bottom: 16px;
}
.login-desc {
    color: #52647a;
    font-size: 16px;
    line-height: 1.75;
}
.hero {
    background: linear-gradient(135deg, #073b91 0%, #0b55c4 55%, #1b78ff 100%);
    border-radius: 32px;
    padding: 50px 54px;
    color: white;
    box-shadow: 0 22px 55px rgba(7, 59, 145, 0.25);
    margin-bottom: 28px;
}
.hero-title {
    font-size: 42px;
    font-weight: 850;
    line-height: 1.22;
    letter-spacing: -1.4px;
    margin-bottom: 14px;
}
.hero-sub {
    font-size: 18px;
    line-height: 1.7;
    opacity: 0.92;
}
.badge {
    display: inline-block;
    background: rgba(255,255,255,0.14);
    border: 1px solid rgba(255,255,255,0.28);
    padding: 8px 14px;
    border-radius: 999px;
    font-size: 14px;
    margin-bottom: 16px;
}
.oasis-card {
    background: rgba(255, 255, 255, 0.95);
    border: 1px solid #e3eaf6;
    border-radius: 24px;
    padding: 34px 38px;
    box-shadow: 0 18px 45px rgba(10, 42, 96, 0.08);
}
.point-card {
    background: white;
    border: 1px solid #e6edf8;
    border-radius: 20px;
    padding: 22px 22px 20px 22px;
    min-height: 145px;
    box-shadow: 0 12px 28px rgba(15, 55, 125, 0.06);
}
.point-icon {
    font-size: 27px;
    line-height: 1;
    margin-bottom: 14px;
}
.point-title {
    font-size: 18px;
    font-weight: 850;
    color: #082e68;
    line-height: 1.25;
    margin-bottom: 10px;
}
.point-desc {
    color: #64748b;
    font-size: 14px;
    line-height: 1.55;
    margin: 0;
}
.section-title {
    font-size: 24px;
    font-weight: 850;
    color: #082e68;
    margin-bottom: 10px;
    line-height: 1.25;
}
.section-desc {
    color: #64748b;
    font-size: 15px;
    margin-bottom: 20px;
    line-height: 1.65;
}
.preview-box {
    background:white;
    border:1px solid #e6edf8;
    border-radius:22px;
    padding:26px;
    box-shadow:0 12px 28px rgba(15, 55, 125, 0.06);
    margin-top:24px;
}
.stButton > button {
    background: linear-gradient(135deg, #063f9b 0%, #1261d8 100%);
    color: white;
    border: none;
    border-radius: 14px;
    padding: 0.78rem 1.4rem;
    font-weight: 750;
    box-shadow: 0 12px 24px rgba(18, 97, 216, 0.22);
}
.stDownloadButton > button {
    background: #0b2d66;
    color: white;
    border: none;
    border-radius: 14px;
    padding: 0.78rem 1.4rem;
    font-weight: 750;
}
[data-testid="stFileUploader"] {
    background: white;
    border: 1px dashed #b8c7df;
    border-radius: 22px;
    padding: 20px;
}
.stTextInput > div > div > input {
    border-radius: 13px;
    height: 46px;
}
.oasis-footer {
    text-align: center;
    color: #94a3b8;
    font-size: 13px;
    margin-top: 34px;
}

/* ================= v3.1.0 UI/UX 리뉴얼 ================= */
:root {
    --oasis-navy: #062b63;
    --oasis-blue: #0b5bd3;
    --oasis-sky: #eaf4ff;
    --oasis-line: #dce8f8;
    --oasis-text: #102a43;
    --oasis-muted: #64748b;
}
.stApp {
    background:
        radial-gradient(circle at top left, rgba(41, 126, 255, 0.12), transparent 36%),
        linear-gradient(135deg, #f7fbff 0%, #ffffff 45%, #eef6ff 100%);
}
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #062b63 0%, #073b91 52%, #0b5bd3 100%);
    border-right: 1px solid rgba(255,255,255,0.12);
}
[data-testid="stSidebar"] > div:first-child {
    padding-top: 1.15rem;
}
[data-testid="stSidebar"] img {
    display: block;
    margin: 0 auto 0.7rem auto;
    filter: drop-shadow(0 10px 22px rgba(0,0,0,0.16));
}
.sidebar-brand {
    text-align: center;
    color: white;
    margin: -4px 0 18px 0;
}
.sidebar-brand-title {
    font-size: 21px;
    font-weight: 900;
    letter-spacing: 0.5px;
    line-height: 1.15;
}
.sidebar-brand-sub {
    font-size: 12px;
    color: rgba(255,255,255,0.72);
    margin-top: 5px;
    letter-spacing: 1.2px;
}
.sidebar-user-card {
    background: rgba(255,255,255,0.12);
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 18px;
    padding: 13px 14px;
    margin: 6px 0 18px 0;
    color: white;
    box-shadow: 0 10px 28px rgba(0,0,0,0.10);
}
.sidebar-user-card .name {
    font-size: 17px;
    font-weight: 850;
    line-height: 1.25;
}
.sidebar-user-card .role {
    font-size: 12px;
    opacity: 0.75;
    margin-top: 4px;
}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stRadio > label,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] .stCaptionContainer {
    color: rgba(255,255,255,0.82) !important;
}
[data-testid="stSidebar"] div[role="radiogroup"] {
    gap: 8px;
}
[data-testid="stSidebar"] div[role="radiogroup"] label {
    background: rgba(255,255,255,0.08);
    border: 1px solid rgba(255,255,255,0.12);
    border-radius: 16px;
    padding: 12px 14px !important;
    margin: 0 0 8px 0;
    min-height: 48px;
    transition: all 0.15s ease;
}
[data-testid="stSidebar"] div[role="radiogroup"] label:hover {
    background: rgba(255,255,255,0.16);
    transform: translateX(3px);
}
[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {
    background: #ffffff;
    border-color: #ffffff;
    box-shadow: 0 14px 30px rgba(0,0,0,0.18);
}
[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) p {
    color: #062b63 !important;
    font-weight: 900 !important;
}
[data-testid="stSidebar"] div[role="radiogroup"] label p {
    font-size: 18px !important;
    font-weight: 780 !important;
    letter-spacing: -0.2px;
}
[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,0.16) !important;
    margin: 1rem 0;
}
.sidebar-section-label {
    color: rgba(255,255,255,0.56);
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 1.1px;
    margin: 18px 0 8px 4px;
    text-transform: uppercase;
}
.block-container {
    padding-top: 1.35rem;
    max-width: 1240px;
}
.oasis-topbar {
    justify-content: flex-start;
    background: rgba(255,255,255,0.74);
    border: 1px solid rgba(220,232,248,0.85);
    border-radius: 24px;
    padding: 16px 22px;
    box-shadow: 0 16px 38px rgba(15,55,125,0.07);
    backdrop-filter: blur(8px);
    margin-bottom: 24px;
}
.oasis-card, .preview-box, .point-card {
    border-radius: 26px;
    border: 1px solid rgba(220,232,248,0.98);
    box-shadow: 0 18px 42px rgba(15,55,125,0.08);
}
.oasis-card:hover, .point-card:hover {
    box-shadow: 0 22px 48px rgba(15,55,125,0.11);
}
.section-title, h1, h2, h3 {
    letter-spacing: -0.6px;
}
.hero {
    position: relative;
    overflow: hidden;
}
.hero:after {
    content: "";
    position: absolute;
    width: 290px;
    height: 290px;
    right: -80px;
    top: -70px;
    border-radius: 999px;
    background: rgba(255,255,255,0.12);
}
.metric-card {
    background: white;
    border: 1px solid #e6edf8;
    border-radius: 22px;
    padding: 22px 24px;
    box-shadow: 0 14px 34px rgba(15, 55, 125, 0.07);
}
.metric-title {
    color: #64748b;
    font-size: 13px;
    font-weight: 750;
    margin-bottom: 8px;
}
.metric-value {
    color: #062b63;
    font-size: 28px;
    font-weight: 900;
    line-height: 1.1;
}
.stButton > button, .stDownloadButton > button {
    min-height: 46px;
    font-size: 15px;
    border-radius: 16px;
}
[data-testid="stDataFrame"] {
    border-radius: 18px;
    overflow: hidden;
    border: 1px solid #e6edf8;
}


/* v3.1.5: 상단 헤더와 로그인 로고 크기 실제 반영 */
.login-logo img {
    width: 320px !important;
    max-width: 82% !important;
    height: auto !important;
}
.oasis-topbar {
    gap: 26px !important;
    padding: 22px 34px !important;
}
.oasis-topbar-logo img {
    width: 365px !important;
    max-width: 38vw !important;
    min-width: 300px !important;
    height: auto !important;
}
.oasis-topbar-title {
    font-size: 26px !important;
    font-weight: 950 !important;
    color: #062b63 !important;
    line-height: 1.2 !important;
    letter-spacing: -0.7px !important;
}
.oasis-topbar-sub {
    font-size: 14px !important;
    color: #64748b !important;
    margin-top: 7px !important;
    font-weight: 600 !important;
}

</style>
""", unsafe_allow_html=True)


# v3.1.1: 사이드바 로고/메뉴 디자인 최종 보정
apply_oasis_ui()

def show_company_preview(result_file):
    previews = extract_company_previews(result_file)

    st.markdown("<div class='preview-box'>", unsafe_allow_html=True)
    st.markdown("### 👀 업체별 TOP3 미리보기")

    if not previews:
        st.info("미리보기 데이터를 찾지 못했습니다. 결과 엑셀을 다운로드해 확인해주세요.")
        st.markdown("</div>", unsafe_allow_html=True)
        return

    company_names = list(previews.keys())
    selected_company = st.selectbox("업체 선택", company_names)

    if selected_company:
        st.dataframe(previews[selected_company], width='stretch', hide_index=True)

    st.markdown("</div>", unsafe_allow_html=True)


# =====================================================
# v3.0.0 고객관리 기본 화면 유틸
# =====================================================
def _is_blank_customer_value(value):
    """CRM 고객 수 계산용 공란 판정.

    엑셀 양식에 서식/검증이 999행까지 들어가 있으면 pandas가 빈 행을 함께 읽을 수 있다.
    이 함수는 실제 값이 없는 행(None, NaN, 공백, 문자열 None/nan)을 고객으로 세지 않기 위해 사용한다.
    """
    if pd.isna(value):
        return True
    text = str(value).strip()
    return text == "" or text.lower() in {"none", "nan", "null", "<na>", "-"}


def _clean_customer_df(df: pd.DataFrame) -> pd.DataFrame:
    """실제 입력된 고객 행만 남긴다.

    기준: 업체명 또는 사업자등록번호 중 하나라도 실제 값이 있는 행만 고객으로 인정한다.
    기존 고객DB 서식 행이 고객 수에 포함되어 999건으로 표시되는 문제를 방지한다.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    # 완전 공란 행 1차 제거
    df = df.dropna(how="all")

    key_cols = [c for c in ["업체명", "사업자등록번호"] if c in df.columns]
    if not key_cols:
        # 양식이 다른 경우 첫 번째 컬럼을 업체명 대용으로 사용
        key_cols = [df.columns[0]] if len(df.columns) else []

    if key_cols:
        valid_mask = df[key_cols].apply(
            lambda row: any(not _is_blank_customer_value(v) for v in row),
            axis=1
        )
        df = df[valid_mask]

    # 인덱스를 새로 맞춰 화면 표시와 상세 선택 오류를 방지
    return df.reset_index(drop=True)


@st.cache_data(show_spinner=False, ttl=60)
def _read_customer_sheet_cached(path_str: str, mtime: float):
    """누적 고객DB 고객DB 시트를 캐시로 읽고 실제 고객 행만 반환한다.

    v3.2.2: 서식만 있는 빈 행을 고객으로 세지 않도록 정리한다.
    """
    df = pd.read_excel(path_str, sheet_name="고객DB", dtype=object)
    return _clean_customer_df(df)


def read_current_user_customer_df(user_id):
    """로그인한 회원의 누적 고객DB를 읽어 고객관리 화면에 표시할 DataFrame을 반환한다."""
    path = get_user_cumulative_db_path(user_id)
    if not path.exists():
        return pd.DataFrame(), path

    try:
        # v3.2.1: 파일 구조 보정은 최초 진입 시에만 최소화하고, 실제 엑셀 로딩은 캐시 처리한다.
        path, _, _ = ensure_user_cumulative_db_format(user_id)
        mtime = path.stat().st_mtime
        df = _read_customer_sheet_cached(str(path), mtime)
        return df.copy(), path
    except Exception:
        return pd.DataFrame(), path


def render_home_page():
    st.markdown("""
    <div class="hero">
        <div class="badge">OASIS TAX & ACCOUNTING · v3.2.2</div>
        <div class="hero-title">오아시스 내부 CRM +<br>지원사업 컨설팅 플랫폼</div>
        <div class="hero-sub">
            고객DB, 크레탑 자동등록, 정책자금 매칭, 실행이력 관리를 하나의 내부 업무 시스템으로 통합합니다.
        </div>
    </div>
    """, unsafe_allow_html=True)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("""
        <div class="point-card">
            <div class="point-icon">👥</div>
            <div class="point-title">고객관리</div>
            <div class="point-desc">회원별 누적 고객DB를 검색하고 업체별 상세정보를 빠르게 확인합니다.</div>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown("""
        <div class="point-card">
            <div class="point-icon">📄</div>
            <div class="point-title">크레탑 자동등록</div>
            <div class="point-desc">크레탑 PDF에서 추출 가능한 정보를 고객DB에 자동으로 누적합니다.</div>
        </div>
        """, unsafe_allow_html=True)
    with c3:
        st.markdown("""
        <div class="point-card">
            <div class="point-icon">📌</div>
            <div class="point-title">정책자금 매칭</div>
            <div class="point-desc">업체별 추천사업과 상담 포인트를 결과 엑셀로 생성합니다.</div>
        </div>
        """, unsafe_allow_html=True)


def format_customer_display_value(value, number=False):
    """고객 상세화면에서 NaN/None을 숨기고 숫자에는 천 단위 콤마를 표시한다."""
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return ""

    if not number:
        return text

    try:
        numeric = float(text.replace(",", ""))
        if numeric.is_integer():
            return f"{int(numeric):,}"
        return f"{numeric:,.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return text


def render_customer_management_page(user_id):
    st.markdown("### 👥 고객관리 CRM")
    st.caption("내 누적 고객DB를 기준으로 고객을 검색하고, 고객 상태·상담메모·다음 액션·타임라인을 관리합니다.")

    df, path = read_current_user_customer_df(user_id)
    if df.empty:
        st.info("아직 등록된 고객DB가 없습니다. 고객DB 업로드 또는 크레탑 자동등록으로 먼저 고객을 추가해주세요.")
        return

    df = df.copy()
    total_count = len(df)
    company_col = "업체명" if "업체명" in df.columns else df.columns[0]
    biz_col = "사업자등록번호" if "사업자등록번호" in df.columns else None
    industry_col = "업종명" if "업종명" in df.columns else None

    # v3.2.0: 엑셀 원본을 건드리지 않고 CRM 보조 데이터에서 고객 상태를 불러온다.
    crm_keys = []
    crm_statuses = []
    for _, row in df.iterrows():
        key = make_customer_key(row.get(company_col, ""), row.get(biz_col, "") if biz_col else "")
        crm_keys.append(key)
        crm_statuses.append(get_customer_record(user_id, key).get("status", "신규"))
    df["CRM상태"] = crm_statuses

    crm_summary = get_crm_summary(user_id)

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("누적 고객 수", f"{total_count}건")
    with m2:
        if industry_col:
            st.metric("업종 수", f"{df[industry_col].dropna().astype(str).nunique()}개")
        else:
            st.metric("업종 수", "-")
    with m3:
        st.metric("상담중", f"{crm_summary.get('상담중', 0)}건")
    with m4:
        st.metric("신청/계약", f"{crm_summary.get('신청완료', 0) + crm_summary.get('계약완료', 0)}건")

    profile_summary = get_profile_summary(user_id)
    p1, p2, p3 = st.columns(3)
    p1.metric("중요 고객", f"{profile_summary.get('high_priority', 0)}건")
    p2.metric("진행 중", f"{profile_summary.get('active_pipeline', 0)}건")
    p3.metric("계약 완료", f"{profile_summary.get('completed', 0)}건")

    due_summary = get_due_action_summary(user_id)
    st.markdown("#### 후속관리 알림")
    d1, d2, d3 = st.columns(3)
    d1.metric("오늘 연락", f"{len(due_summary.get('today', []))}건")
    d2.metric("기한 경과", f"{len(due_summary.get('overdue', []))}건")
    d3.metric("7일 이내 예정", f"{len(due_summary.get('week', []))}건")

    alert_rows = (
        due_summary.get("overdue", [])
        + due_summary.get("today", [])
        + due_summary.get("week", [])
    )
    if alert_rows:
        with st.expander("후속관리 대상 고객 보기", expanded=False):
            st.dataframe(
                pd.DataFrame(alert_rows),
                hide_index=True,
                width='stretch',
            )

    st.markdown("#### 고객 검색/필터")
    f1, f2 = st.columns([2, 1])
    with f1:
        search_keyword = st.text_input("고객 검색", placeholder="업체명, 대표자명, 사업자번호, 업종명으로 검색")
    with f2:
        status_filter = st.selectbox("상태 필터", ["전체"] + STATUS_OPTIONS)

    filtered = df.copy()
    if search_keyword.strip():
        keyword = search_keyword.strip().lower()
        mask = filtered.astype(str).apply(
            lambda col: col.str.lower().str.contains(keyword, na=False)
        ).any(axis=1)
        filtered = filtered[mask]

    if status_filter != "전체":
        filtered = filtered[filtered["CRM상태"] == status_filter]

    display_cols = [c for c in ["CRM상태", "업체명", "대표자명", "사업자등록번호", "업종명", "사업장 소재지", "종업원수", "매출액", "영업이익", "당기순이익"] if c in filtered.columns]
    if not display_cols:
        display_cols = list(filtered.columns[:8])

    st.markdown("#### 고객 목록")

    if filtered.empty:
        st.warning("검색 결과가 없습니다.")
        return

    # v3.2.2: 고객이 많아져도 화면이 느려지지 않도록 20개씩 표시한다.
    page_size = 20
    total_filtered = len(filtered)
    total_pages = max((total_filtered - 1) // page_size + 1, 1)

    p_left, p_right = st.columns([1, 2])
    with p_left:
        page = st.number_input("페이지", min_value=1, max_value=total_pages, value=1, step=1)
    with p_right:
        st.caption(f"검색/필터 결과 {total_filtered}건 · {page_size}개씩 표시")

    start = (int(page) - 1) * page_size
    end = start + page_size
    page_df = filtered.iloc[start:end]

    st.dataframe(page_df[display_cols], width='stretch', hide_index=True)

    option_labels = []
    row_map = {}
    for idx, row in page_df.iterrows():
        company = str(row.get(company_col, "")).strip() or f"고객 {idx + 1}"
        biz_no = str(row.get(biz_col, "")).strip() if biz_col else ""
        label = f"{company}"
        if biz_no and biz_no.lower() not in {"nan", "none"}:
            label += f" · {biz_no}"
        label = f"{label} · #{idx}"
        option_labels.append(label)
        row_map[label] = idx

    st.markdown("#### 고객 상세/상담관리")
    selected_label = st.selectbox("상세보기 업체 선택", option_labels)
    selected_idx = row_map[selected_label]
    selected_row = df.loc[selected_idx]

    selected_company = str(selected_row.get(company_col, "") or "")
    selected_biz = str(selected_row.get(biz_col, "") or "") if biz_col else ""
    selected_key = make_customer_key(selected_company, selected_biz)
    crm_record = get_customer_record(user_id, selected_key)
    crm_profile = get_crm_profile(
        user_id,
        selected_key,
        selected_biz,
    )

    detail_left, detail_right = st.columns([1, 1])
    with detail_left:
        st.markdown("##### 기업 기본정보")
        st.write(f"**업체명**: {selected_row.get('업체명', '')}")
        st.write(f"**대표자명**: {selected_row.get('대표자명', '')}")
        st.write(f"**사업자등록번호**: {selected_row.get('사업자등록번호', '')}")
        st.write(f"**업종명**: {selected_row.get('업종명', '')}")
        st.write(f"**사업장 소재지**: {selected_row.get('사업장 소재지', '')}")
    with detail_right:
        st.markdown("##### 재무/규모")
        employee_value = selected_row.get("종업원수", selected_row.get("상시근로자수", ""))
        sales_value = selected_row.get("매출액", "")
        if not format_customer_display_value(sales_value):
            sales_value = selected_row.get("연매출", selected_row.get("전년도매출", ""))

        st.write(f"**종업원수**: {format_customer_display_value(employee_value, number=True)}")
        st.write(f"**매출액**: {format_customer_display_value(sales_value, number=True)}")
        st.write(f"**영업이익**: {format_customer_display_value(selected_row.get('영업이익', ''), number=True)}")
        st.write(f"**당기순이익**: {format_customer_display_value(selected_row.get('당기순이익', ''), number=True)}")
        st.write(
            f"**설립일**: "
            f"{format_customer_display_value(selected_row.get('설립일', selected_row.get('설립년도', '')))}"
        )

    y_cols = [c for c in ["벤처", "이노비즈", "메인비즈", "기업부설연구소", "연구개발전담부서", "특허보유", "상표", "R&D수행", "스마트공장도입"] if c in df.columns]
    if y_cols:
        with st.expander("인증·기술 현황"):
            st.dataframe(pd.DataFrame([{"항목": c, "값": selected_row.get(c, "")} for c in y_cols]), width='stretch', hide_index=True)

    memo_cols = [c for c in ["키워드메모", "주요 사업내용", "비고", "기술성메모"] if c in df.columns]
    if memo_cols:
        with st.expander("고객DB 메모/사업내용"):
            for col in memo_cols:
                val = selected_row.get(col, "")
                if pd.notna(val) and str(val).strip():
                    st.write(f"**{col}**")
                    st.write(str(val))

    with st.expander("고객 기본정보 직접 수정", expanded=False):
        edit_left, edit_right = st.columns(2)
        with edit_left:
            edit_company = st.text_input(
                "업체명",
                value=format_customer_display_value(selected_row.get("업체명", "")),
                key=f"edit_company_{selected_idx}",
            )
            edit_representative = st.text_input(
                "대표자명",
                value=format_customer_display_value(selected_row.get("대표자명", "")),
                key=f"edit_representative_{selected_idx}",
            )
            edit_business_no = st.text_input(
                "사업자등록번호",
                value=format_customer_display_value(selected_row.get("사업자등록번호", "")),
                key=f"edit_business_no_{selected_idx}",
            )
            edit_industry = st.text_input(
                "업종명",
                value=format_customer_display_value(selected_row.get("업종명", "")),
                key=f"edit_industry_{selected_idx}",
            )
            edit_address = st.text_area(
                "사업장 소재지",
                value=format_customer_display_value(selected_row.get("사업장 소재지", "")),
                height=90,
                key=f"edit_address_{selected_idx}",
            )
        with edit_right:
            edit_employee = st.text_input(
                "종업원수",
                value=format_customer_display_value(
                    selected_row.get("종업원수", selected_row.get("상시근로자수", ""))
                ),
                key=f"edit_employee_{selected_idx}",
            )
            edit_sales = st.text_input(
                "매출액",
                value=format_customer_display_value(selected_row.get("매출액", ""), number=True),
                key=f"edit_sales_{selected_idx}",
            )
            edit_operating = st.text_input(
                "영업이익",
                value=format_customer_display_value(selected_row.get("영업이익", ""), number=True),
                key=f"edit_operating_{selected_idx}",
            )
            edit_net = st.text_input(
                "당기순이익",
                value=format_customer_display_value(selected_row.get("당기순이익", ""), number=True),
                key=f"edit_net_{selected_idx}",
            )
            edit_establishment = st.text_input(
                "설립일",
                value=format_customer_display_value(
                    selected_row.get("설립일", selected_row.get("설립년도", ""))
                ),
                placeholder="YYYY-MM-DD",
                key=f"edit_establishment_{selected_idx}",
            )

        if st.button(
            "고객 기본정보 저장",
            key=f"save_customer_edit_{selected_idx}",
            width='stretch',
        ):
            def parse_numeric_input(value):
                text = str(value or "").replace(",", "").strip()
                if not text:
                    return ""
                try:
                    return int(float(text))
                except ValueError:
                    return text

            update_values = {
                "업체명": edit_company,
                "대표자명": edit_representative,
                "사업자등록번호": edit_business_no,
                "업종명": edit_industry,
                "사업장 소재지": edit_address,
                "종업원수": parse_numeric_input(edit_employee),
                "매출액": parse_numeric_input(edit_sales),
                "영업이익": parse_numeric_input(edit_operating),
                "당기순이익": parse_numeric_input(edit_net),
                "설립일": edit_establishment,
            }
            ok, msg = update_user_customer_record(
                user_id,
                selected_idx,
                update_values,
            )
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    st.markdown("""
    <style>
    .oasis-analysis-title {
        padding: 18px 22px;
        border-radius: 18px;
        background: linear-gradient(135deg, #0f3f91 0%, #2563eb 100%);
        color: white;
        margin: 18px 0 14px 0;
        box-shadow: 0 10px 28px rgba(37, 99, 235, 0.18);
    }
    .oasis-analysis-title h3 {
        margin: 0;
        font-size: 1.35rem;
        font-weight: 800;
    }
    .oasis-analysis-title p {
        margin: 7px 0 0 0;
        opacity: 0.88;
        font-size: 0.92rem;
    }
    .oasis-score-card {
        padding: 15px 18px;
        border-radius: 16px;
        background: #ffffff;
        border: 1px solid #dbe7fb;
        box-shadow: 0 7px 20px rgba(15, 63, 145, 0.08);
        margin-bottom: 12px;
    }
    .oasis-score-label {
        color: #64748b;
        font-size: 0.82rem;
        font-weight: 700;
    }
    .oasis-score-value {
        color: #0f3f91;
        font-size: 1.55rem;
        font-weight: 850;
        margin-top: 4px;
    }
    </style>
    <div class="oasis-analysis-title">
        <h3>AI 기업분석 카드</h3>
        <p>현재 고객DB와 크레탑 재무정보를 기준으로 상담 포인트를 빠르게 정리합니다.</p>
    </div>
    """, unsafe_allow_html=True)

    analysis_points = []
    risk_points = []
    question_points = []

    employee_number = selected_row.get(
        "종업원수",
        selected_row.get("상시근로자수", ""),
    )
    try:
        employee_number = int(float(str(employee_number).replace(",", "")))
    except Exception:
        employee_number = 0

    sales_number = selected_row.get(
        "매출액",
        selected_row.get("연매출", ""),
    )
    try:
        sales_number = int(float(str(sales_number).replace(",", "")))
    except Exception:
        sales_number = 0

    operating_number = selected_row.get("영업이익", "")
    net_number = selected_row.get("당기순이익", "")
    try:
        operating_number = int(float(str(operating_number).replace(",", "")))
    except Exception:
        operating_number = 0
    try:
        net_number = int(float(str(net_number).replace(",", "")))
    except Exception:
        net_number = 0

    completeness_fields = [
        selected_row.get("사업자등록번호", ""),
        selected_row.get("사업장 소재지", ""),
        selected_row.get("설립일", selected_row.get("설립년도", "")),
        sales_number,
        operating_number,
        net_number,
        employee_number,
    ]
    completed_fields = sum(
        1 for value in completeness_fields
        if format_customer_display_value(value)
    )
    completeness_score = round(completed_fields / len(completeness_fields) * 100)

    industry_text = str(selected_row.get("업종명", "") or "")
    if sales_number:
        analysis_points.append(f"최근 확인 매출액은 {sales_number:,}원입니다.")
    if operating_number:
        analysis_points.append(f"영업이익은 {operating_number:,}원으로 확인됩니다.")
    if net_number:
        analysis_points.append(f"당기순이익은 {net_number:,}원으로 확인됩니다.")
    if employee_number:
        analysis_points.append(f"종업원수는 {employee_number:,}명입니다.")
    if any(word in industry_text for word in ["제조", "건설", "운송", "물류"]):
        analysis_points.append("시설·운전자금 및 보증기관 연계 검토 가치가 있습니다.")
    if str(selected_row.get("벤처", "")).upper() == "Y":
        analysis_points.append("벤처기업 우대사업을 우선 검토할 수 있습니다.")
    if (
        str(selected_row.get("기업부설연구소", "")).upper() == "Y"
        or str(selected_row.get("연구개발전담부서", "")).upper() == "Y"
    ):
        analysis_points.append("R&D·기술개발 지원사업 검토에 유리한 요소가 있습니다.")

    if not net_number:
        risk_points.append("당기순이익 확인이 필요합니다.")
    if not format_customer_display_value(selected_row.get("사업장 소재지", "")):
        risk_points.append("사업장 소재지 보완이 필요합니다.")
    if not format_customer_display_value(
        selected_row.get("설립일", selected_row.get("설립년도", ""))
    ):
        risk_points.append("설립일 보완이 필요합니다.")

    question_points.extend([
        "최근 1년 이내 시설·기계·차량 투자계획이 있나요?",
        "향후 6개월 내 신규채용 또는 고용유지 계획이 있나요?",
        "기존 정책자금·보증기관 대출 잔액과 만기는 어떻게 되나요?",
    ])

    s1, s2, s3 = st.columns(3)
    with s1:
        st.markdown(
            f"""
            <div class="oasis-score-card">
                <div class="oasis-score-label">정보 완성도</div>
                <div class="oasis-score-value">{completeness_score}%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with s2:
        profitability = "흑자" if net_number > 0 else "확인 필요"
        st.markdown(
            f"""
            <div class="oasis-score-card">
                <div class="oasis-score-label">수익성 상태</div>
                <div class="oasis-score-value">{profitability}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with s3:
        followup_count = len(risk_points)
        st.markdown(
            f"""
            <div class="oasis-score-card">
                <div class="oasis-score-label">보완 필요</div>
                <div class="oasis-score-value">{followup_count}건</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    a1, a2, a3 = st.columns(3)
    with a1:
        with st.container(border=True):
            st.markdown("#### 검토 포인트")
            for item in analysis_points or ["추가 기업정보 확인이 필요합니다."]:
                st.markdown(f"- {item}")
    with a2:
        with st.container(border=True):
            st.markdown("#### 확인 필요사항")
            for item in risk_points or ["주요 누락정보가 확인되지 않았습니다."]:
                st.markdown(f"- {item}")
    with a3:
        with st.container(border=True):
            st.markdown("#### 대표 미팅 질문")
            for item in question_points:
                st.markdown(f"- {item}")

    st.caption(
        "현재 고객DB 값에 따른 사전 분석입니다. "
        "최종 정책자금 추천은 정책자금 매칭 결과와 함께 판단합니다."
    )

    st.markdown("##### 기업 히스토리")
    customer_history = get_customer_history(
        user_id,
        selected_biz,
    )
    if customer_history:
        for change_message in build_change_summary(
            customer_history
        ):
            st.write(f"• {change_message}")

        with st.expander("기업 데이터 변경이력 보기", expanded=False):
            history_df = build_history_table(customer_history)
            st.dataframe(
                history_df,
                hide_index=True,
                width='stretch',
            )
    else:
        st.info(
            "아직 저장된 기업 히스토리가 없습니다. "
            "크레탑 PDF를 다시 분석하면 스냅샷이 생성됩니다."
        )

    st.markdown("##### CRM 관리")
    with st.form(key=f"crm_form_{selected_key}"):
        c1, c2, c3 = st.columns([1, 1, 1])
        with c1:
            current_status = crm_record.get("status", "신규")
            status_idx = STATUS_OPTIONS.index(current_status) if current_status in STATUS_OPTIONS else 0
            new_status = st.selectbox("고객 상태", STATUS_OPTIONS, index=status_idx)
        with c2:
            current_action = crm_record.get("next_action", "없음")
            action_idx = ACTION_OPTIONS.index(current_action) if current_action in ACTION_OPTIONS else len(ACTION_OPTIONS) - 1
            new_action = st.selectbox("다음 액션", ACTION_OPTIONS, index=action_idx)
        with c3:
            from datetime import date, datetime
            raw_next_date = str(crm_record.get("next_date", "") or "")
            try:
                default_next_date = datetime.strptime(raw_next_date[:10], "%Y-%m-%d").date()
            except ValueError:
                default_next_date = None
            selected_next_date = st.date_input(
                "다음 예정일",
                value=default_next_date,
                key=f"crm_next_date_{selected_key}",
            )
            new_next_date = selected_next_date.strftime("%Y-%m-%d") if selected_next_date else ""

        e1, e2, e3 = st.columns([1, 1, 1])
        with e1:
            current_stage = crm_profile.get("pipeline_stage", "신규")
            stage_index = (
                PIPELINE_OPTIONS.index(current_stage)
                if current_stage in PIPELINE_OPTIONS
                else 0
            )
            pipeline_stage = st.selectbox(
                "상담 진행단계",
                PIPELINE_OPTIONS,
                index=stage_index,
            )
        with e2:
            current_priority = str(crm_profile.get("priority", "3"))
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
            )
        with e3:
            assigned_manager = st.text_input(
                "담당자",
                value=str(
                    crm_profile.get("assigned_manager", "")
                    or CURRENT_USER_NAME
                    or ""
                ),
            )

        new_memo = st.text_area("상담 메모", value=str(crm_record.get("memo", "") or ""), height=140, placeholder="대표 상담내용, 니즈, 후속조치 등을 기록하세요.")
        submitted = st.form_submit_button("CRM 정보 저장", width='stretch')

    if submitted:
        saved_profile = save_crm_profile(
            user_id,
            selected_key,
            pipeline_stage,
            priority,
            assigned_manager,
        )
        detail = (
            f"상태: {new_status} / 진행단계: {pipeline_stage} / "
            f"중요도: {priority} / 담당자: {assigned_manager} / "
            f"다음 액션: {new_action} / 예정일: {new_next_date}"
        )
        ok, msg = upsert_customer_record(
            user_id=user_id,
            customer_key=selected_key,
            company_name=selected_company,
            business_no=selected_biz,
            status=new_status,
            next_action=new_action,
            next_date=new_next_date,
            memo=new_memo,
            event_title="CRM 정보 저장",
            event_detail=detail,
        )
        if ok:
            updated_crm = get_customer_record(
                user_id,
                selected_key,
            )
            updated_crm = merge_profile_into_crm_record(
                updated_crm,
                saved_profile,
            )
            sync_crm_record(
                user_id,
                selected_biz,
                updated_crm,
            )
            st.success(msg)
            st.rerun()
        else:
            st.error(msg)

    with st.expander("타임라인 추가", expanded=False):
        tl_title = st.text_input("이력 제목", placeholder="예: 대표 통화, 자료요청, 제안서 발송")
        tl_detail = st.text_area("이력 내용", height=100, placeholder="상담 내용이나 후속조치 내용을 입력하세요.")
        if st.button("타임라인 저장", width='stretch'):
            if not tl_title.strip() and not tl_detail.strip():
                st.warning("저장할 내용을 입력해주세요.")
            else:
                ok, msg = append_timeline_event(
                    user_id,
                    selected_key,
                    tl_title.strip() or "상담이력",
                    tl_detail.strip(),
                )
                if ok:
                    updated_crm = get_customer_record(
                        user_id,
                        selected_key,
                    )
                    updated_crm = merge_profile_into_crm_record(
                        updated_crm,
                        crm_profile,
                    )
                    sync_crm_record(
                        user_id,
                        selected_biz,
                        updated_crm,
                    )
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    st.markdown("##### 상담 타임라인")
    latest_record = get_customer_record(user_id, selected_key)
    timeline = latest_record.get("timeline", [])
    if not timeline:
        st.info("아직 등록된 상담이력이 없습니다.")
    else:
        for item in timeline[:10]:
            with st.container(border=True):
                st.write(f"**{item.get('title', '상담이력')}**")
                st.caption(item.get("at", ""))
                if item.get("detail"):
                    st.write(item.get("detail"))

def render_cumulative_db_page(user_id):
    st.markdown("### 📥 내 누적 고객DB")
    st.caption("회원별로 누적된 고객DB를 기존 고객DB 양식 그대로 다운로드합니다.")

    cumulative_db_path = get_user_cumulative_db_path(user_id)
    if not cumulative_db_path.exists():
        st.info("아직 누적 저장된 고객DB가 없습니다.")
        return

    cumulative_db_path, total_count, _ = ensure_user_cumulative_db_format(user_id)
    address_repair = repair_user_customer_addresses(user_id)
    if address_repair.get("updated_rows", 0) > 0:
        st.success(address_repair.get("message", ""))
    st.success(f"현재 누적 고객 수: {total_count}건")
    with open(cumulative_db_path, "rb") as f:
        st.download_button(
            label="내 누적 고객DB 다운로드",
            data=f,
            file_name="고객DB누적.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width='stretch'
        )


if not check_login():
    login_form(logo_html)

CURRENT_USER_ID = st.session_state.get("current_user_id", "")
CURRENT_USER_NAME = st.session_state.get("current_user_name", "")
CURRENT_USER_IS_ADMIN = is_admin(CURRENT_USER_ID)
USER_DIRS = get_user_dirs(CURRENT_USER_ID)
USER_UPLOAD_DIR = USER_DIRS["uploads"]
USER_RESULT_DIR = USER_DIRS["results"]

# Streamlit Cloud는 재배포 시 로컬 파일이 초기화될 수 있다.
# 기존 로컬 고객이 없을 때만 Supabase 고객자료를 자동 복원한다.
restore_session_key = f"cloud_customer_restore_{CURRENT_USER_ID}"
if not st.session_state.get(restore_session_key):
    restore_result = restore_customer_db_if_needed(CURRENT_USER_ID)
    st.session_state[restore_session_key] = True
    st.session_state["cloud_customer_restore_result"] = restore_result

crm_restore_session_key = f"cloud_crm_restore_{CURRENT_USER_ID}"
if not st.session_state.get(crm_restore_session_key):
    crm_restore_result = restore_crm_from_cloud(CURRENT_USER_ID)
    st.session_state[crm_restore_session_key] = True
    st.session_state["cloud_crm_restore_result"] = crm_restore_result

# v3.0.0: 상단 탭/가로 메뉴 대신 사이드바 기반 메뉴로 전환
with st.sidebar:
    st.markdown(logo_html(360), unsafe_allow_html=True)
    st.markdown("""
    <div class="sidebar-brand">
        <div class="sidebar-brand-title">오아시스 세무회계</div>
        <div class="sidebar-brand-sub">OASIS TAX & ACCOUNTING</div>
    </div>
    """, unsafe_allow_html=True)

    if CURRENT_USER_NAME:
        role_badge = "관리자" if CURRENT_USER_IS_ADMIN else "회원"
        st.markdown(f"""
        <div class="sidebar-user-card">
            <div class="name">{CURRENT_USER_NAME}님</div>
            <div class="role">{role_badge} 계정으로 로그인 중</div>
        </div>
        """, unsafe_allow_html=True)

    st.markdown('<div class="sidebar-section-label">MAIN</div>', unsafe_allow_html=True)
    menu_label_map = {
        "홈": "홈",
        "크레탑 자동등록": "크레탑 자동등록",
        "기업 컨설팅": "기업관리센터",
        "AI 코파일럿": "AI 코파일럿",
        "내 누적 고객DB": "내 누적 고객DB",
        "실행이력": "실행이력",
        "담당자 통계": "담당자 통계",
    }
    if CURRENT_USER_IS_ADMIN:
        menu_label_map["회원 승인 관리"] = "회원 승인 관리"
        menu_label_map["시스템 관리"] = "시스템 관리"
        menu_label_map["클라우드 DB 관리"] = "클라우드 DB 관리"
        menu_label_map["AI 사용량"] = "AI 사용량"

    # 휴지통은 좌측 메뉴의 가장 마지막 항목으로 유지
    menu_label_map["휴지통"] = "휴지통"

    pending_menu = st.session_state.pop(
        "_oasis_pending_main_menu",
        None,
    )
    if pending_menu in menu_label_map:
        st.session_state["active_main_menu_v311"] = pending_menu

    current_sidebar_value = st.session_state.get(
        "active_main_menu_v311"
    )
    if current_sidebar_value not in menu_label_map:
        st.session_state["active_main_menu_v311"] = "기업 컨설팅"

    selected_menu_label = st.radio(
        "메뉴",
        list(menu_label_map.keys()),
        key="active_main_menu_v311",
        label_visibility="collapsed"
    )
    active_tab = menu_label_map[selected_menu_label]

    # v5.0 호환 처리: 이전 세션의 고객관리 메뉴는 통합 화면으로 이동
    if active_tab in {
        "고객관리",
        "통합 정책자금 매칭",
        "주가평가",
    }:
        active_tab = "기업관리센터"

    st.divider()
    render_password_change(CURRENT_USER_ID)
    logout_button()

st.markdown(f"""
<div class="oasis-topbar">
    <div class="oasis-topbar-logo">{logo_html(365)}</div>
    <div style="margin-left:22px;">
        <div class="oasis-topbar-title">오아시스 내부 업무 시스템</div>
        <div class="oasis-topbar-sub">기업 컨설팅 · 크레탑 자동등록 · AI 코파일럿</div>
    </div>
</div>
""", unsafe_allow_html=True)

if active_tab == "홈":
    restore_notice = st.session_state.get(
        "cloud_customer_restore_result",
        {},
    )
    if restore_notice.get("restored"):
        st.success(restore_notice.get("message", ""))

    crm_restore_notice = st.session_state.get(
        "cloud_crm_restore_result",
        {},
    )
    if crm_restore_notice.get("restored", 0) > 0:
        st.success(crm_restore_notice.get("message", ""))

    render_home_page()

elif active_tab == "기업관리센터":
    render_enterprise_management_center(
        CURRENT_USER_ID,
        CURRENT_USER_NAME,
    )

elif active_tab == "휴지통":
    render_customer_trash_page(
        CURRENT_USER_ID,
        CURRENT_USER_NAME,
    )

elif active_tab == "AI 코파일럿":
    render_copilot_page(
        CURRENT_USER_ID,
        CURRENT_USER_NAME,
    )

elif active_tab == "내 누적 고객DB":
    render_cumulative_db_page(CURRENT_USER_ID)

elif active_tab == "통합 정책자금 매칭":
    st.markdown("### 등록 고객 통합 정책자금 AI 매칭")
    st.caption(
        "크레탑 자동등록으로 생성된 누적 고객DB에서 업체를 선택하면 "
        "별도 엑셀 업로드 없이 바로 정책자금 매칭을 실행합니다."
    )

    cumulative_db_path = get_user_cumulative_db_path(CURRENT_USER_ID)
    registered_customers = load_registered_customers(cumulative_db_path)

    if registered_customers.empty:
        st.info(
            "등록된 고객이 없습니다. 먼저 크레탑 자동등록 또는 고객DB 업로드로 "
            "고객을 등록해주세요."
        )
    else:
        customer_labels, customer_row_map = build_customer_labels(
            registered_customers
        )

        selected_customer_label = st.selectbox(
            "매칭할 등록 고객",
            customer_labels,
            key="policy_registered_customer_selector",
        )
        selected_customer_index = customer_row_map[selected_customer_label]
        selected_customer_row = registered_customers.loc[
            selected_customer_index
        ]

        preview_df = customer_preview(selected_customer_row)
        if not preview_df.empty:
            with st.expander("선택 고객정보 확인", expanded=True):
                st.dataframe(
                    preview_df,
                    hide_index=True,
                    width='stretch',
                )

        missing_fields = []
        required_checks = {
            "업종명": ["업종명"],
            "사업장 소재지": ["사업장 소재지"],
            "설립일": ["설립일", "설립년도"],
            "종업원수": ["종업원수", "상시근로자수"],
            "매출액": ["매출액", "연매출", "전년도매출"],
        }

        for display_name, candidates in required_checks.items():
            has_value = False
            for candidate in candidates:
                if candidate not in selected_customer_row.index:
                    continue
                value = selected_customer_row.get(candidate)
                if (
                    value is not None
                    and str(value).strip().lower()
                    not in {"", "nan", "none", "nat"}
                ):
                    has_value = True
                    break
            if not has_value:
                missing_fields.append(display_name)

        if missing_fields:
            st.warning(
                "다음 정보가 비어 있어 일부 사업의 매칭 정확도가 낮아질 수 있습니다: "
                + ", ".join(missing_fields)
            )

        selected_business_no = selected_customer_row.get(
            "사업자등록번호",
            "",
        )
        saved_preferences = get_matching_preferences(
            CURRENT_USER_ID,
            selected_business_no,
        )

        st.markdown("#### 고객별 매칭설정")
        st.caption(
            "입력한 키워드는 선택 고객용 임시 매칭파일에만 반영되며 "
            "기존 고객DB 원본은 수정하지 않습니다."
        )

        matching_keywords_text = st.text_area(
            "매칭키워드",
            value=", ".join(
                saved_preferences.get("매칭키워드", []) or []
            ),
            placeholder="예: 골재운송, 중장비, 차량구매, 운전자금",
            key=f"policy_match_keywords_{selected_customer_index}",
            height=80,
        )

        selected_interests = st.multiselect(
            "관심지원분야",
            INTEREST_OPTIONS,
            default=[
                item
                for item in (
                    saved_preferences.get("관심지원분야", []) or []
                )
                if item in INTEREST_OPTIONS
            ],
            key=f"policy_interest_fields_{selected_customer_index}",
        )

        exclusion_keywords_text = st.text_area(
            "제외키워드",
            value=", ".join(
                saved_preferences.get("제외키워드", []) or []
            ),
            placeholder="예: 온라인마케팅, 수출, 스마트공장",
            key=f"policy_exclusion_keywords_{selected_customer_index}",
            height=70,
        )

        p1, p2, p3 = st.columns(3)
        with p1:
            fund_purpose = st.text_input(
                "자금사용목적",
                value=str(
                    saved_preferences.get("자금사용목적", "") or ""
                ),
                placeholder="예: 차량 교체",
                key=f"policy_fund_purpose_{selected_customer_index}",
            )
        with p2:
            planned_amount = st.text_input(
                "투자예정금액",
                value=str(
                    saved_preferences.get("투자예정금액", "") or ""
                ),
                placeholder="예: 2억원",
                key=f"policy_planned_amount_{selected_customer_index}",
            )
        with p3:
            planned_timing = st.text_input(
                "투자예정시기",
                value=str(
                    saved_preferences.get("투자예정시기", "") or ""
                ),
                placeholder="예: 2026년 하반기",
                key=f"policy_planned_timing_{selected_customer_index}",
            )

        if st.button(
            "고객별 매칭설정 저장",
            key=f"policy_save_preferences_{selected_customer_index}",
            width='stretch',
        ):
            try:
                saved_preferences = save_matching_preferences(
                    CURRENT_USER_ID,
                    selected_business_no,
                    company_name=str(
                        selected_customer_row.get("업체명", "") or ""
                    ),
                    matching_keywords=matching_keywords_text,
                    interest_fields=selected_interests,
                    exclusion_keywords=exclusion_keywords_text,
                    fund_purpose=fund_purpose,
                    planned_amount=planned_amount,
                    planned_timing=planned_timing,
                )
                st.success("고객별 정책자금 매칭설정을 저장했습니다.")
            except Exception as exc:
                st.error(f"매칭설정 저장 중 오류가 발생했습니다: {exc}")

        current_multi_source_preferences = {
            "매칭키워드": [
                item.strip()
                for item in matching_keywords_text.split(",")
                if item.strip()
            ],
            "관심지원분야": selected_interests,
            "제외키워드": [
                item.strip()
                for item in exclusion_keywords_text.split(",")
                if item.strip()
            ],
            "자금사용목적": fund_purpose,
            "투자예정금액": planned_amount,
            "투자예정시기": planned_timing,
        }

        with st.container(border=True):
            render_multi_source_match(
                CURRENT_USER_ID,
                selected_customer_row,
                current_multi_source_preferences,
            )

        registered_manager_name = st.text_input(
            "담당자명",
            value=CURRENT_USER_NAME or "",
            key="policy_registered_manager_name",
        )

        if False and st.button(
            "선택 고객 정책자금 매칭 실행",
            type="primary",
            width='stretch',
            key="policy_registered_match_button",
        ):
            if not registered_manager_name.strip():
                st.warning("담당자명을 입력해주세요.")
            else:
                try:
                    cumulative_db_path, _, _ = (
                        ensure_user_cumulative_db_format(CURRENT_USER_ID)
                    )

                    with st.spinner(
                        "선택 고객용 매칭파일을 만들고 정책자금을 분석 중입니다..."
                    ):
                        current_preferences = save_matching_preferences(
                            CURRENT_USER_ID,
                            selected_business_no,
                            company_name=str(
                                selected_customer_row.get("업체명", "") or ""
                            ),
                            matching_keywords=matching_keywords_text,
                            interest_fields=selected_interests,
                            exclusion_keywords=exclusion_keywords_text,
                            fund_purpose=fund_purpose,
                            planned_amount=planned_amount,
                            planned_timing=planned_timing,
                        )

                        single_customer_path = create_single_customer_workbook(
                            cumulative_path=cumulative_db_path,
                            destination_dir=USER_UPLOAD_DIR,
                            selected_row=selected_customer_row,
                            manager_name=registered_manager_name,
                            matching_preferences=current_preferences,
                        )

                        # 기존 main.py 매칭 엔진 호환용 임시파일.
                        # 원본 누적 고객DB는 수정하지 않는다.
                        shutil.copy2(
                            single_customer_path,
                            ROOT_DIR / "고객DB.xlsx",
                        )

                        before_files = set(glob.glob("매칭결과_*.xlsx"))
                        result = subprocess.run(
                            [sys.executable, str(ROOT_DIR / "main.py")],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="ignore",
                        )

                        moved_files = move_result_files_to_results(
                            before_files
                        )

                    if moved_files:
                        latest_file = max(
                            moved_files,
                            key=os.path.getmtime,
                        )
                        user_result_path = (
                            USER_RESULT_DIR
                            / os.path.basename(latest_file)
                        )
                        try:
                            shutil.copy2(
                                latest_file,
                                user_result_path,
                            )
                            latest_file = str(user_result_path)
                        except Exception:
                            pass

                        st.session_state.latest_result_file = latest_file
                        company_name = str(
                            selected_customer_row.get("업체명", "")
                            or ""
                        )

                        append_run_history(
                            upload_file_name=single_customer_path.name,
                            result_file=latest_file,
                            status="성공",
                            memo=f"등록 고객 자동매칭: {company_name}",
                            manager_name=registered_manager_name,
                            user_id=CURRENT_USER_ID,
                        )

                        run_cleanup()
                        st.success(
                            f"{company_name} 정책자금 매칭이 완료되었습니다."
                        )
                    else:
                        append_run_history(
                            single_customer_path.name,
                            "",
                            "실패",
                            "등록 고객 자동매칭 결과파일 없음",
                            manager_name=registered_manager_name,
                            user_id=CURRENT_USER_ID,
                        )
                        run_cleanup()
                        st.error(
                            "결과 파일을 찾지 못했습니다. 실행 로그를 확인해주세요."
                        )

                    with st.expander("실행 로그 보기"):
                        if result.stdout:
                            st.code(result.stdout)
                        if result.stderr:
                            st.code(result.stderr)

                except PermissionError:
                    st.error(
                        "고객DB 파일이 열려 있습니다. 엑셀 파일을 닫고 다시 실행해주세요."
                    )
                except Exception as exc:
                    st.error(
                        f"등록 고객 정책자금 매칭 중 오류가 발생했습니다: {exc}"
                    )

    st.divider()
    show_legacy_upload = st.checkbox(
        "관리자·레거시 고객DB 업로드 도구 보기",
        value=False,
        key="show_legacy_customer_upload_v660",
    )
    if not show_legacy_upload:
        st.info(
            "일반 매칭은 위 등록 고객 통합매칭을 사용합니다. "
            "고객DB 업로드는 기존 호환과 일괄등록 용도로만 유지됩니다."
        )
        st.stop()

    with st.expander(
        "기존 방식: 고객DB 엑셀 파일 직접 업로드",
        expanded=False,
    ):
        st.caption(
            "외부 고객DB 파일을 직접 매칭해야 하는 경우에만 사용하세요."
        )

    left, right = st.columns([1.2, 1])

    with left:
        st.markdown("""
        <div class="oasis-card">
            <div class="section-title">고객DB 업로드</div>
            <div class="section-desc">
                담당자명을 입력하고 고객DB 엑셀 파일을 업로드한 뒤 매칭 실행 버튼을 눌러주세요.<br>
                <b>파일명은 자유롭게 변경 가능하지만, 시트명과 컬럼명은 변경하지 말아주세요.</b>
            </div>
        """, unsafe_allow_html=True)

        manager_name = st.text_input("담당자명", placeholder="예: 임주형")

        template_file = find_customer_template()
        if template_file:
            with open(template_file, "rb") as f:
                template_data = f.read()
            template_name = "고객DB_양식.xlsx"
        else:
            template_data = make_basic_customer_template_bytes()
            template_name = "고객DB_기본양식.xlsx"

        st.download_button(
            label="고객DB 양식 다운로드",
            data=template_data,
            file_name=template_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            width='stretch'
        )

        cumulative_db_path = get_user_cumulative_db_path(CURRENT_USER_ID)
        if cumulative_db_path.exists():
            cumulative_db_path, _, _ = ensure_user_cumulative_db_format(CURRENT_USER_ID)
            with open(cumulative_db_path, "rb") as f:
                st.download_button(
                    label="내 누적 고객DB 다운로드",
                    data=f,
                    file_name="고객DB누적.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width='stretch'
                )
        else:
            st.caption("아직 누적 저장된 고객DB가 없습니다.")

        uploaded_file = st.file_uploader(
            "고객DB.xlsx 파일 업로드",
            type=["xlsx"],
            label_visibility="collapsed"
        )

        st.markdown("</div>", unsafe_allow_html=True)

    with right:
        st.markdown("""
        <div class="oasis-card">
            <div class="section-title">내부 사용 안내</div>
            <div class="section-desc">
                1. 고객DB 양식 다운로드<br>
                2. 담당자명 입력 및 고객DB 업로드<br>
                3. 매칭 실행<br>
                4. 결과 확인 및 다운로드
            </div>
        </div>
        """, unsafe_allow_html=True)

    if uploaded_file is not None:
        if not manager_name.strip():
            st.warning("담당자명을 입력해주세요.")
            st.stop()

        uploaded_save_name = make_upload_filename(uploaded_file.name)
        uploaded_save_path = USER_UPLOAD_DIR / uploaded_save_name

        try:
            with open(uploaded_save_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            # 기존 main.py 호환용: 루트의 고객DB.xlsx로 임시 저장
            with open(ROOT_DIR / "고객DB.xlsx", "wb") as f:
                f.write(uploaded_file.getbuffer())

            is_valid, validation_errors, validation_warnings = validate_customer_workbook("고객DB.xlsx")

            if validation_errors:
                st.error("업로드한 고객DB 양식에 문제가 있습니다.")
                for err in validation_errors:
                    st.write(f"- {err}")
                st.info("파일명은 바꿔도 되지만, 시트명과 컬럼명은 변경하면 안 됩니다.")
                st.stop()

            if validation_warnings:
                with st.expander("양식 확인 경고"):
                    for warn in validation_warnings:
                        st.write(f"- {warn}")

            cumulative_path, saved_rows = append_user_customer_db(
                uploaded_save_path,
                CURRENT_USER_ID,
                manager_name=manager_name.strip() or CURRENT_USER_NAME
            )
            cumulative_path, _, _ = ensure_user_cumulative_db_format(CURRENT_USER_ID)

            st.session_state.latest_upload_file = str(uploaded_save_path)
            if saved_rows:
                st.success(f"업로드 완료: {uploaded_save_name} / 누적DB {saved_rows}건 저장")
            else:
                st.success(f"업로드 완료: {uploaded_save_name}")

            if st.button("정책자금 매칭 실행", width='stretch'):
                with st.spinner("정책자금과 고용지원금을 분석 중입니다..."):
                    before_files = set(glob.glob("매칭결과_*.xlsx"))

                    result = subprocess.run(
                        [sys.executable, str(ROOT_DIR / "main.py")],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="ignore"
                    )

                    moved_files = move_result_files_to_results(before_files)

                    if moved_files:
                        latest_file = max(moved_files, key=os.path.getmtime)
                        user_result_path = USER_RESULT_DIR / os.path.basename(latest_file)
                        try:
                            shutil.copy2(latest_file, user_result_path)
                            latest_file = str(user_result_path)
                        except Exception:
                            pass
                        st.session_state.latest_result_file = latest_file

                        st.success("매칭이 완료되었습니다.")

                        append_run_history(
                            upload_file_name=uploaded_save_name,
                            result_file=latest_file,
                            status="성공",
                            memo="정상 실행",
                            manager_name=manager_name,
                            user_id=CURRENT_USER_ID
                        )

                        run_cleanup()

                    else:
                        append_run_history(
                            uploaded_save_name, "", "실패", "결과파일 없음",
                            manager_name=manager_name,
                            user_id=CURRENT_USER_ID
                        )
                        run_cleanup()
                        st.error("결과 파일을 찾지 못했습니다. 실행 로그를 확인해주세요.")

                    with st.expander("실행 로그 보기"):
                        if result.stdout:
                            st.code(result.stdout)
                        if result.stderr:
                            st.code(result.stderr)

        except PermissionError:
            append_run_history(
                uploaded_save_name, "", "실패", "고객DB 파일 열림",
                manager_name=manager_name if "manager_name" in locals() else "",
                user_id=CURRENT_USER_ID
            )
            st.error("고객DB.xlsx 파일이 열려 있어 업로드할 수 없습니다. 엑셀 파일을 닫고 다시 시도해주세요.")

    if st.session_state.latest_result_file and os.path.exists(st.session_state.latest_result_file):
        show_company_preview(st.session_state.latest_result_file)

        with open(st.session_state.latest_result_file, "rb") as f:
            st.download_button(
                label="결과 엑셀 다운로드",
                data=f,
                file_name=os.path.basename(st.session_state.latest_result_file),
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width='stretch'
            )



elif active_tab == "주가평가":
    render_stock_valuation_page(
        CURRENT_USER_ID,
        CURRENT_USER_NAME,
    )

elif active_tab == "크레탑 자동등록":
    st.markdown("### 📄 크레탑 PDF로 고객 자동등록")
    st.caption("크레탑 기업종합보고서 PDF를 업로드하면 추출 가능한 항목을 읽어 내 누적 고객DB에 1행으로 추가합니다.")

    # v2.3.4: 저장 직후에는 앱을 한 번 재실행하여 상단 다운로드 버튼까지 최신 파일을 읽도록 한다.
    # 재실행 후에도 사용자가 저장 결과를 확인할 수 있게 session_state 메시지를 표시한다.
    if st.session_state.get("cretop_last_message"):
        last_saved_count = int(st.session_state.get("cretop_last_saved_count", 0) or 0)
        last_total_count = int(st.session_state.get("cretop_last_total_count", 0) or 0)
        last_message = st.session_state.get("cretop_last_message", "")
        if last_saved_count > 0:
            st.success(f"{last_message} 현재 누적 고객 수: {last_total_count}건")
        else:
            st.info(last_message or "저장된 신규 데이터가 없습니다.")

        # v2.3.6: 화면 진입만으로 엑셀 파일을 읽지 않고, 사용자가 다운로드를 요청할 때만 읽는다.
        last_cumulative_path = get_user_cumulative_db_path(CURRENT_USER_ID)
        if last_cumulative_path.exists():
            if st.button("최신 누적 고객DB 다운로드 준비", key="cretop_prepare_latest_download", width='stretch'):
                with open(last_cumulative_path, "rb") as f:
                    st.download_button(
                        label="최신 내 누적 고객DB 다운로드",
                        data=f,
                        file_name="고객DB누적.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        width='stretch',
                        key="cretop_latest_download_after_save"
                    )

    cretop_col1, cretop_col2 = st.columns([1.1, 1])

    with cretop_col1:
        st.markdown("#### 1. PDF 업로드")
        cretop_manager_name = st.text_input(
            "담당자명",
            value=CURRENT_USER_NAME or "",
            key="cretop_manager_name",
            placeholder="예: 임주형"
        )
        cretop_pdf = st.file_uploader(
            "크레탑 기업종합보고서 PDF 업로드",
            type=["pdf"],
            key="cretop_pdf_uploader"
        )

    with cretop_col2:
        st.markdown("#### 자동 입력되는 주요 항목")
        st.write("업체명, 대표자명, 사업자등록번호, 법인번호, 주소, 업종, 종업원수, 설립일, 매출액, 영업이익, 당기순이익, 자산/부채/자본, 벤처·이노비즈·메인비즈·연구소·특허·상표 여부")
        st.info("자동 추출이 어려운 상담메모, 희망자금, 고용계획 등은 공란으로 유지합니다.")

    if cretop_pdf is not None:
        if not cretop_manager_name.strip():
            st.warning("담당자명을 입력해주세요.")
            st.stop()

        st.caption("PDF 업로드가 완료되었습니다. 아래 버튼을 눌러 분석을 시작하세요.")
        analyze_requested = st.button(
            "크레탑 PDF 분석하기",
            key="cretop_analyze_button",
            width='stretch',
        )

        if analyze_requested:
            import hashlib

            pdf_save_name = make_upload_filename(cretop_pdf.name).replace(
                "업로드고객DB_",
                "크레탑PDF_",
            )
            pdf_save_path = USER_UPLOAD_DIR / pdf_save_name

            # UploadedFile을 메모리에 복제하지 않고 디스크로 바로 저장한다.
            cretop_pdf.seek(0)
            with open(pdf_save_path, "wb") as destination:
                shutil.copyfileobj(cretop_pdf, destination, length=1024 * 1024)

            hash_object = hashlib.md5()
            with open(pdf_save_path, "rb") as source_file:
                for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
                    hash_object.update(chunk)
            pdf_hash = hash_object.hexdigest()

            status_box = st.status("크레탑 PDF 분석을 준비하고 있습니다.", expanded=True)

            # 1단계: 별도 프로세스에서 사업자번호를 동적으로 탐색
            status_box.write("1단계: 문서 전체에서 사업자등록번호를 탐색합니다.")
            identity_data, identity_error, identity_logs = run_cretop_worker(
                pdf_save_path,
                mode="identity",
                timeout=60,
            )

            if identity_logs:
                last_log = identity_logs[-1]
                status_box.write(
                    f"사업자정보 탐색: {last_log.get('page', '')}/"
                    f"{last_log.get('total', '')} 페이지"
                )

            business_no = identity_data.get("사업자등록번호", "")
            company_name = identity_data.get("업체명", "")
            representative_name = identity_data.get("대표자명", "")
            is_dup = (
                not identity_error
                and check_user_customer_duplicate(
                    CURRENT_USER_ID,
                    business_no,
                    company_name=company_name,
                    representative_name=representative_name,
                )
            )

            if is_dup:
                link_business_no_to_legacy_customer(
                    CURRENT_USER_ID,
                    business_no,
                    company_name=company_name,
                    representative_name=representative_name,
                )

            # 신규·중복 여부와 관계없이 전체 보고서를 분석한다.
            # 중복 고객이면 새 행을 만들지 않고 기존 행의 누락정보를 갱신한다.
            status_box.write(
                "2단계: 문서 전체에서 소재지·설립일·재무정보를 탐색합니다."
            )
            extracted_data, extract_error, analysis_logs = run_cretop_worker(
                pdf_save_path,
                mode="full",
                timeout=180,
            )

            duplicate_refresh_message = ""
            duplicate_refresh_count = 0

            if extract_error:
                status_box.update(
                    label="PDF 분석 작업이 안전하게 중단되었습니다.",
                    state="error",
                    expanded=True,
                )
            elif is_dup:
                (
                    refresh_ok,
                    duplicate_refresh_message,
                    duplicate_refresh_count,
                ) = refresh_existing_customer_from_cretop(
                    CURRENT_USER_ID,
                    extracted_data,
                )
                if refresh_ok:
                    status_box.update(
                        label="기존 고객의 누락정보를 갱신했습니다.",
                        state="complete",
                        expanded=False,
                    )
                else:
                    status_box.update(
                        label="기존 고객은 확인했지만 정보 갱신에 실패했습니다.",
                        state="error",
                        expanded=True,
                    )
            else:
                status_box.update(
                    label="크레탑 PDF 분석이 완료되었습니다.",
                    state="complete",
                    expanded=False,
                )

            # 작은 JSON 결과만 세션에 보관
            if not extract_error and extracted_data:
                extracted_data = enrich_address_fields(extracted_data)
                save_cretop_financial_snapshot(
                    CURRENT_USER_ID,
                    extracted_data,
                )
                save_customer_snapshot(
                    CURRENT_USER_ID,
                    extracted_data,
                    source="cretop",
                )

            st.session_state["cretop_pdf_hash"] = pdf_hash
            st.session_state["cretop_pdf_save_path"] = str(pdf_save_path)
            st.session_state["cretop_extracted_data"] = dict(extracted_data or {})
            st.session_state["cretop_extract_error"] = extract_error or identity_error
            st.session_state["cretop_is_duplicate"] = bool(is_dup)
            st.session_state["cretop_analysis_logs"] = analysis_logs[-20:]
            st.session_state["cretop_duplicate_refresh_message"] = (
                duplicate_refresh_message if is_dup else ""
            )
            st.session_state["cretop_duplicate_refresh_count"] = (
                duplicate_refresh_count if is_dup else 0
            )

        pdf_save_path = Path(st.session_state.get("cretop_pdf_save_path", ""))
        extracted_data = dict(st.session_state.get("cretop_extracted_data", {}) or {})
        extract_error = st.session_state.get("cretop_extract_error", "")
        is_dup = bool(st.session_state.get("cretop_is_duplicate", False))
        analysis_logs = st.session_state.get("cretop_analysis_logs", [])
        duplicate_refresh_message = st.session_state.get(
            "cretop_duplicate_refresh_message",
            "",
        )
        duplicate_refresh_count = st.session_state.get(
            "cretop_duplicate_refresh_count",
            0,
        )

        if extract_error:
            st.error(extract_error)
            with st.expander("PDF 분석 로그"):
                if analysis_logs:
                    st.json(analysis_logs)
                else:
                    st.caption("수집된 분석 로그가 없습니다.")

        elif is_dup:
            st.markdown("#### 기존 고객정보 갱신 완료")
            st.warning(
                f"{extracted_data.get('업체명', '해당 업체')} "
                f"({extracted_data.get('사업자등록번호', '')})는 이미 등록된 고객입니다."
            )
            if duplicate_refresh_message:
                st.success(duplicate_refresh_message)
            st.info(
                "새 고객 행은 추가하지 않고 기존 고객의 사업장 소재지, 설립일, "
                "당기순이익 등 누락·재무정보만 최신 크레탑 값으로 갱신했습니다."
            )

            preview_keys = [
                "업체명", "대표자명", "사업자등록번호",
                "사업장 소재지", "설립일", "종업원수",
                "매출액", "영업이익", "당기순이익",
                "자산총계", "부채총계", "자본총계",
            ]
            numeric_keys = {
                "매출액", "영업이익", "당기순이익",
                "자산총계", "부채총계", "자본총계",
            }
            duplicate_preview = pd.DataFrame([
                {
                    "항목": key,
                    "갱신값": (
                        f"{int(extracted_data.get(key)):,}"
                        if key in numeric_keys
                        and isinstance(extracted_data.get(key), (int, float))
                        else extracted_data.get(key, "")
                    ),
                }
                for key in preview_keys
            ])
            st.dataframe(
                duplicate_preview,
                hide_index=True,
                width='stretch',
            )

        elif extracted_data:
            st.markdown("#### 2. 추출값 미리보기")
            preview_keys = [
                "업체명", "대표자명", "사업자등록번호", "법인등록번호",
                "업종명", "사업장 소재지", "종업원수", "설립일",
                "기업유형", "기업규모", "매출액", "영업이익",
                "당기순이익", "자산총계", "부채총계", "자본총계",
                "벤처", "이노비즈", "메인비즈", "연구개발전담부서",
                "기업부설연구소", "특허보유", "상표",
            ]
            numeric_preview_keys = {
                "매출액", "영업이익", "당기순이익",
                "자산총계", "부채총계", "자본총계",
            }
            preview_df = pd.DataFrame([
                {
                    "항목": key,
                    "추출값": (
                        f"{int(extracted_data.get(key)):,}"
                        if key in numeric_preview_keys
                        and isinstance(extracted_data.get(key), (int, float))
                        else extracted_data.get(key, "")
                    ),
                }
                for key in preview_keys
            ])
            st.dataframe(preview_df, width='stretch', hide_index=True)

            with st.expander("분석 진행 로그"):
                if analysis_logs:
                    st.dataframe(pd.DataFrame(analysis_logs), hide_index=True, width='stretch')
                else:
                    st.caption("분석 로그가 없습니다.")

            st.markdown("#### 3. 정책자금 매칭설정")
            st.caption(
                "이 설정은 별도 파일에 저장되며 기존 고객DB 양식은 변경하지 않습니다."
            )

            cretop_business_no = extracted_data.get(
                "사업자등록번호",
                "",
            )
            cretop_saved_preferences = get_matching_preferences(
                CURRENT_USER_ID,
                cretop_business_no,
            )

            cretop_matching_keywords = st.text_area(
                "매칭키워드",
                value=", ".join(
                    cretop_saved_preferences.get("매칭키워드", []) or []
                ),
                placeholder="예: 시설투자, 차량구매, 운전자금, 신규채용",
                key="cretop_matching_keywords",
                height=80,
            )

            cretop_interest_fields = st.multiselect(
                "관심지원분야",
                INTEREST_OPTIONS,
                default=[
                    item
                    for item in (
                        cretop_saved_preferences.get(
                            "관심지원분야",
                            [],
                        )
                        or []
                    )
                    if item in INTEREST_OPTIONS
                ],
                key="cretop_interest_fields",
            )

            cretop_exclusion_keywords = st.text_area(
                "제외키워드",
                value=", ".join(
                    cretop_saved_preferences.get("제외키워드", []) or []
                ),
                placeholder="예: 온라인마케팅, 수출, 스마트공장",
                key="cretop_exclusion_keywords",
                height=70,
            )

            cp1, cp2, cp3 = st.columns(3)
            with cp1:
                cretop_fund_purpose = st.text_input(
                    "자금사용목적",
                    value=str(
                        cretop_saved_preferences.get(
                            "자금사용목적",
                            "",
                        )
                        or ""
                    ),
                    placeholder="예: 운전자금",
                    key="cretop_fund_purpose",
                )
            with cp2:
                cretop_planned_amount = st.text_input(
                    "투자예정금액",
                    value=str(
                        cretop_saved_preferences.get(
                            "투자예정금액",
                            "",
                        )
                        or ""
                    ),
                    placeholder="예: 3억원",
                    key="cretop_planned_amount",
                )
            with cp3:
                cretop_planned_timing = st.text_input(
                    "투자예정시기",
                    value=str(
                        cretop_saved_preferences.get(
                            "투자예정시기",
                            "",
                        )
                        or ""
                    ),
                    placeholder="예: 6개월 이내",
                    key="cretop_planned_timing",
                )

            add_customer_clicked = st.button(
                "내 누적 고객DB에 추가",
                width='stretch',
                key="cretop_add_to_cumulative_db",
            )

            if add_customer_clicked:
                with st.spinner("누적 고객DB에 저장 중입니다..."):
                    cumulative_path, saved_count, message, _, saved_preview = (
                        append_cretop_to_user_customer_db(
                            pdf_save_path,
                            CURRENT_USER_ID,
                            manager_name=(
                                cretop_manager_name.strip()
                                or CURRENT_USER_NAME
                            ),
                            duplicate_action="skip",
                            extracted_data=extracted_data,
                        )
                    )

                if saved_count > 0:
                    try:
                        save_matching_preferences(
                            CURRENT_USER_ID,
                            cretop_business_no,
                            company_name=str(
                                extracted_data.get("업체명", "") or ""
                            ),
                            matching_keywords=cretop_matching_keywords,
                            interest_fields=cretop_interest_fields,
                            exclusion_keywords=cretop_exclusion_keywords,
                            fund_purpose=cretop_fund_purpose,
                            planned_amount=cretop_planned_amount,
                            planned_timing=cretop_planned_timing,
                        )
                    except Exception as preference_error:
                        st.warning(
                            "고객은 등록됐지만 매칭설정 저장에 실패했습니다: "
                            f"{preference_error}"
                        )

                    sync_customer_snapshot(
                        CURRENT_USER_ID,
                        extracted_data,
                        source="cretop_registration",
                        manager_name=CURRENT_USER_NAME,
                    )

                    total_count = count_user_cumulative_rows(CURRENT_USER_ID)
                    st.success(f"{message} 현재 누적 고객 수: {total_count}건")
                    st.session_state["cretop_last_message"] = message
                    st.session_state["cretop_last_saved_count"] = saved_count
                    st.session_state["cretop_last_total_count"] = total_count
                else:
                    st.info(message)

elif active_tab == "실행이력":
    st.markdown("### 📚 실행이력")

    history_df = read_run_history(CURRENT_USER_ID)

    if history_df.empty:
        st.info("아직 실행이력이 없습니다.")
    else:
        st.dataframe(history_df, width='stretch', hide_index=True)

        latest_result = history_df.iloc[0].get("결과파일", "")

        if isinstance(latest_result, str) and latest_result and os.path.exists(latest_result):
            st.markdown("#### 최근 결과파일 다운로드")
            with open(latest_result, "rb") as f:
                st.download_button(
                    label="최근 결과 다운로드",
                    data=f,
                    file_name=os.path.basename(latest_result),
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width='stretch'
                )

elif active_tab == "담당자 통계":
    st.markdown("### 👤 담당자별 실행횟수")

    stats_df = get_manager_stats(CURRENT_USER_ID)

    if stats_df.empty:
        st.info("담당자별 통계가 아직 없습니다.")
    else:
        st.dataframe(stats_df, width='stretch', hide_index=True)


elif CURRENT_USER_IS_ADMIN and active_tab == "회원 승인 관리":
    st.markdown("### 🔐 회원 승인 관리")
    st.caption("회원가입 신청자는 관리자 승인 전까지 매칭 시스템에 로그인할 수 없습니다.")

    pending_users = list_pending_users()

    st.markdown("#### 승인 대기 회원")
    if not pending_users:
        st.info("승인 대기 중인 회원이 없습니다.")
    else:
        for row in pending_users:
            with st.container(border=True):
                c1, c2, c3 = st.columns([2.2, 1, 1])
                with c1:
                    st.write(f"**{row.get('이름', '')}**")
                    st.caption(f"아이디: {row.get('아이디', '')} / 가입일시: {row.get('가입일시', '')}")
                with c2:
                    if st.button("승인", key=f"approve_{row.get('아이디', '')}", use_container_width=True):
                        ok, msg = approve_user(row.get('아이디', ''), CURRENT_USER_ID)
                        if ok:
                            st.success(msg)
                            st.rerun()
                        else:
                            st.error(msg)
                with c3:
                    if st.button("거절", key=f"reject_{row.get('아이디', '')}", use_container_width=True):
                        ok, msg = reject_user(row.get('아이디', ''), CURRENT_USER_ID)
                        if ok:
                            st.warning(msg)
                            st.rerun()
                        else:
                            st.error(msg)

    st.markdown("#### 전체 회원 현황")
    all_users = list_all_users_for_admin()
    if all_users:
        st.dataframe(pd.DataFrame(all_users), width='stretch', hide_index=True)
    else:
        st.info("등록된 회원이 없습니다.")

elif CURRENT_USER_IS_ADMIN and active_tab == "시스템 관리":
    render_system_management_page(
        project_root=ROOT_DIR,
        current_user_id=CURRENT_USER_ID,
    )

elif CURRENT_USER_IS_ADMIN and active_tab == "AI 사용량":
    render_ai_usage_page(
        CURRENT_USER_ID,
        CURRENT_USER_NAME,
    )

elif CURRENT_USER_IS_ADMIN and active_tab == "클라우드 DB 관리":
    render_cloud_database_page(
        CURRENT_USER_ID,
        CURRENT_USER_NAME,
    )

    sync_status = get_cloud_sync_status(CURRENT_USER_ID)
    st.markdown("### 실시간 이중저장 상태")
    s1, s2 = st.columns(2)
    s1.metric(
        "Supabase 설정",
        "연결됨" if sync_status["configured"] else "미설정",
    )
    s2.metric("동기화 대기", f"{sync_status['queued']}건")

    if st.button(
        "동기화 대기자료 다시 전송",
        width='stretch',
        key="retry_cloud_sync_queue_button",
    ):
        result = retry_cloud_sync_queue(CURRENT_USER_ID)
        if result["failed"] == 0:
            st.success(
                f"대기자료 {result['success']}건을 모두 전송했습니다."
            )
        else:
            st.warning(
                f"{result['success']}건 성공, "
                f"{result['failed']}건은 대기 중입니다."
            )

st.markdown("""
<div class="oasis-footer">
    © OASIS TAX & ACCOUNTING. All rights reserved.
</div>
""", unsafe_allow_html=True)
