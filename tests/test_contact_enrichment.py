from __future__ import annotations

import unittest
from unittest.mock import patch

import contact_enrichment
import naver_web_search_client
from website_contact_parser import extract_public_contacts


class _NaverResponse:
    ok = True
    status_code = 200
    text = ""

    def __init__(self, query: str) -> None:
        self.query = query

    def json(self) -> dict:
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

    @patch("contact_enrichment.inspect_website")
    @patch("contact_enrichment.naver_web_search_client.search_official_websites")
    @patch("contact_enrichment.localdata_contact_client.search_company")
    @patch("contact_enrichment.naver_web_search_client.search_public_phones")
    @patch("contact_enrichment.kakao_local_client.search_company")
    def test_enrichment_labels_public_business_mobile_and_caches(
        self,
        kakao,
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
        self.assertEqual(naver_phone.call_count, 1)


if __name__ == "__main__":
    unittest.main()
