from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v7.2.0"
TARGETS = [
    "enterprise_center.py",
    "enterprise_customer_management.py",
    "login_session_guard.py",
    "app.py",
    "auth.py",
    "VERSION.txt",
]


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        fail(f"Patch point not found: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    root = Path.cwd()
    if not (root / "app.py").exists():
        fail("Run this patch from the OASIS project root folder.")

    version_path = root / "VERSION.txt"
    current = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists()
        else ""
    )
    if current and current not in {
        "v7.1.0", "7.1.0", "v7.2.0", "7.2.0"
    }:
        fail(f"Expected v7.1.0 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v7.2.0_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        src = root / name
        if src.exists():
            dst = backup / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    for relative in [
        "enterprise_center.py",
        "enterprise_customer_management.py",
        "login_session_guard.py",
    ]:
        src = root / "payload" / relative
        if not src.exists():
            fail(f"payload/{relative} is missing.")
        shutil.copy2(src, root / relative)

    auth_path = root / "auth.py"
    auth = auth_path.read_text(encoding="utf-8")
    if "from login_session_guard import" not in auth:
        auth = replace_once(
            auth,
            "import streamlit as st\n",
            "import streamlit as st\n\n"
            "from login_session_guard import (\n"
            "    end_login_session,\n"
            "    start_login_session,\n"
            ")\n",
            "auth session import",
        )

    if "st.session_state.login_session_token = start_login_session" not in auth:
        auth = replace_once(
            auth,
            '                st.session_state.current_user_role = user.get("role", "member")\n'
            '                st.session_state.latest_result_file = None\n',
            '                st.session_state.current_user_role = user.get("role", "member")\n'
            '                st.session_state.login_session_token = start_login_session(\n'
            '                    user.get("user_id", "")\n'
            '                )\n'
            '                st.session_state.latest_result_file = None\n',
            "login token creation",
        )

    logout_tail = auth.split("def logout_button():", 1)[-1]
    if "end_login_session(" not in logout_tail:
        auth = replace_once(
            auth,
            'def logout_button():\n'
            '    if st.button("로그아웃", use_container_width=True):\n'
            '        st.session_state.logged_in = False\n',
            'def logout_button():\n'
            '    if st.button("로그아웃", use_container_width=True):\n'
            '        end_login_session(\n'
            '            st.session_state.get("current_user_id", ""),\n'
            '            st.session_state.get("login_session_token", ""),\n'
            '        )\n'
            '        st.session_state.logged_in = False\n',
            "logout invalidation",
        )

    if 'st.session_state.login_session_token = ""' not in auth:
        auth = replace_once(
            auth,
            '        st.session_state.current_user_role = ""\n'
            '        st.session_state.latest_result_file = None\n',
            '        st.session_state.current_user_role = ""\n'
            '        st.session_state.login_session_token = ""\n'
            '        st.session_state.latest_result_file = None\n',
            "logout token clear",
        )
    auth_path.write_text(auth, encoding="utf-8", newline="\n")

    app_path = root / "app.py"
    app = app_path.read_text(encoding="utf-8")

    if "from enterprise_customer_management import render_trash_page" not in app:
        app = replace_once(
            app,
            "from enterprise_center import render_enterprise_management_center\n",
            "from enterprise_center import render_enterprise_management_center\n"
            "from enterprise_customer_management import render_trash_page\n"
            "from login_session_guard import (\n"
            "    clear_streamlit_login_state,\n"
            "    validate_login_session,\n"
            ")\n",
            "app imports",
        )

    if "같은 아이디로 다른 기기에서 로그인" not in app:
        app = replace_once(
            app,
            "if not check_login():\n"
            "    login_form(logo_html)\n\n"
            'CURRENT_USER_ID = st.session_state.get("current_user_id", "")\n',
            "if not check_login():\n"
            "    login_form(logo_html)\n\n"
            "if not validate_login_session(\n"
            '    st.session_state.get("current_user_id", ""),\n'
            '    st.session_state.get("login_session_token", ""),\n'
            "):\n"
            "    clear_streamlit_login_state()\n"
            "    st.warning(\n"
            '        "같은 아이디로 다른 기기에서 로그인하여 기존 접속이 종료되었습니다."\n'
            "    )\n"
            "    login_form(logo_html)\n\n"
            'CURRENT_USER_ID = st.session_state.get("current_user_id", "")\n',
            "single login check",
        )

    if 'menu_label_map["휴지통"] = "고객 휴지통"' not in app:
        app = replace_once(
            app,
            '        menu_label_map["AI 사용량"] = "AI 사용량"\n\n'
            "    selected_menu_label = st.radio(\n",
            '        menu_label_map["AI 사용량"] = "AI 사용량"\n\n'
            '    menu_label_map["휴지통"] = "고객 휴지통"\n\n'
            "    selected_menu_label = st.radio(\n",
            "trash bottom menu",
        )

    if 'active_tab == "고객 휴지통"' not in app:
        app = replace_once(
            app,
            'elif active_tab == "AI 코파일럿":\n'
            "    render_copilot_page(\n"
            "        CURRENT_USER_ID,\n"
            "        CURRENT_USER_NAME,\n"
            "    )\n\n"
            'elif active_tab == "내 누적 고객DB":\n',
            'elif active_tab == "AI 코파일럿":\n'
            "    render_copilot_page(\n"
            "        CURRENT_USER_ID,\n"
            "        CURRENT_USER_NAME,\n"
            "    )\n\n"
            'elif active_tab == "고객 휴지통":\n'
            "    render_trash_page(\n"
            "        CURRENT_USER_ID,\n"
            "        CURRENT_USER_NAME,\n"
            "    )\n\n"
            'elif active_tab == "내 누적 고객DB":\n',
            "trash route",
        )
    app_path.write_text(app, encoding="utf-8", newline="\n")

    version_path.write_text(VERSION + "\n", encoding="utf-8")
    changelog_src = root / "payload" / "CHANGELOG_v7.2.0.md"
    if changelog_src.exists():
        shutil.copy2(changelog_src, root / "CHANGELOG_v7.2.0.md")

    for name in [
        "enterprise_center.py",
        "enterprise_customer_management.py",
        "login_session_guard.py",
        "auth.py",
        "app.py",
    ]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=supabase_v720_upgrade.sql")
    print("RESULT=UI simplified, trash menu added, single-login protection enabled.")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
