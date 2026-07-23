from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import requests

from contact_matching import (
    address_hint,
    contact_match_score,
    is_mobile_phone,
    normalize_phone,
    search_company_name,
)


KAKAO_KEY_ENV = "KAKAO_REST_API_KEY"
KAKAO_KEYWORD_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"


def key_status() -> dict[str, Any]:
    key = os.environ.get(KAKAO_KEY_ENV, "").strip()
    return {
        "configured": bool(key),
        "env_name": KAKAO_KEY_ENV,
        "masked": f"{key[:4]}{'*' * 12}" if key else "미등록",
    }


def _headers() -> dict[str, str]:
    key = os.environ.get(KAKAO_KEY_ENV, "").strip()
    return {"Authorization": f"KakaoAK {key}"}


def test_connection(timeout: int = 10) -> dict[str, Any]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"{KAKAO_KEY_ENV}가 없습니다.",
            "checked_at": checked_at,
        }
    try:
        response = requests.get(
            KAKAO_KEYWORD_URL,
            headers=_headers(),
            params={"query": "서울특별시청", "size": 1},
            timeout=timeout,
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "카카오 로컬 API 응답시간이 초과되었습니다.",
            "checked_at": checked_at,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": f"카카오 로컬 API 연결 실패: {type(exc).__name__}",
            "checked_at": checked_at,
        }
    ok = response.ok
    return {
        "ok": ok,
        "status": "CONNECTED" if ok else f"HTTP_{response.status_code}",
        "message": (
            "카카오 로컬 API 연결에 성공했습니다."
            if ok
            else f"카카오 로컬 API가 HTTP {response.status_code}을 반환했습니다."
        ),
        "http_status": response.status_code,
        "checked_at": checked_at,
    }


def search_company(
    company_name: str,
    address: str,
    *,
    timeout: int = 10,
    size: int = 10,
) -> dict[str, Any]:
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"{KAKAO_KEY_ENV}가 없습니다.",
            "candidates": [],
        }

    base_name = search_company_name(company_name)
    query = " ".join(value for value in (base_name, address_hint(address)) if value)
    try:
        response = requests.get(
            KAKAO_KEYWORD_URL,
            headers=_headers(),
            params={"query": query, "size": min(15, max(1, int(size)))},
            timeout=timeout,
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "카카오 장소검색 시간이 초과되었습니다.",
            "candidates": [],
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": f"카카오 장소검색 실패: {type(exc).__name__}",
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
            "message": "카카오 응답을 해석할 수 없습니다.",
            "candidates": [],
        }

    payloads = [payload]
    # Address spelling in NPS data often differs from Kakao's registered address.
    # Retry with the company name alone when the combined query has no result.
    initial_documents = payload.get("documents", []) if isinstance(payload, dict) else []
    if not initial_documents and base_name and query != base_name:
        try:
            fallback_response = requests.get(
                KAKAO_KEYWORD_URL,
                headers=_headers(),
                params={"query": base_name, "size": min(15, max(1, int(size)))},
                timeout=timeout,
            )
            if fallback_response.ok:
                fallback_payload = fallback_response.json()
                payloads.append(fallback_payload)
        except (requests.RequestException, ValueError):
            pass

    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for current_payload in payloads:
        for item in (
            current_payload.get("documents", [])
            if isinstance(current_payload, dict)
            else []
        ):
            if not isinstance(item, dict):
                continue
            candidate_address = (
                str(item.get("road_address_name") or "").strip()
                or str(item.get("address_name") or "").strip()
            )
            phone = normalize_phone(item.get("phone"))
            score = contact_match_score(
                company_name,
                address,
                item.get("place_name"),
                candidate_address,
                has_phone=bool(phone),
                active=True,
            )
            key = (
                str(item.get("place_name") or ""),
                candidate_address,
                phone,
            )
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "company_name": str(item.get("place_name") or ""),
                    "address": candidate_address,
                    "phone": phone,
                    "phone_type": (
                        "mobile_unverified"
                        if is_mobile_phone(phone)
                        else "company_main"
                    ),
                    "source_type": "kakao_local",
                    "source_url": str(item.get("place_url") or ""),
                    "confidence": score,
                    "raw": item,
                }
            )
    candidates.sort(key=lambda row: row["confidence"], reverse=True)
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": f"카카오 검색결과 {len(candidates)}건",
        "query": query,
        "fallback_query": base_name if len(payloads) > 1 else "",
        "candidates": candidates,
    }
