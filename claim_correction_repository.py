from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from claim_correction_catalog import document_plan
from cloud_db import CloudDatabase, cloud_is_configured
from tilko_claim_client import CollectedClaimDocument


CLAIM_STORAGE_BUCKET = "oasis-claim-documents"
_CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "text/csv": ".csv",
}


class ClaimRepositoryError(RuntimeError):
    """A storage error whose text does not disclose backend details."""


@dataclass(frozen=True)
class ClaimRepositoryStatus:
    configured: bool
    available: bool
    message: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_owner(owner_user_id: str) -> str:
    owner = str(owner_user_id or "").strip().lower()
    if not owner:
        raise ClaimRepositoryError("로그인 사용자 정보를 확인할 수 없습니다.")
    return owner


def _masked_name(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= 1:
        return text
    if len(text) == 2:
        return f"{text[0]}*"
    return f"{text[0]}{'*' * (len(text) - 2)}{text[-1]}"


def _masked_phone(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) < 7:
        return ""
    return f"{digits[:3]}-****-{digits[-4:]}"


def _masked_business_no(value: Any) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) != 10:
        return ""
    return f"{digits[:3]}-**-*****"


def _customer_reference(
    owner_user_id: str,
    business_no: str,
    company_name: str,
    representative_name: str = "",
    cellphone: str = "",
) -> str:
    source = "|".join(
        (
            owner_user_id.strip().lower(),
            "".join(character for character in business_no if character.isdigit()),
            company_name.strip().lower(),
            representative_name.strip().lower(),
            "".join(character for character in cellphone if character.isdigit()),
        )
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _safe_storage_error(exc: Exception) -> ClaimRepositoryError:
    text = str(exc)
    if any(
        marker in text
        for marker in ("PGRST205", "42P01", "Could not find the table", "404")
    ):
        return ClaimRepositoryError(
            "경정청구 전용 Supabase 테이블을 먼저 설치해야 합니다."
        )
    return ClaimRepositoryError(
        "경정청구 저장소에 연결하지 못했습니다. 잠시 후 다시 시도해주세요."
    )


class ClaimRepository:
    def __init__(
        self,
        owner_user_id: str,
        database: CloudDatabase | None = None,
    ):
        self.owner_user_id = _required_owner(owner_user_id)
        if database is None and not cloud_is_configured():
            raise ClaimRepositoryError("Supabase 연결 설정이 필요합니다.")
        self.database = database or CloudDatabase()

    def status(self) -> ClaimRepositoryStatus:
        try:
            self.database.rpc(
                "oasis_claim_list_cases",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_limit": 1,
                },
            )
            return ClaimRepositoryStatus(True, True, "저장소 연결 완료")
        except Exception as exc:
            safe = _safe_storage_error(exc)
            return ClaimRepositoryStatus(True, False, str(safe))

    def create_case(
        self,
        *,
        company_name: str,
        business_no: str,
        business_type: str,
        representative_name: str,
        cellphone: str,
        requested_by: str,
        selected_sources: list[str],
        consent_version: str,
        consent_text_sha256: str,
        consent_channel: str,
        retention_policy_version: str,
        collection_authority_confirmed: bool,
    ) -> dict[str, Any]:
        if not collection_authority_confirmed:
            raise ClaimRepositoryError(
                "고객 동의와 자료조회 권한 확인이 필요합니다."
            )
        case_id = str(uuid.uuid4())
        now = _now()
        source_set = {str(source or "").strip().lower() for source in selected_sources}
        case = {
            "id": case_id,
            "owner_user_id": self.owner_user_id,
            "customer_ref": _customer_reference(
                self.owner_user_id,
                business_no,
                company_name,
                representative_name,
                cellphone,
            ),
            "company_name": str(company_name or "").strip(),
            "business_no_masked": _masked_business_no(business_no),
            "business_type": business_type,
            "representative_name_masked": _masked_name(representative_name),
            "phone_masked": _masked_phone(cellphone),
            "auth_method": (
                "kakao" if business_type == "individual" else "joint_certificate"
            ),
            "hometax_status": (
                "request_ready" if "hometax" in source_set else "not_requested"
            ),
            "comwel_status": (
                "request_ready" if "comwel" in source_set else "not_requested"
            ),
            "overall_status": "auth_preparing",
            "consent_version": consent_version,
            "consent_confirmed_at": now,
            "consent_text_sha256": str(consent_text_sha256 or "").strip(),
            "consent_channel": str(consent_channel or "").strip(),
            "retention_policy_version": str(
                retention_policy_version or ""
            ).strip(),
            "collection_authority_confirmed_at": now,
            "requested_by": str(requested_by or self.owner_user_id).strip(),
            "requested_at": now,
            "updated_at": now,
        }
        try:
            documents = []
            for row in document_plan():
                source = "hometax" if row["source"] == "홈택스" else "comwel"
                if source not in source_set:
                    continue
                documents.append(
                    {
                        "id": str(uuid.uuid4()),
                        "owner_user_id": self.owner_user_id,
                        "case_id": case_id,
                        "source": source,
                        "document_code": row["document_code"],
                        "document_name": row["document_name"],
                        "period_year": row["period_year"],
                        "status": "auth_pending",
                        "facts": {},
                        "created_at": now,
                        "updated_at": now,
                    }
                )
            self.database.rpc(
                "oasis_create_claim_case",
                {
                    "p_case": case,
                    "p_documents": documents,
                    "p_audit": {
                        "action": "case_created",
                        "source": "system",
                        "outcome": "success",
                        "metadata": {
                            "business_type": business_type,
                            "sources": sorted(source_set),
                            "document_count": len(documents),
                        },
                    },
                },
            )
            return case
        except Exception as exc:
            raise _safe_storage_error(exc) from exc

    def append_audit_event(
        self,
        *,
        case_id: str,
        action: str,
        source: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        safe_metadata = {
            key: value
            for key, value in dict(metadata or {}).items()
            if key
            not in {
                "identity_number",
                "birth_date",
                "cellphone",
                "token",
                "certificate",
                "password",
            }
        }
        try:
            self.database.rpc(
                "oasis_claim_append_audit",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_case_id": str(case_id),
                    "p_action": action,
                    "p_source": source,
                    "p_outcome": outcome,
                    "p_metadata": safe_metadata,
                },
            )
        except Exception:
            # 감사로그 실패가 인증 원문이나 예외 원문을 로컬에 남기지 않도록
            # 별도 재시도 큐를 사용하지 않는다.
            return

    def list_cases(self, limit: int = 500) -> list[dict[str, Any]]:
        try:
            rows = self.database.rpc(
                "oasis_claim_list_cases",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_limit": max(1, min(int(limit), 1000)),
                },
            )
            return rows if isinstance(rows, list) else []
        except Exception as exc:
            raise _safe_storage_error(exc) from exc

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        try:
            rows = self.database.rpc(
                "oasis_claim_get_case",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_case_id": str(case_id),
                },
            )
            return rows[0] if isinstance(rows, list) and rows else None
        except Exception as exc:
            raise _safe_storage_error(exc) from exc

    def list_documents(self, case_id: str) -> list[dict[str, Any]]:
        try:
            rows = self.database.rpc(
                "oasis_claim_list_documents",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_case_id": str(case_id),
                },
            )
            return rows if isinstance(rows, list) else []
        except Exception as exc:
            raise _safe_storage_error(exc) from exc

    def update_document_status(
        self,
        case_id: str,
        *,
        source: str,
        status: str,
    ) -> None:
        current = self.get_case(case_id)
        if current is None:
            raise ClaimRepositoryError("해당 경정청구 건을 찾을 수 없습니다.")
        current_owner = str(current.get("owner_user_id", "")).strip().lower()
        if current_owner != self.owner_user_id:
            raise ClaimRepositoryError("해당 경정청구 건에 접근할 수 없습니다.")
        safe_source = str(source or "").strip().lower()
        if safe_source not in {"hometax", "comwel"}:
            raise ClaimRepositoryError("수집 기관을 확인할 수 없습니다.")
        try:
            self.database.rpc(
                "oasis_claim_update_document_status",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_case_id": str(case_id),
                    "p_source": safe_source,
                    "p_status": str(status or "").strip(),
                },
            )
        except Exception as exc:
            raise _safe_storage_error(exc) from exc

    def update_case_status(
        self,
        case_id: str,
        **updates: Any,
    ) -> dict[str, Any]:
        allowed = {
            "hometax_status",
            "comwel_status",
            "overall_status",
            "auth_requested_at",
            "auth_completed_at",
            "last_safe_error_code",
        }
        clean_updates = {
            key: value
            for key, value in updates.items()
            if key in allowed
        }
        current = self.get_case(case_id)
        if current is None:
            raise ClaimRepositoryError("해당 경정청구 건을 찾을 수 없습니다.")
        current_owner = str(current.get("owner_user_id", "")).strip().lower()
        if current_owner != self.owner_user_id:
            raise ClaimRepositoryError("해당 경정청구 건에 접근할 수 없습니다.")
        try:
            rows = self.database.rpc(
                "oasis_claim_update_case_status",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_case_id": str(case_id),
                    "p_updates": clean_updates,
                },
            )
            if isinstance(rows, list) and rows:
                return rows[0]
            current.update(clean_updates)
            return current
        except Exception as exc:
            raise _safe_storage_error(exc) from exc

    def _document_by_code(
        self,
        case_id: str,
        document_code: str,
        period_year: int | None = None,
    ) -> dict[str, Any]:
        documents = self.list_documents(case_id)
        matches = [
            document
            for document in documents
            if str(document.get("document_code", "")) == document_code
            and (
                period_year is None
                or int(document.get("period_year") or 0) == int(period_year)
            )
        ]
        if len(matches) != 1:
            raise ClaimRepositoryError(
                "수집할 경정청구 서류 항목을 확인하지 못했습니다."
            )
        return matches[0]

    def store_collected_document(
        self,
        case_id: str,
        *,
        document_code: str,
        document: CollectedClaimDocument,
        period_year: int | None = None,
    ) -> dict[str, Any]:
        target = self._document_by_code(
            case_id,
            document_code,
            period_year,
        )
        document_id = str(target.get("id", "")).strip()
        if not document_id:
            raise ClaimRepositoryError("서류 저장 식별값이 없습니다.")
        owner_folder = hashlib.sha256(
            self.owner_user_id.encode("utf-8")
        ).hexdigest()[:24]
        requested_extension = Path(
            str(document.file_name or "")
        ).suffix.lower()
        expected_extension = _CONTENT_TYPE_EXTENSIONS.get(
            str(document.content_type or "").lower(),
            "",
        )
        if (
            requested_extension
            and expected_extension
            and requested_extension != expected_extension
        ):
            raise ClaimRepositoryError(
                "서류 파일 형식과 콘텐츠 형식이 일치하지 않습니다."
            )
        extension = (
            requested_extension
            if requested_extension
            in set(_CONTENT_TYPE_EXTENSIONS.values())
            else expected_extension
        )
        if not extension:
            raise ClaimRepositoryError(
                "저장할 서류 파일 형식을 확인할 수 없습니다."
            )
        storage_path = f"{owner_folder}/{case_id}/{document_id}{extension}"
        content_sha256 = hashlib.sha256(document.content).hexdigest()
        retention_days = max(
            1,
            min(
                int(os.environ.get("CLAIM_DOCUMENT_RETENTION_DAYS", "90")),
                365,
            ),
        )
        retention_until = (
            datetime.now(timezone.utc) + timedelta(days=retention_days)
        ).isoformat()
        provider_reference_hash = (
            hashlib.sha256(
                document.provider_reference.encode("utf-8")
            ).hexdigest()
            if document.provider_reference
            else ""
        )
        facts = {
            key: value
            for key, value in dict(document.facts or {}).items()
            if key not in {"identity_number", "birth_date", "cellphone"}
        }
        facts["download_file_name"] = (
            str(document.file_name or "").strip()
            or f"{document_code}{extension}"
        )
        if provider_reference_hash:
            facts["provider_reference_sha256"] = provider_reference_hash

        try:
            self.database.upload_private_object(
                CLAIM_STORAGE_BUCKET,
                storage_path,
                document.content,
                document.content_type,
            )
            rows = self.database.rpc(
                "oasis_claim_finalize_document",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_case_id": str(case_id),
                    "p_document_id": document_id,
                    "p_status": "ready",
                    "p_storage_bucket": CLAIM_STORAGE_BUCKET,
                    "p_storage_path": storage_path,
                    "p_content_sha256": content_sha256,
                    "p_content_type": document.content_type,
                    "p_size_bytes": len(document.content),
                    "p_retention_until": retention_until,
                    "p_facts": facts,
                    "p_safe_error_code": None,
                },
            )
        except Exception as exc:
            try:
                self.database.delete_private_object(
                    CLAIM_STORAGE_BUCKET,
                    storage_path,
                )
            except Exception:
                pass
            raise _safe_storage_error(exc) from exc
        if isinstance(rows, list) and rows:
            return rows[0]
        target.update(
            {
                "status": "ready",
                "storage_bucket": CLAIM_STORAGE_BUCKET,
                "storage_path": storage_path,
                "content_sha256": content_sha256,
                "content_type": document.content_type,
                "size_bytes": len(document.content),
                "retention_until": retention_until,
            }
        )
        return target

    def fail_document(
        self,
        case_id: str,
        *,
        document_code: str,
        safe_error_code: str,
        period_year: int | None = None,
    ) -> None:
        target = self._document_by_code(
            case_id,
            document_code,
            period_year,
        )
        try:
            self.database.rpc(
                "oasis_claim_finalize_document",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_case_id": str(case_id),
                    "p_document_id": str(target.get("id", "")),
                    "p_status": "failed",
                    "p_storage_bucket": None,
                    "p_storage_path": None,
                    "p_content_sha256": None,
                    "p_content_type": None,
                    "p_size_bytes": None,
                    "p_retention_until": None,
                    "p_facts": {},
                    "p_safe_error_code": str(safe_error_code or "")[:80],
                },
            )
        except Exception as exc:
            raise _safe_storage_error(exc) from exc

    def document_download_url(
        self,
        case_id: str,
        document_id: str,
    ) -> str:
        target = next(
            (
                document
                for document in self.list_documents(case_id)
                if str(document.get("id", "")) == str(document_id)
            ),
            None,
        )
        if (
            target is None
            or str(target.get("status", "")) != "ready"
            or not target.get("storage_bucket")
            or not target.get("storage_path")
        ):
            raise ClaimRepositoryError(
                "다운로드 가능한 서류를 찾지 못했습니다."
            )
        try:
            facts = target.get("facts")
            download_name = ""
            if isinstance(facts, dict):
                download_name = str(
                    facts.get("download_file_name", "") or ""
                ).strip()
            if not download_name:
                extension = Path(
                    str(target.get("storage_path", "") or "")
                ).suffix
                download_name = (
                    f"{target.get('document_code') or 'claim-document'}"
                    f"{extension or '.bin'}"
                )
            return self.database.create_private_signed_url(
                str(target["storage_bucket"]),
                str(target["storage_path"]),
                expires_in=60,
                download_name=download_name,
            )
        except Exception as exc:
            raise _safe_storage_error(exc) from exc
