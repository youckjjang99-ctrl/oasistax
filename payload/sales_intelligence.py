from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import kakao_local_client
import localdata_contact_client
import naver_web_search_client
from contact_enrichment import enrich_company
from contact_matching import is_mobile_phone, normalize_phone


REVIEW_SCORE = 65


def _number(prospect: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = prospect.get(key)
        if value not in (None, ""):
            try:
                return int(float(str(value).replace(",", "")))
            except (TypeError, ValueError):
                continue
    return 0


def _text(prospect: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(prospect.get(key) or "").strip()
        if value:
            return value
    return ""


def _phone_result(
    phone: str,
    source: str,
    confidence: int,
    *,
    status: str = "FOUND",
    trace: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    normalized_phone = normalize_phone(phone)
    return {
        "phone": normalized_phone,
        "phone_source": source,
        "phone_confidence": confidence,
        "phone_review_required": is_mobile_phone(normalized_phone),
        "contact_status": status if normalized_phone else (
            status if status in {"ERROR", "NOT_FOUND"} else "NOT_FOUND"
        ),
        "contact_trace": trace or [],
    }


def _source_label(source_type: str) -> str:
    source_type = str(source_type or "")
    if source_type == "official_website":
        return "공식 홈페이지"
    if source_type == "kakao_local":
        return "카카오 로컬"
    if source_type == "naver_web_snippet":
        return "네이버 공개검색"
    if source_type.startswith("localdata:"):
        return "승인 인허가 API"
    return source_type or "공개 연락처"


def _extended_phone(
    company_name: str,
    address: str,
    industry_name: str,
) -> dict[str, Any]:
    """빠른 조회에서 비어 있는 대표전화를 전체 공개 연락처 흐름으로 보강."""
    try:
        enriched = enrich_company(
            {
                "company_name": company_name,
                "address": address,
                "industry_name": industry_name,
            },
            skip_kakao=True,
            skip_localdata=True,
            max_website_candidates=1,
            website_timeout=4,
            website_max_pages=2,
        )
    except Exception as exc:
        return _phone_result(
            "",
            "",
            0,
            status="ERROR",
            trace=[
                {
                    "stage": "contact_enrichment",
                    "status": type(exc).__name__,
                    "message": str(exc),
                }
            ],
        )
    phone_contacts = [
        row
        for row in enriched.get("contacts", [])
        if row.get("contact_type") == "phone"
        and str(row.get("contact_value") or "").strip()
        and str(row.get("verification_status") or "") != "rejected"
    ]
    if phone_contacts:
        phone_contacts.sort(
            key=lambda row: (
                row.get("verification_status") != "auto_verified",
                is_mobile_phone(row.get("contact_value", "")),
                -int(row.get("confidence") or 0),
            )
        )
        best = phone_contacts[0]
        return _phone_result(
            str(best.get("contact_value") or ""),
            _source_label(str(best.get("source_type") or "")),
            int(best.get("confidence") or 0),
            trace=list(enriched.get("trace") or []),
        )
    return _phone_result(
        "",
        "",
        0,
        status="NOT_FOUND",
        trace=list(enriched.get("trace") or []),
    )


def _best_phone(
    company_name: str,
    address: str,
    industry_name: str,
    *,
    allow_extended: bool = True,
) -> dict[str, Any]:
    # 서로 독립적인 무료 공개 소스를 병렬 조회한다. 기존 순차 방식은
    # 업체 한 곳마다 타임아웃이 누적되어 전체 검색이 수분 이상 지연됐다.
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            "kakao": executor.submit(
                kakao_local_client.search_company,
                company_name,
                address,
                timeout=5,
            ),
            "localdata": executor.submit(
                localdata_contact_client.search_company,
                company_name,
                address,
                industry_name,
                timeout=5,
                max_services=2,
            ),
            "naver": executor.submit(
                naver_web_search_client.search_public_phones,
                company_name,
                address,
                timeout=5,
            ),
        }
        source_results: dict[str, dict[str, Any]] = {}
        trace: list[dict[str, Any]] = []
        for source_name, future in futures.items():
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "status": type(exc).__name__,
                    "message": str(exc),
                    "candidates": [],
                }
            source_results[source_name] = result
            trace.append(
                {
                    "stage": source_name,
                    "status": result.get("status"),
                    "message": result.get("message"),
                }
            )

    candidates: list[dict[str, Any]] = []
    for result in source_results.values():
        candidates.extend(
            row
            for row in result.get("candidates", [])
            if row.get("phone")
            and int(row.get("confidence") or 0) >= REVIEW_SCORE
        )
    if candidates:
        source_priority = {
            "kakao_local": 3,
            "localdata": 2,
            "naver_web_snippet": 1,
        }
        candidates.sort(
            key=lambda row: (
                int(row.get("confidence") or 0),
                source_priority.get(
                    str(row.get("source_type") or "").split(":", 1)[0],
                    0,
                ),
            ),
            reverse=True,
        )
        best = candidates[0]
        return _phone_result(
            str(best.get("phone") or ""),
            _source_label(str(best.get("source_type") or "")),
            int(best.get("confidence") or 0),
            trace=trace,
        )

    if allow_extended:
        return _extended_phone(company_name, address, industry_name)
    return _phone_result(
        "",
        "",
        0,
        status="NOT_FOUND",
        trace=trace,
    )


def _sales_needs(
    new_employee_count: int,
    lost_employee_count: int,
    employee_count: int,
) -> list[dict[str, Any]]:
    needs: list[dict[str, Any]] = []
    net_hiring = new_employee_count - lost_employee_count
    if net_hiring > 0:
        needs.append(
            {
                "code": "employment_growth",
                "topic": "고용지원금",
                "reason": (
                    f"신규취득 {new_employee_count}명, "
                    f"상실 {lost_employee_count}명으로 순증 {net_hiring}명"
                ),
                "question": (
                    "최근 채용 인원에 대해 청년·고령자·육아지원 등 "
                    "고용지원금 사전검토를 받아보셨나요?"
                ),
            }
        )
    elif new_employee_count > 0:
        needs.append(
            {
                "code": "recent_hiring",
                "topic": "신규채용 지원금",
                "reason": f"최근 신규취득자 {new_employee_count}명 확인",
                "question": (
                    "최근 입사자의 연령과 채용경로를 기준으로 "
                    "고용지원금 적용 여부를 확인해보셨나요?"
                ),
            }
        )
    elif employee_count >= 5:
        needs.append(
            {
                "code": "employment_diagnosis",
                "topic": "고용지원금 사전진단",
                "reason": f"국민연금 가입자 {employee_count}명",
                "question": (
                    "향후 채용계획이나 근로시간 단축·유연근무제도 "
                    "도입계획이 있으신가요?"
                ),
            }
        )
    if not needs:
        needs.append(
            {
                "code": "policy_fund",
                "topic": "정책자금 사전진단",
                "reason": "서울·경기 주식회사 사업장 공개정보 확인",
                "question": (
                    "올해 시설·운전자금 계획이나 추가 채용계획이 있으신가요?"
                ),
            }
        )
    return needs


def _script(
    company_name: str,
    needs: list[dict[str, Any]],
    phone_review_required: bool,
) -> str:
    primary = needs[0]
    opening = (
        f"안녕하세요, {company_name} 대표님 또는 정부지원제도 담당자분 "
        "연결 가능하실까요? 오아시스 기업지원센터입니다."
    )
    reason = (
        f"공개된 기업정보를 확인하던 중 {primary['reason']} 내용이 있어 "
        f"{primary['topic']} 검토 가능성을 안내드리려고 연락드렸습니다."
    )
    close = (
        f"{primary['question']} 대상 여부는 세부조건 확인이 필요해서, "
        "20초 정도 몇 가지만 여쭤봐도 괜찮을까요?"
    )
    if phone_review_required:
        close += " 현재 번호가 대표번호인지 먼저 확인 부탁드립니다."
    return " ".join((opening, reason, close))


def analyze_sales_candidate(
    prospect: dict[str, Any],
    *,
    contact_mode: str = "full",
) -> dict[str, Any]:
    company_name = _text(prospect, "company_name", "사업장명")
    address = _text(prospect, "address", "주소")
    industry_name = _text(prospect, "industry_name", "업종명")
    employee_count = _number(prospect, "employee_count", "가입자수")
    new_employee_count = _number(
        prospect,
        "new_employee_count",
        "신규취득자수",
    )
    lost_employee_count = _number(
        prospect,
        "lost_employee_count",
        "상실가입자수",
    )

    phone_result = _best_phone(
        company_name,
        address,
        industry_name,
        allow_extended=contact_mode != "quick",
    )
    needs = _sales_needs(
        new_employee_count,
        lost_employee_count,
        employee_count,
    )
    net_hiring = new_employee_count - lost_employee_count
    score = 20
    if phone_result.get("phone"):
        score += 25
    if net_hiring > 0:
        score += min(35, 20 + net_hiring * 3)
    elif new_employee_count > 0:
        score += 15
    if employee_count >= 5:
        score += 10
    score = min(100, score)
    grade = "A" if score >= 75 else ("B" if score >= 55 else "C")
    analyzed_at = datetime.now(timezone.utc).isoformat()
    return {
        "company_name": company_name,
        "phone": phone_result.get("phone", ""),
        "phone_source": phone_result.get("phone_source", ""),
        "phone_confidence": phone_result.get("phone_confidence", 0),
        "phone_review_required": phone_result.get(
            "phone_review_required",
            False,
        ),
        "contact_status": phone_result.get("contact_status", ""),
        "contact_trace": phone_result.get("contact_trace", []),
        "employee_count": employee_count,
        "new_employee_count": new_employee_count,
        "lost_employee_count": lost_employee_count,
        "net_hiring": net_hiring,
        "sales_topics": [row["topic"] for row in needs],
        "sales_reasons": [row["reason"] for row in needs],
        "needs_questions": [row["question"] for row in needs],
        "primary_topic": needs[0]["topic"],
        "recommendation_score": score,
        "recommendation_grade": grade,
        "first_call_script": _script(
            company_name,
            needs,
            bool(phone_result.get("phone_review_required")),
        ),
        "analyzed_at": analyzed_at,
        "contact_mode": contact_mode,
    }


def merge_analysis(
    prospect: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(prospect)
    existing_reasons = list(prospect.get("추천사유") or [])
    sales_reasons = list(analysis.get("sales_reasons") or [])
    combined_reasons = list(dict.fromkeys(existing_reasons + sales_reasons))
    merged.pop("특허등록", None)
    merged.pop("특허확인", None)
    merged["영업분석"] = analysis
    merged["대표전화"] = analysis.get("phone", "")
    merged["전화출처"] = analysis.get("phone_source", "")
    merged["연락처상태"] = analysis.get("contact_status", "")
    merged["연락처조회이력"] = analysis.get("contact_trace", [])
    merged["순고용증가"] = analysis.get("net_hiring", 0)
    merged["영업주제"] = " · ".join(analysis.get("sales_topics") or [])
    merged["추천등급"] = analysis.get("recommendation_grade", "")
    merged["우선순위점수"] = analysis.get("recommendation_score", 0)
    merged["추천사유"] = combined_reasons
    merged["초회전화스크립트"] = analysis.get("first_call_script", "")
    return merged
