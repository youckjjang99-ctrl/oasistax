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
# Only an auto-confirmable public phone is a provider-level match. Lower
# scores may still be returned as review candidates, but must not suppress the
# fallback query or advance a scheduled row to complete.
MATCH_CONFIDENCE = 85


def key_status() -> dict[str, Any]:
    key = os.environ.get(KAKAO_KEY_ENV, "").strip()
    return {
        "configured": bool(key),
        "env_name": KAKAO_KEY_ENV,
        # 키 일부도 화면, 로그 또는 작업 결과에 남기지 않는다.
        "masked": "설정됨" if key else "미등록",
    }


def _headers() -> dict[str, str]:
    key = os.environ.get(KAKAO_KEY_ENV, "").strip()
    return {"Authorization": f"KakaoAK {key}"}


def _http_error_code(status_code: Any) -> str:
    try:
        return f"HTTP_{int(status_code)}"
    except (TypeError, ValueError):
        return "HTTP_ERROR"


def _provider_error(
    safe_error_code: str,
    *,
    request_count: int,
) -> dict[str, Any]:
    messages = {
        "KEY_MISSING": "카카오 API 인증 설정을 확인해 주세요.",
        "TIMEOUT": "카카오 장소검색 응답시간이 초과되었습니다.",
        "NETWORK_ERROR": "카카오 장소검색 연결에 실패했습니다.",
        "INVALID_JSON": "카카오 장소검색 응답을 확인할 수 없습니다.",
    }
    return {
        "ok": False,
        "outcome": "error",
        "status": safe_error_code,
        "safe_error_code": safe_error_code,
        "message": messages.get(
            safe_error_code,
            "카카오 장소검색 공급자 오류가 발생했습니다.",
        ),
        "request_count": request_count,
        "candidates": [],
    }


def _connection_category(safe_error_code: str) -> str:
    if safe_error_code in {"KEY_MISSING", "HTTP_401"}:
        return "AUTH_ERROR"
    if safe_error_code == "HTTP_403":
        return "PERMISSION_ERROR"
    if safe_error_code == "HTTP_429":
        return "QUOTA_ERROR"
    return "NETWORK_ERROR"


def _connection_result(
    *,
    checked_at: str,
    safe_error_code: str,
    request_count: int,
    http_status: int | None = None,
) -> dict[str, Any]:
    category = _connection_category(safe_error_code)
    messages = {
        "AUTH_ERROR": "카카오 API 인증 설정을 확인해 주세요.",
        "PERMISSION_ERROR": "카카오 API 사용 권한을 확인해 주세요.",
        "QUOTA_ERROR": "카카오 API 일일 쿼터를 확인해 주세요.",
        "NETWORK_ERROR": "카카오 API 연결 상태를 확인해 주세요.",
    }
    result: dict[str, Any] = {
        "ok": False,
        "status": category,
        "category": category,
        "safe_error_code": safe_error_code,
        "message": messages[category],
        "request_count": request_count,
        "checked_at": checked_at,
    }
    if http_status is not None:
        result["http_status"] = http_status
    return result


def _json_payload(response: Any) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    documents = payload.get("documents")
    if not isinstance(documents, list):
        return None
    if any(not isinstance(item, dict) for item in documents):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    total_count = meta.get("total_count")
    pageable_count = meta.get("pageable_count")
    is_end = meta.get("is_end")
    if (
        not isinstance(total_count, int)
        or isinstance(total_count, bool)
        or total_count < 0
        or not isinstance(pageable_count, int)
        or isinstance(pageable_count, bool)
        or pageable_count < 0
        or not isinstance(is_end, bool)
    ):
        return None
    return payload


def _has_trusted_phone_match(
    documents: list[dict[str, Any]],
    company_name: str,
    address: str,
) -> bool:
    for item in documents:
        candidate_address = (
            str(item.get("road_address_name") or "").strip()
            or str(item.get("address_name") or "").strip()
        )
        phone = normalize_phone(item.get("phone"))
        if not phone:
            continue
        score = contact_match_score(
            company_name,
            address,
            item.get("place_name"),
            candidate_address,
            has_phone=True,
            active=True,
        )
        if score >= MATCH_CONFIDENCE:
            return True
    return False


