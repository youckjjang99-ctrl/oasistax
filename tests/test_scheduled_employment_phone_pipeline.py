from __future__ import annotations

import unittest
from unittest.mock import call, patch

import scheduled_employment_phone_pipeline as pipeline


class EmploymentPhonePipelineTest(unittest.TestCase):
    @patch.object(pipeline, "run_enrichment", side_effect=[0, 0])
    def test_runs_kakao_before_daum(self, run_enrichment) -> None:
        result = pipeline.run_phone_pipeline()

        self.assertEqual(result, 0)
        self.assertEqual(
            [item.kwargs["phone_provider"] for item in run_enrichment.call_args_list],
            ["kakao", "daum"],
        )

    @patch.object(pipeline, "run_enrichment", side_effect=[3])
    def test_does_not_start_daum_when_kakao_job_fails(
        self,
        run_enrichment,
    ) -> None:
        result = pipeline.run_phone_pipeline()

        self.assertEqual(result, 3)
        self.assertEqual(run_enrichment.call_count, 1)
        self.assertEqual(
            run_enrichment.call_args,
            call(
                stage="phone",
                phone_provider="kakao",
                workers=12,
                batch_size=200,
                max_records=0,
                max_requests=pipeline.KAKAO_DAILY_SAFE_REQUESTS,
            ),
        )

    @patch.object(
        pipeline,
        "run_enrichment",
        side_effect=[pipeline.EXIT_DAILY_QUOTA, 0],
    )
    def test_safe_request_limit_still_drains_existing_daum_queue(
        self,
        run_enrichment,
    ) -> None:
        result = pipeline.run_phone_pipeline()

        self.assertEqual(result, 0)
        self.assertEqual(
            [item.kwargs["phone_provider"] for item in run_enrichment.call_args_list],
            ["kakao", "daum"],
        )


if __name__ == "__main__":
    unittest.main()
