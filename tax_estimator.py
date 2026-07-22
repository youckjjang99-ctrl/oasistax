from __future__ import annotations
from typing import Any


def estimate_effect(rule: dict[str, Any], base_amount: float | None = None, headcount: int | None = None) -> dict[str, Any]:
    kind = rule.get("calc_type")
    if kind == "rate" and base_amount and base_amount > 0:
        low = round(base_amount * float(rule.get("low_rate", 0)))
        high = round(base_amount * float(rule.get("high_rate", 0)))
        return {"low": low, "high": high, "label": f"{low:,.0f}원 ~ {high:,.0f}원"}
    if kind == "per_head" and headcount and headcount > 0:
        low = round(headcount * float(rule.get("low_amount", 0)))
        high = round(headcount * float(rule.get("high_amount", 0)))
        return {"low": low, "high": high, "label": f"{low:,.0f}원 ~ {high:,.0f}원"}
    return {"low": None, "high": None, "label": "기초자료 입력 후 산정"}


def confidence_score(reasons: list[str], missing: list[str], status: str) -> int:
    score = 35 + min(35, len(reasons) * 12) - min(25, len(missing) * 3)
    if status == "검토 우선":
        score += 12
    elif status == "가능성 낮음":
        score -= 8
    return max(20, min(95, score))
