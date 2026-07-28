from __future__ import annotations

import unittest
from unittest.mock import patch

import scheduled_employment_contact_enrichment as job


class EmploymentContactEnrichmentTest(unittest.TestCase):
    @patch.object(job, "_patch", return_value=True)
    @patch.object(job, "enrich_company")
    def test_saves_mobile_landline_email_and_instagram(
        self,
        enrich,
        patch_row,
    ) -> None:
        enrich.return_value = {
            "ok": True,
            "contacts": [
                {
                    "contact_type": "phone",
                    "contact_value": "010-1111-2222",
                    "source_type": "naver_web_snippet",
                    "source_url": "https://example.com/mobile",
                    "confidence": 90,
                    "verification_status": "auto_verified",
                    "is_primary": True,
                },
                {
                    "contact_type": "phone",
                    "contact_value": "02-333-4444",
                    "source_type": "kakao_local",
                    "source_url": "https://place.map.kakao.com/1",
                    "confidence": 95,
                    "verification_status": "auto_verified",
                    "is_primary": False,
                },
                {
                    "contact_type": "email",
                    "contact_value": "hello@example.com",
                    "source_type": "naver_web_snippet",
                    "source_url": "https://example.com",
                    "confidence": 85,
                    "verification_status": "review_required",
                    "is_primary": False,
                },
                {
                    "contact_type": "instagram",
                    "contact_value": "@oasis.test",
                    "source_type": "naver_web_snippet",
                    "source_url": "https://instagram.com/oasis.test/",
                    "confidence": 88,
                    "verification_status": "review_required",
                    "is_primary": False,
                },
            ],
        }
        result = job._enrich_one(
            {
                "contact_key": "business:1234567890",
                "status": "pending",
                "attempt_count": 0,
                "business_no": "1234567890",
                "company_name": "테스트기업",
                "address": "서울특별시 강남구",
                "industry_name": "서비스업",
            }
        )

        self.assertEqual(result["status"], "matched")
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["mobile_phone"], "010-1111-2222")
        self.assertEqual(saved["landline_phone"], "02-333-4444")
        self.assertEqual(saved["email"], "hello@example.com")
        self.assertEqual(saved["instagram_id"], "@oasis.test")
        self.assertEqual(saved["status"], "matched")
        enrich.assert_called_once()
        self.assertTrue(enrich.call_args.kwargs["bulk_mode"])

    @patch.object(job, "_patch", return_value=True)
    @patch.object(
        job,
        "enrich_company",
        return_value={"ok": True, "contacts": []},
    )
    def test_no_match_is_checked_again_after_ninety_days(
        self,
        _enrich,
        patch_row,
    ) -> None:
        result = job._enrich_one(
            {
                "contact_key": "place:test",
                "status": "pending",
                "attempt_count": 2,
                "company_name": "미확인기업",
                "address": "경기도 수원시",
            }
        )
        self.assertEqual(result["status"], "no_match")
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["status"], "no_match")
        self.assertEqual(saved["attempt_count"], 3)
        self.assertIn("next_check_at", saved)


if __name__ == "__main__":
    unittest.main()