def test_connection(timeout: int = 10) -> dict[str, Any]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not key_status()["configured"]:
        return _connection_result(
            checked_at=checked_at,
            safe_error_code="KEY_MISSING",
            request_count=0,
        )
    try:
        response = requests.get(
            KAKAO_KEYWORD_URL,
            headers=_headers(),
            params={"query": "서울특별시청", "size": 1},
            timeout=timeout,
        )
    except requests.Timeout:
        return _connection_result(
            checked_at=checked_at,
            safe_error_code="TIMEOUT",
            request_count=1,
        )
    except requests.RequestException:
        return _connection_result(
            checked_at=checked_at,
            safe_error_code="NETWORK_ERROR",
            request_count=1,
        )

    status_code = int(response.status_code)
    if status_code != 200:
        return _connection_result(
            checked_at=checked_at,
            safe_error_code=_http_error_code(status_code),
            request_count=1,
            http_status=status_code,
        )
    if _json_payload(response) is None:
        return _connection_result(
            checked_at=checked_at,
            safe_error_code="INVALID_JSON",
            request_count=1,
            http_status=status_code,
        )
    return {
        "ok": True,
        "status": "CONNECTED",
        "category": "CONNECTED",
        "safe_error_code": "",
        "message": "카카오 로컬 API 연결에 성공했습니다.",
        "http_status": status_code,
        "request_count": 1,
        "checked_at": checked_at,
    }


def search_company(
    company_name: str,
    address: str,
    *,
    timeout: int = 5,
    size: int = 10,
) -> dict[str, Any]:
    if not key_status()["configured"]:
        return _provider_error("KEY_MISSING", request_count=0)

    base_name = search_company_name(company_name)
    query = " ".join(value for value in (base_name, address_hint(address)) if value)
    request_count = 1
    try:
        response = requests.get(
            KAKAO_KEYWORD_URL,
            headers=_headers(),
            params={"query": query, "size": min(15, max(1, int(size)))},
            timeout=timeout,
        )
    except requests.Timeout:
        return _provider_error("TIMEOUT", request_count=request_count)
    except requests.RequestException:
        return _provider_error("NETWORK_ERROR", request_count=request_count)

    if int(response.status_code) != 200:
        return _provider_error(
            _http_error_code(response.status_code),
            request_count=request_count,
        )
    payload = _json_payload(response)
    if payload is None:
        return _provider_error("INVALID_JSON", request_count=request_count)

    payloads = [payload]
    # 국민연금 주소와 카카오 등록주소의 표기가 다르거나 첫 검색결과에
    # 전화번호가 없는 경우 회사명 단독검색까지 한 번 더 확인한다.
    initial_documents = payload.get("documents", [])
    initial_has_trusted_match = _has_trusted_phone_match(
        initial_documents,
        company_name,
        address,
    )
    if not initial_has_trusted_match and base_name and query != base_name:
        request_count += 1
        try:
            fallback_response = requests.get(
                KAKAO_KEYWORD_URL,
                headers=_headers(),
                params={"query": base_name, "size": min(15, max(1, int(size)))},
                timeout=timeout,
            )
        except requests.Timeout:
            return _provider_error("TIMEOUT", request_count=request_count)
        except requests.RequestException:
            return _provider_error("NETWORK_ERROR", request_count=request_count)
        if int(fallback_response.status_code) != 200:
            return _provider_error(
                _http_error_code(fallback_response.status_code),
                request_count=request_count,
            )
        fallback_payload = _json_payload(fallback_response)
        if fallback_payload is None:
            return _provider_error("INVALID_JSON", request_count=request_count)
        payloads.append(fallback_payload)

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
                }
            )
    candidates.sort(key=lambda row: row["confidence"], reverse=True)
    matched = any(
        row.get("phone") and int(row.get("confidence") or 0) >= MATCH_CONFIDENCE
        for row in candidates
    )
    outcome = "matched" if matched else "no_match"
    return {
        "ok": True,
        "outcome": outcome,
        "status": "MATCHED" if matched else "NO_MATCH",
        "safe_error_code": "",
        "message": f"카카오 검색결과 {len(candidates)}건",
        "request_count": request_count,
        "candidates": candidates,
    }
