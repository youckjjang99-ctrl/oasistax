from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from claim_remote_crypto import ClaimRemoteCrypto, ClaimRemoteCryptoError
from cloud_db import CloudDatabase, cloud_is_configured


DEFAULT_KEY_VERSION = "v1"
DEFAULT_INVITE_TTL_SECONDS = 24 * 60 * 60
DEFAULT_JOB_TTL_SECONDS = 45 * 60
DEFAULT_SENSITIVE_TTL_SECONDS = 10 * 60
DEFAULT_OUTBOX_TTL_SECONDS = 24 * 60 * 60


class ClaimRemoteRepositoryError(RuntimeError):
    """A remote-claim storage error safe to show to an operator."""

    def __init__(self, message: str, *, error_code: str):
        super().__init__(message)
        self.error_code = str(error_code or "REMOTE_REPOSITORY_ERROR")


@dataclass(frozen=True)
class RemoteInvite:
    token: str
    record: dict[str, Any]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    selected = value
    if selected.tzinfo is None:
        selected = selected.replace(tzinfo=timezone.utc)
    return selected.astimezone(timezone.utc).isoformat()


def _required_owner(value: Any) -> str:
    owner = str(value or "").strip().lower()
    if not owner or len(owner) > 200:
        raise ClaimRemoteRepositoryError(
            "로그인 사용자 정보를 확인할 수 없습니다.",
            error_code="REMOTE_OWNER_REQUIRED",
        )
    return owner


def _required_worker(value: Any) -> str:
    worker = str(value or "").strip()
    if not worker or len(worker) > 120:
        raise ClaimRemoteRepositoryError(
            "백그라운드 작업자 식별값을 확인할 수 없습니다.",
            error_code="REMOTE_WORKER_REQUIRED",
        )
    return worker


def _required_uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value or "").strip()))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ClaimRemoteRepositoryError(
            f"{label} 식별값을 확인할 수 없습니다.",
            error_code="REMOTE_INVALID_ID",
        ) from exc


def _bounded_text(value: Any, maximum: int) -> str:
    return str(value or "").strip()[:maximum]


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(row) for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        return [dict(value)]
    return []


def _first_row(value: Any, *, error_code: str) -> dict[str, Any]:
    rows = _rows(value)
    if not rows:
        raise ClaimRemoteRepositoryError(
            "원격 인증 저장 결과를 확인하지 못했습니다.",
            error_code=error_code,
        )
    return rows[0]


_BACKEND_ERROR_MAP = (
    (
        "REMOTE_INVITE_NOT_FOUND",
        "인증 링크를 찾지 못했습니다.",
    ),
    (
        "REMOTE_INVITE_EXPIRED",
        "인증 링크의 유효시간이 지났습니다.",
    ),
    (
        "REMOTE_INVITE_ALREADY_CONSUMED",
        "이미 사용한 인증 링크입니다.",
    ),
    (
        "REMOTE_INVITE_NOT_ACTIVE",
        "현재 사용할 수 없는 인증 링크입니다.",
    ),
    (
        "REMOTE_JOB_NOT_RESERVED",
        "인증 요청 준비 상태를 확인하지 못했습니다.",
    ),
    (
        "REMOTE_JOB_LEASE_LOST",
        "작업 처리 권한이 만료되었습니다.",
    ),
    (
        "REMOTE_OUTBOX_LEASE_LOST",
        "메시지 처리 권한이 만료되었습니다.",
    ),
    (
        "REMOTE_OUTBOX_IDEMPOTENCY_CONFLICT",
        "같은 발송 식별값에 다른 메시지가 연결되어 있습니다.",
    ),
)


