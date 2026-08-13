from __future__ import annotations

import html
import os
import re
import time
from datetime import datetime
from threading import Lock
from typing import Any

import requests
import website_contact_parser

from contact_matching import (
    address_hint,
    address_match_score,
    company_score,
    is_mobile_phone,
    normalize_company_name,
    normalize_phone,
    search_company_name,
)


KAKAO_KEY_ENV = "KAKAO_REST_API_KEY"
DAUM_WEB_URL = "https://dapi.kakao.com/v2/search/web"
MIN_COMPANY_SCORE = 38
MIN_ADDRESS_SCORE = 10
MOBILE_MIN_ADDRESS_SCORE = 21
AUTO_CONFIRM_SCORE = 85
MIN_REQUEST_INTERVAL_SECONDS = 0.15
DEFAULT_RESULT_SIZE = 20
DEFAULT_REQUEST_BUDGET = 2
MAX_SOURCE_PAGE_VERIFICATIONS = 1
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
    *,
    request_count: int = 0,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": status,
        "message": message,
        "queries": [query] if query else [],
        "candidates": [],
        "contacts": [],
        "request_count": max(0, int(request_count)),
        "diagnostics": {
            "documents": 0,
            "raw_mobile": 0,
            "raw_landline": 0,
            "name_rejected": 0,
            "evidence_rejected": 0,
            "accepted_mobile": 0,
            "accepted_landline": 0,
            "source_pages_checked": 0,
            "source_pages_verified": 0,
        },
        "trace": [
            {
                "stage": "daum_web_phone",
                "status": status,
                "message": message,
            }
        ],
    }


def _business_number_matches(value: Any, business_no: Any) -> bool:
    expected = re.sub(r"[^0-9]", "", str(business_no or ""))
    if len(expected) != 10:
        return False
    text = str(value or "")
    compact = re.search(
        rf"(?<!\d){re.escape(expected)}(?!\d)",
        text,
    )
    if compact:
        return True
    first, middle, last = expected[:3], expected[3:5], expected[5:]
    formatted = re.search(
        rf"(?<!\d){first}(?P<separator>[-.]|\s+)"
        rf"{middle}(?P=separator){last}(?!\d)",
        text,
    )
    return formatted is not None


def _mobile_name_evidence(company_name: str, candidate_title: str) -> bool:
    """Select address-free mobile snippets for source-page verification."""
    expected = normalize_company_name(company_name)
    candidate = normalize_company_name(candidate_title)
    return bool(
        len(expected) >= 4
        and expected in candidate
        and company_score(company_name, candidate_title) >= MIN_COMPANY_SCORE
    )


def _verified_source_phones(
    source_url: str,
    company_name: str,
    address: str,
    business_no: str,
    *,
    timeout: int,
) -> set[str]:
    """Return phones independently verified on the linked public page."""
    if not source_url:
        return set()
    result = website_contact_parser.inspect_website(
        source_url,
        company_name,
        address,
        business_no,
        timeout=max(2, min(5, int(timeout))),
        max_pages=1,
    )
    if not result.get("ok") or int(result.get("confidence") or 0) < 45:
        return set()
    return {
        normalized
        for row in result.get("contacts") or []
        if str((row or {}).get("contact_type") or "") == "phone"
        and (
            normalized := normalize_phone(
                (row or {}).get("contact_value")
            )
        )
    }


def _request_documents(
    query: str,
    *,
    timeout: int,
    size: int,
) -> dict[str, Any]:
    request_count = 1
    try:
        _wait_for_request_slot()
        response = requests.get(
            DAUM_WEB_URL,
            headers=_headers(),
            params={
                "query": query,
                "page": 1,
                "size": min(50, max(1, int(size))),
                "sort": "accuracy",
            },
            timeout=max(2, int(timeout)),
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "다음웹 검색 응답시간이 초과되었습니다.",
            "documents": [],
            "request_count": request_count,
        }
    except requests.RequestException:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": "다음웹 검색 연결에 실패했습니다.",
            "documents": [],
            "request_count": request_count,
        }
    if not response.ok:
        return {
            "ok": False,
            "status": f"HTTP_{response.status_code}",
            "message": f"다음웹 검색 HTTP_{response.status_code}",
            "documents": [],
            "request_count": request_count,
        }
    try:
        payload = response.json()
    except ValueError:
        return {
            "ok": False,
            "status": "INVALID_JSON",
            "message": "다음웹 검색 응답을 해석하지 못했습니다.",
            "documents": [],
            "request_count": request_count,
        }
    documents = payload.get("documents", []) if isinstance(payload, dict) else []
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": "다음웹 검색 성공",
        "documents": documents if isinstance(documents, list) else [],
        "request_count": request_count,
    }


