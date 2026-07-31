from __future__ import annotations

import base64
import json
import threading
import time
import unittest
from collections import defaultdict
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from claim_correction_center import (
    _CLAIM_JOBS,
    _CLAIM_JOB_LOCK,
    _claim_document_is_downloadable,
    _claim_job_owner_ref,
    _claim_result_document_status,
    _collect_case_documents,
    _collect_supported_hometax_documents,
    _run_background_claim_job,
    _seal_claim_job_payload,
)
from claim_correction_repository import ClaimRepository
from tilko_claim_client import (
    ClaimProviderError,
    CollectedClaimDocument,
    HometaxBusinessCandidate,
    HometaxBusinessDiscovery,
    TilkoClaimClient,
    TilkoClaimConfig,
)


VALID_BUSINESS_NUMBER = "2208162517"


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


def _tilko_client() -> TilkoClaimClient:
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


def _transient() -> dict[str, object]:
    return {
        "hometax": _session(),
        "comwel": _session(),
    }


def _document(
    file_name: str,
    *,
    content: bytes = b'{"result":"ok"}',
    content_type: str = "application/json",
    facts: dict | None = None,
) -> CollectedClaimDocument:
    return CollectedClaimDocument(
        content=content,
        file_name=file_name,
        content_type=content_type,
        provider_reference=f"{file_name}-reference",
        facts=dict(facts or {}),
    )


class _JsonResponse:
    ok = True
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _CapturingDatabase:
    def __init__(self):
        self.rpc_calls: list[tuple[str, dict]] = []

    def rpc(self, function_name: str, parameters: dict):
        self.rpc_calls.append((function_name, parameters))
        if function_name == "oasis_create_claim_case":
            return parameters["p_case"]["id"]
        return []


