from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from claim_correction_catalog import (
    automatic_collection_supported,
    document_plan,
)
from cloud_db import CloudDatabase, cloud_is_configured
from tilko_claim_client import CollectedClaimDocument


CLAIM_STORAGE_BUCKET = "oasis-claim-documents"
CLAIM_DOWNLOAD_URL_TTL_SECONDS = 60
CLAIM_DOCUMENT_PAGE_SIZE = 500
CLAIM_DOWNLOAD_EXTENSION_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "application/json": ".json",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "text/csv": ".csv",
}
CLAIM_DEFAULT_COLLECTION_KEY = "default"
_CLAIM_COLLECTION_KEY_PATTERN = re.compile(r"^v_[0-9a-f]{32}$")
_PRIVATE_RESIDENT_NUMBER_PATTERN = re.compile(
    r"(?<!\d)(\d{6})[- _]?(\d{7})(?!\d)"
)
_PRIVATE_MOBILE_NUMBER_PATTERN = re.compile(
    r"(?<!\d)(01[016789])[- _]?(\d{3,4})[- _]?(\d{4})(?!\d)"
)
_PRIVATE_BUSINESS_NUMBER_PATTERN = re.compile(
    r"(?<!\d)(\d{3})[- _]?(\d{2})[- _]?(\d{3})(\d{2})(?!\d)"
)
_PRIVATE_METADATA_KEY_FRAGMENTS = (
    "apikey",
    "apitxkey",
    "authorization",
    "birthdate",
    "businessnumber",
    "cellphone",
    "certificate",
    "cookie",
    "credential",
    "cxid",
    "identitynumber",
    "jumin",
    "managementnumber",
    "mobilenumber",
    "password",
    "phonenumber",
    "reqtxid",
    "residentnumber",
    "residentregistration",
    "rgno",
    "secret",
    "session",
    "ssn",
    "token",
    "txid",
    "wonbuno",
)
_SAFE_AGGREGATE_KEY_SUFFIXES = (
    "amount",
    "available",
    "bytes",
    "count",
    "exists",
    "flag",
    "masked",
    "present",
    "size",
    "status",
    "sum",
    "total",
)


def _owner_storage_folder(owner_user_id: str) -> str:
    return hashlib.sha256(owner_user_id.encode("utf-8")).hexdigest()[:24]


def _normalized_metadata_key(value: Any) -> str:
    return "".join(
        character
        for character in str(value or "").casefold()
        if character.isalnum()
    )


def _private_metadata_key(value: Any) -> bool:
    normalized = _normalized_metadata_key(value)
    if not normalized:
        return False
    if normalized.endswith(_SAFE_AGGREGATE_KEY_SUFFIXES):
        return False
    return any(
        fragment in normalized
        for fragment in _PRIVATE_METADATA_KEY_FRAGMENTS
    )


def _mask_private_text(value: Any, *, mask_business_number: bool = False) -> str:
    text = str(value or "")

    def mask_resident_number(match: re.Match[str]) -> str:
        try:
            datetime.strptime(match.group(1), "%y%m%d")
        except ValueError:
            return match.group(0)
        return "ID-REDACTED"

    text = _PRIVATE_RESIDENT_NUMBER_PATTERN.sub(mask_resident_number, text)
    text = _PRIVATE_MOBILE_NUMBER_PATTERN.sub(
        lambda match: f"{match.group(1)}-****-{match.group(3)}",
        text,
    )
    if mask_business_number:
        text = _PRIVATE_BUSINESS_NUMBER_PATTERN.sub(
            lambda match: (
                f"{match.group(1)}-XX-XXX{match.group(4)}"
            ),
            text,
        )
    return text


def _sanitize_private_metadata(value: Any) -> Any:
    """Recursively remove secret fields and mask private scalar patterns."""
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, candidate in value.items():
            safe_key = str(key)
            if _private_metadata_key(safe_key):
                continue
            sanitized[safe_key] = _sanitize_private_metadata(candidate)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_sanitize_private_metadata(candidate) for candidate in value]
    if isinstance(value, str):
        return _mask_private_text(value)
    if isinstance(value, int) and not isinstance(value, bool):
        masked = _mask_private_text(value)
        return value if masked == str(value) else masked
    return value


