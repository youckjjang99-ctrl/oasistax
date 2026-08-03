from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests
import streamlit as st

from data_safety_storage import require_owner_context


BUCKET_NAME = "oasis-consultation-audio"
TABLE_NAME = "oasis_consultation_audio"
REQUEST_TIMEOUT = 900


def normalize_business_no(value: str) -> str:
    """사업자번호를 숫자 10자리 기준으로 정규화한다."""
    return re.sub(r"[^0-9]", "", str(value or ""))


def _normalize_company_name(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).lower()


def _secret(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value.strip()
        try:
            if name in st.secrets:
                return str(st.secrets[name]).strip()
        except Exception:
            pass
    return ""


def _config() -> tuple[str, str]:
    url = _secret("SUPABASE_URL")
    key = _secret(
        "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_SERVICE_KEY",
        "SUPABASE_KEY",
    )
    return url.rstrip("/"), key


def storage_is_configured() -> bool:
    url, key = _config()
    return bool(url and key)


def _headers(content_type: str | None = None) -> dict[str, str]:
    _, key = _config()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _safe_segment(value: str, fallback: str) -> str:
    cleaned = re.sub(
        r"[^0-9A-Za-z가-힣._-]+",
        "_",
        str(value or "").strip(),
    ).strip("._-")
    return cleaned[:80] or fallback


def _business_key(company_name: str, business_no: str) -> str:
    digits = normalize_business_no(business_no)
    return digits or _safe_segment(company_name, "unknown_company")


def _audio_hash(audio_bytes: bytes) -> str:
    return hashlib.sha256(audio_bytes).hexdigest()


def _object_path(
    user_id: str,
    company_name: str,
    business_no: str,
    filename: str,
    digest: str,
) -> str:
    suffix = Path(filename).suffix.lower() or ".m4a"
    return "/".join(
        [
            _safe_segment(user_id, "unknown_user"),
            _business_key(company_name, business_no),
            datetime.now().strftime("%Y/%m"),
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{digest[:12]}{suffix}",
        ]
    )


def _rest_url(path: str) -> str:
    url, _ = _config()
    return f"{url}{path}"


def _select_audio_rows(user_id: str, limit: int = 500) -> list[dict[str, Any]]:
    user_id = require_owner_context(user_id, allow_admin=True)
    if not storage_is_configured():
        return []
    response = requests.get(
        _rest_url(f"/rest/v1/{TABLE_NAME}"),
        headers=_headers(),
        params={
            "select": "*",
            "owner_user_id": f"eq.{user_id}",
            "order": "created_at.desc",
            "limit": str(limit),
        },
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return []
    rows = response.json()
    return rows if isinstance(rows, list) else []


def _select_owned_audio_metadata(
    owner_user_id: str,
    *,
    audio_id: str = "",
    storage_path: str = "",
) -> dict[str, Any] | None:
    """Load one audio row through an owner-scoped service-role query.

    The service-role key bypasses RLS, so every lookup used for a privileged
    Storage operation must include the trusted owner identifier explicitly.
    """
    owner_user_id = require_owner_context(owner_user_id, allow_admin=True)
    normalized_audio_id = str(audio_id or "").strip()
    normalized_path = str(storage_path or "").strip()
    if not storage_is_configured() or not (normalized_audio_id or normalized_path):
        return None

    params = {
        "select": (
            "audio_id,owner_user_id,storage_bucket,storage_path,status,archived_at"
        ),
        "owner_user_id": f"eq.{owner_user_id}",
        "limit": "1",
    }
    if normalized_audio_id:
        params["audio_id"] = f"eq.{normalized_audio_id}"
    else:
        params["storage_path"] = f"eq.{normalized_path}"

    response = requests.get(
        _rest_url(f"/rest/v1/{TABLE_NAME}"),
        headers=_headers(),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return None
    rows = response.json()
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def _same_company(row: dict[str, Any], company_name: str, business_no: str) -> bool:
    target_no = normalize_business_no(business_no)
    row_no = normalize_business_no(
        str(row.get("business_no_normalized") or row.get("business_no") or "")
    )
    if target_no and row_no:
        return target_no == row_no
    target_name = _normalize_company_name(company_name)
    row_name = _normalize_company_name(str(row.get("company_name", "")))
    return bool(target_name and row_name and target_name == row_name)


def _is_archived(row: dict[str, Any]) -> bool:
    status = str(row.get("status", "") or "").strip().lower()
    return bool(
        status in {"archived", "inactive", "deleted"}
        or row.get("archived_at")
    )


def find_existing_audio(
    user_id: str,
    business_no: str,
    digest: str,
    company_name: str = "",
) -> dict[str, Any] | None:
    """같은 사용자의 동일 기업·동일 원본/저장파일 해시를 찾는다."""
    for row in _select_audio_rows(user_id):
        if _is_archived(row):
            continue
        if not _same_company(row, company_name, business_no):
            continue
        hashes = {
            str(row.get("audio_sha256", "")).strip(),
            str(row.get("original_audio_sha256", "")).strip(),
        }
        if digest and digest in hashes:
            return row
    return None


def _queue_audio_metadata_recovery(
    user_id: str,
    record: dict[str, Any],
    error_code: str,
) -> bool:
    """Persist a safe, idempotent metadata reconciliation job.

    The Storage object is deliberately retained.  The queue payload contains
    the metadata required to reconnect it, while queue errors are sanitized by
    the shared outbox implementation.
    """
    try:
        from sync_outbox import enqueue_outbox
        from utils import get_user_dirs

        queue_path = get_user_dirs(user_id)["base"] / "cloud_sync_queue.json"
        enqueue_outbox(
            queue_path,
            user_id,
            "audio_metadata_reconcile",
            TABLE_NAME,
            [dict(record)],
            "audio_id",
            error=error_code,
        )
        return True
    except Exception:
        # Never compensate by deleting the already uploaded customer original.
        return False


def upload_audio(
    user_id: str,
    user_name: str,
    company_name: str,
    business_no: str,
    filename: str,
    audio_bytes: bytes,
    content_type: str = "application/octet-stream",
    original_audio_sha256: str = "",
    original_filename: str = "",
    original_size_bytes: int | None = None,
) -> dict[str, Any]:
    """
    원본 녹음파일을 Supabase Storage에 저장하고 메타데이터를 기록한다.
    동일 회원·사업자번호·파일해시가 이미 있으면 기존 파일을 재사용한다.
    """
    user_id = require_owner_context(user_id)
    if not storage_is_configured():
        return {
            "stored": False,
            "message": (
                "Supabase Storage 설정이 없어 원본 음성은 영구 저장되지 않았습니다."
            ),
        }

    digest = _audio_hash(audio_bytes)
    source_digest = original_audio_sha256 or digest
    existing = find_existing_audio(
        user_id,
        business_no,
        source_digest,
        company_name=company_name,
    )
    if existing:
        return {
            "stored": True,
            "reused": True,
            "message": "동일한 녹음파일이 이미 저장되어 기존 파일을 연결했습니다.",
            "record": existing,
        }

    object_path = _object_path(
        user_id,
        company_name,
        business_no,
        filename,
        digest,
    )
    encoded_path = quote(object_path, safe="/")

    upload_response = requests.post(
        _rest_url(
            f"/storage/v1/object/{BUCKET_NAME}/{encoded_path}"
        ),
        headers={
            **_headers(content_type),
            "x-upsert": "false",
        },
        data=audio_bytes,
        timeout=REQUEST_TIMEOUT,
    )
    if not upload_response.ok:
        raise RuntimeError(
            "Supabase 음성파일 업로드 실패: "
            f"HTTP {upload_response.status_code}. 응답 본문은 보안상 표시하지 않습니다."
        )

    record = {
        "audio_id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "owner_user_id": user_id,
        "user_name": user_name,
        "company_name": company_name,
        "business_no": business_no,
        "business_no_normalized": normalize_business_no(business_no),
        "original_filename": original_filename or filename,
        "storage_bucket": BUCKET_NAME,
        "storage_path": object_path,
        "audio_sha256": digest,
        "original_audio_sha256": source_digest,
        "size_bytes": len(audio_bytes),
        "original_size_bytes": int(original_size_bytes or len(audio_bytes)),
        "content_type": content_type,
        "journal_id": "",
        "consultation_title": "",
        "summary": "",
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }

    metadata_response = requests.post(
        _rest_url(f"/rest/v1/{TABLE_NAME}"),
        headers={
            **_headers("application/json"),
            "Prefer": "return=representation",
        },
        data=json.dumps(record, ensure_ascii=False),
        timeout=REQUEST_TIMEOUT,
    )
    if not metadata_response.ok:
        # Storage에는 올라갔지만 메타데이터가 실패한 경우 파일을 지워 고아파일을 막는다.
        queued = _queue_audio_metadata_recovery(
            user_id,
            record,
            f"metadata_http_{metadata_response.status_code}",
        )
        raise RuntimeError(
            "음성파일 메타데이터 저장 실패: "
            + (
                " 메타데이터 복구 작업을 등록했습니다."
                if queued
                else " 메타데이터 복구 대기열을 확인해 주세요."
            )
        )

    rows = metadata_response.json()
    saved = rows[0] if isinstance(rows, list) and rows else record

    return {
        "stored": True,
        "reused": False,
        "message": "원본 녹음파일을 Supabase Storage에 영구 저장했습니다.",
        "record": saved,
    }


def link_audio_to_journal(
    audio_id: str,
    journal_id: str,
    consultation_title: str,
    summary: str,
    *,
    owner_user_id: str = "",
) -> bool:
    owner_user_id = require_owner_context(owner_user_id, allow_admin=True)
    if not storage_is_configured() or not audio_id:
        return False

    response = requests.patch(
        _rest_url(f"/rest/v1/{TABLE_NAME}"),
        headers={
            **_headers("application/json"),
            "Prefer": "return=representation",
        },
        params={
            "audio_id": f"eq.{audio_id}",
            "owner_user_id": f"eq.{owner_user_id}",
        },
        data=json.dumps(
            {
                "journal_id": journal_id,
                "consultation_title": consultation_title,
                "summary": summary,
            },
            ensure_ascii=False,
        ),
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return False
    rows = response.json() if response.text else []
    return bool(isinstance(rows, list) and rows)


def list_company_audio(
    user_id: str,
    business_no: str,
    company_name: str = "",
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """표기 차이를 무시하고 같은 기업의 클라우드 녹음을 조회한다."""
    rows = _select_audio_rows(user_id, limit=1000)
    return [
        row for row in rows
        if (include_archived or not _is_archived(row))
        if _same_company(row, company_name, business_no)
    ][:100]


def create_signed_audio_url(
    storage_path: str,
    expires_in: int = 3600,
    *,
    owner_user_id: str = "",
    audio_id: str = "",
) -> str:
    owner_user_id = require_owner_context(owner_user_id, allow_admin=True)
    requested_path = str(storage_path or "").strip()
    if not storage_is_configured() or not (audio_id or requested_path):
        return ""

    metadata = _select_owned_audio_metadata(
        owner_user_id,
        audio_id=audio_id,
        storage_path=requested_path if not audio_id else "",
    )
    if not metadata or _is_archived(metadata):
        return ""
    metadata_path = str(metadata.get("storage_path", "") or "").strip()
    metadata_bucket = str(metadata.get("storage_bucket", "") or BUCKET_NAME).strip()
    if not metadata_path or metadata_bucket != BUCKET_NAME:
        return ""
    if requested_path and requested_path != metadata_path:
        return ""

    encoded_path = quote(metadata_path, safe="/")
    safe_expires_in = max(60, min(int(expires_in or 3600), 3600))
    response = requests.post(
        _rest_url(
            f"/storage/v1/object/sign/{BUCKET_NAME}/{encoded_path}"
        ),
        headers=_headers("application/json"),
        data=json.dumps({"expiresIn": safe_expires_in}),
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return ""

    data = response.json()
    signed_path = data.get("signedURL") or data.get("signedUrl") or ""
    if not signed_path:
        return ""

    url, _ = _config()
    if signed_path.startswith("http"):
        return signed_path
    return f"{url}/storage/v1{signed_path}"


def archive_audio(
    audio_id: str,
    *,
    archived_by: str = "",
    reason: str = "사용자 보관 처리",
    owner_user_id: str = "",
) -> tuple[bool, str]:
    """Archive metadata while preserving the original Storage object."""
    owner_user_id = require_owner_context(
        owner_user_id,
        allow_admin=True,
    )
    if not storage_is_configured():
        return False, "Supabase Storage 설정을 확인해 주세요."
    if not str(audio_id or "").strip():
        return False, "보관할 녹취 식별값이 없습니다."

    response = requests.patch(
        _rest_url(f"/rest/v1/{TABLE_NAME}"),
        headers={
            **_headers("application/json"),
            "Prefer": "return=representation",
        },
        params={
            "audio_id": f"eq.{audio_id}",
            **(
                {"owner_user_id": f"eq.{owner_user_id}"}
                if owner_user_id
                else {}
            ),
        },
        data=json.dumps(
            {
                "status": "archived",
                "archived_at": datetime.now().isoformat(timespec="seconds"),
                "archived_by": str(archived_by or ""),
                "archive_reason": str(reason or "사용자 보관 처리")[:500],
            },
            ensure_ascii=False,
        ),
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return False, (
            "녹취 원본은 삭제하지 않았습니다. "
            "보관 상태 저장을 위해 데이터베이스 업데이트가 필요합니다."
        )
    rows = response.json() if response.text else []
    if not isinstance(rows, list) or not rows:
        return False, "녹취 원본은 유지했지만 보관 대상을 찾지 못했습니다."
    return True, "녹취를 보관 처리했습니다. 원본 파일과 이력은 유지됩니다."


def delete_audio(
    audio_id: str,
    storage_path: str,
    *,
    owner_user_id: str = "",
) -> tuple[bool, str]:
    """Backward-compatible UI action: delete now means non-destructive archive."""
    del storage_path
    return archive_audio(
        audio_id,
        owner_user_id=owner_user_id,
    )


def purge_audio_with_admin_approval(
    audio_id: str,
    storage_path: str,
    *,
    admin_approved: bool = False,
    owner_user_id: str = "",
) -> tuple[bool, str]:
    """Preserve the compatibility API while keeping physical deletion disabled."""
    del audio_id, storage_path, admin_approved, owner_user_id
    return False, "녹취 원본의 물리 삭제는 비활성화되어 있습니다. 보관 처리를 이용해 주세요."


def _purge_audio_unchecked(
    audio_id: str,
    storage_path: str,
    *,
    owner_user_id: str = "",
) -> tuple[bool, str]:
    """Compatibility guard: physical deletion is not an available operation."""
    del audio_id, storage_path, owner_user_id
    return False, "녹취 원본의 물리 삭제는 비활성화되어 있습니다."
