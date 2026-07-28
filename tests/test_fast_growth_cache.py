from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prospect_db_repository import load_fast_growth_candidates


class _Response:
    ok = True
    status_code = 200
    text = "[]"

    def json(self):
        return [
            {
                "source_type": "comwel_annual",
                "source_record_key": "1234567890",
                "business_no": "1234567890",
                "company_name": "테스트 주식회사",
                "address": "서울특별시 강남구 테헤란로 1",
                "province_name": "서울특별시",
                "district_name": "강남구",
                "province_code": "11",
                "district_code": "",
                "industry_code": "58222",
                "industry_name": "응용 소프트웨어 개발 및 공급업",
                "current_employee_count": 8,
                "previous_employee_count": 5,
                "employee_growth": 3,
                "previous_period": "2024",
                "current_period": "2025",
                "growth_frequency": "annual",
                "is_new_company": False,
            }
        ]


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
        request_get.return_value = _Response()
        rows = load_fast_growth_candidates(
            "11",
            minimum_employees=1,
            limit=3,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["사업자등록번호"], "1234567890")
        self.assertEqual(rows[0]["가입자수"], 8)
        self.assertEqual(rows[0]["선택고용증가"], 3)
        self.assertEqual(rows[0]["고용증가기준"], "annual")
        params = request_get.call_args.kwargs["params"]
        self.assertEqual(params["province_code"], "eq.11")
        self.assertEqual(params["current_employee_count"], "gte.1")


if __name__ == "__main__":
    unittest.main()
