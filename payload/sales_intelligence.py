from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import kakao_local_client
import kipris_patent_client
import localdata_contact_client
from contact_matching import is_mobile_phone


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


def _best_phone(
    company_name: str,
    address: str,
    industry_name: str,
) -> dict[str, Any]:
    kakao = kakao_local_client.search_company(company_name, address)
    candidates = [
        row
        for row in kakao.get("candidates", [])
        if row.get("phone") and int(row.get("confidence") or 0) >= 65
    ]
    if candidates:
        best = candidates[0]
        return {
            "phone": best.get("phone", ""),
            "phone_source": "카카오 로컬",
            "phone_confidence": int(best.get("confidence") or 0),
            "phone_review_required": is_mobile_phone(best.get("phone")),
            "contact_status": "FOUND",
        }

    localdata = localdata_contact_client.search_company(
        company_name,
        address,
        industry_name,
        max_services=2,
    )
    candidates = [
        row
        for row in localdata.get("candidates", [])
        if row.get("phone") and int(row.get("confidence") or 0) >= 65
    ]
    if candidates:
        best = candidates[0]
        return {
            "phone": best.get("phone", ""),
            "phone_source": str(best.get("source_type") or "인허가 API"),
            "phone_confidence": int(best.get("confidence") or 0),
            "phone_review_required": is_mobile_phone(best.get("phone")),
            "contact_status": "FOUND",
        }
    return {
        "phone": "",
        "phone_source": "",
        "phone_confidence": 0,
        "phone_review_required": False,
        "contact_status": (
            kakao.get("status")
            if kakao.get("status") not in {"SUCCESS", None}
            else localdata.get("status", "NOT_FOUND")
        ),
    }


def _sales_needs(
    patent_count: int,
    new_employee_count: int,
    lost_employee_count: int,
    employee_count: int,
) -> list[dict[str, Any]]:
    needs: list[dict[str, Any]] = []
    net_hiring = new_employee_count - lost_employee_count
    if patent_count > 0:
        needs.append(
            {
                "code": "patent_benefit",
                "topic": "특허·연구개발 혜택",
                "reason": f"등록특허 {patent_count}건 확인",
                "question": (
                    "보유 특허와 연구개발비에 대해 정책자금·기술보증·"
                    "세액공제 적용 가능성을 검토하고 계신가요?"
                ),
            }
        )
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
                "reason": "서울·경기 사업장 공개정보 확인",
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
    secondary = needs[1] if len(needs) > 1 else None
    opening = (
        f"안녕하세요, {company_name} 대표님 또는 정부지원제도 담당자분 "
        "연결 가능하실까요? 오아시스 기업지원센터입니다."
    )
    reason = (
        f"공개된 기업정보를 확인하던 중 {primary['reason']} 내용이 있어 "
        f"{primary['topic']} 검토 가능성을 안내드리려고 연락드렸습니다."
    )
    if secondary:
        reason += (
            f" 또한 {secondary['reason']} 부분도 함께 확인할 수 있습니다."
        )
    close = (
        f"{primary['question']} 대상 여부는 세부조건 확인이 필요해서, "
        "20초 정도 몇 가지만 여쭤봐도 괜찮을까요?"
    )
    if phone_review_required:
        close += " 현재 번호가 대표번호인지 먼저 확인 부탁드립니다."
    return " ".join((opening, reason, close))


def analyze_sales_candidate(prospect: dict[str, Any]) -> dict[str, Any]:
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

    with ThreadPoolExecutor(max_workers=2) as executor:
        phone_future = executor.submit(
            _best_phone,
            company_name,
            address,
            industry_name,
        )
        patent_future = executor.submit(
            kipris_patent_client.search_registered_patents,
            company_name,
        )
        phone_result = phone_future.result()
        patent_result = patent_future.result()

    patent_count = int(patent_result.get("registered_count") or 0)
    needs = _sales_needs(
        patent_count,
        new_employee_count,
        lost_employee_count,
        employee_count,
    )
    net_hiring = new_employee_count - lost_employee_count
    score = 20
    if phone_result.get("phone"):
        score += 20
    if patent_count:
        score += min(30, 20 + patent_count * 2)
    if net_hiring > 0:
        score += min(25, 15 + net_hiring * 2)
    elif new_employee_count > 0:
        score += 10
    if employee_count >= 5:
        score += 5
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
        "patent_status": patent_result.get("status", ""),
        "patent_message": patent_result.get("message", ""),
        "registered_patent_count": patent_count,
        "active_patent_count": int(patent_result.get("active_count") or 0),
        "patent_titles": [
            row.get("invention_title", "")
            for row in patent_result.get("patents", [])[:5]
        ],
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
    }


def merge_analysis(
    prospect: dict[str, Any],
    analysis: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(prospect)
    existing_reasons = list(prospect.get("추천사유") or [])
    sales_reasons = list(analysis.get("sales_reasons") or [])
    combined_reasons = list(dict.fromkeys(existing_reasons + sales_reasons))
    merged["영업분석"] = analysis
    merged["대표전화"] = analysis.get("phone", "")
    merged["특허등록"] = analysis.get("registered_patent_count", 0)
    merged["특허확인"] = analysis.get("patent_status", "")
    merged["순고용증가"] = analysis.get("net_hiring", 0)
    merged["영업주제"] = " · ".join(analysis.get("sales_topics") or [])
    merged["추천등급"] = analysis.get("recommendation_grade", "")
    merged["우선순위점수"] = analysis.get("recommendation_score", 0)
    merged["추천사유"] = combined_reasons
    merged["초회전화스크립트"] = analysis.get("first_call_script", "")
    return merged
