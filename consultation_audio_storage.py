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


BUCKET_NAME = "oasis-consultation-audio"
TABLE_NAME = "oasis_consultation_audio"
REQUEST_TIMEOUT = 180


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
    digits = re.sub(r"[^0-9]", "", business_no or "")
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


def find_existing_audio(
    user_id: str,
    business_no: str,
    digest: str,
) -> dict[str, Any] | None:
    if not storage_is_configured():
        return None

    params = {
        "select": "*",
        "owner_user_id": f"eq.{user_id}",
        "business_no": f"eq.{business_no}",
        "audio_sha256": f"eq.{digest}",
        "order": "created_at.desc",
        "limit": "1",
    }
    response = requests.get(
        _rest_url(f"/rest/v1/{TABLE_NAME}"),
        headers=_headers(),
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return None

    rows = response.json()
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def upload_audio(
    user_id: str,
    user_name: str,
    company_name: str,
    business_no: str,
    filename: str,
    audio_bytes: bytes,
    content_type: str = "application/octet-stream",
) -> dict[str, Any]:
    """
    원본 녹음파일을 Supabase Storage에 저장하고 메타데이터를 기록한다.
    동일 회원·사업자번호·파일해시가 이미 있으면 기존 파일을 재사용한다.
    """
    if not storage_is_configured():
        return {
            "stored": False,
            "message": (
                "Supabase Storage 설정이 없어 원본 음성은 영구 저장되지 않았습니다."
            ),
        }

    digest = _audio_hash(audio_bytes)
    existing = find_existing_audio(
        user_id,
        business_no,
        digest,
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
            f"HTTP {upload_response.status_code} "
            f"{upload_response.text[:500]}"
        )

    record = {
        "audio_id": datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "owner_user_id": user_id,
        "user_name": user_name,
        "company_name": company_name,
        "business_no": business_no,
        "original_filename": filename,
        "storage_bucket": BUCKET_NAME,
        "storage_path": object_path,
        "audio_sha256": digest,
        "size_bytes": len(audio_bytes),
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
        requests.delete(
            _rest_url(
                f"/storage/v1/object/{BUCKET_NAME}/{encoded_path}"
            ),
            headers=_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        raise RuntimeError(
            "음성파일 메타데이터 저장 실패: "
            f"HTTP {metadata_response.status_code} "
            f"{metadata_response.text[:500]}"
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
) -> bool:
    if not storage_is_configured() or not audio_id:
        return False

    response = requests.patch(
        _rest_url(f"/rest/v1/{TABLE_NAME}"),
        headers={
            **_headers("application/json"),
            "Prefer": "return=minimal",
        },
        params={"audio_id": f"eq.{audio_id}"},
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
    return response.ok


def list_company_audio(
    user_id: str,
    business_no: str,
) -> list[dict[str, Any]]:
    if not storage_is_configured():
        return []

    response = requests.get(
        _rest_url(f"/rest/v1/{TABLE_NAME}"),
        headers=_headers(),
        params={
            "select": "*",
            "owner_user_id": f"eq.{user_id}",
            "business_no": f"eq.{business_no}",
            "order": "created_at.desc",
            "limit": "100",
        },
        timeout=REQUEST_TIMEOUT,
    )
    if not response.ok:
        return []

    rows = response.json()
    return rows if isinstance(rows, list) else []


def create_signed_audio_url(
    storage_path: str,
    expires_in: int = 3600,
) -> str:
    if not storage_is_configured() or not storage_path:
        return ""

    encoded_path = quote(storage_path, safe="/")
    response = requests.post(
        _rest_url(
            f"/storage/v1/object/sign/{BUCKET_NAME}/{encoded_path}"
        ),
        headers=_headers("application/json"),
        data=json.dumps({"expiresIn": expires_in}),
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


def delete_audio(audio_id: str, storage_path: str) -> tuple[bool, str]:
    if not storage_is_configured():
        return False, "Supabase Storage가 설정되지 않았습니다."

    encoded_path = quote(storage_path, safe="/")
    file_response = requests.delete(
        _rest_url(
            f"/storage/v1/object/{BUCKET_NAME}/{encoded_path}"
        ),
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )

    metadata_response = requests.delete(
        _rest_url(f"/rest/v1/{TABLE_NAME}"),
        headers=_headers(),
        params={"audio_id": f"eq.{audio_id}"},
        timeout=REQUEST_TIMEOUT,
    )

    if file_response.ok and metadata_response.ok:
        return True, "음성파일과 연결정보를 삭제했습니다."
    return False, "음성파일 삭제 중 일부 작업이 완료되지 않았습니다."
