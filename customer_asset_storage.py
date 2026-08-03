from __future__ import annotations

import hashlib
import mimetypes
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from cloud_db import (
    CloudDatabase,
    PRIVATE_CUSTOMER_ASSET_BUCKET,
    TABLE_CUSTOMER_ASSETS,
    cloud_is_configured,
)
from data_safety_storage import (
    feature_enabled,
    require_owner_context,
    safe_error_code,
)
from sync_outbox import enqueue_outbox
from utils import get_user_dirs


PRIVATE_ASSET_FEATURE_FLAG = "OASIS_PRIVATE_ASSETS_V1"
CUSTOMER_ASSET_LINKS_TABLE = "oasis_customer_asset_links"
ASSET_METADATA_RECOVERY_JOB = "customer_asset_metadata_reconcile"
ASSET_LINK_RECOVERY_JOB = "customer_asset_link_reconcile"
RECOVERY_QUEUE_FILENAME = "cloud_sync_queue.json"


@dataclass(frozen=True)
class CustomerAssetWriteResult:
    local_preserved: bool
    cloud_enabled: bool
    cloud_saved: bool
    metadata_saved: bool
    asset_id: str = ""
    error_code: str = ""
    recovery_queued: bool = False
    recovery_queue: str = ""

    @property
    def degraded(self) -> bool:
        return self.cloud_enabled and not (self.cloud_saved and self.metadata_saved)

    def as_dict(self) -> dict[str, Any]:
        return {"degraded": self.degraded, **asdict(self)}


def _safe_filename(value: str) -> str:
    source = Path(str(value or "file")).name
    suffix = Path(source).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ""
    stem = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", Path(source).stem)
    stem = stem.strip("._")[:80] or "file"
    return f"{stem}{suffix}"


def _storage_path(owner_user_id: str, filename: str) -> str:
    owner_hash = hashlib.sha256(
        str(owner_user_id).encode("utf-8")
    ).hexdigest()[:20]
    date_path = datetime.now(timezone.utc).strftime("%Y/%m/%d")
    return f"{owner_hash}/{date_path}/{uuid.uuid4().hex}_{_safe_filename(filename)}"


def _asset_association_key(
    *,
    customer_id: str | None,
    source_type: str,
    source_id: str,
) -> str:
    """Return a stable, non-PII identifier for one logical asset link."""
    canonical = "\x1f".join(
        (
            str(customer_id or "").strip().lower(),
            str(source_type or "").strip().lower(),
            str(source_id or "").strip(),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _asset_link_row(
    *,
    owner_user_id: str,
    asset_id: str,
    customer_id: str | None,
    source_type: str,
    source_id: str,
) -> dict[str, Any]:
    association_key = _asset_association_key(
        customer_id=customer_id,
        source_type=source_type,
        source_id=source_id,
    )
    link_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "oasis://customer-asset-link/"
                f"{owner_user_id}/{asset_id}/{association_key}"
            ),
        )
    )
    return {
        "id": link_id,
        "owner_user_id": str(owner_user_id),
        "asset_id": str(asset_id),
        "customer_id": str(customer_id) if customer_id else None,
        "association_key": association_key,
        "source_type": str(source_type or ""),
        "source_id": str(source_id or ""),
    }


def _reconciliation_queue_path(owner_user_id: str) -> Path:
    """Share the persistent per-owner queue used by the existing cloud sync."""
    return get_user_dirs(owner_user_id)["base"] / RECOVERY_QUEUE_FILENAME


