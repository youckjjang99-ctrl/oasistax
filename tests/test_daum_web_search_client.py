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
        self.assertEqual(request_get.call_count, 2)
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
        self.assertEqual(len(result["review_candidates"]), 1)
        self.assertEqual(
            result["review_candidates"][0]["mobile_phone"],
            _mobile("2468", "1357"),
        )
        self.assertLess(result["review_candidates"][0]["confidence"], 85)
        self.assertEqual(
            result["diagnostics"]["review_mobile_candidates"],
            1,
        )
        verify_source.assert_called_once()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_distinctive_name_and_region_token_accepts_mobile(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "블루하버컨설팅 연락처",
                "contents": "강남구 문의 " + _mobile("1357", "2468"),
                "url": "https://blueharbor.example/contact",
            }]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "블루하버컨설팅",
            "서울특별시 강남구 테헤란로 123",
        )

        self.assertEqual(request_get.call_count, 1)
        self.assertEqual(
            [row["contact_value"] for row in result["contacts"]],
            [_mobile("1357", "2468")],
        )
        self.assertEqual(
            result["contacts"][0]["metadata"]["evidence"],
            "name_and_region",
        )
        self.assertEqual(
            result["diagnostics"]["accepted_mobile_name_and_region"],
            1,
        )
        verify_source.assert_not_called()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_same_mobile_on_two_domains_is_independent_evidence(
        self,
        request_get,
        verify_source,
    ) -> None:
        phone = _mobile("2468", "1357")
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [
                {
                    "title": "BlueHarborConsulting contact",
                    "contents": f"Public inquiry {phone}",
                    "url": "https://directory-one.example/company",
                },
                {
                    "title": "BlueHarborConsulting guide",
                    "contents": f"Representative inquiry {phone}",
                    "url": "https://directory-two.test/company",
                },
            ]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "BlueHarborConsulting",
            "Seoul Gangnam Teheran 123",
        )

        self.assertEqual(request_get.call_count, 1)
        self.assertEqual(
            [row["contact_value"] for row in result["contacts"]],
            [phone],
        )
        self.assertEqual(
            result["contacts"][0]["metadata"]["evidence"],
            "independent_sources",
        )
        self.assertEqual(
            result["diagnostics"][
                "accepted_mobile_independent_sources"
            ],
            1,
        )
        verify_source.assert_not_called()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_same_domain_does_not_count_as_independent_evidence(
        self,
        request_get,
        verify_source,
    ) -> None:
        phone = _mobile("1122", "3344")
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [
                {
                    "title": "BlueHarborConsulting contact",
                    "contents": f"Public inquiry {phone}",
                    "url": "https://a.directory.example.com/company",
                },
                {
                    "title": "BlueHarborConsulting guide",
                    "contents": f"Representative inquiry {phone}",
                    "url": "https://b.directory.example.com/company",
                },
            ]
        }
        empty = Mock(ok=True, status_code=200, text="")
        empty.json.return_value = {"documents": []}
        request_get.side_effect = [response, empty]

        result = client.search_public_phones(
            "BlueHarborConsulting",
            "Seoul Gangnam Teheran 123",
        )

        self.assertEqual(result["contacts"], [])
        self.assertEqual(request_get.call_count, 2)
        self.assertGreaterEqual(verify_source.call_count, 1)
        self.assertLessEqual(verify_source.call_count, 2)

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_checks_up_to_three_source_pages(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [
                {
                    "title": "BlueHarborConsulting contact",
                    "contents": f"Inquiry {_mobile(str(index) * 4, '7788')}",
                    "url": f"https://source{index}.example/contact",
                }
                for index in range(1, 4)
            ]
        }
        empty = Mock(ok=True, status_code=200, text="")
        empty.json.return_value = {"documents": []}
        request_get.side_effect = [response, empty]

        result = client.search_public_phones(
            "BlueHarborConsulting",
            "Seoul Gangnam Teheran 123",
        )

        self.assertEqual(result["contacts"], [])
        self.assertEqual(result["diagnostics"]["source_pages_checked"], 3)
        self.assertEqual(verify_source.call_count, 3)

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_body_only_company_name_does_not_auto_verify_mobile(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "다른업체 연락처",
                "contents": (
                    "블루하버컨설팅 서울 강남구 문의 "
                    + _mobile("1212", "3434")
                ),
                "url": "https://other.example/contact",
            }]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "블루하버컨설팅",
            "서울특별시 강남구",
        )

        self.assertEqual(result["contacts"], [])
        self.assertGreater(result["diagnostics"]["name_rejected"], 0)
        verify_source.assert_not_called()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_conflicting_broad_region_rejects_name_and_region(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "블루하버컨설팅 연락처",
                "contents": (
                    "부산광역시 강남구 문의 "
                    + _mobile("2323", "4545")
                ),
                "url": "https://blueharbor.example/contact",
            }]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "블루하버컨설팅",
            "서울특별시 강남구",
        )

        self.assertEqual(result["contacts"], [])
        self.assertEqual(result["review_candidates"], [])
        verify_source.assert_not_called()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_multiple_broad_regions_do_not_auto_verify_mobile(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "블루하버컨설팅 연락처",
                "contents": (
                    "서울특별시 강남구 / 부산광역시 지점 문의 "
                    + _mobile("2424", "4646")
                ),
                "url": "https://blueharbor.example/contact",
            }]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "블루하버컨설팅",
            "서울특별시 강남구",
        )

        self.assertEqual(result["contacts"], [])
        verify_source.assert_not_called()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_ambiguous_local_region_requires_matching_broad_region(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "블루하버컨설팅 연락처",
                "contents": (
                    "부산광역시 중구 문의 "
                    + _mobile("3434", "5656")
                ),
                "url": "https://blueharbor.example/contact",
            }]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "블루하버컨설팅",
            "서울특별시 중구",
        )

        self.assertEqual(result["contacts"], [])
        verify_source.assert_not_called()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_later_business_number_upgrades_weak_duplicate(
        self,
        request_get,
        verify_source,
    ) -> None:
        phone = _mobile("4545", "6767")
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [
                {
                    "title": "블루하버컨설팅 연락처",
                    "contents": f"강남구 문의 {phone}",
                    "url": "https://weak.example/contact",
                },
                {
                    "title": "블루하버컨설팅 공식",
                    "contents": (
                        "사업자등록번호 123-45-67890 문의 " + phone
                    ),
                    "url": "https://strong.example/contact",
                },
            ]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "블루하버컨설팅",
            "서울특별시 강남구 테헤란로",
            _business_number(),
        )

        self.assertEqual(len(result["contacts"]), 1)
        self.assertEqual(
            result["contacts"][0]["metadata"]["evidence"],
            "business_no",
        )
        self.assertEqual(
            result["contacts"][0]["source_url"],
            "https://strong.example/contact",
        )
        self.assertEqual(result["diagnostics"]["accepted_mobile"], 1)
        self.assertEqual(
            result["diagnostics"]["accepted_mobile_business_no"],
            1,
        )
        self.assertEqual(
            result["diagnostics"]["accepted_mobile_name_and_region"],
            0,
        )
        verify_source.assert_not_called()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_multiple_mobiles_in_one_document_require_more_evidence(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "블루하버컨설팅 연락처",
                "contents": (
                    "서울특별시 강남구 대표 "
                    + _mobile("2525", "4747")
                    + " 작성자 "
                    + _mobile("3636", "5858")
                ),
                "url": "https://blueharbor.example/contact",
            }]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "블루하버컨설팅",
            "서울특별시 강남구",
        )

        self.assertEqual(result["contacts"], [])
        self.assertEqual(result["diagnostics"]["accepted_mobile"], 0)
        self.assertEqual(len(result["review_candidates"]), 2)
        self.assertTrue(all(
            int(row["evidence"]["document_mobile_count"]) == 2
            for row in result["review_candidates"]
        ))
        self.assertGreaterEqual(verify_source.call_count, 1)
        self.assertLessEqual(verify_source.call_count, 2)

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones")
    @patch.object(client.requests, "get")
    def test_later_strong_source_candidate_is_not_starved(
        self,
        request_get,
        verify_source,
    ) -> None:
        strong_phone = _mobile("8888", "9999")
        documents = [
            {
                "title": "BlueHarborConsulting 관련 글",
                "contents": f"문의 {_mobile(str(index) * 4, '1111')}",
                "url": f"https://weak{index}.example/contact",
            }
            for index in range(1, 4)
        ]
        documents.append({
            "title": "BlueHarborConsulting",
            "contents": f"문의 {strong_phone}",
            "url": "https://strong.example/contact",
        })
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {"documents": documents}
        empty = Mock(ok=True, status_code=200, text="")
        empty.json.return_value = {"documents": []}
        request_get.side_effect = [response, empty]
        verify_source.side_effect = lambda url, *_args, **_kwargs: (
            {strong_phone} if "strong.example" in url else set()
        )

        result = client.search_public_phones(
            "BlueHarborConsulting",
            "서울특별시 강남구",
        )

        self.assertEqual(
            [row["contact_value"] for row in result["contacts"]],
            [strong_phone],
        )
        self.assertIn(
            "strong.example",
            verify_source.call_args_list[0].args[0],
        )
        self.assertLessEqual(
            result["diagnostics"]["source_pages_checked"],
            5,
        )

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_final_diagnostics_count_unique_phone_once(
        self,
        request_get,
        verify_source,
    ) -> None:
        phone = _mobile("5656", "7878")
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [
                {
                    "title": "BlueHarborConsulting 안내",
                    "contents": f"문의 {phone}",
                    "url": "https://one.example/contact",
                },
                {
                    "title": "BlueHarborConsulting 소식",
                    "contents": f"문의 {phone}",
                    "url": "https://two.test/contact",
                },
                {
                    "title": "BlueHarborConsulting",
                    "contents": f"서울특별시 강남구 문의 {phone}",
                    "url": "https://official.example/contact",
                },
            ]
        }
        request_get.return_value = response

        result = client.search_public_phones(
            "BlueHarborConsulting",
            "서울특별시 강남구",
        )

        self.assertEqual(len(result["contacts"]), 1)
        self.assertEqual(
            result["contacts"][0]["metadata"]["evidence"],
            "address",
        )
        self.assertEqual(result["diagnostics"]["accepted_mobile"], 1)
        self.assertEqual(
            result["diagnostics"]["accepted_mobile_address"],
            1,
        )
        self.assertEqual(
            result["diagnostics"][
                "accepted_mobile_independent_sources"
            ],
            0,
        )
        verify_source.assert_not_called()

    @patch.dict("os.environ", {"KAKAO_REST_API_KEY": "test-key"})
    @patch.object(client, "_verified_source_phones", return_value=set())
    @patch.object(client.requests, "get")
    def test_short_name_keeps_strong_address_requirement(
        self,
        request_get,
        verify_source,
    ) -> None:
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "documents": [{
                "title": "ABC contact",
                "contents": (
                    "Gangnam Busan Blog Office "
                    + _mobile("9988", "7766")
                ),
                "url": "https://abc.example/contact",
            }]
        }
        empty = Mock(ok=True, status_code=200, text="")
        empty.json.return_value = {"documents": []}
        request_get.side_effect = [response, empty]

        result = client.search_public_phones(
            "ABC",
            "Seoul Gangnam Teheran 123",
        )

        self.assertEqual(result["contacts"], [])
        verify_source.assert_not_called()

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
