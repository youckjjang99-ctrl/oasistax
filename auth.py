import os
import json
import hashlib
import secrets
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import streamlit as st

from cloud_db import CloudDatabase, cloud_is_configured


ROOT_DIR = Path(__file__).parent
DATA_DIR = ROOT_DIR / "data"
USERS_FILE = DATA_DIR / "users.json"
MIN_PASSWORD_LENGTH = 12
MAX_LOGIN_FAILURES = 5
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_LOCK_SECONDS = 15 * 60
_LOGIN_ATTEMPTS: dict[str, dict[str, float | int]] = {}
_LOGIN_ATTEMPTS_LOCK = threading.Lock()


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


def _password_policy_error(password: str) -> str:
    value = str(password or "")
    if len(value) < MIN_PASSWORD_LENGTH:
        return f"비밀번호는 {MIN_PASSWORD_LENGTH}자리 이상으로 입력해주세요."
    if not re.search(r"[A-Za-z가-힣]", value) or not re.search(r"[0-9]", value):
        return "비밀번호에는 문자와 숫자를 각각 하나 이상 포함해주세요."
    return ""


def _login_lock_remaining(user_id: str) -> int:
    safe_id = _safe_user_id(user_id)
    if not safe_id:
        return 0
    now = time.monotonic()
    with _LOGIN_ATTEMPTS_LOCK:
        record = _LOGIN_ATTEMPTS.get(safe_id)
        if not record:
            return 0
        locked_until = float(record.get("locked_until") or 0)
        if locked_until <= now:
            if now - float(record.get("first_failure") or now) > (
                LOGIN_FAILURE_WINDOW_SECONDS
            ):
                _LOGIN_ATTEMPTS.pop(safe_id, None)
            return 0
        return max(1, int(locked_until - now))


def _record_login_failure(user_id: str) -> None:
    safe_id = _safe_user_id(user_id)
    if not safe_id:
        return
    now = time.monotonic()
    with _LOGIN_ATTEMPTS_LOCK:
        record = _LOGIN_ATTEMPTS.get(safe_id) or {
            "count": 0,
            "first_failure": now,
            "locked_until": 0,
        }
        if now - float(record.get("first_failure") or now) > (
            LOGIN_FAILURE_WINDOW_SECONDS
        ):
            record = {
                "count": 0,
                "first_failure": now,
                "locked_until": 0,
            }
        record["count"] = int(record.get("count") or 0) + 1
        if int(record["count"]) >= MAX_LOGIN_FAILURES:
            record["locked_until"] = now + LOGIN_LOCK_SECONDS
        _LOGIN_ATTEMPTS[safe_id] = record


def _clear_login_failures(user_id: str) -> None:
    safe_id = _safe_user_id(user_id)
    if not safe_id:
        return
    with _LOGIN_ATTEMPTS_LOCK:
        _LOGIN_ATTEMPTS.pop(safe_id, None)


def _safe_user_id(user_id):
    user_id = str(user_id).strip().lower()
    user_id = re.sub(r"[^a-z0-9가-힣_.@-]", "", user_id)
    return user_id[:50]


def _load_local_users():
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


def _save_local_users(users):
    _ensure_data_dir()
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _user_to_cloud_row(user_id, user):
    return {
        "user_id": _safe_user_id(user_id),
        "name": str(user.get("name", "")),
        "salt": str(user.get("salt", "")),
        "password_hash": str(user.get("password_hash", "")),
        "role": str(user.get("role", "member")),
        "status": str(user.get("status", "approved")),
        "created_at": str(user.get("created_at", "")),
        "approved_at": str(user.get("approved_at", "")),
        "approved_by": str(user.get("approved_by", "")),
        "password_changed_at": str(user.get("password_changed_at", "")),
    }


def _cloud_row_to_user(row):
    return {
        "user_id": _safe_user_id(row.get("user_id", "")),
        "name": str(row.get("name", "")),
        "salt": str(row.get("salt", "")),
        "password_hash": str(row.get("password_hash", "")),
        "role": str(row.get("role", "member")),
        "status": str(row.get("status", "approved")),
        "created_at": str(row.get("created_at", "")),
        "approved_at": str(row.get("approved_at", "")),
        "approved_by": str(row.get("approved_by", "")),
        "password_changed_at": str(row.get("password_changed_at", "")),
    }


