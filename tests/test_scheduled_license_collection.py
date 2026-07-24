from __future__ import annotations

import unittest
from unittest.mock import patch

import scheduled_license_collection


class ScheduledLicenseCollectionTest(unittest.TestCase):
    @patch("scheduled_license_collection._upsert_progress")
    @patch("scheduled_license_collection.save_sync_run")
    @patch("scheduled_license_collection.save_businesses", return_value=1)
    @patch(
        "scheduled_license_collection.localdata_contact_client.fetch_business_page"
    )
    def test_service_resumes_from_checkpoint_and_completes(
        self,
        fetch_page,
        _save_businesses,
        _save_sync_run,
        upsert_progress,
    ) -> None:
        fetch_page.return_value = {
            "ok": True,
            "items": [{"source_key": "x", "company_name": "업체"}],
            "raw_received_count": 1,
            "message": "정상",
        }

        result = scheduled_license_collection._collect_service(
            "monthly-2026-07",
            "test-service",
            start_page=7,
            max_pages=100,
            rows_per_page=1000,
        )

        self.assertEqual(fetch_page.call_args.kwargs["page_no"], 7)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(
            upsert_progress.call_args.kwargs["status"],
            "completed",
        )
        self.assertEqual(upsert_progress.call_args.kwargs["next_page"], 8)

    @patch("scheduled_license_collection._upsert_progress")
    @patch("scheduled_license_collection.save_sync_run")
    @patch(
        "scheduled_license_collection.localdata_contact_client.fetch_business_page"
    )
    def test_failed_page_keeps_same_resume_page(
        self,
        fetch_page,
        _save_sync_run,
        upsert_progress,
    ) -> None:
        fetch_page.return_value = {
            "ok": False,
            "status": "TIMEOUT",
            "message": "응답시간 초과",
            "items": [],
            "raw_received_count": 0,
        }

        result = scheduled_license_collection._collect_service(
            "monthly-2026-07",
            "test-service",
            start_page=4,
            max_pages=100,
            rows_per_page=1000,
        )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            upsert_progress.call_args.kwargs["next_page"],
            4,
        )


if __name__ == "__main__":
    unittest.main()
