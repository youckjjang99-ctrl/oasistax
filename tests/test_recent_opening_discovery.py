from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from prospect_collection_service import collect_recent_opening_companies
from prospect_db_repository import (
    load_recent_opening_candidates,
    remove_existing_prospects,
)


class _Response:
    def __init__(self, rows):
        self.ok = True
        self.status_code = 200
        self.text = "rows"
        self._rows = rows

    def json(self):
        return self._rows


class RecentOpeningDiscoveryTest(unittest.TestCase):
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
    def test_maps_nps_and_comwel_opening_signals(
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
                    "business_no": "1234567890",
                    "company_name": "새봄 주식회사",
                    "address": "서울특별시 마포구 월드컵로 1",
                    "province_name": "서울특별시",
                    "district_name": "마포구",
                    "industry_code": "62010",
                    "industry_name": "소프트웨어 개발업",
                    "industry_category": "서비스업",
                    "current_employee_count": 12,
                    "opening_signal_date": "2026-05-03",
                    "opening_signal_year": 2026,
                    "opening_signal_precision": "day",
                    "source_period": "202606",
                    "mobile_phone": "010-1111-2222",
                    "landline_phone": "",
                    "email": "",
                    "instagram": "",
                    "instagram_url": "",
                    "contact_status": "matched",
                },
                {
                    "source_type": "comwel_annual",
                    "source_record_key": "9876543210",
                    "business_no": "9876543210",
                    "company_name": "한결식품",
                    "address": "서울특별시 강남구 테헤란로 1",
                    "province_name": "서울특별시",
                    "district_name": "강남구",
                    "industry_code": "10799",
                    "industry_name": "기타 식품 제조업",
                    "industry_category": "제조업",
                    "current_employee_count": 8,
                    "opening_signal_date": None,
                    "opening_signal_year": 2025,
                    "opening_signal_precision": "year",
                    "source_period": "2025",
                    "mobile_phone": "",
                    "landline_phone": "02-333-4444",
                    "email": "",
                    "instagram": "",
                    "instagram_url": "",
                    "contact_status": "matched",
                },
                {
                    "source_type": "nps_monthly",
                    "source_record_key": "nps-without-business-no",
                    "business_no": "",
                    "company_name": "번호없는 새봄상점",
                    "address": "서울특별시 마포구 성산로 2",
                    "province_name": "서울특별시",
                    "district_name": "마포구",
                    "industry_code": "56111",
                    "industry_name": "한식 일반 음식점업",
                    "industry_category": "외식업",
                    "current_employee_count": 4,
                    "opening_signal_date": "2026-06-11",
                    "opening_signal_year": 2026,
                    "opening_signal_precision": "day",
                    "source_period": "202606",
                    "mobile_phone": "",
                    "landline_phone": "02-555-7777",
                    "email": "",
                    "instagram": "",
                    "instagram_url": "",
                    "contact_status": "matched",
                },
            ]
        )

        rows = load_recent_opening_candidates(
            "11",
            minimum_employees=1,
            maximum_employees=30,
            recent_months=12,
            include_comwel_annual=True,
            business_type="stock",
            district_name="마포구",
            industry_categories=["서비스업"],
            contact_channels=["mobile_phone"],
            limit=50,
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["source_key"], "recent_opening:1234567890")
        self.assertEqual(rows[0]["신규추정일"], "2026-05-03")
        self.assertEqual(
            rows[0]["신규근거"],
            "국민연금 사업장 적용일",
        )
        self.assertEqual(
            rows[1]["신규개업구분"],
            "2025년 신규 추정",
        )
        self.assertEqual(
            rows[1]["신규근거"],
            "근로복지공단 연간 자료 최초 등장",
        )
        self.assertEqual(rows[2]["사업자등록번호"], "")
        self.assertEqual(rows[2]["사업자번호상태"], "미확인")
        self.assertEqual(
            rows[2]["source_key"],
            "recent_opening:nps_monthly:nps-without-business-no",
        )
        payload = json.loads(request_post.call_args.kwargs["data"])
        self.assertTrue(
            request_post.call_args.args[0].endswith(
                "/rpc/oasis_search_recent_openings_v2"
            )
        )
        self.assertEqual(payload["p_province_code"], "11")
        self.assertEqual(payload["p_province_name"], "서울특별시")
        self.assertEqual(payload["p_recent_months"], 12)
        self.assertTrue(payload["p_include_comwel_annual"])
        self.assertEqual(payload["p_district"], "마포구")
        self.assertEqual(payload["p_industries"], ["서비스업"])
        self.assertEqual(payload["p_contact_channels"], ["mobile_phone"])
        self.assertEqual(payload["p_business_type"], "stock")

    @patch("prospect_collection_service.remove_existing_prospects")
    @patch(
        "prospect_collection_service.remove_existing_customers",
        side_effect=lambda rows: (rows, 0),
    )
    @patch(
        "prospect_collection_service.load_recent_opening_candidates",
        return_value=[
            {
                "source_key": f"recent_opening:{index:010d}",
                "사업자등록번호": f"{index:010d}",
                "사업장명": f"신규 주식회사 {index}",
            }
            for index in range(150)
        ],
    )
    @patch(
        "prospect_collection_service.existing_prospect_identities",
        return_value=(set(), set(), set()),
    )
    def test_recent_opening_query_is_precomputed_and_limited(
        self,
        _identities,
        loader,
        _customers,
        remove_prospects,
    ) -> None:
        remove_prospects.side_effect = lambda rows, **_kwargs: (rows, 0)
        events: list[dict] = []

        result = collect_recent_opening_companies(
            "11",
            target_count=100,
            recent_months=6,
            include_comwel_annual=False,
            business_type="all",
            progress=events.append,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["found_count"], 100)
        self.assertEqual(result["stats"]["source_mode"], "precomputed")
        self.assertEqual(result["stats"]["discovery_type"], "recent_opening")
        self.assertEqual(events[0]["stage"], "recent_opening")
        self.assertEqual(events[1]["stage"], "recent_opening_complete")
        self.assertEqual(
            loader.call_args.kwargs["limit"],
            300,
        )
        self.assertFalse(
            loader.call_args.kwargs["include_comwel_annual"]
        )
        self.assertEqual(
            loader.call_args.kwargs["business_type"],
            "all",
        )

    def test_missing_business_number_uses_source_and_place_deduplication(
        self,
    ) -> None:
        rows = [
            {
                "source_key": "recent_opening:nps_monthly:nps-1",
                "사업자등록번호": "",
                "사업장명": "번호없는 상점",
                "주소": "서울특별시 마포구 성산로 2",
            },
            {
                "source_key": "recent_opening:nps_monthly:nps-2",
                "사업자등록번호": "",
                "사업장명": "다른 상점",
                "주소": "서울특별시 마포구 월드컵로 3",
            },
        ]

        filtered, excluded = remove_existing_prospects(
            rows,
            source_keys={"recent_opening:nps_monthly:nps-1"},
            business_nos=set(),
            company_address_keys={"다른상점|서울특별시마포구월드컵로3"},
        )

        self.assertEqual(filtered, [])
        self.assertEqual(excluded, 2)


if __name__ == "__main__":
    unittest.main()
