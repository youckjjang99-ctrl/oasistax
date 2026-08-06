from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import scheduled_employment_contact_enrichment as job


class EmploymentContactEnrichmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.guard_state_patcher = patch.object(
            job.kakao_provider_runtime,
            "get_guard_state",
            return_value={
                "state": job.kakao_provider_runtime.GUARD_STATE_READY,
                "guard_generation": 0,
            },
        )
        self.guard_trip_patcher = patch.object(
            job.kakao_provider_runtime,
            "trip_guard",
            return_value=True,
        )
        self.held_work_patcher = patch.object(
            job,
            "_has_kakao_no_match_holds",
            return_value=False,
        )
        self.guard_state = self.guard_state_patcher.start()
        self.guard_trip = self.guard_trip_patcher.start()
        self.held_work = self.held_work_patcher.start()
        self.addCleanup(self.guard_state_patcher.stop)
        self.addCleanup(self.guard_trip_patcher.stop)
        self.addCleanup(self.held_work_patcher.stop)

    @patch.object(job.requests, "get")
    @patch.object(job, "CloudDatabase")
    def test_digital_selection_excludes_phone_only_sources(
        self,
        cloud_database,
        request_get,
    ) -> None:
        db = Mock()
        db.headers = {}
        db.config.timeout = 20
        db._url.return_value = "https://example.supabase.co/rest/v1/contacts"
        cloud_database.return_value = db
        response = Mock(ok=True, text="[]")
        response.json.return_value = []
        request_get.return_value = response

        job._select_rows(stage="digital", status="pending", limit=25)

        self.assertEqual(
            request_get.call_args.kwargs["params"]["source_type"],
            "not.in.(comwel_all_employers)",
        )

    @patch.object(job.requests, "get")
    @patch.object(job, "CloudDatabase")
    def test_phone_selection_keeps_phone_only_sources_eligible(
        self,
        cloud_database,
        request_get,
    ) -> None:
        db = Mock()
        db.headers = {}
        db.config.timeout = 20
        db._url.return_value = "https://example.supabase.co/rest/v1/contacts"
        cloud_database.return_value = db
        response = Mock(ok=True, text="[]")
        response.json.return_value = []
        request_get.return_value = response

        job._select_rows(
            stage="phone",
            status="pending",
            limit=25,
            phone_provider="kakao",
        )

        self.assertNotIn(
            "source_type",
            request_get.call_args.kwargs["params"],
        )
        self.assertEqual(
            request_get.call_args.kwargs["params"]["phone_last_error"],
            f"neq.{job.KAKAO_NO_MATCH_HELD}",
        )

    @patch.object(job, "CloudDatabase")
    def test_kakao_hold_check_uses_service_role_rpc(
        self,
        cloud_database,
    ) -> None:
        self.held_work_patcher.stop()
        db = Mock()
        db.rpc.return_value = False
        cloud_database.return_value = db

        self.assertFalse(job._has_kakao_no_match_holds())
        db.rpc.assert_called_once_with(
            "oasis_has_kakao_no_match_holds",
            {},
        )

    @patch.object(job, "CloudDatabase")
    def test_kakao_hold_reset_uses_service_role_rpc(
        self,
        cloud_database,
    ) -> None:
        self.held_work_patcher.stop()
        db = Mock()
        db.rpc.return_value = 2
        cloud_database.return_value = db

        job._clear_stale_kakao_no_match_holds()
        db.rpc.assert_called_once_with(
            "oasis_clear_kakao_no_match_holds",
            {},
        )

    @patch.object(job, "CloudDatabase")
    def test_kakao_hold_check_accepts_wrapped_rpc_response(
        self,
        cloud_database,
    ) -> None:
        self.held_work_patcher.stop()
        db = Mock()
        db.rpc.return_value = {"oasis_has_kakao_no_match_holds": False}
        cloud_database.return_value = db

        self.assertFalse(job._has_kakao_no_match_holds())

    @patch.object(job, "CloudDatabase")
    def test_kakao_hold_check_rejects_ambiguous_rpc_response(
        self,
        cloud_database,
    ) -> None:
        self.held_work_patcher.stop()
        db = Mock()
        db.rpc.return_value = {"first": False, "second": False}
        cloud_database.return_value = db

        with self.assertRaisesRegex(RuntimeError, "invalid result"):
            job._has_kakao_no_match_holds()

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
    @patch.object(job, "_enrich_one", side_effect=RuntimeError("db timeout"))
    @patch.object(job, "_eligible_rows")
    def test_single_row_failure_does_not_abort_daily_run(
        self,
        eligible,
        _enrich_one,
        _naver_key,
    ) -> None:
        eligible.side_effect = [
            [{"contact_key": "place:test"}],
            [{"contact_key": "place:test"}],
        ]

        result = job.run_enrichment(
            stage="phone",
            phone_provider="naver",
            max_records=1,
        )

        self.assertEqual(result, 0)

    @patch.object(
        job.naver_web_search_client,
        "key_status",
        return_value={"configured": True},
    )
    @patch.object(job, "_eligible_rows")
    def test_auto_provider_finishes_kakao_queue_before_naver(
        self,
        eligible,
        _naver_key,
    ) -> None:
        eligible.side_effect = [
            [{"contact_key": "kakao:pending"}],
            [],
        ]

        with patch.multiple(
            job.kakao_provider_runtime,
            acquire_lease=Mock(return_value=True),
            release_lease=Mock(return_value=True),
            renew_lease=Mock(return_value=True),
            get_daily_usage=Mock(
                return_value={"request_count": 1, "blocked_until": ""}
            ),
            test_connection_and_record=Mock(
                return_value={
                    "ok": True,
                    "category": "CONNECTED",
                    "request_count": 1,
                }
            ),
            reserve_quota=Mock(
                return_value={
                    "request_count": 3,
                    "reserved": True,
                    "blocked_until": "",
                }
            ),
            reconcile_usage=Mock(
                return_value={"request_count": 1, "blocked_until": ""}
            ),
        ), patch.object(
            job,
            "_clear_stale_kakao_no_match_holds",
            return_value=None,
        ):
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
            "provider_results": {
                "kakao": {
                    "outcome": "matched",
                    "request_count": 1,
                }
            },
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
        return_value={
            "ok": True,
            "provider_results": {
                "kakao": {
                    "outcome": "no_match",
                    "request_count": 2,
                }
            },
            "contacts": [],
        },
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
        self.assertEqual(result["status"], "no_match")
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["status"], "pending")
        self.assertEqual(saved["phone_status"], "pending")
        self.assertEqual(saved["phone_provider_stage"], "naver")
        self.assertEqual(saved["attempt_count"], 3)
        self.assertIn("phone_next_check_at", saved)
        self.assertFalse(enrich.call_args.kwargs["skip_kakao"])
        self.assertTrue(enrich.call_args.kwargs["skip_naver"])

    @patch.object(job, "_patch", return_value=True)
    @patch.object(job, "enrich_company")
    def test_existing_phone_does_not_mask_a_fresh_kakao_no_match(
        self,
        enrich,
        patch_row,
    ) -> None:
        enrich.return_value = {
            "ok": True,
            "provider_results": {
                "kakao": {
                    "outcome": "no_match",
                    "request_count": 2,
                }
            },
            "contacts": [],
        }

        result = job._enrich_one(
            {
                "contact_key": "place:existing-phone",
                "status": "matched",
                "phone_status": "pending",
                "phone_provider_stage": "kakao",
                "mobile_phone": "-".join(("010", "0000", "0000")),
                "company_name": "test",
                "address": "test",
            },
            "phone",
            "kakao",
        )

        self.assertEqual(result["outcome"], "no_match")
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["phone_status"], "pending")
        self.assertEqual(saved["phone_provider_stage"], "naver")

    @patch.object(job, "_patch", return_value=True)
    @patch.object(job, "enrich_company")
    def test_unknown_kakao_outcome_is_not_treated_as_no_match(
        self,
        enrich,
        patch_row,
    ) -> None:
        enrich.return_value = {
            "ok": True,
            "provider_results": {
                "kakao": {
                    "outcome": "unexpected",
                    "request_count": 1,
                }
            },
            "contacts": [],
        }

        result = job._enrich_one(
            {
                "contact_key": "place:unknown-outcome",
                "phone_status": "pending",
                "phone_provider_stage": "kakao",
                "company_name": "test",
            },
            "phone",
            "kakao",
        )

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["safe_error_code"], "INVALID_JSON")
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["phone_provider_stage"], "kakao")

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
        self.assertEqual(saved["phone_last_error"], "HTTP_429")

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

    @patch.object(job, "_patch", return_value=True)
    @patch.object(job, "enrich_company")
    def test_kakao_provider_error_stays_in_kakao_with_safe_code(
        self,
        enrich,
        patch_row,
    ) -> None:
        enrich.return_value = {
            "ok": False,
            "status": "provider_error",
            "safe_error_code": "HTTP_500",
            "provider_results": {
                "kakao": {
                    "outcome": "error",
                    "safe_error_code": "HTTP_500",
                    "request_count": 2,
                }
            },
            "contacts": [],
        }

        result = job._enrich_one(
            {
                "contact_key": "place:error",
                "phone_status": "pending",
                "phone_provider_stage": "kakao",
                "company_name": "test",
                "address": "test",
            },
            "phone",
            "kakao",
        )

        self.assertEqual(result["outcome"], "error")
        self.assertTrue(result["provider_error"])
        self.assertEqual(result["safe_error_code"], "HTTP_500")
        self.assertEqual(result["request_count"], 2)
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["phone_provider_stage"], "kakao")
        self.assertEqual(saved["phone_status"], "error")
        self.assertEqual(saved["phone_last_error"], "HTTP_500")
        self.assertNotIn("response", str(saved).lower())

    @patch.object(job, "_patch", return_value=True)
    @patch.object(job, "enrich_company")
    def test_kakao_no_match_can_be_held_until_guard_passes(
        self,
        enrich,
        patch_row,
    ) -> None:
        enrich.return_value = {
            "ok": True,
            "contacts": [],
            "provider_results": {
                "kakao": {
                    "outcome": "no_match",
                    "safe_error_code": "",
                    "request_count": 2,
                }
            },
        }

        result = job._enrich_one(
            {
                "contact_key": "place:held",
                "phone_status": "pending",
                "phone_provider_stage": "kakao",
                "company_name": "test",
                "address": "test",
            },
            "phone",
            "kakao",
            hold_kakao_no_match=True,
        )

        self.assertEqual(result["outcome"], "no_match")
        self.assertTrue(result["held"])
        self.assertEqual(result["request_count"], 2)
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["phone_provider_stage"], "kakao")
        self.assertEqual(saved["phone_status"], "pending")
        self.assertEqual(
            saved["phone_last_error"],
            job.KAKAO_NO_MATCH_HELD,
        )

    @patch.object(job, "_patch", return_value=True)
    @patch.object(job, "enrich_company")
    def test_cached_kakao_result_does_not_increment_request_usage(
        self,
        enrich,
        _patch_row,
    ) -> None:
        enrich.return_value = {
            "ok": True,
            "cache_hit": True,
            "contacts": [],
            "provider_results": {
                "kakao": {
                    "outcome": "no_match",
                    "request_count": 2,
                }
            },
        }

        result = job._enrich_one(
            {
                "contact_key": "place:cached",
                "phone_status": "pending",
                "phone_provider_stage": "kakao",
                "company_name": "test",
            },
            "phone",
            "kakao",
        )

        self.assertEqual(result["outcome"], "no_match")
        self.assertEqual(result["request_count"], 0)

    @patch.object(job, "_patch", return_value=True)
    @patch.object(job, "enrich_company")
    @patch.object(
        job,
        "_now",
        return_value=datetime(2026, 8, 4, 3, 0, tzinfo=timezone.utc),
    )
    def test_kakao_429_waits_until_next_kst_quota_reset(
        self,
        _now,
        enrich,
        patch_row,
    ) -> None:
        enrich.return_value = {
            "ok": False,
            "safe_error_code": "HTTP_429",
            "provider_results": {
                "kakao": {
                    "outcome": "error",
                    "safe_error_code": "HTTP_429",
                    "request_count": 1,
                }
            },
            "contacts": [],
        }

        result = job._enrich_one(
            {
                "contact_key": "place:quota",
                "phone_status": "pending",
                "phone_provider_stage": "kakao",
                "company_name": "test",
            },
            "phone",
            "kakao",
        )

        self.assertTrue(result["halt"])
        saved = patch_row.call_args_list[-1].args[1]
        self.assertEqual(saved["phone_last_error"], "HTTP_429")
        self.assertEqual(
            saved["phone_next_check_at"],
            "2026-08-04T15:00:00+00:00",
        )

    @patch.object(job, "_patch")
    @patch.object(job, "enrich_company")
    def test_kakao_429_survives_error_state_patch_failure(
        self,
        enrich,
        patch_row,
    ) -> None:
        patch_row.side_effect = [True, RuntimeError("database unavailable")]
        enrich.return_value = {
            "ok": False,
            "safe_error_code": "HTTP_429",
            "provider_results": {
                "kakao": {
                    "outcome": "error",
                    "safe_error_code": "HTTP_429",
                    "request_count": 1,
                }
            },
            "contacts": [],
        }

        result = job._enrich_one(
            {
                "contact_key": "place:quota-patch-failure",
                "phone_status": "pending",
                "phone_provider_stage": "kakao",
                "company_name": "test",
            },
            "phone",
            "kakao",
        )

        self.assertEqual(result["safe_error_code"], "HTTP_429")
        self.assertEqual(result["request_count"], 1)
        self.assertTrue(result["halt"])
        self.assertTrue(result["fatal"])

    @patch.object(job, "_eligible_rows")
    @patch.object(job, "_clear_stale_kakao_no_match_holds")
    def test_failed_preflight_stops_before_queue_selection(
        self,
        clear_holds,
        eligible,
    ) -> None:
        with patch.multiple(
            job.kakao_provider_runtime,
            acquire_lease=Mock(return_value=True),
            release_lease=Mock(return_value=True),
            get_daily_usage=Mock(
                return_value={"request_count": 0, "blocked_until": ""}
            ),
            test_connection_and_record=Mock(
                return_value={
                    "ok": False,
                    "category": "AUTH_ERROR",
                    "safe_error_code": "HTTP_401",
                    "request_count": 1,
                }
            ),
        ):
            result = job.run_enrichment(
                stage="phone",
                phone_provider="kakao",
            )

        self.assertEqual(result, job.EXIT_PREFLIGHT_FAILED)
        eligible.assert_not_called()
        clear_holds.assert_not_called()

    @patch.object(job, "_eligible_rows")
    def test_lease_conflict_stops_before_preflight_or_queue(
        self,
        eligible,
    ) -> None:
        preflight = Mock()
        with patch.multiple(
            job.kakao_provider_runtime,
            acquire_lease=Mock(return_value=False),
            release_lease=Mock(return_value=True),
            test_connection_and_record=preflight,
        ):
            result = job.run_enrichment(
                stage="phone",
                phone_provider="kakao",
            )

        self.assertEqual(result, job.EXIT_LEASE_UNAVAILABLE)
        preflight.assert_not_called()
        eligible.assert_not_called()

    @patch.object(job, "_eligible_rows")
    def test_blocked_guard_stops_before_hold_check_preflight_and_queue(
        self,
        eligible,
    ) -> None:
        self.guard_state.return_value = {
            "state": job.kakao_provider_runtime.GUARD_STATE_BLOCKED,
            "guard_generation": 3,
            "guard_reason": (
                job.kakao_provider_runtime.GUARD_REASON_INITIAL_ZERO_MATCH_RATE
            ),
        }
        preflight = Mock()
        with patch.multiple(
            job.kakao_provider_runtime,
            acquire_lease=Mock(return_value=True),
            release_lease=Mock(return_value=True),
            test_connection_and_record=preflight,
        ):
            result = job.run_enrichment(
                stage="phone",
                phone_provider="kakao",
            )

        self.assertEqual(result, job.EXIT_PROVIDER_GUARD)
        self.held_work.assert_not_called()
        preflight.assert_not_called()
        eligible.assert_not_called()

    @patch.object(job, "_eligible_rows")
    def test_ready_guard_does_not_run_an_extra_hold_probe(
        self,
        eligible,
    ) -> None:
        self.held_work.side_effect = RuntimeError("unavailable")
        eligible.return_value = []
        preflight = Mock()
        with patch.multiple(
            job.kakao_provider_runtime,
            acquire_lease=Mock(return_value=True),
            release_lease=Mock(return_value=True),
            get_daily_usage=Mock(
                return_value={"request_count": 0, "blocked_until": ""}
            ),
            test_connection_and_record=preflight,
        ):
            preflight.return_value = {"ok": True, "category": "CONNECTED"}
            result = job.run_enrichment(
                stage="phone",
                phone_provider="kakao",
            )

        self.assertEqual(result, 0)
        self.held_work.assert_not_called()
        self.guard_trip.assert_not_called()
        preflight.assert_called_once()
        eligible.assert_called()

    @patch.object(job, "_eligible_rows")
    @patch.object(job, "_clear_stale_kakao_no_match_holds")
    def test_approved_resume_preflight_failure_keeps_holds_and_approval(
        self,
        clear_holds,
        eligible,
    ) -> None:
        self.guard_state.return_value = {
            "state": (
                job.kakao_provider_runtime.GUARD_STATE_RESUME_APPROVED
            ),
            "guard_generation": 4,
        }
        consume = Mock(return_value=True)
        with patch.multiple(
            job.kakao_provider_runtime,
            acquire_lease=Mock(return_value=True),
            release_lease=Mock(return_value=True),
            get_daily_usage=Mock(
                return_value={"request_count": 0, "blocked_until": ""}
            ),
            test_connection_and_record=Mock(
                return_value={
                    "ok": False,
                    "category": "AUTH_ERROR",
                    "safe_error_code": "HTTP_401",
                }
            ),
            consume_guard_resume=consume,
        ):
            result = job.run_enrichment(
                stage="phone",
                phone_provider="kakao",
            )

        self.assertEqual(result, job.EXIT_PREFLIGHT_FAILED)
        clear_holds.assert_not_called()
        consume.assert_not_called()
        eligible.assert_not_called()

    @patch.object(job, "_eligible_rows", return_value=[])
    def test_approved_resume_consumes_before_queue(
        self,
        eligible,
    ) -> None:
        events: list[str] = []
        self.guard_state.return_value = {
            "state": (
                job.kakao_provider_runtime.GUARD_STATE_RESUME_APPROVED
            ),
            "guard_generation": 5,
        }
        def consume(*_args, **_kwargs):
            events.append("consume")
            return True

        eligible.side_effect = lambda *_args, **_kwargs: (
            events.append("queue") or []
        )
        with patch.multiple(
            job.kakao_provider_runtime,
            acquire_lease=Mock(return_value=True),
            release_lease=Mock(return_value=True),
            get_daily_usage=Mock(
                return_value={"request_count": 1, "blocked_until": ""}
            ),
            test_connection_and_record=Mock(
                return_value={"ok": True, "category": "CONNECTED"}
            ),
            consume_guard_resume=Mock(side_effect=consume),
        ):
            result = job.run_enrichment(
                stage="phone",
                phone_provider="kakao",
            )

        self.assertEqual(result, 0)
        self.assertEqual(events, ["consume", "queue"])

    @patch.object(job, "_release_kakao_no_match_holds")
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(job.kakao_provider_runtime, "reserve_quota")
    @patch.object(job, "_enrich_one")
    @patch.object(job, "_eligible_rows")
    def test_actual_kakao_request_count_is_persisted(
        self,
        eligible,
        enrich_one,
        reserve_quota,
        reconcile_usage,
        _renew,
        release_holds,
    ) -> None:
        eligible.side_effect = [[{"contact_key": "place:1"}], []]
        enrich_one.return_value = {
            "status": "no_match",
            "outcome": "no_match",
            "request_count": 2,
            "held": True,
            "contact_key": "place:1",
        }
        reserve_quota.return_value = {
            "request_count": 3,
            "reserved": True,
            "blocked_until": "",
            "quota_date": "2026-08-05",
        }
        reconcile_usage.return_value = {
            "request_count": 3,
            "blocked_until": "",
        }

        result = job._run_provider_batches(
            stage="phone",
            phone_provider="kakao",
            workers=1,
            batch_size=20,
            max_records=10,
            max_requests=85000,
            daily_request_count=1,
            lease_token="lease",
        )

        self.assertEqual(result, 0)
        reserve_quota.assert_called_once_with(2, 85000)
        reconcile_usage.assert_called_once_with(
            2,
            2,
            "",
            reservation_date="2026-08-05",
        )
        release_holds.assert_called_once_with(["place:1"])

    @patch.object(job, "_release_kakao_no_match_holds")
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(job.kakao_provider_runtime, "reserve_quota")
    @patch.object(job, "_enrich_one")
    @patch.object(job, "_eligible_rows")
    def test_ten_consecutive_provider_errors_stop_job(
        self,
        eligible,
        enrich_one,
        reserve_quota,
        reconcile_usage,
        _renew,
        release_holds,
    ) -> None:
        eligible.return_value = [
            {"contact_key": f"place:{index}"} for index in range(10)
        ]
        enrich_one.return_value = {
            "status": "error",
            "outcome": "error",
            "request_count": 1,
            "provider_error": True,
            "safe_error_code": "HTTP_500",
        }
        reserve_quota.return_value = {
            "request_count": 21,
            "reserved": True,
            "blocked_until": "",
        }
        reconcile_usage.return_value = {
            "request_count": 11,
            "blocked_until": "",
        }

        result = job._run_provider_batches(
            stage="phone",
            phone_provider="kakao",
            workers=10,
            batch_size=200,
            max_records=100,
            max_requests=85000,
            daily_request_count=1,
            lease_token="lease",
        )

        self.assertEqual(result, job.EXIT_PROVIDER_GUARD)
        self.assertEqual(enrich_one.call_count, 10)
        release_holds.assert_not_called()
        self.guard_trip.assert_called_once()
        self.assertEqual(
            self.guard_trip.call_args.args[2],
            (
                job.kakao_provider_runtime.GUARD_REASON_CONSECUTIVE_PROVIDER_ERRORS
            ),
        )

    @patch.object(job, "_release_kakao_no_match_holds")
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(job.kakao_provider_runtime, "reserve_quota")
    @patch.object(job, "_enrich_one")
    @patch.object(job, "_eligible_rows")
    def test_http_429_blocks_more_batches_and_records_quota_code(
        self,
        eligible,
        enrich_one,
        reserve_quota,
        reconcile_usage,
        _renew,
        release_holds,
    ) -> None:
        eligible.return_value = [
            {"contact_key": "place:no-match"},
            {"contact_key": "place:quota"},
        ]

        def provider_result(row, *_args, **_kwargs):
            if row["contact_key"] == "place:no-match":
                return {
                    "status": "no_match",
                    "outcome": "no_match",
                    "request_count": 2,
                    "held": True,
                    "contact_key": row["contact_key"],
                }
            return {
                "status": "error",
                "outcome": "error",
                "request_count": 1,
                "provider_error": True,
                "safe_error_code": "HTTP_429",
                "halt": True,
            }

        enrich_one.side_effect = provider_result
        reserve_quota.return_value = {
            "request_count": 5,
            "reserved": True,
            "blocked_until": "",
            "quota_date": "2026-08-05",
        }
        reconcile_usage.return_value = {
            "request_count": 4,
            "blocked_until": "2026-08-04T15:00:00+00:00",
        }

        result = job._run_provider_batches(
            stage="phone",
            phone_provider="kakao",
            workers=1,
            batch_size=200,
            max_records=100,
            max_requests=85000,
            daily_request_count=1,
            lease_token="lease",
        )

        self.assertEqual(result, job.EXIT_PROVIDER_QUOTA)
        self.assertEqual(eligible.call_count, 1)
        reserve_quota.assert_called_once_with(4, 85000)
        reconcile_usage.assert_called_once_with(
            4,
            3,
            "HTTP_429",
            reservation_date="2026-08-05",
        )
        release_holds.assert_not_called()

    @patch.object(job, "_release_kakao_no_match_holds")
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(job.kakao_provider_runtime, "reserve_quota")
    @patch.object(job, "_enrich_one")
    @patch.object(job, "_eligible_rows")
    def test_initial_all_no_match_guard_retains_kakao_rows(
        self,
        eligible,
        enrich_one,
        reserve_quota,
        reconcile_usage,
        _renew,
        release_holds,
    ) -> None:
        eligible.return_value = [
            {"contact_key": f"place:{index}"} for index in range(3)
        ]
        enrich_one.side_effect = lambda row, *_args, **_kwargs: {
            "status": "no_match",
            "outcome": "no_match",
            "request_count": 2,
            "held": True,
            "contact_key": row["contact_key"],
        }
        reserve_quota.return_value = {
            "request_count": 7,
            "reserved": True,
            "blocked_until": "",
        }
        reconcile_usage.return_value = {
            "request_count": 7,
            "blocked_until": "",
        }

        with patch.object(job, "KAKAO_INITIAL_ZERO_MATCH_LIMIT", 3):
            result = job._run_provider_batches(
                stage="phone",
                phone_provider="kakao",
                workers=3,
                batch_size=200,
                max_records=100,
                max_requests=85000,
                daily_request_count=1,
                lease_token="lease",
            )

        self.assertEqual(result, job.EXIT_PROVIDER_GUARD)
        release_holds.assert_not_called()
        self.guard_trip.assert_called_once()
        self.assertEqual(
            self.guard_trip.call_args.args[2],
            job.kakao_provider_runtime.GUARD_REASON_INITIAL_ZERO_MATCH_RATE,
        )

    @patch.object(job, "_release_kakao_no_match_holds")
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(job.kakao_provider_runtime, "reserve_quota")
    @patch.object(job, "_enrich_one")
    @patch.object(job, "_eligible_rows")
    def test_rolling_zero_match_guard_retains_recent_rows(
        self,
        eligible,
        enrich_one,
        reserve_quota,
        reconcile_usage,
        _renew,
        release_holds,
    ) -> None:
        eligible.side_effect = [
            [{"contact_key": f"matched:{index}"} for index in range(3)],
            [{"contact_key": f"held:{index}"} for index in range(5)],
        ]

        def outcome(row, *_args, **_kwargs):
            matched = str(row["contact_key"]).startswith("matched:")
            return {
                "status": "matched" if matched else "no_match",
                "outcome": "matched" if matched else "no_match",
                "request_count": 1,
                "held": not matched,
                "contact_key": row["contact_key"],
            }

        enrich_one.side_effect = outcome
        reserve_quota.side_effect = [
            {
                "request_count": 7,
                "reserved": True,
                "blocked_until": "",
            },
            {
                "request_count": 14,
                "reserved": True,
                "blocked_until": "",
            },
        ]
        reconcile_usage.side_effect = [
            {"request_count": 4, "blocked_until": ""},
            {"request_count": 9, "blocked_until": ""},
        ]

        with patch.object(job, "KAKAO_INITIAL_ZERO_MATCH_LIMIT", 3), \
             patch.object(job, "KAKAO_ROLLING_ZERO_MATCH_LIMIT", 5):
            result = job._run_provider_batches(
                stage="phone",
                phone_provider="kakao",
                workers=5,
                batch_size=200,
                max_records=100,
                max_requests=85000,
                daily_request_count=1,
                lease_token="lease",
            )

        self.assertEqual(result, job.EXIT_PROVIDER_GUARD)
        release_holds.assert_not_called()
        self.guard_trip.assert_called_once()
        self.assertEqual(
            self.guard_trip.call_args.args[2],
            job.kakao_provider_runtime.GUARD_REASON_ROLLING_ZERO_MATCH_RATE,
        )

    @patch.object(job, "_release_kakao_no_match_holds")
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(job.kakao_provider_runtime, "reserve_quota")
    @patch.object(job, "_enrich_one")
    @patch.object(job, "_eligible_rows")
    def test_request_limit_reserves_two_calls_per_company(
        self,
        eligible,
        enrich_one,
        reserve_quota,
        reconcile_usage,
        _renew,
        release_holds,
    ) -> None:
        eligible.return_value = [{"contact_key": "place:1"}]
        enrich_one.return_value = {
            "status": "no_match",
            "outcome": "no_match",
            "request_count": 2,
            "held": True,
            "contact_key": "place:1",
        }
        reserve_quota.return_value = {
            "request_count": 10,
            "reserved": True,
            "blocked_until": "",
            "quota_date": "2026-08-05",
        }
        reconcile_usage.return_value = {
            "request_count": 10,
            "blocked_until": "",
        }

        result = job._run_provider_batches(
            stage="phone",
            phone_provider="kakao",
            workers=10,
            batch_size=200,
            max_records=100,
            max_requests=10,
            daily_request_count=8,
            lease_token="lease",
        )

        self.assertEqual(result, job.EXIT_DAILY_QUOTA)
        self.assertEqual(eligible.call_args.args[0], 1)
        reserve_quota.assert_called_once_with(2, 10)
        reconcile_usage.assert_called_once_with(
            2,
            2,
            "",
            reservation_date="2026-08-05",
        )
        release_holds.assert_called_once_with(["place:1"])

    @patch.object(job, "_release_kakao_no_match_holds")
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(
        job.kakao_provider_runtime,
        "reserve_quota",
        return_value={
            "request_count": 84_999,
            "reserved": False,
            "blocked_until": "",
            "last_safe_error_code": "",
        },
    )
    @patch.object(job, "_enrich_one")
    @patch.object(
        job,
        "_eligible_rows",
        return_value=[{"contact_key": "place:1"}],
    )
    def test_atomic_quota_denial_stops_before_provider_call(
        self,
        _eligible,
        enrich_one,
        reserve_quota,
        reconcile_usage,
        release_holds,
    ) -> None:
        result = job._run_provider_batches(
            stage="phone",
            phone_provider="kakao",
            workers=1,
            batch_size=1,
            max_records=1,
            max_requests=85_000,
            daily_request_count=84_998,
            lease_token="lease",
        )

        self.assertEqual(result, job.EXIT_DAILY_QUOTA)
        reserve_quota.assert_called_once_with(2, 85_000)
        enrich_one.assert_not_called()
        reconcile_usage.assert_not_called()
        release_holds.assert_not_called()

    @patch.object(job, "_release_kakao_no_match_holds")
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(
        job.kakao_provider_runtime,
        "reserve_quota",
        return_value={
            "request_count": 3,
            "reserved": True,
            "blocked_until": "",
            "quota_date": "2026-08-05",
        },
    )
    @patch.object(job, "_enrich_one", side_effect=RuntimeError("worker lost"))
    @patch.object(
        job,
        "_eligible_rows",
        return_value=[{"contact_key": "place:1"}],
    )
    def test_unknown_worker_failure_keeps_full_quota_reservation(
        self,
        _eligible,
        _enrich_one,
        _reserve_quota,
        reconcile_usage,
        _renew,
        release_holds,
    ) -> None:
        reconcile_usage.return_value = {
            "request_count": 3,
            "blocked_until": "",
        }

        result = job._run_provider_batches(
            stage="phone",
            phone_provider="kakao",
            workers=1,
            batch_size=1,
            max_records=1,
            max_requests=85_000,
            daily_request_count=1,
            lease_token="lease",
        )

        self.assertEqual(result, 0)
        reconcile_usage.assert_called_once_with(
            2,
            2,
            "PROVIDER_ERROR",
            reservation_date="2026-08-05",
        )
        release_holds.assert_not_called()


if __name__ == "__main__":
    unittest.main()
