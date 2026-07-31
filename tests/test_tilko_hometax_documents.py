from __future__ import annotations

import base64
import json
import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from claim_correction_center import (
    _claim_collection_scope_fingerprint,
    _claim_collection_variant_key,
    _collect_supported_hometax_documents,
)
from tilko_claim_client import (
    HOMETAX_CLOSURE_CERTIFICATE,
    HOMETAX_HOST,
    HOMETAX_INCOME_TAX_HELP,
    HOMETAX_INCOME_TAX_RETURN,
    ClaimProviderError,
    CollectedClaimDocument,
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


def _document(year: int | None = None) -> CollectedClaimDocument:
    return CollectedClaimDocument(
        content=json.dumps(
            {"year": year, "record_count": 1},
            sort_keys=True,
        ).encode("utf-8"),
        file_name=f"hometax-document-{year or 'current'}.json",
        content_type="application/json",
        provider_reference=f"provider-{year or 'current'}",
        facts={"year": str(year)} if year else {},
    )


class TilkoHometaxDocumentClientTests(unittest.TestCase):
    @patch("tilko_claim_client.requests.post")
    def test_income_tax_return_accepts_transient_taxpayer_number(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "taxpayer-return-reference",
                "Result": [],
                "BinaryResult": [],
            }
        )
        identity_number = "9010191234567"

        document = _client().collect_hometax_income_tax_return(
            year=2024,
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            business_number=identity_number,
            session=_session(),
        )

        payload = post.call_args.kwargs["json"]
        self.assertNotEqual(payload["BusinessNumber"], identity_number)
        self.assertNotIn(identity_number, json.dumps(payload))
        self.assertNotIn(identity_number, json.dumps(document.facts))
        self.assertEqual(
            document.facts["query_strategy"],
            "filing_year_taxpayer_v3",
        )

    @patch("tilko_claim_client.requests.post")
    def test_income_tax_return_uses_hometax_v1_and_encrypts_payload(
        self,
        post,
    ):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "income-return-reference",
                "Result": [],
                "BinaryResult": [],
            }
        )

        document = _client().collect_hometax_income_tax_return(
            year=2024,
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            business_number="2208162517",
            session=_session(),
        )

        self.assertEqual(
            post.call_args.args[0],
            f"{HOMETAX_HOST}{HOMETAX_INCOME_TAX_RETURN}",
        )
        payload = post.call_args.kwargs["json"]
        self.assertNotIn("Auth", payload)
        self.assertEqual(payload["StartDate"], "20250101")
        self.assertEqual(payload["EndDate"], "20251231")
        self.assertEqual(payload["PrivateAuthType"], "0")
        for field_name, plaintext in _session().items():
            self.assertEqual(payload[field_name], plaintext)
        encrypted_plaintext = {
            "BirthDate": "901019",
            "UserName": "홍길동",
            "UserCellphoneNumber": "01012345678",
            "BusinessNumber": "2208162517",
        }
        for field_name, plaintext in encrypted_plaintext.items():
            self.assertTrue(payload[field_name])
            self.assertNotEqual(payload[field_name], plaintext)
        self.assertEqual(document.content_type, "application/json")
        self.assertTrue(document.facts["no_data"])
        self.assertEqual(document.facts["year"], "2024")
        self.assertEqual(document.facts["filing_year"], "2025")
        self.assertEqual(document.facts["query_strategy"], "filing_year_v2")

    @patch("tilko_claim_client.requests.post")
    def test_income_tax_return_collects_downloadable_pdf(self, post):
        pdf = b"%PDF-1.7\nincome-tax-return"
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "income-return-pdf-reference",
                "Result": [
                    {
                        "txnrmStrtDt": "20240101",
                        "txnrmEndDt": "20241231",
                    }
                ],
                "BinaryResult": [
                    {
                        "FileName": "../income-return.pdf",
                        "FileExtension": "pdf",
                        "Result": base64.b64encode(pdf).decode("ascii"),
                    }
                ],
            }
        )

        document = _client().collect_hometax_income_tax_return(
            year=2024,
            birth_date="19901019",
            user_name="홍길동",
            cellphone="01012345678",
            business_number="2208162517",
            session=_session(),
        )

        self.assertEqual(document.content, pdf)
        self.assertEqual(document.content_type, "application/pdf")
        self.assertEqual(document.file_name, "income-return.pdf")
        self.assertEqual(document.facts["year"], "2024")
        self.assertEqual(document.facts["filing_year"], "2025")
        self.assertEqual(document.facts["query_strategy"], "filing_year_v2")
        self.assertEqual(document.facts["pdf_count"], 1)

    @patch("tilko_claim_client.requests.post")
    def test_income_tax_return_filters_other_tax_years(self, post):
        selected_pdf = b"%PDF-1.7\nselected-tax-year"
        other_pdf = b"%PDF-1.7\nother-tax-year"
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "mixed-income-return-reference",
                "Result": [
                    {
                        "txnrmStrtDt": "20240101",
                        "txnrmEndDt": "20241231",
                    },
                    {
                        "txnrmStrtDt": "20230101",
                        "txnrmEndDt": "20231231",
                    },
                ],
                "BinaryResult": [
                    {
                        "FileName": "selected.pdf",
                        "FileExtension": "pdf",
                        "Result": base64.b64encode(selected_pdf).decode(
                            "ascii"
                        ),
                    },
                    {
                        "FileName": "other.pdf",
                        "FileExtension": "pdf",
                        "Result": base64.b64encode(other_pdf).decode("ascii"),
                    },
                ],
            }
        )

        document = _client().collect_hometax_income_tax_return(
            year=2024,
            birth_date="19901019",
            user_name="홍길동",
            cellphone="01012345678",
            business_number="2208162517",
            session=_session(),
        )

        self.assertEqual(document.content, selected_pdf)
        self.assertEqual(document.file_name, "selected.pdf")
        self.assertTrue(document.facts["tax_year_verified"])
        self.assertEqual(
            document.facts["discarded_mismatched_records"],
            1,
        )

    @patch("tilko_claim_client.requests.post")
    def test_income_tax_return_rejects_unverified_tax_year(self, post):
        pdf = b"%PDF-1.7\nunverified-tax-year"
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": [{"TaxYear": "2024"}],
                "BinaryResult": [
                    {
                        "FileName": "unverified.pdf",
                        "FileExtension": "pdf",
                        "Result": base64.b64encode(pdf).decode("ascii"),
                    }
                ],
            }
        )

        with self.assertRaises(ClaimProviderError):
            _client().collect_hometax_income_tax_return(
                year=2024,
                birth_date="19901019",
                user_name="홍길동",
                cellphone="01012345678",
                business_number="2208162517",
                session=_session(),
            )

    @patch("tilko_claim_client.date")
    @patch("tilko_claim_client.requests.post")
    def test_income_tax_return_caps_current_filing_year_at_today(
        self,
        post,
        mocked_date,
    ):
        mocked_date.side_effect = lambda *args, **kwargs: date(
            *args,
            **kwargs,
        )
        mocked_date.today.return_value = date(2026, 7, 31)
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "Result": [],
                "BinaryResult": [],
            }
        )

        _client().collect_hometax_income_tax_return(
            year=2025,
            birth_date="19901019",
            user_name="홍길동",
            cellphone="01012345678",
            business_number="2208162517",
            session=_session(),
        )

        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["StartDate"], "20260101")
        self.assertEqual(payload["EndDate"], "20260731")

    @patch("tilko_claim_client.requests.post")
    def test_income_tax_help_collects_redacted_year_json(self, post):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "income-help-reference",
                "Result": [
                    {
                        "TaxYear": "2025",
                        "IncomeAmount": 123456,
                        "UserName": "홍길동",
                        "Nested": {
                            "UserCellphoneNumber": "01012345678",
                        },
                    }
                ],
            }
        )

        document = _client().collect_hometax_income_tax_help(
            year=2025,
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
        )

        self.assertEqual(
            post.call_args.args[0],
            f"{HOMETAX_HOST}{HOMETAX_INCOME_TAX_HELP}",
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["Year"], "2025")
        auth = payload["Auth"]
        for field_name, plaintext in {
            "BirthDate": "901019",
            "UserName": "홍길동",
            "UserCellphoneNumber": "01012345678",
        }.items():
            self.assertTrue(auth[field_name])
            self.assertNotEqual(auth[field_name], plaintext)
        for field_name, plaintext in _session().items():
            self.assertEqual(auth[field_name], plaintext)

        self.assertEqual(document.content_type, "application/json")
        self.assertEqual(document.provider_reference, "income-help-reference")
        self.assertEqual(document.facts["year"], "2025")
        self.assertEqual(document.facts["record_count"], 1)
        stored = document.content.decode("utf-8")
        self.assertIn("[REDACTED]", stored)
        self.assertNotIn("홍길동", stored)
        self.assertNotIn("01012345678", stored)

    @patch("tilko_claim_client.requests.post")
    def test_closure_certificate_collects_pdf(self, post):
        pdf = b"%PDF-1.7\nclosure-certificate"
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "closure-reference",
                "Result": [
                    {
                        "FileName": "../../closure.pdf",
                        "PdfData": base64.b64encode(pdf).decode("ascii"),
                    }
                ],
            }
        )

        document = _client().collect_hometax_closure_certificate(
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            business_number="2208162517",
            session=_session(),
        )

        self.assertEqual(
            post.call_args.args[0],
            f"{HOMETAX_HOST}{HOMETAX_CLOSURE_CERTIFICATE}",
        )
        payload = post.call_args.kwargs["json"]
        self.assertNotEqual(payload["BusinessNumber"], "2208162517")
        self.assertEqual(payload["IssueType"], "99")
        self.assertEqual(payload["Organization"], "99")
        self.assertEqual(document.content, pdf)
        self.assertEqual(document.content_type, "application/pdf")
        self.assertEqual(document.file_name, "closure.pdf")

    @patch("tilko_claim_client.requests.post")
    def test_closure_certificate_empty_result_is_downloadable_no_data_json(
        self,
        post,
    ):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "empty-closure-reference",
                "Result": [],
            }
        )

        document = _client().collect_hometax_closure_certificate(
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            business_number="2208162517",
            session=_session(),
        )

        self.assertEqual(document.content_type, "application/json")
        self.assertTrue(document.file_name.endswith("-no-data.json"))
        self.assertEqual(
            document.facts,
            {
                "no_data": True,
                "no_data_reason": "active_business_no_closure",
                "record_count": 0,
            },
        )
        self.assertEqual(
            json.loads(document.content.decode("utf-8")),
            document.facts,
        )

    @patch("tilko_claim_client.requests.post")
    def test_closure_certificate_8801015_returns_no_data_json(
        self,
        post,
    ):
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 1,
                "TargetCode": "8801015",
                "ApiTxKey": "closure-no-data-reference",
                "Result": [],
            }
        )

        document = _client().collect_hometax_closure_certificate(
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            business_number="2208162517",
            session=_session(),
        )

        self.assertEqual(
            post.call_args.args[0],
            f"{HOMETAX_HOST}{HOMETAX_CLOSURE_CERTIFICATE}",
        )
        self.assertEqual(document.content_type, "application/json")
        self.assertEqual(
            document.file_name,
            "hometax-closure-certificate-no-data.json",
        )
        self.assertEqual(
            document.facts,
            {
                "no_data": True,
                "no_data_reason": "active_business_no_closure",
                "record_count": 0,
            },
        )
        self.assertEqual(
            json.loads(document.content.decode("utf-8")),
            document.facts,
        )


