from __future__ import annotations

import base64
import re
from typing import Any, Mapping

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from claim_public_gateway import (
    ClaimPublicSettings,
    RepositoryClaimPublicService,
    create_app,
)
from claim_remote_crypto import ClaimRemoteCrypto


INVITE_TOKEN = "A" * 43


class _FakeClaimService:
    def __init__(self):
        self.opened_tokens: list[str] = []
        self.submissions: list[dict[str, Any]] = []
        self.cancellations: list[dict[str, Any]] = []
        self.flow_type = "staff_targeted"
        self.status: dict[str, Any] = {
            "status": "opened",
            "progress": 0,
            "name": '홍길동<script>alert("x")</script>',
            "phone": "-".join(("010", "1234", "5678")),
        }

    def open_invite(self, invite_token: str) -> Mapping[str, Any]:
        self.opened_tokens.append(invite_token)
        return {
            "invite_id": "7b2e6b86-b209-4a24-bf77-a75059951b2a",
            "owner_ref": "owner-opaque-ref",
            "status": "opened",
            "flow_type": self.flow_type,
        }

    def submit_customer(self, **payload: Any) -> Mapping[str, Any]:
        self.submissions.append(dict(payload))
        self.status = {
            "status": "queued",
            "stage": "hometax_request",
            "progress": 0,
            "safe_message": "국세청 홈택스 인증 요청을 준비하고 있습니다.",
        }
        return self.status

    def get_status(self, **payload: Any) -> Mapping[str, Any]:
        return dict(self.status)

    def cancel_customer(self, **payload: Any) -> Mapping[str, Any]:
        self.cancellations.append(dict(payload))
        self.status = {
            "status": "cancelled",
            "stage": "customer_opt_out",
            "progress": 0,
            "submitted": True,
            "complete": True,
        }
        return dict(self.status)


def _crypto() -> ClaimRemoteCrypto:
    return ClaimRemoteCrypto(
        cipher=Fernet(base64.urlsafe_b64encode(b"k" * 32)),
        link_pepper=b"p" * 48,
        session_secret=b"s" * 48,
    )


def _client(
    service: _FakeClaimService,
) -> TestClient:
    app = create_app(
        service=service,
        crypto=_crypto(),
        settings=ClaimPublicSettings(
            secure_cookies=True,
            session_seconds=3600,
        ),
    )
    return TestClient(app, base_url="https://testserver")


