from __future__ import annotations

import html
import os
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

from contact_matching import address_hint, company_score, search_company_name


NAVER_ID_ENV = "NAVER_CLIENT_ID"
NAVER_SECRET_ENV = "NAVER_CLIENT_SECRET"
NAVER_WEB_URL = "https://openapi.naver.com/v1/search/webkr.json"
BLOCKED_DOMAINS = {
    "naver.com",
    "blog.naver.com",
    "map.naver.com",
    "place.map.kakao.com",
    "kakao.com",
    "jobkorea.co.kr",
    "saramin.co.kr",
    "catch.co.kr",
    "wanted.co.kr",
    "bizno.net",
    "bizno1.com",
    "nicebizinfo.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
}


def key_status() -> dict[str, Any]:
    client_id = os.environ.get(NAVER_ID_ENV, "").strip()
    secret = os.environ.get(NAVER_SECRET_ENV, "").strip()
    return {
        "configured": bool(client_id and secret),
        "client_id_configured": bool(client_id),
        "secret_configured": bool(secret),
        "env_names": [NAVER_ID_ENV, NAVER_SECRET_ENV],
        "masked": f"{client_id[:4]}{'*' * 12}" if client_id else "미등록",
    }


def _headers() -> dict[str, str]:
    return {
        "X-Naver-Client-Id": os.environ.get(NAVER_ID_ENV, "").strip(),
        "X-Naver-Client-Secret": os.environ.get(NAVER_SECRET_ENV, "").strip(),
        "User-Agent": "OASIS-CRM/9.7.0",
    }


def _plain(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"<[^>]+>", "", text).strip()


def _blocked(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in BLOCKED_DOMAINS)


def test_connection(timeout: int = 10) -> dict[str, Any]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": "네이버 Client ID 또는 Secret이 없습니다.",
            "checked_at": checked_at,
        }
    try:
        response = requests.get(
            NAVER_WEB_URL,
            headers=_headers(),
            params={"query": "OASIS CRM", "display": 1, "start": 1},
            timeout=timeout,
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "네이버 검색 API 응답시간이 초과되었습니다.",
            "checked_at": checked_at,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": f"네이버 검색 API 연결 실패: {type(exc).__name__}",
            "checked_at": checked_at,
        }
    return {
        "ok": response.ok,
        "status": "CONNECTED" if response.ok else f"HTTP_{response.status_code}",
        "message": (
            "네이버 웹문서 검색 API 연결에 성공했습니다."
            if response.ok
            else f"네이버 검색 API가 HTTP {response.status_code}을 반환했습니다."
        ),
        "http_status": response.status_code,
        "checked_at": checked_at,
    }


def search_official_websites(
    company_name: str,
    address: str,
    *,
    timeout: int = 10,
    display: int = 10,
) -> dict[str, Any]:
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": "네이버 검색 API 키가 없습니다.",
            "candidates": [],
        }
    base_name = search_company_name(company_name)
    query = f'"{base_name}" {address_hint(address)} 공식 홈페이지'.strip()
    try:
        response = requests.get(
            NAVER_WEB_URL,
            headers=_headers(),
            params={
                "query": query,
                "display": min(20, max(1, int(display))),
                "start": 1,
            },
            timeout=timeout,
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "공식 홈페이지 검색 시간이 초과되었습니다.",
            "candidates": [],
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": f"공식 홈페이지 검색 실패: {type(exc).__name__}",
            "candidates": [],
        }
    if not response.ok:
        return {
            "ok": False,
            "status": f"HTTP_{response.status_code}",
            "message": response.text[:200],
            "candidates": [],
        }
    try:
        payload = response.json()
    except ValueError:
        return {
            "ok": False,
            "status": "INVALID_JSON",
            "message": "네이버 검색 응답을 해석할 수 없습니다.",
            "candidates": [],
        }

    candidates: list[dict[str, Any]] = []
    for item in payload.get("items", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("link") or "").strip()
        if not url.startswith(("http://", "https://")) or _blocked(url):
            continue
        title = _plain(item.get("title"))
        description = _plain(item.get("description"))
        score = company_score(company_name, f"{title} {description}")
        if score <= 0:
            continue
        candidates.append(
            {
                "url": url,
                "title": title,
                "description": description,
                "search_confidence": min(50, score + 5),
            }
        )
    candidates.sort(key=lambda row: row["search_confidence"], reverse=True)
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": f"공식 홈페이지 후보 {len(candidates)}건",
        "query": query,
        "candidates": candidates,
    }