def _safe_download_file_name(value: Any, fallback: str) -> str:
    raw_name = str(value or "").replace("\\", "/").split("/")[-1].strip()
    raw_name = _mask_private_text(
        raw_name,
        mask_business_number=True,
    ).replace("-****-", "-XXXX-")
    clean_name = "".join(
        character
        for character in raw_name
        if ord(character) >= 32
        and character not in {'"', "*", ":", "<", ">", "?", "|"}
    ).strip(" .")
    if not clean_name:
        clean_name = _mask_private_text(
            str(fallback or "claim-document.bin").strip(),
            mask_business_number=True,
        ).replace("-****-", "-XXXX-")
    return clean_name[:180]


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


def _collection_key(value: Any) -> str:
    selected = str(value or CLAIM_DEFAULT_COLLECTION_KEY).strip().lower()
    if (
        selected != CLAIM_DEFAULT_COLLECTION_KEY
        and not _CLAIM_COLLECTION_KEY_PATTERN.fullmatch(selected)
    ):
        raise ClaimRepositoryError(
            "수집 자료 구분값을 확인하지 못했습니다."
        )
    return selected


def _safe_variant_facts(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    sanitized = _sanitize_private_metadata(value)
    return sanitized if isinstance(sanitized, dict) else {}


def _rpc_signature_unavailable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "pgrst202",
            "could not find the function",
            "function public.oasis_claim_list_documents",
            "oasis_claim_list_documents(p_case_id",
        )
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
        case_id: str | None = None,
    ) -> dict[str, Any]:
        if not collection_authority_confirmed:
            raise ClaimRepositoryError(
                "고객 동의와 자료조회 권한 확인이 필요합니다."
            )
        try:
            case_id = (
                str(uuid.UUID(str(case_id).strip()))
                if case_id
                else str(uuid.uuid4())
            )
        except (ValueError, TypeError, AttributeError) as exc:
            raise ClaimRepositoryError(
                "경정청구 요청 식별값을 확인하지 못했습니다."
            ) from exc
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
                        "collection_key": CLAIM_DEFAULT_COLLECTION_KEY,
                        "status": (
                            "auth_pending"
                            if automatic_collection_supported(
                                str(row["document_code"])
                            )
                            else "integration_required"
                        ),
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
        safe_metadata = _safe_variant_facts(metadata)
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

    def delete_case(self, case_id: str) -> None:
        """Hide one owner-scoped case while preserving its audit trail."""

        safe_case_id = str(case_id or "").strip()
        if not safe_case_id:
            raise ClaimRepositoryError("삭제할 경정청구 건을 확인하지 못했습니다.")
        current = self.get_case(safe_case_id)
        if current is None:
            raise ClaimRepositoryError("해당 경정청구 건을 찾을 수 없습니다.")
        current_owner = str(current.get("owner_user_id", "")).strip().lower()
        if current_owner != self.owner_user_id:
            raise ClaimRepositoryError("해당 경정청구 건에 접근할 수 없습니다.")
        try:
            deleted = self.database.rpc(
                "oasis_claim_soft_delete_case",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_case_id": safe_case_id,
                },
            )
        except Exception as exc:
            raise _safe_storage_error(exc) from exc
        if deleted is not True:
            raise ClaimRepositoryError("고객을 목록에서 삭제하지 못했습니다.")

    def list_documents(self, case_id: str) -> list[dict[str, Any]]:
        safe_case_id = str(case_id)
        collected: list[dict[str, Any]] = []
        offset = 0
        try:
            while True:
                try:
                    rows = self.database.rpc(
                        "oasis_claim_list_documents",
                        {
                            "p_owner_user_id": self.owner_user_id,
                            "p_case_id": safe_case_id,
                            "p_limit": CLAIM_DOCUMENT_PAGE_SIZE,
                            "p_offset": offset,
                        },
                    )
                except Exception as exc:
                    if offset != 0 or not _rpc_signature_unavailable(exc):
                        raise
                    # Rolling-deploy compatibility: an app release can reach
                    # the legacy two-argument RPC before v10.2.9 is applied.
                    # Once the migration lands, all reads use paginated calls.
                    legacy_rows = self.database.rpc(
                        "oasis_claim_list_documents",
                        {
                            "p_owner_user_id": self.owner_user_id,
                            "p_case_id": safe_case_id,
                        },
                    )
                    return (
                        legacy_rows
                        if isinstance(legacy_rows, list)
                        else []
                    )
                page = rows if isinstance(rows, list) else []
                collected.extend(
                    row for row in page if isinstance(row, dict)
                )
                if len(page) < CLAIM_DOCUMENT_PAGE_SIZE:
                    return collected
                offset += CLAIM_DOCUMENT_PAGE_SIZE
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
        collection_key: str = CLAIM_DEFAULT_COLLECTION_KEY,
    ) -> dict[str, Any]:
        selected_collection_key = _collection_key(collection_key)
        documents = self.list_documents(case_id)
        matches = [
            document
            for document in documents
            if str(document.get("document_code", "")) == document_code
            and (
                period_year is None
                or int(document.get("period_year") or 0) == int(period_year)
            )
            and str(
                document.get(
                    "collection_key",
                    CLAIM_DEFAULT_COLLECTION_KEY,
                )
                or CLAIM_DEFAULT_COLLECTION_KEY
            ).strip().lower()
            == selected_collection_key
        ]
        if len(matches) != 1:
            raise ClaimRepositoryError(
                "수집할 경정청구 서류 항목을 확인하지 못했습니다."
            )
        return matches[0]

    def ensure_document_variant(
        self,
        case_id: str,
        *,
        document_code: str,
        collection_key: str,
        period_year: int | None = None,
        facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_collection_key = _collection_key(collection_key)
        safe_facts = _safe_variant_facts(facts)
        try:
            rows = self.database.rpc(
                "oasis_claim_ensure_document_variant",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_case_id": str(case_id),
                    "p_document_code": str(document_code or "").strip(),
                    "p_period_year": (
                        int(period_year) if period_year is not None else None
                    ),
                    "p_collection_key": selected_collection_key,
                    "p_document_id": str(uuid.uuid4()),
                    "p_facts": safe_facts,
                },
            )
        except Exception as exc:
            raise _safe_storage_error(exc) from exc
        if isinstance(rows, list) and rows:
            return rows[0]
        return self._document_by_code(
            case_id,
            document_code,
            period_year,
            selected_collection_key,
        )

    def store_collected_document(
        self,
        case_id: str,
        *,
        document_code: str,
        document: CollectedClaimDocument,
        period_year: int | None = None,
        collection_key: str = CLAIM_DEFAULT_COLLECTION_KEY,
    ) -> dict[str, Any]:
        selected_collection_key = _collection_key(collection_key)
        if selected_collection_key == CLAIM_DEFAULT_COLLECTION_KEY:
            target = self._document_by_code(
                case_id,
                document_code,
                period_year,
                selected_collection_key,
            )
        else:
            target = self.ensure_document_variant(
                case_id,
                document_code=document_code,
                period_year=period_year,
                collection_key=selected_collection_key,
                # Facts are finalized only after the replacement object is
                # safely uploaded. Updating an existing ready variant here
                # could make an old file appear to belong to a new scope if
                # the upload or finalize step later failed.
                facts={},
            )
        document_id = str(target.get("id", "")).strip()
        if not document_id:
            raise ClaimRepositoryError("서류 저장 식별값이 없습니다.")
        owner_folder = _owner_storage_folder(self.owner_user_id)
        requested_extension = Path(
            str(document.file_name or "")
        ).suffix.lower()
        expected_extension = CLAIM_DOWNLOAD_EXTENSION_BY_CONTENT_TYPE.get(
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
            in set(CLAIM_DOWNLOAD_EXTENSION_BY_CONTENT_TYPE.values())
            else expected_extension
        )
        if not extension:
            raise ClaimRepositoryError(
                "저장할 서류 파일 형식을 확인할 수 없습니다."
            )
        content_sha256 = hashlib.sha256(document.content).hexdigest()
        previous_storage_bucket = str(
            target.get("storage_bucket", "") or ""
        ).strip()
        previous_storage_path = str(
            target.get("storage_path", "") or ""
        ).strip()
        previous_content_sha256 = str(
            target.get("content_sha256", "") or ""
        ).strip().lower()
        previous_content_type = str(
            target.get("content_type", "") or ""
        ).strip().lower()
        reusable_previous_object = bool(
            str(target.get("status", "")) == "ready"
            and previous_storage_bucket == CLAIM_STORAGE_BUCKET
            and previous_storage_path
            and previous_content_sha256 == content_sha256
            and previous_content_type
            == str(document.content_type or "").strip().lower()
        )
        storage_path = (
            previous_storage_path
            if reusable_previous_object
            else (
                f"{owner_folder}/{case_id}/{document_id}-"
                f"{uuid.uuid4().hex[:16]}{extension}"
                if previous_storage_path
                else f"{owner_folder}/{case_id}/{document_id}{extension}"
            )
        )
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
        facts = _safe_variant_facts(document.facts)
        facts["download_file_name"] = _safe_download_file_name(
            str(document.file_name or "").strip(),
            f"{document_code}{extension}",
        )
        if provider_reference_hash:
            facts["provider_reference_sha256"] = provider_reference_hash

        try:
            if not reusable_previous_object:
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
            # Preserve the uploaded object when metadata finalization fails.
            # The content-addressed path can be reconciled or reused on retry;
            # deleting it here could destroy the only durable customer copy.
            raise _safe_storage_error(exc) from exc
        # Previous versions are intentionally retained as customer records.
        # They may be reconciled by a future version index, but are never
        # physically deleted during collection or replacement.
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
        collection_key: str = CLAIM_DEFAULT_COLLECTION_KEY,
        facts: dict[str, Any] | None = None,
    ) -> None:
        selected_collection_key = _collection_key(collection_key)
        if selected_collection_key == CLAIM_DEFAULT_COLLECTION_KEY:
            target = self._document_by_code(
                case_id,
                document_code,
                period_year,
                selected_collection_key,
            )
        else:
            target = self.ensure_document_variant(
                case_id,
                document_code=document_code,
                period_year=period_year,
                collection_key=selected_collection_key,
                facts=facts,
            )
        safe_facts = _safe_variant_facts(facts)
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
                    "p_facts": safe_facts,
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
        safe_case_id = str(case_id or "").strip()
        safe_document_id = str(document_id or "").strip()
        documents = self.list_documents(safe_case_id)
        target = next(
            (
                document
                for document in documents
                if str(document.get("id", "")) == safe_document_id
            ),
            None,
        )
        return self._document_download_url_from_target(
            safe_case_id,
            safe_document_id,
            target,
        )

    def document_download_urls(
        self,
        case_id: str,
        document_ids: list[str],
    ) -> list[str]:
        """Issue document links after one owner-scoped document-list read.

        Every requested document still passes the same ownership, case,
        status, retention, bucket, path, content-type and filename checks as
        :meth:`document_download_url`.  A separate audit event is also kept
        for every signed link, matching the single-document behavior.
        """

        safe_case_id = str(case_id or "").strip()
        safe_document_ids = [
            str(document_id or "").strip()
            for document_id in document_ids
        ]
        if not safe_document_ids:
            return []
        documents = self.list_documents(safe_case_id)
        documents_by_id = {
            str(document.get("id", "")): document
            for document in documents
            if isinstance(document, dict)
        }
        return [
            self._document_download_url_from_target(
                safe_case_id,
                safe_document_id,
                documents_by_id.get(safe_document_id),
            )
            for safe_document_id in safe_document_ids
        ]

    def _document_download_url_from_target(
        self,
        safe_case_id: str,
        safe_document_id: str,
        target: dict[str, Any] | None,
    ) -> str:
        if (
            target is None
            or str(target.get("owner_user_id", "")).strip().lower()
            != self.owner_user_id
            or str(target.get("case_id", "")).strip() != safe_case_id
            or str(target.get("status", "")) != "ready"
            or target.get("deleted_at")
            or not target.get("storage_bucket")
            or not target.get("storage_path")
        ):
            raise ClaimRepositoryError(
                "다운로드 가능한 서류를 찾지 못했습니다."
            )
        storage_bucket = str(target.get("storage_bucket", "")).strip()
        storage_path = str(target.get("storage_path", "")).strip().lstrip("/")
        storage_parts = PurePosixPath(storage_path).parts
        expected_owner_folder = _owner_storage_folder(self.owner_user_id)
        content_type = str(target.get("content_type", "") or "").strip().lower()
        expected_extension = CLAIM_DOWNLOAD_EXTENSION_BY_CONTENT_TYPE.get(
            content_type,
            "",
        )
        storage_stem = (
            PurePosixPath(storage_parts[2]).stem
            if len(storage_parts) == 3
            else ""
        )
        valid_document_stem = bool(
            storage_stem == safe_document_id
            or re.fullmatch(
                rf"{re.escape(safe_document_id)}-[0-9a-f]{{16}}",
                storage_stem,
            )
        )
        if (
            storage_bucket != CLAIM_STORAGE_BUCKET
            or len(storage_parts) != 3
            or storage_parts[0] != expected_owner_folder
            or storage_parts[1] != safe_case_id
            or not valid_document_stem
            or PurePosixPath(storage_parts[2]).suffix.lower()
            != expected_extension
        ):
            raise ClaimRepositoryError(
                "다운로드 가능한 서류를 찾지 못했습니다."
            )
        retention_until = str(target.get("retention_until", "") or "").strip()
        if not retention_until:
            raise ClaimRepositoryError(
                "서류 보관기한을 확인할 수 없습니다."
            )
        try:
            retention_deadline = datetime.fromisoformat(
                retention_until.replace("Z", "+00:00")
            )
            if retention_deadline.tzinfo is None:
                retention_deadline = retention_deadline.replace(
                    tzinfo=timezone.utc
                )
        except ValueError as exc:
            raise ClaimRepositoryError(
                "서류 보관기한을 확인할 수 없습니다."
            ) from exc
        if retention_deadline <= datetime.now(timezone.utc):
            raise ClaimRepositoryError(
                "서류 보관기한이 만료되었습니다."
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
                    storage_path
                ).suffix
                download_name = (
                    f"{target.get('document_code') or 'claim-document'}"
                    f"{extension or '.bin'}"
                )
            download_name = _safe_download_file_name(
                download_name,
                f"{target.get('document_code') or 'claim-document'}"
                f"{expected_extension}",
            )
            if PurePosixPath(download_name).suffix.lower() != expected_extension:
                download_name = _safe_download_file_name(
                    (
                        f"{target.get('document_code') or 'claim-document'}"
                        f"{expected_extension}"
                    ),
                    f"claim-document{expected_extension}",
                )
            download_url = self.database.create_private_signed_url(
                storage_bucket,
                storage_path,
                expires_in=CLAIM_DOWNLOAD_URL_TTL_SECONDS,
                download_name=download_name,
            )
            self.append_audit_event(
                case_id=safe_case_id,
                action="download_link_issued",
                source=str(target.get("source", "") or "system"),
                outcome="success",
                metadata={
                    "document_id": safe_document_id,
                    "document_code": str(
                        target.get("document_code", "") or ""
                    ),
                    "link_ttl_seconds": CLAIM_DOWNLOAD_URL_TTL_SECONDS,
                },
            )
            return download_url
        except Exception as exc:
            raise _safe_storage_error(exc) from exc