def _queue_asset_reconciliation(
    *,
    owner_user_id: str,
    asset_row: dict[str, Any] | None,
    link_row: dict[str, Any] | None,
    error_code: str,
) -> tuple[bool, str]:
    """Queue safe, idempotent metadata repairs without a local source path.

    The already-uploaded Storage object and original local file are deliberately
    left untouched.  Only stable metadata needed to reconnect that object is
    placed in the owner-scoped recovery queue.  ``error_code`` must be a safe
    class/code value, never the raw provider exception text.
    """
    queue_path = _reconciliation_queue_path(owner_user_id)
    steps: list[tuple[str, str, dict[str, Any], str]] = []
    if asset_row:
        steps.append(
            (
                ASSET_METADATA_RECOVERY_JOB,
                TABLE_CUSTOMER_ASSETS,
                dict(asset_row),
                "storage_bucket,storage_path",
            )
        )
    if link_row:
        steps.append(
            (
                ASSET_LINK_RECOVERY_JOB,
                CUSTOMER_ASSET_LINKS_TABLE,
                dict(link_row),
                "owner_user_id,asset_id,association_key",
            )
        )
    if not steps:
        return False, ""

    locations: list[str] = []
    for job_type, table, row, on_conflict in steps:
        try:
            location, _job = enqueue_outbox(
                queue_path,
                owner_user_id,
                job_type,
                table,
                [row],
                on_conflict,
                error=str(error_code or "asset_metadata_write_failed")[:80],
            )
            locations.append(str(location))
        except Exception:
            # The caller still receives a degraded result.  Never include the
            # queue exception text because it may contain customer data.
            return False, "partial" if locations else ""
    unique_locations = set(locations)
    return True, locations[0] if len(unique_locations) == 1 else "mixed"


def _upsert_asset_link(
    db: CloudDatabase,
    *,
    owner_user_id: str,
    asset_id: str,
    customer_id: str | None,
    source_type: str,
    source_id: str,
) -> None:
    link_row = _asset_link_row(
        owner_user_id=owner_user_id,
        asset_id=asset_id,
        customer_id=customer_id,
        source_type=source_type,
        source_id=source_id,
    )
    db.upsert(
        CUSTOMER_ASSET_LINKS_TABLE,
        [link_row],
        "owner_user_id,asset_id,association_key",
    )


