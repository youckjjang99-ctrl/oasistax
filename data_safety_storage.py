from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from cloud_db import CloudDatabase, cloud_is_configured


COPILOT_ASSETS_TABLE = "oasis_copilot_assets"


def _current_session_identity() -> tuple[str, str]:
    """Return the trusted Streamlit user/role when a session is active."""
    try:
        import streamlit as st

        if not bool(st.session_state.get("logged_in", False)):
            return "", ""
        return (
            str(st.session_state.get("current_user_id", "") or "").strip(),
            str(st.session_state.get("current_user_role", "") or "").strip(),
        )
    except Exception:
        # Background workers and unit tests do not have a Streamlit session.
        return "", ""


def require_owner_context(
    owner_user_id: str,
    *,
    allow_admin: bool = False,
) -> str:
    """Reject cross-user service-role calls when a trusted UI session exists.

    The server also runs background jobs without a Streamlit session, so the
    absence of session context intentionally preserves those existing callers.
    """
    requested = str(owner_user_id or "").strip()
    current_user_id, current_role = _current_session_identity()
    if current_user_id and not requested:
        requested = current_user_id
    if not requested:
        raise PermissionError("소유자 식별값이 없어 고객 자산에 접근할 수 없습니다.")
    if current_user_id and requested != current_user_id:
        if not (allow_admin and current_role == "admin"):
            raise PermissionError("다른 사용자의 고객 자산에는 접근할 수 없습니다.")
    return requested


def _timestamp_rank(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return 0.0


def feature_enabled(name: str, *, default: bool = False) -> bool:
    """Read a boolean feature flag without requiring Streamlit at import time."""
    value = os.environ.get(name)
    if value is None:
        try:
            import streamlit as st

            if name in st.secrets:
                value = str(st.secrets[name])
        except Exception:
            value = None
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def safe_error_code(exc: BaseException) -> str:
    """Return only an exception class name so customer data cannot leak."""
    return type(exc).__name__[:80] or "cloud_write_error"


@dataclass(frozen=True)
class StorageWriteStatus:
    local_saved: bool
    cloud_enabled: bool
    cloud_attempted: bool
    cloud_saved: bool
    degraded: bool = False
    error_code: str = ""
    error_summary: str = ""

    @property
    def ok(self) -> bool:
        return self.local_saved and not self.degraded

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, **asdict(self)}


def local_only_status() -> StorageWriteStatus:
    return StorageWriteStatus(
        local_saved=True,
        cloud_enabled=False,
        cloud_attempted=False,
        cloud_saved=False,
    )


def write_copilot_asset(
    *,
    owner_user_id: str,
    asset_type: str,
    asset_key: str,
    payload: dict[str, Any],
    source_updated_at: str,
) -> StorageWriteStatus:
    """Dual-write one Copilot asset when the opt-in migration flag is enabled."""
    owner_user_id = require_owner_context(owner_user_id)
    if not feature_enabled("OASIS_CLOUD_COPILOT_V1", default=False):
        return local_only_status()
    if not cloud_is_configured():
        return StorageWriteStatus(
            local_saved=True,
            cloud_enabled=True,
            cloud_attempted=False,
            cloud_saved=False,
            degraded=True,
            error_code="cloud_not_configured",
            error_summary="클라우드 저장 설정을 확인해 주세요.",
        )
    try:
        CloudDatabase().upsert(
            COPILOT_ASSETS_TABLE,
            [
                {
                    "owner_user_id": str(owner_user_id),
                    "asset_type": str(asset_type),
                    "asset_key": str(asset_key),
                    "payload": dict(payload or {}),
                    "source_updated_at": str(source_updated_at or ""),
                }
            ],
            on_conflict="owner_user_id,asset_type,asset_key",
        )
    except Exception as exc:
        return StorageWriteStatus(
            local_saved=True,
            cloud_enabled=True,
            cloud_attempted=True,
            cloud_saved=False,
            degraded=True,
            error_code=safe_error_code(exc),
            error_summary="클라우드 저장에 실패해 로컬 원본을 유지했습니다.",
        )
    return StorageWriteStatus(
        local_saved=True,
        cloud_enabled=True,
        cloud_attempted=True,
        cloud_saved=True,
    )