def load_users():
    local_users = _load_local_users()
    if not cloud_is_configured():
        return local_users

    try:
        db = CloudDatabase()
        rows = db.select(
            TABLE_USERS,
            columns=(
                "user_id,name,salt,password_hash,role,status,created_at,"
                "approved_at,approved_by,password_changed_at"
            ),
            order="created_at.asc",
        )
        cloud_users = {}
        for row in rows:
            user = _cloud_row_to_user(row)
            user_id = user.get("user_id", "")
            if user_id:
                cloud_users[user_id] = user

        # 기존 users.json 계정은 Supabase에 없는 계정만 최초 자동 이전한다.
        missing_rows = []
        for user_id, user in local_users.items():
            safe_id = _safe_user_id(user_id)
            if safe_id and safe_id not in cloud_users and isinstance(user, dict):
                migrated = dict(user)
                migrated["user_id"] = safe_id
                cloud_users[safe_id] = migrated
                missing_rows.append(_user_to_cloud_row(safe_id, migrated))

        if missing_rows:
            db.upsert(TABLE_USERS, missing_rows, "user_id")

        _save_local_users(cloud_users)
        return cloud_users
    except Exception:
        # Supabase 장애 시 기존 계정 로그인까지 중단되지 않도록 로컬 백업을 읽는다.
        return local_users


def save_users(users):
    normalized = {}
    for user_id, user in (users or {}).items():
        safe_id = _safe_user_id(user_id)
        if safe_id and isinstance(user, dict):
            normalized[safe_id] = dict(user)
            normalized[safe_id]["user_id"] = safe_id

    if cloud_is_configured():
        rows = [
            _user_to_cloud_row(user_id, user)
            for user_id, user in normalized.items()
        ]
        CloudDatabase().upsert(TABLE_USERS, rows, "user_id")

    _save_local_users(normalized)


def ensure_default_admin():
    """기존 v2 로그인 방식과의 호환을 위해 기본 관리자 계정을 자동 생성한다."""
    users = load_users()
    login_id = _safe_user_id(get_secret("APP_LOGIN_ID", ""))
    login_pw = str(get_secret("APP_LOGIN_PW", "") or "")

    changed = False

    if (
        login_id
        and login_pw
        and not _password_policy_error(login_pw)
        and login_id not in users
    ):
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
        try:
            save_users(users)
        except Exception:
            _save_local_users(users)


def create_user(user_id, password, name):
    user_id = _safe_user_id(user_id)
    name = str(name).strip()

    if not user_id:
        return False, "아이디를 입력해주세요."
    password_error = _password_policy_error(str(password))
    if password_error:
        return False, password_error
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
    try:
        save_users(users)
    except Exception as exc:
        return False, (
            "회원정보를 Supabase에 저장하지 못했습니다. "
            "oasis_users 테이블과 Railway 환경변수를 확인해주세요. "
            f"({type(exc).__name__})"
        )
    return True, "회원가입 신청이 완료되었습니다. 관리자 승인 후 로그인할 수 있습니다."


def _load_authoritative_user_for_login(user_id: str):
    # 로그인 검증은 Supabase의 최신 계정정보를 우선 사용합니다.
    safe_id = _safe_user_id(user_id)
    if not safe_id:
        return None, "아이디 또는 비밀번호가 올바르지 않습니다."

    if cloud_is_configured():
        try:
            rows = CloudDatabase().select(
                TABLE_USERS,
                filters={"user_id": safe_id},
                columns=(
                    "user_id,name,salt,password_hash,role,status,created_at,"
                    "approved_at,approved_by,password_changed_at"
                ),
                limit=1,
            )
        except Exception:
            return None, "로그인 서버에 연결하지 못했습니다. 잠시 후 다시 시도해주세요."

        if not rows:
            return None, "아이디 또는 비밀번호가 올바르지 않습니다."

        user = _cloud_row_to_user(rows[0])
        local_users = _load_local_users()
        local_users[safe_id] = user
        _save_local_users(local_users)
        return user, ""

    return _load_local_users().get(safe_id), ""


def authenticate_user(user_id, password):
    ok, user, _msg = authenticate_user_with_message(user_id, password)
    return user if ok else None


