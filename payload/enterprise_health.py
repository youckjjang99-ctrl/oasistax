from __future__ import annotations
from typing import Any


def _clamp(value: Any, default: int = 0) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except Exception:
        return default


def _grade(score: int) -> str:
    if score >= 90: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "B"
    if score >= 60: return "C"
    return "D"


def _status(score: int) -> str:
    if score >= 80: return "양호"
    if score >= 65: return "보통"
    if score >= 50: return "주의"
    return "우선 개선"


def _priority(level: int) -> str:
    return "즉시" if level >= 85 else "우선" if level >= 70 else "검토"


def build_enterprise_health(analysis: dict[str, Any] | None, tax_core: dict[str, Any] | None,
                            anomaly_result: dict[str, Any] | None) -> dict[str, Any]:
    analysis, tax_core, anomaly_result = analysis or {}, tax_core or {}, anomaly_result or {}
    health = analysis.get("company_health", {}) or {}
    completeness = _clamp(analysis.get("completeness", 0))
    confidence = _clamp(analysis.get("ai_confidence", 0))

    profitability = _clamp(health.get("profitability_score", 0))
    stability = _clamp(health.get("stability_score", 0))
    growth = _clamp(health.get("growth_score", 0))
    financial = _clamp(profitability * .40 + stability * .40 + growth * .20)

    tax_opportunity = _clamp(tax_core.get("overall_score", health.get("tax_opportunity_score", 0)))
    tax_confidence = _clamp(tax_core.get("overall_confidence", confidence))
    tax_readiness = _clamp(tax_opportunity * .70 + tax_confidence * .30)
    anomaly_risk = _clamp(anomaly_result.get("overall_score", health.get("tax_risk_score", 0)))
    risk_control = _clamp(100 - anomaly_risk)

    sources = analysis.get("data_sources", {}) or {}
    employee_summary = analysis.get("employee_summary", {}) or {}
    governance_signals = [bool(sources.get("registry")), bool(sources.get("stock_valuation")),
                          bool(analysis.get("registry")), bool(analysis.get("stock_record"))]
    governance = _clamp(35 + sum(15 for signal in governance_signals if signal))
    employee_connected = bool(sources.get("employee_status"))
    active_count = int(employee_summary.get("active_count", 0) or 0)
    employment = _clamp(35 + (25 if employee_connected else 0) + (20 if active_count >= 5 else 10 if active_count else 0))
    consultation = analysis.get("consultation_context", {}) or {}
    consultation_count = int(consultation.get("count", 0) or analysis.get("consultation_count", 0) or 0)
    execution = _clamp(35 + min(30, consultation_count * 6) + (15 if tax_core else 0) + (15 if anomaly_result else 0))
    data_quality = _clamp(completeness * .65 + confidence * .35)

    categories = [
        {"name": "재무건전성", "score": financial, "weight": 24, "status": _status(financial), "reason": "수익성·안정성·성장성 통합"},
        {"name": "절세준비도", "score": tax_readiness, "weight": 18, "status": _status(tax_readiness), "reason": "절세기회와 증빙 신뢰도 반영"},
        {"name": "리스크관리", "score": risk_control, "weight": 16, "status": _status(risk_control), "reason": "재무 이상징후 위험도의 역점수"},
        {"name": "지배구조·주가", "score": governance, "weight": 12, "status": _status(governance), "reason": "등기·주가평가 연결 상태"},
        {"name": "고용·인사준비", "score": employment, "weight": 10, "status": _status(employment), "reason": "직원현황과 고용규모 반영"},
        {"name": "컨설팅 실행력", "score": execution, "weight": 8, "status": _status(execution), "reason": "상담일지·진단 실행자료 반영"},
        {"name": "자료완성도", "score": data_quality, "weight": 12, "status": _status(data_quality), "reason": "자료 충족도와 AI 신뢰도 반영"},
    ]
    overall = _clamp(sum(item["score"] * item["weight"] for item in categories) / 100)

    actions: list[dict[str, Any]] = []
    for item in anomaly_result.get("items", []) or []:
        score = _clamp(item.get("score", 0))
        if score >= 55:
            actions.append({"title": f"{item.get('name', '재무 이상징후')} 확인", "area": "재무·세무 리스크",
                            "priority_score": min(100, score + 10), "priority": _priority(min(100, score + 10)),
                            "reason": (item.get("reasons") or ["추가 원장 확인 필요"])[0],
                            "next_step": " / ".join((item.get("documents") or ["계정별원장"])[0:2])})
    for item in tax_core.get("priority_items", []) or []:
        score = _clamp(item.get("score", 0))
        if score >= 45:
            actions.append({"title": item.get("name", "절세 검토"), "area": "절세기회",
                            "priority_score": score, "priority": _priority(score),
                            "reason": f"{item.get('status', '자료 확인')} · AI 신뢰도 {item.get('confidence', 0)}%",
                            "next_step": " / ".join((item.get("action_items") or ["증빙자료 확인"])[0:2])})
    if completeness < 70:
        actions.append({"title": "기업자료 보완", "area": "자료관리", "priority_score": 90 - completeness // 2,
                        "priority": "우선", "reason": f"현재 자료 충족도 {completeness}%",
                        "next_step": "크레탑·등기·직원현황·세무증빙 중 미등록 자료 보완"})
    if governance < 65:
        actions.append({"title": "등기·주가·정관 연결 점검", "area": "지배구조", "priority_score": 68,
                        "priority": "검토", "reason": "지배구조 관련 저장자료가 충분하지 않음",
                        "next_step": "최신 법인등기와 주가평가 등록 여부 확인"})
    if not employee_connected:
        actions.append({"title": "직원현황 등록", "area": "고용지원", "priority_score": 62,
                        "priority": "검토", "reason": "고용지원금 검토에 필요한 직원현황 미연결",
                        "next_step": "4대보험 가입자명부 또는 직원현황 등록"})

    unique = {}
    for action in actions: unique.setdefault(action["title"], action)
    actions = sorted(unique.values(), key=lambda item: (-item["priority_score"], item["title"]))[:8]
    strengths = sorted(categories, key=lambda item: (-item["score"], item["name"]))[:2]
    weaknesses = sorted(categories, key=lambda item: (item["score"], item["name"]))[:2]
    return {"overall_score": overall, "grade": _grade(overall), "level": _status(overall),
            "confidence": confidence, "stage": health.get("stage", "판단보류"),
            "stage_reason": health.get("stage_reason", ""), "categories": categories,
            "actions": actions, "strengths": strengths, "weaknesses": weaknesses,
            "disclaimer": "기업 건강점수는 현재 등록자료와 기존 AI 진단결과를 통합한 사전 경영진단 지표입니다. 세액공제, 지원금, 대출, 법률·노무 적용 여부를 확정하지 않습니다."}
