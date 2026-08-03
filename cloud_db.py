from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from performance_cache import invalidate_cache


TABLE_CUSTOMERS = "oasis_customers"
TABLE_CRM = "oasis_crm"
TABLE_FINANCIALS = "oasis_financials"
TABLE_REGISTRY = "oasis_registry"
TABLE_STOCK = "oasis_stock_valuations"
TABLE_MIGRATIONS = "oasis_migration_runs"
TABLE_MATCHING_PREFERENCES = "oasis_matching_preferences"
TABLE_SYNC_OUTBOX = "oasis_sync_outbox"
TABLE_CUSTOMER_ASSETS = "oasis_customer_assets"
TABLE_COPILOT_ASSETS = "oasis_copilot_assets"
TABLE_BACKUP_RUNS = "oasis_backup_runs"
TABLE_RESTORE_DRILLS = "oasis_restore_drills"
TABLE_CUSTOMER_ARCHIVE_EVENTS = "oasis_customer_archive_events"
PRIVATE_CUSTOMER_ASSET_BUCKET = "oasis-customer-assets"


def _invalidate_written_rows(
    table: str,
    rows: list[dict[str, Any]],
) -> None:
    """Invalidate only read caches affected by a successful write."""
    if table != TABLE_CUSTOMERS:
        return
    owner_ids = {
        str(row.get("owner_user_id", "") or "").strip().lower()
        for row in rows
        if isinstance(row, dict)
    }
    for owner_id in owner_ids:
        if owner_id:
            invalidate_cache("registered_customers", owner_id)


def normalize_business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return str(value or "").strip()