def store_private_customer_asset(
    local_path: str | Path,
    *,
    owner_user_id: str,
    asset_type: str,
    customer_id: str | None = None,
    source_type: str = "local_migration",
    source_id: str = "",
) -> CustomerAssetWriteResult:
    """Copy a local asset to private Storage without deleting the local source."""
    owner_user_id = require_owner_context(owner_user_id)
    source = Path(local_path)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    if not feature_enabled(PRIVATE_ASSET_FEATURE_FLAG, default=False):
        return CustomerAssetWriteResult(True, False, False, False)
    if not cloud_is_configured():
        return CustomerAssetWriteResult(
            True,
            True,
            False,
            False,
            error_code="cloud_not_configured",
        )

    content = source.read_bytes()
    checksum = hashlib.sha256(content).hexdigest()
    storage_path = _storage_path(owner_user_id, source.name)
    content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    db = CloudDatabase()
    try:
        existing = db.select(
            TABLE_CUSTOMER_ASSETS,
            filters={
                "owner_user_id": str(owner_user_id),
                "asset_type": str(asset_type),
                "sha256": checksum,
                "status": "active",
            },
            columns="id,customer_id,source_type,source_id",
            limit=1,
        )
    except Exception as exc:
        return CustomerAssetWriteResult(
            True,
            True,
            False,
            False,
            error_code=safe_error_code(exc),
        )
    if existing:
        existing_row = existing[0]
        asset_id = str(existing_row.get("id") or "").strip()
        if not asset_id:
            return CustomerAssetWriteResult(
                True,
                True,
                True,
                False,
                error_code="asset_metadata_invalid",
            )
        link_row = _asset_link_row(
            owner_user_id=owner_user_id,
            asset_id=asset_id,
            customer_id=customer_id,
            source_type=source_type,
            source_id=source_id,
        )
        try:
            _upsert_asset_link(
                db,
                owner_user_id=owner_user_id,
                asset_id=asset_id,
                customer_id=customer_id,
                source_type=source_type,
                source_id=source_id,
            )
        except Exception as exc:
            error_code = safe_error_code(exc)
            recovery_queued, recovery_queue = _queue_asset_reconciliation(
                owner_user_id=owner_user_id,
                asset_row=None,
                link_row=link_row,
                error_code=error_code,
            )
            return CustomerAssetWriteResult(
                True,
                True,
                True,
                False,
                asset_id=asset_id,
                error_code=error_code,
                recovery_queued=recovery_queued,
                recovery_queue=recovery_queue,
            )
        return CustomerAssetWriteResult(
            True,
            True,
            True,
            True,
            asset_id=asset_id,
        )
    try:
        db.upload_private_object(
            PRIVATE_CUSTOMER_ASSET_BUCKET,
            storage_path,
            content,
            content_type,
        )
    except Exception as exc:
        return CustomerAssetWriteResult(
            True,
            True,
            False,
            False,
            error_code=safe_error_code(exc),
        )

    asset_id = str(uuid.uuid4())
    row = {
        "id": asset_id,
        "owner_user_id": str(owner_user_id),
        "customer_id": str(customer_id) if customer_id else None,
        "asset_type": str(asset_type),
        "storage_bucket": PRIVATE_CUSTOMER_ASSET_BUCKET,
        "storage_path": storage_path,
        "original_filename": _safe_filename(source.name),
        "content_type": content_type,
        "size_bytes": len(content),
        "sha256": checksum,
        "status": "active",
        "source_type": str(source_type),
        "source_id": str(source_id or ""),
    }
    link_row = _asset_link_row(
        owner_user_id=owner_user_id,
        asset_id=asset_id,
        customer_id=customer_id,
        source_type=source_type,
        source_id=source_id,
    )
    try:
        db.upsert(
            TABLE_CUSTOMER_ASSETS,
            [row],
            "storage_bucket,storage_path",
        )
        _upsert_asset_link(
            db,
            owner_user_id=owner_user_id,
            asset_id=asset_id,
            customer_id=customer_id,
            source_type=source_type,
            source_id=source_id,
        )
    except Exception as exc:
        # Never delete the source or the uploaded object automatically. The
        # metadata reconciliation job can safely recover this orphan later.
        error_code = safe_error_code(exc)
        recovery_queued, recovery_queue = _queue_asset_reconciliation(
            owner_user_id=owner_user_id,
            asset_row=row,
            link_row=link_row,
            error_code=error_code,
        )
        return CustomerAssetWriteResult(
            True,
            True,
            True,
            False,
            asset_id=asset_id,
            error_code=error_code,
            recovery_queued=recovery_queued,
            recovery_queue=recovery_queue,
        )
    return CustomerAssetWriteResult(
        True,
        True,
        True,
        True,
        asset_id=asset_id,
    )


def migrate_local_customer_assets(
    paths: Iterable[str | Path],
    *,
    owner_user_id: str,
    asset_type: str,
) -> list[CustomerAssetWriteResult]:
    """Repeated copies reuse matching checksums; source files remain intact."""
    results = []
    for path in paths:
        results.append(
            store_private_customer_asset(
                path,
                owner_user_id=owner_user_id,
                asset_type=asset_type,
            )
        )
    return results


def create_customer_asset_download_url(
    asset_id: str,
    *,
    owner_user_id: str,
    expires_in: int = 60,
) -> str:
    owner_user_id = require_owner_context(
        owner_user_id,
        allow_admin=True,
    )
    if not feature_enabled(PRIVATE_ASSET_FEATURE_FLAG, default=False):
        raise RuntimeError("비공개 고객 문서 저장 기능이 아직 활성화되지 않았습니다.")
    db = CloudDatabase()
    rows = db.select(
        TABLE_CUSTOMER_ASSETS,
        filters={
            "id": str(asset_id),
            "owner_user_id": str(owner_user_id),
            "status": "active",
        },
        columns="storage_bucket,storage_path,original_filename",
        limit=1,
    )
    if not rows:
        raise PermissionError("문서를 찾을 수 없거나 접근 권한이 없습니다.")
    row = rows[0]
    return db.create_private_signed_url(
        str(row["storage_bucket"]),
        str(row["storage_path"]),
        expires_in=max(10, min(int(expires_in), 300)),
        download_name=str(row.get("original_filename") or "download"),
    )
