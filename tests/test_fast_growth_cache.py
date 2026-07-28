from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prospect_collection_service import collect_contactable_growth_companies
from prospect_db_repository import load_fast_growth_candidates


class _Response:
    def __init__(self, rows):
        self.ok = True
        self.status_code = 200
        self.text = "rows"
        self._rows = rows

    def json(self):
        return self._rows


class FastGrowthCacheTest(unittest.TestCase):
    @patch(
        "prospect_db_repository.get_cloud_config",
        return_value=SimpleNamespace(
            configured=True,
            url="https://example.supabase.co",
            timeout=20,
        ),
    )
    @patch(
        "prospect_db_repository._rest_headers",
        return_value={"Authorization": "Bearer test"},
    )
    @patch("prospect_db_repository.requests.get")
    def test_maps_compact_growth_row_to_prospect(
        self,
        request_get,
        _headers,
        _config,
    ) -> None:
        request_get.side_effect = [
            _Response(
                [
                    {
                        "snapshot_identity": "nps-1",
                        "current_ym": "202506",
                        "previous_ym": "202505",
                        "business_no": "111111",
                        "company_name": "월간성장 주식회사",
                        "address": "서울특별시 마포구 월드컵로 1",
                        "industry_code": "62010",
                        "industry_name": "컴퓨터 프로그래밍 서비스업",
                        "province_code": "11",
                        "district_code": "11440",
                        "current_employee_count": 12,
                        "previous_employee_count": 8,
                        "employee_growth": 4,
                    }
                ]
            ),
            _Response(
                [
                    {
                        "business_no": "1234567890",
                        "company_name": "테스트 주식회사",
                        "address": "서울특별시 강남구 테헤란로 1",
                        "province": "서울특별시",
                        "district": "강남구",
                        "industry_code": "58222",
                        "industry_name": "응용 소프트웨어 개발 및 공급업",
                        "workers_2024": 5,
                        "workers_2025": 8,
                        "growth_2024_2025": 3,
                        "is_new_2025": False,
                    }
                ]
            ),
        ]
        rows = load_fast_growth_candidates(
            "11",
            minimum_employees=1,
            limit=3,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["사업장명"], "월간성장 주식회사")
        self.assertEqual(rows[0]["선택고용증가"], 4)
        self.assertEqual(rows[0]["고용증가기준"], "monthly")
        self.assertEqual(rows[1]["사업자등록번호"], "1234567890")
        self.assertEqual(rows[1]["가입자수"], 8)
        self.assertEqual(rows[1]["고용증가기준"], "annual")
        nps_params = request_get.call_args_list[0].kwargs["params"]
        comwel_params = request_get.call_args_list[1].kwargs["params"]
        self.assertEqual(nps_params["province_code"], "eq.11")
        self.assertEqual(nps_params["current_employee_count"], "gte.10")
        self.assertEqual(comwel_params["province"], "eq.서울특별시")
        self.assertEqual(
            comwel_params["and"],
            "(workers_2025.gte.1,workers_2025.lte.9)",
        )

    @patch(
        "prospect_db_repository.get_cloud_config",
        return_value=SimpleNamespace(
            configured=True,
            url="https://example.supabase.co",
            timeout=20,
        ),
    )
    @patch(
        "prospect_db_repository._rest_headers",
        return_value={"Authorization": "Bearer test"},
    )
    @patch("prospect_db_repository.requests.get")
    def test_maps_legacy_special_province_code(
        self,
        request_get,
        _headers,
        _config,
    ) -> None:
        request_get.return_value = _Response([])

        load_fast_growth_candidates(
            "42",
            minimum_employees=10,
            limit=3,
        )

        params = request_get.call_args.kwargs["params"]
        self.assertEqual(params["province_code"], "eq.51")

    @patch("prospect_collection_service.fetch_nps_workplaces")
    @patch(
        "prospect_collection_service.load_fast_growth_candidates",
        side_effect=RuntimeError("query timeout"),
    )
    @patch(
        "prospect_collection_service.existing_prospect_identities",
        return_value=(set(), set(), set()),
    )
    def test_precomputed_failure_does_not_fall_back_to_live_nps(
        self,
        _identities,
        _cached,
        fetch_nps,
    ) -> None:
        events: list[dict] = []

        result = collect_contactable_growth_companies(
            "11",
            target_count=30,
            start_page=4,
            max_pages=10,
            growth_only=True,
            progress=events.append,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(
            result["stats"]["source_mode"],
            "precomputed_error",
        )
        self.assertIn("Supabase", result["message"])
        self.assertEqual(events[0]["stage"], "precomputed")
        fetch_nps.assert_not_called()


if __name__ == "__main__":
    unittest.main()
