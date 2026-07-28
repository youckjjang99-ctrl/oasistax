from __future__ import annotations

import html
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import requests

from contact_matching import (
    address_hint,
    company_score,
    contact_match_score,
    is_mobile_phone,
    normalize_email,
    normalize_phone,
    search_company_name,
)


NAVER_ID_ENV = "NAVER_CLIENT_ID"
NAVER_SECRET_ENV = "NAVER_CLIENT_SECRET"
NAVER_WEB_URL = "https://openapi.naver.com/v1/search/webkr.json"
NAVER_LOCAL_URL = "https://openapi.naver.com/v1/search/local.json"
INSTAGRAM_RESERVED_PATHS = {
    "accounts",
    "about",
    "developer",
    "directory",
    "explore",
    "legal",
    "p",
    "reel",
    "reels",
    "stories",
}
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
        "User-Agent": "OASIS-CRM/9.8.1",
    }


def _plain(value: Any) -> str:
    text = html.unescape(str(value or ""))
    return re.sub(r"<[^>]+>", "", text).strip()


def _blocked(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in BLOCKED_DOMAINS)


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


def _instagram_profile(value: Any) -> tuple[str, str]:
    url = html.unescape(str(value or "")).strip()
    match = re.search(
        r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]+)",
        url,
        flags=re.IGNORECASE,
    )
    if not match:
        return "", ""
    username = match.group(1).strip(".").lower()
    if not username or username in INSTAGRAM_RESERVED_PATHS:
        return "", ""
    return f"@{username}", f"https://www.instagram.com/{username}/"


def _public_contacts_from_item(
    company_name: str,
    address: str,
    item: dict[str, Any],
) -> list[dict[str, Any]]:
    title = _plain(item.get("title"))
    description = _plain(item.get("description"))
    url = str(item.get("link") or "").strip()
    score = contact_match_score(
        company_name,
        address,
        title,
        description,
        active=True,
    )
    if score < 65:
        return []
    contacts: list[dict[str, Any]] = []
    combined = f"{title} {description}"
    email_pattern = (
        r"(?<![\w.+-])[\w.%+\-]+@[\w.\-]+\.[A-Za-z]{2,}"
        r"(?![\w.-])"
    )
    for raw_email in re.findall(email_pattern, combined):
        email = normalize_email(raw_email)
        if not email:
            continue
        contacts.append(
            {
                "contact_type": "email",
                "contact_value": email,
                "contact_label": "네이버 공개검색 이메일",
                "source_type": "naver_web_snippet",
                "source_url": url,
                "confidence": score,
                "verification_status": "review_required",
                "is_primary": False,
                "metadata": {"matched_company_name": title},
            }
        )
    instagram, instagram_url = _instagram_profile(
        f"{url} {combined}"
    )
    if instagram:
        contacts.append(
            {
                "contact_type": "instagram",
                "contact_value": instagram,
                "contact_label": "공개 인스타그램",
                "source_type": "naver_web_snippet",
                "source_url": instagram_url,
                "confidence": score,
                "verification_status": "review_required",
                "is_primary": False,
                "metadata": {"matched_company_name": title},
            }
        )
    return contacts


def search_company(
    company_name: str,
    address: str,
    *,
    timeout: int = 5,
    display: int = 5,
) -> dict[str, Any]:
    """네이버 지역검색으로 업체명·주소·장소 링크를 교차 확인한다."""
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": "네이버 검색 API 키가 없습니다.",
            "candidates": [],
        }
    query = f"{search_company_name(company_name)} {address_hint(address)}".strip()
    try:
        response = requests.get(
            NAVER_LOCAL_URL,
            headers=_headers(),
            params={
                "query": query,
                "display": min(5, max(1, int(display))),
                "start": 1,
                "sort": "random",
            },
            timeout=max(2, int(timeout)),
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "네이버 지도 검색 시간이 초과되었습니다.",
            "candidates": [],
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": f"네이버 지도 검색 실패: {type(exc).__name__}",
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
            "message": "네이버 지도 응답을 해석하지 못했습니다.",
            "candidates": [],
        }
    candidates: list[dict[str, Any]] = []
    for item in payload.get("items", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        title = _plain(item.get("title"))
        candidate_address = str(
            item.get("roadAddress") or item.get("address") or ""
        ).strip()
        phone = normalize_phone(item.get("telephone"))
        score = contact_match_score(
            company_name,
            address,
            title,
            candidate_address,
            has_phone=bool(phone),
            active=True,
        )
        if score < 65:
            continue
        candidates.append(
            {
                "company_name": title,
                "address": candidate_address,
                "phone": phone,
                "phone_type": (
                    "public_business_mobile"
                    if is_mobile_phone(phone)
                    else "company_main"
                ),
                "source_type": "naver_local",
                "source_url": str(item.get("link") or "").strip(),
                "confidence": score,
                "raw": item,
            }
        )
    candidates.sort(key=lambda row: int(row["confidence"]), reverse=True)
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": f"네이버 지도 후보 {len(candidates)}건",
        "query": query,
        "candidates": candidates,
    }


