from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import unquote

import requests

from cloud_db import (
    TABLE_CUSTOMER_PROCUREMENT,
    CloudDatabase,
    normalize_business_no,
)


SERVICE_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
SUPPLIER_INFO_URL = (
    "https://apis.data.go.kr/1230000/ao/UsrInfoService02/"
    "getPrcrmntCorpBasicInfo02"
)
PROCUREMENT_STATS_URL = (
    "https://apis.data.go.kr/1230000/at/PubPrcrmntStatInfoService/"
    "getPrcrmntEntrprsAccotBsnsObjAccotArslt"
)
SUCCESS_CODES = {"", "0", "00", "000", "NORMAL SERVICE."}


class ProcurementConfigError(RuntimeError):
    pass


class ProcurementAPIError(RuntimeError):
    pass


def _read_service_key() -> str:
    raw = os.environ.get(SERVICE_KEY_ENV, "").strip()
    if not raw:
        try:
            import streamlit as st

            raw = str(st.secrets.get(SERVICE_KEY_ENV, "") or "").strip()
        except Exception:
            raw = ""
    return unquote(raw) if raw else ""


def _business_digits(value: Any) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def _normal_company_name(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\(\s*주\s*\)|（\s*주\s*）|㈜", "주식회사", text)
    text = re.sub(r"[\s·ㆍ.,()（）\[\]{}_-]+", "", text)
    if text.startswith("주식회사"):
        text = text[len("주식회사") :]
    elif text.endswith("주식회사"):
        text = text[: -len("주식회사")]
    return text.casefold()


def _to_int(value: Any) -> int:
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def _month_shift(value: date, months: int) -> date:
    month_index = value.year * 12 + (value.month - 1) + int(months)
    year, month = divmod(month_index, 12)
    return date(year, month + 1, 1)


def default_query_period(today: date | None = None) -> tuple[str, str]:
    end = (today or date.today()).replace(day=1)
    start = _month_shift(end, -35)
    return start.strftime("%Y%m"), end.strftime("%Y%m")


def _items_from_xml(text: str) -> tuple[str, str, int, list[dict[str, Any]]]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ProcurementAPIError("조달청 API 응답을 해석하지 못했습니다.") from exc
    code = (root.findtext(".//resultCode") or "").strip()
    message = (root.findtext(".//resultMsg") or "").strip()
    total = _to_int(root.findtext(".//totalCount"))
    items: list[dict[str, Any]] = []
    for node in root.findall(".//item"):
        items.append({child.tag: child.text or "" for child in list(node)})
    return code, message, total, items


def _items_from_response(response: Any) -> tuple[str, str, int, list[dict[str, Any]]]:
    try:
        payload = response.json()
    except (ValueError, TypeError, AttributeError):
        return _items_from_xml(str(getattr(response, "text", "") or ""))

    root = payload.get("response", payload) if isinstance(payload, dict) else {}
    header = root.get("header", {}) if isinstance(root, dict) else {}
    body = root.get("body", {}) if isinstance(root, dict) else {}
    code = str(header.get("resultCode", "") or "").strip()
    message = str(header.get("resultMsg", "") or "").strip()
    total = _to_int(body.get("totalCount")) if isinstance(body, dict) else 0
    items_block = body.get("items", []) if isinstance(body, dict) else []
    if isinstance(items_block, dict):
        items = items_block.get("item", [])
    else:
        items = items_block
    if not items and isinstance(body, dict):
        items = body.get("item", [])
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []
    return code, message, total, [item for item in items if isinstance(item, dict)]


class PublicProcurementClient:
    def __init__(
        self,
        service_key: str | None = None,
        *,
        session: requests.Session | None = None,
        timeout: int = 30,
        retries: int = 2,
    ) -> None:
        self.service_key = (service_key if service_key is not None else _read_service_key()).strip()
        if not self.service_key:
            raise ProcurementConfigError(
                "공공데이터포털 API 키가 설정되지 않았습니다."
            )
        self.session = session or requests.Session()
        self.timeout = max(5, int(timeout))
        self.retries = max(0, int(retries))

    def _get_page(
        self,
        url: str,
        params: dict[str, Any],
    ) -> tuple[int, list[dict[str, Any]]]:
        safe_params = {"serviceKey": self.service_key, **params}
        last_error = "조달청 API 연결에 실패했습니다."
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=safe_params,
                    timeout=self.timeout,
                    headers={"User-Agent": "OASIS-CRM/public-procurement"},
                )
            except requests.RequestException as exc:
                last_error = f"조달청 API 연결 실패: {type(exc).__name__}"
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProcurementAPIError(last_error) from exc

            status_code = int(getattr(response, "status_code", 0) or 0)
            if status_code == 429 or status_code >= 500:
                last_error = f"조달청 API가 일시적으로 응답하지 않습니다. (HTTP {status_code})"
                if attempt < self.retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise ProcurementAPIError(last_error)
            if status_code < 200 or status_code >= 300:
                raise ProcurementAPIError(
                    f"조달청 API 조회에 실패했습니다. (HTTP {status_code})"
                )

            code, message, total, items = _items_from_response(response)
            if code.upper() not in SUCCESS_CODES:
                raise ProcurementAPIError(
                    f"조달청 API 오류: {message or code}"
                )
            return total, items
        raise ProcurementAPIError(last_error)

    def find_supplier(self, business_no: Any) -> dict[str, Any] | None:
        digits = _business_digits(business_no)
        if len(digits) != 10:
            raise ValueError("사업자등록번호 10자리를 확인해 주세요.")
        _, items = self._get_page(
            SUPPLIER_INFO_URL,
            {
                "pageNo": 1,
                "numOfRows": 100,
                "inqryDiv": 1,
                "bizno": digits,
                "type": "json",
            },
        )
        exact = [
            item
            for item in items
            if _business_digits(item.get("bizno")) == digits
            and str(item.get("corpNm") or "").strip()
        ]
        if not exact:
            return None
        exact.sort(
            key=lambda item: str(item.get("chgDt") or item.get("rgstDt") or ""),
            reverse=True,
        )
        item = exact[0]
        return {
            "supplier_name": str(item.get("corpNm") or "").strip(),
            "supplier_unity_no": str(item.get("corpUntyNo") or "").strip(),
        }

    def _stats_items(
        self,
        supplier_name: str,
        supplier_unity_no: str,
        start_ym: str,
        end_ym: str,
    ) -> list[dict[str, Any]]:
        page = 1
        page_size = 100
        collected: list[dict[str, Any]] = []
        while page <= 100:
            total, items = self._get_page(
                PROCUREMENT_STATS_URL,
                {
                    "pageNo": page,
                    "numOfRows": page_size,
                    "srchBssYmBgn": start_ym,
                    "srchBssYmEnd": end_ym,
                    "corpUntyNo": supplier_unity_no,
                    "corpNm": supplier_name,
                    "linkSystmCd": "",
                    "type": "json",
                },
            )
            collected.extend(items)
            if not items or len(collected) >= total or len(items) < page_size:
                break
            page += 1
        return collected

    def collect_summary(
        self,
        business_no: Any,
        *,
        start_ym: str | None = None,
        end_ym: str | None = None,
    ) -> dict[str, Any]:
        normalized_business_no = normalize_business_no(business_no)
        if len(_business_digits(normalized_business_no)) != 10:
            raise ValueError("사업자등록번호 10자리를 확인해 주세요.")
        default_start, default_end = default_query_period()
        start_ym = str(start_ym or default_start)
        end_ym = str(end_ym or default_end)
        if not re.fullmatch(r"[0-9]{6}", start_ym) or not re.fullmatch(r"[0-9]{6}", end_ym):
            raise ValueError("조회기간은 YYYYMM 형식이어야 합니다.")
        if start_ym > end_ym:
            raise ValueError("조회 시작월은 종료월보다 늦을 수 없습니다.")

        supplier = self.find_supplier(normalized_business_no)
        base = {
            "business_no": normalized_business_no,
            "supplier_name": "",
            "supplier_unity_no": "",
            "query_start_ym": start_ym,
            "query_end_ym": end_ym,
            "total_count": 0,
            "total_amount": 0,
            "product_count": 0,
            "product_amount": 0,
            "construction_count": 0,
            "construction_amount": 0,
            "general_service_count": 0,
            "general_service_amount": 0,
            "technical_service_count": 0,
            "technical_service_amount": 0,
            "unclassified_count": 0,
            "unclassified_amount": 0,
            "source_systems": [],
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        if supplier is None:
            return {**base, "match_status": "not_registered"}

        base.update(supplier)
        items = self._stats_items(
            supplier["supplier_name"],
            supplier["supplier_unity_no"],
            start_ym,
            end_ym,
        )
        target_name = _normal_company_name(supplier["supplier_name"])
        matched = [
            item
            for item in items
            if _normal_company_name(item.get("corpNm")) == target_name
        ]
        if not matched:
            return {**base, "match_status": "not_found"}

        unity_numbers = {
            str(item.get("corpUntyNo") or "").strip()
            for item in matched
            if str(item.get("corpUntyNo") or "").strip()
        }
        expected_unity = supplier["supplier_unity_no"]
        if expected_unity:
            matched = [
                item
                for item in matched
                if not str(item.get("corpUntyNo") or "").strip()
                or str(item.get("corpUntyNo") or "").strip() == expected_unity
            ]
            unity_numbers = {expected_unity}
        if len(unity_numbers) > 1:
            return {**base, "match_status": "ambiguous"}

        field_map = {
            "total_count": "arsltSumNum",
            "total_amount": "arsltSumAmt",
            "product_count": "prdctArsltNum",
            "product_amount": "prdctArsltAmt",
            "construction_count": "cnstwkArsltNum",
            "construction_amount": "cnstwkArsltAmt",
            "general_service_count": "gnrlSrvceArsltNum",
            "general_service_amount": "gnrlSrvceArsltAmt",
            "technical_service_count": "techSrvceArsltNum",
            "technical_service_amount": "techSrvceArsltAmt",
            "unclassified_count": "unClsfcArsltNum",
            "unclassified_amount": "unClsfcArsltAmt",
        }
        for output_key, source_key in field_map.items():
            base[output_key] = sum(_to_int(item.get(source_key)) for item in matched)
        base["source_systems"] = sorted(
            {
                str(item.get("linkSystmNm") or "").strip()
                for item in matched
                if str(item.get("linkSystmNm") or "").strip()
            }
        )
        if unity_numbers and not base["supplier_unity_no"]:
            base["supplier_unity_no"] = sorted(unity_numbers)[0]
        return {**base, "match_status": "matched"}


def load_procurement_summary(
    owner_user_id: str,
    business_no: Any,
    *,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    owner = str(owner_user_id or "").strip().lower()
    normalized_business_no = normalize_business_no(business_no)
    if not owner or len(_business_digits(normalized_business_no)) != 10:
        return {}
    db = database or CloudDatabase()
    rows = db.select(
        TABLE_CUSTOMER_PROCUREMENT,
        filters={
            "owner_user_id": owner,
            "business_no": normalized_business_no,
        },
        limit=1,
    )
    return dict(rows[0]) if rows else {}


def save_procurement_summary(
    owner_user_id: str,
    summary: dict[str, Any],
    *,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    owner = str(owner_user_id or "").strip().lower()
    if not owner:
        raise ValueError("사용자 정보가 없습니다.")
    allowed = {
        "business_no",
        "supplier_name",
        "supplier_unity_no",
        "query_start_ym",
        "query_end_ym",
        "total_count",
        "total_amount",
        "product_count",
        "product_amount",
        "construction_count",
        "construction_amount",
        "general_service_count",
        "general_service_amount",
        "technical_service_count",
        "technical_service_amount",
        "unclassified_count",
        "unclassified_amount",
        "source_systems",
        "match_status",
        "collected_at",
    }
    row = {key: summary[key] for key in allowed if key in summary}
    row["owner_user_id"] = owner
    row["business_no"] = normalize_business_no(row.get("business_no"))
    row["updated_at"] = datetime.now(timezone.utc).isoformat()
    db = database or CloudDatabase()
    saved = db.upsert(
        TABLE_CUSTOMER_PROCUREMENT,
        [row],
        on_conflict="owner_user_id,business_no",
    )
    return dict(saved[0]) if saved else row


def refresh_procurement_summary(
    owner_user_id: str,
    business_no: Any,
    *,
    client: PublicProcurementClient | None = None,
    database: CloudDatabase | None = None,
) -> dict[str, Any]:
    summary = (client or PublicProcurementClient()).collect_summary(business_no)
    return save_procurement_summary(owner_user_id, summary, database=database)
