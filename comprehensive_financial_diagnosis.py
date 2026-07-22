from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any


ACCOUNT_GROUPS: dict[str, list[str]] = {
    "sales": ["매출액", "수익", "영업수익", "연매출"],
    "assets": ["자산총계", "총자산"],
    "current_assets": ["유동자산"],
    "cash": ["현금및현금성자산", "현금성자산", "현금", "보통예금", "당좌예금"],
    "receivables": ["매출채권", "외상매출금", "받을어음", "미수수익"],
    "inventory": ["재고자산", "상품", "제품", "원재료", "재공품", "저장품"],
    "suspicious_advances": [
        "가지급금",
        "단기대여금",
        "장기대여금",
        "주주임원종업원단기대여금",
        "주주임원종업원장기대여금",
        "임원종업원대여금",
        "선급금",
        "선급비용",
        "미수금",
        "기타미수금",
        "기타채권",
        "전도금",
        "보증금",
        "장기선급금",
        "관계회사대여금",
        "특수관계자대여금",
    ],
    "temporary_receipts": [
        "가수금",
        "주주임원종업원단기차입금",
        "임원차입금",
        "대표자차입금",
        "관계회사차입금",
        "특수관계자차입금",
    ],
    "retained_earnings": [
        "미처분이익잉여금",
        "이익잉여금",
        "이익잉여금합계",
        "미처리결손금",
    ],
    "liabilities": ["부채총계", "총부채"],
    "current_liabilities": ["유동부채"],
    "borrowings": [
        "단기차입금",
        "장기차입금",
        "유동성장기부채",
        "사채",
        "차입금",
    ],
    "equity": ["자본총계", "순자산"],
    "operating_profit": ["영업이익", "영업손익"],
    "net_income": ["당기순이익", "당기순손익"],
    "operating_cashflow": [
        "영업활동현금흐름",
        "영업활동으로인한현금흐름",
        "영업현금흐름",
    ],
    "fixed_assets": [
        "유형자산",
        "토지",
        "건물",
        "기계장치",
        "차량운반구",
        "시설장치",
    ],
    "depreciation": ["감가상각비", "감가상각누계액"],
}


QUESTIONS = {
    "suspicious_advances": [
        "선급금·미수금·대여금의 거래 상대방과 발생 원인은 무엇입니까?",
        "해당 잔액과 관련된 계약서, 세금계산서, 납품일정이 존재합니까?",
        "대표자나 특수관계인이 사용한 법인자금이 포함되어 있습니까?",
        "전기부터 동일하거나 비슷한 잔액이 장기간 유지되고 있습니까?",
        "과거 가수금과 상계하거나 다른 계정으로 대체한 내역이 있습니까?",
    ],
    "temporary_receipts": [
        "가수금의 실제 입금자와 자금 출처를 구분할 수 있습니까?",
        "대표자별 가수금 잔액과 입금일이 계정별원장에 표시되어 있습니까?",
        "향후 상환, 출자전환 또는 자본금 증자 계획이 있습니까?",
        "대표자 사망·승계 시 채권으로 인정될 증빙이 준비되어 있습니까?",
    ],
    "retained_earnings": [
        "이익잉여금이 누적됐지만 실제 현금은 어디에 사용되어 있습니까?",
        "향후 배당, 임원퇴직금, 설비투자 또는 승계 계획이 있습니까?",
        "대표자와 주주의 예상 은퇴·승계 시점은 언제입니까?",
        "현재 주식가치 상승이 상속·증여 부담에 미치는 영향을 검토했습니까?",
    ],
    "receivables": [
        "매출채권의 주요 거래처별 잔액과 회수 예정일은 언제입니까?",
        "6개월 또는 1년 이상 장기 미회수채권이 있습니까?",
        "특수관계인 또는 관계회사에 대한 채권이 포함되어 있습니까?",
        "대손충당금과 실제 부실 가능성을 비교했습니까?",
    ],
    "inventory": [
        "재고 중 1년 이상 판매되지 않은 장기재고가 있습니까?",
        "실물재고와 장부재고가 정기적으로 대조되고 있습니까?",
        "반품·불량·폐기 대상 재고의 평가손실이 반영되어 있습니까?",
        "매출 증가율보다 재고 증가율이 높은 이유는 무엇입니까?",
    ],
    "cashflow": [
        "당기순이익은 흑자인데 현금이 부족한 주요 원인은 무엇입니까?",
        "매출채권·재고·선급금 증가가 영업현금흐름을 압박하고 있습니까?",
        "운전자금 부족을 단기차입금이나 대표자 가수금으로 충당하고 있습니까?",
        "향후 6개월 자금수지표를 작성하고 있습니까?",
    ],
    "leverage": [
        "단기차입금과 1년 내 상환해야 할 원금 규모는 얼마입니까?",
        "금리 상승 시 이자비용 부담과 상환 가능성을 검토했습니까?",
        "대표자 개인 담보·연대보증이 제공되어 있습니까?",
        "장기 시설자금이 단기차입금으로 조달된 부분이 있습니까?",
    ],
}

