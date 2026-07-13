from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests


TABLE_CUSTOMERS = "oasis_customers"
TABLE_CRM = "oasis_crm"
TABLE_FINANCIALS = "oasis_financials"
TABLE_REGISTRY = "oasis_registry"
TABLE_STOCK = "oasis_stock_valuations"
TABLE_MIGRATIONS = "oasis_migration_runs"
TABLE_MATCHING_PREFERENCES = "oasis_matching_preferences"


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
        return response.json() if response.text else []

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
        return response.json() if response.text else []

    def select(
        self,
        table: str,
        filters: dict[str, Any] | None = None,
        columns: str = "*",
        order: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"select": columns}

        for key, value in (filters or {}).items():
            params[key] = f"eq.{value}"

        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(int(limit))

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
