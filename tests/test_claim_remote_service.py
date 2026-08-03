from __future__ import annotations

import hashlib
import hmac
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

import claim_remote_service as remote_service_module
from claim_remote_repository import ClaimRemoteRepositoryError
from claim_remote_service import (
    ClaimRemoteService,
    ClaimRemoteServiceError,
    PROSPECT_INVITE_TTL_SECONDS,
    PROSPECT_SELF_INPUT_FLOW,
    REMOTE_AUTH_TTL_SECONDS,
    TEMPLATE_ENV_BY_CODE,
    TEMPLATE_AUTH_RESUME,
    TEMPLATE_AUTH_START,
    TEMPLATE_NEXT_AUTH,
    remote_invite_environment_readiness,
)


class FakeRemoteState:
    def __init__(self) -> None:
        self.invite_id = "11111111-1111-4111-8111-111111111111"
        self.case_id = "22222222-2222-4222-8222-222222222222"
        self.invite_payload: dict[str, Any] = {}
        self.invite_create_kwargs: dict[str, Any] = {}
        self.cancelled: list[dict[str, Any]] = []
        self.enqueued: list[dict[str, Any]] = []
        self.consumed: list[dict[str, Any]] = []
        self.activated: list[dict[str, Any]] = []
        self.heartbeats: list[dict[str, Any]] = []
        self.job_active = True
        self.job_active_sequence: list[bool] = []
        self.job_active_checks: list[dict[str, Any]] = []
        self.released_jobs: list[dict[str, Any]] = []
        self.released_messages: list[dict[str, Any]] = []
        self.guidance_dispatches: list[dict[str, Any]] = []
        self.decrypted_guidance_message_id = ""
        self.jobs: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.events: list[str] = []
        self.enforce_atomic_consume = False
        self.invite_consumed = False
        self.consume_lock = threading.Lock()
        self.get_invite_barrier: threading.Barrier | None = None


