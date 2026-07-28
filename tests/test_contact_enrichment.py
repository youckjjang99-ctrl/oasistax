from __future__ import annotations

import unittest
from unittest.mock import patch

import contact_enrichment
import naver_web_search_client
import sales_intelligence
from website_contact_parser import extract_public_contacts, instagram_profile


class _NaverResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, query: str) -> None:
        self.query = query

    def json(self) -> dict:
        if "이메일" in self.query:
            return {
                "items": [
                    {
                        "title": "테스트기업",
                        "description": (
                            "서울특별시 강남구 contact@example.com"
                        ),
                        "link": (
                            "https://www.instagram.com/test_company/"
                        ),
                    }
                ]
            }
        phone = (
            "010-1234-5678"
            if "휴대전화" in self.query
            else "02-1234-5678"
        )
        return {
            "items": [
                {
                    "title": "테스트기업",
                    "description": f"서울특별시 강남구 업무 문의 {phone}",
                    "link": "https://example.com/contact",
                }
            ]
        }


class ContactCollectionTest(unittest.TestCase):
    def setUp(self) -> None:
        contact_enrichment._CACHE.clear()

    def test_public_page_extracts_mobile_main_and_toll_free(self) -> None:
        phones, _emails = extract_public_contacts(
            "대표 02-1234-5678 업무용 010-2222-3333 고객센터 1588-1234"
        )
        self.assertEqual(
            set(phones),
            {"02-1234-5678", "010-2222-3333", "1588-1234"},
        )

    @patch.dict(
        "os.environ",
        {"NAVER_CLIENT_ID": "id", "NAVER_CLIENT_SECRET": "secret"},
    )
    @patch("naver_web_search_client.requests.get")
    def test_naver_collects_both_phone_types(self, mocked_get) -> None:
        mocked_get.side_effect = lambda *args, **kwargs: _NaverResponse(
            kwargs["params"]["query"]
        )
        result = naver_web_search_client.search_public_phones(
            "테스트기업",
            "서울특별시 강남구",
        )
        phone_types = {
            row["phone_type"] for row in result["candidates"]
        }
        self.assertIn("company_main", phone_types)
        self.assertIn("public_business_mobile", phone_types)
        self.assertEqual(len(result["queries"]), 4)
        self.assertEqual(
            {
                row["contact_type"] for row in result["contacts"]
            },
            {"email", "instagram"},
        )

    @patch.dict(
        "os.environ",
        {"NAVER_CLIENT_ID": "id", "NAVER_CLIENT_SECRET": "secret"},
    )
    @patch("naver_web_search_client.requests.get")
    def test_naver_bulk_mode_uses_two_queries(self, mocked_get) -> None:
        mocked_get.side_effect = lambda *args, **kwargs: _NaverResponse(
            kwargs["params"]["query"]
        )
        result = naver_web_search_client.search_public_phones(
            "테스트기업",
            "서울특별시 강남구",
            query_mode="bulk",
        )
        self.assertEqual(len(result["queries"]), 2)
        self.assertIn("이메일", result["queries"][1])

    @patch.dict(
        "os.environ",
        {"NAVER_CLIENT_ID": "id", "NAVER_CLIENT_SECRET": "secret"},
    )
    @patch("naver_web_search_client.requests.get")
    def test_naver_phone_stage_skips_digital_query(self, mocked_get) -> None:
        mocked_get.side_effect = lambda *args, **kwargs: _NaverResponse(
            kwargs["params"]["query"]
        )
        result = naver_web_search_client.search_public_phones(
            "테스트기업",
            "서울특별시 강남구",
            query_mode="bulk",
            contact_stage="phone",
        )
        self.assertEqual(len(result["queries"]), 1)
        self.assertNotIn("이메일", result["queries"][0])
        self.assertEqual(result["contacts"], [])

    @patch.dict(
        "os.environ",
        {"NAVER_CLIENT_ID": "id", "NAVER_CLIENT_SECRET": "secret"},
    )
    @patch("naver_web_search_client.requests.get")
    def test_naver_digital_stage_skips_phone_queries(
        self,
        mocked_get,
    ) -> None:
        mocked_get.side_effect = lambda *args, **kwargs: _NaverResponse(
            kwargs["params"]["query"]
        )
        result = naver_web_search_client.search_public_phones(
            "테스트기업",
            "서울특별시 강남구",
            query_mode="bulk",
            contact_stage="digital",
        )
        self.assertEqual(len(result["queries"]), 1)
        self.assertIn("이메일", result["queries"][0])
        self.assertEqual(result["candidates"], [])

    def test_instagram_profile_rejects_post_and_keeps_profile(self) -> None:
        self.assertEqual(
            instagram_profile(
                "https://www.instagram.com/oasis.crm/?hl=ko"
            )[0],
            "@oasis.crm",
        )
        self.assertEqual(
            instagram_profile(
                "https://www.instagram.com/p/ABC123/"
            ),
            ("", ""),
        )

    @patch("contact_enrichment.inspect_website")
    @patch("contact_enrichment.naver_web_search_client.search_official_websites")
    @patch("contact_enrichment.localdata_contact_client.search_company")
    @patch("contact_enrichment.naver_web_search_client.search_public_phones")
    @patch("contact_enrichment.naver_web_search_client.search_company")
    @patch("contact_enrichment.kakao_local_client.search_company")
    def test_enrichment_labels_public_business_mobile_and_caches(
        self,
        kakao,
        naver_local,
        naver_phone,
        localdata,
        websites,
        inspect,
    ) -> None:
        kakao.return_value = {
            "status": "SUCCESS",
            "message": "",
            "candidates": [],
        }
        naver_local.return_value = {
            "status": "SUCCESS",
            "message": "",
            "candidates": [],
        }
        naver_phone.return_value = {
            "status": "SUCCESS",
            "message": "",
            "candidates": [
                {
                    "company_name": "테스트기업",
                    "address": "서울특별시 강남구",
                    "phone": "010-1234-5678",
                    "phone_type": "public_business_mobile",
                    "source_type": "naver_web_snippet",
                    "source_url": "https://example.com/contact",
                    "confidence": 90,
                }
            ],
        }
        localdata.return_value = {
            "status": "SUCCESS",
            "message": "",
            "services": [],
            "candidates": [],
        }
        websites.return_value = {
            "status": "SUCCESS",
            "message": "",
            "candidates": [],
        }
        inspect.return_value = {"ok": False}
        prospect = {
            "사업장명": "테스트기업",
            "주소": "서울특별시 강남구",
        }

        first = contact_enrichment.enrich_company(prospect)
        second = contact_enrichment.enrich_company(prospect)

        phone = next(
            row for row in first["contacts"] if row["contact_type"] == "phone"
        )
        self.assertEqual(phone["contact_label"], "공개 업무용 휴대전화")
        self.assertEqual(phone["verification_status"], "auto_verified")
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(kakao.call_count, 1)
        self.assertEqual(naver_local.call_count, 1)
        self.assertEqual(naver_phone.call_count, 1)

    @patch("contact_enrichment.localdata_contact_client.search_company")
    @patch("contact_enrichment.naver_web_search_client.search_public_phones")
    @patch("contact_enrichment.naver_web_search_client.search_company")
    @patch("contact_enrichment.kakao_local_client.search_company")
    def test_kakao_only_stage_does_not_call_naver(
        self,
        kakao,
        naver_local,
        naver_phone,
        localdata,
    ) -> None:
        kakao.return_value = {
            "status": "SUCCESS",
            "message": "",
            "candidates": [
                {
                    "company_name": "테스트기업",
                    "address": "서울특별시 강남구",
                    "phone": "02-1234-5678",
                    "source_type": "kakao_local",
                    "source_url": "https://place.map.kakao.com/1",
                    "confidence": 95,
                }
            ],
        }

        result = contact_enrichment.enrich_company(
            {
                "company_name": "테스트기업",
                "address": "서울특별시 강남구",
            },
            skip_naver=True,
            skip_localdata=True,
            bulk_mode=True,
            contact_stage="phone",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(
            result["contacts"][0]["contact_value"],
            "02-1234-5678",
        )
        kakao.assert_called_once()
        naver_local.assert_not_called()
        naver_phone.assert_not_called()
        localdata.assert_not_called()

    @patch(
        "sales_intelligence.localdata_contact_client.is_enabled",
        return_value=False,
    )
    @patch("sales_intelligence.naver_web_search_client.search_company")
    @patch("sales_intelligence.naver_web_search_client.search_public_phones")
    @patch("sales_intelligence.kakao_local_client.search_company")
    def test_mobile_is_selected_before_higher_score_landline(
        self,
        kakao,
        naver_phone,
        naver_local,
        _localdata_enabled,
    ) -> None:
        kakao.return_value = {
            "status": "SUCCESS",
            "message": "",
            "candidates": [
                {
                    "phone": "02-1234-5678",
                    "source_type": "kakao_local",
                    "confidence": 100,
                }
            ],
        }
        naver_phone.return_value = {
            "status": "SUCCESS",
            "message": "",
            "contacts": [],
            "candidates": [
                {
                    "phone": "010-1234-5678",
                    "source_type": "naver_web_snippet",
                    "confidence": 70,
                }
            ],
        }
        naver_local.return_value = {
            "status": "SUCCESS",
            "message": "",
            "candidates": [],
        }

        result = sales_intelligence._best_phone(
            "테스트기업",
            "서울특별시 강남구",
            "서비스업",
            allow_extended=False,
        )

        self.assertEqual(result["phone"], "010-1234-5678")


if __name__ == "__main__":
    unittest.main()
