from __future__ import annotations
from typing import Any


def _n(v: Any):
    try:
        s = str(v).replace(",", "").strip()
        return None if s.lower() in {"", "nan", "none", "nat", "-"} else float(s)
    except Exception:
        return None


def _r(a: Any, b: Any):
    a, b = _n(a), _n(b)
    return None if a is None or b in (None, 0) else a / b * 100


def _c(v: float) -> int:
    return max(0, min(round(v), 100))


def build_company_health(
    *, sales: Any, operating_profit: Any, net_income: Any, assets: Any,
    liabilities: Any, equity: Any, employees: Any, completeness: int,
    confidence: int, comprehensive_diagnosis: dict[str, Any],
    consultation_context: dict[str, Any], stock_record: dict[str, Any],
) -> dict[str, Any]:
    s, op, ni, a, l, e, emp = map(_n, [sales, operating_profit, net_income, assets, liabilities, equity, employees])
    om, nm, dr = _r(op, s), _r(ni, s), _r(l, e)

    profitability = 50 + (20 if om is not None and om >= 10 else 12 if om is not None and om >= 5 else 4 if om is not None and om >= 0 else -24 if om is not None else -15)
    profitability += (15 if nm is not None and nm >= 7 else 8 if nm is not None and nm >= 2 else 2 if nm is not None and nm >= 0 else -18 if nm is not None else -10)
    profitability = _c(profitability)

    stability = 55 + (-15 if dr is None else 25 if dr <= 100 else 12 if dr <= 200 else -5 if dr <= 300 else -25)
    if e is not None and e <= 0:
        stability -= 25
    stability = _c(stability)

    growth = 45 + (-15 if s is None else 25 if s >= 1e10 else 18 if s >= 3e9 else 10 if s >= 1e9 else 4 if s >= 3e8 else -12)
    if emp is not None:
        growth += 10 if emp >= 20 else 5 if emp >= 5 else 0
    if op is not None and op < 0:
        growth -= 15
    growth = _c(growth)

    findings = (comprehensive_diagnosis or {}).get("findings", []) if isinstance(comprehensive_diagnosis, dict) else []
    high = sum(1 for x in findings if isinstance(x, dict) and int(x.get("priority", 0) or 0) >= 65)
    tax = _c(35 + min(35, len(findings) * 6) + (10 if ni is not None and ni > 0 else 0) + (5 if stock_record else 0) + (5 if int((consultation_context or {}).get("count", 0) or 0) else 0))
    risk = _c(25 + min(50, high * 10) + (15 if dr is not None and dr >= 300 else 0) + (10 if op is not None and op < 0 else 0))
    score = _c(profitability * .30 + stability * .30 + growth * .20 + (100 - risk) * .10 + confidence * .10)

    if s is None or s < 3e8:
        stage = "재정비기" if (op or 0) < 0 or (ni or 0) < 0 else "초기·소규모 운영기"
        reason = "매출규모가 작고 손익 개선이 우선입니다." if stage == "재정비기" else "매출·현금흐름과 영업기반 확보가 우선입니다."
    elif s < 3e9:
        stage, reason = "성장 기반기", "매출확대와 운전자금·수익성 관리가 중요합니다."
    elif profitability >= 65 and stability >= 60:
        stage, reason = "성숙·관리기", "축적이익·세무·지배구조 관리로 확장할 단계입니다."
    else:
        stage, reason = "성장기", "성장과 재무안정성을 동시에 관리할 단계입니다."

    level = "자료 부족" if completeness < 30 else "높음" if tax >= 75 else "보통" if tax >= 55 else "낮음"
    return {
        "health_score": score,
        "profitability_score": profitability,
        "stability_score": stability,
        "growth_score": growth,
        "tax_opportunity_score": tax,
        "tax_opportunity_level": level,
        "tax_risk_score": risk,
        "stage": stage,
        "stage_reason": reason,
        "disclaimer": "기업건강점수와 절세가능성은 현재 등록자료를 이용한 사전진단이며 세액 또는 절세금액을 확정하지 않습니다.",
    }
