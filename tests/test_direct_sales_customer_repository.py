from __future__ import annotations

import direct_sales_customer_repository as repository


DIRECT_CUSTOMER_ID = "10000000-0000-4000-8000-000000000001"
CUSTOMER_ID = "20000000-0000-4000-8000-000000000002"
OUTBOX_ID = "30000000-0000-4000-8000-000000000003"
TOKEN = "40000000-0000-4000-8000-000000000004"
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64
BUSINESS_NO = "".join(("123", "45", "67890"))
FORMATTED_BUSINESS_NO = "-".join(("123", "45", "67890"))
MOBILE_PHONE = "".join(("010", "1234", "5678"))


def _email(local_part: str) -> str:
    return "@".join((local_part, "example.invalid"))


class _Database:
    def __init__(self, payload=None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, parameters: dict):
        self.calls.append((name, dict(parameters)))
        if self.error:
            raise self.error
        return self.payload


def test_register_uses_actor_bound_rpc_and_never_assignment_rpc():
    db = _Database(
        [{
            "success": True,
            "code": "REGISTERED",
            "direct_customer_id": DIRECT_CUSTOMER_ID,
            "customer_id": CUSTOMER_ID,
        }]
    )

    result = repository.register_direct_customer(
        _email("owner").upper(),
        {
            "company_name": "테스트 업체",
            "business_no": BUSINESS_NO,
            "business_type": "individual",
            "mobile_phone": MOBILE_PHONE,
            "employee_count": 3,
            "marketing_consent_confirmed": True,
            "marketing_consent_method": "전화 확인",
        },
        mobile_phone_hash=HEX_A,
        manager_name="담당자",
        db=db,
    )

    assert result["ok"] is True
    name, parameters = db.calls[0]
    assert name == repository.RPC_REGISTER
    assert "assignment" not in name
    assert parameters["p_current_user_id"] == _email("owner")
    assert parameters["p_mobile_phone_hash"] == HEX_A
    assert parameters["p_marketing_consent_confirmed"] is True


def test_register_exception_is_redacted():
    db = _Database(error=RuntimeError("secret-token " + _email("private")))

    result = repository.register_direct_customer(
        _email("owner"),
        {
            "company_name": "테스트 업체",
            "business_no": BUSINESS_NO,
            "business_type": "individual",
        },
        db=db,
    )

    assert result["ok"] is False
    assert result["code"] == "DIRECT_DB_UNAVAILABLE"
    assert "secret-token" not in result["message"]
    assert "example.invalid" not in result["message"]


def test_list_is_owner_scoped_and_drops_unexpected_fields():
    db = _Database(
        [{
            "direct_customer_id": DIRECT_CUSTOMER_ID,
            "customer_id": CUSTOMER_ID,
            "company_name": "테스트 업체",
            "business_no": FORMATTED_BUSINESS_NO,
            "sales_category": "registered",
            "total_count": 1,
            "internal_secret": "must-not-leak",
        }]
    )

    result = repository.list_direct_customers(
        _email("owner"),
        category="registered",
        db=db,
    )

    assert result["ok"] is True
    assert result["total_count"] == 1
    assert "internal_secret" not in result["customers"][0]
    assert db.calls[0][1]["p_current_user_id"] == _email("owner")
    assert db.calls[0][1]["p_filter"] == "registered"


def test_outreach_requires_consent_and_opaque_bindings():
    blocked_db = _Database()
    blocked = repository.reserve_outreach_attempt(
        _email("owner"),
        "request-12345678",
        HEX_A,
        HEX_B,
        HEX_C,
        DIRECT_CUSTOMER_ID,
        "2026-08-12T00:00:00+00:00",
        "kakao",
        consent_confirmed=False,
        db=blocked_db,
    )
    assert blocked["ok"] is False
    assert blocked_db.calls == []

    db = _Database(
        [{
            "success": True,
            "code": "RESERVED",
            "outbox_id": OUTBOX_ID,
            "status": "reserved",
            "acquired": True,
            "reservation_token": TOKEN,
        }]
    )
    result = repository.reserve_outreach_attempt(
        _email("owner"),
        "request-12345678",
        HEX_A,
        HEX_B,
        HEX_C,
        DIRECT_CUSTOMER_ID,
        "2026-08-12T00:00:00+00:00",
        "kakao",
        consent_confirmed=True,
        db=db,
    )
    assert result["ok"] is True
    parameters = db.calls[0][1]
    assert parameters["p_content_hmac"] == HEX_A
    assert parameters["p_recipient_hmac"] == HEX_B
    assert parameters["p_recipient_phone_hash"] == HEX_C
    assert "recipient" not in repr(parameters).lower() or "hmac" in repr(parameters).lower()


def test_outreach_history_is_allowlisted():
    db = _Database(
        [{
            "outbox_id": OUTBOX_ID,
            "channel": "sms",
            "status": "provider_accepted",
            "safe_result_code": "ACCEPTED",
            "reserved_at": "2026-08-12T00:00:00+00:00",
            "recipient": MOBILE_PHONE,
            "message_body": "private body",
        }]
    )

    result = repository.list_outreach_history(
        _email("owner"),
        DIRECT_CUSTOMER_ID,
        db=db,
    )

    assert result["ok"] is True
    assert "recipient" not in result["history"][0]
    assert "message_body" not in result["history"][0]
