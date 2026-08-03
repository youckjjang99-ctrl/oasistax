from __future__ import annotations

import unittest
from unittest.mock import patch

import prospect_db_center as prospect


TEST_BUSINESS_UID = "business:" + "".join(("123", "45", "67890"))


class _GuidanceStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def evaluate_guidance_eligibility(
        self,
        company,
        *,
        current_user_id,
        is_admin_user,
        assignment,
    ):
        self.calls.append(("pure", dict(company)))
        return {
            "eligible": True,
            "code": "ELIGIBLE",
            "message": "발송할 수 있습니다.",
        }

    def evaluate_send_eligibility(
        self,
        company,
        *,
        current_user_id,
        is_admin_user,
        assignment,
        message_type,
    ):
        self.calls.append(("server", dict(company)))
        return {
            "eligible": True,
            "code": "ELIGIBLE",
            "message": "발송할 수 있습니다.",
            "company_uid": company["company_uid"],
        }


class ProspectKakaoGuidanceUiTests(unittest.TestCase):
    def test_idempotency_key_separates_company_uids_with_same_suffix(self):
        shared_suffix = "12345678901234567890"
        session_state = {}

        with patch.object(prospect.st, "session_state", session_state), patch.object(
            prospect.secrets,
            "token_urlsafe",
            side_effect=["company-a-key", "company-b-key"],
        ) as token_urlsafe:
            company_a_key = prospect._guidance_idempotency_key(
                f"source-alpha:{shared_suffix}",
                "employment_support",
            )
            company_b_key = prospect._guidance_idempotency_key(
                f"source-beta:{shared_suffix}",
                "employment_support",
            )

        self.assertEqual(company_a_key, "company-a-key")
        self.assertEqual(company_b_key, "company-b-key")
        self.assertEqual(token_urlsafe.call_count, 2)
        self.assertEqual(len(session_state), 2)

    def test_idempotency_key_separates_guidance_types(self):
        session_state = {}

        with patch.object(prospect.st, "session_state", session_state), patch.object(
            prospect.secrets,
            "token_urlsafe",
            side_effect=["employment-key", "policy-key"],
        ) as token_urlsafe:
            employment_key = prospect._guidance_idempotency_key(
                TEST_BUSINESS_UID,
                "employment_support",
            )
            policy_key = prospect._guidance_idempotency_key(
                TEST_BUSINESS_UID,
                "policy_fund",
            )

        self.assertEqual(employment_key, "employment-key")
        self.assertEqual(policy_key, "policy-key")
        self.assertEqual(token_urlsafe.call_count, 2)
        self.assertEqual(len(session_state), 2)

    def test_idempotency_key_is_stable_for_the_same_request(self):
        session_state = {}

        with patch.object(prospect.st, "session_state", session_state), patch.object(
            prospect.secrets,
            "token_urlsafe",
            return_value="stable-key",
        ) as token_urlsafe:
            first = prospect._guidance_idempotency_key(
                TEST_BUSINESS_UID,
                "employment_support",
            )
            second = prospect._guidance_idempotency_key(
                TEST_BUSINESS_UID,
                "employment_support",
            )

        self.assertEqual(first, "stable-key")
        self.assertEqual(second, first)
        token_urlsafe.assert_called_once_with(32)
        self.assertEqual(len(session_state), 1)

    def test_company_payload_is_minimal_and_keeps_only_guidance_fields(self):
        payload = prospect._guidance_company_payload(
            {
                "_company_uid": TEST_BUSINESS_UID,
                "_prospect_id": "prospect-1",
                "업체명": "테스트 업체",
                "사업자유형": "개인사업자 후보",
                "_verified_business_type": "individual",
                "휴대전화": "-".join(("010", "1234", "5678")),
                "주민번호": "should-not-pass",
                "메모": "should-not-pass",
            }
        )

        self.assertEqual(
            set(payload),
            {
                "id",
                "company_id",
                "company_uid",
                "company_name",
                "business_type",
                "mobile_phone",
            },
        )
        self.assertNotIn("주민번호", payload)
        self.assertNotIn("메모", payload)
        self.assertEqual(payload["business_type"], "individual")

    def test_visible_business_type_guess_is_not_treated_as_verified(self):
        payload = prospect._guidance_company_payload(
            {
                "_company_uid": "source:" + ("a" * 64),
                "업체명": "개인사업자로 추정된 업체",
                "사업자유형": "개인사업자",
                "휴대전화": "-".join(("010", "1234", "5678")),
            }
        )

        self.assertEqual(payload["business_type"], "")

    def test_receiver_is_masked_for_dialog_display(self):
        masked = prospect._mask_guidance_mobile("-".join(("010", "1234", "5678")))

        self.assertEqual(masked, "010-****-5678")
        self.assertNotIn("1234", masked)

    def test_ui_eligibility_requires_server_composite_check(self):
        guidance = _GuidanceStub()
        company = {
            "company_uid": TEST_BUSINESS_UID,
            "mobile_phone": "-".join(("010", "1234", "5678")),
        }
        assignment = {
            "assignment_id": "assignment-1",
            "assigned_user_id": "user-1",
        }

        with patch.object(
            prospect,
            "_load_company_kakao_guidance",
            return_value=guidance,
        ):
            result = prospect._guidance_eligibility_for_ui(
                company,
                assignment,
                current_user_id="user-1",
                is_admin_user=False,
                message_type="employment_support",
            )

        self.assertTrue(result["eligible"])
        self.assertEqual([name for name, _ in guidance.calls], ["pure", "server"])
        self.assertNotIn("recipient_phone_hash", result)
        self.assertNotIn("mobile_phone", result)

    def test_ui_fails_closed_without_composite_check(self):
        class PureOnly:
            @staticmethod
            def evaluate_guidance_eligibility(*args, **kwargs):
                return {"eligible": True, "code": "ELIGIBLE"}

        with patch.object(
            prospect,
            "_load_company_kakao_guidance",
            return_value=PureOnly(),
        ):
            result = prospect._guidance_eligibility_for_ui(
                {"company_uid": TEST_BUSINESS_UID},
                {"assignment_id": "assignment-1"},
                current_user_id="user-1",
                is_admin_user=False,
                message_type="employment_support",
            )

        self.assertFalse(result["eligible"])
        self.assertEqual(result["code"], "FEATURE_NOT_READY")

    def test_duplicate_result_uses_fixed_non_sensitive_message(self):
        level, message = prospect._guidance_result_message(
            {"ok": False, "code": "DUPLICATE_WITHIN_7_DAYS"}
        )

        self.assertEqual(level, "warning")
        self.assertIn("최근 7일 이내", message)
        self.assertNotIn("010", message)

    def test_admin_settings_require_reason_before_rpc(self):
        guidance = unittest.mock.Mock()

        result = prospect._guidance_admin_update_settings(
            guidance,
            current_user_id="admin-1",
            enabled=True,
            daily_limit=100,
            reason="   ",
        )

        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "REASON_REQUIRED")
        guidance.update_guidance_admin_settings.assert_not_called()

    def test_company_control_uses_uid_and_never_passes_phone_hash(self):
        guidance = unittest.mock.Mock()
        guidance.set_guidance_contact_control.return_value = {
            "success": True,
            "code": "UPDATED",
        }

        result = prospect._guidance_admin_set_company_control(
            guidance,
            current_user_id="admin-1",
            company_uid=TEST_BUSINESS_UID,
            status="admin_blocked",
            reason="관리자 검토",
        )

        self.assertTrue(result["success"])
        guidance.set_guidance_contact_control.assert_called_once_with(
            current_user_id="admin-1",
            company_uid=TEST_BUSINESS_UID,
            recipient_phone_hash="",
            status="admin_blocked",
            reason="관리자 검토",
        )

    def test_admin_history_excludes_phone_hash_and_raw_company_name(self):
        frame = prospect._guidance_admin_history_frame(
            [
                {
                    "created_at": "2026-08-03T01:02:03Z",
                    "company_name": "원본 업체명",
                    "company_name_masked": "원***",
                    "recipient_phone": "-".join(("010", "1234", "5678")),
                    "recipient_phone_masked": "010-****-5678",
                    "recipient_phone_hash": "secret-hash",
                    "message_type": "employment_support",
                    "status": "sent",
                }
            ],
            {"employment_support": "고용지원금 안내"},
        )

        self.assertNotIn("수신번호", frame.columns)
        rendered = frame.to_string(index=False)
        self.assertNotIn("010", rendered)
        self.assertNotIn("secret-hash", rendered)
        self.assertNotIn("원본 업체명", rendered)
        self.assertIn("원***", rendered)

    def test_non_admin_cannot_render_admin_panel(self):
        with patch.object(prospect.st, "expander") as expander:
            prospect._render_guidance_admin_readonly(
                "member-1",
                is_admin_user=False,
            )

        expander.assert_not_called()


if __name__ == "__main__":
    unittest.main()