def authenticate_user_with_message(user_id, password):
    user_id = _safe_user_id(user_id)
    if not user_id or not str(password):
        return False, None, "아이디 또는 비밀번호가 올바르지 않습니다."
    lock_remaining = _login_lock_remaining(user_id)
    if lock_remaining:
        wait_minutes = max(1, (lock_remaining + 59) // 60)
        return (
            False,
            None,
            f"로그인 시도가 잠시 제한되었습니다. 약 {wait_minutes}분 후 다시 시도해주세요.",
        )

    ensure_default_admin()

    user, load_error = _load_authoritative_user_for_login(user_id)
    if load_error:
        return False, None, load_error
    if not user:
        _record_login_failure(user_id)
        return False, None, "아이디 또는 비밀번호가 올바르지 않습니다."

    salt = str(user.get("salt", "") or "")
    password_hash = str(user.get("password_hash", "") or "")
    if not salt or not password_hash:
        return False, None, "계정의 비밀번호 정보가 올바르지 않습니다. 관리자에게 문의해주세요."
    if not _verify_password(str(password), salt, password_hash):
        _record_login_failure(user_id)
        return False, None, "아이디 또는 비밀번호가 올바르지 않습니다."

    status = user.get("status", "approved")
    if status == "pending":
        return False, None, "관리자 승인 대기 중입니다. 승인 후 로그인할 수 있습니다."
    if status == "rejected":
        return False, None, "회원가입이 승인되지 않은 계정입니다. 관리자에게 확인해주세요."
    if status != "approved":
        return False, None, "계정 상태를 확인할 수 없습니다. 관리자에게 확인해주세요."

    _clear_login_failures(user_id)
    return True, user, "로그인 성공"


def is_admin(user_id=None):
    ensure_default_admin()
    user_id = _safe_user_id(user_id or st.session_state.get("current_user_id", ""))
    if cloud_is_configured():
        user, load_error = _load_authoritative_user_for_login(user_id)
        if load_error:
            return False
        user = user or {}
    else:
        user = _load_local_users().get(user_id, {})
    return user.get("role") == "admin" and user.get("status", "approved") == "approved"


def can_view_mobile_numbers(user_id=None) -> bool:
    user_id = _safe_user_id(
        user_id or st.session_state.get("current_user_id", "")
    )
    owner_id = _safe_user_id(
        get_secret("MOBILE_PHONE_OWNER_ID", "")
        or get_secret("APP_LOGIN_ID", "")
    )
    return bool(owner_id and user_id == owner_id and is_admin(user_id))


def list_pending_users(requested_by=""):
    if not is_admin(requested_by):
        return []
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


def list_all_users_for_admin(requested_by=""):
    if not is_admin(requested_by):
        return []
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
    if not is_admin(approved_by):
        return False, "관리자 권한이 필요합니다."
    ensure_default_admin()
    user_id = _safe_user_id(user_id)
    users = load_users()
    if user_id not in users:
        return False, "대상 회원을 찾을 수 없습니다."
    users[user_id]["status"] = "approved"
    users[user_id]["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    users[user_id]["approved_by"] = approved_by or "admin"
    try:
        save_users(users)
    except Exception as exc:
        return False, (
            "회원 승인정보를 Supabase에 저장하지 못했습니다. "
            f"({type(exc).__name__})"
        )
    return True, f"{user_id} 회원을 승인했습니다."


def reject_user(user_id, approved_by=""):
    if not is_admin(approved_by):
        return False, "관리자 권한이 필요합니다."
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
    try:
        save_users(users)
    except Exception as exc:
        return False, (
            "회원 거절정보를 Supabase에 저장하지 못했습니다. "
            f"({type(exc).__name__})"
        )
    return True, f"{user_id} 회원을 거절 처리했습니다."


TABLE_USERS = "oasis_users"
TABLE_LOGIN_SESSIONS = "oasis_login_sessions"


def _register_login_session(user_id: str) -> str:
    token = uuid.uuid4().hex
    if cloud_is_configured():
        try:
            CloudDatabase().upsert(
                TABLE_LOGIN_SESSIONS,
                [{
                    "owner_user_id": _safe_user_id(user_id),
                    "session_token": token,
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }],
                "owner_user_id",
            )
        except Exception:
            pass
    return token


def _session_is_current(user_id: str, token: str) -> bool:
    if not user_id or not token or not cloud_is_configured():
        return True
    try:
        rows = CloudDatabase().select(
            TABLE_LOGIN_SESSIONS,
            filters={"owner_user_id": _safe_user_id(user_id)},
            columns="session_token",
            limit=1,
        )
        if not rows:
            return False
        return str(rows[0].get("session_token", "")) == str(token)
    except Exception:
        return False


def _clear_local_login_state() -> None:
    st.session_state.logged_in = False
    st.session_state.current_user_id = ""
    st.session_state.current_user_name = ""
    st.session_state.current_user_role = ""
    st.session_state.login_session_token = ""
    st.session_state.latest_result_file = None
    st.session_state.latest_upload_file = None



def change_password(
    user_id: str,
    current_password: str,
    new_password: str,
) -> tuple[bool, str]:
    ensure_default_admin()
    user_id = _safe_user_id(user_id)
    users = load_users()
    user = users.get(user_id)

    if not user:
        return False, "계정정보를 찾을 수 없습니다."
    if not _verify_password(
        current_password,
        user.get("salt", ""),
        user.get("password_hash", ""),
    ):
        return False, "현재 비밀번호가 일치하지 않습니다."
    password_error = _password_policy_error(str(new_password))
    if password_error:
        return False, password_error
    if current_password == new_password:
        return False, "현재 비밀번호와 다른 비밀번호를 입력해주세요."

    salt, password_hash = _hash_password(new_password)
    user["salt"] = salt
    user["password_hash"] = password_hash
    user["password_changed_at"] = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    users[user_id] = user
    try:
        save_users(users)
    except Exception as exc:
        return False, (
            "변경된 비밀번호를 Supabase에 저장하지 못했습니다. "
            f"({type(exc).__name__})"
        )

    # 비밀번호 변경 즉시 새 토큰을 발급해 다른 기기의 기존 세션을 만료
    new_token = _register_login_session(user_id)
    st.session_state.login_session_token = new_token
    return True, "비밀번호가 변경되었습니다. 다시 로그인해주세요."


def render_password_change(user_id: str) -> None:
    with st.expander("비밀번호 변경", expanded=False):
        current_password = st.text_input(
            "현재 비밀번호",
            type="password",
            key="password_change_current_v740",
        )
        new_password = st.text_input(
            "새 비밀번호",
            type="password",
            placeholder="12자리 이상 · 문자와 숫자 포함",
            key="password_change_new_v740",
        )
        new_password_confirm = st.text_input(
            "새 비밀번호 확인",
            type="password",
            key="password_change_confirm_v740",
        )

        if st.button(
            "비밀번호 변경",
            use_container_width=True,
            key="password_change_button_v740",
        ):
            if new_password != new_password_confirm:
                st.error("새 비밀번호 확인이 일치하지 않습니다.")
                return

            ok, message = change_password(
                user_id,
                current_password,
                new_password,
            )
            if not ok:
                st.error(message)
                return

            _clear_local_login_state()
            st.success(message)
            st.rerun()

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

    if "login_session_token" not in st.session_state:
        st.session_state.login_session_token = ""

    ensure_default_admin()

    if st.session_state.logged_in:
        if not _session_is_current(
            st.session_state.current_user_id,
            st.session_state.login_session_token,
        ):
            _clear_local_login_state()
            st.warning(
                "동일 계정으로 새 로그인이 확인되어 현재 기기에서 자동 로그아웃되었습니다."
            )
            return False

    return st.session_state.logged_in


def login_form(logo_html_func):
    st.markdown("<div class='login-wrap'>", unsafe_allow_html=True)
    st.markdown(f"<div class='login-logo'>{logo_html_func(320)}</div>", unsafe_allow_html=True)

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
                st.session_state.login_session_token = _register_login_session(
                    user.get("user_id", "")
                )
                st.session_state.latest_result_file = None
                st.session_state.latest_upload_file = None
                st.rerun()
            else:
                st.error(msg)

    with signup_tab:
        new_name = st.text_input("이름", placeholder="예: 임주형", key="signup_name")
        new_id = st.text_input("아이디", placeholder="영문/숫자/한글 사용 가능", key="signup_user_id")
        new_pw = st.text_input(
            "비밀번호",
            type="password",
            placeholder="12자리 이상 · 문자와 숫자 포함",
            key="signup_user_pw",
        )
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
        _clear_local_login_state()
        st.rerun()
