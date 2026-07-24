from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import localdata_contact_client
import licensed_business_sync
from korea_regions import (
    ALL_DISTRICTS,
    district_label,
    district_options,
    matches_region,
    region_query,
    resolve_region,
)


class _Response:
    ok = True
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "정상"},
                "body": {
                    "totalCount": 2,
                    "items": [
                        {
                            "MNG_NO": "SEOUL-1",
                            "BPLC_NM": "서울업체",
                            "ROAD_NM_ADDR": "서울특별시 강남구 테헤란로 1",
                            "SALS_STTS_CD": "01",
                        },
                        {
                            "MNG_NO": "BUSAN-1",
                            "BPLC_NM": "부산업체",
                            "ROAD_NM_ADDR": "부산광역시 강남구 가상로 1",
                            "SALS_STTS_CD": "01",
                        },
                    ],
                },
            }
        }


class KoreaRegionsTest(unittest.TestCase):
    def test_hierarchy_labels_and_options(self) -> None:
        self.assertEqual(district_label("서울특별시"), "구·군")
        self.assertEqual(district_label("경기도"), "시·군")
        self.assertIn("강남구", district_options("서울특별시"))
        self.assertIn("수원시", district_options("경기도"))

    def test_region_matching_supports_former_province_names(self) -> None:
        self.assertTrue(
            matches_region(
                "강원도 춘천시 중앙로 1",
                "강원특별자치도",
                "춘천시",
            )
        )
        self.assertTrue(
            matches_region(
                "전라북도 전주시 완산구 효자로 1",
                "전북특별자치도",
                "전주시",
            )
        )
        self.assertFalse(
            matches_region(
                "광주광역시 북구 무등로 1",
                "경기도",
                "광주시",
            )
        )
        self.assertFalse(
            matches_region(
                "경기도 광주시 경안로 1",
                "광주광역시",
            )
        )

    def test_region_resolution(self) -> None:
        self.assertEqual(
            resolve_region("경기도 수원시 영통구 광교로 1"),
            ("경기도", "수원시"),
        )
        self.assertEqual(
            region_query("서울특별시", ALL_DISTRICTS),
            "서울특별시",
        )
        self.assertEqual(
            region_query("서울특별시", "강남구"),
            "서울특별시 강남구",
        )


class LicenseApiRegionTest(unittest.TestCase):
    @patch.dict(os.environ, {"DATA_GO_KR_SERVICE_KEY": "test-key"})
    @patch("localdata_contact_client.requests.get")
    def test_api_query_and_response_region_filter(self, mock_get) -> None:
        mock_get.return_value = _Response()
        service_key = next(iter(localdata_contact_client.SERVICES))

        result = localdata_contact_client.fetch_business_page(
            service_key,
            province="서울특별시",
            district="강남구",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["raw_received_count"], 2)
        self.assertEqual(result["region_filtered_count"], 1)
        self.assertEqual(
            [item["company_name"] for item in result["items"]],
            ["서울업체"],
        )
        self.assertEqual(result["items"][0]["province"], "서울특별시")
        self.assertEqual(result["items"][0]["district"], "강남구")
        self.assertEqual(
            mock_get.call_args.kwargs["params"]["cond[ROAD_NM_ADDR::LIKE]"],
            "서울특별시 강남구",
        )

    @patch("licensed_business_sync.save_sync_run")
    @patch("licensed_business_sync.save_businesses", return_value=0)
    @patch("licensed_business_sync.localdata_contact_client.fetch_business_page")
    def test_sync_continues_after_region_filter_empties_full_page(
        self,
        mock_fetch,
        _mock_save,
        _mock_run,
    ) -> None:
        mock_fetch.side_effect = [
            {
                "ok": True,
                "items": [],
                "raw_received_count": 1000,
                "region_filtered_count": 1000,
            },
            {
                "ok": True,
                "items": [],
                "raw_received_count": 1,
                "region_filtered_count": 1,
            },
        ]
        service_key = next(iter(localdata_contact_client.SERVICES))

        result = licensed_business_sync.sync_services(
            [service_key],
            max_pages_per_service=3,
            rows_per_page=1000,
            province="서울특별시",
            district="강남구",
        )

        self.assertEqual(mock_fetch.call_count, 2)
        self.assertEqual(result["raw_received"], 1001)
        self.assertEqual(result["region_filtered"], 1001)


if __name__ == "__main__":
    unittest.main()
