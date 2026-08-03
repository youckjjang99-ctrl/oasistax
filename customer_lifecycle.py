from __future__ import annotations

import os
import uuid
from typing import Any

from cloud_db import (
    CloudDatabase,
    TABLE_CUSTOMERS,
    cloud_is_configured,
)


DATA_SAFETY_FEATURE_FLAG = "OASIS_DATA_SAFETY_V1"
_MAX_REASON_LENGTH = 500


def customer_lifecycle_enabled() -> bool:
    return str(os.environ.get(DATA_SAFETY_FEATURE_FLAG, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _validated_uuid(value: Any, label: str) -> str:
    raw = str(value or "").strip()
    try:
        return str(uuid.UUID(raw))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"올바른 {label}가 아닙니다.") from exc


def _validated_user_id(value: Any, label: str) -> str:
    raw = str(value or "").strip()
    if not raw or len(raw) > 200 or any(char in raw for char in "\r\n\x00"):
        raise ValueError(f"올바른 {label}가 아닙니다.")
    return raw


def _validated_reason(value: Any) -> str:
    raw = " ".join(str(value or "").split())
    if not raw:
        raise ValueError("변경 사유를 입력해 주세요.")
    if len(raw) > _MAX_REASON_LENGTH:
        raise ValueError("변경 사유는 500자 이하로 입력해 주세요.")
    return raw


def _ensure_available() -> None:
    if not customer_lifecycle_enabled():
        raise RuntimeError("고객 아카이브 기능이 아직 운영 적용 전입니다.")
    if not cloud_is_configured():
        raise RuntimeError("Supabase 연결 설정을 확인해 주세요.")


def _lifecycle_rpc(
    function_name: str,
    *,
    customer_id: Any,
    owner_user_id: Any,
    actor_user_id: Any,
    reason: Any,
    idempotency_key: str | None = None,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    _ensure_available()
    safe_customer_id = _validated_uuid(customer_id, "고객 ID")
    safe_owner_id = _validated_user_id(owner_user_id, "소유자 ID")
    safe_actor_id = _validated_user_id(actor_user_id, "작업자 ID")
    safe_reason = _validated_reason(reason)
    safe_idempotency = str(idempotency_key or uuid.uuid4()).strip()
    if len(safe_idempotency) > 200 or not safe_idempotency:
        raise ValueError("올바른 멱등성 키가 아닙니다.")

    target = db or CloudDatabase()
    result = target.rpc(
        function_name,
        {
            "p_customer_id": safe_customer_id,
            "p_owner_user_id": safe_owner_id,
            "p_actor_user_id": safe_actor_id,
            "p_reason": safe_reason,
            "p_idempotency_key": safe_idempotency,
        },
    )
    if result is False or result is None:
        raise RuntimeError("고객 상태 변경이 완료되지 않았습니다.")
    return {
        "ok": True,
        "customer_id": safe_customer_id,
        "owner_user_id": safe_owner_id,
    }


def archive_customer(
    *,
    customer_id: Any,
    owner_user_id: Any,
    actor_user_id: Any,
    reason: Any,
    idempotency_key: str | None = None,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """Move a customer to archive state without deleting any customer data."""
    return _lifecycle_rpc(
        "oasis_archive_customer",
        customer_id=customer_id,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        reason=reason,
        idempotency_key=idempotency_key,
        db=db,
    )


def reactivate_customer(
    *,
    customer_id: Any,
    owner_user_id: Any,
    actor_user_id: Any,
    reason: Any,
    idempotency_key: str | None = None,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    """Reactivate an archived customer while retaining the complete history."""
    return _lifecycle_rpc(
        "oasis_reactivate_customer",
        customer_id=customer_id,
        owner_user_id=owner_user_id,
        actor_user_id=actor_user_id,
        reason=reason,
        idempotency_key=idempotency_key,
        db=db,
    )


def list_archived_customers(
    *,
    limit: int = 100,
    db: CloudDatabase | None = None,
) -> list[dict[str, Any]]:
    """Read a bounded archive operations list without contact/identity fields."""
    _ensure_available()
    target = db or CloudDatabase()
    return target.select(
        TABLE_CUSTOMERS,
        filters={"lifecycle_status": "archived"},
        columns=(
            "id,owner_user_id,company_name,lifecycle_status,archived_at,"
            "archive_reason"
        ),
        order="archived_at.desc,id.asc",
        limit=max(1, min(int(limit), 500)),
    )
