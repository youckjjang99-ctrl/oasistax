import unittest

from prospect_db_center import (
    _display_frame,
    _effective_contact_filter_labels,
    _effective_prospect_mobile_visibility,
    _effective_prospect_target_count,
    _limit_prospect_result_for_role,
    _redact_mobile_candidate,
    _sanitize_search_result,
    _saved_candidate_frame,
)


class ProspectMobileAccessTests(unittest.TestCase):
    def test_mobile_visibility_never_widens_authenticated_permission(self):
        self.assertFalse(
            _effective_prospect_mobile_visibility(
                False,
                is_admin_user=False,
            )
        )
        self.assertFalse(
            _effective_prospect_mobile_visibility(
                False,
                is_admin_user=True,
            )
        )
        self.assertTrue(
            _effective_prospect_mobile_visibility(
                True,
                is_admin_user=True,
            )
        )

    def test_member_search_count_is_always_capped_at_thirty(self):
        self.assertEqual(
            _effective_prospect_target_count(500, is_admin_user=False),
            30,
        )
        self.assertEqual(
            _effective_prospect_target_count(1, is_admin_user=False),
            30,
        )
        self.assertEqual(
            _effective_prospect_target_count(300, is_admin_user=True),
            300,
        )
        oversized = {
            "items": [{"source_key": str(index)} for index in range(50)],
            "found_count": 50,
        }
        member_result = _limit_prospect_result_for_role(
            oversized,
            is_admin_user=False,
        )
        admin_result = _limit_prospect_result_for_role(
            oversized,
            is_admin_user=True,
        )
        self.assertEqual(member_result["found_count"], 30)
        self.assertEqual(len(member_result["items"]), 30)
        self.assertEqual(admin_result["found_count"], 50)
        self.assertEqual(len(admin_result["items"]), 50)

    def test_member_phone_filters_are_mandatory_together(self):
        self.assertEqual(
            _effective_contact_filter_labels(
                ["휴대전화"],
                is_admin_user=False,
            ),
            ["휴대전화", "일반전화"],
        )
        self.assertEqual(
            _effective_contact_filter_labels(
                ["이메일"],
                is_admin_user=False,
            ),
            ["휴대전화", "일반전화", "이메일"],
        )
        self.assertEqual(
            _effective_contact_filter_labels(
                ["일반전화", "이메일"],
                is_admin_user=True,
            ),
            ["일반전화", "이메일"],
        )

    def test_member_receives_landline_but_not_mobile(self):
        item = {
            "사업장명": "테스트 업체",
            "대표전화": "010-1234-5678",
            "휴대전화": "010-1234-5678",
            "일반전화": "02-1234-5678",
            "전화유형": "휴대전화",
            "이메일": "hello@example.com",
            "source_data": {
                "sales_intelligence_v971": {
                    "phone": "010-9999-8888",
                },
                "raw_note": "문의 010-7777-6666 또는 공식 이메일",
            },
        }

        redacted = _redact_mobile_candidate(item, can_view_mobile=False)

        self.assertEqual(redacted["대표전화"], "02-1234-5678")
        self.assertEqual(redacted["휴대전화"], "")
        self.assertEqual(redacted["일반전화"], "02-1234-5678")
        self.assertEqual(
            redacted["source_data"]["sales_intelligence_v971"]["phone"],
            "",
        )
        self.assertNotIn(
            "010-7777-6666",
            redacted["source_data"]["raw_note"],
        )
        frame = _display_frame([item], can_view_mobile=False)
        self.assertEqual(frame.loc[0, "휴대전화"], "")
        self.assertEqual(frame.loc[0, "일반전화"], "02-1234-5678")

    def test_owner_keeps_mobile_number(self):
        item = {
            "사업장명": "테스트 업체",
            "대표전화": "010-1234-5678",
            "휴대전화": "010-1234-5678",
            "일반전화": "02-1234-5678",
        }

        frame = _display_frame([item], can_view_mobile=True)

        self.assertEqual(frame.loc[0, "휴대전화"], "010-1234-5678")
        self.assertEqual(frame.loc[0, "대표전화"], "010-1234-5678")

    def test_member_search_result_excludes_mobile_only_company(self):
        result = {
            "ok": True,
            "found_count": 2,
            "items": [
                {
                    "사업장명": "휴대전화 전용",
                    "대표전화": "010-1111-2222",
                    "휴대전화": "010-1111-2222",
                },
                {
                    "사업장명": "이메일 보유",
                    "대표전화": "010-3333-4444",
                    "휴대전화": "010-3333-4444",
                    "이메일": "sales@example.com",
                },
            ],
        }

        sanitized = _sanitize_search_result(result, can_view_mobile=False)

        self.assertEqual(sanitized["found_count"], 1)
        self.assertEqual(sanitized["items"][0]["사업장명"], "이메일 보유")
        self.assertEqual(sanitized["items"][0]["휴대전화"], "")

    def test_saved_candidates_use_landline_for_member(self):
        rows = [
            {
                "id": "prospect-1",
                "company_name": "테스트 업체",
                "source_data": {
                    "sales_intelligence_v971": {
                        "phone": "010-9999-8888",
                        "email": "hello@example.com",
                    }
                },
            }
        ]
        contacts = [
            {
                "prospect_id": "prospect-1",
                "contact_type": "phone",
                "contact_value": "010-1234-5678",
                "confidence": 100,
            },
            {
                "prospect_id": "prospect-1",
                "contact_type": "phone",
                "contact_value": "031-123-4567",
                "confidence": 90,
            },
        ]

        frame = _saved_candidate_frame(
            rows,
            contacts,
            can_view_mobile=False,
        )

        self.assertEqual(frame.loc[0, "휴대전화"], "")
        self.assertEqual(frame.loc[0, "일반전화"], "031-123-4567")
        self.assertEqual(frame.loc[0, "대표전화"], "031-123-4567")


if __name__ == "__main__":
    unittest.main()
