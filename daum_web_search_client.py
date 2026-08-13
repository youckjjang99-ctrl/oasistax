from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime
from threading import Lock
from typing import Any

import requests

from contact_matching import (
    address_hint,
    address_match_score,
    company_score,
    is_mobile_phone,
    normalize_phone,
    search_company_name,
)


KAKAO_KEY_ENV = "KAKAO_REST_API_KEY"
DAUM_WEB_URL = "https://dapi.kakao.com/v2/search/web"
MIN_COMPANY_SCORE = 38
MIN_ADDRESS_SCORE = 10
AUTO_CONFIRM_SCORE = 85
MIN_REQUEST_INTERVAL_SECONDS = 0.11
_REQUEST_LOCK = Lock()
_LAST_REQUEST_AT = 0.0


def key_status() -> dict[str, Any]:
    key = os.environ.get(KAKAO_KEY_ENV, "").strip()
    return {
        "configured": bool(key),
        "env_name": KAKAO_KEY_ENV,
        "masked": f"{key[:4]}{'*' * 12}" if key else "미등록",
    }


def _headers() -> dict[str, str]:
    return {
        "Authorization": (
            f"KakaoAK {os.environ.get(KAKAO_KEY_ENV, '').strip()}"
        ),
        "User-Agent": "OASIS-CRM/9.8.1",
    }


def _plain(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"<[^>]+>", " ", text).strip()


def _phones_from_text(value: Any) -> list[str]:
    text = _plain(value)
    pattern = (
        r"(?<!\d)(?:\+?82[\s().-]?)?"
        r"(?:0?2|0?1[016789]|0?[3-6][1-5]|0?50|0?70|0?80)"
        r"[\s().-]*\d{3,4}[\s.-]*\d{4}(?!\d)"
        r"|(?<!\d)1[568]\d{2}[\s.-]*\d{4}(?!\d)"
    )
    return list(
        dict.fromkeys(
            normalized
            for raw in re.findall(pattern, text)
            if (normalized := normalize_phone(raw))
        )
    )


def _wait_for_request_slot() -> None:
    global _LAST_REQUEST_AT
    with _REQUEST_LOCK:
        now = time.monotonic()
        wait_seconds = (
            _LAST_REQUEST_AT + MIN_REQUEST_INTERVAL_SECONDS - now
        )
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        _LAST_REQUEST_AT = time.monotonic()


def _empty_result(
    status: str,
    message: str,
    query: str = "",
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "message": message,
        "queries": [query] if query else [],
        "candidates": [],
        "contacts": [],
        "trace": [
            {
                "stage": "daum_web_phone",
                "status": status,
                "message": message,
            }
        ],
    }


def search_public_phones(
    company_name: str,
    address: str,
    *,
    timeout: int = 6,
    size: int = 10,
) -> dict[str, Any]:
    """Find public phones using one Daum Web request per company."""
    if not key_status()["configured"]:
        return _empty_result(
            "KEY_MISSING",
            "다음웹 검색에 필요한 Kakao REST API 키가 없습니다.",
        )

    base_name = search_company_name(company_name)
    query = (
        f'"{base_name}" {address_hint(address)} 전화 연락처'
    ).strip()
    try:
        _wait_for_request_slot()
        response = requests.get(
            DAUM_WEB_URL,
            headers=_headers(),
            params={
                "query": query,
                "size": min(50, max(1, int(size))),
            },
            timeout=max(2, int(timeout)),
        )
    except requests.Timeout:
        return _empty_result(
            "TIMEOUT",
            "다음웹 검색 응답시간이 초과되었습니다.",
            query,
        )
    except requests.RequestException:
        return _empty_result(
            "NETWORK_ERROR",
            "다음웹 검색 연결에 실패했습니다.",
            query,
        )
    if not response.ok:
        return _empty_result(
            f"HTTP_{response.status_code}",
            f"다음웹 검색 HTTP_{response.status_code}",
            query,
        )
    try:
        payload = response.json()
    except ValueError:
        return _empty_result(
            "INVALID_JSON",
            "다음웹 검색 응답을 해석하지 못했습니다.",
            query,
        )

    candidates: list[dict[str, Any]] = []
    contacts: list[dict[str, Any]] = []
    seen_phones: set[str] = set()
    documents = payload.get("documents", []) if isinstance(payload, dict) else []
    for document in documents:
        if not isinstance(document, dict):
            continue
        title = _plain(document.get("title"))
        contents = _plain(document.get("contents"))
        source_url = str(document.get("url") or "").strip()
        name_score = company_score(company_name, title)
        location_score = address_match_score(address, contents)
        if (
            name_score < MIN_COMPANY_SCORE
            or location_score < MIN_ADDRESS_SCORE
        ):
            continue
        confidence = max(
            AUTO_CONFIRM_SCORE,
            min(100, name_score + location_score + 20),
        )
        for phone in _phones_from_text(f"{title} {contents}"):
            if phone in seen_phones:
                continue
            seen_phones.add(phone)
            phone_type = (
                "public_business_mobile"
                if is_mobile_phone(phone)
                else "company_main"
            )
            candidates.append(
                {
                    "company_name": title,
                    "address": contents,
                    "phone": phone,
                    "phone_type": phone_type,
                    "source_type": "daum_web_snippet",
                    "source_url": source_url,
                    "confidence": confidence,
                    "public_business_source": True,
                }
            )
            contacts.append(
                {
                    "contact_type": "phone",
                    "contact_value": phone,
                    "contact_label": (
                        "공개 업무용 핸드폰"
                        if is_mobile_phone(phone)
                        else "사업장 공개 대표전화"
                    ),
                    "source_type": "daum_web_snippet",
                    "source_url": source_url,
                    "confidence": confidence,
                    "verification_status": "auto_verified",
                    "is_primary": not contacts,
                    "metadata": {
                        "matched_company_name": title,
                        "matched_address": contents,
                        "phone_type": phone_type,
                        "public_business_source": True,
                    },
                }
            )
    candidates.sort(key=lambda row: int(row["confidence"]), reverse=True)
    contacts.sort(key=lambda row: int(row["confidence"]), reverse=True)
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": f"다음웹 공개 전화번호 {len(candidates)}건",
        "queries": [query],
        "candidates": candidates,
        "contacts": contacts,
        "trace": [
            {
                "stage": "daum_web_phone",
                "status": "SUCCESS",
                "message": f"{len(candidates)}건",
            }
        ],
    }


def test_connection(timeout: int = 10) -> dict[str, Any]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "checked_at": checked_at,
        }
    try:
        _wait_for_request_slot()
        response = requests.get(
            DAUM_WEB_URL,
            headers=_headers(),
            params={"query": "OASIS CRM", "size": 1},
            timeout=max(2, int(timeout)),
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "checked_at": checked_at,
        }
    except requests.RequestException:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "checked_at": checked_at,
        }
    return {
        "ok": response.ok,
        "status": (
            "CONNECTED" if response.ok else f"HTTP_{response.status_code}"
        ),
        "http_status": response.status_code,
        "checked_at": checked_at,
    }