@patch.dict(
    "os.environ",
    {
        "CLAIM_DOCUMENT_VARIANT_KEY": (
            "claim-scope-test-secret-" + ("x" * 32)
        )
    },
)
class TilkoHometaxAnnualCollectionTests(unittest.TestCase):
    def test_seven_year_rows_are_stored_by_year_and_ready_years_are_skipped(
        self,
    ):
        years = list(range(2019, 2026))
        scope_fingerprint = _claim_collection_scope_fingerprint(
            "case-1",
            "business",
            "2208162517",
        )
        repository = MagicMock()
        collection_key = _claim_collection_variant_key(
            "case-1",
            "business",
            "2208162517",
        )
        repository.list_documents.return_value = [
            {
                "source": "hometax",
                "document_code": "hometax_income_tax_help",
                "period_year": year,
                "status": "ready" if year == 2022 else "auth_pending",
            }
            for year in years
        ] + [
            {
                "source": "hometax",
                "document_code": "hometax_income_tax_return",
                "period_year": year,
                "status": "auth_pending",
            }
            for year in years
        ] + [
            {
                "source": "hometax",
                "document_code": code,
                "period_year": None,
                "status": (
                    "ready"
                    if code == "hometax_tax_payment_certificate"
                    else "auth_pending"
                ),
            }
            for code in (
                "hometax_business_registration_certificate",
                "hometax_tax_payment_certificate",
                "hometax_closure_certificate",
            )
        ] + [
            {
                "source": "hometax",
                "document_code": document_code,
                "period_year": period_year,
                "collection_key": collection_key,
                "status": "ready",
                "facts": {
                    "collection_scope_fingerprint": scope_fingerprint,
                    **(
                        {
                            "query_strategy": "filing_year_v2",
                            "tax_year_verified": True,
                        }
                        if document_code == "hometax_income_tax_return"
                        else {}
                    ),
                },
            }
            for document_code, period_year in (
                ("hometax_income_tax_return", 2024),
                ("hometax_business_registration_certificate", None),
                ("hometax_closure_certificate", None),
            )
        ]
        repository.store_collected_document.return_value = {
            "status": "ready",
        }
        client = MagicMock()
        client.collect_hometax_income_tax_help.side_effect = (
            lambda **kwargs: _document(int(kwargs["year"]))
        )
        client.collect_hometax_income_tax_return.side_effect = (
            lambda **kwargs: _document(int(kwargs["year"]))
        )

        summary = _collect_supported_hometax_documents(
            repository,
            client,
            case_id="case-1",
            birth_date="901019",
            representative="홍길동",
            cellphone="01012345678",
            business_number="2208162517",
            session=_session(),
        )

        help_years = {
            int(call.kwargs["year"])
            for call in (
                client.collect_hometax_income_tax_help.call_args_list
            )
        }
        return_years = {
            int(call.kwargs["year"])
            for call in (
                client.collect_hometax_income_tax_return.call_args_list
            )
        }
        self.assertEqual(help_years, set(years) - {2022})
        self.assertEqual(return_years, set(years) - {2024})

        stored_annual_keys = {
            (
                call.kwargs["document_code"],
                int(call.kwargs["period_year"]),
            )
            for call in repository.store_collected_document.call_args_list
            if call.kwargs["document_code"]
            in {
                "hometax_income_tax_help",
                "hometax_income_tax_return",
            }
        }
        expected_annual_keys = {
            ("hometax_income_tax_help", year)
            for year in set(years) - {2022}
        } | {
            ("hometax_income_tax_return", year)
            for year in set(years) - {2024}
        }
        self.assertEqual(stored_annual_keys, expected_annual_keys)
        self.assertEqual(summary["failed"], 0)
        client.collect_hometax_business_registration_certificate.assert_not_called()
        client.collect_hometax_tax_payment_certificate.assert_not_called()
        client.collect_hometax_closure_certificate.assert_not_called()

    def test_taxpayer_scope_collects_one_income_return_per_year(self):
        years = list(range(2019, 2026))
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "hometax",
                "document_code": "hometax_income_tax_return",
                "period_year": year,
                "status": "auth_pending",
            }
            for year in years
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.collect_hometax_income_tax_return.side_effect = (
            lambda **kwargs: _document(int(kwargs["year"]))
        )
        identity_number = "9010191234567"

        summary = _collect_supported_hometax_documents(
            repository,
            client,
            case_id="taxpayer-scope-case",
            birth_date="901019",
            representative="홍길동",
            cellphone="01012345678",
            identity_number=identity_number,
            business_number="2208162517",
            businesses=[
                {
                    "business_number": "2208162517",
                    "business_name": "본점",
                },
                {
                    "business_number": "1208800767",
                    "business_name": "지점",
                },
            ],
            session=_session(),
        )

        self.assertEqual(summary["target"], len(years))
        self.assertEqual(
            client.collect_hometax_income_tax_return.call_count,
            len(years),
        )
        self.assertTrue(
            all(
                call.kwargs["business_number"] == identity_number
                for call in client.collect_hometax_income_tax_return.call_args_list
            )
        )
        self.assertTrue(
            all(
                "collection_key" not in call.kwargs
                for call in repository.store_collected_document.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
