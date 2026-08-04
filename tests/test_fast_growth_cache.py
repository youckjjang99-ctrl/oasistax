from __future__ import annotations

import unittest
import json
from types import SimpleNamespace
from unittest.mock import patch

import requests

from prospect_collection_service import collect_contactable_growth_companies
from prospect_db_repository import load_fast_growth_candidates


class _Response:
    def __init__(self, rows, *, ok=True, status_code=200, text="rows"):
        self.ok = ok
        self.status_code = status_code
        self.text = text
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
    @patch("prospect_db_repository.requests.post")
    def test_maps_compact_growth_row_to_prospect(
        self,
        request_post,
        _headers,
        _config,
    ) -> None:
        request_post.return_value = _Response(
            [
                    {
                        "source_type": "nps_monthly",
                        "source_record_key": "nps-1",
                        "business_no": "111111",
                        "company_name": "월간성장 주식회사",
                        "address": "서울특별시 마포구 월드컵로 1",
                        "province_name": "서울특별시",
                        "district_name": "마포구",
                        "province_code": "11",
                        "district_code": "11440",
                        "industry_code": "62010",
                        "industry_name": "컴퓨터 프로그래밍 서비스업",
                        "industry_category": "서비스업",
                        "current_employee_count": 12,
                        "previous_employee_count": 8,
                        "employee_growth": 4,
                        "previous_period": "202505",
                        "current_period": "202506",
                        "growth_frequency": "monthly",
                        "is_new_company": False,
                        "mobile_phone": "010-1111-2222",
                        "landline_phone": "02-111-2222",
                        "email": "hello@example.com",
                        "instagram": "@monthly",
                        "instagram_url": "https://instagram.com/monthly/",
                        "contact_status": "matched",
                    },
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
                        "industry_category": "서비스업",
                        "current_employee_count": 8,
                        "previous_employee_count": 5,
                        "employee_growth": 3,
                        "previous_period": "2024",
                        "current_period": "2025",
                        "growth_frequency": "annual",
                        "is_new_company": False,
                        "mobile_phone": "",
                        "landline_phone": "02-333-4444",
                        "email": "",
                        "instagram": "",
                        "instagram_url": "",
                        "contact_status": "matched",
                    },
            ]
        )
        rows = load_fast_growth_candidates(
            "11",
            minimum_employees=1,
            maximum_employees=30,
            minimum_growth=2,
            business_type="stock",
            district_name="강남구",
            industry_categories=["서비스업"],
            contact_channels=["mobile_phone", "email"],
            limit=3,
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["사업장명"], "월간성장 주식회사")
        self.assertEqual(rows[0]["선택고용증가"], 4)
        self.assertEqual(rows[0]["고용증가기준"], "monthly")
        self.assertEqual(rows[0]["고용증가구분"], "전월대비 +4명")
        self.assertEqual(rows[0]["휴대전화"], "010-1111-2222")
        self.assertEqual(rows[1]["사업자등록번호"], "1234567890")
        self.assertEqual(rows[1]["가입자수"], 8)
        self.assertEqual(rows[1]["고용증가기준"], "annual")
        self.assertEqual(rows[1]["고용증가구분"], "전년대비 +3명")
        payload = json.loads(request_post.call_args.kwargs["data"])
        self.assertTrue(
            request_post.call_args.args[0].endswith(
                "/rpc/oasis_search_employment_growth_v2"
            )
        )
        self.assertEqual(payload["p_province_code"], "11")
        self.assertEqual(payload["p_province_name"], "서울특별시")
        self.assertEqual(payload["p_district"], "강남구")
        self.assertEqual(payload["p_min_employees"], 1)
        self.assertEqual(payload["p_max_employees"], 30)
        self.assertEqual(payload["p_industries"], ["서비스업"])
        self.assertEqual(
            payload["p_contact_channels"],
            ["email", "mobile_phone"],
        )
        self.assertEqual(payload["p_business_type"], "stock")
        self.assertEqual(payload["p_limit"], 3)

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
    @patch("prospect_db_repository.requests.post")
    def test_maps_legacy_special_province_code(
        self,
        request_post,
        _headers,
        _config,
    ) -> None:
        request_post.return_value = _Response([])

        load_fast_growth_candidates(
            "42",
            minimum_employees=10,
            limit=3,
        )

        payload = json.loads(request_post.call_args.kwargs["data"])
        self.assertEqual(payload["p_province_code"], "51")
        self.assertEqual(payload["p_province_name"], "강원특별자치도")

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
    @patch("prospect_db_repository.requests.post")
    def test_statement_timeout_returns_safe_actionable_error(
        self,
        request_post,
        _headers,
        _config,
    ) -> None:
        request_post.return_value = _Response(
            [],
            ok=False,
            status_code=500,
            text='{"code":"57014","message":"statement timeout"}',
        )

        with self.assertRaisesRegex(RuntimeError, "조회 시간이 초과"):
            load_fast_growth_candidates("", limit=100)

    def test_read_timeout_maps_to_safe_exception_and_growth_error_code(self):
        timeout_detail = "internal upstream detail"
        with patch(
            "prospect_db_repository.get_cloud_config",
            return_value=SimpleNamespace(
                configured=True,
                url="https://example.supabase.co",
                timeout=20,
            ),
        ), patch(
            "prospect_db_repository._rest_headers",
            return_value={"Authorization": "Bearer test"},
        ), patch(
            "prospect_db_repository.requests.post",
            side_effect=requests.exceptions.ReadTimeout(timeout_detail),
        ), patch(
            "prospect_collection_service.existing_prospect_identities",
            return_value=(set(), set(), set()),
        ), patch(
            "prospect_collection_service.fetch_nps_workplaces",
        ) as fetch_nps:
            with self.assertRaisesRegex(RuntimeError, "조회 시간이 초과") as error:
                load_fast_growth_candidates("", limit=100)

            self.assertNotIn(timeout_detail, str(error.exception))
            result = collect_contactable_growth_companies(
                "",
                target_count=100,
                growth_only=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "GROWTH_SEARCH_TIMEOUT")
        self.assertIn("조회 시간이 초과", result["message"])
        self.assertNotIn(timeout_detail, result["message"])
        fetch_nps.assert_not_called()

    @patch("prospect_collection_service.remove_existing_prospects")
    @patch(
        "prospect_collection_service.remove_existing_customers",
        side_effect=lambda rows: (rows, 0),
    )
    @patch(
        "prospect_collection_service.load_fast_growth_candidates",
        return_value=[{"source_key": f"row-{index}"} for index in range(500)],
    )
    @patch(
        "prospect_collection_service.existing_prospect_identities",
        return_value=(set(), set(), set()),
    )
    def test_precomputed_query_supports_500_results(
        self,
        _identities,
        cached,
        _customers,
        remove_prospects,
    ) -> None:
        remove_prospects.side_effect = lambda rows, **_kwargs: (rows, 0)
        result = collect_contactable_growth_companies(
            "11",
            target_count=500,
            business_type="all",
            contact_channels=["email"],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["found_count"], 500)
        self.assertEqual(result["contact_channels"], ["email"])
        self.assertEqual(
            cached.call_args.kwargs["business_type"],
            "all",
        )
        self.assertEqual(cached.call_args.kwargs["limit"], 500)

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

    @patch("prospect_collection_service.fetch_nps_workplaces")
    @patch(
        "prospect_collection_service.load_fast_growth_candidates",
        side_effect=RuntimeError("성장기업 조회 시간이 초과되었습니다."),
    )
    @patch(
        "prospect_collection_service.existing_prospect_identities",
        return_value=(set(), set(), set()),
    )
    def test_precomputed_timeout_exposes_safe_error_code(
        self,
        _identities,
        _cached,
        fetch_nps,
    ) -> None:
        result = collect_contactable_growth_companies(
            "",
            target_count=100,
            growth_only=True,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "GROWTH_SEARCH_TIMEOUT")
        fetch_nps.assert_not_called()


if __name__ == "__main__":
    unittest.main()
