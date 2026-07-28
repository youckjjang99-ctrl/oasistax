from __future__ import annotations

import unittest
from unittest.mock import patch

import scheduled_employment_contact_enrichment as job


class EmploymentContactEnrichmentTest(unittest.TestCase):
    @patch.object(job, "_patch", return_value=True)
    @patch.object(job, "enrich_company")
    def test_phone_stage_saves_only_phone_contacts(
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
        self.assertEqual(saved["email"], "")
        self.assertEqual(saved["instagram_id"], "")
        self.assertEqual(saved["status"], "matched")
        self.assertEqual(saved["phone_status"], "matched")
        enrich.assert_called_once()
        self.assertTrue(enrich.call_args.kwargs["bulk_mode"])
        self.assertEqual(
            enrich.call_args.kwargs["contact_stage"],
            "phone",
        )

    @patch.object(job, "_patch", return_value=True)
    @patch.object(job, "enrich_company")
    def test_digital_stage_preserves_phone_and_saves_digital_contacts(
        self,
        enrich,
        patch_row,
    ) -> None:
        enrich.return_value = {
            "ok": True,
            "contacts": [
                {
                    "contact_type": "email",
                    "contact_value": "hello@example.com",
                    "source_type": "official_website",
                    "source_url": "https://example.com",
                    "confidence": 90,
                    "verification_status": "review_required",
                },
                {
                    "contact_type": "instagram",
                    "contact_value": "@oasis.test",
                    "source_type": "naver_web_snippet",
                    "source_url": "https://instagram.com/oasis.test/",
                    "confidence": 88,
                    "verification_status": "review_required",
                },
            ],
        }
        result = job._enrich_one(
            {
                "contact_key": "business:1234567890",
                "status": "matched",
                "digital_status": "pending",
                "attempt_count": 1,
                "digital_attempt_count": 0,
                "company_name": "테스트기업",
                "address": "서울특별시 강남구",
                "mobile_phone": "010-1111-2222",
                "landline_phone": "",
                "email": "",
                "instagram_id": "",
                "instagram_url": "",
            },
            "digital",
        )

        self.assertEqual(result["status"], "matched")
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["mobile_phone"], "010-1111-2222")
        self.assertEqual(saved["email"], "hello@example.com")
        self.assertEqual(saved["instagram_id"], "@oasis.test")
        self.assertEqual(saved["digital_status"], "matched")
        self.assertFalse(enrich.call_args.kwargs["bulk_mode"])
        self.assertEqual(
            enrich.call_args.kwargs["contact_stage"],
            "digital",
        )

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
        self.assertEqual(saved["phone_status"], "no_match")
        self.assertEqual(saved["attempt_count"], 3)
        self.assertIn("phone_next_check_at", saved)

    @patch.object(job, "_patch", return_value=True)
    @patch.object(
        job,
        "enrich_company",
        return_value={
            "ok": True,
            "contacts": [],
            "trace": [
                {
                    "stage": "naver_phone",
                    "status": "HTTP_429",
                }
            ],
        },
    )
    def test_provider_limit_pauses_instead_of_saving_no_match(
        self,
        _enrich,
        patch_row,
    ) -> None:
        result = job._enrich_one(
            {
                "contact_key": "place:quota",
                "phone_status": "pending",
                "company_name": "테스트기업",
                "address": "서울특별시 강남구",
            }
        )
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["halt"])
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["phone_status"], "error")
        self.assertIn("UpstreamLimitError", saved["phone_last_error"])


if __name__ == "__main__":
    unittest.main()
