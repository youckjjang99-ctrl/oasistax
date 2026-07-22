from __future__ import annotations

import math
import re
from typing import Any

from comprehensive_financial_diagnosis import build_comprehensive_financial_diagnosis


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "nan", "nat", "-"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    raw = _clean(value).replace(",", "")
    if not raw:
        return None
    multiplier = 1.0
    if "억원" in raw or raw.endswith("억"):
        multiplier = 100_000_000.0
    elif "백만원" in raw:
        multiplier = 1_000_000.0
    elif "천만원" in raw:
        multiplier = 10_000_000.0
    elif "만원" in raw:
        multiplier = 10_000.0
    elif "천원" in raw:
        multiplier = 1_000.0
    negative = raw.startswith("(") and raw.endswith(")")
    numeric = re.sub(r"[^0-9.+-]", "", raw)
    if not numeric:
        return None
    try:
        result = float(numeric) * multiplier
        return -abs(result) if negative else result
    except ValueError:
        return None


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        try:
            value = value.to_dict()
        except Exception:
            pass
    output: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            output.update(_flatten(item, path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            path = f"{prefix}.{index}" if prefix else str(index)
            output.update(_flatten(item, path))
    else:
        output[prefix] = value
    return output


def _find_latest(flat: dict[str, Any], aliases: list[str]) -> float | None:
    found: list[tuple[int, float]] = []
    for key, raw in flat.items():
        normalized = re.sub(r"[^0-9A-Za-z가-힣]", "", str(key)).lower()
        if not any(
            re.sub(r"[^0-9A-Za-z가-힣]", "", alias).lower() in normalized
            for alias in aliases
        ):
            continue
        value = _number(raw)
        if value is None:
            continue
        years = re.findall(r"(20\d{2})", str(key))
        found.append((int(years[-1]) if years else 9999, value))
    if not found:
        return None
    found.sort(key=lambda item: item[0], reverse=True)
    return found[0][1]


def _positive_matches(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    result: list[str] = []
    for keyword in keywords:
        token = keyword.lower()
        if token not in lowered:
            continue
        negative_patterns = [
            rf"{re.escape(token)}\s*(없음|없다|미보유|해당없음|계획없음|안함|하지 않음)",
            rf"(없음|없다|미보유|해당없음|계획없음)\s*{re.escape(token)}",
        ]
        if not any(re.search(pattern, lowered) for pattern in negative_patterns):
            result.append(keyword)
    return list(dict.fromkeys(result))


def _clip(value: float) -> int:
    return max(0, min(int(round(value)), 100))


def _impact_score(amount: float | None, denominator: float | None) -> int:
    if amount is None or amount <= 0:
        return 0
    if amount >= 1_000_000_000:
        absolute = 30
    elif amount >= 300_000_000:
        absolute = 24
    elif amount >= 100_000_000:
        absolute = 18
    elif amount >= 30_000_000:
        absolute = 12
    elif amount >= 10_000_000:
        absolute = 7
    else:
        absolute = 3

    relative = 0
    if denominator not in (None, 0):
        ratio = amount / abs(denominator) * 100
        if ratio >= 20:
            relative = 28
        elif ratio >= 10:
            relative = 22
        elif ratio >= 5:
            relative = 16
        elif ratio >= 2:
            relative = 9
        elif ratio >= 1:
            relative = 5
    return min(45, absolute + relative)


def _scale_score(
    sales: float | None,
    assets: float | None,
    employees: float | None,
) -> int:
    score = 0
    if sales is not None:
        score += (
            35 if sales >= 30_000_000_000 else
            30 if sales >= 10_000_000_000 else
            24 if sales >= 3_000_000_000 else
            18 if sales >= 1_000_000_000 else
            11 if sales >= 300_000_000 else
            4 if sales > 0 else 0
        )
    if assets is not None:
        score += (
            30 if assets >= 30_000_000_000 else
            24 if assets >= 10_000_000_000 else
            18 if assets >= 3_000_000_000 else
            12 if assets >= 1_000_000_000 else
            5 if assets > 0 else 0
        )
    if employees is not None:
        score += (
            20 if employees >= 50 else
            15 if employees >= 20 else
            10 if employees >= 10 else
            6 if employees >= 5 else
            2 if employees > 0 else 0
        )
    return min(score, 85)


def _stage(
    sales: float | None,
    operating_profit: float | None,
    net_income: float | None,
    retained: float | None,
    succession_signal: bool,
) -> tuple[str, str]:
    if succession_signal and (retained or 0) >= 500_000_000:
        return "승계 준비기", "승계 의도와 누적 이익 신호가 함께 확인됨"
    if sales in (None, 0) or sales < 300_000_000:
        if (operating_profit or 0) < 0 or (net_income or 0) < 0:
            return "재정비기", "매출 규모가 작고 손익 개선이 우선됨"
        return "초기·소규모 운영기", "현재 매출 규모가 작아 매출·현금흐름 점검이 우선됨"
    if sales < 3_000_000_000:
        return "성장 기반기", "매출 확대와 운전자금·수익성 관리가 우선됨"
    if (retained or 0) >= 1_000_000_000:
        return "성숙·자본관리기", "영업규모와 누적 이익을 함께 관리할 단계임"
    return "성장기", "투자·인력·자금조달과 수익성 관리가 중요한 단계임"


def _result(
    topic: str,
    score: float,
    confidence: int,
    evidence: list[str],
    penalties: list[str],
    matched: list[str],
    questions: list[str],
    documents: list[str],
    components: dict[str, int],
) -> dict[str, Any]:
    final = _clip(score)
    status = (
        "우선 상담" if final >= 75 else
        "검토 권장" if final >= 55 else
        "추가 확인" if final >= 35 else
        "우선순위 낮음"
    )
    return {
        "topic": topic,
        "score": final,
        "confidence": _clip(confidence),
        "status": status,
        "evidence": list(dict.fromkeys(evidence)),
        "penalties": list(dict.fromkeys(penalties)),
        "matched": list(dict.fromkeys(matched)),
        "questions": questions,
        "documents": documents,
        "components": components,
    }


def build_priority_recommendations(
    customer: Any,
    preferences: dict[str, Any],
    memory: dict[str, Any],
    integrated_context: dict[str, Any] | None,
    topic_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    context = integrated_context if isinstance(integrated_context, dict) else {}
    financial = context.get("financial", {})
    if not isinstance(financial, dict):
        financial = {}

    flat = {}
    flat.update(_flatten(customer, "customer"))
    flat.update(_flatten(financial, "financial"))

    customer_values = getattr(customer, "values", [])
    text = " ".join([
        " ".join(_clean(value) for value in customer_values),
        " ".join(_clean(value) for value in preferences.values()),
        " ".join(_clean(value) for value in memory.values()),
        _clean(context.get("combined_text", "")),
    ])

    sales = _find_latest(flat, ["매출액", "연매출", "전년도매출", "영업수익"])
    assets = _find_latest(flat, ["자산총계", "총자산"])
    operating_profit = _find_latest(flat, ["영업이익", "영업손익"])
    net_income = _find_latest(flat, ["당기순이익", "당기순손익"])
    equity = _find_latest(flat, ["자본총계", "순자산"])
    retained = _find_latest(
        flat,
        ["미처분이익잉여금", "이익잉여금", "이익잉여금합계"],
    )
    employees = _find_latest(flat, ["종업원수", "상시근로자수", "직원수"])

    diagnosis = build_comprehensive_financial_diagnosis(customer, financial)
    finding_map = {
        item.get("id"): item
        for item in diagnosis.get("findings", [])
        if isinstance(item, dict)
    }
    suspicious = finding_map.get("suspicious_advances", {})
    suspicious_amount = sum(
        max(0.0, _number(hit.get("value")) or 0.0)
        for hit in (suspicious.get("account_hits", []) or [])
        if isinstance(hit, dict)
    )

    latest_stock = context.get("latest_stock", {})
    if not isinstance(latest_stock, dict):
        latest_stock = {}
    stock_result = latest_stock.get("result", {})
    if not isinstance(stock_result, dict):
        stock_result = {}
    stock_value = _number(stock_result.get("total_equity_value"))
    has_stock = bool(stock_value and stock_value > 0)

    succession_signals = _positive_matches(
        text,
        ["가업승계", "후계자", "자녀승계", "승계계획", "상속세", "증여계획"],
    )
    stage, stage_reason = _stage(
        sales,
        operating_profit,
        net_income,
        retained,
        bool(succession_signals),
    )
    scale = _scale_score(sales, assets, employees)
    small_sales = sales is None or sales < 300_000_000
    low_activity = small_sales and (operating_profit or 0) <= 0
    rule_map = {rule["topic"]: rule for rule in topic_rules}
    results: list[dict[str, Any]] = []

    rule = rule_map["정책자금"]
    matched = _positive_matches(text, rule["keywords"])
    explicit = _positive_matches(
        text,
        ["자금부족", "운전자금 필요", "시설투자 계획", "기계구입", "증설", "대출 필요"],
    )
    score = 18 + min(25, len(matched) * 5)
    evidence: list[str] = []
    penalties: list[str] = []
    if explicit:
        score += 25
        evidence.append("구체적인 자금수요 신호가 확인됨")
    if low_activity:
        score += 10
        evidence.append("매출·손익이 낮아 자금 및 사업 정상화 검토가 필요함")
    if not explicit:
        score = min(score, 58)
        penalties.append("필요금액·자금용도·투자일정이 없어 점수를 제한함")
    results.append(_result(
        "정책자금", score, 70 if explicit else 42,
        evidence, penalties, matched, rule["questions"], rule["documents"],
        {"자금수요": 25 if explicit else 0, "기업상황": 10 if low_activity else 0},
    ))

    rule = rule_map["가지급금 정리"]
    matched = _positive_matches(text, rule["keywords"])
    amount_score = _impact_score(suspicious_amount, assets or sales)
    score = 8 + amount_score + min(18, len(matched) * 5)
    evidence, penalties = [], []
    if suspicious_amount > 0:
        evidence.append(f"잠재 관련계정 합계 약 {suspicious_amount:,.0f}원 탐지")
    if matched:
        evidence.append("가지급금 관련 상담·등록 표현이 확인됨")
    if not suspicious_amount:
        score = min(score, 32)
        penalties.append("가지급금·대여금·선급금·미수금의 금액근거가 없음")
    if small_sales and suspicious_amount < 30_000_000:
        score = min(score, 25)
        penalties.append("매출과 관련계정 규모가 작아 현재 우선순위를 낮춤")
    results.append(_result(
        "가지급금 정리", score,
        suspicious.get("confidence", 35) if suspicious else 25,
        evidence, penalties, matched, rule["questions"], rule["documents"],
        {"금액영향": amount_score, "상담신호": min(18, len(matched) * 5)},
    ))

    rule = rule_map["이익소각·자기주식"]
    matched = _positive_matches(text, rule["keywords"])
    retained_score = _impact_score(retained, equity or assets)
    score = 5 + retained_score + (12 if has_stock else 0) + min(15, len(matched) * 4)
    evidence, penalties = [], []
    if retained and retained > 0:
        evidence.append(f"이익잉여금 약 {retained:,.0f}원 확인")
    if has_stock:
        evidence.append(f"저장 주가평가 전체가치 약 {stock_value:,.0f}원 확인")
    if not retained or retained < 100_000_000:
        score = min(score, 24)
        penalties.append("충분한 배당가능이익 근거가 없어 점수를 제한함")
    if not has_stock:
        score = min(score, 38)
        penalties.append("주가평가가 없어 실행가능성 판단이 어려움")
    if small_sales:
        score -= 12
        penalties.append("매출·현금흐름 정상화보다 뒤 순서로 조정함")
    results.append(_result(
        "이익소각·자기주식", score, 72 if retained and has_stock else 30,
        evidence, penalties, matched, rule["questions"], rule["documents"],
        {"이익잉여금영향": retained_score, "주가자료": 12 if has_stock else 0},
    ))

    rule = rule_map["가업승계"]
    matched = _positive_matches(text, rule["keywords"])
    explicit = bool(succession_signals)
    score = 4 + (38 if explicit else 0) + (14 if has_stock else 0)
    if retained and retained >= 500_000_000:
        score += 15
    evidence, penalties = [], []
    if explicit:
        evidence.append("후계자·상속·증여 등 명시적인 승계의도가 확인됨")
    if has_stock:
        evidence.append("주가평가 자료가 연결됨")
    if retained and retained >= 500_000_000:
        evidence.append("누적 이익이 승계가치에 영향을 줄 가능성이 있음")
    if not explicit:
        score = min(score, 22)
        penalties.append("대표 연령·후계자·승계시기 등 승계의도가 확인되지 않음")
    if small_sales and not explicit:
        score = min(score, 12)
        penalties.append("승계보다 사업성과·현금흐름 점검을 우선함")
    results.append(_result(
        "가업승계", score, 78 if explicit and has_stock else 22,
        evidence, penalties, matched, rule["questions"], rule["documents"],
        {"승계의도": 38 if explicit else 0, "주가자료": 14 if has_stock else 0},
    ))

    rule = rule_map["정관개정"]
    matched = _positive_matches(text, rule["keywords"])
    defects = _positive_matches(
        text,
        ["정관 미비", "퇴직금 규정 미비", "유족보상금 미비", "개정 필요", "정관 오래됨"],
    )
    score = 8 + min(25, len(matched) * 4) + (30 if defects else 0)
    evidence, penalties = [], []
    if defects:
        evidence.append("구체적인 정관 미비사항 또는 개정 필요성이 확인됨")
    elif matched:
        evidence.append("정관 관련 표현은 있으나 구체적인 미비사항은 확인되지 않음")
    if not defects:
        score = min(score, 42)
        penalties.append("실제 조문·개정일·미비사항 근거가 없어 점수를 제한함")
    if small_sales and not defects:
        score -= 8
        penalties.append("저매출 기업에서는 영업·현금흐름을 먼저 점검함")
    results.append(_result(
        "정관개정", score, 75 if defects else 30,
        evidence, penalties, matched, rule["questions"], rule["documents"],
        {"구체적미비": 30 if defects else 0, "관련표현": min(25, len(matched) * 4)},
    ))

    rule = rule_map["법인보험·퇴직재원"]
    matched = _positive_matches(text, rule["keywords"])
    explicit = _positive_matches(
        text,
        ["퇴직계획", "은퇴계획", "유고재원", "보장부족"],
    )
    score = 6 + min(20, len(matched) * 4) + (25 if explicit else 0)
    evidence, penalties = [], []
    if explicit:
        evidence.append("퇴직·유고재원 관련 명시적 니즈가 확인됨")
    else:
        score = min(score, 32)
        penalties.append("대표 보수·퇴직시기·필요재원 근거가 없음")
    results.append(_result(
        "법인보험·퇴직재원", score, 65 if explicit else 25,
        evidence, penalties, matched, rule["questions"], rule["documents"],
        {"명시적니즈": 25 if explicit else 0},
    ))

    rule = rule_map["세액공제·경정청구"]
    matched = _positive_matches(text, rule["keywords"])
    tax_signals = _positive_matches(
        text,
        ["법인세", "세액공제", "시설투자", "고용증가", "연구개발비", "경정청구"],
    )
    score = 12 + min(30, len(matched) * 5) + (15 if tax_signals else 0)
    evidence, penalties = [], []
    if tax_signals:
        evidence.append("세금·고용·투자 관련 검토신호가 확인됨")
    if (operating_profit or 0) <= 0 and (net_income or 0) <= 0:
        score -= 10
        penalties.append("현재 이익이 낮아 즉시 절세보다 향후 적용 검토가 적합함")
    results.append(_result(
        "세액공제·경정청구", score, 58 if tax_signals else 35,
        evidence, penalties, matched, rule["questions"], rule["documents"],
        {"세무신호": 15 if tax_signals else 0, "키워드근거": min(30, len(matched) * 5)},
    ))

    results.sort(key=lambda item: (item["score"], item["confidence"]), reverse=True)

    if low_activity:
        results.insert(0, {
            "topic": "매출·현금흐름 정상화",
            "score": 88,
            "confidence": 82,
            "status": "최우선 상담",
            "evidence": [
                "확인 매출규모가 매우 작거나 손익이 낮아 영업구조 점검이 우선됨",
                stage_reason,
            ],
            "penalties": [],
            "matched": ["매출", "현금흐름"],
            "questions": [
                "현재 실제 영업이 진행 중이며 올해 예상매출은 얼마입니까?",
                "주요 매출처와 수주·계약 파이프라인은 어떻게 됩니까?",
                "월 고정비와 향후 6개월 자금부족 예상액은 얼마입니까?",
                "휴업·사업전환·신규사업 계획이 있습니까?",
            ],
            "documents": [
                "최근 12개월 월별 매출자료",
                "부가세 신고자료",
                "통장 입출금내역",
                "수주·계약서",
                "월별 고정비 및 자금수지표",
            ],
            "components": {"저매출상태": 50, "손익상태": 38},
        })

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in results:
        if item["topic"] in seen:
            continue
        seen.add(item["topic"])
        unique.append(item)

    return {
        "stage": stage,
        "stage_reason": stage_reason,
        "scale_score": scale,
        "recommendations": unique,
        "method": (
            "기업규모·금액영향·재무비율·성장단계·상담의도·"
            "자료충족도·실행가능성을 함께 반영"
        ),
    }
