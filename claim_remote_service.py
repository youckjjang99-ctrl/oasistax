from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from claim_correction_repository import ClaimRepository, ClaimRepositoryError
from claim_remote_crypto import remote_claim_crypto_readiness
from claim_remote_repository import (
    ClaimRemoteRepository,
    ClaimRemoteRepositoryError,
)
from solapi_alimtalk_client import (
    SolapiAlimtalkClient,
    SolapiAlimtalkConfig,
    SolapiAlimtalkError,
    environment_readiness as solapi_environment_readiness,
    guidance_send_readiness,
)
from tilko_claim_client import (
    ClaimProviderError,
    TilkoClaimClient,
    is_transient_provider_error,
)


REMOTE_CONSENT_VERSION = "claim-remote-customer-v1-2026-07-31"
REMOTE_RETENTION_POLICY_VERSION = "claim-document-retention-v1-2026-07"
REMOTE_JOB_TTL_SECONDS = 45 * 60
REMOTE_AUTH_TTL_SECONDS = 10 * 60
REMOTE_SUBMISSION_RESERVATION_SECONDS = 5 * 60
PROSPECT_INVITE_TTL_SECONDS = 7 * 24 * 60 * 60

STAFF_TARGETED_FLOW = "staff_targeted"
PROSPECT_SELF_INPUT_FLOW = "prospect_self_input"

TEMPLATE_AUTH_START = "auth_start"
TEMPLATE_AUTH_RESUME = "auth_resume"
TEMPLATE_NEXT_AUTH = "next_auth"
TEMPLATE_COMPLETE = "complete"
TEMPLATE_FAILED = "failed"

TEMPLATE_ENV_BY_CODE = {
    TEMPLATE_AUTH_START: "SOLAPI_TEMPLATE_AUTH_START_ID",
    TEMPLATE_AUTH_RESUME: "SOLAPI_TEMPLATE_AUTH_RESUME_ID",
    TEMPLATE_NEXT_AUTH: "SOLAPI_TEMPLATE_NEXT_AUTH_ID",
    TEMPLATE_COMPLETE: "SOLAPI_TEMPLATE_COMPLETE_ID",
    TEMPLATE_FAILED: "SOLAPI_TEMPLATE_FAILED_ID",
    "GUIDANCE_EMPLOYMENT_SUPPORT": (
        "SOLAPI_TEMPLATE_GUIDANCE_EMPLOYMENT_SUPPORT_ID"
    ),
    "GUIDANCE_POLICY_FUNDING": "SOLAPI_TEMPLATE_GUIDANCE_POLICY_FUNDING_ID",
    "GUIDANCE_TAX_CREDIT": "SOLAPI_TEMPLATE_GUIDANCE_TAX_CREDIT_ID",
}

_PHONE_PATTERN = re.compile(r"^01(?:0|1|6|7|8|9)\d{7,8}$")
_SAFE_ERROR_CODE_PATTERN = re.compile(r"^[A-Z0-9_-]{1,80}$")
_DEFAULT_WORKER_OWNER = "claim-remote-worker-system"


def _explicitly_enabled(value: Any) -> bool:
    """Return true only for an explicit opt-in value."""

    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _notify_guidance_outbox_status(
    *,
    guidance_message_id: str,
    status: str,
    provider_message_id: str = "",
    error_code: str = "",
) -> None:
    """Best-effort bridge to the DB-discovery guidance status store.

    The import is deliberately lazy so the existing claim worker does not
    acquire a hard dependency on the optional DB-discovery feature.  Callback
    failures never change the outbox result and never weaken the send gate.
    """

    if not str(guidance_message_id or "").strip():
        return
    try:
        from company_kakao_guidance import (  # noqa: PLC0415
            notify_guidance_outbox_status,
        )

        notify_guidance_outbox_status(
            guidance_message_id=str(guidance_message_id).strip(),
            status=str(status or "").strip().lower()[:40],
            provider_message_id=str(provider_message_id or "").strip()[:200],
            error_code=_safe_error_code(error_code, "") if error_code else "",
        )
    except Exception:
        # Never log callback arguments: they can be correlated with a customer
        # communication.  The guidance service can reconcile from outbox state.
        return


def _check_guidance_send_ready(
    guidance_message_id: str,
    canonical_contact_id: str,
    recipient_phone_hash: str,
) -> dict[str, Any]:
    """Fail closed unless the guidance row is still sendable.

    Cancellation, customer opt-out and administrator contact controls can be
    changed after an outbox row is leased.  The provider worker therefore
    performs this server-side check immediately before making the external
    request.  No recipient or customer data is returned by this bridge.
    """

    message_id = str(guidance_message_id or "").strip()
    contact_id = str(canonical_contact_id or "").strip()
    phone_hash = str(recipient_phone_hash or "").strip().lower()
    if not message_id or not contact_id or not re.fullmatch(
        r"[0-9a-f]{64}", phone_hash
    ):
        return {
            "allowed": False,
            "code": "GUIDANCE_DELIVERY_BINDING_INVALID",
        }
    try:
        from company_kakao_guidance import (  # noqa: PLC0415
            check_guidance_send_ready,
        )

        result = check_guidance_send_ready(
            message_id,
            canonical_contact_id=contact_id,
            recipient_phone_hash=phone_hash,
        )
        if (
            not isinstance(result, Mapping)
            or not isinstance(result.get("allowed"), bool)
        ):
            return {
                "allowed": False,
                "code": "GUIDANCE_SEND_STATE_UNAVAILABLE",
            }
        return {
            "allowed": result["allowed"],
            "code": _safe_error_code(
                result.get("code", ""),
                "GUIDANCE_SEND_STATE_BLOCKED",
            ),
        }
    except Exception:
        # Fail closed and do not log connector details.  RPC/provider errors
        # can contain identifiers that must not be exposed in application logs.
        return {
            "allowed": False,
            "code": "GUIDANCE_SEND_STATE_UNAVAILABLE",
        }


def _cancel_guidance_for_invite(
    *,
    invite_id: str,
    owner_user_id: str,
) -> None:
    """Best-effort opt-out propagation for the optional guidance feature."""

    if not str(invite_id or "").strip() or not str(owner_user_id or "").strip():
        return
    try:
        from company_kakao_guidance import (  # noqa: PLC0415
            cancel_guidance_for_invite,
        )

        cancel_guidance_for_invite(
            invite_id=str(invite_id).strip(),
            owner_user_id=str(owner_user_id).strip().lower(),
            opt_out=True,
            reason="customer_opt_out",
        )
    except Exception:
        return


