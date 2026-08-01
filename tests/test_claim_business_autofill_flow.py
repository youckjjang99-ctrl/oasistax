from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from claim_correction_center import (
    _business_collection_scopes,
    _claim_collection_scope_fingerprint,
    _collect_case_documents,
    _collect_supported_comwel_documents,
    _collect_supported_hometax_documents,
    _discover_hometax_business_number,
    _masked_business_choice_no,
)
from tilko_claim_client import (
    ClaimProviderError,
    CollectedClaimDocument,
    HometaxBusinessCandidate,
    HometaxBusinessDiscovery,
)


VALID_BUSINESS_NUMBER = "1208800767"
SECOND_VALID_BUSINESS_NUMBER = "2208162517"
TEST_VARIANT_SECRET = "claim-scope-test-secret-" + ("x" * 32)


def _document(
    file_name: str,
    *,
    facts: dict | None = None,
) -> CollectedClaimDocument:
    return CollectedClaimDocument(
        content=b'{"result":"ok"}',
        file_name=file_name,
        content_type="application/json",
        provider_reference=f"{file_name}-reference",
        facts=dict(facts or {}),
    )


def _planned_documents() -> list[dict]:
    return [
        {
            "source": "hometax",
            "document_code": "hometax_business_registration_list",
            "period_year": None,
            "status": "integration_required",
        },
        {
            "source": "hometax",
            "document_code": "hometax_business_registration_certificate",
            "period_year": None,
            "status": "integration_required",
        },
        {
            "source": "hometax",
            "document_code": "hometax_tax_payment_certificate",
            "period_year": None,
            "status": "integration_required",
        },
        {
            "source": "comwel",
            "document_code": "comwel_total_remuneration",
            "period_year": 2025,
            "status": "integration_required",
        },
        {
            "source": "comwel",
            "document_code": "comwel_management_number_list",
            "period_year": None,
            "status": "integration_required",
        },
    ]


def _repository() -> MagicMock:
    repository = MagicMock()
    repository.list_documents.return_value = _planned_documents()
    repository.store_collected_document.return_value = {"status": "ready"}
    return repository


def _client_with_discovery(
    candidates: tuple[HometaxBusinessCandidate, ...],
) -> MagicMock:
    client = MagicMock()
    client.discover_hometax_businesses.return_value = (
        HometaxBusinessDiscovery(
            document=_document(
                "hometax-business-registration-list.json",
                facts={"business_count": len(candidates)},
            ),
            candidates=candidates,
        )
    )
    client.collect_hometax_business_registration_certificate.return_value = (
        _document("hometax-business-registration-certificate.json")
    )
    client.collect_hometax_tax_payment_certificate.return_value = _document(
        "hometax-tax-payment-certificate.json"
    )
    client.collect_comwel_management_numbers.return_value = _document(
        "comwel-management-numbers.json",
        facts={"management_numbers": ["1112233333"]},
    )
    client.collect_comwel_total_remuneration.return_value = _document(
        "comwel-total-remuneration-2025.json",
        facts={"year": 2025},
    )
    return client


def _transient() -> dict:
    return {
        "hometax": {
            "Token": "hometax-token",
            "CxId": "hometax-cx",
            "TxId": "hometax-tx",
            "ReqTxId": "hometax-request",
        },
        "comwel": {
            "Token": "comwel-token",
            "CxId": "comwel-cx",
            "TxId": "comwel-tx",
            "ReqTxId": "comwel-request",
        },
    }


