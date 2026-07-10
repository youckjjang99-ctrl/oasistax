import streamlit as st
import subprocess
import sys
import os
import glob
import shutil
import pandas as pd
from pathlib import Path

from auth import (
    apply_env_secrets, check_login, login_form, logout_button,
    is_admin, list_pending_users, list_all_users_for_admin,
    approve_user, reject_user
)
from history import append_run_history, read_run_history, get_manager_stats
from utils import (
    ROOT_DIR, TEMPLATE_DIR, UPLOAD_DIR, RESULT_DIR,
    logo_html, make_upload_filename, find_customer_template,
    make_basic_customer_template_bytes, run_cleanup,
    move_result_files_to_results, extract_company_previews,
    get_user_dirs, get_user_cumulative_db_path, append_user_customer_db
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
    page_title="OASIS 내부 지원사업 매칭",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed"
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
</style>
""", unsafe_allow_html=True)


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


if not check_login():
    login_form(logo_html)

CURRENT_USER_ID = st.session_state.get("current_user_id", "")
CURRENT_USER_NAME = st.session_state.get("current_user_name", "")
CURRENT_USER_IS_ADMIN = is_admin(CURRENT_USER_ID)
USER_DIRS = get_user_dirs(CURRENT_USER_ID)
USER_UPLOAD_DIR = USER_DIRS["uploads"]
USER_RESULT_DIR = USER_DIRS["results"]

logo_col, logout_col = st.columns([5, 1])
with logo_col:
    st.markdown(f"""
    <div class="oasis-topbar">
        {logo_html(245)}
    </div>
    """, unsafe_allow_html=True)
with logout_col:
    if CURRENT_USER_NAME:
        role_badge = "관리자" if CURRENT_USER_IS_ADMIN else "회원"
        st.caption(f"{CURRENT_USER_NAME}님 · {role_badge}")
    logout_button()

st.markdown("""
<div class="hero">
    <div class="badge">OASIS TAX & ACCOUNTING</div>
    <div class="hero-title">오아시스 내부 전용<br>지원사업 매칭 시스템</div>
    <div class="hero-sub">
        오아시스 내부 담당자가 고객DB를 업로드하면 업체별 정책자금, 공고형 지원사업,<br>
        고용지원금 후보를 자동 정리하고 상담용 결과파일을 생성합니다.
    </div>
</div>
""", unsafe_allow_html=True)

c1, c2, c3 = st.columns(3)

with c1:
    st.markdown("""
    <div class="point-card">
        <div class="point-icon">📌</div>
        <div class="point-title">업체별 리포트</div>
        <div class="point-desc">업체별 TOP 추천사업과 상담 포인트를 한 시트에 정리합니다.</div>
    </div>
    """, unsafe_allow_html=True)

with c2:
    st.markdown("""
    <div class="point-card">
        <div class="point-icon">📚</div>
        <div class="point-title">실행이력 관리</div>
        <div class="point-desc">담당자명, 업로드 파일, 결과파일 이력을 자동 저장합니다.</div>
    </div>
    """, unsafe_allow_html=True)

with c3:
    st.markdown("""
    <div class="point-card">
        <div class="point-icon">👀</div>
        <div class="point-title">업체 선택 미리보기</div>
        <div class="point-desc">웹에서 업체별 TOP3를 바로 확인할 수 있습니다.</div>
    </div>
    """, unsafe_allow_html=True)

st.write("")
st.write("")

tab_labels = ["매칭 실행", "실행이력", "담당자 통계"]
if CURRENT_USER_IS_ADMIN:
    tab_labels.append("회원 승인 관리")

tabs = st.tabs(tab_labels)
tab1, tab2, tab3 = tabs[0], tabs[1], tabs[2]
tab4 = tabs[3] if CURRENT_USER_IS_ADMIN else None

with tab1:
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

with tab2:
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

with tab3:
    st.markdown("### 👤 담당자별 실행횟수")

    stats_df = get_manager_stats(CURRENT_USER_ID)

    if stats_df.empty:
        st.info("담당자별 통계가 아직 없습니다.")
    else:
        st.dataframe(stats_df, width='stretch', hide_index=True)


if CURRENT_USER_IS_ADMIN and tab4 is not None:
    with tab4:
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

st.markdown("""
<div class="oasis-footer">
    © OASIS TAX & ACCOUNTING. All rights reserved.
</div>
""", unsafe_allow_html=True)