DOCUMENTS = {
    "suspicious_advances": [
        "선급금·선급비용·미수금·대여금 계정별원장",
        "거래처별 잔액명세",
        "관련 계약서·세금계산서·지급증빙",
        "법인 통장 및 법인카드 거래내역",
        "대표자 가수금·대여금 원장",
    ],
    "temporary_receipts": [
        "가수금 계정별원장",
        "대표자별 입금내역",
        "금전소비대차계약서",
        "주주명부 및 법인등기",
        "자본금 증자·출자전환 검토자료",
    ],
    "retained_earnings": [
        "최근 3개년 재무제표",
        "이익잉여금처분계산서",
        "주주명부·법인등기",
        "정관·임원퇴직금·배당 규정",
        "최신 비상장주식 가치평가",
    ],
    "receivables": [
        "매출채권 연령분석표",
        "거래처별 원장",
        "세금계산서·계약서",
        "회수계획 및 대손처리 내역",
    ],
    "inventory": [
        "품목별 재고수불부",
        "재고 연령분석표",
        "실사보고서",
        "폐기·평가손실 자료",
    ],
    "cashflow": [
        "현금흐름표",
        "월별 자금수지표",
        "매출채권·재고·매입채무 증감표",
        "차입금 상환일정",
    ],
    "leverage": [
        "금융기관별 대출현황",
        "원리금 상환계획",
        "담보·보증 내역",
        "금리 조건 및 약정서",
    ],
}

DIRECTIONS = {
    "suspicious_advances": [
        "업무 관련성과 상대방을 먼저 확인한 뒤 실제 가지급금 여부를 확정합니다.",
        "확정 전에는 급여·상여·배당·퇴직금·상계 등 특정 정리수단을 먼저 권하지 않습니다.",
        "장기 미회수 또는 특수관계인 사용분이면 인정이자·상여처분·업무무관자산 위험을 검토합니다.",
    ],
    "temporary_receipts": [
        "채권자와 자금출처를 확정하고 상환·출자전환·증자 가능성을 비교합니다.",
        "대표자 채권이 상속재산과 주식가치에 미치는 영향을 함께 검토합니다.",
    ],
    "retained_earnings": [
        "현금 보유 여부와 자산 구성부터 확인하고 배당·퇴직·투자·승계 계획을 통합 설계합니다.",
        "정관, 주가평가, 주주구조를 함께 확인해 실행순서를 결정합니다.",
    ],
    "receivables": [
        "장기 미회수채권을 정상·지연·부실로 구분하고 회수계획을 수립합니다.",
        "운전자금 수요와 대손세무처리를 함께 검토합니다.",
    ],
    "inventory": [
        "실물재고와 장부재고를 대조하고 장기·불량재고의 평가 적정성을 검토합니다.",
        "재고회전 개선과 운전자금 조달계획을 함께 수립합니다.",
    ],
    "cashflow": [
        "손익이 아닌 월별 현금 유입·유출을 기준으로 자금부족 원인을 분해합니다.",
        "채권회수·재고감축·차입구조 조정과 운전자금 조달을 함께 검토합니다.",
    ],
    "leverage": [
        "단기·장기 자금용도를 맞추고 만기집중과 금리위험을 줄이는 구조를 검토합니다.",
        "정책자금·보증은 진단 후 실행수단 중 하나로만 비교합니다.",
    ],
}


def _clean_key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", str(value or "")).lower()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat", "-"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.replace(",", "").replace("원", "").replace("%", "")
    text = re.sub(r"[^0-9.+-]", "", text)
    if not text:
        return None
    try:
        result = float(text)
        return -abs(result) if negative else result
    except ValueError:
        return None


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    if hasattr(value, "to_dict"):
        try:
            value = value.to_dict()
        except Exception:
            pass
    if isinstance(value, dict):
        for key, item in value.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(_flatten(item, next_prefix))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            next_prefix = f"{prefix}.{index}" if prefix else str(index)
            flattened.update(_flatten(item, next_prefix))
    else:
        flattened[prefix] = value
    return flattened