@patch.dict(
    "os.environ",
    {"CLAIM_DOCUMENT_VARIANT_KEY": TEST_VARIANT_SECRET},
)
class ClaimBusinessAutofillFlowTests(unittest.TestCase):
    def test_refund_is_collected_once_per_taxpayer_across_businesses(self):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "hometax",
                "document_code": "hometax_refund",
                "period_year": None,
                "collection_key": "default",
                "status": "auth_pending",
            }
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.hometax_refund_ready = True
        client.collect_hometax_refund.return_value = _document(
            "hometax-refund.json",
            facts={"record_count": 2},
        )

        summary = _collect_supported_hometax_documents(
            repository,
            client,
            case_id="refund-taxpayer-case",
            birth_date="19901019",
            representative="홍길동",
            cellphone="01012345678",
            identity_number="9010191234567",
            business_number=VALID_BUSINESS_NUMBER,
            businesses=[
                {
                    "business_number": VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스 본점",
                },
                {
                    "business_number": SECOND_VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스 지점",
                },
            ],
            session=_transient()["hometax"],
        )

        self.assertEqual(summary["target"], 1)
        self.assertEqual(summary["ready"], 1)
        client.collect_hometax_refund.assert_called_once_with(
            taxpayer_number="9010191234567"
        )
        refund_calls = [
            call
            for call in repository.store_collected_document.call_args_list
            if call.kwargs["document_code"] == "hometax_refund"
        ]
        self.assertEqual(len(refund_calls), 1)

    def test_unconfigured_refund_is_skipped_without_inflating_progress(self):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "hometax",
                "document_code": "hometax_refund",
                "period_year": None,
                "collection_key": "default",
                "status": "integration_required",
            }
        ]
        client = MagicMock()
        client.hometax_refund_ready = False

        summary = _collect_supported_hometax_documents(
            repository,
            client,
            case_id="refund-unconfigured-case",
            birth_date="19901019",
            representative="홍길동",
            cellphone="01012345678",
            identity_number="9010191234567",
            business_number=VALID_BUSINESS_NUMBER,
            session=_transient()["hometax"],
        )

        self.assertEqual(summary["target"], 0)
        self.assertEqual(summary["ready"], 0)
        self.assertIn(
            "hometax_refund:agent_credentials_required",
            summary["skipped"],
        )
        client.collect_hometax_refund.assert_not_called()

    def test_worker_status_is_collected_for_each_management_scope(self):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "collection_key": "default",
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_worker_status",
                "period_year": None,
                "collection_key": "default",
                "status": "auth_pending",
            },
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.comwel_worker_status_ready = True
        client.collect_comwel_management_numbers.return_value = _document(
            "management-numbers.json",
            facts={
                "management_numbers": [
                    "1112233333",
                    "2223344444",
                ]
            },
        )
        client.collect_comwel_worker_status.return_value = _document(
            "comwel-worker-status.json",
            facts={"record_count": 3, "active_count": 1},
        )

        summary = _collect_supported_comwel_documents(
            repository,
            client,
            case_id="worker-status-case",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            business_number=VALID_BUSINESS_NUMBER,
            businesses=[
                {
                    "business_number": VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스",
                }
            ],
            session=_transient()["comwel"],
        )

        self.assertEqual(summary["target"], 3)
        self.assertEqual(summary["ready"], 3)
        self.assertEqual(
            client.collect_comwel_worker_status.call_count,
            2,
        )
        called_management_numbers = {
            call.kwargs["management_number"]
            for call in client.collect_comwel_worker_status.call_args_list
        }
        self.assertEqual(
            called_management_numbers,
            {"1112233333", "2223344444"},
        )
        worker_store_calls = [
            call
            for call in repository.store_collected_document.call_args_list
            if call.kwargs["document_code"] == "comwel_worker_status"
        ]
        self.assertEqual(len(worker_store_calls), 2)

    def test_single_hometax_business_is_forwarded_to_comwel_collectors(self):
        repository = _repository()
        client = _client_with_discovery(
            (
                HometaxBusinessCandidate(
                    business_number=VALID_BUSINESS_NUMBER,
                    business_name="오아시스",
                    business_status="계속사업자",
                ),
            )
        )
        transient = _transient()

        summary = _collect_case_documents(
            repository,
            client,
            case_id="single-business-case",
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
        self.assertFalse(summary["business_selection_required"])
        self.assertFalse(summary["business_number_missing"])
        client.collect_comwel_management_numbers.assert_called_once()
        client.collect_comwel_total_remuneration.assert_called_once()
        self.assertEqual(
            client.collect_comwel_management_numbers.call_args.kwargs[
                "business_number"
            ],
            VALID_BUSINESS_NUMBER,
        )
        self.assertEqual(
            client.collect_comwel_total_remuneration.call_args.kwargs[
                "business_number"
            ],
            VALID_BUSINESS_NUMBER,
        )

    def test_multiple_hometax_businesses_collect_every_scope_without_pause(
        self,
    ):
        repository = _repository()
        client = _client_with_discovery(
            (
                HometaxBusinessCandidate(
                    business_number=VALID_BUSINESS_NUMBER,
                    business_name="오아시스 본점",
                ),
                HometaxBusinessCandidate(
                    business_number=SECOND_VALID_BUSINESS_NUMBER,
                    business_name="오아시스 지점",
                ),
            )
        )
        management_by_business = {
            VALID_BUSINESS_NUMBER: "1112233333",
            SECOND_VALID_BUSINESS_NUMBER: "4445566666",
        }
        client.collect_comwel_management_numbers.side_effect = (
            lambda **kwargs: _document(
                (
                    "comwel-management-"
                    f"{management_by_business[kwargs['business_number']]}.json"
                ),
                facts={
                    "management_numbers": [
                        management_by_business[
                            kwargs["business_number"]
                        ]
                    ]
                },
            )
        )
        transient = _transient()

        summary = _collect_case_documents(
            repository,
            client,
            case_id="multiple-business-case",
            birth_date="19901019",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            transient=transient,
        )

        self.assertFalse(summary["business_selection_required"])
        self.assertFalse(summary["selection_required"])
        self.assertFalse(summary["business_number_missing"])
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(len(transient["business_candidates"]), 2)
        self.assertEqual(
            {
                call.kwargs["business_number"]
                for call in (
                    client
                    .collect_hometax_business_registration_certificate
                    .call_args_list
                )
            },
            {VALID_BUSINESS_NUMBER, SECOND_VALID_BUSINESS_NUMBER},
        )
        self.assertEqual(
            {
                call.kwargs["business_number"]
                for call in (
                    client.collect_comwel_management_numbers.call_args_list
                )
            },
            {VALID_BUSINESS_NUMBER, SECOND_VALID_BUSINESS_NUMBER},
        )
        self.assertEqual(
            {
                (
                    call.kwargs["business_number"],
                    call.kwargs["management_number"],
                )
                for call in (
                    client.collect_comwel_total_remuneration.call_args_list
                )
            },
            set(management_by_business.items()),
        )
        client.collect_comwel_workplace_rate.assert_not_called()
        scoped_store_calls = [
            call
            for call in repository.store_collected_document.call_args_list
            if call.kwargs.get("document_code")
            in {
                "hometax_business_registration_certificate",
                "comwel_management_number_list",
                "comwel_total_remuneration",
            }
            and call.kwargs.get("collection_key")
        ]
        self.assertTrue(scoped_store_calls)
        self.assertTrue(
            all(
                str(call.kwargs["collection_key"]).startswith("v_")
                for call in scoped_store_calls
            )
        )
        safe_summary = {
            "complete": summary["complete"],
            "ready": summary["ready"],
            "failed": summary["failed"],
            "target": summary["target"],
        }
        self.assertNotIn(
            VALID_BUSINESS_NUMBER,
            repr(safe_summary),
        )
        self.assertNotIn(
            SECOND_VALID_BUSINESS_NUMBER,
            repr(safe_summary),
        )

    def test_business_variant_keys_are_deterministic_opaque_and_deduplicated(
        self,
    ):
        candidates = [
            {
                "business_number": SECOND_VALID_BUSINESS_NUMBER,
                "business_name": "오아시스 지점",
            },
            {
                "business_number": VALID_BUSINESS_NUMBER,
                "business_name": "오아시스 본점",
            },
            {
                "business_number": SECOND_VALID_BUSINESS_NUMBER,
                "business_name": "",
            },
        ]

        first = _business_collection_scopes(
            "multiple-business-case",
            candidates,
        )
        reordered = _business_collection_scopes(
            "multiple-business-case",
            list(reversed(candidates)),
        )

        self.assertEqual(first, reordered)
        self.assertEqual(len(first), 2)
        self.assertNotEqual(
            first[0]["collection_key"],
            first[1]["collection_key"],
        )
        for scope in first:
            self.assertRegex(
                scope["collection_key"],
                r"^v_[0-9a-f]{32}$",
            )
            self.assertNotIn(
                scope["business_number"],
                scope["collection_key"],
            )

        with_lower_business = _business_collection_scopes(
            "multiple-business-case",
            [
                {"business_number": "1198800767"},
                *candidates,
            ],
        )
        keys_by_number = {
            scope["business_number"]: scope["collection_key"]
            for scope in with_lower_business
        }
        for scope in first:
            self.assertEqual(
                keys_by_number[scope["business_number"]],
                scope["collection_key"],
            )

    def test_one_business_failure_preserves_other_business_document(self):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "hometax",
                "document_code": (
                    "hometax_business_registration_certificate"
                ),
                "period_year": None,
                "collection_key": "default",
                "status": "auth_pending",
            }
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()

        def collect_certificate(**kwargs):
            if kwargs["business_number"] == SECOND_VALID_BUSINESS_NUMBER:
                raise ClaimProviderError(
                    "두 번째 사업자 조회 실패",
                    error_code="SECOND_BUSINESS_FAILED",
                )
            return _document("business-registration-certificate.pdf")

        client.collect_hometax_business_registration_certificate.side_effect = (
            collect_certificate
        )
        businesses = [
            {
                "business_number": VALID_BUSINESS_NUMBER,
                "business_name": "오아시스 본점",
            },
            {
                "business_number": SECOND_VALID_BUSINESS_NUMBER,
                "business_name": "오아시스 지점",
            },
        ]

        summary = _collect_supported_hometax_documents(
            repository,
            client,
            case_id="partial-business-case",
            birth_date="19901019",
            representative="홍길동",
            cellphone="01012345678",
            business_number=VALID_BUSINESS_NUMBER,
            businesses=businesses,
            session=_transient()["hometax"],
        )

        self.assertEqual(summary["target"], 2)
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(
            repository.store_collected_document.call_count,
            1,
        )
        stored_call = repository.store_collected_document.call_args
        self.assertRegex(
            stored_call.kwargs["collection_key"],
            r"^v_[0-9a-f]{32}$",
        )
        repository.fail_document.assert_called_once()
        failed_call = repository.fail_document.call_args
        self.assertRegex(
            failed_call.kwargs["collection_key"],
            r"^v_[0-9a-f]{32}$",
        )
        self.assertNotIn(
            SECOND_VALID_BUSINESS_NUMBER,
            repr(failed_call.kwargs.get("facts", {})),
        )
        self.assertIn(
            "220-**-*****",
            repr(failed_call.kwargs.get("facts", {})),
        )

    def test_empty_management_numbers_finalize_rate_as_no_data(
        self,
    ):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "collection_key": "default",
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_total_remuneration",
                "period_year": 2025,
                "collection_key": "default",
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_workplace_rate",
                "period_year": 2025,
                "collection_key": "default",
                "status": "auth_pending",
            },
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.collect_comwel_management_numbers.return_value = _document(
            "management-numbers.json",
            facts={
                "record_count": 0,
                "management_numbers": [],
                "no_data": True,
            },
        )
        client.collect_comwel_total_remuneration.return_value = _document(
            "remuneration-2025.xlsx",
            facts={"year": 2025},
        )
        client.collect_comwel_workplace_rate.return_value = _document(
            "rate-2025.json",
            facts={
                "year": 2025,
                "no_data": True,
                "no_data_reason": "no_workplace_rate",
            },
        )

        summary = _collect_supported_comwel_documents(
            repository,
            client,
            case_id="no-management-number-case",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            business_number=VALID_BUSINESS_NUMBER,
            businesses=[
                {
                    "business_number": VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스",
                }
            ],
            session=_transient()["comwel"],
        )

        self.assertEqual(summary["target"], 3)
        self.assertEqual(summary["ready"], 3)
        self.assertEqual(summary["failed"], 0)
        client.collect_comwel_management_numbers.assert_called_once()
        client.collect_comwel_total_remuneration.assert_called_once()
        client.collect_comwel_workplace_rate.assert_called_once()
        self.assertEqual(
            client.collect_comwel_workplace_rate.call_args.kwargs[
                "management_number"
            ],
            "",
        )
        rate_store_call = next(
            call
            for call in repository.store_collected_document.call_args_list
            if call.kwargs["document_code"] == "comwel_workplace_rate"
        )
        self.assertTrue(rate_store_call.kwargs["document"].facts["no_data"])
        self.assertEqual(
            rate_store_call.kwargs["document"].facts["no_data_reason"],
            "no_workplace_rate",
        )

    def test_verified_empty_management_numbers_are_not_queried_again(self):
        case_id = "cached-empty-management-case"
        scope = _business_collection_scopes(
            case_id,
            [
                {
                    "business_number": VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스",
                }
            ],
        )[0]
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "collection_key": "default",
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "collection_key": scope["collection_key"],
                "status": "ready",
                "facts": {
                    "no_data": True,
                    "no_data_reason": "no_management_number",
                    "management_numbers": [],
                    "collection_scope_fingerprint": scope[
                        "collection_scope_fingerprint"
                    ],
                },
            },
        ]
        client = MagicMock()

        summary = _collect_supported_comwel_documents(
            repository,
            client,
            case_id=case_id,
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            business_number=VALID_BUSINESS_NUMBER,
            businesses=[
                {
                    "business_number": VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스",
                }
            ],
            management_cache={scope["collection_key"]: []},
            session=_transient()["comwel"],
        )

        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["failed"], 0)
        client.collect_comwel_management_numbers.assert_not_called()
        repository.store_collected_document.assert_not_called()

    def test_provider_blocked_empty_management_numbers_are_retried(self):
        case_id = "blocked-empty-management-case"
        scope = _business_collection_scopes(
            case_id,
            [
                {
                    "business_number": VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스",
                }
            ],
        )[0]
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "collection_key": "default",
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "collection_key": scope["collection_key"],
                "status": "ready",
                "facts": {
                    "no_data": True,
                    "provider_query_attempted": False,
                    "management_numbers": [],
                    "collection_scope_fingerprint": scope[
                        "collection_scope_fingerprint"
                    ],
                },
            },
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.collect_comwel_management_numbers.return_value = _document(
            "management-numbers.json",
            facts={
                "no_data": True,
                "no_data_reason": "no_management_number",
                "management_numbers": [],
            },
        )

        _collect_supported_comwel_documents(
            repository,
            client,
            case_id=case_id,
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            business_number=VALID_BUSINESS_NUMBER,
            businesses=[
                {
                    "business_number": VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스",
                }
            ],
            management_cache={scope["collection_key"]: []},
            session=_transient()["comwel"],
        )

        client.collect_comwel_management_numbers.assert_called_once()
        repository.store_collected_document.assert_called_once()

    def test_management_lookup_failure_does_not_become_no_data(self):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "collection_key": "default",
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_total_remuneration",
                "period_year": 2025,
                "collection_key": "default",
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_workplace_rate",
                "period_year": 2025,
                "collection_key": "default",
                "status": "auth_pending",
            },
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.collect_comwel_management_numbers.side_effect = (
            ClaimProviderError(
                "관리번호 조회 실패",
                error_code="MANAGEMENT_LOOKUP_FAILED",
            )
        )
        client.collect_comwel_total_remuneration.return_value = _document(
            "remuneration-2025.xlsx",
            facts={"year": 2025},
        )

        summary = _collect_supported_comwel_documents(
            repository,
            client,
            case_id="management-lookup-failed-case",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            business_number=VALID_BUSINESS_NUMBER,
            businesses=[
                {
                    "business_number": VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스",
                }
            ],
            session=_transient()["comwel"],
        )

        self.assertEqual(summary["target"], 3)
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["failed"], 2)
        client.collect_comwel_total_remuneration.assert_called_once()
        client.collect_comwel_workplace_rate.assert_not_called()
        rate_failures = [
            call
            for call in repository.fail_document.call_args_list
            if call.kwargs["document_code"] == "comwel_workplace_rate"
        ]
        self.assertEqual(len(rate_failures), 1)
        self.assertEqual(
            rate_failures[0].kwargs["safe_error_code"],
            "COMWEL_MANAGEMENT_NUMBER_LOOKUP_FAILED",
        )
        self.assertFalse(
            any(
                call.kwargs["document_code"] == "comwel_workplace_rate"
                for call in repository.store_collected_document.call_args_list
            )
        )

    def test_ready_business_variant_is_not_recollected_or_overwritten(self):
        businesses = [
            {
                "business_number": VALID_BUSINESS_NUMBER,
                "business_name": "오아시스 본점",
            },
            {
                "business_number": SECOND_VALID_BUSINESS_NUMBER,
                "business_name": "오아시스 지점",
            },
        ]
        scopes = _business_collection_scopes(
            "ready-variant-case",
            businesses,
        )
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "hometax",
                "document_code": (
                    "hometax_business_registration_certificate"
                ),
                "period_year": None,
                "collection_key": "default",
                "status": "auth_pending",
                "facts": {},
            },
            *[
                {
                    "source": "hometax",
                    "document_code": (
                        "hometax_business_registration_certificate"
                    ),
                    "period_year": None,
                    "collection_key": scope["collection_key"],
                    "status": "ready",
                    "facts": {
                        "collection_scope_fingerprint": scope[
                            "collection_scope_fingerprint"
                        ]
                    },
                }
                for scope in scopes
            ],
        ]
        client = MagicMock()

        summary = _collect_supported_hometax_documents(
            repository,
            client,
            case_id="ready-variant-case",
            birth_date="19901019",
            representative="홍길동",
            cellphone="01012345678",
            business_number=VALID_BUSINESS_NUMBER,
            businesses=businesses,
            session=_transient()["hometax"],
        )

        self.assertEqual(summary["target"], 2)
        self.assertEqual(summary["ready"], 2)
        self.assertEqual(summary["failed"], 0)
        client.collect_hometax_business_registration_certificate.assert_not_called()
        repository.store_collected_document.assert_not_called()
        repository.fail_document.assert_not_called()

    def test_missing_business_collects_remuneration_and_blocks_dependent_docs(
        self,
    ):
        repository = _repository()
        client = _client_with_discovery(
            (
                HometaxBusinessCandidate(
                    business_number="1234567890",
                    business_name="유효하지 않은 사업자",
                ),
            )
        )
        transient = _transient()

        summary = _collect_case_documents(
            repository,
            client,
            case_id="invalid-business-case",
            birth_date="19901019",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            transient=transient,
        )

        self.assertTrue(summary["business_number_missing"])
        self.assertFalse(summary["business_selection_required"])
        self.assertEqual(summary["business_blocked_count"], 1)
        self.assertTrue(
            any(
                error.get("safe_error_code")
                == "BUSINESS_NUMBER_NOT_FOUND"
                for error in summary["errors"]
            )
        )
        client.collect_comwel_management_numbers.assert_not_called()
        client.collect_comwel_total_remuneration.assert_called_once()
        client.collect_comwel_workplace_rate.assert_not_called()

    def test_business_choice_mask_distinguishes_same_prefix_numbers(self):
        self.assertEqual(
            _masked_business_choice_no(VALID_BUSINESS_NUMBER),
            "120-**-***67",
        )
        self.assertEqual(
            _masked_business_choice_no(SECOND_VALID_BUSINESS_NUMBER),
            "220-**-***17",
        )

    def test_rediscovery_failure_preserves_existing_ready_document(self):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_list",
                "period_year": None,
                "status": "ready",
            }
        ]
        client = MagicMock()
        client.discover_hometax_businesses.side_effect = ClaimProviderError(
            "일시적인 홈택스 조회 오류",
            error_code="HOMETAX_TEMPORARY",
        )

        summary = _discover_hometax_business_number(
            repository,
            client,
            case_id="existing-ready-case",
            birth_date="19901019",
            representative="홍길동",
            cellphone="01012345678",
            session=_transient()["hometax"],
            transient={},
        )

        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["failed"], 1)
        self.assertTrue(summary["errors"])
        repository.fail_document.assert_not_called()

    def test_any_collection_error_prevents_complete_status(self):
        repository = MagicMock()
        repository.list_documents.return_value = []
        repository.update_case_status.return_value = {}
        client = MagicMock()
        discovery = {
            "target": 1,
            "ready": 1,
            "failed": 0,
            "errors": [],
            "business_number": VALID_BUSINESS_NUMBER,
            "candidates": [
                {"business_number": VALID_BUSINESS_NUMBER}
            ],
            "selection_required": False,
        }
        hometax = {
            "target": 1,
            "ready": 1,
            "failed": 0,
            "skipped": [],
            "errors": [
                {
                    "document_code": "hometax_income_tax_return",
                    "safe_error_code": "STALE_SCOPE_REFRESH_FAILED",
                }
            ],
            "business_numbers": [],
            "ready_keys": [],
        }
        comwel = {
            "target": 1,
            "ready": 1,
            "failed": 0,
            "skipped": [],
            "errors": [],
            "management_numbers": [],
            "management_number_count": 0,
            "selection_required": False,
        }

        with (
            patch(
                "claim_correction_center._discover_hometax_business_number",
                return_value=discovery,
            ),
            patch(
                "claim_correction_center._collect_supported_hometax_documents",
                return_value=hometax,
            ),
            patch(
                "claim_correction_center._collect_supported_comwel_documents",
                return_value=comwel,
            ),
        ):
            summary = _collect_case_documents(
                repository,
                client,
                case_id="summary-error-case",
                birth_date="19901019",
                identity_number="9010191234567",
                representative="홍길동",
                cellphone="01012345678",
                transient={
                    **_transient(),
                    "business_candidates": [
                        {"business_number": VALID_BUSINESS_NUMBER}
                    ],
                },
            )

        self.assertEqual(summary["ready"], summary["target"])
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(summary["errors"])
        self.assertFalse(summary["complete"])
        self.assertTrue(
            any(
                call.kwargs.get("overall_status")
                == "auth_complete_collection_pending"
                for call in repository.update_case_status.call_args_list
            )
        )


if __name__ == "__main__":
    unittest.main()