def _open_invite(client: TestClient) -> str:
    response = client.get(
        f"/c/{INVITE_TOKEN}",
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/claim"
    return response.headers["set-cookie"]


def _csrf_from(response_text: str) -> str:
    match = re.search(
        r'name="csrf_token" value="([^"]+)"',
        response_text,
    )
    assert match is not None
    return match.group(1)


def test_repository_adapter_strips_legacy_recipient_for_self_input() -> None:
    class _Repository:
        owner_user_id = "@".join(("owner", "example.test"))

        def mark_invite_opened(self, invite_token: str) -> Mapping[str, Any]:
            assert invite_token == INVITE_TOKEN
            return {
                "id": "7b2e6b86-b209-4a24-bf77-a75059951b2a",
                "status": "opened",
                "secure_payload_ciphertext": "encrypted",
            }

        def decrypt_payload(self, ciphertext: str) -> Mapping[str, Any]:
            assert ciphertext == "encrypted"
            return {
                "flow_type": "prospect_self_input",
                "recipient_name": "공개 DB 상호",
                "recipient_phone": "".join(("010", "1234", "5678")),
            }

    service = RepositoryClaimPublicService(
        _Repository(),
        lambda **_payload: {},
    )

    opened = service.open_invite(INVITE_TOKEN)

    assert opened["flow_type"] == "prospect_self_input"
    assert opened["name"] == ""
    assert opened["phone"] == ""


def test_exchange_removes_raw_token_and_sets_hardened_cookie() -> None:
    service = _FakeClaimService()
    client = _client(service)

    cookie_header = _open_invite(client)

    assert service.opened_tokens == [INVITE_TOKEN]
    assert INVITE_TOKEN not in cookie_header
    assert "HttpOnly" in cookie_header
    assert "Secure" in cookie_header
    assert "SameSite=lax" in cookie_header

    form = client.get("/claim")
    assert form.status_code == 200
    assert INVITE_TOKEN not in form.text
    assert "<script>alert" not in form.text
    assert "&lt;script&gt;" in form.text
    assert form.headers["cache-control"].startswith("no-store")
    assert form.headers["referrer-policy"] == "no-referrer"


def test_submit_requires_matching_csrf_and_explicit_consents() -> None:
    service = _FakeClaimService()
    client = _client(service)
    _open_invite(client)

    rejected = client.post(
        "/claim/submit",
        data={
            "csrf_token": "wrong",
            "name": "홍길동",
            "phone": "-".join(("010", "1234", "5678")),
            "resident_number": "".join(("900101", "1", "234567")),
            "privacy_consent": "yes",
            "third_party_consent": "yes",
        },
        follow_redirects=False,
    )
    assert rejected.status_code == 403
    assert not service.submissions

    form = client.get("/claim")
    csrf = _csrf_from(form.text)
    missing_consent = client.post(
        "/claim/submit",
        data={
            "csrf_token": csrf,
            "name": "홍길동",
            "phone": "-".join(("010", "1234", "5678")),
            "resident_number": "".join(("900101", "1", "234567")),
            "privacy_consent": "yes",
        },
        follow_redirects=False,
    )
    assert missing_consent.status_code == 400
    assert not service.submissions


def test_valid_submission_is_normalized_and_status_exposes_no_pii() -> None:
    service = _FakeClaimService()
    client = _client(service)
    _open_invite(client)
    form = client.get("/claim")
    csrf = _csrf_from(form.text)

    submitted = client.post(
        "/claim/submit",
        data={
            "csrf_token": csrf,
            "name": "  홍  길동 ",
            "phone": "-".join(("010", "1234", "5678")),
            "resident_number": "-".join(("900101", "1234567")),
            "privacy_consent": "yes",
            "third_party_consent": "yes",
        },
        follow_redirects=False,
    )

    assert submitted.status_code == 303
    assert submitted.headers["location"] == "/claim"
    assert len(service.submissions) == 1
    payload = service.submissions[0]
    assert payload["name"] == "홍 길동"
    assert payload["owner_ref"] == "owner-opaque-ref"
    assert payload["phone"] == "".join(("010", "1234", "5678"))
    assert payload["resident_number"] == "".join(("900101", "1", "234567"))
    assert payload["consents"] == {
        "privacy_and_unique_identifier": True,
        "third_party_processing": True,
    }

    status = client.get("/api/claim/status")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert "".join(("900101", "1", "234567")) not in status.text
    assert "".join(("010", "1234", "5678")) not in status.text
    assert "홍 길동" not in status.text


def test_prospect_form_never_prefills_source_recipient_and_uses_split_id() -> None:
    service = _FakeClaimService()
    service.flow_type = "prospect_self_input"
    client = _client(service)
    _open_invite(client)

    form = client.get("/claim")

    assert form.status_code == 200
    assert 'name="birth_date"' in form.text
    assert 'name="identity_rear"' in form.text
    assert 'name="company_name"' in form.text
    assert 'name="business_no"' in form.text
    assert 'action="/claim/cancel"' in form.text
    assert "-".join(("010", "1234", "5678")) not in form.text
    assert "&lt;script&gt;" not in form.text
    assert "안내 메시지를 받은 번호와 달라도 괜찮습니다." in form.text
    assert "대표자 본인 명의 휴대전화번호" in form.text
    assert "간편인증서 가입정보와 일치하지 않으면 인증이 실패" in form.text


def test_prospect_submit_combines_customer_identity_and_optional_company() -> None:
    service = _FakeClaimService()
    service.flow_type = "prospect_self_input"
    client = _client(service)
    _open_invite(client)
    csrf = _csrf_from(client.get("/claim").text)

    submitted = client.post(
        "/claim/submit",
        data={
            "csrf_token": csrf,
            "name": "고객입력",
            "phone": "-".join(("010", "9999", "8888")),
            "birth_date": "900101",
            "identity_rear": "1234567",
            "company_name": "고객 상호",
            "business_no": "-".join(("123", "45", "67890")),
            "privacy_consent": "yes",
            "third_party_consent": "yes",
        },
        follow_redirects=False,
    )

    assert submitted.status_code == 303
    payload = service.submissions[0]
    assert payload["resident_number"] == "".join(("900101", "1", "234567"))
    assert payload["company_name"] == "고객 상호"
    assert payload["business_no"] == "".join(("123", "45", "67890"))


def test_prospect_cancel_requires_csrf_and_stops_refresh() -> None:
    service = _FakeClaimService()
    service.flow_type = "prospect_self_input"
    client = _client(service)
    _open_invite(client)
    csrf = _csrf_from(client.get("/claim").text)

    rejected = client.post(
        "/claim/cancel",
        data={"csrf_token": "wrong"},
        follow_redirects=False,
    )
    assert rejected.status_code == 403
    assert service.cancellations == []

    cancelled = client.post(
        "/claim/cancel",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert cancelled.status_code == 303
    assert service.cancellations[0]["reason"] == "customer_opt_out"
    page = client.get("/claim")
    assert page.status_code == 200
    assert 'http-equiv="refresh"' not in page.text


def test_invalid_resident_number_is_not_echoed_back() -> None:
    service = _FakeClaimService()
    client = _client(service)
    _open_invite(client)
    csrf = _csrf_from(client.get("/claim").text)
    invalid_value = "900101-9234567"

    response = client.post(
        "/claim/submit",
        data={
            "csrf_token": csrf,
            "name": "홍길동",
            "phone": "".join(("010", "1234", "5678")),
            "resident_number": invalid_value,
            "privacy_consent": "yes",
            "third_party_consent": "yes",
        },
    )

    assert response.status_code == 400
    assert invalid_value not in response.text
    assert not service.submissions


def test_health_does_not_disclose_configuration() -> None:
    service = _FakeClaimService()
    client = _client(service)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert "secret" not in response.text.lower()
