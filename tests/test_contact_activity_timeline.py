from __future__ import annotations

import inspect

import prospect_db_center as prospect


def test_contact_activity_rows_are_newest_first_in_korea_time() -> None:
    rows = prospect._contact_activity_rows(
        [
            {
                "contact_method": "전화",
                "contact_result": "부재중",
                "notes": "첫 번째 기록",
                "contacted_at": "2026-08-05T01:15:00Z",
                "next_contact_at": "2026-08-06T02:00:00Z",
            },
            {
                "contact_method": "상담",
                "contact_result": "상담 완료",
                "notes": "후속 상담 기록",
                "contacted_at": "2026-08-05T04:30:00Z",
            },
        ]
    )

    assert [row["상담내용"] for row in rows] == [
        "후속 상담 기록",
        "첫 번째 기록",
    ]
    assert rows[0]["일시 (KST)"] == "2026.08.05 13:30"
    assert rows[1]["일시 (KST)"] == "2026.08.05 10:15"
    assert rows[1]["다음 연락예정일"] == "2026.08.06 11:00"


def test_contact_activity_rows_keep_blank_details_readable() -> None:
    rows = prospect._contact_activity_rows(
        [
            {
                "contact_method": "문자",
                "contact_result": "발송",
                "created_at": "2026-08-05T09:00:00+09:00",
                "notes": "",
            }
        ]
    )

    assert rows == [
        {
            "일시 (KST)": "2026.08.05 09:00",
            "연락방식": "문자",
            "연락결과": "발송",
            "상담내용": "-",
            "다음 연락예정일": "-",
        }
    ]


def test_company_selection_shows_timeline_before_blank_entry_form() -> None:
    source = inspect.getsource(prospect._render_contact_results)

    timeline_at = source.index('st.markdown("#### 업체 활동 이력")')
    query_at = source.index(
        "sales_assignments.list_company_contacts(",
        timeline_at,
    )
    form_at = source.index(
        'with st.form("contact_results_record_form_v1050"'
    )

    assert timeline_at < query_at < form_at
    assert source.count("sales_assignments.list_company_contacts(") == 2
    assert "최신순" in source
    assert "일시 (KST)" in source
    assert "상담내용" in source
    assert "메모·상담내용" not in source
