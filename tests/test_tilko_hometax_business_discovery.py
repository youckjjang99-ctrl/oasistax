import base64
import json
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from tilko_claim_client import TilkoClaimClient, TilkoClaimConfig


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


class TilkoHometaxBusinessDiscoveryTests(unittest.TestCase):
    @patch("tilko_claim_client.requests.post")
    def test_discovers_only_exact_business_number_fields(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "business-info-reference",
                "Result": {
                    "Rows": [
                        {
                            "txprDscmNo": "220-81-62517",
                            "txprNm": "  테스트\u0000 사업장  ",
                            "txprStatNm": " 계속사업자 ",
                            "tin": "9999999999",
                            "txprDscmNoEncCntn": "1111111111",
                        },
                        {
                            "BusinessNumber": "120 88 00767",
                            "BusinessName": "두번째 사업장",
                            "BusinessStatus": "정상",
                        },
                        {
                            "businessnumber": "3333333333",
                            "EncryptedBusinessNumber": "4444444444",
                        },
                        {"txprDscmNo": "123456789"},
                        {"BusinessNumber": "12345678901"},
                    ]
                },
            }
        )

        discovery = _client().discover_hometax_businesses(
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
        )

        self.assertTrue(
            post.call_args.args[0].endswith(
                "/api/v2.0/HometaxSimpleAuth/MyBizInfo"
            )
        )
        self.assertEqual(
            [candidate.business_number for candidate in discovery.candidates],
            ["2208162517", "1208800767"],
        )
        self.assertEqual(
            discovery.candidates[0].business_name,
            "테스트 사업장",
        )
        self.assertEqual(
            discovery.candidates[0].business_status,
            "계속사업자",
        )
        self.assertEqual(discovery.document.facts["record_count"], 2)
        self.assertEqual(
            discovery.document.file_name,
            "hometax-business-registration-list.json",
        )
        self.assertEqual(
            discovery.document.content_type,
            "application/json",
        )
        self.assertEqual(
            discovery.document.provider_reference,
            "business-info-reference",
        )

        stored = discovery.document.content.decode("utf-8")
        self.assertNotIn("2208162517", stored)
        self.assertNotIn("1208800767", stored)
        self.assertNotIn("9999999999", stored)
        self.assertNotIn("1111111111", stored)
        self.assertNotIn("3333333333", stored)
        self.assertNotIn("4444444444", stored)
        safe_document = json.loads(stored)
        self.assertEqual(
            safe_document["businesses"][0]["business_number_masked"],
            "220-**-*****",
        )

    @patch("tilko_claim_client.requests.post")
    def test_includes_session_and_encrypts_only_required_auth_fields(
        self,
        post,
    ):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": {
                    "txprDscmNo": "2208162517",
                },
            }
        )

        _client().discover_hometax_businesses(
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
        )

        auth = post.call_args.kwargs["json"]["Auth"]
        encrypted_plaintext = {
            "BirthDate": "901019",
            "UserName": "홍길동",
            "UserCellphoneNumber": "01012345678",
        }
        self.assertEqual(
            set(auth),
            {
                *encrypted_plaintext,
                "PrivateAuthType",
                *_session(),
            },
        )
        for field_name, plaintext in encrypted_plaintext.items():
            self.assertTrue(auth[field_name])
            self.assertNotEqual(auth[field_name], plaintext)
        self.assertEqual(auth["PrivateAuthType"], "0")
        for field_name, plaintext in _session().items():
            self.assertEqual(auth[field_name], plaintext)

    @patch("tilko_claim_client.requests.post")
    def test_merges_duplicate_candidates_without_raw_response_storage(
        self,
        post,
    ):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ResultData": [
                    {
                        "txprDscmNo": "2208162517",
                        "BusinessName": "",
                        "SecretPayload": "do-not-store",
                    },
                    {
                        "BusinessNumber": "2208162517",
                        "BusinessName": "병합 사업장",
                        "StatusName": "정상",
                    },
                ],
            }
        )

        discovery = _client().discover_hometax_businesses(
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
        )

        self.assertEqual(len(discovery.candidates), 1)
        self.assertEqual(
            discovery.candidates[0].business_name,
            "병합 사업장",
        )
        self.assertEqual(
            discovery.candidates[0].business_status,
            "정상",
        )
        self.assertNotIn(
            "do-not-store",
            discovery.document.content.decode("utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
