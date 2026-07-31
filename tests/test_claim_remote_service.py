from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from claim_remote_repository import ClaimRemoteRepositoryError
from claim_remote_service import (
    ClaimRemoteService,
    ClaimRemoteServiceError,
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
        self.enqueued: list[dict[str, Any]] = []
        self.consumed: list[dict[str, Any]] = []
        self.activated: list[dict[str, Any]] = []
        self.released_jobs: list[dict[str, Any]] = []
        self.released_messages: list[dict[str, Any]] = []
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
        return SimpleNamespace(
            token="T" * 48,
            record={
                "id": self.state.invite_id,
                "status": "created",
                "expires_at": "2026-08-01T00:00:00+00:00",
            },
        )

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
            return dict(ciphertext)
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
        return {}

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
        owner_user_id="OWNER@EXAMPLE.COM",
        requested_by="담당자",
        customer_name="홍길동",
        customer_phone="010-1234-5678",
    )

    assert result["message_queued"] is True
    assert state.invite_payload == {
        "recipient_name": "홍길동",
        "recipient_phone": "01012345678",
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
            owner_user_id="owner@example.com",
            requested_by="담당자",
            customer_name="홍길동",
            customer_phone="01012345678",
        )

    assert raised.value.error_code == "REMOTE_INVITE_NOT_READY"
    assert state.invite_payload == {}
    assert state.enqueued == []


def test_customer_submit_requests_hometax_and_consumes_invite_into_job() -> None:
    state = FakeRemoteState()
    state.invite_payload = {
        "recipient_name": "홍길동",
        "recipient_phone": "01012345678",
        "requested_by": "오아시스 담당자",
    }
    service, claims, tilko = build_service(state)

    result = service.submit_customer(
        owner_ref="owner@example.com",
        invite_id=state.invite_id,
        invite_token="T" * 48,
        name="홍길동",
        phone="010-1234-5678",
        resident_number="9001011234567",
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
            "cellphone": "01012345678",
        }
    ]
    assert claims["owner@example.com"].created[0]["consent_channel"] == (
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
    assert durable["auth_context"]["identity_number"] == "9001011234567"
    assert state.consumed[0]["max_attempts"] == 100


@pytest.mark.parametrize(
    ("submitted_name", "submitted_phone"),
    [
        ("다른사람", "01012345678"),
        ("홍길동", "01099998888"),
    ],
)
def test_customer_submit_rejects_invite_target_mismatch_before_side_effects(
    submitted_name: str,
    submitted_phone: str,
) -> None:
    state = FakeRemoteState()
    state.invite_payload = {
        "recipient_name": "홍길동",
        "recipient_phone": "01012345678",
        "requested_by": "담당자",
    }
    service, claims, tilko = build_service(state)

    with pytest.raises(ClaimRemoteServiceError) as raised:
        service.submit_customer(
            owner_ref="owner@example.com",
            invite_id=state.invite_id,
            invite_token="T" * 48,
            name=submitted_name,
            phone=submitted_phone,
            resident_number="9001011234567",
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
        "recipient_phone": "01012345678",
        "requested_by": "담당자",
    }
    state.enforce_atomic_consume = True
    state.get_invite_barrier = threading.Barrier(2)
    service, claims, tilko = build_service(state)

    def submit() -> Mapping[str, Any]:
        return service.submit_customer(
            owner_ref="owner@example.com",
            invite_id=state.invite_id,
            invite_token="T" * 48,
            name="홍길동",
            phone="01012345678",
            resident_number="9001011234567",
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
    assert len(claims["owner@example.com"].created) == 1


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
            "owner_user_id": "owner@example.com",
            "invite_id": state.invite_id,
            "case_id": state.case_id,
            "secure_payload_ciphertext": {
                "expires_at": 4_000_000_000.0,
                "absolute_expires_at": 4_000_000_000.0,
                "invite_url": "https://claim.example.test/c/token",
                "hometax": {"Token": "provider-token"},
                "auth_context": {
                    "representative": "홍길동",
                    "cellphone": "01012345678",
                    "birth_date": "19900101",
                    "identity_number": "9001011234567",
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
        "owner_user_id": "owner@example.com",
        "invite_id": state.invite_id,
        "case_id": None,
        "template_code": TEMPLATE_AUTH_START,
        "secure_payload_ciphertext": {
            "to": "01012345678",
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
