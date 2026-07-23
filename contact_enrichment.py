from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import kakao_local_client
import localdata_contact_client
import naver_web_search_client
from contact_matching import is_mobile_phone
from website_contact_parser import inspect_website


AUTO_CONFIRM_SCORE = 85
REVIEW_SCORE = 65


def api_statuses() -> dict[str, dict[str, Any]]:
    return {
        "kakao": kakao_local_client.key_status(),
        "naver": naver_web_search_client.key_status(),
        "localdata": localdata_contact_client.key_status(),
    }


def test_connections() -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=3) as executor:
        kakao_future = executor.submit(kakao_local_client.test_connection)
        naver_future = executor.submit(naver_web_search_client.test_connection)
        localdata_future = executor.submit(localdata_contact_client.test_services)
        results = {
            "kakao": kakao_future.result(),
            "naver": naver_future.result(),
            "localdata": localdata_future.result(),
        }
    return {
        "ok": all(result.get("ok") for result in results.values()),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sources": results,
    }


def _verification_status(
    score: int,
    *,
    phone: str = "",
    is_email: bool = False,
    email_domain_match: bool = False,
) -> str:
    if phone and is_mobile_phone(phone):
        return "review_required"
    if is_email:
        if score >= 90 and email_domain_match:
            return "auto_verified"
        if score >= REVIEW_SCORE:
            return "review_required"
        return "rejected"
    if score >= AUTO_CONFIRM_SCORE:
        return "auto_verified"
    if score >= REVIEW_SCORE:
        return "review_required"
    return "rejected"


def _phone_contact(candidate: dict[str, Any]) -> dict[str, Any] | None:
    phone = str(candidate.get("phone") or "").strip()
    score = int(candidate.get("confidence") or 0)
    source_type = str(candidate.get("source_type") or "")
    # A phone displayed on a page that contains the company name can still be
    # useful for a manual call check even when the address is not written there.
    if not phone or (
        score < REVIEW_SCORE
        and not (source_type == "official_website" and score >= 45)
    ):
        return None
    review_only = score < REVIEW_SCORE
    verified_score = max(REVIEW_SCORE, score) if review_only else score
    return {
        "contact_type": "phone",
        "contact_value": phone,
        "contact_label": (
            "휴대전화 확인 필요"
            if is_mobile_phone(phone)
            else "사업장 공개 대표전화"
        ),
        "source_type": source_type,
        "source_url": str(candidate.get("source_url") or ""),
        "confidence": verified_score,
        "verification_status": _verification_status(verified_score, phone=phone),
        "is_primary": score >= AUTO_CONFIRM_SCORE and not is_mobile_phone(phone),
        "metadata": {
            "matched_company_name": candidate.get("company_name", ""),
            "matched_address": candidate.get("address", ""),
            "phone_type": candidate.get("phone_type", ""),
            "review_only": review_only,
        },
    }


