from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import daum_web_search_client as client


class DaumWebSearchClientTest(unittest.TestCase):
    @patch.dict("os.environ", {}, clear=True)
    def test_missing_key_stops_without_request(self) -> None:
        result = client.search_public_phones(
            "테스트기업",
            "서울특별시 강남구",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "KEY_MISSING")
        self.assertEqual(result["contacts"], [])

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client.requests, "get")
    def test_collects_both_phone_types_for_matching_company(
        self,
        request_get,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [
                {
                    "title": "테스트기업 연락처",
                    "contents": (
                        "서울특별시 강남구 테헤란로 대표전화 "
                        "02-1234-5678 업무용 010-2345-6789"
                    ),
                    "url": "https://example.com/contact",
                }
            ]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "테스트기업",
            "서울특별시 강남구 테헤란로",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(request_get.call_count, 1)
        self.assertEqual(
            {row["contact_value"] for row in result["contacts"]},
            {"02-1234-5678", "010-2345-6789"},
        )
        self.assertTrue(
            all(
                row["source_type"] == "daum_web_snippet"
                for row in result["contacts"]
            )
        )

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client.requests, "get")
    def test_rejects_same_name_in_another_region(self, request_get) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [
                {
                    "title": "테스트기업 연락처",
                    "contents": "부산광역시 해운대구 대표전화 051-123-4567",
                    "url": "https://example.com/contact",
                }
            ]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "테스트기업",
            "서울특별시 강남구",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["contacts"], [])

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client.requests, "get")
    def test_quota_response_is_exposed_to_pipeline(self, request_get) -> None:
        response = Mock(ok=False, status_code=429, text="quota")
        request_get.return_value = response

        result = client.search_public_phones(
            "테스트기업",
            "서울특별시 강남구",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "HTTP_429")
        self.assertEqual(
            result["trace"][0]["stage"],
            "daum_web_phone",
        )


if __name__ == "__main__":
    unittest.main()
