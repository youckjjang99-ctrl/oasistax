import base64
import json
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from tilko_claim_client import (
    ClaimProviderError,
    TilkoClaimClient,
    TilkoClaimConfig,
)


def _public_key_b64() -> str:
    public_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    ).public_key()
    der = public_key.public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii")


def _client() -> TilkoClaimClient:
    return TilkoClaimClient(
        TilkoClaimConfig(
            api_key="api-key",
            rsa_public_key=_public_key_b64(),
            collection_enabled=True,
        )
    )


def _session() -> dict[str, str]:
    return {
        "Token": "token",
        "CxId": "cx",
        "TxId": "tx",
        "ReqTxId": "req",
    }


class _JsonResponse:
    ok = True
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class TilkoComwelDocumentTests(unittest.TestCase):
    @patch("tilko_claim_client.requests.post")
    def test_total_remuneration_returns_sanitized_excel(self, post):
        excel = b"PK\x03\x04" + b"xlsx-data"
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "tx-reference",
                "Result": {
                    "FileName": "..\\..\\remuneration.xlsx",
                    "ExcelData": base64.b64encode(excel).decode("ascii"),
                    "Data": [{"Seq": 1}, {"Seq": 2}],
                },
            }
        )

        document = _client().collect_comwel_total_remuneration(
            year=2025,
            identity_number="9010191234567",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
            business_number="2208162517",
            management_number="123-45-67890",
        )

        self.assertTrue(
            post.call_args.args[0].endswith(
                "/api/v2.0/KcomwelSimpleAuth/SelectBosuJeopsuList"
            )
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["UserGroupFlag"], "1")
        self.assertEqual(payload["IndividualFlag"], "1")
        self.assertEqual(payload["BoheomYear"], "2025")
        self.assertEqual(payload["GwanriNo"], "123-45-67890")
        self.assertNotEqual(payload["BusinessNumber"], "2208162517")
        self.assertNotEqual(
            payload["Auth"]["IdentityNumber"],
            "9010191234567",
        )
        self.assertEqual(payload["Auth"]["Token"], "token")
        self.assertEqual(document.content, excel)
        self.assertEqual(document.file_name, "remuneration.xlsx")
        self.assertEqual(
            document.content_type,
            (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )
        self.assertEqual(document.provider_reference, "tx-reference")
        self.assertEqual(document.facts["year"], "2025")
        self.assertEqual(document.facts["record_count"], 2)
        self.assertEqual(
            document.facts["management_numbers"],
            ["123-45-67890"],
        )
        facts = json.dumps(document.facts, ensure_ascii=False)
        self.assertNotIn("9010191234567", facts)
        self.assertNotIn("01012345678", facts)

    @patch("tilko_claim_client.requests.post")
    def test_total_remuneration_empty_result_returns_no_data_json(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "empty-remuneration-reference",
                "Result": [],
            }
        )

        document = _client().collect_comwel_total_remuneration(
            year=2025,
            identity_number="9010191234567",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
            business_number="2208162517",
        )

        self.assertEqual(document.content_type, "application/json")
        self.assertEqual(
            document.file_name,
            "comwel-total-remuneration-2025-no-data.json",
        )
        self.assertEqual(
            document.facts,
            {
                "no_data": True,
                "record_count": 0,
                "year": "2025",
            },
        )
        self.assertEqual(
            json.loads(document.content.decode("utf-8")),
            document.facts,
        )
        stored = document.content.decode("utf-8")
        self.assertNotIn("9010191234567", stored)
        self.assertNotIn("01012345678", stored)
        self.assertNotIn("홍길동", stored)

    @patch("tilko_claim_client.requests.post")
    def test_total_remuneration_7701001_returns_no_data_json(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 1,
                "TargetCode": "7701001",
                "ApiTxKey": "remuneration-no-data-reference",
                "Result": [],
            }
        )

        document = _client().collect_comwel_total_remuneration(
            year=2025,
            identity_number="9010191234567",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
            business_number="2208162517",
        )

        self.assertEqual(document.content_type, "application/json")
        self.assertEqual(
            document.file_name,
            "comwel-total-remuneration-2025-no-data.json",
        )
        self.assertEqual(
            document.facts,
            {
                "no_data": True,
                "record_count": 0,
                "year": "2025",
            },
        )
        self.assertEqual(
            json.loads(document.content.decode("utf-8")),
            document.facts,
        )

    @patch("tilko_claim_client.requests.post")
    def test_total_remuneration_nonempty_result_without_file_still_fails(
        self,
        post,
    ):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": {"Data": []},
            }
        )

        with self.assertRaises(ClaimProviderError):
            _client().collect_comwel_total_remuneration(
                year=2025,
                identity_number="9010191234567",
                user_name="홍길동",
                cellphone="01012345678",
                session=_session(),
            )

    @patch("tilko_claim_client.requests.post")
    def test_empty_result_without_success_code_still_fails(self, post):
        post.return_value = _JsonResponse({"Result": []})

        with self.assertRaises(ClaimProviderError):
            _client().collect_comwel_total_remuneration(
                year=2025,
                identity_number="9010191234567",
                user_name="홍길동",
                cellphone="01012345678",
                session=_session(),
            )

    @patch("tilko_claim_client.requests.post")
    def test_management_numbers_returns_redacted_private_json(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "mybiz-reference",
                "Result": [
                    {
                        "GwanriNo": "111-22-33333",
                        "BusinessName": "테스트 사업장",
                        "IdentityNumber": "9010191234567",
                        "UserCellphoneNumber": "01012345678",
                    },
                    {
                        "GwanriNo": "444-55-66666",
                        "UserName": "홍길동",
                        "JuminNo": "901019-1234567",
                        "HpNo": "010-8765-4321",
                    },
                ],
            }
        )

        document = _client().collect_comwel_management_numbers(
            identity_number="9010191234567",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
            business_number="2208162517",
        )

        self.assertTrue(
            post.call_args.args[0].endswith(
                "/api/v2.0/KcomwelSimpleAuth/MyBizInfo"
            )
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["UserGroupFlag"], "1")
        self.assertEqual(payload["IndividualFlag"], "1")
        self.assertNotEqual(payload["BusinessNumber"], "2208162517")
        self.assertEqual(document.file_name, "comwel-management-numbers.json")
        self.assertEqual(document.content_type, "application/json")
        self.assertEqual(document.provider_reference, "mybiz-reference")
        self.assertEqual(
            document.facts["management_numbers"],
            ["111-22-33333", "444-55-66666"],
        )
        self.assertEqual(document.facts["record_count"], 2)
        stored_json = document.content.decode("utf-8")
        self.assertIn("테스트 사업장", stored_json)
        self.assertIn("[REDACTED]", stored_json)
        self.assertNotIn("9010191234567", stored_json)
        self.assertNotIn("01012345678", stored_json)
        self.assertNotIn("010-8765-4321", stored_json)
        self.assertNotIn("홍길동", stored_json)
        facts = json.dumps(document.facts, ensure_ascii=False)
        self.assertNotIn("9010191234567", facts)
        self.assertNotIn("01012345678", facts)

    @patch("tilko_claim_client.requests.post")
    def test_workplace_rate_returns_pdf_and_safe_facts(self, post):
        pdf = b"%PDF-1.7\nworkplace-rate"
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "rate-reference",
                "PdfData": base64.b64encode(pdf).decode("ascii"),
                "Result": {
                    "FileName": "../../rate.exe",
                    "Issued": "Y",
                },
            }
        )

        document = _client().collect_comwel_workplace_rate(
            year="2024",
            identity_number="9010191234567",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
            management_number="123-45-67890",
        )

        self.assertTrue(
            post.call_args.args[0].endswith(
                "/api/v2.0/KcomwelSimpleAuth/T100110021005"
            )
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["UserGroupFlag"], "1")
        self.assertEqual(payload["IndividualFlag"], "1")
        self.assertEqual(payload["Year"], "2024")
        self.assertEqual(payload["GwanriNo"], "123-45-67890")
        self.assertEqual(document.content, pdf)
        self.assertEqual(document.file_name, "rate.pdf")
        self.assertEqual(document.content_type, "application/pdf")
        self.assertEqual(document.facts["year"], "2024")
        self.assertEqual(
            document.facts["management_numbers"],
            ["123-45-67890"],
        )
        facts = json.dumps(document.facts, ensure_ascii=False)
        self.assertNotIn("9010191234567", facts)
        self.assertNotIn("01012345678", facts)

    @patch("tilko_claim_client.requests.post")
    def test_workplace_rate_empty_result_returns_no_data_json(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "empty-rate-reference",
                "ResultData": {},
            }
        )

        document = _client().collect_comwel_workplace_rate(
            year=2024,
            identity_number="9010191234567",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
            management_number="123-45-67890",
        )

        self.assertEqual(document.content_type, "application/json")
        self.assertEqual(
            document.file_name,
            "comwel-workplace-rate-2024-no-data.json",
        )
        self.assertEqual(
            document.facts,
            {
                "no_data": True,
                "record_count": 0,
                "year": "2024",
            },
        )
        self.assertEqual(
            json.loads(document.content.decode("utf-8")),
            document.facts,
        )
        stored = document.content.decode("utf-8")
        self.assertNotIn("9010191234567", stored)
        self.assertNotIn("01012345678", stored)
        self.assertNotIn("123-45-67890", stored)

    @patch("tilko_claim_client.requests.post")
    def test_workplace_rate_nonempty_result_without_file_still_fails(
        self,
        post,
    ):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": {"Issued": "N"},
            }
        )

        with self.assertRaises(ClaimProviderError):
            _client().collect_comwel_workplace_rate(
                year=2024,
                identity_number="9010191234567",
                user_name="홍길동",
                cellphone="01012345678",
                session=_session(),
                management_number="123-45-67890",
            )

    @patch("tilko_claim_client.requests.post")
    def test_empty_result_with_provider_error_still_fails(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 1,
                "TargetCode": "COMWEL_FAILURE",
                "Result": [],
            }
        )

        with self.assertRaises(ClaimProviderError):
            _client().collect_comwel_total_remuneration(
                year=2025,
                identity_number="9010191234567",
                user_name="홍길동",
                cellphone="01012345678",
                session=_session(),
            )

    def test_management_number_collection_requires_business_number(self):
        with self.assertRaises(ClaimProviderError):
            _client().collect_comwel_management_numbers(
                identity_number="9010191234567",
                user_name="홍길동",
                cellphone="01012345678",
                session=_session(),
                business_number="",
            )

    def test_comwel_document_year_must_be_four_digits(self):
        with self.assertRaises(ClaimProviderError):
            _client().collect_comwel_total_remuneration(
                year="25",
                identity_number="9010191234567",
                user_name="홍길동",
                cellphone="01012345678",
                session=_session(),
            )


if __name__ == "__main__":
    unittest.main()
