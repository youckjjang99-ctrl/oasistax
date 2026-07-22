from __future__ import annotations
from typing import Any


def _clean(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"", "none", "nan", "nat", "-"} else s


def _has(v: Any) -> bool:
    if isinstance(v, dict):
        return any(_has(x) for x in v.values())
    if isinstance(v, list):
        return bool(v)
    return bool(_clean(v))


def _financial_depth(financial: dict[str, Any]) -> tuple[bool, bool]:
    if not isinstance(financial, dict) or not financial:
        return False, False
    summary = ("매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계")
    summary_ok = sum(1 for k in summary if _clean(financial.get(k))) >= 4
    signals = (
        "계정과목", "계정별", "재무상태표", "손익계산서", "현금흐름표",
        "재무연도별", "balance_sheet", "income_statement", "accounts",
    )
    detail = sum(
        1 for k, v in financial.items()
        if any(s.lower() in str(k).lower() for s in signals) and _has(v)
    )
    return summary_ok, detail >= 2


def build_data_completeness(
    *, business_no: str, industry: str, address: str, establishment: str,
    financial: dict[str, Any], registry: dict[str, Any], stock_record: dict[str, Any],
    consultation_context: dict[str, Any], employee_context: dict[str, Any],
    articles_review: dict[str, Any], preferences: dict[str, Any],
) -> dict[str, Any]:
    summary_ok, detail_ok = _financial_depth(financial)
    profile = sum(bool(_clean(x)) for x in [business_no, industry, address, establishment]) / 4
    consult = int((consultation_context or {}).get("count", 0) or 0)
    transcript = int((consultation_context or {}).get("transcript_count", 0) or 0)
    employee_summary = (employee_context or {}).get("summary", {}) if isinstance(employee_context, dict) else {}
    employee_ok = bool(
        employee_context and (
            (employee_context or {}).get("employees")
            or employee_summary.get("active_count")
            or employee_summary.get("total_count")
        )
    )
    pref_ok = bool(
        preferences and any(
            preferences.get(k)
            for k in ("관심지원분야", "매칭키워드", "자금사용목적", "저장정책자금")
        )
    )

    components = [
        {"name": "기업 기본정보", "weight": 5, "earned": round(5 * profile, 1), "ready": profile >= .75, "next_action": "사업자번호·업종·주소·설립일 확인"},
        {"name": "크레탑 요약재무", "weight": 12, "earned": 12 if summary_ok else 0, "ready": summary_ok, "next_action": "최근 결산 크레탑 재무자료 등록"},
        {"name": "상세재무·계정과목", "weight": 13, "earned": 13 if detail_ok else 0, "ready": detail_ok, "next_action": "상세 재무제표와 계정과목 자료 등록"},
        {"name": "계정별원장", "weight": 15, "earned": 0, "ready": False, "next_action": "가지급금·대여금·접대비 등 계정별원장 등록"},
        {"name": "법인세 신고자료", "weight": 10, "earned": 0, "ready": False, "next_action": "법인세 신고서·조정계산서·세액공제명세 등록"},
        {"name": "부가세 신고자료", "weight": 5, "earned": 0, "ready": False, "next_action": "최근 부가세 과세표준·신고자료 등록"},
        {"name": "법인등기", "weight": 10, "earned": 10 if registry else 0, "ready": bool(registry), "next_action": "최신 법인등기 등록"},
        {"name": "정관검토", "weight": 8, "earned": 8 if articles_review else 0, "ready": bool(articles_review), "next_action": "현행 정관과 임원규정 등록"},
        {"name": "직원현황", "weight": 7, "earned": 7 if employee_ok else 0, "ready": employee_ok, "next_action": "4대보험 가입자명부 등록"},
        {"name": "상담일지", "weight": 5, "earned": 5 if consult else 0, "ready": bool(consult), "next_action": "대표 상담일지 작성"},
        {"name": "녹취분석", "weight": 4, "earned": 4 if transcript else 0, "ready": bool(transcript), "next_action": "상담 녹취 분석 등록"},
        {"name": "주가평가", "weight": 4, "earned": 4 if stock_record else 0, "ready": bool(stock_record), "next_action": "비상장주식 가치평가 저장"},
        {"name": "대표 니즈·매칭설정", "weight": 2, "earned": 2 if pref_ok else 0, "ready": pref_ok, "next_action": "자금용도·관심분야·상담목표 입력"},
    ]

    score = max(0, min(round(sum(float(x["earned"]) for x in components)), 100))
    core = sum(
        1 for x in components
        if x["name"] in {"계정별원장", "법인세 신고자료", "상세재무·계정과목"} and x["ready"]
    )
    confidence = min(score, 32 if core == 0 else 55 if core == 1 else 78 if core == 2 else 95)
    status = "매우 충분" if score >= 85 else "충분" if score >= 70 else "보통" if score >= 50 else "보완 필요" if score >= 30 else "자료 부족"
    confidence_status = "매우 높음" if confidence >= 80 else "높음" if confidence >= 60 else "보통" if confidence >= 40 else "낮음"
    actions = [
        {"name": x["name"], "gain": x["weight"], "action": x["next_action"]}
        for x in components if not x["ready"]
    ]
    actions.sort(key=lambda x: x["gain"], reverse=True)
    return {
        "score": score,
        "status": status,
        "confidence": confidence,
        "confidence_status": confidence_status,
        "components": components,
        "missing_sources": [x["name"] for x in components if not x["ready"]],
        "next_actions": actions,
    }