def _read_secret(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    if value:
        return str(value).strip()

    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass

    return default


@dataclass
class CloudConfig:
    url: str
    secret_key: str
    timeout: int = 20

    @property
    def configured(self) -> bool:
        return bool(self.url and self.secret_key)


def get_cloud_config() -> CloudConfig:
    return CloudConfig(
        url=_read_secret("SUPABASE_URL").rstrip("/"),
        secret_key=(
            _read_secret("SUPABASE_SECRET_KEY")
            or _read_secret("SUPABASE_SERVICE_ROLE_KEY")
        ),
    )


class CloudDatabase:
    def __init__(self, config: CloudConfig | None = None):
        self.config = config or get_cloud_config()
        if not self.config.configured:
            raise RuntimeError(
                "SUPABASE_URL과 SUPABASE_SECRET_KEY가 설정되지 않았습니다."
            )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "apikey": self.config.secret_key,
            "Authorization": f"Bearer {self.config.secret_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }

    def _url(self, table: str) -> str:
        return f"{self.config.url}/rest/v1/{table}"

    def health_check(self) -> tuple[bool, str]:
        try:
            response = requests.get(
                self._url(TABLE_CUSTOMERS),
                headers=self.headers,
                params={"select": "id", "limit": "1"},
                timeout=self.config.timeout,
            )
            if response.ok:
                return True, "Supabase 연결 및 테이블 확인이 완료되었습니다."
            return False, (
                f"연결 실패 HTTP {response.status_code}: "
                f"{response.text[:300]}"
            )
        except requests.RequestException as exc:
            return False, f"Supabase 연결 실패: {exc}"

    def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        on_conflict: str,
    ) -> list[dict[str, Any]]:
        if not rows:
            return []

        headers = dict(self.headers)
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        response = requests.post(
            self._url(table),
            headers=headers,
            params={"on_conflict": on_conflict},
            data=json.dumps(rows, ensure_ascii=False, default=str),
            timeout=self.config.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"{table} 저장 실패 HTTP {response.status_code}: "
                f"{response.text[:800]}"
            )
        result = response.json() if response.text else []
        _invalidate_written_rows(table, rows)
        return result

    def insert(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return []
        response = requests.post(
            self._url(table),
            headers=self.headers,
            data=json.dumps(rows, ensure_ascii=False, default=str),
            timeout=self.config.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"{table} 저장 실패 HTTP {response.status_code}: "
                f"{response.text[:800]}"
            )
        result = response.json() if response.text else []
        _invalidate_written_rows(table, rows)
        return result

    def rpc(
        self,
        function_name: str,
        parameters: dict[str, Any],
    ) -> Any:
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "", str(function_name or ""))
        if not safe_name or safe_name != function_name:
            raise ValueError("올바르지 않은 RPC 함수명입니다.")
        response = requests.post(
            f"{self.config.url}/rest/v1/rpc/{safe_name}",
            headers=self.headers,
            data=json.dumps(parameters, ensure_ascii=False, default=str),
            timeout=self.config.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"{safe_name} 실행 실패 HTTP {response.status_code}: "
                f"{response.text[:800]}"
            )
        return response.json() if response.text else None

    def upload_private_object(
        self,
        bucket: str,
        path: str,
        content: bytes,
        content_type: str,
    ) -> None:
        safe_bucket = re.sub(r"[^a-zA-Z0-9_-]", "", str(bucket or ""))
        clean_path = str(path or "").strip().lstrip("/")
        if (
            not safe_bucket
            or safe_bucket != bucket
            or not clean_path
            or ".." in clean_path.split("/")
        ):
            raise ValueError("올바르지 않은 Storage 경로입니다.")
        headers = {
            "apikey": self.config.secret_key,
            "Authorization": f"Bearer {self.config.secret_key}",
            "Content-Type": str(content_type or "application/octet-stream"),
            "x-upsert": "false",
        }
        encoded_path = quote(clean_path, safe="/")
        response = requests.post(
            (
                f"{self.config.url}/storage/v1/object/"
                f"{safe_bucket}/{encoded_path}"
            ),
            headers=headers,
            data=content,
            timeout=max(self.config.timeout, 60),
        )
        if not response.ok:
            raise RuntimeError(
                f"Storage 업로드 실패 HTTP {response.status_code}"
            )

    def delete_private_object(self, bucket: str, path: str) -> None:
        safe_bucket = re.sub(r"[^a-zA-Z0-9_-]", "", str(bucket or ""))
        clean_path = str(path or "").strip().lstrip("/")
        if not safe_bucket or not clean_path:
            return
        requests.delete(
            f"{self.config.url}/storage/v1/object/{safe_bucket}",
            headers=self.headers,
            data=json.dumps({"prefixes": [clean_path]}),
            timeout=self.config.timeout,
        )

    def create_private_signed_url(
        self,
        bucket: str,
        path: str,
        *,
        expires_in: int = 60,
        download_name: str = "",
    ) -> str:
        safe_bucket = re.sub(r"[^a-zA-Z0-9_-]", "", str(bucket or ""))
        clean_path = str(path or "").strip().lstrip("/")
        if not safe_bucket or not clean_path:
            raise ValueError("올바르지 않은 Storage 경로입니다.")
        encoded_path = quote(clean_path, safe="/")
        payload: dict[str, Any] = {
            "expiresIn": max(10, min(int(expires_in), 300)),
        }
        if download_name:
            payload["download"] = str(download_name)
        response = requests.post(
            (
                f"{self.config.url}/storage/v1/object/sign/"
                f"{safe_bucket}/{encoded_path}"
            ),
            headers=self.headers,
            data=json.dumps(payload, ensure_ascii=False),
            timeout=self.config.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"Storage 다운로드 링크 생성 실패 HTTP "
                f"{response.status_code}"
            )
        data = response.json() if response.text else {}
        signed_url = str(
            data.get("signedURL") or data.get("signedUrl") or ""
        ).strip()
        if not signed_url:
            raise RuntimeError("Storage 다운로드 링크를 받지 못했습니다.")
        if signed_url.startswith("http"):
            return signed_url
        if signed_url.startswith("/storage/v1/"):
            return f"{self.config.url}{signed_url}"
        return (
            f"{self.config.url}/storage/v1/"
            f"{signed_url.lstrip('/')}"
        )

    def select(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        columns: str = "*",
        order: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"select": columns}

        for key, value in (filters or {}).items():
            params[key] = f"eq.{value}"

        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(int(limit))
        if offset is not None:
            params["offset"] = str(max(0, int(offset)))

        response = requests.get(
            self._url(table),
            headers=self.headers,
            params=params,
            timeout=self.config.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"{table} 조회 실패 HTTP {response.status_code}: "
                f"{response.text[:800]}"
            )

        data = response.json() if response.text else []
        return data if isinstance(data, list) else []

    def select_all(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        columns: str = "*",
        order: str | None = None,
        *,
        page_size: int = 1000,
        max_rows: int = 100000,
    ) -> list[dict[str, Any]]:
        """Read a bounded result set in stable server-side pages."""
        safe_page_size = max(1, min(int(page_size), 1000))
        safe_max_rows = max(1, int(max_rows))
        rows: list[dict[str, Any]] = []
        offset = 0
        while offset < safe_max_rows:
            batch = self.select(
                table,
                filters=filters,
                columns=columns,
                order=order,
                limit=min(safe_page_size, safe_max_rows - offset),
                offset=offset,
            )
            rows.extend(batch)
            if len(batch) < safe_page_size:
                break
            offset += len(batch)
        return rows


    def count(self, table: str, owner_user_id: str | None = None) -> int:
        headers = dict(self.headers)
        headers["Prefer"] = "count=exact"
        params = {"select": "id"}
        if owner_user_id:
            params["owner_user_id"] = f"eq.{owner_user_id}"

        response = requests.get(
            self._url(table),
            headers=headers,
            params=params,
            timeout=self.config.timeout,
        )
        if not response.ok:
            raise RuntimeError(
                f"{table} 조회 실패 HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        content_range = response.headers.get("Content-Range", "")
        if "/" in content_range:
            total = content_range.split("/")[-1]
            if total.isdigit():
                return int(total)

        data = response.json() if response.text else []
        return len(data)


def cloud_is_configured() -> bool:
    return get_cloud_config().configured