class ClaimRemoteRepository:
    def __init__(
        self,
        owner_user_id: str,
        *,
        database: CloudDatabase | None = None,
        crypto: ClaimRemoteCrypto | None = None,
        key_version: str | None = None,
    ):
        self.owner_user_id = _required_owner(owner_user_id)
        if database is None and not cloud_is_configured():
            raise ClaimRemoteRepositoryError(
                "Supabase 연결 설정이 필요합니다.",
                error_code="REMOTE_STORAGE_NOT_CONFIGURED",
            )
        self.database = database or CloudDatabase()
        try:
            self.crypto = crypto or ClaimRemoteCrypto.from_environment()
        except ClaimRemoteCryptoError as exc:
            raise ClaimRemoteRepositoryError(
                "원격 인증 암호화 설정을 확인해 주세요.",
                error_code="REMOTE_CRYPTO_NOT_CONFIGURED",
            ) from exc
        self.key_version = _bounded_text(
            key_version or DEFAULT_KEY_VERSION,
            40,
        )
        if not self.key_version or not re.fullmatch(
            r"[A-Za-z0-9._-]{1,40}",
            self.key_version,
        ):
            raise ClaimRemoteRepositoryError(
                "원격 인증 암호화 키 버전을 확인해 주세요.",
                error_code="REMOTE_KEY_VERSION_INVALID",
            )

    @classmethod
    def _global_invite_action(
        cls,
        token: str,
        *,
        rpc_name: str,
        database: CloudDatabase | None = None,
        crypto: ClaimRemoteCrypto | None = None,
    ) -> dict[str, Any]:
        if database is None and not cloud_is_configured():
            raise ClaimRemoteRepositoryError(
                "Supabase 연결 설정이 필요합니다.",
                error_code="REMOTE_STORAGE_NOT_CONFIGURED",
            )
        selected_database = database or CloudDatabase()
        try:
            selected_crypto = crypto or ClaimRemoteCrypto.from_environment()
        except ClaimRemoteCryptoError as exc:
            raise ClaimRemoteRepositoryError(
                "원격 인증 암호화 설정을 확인해 주세요.",
                error_code="REMOTE_CRYPTO_NOT_CONFIGURED",
            ) from exc

        try:
            token_digest = selected_crypto.invite_token_hash(token)
        except ClaimRemoteCryptoError as exc:
            raise ClaimRemoteRepositoryError(
                "인증 링크 토큰 형식을 확인해 주세요.",
                error_code="REMOTE_TOKEN_INVALID",
            ) from exc

        try:
            result = selected_database.rpc(
                rpc_name,
                {"p_token_hash": token_digest},
            )
        except Exception as exc:
            backend_text = str(exc)
            for marker, message in _BACKEND_ERROR_MAP:
                if marker in backend_text:
                    raise ClaimRemoteRepositoryError(
                        message,
                        error_code=marker,
                    ) from exc
            raise ClaimRemoteRepositoryError(
                "원격 인증 저장소에 연결하지 못했습니다.",
                error_code="REMOTE_REPOSITORY_UNAVAILABLE",
            ) from exc

        record = _first_row(
            result,
            error_code="REMOTE_INVITE_NOT_FOUND",
        )
        if str(record.get("status") or "").strip().lower() == "expired":
            raise ClaimRemoteRepositoryError(
                "인증 링크의 유효시간이 지났습니다.",
                error_code="REMOTE_INVITE_EXPIRED",
            )
        return record

    @classmethod
    def resolve_invite(
        cls,
        token: str,
        *,
        database: CloudDatabase | None = None,
        crypto: ClaimRemoteCrypto | None = None,
    ) -> dict[str, Any]:
        """Resolve an opaque public token without exposing it to storage."""

        return cls._global_invite_action(
            token,
            rpc_name="oasis_claim_remote_resolve_invite",
            database=database,
            crypto=crypto,
        )

    @classmethod
    def mark_invite_opened_global(
        cls,
        token: str,
        *,
        database: CloudDatabase | None = None,
        crypto: ClaimRemoteCrypto | None = None,
    ) -> dict[str, Any]:
        """Open an invite when the public route does not know its owner."""

        return cls._global_invite_action(
            token,
            rpc_name="oasis_claim_remote_mark_invite_opened_global",
            database=database,
            crypto=crypto,
        )

    def _rpc(self, name: str, parameters: dict[str, Any]) -> Any:
        try:
            return self.database.rpc(name, parameters)
        except Exception as exc:
            backend_text = str(exc)
            for marker, message in _BACKEND_ERROR_MAP:
                if marker in backend_text:
                    raise ClaimRemoteRepositoryError(
                        message,
                        error_code=marker,
                    ) from exc
            raise ClaimRemoteRepositoryError(
                "원격 인증 저장소에 연결하지 못했습니다.",
                error_code="REMOTE_REPOSITORY_UNAVAILABLE",
            ) from exc

    def token_hash(self, token: str) -> str:
        try:
            return self.crypto.invite_token_hash(token)
        except ClaimRemoteCryptoError as exc:
            raise ClaimRemoteRepositoryError(
                "인증 링크 토큰 형식을 확인해 주세요.",
                error_code="REMOTE_TOKEN_INVALID",
            ) from exc

    def encrypt_payload(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise ClaimRemoteRepositoryError(
                "암호화할 인증정보 형식을 확인해 주세요.",
                error_code="REMOTE_PAYLOAD_INVALID",
            )
        try:
            return self.crypto.encrypt_json(payload)
        except ClaimRemoteCryptoError as exc:
            raise ClaimRemoteRepositoryError(
                "임시 인증정보를 암호화하지 못했습니다.",
                error_code="REMOTE_PAYLOAD_ENCRYPT_FAILED",
            ) from exc

    def decrypt_payload(self, ciphertext: Any) -> dict[str, Any]:
        encoded = str(ciphertext or "").strip()
        if not encoded:
            raise ClaimRemoteRepositoryError(
                "복구할 임시 인증정보가 없습니다.",
                error_code="REMOTE_PAYLOAD_MISSING",
            )
        try:
            payload = self.crypto.decrypt_json(encoded)
        except ClaimRemoteCryptoError as exc:
            raise ClaimRemoteRepositoryError(
                "임시 인증정보를 복구하지 못했습니다.",
                error_code="REMOTE_PAYLOAD_DECRYPT_FAILED",
            ) from exc
        return payload

    def create_invite(
        self,
        *,
        secure_payload: dict[str, Any],
        recipient_name_masked: str = "",
        recipient_phone_masked: str = "",
        expires_at: datetime | None = None,
        invite_id: str | None = None,
    ) -> RemoteInvite:
        selected_id = _required_uuid(
            invite_id or uuid.uuid4(),
            "초대",
        )
        selected_expiry = expires_at or (
            _utc_now() + timedelta(seconds=DEFAULT_INVITE_TTL_SECONDS)
        )
        if selected_expiry <= _utc_now():
            raise ClaimRemoteRepositoryError(
                "인증 링크 만료시각은 현재 이후여야 합니다.",
                error_code="REMOTE_INVITE_EXPIRY_INVALID",
            )
        token = self.crypto.generate_invite_token()
        token_digest = self.token_hash(token)
        ciphertext = self.encrypt_payload(secure_payload)
        result = self._rpc(
            "oasis_claim_remote_create_invite",
            {
                "p_invite": {
                    "id": selected_id,
                    "owner_user_id": self.owner_user_id,
                    "token_hash": token_digest,
                    "secure_payload_ciphertext": ciphertext,
                    "payload_key_version": self.key_version,
                    "recipient_name_masked": _bounded_text(
                        recipient_name_masked,
                        120,
                    ),
                    "recipient_phone_masked": _bounded_text(
                        recipient_phone_masked,
                        40,
                    ),
                    "expires_at": _iso(selected_expiry),
                }
            },
        )
        return RemoteInvite(
            token=token,
            record=_first_row(
                result,
                error_code="REMOTE_INVITE_CREATE_FAILED",
            ),
        )

    def get_invite(self, token: str) -> dict[str, Any] | None:
        result = self._rpc(
            "oasis_claim_remote_get_invite",
            {
                "p_owner_user_id": self.owner_user_id,
                "p_token_hash": self.token_hash(token),
            },
        )
        rows = _rows(result)
        return rows[0] if rows else None

    def mark_invite_opened(self, token: str) -> dict[str, Any]:
        return _first_row(
            self._rpc(
                "oasis_claim_remote_mark_invite_opened",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_token_hash": self.token_hash(token),
                },
            ),
            error_code="REMOTE_INVITE_OPEN_FAILED",
        )

    def cancel_invite(
        self,
        token: str,
        *,
        reason: str = "customer_opt_out",
    ) -> dict[str, Any]:
        """Cancel an invite and its pending work via one atomic RPC."""

        selected_reason = _bounded_text(reason, 120)
        if not selected_reason:
            selected_reason = "customer_opt_out"
        return _first_row(
            self._rpc(
                "oasis_claim_remote_cancel_invite",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_token_hash": self.token_hash(token),
                    "p_reason": selected_reason,
                },
            ),
            error_code="REMOTE_INVITE_CANCEL_FAILED",
        )

    def get_session_status(self, invite_id: str) -> dict[str, Any]:
        """Return only browser-session-safe invite and job status fields."""

        return _first_row(
            self._rpc(
                "oasis_claim_remote_get_session_status",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_invite_id": _required_uuid(invite_id, "초대"),
                },
            ),
            error_code="REMOTE_INVITE_NOT_FOUND",
        )

    def consume_invite(
        self,
        token: str,
        *,
        case_id: str,
        secure_job_payload: dict[str, Any],
        hard_expires_at: datetime | None = None,
        sensitive_expires_at: datetime | None = None,
        job_id: str | None = None,
        stage: str = "hometax_request",
        max_attempts: int = 12,
        initial_status: str = "queued",
        next_run_at: datetime | None = None,
    ) -> dict[str, Any]:
        selected_job_id = _required_uuid(
            job_id or uuid.uuid4(),
            "작업",
        )
        selected_case_id = _required_uuid(case_id, "경정청구")
        selected_now = _utc_now()
        selected_expiry = hard_expires_at or (
            selected_now + timedelta(seconds=DEFAULT_JOB_TTL_SECONDS)
        )
        selected_sensitive_expiry = sensitive_expires_at or (
            selected_now + timedelta(seconds=DEFAULT_SENSITIVE_TTL_SECONDS)
        )
        if (
            selected_expiry <= selected_now
            or selected_sensitive_expiry <= selected_now
        ):
            raise ClaimRemoteRepositoryError(
                "백그라운드 작업 만료시각은 현재 이후여야 합니다.",
                error_code="REMOTE_JOB_EXPIRY_INVALID",
            )
        result = self._rpc(
            "oasis_claim_remote_consume_invite",
            {
                "p_owner_user_id": self.owner_user_id,
                "p_token_hash": self.token_hash(token),
                "p_job": {
                    "id": selected_job_id,
                    "case_id": selected_case_id,
                    "stage": _bounded_text(stage, 80),
                    "secure_payload_ciphertext": self.encrypt_payload(
                        secure_job_payload
                    ),
                    "payload_key_version": self.key_version,
                    "hard_expires_at": _iso(selected_expiry),
                    "sensitive_expires_at": _iso(
                        min(selected_sensitive_expiry, selected_expiry)
                    ),
                    "max_attempts": max(1, min(int(max_attempts), 100)),
                    "initial_status": _bounded_text(
                        str(initial_status or "queued").lower(),
                        20,
                    ),
                    "next_run_at": _iso(next_run_at or _utc_now()),
                },
            },
        )
        return _first_row(
            result,
            error_code="REMOTE_INVITE_CONSUME_FAILED",
        )

    def activate_reserved_job(
        self,
        job_id: str,
        *,
        case_id: str,
        secure_payload: dict[str, Any],
        stage: str = "hometax_pending",
    ) -> dict[str, Any]:
        return _first_row(
            self._rpc(
                "oasis_claim_remote_activate_reserved_job",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_job_id": _required_uuid(job_id, "작업"),
                    "p_case_id": _required_uuid(case_id, "경정청구"),
                    "p_secure_payload_ciphertext": self.encrypt_payload(
                        secure_payload
                    ),
                    "p_stage": _bounded_text(
                        str(stage or "hometax_pending").lower(),
                        80,
                    ),
                },
            ),
            error_code="REMOTE_JOB_ACTIVATE_FAILED",
        )

    def fail_reserved_job(
        self,
        job_id: str,
        *,
        case_id: str,
        safe_error_code: str,
        safe_message: str = "",
    ) -> dict[str, Any]:
        return _first_row(
            self._rpc(
                "oasis_claim_remote_fail_reserved_job",
                {
                    "p_owner_user_id": self.owner_user_id,
                    "p_job_id": _required_uuid(job_id, "작업"),
                    "p_case_id": _required_uuid(case_id, "경정청구"),
                    "p_safe_error_code": _bounded_text(
                        str(safe_error_code or "REMOTE_SUBMISSION_FAILED")
                        .upper(),
                        80,
                    ),
                    "p_safe_message": _bounded_text(safe_message, 500),
                },
            ),
            error_code="REMOTE_JOB_FAIL_FAILED",
        )

    def lease_jobs(
        self,
        worker_id: str,
        *,
        limit: int = 1,
        lease_seconds: int = 60,
    ) -> list[dict[str, Any]]:
        return _rows(
            self._rpc(
                "oasis_claim_remote_lease_jobs",
                {
                    "p_worker_id": _required_worker(worker_id),
                    "p_limit": max(1, min(int(limit), 50)),
                    "p_lease_seconds": max(
                        15,
                        min(int(lease_seconds), 600),
                    ),
                },
            )
        )

    def heartbeat_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        sensitive_expires_at: datetime | None = None,
        stage: str | None = None,
    ) -> dict[str, Any]:
        return _first_row(
            self._rpc(
                "oasis_claim_remote_heartbeat_job",
                {
                    "p_job_id": _required_uuid(job_id, "작업"),
                    "p_worker_id": _required_worker(worker_id),
                    "p_lease_seconds": max(
                        15,
                        min(int(lease_seconds), 600),
                    ),
                    "p_sensitive_expires_at": (
                        _iso(sensitive_expires_at)
                        if sensitive_expires_at is not None
                        else None
                    ),
                    "p_stage": (
                        _bounded_text(str(stage).lower(), 80)
                        if stage is not None
                        else None
                    ),
                },
            ),
            error_code="REMOTE_JOB_HEARTBEAT_FAILED",
        )

    def check_job_active(
        self,
        job_id: str,
        *,
        mode: str,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        """Return a PII-free, point-in-time provider-call decision.

        ``submission_reserved`` covers the synchronous first authentication
        request before a worker lease exists. ``leased`` additionally binds
        the decision to the current worker and its unexpired lease.
        """

        expected_job_id = _required_uuid(job_id, "작업")
        selected_mode = _bounded_text(mode, 40).lower()
        if selected_mode not in {"submission_reserved", "leased"}:
            raise ClaimRemoteRepositoryError(
                "작업 활성 상태 확인 방식을 확인할 수 없습니다.",
                error_code="REMOTE_JOB_ACTIVE_MODE_INVALID",
            )
        selected_worker = (
            _required_worker(worker_id)
            if selected_mode == "leased"
            else None
        )
        result = _first_row(
            self._rpc(
                "oasis_claim_remote_check_job_active",
                {
                    "p_job_id": expected_job_id,
                    "p_owner_user_id": self.owner_user_id,
                    "p_mode": selected_mode,
                    "p_worker_id": selected_worker,
                },
            ),
            error_code="REMOTE_JOB_ACTIVE_CHECK_FAILED",
        )
        allowed = result.get("allowed")
        code = _bounded_text(result.get("code"), 80).upper()
        try:
            returned_job_id = str(
                uuid.UUID(str(result.get("job_id") or "").strip())
            )
        except (AttributeError, TypeError, ValueError) as exc:
            raise ClaimRemoteRepositoryError(
                "작업 활성 상태 응답을 확인할 수 없습니다.",
                error_code="REMOTE_JOB_ACTIVE_RESPONSE_INVALID",
            ) from exc
        if (
            not isinstance(allowed, bool)
            or returned_job_id != expected_job_id
            or not re.fullmatch(r"[A-Z0-9_-]{1,80}", code)
        ):
            raise ClaimRemoteRepositoryError(
                "작업 활성 상태 응답을 확인할 수 없습니다.",
                error_code="REMOTE_JOB_ACTIVE_RESPONSE_INVALID",
            )
        return {
            "allowed": allowed,
            "code": code,
            "job_id": returned_job_id,
        }

    def release_job(
        self,
        job_id: str,
        worker_id: str,
        *,
        next_status: str,
        stage: str,
        secure_payload: dict[str, Any] | None = None,
        progress: int = 0,
        next_run_at: datetime | None = None,
        safe_message: str = "",
        safe_error_code: str = "",
        sensitive_expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        terminal = str(next_status or "").strip().lower() in {
            "complete",
            "partial",
            "failed",
            "expired",
            "cancelled",
        }
        ciphertext = (
            ""
            if terminal
            else self.encrypt_payload(secure_payload or {})
        )
        return _first_row(
            self._rpc(
                "oasis_claim_remote_release_job",
                {
                    "p_job_id": _required_uuid(job_id, "작업"),
                    "p_worker_id": _required_worker(worker_id),
                    "p_next_status": _bounded_text(next_status, 40),
                    "p_stage": _bounded_text(stage, 80),
                    "p_secure_payload_ciphertext": ciphertext,
                    "p_payload_key_version": self.key_version,
                    "p_progress": max(0, min(int(progress), 100)),
                    "p_next_run_at": _iso(next_run_at or _utc_now()),
                    "p_safe_message": _bounded_text(safe_message, 500),
                    "p_safe_error_code": _bounded_text(
                        safe_error_code,
                        80,
                    ),
                    "p_sensitive_expires_at": (
                        _iso(sensitive_expires_at)
                        if sensitive_expires_at is not None
                        else None
                    ),
                },
            ),
            error_code="REMOTE_JOB_RELEASE_FAILED",
        )

    def enqueue_message(
        self,
        *,
        idempotency_key: str,
        event_type: str,
        template_code: str,
        secure_payload: dict[str, Any],
        invite_id: str | None = None,
        case_id: str | None = None,
        run_after: datetime | None = None,
        expires_at: datetime | None = None,
        max_attempts: int = 8,
        message_id: str | None = None,
        guidance_message_id: str | None = None,
    ) -> dict[str, Any]:
        if not invite_id and not case_id:
            raise ClaimRemoteRepositoryError(
                "메시지에 초대 또는 경정청구 식별값이 필요합니다.",
                error_code="REMOTE_OUTBOX_TARGET_REQUIRED",
            )
        selected_expiry = expires_at or (
            _utc_now() + timedelta(seconds=DEFAULT_OUTBOX_TTL_SECONDS)
        )
        if selected_expiry <= _utc_now():
            raise ClaimRemoteRepositoryError(
                "메시지 만료시각은 현재 이후여야 합니다.",
                error_code="REMOTE_OUTBOX_EXPIRY_INVALID",
            )
        result = self._rpc(
            "oasis_claim_remote_enqueue_outbox",
            {
                "p_message": {
                    "id": _required_uuid(
                        message_id or uuid.uuid4(),
                        "메시지",
                    ),
                    "owner_user_id": self.owner_user_id,
                    "invite_id": (
                        _required_uuid(invite_id, "초대")
                        if invite_id
                        else None
                    ),
                    "case_id": (
                        _required_uuid(case_id, "경정청구")
                        if case_id
                        else None
                    ),
                    "guidance_message_id": (
                        _required_uuid(guidance_message_id, "안내 메시지")
                        if guidance_message_id
                        else None
                    ),
                    "event_type": _bounded_text(event_type, 80),
                    "template_code": _bounded_text(template_code, 120),
                    "idempotency_key": _bounded_text(
                        idempotency_key,
                        200,
                    ),
                    "secure_payload_ciphertext": self.encrypt_payload(
                        secure_payload
                    ),
                    "payload_key_version": self.key_version,
                    "run_after": _iso(run_after or _utc_now()),
                    "expires_at": _iso(selected_expiry),
                    "max_attempts": max(1, min(int(max_attempts), 100)),
                }
            },
        )
        return _first_row(
            result,
            error_code="REMOTE_OUTBOX_ENQUEUE_FAILED",
        )

    def lease_messages(
        self,
        worker_id: str,
        *,
        limit: int = 10,
        lease_seconds: int = 60,
    ) -> list[dict[str, Any]]:
        return _rows(
            self._rpc(
                "oasis_claim_remote_lease_outbox",
                {
                    "p_worker_id": _required_worker(worker_id),
                    "p_limit": max(1, min(int(limit), 100)),
                    "p_lease_seconds": max(
                        15,
                        min(int(lease_seconds), 600),
                    ),
                },
            )
        )

    def begin_guidance_dispatch(
        self,
        message_id: str,
        worker_id: str,
        *,
        canonical_contact_id: str,
        recipient_phone_hash: str,
    ) -> dict[str, Any]:
        """Erase the live guidance destination before the provider call.

        This creates an at-most-once boundary: after it succeeds, an abandoned
        lease cannot expose a decryptable phone number to an automatic retry.
        """

        phone_hash = _bounded_text(recipient_phone_hash, 64).lower()
        if not re.fullmatch(r"[0-9a-f]{64}", phone_hash):
            raise ClaimRemoteRepositoryError(
                "발송 연락처 결속을 확인할 수 없습니다.",
                error_code="REMOTE_GUIDANCE_BINDING_INVALID",
            )
        return _first_row(
            self._rpc(
                "oasis_claim_remote_begin_guidance_dispatch",
                {
                    "p_message_id": _required_uuid(message_id, "메시지"),
                    "p_worker_id": _required_worker(worker_id),
                    "p_contact_id": _required_uuid(
                        canonical_contact_id,
                        "발송 연락처",
                    ),
                    "p_recipient_phone_hash": _bounded_text(
                        phone_hash, 64
                    ),
                },
            ),
            error_code="REMOTE_GUIDANCE_DISPATCH_FAILED",
        )

    def release_message(
        self,
        message_id: str,
        worker_id: str,
        *,
        next_status: str,
        secure_payload: dict[str, Any] | None = None,
        provider_message_id: str = "",
        next_run_at: datetime | None = None,
        safe_error_code: str = "",
    ) -> dict[str, Any]:
        terminal = str(next_status or "").strip().lower() in {
            "sent",
            "delivered",
            "failed",
            "cancelled",
            "expired",
        }
        ciphertext = (
            ""
            if terminal
            else self.encrypt_payload(secure_payload or {})
        )
        return _first_row(
            self._rpc(
                "oasis_claim_remote_release_outbox",
                {
                    "p_message_id": _required_uuid(
                        message_id,
                        "메시지",
                    ),
                    "p_worker_id": _required_worker(worker_id),
                    "p_next_status": _bounded_text(next_status, 40),
                    "p_secure_payload_ciphertext": ciphertext,
                    "p_payload_key_version": self.key_version,
                    "p_provider_message_id": _bounded_text(
                        provider_message_id,
                        200,
                    ),
                    "p_next_run_at": _iso(next_run_at or _utc_now()),
                    "p_safe_error_code": _bounded_text(
                        safe_error_code,
                        80,
                    ),
                },
            ),
            error_code="REMOTE_OUTBOX_RELEASE_FAILED",
        )

    def expire_due(self) -> dict[str, int]:
        result = self._rpc("oasis_claim_remote_expire_due", {})
        if not isinstance(result, dict):
            return {"invites": 0, "jobs": 0, "messages": 0}
        return {
            "invites": int(result.get("invites") or 0),
            "jobs": int(result.get("jobs") or 0),
            "messages": int(result.get("messages") or 0),
        }