def search_public_phones(
    company_name: str,
    address: str,
    *,
    timeout: int = 5,
    display: int = 10,
) -> dict[str, Any]:
    """네이버 웹 검색 결과에 공개 노출된 회사 전화만 보수적으로 수집."""
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": "네이버 검색 API 키가 없습니다.",
            "candidates": [],
        }
    base_name = search_company_name(company_name)
    queries = [
        f'"{base_name}" 대표전화 {address_hint(address)}'.strip(),
        f'"{base_name}" 문의 연락처 {address_hint(address)}'.strip(),
        f'"{base_name}" 업무용 휴대전화 010'.strip(),
        f'"{base_name}" 이메일 인스타그램'.strip(),
    ]
    candidates: list[dict[str, Any]] = []
    public_contacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    seen_contacts: set[tuple[str, str]] = set()
    checked_queries: list[str] = []
    def _search_query(query: str) -> tuple[str, list[dict[str, Any]]]:
        try:
            response = requests.get(
                NAVER_WEB_URL,
                headers=_headers(),
                params={
                    "query": query,
                    "display": min(20, max(1, int(display))),
                    "start": 1,
                },
                timeout=max(2, int(timeout)),
            )
        except requests.Timeout:
            return "TIMEOUT", []
        except requests.RequestException:
            return "NETWORK_ERROR", []
        if not response.ok:
            return f"HTTP_{response.status_code}", []
        try:
            payload = response.json()
        except ValueError:
            return "INVALID_JSON", []
        items = payload.get("items", []) if isinstance(payload, dict) else []
        return "SUCCESS", [
            item for item in items if isinstance(item, dict)
        ]

    last_status = "SUCCESS"
    # 네이버 호출은 동시에 2개까지만 실행해 응답시간은 줄이되
    # 공개 API의 순간 호출량이 과도하게 늘어나지 않게 제한한다.
    with ThreadPoolExecutor(max_workers=2) as executor:
        query_results = list(executor.map(_search_query, queries))
    successful_queries = 0
    for query, (query_status, items) in zip(queries, query_results):
        checked_queries.append(query)
        if query_status != "SUCCESS":
            last_status = query_status
            continue
        successful_queries += 1
        for item in items:
            title = _plain(item.get("title"))
            description = _plain(item.get("description"))
            combined = f"{title} {description}"
            for contact in _public_contacts_from_item(
                company_name,
                address,
                item,
            ):
                contact_key = (
                    str(contact.get("contact_type") or ""),
                    str(contact.get("contact_value") or ""),
                )
                if contact_key in seen_contacts:
                    continue
                seen_contacts.add(contact_key)
                public_contacts.append(contact)
            for phone in _phones_from_text(combined):
                score = contact_match_score(
                    company_name,
                    address,
                    title,
                    description,
                    has_phone=True,
                    active=True,
                )
                if score < 65:
                    continue
                url = str(item.get("link") or "").strip()
                key = (phone, url)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "company_name": title,
                        "address": description,
                        "phone": phone,
                        "phone_type": (
                            "public_business_mobile"
                            if is_mobile_phone(phone)
                            else "company_main"
                        ),
                        "source_type": "naver_web_snippet",
                        "source_url": url,
                        "confidence": score,
                        "raw": {
                            "title": title,
                            "description": description,
                        },
                    }
                )
    candidates.sort(key=lambda row: int(row["confidence"]), reverse=True)
    if successful_queries:
        last_status = "SUCCESS"
    return {
        "ok": last_status == "SUCCESS",
        "status": last_status,
        "message": f"네이버 공개 업무전화 {len(candidates)}건",
        "queries": checked_queries,
        "candidates": candidates,
        "contacts": public_contacts,
    }


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

    def _candidates(current_payload: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for item in (
            current_payload.get("items", [])
            if isinstance(current_payload, dict)
            else []
        ):
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
            found.append(
                {
                    "url": url,
                    "title": title,
                    "description": description,
                    "search_confidence": min(50, score + 5),
                }
            )
        return found

    candidates = _candidates(payload)
    fallback_query = ""
    if not candidates and base_name:
        fallback_query = f'"{base_name}" {address_hint(address)}'.strip()
        try:
            fallback_response = requests.get(
                NAVER_WEB_URL,
                headers=_headers(),
                params={"query": fallback_query, "display": min(20, max(1, int(display))), "start": 1},
                timeout=timeout,
            )
            if fallback_response.ok:
                candidates = _candidates(fallback_response.json())
        except (requests.RequestException, ValueError):
            pass
    candidates.sort(key=lambda row: row["search_confidence"], reverse=True)
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": f"공식 홈페이지 후보 {len(candidates)}건",
        "query": query,
        "fallback_query": fallback_query,
        "candidates": candidates,
    }
