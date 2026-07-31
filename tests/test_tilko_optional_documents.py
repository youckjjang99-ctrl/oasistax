from __future__ import annotations

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
    HOMETAX_AGENT_REFUND,
    ClaimProviderError,
    TilkoClaimClient,
    TilkoClaimConfig,
    provider_readiness,
)


WORKER_ENDPOINT = (
    "/api/v2.0/KcomwelSimpleAuth/ContractWorkerStatus"
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


def _config(**overrides) -> TilkoClaimConfig:
    values = {
        "api_key": "test-api-key",
        "rsa_public_key": _public_key_b64(),
        "collection_enabled": True,
    }
    values.update(overrides)
    return TilkoClaimConfig(**values)


def _refund_config(**overrides) -> TilkoClaimConfig:
    values = {
        "hometax_refund_enabled": True,
        "hometax_agent_cert_file_b64": base64.b64encode(
            b"certificate-bytes"
        ).decode("ascii"),
        "hometax_agent_key_file_b64": base64.b64encode(
            b"private-key-bytes"
        ).decode("ascii"),
        "hometax_agent_cert_password": "test-password",
    }
    values.update(overrides)
    return _config(**values)


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


class TilkoOptionalDocumentTests(unittest.TestCase):
    def test_optional_readiness_is_reported_separately(self):
        config = _config()

        readiness = provider_readiness(config)

        self.assertTrue(readiness["simple_auth_ready"])
        self.assertFalse(readiness["hometax_refund_ready"])
        self.assertFalse(readiness["comwel_worker_status_ready"])
        self.assertEqual(readiness["missing"], [])
        self.assertIn(
            "TILKO_HOMETAX_AGENT_CERT_FILE_B64",
            readiness["hometax_refund_missing"],
        )
        self.assertIn(
            "TILKO_COMWEL_WORKER_STATUS_ENDPOINT",
            readiness["comwel_worker_status_missing"],
        )

    @patch("tilko_claim_client.requests.post")
    def test_refund_uses_official_exact_endpoint_and_redacts_private_data(
        self,
        post,
    ):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "refund-reference",
                "Result": {
                    "Token": "refund-session-token",
                    "Credential": {
                        "Cookie": "refund-session-cookie",
                    },
                    "Result": [
                        {
                            "txprDscmNo": "9912311000000",
                            "accnoEncCntn": "000000000000",
                            "dpstAcntNo": "private-refund-account",
                            "rprsTxprNm": "테스트대표",
                            "refundAmount": "12000",
                            "resultMsg": {
                                "sessionMap": {
                                    "providerSession": "private-session-map"
                                }
                            },
                            "gdncFrwBrkdId": "private-guidance-id",
                            "intfId": "private-interface-id",
                            "rmtnBrkdId": "private-remittance-id",
                            "txaaId": "private-agent-id",
                            "tin": "private-taxpayer-id",
                        }
                    ]
                },
            }
        )
        encrypted_values = []

        def fake_encrypt(_key, value):
            encrypted_values.append(value)
            return f"encrypted-{len(encrypted_values)}"

        with patch(
            "tilko_claim_client._aes_encrypt",
            side_effect=fake_encrypt,
        ):
            client = TilkoClaimClient(_refund_config())
            document = client.collect_hometax_refund(
                taxpayer_number="9912311000000",
                start_date="20210101",
                end_date="20260101",
            )

        self.assertTrue(client.hometax_refund_ready)
        self.assertEqual(
            post.call_args.args[0],
            f"https://api.tilko.net{HOMETAX_AGENT_REFUND}",
        )
        payload = post.call_args.kwargs["json"]
        self.assertTrue(
            all(
                str(payload[field_name]).startswith("encrypted-")
                for field_name in (
                    "CertFile",
                    "KeyFile",
                    "CertPassword",
                    "BusinessNumber",
                    "StartDate",
                    "EndDate",
                )
            )
        )
        self.assertIn(b"certificate-bytes", encrypted_values)
        self.assertIn(b"private-key-bytes", encrypted_values)
        self.assertEqual(document.facts["record_count"], 1)
        self.assertFalse(document.facts["no_data"])
        self.assertEqual(document.facts["query_start_date"], "20210101")
        stored = document.content.decode("utf-8")
        self.assertIn("12000", stored)
        self.assertNotIn("txprDscmNo", stored)
        self.assertNotIn("resultMsg", stored)
        self.assertNotIn("9912311000000", stored)
        self.assertNotIn("000000000000", stored)
        self.assertNotIn("private-refund-account", stored)
        self.assertNotIn("테스트대표", stored)
        self.assertNotIn("refund-session-token", stored)
        self.assertNotIn("refund-session-cookie", stored)
        for internal_value in (
            "private-session-map",
            "private-guidance-id",
            "private-interface-id",
            "private-remittance-id",
            "private-agent-id",
            "private-taxpayer-id",
        ):
            self.assertNotIn(internal_value, stored)
        facts = json.dumps(document.facts, ensure_ascii=False)
        self.assertNotIn("9912311000000", facts)

    @patch("tilko_claim_client.requests.post")
    def test_refund_invalid_credential_is_rejected_before_network(self, post):
        client = TilkoClaimClient(
            _refund_config(hometax_agent_cert_file_b64="not-base64")
        )

        with self.assertRaises(ClaimProviderError):
            client.collect_hometax_refund(
                taxpayer_number="9912311000000",
                start_date="20210101",
                end_date="20260101",
            )

        post.assert_not_called()

    @patch("tilko_claim_client.requests.post")
    def test_refund_unexpected_result_shape_is_not_false_no_data(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": {"providerChangedShape": {"count": 0}},
            }
        )
        client = TilkoClaimClient(_refund_config())

        with self.assertRaises(ClaimProviderError):
            client.collect_hometax_refund(
                taxpayer_number="9912311000000",
                start_date="20210101",
                end_date="20260101",
            )

    @patch("tilko_claim_client.requests.post")
    def test_refund_unknown_nonempty_row_is_contract_drift(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": {
                    "Result": [{"providerChangedShape": {"count": 1}}]
                },
            }
        )
        client = TilkoClaimClient(_refund_config())

        with self.assertRaises(ClaimProviderError):
            client.collect_hometax_refund(
                taxpayer_number="9912311000000",
                start_date="20210101",
                end_date="20260101",
            )

    @patch("tilko_claim_client.requests.post")
    def test_unknown_hometax_agent_endpoint_is_not_allowed(self, post):
        client = TilkoClaimClient(_refund_config())

        with self.assertRaises(ClaimProviderError):
            client._post(
                "https://api.tilko.net",
                "/api/v1.0/HometaxAgent/Unknown",
                {},
                (),
            )

        post.assert_not_called()

    @patch("tilko_claim_client.requests.post")
    def test_worker_endpoint_is_contract_gated_and_fails_closed(self, post):
        client = TilkoClaimClient(
            _config(
                comwel_worker_status_enabled=True,
                comwel_worker_status_endpoint=(
                    f"{WORKER_ENDPOINT}?redirect=other"
                ),
            )
        )

        self.assertFalse(client.comwel_worker_status_ready)
        with self.assertRaises(ClaimProviderError) as raised:
            client.collect_comwel_worker_status(
                identity_number="9912311000000",
                user_name="테스트사용자",
                cellphone="01000000000",
                session=_session(),
                management_number="00000000000",
            )

        self.assertEqual(
            raised.exception.error_code,
            "COMWEL_WORKER_STATUS_API_NOT_CONFIGURED",
        )
        post.assert_not_called()

    @patch("tilko_claim_client.requests.post")
    def test_worker_structured_response_has_aggregate_facts_and_redaction(
        self,
        post,
    ):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "worker-reference",
                "Result": {
                    "sessionDto": {
                        "Token": "nested-token",
                        "CxId": "nested-cx",
                        "Credential": {
                            "CookieCollection": ["nested-cookie"],
                            "Secret": "nested-secret",
                        },
                        "outDatasetList": {
                            "dsOutList": [
                                {
                                    "GEUNROJA_NM": "근로자일",
                                    "GEUNROJA_RGNO": "9001011000000",
                                    "GEUNROJA_WONBU_NO": "private-one",
                                    "DAEPYOJA_NM": "private-representative",
                                    "DAEPYOJA_RGNO": "8001011000000",
                                    "CUSTOMER_ID": "private-customer-id",
                                    "MINWONIN_ID": "private-minwon-id",
                                    "PW": "private-password",
                                    "GY_STATUS_NM": "고용종료",
                                    "SJ_STATUS_NM": "고용종료",
                                    "GYB_JAGYEOK_CHWIDEUK_DT": "20210101",
                                    "GYB_JAGYEOK_SANGSIL_DT": "20211231",
                                    "GY_MM_AVG_BOSU_PRC": "2000000",
                                },
                                {
                                    "GEUNROJA_NM": "근로자이",
                                    "GEUNROJA_RGNO": "9001012000000",
                                    "GEUNROJA_WONBU_NO": "private-two",
                                    "GY_STATUS_NM": "고용중",
                                    "SJ_STATUS_NM": "고용중",
                                },
                            ]
                        }
                    }
                },
            }
        )
        client = TilkoClaimClient(
            _config(
                comwel_worker_status_enabled=True,
                comwel_worker_status_endpoint=WORKER_ENDPOINT,
            )
        )

        document = client.collect_comwel_worker_status(
            identity_number="9912311000000",
            user_name="테스트사용자",
            cellphone="01000000000",
            session=_session(),
            management_number="00000000000",
        )

        self.assertTrue(client.comwel_worker_status_ready)
        self.assertEqual(
            post.call_args.args[0],
            f"https://api24.tilko.net{WORKER_ENDPOINT}",
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["GwanriNo"], "00000000000")
        self.assertEqual(payload["IsBusinessClosed"], "1")
        self.assertNotEqual(payload["Auth"]["IdentityNumber"], "9912311000000")
        self.assertEqual(document.facts["record_count"], 2)
        self.assertEqual(document.facts["active_count"], 1)
        self.assertEqual(document.facts["ended_count"], 1)
        stored = document.content.decode("utf-8")
        self.assertIn("employment_insurance_status", stored)
        self.assertIn("employment_monthly_average_wage", stored)
        self.assertIn("2000000", stored)
        self.assertNotIn("GEUNROJA_NM", stored)
        for private_value in (
            "근로자일",
            "근로자이",
            "9001011000000",
            "9001012000000",
            "private-one",
            "private-two",
            "private-representative",
            "private-customer-id",
            "private-minwon-id",
            "private-password",
            "8001011000000",
            "nested-token",
            "nested-cx",
            "nested-cookie",
            "nested-secret",
        ):
            self.assertNotIn(private_value, stored)
        facts = json.dumps(document.facts, ensure_ascii=False)
        self.assertNotIn("00000000000", facts)
        self.assertNotIn("9912311000000", facts)

    @patch("tilko_claim_client.requests.post")
    def test_worker_raw_excel_is_rejected_without_returning_private_bytes(
        self,
        post,
    ):
        raw_excel = b"PK\x03\x04private-worker-workbook"
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "unsafe-worker-reference",
                "Result": {
                    "ExcelData": base64.b64encode(raw_excel).decode("ascii")
                },
            }
        )
        client = TilkoClaimClient(
            _config(
                comwel_worker_status_enabled=True,
                comwel_worker_status_endpoint=WORKER_ENDPOINT,
            )
        )

        with self.assertRaises(ClaimProviderError) as raised:
            client.collect_comwel_worker_status(
                identity_number="9912311000000",
                user_name="테스트사용자",
                cellphone="01000000000",
                session=_session(),
                management_number="00000000000",
            )

        self.assertEqual(
            raised.exception.error_code,
            "COMWEL_WORKER_STATUS_UNSAFE_EXCEL",
        )
        self.assertNotIn(raw_excel.decode("latin1"), str(raised.exception))

    @patch("tilko_claim_client.requests.post")
    def test_worker_unexpected_result_shape_is_not_false_no_data(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": {"providerChangedShape": {"count": 0}},
            }
        )
        client = TilkoClaimClient(
            _config(
                comwel_worker_status_enabled=True,
                comwel_worker_status_endpoint=WORKER_ENDPOINT,
            )
        )

        with self.assertRaises(ClaimProviderError):
            client.collect_comwel_worker_status(
                identity_number="9912311000000",
                user_name="테스트사용자",
                cellphone="01000000000",
                session=_session(),
                management_number="00000000000",
            )

    @patch("tilko_claim_client.requests.post")
    def test_worker_unknown_nonempty_row_is_contract_drift(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": {
                    "sessionDto": {
                        "outDatasetList": {
                            "dsOutList": [
                                {"providerChangedShape": "unexpected"}
                            ]
                        }
                    }
                },
            }
        )
        client = TilkoClaimClient(
            _config(
                comwel_worker_status_enabled=True,
                comwel_worker_status_endpoint=WORKER_ENDPOINT,
            )
        )

        with self.assertRaises(ClaimProviderError):
            client.collect_comwel_worker_status(
                identity_number="9912311000000",
                user_name="테스트사용자",
                cellphone="01000000000",
                session=_session(),
                management_number="00000000000",
            )

    @patch("tilko_claim_client.requests.post")
    def test_worker_structured_rows_discard_raw_excel_and_store_allowlist(
        self,
        post,
    ):
        raw_excel = b"PK\x03\x04private-worker-workbook"
        encoded_excel = base64.b64encode(raw_excel).decode("ascii")
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": {
                    "ExcelData": encoded_excel,
                    "sessionDto": {
                        "outDatasetList": {
                            "dsOutList": [
                                {
                                    "GEUNROJA_NM": "비공개근로자",
                                    "GEUNROJA_RGNO": "9001011000000",
                                    "GY_STATUS_NM": "고용중",
                                    "GYB_JAGYEOK_CHWIDEUK_DT": "20250101",
                                }
                            ]
                        }
                    },
                },
            }
        )
        client = TilkoClaimClient(
            _config(
                comwel_worker_status_enabled=True,
                comwel_worker_status_endpoint=WORKER_ENDPOINT,
            )
        )

        document = client.collect_comwel_worker_status(
            identity_number="9912311000000",
            user_name="테스트사용자",
            cellphone="01000000000",
            session=_session(),
            management_number="00000000000",
        )

        stored = document.content.decode("utf-8")
        self.assertIn("20250101", stored)
        self.assertIn("고용중", stored)
        self.assertNotIn(encoded_excel, stored)
        self.assertNotIn("비공개근로자", stored)
        self.assertNotIn("9001011000000", stored)

    @patch("tilko_claim_client.requests.post")
    def test_worker_explicit_empty_rows_with_excel_is_valid_no_data(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": {
                    "ExcelData": base64.b64encode(
                        b"PK\x03\x04empty-worker-workbook"
                    ).decode("ascii"),
                    "sessionDto": {
                        "outDatasetList": {"dsOutList": []}
                    },
                },
            }
        )
        client = TilkoClaimClient(
            _config(
                comwel_worker_status_enabled=True,
                comwel_worker_status_endpoint=WORKER_ENDPOINT,
            )
        )

        document = client.collect_comwel_worker_status(
            identity_number="9912311000000",
            user_name="테스트사용자",
            cellphone="01000000000",
            session=_session(),
            management_number="00000000000",
        )

        self.assertTrue(document.facts["no_data"])
        self.assertEqual(document.facts["record_count"], 0)
        self.assertEqual(json.loads(document.content)["workers"], [])


if __name__ == "__main__":
    unittest.main()