def search_public_phones(
    company_name: str,
    address: str,
    business_no: str = "",
    *,
    timeout: int = 6,
    size: int = DEFAULT_RESULT_SIZE,
    request_budget: int = DEFAULT_REQUEST_BUDGET,
) -> dict[str, Any]:
    """Find public business phones with a mobile-first request budget."""
    if not key_status()["configured"]:
        return _empty_result(
            "KEY_MISSING",
            "다음웹 검색에 필요한 Kakao REST API 키가 없습니다.",
        )

    base_name = search_company_name(company_name)
    if not base_name:
        return _empty_result("INVALID_QUERY", "업체명이 없습니다.")

    budget = max(1, min(DEFAULT_REQUEST_BUDGET, int(request_budget)))
    # Keep the first query deliberately narrow. Extra words such as
    # "대표/문의/연락처" excluded many valid company pages whose snippets
    # contained the public 010 number but not those labels.
    mobile_query = f'"{base_name}" 010'.strip()
    location_mobile_query = (
        f'"{base_name}" {address_hint(address)} 010'
    ).strip()
    query_specs = [("mobile_first", mobile_query)]
    if budget > 1:
        query_specs.append(("mobile_with_location", location_mobile_query))

    candidates: list[dict[str, Any]] = []
    contacts: list[dict[str, Any]] = []
    seen_phones: set[str] = set()
    queries: list[str] = []
    trace: list[dict[str, Any]] = []
    request_count = 0
    diagnostics = {
        "documents": 0,
        "raw_mobile": 0,
        "raw_landline": 0,
        "name_rejected": 0,
        "evidence_rejected": 0,
        "accepted_mobile": 0,
        "accepted_landline": 0,
        "source_pages_checked": 0,
        "source_pages_verified": 0,
    }
    source_page_cache: dict[str, set[str]] = {}

    for query_mode, query in query_specs:
        queries.append(query)
        result = _request_documents(query, timeout=timeout, size=size)
        request_count += int(result.get("request_count") or 0)
        if not result.get("ok"):
            status = str(result.get("status") or "PROVIDER_ERROR")
            trace.append({
                "stage": "daum_web_phone",
                "query_mode": query_mode,
                "status": status,
                "message": str(result.get("message") or ""),
            })
            return {
                **_empty_result(
                    status,
                    str(result.get("message") or "다음웹 검색 실패"),
                    query,
                    request_count=request_count,
                ),
                "queries": queries,
                "trace": trace,
                "diagnostics": diagnostics,
            }

        documents = result.get("documents") or []
        diagnostics["documents"] += len(documents)
        accepted_mobile_before = diagnostics["accepted_mobile"]
        for document in documents:
            if not isinstance(document, dict):
                continue
            title = _plain(document.get("title"))
            contents = _plain(document.get("contents"))
            source_url = str(document.get("url") or "").strip()
            phones = _phones_from_text(f"{title} {contents}")
            if not phones:
                continue
            for phone in phones:
                diagnostics[
                    "raw_mobile" if is_mobile_phone(phone) else "raw_landline"
                ] += 1

            location_score = address_match_score(
                address,
                f"{title} {contents}",
            )
            business_match = _business_number_matches(
                f"{title} {contents}",
                business_no,
            )
            name_score = company_score(company_name, title)
            expected_name = normalize_company_name(company_name)
            candidate_name = normalize_company_name(title)
            business_backed_short_name = bool(
                business_match
                and expected_name
                and expected_name in candidate_name
            )
            if (
                name_score < MIN_COMPANY_SCORE
                and not business_backed_short_name
            ):
                diagnostics["name_rejected"] += len(phones)
                continue
            strong_mobile_name = _mobile_name_evidence(company_name, title)

            for phone in phones:
                mobile = is_mobile_phone(phone)
                evidence_label = ""
                if query_mode == "mobile_first":
                    if not mobile:
                        evidence = location_score >= MIN_ADDRESS_SCORE
                        if evidence:
                            evidence_label = "address"
                    elif business_match:
                        evidence = True
                        evidence_label = "business_no"
                    elif location_score >= MOBILE_MIN_ADDRESS_SCORE:
                        evidence = True
                        evidence_label = "address"
                    elif strong_mobile_name and source_url:
                        if source_url not in source_page_cache and len(
                            source_page_cache
                        ) < MAX_SOURCE_PAGE_VERIFICATIONS:
                            diagnostics["source_pages_checked"] += 1
                            try:
                                source_page_cache[source_url] = (
                                    _verified_source_phones(
                                        source_url,
                                        company_name,
                                        address,
                                        business_no,
                                        timeout=timeout,
                                    )
                                )
                            except Exception:
                                source_page_cache[source_url] = set()
                        evidence = phone in source_page_cache.get(
                            source_url,
                            set(),
                        )
                        if evidence:
                            evidence_label = "source_page"
                            diagnostics["source_pages_verified"] += 1
                    else:
                        evidence = False
                else:
                    evidence = location_score >= (
                        MOBILE_MIN_ADDRESS_SCORE
                        if mobile
                        else MIN_ADDRESS_SCORE
                    )
                    if evidence:
                        evidence_label = "address"
                if not evidence:
                    diagnostics["evidence_rejected"] += 1
                    continue
                if phone in seen_phones:
                    continue
                seen_phones.add(phone)
                confidence = min(
                    100,
                    max(name_score, 38 if business_backed_short_name else 0)
                    + (25 if business_match else 0)
                    + min(25, location_score)
                    + (25 if evidence_label == "source_page" else 0)
                    + (22 if mobile and query_mode == "mobile_first" else 20),
                )
                confidence = max(AUTO_CONFIRM_SCORE, confidence)
                phone_type = (
                    "public_business_mobile" if mobile else "company_main"
                )
                diagnostics[
                    "accepted_mobile" if mobile else "accepted_landline"
                ] += 1
                candidates.append({
                    "company_name": title,
                    "address": contents,
                    "phone": phone,
                    "phone_type": phone_type,
                    "source_type": "daum_web_snippet",
                    "source_url": source_url,
                    "confidence": confidence,
                    "public_business_source": True,
                })
                contacts.append({
                    "contact_type": "phone",
                    "contact_value": phone,
                    "contact_label": (
                        "공개 업무용 핸드폰"
                        if mobile
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
                        "query_mode": query_mode,
                        "evidence": (
                            evidence_label or "address"
                        ),
                    },
                })
        accepted_in_query = (
            diagnostics["accepted_mobile"] - accepted_mobile_before
        )
        trace.append({
            "stage": "daum_web_phone",
            "query_mode": query_mode,
            "status": "SUCCESS",
            "documents": len(documents),
            "accepted_mobile": accepted_in_query,
        })
        if query_mode == "mobile_first" and accepted_in_query:
            break

    candidates.sort(key=lambda row: int(row["confidence"]), reverse=True)
    contacts.sort(
        key=lambda row: (
            is_mobile_phone(row.get("contact_value")),
            int(row["confidence"]),
        ),
        reverse=True,
    )
    for index, contact in enumerate(contacts):
        contact["is_primary"] = index == 0
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": f"다음웹 공개 전화번호 {len(candidates)}건",
        "queries": queries,
        "candidates": candidates,
        "contacts": contacts,
        "request_count": request_count,
        "diagnostics": diagnostics,
        "trace": trace,
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
