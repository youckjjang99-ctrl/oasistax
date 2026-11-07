"""Conservative, side-effect-free share-history plans for stock valuation.

This module does not calculate deemed gifts or infer paid-in cash from par
value. Income adjustments under Enforcement Decree 56(5) remain an explicitly
blocked expert-input step. Statutory fractional conversions are never rounded.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from typing import Any


LAW_REFERENCES = (
    "https://www.law.go.kr/법령/상속세및증여세법시행령/제56조",
    "https://www.law.go.kr/법령/상속세및증여세법시행규칙/제17조의3",
)


def capital_source_signature(registry: dict[str, Any]) -> str:
    """Bind a confirmed plan to the unchanged original registry snapshot."""
    if not isinstance(registry, dict):
        raise ValueError("등기 원본 정보 형식을 확인해주세요.")
    try:
        encoded = json.dumps(registry, sort_keys=True, ensure_ascii=False, default=str, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("등기 원본의 자본금·주식수는 유한한 정수로 확인해주세요.") from None
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _day(value: Any, label: str, *, optional: bool = False) -> date | None:
    if value in (None, "") and optional:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        raise ValueError(f"{label}은 YYYY-MM-DD 형식의 실제 효력일로 확인해주세요.") from None


def _integer(value: Any, label: str, *, optional: bool = False) -> int | None:
    if value in (None, "") and optional:
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label}은 양의 정수로 입력해주세요.") from None
    if isinstance(value, bool) or not number.is_finite() or number <= 0 or number != number.to_integral_value():
        raise ValueError(f"{label}은 양의 정수로 입력해주세요.")
    return int(number)


def _next_year(value: date) -> date | None:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return None  # Do not assume a leap-day fiscal-period convention.


def _kind(value: Any) -> str:
    token = str(value or "unknown").strip().lower()
    return {"paid": "paid", "paid_increase": "paid", "paid_decrease": "paid",
            "유상": "paid", "유상증자": "paid", "유상감자": "paid",
            "bonus": "bonus", "bonus_increase": "bonus", "bonus_decrease": "bonus",
            "무상": "bonus", "무상증자": "bonus", "무상감자": "bonus",
            "unknown": "unknown", "미확인": "unknown", "": "unknown"}.get(token, "unsupported")


def _snapshots(registry: dict[str, Any]) -> list[dict[str, Any]]:
    values = registry.get("capital_history", []) or []
    if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
        raise ValueError("등기 자본변동 이력 형식을 확인해주세요.")
    result = []
    for row in values:
        normalized = deepcopy(row)
        normalized.update(shares=_integer(row.get("shares"), "등기 주식수", optional=True),
                          capital=_integer(row.get("capital"), "등기 자본금", optional=True),
                          par_value=_integer(row.get("par_value"), "액면가", optional=True),
                          effective_date=_day(row.get("effective_date"), "등기 효력일", optional=True))
        result.append(normalized)
    return sorted(result, key=lambda row: row["effective_date"] or date.max)


def _events(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
        raise ValueError("확정할 자본변동 이력 형식을 확인해주세요.")
    result = []
    for row in values:
        event = deepcopy(row)
        event["effective_date"] = _day(row.get("effective_date"), "자본변동 효력일")
        for field in ("before_shares", "after_shares"):
            event[field] = _integer(row.get(field), "변동 전·후 주식수")
        for field in ("before_capital", "after_capital", "par_value"):
            event[field] = _integer(row.get(field), "자본금·액면가", optional=True)
        if row.get("issue_price") not in (None, ""):
            event["issue_price"] = _integer(row["issue_price"], "실제 1주당 납입액")
        else:
            event["issue_price"] = None
        event["kind"] = _kind(row.get("kind"))
        event["original_kind"] = str(row.get("kind") or "unknown")
        result.append(event)
    result.sort(key=lambda row: row["effective_date"])
    for previous, current in zip(result, result[1:]):
        if previous["after_shares"] != current["before_shares"]:
            raise ValueError("연속한 자본변동의 이전 주식수와 이후 주식수가 일치하지 않습니다.")
        if (previous.get("after_capital") is not None and current.get("before_capital") is not None
                and previous["after_capital"] != current["before_capital"]):
            raise ValueError("연속한 자본변동의 이전 자본금과 이후 자본금이 일치하지 않습니다.")
    return result


def _periods(values: Any, valuation: date) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(values, list) or not values or any(not isinstance(row, dict) for row in values):
        raise ValueError("평가에 사용할 사업연도와 실제 시작일·종료일이 필요합니다.")
    periods, blockers = [], []
    for row in values:
        start = _day(row.get("period_start"), "사업연도 시작일")
        end = _day(row.get("period_end"), "사업연도 종료일")
        year = _integer(row.get("year"), "사업연도")
        if end < start or end >= valuation:
            raise ValueError("사업연도 종료일은 시작일 이후이며 평가기준일 전이어야 합니다.")
        if year != end.year:
            raise ValueError("사업연도 표시는 실제 결산 종료일의 연도와 일치해야 합니다.")
        if _next_year(start) != end + timedelta(days=1):
            blockers.append("1년 미만·초과 사업연도는 연환산과 자본변동을 전문가가 별도 확인해야 합니다.")
        periods.append({"year": year, "period_start": start, "period_end": end})
    periods.sort(key=lambda row: row["period_start"])
    if len({row["year"] for row in periods}) != len(periods):
        raise ValueError("동일한 사업연도가 중복되었습니다.")
    if len(periods) != 3:
        blockers.append("직전 3개 사업연도가 모두 확인되지 않아 자동 환산을 확정하지 않습니다.")
    for previous, current in zip(periods, periods[1:]):
        if previous["period_end"] + timedelta(days=1) != current["period_start"]:
            blockers.append("사업연도가 연속하지 않아 자본변동 자동 환산을 확정하지 않습니다.")
    current_start = periods[-1]["period_end"] + timedelta(days=1)
    next_start = _next_year(current_start)
    if next_start is None or valuation >= next_start:
        blockers.append("평가기준일 직전 사업연도 자료가 누락되었거나 결산기간 확인이 필요합니다.")
    return periods, blockers


def build_capital_share_plan(
    registry: dict[str, Any], valuation_date: Any, annual_periods: list[dict[str, Any]], *,
    events: list[dict[str, Any]] | None = None, history_confirmed: bool = False,
) -> dict[str, Any]:
    """Return reviewed share counts, or explicit blockers instead of assumptions.

    ``source_shares`` is the unadjusted year-end count. ``shares`` is either an
    exact integral statutory conversion or None when manual resolution is
    necessary. All flags are advisory inputs to the caller's final-calculation
    gate; ``can_calculate`` must be honored.
    """
    if history_confirmed is not True:
        raise ValueError("원본 등기와 실제 효력일·유무상 여부를 확인한 뒤 자본변동 이력을 확정해주세요.")
    signature = capital_source_signature(registry)
    valuation = _day(valuation_date, "평가기준일")
    periods, blockers = _periods(deepcopy(annual_periods), valuation)
    source_snapshots = _snapshots(registry)
    snapshots = [row for row in source_snapshots if row["shares"] is not None]
    partial = [row for row in source_snapshots if row["shares"] is None]
    known_capitals = {row["capital"] for row in snapshots if row["capital"] is not None}
    if any(row["capital"] is not None and row["capital"] not in known_capitals for row in partial):
        blockers.append("주식수가 없는 자본금 변동 이력이 있어 누락된 변동을 전문가가 확인해야 합니다.")
    if len({row["par_value"] for row in source_snapshots if row["par_value"] is not None}) > 1:
        blockers.append("액면가 변경 이력이 있어 분할·병합 등을 전문가가 확인해야 합니다.")
    supplied = events is not None
    raw_events = deepcopy(events if supplied else registry.get("capital_events", []))
    if not raw_events and not supplied:
        dated = [row for row in snapshots if row.get("effective_date") is not None]
        for before, after in zip(dated, dated[1:]):
            if before["shares"] != after["shares"] or before["capital"] != after["capital"]:
                raw_events.append({"before_shares": before["shares"], "after_shares": after["shares"],
                                   "before_capital": before["capital"], "after_capital": after["capital"],
                                   "par_value": after["par_value"], "effective_date": after["effective_date"],
                                   "kind": "unknown", "source": "registry", "review_required": True})
    changes = _events(raw_events or [])
    dated_snapshots = [row for row in snapshots if row["effective_date"] is not None]
    if supplied and not changes and any(
        before["shares"] != after["shares"] or before["capital"] != after["capital"]
        for before, after in zip(dated_snapshots, dated_snapshots[1:])
    ):
        blockers.append("원본 등기의 자본변동이 확정 이력에서 누락되어 최종 계산을 차단합니다.")
    if changes:
        def state(when: date) -> tuple[int | None, int | None]:
            shares, capital = changes[0]["before_shares"], changes[0].get("before_capital")
            for event in changes:
                if event["effective_date"] <= when:
                    shares, capital = event["after_shares"], event.get("after_capital")
            return shares, capital
    else:
        def state(when: date) -> tuple[int | None, int | None]:
            known = [row for row in snapshots if row["effective_date"] is not None and row["effective_date"] <= when]
            return (known[-1]["shares"], known[-1]["capital"]) if known else (None, None)
    # Explicit reviewed events can replace ambiguous OCR dates, but may not
    # silently contradict a clear dated original snapshot.
    for snapshot in snapshots:
        if snapshot["effective_date"] is None or snapshot.get("review_required"):
            continue
        shares, capital = state(snapshot["effective_date"])
        if shares != snapshot["shares"] or (capital is not None and snapshot["capital"] is not None and capital != snapshot["capital"]):
            raise ValueError("확정한 자본변동 이력이 원본 등기의 주식수·자본금과 일치하지 않습니다.")
    if not supplied and any(row.get("review_required") or row["effective_date"] is None for row in snapshots):
        blockers.append("원본 등기 이력의 미확정 효력일·주식수는 수정·확정이 필요합니다.")
    current_shares, current_capital = state(valuation)
    if current_shares is None or current_capital is None:
        blockers.append("평가기준일 현재 주식수·자본금의 확정 근거가 부족합니다.")
    oldest_start, latest_end = periods[0]["period_start"], periods[-1]["period_end"]
    relevant = [event for event in changes if oldest_start <= event["effective_date"] <= valuation]
    historical = [event for event in relevant if event["effective_date"] <= latest_end]
    current = [event for event in relevant if event["effective_date"] > latest_end]
    if len(relevant) > 1:
        blockers.append("복수 증감자·복합 자본변동은 전문가가 환산과 순손익 조정을 확정해야 합니다.")
    for event in relevant:
        if event.get("review_required") and not supplied:
            blockers.append("미확정 자본변동은 실제 효력일·유무상 여부를 확인해야 합니다.")
        if event["kind"] in {"unknown", "unsupported"}:
            blockers.append("자본변동 종류가 미확인 또는 자동 지원 범위 밖이므로 최종 계산을 차단합니다.")
        original_kind = str(event.get("original_kind", "")).lower()
        if ((original_kind.endswith("_increase") or original_kind.endswith("증자"))
                and event["after_shares"] <= event["before_shares"]) or (
            (original_kind.endswith("_decrease") or original_kind.endswith("감자"))
                and event["after_shares"] >= event["before_shares"]
        ):
            blockers.append("선택한 증감자 종류와 실제 주식수 증가·감소 방향이 일치하지 않습니다.")
        if event["before_shares"] == event["after_shares"]:
            blockers.append("주식수 변동 없는 자본변동·액면변경은 전문가 확인이 필요합니다.")
        par = event.get("par_value")
        if par is not None and any(event.get(side + "_capital") is not None
                                   and event[side + "_capital"] != event[side + "_shares"] * par
                                   for side in ("before", "after")):
            blockers.append("액면가·주식수·등기 자본금이 일치하지 않아 액면변경 등 별도 검토가 필요합니다.")
    if any(event["kind"] != "paid" or event["after_shares"] <= event["before_shares"] for event in current):
        blockers.append("당해 사업연도의 무상·미확인 증감자 등은 별도 법령 검토가 필요해 자동 확정하지 않습니다.")
    annual_rows = []
    for period in periods:
        source_shares, _ = state(period["period_end"])
        amount = Fraction(source_shares, 1) if source_shares is not None else None
        income = any(event["kind"] in {"paid", "unknown", "unsupported"}
                     and period["period_start"] <= event["effective_date"] for event in historical)
        if source_shares is None:
            blockers.append("과거 결산일 주식수의 확정 근거가 부족합니다.")
        if len(relevant) == 1 and len(historical) == 1 and historical[0]["kind"] in {"paid", "bonus"}:
            event = historical[0]
            if period["period_end"] < event["effective_date"] and amount is not None:
                # With exactly one event, its before count is also the prior
                # fiscal year-end denominator. No chained-event assumptions.
                amount *= Fraction(event["after_shares"], event["before_shares"])
        integral = amount is not None and amount.denominator == 1
        if amount is not None and not integral:
            blockers.append("환산주식수가 소수이므로 반올림하지 않고 법정 처리방법을 전문가가 확정해야 합니다.")
        annual_rows.append({"year": period["year"], "period_start": period["period_start"].isoformat(),
                            "period_end": period["period_end"].isoformat(), "source_shares": source_shares,
                            "shares": int(amount) if integral else None,
                            "exact_shares": {"numerator": amount.numerator, "denominator": amount.denominator} if amount is not None else None,
                            "requires_income_adjustment": income})
    income_required = any(row["requires_income_adjustment"] for row in annual_rows)
    if income_required:
        blockers.append("과거 유상 증감자 등의 순손익 가산·차감 및 월할은 자동 계산하지 않습니다. 전문가 최종 입력이 필요합니다.")
    blockers = list(dict.fromkeys(blockers))
    warnings = list(blockers)
    if current and all(event["kind"] == "paid" and event["after_shares"] > event["before_shares"] for event in current):
        warnings.append("당해 사업연도 유상증자는 현재 주식수에만 반영합니다. 과거 결산일 주식수·순손익을 자동 소급 변경하지 않습니다.")
    warnings.append("자본금·액면가로 실제 납입액을 추정하지 않으며 저가증자 증여세는 계산하지 않습니다.")
    normalized_events = [{**deepcopy(event), "effective_date": event["effective_date"].isoformat()} for event in changes]
    for row in annual_rows:
        row["unsupported"] = bool(blockers)
        row["requires_expert_confirmation"] = bool(blockers)
    return {"source_signature": signature, "valuation_date": valuation.isoformat(),
            "history_confirmed": True, "current_shares": current_shares, "current_capital": current_capital,
            "annual_rows": annual_rows, "events": normalized_events, "warnings": warnings,
            "blocking_reasons": blockers, "unsupported": bool(blockers), "can_calculate": not blockers,
            "requires_expert_confirmation": bool(blockers), "requires_income_adjustment": income_required,
            "law_references": list(LAW_REFERENCES)}
