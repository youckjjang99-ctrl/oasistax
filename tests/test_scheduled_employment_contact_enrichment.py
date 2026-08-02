from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import scheduled_employment_contact_enrichment as job


class EmploymentContactEnrichmentTest(unittest.TestCase):
    @patch.object(job.time, "sleep")
    @patch.object(job.requests, "patch")
    @patch.object(job, "CloudDatabase")
    def test_patch_retries_statement_timeout_and_returns_compact_row(
        self,
        cloud_database,
        request_patch,
        _sleep,
    ) -> None:
        db = Mock()
        db.headers = {}
        db.config.timeout = 20
        db._url.return_value = "https://example.supabase.co/rest/v1/contacts"
        cloud_database.return_value = db
        timed_out = Mock(
            ok=False,
            status_code=500,
            text='{"code":"57014","message":"statement timeout"}',
        )
        succeeded = Mock(
            ok=True,
            status_code=200,
            text='[{"contact_key":"place:test"}]',
        )
        succeeded.json.return_value = [{"contact_key": "place:test"}]
        request_patch.side_effect = [timed_out, succeeded]

        claimed = job._patch(
            "place:test",
            {"phone_status": "processing"},
            expected_status="pending",
            status_field="phone_status",
            expected_phone_provider_stage="naver",
        )

        self.assertTrue(claimed)
        self.assertEqual(request_patch.call_count, 2)
        self.assertEqual(
            request_patch.call_args.kwargs["params"]["select"],
            "contact_key",
        )
        self.assertEqual(
            request_patch.call_args.kwargs["params"][
                "phone_provider_stage"
            ],
            "eq.naver",
        )

    @patch.object(job, "_patch", return_value=True)
    def test_phone_claim_compares_provider_stage_atomically(
        self,
        patch_row,
    ) -> None:
        claimed = job._claim(
            {
                "contact_key": "place:test",
                "phone_status": "pending",
                "phone_provider_stage": "naver",
            },
            "phone",
            "naver",
        )

        self.assertTrue(claimed)
        self.assertEqual(
            patch_row.call_args.kwargs[
                "expected_phone_provider_stage"
            ],
            "naver",
        )
        self.assertEqual(
            patch_row.call_args.kwargs["expected_status"],
            "pending",
        )

    @patch.object(
        job.naver_web_search_client,
        "key_status",
        return_value={"configured": True},
    )
    @patch.object(
        job.kakao_local_client,
        "key_status",
        return_value={"configured": True},
    )
    @patch.object(job, "_enrich_one", side_effect=RuntimeError("db timeout"))
    @patch.object(job, "_eligible_rows")
    def test_single_row_failure_does_not_abort_daily_run(
        self,
        eligible,
        _enrich_one,
        _kakao_key,
        _naver_key,
    ) -> None:
        eligible.side_effect = [
            [{"contact_key": "place:test"}],
            [{"contact_key": "place:test"}],
        ]

        result = job.run_enrichment(
            stage="phone",
            phone_provider="auto",
            max_records=1,
        )

        self.assertEqual(result, 0)

    @patch.object(
        job.naver_web_search_client,
        "key_status",
        return_value={"configured": True},
    )
    @patch.object(
        job.kakao_local_client,
        "key_status",
        return_value={"configured": True},
    )
    @patch.object(job, "_eligible_rows")
    def test_auto_provider_finishes_kakao_queue_before_naver(
        self,
        eligible,
        _kakao_key,
        _naver_key,
    ) -> None:
        eligible.side_effect = [
            [{"contact_key": "kakao:pending"}],
            [],
        ]

        result = job.run_enrichment(
            stage="phone",
            phone_provider="auto",
            max_records=1,
        )

        self.assertEqual(result, 0)
        self.assertEqual(
            eligible.call_args_list[0].args,
            (1, "phone", "kakao"),
        )
        self.assertEqual(
            eligible.call_args_list[1].args,
            (1, "phone", "kakao"),
        )

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
            },
            "phone",
            "kakao",
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
        self.assertFalse(enrich.call_args.kwargs["skip_kakao"])
        self.assertTrue(enrich.call_args.kwargs["skip_naver"])
        self.assertEqual(saved["phone_provider_stage"], "complete")

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
    def test_kakao_no_match_moves_to_naver_queue(
        self,
        enrich,
        patch_row,
    ) -> None:
        result = job._enrich_one(
            {
                "contact_key": "place:test",
                "status": "pending",
                "attempt_count": 2,
                "company_name": "미확인기업",
                "address": "경기도 수원시",
            },
            "phone",
            "kakao",
        )
        self.assertEqual(result["status"], "fallback")
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["status"], "pending")
        self.assertEqual(saved["phone_status"], "pending")
        self.assertEqual(saved["phone_provider_stage"], "naver")
        self.assertEqual(saved["attempt_count"], 3)
        self.assertIn("phone_next_check_at", saved)
        self.assertFalse(enrich.call_args.kwargs["skip_kakao"])
        self.assertTrue(enrich.call_args.kwargs["skip_naver"])

    @patch.object(job, "_patch", return_value=True)
    @patch.object(
        job,
        "enrich_company",
        return_value={"ok": True, "contacts": []},
    )
    def test_naver_no_match_completes_phone_pipeline(
        self,
        enrich,
        patch_row,
    ) -> None:
        result = job._enrich_one(
            {
                "contact_key": "place:test",
                "status": "pending",
                "phone_status": "pending",
                "phone_provider_stage": "naver",
                "company_name": "테스트기업",
                "address": "경기도 수원시",
            },
            "phone",
            "naver",
        )
        self.assertEqual(result["status"], "no_match")
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["status"], "no_match")
        self.assertEqual(saved["phone_status"], "no_match")
        self.assertEqual(saved["phone_provider_stage"], "complete")
        self.assertTrue(enrich.call_args.kwargs["skip_kakao"])
        self.assertFalse(enrich.call_args.kwargs["skip_naver"])

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
            },
            "phone",
            "naver",
        )
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["halt"])
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["phone_status"], "error")
        self.assertEqual(saved["phone_provider_stage"], "naver")
        self.assertIn("UpstreamLimitError", saved["phone_last_error"])

    @patch.object(job, "_patch", return_value=True)
    @patch.object(
        job,
        "enrich_company",
        side_effect=RuntimeError("temporary upstream failure"),
    )
    def test_naver_error_remains_retryable_in_naver_queue(
        self,
        _enrich,
        patch_row,
    ) -> None:
        result = job._enrich_one(
            {
                "contact_key": "place:retry",
                "phone_status": "pending",
                "phone_provider_stage": "naver",
                "company_name": "테스트기업",
                "address": "서울특별시 강남구",
            },
            "phone",
            "naver",
        )

        self.assertEqual(result["status"], "error")
        self.assertFalse(result["halt"])
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["phone_status"], "error")
        self.assertEqual(saved["phone_provider_stage"], "naver")
        self.assertIn("phone_next_check_at", saved)
        self.assertEqual(
            patch_row.call_args_list[-1].kwargs[
                "expected_phone_provider_stage"
            ],
            "naver",
        )


if __name__ == "__main__":
    unittest.main()
