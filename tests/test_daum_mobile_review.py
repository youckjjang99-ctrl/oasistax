from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import company_sales_assignment as assignments
import scheduled_employment_contact_enrichment as enrichment


MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase"
    / "migrations"
    / "20260815005450_add_daum_mobile_candidate_review.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8").lower()


class DaumMobileReviewRepositoryTests(unittest.TestCase):
    @patch.object(enrichment, "CloudDatabase")
    def test_collector_persists_only_bounded_mobile_candidates(
        self,
        cloud_database,
    ) -> None:
        db = Mock()
        cloud_database.return_value = db

        enrichment._persist_daum_mobile_review_candidates(
            "place:test",
            [
                {
                    "mobile_phone": "010-1234-5678",
                    "source_url": "https://example.com/contact",
                    "query_mode": "mobile_first",
                    "confidence": 99,
                    "evidence": {"name_score": 45},
                },
                {
                    "mobile_phone": "02-1234-5678",
                    "source_url": "https://example.com/landline",
                },
            ],
        )

        function_name, parameters = db.rpc.call_args.args
        self.assertEqual(
            function_name,
            "oasis_upsert_daum_mobile_review_candidates",
        )
        self.assertEqual(len(parameters["p_candidates"]), 1)
        self.assertEqual(
            parameters["p_candidates"][0]["mobile_phone"],
            "01012345678",
        )
        self.assertEqual(parameters["p_candidates"][0]["confidence"], 84)

    def test_admin_candidate_list_is_bounded_and_field_filtered(self) -> None:
        db = Mock()
        db.rpc.return_value = [{
            "candidate_id": "candidate-1",
            "company_name": "테스트기업",
            "mobile_phone": "01012345678",
            "review_status": "pending",
            "private_field": "must-not-leak",
        }]

        result = assignments.list_admin_daum_mobile_candidates(
            "admin@example.com",
            statuses=["pending"],
            limit=5000,
            db=db,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["candidates"]), 1)
        self.assertNotIn("private_field", result["candidates"][0])
        function_name, parameters = db.rpc.call_args.args
        self.assertEqual(
            function_name,
            "oasis_list_admin_daum_mobile_candidates",
        )
        self.assertEqual(parameters["p_limit"], 1000)
        self.assertEqual(parameters["p_statuses"], ["pending"])

    def test_admin_review_returns_conflict_without_claiming_success(self) -> None:
        db = Mock()
        db.rpc.return_value = [{
            "success": False,
            "code": "MOBILE_ALREADY_EXISTS",
            "message": "기존 번호를 덮어쓰지 않았습니다.",
            "review_status": "pending",
        }]

        result = assignments.admin_review_daum_mobile_candidate(
            "admin@example.com",
            "candidate-1",
            "approve",
            reason="원문 확인",
            db=db,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "MOBILE_ALREADY_EXISTS")
        self.assertEqual(result["review_status"], "pending")


class DaumMobileReviewMigrationTests(unittest.TestCase):
    def test_review_tables_are_service_only_with_rls(self) -> None:
        self.assertIn(
            "alter table public.oasis_daum_mobile_review_candidates\n"
            "    enable row level security",
            SQL,
        )
        self.assertIn(
            "alter table public.oasis_daum_mobile_review_candidates\n"
            "    force row level security",
            SQL,
        )
        self.assertIn(
            "from public, anon, authenticated",
            SQL,
        )
        self.assertIn("to service_role", SQL)
        self.assertNotIn(
            "grant select on table public.oasis_daum_mobile_review_candidates "
            "to authenticated",
            SQL,
        )

    def test_admin_rpcs_enforce_admin_and_do_not_overwrite_mobile(self) -> None:
        self.assertGreaterEqual(
            SQL.count("public.oasis_sales_actor_is_admin"),
            2,
        )
        self.assertIn("message = 'admin_required'", SQL)
        self.assertIn("mobile_already_exists", SQL)
        self.assertIn("v_existing_mobile <> v_candidate.mobile_phone", SQL)
        self.assertIn("'daum_web_snippet'", SQL)
        self.assertIn("'admin_approved_candidate'", SQL)

    def test_candidate_input_is_bounded_and_metrics_are_pii_free(self) -> None:
        self.assertIn("jsonb_array_length", SQL)
        self.assertIn("review_status text not null default 'pending'", SQL)
        self.assertIn(
            "create table if not exists "
            "public.oasis_contact_enrichment_run_metrics",
            SQL,
        )
        metrics_definition = SQL.split(
            "create table if not exists "
            "public.oasis_contact_enrichment_run_metrics",
            1,
        )[1].split(");", 1)[0]
        self.assertNotIn("mobile_phone", metrics_definition)
        self.assertNotIn("company_name", metrics_definition)
        self.assertNotIn("business_no", metrics_definition)


if __name__ == "__main__":
    unittest.main()