def _deduplicate(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_priority = {
        "official_website": 3,
        "kakao_local": 2,
        "naver_web_snippet": 1,
    }
    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for contact in contacts:
        if contact.get("verification_status") == "rejected":
            continue
        key = (
            str(contact.get("contact_type") or ""),
            str(contact.get("contact_value") or ""),
        )
        if not all(key):
            continue
        current = selected.get(key)
        if current is None:
            selected[key] = contact
            continue
        current_rank = (
            int(current.get("confidence") or 0),
            source_priority.get(str(current.get("source_type") or ""), 1),
        )
        new_rank = (
            int(contact.get("confidence") or 0),
            source_priority.get(str(contact.get("source_type") or ""), 1),
        )
        if new_rank > current_rank:
            selected[key] = contact
    ordered = list(selected.values())
    ordered.sort(
        key=lambda row: (
            row.get("contact_type") != "phone",
            -int(row.get("confidence") or 0),
        )
    )
    return ordered


def enrich_company(
    prospect: dict[str, Any],
    *,
    skip_kakao: bool = False,
    skip_localdata: bool = False,
    max_website_candidates: int = 3,
    website_timeout: int = 10,
    website_max_pages: int = 4,
) -> dict[str, Any]:
    company_name = str(
        prospect.get("company_name") or prospect.get("사업장명") or ""
    ).strip()
    address = str(
        prospect.get("address") or prospect.get("주소") or ""
    ).strip()
    business_no = str(
        prospect.get("business_no")
        or prospect.get("사업자등록번호")
        or ""
    ).strip()
    industry_name = str(
        prospect.get("industry_name") or prospect.get("업종명") or ""
    ).strip()
    contacts: list[dict[str, Any]] = []
    trace: list[dict[str, Any]] = []

    kakao = (
        {"candidates": [], "status": "SKIPPED", "message": "빠른 조회에서 확인 완료"}
        if skip_kakao
        else kakao_local_client.search_company(company_name, address)
    )
    trace.append(
        {
            "stage": "kakao",
            "status": kakao.get("status"),
            "message": kakao.get("message"),
        }
    )
    kakao_best = next(
        (
            row
            for row in kakao.get("candidates", [])
            if row.get("phone") and int(row.get("confidence") or 0) >= REVIEW_SCORE
        ),
        None,
    )
    if kakao_best:
        contact = _phone_contact(kakao_best)
        if contact:
            contacts.append(contact)

    reliable_kakao_phone = bool(
        kakao_best
        and int(kakao_best.get("confidence") or 0) >= AUTO_CONFIRM_SCORE
        and not is_mobile_phone(kakao_best.get("phone"))
    )
    if not reliable_kakao_phone and not skip_localdata:
        localdata = localdata_contact_client.search_company(
            company_name,
            address,
            industry_name,
        )
        trace.append(
            {
                "stage": "localdata",
                "status": localdata.get("status"),
                "message": localdata.get("message"),
                "services": [
                    {
                        "label": row.get("label"),
                        "status": row.get("status"),
                    }
                    for row in localdata.get("services", [])
                ],
            }
        )
        for candidate in localdata.get("candidates", [])[:3]:
            contact = _phone_contact(candidate)
            if contact:
                contacts.append(contact)
                if contact["verification_status"] == "auto_verified":
                    break
    else:
        trace.append(
            {
                "stage": "localdata",
                "status": "SKIPPED",
                "message": (
                    "빠른 조회에서 확인 완료"
                    if skip_localdata
                    else "카카오에서 신뢰도 높은 대표전화를 확인했습니다."
                ),
            }
        )

    website_url = ""
    website_confidence = 0
    naver = naver_web_search_client.search_official_websites(
        company_name,
        address,
        timeout=max(2, int(website_timeout)),
        display=8,
    )
    trace.append(
        {
            "stage": "naver",
            "status": naver.get("status"),
            "message": naver.get("message"),
        }
    )
    for candidate in naver.get("candidates", [])[
        : max(1, int(max_website_candidates))
    ]:
        website = inspect_website(
            candidate.get("url", ""),
            company_name,
            address,
            business_no,
            timeout=max(2, int(website_timeout)),
            max_pages=max(1, int(website_max_pages)),
        )
        if not website.get("ok"):
            continue
        score = int(website.get("confidence") or 0)
        website_contacts = list(website.get("contacts") or [])
        has_public_phone = any(
            row.get("contact_type") == "phone"
            and str(row.get("contact_value") or "").strip()
            for row in website_contacts
        )
        # Exact company-name matches score 45 before address/business-number
        # signals are available. Keep a published phone in that case as a
        # manual-review lead instead of silently dropping it.
        if score < 45 or (score < REVIEW_SCORE and not has_public_phone):
            trace.append(
                {
                    "stage": "website",
                    "status": "LOW_CONFIDENCE",
                    "message": "회사 확인 점수가 낮고 공개 전화가 없습니다.",
                    "confidence": score,
                }
            )
            continue
        candidate_url = str(
            website.get("website_url") or candidate.get("url") or ""
        )
        if not website_url:
            website_url = candidate_url
            website_confidence = score
            contacts.append(
                {
                    "contact_type": "website",
                    "contact_value": website_url,
                    "contact_label": "공식 홈페이지",
                    "source_type": "naver_web_search",
                    "source_url": website_url,
                    "confidence": score,
                    "verification_status": _verification_status(score),
                    "is_primary": True,
                    "metadata": {
                        "pages_checked": website.get("pages_checked", 0)
                    },
                }
            )
        candidate_has_phone = False
        for contact in website_contacts:
            contact = dict(contact)
            contact_score = int(contact.get("confidence") or 0)
            is_phone = contact.get("contact_type") == "phone"
            review_only = is_phone and contact_score < REVIEW_SCORE
            if review_only:
                contact_score = REVIEW_SCORE
                contact.setdefault("metadata", {})["review_only"] = True
                contact.setdefault("metadata", {})["review_reason"] = (
                    "회사명은 확인됐으나 주소 또는 사업자번호 교차확인이 부족합니다."
                )
                contact["confidence"] = contact_score
            domain_match = bool(
                (contact.get("metadata") or {}).get("domain_match")
            )
            contact["verification_status"] = _verification_status(
                contact_score,
                phone=(
                    contact.get("contact_value", "")
                    if is_phone
                    else ""
                ),
                is_email=contact.get("contact_type") == "email",
                email_domain_match=domain_match,
            )
            contact["is_primary"] = (
                contact["verification_status"] == "auto_verified"
            )
            contacts.append(contact)
            if contact.get("contact_type") == "phone":
                candidate_has_phone = True
        trace.append(
            {
                "stage": "website",
                "status": website.get("status"),
                "message": website.get("message"),
                "confidence": score,
            }
        )
        if candidate_has_phone:
            break
    if not website_url:
        trace.append(
            {
                "stage": "website",
                "status": "NOT_CONFIRMED",
                "message": "공식 홈페이지를 확정하지 못했습니다.",
            }
        )

    contacts = _deduplicate(contacts)
    status = "not_found"
    if any(row.get("verification_status") == "auto_verified" for row in contacts):
        status = "auto_verified"
    elif contacts:
        status = "review_required"
    collected_at = datetime.now(timezone.utc).isoformat()
    for contact in contacts:
        contact["collected_at"] = collected_at
        contact.setdefault("metadata", {})
    return {
        "ok": True,
        "company_name": company_name,
        "address": address,
        "status": status,
        "contacts": contacts,
        "website_url": website_url,
        "website_confidence": website_confidence,
        "trace": trace,
        "collected_at": collected_at,
    }
