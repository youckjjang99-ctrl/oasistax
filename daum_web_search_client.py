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
    registrable_domain,
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
MAX_SOURCE_PAGE_VERIFICATIONS = 5
MAX_REVIEW_MOBILE_CANDIDATES = 5
MIN_INDEPENDENT_MOBILE_SOURCES = 2
AMBIGUOUS_LOCAL_REGIONS = {"중구", "동구", "서구", "남구", "북구"}
BROAD_REGION_ALIASES = {
    "서울": "서울",
    "서울특별시": "서울",
    "부산": "부산",
    "부산광역시": "부산",
    "대구": "대구",
    "대구광역시": "대구",
    "인천": "인천",
    "인천광역시": "인천",
    "광주": "광주",
    "광주광역시": "광주",
    "대전": "대전",
    "대전광역시": "대전",
    "울산": "울산",
    "울산광역시": "울산",
    "세종": "세종",
    "세종특별자치시": "세종",
    "경기": "경기",
    "경기도": "경기",
    "강원": "강원",
    "강원도": "강원",
    "강원특별자치도": "강원",
    "충북": "충북",
    "충청북도": "충북",
    "충남": "충남",
    "충청남도": "충남",
    "전북": "전북",
    "전라북도": "전북",
    "전북특별자치도": "전북",
    "전남": "전남",
    "전라남도": "전남",
    "경북": "경북",
    "경상북도": "경북",
    "경남": "경남",
    "경상남도": "경남",
    "제주": "제주",
    "제주도": "제주",
    "제주특별자치도": "제주",
}
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
        "review_candidates": [],
        "request_count": max(0, int(request_count)),
        "diagnostics": {
            "documents": 0,
            "raw_mobile": 0,
            "raw_landline": 0,
            "name_rejected": 0,
            "evidence_rejected": 0,
            "accepted_mobile": 0,
            "accepted_landline": 0,
            "accepted_mobile_business_no": 0,
            "accepted_mobile_address": 0,
            "accepted_mobile_name_and_region": 0,
            "accepted_mobile_independent_sources": 0,
            "accepted_mobile_source_page": 0,
            "source_pages_checked": 0,
            "source_pages_verified": 0,
            "review_mobile_candidates": 0,
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


def _independent_source_domain(value: Any) -> str:
    """Return a conservative site key for cross-source mobile evidence."""
    host = registrable_domain(value)
    if not host:
        return ""
    labels = [label for label in host.split(".") if label]
    if len(labels) < 2:
        return host
    korean_second_level = {
        "co.kr",
        "go.kr",
        "ne.kr",
        "or.kr",
        "pe.kr",
        "re.kr",
    }
    suffix = ".".join(labels[-2:])
    if suffix in korean_second_level and len(labels) >= 3:
        return ".".join(labels[-3:])
    return suffix


def _admin_regions(value: Any) -> tuple[set[str], set[str]]:
    """Extract broad and local Korean administrative regions."""
    text = re.sub(r"[(),]", " ", str(value or ""))
    tokens = [token.strip() for token in text.split() if token.strip()]
    broad: set[str] = set()
    local: set[str] = set()
    for token in tokens:
        cleaned = re.sub(r"[^0-9A-Za-z가-힣]", "", token)
        if not cleaned:
            continue
        alias = BROAD_REGION_ALIASES.get(cleaned)
        if alias:
            broad.add(alias)
            continue
        if re.fullmatch(r"[가-힣]{2,}(?:시|군|구)", cleaned):
            local.add(cleaned)
    return broad, local


def _same_admin_region(expected: Any, candidate: Any) -> bool:
    expected_broad, expected_local = _admin_regions(expected)
    candidate_broad, candidate_local = _admin_regions(candidate)
    if expected_broad and candidate_broad and not (
        expected_broad & candidate_broad
    ):
        return False
    common_local = expected_local & candidate_local
    if not common_local:
        return False
    if common_local & AMBIGUOUS_LOCAL_REGIONS:
        return bool(expected_broad & candidate_broad)
    return True


def _admin_region_conflicts(expected: Any, candidate: Any) -> bool:
    expected_broad, _ = _admin_regions(expected)
    candidate_broad, _ = _admin_regions(candidate)
    return bool(
        expected_broad
        and candidate_broad
        and (candidate_broad - expected_broad)
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
    """Find public business phones with balanced mobile verification."""
    if not key_status()["configured"]:
        return _empty_result(
            "KEY_MISSING",
            "다음웹 검색에 필요한 Kakao REST API 키가 없습니다.",
        )

    base_name = search_company_name(company_name)
    if not base_name:
        return _empty_result("INVALID_QUERY", "업체명이 없습니다.")

    budget = max(1, min(DEFAULT_REQUEST_BUDGET, int(request_budget)))
    query_specs = [("mobile_first", f'"{base_name}" 010'.strip())]
    if budget > 1:
        query_specs.append((
            "mobile_with_location",
            f'"{base_name}" {address_hint(address)} 010'.strip(),
        ))

    candidates: list[dict[str, Any]] = []
    contacts: list[dict[str, Any]] = []
    seen_landlines: set[str] = set()
    mobile_observations: dict[str, list[dict[str, Any]]] = {}
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
        "accepted_mobile_business_no": 0,
        "accepted_mobile_address": 0,
        "accepted_mobile_name_and_region": 0,
        "accepted_mobile_independent_sources": 0,
        "accepted_mobile_source_page": 0,
        "source_pages_checked": 0,
        "source_pages_verified": 0,
        "review_mobile_candidates": 0,
    }
    evidence_rank = {
        "name_and_region": 1,
        "independent_sources": 2,
        "address": 3,
        "source_page": 4,
        "business_no": 5,
    }

    def _domains_by_phone() -> dict[str, set[str]]:
        domains: dict[str, set[str]] = {}
        for phone, rows in mobile_observations.items():
            for row in rows:
                domain = str(row.get("source_domain") or "")
                if (
                    row.get("strong_name")
                    and not row.get("region_conflict")
                    and domain
                ):
                    domains.setdefault(phone, set()).add(domain)
        return domains

    def _direct_evidence(
        phone: str,
        row: dict[str, Any],
        domains: dict[str, set[str]],
    ) -> str:
        ambiguous_document = int(row.get("document_mobile_count") or 0) > 1
        if row.get("business_match") and not ambiguous_document:
            return "business_no"
        if (
            not ambiguous_document
            and not row.get("region_conflict")
            and int(row.get("location_score") or 0)
            >= MOBILE_MIN_ADDRESS_SCORE
        ):
            return "address"
        if (
            not ambiguous_document
            and not row.get("region_conflict")
            and row.get("strong_name")
            and row.get("admin_region_match")
        ):
            return "name_and_region"
        if (
            row.get("strong_name")
            and not row.get("region_conflict")
            and len(domains.get(phone, set()))
            >= MIN_INDEPENDENT_MOBILE_SOURCES
        ):
            return "independent_sources"
        return ""

    def _selection_key(
        row: dict[str, Any],
        evidence: str,
    ) -> tuple[int, int, int, int]:
        return (
            evidence_rank.get(evidence, 0),
            int(row.get("name_score") or 0),
            int(row.get("location_score") or 0),
            1 if row.get("source_url") else 0,
        )

    def _best_direct_mobile() -> dict[str, tuple[dict[str, Any], str]]:
        domains = _domains_by_phone()
        best: dict[str, tuple[dict[str, Any], str]] = {}
        for phone, rows in mobile_observations.items():
            for row in rows:
                evidence = _direct_evidence(phone, row, domains)
                if not evidence:
                    continue
                previous = best.get(phone)
                if previous is None or _selection_key(
                    row,
                    evidence,
                ) > _selection_key(previous[0], previous[1]):
                    best[phone] = (row, evidence)
        return best

    def _append_result(
        phone: str,
        row: dict[str, Any],
        evidence: str,
        *,
        mobile: bool,
    ) -> None:
        name_score = int(row.get("name_score") or 0)
        location_score = int(row.get("location_score") or 0)
        business_match = bool(row.get("business_match"))
        confidence = min(
            100,
            max(name_score, 38 if business_match else 0)
            + (25 if business_match else 0)
            + min(25, location_score)
            + {
                "business_no": 25,
                "source_page": 25,
                "address": 20,
                "independent_sources": 18,
                "name_and_region": 18,
            }.get(evidence, 15),
        )
        confidence = max(AUTO_CONFIRM_SCORE, confidence)
        phone_type = "public_business_mobile" if mobile else "company_main"
        title = str(row.get("title") or "")
        contents = str(row.get("contents") or "")
        source_url = str(row.get("source_url") or "")
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
                "공개 업무용 핸드폰" if mobile else "사업장 공개 대표전화"
            ),
            "source_type": "daum_web_snippet",
            "source_url": source_url,
            "confidence": confidence,
            "verification_status": "auto_verified",
            "is_primary": False,
            "metadata": {
                "matched_company_name": title,
                "matched_address": contents,
                "phone_type": phone_type,
                "public_business_source": True,
                "query_mode": str(row.get("query_mode") or ""),
                "evidence": evidence,
            },
        })

    previous_direct_count = 0
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
        for document in documents:
            if not isinstance(document, dict):
                continue
            title = _plain(document.get("title"))
            contents = _plain(document.get("contents"))
            source_url = str(document.get("url") or "").strip()
            full_text = f"{title} {contents}"
            phones = _phones_from_text(full_text)
            if not phones:
                continue
            document_mobile_count = sum(
                1 for phone in phones if is_mobile_phone(phone)
            )
            location_score = address_match_score(address, full_text)
            business_match = _business_number_matches(
                full_text,
                business_no,
            )
            title_name_score = company_score(company_name, title)
            expected_name = normalize_company_name(company_name)
            title_candidate_name = normalize_company_name(title)
            strong_name = _mobile_name_evidence(company_name, title)
            admin_region_match = _same_admin_region(address, full_text)
            region_conflict = _admin_region_conflicts(address, full_text)
            source_domain = _independent_source_domain(source_url)

            for phone in phones:
                mobile = is_mobile_phone(phone)
                diagnostics[
                    "raw_mobile" if mobile else "raw_landline"
                ] += 1
                business_backed_short_name = bool(
                    business_match
                    and expected_name
                    and expected_name in title_candidate_name
                )
                if (
                    title_name_score < MIN_COMPANY_SCORE
                    and not business_backed_short_name
                ):
                    diagnostics["name_rejected"] += 1
                    continue
                observation = {
                    "title": title,
                    "contents": contents,
                    "source_url": source_url,
                    "source_domain": source_domain,
                    "query_mode": query_mode,
                    "name_score": title_name_score,
                    "location_score": location_score,
                    "business_match": business_match,
                    "strong_name": strong_name,
                    "admin_region_match": admin_region_match,
                    "region_conflict": region_conflict,
                    "document_mobile_count": document_mobile_count,
                }
                if mobile:
                    mobile_observations.setdefault(phone, []).append(
                        observation
                    )
                    continue
                if location_score < MIN_ADDRESS_SCORE:
                    diagnostics["evidence_rejected"] += 1
                    continue
                if phone in seen_landlines:
                    continue
                seen_landlines.add(phone)
                diagnostics["accepted_landline"] += 1
                _append_result(
                    phone,
                    observation,
                    "address",
                    mobile=False,
                )

        current_direct_count = len(_best_direct_mobile())
        accepted_in_query = max(
            0,
            current_direct_count - previous_direct_count,
        )
        previous_direct_count = current_direct_count
        trace.append({
            "stage": "daum_web_phone",
            "query_mode": query_mode,
            "status": "SUCCESS",
            "documents": len(documents),
            "accepted_mobile": accepted_in_query,
        })
        if query_mode == "mobile_first" and current_direct_count:
            break

    best_mobile = _best_direct_mobile()
    verification_candidates: list[
        tuple[tuple[int, int, int], str, dict[str, Any]]
    ] = []
    seen_verification_pairs: set[tuple[str, str]] = set()
    for phone, rows in mobile_observations.items():
        if phone in best_mobile:
            continue
        for row in rows:
            source_url = str(row.get("source_url") or "")
            pair = (phone, source_url)
            if (
                not row.get("strong_name")
                or row.get("region_conflict")
                or not source_url
                or pair in seen_verification_pairs
            ):
                continue
            seen_verification_pairs.add(pair)
            verification_candidates.append((
                (
                    int(row.get("name_score") or 0),
                    int(row.get("location_score") or 0),
                    1 if row.get("source_domain") else 0,
                ),
                phone,
                row,
            ))
    verification_candidates.sort(key=lambda item: item[0], reverse=True)
    source_page_cache: dict[str, set[str]] = {}
    for _, phone, row in verification_candidates:
        if len(source_page_cache) >= MAX_SOURCE_PAGE_VERIFICATIONS:
            break
        if phone in best_mobile:
            continue
        source_url = str(row.get("source_url") or "")
        if source_url not in source_page_cache:
            diagnostics["source_pages_checked"] += 1
            try:
                source_page_cache[source_url] = _verified_source_phones(
                    source_url,
                    company_name,
                    address,
                    business_no,
                    timeout=timeout,
                )
            except Exception:
                source_page_cache[source_url] = set()
        if phone in source_page_cache.get(source_url, set()):
            best_mobile[phone] = (row, "source_page")
            diagnostics["source_pages_verified"] += 1

    diagnostics["evidence_rejected"] += max(
        0,
        len(mobile_observations) - len(best_mobile),
    )
    review_candidates: list[dict[str, Any]] = []
    if not best_mobile:
        domains_by_phone = _domains_by_phone()
        for phone, rows in mobile_observations.items():
            reviewable = [
                row
                for row in rows
                if row.get("strong_name")
                and not row.get("region_conflict")
                and row.get("source_url")
            ]
            if not reviewable:
                continue
            best_row = max(
                reviewable,
                key=lambda row: (
                    int(row.get("name_score") or 0),
                    int(row.get("location_score") or 0),
                    1 if row.get("admin_region_match") else 0,
                    1
                    if int(row.get("document_mobile_count") or 0) == 1
                    else 0,
                ),
            )
            source_url = str(best_row.get("source_url") or "")
            independent_source_count = len(
                domains_by_phone.get(phone, set())
            )
            confidence = min(
                AUTO_CONFIRM_SCORE - 1,
                max(45, int(best_row.get("name_score") or 0))
                + min(15, int(best_row.get("location_score") or 0))
                + (8 if best_row.get("admin_region_match") else 0)
                + min(6, independent_source_count * 3)
                + (
                    4
                    if int(best_row.get("document_mobile_count") or 0) == 1
                    else 0
                ),
            )
            review_candidates.append({
                "mobile_phone": phone,
                "source_url": source_url,
                "query_mode": str(best_row.get("query_mode") or ""),
                "confidence": confidence,
                "evidence": {
                    "name_score": int(best_row.get("name_score") or 0),
                    "location_score": int(
                        best_row.get("location_score") or 0
                    ),
                    "admin_region_match": bool(
                        best_row.get("admin_region_match")
                    ),
                    "business_number_match": bool(
                        best_row.get("business_match")
                    ),
                    "independent_source_count": independent_source_count,
                    "document_mobile_count": int(
                        best_row.get("document_mobile_count") or 0
                    ),
                    "source_page_checked": source_url in source_page_cache,
                    "source_page_verified": False,
                    "reason": (
                        "source_page_not_verified"
                        if source_url in source_page_cache
                        else "automatic_evidence_insufficient"
                    ),
                },
            })
        review_candidates.sort(
            key=lambda row: int(row.get("confidence") or 0),
            reverse=True,
        )
        review_candidates = review_candidates[
            :MAX_REVIEW_MOBILE_CANDIDATES
        ]
        diagnostics["review_mobile_candidates"] = len(review_candidates)
    for phone, (row, evidence) in best_mobile.items():
        diagnostics["accepted_mobile"] += 1
        diagnostics[f"accepted_mobile_{evidence}"] += 1
        _append_result(phone, row, evidence, mobile=True)

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
        "review_candidates": review_candidates,
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
