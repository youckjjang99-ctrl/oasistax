from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from cloud_db import CloudDatabase, cloud_is_configured

TABLE_LOGIN_SESSIONS = "oasis_login_sessions"
LOCAL_PATH = Path(__file__).parent / "data" / "login_sessions.json"


def _load_local() -> dict[str, Any]:
    if not LOCAL_PATH.exists():
        return {}
    try:
        data = json.loads(LOCAL_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_local(data: dict[str, Any]) -> None:
    LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def start_login_session(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now().isoformat(timespec="seconds")
    local = _load_local()
    local[user_id] = {"session_token": token, "updated_at": now}
    _save_local(local)

    if cloud_is_configured():
        try:
            CloudDatabase().upsert(
                TABLE_LOGIN_SESSIONS,
                [{
                    "owner_user_id": user_id,
                    "session_token": token,
                    "updated_at": now,
                }],
                "owner_user_id",
            )
        except Exception:
            pass
    return token


def _current_token(user_id: str) -> str:
    if cloud_is_configured():
        try:
            rows = CloudDatabase().select(
                TABLE_LOGIN_SESSIONS,
                filters={"owner_user_id": user_id},
                columns="session_token",
                limit=1,
            )
            if rows:
                return str(rows[0].get("session_token", "") or "")
        except Exception:
            pass
    return str(
        _load_local().get(user_id, {}).get("session_token", "") or ""
    )


def validate_login_session(user_id: str, token: str) -> bool:
    if not user_id or not token:
        return False
    return secrets.compare_digest(_current_token(user_id), token)


def end_login_session(user_id: str, token: str) -> None:
    if user_id and token:
        current = _current_token(user_id)
        if current and secrets.compare_digest(current, token):
            start_login_session(user_id)


def clear_streamlit_login_state() -> None:
    st.session_state.logged_in = False
    st.session_state.current_user_id = ""
    st.session_state.current_user_name = ""
    st.session_state.current_user_role = ""
    st.session_state.login_session_token = ""
    st.session_state.latest_result_file = None
    st.session_state.latest_upload_file = None
