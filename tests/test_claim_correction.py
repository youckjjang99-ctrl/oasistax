from __future__ import annotations

import base64
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from claim_correction_catalog import DOCUMENT_SPECS, document_plan, seven_years
from claim_correction_center import (
    _birth_date_from_identity,
    _is_valid_business_no,
    _resolve_auth_progress,
)
from claim_correction_repository import (
    ClaimRepository,
    ClaimRepositoryError,
    _masked_business_no,
    _masked_name,
    _masked_phone,
)
from tilko_claim_client import (
    ClaimProviderError,
    TilkoClaimClient,
    TilkoClaimConfig,
    provider_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


class _Response:
    ok = True
    status_code = 200

    @staticmethod
    def json():
        return {
            "ErrorCode": 0,
            "ResultData": {
                "Credential": {
                    "Token": "token",
                    "CxId": "cx",
                    "TxId": "tx",
                    "ReqTxId": "req",
                    "CookieCollection": {
                        "Value": "must-not-be-persisted"
                    },
                },
            },
        }


class _CheckResponse:
    ok = True
    status_code = 200

    @staticmethod
    def json():
        return {
            "ErrorCode": 0,
            "Result": False,
        }


class _FakeDatabase:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.inserted = []
        self.upserted = []
        self.updated = []
        self.rpc_calls = []

    def select(self, _table, filters=None, **_kwargs):
        result = self.rows
        for key, value in (filters or {}).items():
            result = [row for row in result if str(row.get(key)) == str(value)]
        return result

    def insert(self, table, rows):
        self.inserted.append((table, rows))
        return rows

    def upsert(self, table, rows, on_conflict):
        self.upserted.append((table, rows, on_conflict))
        return rows

    def update(self, table, filters, values):
        self.updated.append((table, filters, values))
        return []

    def rpc(self, function_name, parameters):
        self.rpc_calls.append((function_name, parameters))
        if function_name == "oasis_create_claim_case":
            return parameters["p_case"]["id"]
        if function_name == "oasis_claim_list_cases":
            return [
                row
                for row in self.rows
                if str(row.get("owner_user_id"))
                == str(parameters["p_owner_user_id"])
            ][: int(parameters["p_limit"])]
        if function_name == "oasis_claim_get_case":
            return [
                row
                for row in self.rows
                if str(row.get("owner_user_id"))
                == str(parameters["p_owner_user_id"])
                and str(row.get("id")) == str(parameters["p_case_id"])
            ][:1]
        if function_name == "oasis_claim_list_documents":
            return []
        if function_name == "oasis_claim_update_case_status":
            matches = [
                row
                for row in self.rows
                if str(row.get("owner_user_id"))
                == str(parameters["p_owner_user_id"])
                and str(row.get("id")) == str(parameters["p_case_id"])
            ]
            if not matches:
                return []
            updated = dict(matches[0])
            updated.update(parameters["p_updates"])
            return [updated]
        if function_name == "oasis_claim_update_document_status":
            return 0
        if function_name == "oasis_claim_append_audit":
            return 1
        raise AssertionError(function_name)


def _public_key_b64() -> str:
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    der = private_key.public_key().public_bytes(
        Encoding.DER,
        PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(der).decode("ascii")


class ClaimCorrectionTests(unittest.TestCase):
    def test_seven_year_plan_uses_previous_seven_tax_years(self):
        self.assertEqual(
            seven_years(2026),
            [2025, 2024, 2023, 2022, 2021, 2020, 2019],
        )
        plan = document_plan(2026)
        seven_year_specs = [
            spec for spec in DOCUMENT_SPECS if "7개년" in spec.period
        ]
        expected = len(DOCUMENT_SPECS) + 6 * len(seven_year_specs)
        self.assertEqual(len(plan), expected)

    def test_identity_to_hometax_birth_date(self):
        self.assertEqual(
            _birth_date_from_identity("901019", "1"),
            "19901019",
        )
        self.assertEqual(
            _birth_date_from_identity("050101", "3"),
            "20050101",
        )
        self.assertEqual(_birth_date_from_identity("901019", ""), "")
        self.assertEqual(_birth_date_from_identity("991332", "1"), "")

    def test_business_number_checksum(self):
        self.assertTrue(_is_valid_business_no("220-81-62517"))
        self.assertFalse(_is_valid_business_no("123-45-67890"))

    def test_two_source_auth_never_completes_after_partial_failure(self):
        status, all_completed, any_failed = _resolve_auth_progress(
            ["hometax", "comwel"],
            {
                "hometax": "auth_complete",
                "comwel": "failed",
            },
        )
        self.assertEqual(status, "auth_partial")
        self.assertFalse(all_completed)
        self.assertTrue(any_failed)

    def test_two_source_auth_completes_only_when_both_are_complete(self):
        status, all_completed, any_failed = _resolve_auth_progress(
            ["hometax", "comwel"],
            {
                "hometax": "auth_complete",
                "comwel": "auth_complete",
            },
        )
        self.assertEqual(status, "auth_complete_collection_pending")
        self.assertTrue(all_completed)
        self.assertFalse(any_failed)

    def test_sensitive_values_are_masked_before_storage(self):
        self.assertEqual(_masked_name("홍길동"), "홍*동")
        self.assertEqual(_masked_phone("01012345678"), "010-****-5678")
        self.assertEqual(
            _masked_business_no("123-45-67890"),
            "123-**-*****",
        )

    def test_provider_is_off_until_explicitly_enabled(self):
        config = TilkoClaimConfig(
            api_key="configured",
            rsa_public_key="configured",
            collection_enabled=False,
        )
        status = provider_readiness(config)
        self.assertFalse(status["simple_auth_ready"])
        self.assertIn("CLAIM_COLLECTION_ENABLED", status["missing"])

    @patch("tilko_claim_client.requests.post", return_value=_Response())
    def test_hometax_request_encrypts_personal_values(self, post):
        config = TilkoClaimConfig(
            api_key="api-key",
            rsa_public_key=_public_key_b64(),
            collection_enabled=True,
        )
        session = TilkoClaimClient(config).request_hometax_kakao(
            birth_date="19901019",
            user_name="홍길동",
            cellphone="01012345678",
        )
        payload = post.call_args.kwargs["json"]
        self.assertNotEqual(payload["BirthDate"], "19901019")
        self.assertNotEqual(payload["UserName"], "홍길동")
        self.assertNotEqual(payload["UserCellphoneNumber"], "01012345678")
        self.assertEqual(payload["PrivateAuthType"], "0")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(
            session,
            {
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        )
        self.assertNotIn("CookieCollection", session)

    @patch("tilko_claim_client.requests.post", return_value=_CheckResponse())
    def test_hometax_check_encrypts_only_marked_fields(self, post):
        config = TilkoClaimConfig(
            api_key="api-key",
            rsa_public_key=_public_key_b64(),
            collection_enabled=True,
        )
        completed = TilkoClaimClient(config).check_hometax_kakao(
            birth_date="19901019",
            user_name="홍길동",
            cellphone="01012345678",
            session={
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        )
        payload = post.call_args.kwargs["json"]["Auth"]
        for key, plain in {
            "BirthDate": "19901019",
            "UserName": "홍길동",
            "UserCellphoneNumber": "01012345678",
        }.items():
            self.assertNotEqual(payload[key], plain)
        for key, plain in {
            "PrivateAuthType": "0",
            "Token": "token",
            "CxId": "cx",
            "TxId": "tx",
            "ReqTxId": "req",
        }.items():
            self.assertEqual(payload[key], plain)
        self.assertFalse(completed)

    def test_provider_rejects_non_official_host(self):
        config = TilkoClaimConfig(
            api_key="api-key",
            rsa_public_key=_public_key_b64(),
            collection_enabled=True,
            hometax_host="https://example.invalid",
        )
        with self.assertRaises(ClaimProviderError):
            TilkoClaimClient(config)

    @patch("tilko_claim_client.requests.post", return_value=_CheckResponse())
    def test_comwel_check_keeps_control_fields_plain(self, post):
        config = TilkoClaimConfig(
            api_key="api-key",
            rsa_public_key=_public_key_b64(),
            collection_enabled=True,
        )
        TilkoClaimClient(config).check_comwel_kakao(
            identity_number="9010191234567",
            user_name="홍길동",
            cellphone="01012345678",
            session={
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["UserGroupFlag"], "1")
        self.assertEqual(payload["IndividualFlag"], "1")
        self.assertEqual(payload["Auth"]["PrivateAuthType"], "0")
        self.assertEqual(payload["Auth"]["Token"], "token")

    def test_case_and_document_plan_use_one_transaction_rpc(self):
        fake = _FakeDatabase()
        repository = ClaimRepository("owner-user", database=fake)
        case = repository.create_case(
            company_name="오아시스",
            business_no="1234567890",
            business_type="individual",
            representative_name="홍길동",
            cellphone="01012345678",
            requested_by="담당자",
            selected_sources=["hometax", "comwel"],
            consent_version="test-v1",
            consent_text_sha256="a" * 64,
            consent_channel="staff_attestation",
            retention_policy_version="test-retention-v1",
            collection_authority_confirmed=True,
        )
        self.assertEqual(case["business_no_masked"], "123-**-*****")
        self.assertEqual(len(fake.rpc_calls), 1)
        function_name, parameters = fake.rpc_calls[0]
        self.assertEqual(function_name, "oasis_create_claim_case")
        self.assertTrue(parameters["p_documents"])
        self.assertEqual(fake.inserted, [])

    def test_repository_refuses_cross_owner_status_update(self):
        fake = _FakeDatabase(
            [
                {
                    "id": "case-1",
                    "owner_user_id": "another-user",
                    "company_name": "테스트",
                }
            ]
        )
        repository = ClaimRepository("owner-user", database=fake)
        with self.assertRaises(ClaimRepositoryError):
            repository.update_case_status(
                "case-1",
                overall_status="ready",
            )
        self.assertEqual(fake.upserted, [])

    def test_app_has_claim_menu_and_route(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('"경정청구": "경정청구"', source)
        self.assertIn('elif active_tab == "경정청구":', source)
        self.assertIn("render_claim_correction_center(", source)

    def test_personal_flow_always_requires_both_kakao_requests(self):
        source = (ROOT / "claim_correction_center.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('sources = ["hometax", "comwel"]', source)
        self.assertIn('"카카오 인증 2건 발송"', source)


if __name__ == "__main__":
    unittest.main()
