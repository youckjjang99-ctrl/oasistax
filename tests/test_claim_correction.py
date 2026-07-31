from __future__ import annotations

import base64
import hashlib
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)

from claim_correction_catalog import DOCUMENT_SPECS, document_plan, seven_years
from claim_correction_center import (
    CLAIM_AUTH_STAGE_MESSAGES,
    _CLAIM_JOBS,
    _CLAIM_JOB_LOCK,
    _advance_personal_case,
    _birth_date_from_identity,
    _claim_auth_stage,
    _claim_collection_progress,
    _claim_collection_progress_from_repository,
    _claim_document_is_downloadable,
    _claim_job_owner_ref,
    _claim_job_can_continue,
    _claim_progress,
    _collect_supported_comwel_documents,
    _collect_supported_hometax_documents,
    _expire_claim_job,
    _is_valid_business_no,
    _next_auth_action,
    _claim_collection_retry_state,
    _resolve_auth_progress,
    _retry_authenticated_claim_collection,
    _run_background_claim_job,
    _seal_claim_job_payload,
    _select_claim_management_number,
    _set_claim_expiry,
    _sync_interrupted_claim_case,
    _unseal_claim_job_payload,
    _update_claim_job,
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
    CollectedClaimDocument,
    TilkoClaimClient,
    TilkoClaimConfig,
    is_transient_provider_error,
    provider_readiness,
)


ROOT = Path(__file__).resolve().parents[1]
TEST_VARIANT_SECRET = "claim-scope-test-secret-" + ("x" * 32)


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


class _ProviderErrorResponse:
    ok = True
    status_code = 200

    def __init__(self, *, error_code, target_code=None):
        self.error_code = error_code
        self.target_code = target_code

    def json(self):
        payload = {"ErrorCode": self.error_code}
        if self.target_code is not None:
            payload["TargetCode"] = self.target_code
        return payload


class _DocumentResponse:
    ok = True
    status_code = 200

    @staticmethod
    def json():
        return {
            "ErrorCode": 0,
            "Result": {
                "FileName": "certificate.pdf",
                "PdfData": base64.b64encode(
                    b"%PDF-1.7\nclaim-document"
                ).decode("ascii"),
                "CerCvaIsnNo": "provider-reference",
            },
        }


class _CollectionRepository:
    def __init__(self):
        self.stored_codes = []
        self.failed_codes = []

    @staticmethod
    def list_documents(_case_id):
        return [
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_certificate",
                "status": "auth_pending",
            },
            {
                "source": "hometax",
                "document_code": "hometax_tax_payment_certificate",
                "status": "auth_pending",
            },
        ]

    def store_collected_document(self, _case_id, *, document_code, document):
        self.stored_codes.append(document_code)
        return {"status": "ready", "size_bytes": len(document.content)}

    def fail_document(self, _case_id, *, document_code, safe_error_code):
        self.failed_codes.append((document_code, safe_error_code))


class _CollectionClient:
    def __init__(self):
        self.business_calls = 0
        self.tax_calls = 0

    def collect_hometax_business_registration_certificate(self, **_kwargs):
        self.business_calls += 1
        return CollectedClaimDocument(
            content=b"%PDF-business",
            file_name="business.pdf",
            content_type="application/pdf",
            provider_reference="business",
            facts={},
        )

    def collect_hometax_tax_payment_certificate(self, **_kwargs):
        self.tax_calls += 1
        return CollectedClaimDocument(
            content=b"%PDF-tax",
            file_name="tax.pdf",
            content_type="application/pdf",
            provider_reference="tax",
            facts={},
        )


class _FlowRepository:
    def __init__(self):
        self.case = {
            "id": "case-1",
            "hometax_status": "auth_requested",
            "comwel_status": "request_ready",
            "overall_status": "auth_pending",
        }
        self.audit_events = []

    def update_case_status(self, _case_id, **updates):
        self.case.update(updates)
        return dict(self.case)

    def append_audit_event(self, **event):
        self.audit_events.append(event)


class _FlowClient:
    def __init__(self, hometax_results):
        self.hometax_results = list(hometax_results)
        self.comwel_request_count = 0
        self.comwel_check_count = 0

    def check_hometax_kakao(self, **_kwargs):
        return self.hometax_results.pop(0)

    def request_comwel_kakao(self, **_kwargs):
        self.comwel_request_count += 1
        return {
            "Token": "comwel-token",
            "CxId": "comwel-cx",
            "TxId": "comwel-tx",
            "ReqTxId": "comwel-req",
        }

    def check_comwel_kakao(self, **_kwargs):
        self.comwel_check_count += 1
        return False


class _BackgroundFlowRepository(_FlowRepository):
    def __init__(self):
        super().__init__()
        self.documents = [
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_certificate",
                "status": "auth_pending",
            },
            {
                "source": "hometax",
                "document_code": "hometax_tax_payment_certificate",
                "status": "auth_pending",
            },
            {
                "source": "comwel",
                "document_code": "comwel_worker_status",
                "status": "auth_pending",
            },
        ]

    def get_case(self, _case_id):
        return dict(self.case)

    def list_documents(self, _case_id):
        return [dict(document) for document in self.documents]

    def update_document_status(self, _case_id, *, source, status):
        for document in self.documents:
            if document["source"] == source:
                document["status"] = status

    def store_collected_document(
        self,
        _case_id,
        *,
        document_code,
        document,
        period_year=None,
        collection_key="default",
    ):
        selected_year = int(period_year or 0)
        selected_key = str(collection_key or "default")
        row = next(
            (
                candidate
                for candidate in self.documents
                if candidate["document_code"] == document_code
                and int(candidate.get("period_year") or 0) == selected_year
                and str(candidate.get("collection_key") or "default")
                == selected_key
            ),
            None,
        )
        if row is None:
            row = {
                "source": (
                    "comwel"
                    if str(document_code).startswith("comwel_")
                    else "hometax"
                ),
                "document_code": document_code,
                "period_year": period_year,
                "collection_key": selected_key,
            }
            self.documents.append(row)
        row["status"] = "ready"
        row["facts"] = dict(document.facts or {})
        return {"status": "ready", "size_bytes": len(document.content)}

    def fail_document(
        self,
        _case_id,
        *,
        document_code,
        safe_error_code,
        period_year=None,
        collection_key="default",
        facts=None,
    ):
        selected_year = int(period_year or 0)
        selected_key = str(collection_key or "default")
        row = next(
            (
                candidate
                for candidate in self.documents
                if candidate["document_code"] == document_code
                and int(candidate.get("period_year") or 0) == selected_year
                and str(candidate.get("collection_key") or "default")
                == selected_key
            ),
            None,
        )
        if row is None:
            row = {
                "source": (
                    "comwel"
                    if str(document_code).startswith("comwel_")
                    else "hometax"
                ),
                "document_code": document_code,
                "period_year": period_year,
                "collection_key": selected_key,
            }
            self.documents.append(row)
        row["status"] = "failed"
        row["last_safe_error_code"] = safe_error_code
        row["facts"] = dict(facts or {})


class _ComwelStatusFailureRepository(_FlowRepository):
    def __init__(self):
        super().__init__()
        self.case.update(
            {
                "hometax_status": "auth_complete",
                "comwel_status": "request_ready",
            }
        )

    def get_case(self, _case_id):
        return dict(self.case)

    def update_case_status(self, _case_id, **updates):
        if updates.get("comwel_status") == "auth_requested":
            raise ClaimRepositoryError("temporary database failure")
        return super().update_case_status(_case_id, **updates)


class _BackgroundFlowClient(_CollectionClient):
    @staticmethod
    def check_hometax_kakao(**_kwargs):
        return True

    @staticmethod
    def request_comwel_kakao(**_kwargs):
        return {
            "Token": "comwel-token",
            "CxId": "comwel-cx",
            "TxId": "comwel-tx",
            "ReqTxId": "comwel-req",
        }

    @staticmethod
    def check_comwel_kakao(**_kwargs):
        return True


class _TransientBackgroundFlowClient(_BackgroundFlowClient):
    def __init__(self):
        super().__init__()
        self.hometax_check_count = 0
        self.comwel_request_count = 0

    def check_hometax_kakao(self, **_kwargs):
        self.hometax_check_count += 1
        if self.hometax_check_count == 1:
            raise ClaimProviderError(
                "중계 API 요청이 거절되었습니다. 오류코드: OACX_NO_USER",
                error_code="OACX_NO_USER",
            )
        return True

    def request_comwel_kakao(self, **_kwargs):
        self.comwel_request_count += 1
        return super().request_comwel_kakao(**_kwargs)


class _TransientDocumentBackgroundFlowClient(_BackgroundFlowClient):
    def __init__(self):
        super().__init__()
        self.tax_attempt_count = 0

    def collect_hometax_tax_payment_certificate(self, **kwargs):
        self.tax_attempt_count += 1
        if self.tax_attempt_count == 1:
            raise ClaimProviderError(
                "중계 API 요청이 거절되었습니다. 오류코드: OACX_NO_USER",
                error_code="OACX_NO_USER",
            )
        return super().collect_hometax_tax_payment_certificate(**kwargs)


class _FakeDatabase:
    def __init__(self, rows=None, documents=None):
        self.rows = list(rows or [])
        self.documents = list(documents or [])
        self.inserted = []
        self.upserted = []
        self.updated = []
        self.rpc_calls = []
        self.uploads = []
        self.deleted_objects = []
        self.signed_url_calls = []

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
            matches = [
                row
                for row in self.documents
                if str(row.get("owner_user_id"))
                == str(parameters["p_owner_user_id"])
                and str(row.get("case_id"))
                == str(parameters["p_case_id"])
            ]
            offset = int(parameters.get("p_offset", 0))
            limit = int(parameters.get("p_limit", len(matches) or 1))
            return matches[offset : offset + limit]
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
        if function_name == "oasis_claim_ensure_document_variant":
            selected_year = int(parameters["p_period_year"] or 0)
            selected_key = str(parameters["p_collection_key"])
            existing = next(
                (
                    row
                    for row in self.documents
                    if str(row.get("owner_user_id"))
                    == str(parameters["p_owner_user_id"])
                    and str(row.get("case_id"))
                    == str(parameters["p_case_id"])
                    and str(row.get("document_code"))
                    == str(parameters["p_document_code"])
                    and int(row.get("period_year") or 0) == selected_year
                    and str(row.get("collection_key") or "default")
                    == selected_key
                ),
                None,
            )
            if existing is not None:
                return [dict(existing)]
            template = next(
                (
                    row
                    for row in self.documents
                    if str(row.get("owner_user_id"))
                    == str(parameters["p_owner_user_id"])
                    and str(row.get("case_id"))
                    == str(parameters["p_case_id"])
                    and str(row.get("document_code"))
                    == str(parameters["p_document_code"])
                    and int(row.get("period_year") or 0) == selected_year
                    and str(row.get("collection_key") or "default")
                    == "default"
                ),
                None,
            )
            if template is None:
                return []
            variant = {
                **dict(template),
                "id": str(parameters["p_document_id"]),
                "collection_key": selected_key,
                "status": "auth_pending",
                "facts": dict(parameters["p_facts"] or {}),
                "storage_bucket": None,
                "storage_path": None,
            }
            self.documents.append(variant)
            return [dict(variant)]
        if function_name == "oasis_claim_finalize_document":
            matches = [
                row
                for row in self.documents
                if str(row.get("owner_user_id"))
                == str(parameters["p_owner_user_id"])
                and str(row.get("case_id"))
                == str(parameters["p_case_id"])
                and str(row.get("id"))
                == str(parameters["p_document_id"])
            ]
            if not matches:
                return []
            updated = dict(matches[0])
            updated.update(
                {
                    "status": parameters["p_status"],
                    "storage_bucket": parameters["p_storage_bucket"],
                    "storage_path": parameters["p_storage_path"],
                    "content_sha256": parameters["p_content_sha256"],
                    "content_type": parameters["p_content_type"],
                    "size_bytes": parameters["p_size_bytes"],
                    "retention_until": parameters["p_retention_until"],
                    "facts": parameters["p_facts"],
                }
            )
            return [updated]
        if function_name == "oasis_claim_append_audit":
            return 1
        raise AssertionError(function_name)

    def upload_private_object(
        self,
        bucket,
        path,
        content,
        content_type,
    ):
        self.uploads.append((bucket, path, content, content_type))

    def delete_private_object(self, bucket, path):
        self.deleted_objects.append((bucket, path))

    def create_private_signed_url(
        self,
        bucket,
        path,
        *,
        expires_in,
        download_name,
    ):
        self.signed_url_calls.append(
            {
                "bucket": bucket,
                "path": path,
                "expires_in": expires_in,
                "download_name": download_name,
            }
        )
        return (
            f"https://example.supabase.co/signed/{bucket}/{path}"
            f"?expires={expires_in}&download={download_name}"
        )


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


