from __future__ import annotations

import inspect

import prospect_db_center as prospect


def _phone(*parts: str) -> str:
    return "-".join(parts)


def test_assignment_phone_prefers_visible_mobile_then_landline() -> None:
    assignment = {
        "source_data": {
            "mobile_phone": _phone("010", "1111", "2222"),
            "landline_phone": _phone("02", "333", "4444"),
        }
    }

    assert prospect._assignment_contact_phone(
        assignment,
        can_view_mobile=True,
    ) == _phone("010", "1111", "2222")
    assert prospect._assignment_contact_phone(
        assignment,
        can_view_mobile=False,
    ) == _phone("02", "333", "4444")


def test_assignment_phone_uses_nested_analysis_without_leaking_mobile() -> None:
    assignment = {
        "source_data": {
            "sales_intelligence_v971": {
                "phone": _phone("010", "5555", "6666"),
            }
        }
    }

    assert prospect._assignment_contact_phone(
        assignment,
        can_view_mobile=False,
    ) == ""
    assert prospect._assignment_contact_phone(
        assignment,
        can_view_mobile=True,
    ) == _phone("010", "5555", "6666")


def test_latest_contact_result_drives_progress_label() -> None:
    assignment = {"status": "assigned"}

    assert prospect._contact_progress_label(assignment, None) == "미연락"
    assert prospect._contact_progress_label(
        assignment,
        {"contact_result": "missed"},
    ) == "부재중"
    assert prospect._contact_progress_label(
        assignment,
        {"contact_result": "connected"},
    ) == "연락"
    assert prospect._contact_progress_label(
        assignment,
        {"contact_result": "consultation_scheduled"},
    ) == "상담예약"


def test_latest_contact_is_selected_per_company_by_time() -> None:
    latest = prospect._latest_contact_by_company(
        [
            {
                "company_uid": "source:" + "a" * 64,
                "contact_result": "missed",
                "contacted_at": "2026-08-05T01:00:00Z",
            },
            {
                "company_uid": "source:" + "a" * 64,
                "contact_result": "connected",
                "contacted_at": "2026-08-05T02:00:00Z",
            },
            {
                "company_uid": "source:" + "b" * 64,
                "contact_result": "sms_sent",
                "contacted_at": "2026-08-05T01:30:00Z",
            },
        ]
    )

    assert latest["source:" + "a" * 64]["contact_result"] == "connected"
    assert latest["source:" + "b" * 64]["contact_result"] == "sms_sent"


def test_contact_selector_includes_progress_phone_and_permission() -> None:
    source = inspect.getsource(prospect._render_contact_results)
    render_source = inspect.getsource(prospect.render_prospect_db_center)

    assert "latest_contact_by_uid" in source
    assert "progress_label" in source
    assert "contact_phone" in source
    assert "연락처 없음" in source
    assert "can_view_mobile=can_view_mobile" in source
    assert "can_view_mobile=can_view_mobile" in render_source
