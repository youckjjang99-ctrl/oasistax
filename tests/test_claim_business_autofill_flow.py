from __future__ import annotations

import json
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from claim_correction_center import (
    _CLAIM_JOBS,
    _CLAIM_JOB_LOCK,
    _business_candidate_token,
    _claim_job_owner_ref,
    _collect_case_documents,
    _discover_hometax_business_number,
    _masked_business_choice_no,
    _seal_claim_job_payload,
    _select_claim_business_number,
    _unseal_claim_job_payload,
)
from tilko_claim_client import (
    ClaimProviderError,
    CollectedClaimDocument,
    HometaxBusinessCandidate,
    HometaxBusinessDiscovery,
)


VALID_BUSINESS_NUMBER = "1208800767"
SECOND_VALID_BUSINESS_NUMBER = "2208162517"


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


class ClaimBusinessAutofillFlowTests(unittest.TestCase):
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

    def test_multiple_hometax_businesses_pause_before_comwel_collection(self):
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

        self.assertTrue(summary["business_selection_required"])
        self.assertFalse(summary["business_number_missing"])
        self.assertNotIn("business_number", transient)
        self.assertEqual(len(transient["business_candidates"]), 2)
        client.collect_comwel_management_numbers.assert_not_called()
        client.collect_comwel_total_remuneration.assert_not_called()
        client.collect_comwel_workplace_rate.assert_not_called()

    def test_business_selection_token_reseals_and_requeues_without_raw_summary(
        self,
    ):
        case_id = "business-selection-case"
        user_id = "business-selection-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        selection_token = _business_candidate_token(
            case_id,
            VALID_BUSINESS_NUMBER,
        )
        transient = {
            **_transient(),
            "expires_at": time.time() + 60,
            "request_started_at": time.time(),
            "business_candidates": [
                {
                    "business_number": VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스",
                    "business_status": "계속사업자",
                },
                {
                    "business_number": SECOND_VALID_BUSINESS_NUMBER,
                    "business_name": "오아시스 지점",
                    "business_status": "계속사업자",
                },
            ],
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "awaiting_business_selection",
                "progress": 80,
                "safe_message": "",
                "summary": {
                    "business_choices": [
                        {
                            "token": selection_token,
                            "label": "오아시스 · 120-**-*****",
                        }
                    ]
                },
                "wake_event": threading.Event(),
            }
        try:
            with patch(
                "claim_correction_center._activate_background_claim_job",
                return_value=True,
            ) as activate:
                selected = _select_claim_business_number(
                    user_id,
                    case_id,
                    selection_token,
                )

            self.assertTrue(selected)
            activate.assert_called_once_with(
                user_id,
                case_id,
                initial_delay=0,
            )
            with _CLAIM_JOB_LOCK:
                job = dict(_CLAIM_JOBS[case_id])
            restored = _unseal_claim_job_payload(job["sealed_payload"])
            self.assertEqual(
                restored["selected_business_number"],
                VALID_BUSINESS_NUMBER,
            )
            self.assertEqual(
                restored["business_number"],
                VALID_BUSINESS_NUMBER,
            )
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["summary"], {})

            safe_job_view = {
                key: value
                for key, value in job.items()
                if key not in {"sealed_payload", "wake_event"}
            }
            self.assertNotIn(
                VALID_BUSINESS_NUMBER,
                json.dumps(safe_job_view, ensure_ascii=False),
            )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_missing_or_invalid_hometax_business_skips_comwel_with_safe_error(
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
        self.assertEqual(summary["business_blocked_count"], 2)
        self.assertTrue(
            any(
                error.get("safe_error_code")
                == "BUSINESS_NUMBER_NOT_FOUND"
                for error in summary["errors"]
            )
        )
        client.collect_comwel_management_numbers.assert_not_called()
        client.collect_comwel_total_remuneration.assert_not_called()
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

        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertTrue(summary["errors"])
        repository.fail_document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
