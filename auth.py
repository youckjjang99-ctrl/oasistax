import os
import json
import hashlib
import secrets
import re
from datetime import datetime
from pathlib import Path

import streamlit as st


ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "data"
USERS_FILE = DATA_DIR / "users.json"


# ================================
# 기본 Secret / 환경변수 처리
# ================================
def get_secret(key, default=""):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return os.getenv(key, default)


def apply_env_secrets():
    bizinfo_api_key = get_secret("BIZINFO_API_KEY", "")
    if bizinfo_api_key:
        os.environ["BIZINFO_API_KEY"] = bizinfo_api_key


# ================================
# v2.1 회원 계정 유틸
# ================================
def _ensure_data_dir():
    DATA_DIR.mkdir(exist_ok=True)


def _hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt.encode("utf-8"),
        120000,
    ).hex()
    return salt, digest


def _verify_password(password, salt, password_hash):
    _, check_hash = _hash_password(password, salt)
    return secrets.compare_digest(check_hash, password_hash)


def _safe_user_id(user_id):
    user_id = str(user_id).strip().lower()
    user_id = re.sub(r"[^a-z0-9가-힣_.@-]", "", user_id)
    return user_id[:50]


def load_users():
    _ensure_data_dir()
    if USERS_FILE.exists():
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                users = json.load(f)
            if isinstance(users, dict):
                return users
        except Exception:
            pass
    return {}


