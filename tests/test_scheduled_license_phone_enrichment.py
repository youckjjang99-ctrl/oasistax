from __future__ import annotations

import unittest
from unittest.mock import patch

import scheduled_license_phone_enrichment as job


class ScheduledLicensePhoneEnrichmentTests(unittest.TestCase):
    @patch.object(job, "_patch_if_phone_empty", return_value=True)
    @patch.object(job.kakao_local_client, "search_company")
    def test_saves_only_confident_phone(self, search, patch_phone):
        search.return_value = {
            "ok": True,
            "candidates": [
                {
                    "phone": "02-123-4567",
                    "confidence": 91,
                    "source_url": "https://place.map.kakao.com/1",
                }
            ],
        }
        result = job._enrich_one(
            {
                "source_key": "svc:1",
                "company_name": "테스트상사",
                "address": "서울 강남구 테헤란로 1",
            },
            85,
        )
        self.assertEqual(result["status"], "matched")
        values = patch_phone.call_args.args[1]
        self.assertEqual(values["phone"], "02-123-4567")
        self.assertEqual(values["phone_source"], "kakao_local")
        self.assertEqual(values["phone_confidence"], 91)

    @patch.object(job, "_patch_if_phone_empty", return_value=True)
    @patch.object(job.kakao_local_client, "search_company")
    def test_rejects_phone_below_threshold(self, search, patch_phone):
        search.return_value = {
            "ok": True,
            "candidates": [
                {
                    "phone": "010-1234-5678",
                    "confidence": 84,
                    "source_url": "https://place.map.kakao.com/2",
                }
            ],
        }
        result = job._enrich_one(
            {
                "source_key": "svc:2",
                "company_name": "동명이인상사",
                "address": "서울 중구 세종대로 1",
            },
            85,
        )
        self.assertEqual(result["status"], "no_match")
        values = patch_phone.call_args.args[1]
        self.assertNotIn("phone", values)
        self.assertEqual(values["phone_enrichment_status"], "no_match")


if __name__ == "__main__":
    unittest.main()
