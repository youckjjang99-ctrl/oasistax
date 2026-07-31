from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from claim_correction_center import (
    _business_collection_scopes,
    _claim_collection_progress,
    _claim_collection_scope_fingerprint,
    _claim_collection_variant_key,
    _collect_supported_comwel_documents,
    _collect_supported_hometax_documents,
)
from claim_correction_repository import ClaimRepositoryError
from tilko_claim_client import ClaimProviderError, CollectedClaimDocument


FIRST_BUSINESS_NUMBER = "1208800767"
SECOND_BUSINESS_NUMBER = "2208162517"
NEW_LOWER_BUSINESS_NUMBER = "1198800767"
TEST_VARIANT_SECRET = "claim-scope-test-secret-" + ("x" * 32)


def _collected() -> CollectedClaimDocument:
    return CollectedClaimDocument(
        content=b"%PDF-1.7\nscope-document",
        file_name="business-registration-certificate.pdf",
        content_type="application/pdf",
        provider_reference="scope-reference",
        facts={},
    )


def _ready_row(
    *,
    collection_key: str,
    fingerprint: str | None,
) -> dict:
    facts = (
        {"collection_scope_fingerprint": fingerprint}
        if fingerprint is not None
        else {}
    )
    return {
        "source": "hometax",
        "document_code": "hometax_business_registration_certificate",
        "period_year": None,
        "collection_key": collection_key,
        "status": "ready",
        "facts": facts,
    }


def _planned_row(
    *,
    source: str = "hometax",
    document_code: str = "hometax_business_registration_certificate",
    period_year: int | None = None,
) -> dict:
    return {
        "source": source,
        "document_code": document_code,
        "period_year": period_year,
        "collection_key": "default",
        "status": "auth_pending",
        "facts": {},
    }


def _collect(
    repository: MagicMock,
    client: MagicMock,
    *,
    case_id: str,
    businesses: list[dict],
) -> dict:
    return _collect_supported_hometax_documents(
        repository,
        client,
        case_id=case_id,
        birth_date="19901019",
        representative="테스트",
        cellphone="01012345678",
        business_number=str(businesses[0]["business_number"]),
        session={
            "Token": "token",
            "CxId": "cx",
            "TxId": "tx",
            "ReqTxId": "req",
        },
        businesses=businesses,
    )