def _year_from_key(key: str) -> int | None:
    matches = re.findall(r"(20\d{2})", key)
    return int(matches[-1]) if matches else None


def _match_alias(key: str, aliases: list[str]) -> bool:
    normalized = _clean_key(key)
    return any(_clean_key(alias) in normalized for alias in aliases)


def _collect_series(
    flat: dict[str, Any],
    aliases: list[str],
) -> tuple[dict[int, float], list[dict[str, Any]]]:
    by_year: dict[int, float] = defaultdict(float)
    hits: list[dict[str, Any]] = []
    undated: list[float] = []

    for key, raw in flat.items():
        if not _match_alias(key, aliases):
            continue
        value = _number(raw)
        if value is None:
            continue
        year = _year_from_key(key)
        hits.append({"account": key, "year": year, "value": value})
        if year is None:
            undated.append(value)
        else:
            by_year[year] += value

    if not by_year and undated:
        by_year[9999] = sum(undated)
    return dict(by_year), hits


def _latest(series: dict[int, float]) -> tuple[int | None, float | None]:
    if not series:
        return None, None
    year = sorted(series)[-1]
    return year, series[year]


def _previous(series: dict[int, float]) -> tuple[int | None, float | None]:
    if len(series) < 2:
        return None, None
    year = sorted(series)[-2]
    return year, series[year]