def save_users(users):
    _ensure_data_dir()
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def ensure_default_admin():
    """기존 v2 로그인 방식과의 호환을 위해 기본 관리자 계정을 자동 생성한다."""
    users = load_users()
    login_id = _safe_user_id(get_secret("APP_LOGIN_ID", "oasistax"))
    login_pw = get_secret("APP_LOGIN_PW", "1234")

    changed = False

    if login_id and login_id not in users:
        salt, password_hash = _hash_password(login_pw)
        users[login_id] = {
            "user_id": login_id,
            "name": "OASIS 관리자",
            "salt": salt,
            "password_hash": password_hash,
            "role": "admin",
            "status": "approved",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "approved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "approved_by": "system",
        }
        changed = True

    # v2.2 승인제 도입 전 생성된 기존 계정은 사용 중단을 막기 위해 승인 상태로 보정한다.
    for uid, user in list(users.items()):
        if not isinstance(user, dict):
            continue
        if user.get("role") == "admin":
            if user.get("status") != "approved":
                user["status"] = "approved"
                user.setdefault("approved_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                user.setdefault("approved_by", "system")
                changed = True
        elif "status" not in user:
            user["status"] = "approved"
            user.setdefault("approved_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            user.setdefault("approved_by", "migration")
            changed = True

    if changed:
        save_users(users)


def create_user(user_id, password, name):
    user_id = _safe_user_id(user_id)
    name = str(name).strip()

    if not user_id:
        return False, "아이디를 입력해주세요."
    if len(str(password)) < 4:
        return False, "비밀번호는 4자리 이상으로 입력해주세요."
    if not name:
        return False, "이름을 입력해주세요."

    users = load_users()
    if user_id in users:
        return False, "이미 사용 중인 아이디입니다."

    salt, password_hash = _hash_password(password)
    users[user_id] = {
        "user_id": user_id,
        "name": name,
        "salt": salt,
        "password_hash": password_hash,
        "role": "member",
        "status": "pending",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "approved_at": "",
        "approved_by": "",
    }
    save_users(users)
    return True, "회원가입 신청이 완료되었습니다. 관리자 승인 후 로그인할 수 있습니다."


def authenticate_user(user_id, password):
    ok, user, _msg = authenticate_user_with_message(user_id, password)
    return user if ok else None


def authenticate_user_with_message(user_id, password):
    ensure_default_admin()
    user_id = _safe_user_id(user_id)
    users = load_users()
    user = users.get(user_id)
    if not user:
        return False, None, "아이디 또는 비밀번호가 올바르지 않습니다."
    if not _verify_password(password, user.get("salt", ""), user.get("password_hash", "")):
        return False, None, "아이디 또는 비밀번호가 올바르지 않습니다."

    status = user.get("status", "approved")
    if status == "pending":
        return False, None, "관리자 승인 대기 중입니다. 승인 후 로그인할 수 있습니다."
    if status == "rejected":
        return False, None, "회원가입이 승인되지 않은 계정입니다. 관리자에게 확인해주세요."
    if status != "approved":
        return False, None, "계정 상태를 확인할 수 없습니다. 관리자에게 확인해주세요."

    return True, user, "로그인 성공"


def is_admin(user_id=None):
    ensure_default_admin()
    user_id = _safe_user_id(user_id or st.session_state.get("current_user_id", ""))
    user = load_users().get(user_id, {})
    return user.get("role") == "admin" and user.get("status", "approved") == "approved"


def list_pending_users():
    ensure_default_admin()
    users = load_users()
    rows = []
    for uid, user in users.items():
        if isinstance(user, dict) and user.get("status") == "pending":
            rows.append({
                "아이디": uid,
                "이름": user.get("name", ""),
                "권한": user.get("role", "member"),
                "가입일시": user.get("created_at", ""),
            })
    return rows


def list_all_users_for_admin():
    ensure_default_admin()
    users = load_users()
    rows = []
    for uid, user in users.items():
        if isinstance(user, dict):
            rows.append({
                "아이디": uid,
                "이름": user.get("name", ""),
                "권한": user.get("role", "member"),
                "상태": user.get("status", "approved"),
                "가입일시": user.get("created_at", ""),
                "승인일시": user.get("approved_at", ""),
                "승인자": user.get("approved_by", ""),
            })
    return rows


def approve_user(user_id, approved_by=""):
    ensure_default_admin()
    user_id = _safe_user_id(user_id)
    users = load_users()
    if user_id not in users:
        return False, "대상 회원을 찾을 수 없습니다."
    users[user_id]["status"] = "approved"
    users[user_id]["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    users[user_id]["approved_by"] = approved_by or "admin"
    save_users(users)
    return True, f"{user_id} 회원을 승인했습니다."


def reject_user(user_id, approved_by=""):
    ensure_default_admin()
    user_id = _safe_user_id(user_id)
    users = load_users()
    if user_id not in users:
        return False, "대상 회원을 찾을 수 없습니다."
    if users[user_id].get("role") == "admin":
        return False, "관리자 계정은 거절 처리할 수 없습니다."
    users[user_id]["status"] = "rejected"
    users[user_id]["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    users[user_id]["approved_by"] = approved_by or "admin"
    save_users(users)
    return True, f"{user_id} 회원을 거절 처리했습니다."


def check_login():
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False

    if "current_user_id" not in st.session_state:
        st.session_state.current_user_id = ""

    if "current_user_name" not in st.session_state:
        st.session_state.current_user_name = ""

    if "current_user_role" not in st.session_state:
        st.session_state.current_user_role = ""

    if "latest_result_file" not in st.session_state:
        st.session_state.latest_result_file = None

    if "latest_upload_file" not in st.session_state:
        st.session_state.latest_upload_file = None

    ensure_default_admin()
    return st.session_state.logged_in


def login_form(logo_html_func):
    st.markdown("<div class='login-wrap'>", unsafe_allow_html=True)
    st.markdown(f"<div class='login-logo'>{logo_html_func(260)}</div>", unsafe_allow_html=True)

    st.markdown("""
    <div class="login-panel">
        <div class="login-title">OASIS 내부 지원사업 매칭</div>
        <div class="login-desc">
            오아시스 관계자 전용 정책자금 · 고용지원금 매칭 시스템입니다.<br>
            회원별로 고객DB와 실행이력을 분리 저장합니다.
        </div>
    </div>
    """, unsafe_allow_html=True)

    login_tab, signup_tab = st.tabs(["로그인", "회원가입"])

    with login_tab:
        user_id = st.text_input("아이디", placeholder="아이디를 입력하세요", key="login_user_id")
        user_pw = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요", key="login_user_pw")

        if st.button("로그인", use_container_width=True):
            ok, user, msg = authenticate_user_with_message(user_id, user_pw)
            if ok and user:
                st.session_state.logged_in = True
                st.session_state.current_user_id = user.get("user_id", "")
                st.session_state.current_user_name = user.get("name", "")
                st.session_state.current_user_role = user.get("role", "member")
                st.session_state.latest_result_file = None
                st.session_state.latest_upload_file = None
                st.rerun()
            else:
                st.error(msg)

    with signup_tab:
        new_name = st.text_input("이름", placeholder="예: 임주형", key="signup_name")
        new_id = st.text_input("아이디", placeholder="영문/숫자/한글 사용 가능", key="signup_user_id")
        new_pw = st.text_input("비밀번호", type="password", placeholder="4자리 이상", key="signup_user_pw")
        new_pw2 = st.text_input("비밀번호 확인", type="password", placeholder="비밀번호 재입력", key="signup_user_pw2")

        if st.button("회원가입", use_container_width=True):
            if new_pw != new_pw2:
                st.error("비밀번호 확인이 일치하지 않습니다.")
            else:
                ok, msg = create_user(new_id, new_pw, new_name)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()


def logout_button():
    if st.button("로그아웃", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.current_user_id = ""
        st.session_state.current_user_name = ""
        st.session_state.current_user_role = ""
        st.session_state.latest_result_file = None
        st.session_state.latest_upload_file = None
        st.rerun()
