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
    if not phone or score < REVIEW_SCORE:
        return None
    return {
        "contact_type": "phone",
        "contact_value": phone,
        "contact_label": (
            "휴대전화 확인 필요"
            if is_mobile_phone(phone)
            else "사업장 공개 대표전화"
        ),
        "source_type": str(candidate.get("source_type") or ""),
        "source_url": str(candidate.get("source_url") or ""),
        "confidence": score,
        "verification_status": _verification_status(score, phone=phone),
        "is_primary": score >= AUTO_CONFIRM_SCORE and not is_mobile_phone(phone),
        "metadata": {
            "matched_company_name": candidate.get("company_name", ""),
            "matched_address": candidate.get("address", ""),
            "phone_type": candidate.get("phone_type", ""),
        },
    }


def _deduplicate(contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_priority = {
        "official_website": 3,
        "kakao_local": 2,
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


def enrich_company(prospect: dict[str, Any]) -> dict[str, Any]:
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

    kakao = kakao_local_client.search_company(company_name, address)
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
    if not reliable_kakao_phone:
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
                "message": "카카오에서 신뢰도 높은 대표전화를 확인했습니다.",
            }
        )

    website_url = ""
    website_confidence = 0
    naver = naver_web_search_client.search_official_websites(
        company_name,
        address,
    )
    trace.append(
        {
            "stage": "naver",
            "status": naver.get("status"),
            "message": naver.get("message"),
        }
    )
    for candidate in naver.get("candidates", [])[:3]:
        website = inspect_website(
            candidate.get("url", ""),
            company_name,
            address,
            business_no,
        )
        if not website.get("ok"):
            continue
        score = int(website.get("confidence") or 0)
        if score < REVIEW_SCORE:
            continue
        website_url = str(website.get("website_url") or candidate.get("url") or "")
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
                "metadata": {"pages_checked": website.get("pages_checked", 0)},
            }
        )
        for contact in website.get("contacts", []):
            contact = dict(contact)
            contact_score = int(contact.get("confidence") or 0)
            domain_match = bool(
                (contact.get("metadata") or {}).get("domain_match")
            )
            contact["verification_status"] = _verification_status(
                contact_score,
                phone=(
                    contact.get("contact_value", "")
                    if contact.get("contact_type") == "phone"
                    else ""
                ),
                is_email=contact.get("contact_type") == "email",
                email_domain_match=domain_match,
            )
            contact["is_primary"] = (
                contact["verification_status"] == "auto_verified"
            )
            contacts.append(contact)
        trace.append(
            {
                "stage": "website",
                "status": website.get("status"),
                "message": website.get("message"),
                "confidence": score,
            }
        )
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