class ClaimRemoteServiceError(RuntimeError):
    """A redacted remote-flow error safe to render in the customer UI."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = "REMOTE_CLAIM_ERROR",
    ):
        super().__init__(str(message or "요청을 처리하지 못했습니다."))
        self.error_code = _safe_error_code(error_code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_error_code(value: Any, fallback: str = "REMOTE_CLAIM_ERROR") -> str:
    raw = re.sub(r"[^A-Z0-9_-]", "_", str(value or "").strip().upper())
    raw = re.sub(r"_+", "_", raw).strip("_")[:80]
    return raw if _SAFE_ERROR_CODE_PATTERN.fullmatch(raw) else fallback


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _clean_name(value: Any) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or "")).strip()
    name = re.sub(r"\s+", " ", name)
    if not 2 <= len(name) <= 50:
        raise ClaimRemoteServiceError(
            "이름을 정확히 입력해 주세요.",
            error_code="INVALID_CUSTOMER_NAME",
        )
    return name


def _clean_phone(value: Any) -> str:
    phone = _digits(value)
    if not _PHONE_PATTERN.fullmatch(phone):
        raise ClaimRemoteServiceError(
            "휴대전화번호를 정확히 입력해 주세요.",
            error_code="INVALID_CUSTOMER_PHONE",
        )
    return phone


def _clean_optional_company_name(value: Any) -> str:
    selected = re.sub(
        r"[\x00-\x1f\x7f]+",
        " ",
        str(value or ""),
    ).strip()
    selected = re.sub(r"\s+", " ", selected)
    if len(selected) > 120:
        raise ClaimRemoteServiceError(
            "상호명은 120자 이내로 입력해 주세요.",
            error_code="INVALID_COMPANY_NAME",
        )
    return selected


def _clean_optional_business_no(value: Any) -> str:
    selected = _digits(value)
    if selected and len(selected) != 10:
        raise ClaimRemoteServiceError(
            "사업자등록번호는 숫자 10자리로 입력해 주세요.",
            error_code="INVALID_BUSINESS_NUMBER",
        )
    return selected


def _clean_required_reference(
    value: Any,
    *,
    label: str,
    error_code: str,
    maximum: int = 200,
) -> str:
    selected = re.sub(
        r"[\x00-\x1f\x7f]+",
        " ",
        str(value or ""),
    ).strip()
    selected = re.sub(r"\s+", " ", selected)
    if not selected or len(selected) > maximum:
        raise ClaimRemoteServiceError(
            f"{label} 정보를 확인해 주세요.",
            error_code=error_code,
        )
    return selected


def _clean_identity(value: Any) -> str:
    identity = _digits(value)
    if len(identity) != 13:
        raise ClaimRemoteServiceError(
            "주민등록번호 13자리를 정확히 입력해 주세요.",
            error_code="INVALID_IDENTITY_NUMBER",
        )
    _birth_date(identity)
    return identity


def _birth_date(identity_number: str) -> str:
    identity = _digits(identity_number)
    if len(identity) != 13:
        raise ClaimRemoteServiceError(
            "주민등록번호를 확인해 주세요.",
            error_code="INVALID_IDENTITY_NUMBER",
        )
    century_by_code = {
        "9": "18",
        "0": "18",
        "1": "19",
        "2": "19",
        "5": "19",
        "6": "19",
        "3": "20",
        "4": "20",
        "7": "20",
        "8": "20",
    }
    century = century_by_code.get(identity[6], "")
    if not century:
        raise ClaimRemoteServiceError(
            "주민등록번호를 확인해 주세요.",
            error_code="INVALID_IDENTITY_NUMBER",
        )
    selected = f"{century}{identity[:6]}"
    try:
        datetime.strptime(selected, "%Y%m%d")
    except ValueError as exc:
        raise ClaimRemoteServiceError(
            "주민등록번호의 생년월일을 확인해 주세요.",
            error_code="INVALID_IDENTITY_NUMBER",
        ) from exc
    return selected


def _masked_name(name: str) -> str:
    selected = str(name or "").strip()
    if len(selected) <= 1:
        return "*"
    return selected[0] + ("*" * max(1, len(selected) - 1))


def _masked_phone(phone: str) -> str:
    digits = _digits(phone)
    if len(digits) < 8:
        return "***"
    return f"{digits[:3]}-****-{digits[-4:]}"


def _consent_hash(
    consent_version: str,
    consents: Mapping[str, bool],
) -> str:
    payload = json.dumps(
        {
            "consent_version": str(consent_version or ""),
            "privacy_and_unique_identifier": bool(
                consents.get("privacy_and_unique_identifier")
            ),
            "third_party_processing": bool(
                consents.get("third_party_processing")
            ),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _public_base_url(value: str | None = None) -> str:
    selected = str(
        value
        or os.environ.get("CLAIM_PUBLIC_BASE_URL", "")
        or ""
    ).strip().rstrip("/")
    if not selected.startswith("https://"):
        raise ClaimRemoteServiceError(
            "고객 인증 주소 설정이 필요합니다.",
            error_code="PUBLIC_BASE_URL_REQUIRED",
        )
    return selected


def _template_auth_link(value: Any) -> str:
    """Return the value expected by templates that already contain HTTPS."""

    selected = str(value or "").strip()
    if selected.startswith("https://"):
        return selected[len("https://") :]
    return selected


def remote_invite_environment_readiness() -> dict[str, Any]:
    """Check remote-invite dependencies without returning secret values."""

    public_url_ready = str(
        os.environ.get("CLAIM_PUBLIC_BASE_URL", "") or ""
    ).strip().startswith("https://")
    crypto_ready, _crypto_message = remote_claim_crypto_readiness()
    variant_key_ready = len(
        str(
            os.environ.get("CLAIM_DOCUMENT_VARIANT_KEY", "")
            or os.environ.get("CLAIM_JOB_ENCRYPTION_KEY", "")
            or ""
        ).strip()
    ) >= 32
    solapi = solapi_environment_readiness(
        required_template_env_names=tuple(TEMPLATE_ENV_BY_CODE.values()),
    )
    missing_components: list[str] = []
    if not public_url_ready:
        missing_components.append("public_url")
    if not crypto_ready:
        missing_components.append("crypto")
    if not variant_key_ready:
        missing_components.append("variant_key")
    if not bool(solapi.get("ready")):
        missing_components.append("solapi")
    return {
        "ready": not missing_components,
        "public_url_ready": public_url_ready,
        "crypto_ready": crypto_ready,
        "variant_key_ready": variant_key_ready,
        "solapi_ready": bool(solapi.get("ready")),
        "missing_components": missing_components,
        "missing_env_names": list(solapi.get("missing_env_names") or []),
    }


def _load_claim_workflow() -> tuple[Callable[..., Any], Callable[..., Any]]:
    # Deferred import prevents the Streamlit module from being imported by
    # lightweight gateway tests and keeps this module free of UI state.
    from claim_correction_center import (  # noqa: PLC0415
        _advance_personal_case,
        _claim_collection_progress_from_repository,
    )

    return (
        _advance_personal_case,
        _claim_collection_progress_from_repository,
    )


_JOB_ACTIVE_GUARD_ERROR_CODES = frozenset(
    {
        "REMOTE_JOB_NO_LONGER_ACTIVE",
        "REMOTE_JOB_ACTIVE_CHECK_UNAVAILABLE",
    }
)


class _JobActiveProviderProxy:
    """Revalidate durable job state at the instant a provider method runs."""

    def __init__(
        self,
        delegate: Any,
        assert_active: Callable[[], bool],
    ) -> None:
        self._delegate = delegate
        self._assert_active = assert_active

    def __getattr__(self, name: str) -> Any:
        selected = getattr(self._delegate, name)
        if not callable(selected):
            return selected

        def guarded(*args: Any, **kwargs: Any) -> Any:
            self._assert_active()
            return selected(*args, **kwargs)

        return guarded


class ClaimRemoteService:
    """Durable public claim orchestration plus restart-safe lease workers."""

    def __init__(
        self,
        *,
        remote_repository_factory: Callable[[str], Any] = ClaimRemoteRepository,
        claim_repository_factory: Callable[[str], Any] = ClaimRepository,
        tilko_client_factory: Callable[[], Any] = TilkoClaimClient,
        solapi_client_factory: Callable[[], Any] | None = None,
        advance_case: Callable[..., Mapping[str, Any]] | None = None,
        progress_reader: Callable[..., tuple[int, str, int, int, bool]]
        | None = None,
        public_base_url: str | None = None,
        worker_owner: str | None = None,
        worker_id: str | None = None,
        poll_seconds: float = 2.0,
        lease_seconds: int = 600,
        reminder_delay_seconds: int = 15 * 60,
        clock: Callable[[], datetime] = _utc_now,
        monotonic: Callable[[], float] = time.time,
        invite_readiness_checker: Callable[[], Mapping[str, Any]]
        | None = None,
        start_worker: bool = False,
    ):
        if advance_case is None or progress_reader is None:
            default_advance, default_progress = _load_claim_workflow()
            advance_case = advance_case or default_advance
            progress_reader = progress_reader or default_progress
        self.remote_repository_factory = remote_repository_factory
        self.claim_repository_factory = claim_repository_factory
        self.tilko_client_factory = tilko_client_factory
        self.solapi_client_factory = (
            solapi_client_factory or self._default_solapi_client
        )
        self.advance_case = advance_case
        self.progress_reader = progress_reader
        self.base_url = _public_base_url(public_base_url)
        self.worker_owner = str(
            worker_owner
            or os.environ.get(
                "CLAIM_REMOTE_WORKER_OWNER",
                _DEFAULT_WORKER_OWNER,
            )
            or _DEFAULT_WORKER_OWNER
        ).strip().lower()
        self.worker_id = str(
            worker_id
            or os.environ.get("CLAIM_REMOTE_WORKER_ID", "")
            or f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
        ).strip()[:120]
        self.poll_seconds = max(0.2, min(float(poll_seconds), 30.0))
        self.lease_seconds = max(15, min(int(lease_seconds), 600))
        self.reminder_delay_seconds = max(
            60,
            min(int(reminder_delay_seconds), 23 * 60 * 60),
        )
        self.clock = clock
        self.monotonic = monotonic
        self.invite_readiness_checker = (
            invite_readiness_checker or remote_invite_environment_readiness
        )
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._manager_repository = self.remote_repository_factory(
            self.worker_owner
        )
        if start_worker:
            self.start()

    @staticmethod
    def _default_solapi_client() -> SolapiAlimtalkClient:
        return SolapiAlimtalkClient(SolapiAlimtalkConfig.from_env())

    def create_staff_invite(
        self,
        *,
        owner_user_id: str,
        requested_by: str,
        customer_name: str,
        customer_phone: str,
    ) -> dict[str, Any]:
        readiness = dict(self.invite_readiness_checker() or {})
        if not bool(readiness.get("ready")):
            raise ClaimRemoteServiceError(
                "카카오톡 원격 인증 발송 설정이 완료되지 않았습니다. "
                "관리자에게 문의해 주세요.",
                error_code="REMOTE_INVITE_NOT_READY",
            )
        owner = str(owner_user_id or "").strip().lower()
        name = _clean_name(customer_name)
        phone = _clean_phone(customer_phone)
        repository = self.remote_repository_factory(owner)
        invite = repository.create_invite(
            secure_payload={
                "recipient_name": name,
                "recipient_phone": phone,
                "requested_by": str(requested_by or owner).strip()[:120],
            },
            recipient_name_masked=_masked_name(name),
            recipient_phone_masked=_masked_phone(phone),
        )
        invite_id = str(invite.record.get("id") or "")
        invite_url = f"{self.base_url}/c/{invite.token}"
        common_payload = {
            "to": phone,
            "variables": {
                "#{고객명}": name,
                "#{인증링크}": _template_auth_link(invite_url),
            },
        }
        repository.enqueue_message(
            idempotency_key=f"invite:{invite_id}:auth-start",
            event_type="AUTH_START",
            template_code=TEMPLATE_AUTH_START,
            secure_payload=common_payload,
            invite_id=invite_id,
        )
        repository.enqueue_message(
            idempotency_key=f"invite:{invite_id}:auth-resume",
            event_type="AUTH_RESUME",
            template_code=TEMPLATE_AUTH_RESUME,
            secure_payload=common_payload,
            invite_id=invite_id,
            run_after=self.clock()
            + timedelta(seconds=self.reminder_delay_seconds),
        )
        return {
            "invite_id": invite_id,
            "status": str(invite.record.get("status") or "created"),
            "expires_at": invite.record.get("expires_at"),
            "message_queued": True,
        }

    def create_prospect_self_input_invite(
        self,
        *,
        owner_user_id: str,
        requested_by: str,
        company_uid: str,
        guidance_type: str,
        guidance_message_id: str,
    ) -> dict[str, Any]:
        """Create a seven-day customer self-input link without sending it.

        Notification delivery is owned by the DB-discovery guidance service.
        This method therefore persists no recipient PII and does not enqueue a
        claim AlimTalk.  The returned opaque URL may be placed in an approved
        guidance template by the caller.
        """

        readiness = dict(self.invite_readiness_checker() or {})
        missing = {
            str(item or "").strip().lower()
            for item in (readiness.get("missing_components") or [])
            if str(item or "").strip()
        }
        storage_ready = bool(readiness.get("ready")) or (
            bool(missing) and missing <= {"solapi"}
        )
        if not storage_ready:
            raise ClaimRemoteServiceError(
                "고객 검토신청 링크 생성 설정이 완료되지 않았습니다. 관리자에게 문의해 주세요.",
                error_code="REMOTE_INVITE_NOT_READY",
            )

        owner = _clean_required_reference(
            str(owner_user_id or "").strip().lower(),
            label="담당자",
            error_code="REMOTE_OWNER_REQUIRED",
        ).lower()
        requester = _clean_required_reference(
            requested_by or owner,
            label="요청자",
            error_code="REMOTE_REQUESTER_REQUIRED",
            maximum=120,
        )
        selected_company_uid = _clean_required_reference(
            company_uid,
            label="업체 식별자",
            error_code="REMOTE_COMPANY_UID_REQUIRED",
            maximum=200,
        )
        selected_guidance_type = _clean_required_reference(
            guidance_type,
            label="안내 유형",
            error_code="REMOTE_GUIDANCE_TYPE_REQUIRED",
            maximum=80,
        )
        # The guidance service owns this identifier and persists invite_id on
        # its reserved row.  Validate the hand-off contract, but deliberately
        # keep the identifier out of the encrypted claim payload.
        _clean_required_reference(
            guidance_message_id,
            label="안내 메시지",
            error_code="REMOTE_GUIDANCE_MESSAGE_REQUIRED",
            maximum=100,
        )
        expires_at = self.clock() + timedelta(
            seconds=PROSPECT_INVITE_TTL_SECONDS
        )
        repository = self.remote_repository_factory(owner)
        invite = repository.create_invite(
            secure_payload={
                "requested_by": requester,
                "company_uid": selected_company_uid,
                "guidance_type": selected_guidance_type,
                "flow_type": PROSPECT_SELF_INPUT_FLOW,
                "customer_self_input": True,
                "enforce_recipient_match": False,
            },
            expires_at=expires_at,
        )
        invite_id = str(invite.record.get("id") or "")
        return {
            "invite_id": invite_id,
            "invite_url": f"{self.base_url}/c/{invite.token}",
            "status": str(invite.record.get("status") or "created"),
            "expires_at": invite.record.get("expires_at") or expires_at,
        }

    def open_invite(self, invite_token: str) -> Mapping[str, Any]:
        record = dict(
            ClaimRemoteRepository.mark_invite_opened_global(
                invite_token,
                database=getattr(
                    self._manager_repository,
                    "database",
                    None,
                ),
                crypto=getattr(
                    self._manager_repository,
                    "crypto",
                    None,
                ),
            )
            or {}
        )
        owner = str(record.get("owner_user_id") or "").strip().lower()
        repository = self.remote_repository_factory(owner)
        owner_record = repository.get_invite(invite_token) or {}
        ciphertext = str(
            owner_record.get("secure_payload_ciphertext") or ""
        )
        secure_payload = (
            repository.decrypt_payload(ciphertext) if ciphertext else {}
        )
        flow_type = str(
            secure_payload.get("flow_type") or STAFF_TARGETED_FLOW
        ).strip().lower()
        self_input = flow_type == PROSPECT_SELF_INPUT_FLOW
        return {
            "invite_id": str(record.get("id") or ""),
            "owner_ref": owner,
            "status": str(record.get("status") or "opened"),
            "expires_at": record.get("expires_at"),
            "name": (
                ""
                if self_input
                else str(secure_payload.get("recipient_name") or "")
            ),
            "phone": (
                ""
                if self_input
                else str(secure_payload.get("recipient_phone") or "")
            ),
            "flow_type": flow_type,
            "customer_self_input": self_input,
        }

    def submit_customer(
        self,
        *,
        owner_ref: str,
        invite_id: str,
        invite_token: str,
        name: str,
        phone: str,
        resident_number: str,
        consent_version: str,
        consents: Mapping[str, bool],
        company_name: str = "",
        business_no: str = "",
    ) -> Mapping[str, Any]:
        owner = str(owner_ref or "").strip().lower()
        customer_name = _clean_name(name)
        customer_phone = _clean_phone(phone)
        identity_number = _clean_identity(resident_number)
        selected_company_name = _clean_optional_company_name(company_name)
        selected_business_no = _clean_optional_business_no(business_no)
        if not (
            bool(consents.get("privacy_and_unique_identifier"))
            and bool(consents.get("third_party_processing"))
        ):
            raise ClaimRemoteServiceError(
                "필수 동의 내용을 모두 확인해 주세요.",
                error_code="CONSENT_REQUIRED",
            )

        remote_repository = self.remote_repository_factory(owner)
        invite = remote_repository.get_invite(invite_token)
        if not invite or str(invite.get("id") or "") != str(invite_id):
            raise ClaimRemoteServiceError(
                "인증 요청을 확인하지 못했습니다.",
                error_code="INVITE_NOT_FOUND",
            )
        invite_status = str(invite.get("status") or "").strip().lower()
        if invite_status == "submitted" or invite.get("consumed_at"):
            return self.get_status(
                owner_ref=owner,
                invite_id=invite_id,
                invite_token=invite_token,
            )
        invite_payload = remote_repository.decrypt_payload(
            invite.get("secure_payload_ciphertext")
        )
        flow_type = str(
            invite_payload.get("flow_type") or STAFF_TARGETED_FLOW
        ).strip().lower()
        # DB-discovery guidance may be delivered to a public business phone,
        # while Tilko simple authentication must use the representative's
        # own certificate phone entered on the customer page.  Prospect
        # self-input links therefore never compare those two identities,
        # even if a legacy/malformed payload contains recipient fields or an
        # old recipient-match flag.
        enforce_target_match = (
            flow_type != PROSPECT_SELF_INPUT_FLOW
            and bool(invite_payload.get("enforce_recipient_match", True))
        )
        if enforce_target_match:
            try:
                expected_name = _clean_name(
                    invite_payload.get("recipient_name")
                )
                expected_phone = _clean_phone(
                    invite_payload.get("recipient_phone")
                )
            except ClaimRemoteServiceError as exc:
                raise ClaimRemoteServiceError(
                    "인증 요청의 고객정보를 확인하지 못했습니다. "
                    "담당자에게 새 인증 요청을 받아 주세요.",
                    error_code="INVITE_TARGET_INVALID",
                ) from exc
            if (
                customer_name != expected_name
                or customer_phone != expected_phone
            ):
                raise ClaimRemoteServiceError(
                    "인증 요청 대상자 정보와 입력한 정보가 일치하지 않습니다. "
                    "담당자에게 확인해 주세요.",
                    error_code="INVITE_TARGET_MISMATCH",
                )

        now = self.clock()
        monotonic_now = self.monotonic()
        case_id = str(uuid.uuid4())
        transient = {
            "request_started_at": monotonic_now,
            "absolute_expires_at": monotonic_now
            + REMOTE_JOB_TTL_SECONDS,
            "expires_at": monotonic_now + REMOTE_AUTH_TTL_SECONDS,
            # This flag is non-PII and lets the worker enforce the shorter
            # retention rules for a number entered solely for simple auth.
            "flow_type": flow_type,
            "stage_started_at": monotonic_now,
            "expected_sources": ["hometax", "comwel"],
            "business_number": "",
            "invite_url": f"{self.base_url}/c/{invite_token}",
            "auth_context": {
                "representative": customer_name,
                "cellphone": customer_phone,
                "birth_date": _birth_date(identity_number),
                "identity_number": identity_number,
            },
        }
        try:
            job = remote_repository.consume_invite(
                invite_token,
                case_id=case_id,
                secure_job_payload=transient,
                hard_expires_at=now
                + timedelta(seconds=REMOTE_JOB_TTL_SECONDS),
                # Representative-entered authentication data is encrypted in
                # the job only for the active auth/collection stage.  The
                # database expires it independently if the worker stops.
                sensitive_expires_at=now
                + timedelta(seconds=REMOTE_AUTH_TTL_SECONDS),
                stage="submission_reserved",
                max_attempts=100,
                initial_status="waiting",
                next_run_at=now
                + timedelta(
                    seconds=REMOTE_SUBMISSION_RESERVATION_SECONDS
                ),
            )
        except ClaimRemoteRepositoryError as exc:
            if exc.error_code == "REMOTE_INVITE_ALREADY_CONSUMED":
                return self.get_status(
                    owner_ref=owner,
                    invite_id=invite_id,
                    invite_token=invite_token,
                )
            raise

        job_id = str(job.get("id") or "")
        if (
            not job_id
            or str(job.get("status") or "").strip().lower() != "waiting"
            or str(job.get("stage") or "").strip().lower()
            != "submission_reserved"
        ):
            raise ClaimRemoteServiceError(
                "인증 요청 보안 준비가 완료되지 않았습니다. "
                "관리자에게 문의해 주세요.",
                error_code="REMOTE_JOB_RESERVATION_NOT_SUPPORTED",
            )

        claim_repository = self.claim_repository_factory(owner)
        try:
            case = claim_repository.create_case(
                case_id=case_id,
                company_name=selected_company_name or "상호명 미입력",
                business_no=selected_business_no,
                business_type="individual",
                representative_name=customer_name,
                # In the DB-discovery self-input flow this number exists only
                # inside the encrypted, expiring auth job.  It must not be
                # copied into the durable case fingerprint or masked columns.
                cellphone=(
                    ""
                    if flow_type == PROSPECT_SELF_INPUT_FLOW
                    else customer_phone
                ),
                requested_by=str(
                    invite_payload.get("requested_by") or owner
                )[:120],
                selected_sources=["hometax", "comwel"],
                consent_version=str(
                    consent_version or REMOTE_CONSENT_VERSION
                )[:120],
                consent_text_sha256=_consent_hash(
                    consent_version,
                    consents,
                ),
                consent_channel="customer_public_page",
                retention_policy_version=REMOTE_RETENTION_POLICY_VERSION,
                collection_authority_confirmed=True,
            )
            case_id = str(case.get("id") or case_id)
        except Exception as exc:
            self._fail_submission_reservation(
                remote_repository,
                job_id,
                case_id,
                "REMOTE_CASE_CREATE_FAILED",
            )
            raise ClaimRemoteServiceError(
                "인증 요청을 안전하게 저장하지 못했습니다. "
                "담당자에게 새 인증 요청을 받아 주세요.",
                error_code="REMOTE_CASE_CREATE_FAILED",
            ) from exc

        def assert_submission_active() -> bool:
            return self._assert_job_active(
                remote_repository,
                job_id,
                mode="submission_reserved",
            )

        client = _JobActiveProviderProxy(
            self.tilko_client_factory(),
            assert_submission_active,
        )
        birth_date = str(
            transient["auth_context"].get("birth_date") or ""
        )
        try:
            hometax_session = client.request_hometax_kakao(
                birth_date=birth_date,
                user_name=customer_name,
                cellphone=customer_phone,
            )
        except Exception as exc:
            if _safe_error_code(
                getattr(exc, "error_code", ""),
                "HOMETAX_AUTH_REQUEST_FAILED",
            ) in _JOB_ACTIVE_GUARD_ERROR_CODES:
                # Cancellation/expiry has already terminalized and scrubbed
                # the reservation. An unavailable guard must also fail closed
                # without rewriting or re-encrypting that durable state.
                raise
            self._fail_submission_reservation(
                remote_repository,
                job_id,
                case_id,
                "HOMETAX_AUTH_REQUEST_FAILED",
            )
            self._mark_case_failed(
                claim_repository,
                case_id,
                "HOMETAX_AUTH_REQUEST_FAILED",
            )
            if isinstance(exc, ClaimRemoteServiceError):
                raise
            raise ClaimRemoteServiceError(
                "국세청 홈택스 인증 요청을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.",
                error_code="HOMETAX_AUTH_REQUEST_FAILED",
            ) from exc

        transient["hometax"] = dict(hometax_session or {})
        try:
            job = remote_repository.activate_reserved_job(
                job_id,
                case_id=case_id,
                secure_payload=transient,
                stage="hometax_pending",
            )
        except Exception as exc:
            reservation_failed = self._fail_submission_reservation(
                remote_repository,
                job_id,
                case_id,
                "REMOTE_JOB_ACTIVATE_FAILED",
            )
            if reservation_failed:
                self._mark_case_failed(
                    claim_repository,
                    case_id,
                    "REMOTE_JOB_ACTIVATE_FAILED",
                )
            raise ClaimRemoteServiceError(
                "인증 요청의 후속 작업을 시작하지 못했습니다. "
                "담당자에게 문의해 주세요.",
                error_code="REMOTE_JOB_ACTIVATE_FAILED",
            ) from exc

        claim_repository.update_case_status(
            case_id,
            hometax_status="auth_requested",
            comwel_status="request_ready",
            overall_status="auth_pending",
            auth_requested_at=now.isoformat(),
            last_safe_error_code=None,
        )
        claim_repository.append_audit_event(
            case_id=case_id,
            action="auth_request",
            source="hometax",
            outcome="success",
            metadata={
                "consent_channel": "customer_public_page",
                "durable_worker": True,
            },
        )
        return {
            "invite_id": invite_id,
            "case_id": case_id,
            "status": str(job.get("status") or "queued"),
            "stage": str(job.get("stage") or "hometax_pending"),
            "progress": 0,
            "submitted": True,
        }

    def cancel_customer(
        self,
        *,
        owner_ref: str,
        invite_id: str,
        invite_token: str,
        reason: str = "customer_opt_out",
    ) -> Mapping[str, Any]:
        """Cancel the public invite and any pending auth/collection work."""

        owner = str(owner_ref or "").strip().lower()
        repository = self.remote_repository_factory(owner)
        invite = repository.get_invite(invite_token)
        if not invite or str(invite.get("id") or "") != str(invite_id):
            raise ClaimRemoteServiceError(
                "인증 요청을 확인하지 못했습니다.",
                error_code="INVITE_NOT_FOUND",
            )
        selected_reason = str(reason or "customer_opt_out").strip()[:120]
        cancelled = dict(
            repository.cancel_invite(
                invite_token,
                reason=selected_reason or "customer_opt_out",
            )
            or {}
        )
        _cancel_guidance_for_invite(
            invite_id=str(invite_id),
            owner_user_id=owner,
        )
        return {
            "invite_id": str(cancelled.get("id") or invite_id),
            "case_id": str(cancelled.get("case_id") or ""),
            "status": "cancelled",
            "stage": "customer_opt_out",
            "progress": int(cancelled.get("progress") or 0),
            "safe_message": "검토신청 안내가 취소되었습니다.",
            "submitted": True,
            "complete": True,
        }

    def get_status(
        self,
        *,
        owner_ref: str,
        invite_id: str,
        invite_token: str,
    ) -> Mapping[str, Any]:
        del invite_token
        repository = self.remote_repository_factory(
            str(owner_ref or "").strip().lower()
        )
        row = dict(repository.get_session_status(invite_id) or {})
        job_status = str(row.get("job_status") or "").lower()
        invite_status = str(row.get("invite_status") or "opened").lower()
        return {
            "invite_id": str(row.get("invite_id") or invite_id),
            "case_id": str(row.get("case_id") or ""),
            "status": job_status or invite_status,
            "stage": str(row.get("job_stage") or ""),
            "progress": int(row.get("progress") or 0),
            "safe_message": str(row.get("safe_message") or ""),
            "submitted": bool(job_status)
            or invite_status in {"submitted", "cancelled"},
            "complete": job_status in {"complete", "partial", "cancelled"}
            or invite_status == "cancelled",
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="claim-remote-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.0, float(timeout)))

    def run_once(self) -> dict[str, int]:
        counts = {"jobs": 0, "messages": 0}
        self._manager_repository.expire_due()
        for row in self._manager_repository.lease_jobs(
            self.worker_id,
            limit=4,
            lease_seconds=self.lease_seconds,
        ):
            counts["jobs"] += 1
            self._process_job(dict(row))
        for row in self._manager_repository.lease_messages(
            self.worker_id,
            limit=20,
            lease_seconds=self.lease_seconds,
        ):
            counts["messages"] += 1
            self._process_outbox(dict(row))
        return counts

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                counts = self.run_once()
                busy = bool(counts["jobs"] or counts["messages"])
            except Exception:
                # Never log provider errors or payloads. A later lease pass
                # safely resumes abandoned work after the lease expires.
                busy = False
            self._stop_event.wait(0.2 if busy else self.poll_seconds)

    def _progress(
        self,
        repository: Any,
        case_id: str,
    ) -> tuple[int, int, int, bool]:
        result = self.progress_reader(repository, case_id)
        percentage, _text, ready, target, verified = result
        return (
            max(0, min(int(percentage or 0), 100)),
            max(0, int(ready or 0)),
            max(0, int(target or 0)),
            bool(verified),
        )

    def _sensitive_payload_expires_at(
        self,
        transient: Mapping[str, Any],
    ) -> datetime:
        """Convert the active monotonic workflow TTL to a database deadline."""

        remaining = float(transient.get("expires_at") or 0) - self.monotonic()
        remaining = max(1.0, min(remaining, float(REMOTE_JOB_TTL_SECONDS)))
        return self.clock() + timedelta(seconds=remaining)

    def _assert_job_active(
        self,
        repository: Any,
        job_id: str,
        *,
        mode: str,
    ) -> bool:
        """Fail closed unless the same durable job may call a provider now."""

        try:
            state = repository.check_job_active(
                job_id,
                mode=mode,
                worker_id=(self.worker_id if mode == "leased" else None),
            )
        except Exception as exc:
            raise ClaimRemoteServiceError(
                "작업 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                error_code="REMOTE_JOB_ACTIVE_CHECK_UNAVAILABLE",
            ) from exc
        if not isinstance(state, Mapping) or state.get("allowed") is not True:
            raise ClaimRemoteServiceError(
                "작업이 취소되었거나 인증 유효시간이 지났습니다.",
                error_code="REMOTE_JOB_NO_LONGER_ACTIVE",
            )
        try:
            expected_job_id = str(uuid.UUID(str(job_id or "").strip()))
            returned_job_id = str(
                uuid.UUID(str(state.get("job_id") or "").strip())
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ClaimRemoteServiceError(
                "작업 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                error_code="REMOTE_JOB_ACTIVE_CHECK_UNAVAILABLE",
            ) from exc
        if returned_job_id != expected_job_id:
            raise ClaimRemoteServiceError(
                "작업 상태를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.",
                error_code="REMOTE_JOB_ACTIVE_CHECK_UNAVAILABLE",
            )
        return True

    def _process_job(self, row: dict[str, Any]) -> None:
        owner = str(row.get("owner_user_id") or "").strip().lower()
        job_id = str(row.get("id") or "")
        invite_id = str(row.get("invite_id") or "")
        case_id = str(row.get("case_id") or "")
        repository = self.remote_repository_factory(owner)
        claim_repository = self.claim_repository_factory(owner)
        try:
            transient = repository.decrypt_payload(
                row.get("secure_payload_ciphertext")
            )
            self._assert_job_active(
                repository,
                job_id,
                mode="leased",
            )
            if str(row.get("stage") or "") == "submission_reserved":
                repository.release_job(
                    job_id,
                    self.worker_id,
                    next_status="failed",
                    stage="submission_failed",
                    progress=0,
                    safe_message=(
                        "인증 요청 준비가 완료되지 않았습니다. "
                        "담당자에게 새 인증 요청을 받아 주세요."
                    ),
                    safe_error_code="REMOTE_SUBMISSION_INCOMPLETE",
                )
                self._mark_case_failed(
                    claim_repository,
                    case_id,
                    "REMOTE_SUBMISSION_INCOMPLETE",
                )
                return
            if float(transient.get("expires_at") or 0) <= self.monotonic():
                raise ClaimProviderError(
                    "인증 유효시간이 지났습니다.",
                    error_code="AUTH_SESSION_EXPIRED",
                )
            context = transient.get("auth_context")
            if not isinstance(context, dict):
                raise ClaimProviderError(
                    "인증정보를 복구하지 못했습니다.",
                    error_code="AUTH_CONTEXT_MISSING",
                )
            case = claim_repository.get_case(case_id)
            if not case:
                raise ClaimRepositoryError("경정청구 요청을 찾지 못했습니다.")
            def assert_job_active() -> bool:
                return self._assert_job_active(
                    repository,
                    job_id,
                    mode="leased",
                )

            client = _JobActiveProviderProxy(
                self.tilko_client_factory(),
                assert_job_active,
            )
            last_heartbeat = [self.monotonic()]

            def on_progress(
                _processed: int,
                _total: int,
                _document_code: str,
            ) -> None:
                assert_job_active()
                entering_collection = (
                    transient.get("retention_stage") != "collection"
                )
                if entering_collection:
                    # This callback begins only after both simple-auth steps
                    # have succeeded.  Mark the encrypted context as an
                    # active collection payload before the first potentially
                    # long Tilko document call, so the database can apply the
                    # collection deadline instead of the ten-minute auth cap.
                    transient["retention_stage"] = "collection"
                if (
                    entering_collection
                    or self.monotonic() - last_heartbeat[0] >= 10
                ):
                    repository.heartbeat_job(
                        job_id,
                        self.worker_id,
                        lease_seconds=self.lease_seconds,
                        sensitive_expires_at=(
                            self._sensitive_payload_expires_at(transient)
                        ),
                        stage="collecting",
                    )
                    last_heartbeat[0] = self.monotonic()

            def should_continue() -> bool:
                assert_job_active()
                return float(
                    transient.get("absolute_expires_at") or 0
                ) > self.monotonic()

            assert_job_active()
            result = dict(
                self.advance_case(
                    claim_repository,
                    client,
                    case=case,
                    transient=transient,
                    representative=str(
                        context.get("representative") or ""
                    ).strip(),
                    cellphone=_digits(context.get("cellphone")),
                    birth_date=_digits(context.get("birth_date")),
                    identity_number=_digits(
                        context.get("identity_number")
                    ),
                    on_progress=on_progress,
                    should_continue=should_continue,
                )
                or {}
            )
            event = str(result.get("event") or "idle")
            progress, ready, target, verified = self._progress(
                claim_repository,
                case_id,
            )
            if event in {"hometax_pending", "comwel_pending"}:
                repository.release_job(
                    job_id,
                    self.worker_id,
                    next_status="waiting",
                    stage=event,
                    secure_payload=transient,
                    progress=progress,
                    next_run_at=self.clock()
                    + timedelta(seconds=self._auth_poll_delay(row)),
                    safe_message=self._auth_wait_message(event),
                    sensitive_expires_at=(
                        self._sensitive_payload_expires_at(transient)
                    ),
                )
                return
            if event == "comwel_requested":
                self._enqueue_case_message(
                    repository,
                    row=row,
                    transient=transient,
                    template_code=TEMPLATE_NEXT_AUTH,
                    event_type="NEXT_AUTH",
                )
                repository.release_job(
                    job_id,
                    self.worker_id,
                    next_status="waiting",
                    stage="comwel_pending",
                    secure_payload=transient,
                    progress=progress,
                    next_run_at=self.clock() + timedelta(seconds=3),
                    sensitive_expires_at=(
                        self._sensitive_payload_expires_at(transient)
                    ),
                    safe_message=(
                        "홈택스 인증이 완료되어 근로복지공단 인증을 발송했습니다."
                    ),
                )
                return
            if event == "collection_complete":
                terminal = (
                    "complete"
                    if verified and target > 0 and ready == target
                    else "partial"
                )
                message = (
                    f"자료 {ready}건 수집이 완료되었습니다."
                    if terminal == "complete"
                    else f"자료 {ready}/{target}건을 수집했습니다. 일부 자료는 확인이 필요합니다."
                )
                self._enqueue_case_message(
                    repository,
                    row=row,
                    transient=transient,
                    template_code=(
                        TEMPLATE_COMPLETE
                        if terminal == "complete"
                        else TEMPLATE_FAILED
                    ),
                    event_type=(
                        "COLLECTION_COMPLETE"
                        if terminal == "complete"
                        else "COLLECTION_PARTIAL"
                    ),
                )
                repository.release_job(
                    job_id,
                    self.worker_id,
                    next_status=terminal,
                    stage="collection_complete",
                    progress=progress,
                    safe_message=message,
                    safe_error_code=(
                        ""
                        if terminal == "complete"
                        else "COLLECTION_PARTIAL"
                    ),
                )
                return
            if event in {
                "collection_partial",
                "business_selection_required",
                "management_selection_required",
            }:
                code = {
                    "business_selection_required": "BUSINESS_SELECTION_REQUIRED",
                    "management_selection_required": (
                        "MANAGEMENT_SELECTION_REQUIRED"
                    ),
                }.get(event, "COLLECTION_PARTIAL")
                self._enqueue_case_message(
                    repository,
                    row=row,
                    transient=transient,
                    template_code=TEMPLATE_FAILED,
                    event_type="COLLECTION_PARTIAL",
                )
                repository.release_job(
                    job_id,
                    self.worker_id,
                    next_status="partial",
                    stage=event,
                    progress=progress,
                    safe_message=(
                        f"자료 {ready}/{target}건을 수집했습니다. 담당자 확인이 필요합니다."
                    ),
                    safe_error_code=code,
                )
                return
            terminal = (
                "complete"
                if verified and target > 0 and ready == target
                else "partial"
            )
            repository.release_job(
                job_id,
                self.worker_id,
                next_status=terminal,
                stage="collection_checked",
                progress=progress,
                safe_message=f"자료 {ready}/{target}건을 확인했습니다.",
                safe_error_code=(
                    "" if terminal == "complete" else "COLLECTION_PARTIAL"
                ),
            )
        except Exception as exc:
            self._handle_job_error(
                row=row,
                repository=repository,
                claim_repository=claim_repository,
                transient=locals().get("transient"),
                error=exc,
                invite_id=invite_id,
                case_id=case_id,
            )

    def _handle_job_error(
        self,
        *,
        row: Mapping[str, Any],
        repository: Any,
        claim_repository: Any,
        transient: Any,
        error: Exception,
        invite_id: str,
        case_id: str,
    ) -> None:
        job_id = str(row.get("id") or "")
        provider_code = _safe_error_code(
            getattr(error, "error_code", ""),
            "REMOTE_WORKER_FAILED",
        )
        if provider_code in _JOB_ACTIVE_GUARD_ERROR_CODES:
            # A cancellation/expiry already made the durable row terminal, or
            # the point-in-time check was unavailable. Do not overwrite that
            # state, requeue sensitive data, or emit a failure notification.
            return
        attempt_count = int(row.get("attempt_count") or 0)
        max_attempts = int(row.get("max_attempts") or 1)
        retryable = bool(
            is_transient_provider_error(error)
            or (
                isinstance(
                    error,
                    (
                        ClaimRemoteRepositoryError,
                        ClaimRepositoryError,
                    ),
                )
                and isinstance(transient, dict)
                and bool(transient)
            )
        )
        if provider_code == "AUTH_SESSION_EXPIRED":
            retryable = False
        if retryable and attempt_count < max_attempts:
            try:
                progress, _ready, _target, _verified = self._progress(
                    claim_repository,
                    case_id,
                )
            except Exception:
                progress = int(row.get("progress") or 0)
            repository.release_job(
                job_id,
                self.worker_id,
                next_status="retry",
                stage=(
                    "collection_retry"
                    if isinstance(transient, Mapping)
                    and transient.get("retention_stage") == "collection"
                    else "retry"
                ),
                secure_payload=(
                    transient if isinstance(transient, dict) else {}
                ),
                progress=progress,
                next_run_at=self.clock() + timedelta(seconds=5),
                sensitive_expires_at=(
                    self._sensitive_payload_expires_at(transient)
                ),
                safe_message="인증 상태를 다시 확인하고 있습니다.",
                safe_error_code=provider_code,
            )
            return
        if isinstance(transient, dict):
            self._enqueue_case_message(
                repository,
                row={
                    **dict(row),
                    "invite_id": invite_id,
                    "case_id": case_id,
                },
                transient=transient,
                template_code=TEMPLATE_FAILED,
                event_type="COLLECTION_FAILED",
            )
        self._mark_case_failed(
            claim_repository,
            case_id,
            provider_code,
        )
        repository.release_job(
            job_id,
            self.worker_id,
            next_status="failed",
            stage="failed",
            progress=int(row.get("progress") or 0),
            safe_message=(
                "인증 또는 자료수집을 완료하지 못했습니다. 담당자에게 새 인증 요청을 받아 주세요."
            ),
            safe_error_code=provider_code,
        )

    @staticmethod
    def _fail_submission_reservation(
        repository: Any,
        job_id: str,
        case_id: str,
        error_code: str,
    ) -> bool:
        if not job_id or not case_id:
            return False
        try:
            repository.fail_reserved_job(
                job_id,
                case_id=case_id,
                safe_error_code=_safe_error_code(error_code),
                safe_message=(
                    "인증 요청을 시작하지 못했습니다. "
                    "담당자에게 새 인증 요청을 받아 주세요."
                ),
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _mark_case_failed(
        repository: Any,
        case_id: str,
        error_code: str,
    ) -> None:
        if not case_id:
            return
        try:
            repository.update_case_status(
                case_id,
                overall_status="failed",
                last_safe_error_code=_safe_error_code(error_code),
            )
            repository.append_audit_event(
                case_id=case_id,
                action="remote_collection",
                source="worker",
                outcome="failed",
                metadata={
                    "safe_error_code": _safe_error_code(error_code),
                },
            )
        except Exception:
            return

    def _enqueue_case_message(
        self,
        repository: Any,
        *,
        row: Mapping[str, Any],
        transient: Mapping[str, Any],
        template_code: str,
        event_type: str,
    ) -> None:
        context = transient.get("auth_context")
        if not isinstance(context, Mapping):
            return
        flow_type = str(
            transient.get("flow_type") or STAFF_TARGETED_FLOW
        ).strip().lower()
        if (
            flow_type == PROSPECT_SELF_INPUT_FLOW
            and event_type != "NEXT_AUTH"
        ):
            # The representative-entered number is an authentication channel,
            # not a durable CRM notification destination.  Only the immediate
            # second-auth prompt may use it; completion/failure notices remain
            # available on the secure status page and staff CRM.
            return
        name = str(context.get("representative") or "").strip()
        phone = _digits(context.get("cellphone"))
        if not name or not _PHONE_PATTERN.fullmatch(phone):
            return
        variables = {"#{고객명}": name}
        invite_url = str(transient.get("invite_url") or "")
        if invite_url.startswith("https://"):
            variables["#{인증링크}"] = _template_auth_link(invite_url)
        repository.enqueue_message(
            idempotency_key=(
                f"case:{str(row.get('case_id') or '')}:"
                f"{event_type.lower()}"
            ),
            event_type=event_type,
            template_code=template_code,
            secure_payload={
                "to": phone,
                "variables": variables,
            },
            invite_id=str(row.get("invite_id") or "") or None,
            case_id=str(row.get("case_id") or "") or None,
            expires_at=(
                self.clock() + timedelta(seconds=REMOTE_AUTH_TTL_SECONDS)
                if flow_type == PROSPECT_SELF_INPUT_FLOW
                else None
            ),
        )

    @staticmethod
    def _auth_wait_message(event: str) -> str:
        if event == "comwel_pending":
            return "근로복지공단 인증 완료를 기다리고 있습니다."
        return "국세청 홈택스 인증 완료를 기다리고 있습니다."

    @staticmethod
    def _auth_poll_delay(row: Mapping[str, Any]) -> int:
        attempts = int(row.get("attempt_count") or 0)
        if attempts <= 10:
            return 3
        if attempts <= 30:
            return 5
        return 10

    def _process_outbox(self, row: dict[str, Any]) -> None:
        owner = str(row.get("owner_user_id") or "").strip().lower()
        repository = self.remote_repository_factory(owner)
        message_id = str(row.get("id") or "")
        event_type = str(row.get("event_type") or "").strip().upper()
        is_guidance = event_type.startswith("GUIDANCE_")
        payload: dict[str, Any] | None = None
        guidance_message_id = ""
        canonical_contact_id = ""
        guidance_recipient_phone = ""
        recipient_phone_hash = ""
        provider_call_started = False
        try:
            if is_guidance:
                raw_guidance_message_id = str(
                    row.get("guidance_message_id") or ""
                ).strip()
                try:
                    guidance_message_id = str(
                        uuid.UUID(raw_guidance_message_id)
                    )
                except (AttributeError, TypeError, ValueError):
                    guidance_message_id = ""
                decrypted_payload = repository.decrypt_payload(
                    row.get("secure_payload_ciphertext")
                )
                if not isinstance(decrypted_payload, Mapping):
                    error_code = "GUIDANCE_DELIVERY_BINDING_INVALID"
                    repository.release_message(
                        message_id,
                        self.worker_id,
                        next_status="cancelled",
                        safe_error_code=error_code,
                    )
                    _notify_guidance_outbox_status(
                        guidance_message_id=guidance_message_id,
                        status="cancelled",
                        error_code=error_code,
                    )
                    return
                payload = dict(decrypted_payload)
                raw_payload_message_id = str(
                    payload.get("guidance_message_id") or ""
                ).strip()
                raw_contact_id = str(
                    payload.get("canonical_contact_id") or ""
                ).strip()
                try:
                    payload_message_id = str(
                        uuid.UUID(raw_payload_message_id)
                    )
                except (AttributeError, TypeError, ValueError):
                    payload_message_id = ""
                try:
                    canonical_contact_id = str(uuid.UUID(raw_contact_id))
                except (AttributeError, TypeError, ValueError):
                    canonical_contact_id = ""

                guidance_recipient_phone = _digits(payload.get("to"))
                if guidance_recipient_phone.startswith("82"):
                    guidance_recipient_phone = (
                        "0" + guidance_recipient_phone[2:]
                    )
                if (
                    not guidance_message_id
                    or payload_message_id != guidance_message_id
                    or not canonical_contact_id
                    or not _PHONE_PATTERN.fullmatch(
                        guidance_recipient_phone
                    )
                ):
                    error_code = "GUIDANCE_DELIVERY_BINDING_INVALID"
                    repository.release_message(
                        message_id,
                        self.worker_id,
                        next_status="cancelled",
                        safe_error_code=error_code,
                    )
                    _notify_guidance_outbox_status(
                        guidance_message_id=guidance_message_id,
                        status="cancelled",
                        error_code=error_code,
                    )
                    return

                phone_hash_key = str(
                    os.environ.get(
                        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY", ""
                    )
                    or ""
                ).strip()
                if len(phone_hash_key) < 32:
                    error_code = "GUIDANCE_PHONE_HASH_KEY_INVALID"
                    repository.release_message(
                        message_id,
                        self.worker_id,
                        next_status="cancelled",
                        safe_error_code=error_code,
                    )
                    _notify_guidance_outbox_status(
                        guidance_message_id=guidance_message_id,
                        status="cancelled",
                        error_code=error_code,
                    )
                    return
                recipient_phone_hash = hmac.new(
                    phone_hash_key.encode("utf-8"),
                    guidance_recipient_phone.encode("ascii"),
                    hashlib.sha256,
                ).hexdigest()
                provider_mode = str(
                    os.environ.get(
                        "OASIS_KAKAO_GUIDANCE_PROVIDER_MODE",
                        "",
                    )
                    or ""
                ).strip().lower()
                template_code = str(row.get("template_code") or "")
                template_env = TEMPLATE_ENV_BY_CODE.get(template_code, "")
                guidance_readiness = guidance_send_readiness(
                    os.environ,
                    required_template_env_names=(template_env,),
                )
                if (
                    provider_mode != "live"
                    or not guidance_readiness.get(
                        "external_send_allowed"
                    )
                ):
                    error_code = (
                        "GUIDANCE_SEND_DISABLED"
                        if provider_mode == "live"
                        else "GUIDANCE_PROVIDER_MODE_INVALID"
                    )
                    repository.release_message(
                        message_id,
                        self.worker_id,
                        next_status="cancelled",
                        safe_error_code=error_code,
                    )
                    _notify_guidance_outbox_status(
                        guidance_message_id=guidance_message_id,
                        status="blocked",
                        error_code=error_code,
                    )
                    return
            if str(row.get("template_code") or "") in {
                TEMPLATE_AUTH_RESUME,
                TEMPLATE_NEXT_AUTH,
            }:
                status = repository.get_session_status(
                    str(row.get("invite_id") or "")
                )
                job_status = str(
                    status.get("job_status") or ""
                ).strip().lower()
                invite_status = str(
                    status.get("invite_status") or ""
                ).strip().lower()
                if job_status in {
                    "complete",
                    "partial",
                    "failed",
                    "expired",
                    "cancelled",
                } or (
                    not job_status
                    and invite_status
                    in {
                        "expired",
                        "cancelled",
                        "send_failed",
                    }
                ):
                    repository.release_message(
                        message_id,
                        self.worker_id,
                        next_status="cancelled",
                    )
                    return
            if payload is None:
                payload = repository.decrypt_payload(
                    row.get("secure_payload_ciphertext")
                )
            template_code = str(row.get("template_code") or "")
            template_env = TEMPLATE_ENV_BY_CODE.get(template_code, "")
            template_id = str(os.environ.get(template_env, "") or "").strip()
            if not template_id:
                raise SolapiAlimtalkError(
                    "CONFIGURATION_MISSING",
                    "알림톡 템플릿 설정이 필요합니다.",
                )
            if is_guidance:
                # Re-check after decryption/config validation and immediately
                # before the provider call.  This closes the queue -> cancel or
                # opt-out race without trusting stale client/outbox state.
                send_state = _check_guidance_send_ready(
                    guidance_message_id,
                    canonical_contact_id,
                    recipient_phone_hash,
                )
                if not bool(send_state.get("allowed", False)):
                    error_code = _safe_error_code(
                        send_state.get("code", ""),
                        "GUIDANCE_SEND_STATE_BLOCKED",
                    )
                    repository.release_message(
                        message_id,
                        self.worker_id,
                        next_status="cancelled",
                        safe_error_code=error_code,
                    )
                    _notify_guidance_outbox_status(
                        guidance_message_id=guidance_message_id,
                        status="cancelled",
                        error_code=error_code,
                    )
                    return
                try:
                    dispatch = repository.begin_guidance_dispatch(
                        message_id,
                        self.worker_id,
                        canonical_contact_id=canonical_contact_id,
                        recipient_phone_hash=recipient_phone_hash,
                    )
                except Exception:
                    dispatch = None
                if (
                    not isinstance(dispatch, Mapping)
                    or dispatch.get("success") is not True
                    or str(dispatch.get("message_id") or "").strip()
                    != guidance_message_id
                ):
                    error_code = "GUIDANCE_DISPATCH_NOT_STARTED"
                    try:
                        repository.release_message(
                            message_id,
                            self.worker_id,
                            next_status="cancelled",
                            safe_error_code=error_code,
                        )
                    except Exception:
                        pass
                    _notify_guidance_outbox_status(
                        guidance_message_id=guidance_message_id,
                        status="cancelled",
                        error_code=error_code,
                    )
                    return
            provider_call_started = True
            result = self.solapi_client_factory().send_alimtalk(
                (
                    guidance_recipient_phone
                    if is_guidance
                    else str(payload.get("to") or "")
                ),
                template_id,
                variables=(
                    payload.get("variables")
                    if isinstance(payload.get("variables"), Mapping)
                    else {}
                ),
                disable_sms=True,
            )
            provider_message_id = str(result.message_id or result.group_id)
            repository.release_message(
                message_id,
                self.worker_id,
                next_status="sent",
                provider_message_id=provider_message_id,
            )
            if is_guidance:
                _notify_guidance_outbox_status(
                    guidance_message_id=guidance_message_id,
                    status="sent",
                    provider_message_id=provider_message_id,
                )
        except Exception as exc:
            code = _safe_error_code(
                getattr(exc, "code", ""),
                "ALIMTALK_SEND_FAILED",
            )
            http_status = int(getattr(exc, "http_status", 0) or 0)
            retryable = code in {"TIMEOUT", "NETWORK_ERROR"} or (
                http_status == 429 or http_status >= 500
            )
            guidance_outcome_unknown = (
                is_guidance
                and provider_call_started
                and (
                    retryable
                    or code
                    in {
                        "INVALID_RESPONSE",
                        "ALIMTALK_SEND_FAILED",
                    }
                )
            )
            if guidance_outcome_unknown:
                code = "GUIDANCE_PROVIDER_OUTCOME_UNKNOWN"
            if is_guidance and provider_call_started:
                retryable = False
            attempt_count = int(row.get("attempt_count") or 0)
            max_attempts = int(row.get("max_attempts") or 1)
            next_status = (
                "retry"
                if retryable and attempt_count < max_attempts
                else "failed"
            )
            repository.release_message(
                message_id,
                self.worker_id,
                next_status=next_status,
                secure_payload=(
                    repository.decrypt_payload(
                        row.get("secure_payload_ciphertext")
                    )
                    if retryable and attempt_count < max_attempts
                    else None
                ),
                next_run_at=self.clock()
                + timedelta(seconds=min(300, 5 * max(1, attempt_count))),
                safe_error_code=code,
            )
            if is_guidance and next_status == "failed":
                _notify_guidance_outbox_status(
                    guidance_message_id=guidance_message_id,
                    status="failed",
                    error_code=code,
                )


_DEFAULT_SERVICE: ClaimRemoteService | None = None
_DEFAULT_SERVICE_LOCK = threading.Lock()


def create_public_claim_service() -> ClaimRemoteService:
    """Factory expected by ``claim_public_gateway.ClaimPublicService``."""

    global _DEFAULT_SERVICE
    with _DEFAULT_SERVICE_LOCK:
        if _DEFAULT_SERVICE is None:
            enabled = str(
                os.environ.get("CLAIM_REMOTE_WORKER_ENABLED", "true")
                or "true"
            ).strip().lower() not in {"0", "false", "no", "off"}
            _DEFAULT_SERVICE = ClaimRemoteService(start_worker=enabled)
            task_automation_enabled = str(
                os.environ.get("OASIS_TASK_AUTOMATION_ENABLED", "true")
                or "true"
            ).strip().lower() not in {"0", "false", "no", "off"}
            if task_automation_enabled:
                # This consumer only creates internal CRM tasks.  It is
                # independent from the Tilko authentication/collection worker
                # and must remain active when that worker is disabled.
                try:
                    from guidance_task_automation import (  # noqa: PLC0415
                        start_guidance_task_automation_worker,
                    )

                    start_guidance_task_automation_worker()
                except Exception:
                    # A DB lease allows safe recovery after restart.  Raw
                    # connector errors are deliberately not logged because
                    # upstream details can contain request information.
                    pass
        return _DEFAULT_SERVICE


def create_staff_claim_invite(
    *,
    owner_user_id: str,
    requested_by: str,
    customer_name: str,
    customer_phone: str,
) -> dict[str, Any]:
    """Queue the two staff-side AlimTalk events without returning the token."""

    return create_public_claim_service().create_staff_invite(
        owner_user_id=owner_user_id,
        requested_by=requested_by,
        customer_name=customer_name,
        customer_phone=customer_phone,
    )


def create_prospect_self_input_invite(
    *,
    owner_user_id: str,
    requested_by: str,
    company_uid: str,
    guidance_type: str,
    guidance_message_id: str,
) -> dict[str, Any]:
    """Create, but do not send, a DB-discovery self-input invite."""

    return create_public_claim_service().create_prospect_self_input_invite(
        owner_user_id=owner_user_id,
        requested_by=requested_by,
        company_uid=company_uid,
        guidance_type=guidance_type,
        guidance_message_id=guidance_message_id,
    )