def _format_amount(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{int(round(value)):,}원"


def _ratio(value: float | None, denominator: float | None) -> float | None:
    if value is None or denominator in (None, 0):
        return None
    return value / denominator * 100


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / abs(previous) * 100


def _finding(
    *,
    finding_id: str,
    title: str,
    category: str,
    importance: int,
    confidence: int,
    facts: list[str],
    account_hits: list[dict[str, Any]],
) -> dict[str, Any]:
    importance = max(0, min(int(importance), 100))
    confidence = max(0, min(int(confidence), 100))
    priority = round(importance * 0.65 + confidence * 0.35)
    status = (
        "최우선 확인"
        if priority >= 80
        else "우선 검토"
        if priority >= 65
        else "추가 확인"
        if priority >= 45
        else "관찰"
    )
    return {
        "id": finding_id,
        "title": title,
        "category": category,
        "importance": importance,
        "confidence": confidence,
        "priority": priority,
        "status": status,
        "facts": facts,
        "questions": QUESTIONS.get(finding_id, []),
        "documents": DOCUMENTS.get(finding_id, []),
        "directions": DIRECTIONS.get(finding_id, []),
        "account_hits": account_hits[:30],
    }


def build_comprehensive_financial_diagnosis(
    customer: Any,
    financial: dict[str, Any],
) -> dict[str, Any]:
    flat = {}
    flat.update(_flatten(customer, "customer"))
    flat.update(_flatten(financial, "financial"))

    series: dict[str, dict[int, float]] = {}
    hits: dict[str, list[dict[str, Any]]] = {}
    for group, aliases in ACCOUNT_GROUPS.items():
        series[group], hits[group] = _collect_series(flat, aliases)

    latest_values: dict[str, float | None] = {}
    latest_years: dict[str, int | None] = {}
    previous_values: dict[str, float | None] = {}
    for group, values in series.items():
        latest_years[group], latest_values[group] = _latest(values)
        _, previous_values[group] = _previous(values)

    assets = latest_values.get("assets")
    sales = latest_values.get("sales")
    current_assets = latest_values.get("current_assets")
    current_liabilities = latest_values.get("current_liabilities")
    equity = latest_values.get("equity")

    findings: list[dict[str, Any]] = []

    suspicious = latest_values.get("suspicious_advances")
    suspicious_ratio = _ratio(suspicious, assets)
    suspicious_growth = _growth(
        suspicious,
        previous_values.get("suspicious_advances"),
    )
    if suspicious is not None and suspicious > 0:
        importance = 48
        if suspicious_ratio is not None:
            importance += min(32, int(suspicious_ratio * 1.8))
        if suspicious_growth is not None and suspicious_growth > 20:
            importance += 12
        direct_advance = any(
            "가지급금" in str(hit.get("account", ""))
            or "대여금" in str(hit.get("account", ""))
            for hit in hits["suspicious_advances"]
        )
        confidence = 42 + min(30, len(hits["suspicious_advances"]) * 6)
        if direct_advance:
            confidence += 20
        facts = [
            f"잠재 업무무관채권 계정 합계는 {_format_amount(suspicious)}입니다.",
        ]
        if suspicious_ratio is not None:
            facts.append(f"자산총계 대비 약 {suspicious_ratio:.1f}%입니다.")
        if suspicious_growth is not None:
            facts.append(f"전기 대비 약 {suspicious_growth:+.1f}% 변동했습니다.")
        facts.append(
            "가지급금뿐 아니라 선급금·선급비용·미수금·대여금·기타채권을 함께 묶어 탐지했습니다."
        )
        findings.append(
            _finding(
                finding_id="suspicious_advances",
                title="가지급금·업무무관채권 가능성",
                category="세무·대표자거래",
                importance=importance,
                confidence=confidence,
                facts=facts,
                account_hits=hits["suspicious_advances"],
            )
        )

    temporary = latest_values.get("temporary_receipts")
    temporary_ratio = _ratio(temporary, assets)
    if temporary is not None and temporary > 0:
        importance = 42 + min(38, int((temporary_ratio or 0) * 1.5))
        confidence = 55 + min(35, len(hits["temporary_receipts"]) * 8)
        facts = [f"대표자·특수관계인 차입성 계정은 {_format_amount(temporary)}입니다."]
        if temporary_ratio is not None:
            facts.append(f"자산총계 대비 약 {temporary_ratio:.1f}%입니다.")
        findings.append(
            _finding(
                finding_id="temporary_receipts",
                title="대표자 가수금·임시자금 의존",
                category="자본·대표자거래",
                importance=importance,
                confidence=confidence,
                facts=facts,
                account_hits=hits["temporary_receipts"],
            )
        )

    retained = latest_values.get("retained_earnings")
    retained_ratio = _ratio(retained, equity)
    if retained is not None and retained > 0:
        importance = 40 + min(40, int((retained_ratio or 0) * 0.35))
        confidence = 58 + min(30, len(hits["retained_earnings"]) * 8)
        facts = [f"이익잉여금 관련 잔액은 {_format_amount(retained)}입니다."]
        if retained_ratio is not None:
            facts.append(f"자본총계 대비 약 {retained_ratio:.1f}%입니다.")
        facts.append(
            "현금 보유 여부, 주가평가, 주주구조와 함께 봐야 배당·퇴직·승계 방향을 정할 수 있습니다."
        )
        findings.append(
            _finding(
                finding_id="retained_earnings",
                title="미처분이익잉여금·주식가치 부담",
                category="자본·승계",
                importance=importance,
                confidence=confidence,
                facts=facts,
                account_hits=hits["retained_earnings"],
            )
        )

    receivables = latest_values.get("receivables")
    receivable_ratio = _ratio(receivables, sales)
    receivable_growth = _growth(receivables, previous_values.get("receivables"))
    sales_growth = _growth(sales, previous_values.get("sales"))
    if receivables is not None and receivables > 0:
        importance = 35 + min(38, int((receivable_ratio or 0) * 0.8))
        if (
            receivable_growth is not None
            and sales_growth is not None
            and receivable_growth > sales_growth + 15
        ):
            importance += 15
        confidence = 55 + min(30, len(hits["receivables"]) * 6)
        facts = [f"매출채권 관련 잔액은 {_format_amount(receivables)}입니다."]
        if receivable_ratio is not None:
            facts.append(f"매출액 대비 약 {receivable_ratio:.1f}%입니다.")
        if receivable_growth is not None:
            facts.append(f"전기 대비 약 {receivable_growth:+.1f}% 변동했습니다.")
        if sales_growth is not None:
            facts.append(f"같은 기간 매출액은 약 {sales_growth:+.1f}% 변동했습니다.")
        findings.append(
            _finding(
                finding_id="receivables",
                title="매출채권 회수·운전자금 위험",
                category="운전자금",
                importance=importance,
                confidence=confidence,
                facts=facts,
                account_hits=hits["receivables"],
            )
        )

    inventory = latest_values.get("inventory")
    inventory_ratio = _ratio(inventory, sales)
    inventory_growth = _growth(inventory, previous_values.get("inventory"))
    if inventory is not None and inventory > 0:
        importance = 32 + min(38, int((inventory_ratio or 0) * 0.8))
        if (
            inventory_growth is not None
            and sales_growth is not None
            and inventory_growth > sales_growth + 20
        ):
            importance += 18
        confidence = 52 + min(30, len(hits["inventory"]) * 5)
        facts = [f"재고자산 관련 잔액은 {_format_amount(inventory)}입니다."]
        if inventory_ratio is not None:
            facts.append(f"매출액 대비 약 {inventory_ratio:.1f}%입니다.")
        if inventory_growth is not None:
            facts.append(f"전기 대비 약 {inventory_growth:+.1f}% 변동했습니다.")
        findings.append(
            _finding(
                finding_id="inventory",
                title="재고자산 적체·평가 위험",
                category="재무·운전자금",
                importance=importance,
                confidence=confidence,
                facts=facts,
                account_hits=hits["inventory"],
            )
        )

    net_income = latest_values.get("net_income")
    operating_cf = latest_values.get("operating_cashflow")
    if net_income is not None and net_income > 0 and operating_cf is not None:
        gap_ratio = _ratio(net_income - operating_cf, abs(net_income))
        if operating_cf < 0 or (gap_ratio is not None and gap_ratio >= 50):
            importance = 65 + (18 if operating_cf < 0 else 8)
            confidence = 75
            facts = [
                f"당기순이익은 {_format_amount(net_income)}입니다.",
                f"영업활동현금흐름은 {_format_amount(operating_cf)}입니다.",
                "회계상 이익과 실제 영업현금 창출력의 차이가 확인됩니다.",
            ]
            findings.append(
                _finding(
                    finding_id="cashflow",
                    title="이익과 영업현금흐름 불일치",
                    category="현금흐름",
                    importance=importance,
                    confidence=confidence,
                    facts=facts,
                    account_hits=[
                        *hits["net_income"],
                        *hits["operating_cashflow"],
                    ],
                )
            )

    liabilities = latest_values.get("liabilities")
    borrowings = latest_values.get("borrowings")
    debt_ratio = _ratio(liabilities, equity)
    current_ratio = _ratio(current_assets, current_liabilities)
    leverage_trigger = (
        (debt_ratio is not None and debt_ratio >= 200)
        or (current_ratio is not None and current_ratio < 100)
        or (borrowings is not None and assets not in (None, 0) and borrowings / assets >= 0.35)
    )
    if leverage_trigger:
        importance = 55
        if debt_ratio is not None and debt_ratio >= 300:
            importance += 20
        if current_ratio is not None and current_ratio < 70:
            importance += 15
        confidence = 70
        facts = []
        if debt_ratio is not None:
            facts.append(f"부채비율은 약 {debt_ratio:.1f}%입니다.")
        if current_ratio is not None:
            facts.append(f"유동비율은 약 {current_ratio:.1f}%입니다.")
        if borrowings is not None:
            facts.append(f"차입금 관련 잔액은 {_format_amount(borrowings)}입니다.")
        findings.append(
            _finding(
                finding_id="leverage",
                title="부채·유동성·차입구조 위험",
                category="재무구조",
                importance=importance,
                confidence=confidence,
                facts=facts,
                account_hits=[
                    *hits["liabilities"],
                    *hits["current_assets"],
                    *hits["current_liabilities"],
                    *hits["borrowings"],
                ],
            )
        )

    findings.sort(
        key=lambda item: (
            item["priority"],
            item["importance"],
            item["confidence"],
        ),
        reverse=True,
    )

    all_questions: list[str] = []
    all_documents: list[str] = []
    all_directions: list[str] = []
    for finding in findings:
        all_questions.extend(finding["questions"])
        all_documents.extend(finding["documents"])
        all_directions.extend(finding["directions"])

    return {
        "version": "v1",
        "findings": findings,
        "top_findings": findings[:5],
        "questions": list(dict.fromkeys(all_questions)),
        "documents": list(dict.fromkeys(all_documents)),
        "directions": list(dict.fromkeys(all_directions)),
        "account_groups_found": {
            group: len(group_hits)
            for group, group_hits in hits.items()
            if group_hits
        },
        "flat_field_count": len(flat),
        "year_count": len(
            {
                year
                for values in series.values()
                for year in values
                if year != 9999
            }
        ),
        "diagnosis_ready": bool(findings),
        "limitations": [
            "크레탑 요약계정과목만으로는 거래 상대방과 업무 관련성을 확정할 수 없습니다.",
            "가지급금 가능성은 선급금·미수금·대여금 등 의심계정을 묶은 사전 탐지입니다.",
            "실제 세무처리와 해결방안은 계정별원장·증빙·정관·주주관계를 확인한 뒤 결정해야 합니다.",
        ],
    }
