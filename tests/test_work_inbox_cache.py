from unittest.mock import patch

import streamlit as st

import work_inbox


class _SessionState(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


def test_work_inbox_is_reused_then_invalidated():
    state = _SessionState()
    first = {"ok": True, "summary": {"today_count": 1}, "items": []}
    second = {"ok": True, "summary": {"today_count": 2}, "items": []}

    with (
        patch.object(st, "session_state", state),
        patch("work_inbox.build_work_inbox", side_effect=[first, second]) as build,
    ):
        assert work_inbox.get_cached_work_inbox("owner") == first
        assert work_inbox.get_cached_work_inbox("owner") == first
        assert build.call_count == 1

        work_inbox.invalidate_work_inbox_cache("owner")
        assert work_inbox.get_cached_work_inbox("owner") == second
        assert build.call_count == 2
