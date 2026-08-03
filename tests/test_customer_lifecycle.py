from __future__ import annotations

import inspect

import pytest

import customer_lifecycle


CUSTOMER_ID = "12345678-1234-4234-8234-123456789012"


class FakeDatabase:
    def __init__(self) -> None:
        self.rpc_calls = []
        self.select_calls = []

    def rpc(self, name, parameters):
        self.rpc_calls.append((name, parameters))
        return True

    def select(self, table, **kwargs):
        self.select_calls.append((table, kwargs))
        return [{"id": CUSTOMER_ID, "lifecycle_status": "archived"}]


@pytest.fixture(autouse=True)
def enable_lifecycle(monkeypatch):
    monkeypatch.setenv("OASIS_DATA_SAFETY_V1", "1")
    monkeypatch.setattr(customer_lifecycle, "cloud_is_configured", lambda: True)


def test_archive_uses_atomic_service_rpc_and_no_physical_delete() -> None:
    db = FakeDatabase()

    result = customer_lifecycle.archive_customer(
        customer_id=CUSTOMER_ID,
        owner_user_id="owner-1",
        actor_user_id="admin-1",
        reason="장기 보관",
        idempotency_key="archive-once",
        db=db,
    )

    assert result["ok"] is True
    assert db.rpc_calls == [
        (
            "oasis_archive_customer",
            {
                "p_customer_id": CUSTOMER_ID,
                "p_owner_user_id": "owner-1",
                "p_actor_user_id": "admin-1",
                "p_reason": "장기 보관",
                "p_idempotency_key": "archive-once",
            },
        )
    ]
    source = inspect.getsource(customer_lifecycle)
    assert "delete(" not in source.lower()
    assert "remove(" not in source.lower()
    assert "unlink(" not in source.lower()


def test_reactivate_uses_atomic_service_rpc() -> None:
    db = FakeDatabase()

    customer_lifecycle.reactivate_customer(
        customer_id=CUSTOMER_ID,
        owner_user_id="owner-1",
        actor_user_id="admin-1",
        reason="상담 재개",
        idempotency_key="reactivate-once",
        db=db,
    )

    assert db.rpc_calls[0][0] == "oasis_reactivate_customer"
    assert db.rpc_calls[0][1]["p_reason"] == "상담 재개"


def test_feature_flag_off_blocks_writes(monkeypatch) -> None:
    db = FakeDatabase()
    monkeypatch.delenv("OASIS_DATA_SAFETY_V1", raising=False)

    with pytest.raises(RuntimeError, match="운영 적용 전"):
        customer_lifecycle.archive_customer(
            customer_id=CUSTOMER_ID,
            owner_user_id="owner-1",
            actor_user_id="admin-1",
            reason="보관",
            db=db,
        )

    assert db.rpc_calls == []


def test_invalid_customer_id_is_rejected_before_rpc() -> None:
    db = FakeDatabase()

    with pytest.raises(ValueError, match="고객 ID"):
        customer_lifecycle.archive_customer(
            customer_id="not-a-uuid",
            owner_user_id="owner-1",
            actor_user_id="admin-1",
            reason="보관",
            db=db,
        )

    assert db.rpc_calls == []


def test_archive_list_is_bounded_and_requests_no_contact_fields() -> None:
    db = FakeDatabase()

    rows = customer_lifecycle.list_archived_customers(limit=9999, db=db)

    assert rows[0]["id"] == CUSTOMER_ID
    table, kwargs = db.select_calls[0]
    assert table == "oasis_customers"
    assert kwargs["filters"] == {"lifecycle_status": "archived"}
    assert kwargs["limit"] == 500
    assert "business_no" not in kwargs["columns"]
    assert "phone" not in kwargs["columns"]
    assert "representative" not in kwargs["columns"]
