from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import requests


SERVICE_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
NPS_BASE_URL = (
    "https://apis.data.go.kr/B552015/"
    "NpsBplcInfoInqireServiceV2/getBassInfoSearchV2"
)
REGION_CODES = {
    "서울특별시": "11",
    "경기도": "41",
}


def _service_key() -> str:
    raw = os.environ.get(SERVICE_KEY_ENV, "").strip()
    if not raw:
        return ""
    # 공공데이터포털의 Encoding 키가 입력된 경우 requests가 다시
    # 인코딩하지 않도록 한 번만 원문으로 되돌린다.
    return unquote(raw)


def service_key_status() -> dict[str, Any]:
    key = _service_key()
    if not key:
        return {
            "configured": False,
            "masked": "미등록",
            "length": 0,
            "env_name": SERVICE_KEY_ENV,
        }
    visible = key[:4] if len(key) >= 4 else ""
    return {
        "configured": True,
        "masked": f"{visible}{'*' * min(max(len(key) - 4, 8), 24)}",
        "length": len(key),
        "env_name": SERVICE_KEY_ENV,
    }


def _xml_error(text: str) -> tuple[str, str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "", ""
    code = (
        root.findtext(".//resultCode")
        or root.findtext(".//returnReasonCode")
        or ""
    ).strip()
    message = (
        root.findtext(".//resultMsg")
        or root.findtext(".//returnAuthMsg")
        or root.findtext(".//errMsg")
        or ""
    ).strip()
    return code, message


def _items_from_json(payload: dict[str, Any]) -> tuple[str, str, int, list[dict]]:
    response = payload.get("response", payload)
    header = response.get("header", {}) if isinstance(response, dict) else {}
    body = response.get("body", {}) if isinstance(response, dict) else {}
    code = str(header.get("resultCode", "")).strip()
    message = str(header.get("resultMsg", "")).strip()
    total_count = int(body.get("totalCount") or 0) if isinstance(body, dict) else 0
    items_block = body.get("items", {}) if isinstance(body, dict) else {}
    if isinstance(items_block, dict):
        items = items_block.get("item", [])
    else:
        items = items_block
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []
    return code, message, total_count, [
        item for item in items if isinstance(item, dict)
    ]


def test_nps_connection(
    region_code: str = "11",
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    key = _service_key()
    if not key:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"Railway 환경변수 {SERVICE_KEY_ENV}가 없습니다.",
            "checked_at": checked_at,
        }
    if region_code not in REGION_CODES.values():
        return {
            "ok": False,
            "status": "INVALID_REGION",
            "message": "허용되지 않은 지역코드입니다.",
            "checked_at": checked_at,
        }

    params = {
        "serviceKey": key,
        "pageNo": 1,
        "numOfRows": 1,
        "dataType": "json",
        "ldongAddrMgplDgCd": region_code,
    }
    try:
        response = requests.get(
            NPS_BASE_URL,
            params=params,
            timeout=timeout,
            headers={"User-Agent": "OASIS-CRM/9.5.0"},
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "국민연금 API 응답시간이 초과되었습니다.",
            "checked_at": checked_at,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": f"국민연금 API 연결 실패: {type(exc).__name__}",
            "checked_at": checked_at,
        }

    common = {
        "http_status": response.status_code,
        "checked_at": checked_at,
        "region_code": region_code,
    }
    text = response.text or ""
    content_type = response.headers.get("content-type", "").lower()

    if "json" in content_type or text.lstrip().startswith(("{", "[")):
        try:
            payload = response.json()
        except ValueError:
            return {
                **common,
                "ok": False,
                "status": "INVALID_JSON",
                "message": "API가 해석할 수 없는 JSON을 반환했습니다.",
            }
        if not isinstance(payload, dict):
            return {
                **common,
                "ok": False,
                "status": "INVALID_RESPONSE",
                "message": "API 응답 구조가 예상과 다릅니다.",
            }
        code, message, total_count, items = _items_from_json(payload)
        success = response.ok and code in {"00", "0", ""}
        return {
            **common,
            "ok": success,
            "status": "CONNECTED" if success else (code or "API_ERROR"),
            "message": (
                "국민연금 사업장 API 연결에 성공했습니다."
                if success
                else (message or "API 인증 또는 요청조건을 확인해 주세요.")
            ),
            "result_code": code,
            "result_message": message,
            "total_count": total_count,
            "sample": items[:1],
            "response_format": "JSON",
        }

    code, message = _xml_error(text)
    success = response.ok and code in {"00", "0"} and not message
    return {
        **common,
        "ok": success,
        "status": "CONNECTED" if success else (code or "API_ERROR"),
        "message": (
            "국민연금 사업장 API 연결에 성공했습니다."
            if success
            else (message or f"API가 HTTP {response.status_code}을 반환했습니다.")
        ),
        "result_code": code,
        "result_message": message,
        "total_count": 0,
        "sample": [],
        "response_format": "XML",
    }