class FakeRemoteRepository:
    def __init__(self, owner: str, state: FakeRemoteState):
        self.owner_user_id = owner
        self.state = state

    def create_invite(self, **kwargs: Any) -> Any:
        self.state.invite_payload = dict(kwargs["secure_payload"])
        self.state.invite_create_kwargs = dict(kwargs)
        return SimpleNamespace(
            token="T" * 48,
            record={
                "id": self.state.invite_id,
                "status": "created",
                "expires_at": "2026-08-01T00:00:00+00:00",
            },
        )

    def cancel_invite(self, token: str, **kwargs: Any) -> dict[str, Any]:
        self.state.cancelled.append({"token": token, **kwargs})
        return {
            "id": self.state.invite_id,
            "status": "cancelled",
            "progress": 0,
        }

    def enqueue_message(self, **kwargs: Any) -> dict[str, Any]:
        self.state.enqueued.append(dict(kwargs))
        return {"id": f"message-{len(self.state.enqueued)}"}

    def get_invite(self, token: str) -> dict[str, Any]:
        assert token == "T" * 48
        barrier = self.state.get_invite_barrier
        if barrier is not None:
            barrier.wait(timeout=5)
        return {
            "id": self.state.invite_id,
            "status": (
                "submitted"
                if self.state.invite_consumed
                else "opened"
            ),
            "secure_payload_ciphertext": "invite-ciphertext",
        }

    def decrypt_payload(self, ciphertext: Any) -> dict[str, Any]:
        if ciphertext == "invite-ciphertext":
            return dict(self.state.invite_payload)
        if isinstance(ciphertext, dict):
            payload = dict(ciphertext)
            self.state.decrypted_guidance_message_id = str(
                payload.get("guidance_message_id") or ""
            )
            return payload
        raise AssertionError(f"unexpected ciphertext marker: {ciphertext!r}")

    def consume_invite(self, token: str, **kwargs: Any) -> dict[str, Any]:
        with self.state.consume_lock:
            if (
                self.state.enforce_atomic_consume
                and self.state.invite_consumed
            ):
                raise ClaimRemoteRepositoryError(
                    "already consumed",
                    error_code="REMOTE_INVITE_ALREADY_CONSUMED",
                )
            self.state.invite_consumed = True
            self.state.events.append("consume")
            self.state.consumed.append({"token": token, **kwargs})
        return {
            "id": "33333333-3333-4333-8333-333333333333",
            "status": kwargs.get("initial_status", "queued"),
            "stage": kwargs["stage"],
        }

    def activate_reserved_job(
        self,
        job_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.state.events.append("activate")
        self.state.activated.append({"job_id": job_id, **kwargs})
        return {
            "id": job_id,
            "status": "queued",
            "stage": kwargs["stage"],
        }

    def fail_reserved_job(
        self,
        job_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.state.events.append("reservation_failed")
        return {
            "id": job_id,
            "status": "failed",
            "stage": "submission_failed",
        }

    def expire_due(self) -> dict[str, int]:
        return {"invites": 0, "jobs": 0, "messages": 0}

    def lease_jobs(self, worker_id: str, **kwargs: Any) -> list[dict[str, Any]]:
        rows = list(self.state.jobs)
        self.state.jobs.clear()
        return rows

    def lease_messages(
        self,
        worker_id: str,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        rows = list(self.state.messages)
        self.state.messages.clear()
        return rows

    def heartbeat_job(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.state.heartbeats.append({"args": args, **kwargs})
        return dict(kwargs)

    def check_job_active(
        self,
        job_id: str,
        *,
        mode: str,
        worker_id: str | None = None,
    ) -> dict[str, Any]:
        self.state.job_active_checks.append({
            "job_id": job_id,
            "owner_user_id": self.owner_user_id,
            "mode": mode,
            "worker_id": worker_id,
        })
        allowed = (
            self.state.job_active_sequence.pop(0)
            if self.state.job_active_sequence
            else self.state.job_active
        )
        return {
            "allowed": allowed,
            "code": "ACTIVE" if allowed else "JOB_CANCELLED",
            "job_id": job_id,
        }

    def release_job(
        self,
        job_id: str,
        worker_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.state.released_jobs.append(
            {"job_id": job_id, "worker_id": worker_id, **kwargs}
        )
        return dict(kwargs)

    def get_session_status(self, invite_id: str) -> dict[str, Any]:
        return {
            "invite_id": invite_id,
            "invite_status": (
                "submitted"
                if self.state.invite_consumed
                else "opened"
            ),
            "job_status": (
                "waiting" if self.state.invite_consumed else ""
            ),
            "job_stage": (
                "submission_reserved"
                if self.state.invite_consumed
                else ""
            ),
        }

    def release_message(
        self,
        message_id: str,
        worker_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self.state.released_messages.append(
            {"message_id": message_id, "worker_id": worker_id, **kwargs}
        )
        return dict(kwargs)

    def begin_guidance_dispatch(
        self,
        message_id: str,
        worker_id: str,
        *,
        canonical_contact_id: str,
        recipient_phone_hash: str,
    ) -> dict[str, Any]:
        self.state.guidance_dispatches.append({
            "message_id": message_id,
            "worker_id": worker_id,
            "canonical_contact_id": canonical_contact_id,
            "recipient_phone_hash": recipient_phone_hash,
        })
        return {
            "success": True,
            "code": "GUIDANCE_DISPATCH_STARTED",
            "message_id": self.state.decrypted_guidance_message_id,
        }


class FakeClaimRepository:
    def __init__(self, owner: str, state: FakeRemoteState):
        self.owner_user_id = owner
        self.state = state
        self.created: list[dict[str, Any]] = []
        self.updated: list[dict[str, Any]] = []
        self.audits: list[dict[str, Any]] = []

    def create_case(self, **kwargs: Any) -> dict[str, Any]:
        self.state.events.append("create_case")
        self.created.append(dict(kwargs))
        return {
            "id": kwargs.get("case_id") or self.state.case_id,
            "owner_user_id": self.owner_user_id,
        }

    def update_case_status(self, case_id: str, **kwargs: Any) -> dict[str, Any]:
        self.updated.append({"case_id": case_id, **kwargs})
        return dict(kwargs)

    def append_audit_event(self, **kwargs: Any) -> None:
        self.audits.append(dict(kwargs))

    def get_case(self, case_id: str) -> dict[str, Any]:
        return {
            "id": case_id,
            "owner_user_id": self.owner_user_id,
            "hometax_status": "auth_requested",
            "comwel_status": "request_ready",
            "overall_status": "auth_pending",
        }


class FakeTilkoClient:
    def __init__(self, state: FakeRemoteState) -> None:
        self.state = state
        self.hometax_requests: list[dict[str, Any]] = []

    def request_hometax_kakao(self, **kwargs: Any) -> dict[str, str]:
        self.state.events.append("hometax_auth")
        self.hometax_requests.append(dict(kwargs))
        return {
            "Token": "provider-token",
            "CxId": "cx",
            "TxId": "tx",
            "ReqTxId": "req",
        }


def build_service(
    state: FakeRemoteState,
    *,
    advance_case: Any = None,
    progress_reader: Any = None,
    solapi_client_factory: Any = None,
) -> tuple[ClaimRemoteService, dict[str, FakeClaimRepository], FakeTilkoClient]:
    claims: dict[str, FakeClaimRepository] = {}
    tilko = FakeTilkoClient(state)

    def remote_factory(owner: str) -> FakeRemoteRepository:
        return FakeRemoteRepository(owner, state)

    def claim_factory(owner: str) -> FakeClaimRepository:
        claims.setdefault(owner, FakeClaimRepository(owner, state))
        return claims[owner]

    service = ClaimRemoteService(
        remote_repository_factory=remote_factory,
        claim_repository_factory=claim_factory,
        tilko_client_factory=lambda: tilko,
        solapi_client_factory=solapi_client_factory
        or (lambda: pytest.fail("SOLAPI should not be called")),
        advance_case=advance_case
        or (lambda *args, **kwargs: {"event": "hometax_pending"}),
        progress_reader=progress_reader
        or (lambda repository, case_id: (0, "0%", 0, 10, True)),
        public_base_url="https://claim.example.test",
        invite_readiness_checker=lambda: {"ready": True},
        start_worker=False,
    )
    return service, claims, tilko


def test_staff_invite_only_stores_name_phone_and_queues_start_and_resume() -> None:
    state = FakeRemoteState()
    service, _claims, _tilko = build_service(state)

    result = service.create_staff_invite(
        owner_user_id="@".join(("OWNER", "EXAMPLE.COM")),
        requested_by="담당자",
        customer_name="홍길동",
        customer_phone="-".join(("010", "1234", "5678")),
    )

    assert result["message_queued"] is True
    assert state.invite_payload == {
        "recipient_name": "홍길동",
        "recipient_phone": "".join(("010", "1234", "5678")),
        "requested_by": "담당자",
    }
    assert [row["template_code"] for row in state.enqueued] == [
        TEMPLATE_AUTH_START,
        TEMPLATE_AUTH_RESUME,
    ]
    assert (
        state.enqueued[0]["secure_payload"]["variables"]["#{인증링크}"]
        == f"claim.example.test/c/{'T' * 48}"
    )
    assert "resident_number" not in state.invite_payload


def test_staff_invite_fails_closed_when_runtime_is_not_ready() -> None:
    state = FakeRemoteState()
    service, _claims, _tilko = build_service(state)
    service.invite_readiness_checker = lambda: {
        "ready": False,
        "missing_components": ["crypto", "solapi"],
    }

    with pytest.raises(ClaimRemoteServiceError) as raised:
        service.create_staff_invite(
            owner_user_id="@".join(("owner", "example.com")),
            requested_by="담당자",
            customer_name="홍길동",
            customer_phone="".join(("010", "1234", "5678")),
        )

    assert raised.value.error_code == "REMOTE_INVITE_NOT_READY"
    assert state.invite_payload == {}
    assert state.enqueued == []


def test_prospect_invite_has_seven_day_ttl_no_recipient_pii_or_outbox() -> None:
    state = FakeRemoteState()
    service, _claims, _tilko = build_service(state)
    before = datetime.now(timezone.utc)

    result = service.create_prospect_self_input_invite(
        owner_user_id="@".join(("owner", "example.com")),
        requested_by="staff-user-id",
        company_uid="company-uid-123",
        guidance_type="employment_support",
        guidance_message_id="55555555-5555-4555-8555-555555555555",
    )

    assert set(result) == {"invite_id", "invite_url", "expires_at", "status"}
    assert result["invite_url"] == f"https://claim.example.test/c/{'T' * 48}"
    assert state.enqueued == []
    assert state.invite_payload == {
        "requested_by": "staff-user-id",
        "company_uid": "company-uid-123",
        "guidance_type": "employment_support",
        "flow_type": PROSPECT_SELF_INPUT_FLOW,
        "customer_self_input": True,
        "enforce_recipient_match": False,
    }
    serialized = repr(state.invite_create_kwargs)
    assert "recipient_name" not in serialized
    assert "recipient_phone" not in serialized
    expires_at = state.invite_create_kwargs["expires_at"]
    assert PROSPECT_INVITE_TTL_SECONDS - 5 <= (
        expires_at - before
    ).total_seconds() <= PROSPECT_INVITE_TTL_SECONDS + 5


def test_prospect_submission_uses_customer_input_without_target_match() -> None:
    state = FakeRemoteState()
    state.invite_payload = {
        "requested_by": "staff-user-id",
        "company_uid": "company-uid-123",
        "guidance_type": "employment_support",
        "flow_type": PROSPECT_SELF_INPUT_FLOW,
        "customer_self_input": True,
        # A stale public DB contact must never be compared with the
        # representative's self-entered simple-auth identity.
        "recipient_name": "DB 공개 상호",
        "recipient_phone": "".join(("010", "1111", "2222")),
        "enforce_recipient_match": True,
    }
    service, claims, tilko = build_service(state)
    before = datetime.now(timezone.utc)

    service.submit_customer(
        owner_ref="@".join(("owner", "example.com")),
        invite_id=state.invite_id,
        invite_token="T" * 48,
        name="고객입력",
        phone="-".join(("010", "9999", "8888")),
        resident_number="".join(("900101", "1", "234567")),
        company_name="고객 상호",
        business_no="-".join(("123", "45", "67890")),
        consent_version="consent-v1",
        consents={
            "privacy_and_unique_identifier": True,
            "third_party_processing": True,
        },
    )

    assert tilko.hometax_requests[0]["user_name"] == "고객입력"
    assert tilko.hometax_requests[0]["cellphone"] == "".join(("010", "9999", "8888"))
    reservation = state.consumed[0]
    assert reservation["secure_job_payload"]["auth_context"]["cellphone"] == (
        "".join(("010", "9999", "8888"))
    )
    assert reservation["secure_job_payload"]["flow_type"] == (
        PROSPECT_SELF_INPUT_FLOW
    )
    sensitive_expires_at = reservation["sensitive_expires_at"]
    assert REMOTE_AUTH_TTL_SECONDS - 5 <= (
        sensitive_expires_at - before
    ).total_seconds() <= REMOTE_AUTH_TTL_SECONDS + 5
    assert sensitive_expires_at < reservation["hard_expires_at"]
    non_secure_reservation = {
        key: value
        for key, value in reservation.items()
        if key != "secure_job_payload"
    }
    assert "".join(("010", "9999", "8888")) not in repr(non_secure_reservation)
    assert "".join(("010", "1111", "2222")) not in repr(reservation["secure_job_payload"])
    created = claims["@".join(("owner", "example.com"))].created[0]
    assert created["company_name"] == "고객 상호"
    assert created["business_no"] == "".join(("123", "45", "67890"))
    assert created["cellphone"] == ""


def test_customer_opt_out_cancels_invite_without_provider_call() -> None:
    state = FakeRemoteState()
    state.invite_payload = {"flow_type": PROSPECT_SELF_INPUT_FLOW}
    service, _claims, tilko = build_service(state)

    result = service.cancel_customer(
        owner_ref="@".join(("owner", "example.com")),
        invite_id=state.invite_id,
        invite_token="T" * 48,
    )

    assert result["status"] == "cancelled"
    assert result["complete"] is True
    assert state.cancelled == [
        {"token": "T" * 48, "reason": "customer_opt_out"}
    ]
    assert tilko.hometax_requests == []


def test_customer_submit_requests_hometax_and_consumes_invite_into_job() -> None:
    state = FakeRemoteState()
    state.invite_payload = {
        "recipient_name": "홍길동",
        "recipient_phone": "".join(("010", "1234", "5678")),
        "requested_by": "오아시스 담당자",
    }
    service, claims, tilko = build_service(state)

    result = service.submit_customer(
        owner_ref="@".join(("owner", "example.com")),
        invite_id=state.invite_id,
        invite_token="T" * 48,
        name="홍길동",
        phone="-".join(("010", "1234", "5678")),
        resident_number="".join(("900101", "1", "234567")),
        consent_version="consent-v1",
        consents={
            "privacy_and_unique_identifier": True,
            "third_party_processing": True,
        },
    )

    assert result["submitted"] is True
    assert tilko.hometax_requests == [
        {
            "birth_date": "19900101",
            "user_name": "홍길동",
            "cellphone": "".join(("010", "1234", "5678")),
        }
    ]
    assert claims["@".join(("owner", "example.com"))].created[0]["consent_channel"] == (
        "customer_public_page"
    )
    reservation = state.consumed[0]
    assert reservation["initial_status"] == "waiting"
    assert reservation["stage"] == "submission_reserved"
    assert state.events[:3] == [
        "consume",
        "create_case",
        "hometax_auth",
    ]
    durable = state.activated[0]["secure_payload"]
    assert durable["hometax"]["Token"] == "provider-token"
    assert durable["auth_context"]["identity_number"] == "".join(("900101", "1", "234567"))
    assert state.consumed[0]["max_attempts"] == 100


def test_customer_cancelled_after_reservation_never_calls_hometax() -> None:
    state = FakeRemoteState()
    state.invite_payload = {
        "recipient_name": "홍길동",
        "recipient_phone": "".join(("010", "1234", "5678")),
        "requested_by": "담당자",
    }
    # Simulates cancel_invite terminalizing and scrubbing the waiting
    # submission reservation after consume_invite, while case creation is in
    # flight, but before the synchronous provider boundary.
    state.job_active = False
    service, claims, tilko = build_service(state)

    with pytest.raises(ClaimRemoteServiceError) as raised:
        service.submit_customer(
            owner_ref="@".join(("owner", "example.com")),
            invite_id=state.invite_id,
            invite_token="T" * 48,
            name="홍길동",
            phone="".join(("010", "1234", "5678")),
            resident_number="".join(("900101", "1", "234567")),
            consent_version="consent-v1",
            consents={
                "privacy_and_unique_identifier": True,
                "third_party_processing": True,
            },
        )

    assert raised.value.error_code == "REMOTE_JOB_NO_LONGER_ACTIVE"
    assert len(state.consumed) == 1
    assert len(claims["@".join(("owner", "example.com"))].created) == 1
    assert state.job_active_checks == [{
        "job_id": "33333333-3333-4333-8333-333333333333",
        "owner_user_id": "@".join(("owner", "example.com")),
        "mode": "submission_reserved",
        "worker_id": None,
    }]
    assert tilko.hometax_requests == []
    assert state.activated == []
    assert "reservation_failed" not in state.events


@pytest.mark.parametrize(
    ("submitted_name", "submitted_phone"),
    [
        ("다른사람", "".join(("010", "1234", "5678"))),
        ("홍길동", "".join(("010", "9999", "8888"))),
    ],
)
def test_customer_submit_rejects_invite_target_mismatch_before_side_effects(
    submitted_name: str,
    submitted_phone: str,
) -> None:
    state = FakeRemoteState()
    state.invite_payload = {
        "recipient_name": "홍길동",
        "recipient_phone": "".join(("010", "1234", "5678")),
        "requested_by": "담당자",
    }
    service, claims, tilko = build_service(state)

    with pytest.raises(ClaimRemoteServiceError) as raised:
        service.submit_customer(
            owner_ref="@".join(("owner", "example.com")),
            invite_id=state.invite_id,
            invite_token="T" * 48,
            name=submitted_name,
            phone=submitted_phone,
            resident_number="".join(("900101", "1", "234567")),
            consent_version="consent-v1",
            consents={
                "privacy_and_unique_identifier": True,
                "third_party_processing": True,
            },
        )

    assert raised.value.error_code == "INVITE_TARGET_MISMATCH"
    assert state.consumed == []
    assert claims == {}
    assert tilko.hometax_requests == []


def test_concurrent_duplicate_submit_dispatches_external_auth_once() -> None:
    state = FakeRemoteState()
    state.invite_payload = {
        "recipient_name": "홍길동",
        "recipient_phone": "".join(("010", "1234", "5678")),
        "requested_by": "담당자",
    }
    state.enforce_atomic_consume = True
    state.get_invite_barrier = threading.Barrier(2)
    service, claims, tilko = build_service(state)

    def submit() -> Mapping[str, Any]:
        return service.submit_customer(
            owner_ref="@".join(("owner", "example.com")),
            invite_id=state.invite_id,
            invite_token="T" * 48,
            name="홍길동",
            phone="".join(("010", "1234", "5678")),
            resident_number="".join(("900101", "1", "234567")),
            consent_version="consent-v1",
            consents={
                "privacy_and_unique_identifier": True,
                "third_party_processing": True,
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: submit(), range(2)))

    assert all(result["submitted"] is True for result in results)
    assert len(state.consumed) == 1
    assert len(tilko.hometax_requests) == 1
    assert len(claims["@".join(("owner", "example.com"))].created) == 1


def test_remote_invite_readiness_fails_closed_without_required_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    required = {
        "CLAIM_PUBLIC_BASE_URL": "https://claim.example.test",
        "CLAIM_JOB_ENCRYPTION_KEY": "e" * 48,
        "CLAIM_LINK_PEPPER": "p" * 48,
        "CLAIM_SESSION_SECRET": "s" * 48,
        "SOLAPI_API_KEY": "api-key",
        "SOLAPI_API_SECRET": "api-secret",
        "SOLAPI_KAKAO_CHANNEL_ID": "channel",
        **{
            environment_name: f"template-{index}"
            for index, environment_name in enumerate(
                TEMPLATE_ENV_BY_CODE.values(),
                start=1,
            )
        },
    }
    for name in required:
        monkeypatch.delenv(name, raising=False)

    missing = remote_invite_environment_readiness()
    assert missing["ready"] is False
    assert "public_url" in missing["missing_components"]
    assert "solapi" in missing["missing_components"]
    assert missing["variant_key_ready"] is False
    assert "variant_key" in missing["missing_components"]

    monkeypatch.setenv("CLAIM_DOCUMENT_VARIANT_KEY", "too-short")
    short_key = remote_invite_environment_readiness()
    assert short_key["variant_key_ready"] is False
    assert "variant_key" in short_key["missing_components"]
    monkeypatch.delenv("CLAIM_DOCUMENT_VARIANT_KEY", raising=False)

    for name, value in required.items():
        monkeypatch.setenv(name, value)
    ready = remote_invite_environment_readiness()
    assert ready["ready"] is True
    assert ready["variant_key_ready"] is True
    serialized = repr(ready)
    for value in required.values():
        assert value not in serialized


def test_new_service_instance_resumes_leased_job_without_memory_state() -> None:
    state = FakeRemoteState()
    state.jobs.append(
        {
            "id": "33333333-3333-4333-8333-333333333333",
            "owner_user_id": "@".join(("owner", "example.com")),
            "invite_id": state.invite_id,
            "case_id": state.case_id,
            "secure_payload_ciphertext": {
                "expires_at": 4_000_000_000.0,
                "absolute_expires_at": 4_000_000_000.0,
                "invite_url": "https://claim.example.test/c/token",
                "hometax": {"Token": "provider-token"},
                "auth_context": {
                    "representative": "홍길동",
                    "cellphone": "".join(("010", "1234", "5678")),
                    "birth_date": "19900101",
                    "identity_number": "".join(("900101", "1", "234567")),
                },
            },
            "attempt_count": 1,
            "max_attempts": 100,
            "progress": 0,
        }
    )

    def advance(
        repository: Any,
        client: Any,
        *,
        transient: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        transient["comwel"] = {"Token": "comwel-token"}
        return {"event": "comwel_requested"}

    # This service represents a fresh Railway process. All state comes from
    # the leased database row rather than the previous process's memory.
    service, _claims, _tilko = build_service(state, advance_case=advance)
    result = service.run_once()

    assert result["jobs"] == 1
    assert state.released_jobs[-1]["next_status"] == "waiting"
    assert state.released_jobs[-1]["stage"] == "comwel_pending"
    assert state.released_jobs[-1]["secure_payload"]["comwel"]["Token"] == (
        "comwel-token"
    )
    assert state.enqueued[-1]["template_code"] == TEMPLATE_NEXT_AUTH
    assert (
        state.enqueued[-1]["secure_payload"]["variables"]["#{인증링크}"]
        == "claim.example.test/c/token"
    )


def test_worker_cancelled_after_decrypt_never_crosses_provider_boundary() -> None:
    state = FakeRemoteState()
    job_id = "33333333-3333-4333-8333-333333333333"
    state.jobs.append(
        {
            "id": job_id,
            "owner_user_id": "@".join(("owner", "example.com")),
            "invite_id": state.invite_id,
            "case_id": state.case_id,
            "stage": "hometax_pending",
            "secure_payload_ciphertext": {
                "expires_at": 4_000_000_000.0,
                "absolute_expires_at": 4_000_000_000.0,
                "auth_context": {
                    "representative": "홍길동",
                    "cellphone": "".join(("010", "1234", "5678")),
                    "birth_date": "19900101",
                    "identity_number": "".join(
                        ("900101", "1", "234567")
                    ),
                },
            },
            "attempt_count": 1,
            "max_attempts": 100,
            "progress": 0,
        }
    )
    # The first check follows decryption, the second precedes orchestration,
    # and cancellation becomes visible at the actual Tilko method boundary.
    state.job_active_sequence = [True, True, False]

    def advance(
        repository: Any,
        client: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client.request_hometax_kakao(
            birth_date="19900101",
            user_name="홍길동",
            cellphone="".join(("010", "1234", "5678")),
        )
        pytest.fail("cancelled job must not return from provider boundary")

    service, _claims, tilko = build_service(state, advance_case=advance)

    result = service.run_once()

    assert result["jobs"] == 1
    assert [check["mode"] for check in state.job_active_checks] == [
        "leased",
        "leased",
        "leased",
    ]
    assert all(
        check["worker_id"] == service.worker_id
        for check in state.job_active_checks
    )
    assert tilko.hometax_requests == []
    assert "hometax_auth" not in state.events
    assert state.released_jobs == []
    assert state.enqueued == []


def test_collection_stage_heartbeats_before_long_document_calls() -> None:
    state = FakeRemoteState()
    state.jobs.append(
        {
            "id": "33333333-3333-4333-8333-333333333333",
            "owner_user_id": "@".join(("owner", "example.com")),
            "invite_id": state.invite_id,
            "case_id": state.case_id,
            "stage": "comwel_pending",
            "secure_payload_ciphertext": {
                "expires_at": 4_000_000_000.0,
                "absolute_expires_at": 4_000_000_000.0,
                "auth_context": {
                    "representative": "representative",
                    "cellphone": "".join(("010", "9999", "8888")),
                    "birth_date": "19900101",
                    "identity_number": "".join(("900101", "1", "234567")),
                },
            },
            "attempt_count": 1,
            "max_attempts": 100,
            "progress": 0,
        }
    )

    def advance(
        repository: Any,
        client: Any,
        *,
        transient: dict[str, Any],
        on_progress: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        transient["expires_at"] = (
            remote_service_module.time.time() + 45 * 60
        )
        on_progress(0, 1, "collection_preparing")
        return {"event": "collection_complete"}

    service, _claims, _tilko = build_service(
        state,
        advance_case=advance,
        progress_reader=lambda repository, case_id: (
            100,
            "100%",
            10,
            10,
            True,
        ),
    )
    before = datetime.now(timezone.utc)

    service.run_once()

    assert state.heartbeats
    heartbeat = state.heartbeats[0]
    assert heartbeat["stage"] == "collecting"
    assert heartbeat["sensitive_expires_at"] > before + timedelta(minutes=10)
    assert state.released_jobs[-1]["next_status"] == "complete"


def test_prospect_auth_phone_outbox_is_short_lived_and_not_reused_after_auth() -> None:
    state = FakeRemoteState()
    service, _claims, _tilko = build_service(state)
    transient = {
        "flow_type": PROSPECT_SELF_INPUT_FLOW,
        "invite_url": "https://claim.example.test/c/token",
        "auth_context": {
            "representative": "고객입력",
            "cellphone": "".join(("010", "9999", "8888")),
        },
    }
    row = {
        "invite_id": state.invite_id,
        "case_id": state.case_id,
    }
    before = datetime.now(timezone.utc)

    service._enqueue_case_message(
        FakeRemoteRepository("@".join(("owner", "example.com")), state),
        row=row,
        transient=transient,
        template_code=TEMPLATE_NEXT_AUTH,
        event_type="NEXT_AUTH",
    )

    assert len(state.enqueued) == 1
    expiry = state.enqueued[0]["expires_at"]
    assert before < expiry <= before + timedelta(minutes=11)

    service._enqueue_case_message(
        FakeRemoteRepository("@".join(("owner", "example.com")), state),
        row=row,
        transient=transient,
        template_code="complete",
        event_type="COLLECTION_COMPLETE",
    )
    assert len(state.enqueued) == 1


@dataclass
class FakeSendResult:
    message_id: str = "solapi-message"
    group_id: str = "solapi-group"


class FakeSolapi:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_alimtalk(
        self,
        to: str,
        template_id: str,
        **kwargs: Any,
    ) -> FakeSendResult:
        self.sent.append(
            {"to": to, "template_id": template_id, **kwargs}
        )
        return FakeSendResult()


GUIDANCE_CONTACT_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
GUIDANCE_PHONE_HASH_KEY = "guidance-phone-hash-key-for-tests"


def test_outbox_sends_approved_template_and_clears_terminal_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = FakeRemoteState()
    solapi = FakeSolapi()
    service, _claims, _tilko = build_service(
        state,
        solapi_client_factory=lambda: solapi,
    )
    monkeypatch.setenv("SOLAPI_TEMPLATE_AUTH_START_ID", "approved-template")
    row = {
        "id": "44444444-4444-4444-8444-444444444444",
        "owner_user_id": "@".join(("owner", "example.com")),
        "invite_id": state.invite_id,
        "case_id": None,
        "template_code": TEMPLATE_AUTH_START,
        "secure_payload_ciphertext": {
            "to": "".join(("010", "1234", "5678")),
            "variables": {
                "#{고객명}": "홍길동",
                "#{인증링크}": "https://claim.example.test/c/token",
            },
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    assert solapi.sent[0]["template_id"] == "approved-template"
    assert solapi.sent[0]["disable_sms"] is True
    assert state.released_messages[-1]["next_status"] == "sent"
    assert "secure_payload" not in state.released_messages[-1]


def test_guidance_mock_outbox_never_calls_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = FakeRemoteState()
    service, _claims, _tilko = build_service(state)
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_PROVIDER_MODE", "mock")
    monkeypatch.setenv(
        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY",
        GUIDANCE_PHONE_HASH_KEY,
    )
    monkeypatch.delenv("OASIS_KAKAO_GUIDANCE_SEND_ENABLED", raising=False)
    row = {
        "id": "66666666-6666-4666-8666-666666666666",
        "guidance_message_id": "77777777-7777-4777-8777-777777777777",
        "owner_user_id": "@".join(("owner", "example.com")),
        "event_type": "GUIDANCE_EMPLOYMENT_SUPPORT",
        "template_code": "GUIDANCE_EMPLOYMENT_SUPPORT",
        "secure_payload_ciphertext": {
            "to": "".join(("010", "1234", "5678")),
            "variables": {"#{검토신청링크}": "https://claim.example.test/c/token"},
            "guidance_message_id": "77777777-7777-4777-8777-777777777777",
            "canonical_contact_id": GUIDANCE_CONTACT_ID,
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    released = state.released_messages[-1]
    assert released["next_status"] == "cancelled"
    assert released["safe_error_code"] == "GUIDANCE_PROVIDER_MODE_INVALID"


def test_guidance_live_disabled_is_blocked_without_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = FakeRemoteState()
    service, _claims, _tilko = build_service(state)
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_PROVIDER_MODE", "live")
    monkeypatch.setenv(
        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY",
        GUIDANCE_PHONE_HASH_KEY,
    )
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_SEND_ENABLED", "false")
    row = {
        "id": "88888888-8888-4888-8888-888888888888",
        "guidance_message_id": "99999999-9999-4999-8999-999999999999",
        "owner_user_id": "@".join(("owner", "example.com")),
        "event_type": "GUIDANCE_POLICY_FUNDING",
        "template_code": "GUIDANCE_POLICY_FUNDING",
        "secure_payload_ciphertext": {
            "to": "".join(("010", "1234", "5678")),
            "variables": {"#{검토신청링크}": "https://claim.example.test/c/token"},
            "guidance_message_id": "99999999-9999-4999-8999-999999999999",
            "canonical_contact_id": GUIDANCE_CONTACT_ID,
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    released = state.released_messages[-1]
    assert released["next_status"] == "cancelled"
    assert released["safe_error_code"] == "GUIDANCE_SEND_DISABLED"


def test_guidance_live_enabled_uses_approved_guidance_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = FakeRemoteState()
    solapi = FakeSolapi()
    service, _claims, _tilko = build_service(
        state,
        solapi_client_factory=lambda: solapi,
    )
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_PROVIDER_MODE", "live")
    monkeypatch.setenv(
        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY",
        GUIDANCE_PHONE_HASH_KEY,
    )
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_SEND_ENABLED", "true")
    monkeypatch.setenv("SOLAPI_API_KEY", "configured-test-key")
    monkeypatch.setenv("SOLAPI_API_SECRET", "configured-test-secret")
    monkeypatch.setenv("SOLAPI_KAKAO_CHANNEL_ID", "configured-test-channel")
    monkeypatch.setenv(
        "SOLAPI_TEMPLATE_GUIDANCE_TAX_CREDIT_ID",
        "approved-guidance-template",
    )
    send_ready_calls: list[tuple[str, str, str]] = []

    def send_ready(
        message_id: str,
        contact_id: str,
        phone_hash: str,
    ) -> dict[str, Any]:
        send_ready_calls.append((message_id, contact_id, phone_hash))
        return {"allowed": True, "code": "READY"}

    monkeypatch.setattr(
        remote_service_module,
        "_check_guidance_send_ready",
        send_ready,
    )
    row = {
        "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "guidance_message_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "owner_user_id": "@".join(("owner", "example.com")),
        "event_type": "GUIDANCE_TAX_CREDIT",
        "template_code": "GUIDANCE_TAX_CREDIT",
        "secure_payload_ciphertext": {
            "to": "".join(("010", "1234", "5678")),
            "variables": {"#{검토신청링크}": "https://claim.example.test/c/token"},
            "guidance_message_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "canonical_contact_id": GUIDANCE_CONTACT_ID,
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    assert solapi.sent[0]["template_id"] == "approved-guidance-template"
    assert solapi.sent[0]["disable_sms"] is True
    expected_hash = hmac.new(
        GUIDANCE_PHONE_HASH_KEY.encode("utf-8"),
        "".join(("010", "1234", "5678")).encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    assert send_ready_calls == [(
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        GUIDANCE_CONTACT_ID,
        expected_hash,
    )]
    assert state.guidance_dispatches == [{
        "message_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "worker_id": service.worker_id,
        "canonical_contact_id": GUIDANCE_CONTACT_ID,
        "recipient_phone_hash": expected_hash,
    }]
    assert state.released_messages[-1]["next_status"] == "sent"


def test_guidance_cancelled_after_lease_never_calls_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = FakeRemoteState()
    solapi = FakeSolapi()
    service, _claims, _tilko = build_service(
        state,
        solapi_client_factory=lambda: solapi,
    )
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_PROVIDER_MODE", "live")
    monkeypatch.setenv(
        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY",
        GUIDANCE_PHONE_HASH_KEY,
    )
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_SEND_ENABLED", "true")
    monkeypatch.setenv("SOLAPI_API_KEY", "configured-test-key")
    monkeypatch.setenv("SOLAPI_API_SECRET", "configured-test-secret")
    monkeypatch.setenv("SOLAPI_KAKAO_CHANNEL_ID", "configured-test-channel")
    monkeypatch.setenv(
        "SOLAPI_TEMPLATE_GUIDANCE_EMPLOYMENT_SUPPORT_ID",
        "approved-guidance-template",
    )
    monkeypatch.setattr(
        remote_service_module,
        "_check_guidance_send_ready",
        lambda _message_id, _contact_id, _phone_hash: {
            "allowed": False,
            "code": "GUIDANCE_CANCELLED",
        },
    )
    row = {
        "id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "guidance_message_id": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
        "owner_user_id": "@".join(("owner", "example.com")),
        "event_type": "GUIDANCE_EMPLOYMENT_SUPPORT",
        "template_code": "GUIDANCE_EMPLOYMENT_SUPPORT",
        "secure_payload_ciphertext": {
            "to": "".join(("010", "1234", "5678")),
            "variables": {
                "#{검토신청링크}": "https://claim.example.test/c/token"
            },
            "guidance_message_id": (
                "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
            ),
            "canonical_contact_id": GUIDANCE_CONTACT_ID,
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    assert solapi.sent == []
    released = state.released_messages[-1]
    assert released["next_status"] == "cancelled"
    assert released["safe_error_code"] == "GUIDANCE_CANCELLED"
    assert "secure_payload" not in released


def test_guidance_encrypted_message_id_cannot_override_clear_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = FakeRemoteState()
    solapi = FakeSolapi()
    service, _claims, _tilko = build_service(
        state,
        solapi_client_factory=lambda: solapi,
    )
    monkeypatch.setenv(
        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY",
        GUIDANCE_PHONE_HASH_KEY,
    )
    monkeypatch.setattr(
        remote_service_module,
        "_check_guidance_send_ready",
        lambda *_args: pytest.fail("binding mismatch must not reach send gate"),
    )
    row = {
        "id": "12121212-1212-4121-8121-121212121212",
        "guidance_message_id": "13131313-1313-4131-8131-131313131313",
        "owner_user_id": "@".join(("owner", "example.com")),
        "event_type": "GUIDANCE_POLICY_FUNDING",
        "template_code": "GUIDANCE_POLICY_FUNDING",
        "secure_payload_ciphertext": {
            "to": "".join(("010", "1234", "5678")),
            "variables": {},
            "guidance_message_id": "14141414-1414-4141-8141-141414141414",
            "canonical_contact_id": GUIDANCE_CONTACT_ID,
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    assert solapi.sent == []
    assert state.guidance_dispatches == []
    released = state.released_messages[-1]
    assert released["next_status"] == "cancelled"
    assert released["safe_error_code"] == "GUIDANCE_DELIVERY_BINDING_INVALID"
    assert "secure_payload" not in released


def test_guidance_missing_phone_hash_key_fails_closed_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = FakeRemoteState()
    solapi = FakeSolapi()
    service, _claims, _tilko = build_service(
        state,
        solapi_client_factory=lambda: solapi,
    )
    monkeypatch.delenv(
        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY",
        raising=False,
    )
    guidance_message_id = "15151515-1515-4151-8151-151515151515"
    row = {
        "id": "16161616-1616-4161-8161-161616161616",
        "guidance_message_id": guidance_message_id,
        "owner_user_id": "@".join(("owner", "example.com")),
        "event_type": "GUIDANCE_TAX_CREDIT",
        "template_code": "GUIDANCE_TAX_CREDIT",
        "secure_payload_ciphertext": {
            "to": "".join(("010", "1234", "5678")),
            "variables": {},
            "guidance_message_id": guidance_message_id,
            "canonical_contact_id": GUIDANCE_CONTACT_ID,
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    assert solapi.sent == []
    assert state.guidance_dispatches == []
    released = state.released_messages[-1]
    assert released["next_status"] == "cancelled"
    assert released["safe_error_code"] == "GUIDANCE_PHONE_HASH_KEY_INVALID"
    assert "secure_payload" not in released


@pytest.mark.parametrize("begin_raises", [False, True])
def test_guidance_dispatch_begin_failure_never_calls_provider(
    monkeypatch: pytest.MonkeyPatch,
    begin_raises: bool,
) -> None:
    state = FakeRemoteState()
    solapi = FakeSolapi()
    service, _claims, _tilko = build_service(
        state,
        solapi_client_factory=lambda: solapi,
    )
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_PROVIDER_MODE", "live")
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_SEND_ENABLED", "true")
    monkeypatch.setenv(
        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY",
        GUIDANCE_PHONE_HASH_KEY,
    )
    monkeypatch.setenv("SOLAPI_API_KEY", "configured-test-key")
    monkeypatch.setenv("SOLAPI_API_SECRET", "configured-test-secret")
    monkeypatch.setenv("SOLAPI_KAKAO_CHANNEL_ID", "configured-test-channel")
    monkeypatch.setenv(
        "SOLAPI_TEMPLATE_GUIDANCE_TAX_CREDIT_ID",
        "approved-guidance-template",
    )
    monkeypatch.setattr(
        remote_service_module,
        "_check_guidance_send_ready",
        lambda *_args: {"allowed": True, "code": "READY"},
    )
    def begin_guidance_dispatch(
        *_args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if begin_raises:
            raise RuntimeError("dispatch state unavailable")
        return {
            "success": False,
            "code": "GUIDANCE_DISPATCH_REJECTED",
            "message_id": "",
        }

    monkeypatch.setattr(
        FakeRemoteRepository,
        "begin_guidance_dispatch",
        begin_guidance_dispatch,
    )
    guidance_message_id = "17171717-1717-4171-8171-171717171717"
    row = {
        "id": "18181818-1818-4181-8181-181818181818",
        "guidance_message_id": guidance_message_id,
        "owner_user_id": "@".join(("owner", "example.com")),
        "event_type": "GUIDANCE_TAX_CREDIT",
        "template_code": "GUIDANCE_TAX_CREDIT",
        "secure_payload_ciphertext": {
            "to": "".join(("010", "1234", "5678")),
            "variables": {},
            "guidance_message_id": guidance_message_id,
            "canonical_contact_id": GUIDANCE_CONTACT_ID,
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    assert solapi.sent == []
    released = state.released_messages[-1]
    assert released["next_status"] == "cancelled"
    assert released["safe_error_code"] == "GUIDANCE_DISPATCH_NOT_STARTED"
    assert "secure_payload" not in released


def test_guidance_ambiguous_provider_outcome_is_terminal_and_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AmbiguousSolapi(FakeSolapi):
        def send_alimtalk(
            self,
            to: str,
            template_id: str,
            **kwargs: Any,
        ) -> FakeSendResult:
            self.sent.append({"to": to, "template_id": template_id, **kwargs})
            raise remote_service_module.SolapiAlimtalkError(
                "TIMEOUT",
                "provider result is unknown",
            )

    state = FakeRemoteState()
    solapi = AmbiguousSolapi()
    service, _claims, _tilko = build_service(
        state,
        solapi_client_factory=lambda: solapi,
    )
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_PROVIDER_MODE", "live")
    monkeypatch.setenv("OASIS_KAKAO_GUIDANCE_SEND_ENABLED", "true")
    monkeypatch.setenv(
        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY",
        GUIDANCE_PHONE_HASH_KEY,
    )
    monkeypatch.setenv("SOLAPI_API_KEY", "configured-test-key")
    monkeypatch.setenv("SOLAPI_API_SECRET", "configured-test-secret")
    monkeypatch.setenv("SOLAPI_KAKAO_CHANNEL_ID", "configured-test-channel")
    monkeypatch.setenv(
        "SOLAPI_TEMPLATE_GUIDANCE_EMPLOYMENT_SUPPORT_ID",
        "approved-guidance-template",
    )
    monkeypatch.setattr(
        remote_service_module,
        "_check_guidance_send_ready",
        lambda *_args: {"allowed": True, "code": "READY"},
    )
    guidance_message_id = "19191919-1919-4191-8191-191919191919"
    row = {
        "id": "20202020-2020-4202-8202-202020202020",
        "guidance_message_id": guidance_message_id,
        "owner_user_id": "@".join(("owner", "example.com")),
        "event_type": "GUIDANCE_EMPLOYMENT_SUPPORT",
        "template_code": "GUIDANCE_EMPLOYMENT_SUPPORT",
        "secure_payload_ciphertext": {
            "to": "".join(("010", "1234", "5678")),
            "variables": {},
            "guidance_message_id": guidance_message_id,
            "canonical_contact_id": GUIDANCE_CONTACT_ID,
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    assert len(solapi.sent) == 1
    assert len(state.guidance_dispatches) == 1
    released = state.released_messages[-1]
    assert released["next_status"] == "failed"
    assert released["secure_payload"] is None
    assert released["safe_error_code"] == "GUIDANCE_PROVIDER_OUTCOME_UNKNOWN"


def test_non_guidance_timeout_keeps_existing_retry_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimeoutSolapi(FakeSolapi):
        def send_alimtalk(
            self,
            to: str,
            template_id: str,
            **kwargs: Any,
        ) -> FakeSendResult:
            self.sent.append({"to": to, "template_id": template_id, **kwargs})
            raise remote_service_module.SolapiAlimtalkError(
                "TIMEOUT",
                "provider result is unknown",
            )

    state = FakeRemoteState()
    solapi = TimeoutSolapi()
    service, _claims, _tilko = build_service(
        state,
        solapi_client_factory=lambda: solapi,
    )
    monkeypatch.setenv("SOLAPI_TEMPLATE_AUTH_START_ID", "approved-template")
    payload = {
        "to": "".join(("010", "1234", "5678")),
        "variables": {"#{인증링크}": "https://claim.example.test/c/token"},
    }
    row = {
        "id": "22222222-2222-4222-8222-222222222223",
        "owner_user_id": "@".join(("owner", "example.com")),
        "invite_id": state.invite_id,
        "event_type": "AUTH_START",
        "template_code": TEMPLATE_AUTH_START,
        "secure_payload_ciphertext": payload,
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    assert len(solapi.sent) == 1
    released = state.released_messages[-1]
    assert released["next_status"] == "retry"
    assert released["secure_payload"] == payload
    assert released["safe_error_code"] == "TIMEOUT"


def test_terminal_next_auth_session_never_calls_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = FakeRemoteState()
    solapi = FakeSolapi()
    service, _claims, _tilko = build_service(
        state,
        solapi_client_factory=lambda: solapi,
    )
    monkeypatch.setattr(
        FakeRemoteRepository,
        "get_session_status",
        lambda _repository, invite_id: {
            "invite_id": invite_id,
            "invite_status": "submitted",
            "job_status": "complete",
            "job_stage": "complete",
        },
    )
    row = {
        "id": "21212121-2121-4212-8212-212121212121",
        "owner_user_id": "@".join(("owner", "example.com")),
        "invite_id": state.invite_id,
        "case_id": state.case_id,
        "event_type": "NEXT_AUTH",
        "template_code": TEMPLATE_NEXT_AUTH,
        "secure_payload_ciphertext": {
            "to": "".join(("010", "9999", "8888")),
            "variables": {"#{인증링크}": "https://claim.example.test/c/token"},
        },
        "attempt_count": 1,
        "max_attempts": 8,
    }

    service._process_outbox(row)

    assert solapi.sent == []
    released = state.released_messages[-1]
    assert released["next_status"] == "cancelled"
    assert "secure_payload" not in released


def test_task_automation_startup_is_independent_from_claim_worker() -> None:
    source = Path(remote_service_module.__file__).read_text(encoding="utf-8")

    assert "if task_automation_enabled:" in source
    assert "if enabled and task_automation_enabled:" not in source


def test_resume_exchange_allows_submitted_but_consume_stays_protected() -> None:
    root = Path(__file__).resolve().parents[1]
    follow_up = (
        root / "supabase_v1027_claim_remote_resume_exchange.sql"
    ).read_text(encoding="utf-8")
    original = (
        root / "supabase_v1026_claim_remote_invites.sql"
    ).read_text(encoding="utf-8")

    assert follow_up.count("if v_invite.status = 'submitted' then") == 2
    assert "Read-only session restoration" in follow_up
    assert (
        "v_invite.status = 'submitted' or v_invite.consumed_at is not null"
        in original
    )
    assert "raise exception 'REMOTE_INVITE_ALREADY_CONSUMED'" in original