@patch.dict(
    "os.environ",
    {"CLAIM_DOCUMENT_VARIANT_KEY": TEST_VARIANT_SECRET},
)
class ClaimScopeFingerprintTests(unittest.TestCase):
    def test_missing_durable_secret_fails_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ClaimRepositoryError):
                _business_collection_scopes(
                    "missing-secret-case",
                    [{"business_number": FIRST_BUSINESS_NUMBER}],
                )

    def test_legacy_ready_default_without_fingerprint_is_recollected(self):
        repository = MagicMock()
        repository.list_documents.return_value = [
            _ready_row(collection_key="default", fingerprint=None)
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.collect_hometax_business_registration_certificate.return_value = (
            _collected()
        )

        summary = _collect(
            repository,
            client,
            case_id="legacy-default-case",
            businesses=[
                {"business_number": FIRST_BUSINESS_NUMBER}
            ],
        )

        client.collect_hometax_business_registration_certificate.assert_called_once()
        repository.store_collected_document.assert_called_once()
        self.assertRegex(
            repository.store_collected_document.call_args.kwargs[
                "collection_key"
            ],
            r"^v_[0-9a-f]{32}$",
        )
        stored = repository.store_collected_document.call_args.kwargs[
            "document"
        ]
        self.assertRegex(
            stored.facts["collection_scope_fingerprint"],
            r"^s_[0-9a-f]{32}$",
        )
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["failed"], 0)

    def test_legacy_default_failure_preserves_old_file_and_fails_variant(self):
        case_id = "mismatched-default-case"
        old_fingerprint = _claim_collection_scope_fingerprint(
            case_id,
            "business",
            SECOND_BUSINESS_NUMBER,
        )
        repository = MagicMock()
        repository.list_documents.return_value = [
            _ready_row(
                collection_key="default",
                fingerprint=old_fingerprint,
            )
        ]
        client = MagicMock()
        client.collect_hometax_business_registration_certificate.side_effect = (
            ClaimProviderError(
                "temporary provider failure",
                error_code="TEMPORARY_PROVIDER_FAILURE",
            )
        )

        summary = _collect(
            repository,
            client,
            case_id=case_id,
            businesses=[
                {"business_number": FIRST_BUSINESS_NUMBER}
            ],
        )

        client.collect_hometax_business_registration_certificate.assert_called_once()
        repository.store_collected_document.assert_not_called()
        repository.fail_document.assert_called_once()
        failed_call = repository.fail_document.call_args
        self.assertRegex(
            failed_call.kwargs["collection_key"],
            r"^v_[0-9a-f]{32}$",
        )
        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["failed"], 1)
        self.assertTrue(summary["errors"])

    def test_mismatched_ready_variant_failure_preserves_old_ready_but_fails_run(
        self,
    ):
        case_id = "mismatched-variant-case"
        scope = _business_collection_scopes(
            case_id,
            [{"business_number": FIRST_BUSINESS_NUMBER}],
        )[0]
        old_fingerprint = _claim_collection_scope_fingerprint(
            case_id,
            "business",
            SECOND_BUSINESS_NUMBER,
        )
        repository = MagicMock()
        repository.list_documents.return_value = [
            _planned_row(),
            _ready_row(
                collection_key=scope["collection_key"],
                fingerprint=old_fingerprint,
            ),
        ]
        client = MagicMock()
        client.collect_hometax_business_registration_certificate.side_effect = (
            ClaimProviderError(
                "temporary provider failure",
                error_code="TEMPORARY_PROVIDER_FAILURE",
            )
        )

        summary = _collect(
            repository,
            client,
            case_id=case_id,
            businesses=[{"business_number": FIRST_BUSINESS_NUMBER}],
        )

        repository.store_collected_document.assert_not_called()
        repository.fail_document.assert_not_called()
        self.assertEqual(summary["ready"], 0)
        self.assertEqual(summary["failed"], 1)
        self.assertTrue(summary["errors"])

    def test_matching_scoped_ready_document_is_skipped(self):
        case_id = "matching-default-case"
        scope = _business_collection_scopes(
            case_id,
            [{"business_number": FIRST_BUSINESS_NUMBER}],
        )[0]
        repository = MagicMock()
        repository.list_documents.return_value = [
            _planned_row(),
            _ready_row(
                collection_key=scope["collection_key"],
                fingerprint=scope["collection_scope_fingerprint"],
            )
        ]
        client = MagicMock()

        summary = _collect(
            repository,
            client,
            case_id=case_id,
            businesses=[
                {"business_number": FIRST_BUSINESS_NUMBER}
            ],
        )

        client.collect_hometax_business_registration_certificate.assert_not_called()
        repository.store_collected_document.assert_not_called()
        repository.fail_document.assert_not_called()
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["failed"], 0)

    def test_candidate_set_change_refreshes_only_drifted_scopes(self):
        case_id = "candidate-set-change-case"
        original_scopes = _business_collection_scopes(
            case_id,
            [
                {"business_number": FIRST_BUSINESS_NUMBER},
                {"business_number": SECOND_BUSINESS_NUMBER},
            ],
        )
        original_by_number = {
            scope["business_number"]: scope for scope in original_scopes
        }
        repository = MagicMock()
        repository.list_documents.return_value = [
            _planned_row(),
            _ready_row(
                collection_key=original_by_number[
                    FIRST_BUSINESS_NUMBER
                ]["collection_key"],
                fingerprint=original_by_number[
                    FIRST_BUSINESS_NUMBER
                ]["collection_scope_fingerprint"],
            ),
            _ready_row(
                collection_key=original_by_number[
                    SECOND_BUSINESS_NUMBER
                ]["collection_key"],
                fingerprint=original_by_number[
                    SECOND_BUSINESS_NUMBER
                ]["collection_scope_fingerprint"],
            ),
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.collect_hometax_business_registration_certificate.return_value = (
            _collected()
        )

        summary = _collect(
            repository,
            client,
            case_id=case_id,
            businesses=[
                {"business_number": NEW_LOWER_BUSINESS_NUMBER},
                {"business_number": FIRST_BUSINESS_NUMBER},
                {"business_number": SECOND_BUSINESS_NUMBER},
            ],
        )

        collected_numbers = {
            call.kwargs["business_number"]
            for call in (
                client.collect_hometax_business_registration_certificate
                .call_args_list
            )
        }
        self.assertEqual(
            collected_numbers,
            {NEW_LOWER_BUSINESS_NUMBER},
        )
        self.assertEqual(summary["target"], 3)
        self.assertEqual(summary["ready"], 3)
        self.assertEqual(summary["failed"], 0)

    def test_matching_management_scopes_skip_all_comwel_calls(self):
        case_id = "matching-management-case"
        management_number = "1112233333"
        business_fingerprint = _claim_collection_scope_fingerprint(
            case_id,
            "business",
            FIRST_BUSINESS_NUMBER,
        )
        management_fingerprint = _claim_collection_scope_fingerprint(
            case_id,
            "management",
            FIRST_BUSINESS_NUMBER,
            management_number,
        )
        business_key = _claim_collection_variant_key(
            case_id,
            "business",
            FIRST_BUSINESS_NUMBER,
        )
        remuneration_key = _claim_collection_variant_key(
            case_id,
            "management",
            FIRST_BUSINESS_NUMBER,
            management_number,
        )
        rate_key = _claim_collection_variant_key(
            case_id,
            "workplace-rate",
            FIRST_BUSINESS_NUMBER,
            management_number,
        )
        repository = MagicMock()
        repository.list_documents.return_value = [
            _planned_row(
                source="comwel",
                document_code="comwel_management_number_list",
            ),
            _planned_row(
                source="comwel",
                document_code="comwel_total_remuneration",
                period_year=2025,
            ),
            _planned_row(
                source="comwel",
                document_code="comwel_workplace_rate",
                period_year=2025,
            ),
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "collection_key": business_key,
                "status": "ready",
                "facts": {
                    "collection_scope_fingerprint": (
                        business_fingerprint
                    )
                },
            },
            {
                "source": "comwel",
                "document_code": "comwel_total_remuneration",
                "period_year": 2025,
                "collection_key": remuneration_key,
                "status": "ready",
                "facts": {
                    "collection_scope_fingerprint": (
                        management_fingerprint
                    )
                },
            },
            {
                "source": "comwel",
                "document_code": "comwel_workplace_rate",
                "period_year": 2025,
                "collection_key": rate_key,
                "status": "ready",
                "facts": {
                    "collection_scope_fingerprint": (
                        management_fingerprint
                    )
                },
            },
        ]
        client = MagicMock()

        summary = _collect_supported_comwel_documents(
            repository,
            client,
            case_id=case_id,
            identity_number="9010191234567",
            representative="테스트",
            cellphone="01012345678",
            business_number=FIRST_BUSINESS_NUMBER,
            session={
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
            businesses=[
                {"business_number": FIRST_BUSINESS_NUMBER}
            ],
            management_cache={business_key: [management_number]},
        )

        client.collect_comwel_management_numbers.assert_not_called()
        client.collect_comwel_total_remuneration.assert_not_called()
        client.collect_comwel_workplace_rate.assert_not_called()
        repository.store_collected_document.assert_not_called()
        self.assertEqual(summary["ready"], 3)
        self.assertEqual(summary["failed"], 0)

    def test_progress_replaces_default_template_with_real_variants(self):
        variant_key = _claim_collection_variant_key(
            "progress-case",
            "business",
            FIRST_BUSINESS_NUMBER,
        )
        documents = [
            _planned_row(),
            _ready_row(
                collection_key=variant_key,
                fingerprint=_claim_collection_scope_fingerprint(
                    "progress-case",
                    "business",
                    FIRST_BUSINESS_NUMBER,
                ),
            ),
        ]

        percentage, _message, ready, target = _claim_collection_progress(
            documents
        )

        self.assertEqual((percentage, ready, target), (100, 1, 1))


if __name__ == "__main__":
    unittest.main()
