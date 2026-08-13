from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import daum_web_search_client as client


def _mobile(middle: str, last: str) -> str:
    return "-".join(("010", middle, last))


def _landline(middle: str, last: str) -> str:
    return "-".join(("02", middle, last))


def _business_number() -> str:
    return "".join(("12345", "67890"))


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
        self.assertEqual(result["request_count"], 1)
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
        self.assertEqual(result["diagnostics"]["accepted_mobile"], 1)
        self.assertEqual(result["diagnostics"]["accepted_landline"], 1)
        query = request_get.call_args.kwargs["params"]["query"]
        self.assertIn("010", query)

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client.requests, "get")
    def test_mobile_first_falls_back_to_location_mobile_query(
        self,
        request_get,
    ) -> None:
        mobile_response = Mock(ok=True, status_code=200, text="")
        mobile_response.json.return_value = {"documents": []}
        general_response = Mock(ok=True, status_code=200, text="")
        general_response.json.return_value = {
            "documents": [{
                "title": "테스트기업 공식 안내",
                "contents": f"서울특별시 강남구 {_mobile('1234', '5678')}",
                "url": "https://example.com",
            }]
        }
        request_get.side_effect = [mobile_response, general_response]

        result = client.search_public_phones(
            "테스트기업",
            "서울특별시 강남구",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(request_get.call_count, 2)
        self.assertEqual(result["request_count"], 2)
        self.assertIn(
            "010",
            request_get.call_args_list[1].kwargs["params"]["query"],
        )
        self.assertEqual(
            [row["contact_value"] for row in result["contacts"]],
            [_mobile("1234", "5678")],
        )

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(
        client,
        "_verified_source_phones",
        return_value={_mobile("2345", "6789")},
    )
    @patch.object(client.requests, "get")
    def test_mobile_without_address_uses_distinctive_company_name(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "오아시스정책연구소 상담",
                "contents": (
                    "상담전화 " + " ".join(("+82", "10", "2345", "6789"))
                ),
                "url": "https://example.com/contact",
            }]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "오아시스정책연구소",
            "서울특별시 강남구",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(request_get.call_count, 1)
        self.assertEqual(
            result["contacts"][0]["contact_value"],
            _mobile("2345", "6789"),
        )
        self.assertEqual(
            result["contacts"][0]["metadata"]["evidence"],
            "source_page",
        )
        verify_source.assert_called_once()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_distinctive_name_alone_does_not_auto_save_mobile(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "오아시스정책연구소 관련 글",
                "contents": f"문의 {_mobile('2468', '1357')}",
                "url": "https://example.com/article",
            }]
        }
        empty = Mock(ok=True, status_code=200, text="")
        empty.json.return_value = {"documents": []}
        request_get.side_effect = [response, empty]

        result = client.search_public_phones(
            "오아시스정책연구소",
            "서울특별시 강남구",
        )

        self.assertEqual(result["contacts"], [])
        self.assertEqual(result["diagnostics"]["source_pages_checked"], 1)
        self.assertEqual(result["diagnostics"]["source_pages_verified"], 0)
        verify_source.assert_called_once()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client.requests, "get")
    def test_short_name_mobile_requires_address_or_business_number(
        self,
        request_get,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "희망 상담",
                "contents": f"문의 {_mobile('1111', '2222')}",
                "url": "https://example.com",
            }]
        }
        empty = Mock(ok=True, status_code=200, text="")
        empty.json.return_value = {"documents": []}
        request_get.side_effect = [response, empty]

        result = client.search_public_phones(
            "희망",
            "서울특별시 강남구",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["contacts"], [])
        self.assertGreater(result["diagnostics"]["name_rejected"], 0)

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client.requests, "get")
    def test_business_number_is_strong_mobile_evidence(
        self,
        request_get,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "희망 안내",
                "contents": (
                    "사업자 "
                    + "-".join(("123", "45", "67890"))
                    + " 문의 "
                    + ".".join(("010", "3333", "4444"))
                ),
                "url": "https://example.com",
            }]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "희망",
            "서울특별시 강남구",
            _business_number(),
        )

        self.assertEqual(request_get.call_count, 1)
        self.assertEqual(
            result["contacts"][0]["contact_value"],
            _mobile("3333", "4444"),
        )
        self.assertEqual(
            result["contacts"][0]["metadata"]["evidence"],
            "business_no",
        )

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_separate_number_groups_are_not_a_business_number_match(
        self,
        request_get,
        _verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "희망 안내",
                "contents": (
                    "문서 "
                    + " / ".join(("12345", "67890"))
                    + " 문의 "
                    + _mobile("3333", "4444")
                ),
                "url": "https://example.com",
            }]
        }
        empty = Mock(ok=True, status_code=200, text="")
        empty.json.return_value = {"documents": []}
        request_get.side_effect = [response, empty]

        result = client.search_public_phones(
            "희망",
            "서울특별시 강남구",
            _business_number(),
        )

        self.assertEqual(result["contacts"], [])

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
        self.assertEqual(result["request_count"], 1)
        self.assertEqual(request_get.call_count, 1)
        self.assertEqual(
            result["trace"][0]["stage"],
            "daum_web_phone",
        )


if __name__ == "__main__":
    unittest.main()