def load_copilot_assets(
    *,
    owner_user_id: str,
    asset_type: str,
    limit: int = 100000,
) -> list[dict[str, Any]]:
    """Best-effort cloud read; callers retain their complete local fallback."""
    owner_user_id = require_owner_context(owner_user_id, allow_admin=True)
    if not feature_enabled("OASIS_CLOUD_COPILOT_V1", default=False):
        return []
    if not cloud_is_configured():
        return []
    try:
        return CloudDatabase().select_all(
            COPILOT_ASSETS_TABLE,
            filters={
                "owner_user_id": str(owner_user_id),
                "asset_type": str(asset_type),
            },
            order="source_updated_at.desc",
            page_size=1000,
            max_rows=max(1, min(int(limit), 100000)),
        )
    except Exception:
        return []


def migrate_local_copilot_assets(
    *,
    owner_user_id: str,
    assets: Iterable[dict[str, Any]],
    batch_size: int = 200,
) -> dict[str, Any]:
    """Idempotently copy missing/newer local Copilot assets to cloud.

    Existing newer cloud rows are never overwritten.  The helper is bounded
    and paginated and may safely run again after a Railway restart.
    """
    owner_user_id = require_owner_context(owner_user_id)
    if not feature_enabled("OASIS_CLOUD_COPILOT_V1", default=False):
        return {"enabled": False, "migrated": 0, "skipped": 0}
    if not cloud_is_configured():
        return {
            "enabled": True,
            "migrated": 0,
            "skipped": 0,
            "degraded": True,
            "error_code": "cloud_not_configured",
        }

    normalized: dict[tuple[str, str], dict[str, Any]] = {}
    for source in assets:
        if not isinstance(source, dict):
            continue
        asset_type = str(source.get("asset_type", "") or "").strip()
        asset_key = str(source.get("asset_key", "") or "").strip()
        payload = source.get("payload", {})
        if not asset_type or not asset_key or not isinstance(payload, dict):
            continue
        normalized[(asset_type, asset_key)] = {
            "owner_user_id": owner_user_id,
            "asset_type": asset_type,
            "asset_key": asset_key,
            "payload": dict(payload),
            "source_updated_at": str(
                source.get("source_updated_at", "") or ""
            ),
        }
    if not normalized:
        return {"enabled": True, "migrated": 0, "skipped": 0}

    db = CloudDatabase()
    try:
        existing_rows = db.select_all(
            COPILOT_ASSETS_TABLE,
            filters={"owner_user_id": owner_user_id},
            columns="asset_type,asset_key,source_updated_at",
            page_size=1000,
            max_rows=100000,
        )
        existing = {
            (
                str(row.get("asset_type", "") or ""),
                str(row.get("asset_key", "") or ""),
            ): _timestamp_rank(row.get("source_updated_at"))
            for row in existing_rows
            if isinstance(row, dict)
        }
        pending = [
            row
            for identity, row in normalized.items()
            if identity not in existing
            or _timestamp_rank(row.get("source_updated_at"))
            > existing.get(identity, 0.0)
        ]
        safe_batch = max(1, min(int(batch_size), 500))
        for start in range(0, len(pending), safe_batch):
            db.upsert(
                COPILOT_ASSETS_TABLE,
                pending[start : start + safe_batch],
                on_conflict="owner_user_id,asset_type,asset_key",
            )
    except Exception as exc:
        return {
            "enabled": True,
            "migrated": 0,
            "skipped": len(normalized),
            "degraded": True,
            "error_code": safe_error_code(exc),
        }
    return {
        "enabled": True,
        "migrated": len(pending),
        "skipped": len(normalized) - len(pending),
    }
