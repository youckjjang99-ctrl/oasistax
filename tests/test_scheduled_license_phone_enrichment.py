from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import scheduled_license_phone_enrichment as job


def _test_phone(*parts: str) -> str:
    return "-".join(parts)


_LANDLINE = _test_phone("02", "123", "4567")
_MOBILE = _test_phone("010", "1234", "5678")


class LicenseHeldWorkQueryTests(unittest.TestCase):
    @patch.object(job.requests, "get")
    def test_held_work_check_reads_only_an_internal_identifier(self, get):
        database = Mock()
        database._url.return_value = "https://example.invalid/table"
        database.headers = {"Authorization": "redacted"}
        database.config.timeout = 30
        response = Mock(ok=True, text="[{}]")
        response.json.return_value = [{}]
        get.return_value = response

        self.assertTrue(job._has_kakao_no_match_holds(database=database))

        params = get.call_args.kwargs["params"]
        self.assertEqual(params["select"], "id")
        self.assertEqual(
            params["phone_enrichment_error"],
            f"eq.{job.KAKAO_NO_MATCH_HELD}",
        )
        self.assertEqual(params["limit"], "1")


class ScheduledLicensePhoneEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.guard_state_patcher = patch.object(
            job.kakao_provider_runtime,
            "get_guard_state",
            return_value={
                "state": job.kakao_provider_runtime.GUARD_STATE_READY,
                "guard_generation": 0,
                "reason": "",
            },
        )
        self.guard_state = self.guard_state_patcher.start()
        self.addCleanup(self.guard_state_patcher.stop)
        self.held_work_patcher = patch.object(
            job,
            "_has_kakao_no_match_holds",
            return_value=False,
        )
        self.held_work = self.held_work_patcher.start()
        self.addCleanup(self.held_work_patcher.stop)

    @patch.object(job, "_patch_if_phone_empty", return_value=True)
    @patch.object(job.kakao_local_client, "search_company")
    def test_saves_only_confident_phone(self, search, patch_phone):
        search.return_value = {
            "ok": True,
            "outcome": "matched",
            "status": "MATCHED",
            "safe_error_code": "",
            "request_count": 2,
            "candidates": [
                {
                    "phone": _LANDLINE,
                    "confidence": 91,
                    "source_url": "https://place.map.kakao.com/1",
                }
            ],
        }
        result = job._enrich_one(
            {
                "source_key": "source:1",
                "company_name": "sample company",
                "address": "sample address",
            },
            85,
        )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["request_count"], 2)
        values = patch_phone.call_args.args[1]
        self.assertEqual(values["phone"], _LANDLINE)
        self.assertEqual(values["phone_source"], "kakao_local")
        self.assertEqual(values["phone_confidence"], 91)

    @patch.object(job, "_patch_if_phone_empty", return_value=True)
    @patch.object(job.kakao_local_client, "search_company")
    def test_rejects_phone_below_threshold(self, search, patch_phone):
        search.return_value = {
            "ok": True,
            "outcome": "no_match",
            "status": "NO_MATCH",
            "safe_error_code": "",
            "request_count": 1,
            "candidates": [
                {
                    "phone": _MOBILE,
                    "confidence": 84,
                    "source_url": "https://place.map.kakao.com/2",
                }
            ],
        }
        result = job._enrich_one(
            {
                "source_key": "source:2",
                "company_name": "sample company",
                "address": "sample address",
            },
            85,
        )
        self.assertEqual(result["status"], "no_match")
        values = patch_phone.call_args.args[1]
        self.assertNotIn("phone", values)
        self.assertEqual(values["phone_enrichment_status"], "pending")
        self.assertEqual(
            values["phone_enrichment_error"],
            job.KAKAO_NO_MATCH_HELD,
        )

    @patch.object(job, "_patch_if_phone_empty", return_value=True)
    @patch.object(job.kakao_local_client, "search_company")
    def test_provider_error_persists_only_safe_code(self, search, patch_phone):
        search.return_value = {
            "ok": False,
            "outcome": "error",
            "status": "HTTP_500",
            "safe_error_code": "HTTP_500",
            "message": "sensitive upstream response",
            "request_count": 2,
            "candidates": [],
        }
        result = job._enrich_one(
            {
                "source_key": "source:3",
                "company_name": "sample company",
                "address": "sample address",
            },
            85,
        )
        self.assertTrue(result["provider_error"])
        self.assertEqual(result["safe_error_code"], "HTTP_500")
        self.assertEqual(result["request_count"], 2)
        values = patch_phone.call_args.args[1]
        self.assertEqual(values["phone_enrichment_error"], "HTTP_500")
        self.assertNotIn("sensitive", str(values))

    @patch.object(job, "_patch_if_phone_empty", return_value=True)
    @patch.object(job.kakao_local_client, "search_company")
    def test_unknown_error_value_is_reduced_to_provider_error(
        self,
        search,
        patch_phone,
    ):
        search.return_value = {
            "ok": False,
            "safe_error_code": "raw upstream body",
            "request_count": 1,
        }
        result = job._enrich_one(
            {
                "source_key": "source:4",
                "company_name": "sample company",
                "address": "sample address",
            },
            85,
        )
        self.assertEqual(result["safe_error_code"], "INVALID_JSON")
        values = patch_phone.call_args.args[1]
        self.assertEqual(values["phone_enrichment_error"], "INVALID_JSON")

    @patch.object(job, "_patch_if_phone_empty", return_value=True)
    @patch.object(job.kakao_local_client, "search_company")
    def test_inconsistent_success_becomes_invalid_json_error(
        self,
        search,
        patch_phone,
    ):
        search.return_value = {
            "ok": True,
            "outcome": "no_match",
            "status": "NO_MATCH",
            "safe_error_code": "",
            "request_count": 1,
            "candidates": [
                {
                    "phone": _LANDLINE,
                    "confidence": 95,
                    "source_url": "https://place.map.kakao.com/3",
                }
            ],
        }

        result = job._enrich_one(
            {
                "source_key": "source:5",
                "company_name": "sample company",
                "address": "sample address",
            },
            85,
        )

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["safe_error_code"], "INVALID_JSON")
        values = patch_phone.call_args.args[1]
        self.assertEqual(values["phone_enrichment_error"], "INVALID_JSON")

    @patch.object(job, "_patch_if_phone_empty", return_value=True)
    def test_held_no_match_release_is_compare_and_set(self, patch_phone):
        job._release_kakao_no_match_holds(["source:held", "source:held"])

        patch_phone.assert_called_once_with(
            "source:held",
            {
                "phone_enrichment_status": "no_match",
                "phone_enrichment_error": "",
            },
            expected_status="pending",
            expected_error=job.KAKAO_NO_MATCH_HELD,
        )

    def test_daily_safe_limit_stays_inside_required_range(self):
        self.assertEqual(job._daily_safe_request_limit(1), 85_000)
        self.assertEqual(job._daily_safe_request_limit(87_000), 87_000)
        self.assertEqual(job._daily_safe_request_limit(100_000), 90_000)

    @patch.object(job.kakao_provider_runtime, "test_connection_and_record")
    @patch.object(job.kakao_provider_runtime, "acquire_lease", return_value=False)
    @patch.object(job, "CloudDatabase", return_value=Mock())
    def test_duplicate_job_stops_before_preflight(
        self,
        _database,
        _acquire,
        preflight,
    ):
        self.assertEqual(job.run_enrichment(max_records=1), 2)
        preflight.assert_not_called()

    @patch.object(job.kakao_provider_runtime, "test_connection_and_record")
    @patch.object(
        job.kakao_provider_runtime,
        "release_lease",
        return_value=True,
    )
    @patch.object(
        job.kakao_provider_runtime,
        "acquire_lease",
        return_value=True,
    )
    @patch.object(job, "CloudDatabase", return_value=Mock())
    def test_blocked_guard_stops_after_lease_before_preflight(
        self,
        _database,
        acquire,
        release,
        preflight,
    ):
        self.guard_state.return_value = {
            "state": job.kakao_provider_runtime.GUARD_STATE_BLOCKED,
            "guard_generation": 3,
            "reason": "INITIAL_ZERO_MATCH",
        }

        self.assertEqual(job.run_enrichment(max_records=1), 3)
        acquire.assert_called_once()
        release.assert_called_once()
        preflight.assert_not_called()

    @patch.object(job.kakao_provider_runtime, "release_lease", return_value=True)
    @patch.object(
        job.kakao_provider_runtime,
        "trip_guard",
        return_value=True,
    )
    @patch.object(job.kakao_provider_runtime, "test_connection_and_record")
    @patch.object(job.kakao_provider_runtime, "acquire_lease")
    @patch.object(job, "CloudDatabase", return_value=Mock())
    def test_orphaned_holds_trip_guard_after_lease_before_preflight(
        self,
        database,
        acquire,
        preflight,
        trip_guard,
        _release,
    ):
        acquire.return_value = True
        self.guard_state.return_value = {
            "state": job.kakao_provider_runtime.GUARD_STATE_READY,
            "guard_generation": 0,
            "reason": "",
        }
        self.held_work.return_value = True

        self.assertEqual(job.run_enrichment(max_records=1), 3)
        trip_guard.assert_called_once()
        self.assertEqual(
            trip_guard.call_args.args[2],
            job.kakao_provider_runtime.GUARD_REASON_ORPHANED_HOLDS,
        )
        self.assertIs(trip_guard.call_args.kwargs["database"], database.return_value)
        acquire.assert_called_once()
        preflight.assert_not_called()

    def test_resume_approval_consumed_after_preflight_and_hold_reset(self):
        database = Mock()
        events: list[str] = []
        self.guard_state.return_value = {
            "state": job.kakao_provider_runtime.GUARD_STATE_RESUME_APPROVED,
            "guard_generation": 7,
            "reason": "INITIAL_ZERO_MATCH",
        }

        with (
            patch.object(job, "CloudDatabase", return_value=database),
            patch.object(
                job.kakao_provider_runtime,
                "acquire_lease",
                return_value=True,
            ),
            patch.object(
                job.kakao_provider_runtime,
                "release_lease",
                return_value=True,
            ),
            patch.object(
                job.kakao_provider_runtime,
                "get_daily_usage",
                return_value={"request_count": 0, "blocked_until": ""},
            ),
            patch.object(
                job.kakao_provider_runtime,
                "test_connection_and_record",
                side_effect=lambda **_kwargs: (
                    events.append("preflight")
                    or {
                        "ok": True,
                        "category": "CONNECTED",
                        "safe_error_code": "",
                    }
                ),
            ),
            patch.object(
                job,
                "_reset_kakao_no_match_holds",
                side_effect=lambda **_kwargs: events.append("reset"),
            ),
            patch.object(
                job.kakao_provider_runtime,
                "consume_guard_resume",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("consume") or True
                ),
            ) as consume,
            patch.object(
                job.kakao_provider_runtime,
                "renew_lease",
                return_value=True,
            ),
            patch.object(
                job,
                "_eligible_rows",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("queue") or []
                ),
            ),
        ):
            self.assertEqual(job.run_enrichment(max_records=1), 0)

        self.assertEqual(events, ["preflight", "reset", "consume", "queue"])
        self.assertEqual(consume.call_args.args[1], 7)
        self.assertIs(consume.call_args.kwargs["database"], database)

    @patch.object(job.kakao_provider_runtime, "release_lease", return_value=True)
    @patch.object(
        job.kakao_provider_runtime,
        "test_connection_and_record",
        return_value={
            "ok": False,
            "category": "AUTH_ERROR",
            "safe_error_code": "HTTP_401",
        },
    )
    @patch.object(
        job.kakao_provider_runtime,
        "get_daily_usage",
        return_value={"request_count": 0, "blocked_until": ""},
    )
    @patch.object(job.kakao_provider_runtime, "acquire_lease", return_value=True)
    @patch.object(job, "_eligible_rows")
    @patch.object(job, "CloudDatabase", return_value=Mock())
    def test_failed_preflight_stops_before_queue_read(
        self,
        _database,
        eligible,
        _acquire,
        _usage,
        _preflight,
        release,
    ):
        self.assertEqual(job.run_enrichment(max_records=1), 2)
        eligible.assert_not_called()
        release.assert_called_once()

    @patch.object(job, "_release_kakao_no_match_holds")
    @patch.object(job.kakao_provider_runtime, "release_lease", return_value=True)
    @patch.object(
        job.kakao_provider_runtime,
        "test_connection_and_record",
        return_value={"ok": True, "category": "CONNECTED", "safe_error_code": ""},
    )
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(job.kakao_provider_runtime, "reserve_quota")
    @patch.object(
        job.kakao_provider_runtime,
        "get_daily_usage",
        side_effect=[
            {"request_count": 0, "blocked_until": ""},
            {"request_count": 1, "blocked_until": ""},
        ],
    )
    @patch.object(job.kakao_provider_runtime, "acquire_lease", return_value=True)
    @patch.object(job, "_eligible_rows", return_value=[{}, {}])
    @patch.object(job, "_enrich_one")
    @patch.object(job.time, "sleep")
    @patch.object(job, "CloudDatabase", return_value=Mock())
    def test_actual_api_requests_are_recorded(
        self,
        _database,
        _sleep,
        enrich,
        _eligible,
        _acquire,
        _usage,
        reserve_quota,
        reconcile_usage,
        _renew,
        _preflight,
        _release,
        release_holds,
    ):
        enrich.side_effect = [
            {
                "status": "matched",
                "outcome": "matched",
                "provider_error": False,
                "fatal": False,
                "safe_error_code": "",
                "request_count": 2,
            },
            {
                "status": "no_match",
                "outcome": "no_match",
                "provider_error": False,
                "fatal": False,
                "safe_error_code": "",
                "request_count": 1,
                "source_key": "source:held",
                "held": True,
            },
        ]
        reserve_quota.return_value = {
            "request_count": 5,
            "reserved": True,
            "blocked_until": "",
            "quota_date": "2026-08-05",
        }
        reconcile_usage.return_value = {
            "request_count": 4,
            "blocked_until": "",
        }
        self.assertEqual(
            job.run_enrichment(workers=1, batch_size=2, max_records=2),
            0,
        )
        reserve_quota.assert_called_once()
        self.assertEqual(reserve_quota.call_args.args, (4, 85_000))
        reconcile_usage.assert_called_once()
        self.assertEqual(reconcile_usage.call_args.args, (4, 3, ""))
        self.assertEqual(
            reconcile_usage.call_args.kwargs["reservation_date"],
            "2026-08-05",
        )
        release_holds.assert_called_once_with(["source:held"])

    @patch.object(job.kakao_provider_runtime, "release_lease", return_value=True)
    @patch.object(
        job.kakao_provider_runtime,
        "test_connection_and_record",
        return_value={"ok": True, "category": "CONNECTED", "safe_error_code": ""},
    )
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
    @patch.object(job.kakao_provider_runtime, "reconcile_usage")
    @patch.object(job.kakao_provider_runtime, "reserve_quota")
    @patch.object(
        job.kakao_provider_runtime,
        "get_daily_usage",
        side_effect=[
            {"request_count": 0, "blocked_until": ""},
            {"request_count": 1, "blocked_until": ""},
        ],
    )
    @patch.object(job.kakao_provider_runtime, "acquire_lease", return_value=True)
    @patch.object(job, "_eligible_rows", return_value=[{}])
    @patch.object(
        job,
        "_enrich_one",
        return_value={
            "status": "error",
            "provider_error": True,
            "fatal": False,
            "safe_error_code": "HTTP_429",
            "request_count": 1,
        },
    )
    @patch.object(job, "CloudDatabase", return_value=Mock())
    def test_http_429_is_recorded_and_stops_same_day(
        self,
        _database,
        _enrich,
        _eligible,
        _acquire,
        _usage,
        reserve_quota,
        reconcile_usage,
        _renew,
        _preflight,
        _release,
    ):
        reserve_quota.return_value = {
            "request_count": 3,
            "reserved": True,
            "blocked_until": "",
            "quota_date": "2026-08-05",
        }
        reconcile_usage.return_value = {
            "request_count": 2,
            "blocked_until": "2026-08-04T15:00:00Z",
            "last_safe_error_code": "HTTP_429",
        }
        self.assertEqual(job.run_enrichment(max_records=10), 2)
        reserve_quota.assert_called_once()
        self.assertEqual(reserve_quota.call_args.args, (2, 85_000))
        reconcile_usage.assert_called_once()
        self.assertEqual(
            reconcile_usage.call_args.args,
            (2, 1, "HTTP_429"),
        )
        self.assertEqual(
            reconcile_usage.call_args.kwargs["reservation_date"],
            "2026-08-05",
        )

    @patch.object(job.kakao_provider_runtime, "release_lease", return_value=True)
    @patch.object(
        job.kakao_provider_runtime,
        "test_connection_and_record",
        return_value={"ok": True, "category": "CONNECTED", "safe_error_code": ""},
    )
    @patch.object(
        job.kakao_provider_runtime,
        "get_daily_usage",
        side_effect=[
            {"request_count": 0, "blocked_until": ""},
            {"request_count": 84_999, "blocked_until": ""},
        ],
    )
    @patch.object(job.kakao_provider_runtime, "acquire_lease", return_value=True)
    @patch.object(job, "_eligible_rows")
    @patch.object(job, "CloudDatabase", return_value=Mock())
    def test_worst_case_two_requests_are_reserved_before_queue_read(
        self,
        _database,
        eligible,
        _acquire,
        _usage,
        _preflight,
        _release,
    ):
        self.assertEqual(job.run_enrichment(max_records=1), 0)
        eligible.assert_not_called()

    @patch.object(job.kakao_provider_runtime, "release_lease", return_value=True)
    @patch.object(
        job.kakao_provider_runtime,
        "test_connection_and_record",
        return_value={"ok": True, "category": "CONNECTED", "safe_error_code": ""},
    )
    @patch.object(job.kakao_provider_runtime, "renew_lease", return_value=True)
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
    @patch.object(
        job.kakao_provider_runtime,
        "get_daily_usage",
        side_effect=[
            {"request_count": 0, "blocked_until": ""},
            {"request_count": 84_998, "blocked_until": ""},
        ],
    )
    @patch.object(job.kakao_provider_runtime, "acquire_lease", return_value=True)
    @patch.object(job, "_eligible_rows", return_value=[{}])
    @patch.object(job, "_enrich_one")
    @patch.object(job, "CloudDatabase", return_value=Mock())
    def test_atomic_quota_denial_stops_before_provider_call(
        self,
        _database,
        enrich,
        _eligible,
        _acquire,
        _usage,
        reserve_quota,
        reconcile_usage,
        _renew,
        _preflight,
        _release,
    ):
        self.assertEqual(job.run_enrichment(max_records=1), 0)
        self.assertEqual(reserve_quota.call_args.args, (2, 85_000))
        enrich.assert_not_called()
        reconcile_usage.assert_not_called()

    def test_first_one_hundred_no_matches_trip_persistent_guard(self):
        database = Mock()
        no_match = {
            "status": "no_match",
            "outcome": "no_match",
            "provider_error": False,
            "fatal": False,
            "safe_error_code": "",
            "request_count": 1,
            "source_key": "source:held",
            "held": True,
        }
        with (
            patch.object(
                job.kakao_provider_runtime,
                "get_daily_usage",
                return_value={"request_count": 0, "blocked_until": ""},
            ),
            patch.object(
                job.kakao_provider_runtime,
                "test_connection_and_record",
                return_value={
                    "ok": True,
                    "category": "CONNECTED",
                    "safe_error_code": "",
                },
            ),
            patch.object(
                job.kakao_provider_runtime,
                "renew_lease",
                return_value=True,
            ),
            patch.object(
                job.kakao_provider_runtime,
                "reserve_quota",
                return_value={"reserved": True, "blocked_until": ""},
            ),
            patch.object(job.kakao_provider_runtime, "reconcile_usage"),
            patch.object(
                job.kakao_provider_runtime,
                "trip_guard",
                return_value=True,
            ) as trip_guard,
            patch.object(
                job.kakao_provider_runtime,
                "new_lease_token",
                return_value="incident-event",
            ),
            patch.object(
                job,
                "_eligible_rows",
                side_effect=lambda limit, _retry: [{} for _ in range(limit)],
            ),
            patch.object(job, "_enrich_one", return_value=no_match),
            patch.object(job, "_release_kakao_no_match_holds") as release,
            patch.object(job.time, "sleep"),
        ):
            result = job._run_with_lease(
                database=database,
                lease_token="incident-token",
                workers=1,
                batch_size=100,
                retry_days=30,
                min_score=85,
                max_records=100,
                safe_limit=85_000,
            )

        self.assertEqual(result, job.EXIT_PROVIDER_GUARD)
        trip_guard.assert_called_once_with(
            "incident-token",
            "incident-event",
            job.kakao_provider_runtime.GUARD_REASON_INITIAL_ZERO_MATCH_RATE,
            job.SOURCE_JOB,
            observed_count=100,
            matched_count=0,
            database=database,
        )
        release.assert_not_called()

    def test_recent_five_hundred_zero_matches_trip_persistent_guard(self):
        database = Mock()
        matched = {
            "status": "matched",
            "outcome": "matched",
            "provider_error": False,
            "fatal": False,
            "safe_error_code": "",
            "request_count": 1,
        }
        no_match = {
            "status": "no_match",
            "outcome": "no_match",
            "provider_error": False,
            "fatal": False,
            "safe_error_code": "",
            "request_count": 1,
            "source_key": "source:held",
            "held": True,
        }
        outcomes = [matched, *([no_match] * 500)]
        with (
            patch.object(
                job.kakao_provider_runtime,
                "get_daily_usage",
                return_value={"request_count": 0, "blocked_until": ""},
            ),
            patch.object(
                job.kakao_provider_runtime,
                "test_connection_and_record",
                return_value={
                    "ok": True,
                    "category": "CONNECTED",
                    "safe_error_code": "",
                },
            ),
            patch.object(
                job.kakao_provider_runtime,
                "renew_lease",
                return_value=True,
            ),
            patch.object(
                job.kakao_provider_runtime,
                "reserve_quota",
                return_value={"reserved": True, "blocked_until": ""},
            ),
            patch.object(job.kakao_provider_runtime, "reconcile_usage"),
            patch.object(
                job.kakao_provider_runtime,
                "trip_guard",
                return_value=True,
            ) as trip_guard,
            patch.object(
                job.kakao_provider_runtime,
                "new_lease_token",
                return_value="incident-event",
            ),
            patch.object(
                job,
                "_eligible_rows",
                side_effect=lambda limit, _retry: [{} for _ in range(limit)],
            ),
            patch.object(job, "_enrich_one", side_effect=outcomes),
            patch.object(job, "_release_kakao_no_match_holds") as release,
            patch.object(job.time, "sleep"),
        ):
            result = job._run_with_lease(
                database=database,
                lease_token="incident-token",
                workers=1,
                batch_size=100,
                retry_days=30,
                min_score=85,
                max_records=501,
                safe_limit=85_000,
            )

        self.assertEqual(result, job.EXIT_PROVIDER_GUARD)
        trip_guard.assert_called_once_with(
            "incident-token",
            "incident-event",
            job.kakao_provider_runtime.GUARD_REASON_ROLLING_ZERO_MATCH_RATE,
            job.SOURCE_JOB,
            observed_count=500,
            matched_count=0,
            database=database,
        )
        release.assert_not_called()

    @patch.object(job.kakao_provider_runtime, "release_lease", return_value=True)
    @patch.object(
        job.kakao_provider_runtime,
        "test_connection_and_record",
        return_value={"ok": True, "category": "CONNECTED", "safe_error_code": ""},
    )
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
    @patch.object(
        job.kakao_provider_runtime,
        "get_daily_usage",
        side_effect=[
            {"request_count": 0, "blocked_until": ""},
            {"request_count": 1, "blocked_until": ""},
        ],
    )
    @patch.object(job.kakao_provider_runtime, "acquire_lease", return_value=True)
    @patch.object(job, "_eligible_rows", return_value=[{}])
    @patch.object(job, "_enrich_one", side_effect=RuntimeError("worker lost"))
    @patch.object(job, "CloudDatabase", return_value=Mock())
    def test_unknown_worker_failure_keeps_full_quota_reservation(
        self,
        _database,
        _enrich,
        _eligible,
        _acquire,
        _usage,
        _reserve_quota,
        reconcile_usage,
        _renew,
        _preflight,
        _release,
    ):
        reconcile_usage.return_value = {
            "request_count": 3,
            "blocked_until": "",
        }

        self.assertEqual(job.run_enrichment(max_records=1), 2)
        self.assertEqual(reconcile_usage.call_args.args, (2, 2, ""))
        self.assertEqual(
            reconcile_usage.call_args.kwargs["reservation_date"],
            "2026-08-05",
        )


if __name__ == "__main__":
    unittest.main()