@patch.dict(
    "os.environ",
    {
        "CLAIM_DOCUMENT_VARIANT_KEY": (
            "claim-scope-test-secret-" + ("x" * 32)
        )
    },
)
class ClaimBusinessFallbackRegressionTests(unittest.TestCase):
    @patch("tilko_claim_client.requests.post")
    def test_tax_certificate_exposes_business_number_only_transiently(
        self,
        post,
    ):
        pdf = b"%PDF-1.7\nsafe-tax-certificate"
        post.return_value = _JsonResponse(
            {
                "ErrorCode": 0,
                "ApiTxKey": "tax-certificate-reference",
                "Result": {
                    "PdfData": base64.b64encode(pdf).decode("ascii"),
                    "JsonData": {
                        "txprDscmNo": "220-81-62517",
                        "txprNm": "테스트 사업자",
                    },
                },
            }
        )

        document = _tilko_client().collect_hometax_tax_payment_certificate(
            birth_date="901019",
            user_name="홍길동",
            cellphone="01012345678",
            session=_session(),
        )

        self.assertEqual(
            document.transient_facts["business_numbers"],
            [VALID_BUSINESS_NUMBER],
        )
        self.assertNotIn(
            VALID_BUSINESS_NUMBER,
            json.dumps(document.facts, ensure_ascii=False),
        )
        self.assertNotIn(VALID_BUSINESS_NUMBER.encode(), document.content)
        self.assertEqual(document.content, pdf)

    def test_missing_hometax_number_still_collects_comwel_remuneration(self):
        planned_documents = [
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_list",
                "period_year": None,
                "status": "auth_pending",
            },
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_certificate",
                "period_year": None,
                "status": "auth_pending",
            },
            {
                "source": "hometax",
                "document_code": "hometax_tax_payment_certificate",
                "period_year": None,
                "status": "auth_pending",
            },
            *[
                {
                    "source": "comwel",
                    "document_code": "comwel_total_remuneration",
                    "period_year": year,
                    "status": "auth_pending",
                }
                for year in (2025, 2024)
            ],
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "status": "auth_pending",
            },
            *[
                {
                    "source": "comwel",
                    "document_code": "comwel_workplace_rate",
                    "period_year": year,
                    "status": "auth_pending",
                }
                for year in (2025, 2024)
            ],
        ]
        repository = MagicMock()
        repository.list_documents.return_value = planned_documents
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.discover_hometax_businesses.return_value = (
            HometaxBusinessDiscovery(
                document=_document(
                    "hometax-business-registration-list.json",
                    facts={"record_count": 0},
                ),
                candidates=(),
            )
        )
        client.collect_hometax_tax_payment_certificate.return_value = (
            _document(
                "tax-payment-certificate.pdf",
                content=b"%PDF-1.7\nsafe",
                content_type="application/pdf",
            )
        )
        client.collect_comwel_total_remuneration.side_effect = (
            lambda **kwargs: _document(
                f"remuneration-{kwargs['year']}.xlsx",
                content=b"PK\x03\x04safe",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                facts={"year": kwargs["year"]},
            )
        )

        summary = _collect_case_documents(
            repository,
            client,
            case_id="missing-business-number-case",
            birth_date="19901019",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            transient=_transient(),
        )

        self.assertEqual(
            {
                call.kwargs["year"]
                for call in (
                    client.collect_comwel_total_remuneration.call_args_list
                )
            },
            {2025, 2024},
        )
        self.assertTrue(
            all(
                call.kwargs["business_number"] == ""
                for call in (
                    client.collect_comwel_total_remuneration.call_args_list
                )
            )
        )
        client.collect_comwel_management_numbers.assert_not_called()
        client.collect_comwel_workplace_rate.assert_not_called()
        self.assertEqual(summary["sources"]["comwel"]["ready"], 2)
        self.assertEqual(summary["business_blocked_count"], 3)

    def test_tax_certificate_business_number_resumes_dependent_collection(
        self,
    ):
        planned_documents = [
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_list",
                "period_year": None,
                "status": "auth_pending",
            },
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_certificate",
                "period_year": None,
                "status": "auth_pending",
            },
            {
                "source": "hometax",
                "document_code": "hometax_tax_payment_certificate",
                "period_year": None,
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_total_remuneration",
                "period_year": 2025,
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "status": "auth_pending",
            },
        ]
        repository = MagicMock()
        repository.list_documents.return_value = planned_documents
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.discover_hometax_businesses.return_value = (
            HometaxBusinessDiscovery(
                document=_document(
                    "hometax-business-registration-list.json",
                    facts={"record_count": 0},
                ),
                candidates=(),
            )
        )
        client.collect_hometax_tax_payment_certificate.return_value = (
            CollectedClaimDocument(
                content=b"%PDF-1.7\nsafe",
                file_name="tax-payment-certificate.pdf",
                content_type="application/pdf",
                provider_reference="tax-reference",
                facts={"issued": "20260731"},
                transient_facts={
                    "business_numbers": [VALID_BUSINESS_NUMBER],
                },
            )
        )
        client.collect_hometax_business_registration_certificate.return_value = (
            _document(
                "business-registration-certificate.pdf",
                content=b"%PDF-1.7\nsafe",
                content_type="application/pdf",
            )
        )
        client.collect_comwel_management_numbers.return_value = _document(
            "management-numbers.json",
            facts={"management_numbers": []},
        )
        client.collect_comwel_total_remuneration.return_value = _document(
            "remuneration-2025.xlsx",
            content=b"PK\x03\x04safe",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            facts={"year": 2025},
        )
        transient = _transient()

        summary = _collect_case_documents(
            repository,
            client,
            case_id="tax-certificate-fallback-case",
            birth_date="19901019",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            transient=transient,
        )

        self.assertEqual(
            transient["business_number"],
            VALID_BUSINESS_NUMBER,
        )
        self.assertFalse(summary["business_number_missing"])
        client.collect_hometax_tax_payment_certificate.assert_called_once()
        client.collect_hometax_business_registration_certificate.assert_called_once()
        client.collect_comwel_management_numbers.assert_called_once()
        client.collect_comwel_total_remuneration.assert_called_once()
        self.assertEqual(
            client.collect_comwel_management_numbers.call_args.kwargs[
                "business_number"
            ],
            VALID_BUSINESS_NUMBER,
        )

    def test_forced_number_refresh_preserves_existing_ready_certificate(
        self,
    ):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "hometax",
                "document_code": "hometax_tax_payment_certificate",
                "status": "ready",
            }
        ]
        client = MagicMock()
        client.collect_hometax_tax_payment_certificate.return_value = (
            CollectedClaimDocument(
                content=b"%PDF-1.7\nnew-provider-response",
                file_name="tax-payment-certificate.pdf",
                content_type="application/pdf",
                provider_reference="new-reference",
                facts={},
                transient_facts={
                    "business_numbers": [VALID_BUSINESS_NUMBER],
                },
            )
        )

        summary = _collect_supported_hometax_documents(
            repository,
            client,
            case_id="ready-tax-certificate",
            birth_date="19901019",
            representative="대표자",
            cellphone="01012345678",
            business_number="",
            session=_session(),
            force_tax_number_discovery=True,
        )

        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(summary["tax_number_discovery_attempted"])
        repository.store_collected_document.assert_not_called()
        repository.fail_document.assert_not_called()

    def test_failed_forced_refresh_does_not_downgrade_ready_certificate(
        self,
    ):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "hometax",
                "document_code": "hometax_tax_payment_certificate",
                "status": "ready",
            }
        ]
        client = MagicMock()
        client.collect_hometax_tax_payment_certificate.side_effect = (
            ClaimProviderError(
                "temporary provider failure",
                error_code="TEMPORARY_FAILURE",
            )
        )

        summary = _collect_supported_hometax_documents(
            repository,
            client,
            case_id="ready-tax-refresh-failed",
            birth_date="19901019",
            representative="대표자",
            cellphone="01012345678",
            business_number="",
            session=_session(),
            force_tax_number_discovery=True,
        )

        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["failed"], 1)
        self.assertFalse(summary["tax_number_discovery_attempted"])
        repository.store_collected_document.assert_not_called()
        repository.fail_document.assert_not_called()

    def test_empty_business_lookups_are_not_rebilled_in_same_session(self):
        planned_documents = [
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_list",
                "period_year": None,
                "status": "ready",
                "facts": {"record_count": 0},
            },
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_certificate",
                "period_year": None,
                "status": "auth_pending",
            },
            {
                "source": "hometax",
                "document_code": "hometax_tax_payment_certificate",
                "period_year": None,
                "status": "ready",
            },
        ]
        repository = MagicMock()
        repository.list_documents.return_value = planned_documents
        client = MagicMock()
        client.discover_hometax_businesses.return_value = (
            HometaxBusinessDiscovery(
                document=_document(
                    "hometax-business-registration-list.json",
                    facts={"record_count": 0},
                ),
                candidates=(),
            )
        )
        client.collect_hometax_tax_payment_certificate.return_value = (
            _document(
                "tax-payment-certificate.pdf",
                content=b"%PDF-1.7\nsafe",
                content_type="application/pdf",
            )
        )
        transient = _transient()

        for _ in range(2):
            _collect_case_documents(
                repository,
                client,
                case_id="same-auth-session",
                birth_date="19901019",
                identity_number="9010191234567",
                representative="대표자",
                cellphone="01012345678",
                transient=transient,
            )

        client.discover_hometax_businesses.assert_called_once()
        client.collect_hometax_tax_payment_certificate.assert_called_once()
        self.assertTrue(
            transient["hometax_business_discovery_attempted"]
        )
        self.assertTrue(
            transient["hometax_tax_number_discovery_attempted"]
        )

    def test_result_status_distinguishes_blocked_and_planned_documents(self):
        case = {"last_safe_error_code": "BUSINESS_NUMBER_NOT_FOUND"}
        blocked = {
            "document_code": "comwel_workplace_rate",
            "status": "failed",
            "facts": {
                "safe_error_code": "BUSINESS_NUMBER_NOT_FOUND"
            },
        }
        newly_supported = {
            "document_code": "hometax_income_tax_return",
            "status": "auth_pending",
            "facts": {},
        }

        self.assertEqual(
            _claim_result_document_status(blocked, case),
            "홈택스 사업자번호 확인 필요",
        )
        self.assertEqual(
            _claim_result_document_status(
                newly_supported,
                {},
            ),
            "고객 인증 대기",
        )

    def test_no_data_status_is_not_presented_as_downloadable_document(self):
        no_data_document = {
            "document_code": "comwel_total_remuneration",
            "status": "ready",
            "facts": {"no_data": True},
            "storage_bucket": "oasis-claim-documents",
            "storage_path": "owner/case/document.json",
            "content_type": "application/json",
            "retention_until": "2099-01-01T00:00:00+00:00",
        }

        self.assertEqual(
            _claim_result_document_status(no_data_document, {}),
            "조회된 신고내역 없음",
        )
        self.assertFalse(
            _claim_document_is_downloadable(no_data_document)
        )

    def test_empty_management_list_marks_rate_rows_as_no_workplace(self):
        workplace_rate = {
            "document_code": "comwel_workplace_rate",
            "status": "auth_pending",
            "facts": {},
        }

        self.assertEqual(
            _claim_result_document_status(
                workplace_rate,
                {},
                no_management_workplaces=True,
            ),
            "조회된 가입 사업장 없음",
        )

    def test_partial_collection_progress_uses_ready_document_ratio(self):
        user_id = "partial-progress-owner"
        case_id = "partial-progress-case"
        owner_ref = _claim_job_owner_ref(user_id)
        transient = {
            "expires_at": time.time() + 300,
            "auth_context": {
                "representative": "대표자",
                "cellphone": "01012345678",
                "birth_date": "19901019",
                "identity_number": "9010191234567",
            },
            "hometax": _session(),
            "comwel": _session(),
        }
        case = {
            "id": case_id,
            "hometax_status": "auth_complete",
            "comwel_status": "auth_complete",
            "overall_status": "collecting",
        }
        repository = MagicMock()
        repository.get_case.return_value = case
        repository.list_documents.return_value = [
            {
                "document_code": "hometax_business_registration_list",
                "status": "ready",
            },
            {
                "document_code": "hometax_tax_payment_certificate",
                "status": "ready",
            },
            {
                "document_code": "comwel_total_remuneration",
                "period_year": 2025,
                "status": "ready",
            },
            {
                "document_code": "comwel_total_remuneration",
                "period_year": 2024,
                "status": "failed",
            },
        ]
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "running",
                "progress": 75,
                "safe_message": "",
                "summary": {},
                "wake_event": threading.Event(),
            }
        try:
            with (
                patch(
                    "claim_correction_center.ClaimRepository",
                    return_value=repository,
                ),
                patch("claim_correction_center.TilkoClaimClient"),
                patch(
                    "claim_correction_center._advance_personal_case",
                    return_value={
                        "event": "collection_partial",
                        "summary": {
                            "ready": 3,
                            "failed": 1,
                            "skipped": [],
                            "business_number_missing": False,
                            "business_blocked_count": 0,
                        },
                    },
                ),
            ):
                _run_background_claim_job(
                    user_id,
                    case_id,
                    owner_ref,
                )
            with _CLAIM_JOB_LOCK:
                snapshot = dict(_CLAIM_JOBS[case_id])
            self.assertEqual(snapshot["status"], "collection_partial")
            self.assertEqual(snapshot["progress"], 75)
            self.assertEqual(snapshot["summary"]["ready"], 3)
            self.assertEqual(snapshot["summary"]["target"], 4)
            self.assertTrue(snapshot["summary"]["progress_verified"])
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_ready_discovery_is_not_overwritten_by_source_wide_status(self):
        planned_documents = [
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_list",
                "period_year": None,
                "status": "auth_pending",
            },
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_certificate",
                "period_year": None,
                "status": "auth_pending",
            },
            {
                "source": "hometax",
                "document_code": "hometax_tax_payment_certificate",
                "period_year": None,
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_total_remuneration",
                "period_year": 2025,
                "status": "auth_pending",
            },
        ]
        repository = MagicMock()
        repository.list_documents.return_value = planned_documents
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.discover_hometax_businesses.return_value = (
            HometaxBusinessDiscovery(
                document=_document(
                    "hometax-business-registration-list.json",
                    facts={"record_count": 1},
                ),
                candidates=(
                    HometaxBusinessCandidate(
                        business_number=VALID_BUSINESS_NUMBER,
                        business_name="테스트 사업자",
                    ),
                ),
            )
        )
        client.collect_hometax_business_registration_certificate.return_value = (
            _document(
                "business-registration-certificate.pdf",
                content=b"%PDF-1.7\nsafe",
                content_type="application/pdf",
            )
        )
        client.collect_hometax_tax_payment_certificate.return_value = (
            _document(
                "tax-payment-certificate.pdf",
                content=b"%PDF-1.7\nsafe",
                content_type="application/pdf",
            )
        )
        client.collect_comwel_total_remuneration.return_value = _document(
            "remuneration-2025.xlsx",
            content=b"PK\x03\x04safe",
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            facts={"year": 2025},
        )

        _collect_case_documents(
            repository,
            client,
            case_id="ready-discovery-case",
            birth_date="19901019",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            transient=_transient(),
        )

        self.assertTrue(
            any(
                call.kwargs.get("document_code")
                == "hometax_business_registration_list"
                for call in repository.store_collected_document.call_args_list
            )
        )
        repository.update_document_status.assert_not_called()

    def test_initial_plan_separates_supported_and_unsupported_documents(self):
        database = _CapturingDatabase()
        repository = ClaimRepository("owner-user", database=database)

        repository.create_case(
            company_name="",
            business_no="",
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

        _, parameters = next(
            call
            for call in database.rpc_calls
            if call[0] == "oasis_create_claim_case"
        )
        statuses_by_code: dict[str, set[str]] = defaultdict(set)
        for document in parameters["p_documents"]:
            statuses_by_code[str(document["document_code"])].add(
                str(document["status"])
            )

        supported_codes = {
            "hometax_business_registration_list",
            "hometax_business_registration_certificate",
            "hometax_tax_payment_certificate",
            "hometax_income_tax_help",
            "hometax_income_tax_return",
            "hometax_closure_certificate",
            "hometax_refund",
            "comwel_total_remuneration",
            "comwel_management_number_list",
            "comwel_workplace_rate",
            "comwel_worker_status",
        }
        unsupported_codes: set[str] = set()
        self.assertTrue(supported_codes <= statuses_by_code.keys())
        self.assertTrue(unsupported_codes <= statuses_by_code.keys())
        for document_code in supported_codes:
            self.assertEqual(
                statuses_by_code[document_code],
                {"auth_pending"},
            )
        for document_code in unsupported_codes:
            self.assertEqual(
                statuses_by_code[document_code],
                {"integration_required"},
            )


if __name__ == "__main__":
    unittest.main()