@patch.dict(
    "os.environ",
    {"CLAIM_DOCUMENT_VARIANT_KEY": TEST_VARIANT_SECRET},
)
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

    def test_downloadable_document_requires_private_ready_file_metadata(self):
        document = {
            "status": "ready",
            "storage_bucket": "oasis-claim-documents",
            "storage_path": "owner/case/document.pdf",
            "content_type": "application/pdf",
            "retention_until": "2099-01-01T00:00:00+00:00",
        }

        self.assertTrue(_claim_document_is_downloadable(document))
        self.assertFalse(
            _claim_document_is_downloadable(
                dict(document, storage_bucket="public-documents")
            )
        )
        self.assertFalse(
            _claim_document_is_downloadable(
                dict(document, content_type="text/html")
            )
        )
        self.assertFalse(
            _claim_document_is_downloadable(
                dict(document, storage_path="owner/case/document.xlsx")
            )
        )

    def test_downloadable_document_rejects_missing_expired_or_deleted_file(self):
        document = {
            "status": "ready",
            "storage_bucket": "oasis-claim-documents",
            "storage_path": "owner/case/document.xlsx",
            "content_type": (
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            "retention_until": "2099-01-01T00:00:00+00:00",
        }

        self.assertFalse(
            _claim_document_is_downloadable(
                dict(document, retention_until=None)
            )
        )
        self.assertFalse(
            _claim_document_is_downloadable(
                dict(document, retention_until="2020-01-01T00:00:00+00:00")
            )
        )
        self.assertFalse(
            _claim_document_is_downloadable(
                dict(
                    document,
                    deleted_at="2026-07-30T00:00:00+00:00",
                )
            )
        )

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

    def test_auth_stages_do_not_increase_collection_progress(self):
        cases = [
            (
                {
                    "hometax_status": "auth_requested",
                    "comwel_status": "request_ready",
                    "overall_status": "auth_pending",
                },
                1,
                CLAIM_AUTH_STAGE_MESSAGES[0],
            ),
            (
                {
                    "hometax_status": "auth_complete",
                    "comwel_status": "auth_requested",
                    "overall_status": "auth_pending",
                },
                2,
                CLAIM_AUTH_STAGE_MESSAGES[1],
            ),
            (
                {
                    "hometax_status": "auth_complete",
                    "comwel_status": "auth_complete",
                    "overall_status": "auth_pending",
                },
                3,
                CLAIM_AUTH_STAGE_MESSAGES[2],
            ),
            (
                {
                    "hometax_status": "auth_complete",
                    "comwel_status": "auth_complete",
                    "overall_status": "collecting",
                },
                4,
                CLAIM_AUTH_STAGE_MESSAGES[3],
            ),
            (
                {
                    "hometax_status": "failed",
                    "comwel_status": "request_ready",
                    "overall_status": "auth_partial",
                },
                1,
                "국세청 홈택스 인증을 완료하지 못했습니다.",
            ),
            (
                {
                    "hometax_status": "auth_complete",
                    "comwel_status": "failed",
                    "overall_status": "auth_partial",
                },
                2,
                "근로복지공단 인증을 완료하지 못했습니다.",
            ),
        ]
        for case, expected_stage, expected_message in cases:
            with self.subTest(stage=expected_stage):
                self.assertEqual(
                    _claim_auth_stage(case),
                    (expected_stage, expected_message),
                )
                self.assertEqual(_claim_progress(case)[0], 0)

        self.assertEqual(
            _claim_progress({"overall_status": "ready"})[0],
            0,
        )

    def test_collection_progress_counts_only_ready_supported_documents(self):
        documents = [
            {
                "document_code": "hometax_business_registration_list",
                "status": "ready",
                "facts": {"no_data": True, "record_count": 0},
            },
            {
                "document_code": "hometax_tax_payment_certificate",
                "status": "ready",
                "facts": {},
            },
            {
                "document_code": "hometax_business_registration_certificate",
                "status": "failed",
                "facts": {"safe_error_code": "PROVIDER_ERROR"},
            },
            {
                "document_code": "comwel_total_remuneration",
                "period_year": 2025,
                "status": "auth_pending",
                "facts": {},
            },
            {
                "document_code": "hometax_income_tax_return",
                "period_year": 2025,
                "status": "ready",
                "facts": {},
            },
        ]

        percentage, text, ready_count, target_count = (
            _claim_collection_progress(documents)
        )

        self.assertEqual(target_count, 5)
        self.assertEqual(ready_count, 3)
        self.assertEqual(percentage, 60)
        self.assertIn("5건 중 3건", text)

    def test_collection_progress_separates_no_data_from_collected(self):
        ready_documents = [
            {
                "source": "hometax",
                "document_code": "hometax_income_tax_return",
                "period_year": 1900 + index,
                "status": "ready",
                "facts": {},
            }
            for index in range(27)
        ]
        no_data_documents = [
            {
                "source": "comwel",
                "document_code": "comwel_total_remuneration",
                "period_year": 2019 + (index % 7),
                "collection_key": f"v_business_{index // 7}",
                "status": "ready",
                "facts": {"no_data": True, "record_count": 0},
            }
            for index in range(14)
        ] + [
            {
                "source": "hometax",
                "document_code": "hometax_closure_certificate",
                "collection_key": f"v_business_{index}",
                "status": "ready",
                "facts": {"no_data": True, "record_count": 0},
            }
            for index in range(2)
        ]

        percentage, text, ready_count, target_count = (
            _claim_collection_progress(
                ready_documents + no_data_documents
            )
        )

        self.assertEqual(target_count, 43)
        self.assertEqual(ready_count, 43)
        self.assertEqual(percentage, 100)
        self.assertIn("수집 완료 27건", text)
        self.assertIn("해당없음 16건", text)

    def test_collection_progress_excludes_rates_when_no_workplace_exists(self):
        documents = [
            {
                "document_code": "comwel_management_number_list",
                "status": "ready",
                "facts": {
                    "record_count": 0,
                    "management_numbers": [],
                    "no_data": True,
                },
            },
            {
                "document_code": "comwel_total_remuneration",
                "period_year": 2025,
                "status": "ready",
                "facts": {"no_data": True},
            },
            {
                "document_code": "comwel_workplace_rate",
                "period_year": 2025,
                "status": "auth_pending",
                "facts": {},
            },
            {
                "document_code": "comwel_workplace_rate",
                "period_year": 2024,
                "status": "failed",
                "facts": {"safe_error_code": "NO_WORKPLACE"},
            },
        ]

        percentage, _, ready_count, target_count = (
            _claim_collection_progress(documents)
        )

        self.assertEqual(target_count, 2)
        self.assertEqual(ready_count, 2)
        self.assertEqual(percentage, 100)

    def test_collection_progress_caps_incomplete_ratio_at_ninety_nine(self):
        documents = [
            {
                "document_code": "comwel_total_remuneration",
                "period_year": 1800 + index,
                "status": "ready" if index < 199 else "failed",
                "facts": {},
            }
            for index in range(200)
        ]

        percentage, _, ready_count, target_count = (
            _claim_collection_progress(documents)
        )

        self.assertEqual(target_count, 200)
        self.assertEqual(ready_count, 199)
        self.assertEqual(percentage, 99)

    def test_repository_progress_failure_is_unverified_and_zero(self):
        repository = MagicMock()
        repository.list_documents.side_effect = RuntimeError(
            "temporary Supabase failure"
        )

        result = _claim_collection_progress_from_repository(
            repository,
            "progress-case",
        )

        self.assertEqual(result[0], 0)
        self.assertIn("Supabase", result[1])
        self.assertEqual(result[2:4], (0, 0))
        self.assertFalse(result[4])

    def test_next_auth_action_never_skips_hometax(self):
        hometax_session = {
            "Token": "token",
            "CxId": "cx",
            "TxId": "tx",
            "ReqTxId": "req",
        }
        self.assertEqual(
            _next_auth_action(
                {
                    "hometax_status": "auth_requested",
                    "comwel_status": "request_ready",
                },
                {"hometax": hometax_session},
            )[0],
            "check_hometax",
        )
        self.assertEqual(
            _next_auth_action(
                {
                    "hometax_status": "auth_complete",
                    "comwel_status": "request_ready",
                },
                {"hometax": hometax_session},
            )[0],
            "request_comwel",
        )
        self.assertEqual(
            _next_auth_action(
                {
                    "hometax_status": "auth_complete",
                    "comwel_status": "auth_requested",
                },
                {
                    "hometax": hometax_session,
                    "comwel": hometax_session,
                },
            )[0],
            "check_comwel",
        )

    def test_sequential_advance_requests_comwel_exactly_once(self):
        repository = _FlowRepository()
        client = _FlowClient([False, True])
        transient = {
            "expires_at": 9999999999,
            "hometax": {
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        }
        common = {
            "representative": "홍길동",
            "cellphone": "01012345678",
            "birth_date": "19901019",
            "identity_number": "9010191234567",
        }

        first = _advance_personal_case(
            repository,
            client,
            case=dict(repository.case),
            transient=transient,
            **common,
        )
        self.assertEqual(first["event"], "hometax_pending")
        self.assertEqual(client.comwel_request_count, 0)

        with patch("claim_correction_center.time.sleep") as dispatch_delay:
            second = _advance_personal_case(
                repository,
                client,
                case=dict(repository.case),
                transient=transient,
                **common,
            )
        dispatch_delay.assert_called_once_with(1.0)
        self.assertEqual(second["event"], "comwel_requested")
        self.assertEqual(client.comwel_request_count, 1)
        self.assertEqual(repository.case["hometax_status"], "auth_complete")
        self.assertEqual(repository.case["comwel_status"], "auth_requested")

        third = _advance_personal_case(
            repository,
            client,
            case=dict(repository.case),
            transient=transient,
            **common,
        )
        self.assertEqual(third["event"], "comwel_pending")
        self.assertEqual(client.comwel_request_count, 1)
        self.assertEqual(client.comwel_check_count, 1)

    def test_expiry_during_hometax_check_blocks_db_update_and_next_auth(self):
        repository = _FlowRepository()
        client = _FlowClient([True])
        transient = {
            "expires_at": time.time() + 60,
            "hometax": {
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        }
        active_checks = iter((True, False))
        with self.assertRaises(ClaimProviderError) as raised:
            _advance_personal_case(
                repository,
                client,
                case=dict(repository.case),
                transient=transient,
                representative="홍길동",
                cellphone="01012345678",
                birth_date="19901019",
                identity_number="9010191234567",
                should_continue=lambda: next(active_checks),
            )
        self.assertEqual(
            raised.exception.error_code,
            "AUTH_SESSION_EXPIRED",
        )
        self.assertEqual(
            repository.case["hometax_status"],
            "auth_requested",
        )
        self.assertEqual(client.comwel_request_count, 0)

    def test_expiry_during_comwel_response_keeps_expiry_error(self):
        repository = _FlowRepository()
        client = _FlowClient([True])
        transient = {
            "expires_at": time.time() + 60,
            "hometax": {
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        }
        active_checks = iter((True, True, True, True, False))
        with patch("claim_correction_center.time.sleep"):
            with self.assertRaises(ClaimProviderError) as raised:
                _advance_personal_case(
                    repository,
                    client,
                    case=dict(repository.case),
                    transient=transient,
                    representative="홍길동",
                    cellphone="01012345678",
                    birth_date="19901019",
                    identity_number="9010191234567",
                    should_continue=lambda: next(active_checks),
                )
        self.assertEqual(
            raised.exception.error_code,
            "AUTH_SESSION_EXPIRED",
        )
        self.assertEqual(client.comwel_request_count, 1)
        self.assertNotEqual(
            repository.case.get("last_safe_error_code"),
            "COMWEL_AUTH_REQUEST_FAILED",
        )

    def test_expiry_after_document_response_blocks_supabase_storage(self):
        repository = _CollectionRepository()
        client = _CollectionClient()
        active_checks = iter((True, True, False))
        with self.assertRaises(ClaimProviderError) as raised:
            _collect_supported_hometax_documents(
                repository,
                client,
                case_id="case-1",
                birth_date="19901019",
                representative="홍길동",
                cellphone="01012345678",
                business_number="",
                session={
                    "Token": "token",
                    "CxId": "cx",
                    "TxId": "tx",
                    "ReqTxId": "req",
                },
                should_continue=lambda: next(active_checks),
            )
        self.assertEqual(
            raised.exception.error_code,
            "AUTH_SESSION_EXPIRED",
        )
        self.assertEqual(repository.stored_codes, [])

    def test_background_job_encrypts_identity_and_enforces_expiry(self):
        case_id = "encrypted-case"
        user_id = "owner-1"
        owner_ref = _claim_job_owner_ref(user_id)
        payload = {
            "expires_at": time.time() + 60,
            "auth_context": {
                "identity_number": "9010191234567",
                "representative": "홍길동",
                "cellphone": "01012345678",
                "birth_date": "19901019",
            },
            "hometax": {
                "Token": "secret-token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        }
        sealed = _seal_claim_job_payload(payload)
        self.assertNotIn(b"9010191234567", sealed)
        self.assertNotIn(b"secret-token", sealed)
        self.assertEqual(
            _unseal_claim_job_payload(sealed)["auth_context"][
                "identity_number"
            ],
            "9010191234567",
        )

        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": sealed,
                "expires_at": time.time() - 1,
                "status": "running",
                "wake_event": threading.Event(),
            }
        try:
            _expire_claim_job(case_id, owner_ref)
            with _CLAIM_JOB_LOCK:
                expired = dict(_CLAIM_JOBS[case_id])
            self.assertEqual(expired["sealed_payload"], b"")
            self.assertEqual(expired["status"], "expired")
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_claim_expiry_never_exceeds_first_request_45_minute_cap(self):
        transient = {
            "request_started_at": 100.0,
            "absolute_expires_at": 2800.0,
            "expires_at": 700.0,
        }
        with patch(
            "claim_correction_center.time.time",
            return_value=1000.0,
        ):
            expires_at = _set_claim_expiry(
                transient,
                45 * 60,
            )
        self.assertEqual(expires_at, 2800.0)
        self.assertEqual(transient["absolute_expires_at"], 2800.0)

    def test_interrupted_auth_updates_case_and_safe_error_code(self):
        repository = _BackgroundFlowRepository()
        with patch(
            "claim_correction_center.ClaimRepository",
            return_value=repository,
        ):
            _sync_interrupted_claim_case(
                "owner-1",
                "case-1",
                active_action="check_hometax",
                safe_error_code="HOMETAX_AUTH_FAILED",
                outcome="failed",
            )
        self.assertEqual(repository.case["hometax_status"], "failed")
        self.assertEqual(repository.case["overall_status"], "auth_partial")
        self.assertEqual(
            repository.case["last_safe_error_code"],
            "HOMETAX_AUTH_FAILED",
        )

    def test_interruption_does_not_reverse_completed_authentication(self):
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "hometax_status": "auth_complete",
                "comwel_status": "failed",
                "overall_status": "auth_partial",
            }
        )
        with patch(
            "claim_correction_center.ClaimRepository",
            return_value=repository,
        ):
            _sync_interrupted_claim_case(
                "owner-1",
                "case-1",
                active_action="check_hometax",
                safe_error_code="COMWEL_AUTH_REQUEST_FAILED",
                outcome="failed",
            )
        self.assertEqual(
            repository.case["hometax_status"],
            "auth_complete",
        )
        self.assertEqual(repository.case["comwel_status"], "failed")

    def test_expired_job_updates_pending_source_in_supabase_case(self):
        case_id = "expired-synced-case"
        user_id = "expired-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "id": case_id,
                "hometax_status": "auth_complete",
                "comwel_status": "auth_pending",
                "overall_status": "auth_pending",
            }
        )
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "owner_user_id": user_id,
                "sealed_payload": _seal_claim_job_payload(
                    {
                        "expires_at": time.time() - 1,
                        "auth_context": {
                            "identity_number": "9010191234567",
                        },
                    }
                ),
                "expires_at": time.time() - 1,
                "status": "running",
                "wake_event": threading.Event(),
            }
        try:
            with patch(
                "claim_correction_center.ClaimRepository",
                return_value=repository,
            ):
                _expire_claim_job(case_id, owner_ref, user_id)
            self.assertEqual(repository.case["comwel_status"], "failed")
            self.assertEqual(repository.case["overall_status"], "auth_partial")
            self.assertEqual(
                repository.case["last_safe_error_code"],
                "AUTH_SESSION_EXPIRED",
            )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_late_worker_cannot_restore_expired_sensitive_payload(self):
        case_id = "expired-race-case"
        user_id = "expired-race-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "owner_user_id": user_id,
                "sealed_payload": b"",
                "expires_at": 0,
                "status": "expired",
                "wake_event": threading.Event(),
            }
        try:
            restored = _update_claim_job(
                case_id,
                owner_ref,
                sealed_payload=b"must-not-return",
                expires_at=time.time() + 60,
                status="running",
            )
            self.assertFalse(restored)
            self.assertFalse(
                _claim_job_can_continue(case_id, owner_ref)
            )
            with _CLAIM_JOB_LOCK:
                expired = dict(_CLAIM_JOBS[case_id])
            self.assertEqual(expired["status"], "expired")
            self.assertEqual(expired["sealed_payload"], b"")
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_collection_partial_expiry_clears_encrypted_payload(self):
        case_id = "partial-expiry-case"
        user_id = "owner-partial"
        owner_ref = _claim_job_owner_ref(user_id)
        sealed = _seal_claim_job_payload(
            {
                "expires_at": time.time() - 1,
                "auth_context": {
                    "identity_number": "9010191234567",
                },
            }
        )
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": sealed,
                "expires_at": time.time() - 1,
                "status": "collection_partial",
                "safe_message": "일부 서류 저장",
                "wake_event": threading.Event(),
            }
        try:
            _expire_claim_job(case_id, owner_ref)
            with _CLAIM_JOB_LOCK:
                expired = dict(_CLAIM_JOBS[case_id])
            self.assertEqual(expired["sealed_payload"], b"")
            self.assertEqual(expired["status"], "expired")
            self.assertIn("일부 서류", expired["safe_message"])
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_authenticated_collection_retry_reuses_unexpired_encrypted_session(self):
        case_id = "retry-collection-case"
        user_id = "retry-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "id": case_id,
                "hometax_status": "auth_complete",
                "comwel_status": "auth_complete",
                "overall_status": "auth_complete_collection_pending",
                "last_safe_error_code": "HOMETAX_DOCUMENT_COLLECTION_FAILED",
            }
        )
        transient = {
            "request_started_at": time.time() - 60,
            "absolute_expires_at": time.time() + 900,
            "expires_at": time.time() + 300,
            "business_number": "1208800767",
            "auth_context": {
                "representative": "홍길동",
                "cellphone": "01012345678",
                "birth_date": "19901019",
                "identity_number": "9010191234567",
            },
            "hometax": {"Token": "hometax-token"},
            "comwel": {"Token": "comwel-token"},
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "owner_user_id": user_id,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "collection_partial",
                "progress": 84,
                "safe_message": "일부 서류 저장",
                "summary": {"ready": 1, "failed": 1},
                "updated_at": time.time(),
                "wake_event": threading.Event(),
            }
        try:
            with (
                patch(
                    "claim_correction_center.ClaimRepository",
                    return_value=repository,
                ),
                patch(
                    "claim_correction_center.provider_readiness",
                    return_value={"simple_auth_ready": True},
                ),
                patch(
                    "claim_correction_center._CLAIM_JOB_EXECUTOR.submit",
                ) as submit,
            ):
                retried, message = _retry_authenticated_claim_collection(
                    user_id,
                    case_id,
                )
            self.assertTrue(retried)
            self.assertIn("재수집", message)
            submit.assert_called_once()
            self.assertEqual(repository.case["overall_status"], "collecting")
            self.assertIsNone(repository.case["last_safe_error_code"])
            self.assertEqual(
                repository.audit_events[-1]["action"],
                "collection_retry_requested",
            )
            self.assertEqual(
                repository.audit_events[-1]["metadata"][
                    "previous_job_status"
                ],
                "collection_partial",
            )
            with _CLAIM_JOB_LOCK:
                retried_job = dict(_CLAIM_JOBS[case_id])
            restored = _unseal_claim_job_payload(
                retried_job["sealed_payload"]
            )
            self.assertEqual(retried_job["status"], "running")
            self.assertEqual(retried_job["progress"], 84)
            self.assertEqual(
                restored["hometax"]["Token"],
                "hometax-token",
            )
            self.assertEqual(
                restored["comwel"]["Token"],
                "comwel-token",
            )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_authenticated_collection_retry_rejects_other_owner(self):
        case_id = "retry-owner-scope-case"
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "id": case_id,
                "hometax_status": "auth_complete",
                "comwel_status": "auth_complete",
            }
        )
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": _claim_job_owner_ref("actual-owner"),
                "owner_user_id": "actual-owner",
                "sealed_payload": b"encrypted",
                "expires_at": time.time() + 300,
                "status": "collection_partial",
                "wake_event": threading.Event(),
            }
        try:
            with (
                patch(
                    "claim_correction_center.ClaimRepository",
                    return_value=repository,
                ),
                patch(
                    "claim_correction_center.provider_readiness",
                    return_value={"simple_auth_ready": True},
                ),
                patch(
                    "claim_correction_center._activate_background_claim_job",
                ) as activate,
            ):
                retried, message = _retry_authenticated_claim_collection(
                    "different-owner",
                    case_id,
                )
            self.assertFalse(retried)
            self.assertIn("임시 인증정보", message)
            activate.assert_not_called()
            self.assertEqual(repository.audit_events, [])
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_authenticated_collection_retry_requires_both_completed_auths(self):
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "hometax_status": "auth_complete",
                "comwel_status": "auth_pending",
            }
        )
        with (
            patch(
                "claim_correction_center.ClaimRepository",
                return_value=repository,
            ),
            patch(
                "claim_correction_center.provider_readiness",
                return_value={"simple_auth_ready": True},
            ),
        ):
            retried, message = _retry_authenticated_claim_collection(
                "owner",
                "case-1",
            )
        self.assertFalse(retried)
        self.assertIn("인증을 모두 완료", message)
        self.assertEqual(repository.audit_events, [])

    def test_collection_retry_does_not_change_state_without_provider_config(self):
        with (
            patch(
                "claim_correction_center.provider_readiness",
                return_value={"simple_auth_ready": False},
            ),
            patch(
                "claim_correction_center.ClaimRepository",
            ) as repository,
        ):
            retried, message = _retry_authenticated_claim_collection(
                "owner",
                "case-1",
            )
        self.assertFalse(retried)
        self.assertIn("API 설정", message)
        repository.assert_not_called()

    def test_collection_retry_does_not_duplicate_running_or_queued_job(self):
        user_id = "active-retry-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "hometax_status": "auth_complete",
                "comwel_status": "auth_complete",
                "overall_status": "collecting",
            }
        )
        for status in ("running", "queued"):
            case_id = f"active-{status}"
            with self.subTest(status=status):
                with _CLAIM_JOB_LOCK:
                    _CLAIM_JOBS[case_id] = {
                        "owner_ref": owner_ref,
                        "sealed_payload": b"encrypted-session",
                        "expires_at": time.time() + 300,
                        "status": status,
                        "wake_event": threading.Event(),
                    }
                try:
                    with (
                        patch(
                            "claim_correction_center.provider_readiness",
                            return_value={"simple_auth_ready": True},
                        ),
                        patch(
                            "claim_correction_center.ClaimRepository",
                            return_value=repository,
                        ),
                        patch(
                            "claim_correction_center._activate_background_claim_job",
                        ) as activate,
                    ):
                        retried, message = (
                            _retry_authenticated_claim_collection(
                                user_id,
                                case_id,
                            )
                        )
                    self.assertTrue(retried)
                    self.assertIn("이미 진행", message)
                    activate.assert_not_called()
                    self.assertEqual(repository.audit_events, [])
                    with _CLAIM_JOB_LOCK:
                        self.assertEqual(
                            _CLAIM_JOBS[case_id]["status"],
                            status,
                        )
                finally:
                    with _CLAIM_JOB_LOCK:
                        _CLAIM_JOBS.pop(case_id, None)

    def test_collection_retry_rolls_case_back_when_worker_cannot_start(self):
        case_id = "retry-start-failure"
        user_id = "retry-start-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "hometax_status": "auth_complete",
                "comwel_status": "auth_complete",
                "overall_status": "auth_complete_collection_pending",
            }
        )
        transient = {
            "expires_at": time.time() + 300,
            "auth_context": {"identity_number": "9010191234567"},
            "hometax": {"Token": "hometax-token"},
            "comwel": {"Token": "comwel-token"},
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "collection_partial",
                "wake_event": threading.Event(),
            }
        try:
            with (
                patch(
                    "claim_correction_center.provider_readiness",
                    return_value={"simple_auth_ready": True},
                ),
                patch(
                    "claim_correction_center.ClaimRepository",
                    return_value=repository,
                ),
                patch(
                    "claim_correction_center._activate_background_claim_job",
                    return_value=False,
                ),
            ):
                retried, _ = _retry_authenticated_claim_collection(
                    user_id,
                    case_id,
                )
            self.assertFalse(retried)
            self.assertEqual(
                repository.case["overall_status"],
                "auth_complete_collection_pending",
            )
            self.assertEqual(
                repository.case["last_safe_error_code"],
                "COLLECTION_RETRY_START_FAILED",
            )
            with _CLAIM_JOB_LOCK:
                self.assertEqual(
                    _CLAIM_JOBS[case_id]["status"],
                    "collection_partial",
                )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_collection_retry_rolls_back_when_audit_save_fails(self):
        case_id = "retry-audit-failure"
        user_id = "retry-audit-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "id": case_id,
                "hometax_status": "auth_complete",
                "comwel_status": "auth_complete",
                "overall_status": "auth_complete_collection_pending",
            }
        )
        repository.append_audit_event = MagicMock(
            side_effect=ClaimRepositoryError("audit unavailable")
        )
        transient = {
            "expires_at": time.time() + 300,
            "auth_context": {"identity_number": "9010191234567"},
            "hometax": {"Token": "hometax-token"},
            "comwel": {"Token": "comwel-token"},
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "collection_partial",
                "wake_event": threading.Event(),
            }
        try:
            with (
                patch(
                    "claim_correction_center.provider_readiness",
                    return_value={"simple_auth_ready": True},
                ),
                patch(
                    "claim_correction_center.ClaimRepository",
                    return_value=repository,
                ),
                patch(
                    "claim_correction_center._activate_background_claim_job",
                ) as activate,
            ):
                retried, _ = _retry_authenticated_claim_collection(
                    user_id,
                    case_id,
                )
            self.assertFalse(retried)
            activate.assert_not_called()
            self.assertEqual(
                repository.case["overall_status"],
                "auth_complete_collection_pending",
            )
            self.assertEqual(
                repository.case["last_safe_error_code"],
                "COLLECTION_RETRY_STATE_SAVE_FAILED",
            )
            with _CLAIM_JOB_LOCK:
                self.assertEqual(
                    _CLAIM_JOBS[case_id]["status"],
                    "collection_partial",
                )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_collection_retry_rolls_back_when_executor_submit_fails(self):
        case_id = "retry-executor-failure"
        user_id = "retry-executor-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "id": case_id,
                "hometax_status": "auth_complete",
                "comwel_status": "auth_complete",
                "overall_status": "auth_complete_collection_pending",
            }
        )
        transient = {
            "expires_at": time.time() + 300,
            "auth_context": {"identity_number": "9010191234567"},
            "hometax": {"Token": "hometax-token"},
            "comwel": {"Token": "comwel-token"},
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "collection_partial",
                "wake_event": threading.Event(),
            }
        try:
            with (
                patch(
                    "claim_correction_center.provider_readiness",
                    return_value={"simple_auth_ready": True},
                ),
                patch(
                    "claim_correction_center.ClaimRepository",
                    return_value=repository,
                ),
                patch(
                    "claim_correction_center._CLAIM_JOB_EXECUTOR.submit",
                    side_effect=RuntimeError("executor unavailable"),
                ),
            ):
                retried, _ = _retry_authenticated_claim_collection(
                    user_id,
                    case_id,
                )
            self.assertFalse(retried)
            self.assertEqual(
                repository.case["overall_status"],
                "auth_complete_collection_pending",
            )
            self.assertEqual(
                repository.case["last_safe_error_code"],
                "COLLECTION_RETRY_START_FAILED",
            )
            with _CLAIM_JOB_LOCK:
                self.assertEqual(
                    _CLAIM_JOBS[case_id]["status"],
                    "collection_partial",
                )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_authenticated_collection_retry_clears_expired_session(self):
        case_id = "retry-expired-case"
        user_id = "retry-expired-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "id": case_id,
                "hometax_status": "auth_complete",
                "comwel_status": "auth_complete",
                "overall_status": "auth_complete_collection_pending",
            }
        )
        transient = {
            "expires_at": time.time() - 1,
            "auth_context": {
                "identity_number": "9010191234567",
            },
            "hometax": {"Token": "hometax-token"},
            "comwel": {"Token": "comwel-token"},
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "owner_user_id": user_id,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "collection_partial",
                "wake_event": threading.Event(),
            }
        try:
            with (
                patch(
                    "claim_correction_center.ClaimRepository",
                    return_value=repository,
                ),
                patch(
                    "claim_correction_center.provider_readiness",
                    return_value={"simple_auth_ready": True},
                ),
            ):
                retried, message = _retry_authenticated_claim_collection(
                    user_id,
                    case_id,
                )
            self.assertFalse(retried)
            self.assertIn("새 인증", message)
            with _CLAIM_JOB_LOCK:
                expired_job = dict(_CLAIM_JOBS[case_id])
            self.assertEqual(expired_job["status"], "expired")
            self.assertEqual(expired_job["sealed_payload"], b"")
            self.assertEqual(
                repository.case["overall_status"],
                "auth_complete_collection_pending",
            )
            self.assertEqual(
                repository.case["last_safe_error_code"],
                "AUTH_SESSION_EXPIRED",
            )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_collection_retry_ui_state_only_exposes_safe_retry_cases(self):
        authenticated_case = {
            "hometax_status": "auth_complete",
            "comwel_status": "auth_complete",
            "overall_status": "auth_complete_collection_pending",
        }
        future = time.time() + 300
        scenarios = [
            (
                "partial",
                authenticated_case,
                {"status": "collection_partial", "expires_at": future},
                True,
                "retryable",
            ),
            (
                "paused",
                authenticated_case,
                {"status": "paused", "expires_at": future},
                True,
                "retryable",
            ),
            (
                "already-running",
                authenticated_case,
                {"status": "running", "expires_at": future},
                True,
                "running",
            ),
            (
                "missing-memory-session",
                authenticated_case,
                None,
                True,
                "reauth_required",
            ),
            (
                "missing-memory-session-while-collecting",
                {
                    **authenticated_case,
                    "overall_status": "collecting",
                },
                None,
                True,
                "reauth_required",
            ),
            (
                "missing-memory-session-while-queued",
                {
                    **authenticated_case,
                    "overall_status": "collection_queued",
                },
                None,
                True,
                "reauth_required",
            ),
            (
                "expired-session",
                authenticated_case,
                {"status": "expired", "expires_at": time.time() - 1},
                True,
                "reauth_required",
            ),
            (
                "provider-unavailable",
                authenticated_case,
                {"status": "collection_partial", "expires_at": future},
                False,
                "provider_unavailable",
            ),
            (
                "authentication-incomplete",
                {
                    **authenticated_case,
                    "comwel_status": "auth_pending",
                },
                {"status": "paused", "expires_at": future},
                True,
                "hidden",
            ),
            (
                "complete",
                {
                    **authenticated_case,
                    "overall_status": "ready",
                },
                {"status": "complete", "expires_at": future},
                True,
                "complete",
            ),
        ]
        for (
            label,
            case,
            snapshot,
            provider_ready,
            expected,
        ) in scenarios:
            with self.subTest(label=label):
                self.assertEqual(
                    _claim_collection_retry_state(
                        case,
                        snapshot,
                        provider_ready=provider_ready,
                    ),
                    expected,
                )

    def test_background_job_completes_sequential_auth_and_collection(self):
        case_id = "case-1"
        user_id = "owner-2"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        client = _BackgroundFlowClient()
        transient = {
            "expires_at": time.time() + 60,
            "business_number": "1208800767",
            "auth_context": {
                "representative": "홍길동",
                "cellphone": "01012345678",
                "birth_date": "19901019",
                "identity_number": "9010191234567",
            },
            "hometax": {
                "Token": "hometax-token",
                "CxId": "hometax-cx",
                "TxId": "hometax-tx",
                "ReqTxId": "hometax-req",
            },
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "running",
                "progress": 25,
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
                patch(
                    "claim_correction_center.TilkoClaimClient",
                    return_value=client,
                ),
                patch(
                    "claim_correction_center.threading.Event.wait",
                    return_value=False,
                ),
            ):
                _run_background_claim_job(
                    user_id,
                    case_id,
                    owner_ref,
                )
            with _CLAIM_JOB_LOCK:
                completed = dict(_CLAIM_JOBS[case_id])
            self.assertEqual(completed["status"], "complete")
            self.assertEqual(completed["progress"], 100)
            self.assertEqual(completed["sealed_payload"], b"")
            self.assertEqual(repository.case["overall_status"], "ready")
            self.assertEqual(
                {
                    row["document_code"]
                    for row in repository.documents
                    if row["status"] == "ready"
                },
                {
                    "hometax_business_registration_certificate",
                    "hometax_tax_payment_certificate",
                },
            )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_collection_complete_event_pauses_when_documents_are_incomplete(
        self,
    ):
        case_id = "strict-completion-case"
        user_id = "strict-completion-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        repository.case.update(
            {
                "id": case_id,
                "hometax_status": "auth_complete",
                "comwel_status": "auth_complete",
                "overall_status": "collecting",
            }
        )
        repository.documents = [
            {
                "source": "hometax",
                "document_code": "hometax_tax_payment_certificate",
                "status": "ready",
            },
            {
                "source": "hometax",
                "document_code": "hometax_business_registration_certificate",
                "status": "failed",
            },
            {
                "source": "comwel",
                "document_code": "comwel_total_remuneration",
                "period_year": 2025,
                "status": "auth_pending",
            },
        ]
        transient = {
            "expires_at": time.time() + 300,
            "auth_context": {
                "representative": "홍길동",
                "cellphone": "01012345678",
                "birth_date": "19901019",
                "identity_number": "9010191234567",
            },
            "hometax": {"Token": "hometax-token"},
            "comwel": {"Token": "comwel-token"},
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "owner_user_id": user_id,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "running",
                "progress": 0,
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
                        "event": "collection_complete",
                        "summary": {
                            "ready": 3,
                            "failed": 0,
                            "skipped": [],
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
                paused = dict(_CLAIM_JOBS[case_id])
            restored = _unseal_claim_job_payload(
                paused["sealed_payload"]
            )

            self.assertEqual(paused["status"], "paused")
            self.assertEqual(paused["progress"], 33)
            self.assertNotEqual(paused["sealed_payload"], b"")
            self.assertEqual(
                paused["summary"],
                {
                    "ready": 1,
                    "target": 3,
                    "progress_verified": True,
                    "failed": 0,
                },
            )
            self.assertEqual(
                restored["hometax"]["Token"],
                "hometax-token",
            )
            self.assertEqual(
                restored["comwel"]["Token"],
                "comwel-token",
            )
            self.assertEqual(
                repository.case["overall_status"],
                "auth_complete_collection_pending",
            )
            self.assertEqual(
                repository.case["last_safe_error_code"],
                "COLLECTION_PROGRESS_INCOMPLETE",
            )
            self.assertEqual(
                repository.audit_events[-1]["action"],
                "collection_progress_verification",
            )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_background_job_reseals_comwel_token_after_database_failure(self):
        case_id = "case-1"
        user_id = "owner-3"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _ComwelStatusFailureRepository()
        client = _BackgroundFlowClient()
        transient = {
            "expires_at": time.time() + 60,
            "business_number": "",
            "auth_context": {
                "representative": "홍길동",
                "cellphone": "01012345678",
                "birth_date": "19901019",
                "identity_number": "9010191234567",
            },
            "hometax": {
                "Token": "hometax-token",
                "CxId": "hometax-cx",
                "TxId": "hometax-tx",
                "ReqTxId": "hometax-req",
            },
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "running",
                "progress": 50,
                "safe_message": "",
                "summary": {},
                "wake_event": threading.Event(),
            }
        original_expires_at = transient["expires_at"]
        try:
            with (
                patch(
                    "claim_correction_center.ClaimRepository",
                    return_value=repository,
                ),
                patch(
                    "claim_correction_center.TilkoClaimClient",
                    return_value=client,
                ),
            ):
                _run_background_claim_job(
                    user_id,
                    case_id,
                    owner_ref,
                )
            with _CLAIM_JOB_LOCK:
                paused = dict(_CLAIM_JOBS[case_id])
            restored = _unseal_claim_job_payload(paused["sealed_payload"])
            self.assertEqual(paused["status"], "paused")
            self.assertEqual(restored["comwel"]["Token"], "comwel-token")
            self.assertGreater(
                paused["expires_at"],
                original_expires_at + 500,
            )
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_background_job_retries_oacx_without_manual_click(self):
        case_id = "transient-oacx-case"
        user_id = "owner-oacx"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        client = _TransientBackgroundFlowClient()
        transient = {
            "expires_at": time.time() + 60,
            "stage_started_at": time.time(),
            "business_number": "1208800767",
            "auth_context": {
                "representative": "홍길동",
                "cellphone": "01012345678",
                "birth_date": "19901019",
                "identity_number": "9010191234567",
            },
            "hometax": {
                "Token": "hometax-token",
                "CxId": "hometax-cx",
                "TxId": "hometax-tx",
                "ReqTxId": "hometax-req",
            },
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "running",
                "progress": 25,
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
                patch(
                    "claim_correction_center.TilkoClaimClient",
                    return_value=client,
                ),
                patch(
                    "claim_correction_center.threading.Event.wait",
                    return_value=False,
                ),
            ):
                _run_background_claim_job(user_id, case_id, owner_ref)
            with _CLAIM_JOB_LOCK:
                completed = dict(_CLAIM_JOBS[case_id])
            self.assertEqual(completed["status"], "complete")
            self.assertEqual(client.hometax_check_count, 2)
            self.assertEqual(client.comwel_request_count, 1)
            self.assertEqual(repository.case["overall_status"], "ready")
            self.assertNotEqual(repository.case["comwel_status"], "failed")
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_background_job_retries_document_oacx_automatically(self):
        case_id = "transient-document-case"
        user_id = "owner-document-oacx"
        owner_ref = _claim_job_owner_ref(user_id)
        repository = _BackgroundFlowRepository()
        client = _TransientDocumentBackgroundFlowClient()
        transient = {
            "expires_at": time.time() + 60,
            "stage_started_at": time.time(),
            "business_number": "1208800767",
            "auth_context": {
                "representative": "홍길동",
                "cellphone": "01012345678",
                "birth_date": "19901019",
                "identity_number": "9010191234567",
            },
            "hometax": {
                "Token": "hometax-token",
                "CxId": "hometax-cx",
                "TxId": "hometax-tx",
                "ReqTxId": "hometax-req",
            },
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "running",
                "progress": 25,
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
                patch(
                    "claim_correction_center.TilkoClaimClient",
                    return_value=client,
                ),
                patch(
                    "claim_correction_center.threading.Event.wait",
                    return_value=False,
                ),
            ):
                _run_background_claim_job(user_id, case_id, owner_ref)
            with _CLAIM_JOB_LOCK:
                completed = dict(_CLAIM_JOBS[case_id])
            self.assertEqual(completed["status"], "complete")
            self.assertEqual(client.tax_attempt_count, 2)
            self.assertEqual(repository.case["overall_status"], "ready")
            tax_document = next(
                row
                for row in repository.documents
                if row["document_code"]
                == "hometax_tax_payment_certificate"
            )
            self.assertEqual(tax_document["status"], "ready")
            self.assertNotIn("last_safe_error_code", tax_document)
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

    def test_missing_business_number_skips_only_business_certificate(self):
        repository = _CollectionRepository()
        client = _CollectionClient()
        summary = _collect_supported_hometax_documents(
            repository,
            client,
            case_id="case-1",
            birth_date="19901019",
            representative="홍길동",
            cellphone="01012345678",
            business_number="",
            session={
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        )
        self.assertEqual(summary["target"], 1)
        self.assertEqual(summary["ready"], 1)
        self.assertEqual(summary["failed"], 0)
        self.assertIn(
            "hometax_business_registration_certificate",
            summary["skipped"],
        )
        self.assertEqual(client.business_calls, 0)
        self.assertEqual(client.tax_calls, 1)
        self.assertEqual(
            repository.stored_codes,
            ["hometax_tax_payment_certificate"],
        )

    def test_comwel_without_business_number_collects_remuneration_only(self):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "comwel",
                "document_code": "comwel_total_remuneration",
                "period_year": year,
                "status": "integration_required",
            }
            for year in (2025, 2024)
        ] + [
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "status": "integration_required",
            },
            {
                "source": "comwel",
                "document_code": "comwel_workplace_rate",
                "period_year": 2025,
                "status": "integration_required",
            },
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.collect_comwel_total_remuneration.side_effect = (
            lambda **kwargs: CollectedClaimDocument(
                content=b"PK\x03\x04xlsx",
                file_name=f"remuneration-{kwargs['year']}.xlsx",
                content_type=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                provider_reference="reference",
                facts={"year": kwargs["year"]},
            )
        )

        summary = _collect_supported_comwel_documents(
            repository,
            client,
            case_id="case-1",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            business_number="",
            session={
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        )

        self.assertEqual(summary["target"], 2)
        self.assertEqual(summary["ready"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(
            {
                call.kwargs["year"]
                for call in client.collect_comwel_total_remuneration.call_args_list
            },
            {2025, 2024},
        )
        client.collect_comwel_management_numbers.assert_not_called()
        client.collect_comwel_workplace_rate.assert_not_called()

    def test_comwel_multiple_management_numbers_collect_every_rate(self):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "status": "integration_required",
            },
            {
                "source": "comwel",
                "document_code": "comwel_workplace_rate",
                "period_year": 2025,
                "status": "integration_required",
            },
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.collect_comwel_management_numbers.return_value = (
            CollectedClaimDocument(
                content=b'{"Result":[]}',
                file_name="management-numbers.json",
                content_type="application/json",
                provider_reference="reference",
                facts={
                    "management_numbers": [
                        "111-22-33333",
                        "444-555-66666",
                    ]
                },
            )
        )
        client.collect_comwel_workplace_rate.return_value = (
            CollectedClaimDocument(
                content=b"%PDF-rate",
                file_name="rate.pdf",
                content_type="application/pdf",
                provider_reference="rate-reference",
                facts={"year": "2025"},
            )
        )

        summary = _collect_supported_comwel_documents(
            repository,
            client,
            case_id="case-1",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            business_number="1208800767",
            session={
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        )

        self.assertEqual(summary["target"], 3)
        self.assertEqual(summary["ready"], 3)
        self.assertFalse(summary["selection_required"])
        self.assertEqual(
            {
                call.kwargs["management_number"]
                for call in (
                    client.collect_comwel_workplace_rate.call_args_list
                )
            },
            {"1112233333", "44455566666"},
        )

    def test_legacy_selected_management_number_does_not_narrow_collection(
        self,
    ):
        repository = MagicMock()
        repository.list_documents.return_value = [
            {
                "source": "comwel",
                "document_code": "comwel_management_number_list",
                "period_year": None,
                "status": "integration_required",
            },
            {
                "source": "comwel",
                "document_code": "comwel_workplace_rate",
                "period_year": 2025,
                "status": "integration_required",
            },
        ]
        repository.store_collected_document.return_value = {
            "status": "ready"
        }
        client = MagicMock()
        client.collect_comwel_management_numbers.return_value = (
            CollectedClaimDocument(
                content=b'{"Result":[]}',
                file_name="management-numbers.json",
                content_type="application/json",
                provider_reference="reference",
                facts={
                    "management_numbers": [
                        "111-22-33333",
                        "444-555-66666",
                    ]
                },
            )
        )
        client.collect_comwel_workplace_rate.return_value = (
            CollectedClaimDocument(
                content=b"%PDF-rate",
                file_name="rate.pdf",
                content_type="application/pdf",
                provider_reference="rate-reference",
                facts={"year": "2025"},
            )
        )

        summary = _collect_supported_comwel_documents(
            repository,
            client,
            case_id="case-1",
            identity_number="9010191234567",
            representative="홍길동",
            cellphone="01012345678",
            business_number="1208800767",
            selected_management_number="444-555-66666",
            session={
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        )

        self.assertFalse(summary["selection_required"])
        self.assertEqual(summary["target"], 3)
        self.assertEqual(summary["ready"], 3)
        self.assertEqual(
            client.collect_comwel_workplace_rate.call_count,
            2,
        )
        self.assertEqual(
            {
                call.kwargs["management_number"]
                for call in (
                    client.collect_comwel_workplace_rate.call_args_list
                )
            },
            {"1112233333", "44455566666"},
        )

    def test_management_number_selection_reseals_and_restarts_job(self):
        case_id = "management-selection-case"
        user_id = "management-owner"
        owner_ref = _claim_job_owner_ref(user_id)
        transient = {
            "expires_at": time.time() + 60,
            "auth_context": {
                "identity_number": "9010191234567",
            },
        }
        with _CLAIM_JOB_LOCK:
            _CLAIM_JOBS[case_id] = {
                "owner_ref": owner_ref,
                "sealed_payload": _seal_claim_job_payload(transient),
                "expires_at": transient["expires_at"],
                "status": "awaiting_management_selection",
                "progress": 85,
                "safe_message": "",
                "summary": {
                    "management_numbers": [
                        "111-22-33333",
                        "444-555-66666",
                    ]
                },
                "wake_event": threading.Event(),
            }
        try:
            with patch(
                "claim_correction_center._activate_background_claim_job",
                return_value=True,
            ) as activate:
                selected = _select_claim_management_number(
                    user_id,
                    case_id,
                    "444-555-66666",
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
                restored["selected_management_number"],
                "44455566666",
            )
            self.assertEqual(job["status"], "queued")
        finally:
            with _CLAIM_JOB_LOCK:
                _CLAIM_JOBS.pop(case_id, None)

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

    @patch(
        "tilko_claim_client.requests.post",
        return_value=_ProviderErrorResponse(
            error_code="E_AUTH",
            target_code="OACX_NO_USER",
        ),
    )
    def test_provider_error_exposes_transient_target_code(self, _post):
        config = TilkoClaimConfig(
            api_key="api-key",
            rsa_public_key=_public_key_b64(),
            collection_enabled=True,
        )
        client = TilkoClaimClient(config)

        with self.assertRaises(ClaimProviderError) as raised:
            client.check_hometax_kakao(
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

        error = raised.exception
        self.assertEqual(error.error_code, "OACX_NO_USER")
        self.assertTrue(error.has_error_code("oacx_no_user"))
        self.assertTrue(error.is_transient)
        self.assertTrue(is_transient_provider_error(error))
        self.assertEqual(
            str(error),
            "중계 API 요청이 거절되었습니다. 오류코드: OACX_NO_USER",
        )

    @patch(
        "tilko_claim_client.requests.post",
        return_value=_ProviderErrorResponse(error_code="AUTH_DENIED"),
    )
    def test_provider_error_exposes_non_transient_error_code(self, _post):
        config = TilkoClaimConfig(
            api_key="api-key",
            rsa_public_key=_public_key_b64(),
            collection_enabled=True,
        )
        client = TilkoClaimClient(config)

        with self.assertRaises(ClaimProviderError) as raised:
            client.check_hometax_kakao(
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

        error = raised.exception
        self.assertEqual(error.error_code, "AUTH_DENIED")
        self.assertFalse(error.is_transient)
        self.assertFalse(is_transient_provider_error(error))
        self.assertEqual(
            str(error),
            "중계 API 요청이 거절되었습니다. 오류코드: AUTH_DENIED",
        )

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

    @patch(
        "tilko_claim_client.requests.post",
        return_value=_DocumentResponse(),
    )
    def test_hometax_business_certificate_encrypts_business_number(
        self,
        post,
    ):
        config = TilkoClaimConfig(
            api_key="api-key",
            rsa_public_key=_public_key_b64(),
            collection_enabled=True,
        )
        document = TilkoClaimClient(
            config
        ).collect_hometax_business_registration_certificate(
            birth_date="19901019",
            user_name="대표자",
            cellphone="01012345678",
            business_number="2208162517",
            session={
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        )
        payload = post.call_args.kwargs["json"]
        self.assertNotEqual(payload["BusinessNumber"], "2208162517")
        self.assertEqual(payload["EnglCvaAplnYn"], "N")
        self.assertEqual(payload["ResnoOpYn"], "N")
        self.assertEqual(payload["IssueType"], "99")
        self.assertEqual(payload["Organization"], "99")
        self.assertTrue(document.content.startswith(b"%PDF-"))
        self.assertEqual(document.content_type, "application/pdf")

    @patch(
        "tilko_claim_client.requests.post",
        return_value=_DocumentResponse(),
    )
    def test_hometax_tax_certificate_uses_non_disclosing_defaults(
        self,
        post,
    ):
        config = TilkoClaimConfig(
            api_key="api-key",
            rsa_public_key=_public_key_b64(),
            collection_enabled=True,
        )
        TilkoClaimClient(config).collect_hometax_tax_payment_certificate(
            birth_date="19901019",
            user_name="대표자",
            cellphone="01012345678",
            session={
                "Token": "token",
                "CxId": "cx",
                "TxId": "tx",
                "ReqTxId": "req",
            },
        )
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["IssueType"], "B0007")
        self.assertEqual(payload["Organization"], "99")
        self.assertEqual(payload["ResnoOpYn"], "N")

    def test_repository_uploads_ready_document_to_private_storage(self):
        case = {
            "id": "00000000-0000-0000-0000-000000000001",
            "owner_user_id": "owner-user",
        }
        stored_document = {
            "id": "00000000-0000-0000-0000-000000000002",
            "case_id": case["id"],
            "owner_user_id": "owner-user",
            "source": "hometax",
            "document_code": "hometax_business_registration_certificate",
            "status": "integration_required",
        }
        fake = _FakeDatabase([case], [stored_document])
        repository = ClaimRepository("owner-user", database=fake)
        ready = repository.store_collected_document(
            case["id"],
            document_code="hometax_business_registration_certificate",
            document=CollectedClaimDocument(
                content=b"%PDF-1.7\nclaim-document",
                file_name="certificate.pdf",
                content_type="application/pdf",
                provider_reference="provider-reference",
                facts={"issued": "Y"},
            ),
        )
        self.assertEqual(ready["status"], "ready")
        self.assertEqual(len(fake.uploads), 1)
        self.assertEqual(fake.uploads[0][0], "oasis-claim-documents")
        self.assertNotIn("owner-user", fake.uploads[0][1])
        finalize_calls = [
            call
            for call in fake.rpc_calls
            if call[0] == "oasis_claim_finalize_document"
        ]
        self.assertEqual(len(finalize_calls), 1)
        self.assertEqual(
            len(finalize_calls[0][1]["p_content_sha256"]),
            64,
        )

    def test_repository_preserves_structured_document_extension(self):
        case = {
            "id": "00000000-0000-0000-0000-000000000011",
            "owner_user_id": "owner-user",
        }
        stored_document = {
            "id": "00000000-0000-0000-0000-000000000012",
            "case_id": case["id"],
            "owner_user_id": "owner-user",
            "source": "comwel",
            "document_code": "comwel_management_number_list",
            "status": "integration_required",
        }
        fake = _FakeDatabase([case], [stored_document])
        repository = ClaimRepository("owner-user", database=fake)
        repository.store_collected_document(
            case["id"],
            document_code="comwel_management_number_list",
            document=CollectedClaimDocument(
                content=b'{"result":[]}',
                file_name="management-numbers.json",
                content_type="application/json",
                provider_reference="provider-reference",
                facts={"management_number_count": 0},
            ),
        )
        self.assertTrue(fake.uploads[0][1].endswith(".json"))
        finalize = next(
            parameters
            for name, parameters in fake.rpc_calls
            if name == "oasis_claim_finalize_document"
        )
        self.assertEqual(finalize["p_content_type"], "application/json")
        self.assertEqual(
            finalize["p_facts"]["download_file_name"],
            "management-numbers.json",
        )

    def test_repository_preserves_each_opaque_document_variant(self):
        case = {
            "id": "00000000-0000-0000-0000-000000000081",
            "owner_user_id": "owner-user",
        }
        base_document = {
            "id": "00000000-0000-0000-0000-000000000082",
            "case_id": case["id"],
            "owner_user_id": "owner-user",
            "source": "hometax",
            "document_code": "hometax_income_tax_return",
            "document_name": "종합소득세 신고서",
            "period_year": 2025,
            "collection_key": "default",
            "status": "auth_pending",
            "facts": {},
        }
        fake = _FakeDatabase([case], [base_document])
        repository = ClaimRepository("owner-user", database=fake)
        first_key = f"v_{'a' * 32}"
        second_key = f"v_{'b' * 32}"

        def collected(scope_label: str, masked_number: str):
            return CollectedClaimDocument(
                content=b"%PDF-1.7\nclaim-document",
                file_name="income-tax-return-2025.pdf",
                content_type="application/pdf",
                provider_reference=f"provider-{scope_label}",
                facts={
                    "scope_label": scope_label,
                    "scope_masked": masked_number,
                    "business_number": "1208800767",
                    "business_numbers": [
                        "1208800767",
                        "2208162517",
                    ],
                    "management_number": "1112233333",
                    "management_numbers": [
                        "1112233333",
                        "4445556666",
                    ],
                },
            )

        first = repository.store_collected_document(
            case["id"],
            document_code="hometax_income_tax_return",
            period_year=2025,
            collection_key=first_key,
            document=collected("사업자 1", "120-**-***67"),
        )
        second = repository.store_collected_document(
            case["id"],
            document_code="hometax_income_tax_return",
            period_year=2025,
            collection_key=second_key,
            document=collected("사업자 2", "220-**-***17"),
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(fake.uploads), 2)
        self.assertNotEqual(fake.uploads[0][1], fake.uploads[1][1])
        variant_rows = [
            row
            for row in fake.documents
            if str(row.get("collection_key", "")).startswith("v_")
        ]
        self.assertEqual(
            {row["collection_key"] for row in variant_rows},
            {first_key, second_key},
        )
        ensure_calls = [
            parameters
            for name, parameters in fake.rpc_calls
            if name == "oasis_claim_ensure_document_variant"
        ]
        finalize_calls = [
            parameters
            for name, parameters in fake.rpc_calls
            if name == "oasis_claim_finalize_document"
        ]
        self.assertEqual(len(ensure_calls), 2)
        self.assertEqual(len(finalize_calls), 2)
        for parameters in ensure_calls:
            self.assertEqual(parameters["p_facts"], {})
        for parameters in finalize_calls:
            facts = parameters["p_facts"]
            self.assertNotIn("business_number", facts)
            self.assertNotIn("business_numbers", facts)
            self.assertNotIn("management_number", facts)
            self.assertNotIn("management_numbers", facts)
            self.assertIn("scope_label", facts)
            self.assertIn("scope_masked", facts)

        same_first = repository.ensure_document_variant(
            case["id"],
            document_code="hometax_income_tax_return",
            period_year=2025,
            collection_key=first_key,
            facts={
                "scope_label": "사업자 1",
                "scope_masked": "120-**-***67",
                "business_number": "1208800767",
            },
        )
        self.assertEqual(same_first["id"], first["id"])
        self.assertEqual(
            len(
                [
                    row
                    for row in fake.documents
                    if row.get("collection_key") == first_key
                ]
            ),
            1,
        )

    def test_failed_variant_refresh_keeps_old_ready_file_and_scope(self):
        case = {
            "id": "00000000-0000-0000-0000-000000000083",
            "owner_user_id": "owner-user",
        }
        old_storage_path = (
            "owner-folder/"
            f"{case['id']}/00000000-0000-0000-0000-000000000084.pdf"
        )
        old_facts = {
            "collection_scope_fingerprint": f"s_{'a' * 32}"
        }
        variant = {
            "id": "00000000-0000-0000-0000-000000000084",
            "case_id": case["id"],
            "owner_user_id": "owner-user",
            "source": "hometax",
            "document_code": "hometax_income_tax_return",
            "document_name": "종합소득세 신고서",
            "period_year": 2025,
            "collection_key": f"v_{'b' * 32}",
            "status": "ready",
            "facts": dict(old_facts),
            "storage_bucket": "oasis-claim-documents",
            "storage_path": old_storage_path,
            "content_sha256": "c" * 64,
            "content_type": "application/pdf",
        }
        base = {
            **variant,
            "id": "00000000-0000-0000-0000-000000000085",
            "collection_key": "default",
            "status": "auth_pending",
            "facts": {},
            "storage_bucket": None,
            "storage_path": None,
        }
        fake = _FakeDatabase([case], [base, variant])
        original_rpc = fake.rpc

        def failing_finalize(function_name, parameters):
            if function_name == "oasis_claim_finalize_document":
                fake.rpc_calls.append((function_name, parameters))
                raise RuntimeError("finalize unavailable")
            return original_rpc(function_name, parameters)

        fake.rpc = failing_finalize
        repository = ClaimRepository("owner-user", database=fake)

        with self.assertRaises(ClaimRepositoryError):
            repository.store_collected_document(
                case["id"],
                document_code="hometax_income_tax_return",
                period_year=2025,
                collection_key=variant["collection_key"],
                document=CollectedClaimDocument(
                    content=b"%PDF-1.7\nreplacement",
                    file_name="income-tax-return-2025.pdf",
                    content_type="application/pdf",
                    provider_reference="replacement-reference",
                    facts={
                        "collection_scope_fingerprint": (
                            f"s_{'d' * 32}"
                        )
                    },
                ),
            )

        ensure = next(
            parameters
            for name, parameters in fake.rpc_calls
            if name == "oasis_claim_ensure_document_variant"
        )
        self.assertEqual(ensure["p_facts"], {})
        self.assertEqual(variant["facts"], old_facts)
        self.assertEqual(len(fake.uploads), 1)
        replacement_path = fake.uploads[0][1]
        self.assertNotEqual(replacement_path, old_storage_path)
        self.assertIn(
            ("oasis-claim-documents", replacement_path),
            fake.deleted_objects,
        )
        self.assertNotIn(
            ("oasis-claim-documents", old_storage_path),
            fake.deleted_objects,
        )

    def test_repository_rejects_non_opaque_variant_key_before_writes(self):
        case_id = "00000000-0000-0000-0000-000000000091"
        fake = _FakeDatabase()
        repository = ClaimRepository("owner-user", database=fake)

        with self.assertRaises(ClaimRepositoryError):
            repository.ensure_document_variant(
                case_id,
                document_code="hometax_income_tax_return",
                period_year=2025,
                collection_key="1208800767",
                facts={"scope_label": "사업자 1"},
            )

        self.assertEqual(fake.rpc_calls, [])
        self.assertEqual(fake.uploads, [])

    def test_repository_creates_owner_scoped_short_lived_download_url(self):
        owner_user_id = "owner-user"
        case_id = "00000000-0000-0000-0000-000000000031"
        document_id = "00000000-0000-0000-0000-000000000032"
        owner_folder = hashlib.sha256(
            owner_user_id.encode("utf-8")
        ).hexdigest()[:24]
        fake = _FakeDatabase(
            rows=[
                {
                    "id": case_id,
                    "owner_user_id": owner_user_id,
                }
            ],
            documents=[
                {
                    "id": document_id,
                    "case_id": case_id,
                    "owner_user_id": owner_user_id,
                    "document_code": "hometax_tax_payment_certificate",
                    "status": "ready",
                    "storage_bucket": "oasis-claim-documents",
                    "storage_path": (
                        f"{owner_folder}/{case_id}/{document_id}.pdf"
                    ),
                    "content_type": "application/pdf",
                    "retention_until": "2099-01-01T00:00:00+00:00",
                    "facts": {
                        "download_file_name": "../납세증명서\r\n.pdf",
                    },
                }
            ],
        )
        repository = ClaimRepository(owner_user_id, database=fake)

        signed_url = repository.document_download_url(
            case_id,
            document_id,
        )

        self.assertIn("https://example.supabase.co/signed/", signed_url)
        self.assertEqual(len(fake.signed_url_calls), 1)
        call = fake.signed_url_calls[0]
        self.assertEqual(call["bucket"], "oasis-claim-documents")
        self.assertEqual(call["expires_in"], 60)
        self.assertEqual(call["download_name"], "납세증명서.pdf")
        self.assertNotIn(owner_user_id, call["path"])
        audit_calls = [
            parameters
            for function_name, parameters in fake.rpc_calls
            if function_name == "oasis_claim_append_audit"
        ]
        self.assertEqual(len(audit_calls), 1)
        self.assertEqual(
            audit_calls[0]["p_action"],
            "download_link_issued",
        )
        self.assertEqual(
            audit_calls[0]["p_metadata"]["document_id"],
            document_id,
        )

    def test_repository_batches_download_urls_with_one_document_read(self):
        owner_user_id = "owner-user"
        case_id = "00000000-0000-0000-0000-000000000131"
        owner_folder = hashlib.sha256(
            owner_user_id.encode("utf-8")
        ).hexdigest()[:24]
        document_ids = [
            "00000000-0000-0000-0000-000000000132",
            "00000000-0000-0000-0000-000000000133",
            "00000000-0000-0000-0000-000000000134",
        ]
        fake = _FakeDatabase(
            documents=[
                {
                    "id": document_id,
                    "case_id": case_id,
                    "owner_user_id": owner_user_id,
                    "document_code": "hometax_tax_payment_certificate",
                    "status": "ready",
                    "storage_bucket": "oasis-claim-documents",
                    "storage_path": (
                        f"{owner_folder}/{case_id}/{document_id}.pdf"
                    ),
                    "content_type": "application/pdf",
                    "retention_until": "2099-01-01T00:00:00+00:00",
                }
                for document_id in document_ids
            ]
        )
        repository = ClaimRepository(owner_user_id, database=fake)

        urls = repository.document_download_urls(case_id, document_ids)

        self.assertEqual(len(urls), len(document_ids))
        document_list_calls = [
            parameters
            for function_name, parameters in fake.rpc_calls
            if function_name == "oasis_claim_list_documents"
        ]
        self.assertEqual(len(document_list_calls), 1)
        self.assertEqual(len(fake.signed_url_calls), len(document_ids))
        audit_calls = [
            parameters
            for function_name, parameters in fake.rpc_calls
            if function_name == "oasis_claim_append_audit"
        ]
        self.assertEqual(len(audit_calls), len(document_ids))
        self.assertEqual(
            [call["p_metadata"]["document_id"] for call in audit_calls],
            document_ids,
        )

    def test_repository_batch_download_rejects_cross_owner_document(self):
        owner_user_id = "owner-user"
        case_id = "00000000-0000-0000-0000-000000000141"
        document_id = "00000000-0000-0000-0000-000000000142"
        fake = _FakeDatabase(
            documents=[
                {
                    "id": document_id,
                    "case_id": case_id,
                    "owner_user_id": "another-owner",
                    "document_code": "hometax_tax_payment_certificate",
                    "status": "ready",
                    "storage_bucket": "oasis-claim-documents",
                    "storage_path": "untrusted/path/document.pdf",
                    "content_type": "application/pdf",
                    "retention_until": "2099-01-01T00:00:00+00:00",
                }
            ]
        )
        repository = ClaimRepository(owner_user_id, database=fake)

        with self.assertRaises(ClaimRepositoryError):
            repository.document_download_urls(case_id, [document_id])

        self.assertEqual(fake.signed_url_calls, [])

    def test_repository_allows_versioned_replacement_document_download(self):
        owner_user_id = "owner-user"
        case_id = "00000000-0000-0000-0000-000000000033"
        document_id = "00000000-0000-0000-0000-000000000034"
        owner_folder = hashlib.sha256(
            owner_user_id.encode("utf-8")
        ).hexdigest()[:24]
        versioned_path = (
            f"{owner_folder}/{case_id}/{document_id}-"
            "0123456789abcdef.pdf"
        )
        fake = _FakeDatabase(
            documents=[
                {
                    "id": document_id,
                    "case_id": case_id,
                    "owner_user_id": owner_user_id,
                    "document_code": "hometax_income_tax_return",
                    "status": "ready",
                    "storage_bucket": "oasis-claim-documents",
                    "storage_path": versioned_path,
                    "content_type": "application/pdf",
                    "retention_until": "2099-01-01T00:00:00+00:00",
                    "facts": {
                        "download_file_name": "종합소득세신고서.pdf",
                    },
                }
            ],
        )
        repository = ClaimRepository(owner_user_id, database=fake)

        signed_url = repository.document_download_url(
            case_id,
            document_id,
        )

        self.assertIn("https://example.supabase.co/signed/", signed_url)
        self.assertEqual(fake.signed_url_calls[0]["path"], versioned_path)

    def test_repository_masks_business_number_in_download_file_name(self):
        owner_user_id = "owner-user"
        case_id = "00000000-0000-0000-0000-000000000035"
        document_id = "00000000-0000-0000-0000-000000000036"
        owner_folder = hashlib.sha256(
            owner_user_id.encode("utf-8")
        ).hexdigest()[:24]
        fake = _FakeDatabase(
            documents=[
                {
                    "id": document_id,
                    "case_id": case_id,
                    "owner_user_id": owner_user_id,
                    "document_code": "hometax_income_tax_return",
                    "status": "ready",
                    "storage_bucket": "oasis-claim-documents",
                    "storage_path": (
                        f"{owner_folder}/{case_id}/{document_id}.pdf"
                    ),
                    "content_type": "application/pdf",
                    "retention_until": "2099-01-01T00:00:00+00:00",
                    "facts": {
                        "download_file_name": (
                            "2025_120-88-00767_종합소득세신고서.pdf"
                        ),
                    },
                }
            ],
        )
        repository = ClaimRepository(owner_user_id, database=fake)

        repository.document_download_url(case_id, document_id)

        download_name = fake.signed_url_calls[0]["download_name"]
        self.assertEqual(
            download_name,
            "2025_120-XX-XXX67_종합소득세신고서.pdf",
        )
        self.assertNotIn("1208800767", download_name)
        self.assertNotIn("120-88-00767", download_name)

    def test_repository_refuses_cross_owner_document_download(self):
        owner_user_id = "owner-user"
        case_id = "00000000-0000-0000-0000-000000000041"
        document_id = "00000000-0000-0000-0000-000000000042"
        another_owner_folder = hashlib.sha256(
            b"another-owner"
        ).hexdigest()[:24]
        fake = _FakeDatabase(
            documents=[
                {
                    "id": document_id,
                    "case_id": case_id,
                    "owner_user_id": owner_user_id,
                    "document_code": "hometax_tax_payment_certificate",
                    "status": "ready",
                    "storage_bucket": "oasis-claim-documents",
                    "storage_path": (
                        f"{another_owner_folder}/{case_id}/"
                        f"{document_id}.pdf"
                    ),
                    "content_type": "application/pdf",
                    "retention_until": "2099-01-01T00:00:00+00:00",
                }
            ]
        )
        repository = ClaimRepository(owner_user_id, database=fake)

        with self.assertRaises(ClaimRepositoryError):
            repository.document_download_url(case_id, document_id)

        self.assertEqual(fake.signed_url_calls, [])

    def test_repository_refuses_expired_document_download(self):
        owner_user_id = "owner-user"
        case_id = "00000000-0000-0000-0000-000000000051"
        document_id = "00000000-0000-0000-0000-000000000052"
        owner_folder = hashlib.sha256(
            owner_user_id.encode("utf-8")
        ).hexdigest()[:24]
        fake = _FakeDatabase(
            documents=[
                {
                    "id": document_id,
                    "case_id": case_id,
                    "owner_user_id": owner_user_id,
                    "document_code": "hometax_tax_payment_certificate",
                    "status": "ready",
                    "storage_bucket": "oasis-claim-documents",
                    "storage_path": (
                        f"{owner_folder}/{case_id}/{document_id}.pdf"
                    ),
                    "content_type": "application/pdf",
                    "retention_until": "2020-01-01T00:00:00+00:00",
                }
            ]
        )
        repository = ClaimRepository(owner_user_id, database=fake)

        with self.assertRaises(ClaimRepositoryError):
            repository.document_download_url(case_id, document_id)

        self.assertEqual(fake.signed_url_calls, [])

    def test_repository_refuses_deleted_or_mismatched_document_download(self):
        owner_user_id = "owner-user"
        case_id = "00000000-0000-0000-0000-000000000061"
        document_id = "00000000-0000-0000-0000-000000000062"
        owner_folder = hashlib.sha256(
            owner_user_id.encode("utf-8")
        ).hexdigest()[:24]
        base_document = {
            "id": document_id,
            "case_id": case_id,
            "owner_user_id": owner_user_id,
            "document_code": "hometax_tax_payment_certificate",
            "status": "ready",
            "storage_bucket": "oasis-claim-documents",
            "storage_path": (
                f"{owner_folder}/{case_id}/{document_id}.pdf"
            ),
            "content_type": "application/pdf",
            "retention_until": "2099-01-01T00:00:00+00:00",
        }
        deleted = dict(
            base_document,
            deleted_at="2026-07-30T00:00:00+00:00",
        )
        fake = _FakeDatabase(documents=[deleted])
        repository = ClaimRepository(owner_user_id, database=fake)

        with self.assertRaises(ClaimRepositoryError):
            repository.document_download_url(case_id, document_id)
        self.assertEqual(fake.signed_url_calls, [])

        mismatched = dict(
            base_document,
            storage_path=(
                f"{owner_folder}/{case_id}/{document_id}.xlsx"
            ),
        )
        fake = _FakeDatabase(documents=[mismatched])
        repository = ClaimRepository(owner_user_id, database=fake)

        with self.assertRaises(ClaimRepositoryError):
            repository.document_download_url(case_id, document_id)
        self.assertEqual(fake.signed_url_calls, [])

    def test_repository_rejects_mismatched_file_extension_and_content_type(self):
        case = {
            "id": "00000000-0000-0000-0000-000000000021",
            "owner_user_id": "owner-user",
        }
        stored_document = {
            "id": "00000000-0000-0000-0000-000000000022",
            "case_id": case["id"],
            "owner_user_id": "owner-user",
            "source": "hometax",
            "document_code": "hometax_tax_payment_certificate",
            "status": "integration_required",
        }
        fake = _FakeDatabase([case], [stored_document])
        repository = ClaimRepository("owner-user", database=fake)
        with self.assertRaises(ClaimRepositoryError):
            repository.store_collected_document(
                case["id"],
                document_code="hometax_tax_payment_certificate",
                document=CollectedClaimDocument(
                    content=b"%PDF-1.7\nclaim-document",
                    file_name="certificate.xlsx",
                    content_type="application/pdf",
                    provider_reference="provider-reference",
                    facts={},
                ),
            )
        self.assertEqual(fake.uploads, [])

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

    def test_claim_document_format_migration_allows_verified_formats(self):
        source = (
            ROOT / "supabase_v1024_claim_document_formats.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("'application/pdf'", source)
        self.assertIn("'application/json'", source)
        self.assertIn(
            "'application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet'",
            source,
        )
        self.assertIn("'application/vnd.ms-excel'", source)
        self.assertIn("to service_role;", source)

    def test_multi_business_variant_migration_is_private_and_idempotent(
        self,
    ):
        source = (
            ROOT / "supabase_v1029_claim_multi_business_documents.sql"
        ).read_text(encoding="utf-8")
        normalized = " ".join(source.lower().split())

        self.assertIn(
            "collection_key ~ '^v_[0-9a-f]{32}$'",
            source,
        )
        self.assertIn("unique nulls not distinct", normalized)
        self.assertIn(
            "period_year, collection_key",
            normalized,
        )
        self.assertIn(
            "create or replace function "
            "public.oasis_claim_ensure_document_variant",
            normalized,
        )
        self.assertIn("security definer", normalized)
        self.assertIn("set search_path = public, pg_temp", normalized)
        self.assertIn(
            "on conflict on constraint "
            "oasis_claim_documents_variant_unique",
            normalized,
        )
        for private_field in (
            "identity_number",
            "birth_date",
            "cellphone",
            "business_number",
            "business_numbers",
            "management_number",
            "management_numbers",
            "resident_number",
        ):
            self.assertIn(f"'{private_field}'", source)
        self.assertIn(
            "from public, anon, authenticated, service_role;",
            normalized,
        )
        self.assertIn("to service_role;", normalized)
        self.assertNotIn("to anon;", normalized)
        self.assertNotIn("to authenticated;", normalized)

    def test_multi_business_migration_lists_every_document_deterministically(
        self,
    ):
        source = (
            ROOT / "supabase_v1029_claim_multi_business_documents.sql"
        ).read_text(encoding="utf-8")
        normalized = " ".join(source.lower().split())
        function_body = normalized.split(
            "create or replace function "
            "public.oasis_claim_list_documents",
            1,
        )[1].split(
            "create or replace function "
            "public.oasis_claim_ensure_document_variant",
            1,
        )[0]

        self.assertIn("returns setof public.oasis_claim_documents", function_body)
        self.assertIn("security definer", function_body)
        self.assertIn("set search_path = public, pg_temp", function_body)
        self.assertIn(
            "where d.owner_user_id = lower(trim(p_owner_user_id)) "
            "and d.case_id = p_case_id",
            function_body,
        )
        self.assertIn(
            "order by d.source asc, d.document_code asc, "
            "d.period_year desc nulls last, d.collection_key asc, d.id asc",
            function_body,
        )
        self.assertIn(
            "p_limit integer default 500, p_offset integer default 0",
            function_body,
        )
        self.assertIn(
            "limit greatest(1, least(coalesce(p_limit, 500), 500))",
            function_body,
        )
        self.assertIn(
            "offset greatest(0, coalesce(p_offset, 0))",
            function_body,
        )
        self.assertIn(
            "drop function if exists "
            "public.oasis_claim_list_documents( text, uuid );",
            normalized,
        )
        self.assertIn(
            "from public, anon, authenticated, service_role;",
            function_body,
        )
        self.assertIn("to service_role;", function_body)

    def test_repository_paginates_beyond_postgrest_max_rows(self):
        documents = [
            {
                "id": f"document-{index}",
                "case_id": "00000000-0000-0000-0000-000000000001",
            }
            for index in range(1250)
        ]
        database = MagicMock()

        def rpc(_function_name, parameters):
            offset = int(parameters["p_offset"])
            limit = int(parameters["p_limit"])
            return documents[offset : offset + limit]

        database.rpc.side_effect = rpc
        repository = ClaimRepository("owner-user", database=database)

        result = repository.list_documents(
            "00000000-0000-0000-0000-000000000001"
        )

        self.assertEqual(result, documents)
        self.assertEqual(database.rpc.call_count, 3)
        self.assertEqual(
            [
                call.args[1]["p_offset"]
                for call in database.rpc.call_args_list
            ],
            [0, 500, 1000],
        )

    def test_repository_falls_back_during_paginated_rpc_rollout(self):
        legacy_documents = [{"id": "legacy-document"}]
        database = MagicMock()
        database.rpc.side_effect = [
            RuntimeError(
                "PGRST202 Could not find the function "
                "public.oasis_claim_list_documents"
            ),
            legacy_documents,
        ]
        repository = ClaimRepository("owner-user", database=database)

        result = repository.list_documents(
            "00000000-0000-0000-0000-000000000001"
        )

        self.assertEqual(result, legacy_documents)
        self.assertEqual(database.rpc.call_count, 2)
        fallback_parameters = database.rpc.call_args_list[1].args[1]
        self.assertNotIn("p_limit", fallback_parameters)
        self.assertNotIn("p_offset", fallback_parameters)

    def test_personal_flow_sends_hometax_before_comwel(self):
        source = (ROOT / "claim_correction_center.py").read_text(
            encoding="utf-8"
        )
        personal_flow = source[
            source.index("def _render_personal_request"):
            source.index("def _render_corporate_request")
        ]
        self.assertIn('"홈택스 카카오 인증 발송"', personal_flow)
        self.assertIn('sources = ["hometax", "comwel"]', personal_flow)
        self.assertNotIn("request_comwel_kakao(", personal_flow)
        self.assertNotIn("상호명을 입력해주세요.", personal_flow)
        self.assertIn(
            "if business_digits and not _is_valid_business_no",
            personal_flow,
        )
        self.assertIn('if action == "check_hometax":', source)
        self.assertIn("comwel_session = client.request_comwel_kakao(", source)
        self.assertIn(
            "_ensure_claim_operation_active(should_continue)",
            source,
        )
        self.assertIn('transient["comwel"] = comwel_session', source)
        self.assertIn('remote_input = input_mode == "카카오톡 발송"', personal_flow)
        self.assertNotIn("_selected_customer(", personal_flow)
        self.assertNotIn("홈택스 카카오 인증 직접발송", personal_flow)
        self.assertNotIn("상호명 (선택)", personal_flow)
        self.assertNotIn("사업자등록번호 (선택)", personal_flow)
        self.assertNotIn("고객 본인입력 링크", personal_flow)
        self.assertNotIn("disabled=remote_input", personal_flow)
        self.assertIn('"주민등록번호 뒤 7자리"', personal_flow)


if __name__ == "__main__":
    unittest.main()
