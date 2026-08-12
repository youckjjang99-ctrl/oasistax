from __future__ import annotations

import inspect

import prospect_db_center as prospect


def test_db_request_time_is_displayed_in_seoul_timezone() -> None:
    assert (
        prospect._format_db_request_time("2026-08-12T01:05:00+00:00")
        == "2026-08-12 10:05"
    )
    assert (
        prospect._format_db_request_time("2026-08-12T10:05:00+09:00")
        == "2026-08-12 10:05"
    )
    assert prospect._format_db_request_time("") == "-"


def test_user_and_admin_request_histories_share_seoul_time_formatter() -> None:
    user_source = inspect.getsource(prospect._render_db_request_home)
    admin_source = inspect.getsource(prospect._render_mobile_db_admin)

    assert '_format_db_request_time(\n                            row.get("requested_at")' in user_source
    assert '_format_db_request_time(\n                        row.get("requested_at")' in admin_source
    assert '.replace("T", " ")[:16]' not in user_source
    assert '.replace("T", " ")[:16]' not in admin_source
