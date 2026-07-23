from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import requests

from contact_matching import normalize_company_name, search_company_name


KIPRIS_KEY_ENV = "KIPRIS_API_KEY"
KIPRIS_BASE_URL = (
    "https://plus.kipris.or.kr/kipo-api/kipi/"
    "patUtiModInfoSearchSevice"
)
ADVANCED_SEARCH_URL = f"{KIPRIS_BASE_URL}/getAdvancedSearch"
APPLICANT_SEARCH_URL = f"{KIPRIS_BASE_URL}/applicantNameSearchInfo"
REJECTED_WORDS = ("거절", "취하", "포기", "무효")
INACTIVE_WORDS = REJECTED_WORDS + ("소멸", "말소")


def _service_key() -> str:
    return os.environ.get(KIPRIS_KEY_ENV, "").strip()


def key_status() -> dict[str, Any]:
    key = _service_key()
    return {
        "configured": bool(key),
        "env_name": KIPRIS_KEY_ENV,
        "masked": f"{key[:4]}{'*' * 12}" if key else "미등록",
    }


def _local_name(tag: str) -> str:
    return str(tag or "").split("}", 1)[-1].lower()


def _text(element: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in element.iter():
        if _local_name(child.tag) in wanted and child.text:
            return child.text.strip()
    return ""


def _parse_xml(content: bytes) -> tuple[str, str, int, list[dict[str, Any]]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise RuntimeError("KIPRIS 응답 XML을 해석할 수 없습니다.") from exc

    code = _text(root, "resultCode", "returnReasonCode")
    message = _text(root, "resultMsg", "returnAuthMsg", "returnReason")
    total_text = _text(root, "totalCount", "totalSearchCount")
    try:
        total_count = int(re.sub(r"[^0-9]", "", total_text) or "0")
    except ValueError:
        total_count = 0

    items: list[dict[str, Any]] = []
    for element in root.iter():
        if _local_name(element.tag) != "item":
            continue
        applicant_name = _text(element, "applicantName")
        application_number = _text(element, "applicationNumber")
        invention_title = _text(element, "inventionTitle")
        register_number = _text(element, "registerNumber")
        register_status = _text(element, "registerStatus")
        if not any(
            (
                applicant_name,
                application_number,
                invention_title,
                register_number,
            )
        ):
            continue
        status_compact = re.sub(r"\s+", "", register_status)
        registered = bool(register_number) and not any(
            word in status_compact for word in REJECTED_WORDS
        )
        active = registered and not any(
            word in status_compact for word in INACTIVE_WORDS
        )
        items.append(
            {
                "applicant_name": applicant_name,
                "application_number": application_number,
                "application_date": _text(element, "applicationDate"),
                "invention_title": invention_title,
                "register_number": register_number,
                "register_date": _text(element, "registerDate"),
                "register_status": register_status,
                "registered": registered,
                "active": active,
                "ipc_number": _text(element, "ipcNumber"),
            }
        )
    if not total_count:
        total_count = len(items)
    return code, message, total_count, items


def _applicant_matches(expected: str, candidate: str) -> bool:
    left = normalize_company_name(expected)
    right = normalize_company_name(candidate)
    if not left or not right:
        return False
    if left == right:
        return True
    return len(left) >= 4 and len(right) >= 4 and (
        left in right or right in left
    )


def _request(
    url: str,
    company_name: str,
    *,
    timeout: int,
    rows: int,
) -> dict[str, Any]:
    query_name = search_company_name(company_name)
    params = {
        "ServiceKey": _service_key(),
        "applicant": query_name,
        "applicantName": query_name,
        "patent": "true",
        "utility": "false",
        "pageNo": 1,
        "numOfRows": min(100, max(1, int(rows))),
    }
    response = requests.get(url, params=params, timeout=timeout)
    if not response.ok:
        return {
            "ok": False,
            "status": f"HTTP_{response.status_code}",
            "message": f"KIPRIS가 HTTP {response.status_code}을 반환했습니다.",
            "items": [],
        }
    code, message, total_count, items = _parse_xml(response.content)
    ok = code in {"", "00", "0"}
    return {
        "ok": ok,
        "status": "CONNECTED" if ok else (code or "API_ERROR"),
        "message": message or "KIPRIS 응답 수신",
        "total_count": total_count,
        "items": items,
    }


def search_registered_patents(
    company_name: str,
    *,
    timeout: int = 12,
    rows: int = 50,
) -> dict[str, Any]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"{KIPRIS_KEY_ENV}가 없습니다.",
            "company_name": company_name,
            "registered_count": 0,
            "active_count": 0,
            "patents": [],
            "checked_at": checked_at,
        }
    try:
        result = _request(
            ADVANCED_SEARCH_URL,
            company_name,
            timeout=timeout,
            rows=rows,
        )
        if result.get("ok") and not result.get("items"):
            result = _request(
                APPLICANT_SEARCH_URL,
                company_name,
                timeout=timeout,
                rows=rows,
            )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "KIPRIS 특허조회 응답시간이 초과되었습니다.",
            "company_name": company_name,
            "registered_count": 0,
            "active_count": 0,
            "patents": [],
            "checked_at": checked_at,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": f"KIPRIS 연결 실패: {type(exc).__name__}",
            "company_name": company_name,
            "registered_count": 0,
            "active_count": 0,
            "patents": [],
            "checked_at": checked_at,
        }
    except RuntimeError as exc:
        return {
            "ok": False,
            "status": "INVALID_RESPONSE",
            "message": str(exc),
            "company_name": company_name,
            "registered_count": 0,
            "active_count": 0,
            "patents": [],
            "checked_at": checked_at,
        }

    if not result.get("ok"):
        return {
            **result,
            "company_name": company_name,
            "registered_count": 0,
            "active_count": 0,
            "patents": [],
            "checked_at": checked_at,
        }

    matched = [
        row
        for row in result.get("items", [])
        if _applicant_matches(company_name, row.get("applicant_name", ""))
    ]
    registered = [row for row in matched if row.get("registered")]
    active = [row for row in registered if row.get("active")]
    return {
        "ok": True,
        "status": "CONNECTED",
        "message": (
            f"등록특허 {len(registered)}건 확인"
            if registered
            else "일치하는 등록특허 없음"
        ),
        "company_name": company_name,
        "matched_count": len(matched),
        "registered_count": len(registered),
        "active_count": len(active),
        "patents": registered,
        "checked_at": checked_at,
    }


def test_connection(timeout: int = 12) -> dict[str, Any]:
    result = search_registered_patents("삼성전자", timeout=timeout, rows=1)
    return {
        "ok": result.get("ok", False),
        "status": result.get("status", "-"),
        "message": (
            "KIPRIS 특허 API 연결에 성공했습니다."
            if result.get("ok")
            else result.get("message", "KIPRIS 연결 실패")
        ),
        "checked_at": result.get("checked_at"),
    }
