from __future__ import annotations

import re
from typing import Any

import pandas as pd

from tax_diagnosis import _clean, _financial_snapshot, _number


def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).startswith("_"):
                continue
            name = f"{prefix} {key}".strip()
            rows.extend(_flatten(child, name))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(_flatten(child, f"{prefix} {index + 1}".strip()))
    else:
        rows.append((prefix, value))
    return rows


def _normalize_label(value: Any) -> str:
    return re.sub(r"[\s·ㆍ_()\-]", "", _clean(value)).lower()


def _account_map(financial: dict[str, Any]) -> dict[str, list[tuple[str, float]]]:
    result: dict[str, list[tuple[str, float]]] = {}
    for path, value in _flatten(financial):
        amount = _number(value)
        if amount is None:
            continue
        label = _normalize_label(path)
        if not label:
            continue
        result.setdefault(label, []).append((path, amount))
    return result


def _find_accounts(accounts: dict[str, list[tuple[str, float]]], keywords: list[str]) -> list[tuple[str, float]]:
    normalized = [_normalize_label(word) for word in keywords]
    found: list[tuple[str, float]] = []
    seen: set[tuple[str, float]] = set()
    for label, values in accounts.items():
        if not any(token and token in label for token in normalized):
            continue
        for value in values:
            if value not in seen:
                found.append(value)
                seen.add(value)
    return found


def _sum_accounts(accounts: dict[str, list[tuple[str, float]]], keywords: list[str]) -> tuple[float, list[str]]:
    rows = _find_accounts(accounts, keywords)
    return sum(abs(amount) for _, amount in rows), [path for path, _ in rows]


def _ratio(amount: float, base: float | None) -> float | None:
    if base in (None, 0):
        return None
    return amount / abs(float(base)) * 100.0


def _severity(score: int) -> str:
    if score >= 80:
        return "높음"
    if score >= 55:
        return "보통"
    return "낮음"


def _signal(
    name: str,
    category: str,
    score: int,
    reasons: list[str],
    documents: list[str],
    questions: list[str],
    account_paths: list[str] | None = None,
    ratio_value: float | None = None,
    caution: str = "",
) -> dict[str, Any]:
    score = max(0, min(95, int(score)))
    return {
        "name": name,
        "category": category,
        "score": score,
        "severity": _severity(score),
        "reasons": reasons,
        "documents": documents,
        "questions": questions,
        "account_paths": account_paths or [],
        "ratio": ratio_value,
        "caution": caution,
    }


def build_financial_anomaly(user_id: str, customer: pd.Series) -> dict[str, Any]:
    business_no = _clean(customer.get("사업자등록번호", ""))
    financial = _financial_snapshot(user_id, business_no)
    accounts = _account_map(financial)

    sales = _number(customer.get("매출액", ""))
    if sales is None:
        sales_amount, _ = _sum_accounts(accounts, ["매출액", "영업수익", "수익합계"])
        sales = sales_amount or None

    assets = _number(customer.get("자산총계", ""))
    if assets is None:
        assets_amount, _ = _sum_accounts(accounts, ["자산총계", "총자산"])
        assets = assets_amount or None

    employees = _number(customer.get("종업원수", "")) or _number(customer.get("상시근로자수", ""))

    results: list[dict[str, Any]] = []

    loan_amount, loan_paths = _sum_accounts(
        accounts,
        ["가지급금", "주주임원종업원단기대여금", "단기대여금", "장기대여금", "대표자대여금", "임원대여금"],
    )
    hidden_amount, hidden_paths = _sum_accounts(
        accounts,
        ["미수금", "미수수익", "선급금", "기타채권", "보증금", "가수금"],
    )
    direct_ratio = _ratio(loan_amount, assets)
    hidden_ratio = _ratio(hidden_amount, assets)
    score = 22
    reasons: list[str] = []
    if loan_amount > 0:
        score += 48
        reasons.append(f"대여금·가지급금 관련 계정 {loan_amount:,.0f}원이 확인됨")
    if hidden_amount > 0:
        score += min(22, 8 + int((hidden_ratio or 0) / 2))
        reasons.append(f"미수금·선급금·기타채권 등 추가 확인 계정 {hidden_amount:,.0f}원이 확인됨")
    if direct_ratio is not None and direct_ratio >= 5:
        score += 10
        reasons.append(f"대여금 관련 금액이 총자산의 약 {direct_ratio:.1f}%")
    if not reasons:
        reasons.append("현재 요약재무에서 대표자 관련 채권 계정이 명확히 확인되지 않음")
    results.append(_signal(
        "숨은 가지급금 가능성", "대표자·특수관계인", score, reasons,
        ["계정별원장", "거래처별 잔액명세서", "대표자 자금거래 내역", "가지급금 인정이자 조정명세"],
        ["대여금·미수금·선급금 중 대표자 또는 특수관계인 관련 금액이 있나요?", "법인카드나 법인계좌의 개인 사용분이 남아 있나요?"],
        loan_paths + hidden_paths, direct_ratio,
        "가수금은 부채계정이므로 그 자체를 가지급금으로 단정하지 않고 자금 흐름을 함께 확인합니다.",
    ))

    expense_rules = [
        ("접대비 과다 검토", "비용", ["접대비", "기업업무추진비"], 1.5,
         ["접대비 계정별원장", "법인카드 사용내역", "증빙불비 내역"],
         ["거래처 접대 목적과 참석자를 기록하고 있나요?", "상품권·현금성 지출이 포함돼 있나요?"]),
        ("광고선전비 과다 검토", "비용", ["광고선전비", "판매촉진비", "홍보비"], 8.0,
         ["광고대행 계약서", "매체별 광고비 명세", "세금계산서"],
         ["광고비가 특정 대행사나 관계사에 집중돼 있나요?", "광고비와 실제 매출 유입을 대조할 수 있나요?"]),
        ("복리후생비 이상 검토", "비용", ["복리후생비", "복지비"], 4.0,
         ["복리후생비 원장", "직원별 지급내역", "사내 복지규정"],
         ["대표자·임원 개인비용이 포함돼 있나요?", "직원 공통 복지 기준이 있나요?"]),
        ("지급수수료 이상 검토", "비용", ["지급수수료", "용역수수료", "외주용역비"], 10.0,
         ["지급수수료 원장", "용역계약서", "거래처별 지급명세"],
         ["관계사 또는 특수관계인에게 지급한 수수료가 있나요?", "실제 용역 결과물을 보관하고 있나요?"]),
    ]
    for name, category, keywords, threshold, documents, questions in expense_rules:
        amount, paths = _sum_accounts(accounts, keywords)
        ratio_value = _ratio(amount, sales)
        item_score = 18
        item_reasons: list[str] = []
        if amount > 0:
            item_score += 20
            item_reasons.append(f"관련 계정 금액 {amount:,.0f}원이 확인됨")
        if ratio_value is not None:
            item_reasons.append(f"매출액 대비 약 {ratio_value:.1f}%")
            if ratio_value >= threshold * 2:
                item_score += 48
            elif ratio_value >= threshold:
                item_score += 32
            elif ratio_value >= threshold * 0.5:
                item_score += 15
        else:
            item_reasons.append("매출액 또는 계정 상세가 부족해 비율판단이 제한됨")
        results.append(_signal(
            name, category, item_score, item_reasons, documents, questions, paths, ratio_value,
            f"표시 기준 {threshold:.1f}%는 이상징후 선별용 내부 기준이며 업종별 세법상 한도나 확정판정이 아닙니다.",
        ))

    payroll_amount, payroll_paths = _sum_accounts(accounts, ["임원급여", "임원보수", "급여", "상여금"])
    payroll_ratio = _ratio(payroll_amount, sales)
    payroll_score = 20
    payroll_reasons: list[str] = []
    if payroll_amount > 0:
        payroll_score += 22
        payroll_reasons.append(f"급여·임원보수 관련 계정 {payroll_amount:,.0f}원이 확인됨")
    if payroll_ratio is not None:
        payroll_reasons.append(f"매출액 대비 약 {payroll_ratio:.1f}%")
        if payroll_ratio >= 25:
            payroll_score += 42
        elif payroll_ratio >= 15:
            payroll_score += 25
    if employees is not None:
        payroll_reasons.append(f"등록 종업원수 {int(employees)}명과 함께 적정성을 검토할 수 있음")
    if not payroll_reasons:
        payroll_reasons.append("임원보수·급여 상세자료가 부족함")
    results.append(_signal(
        "임원급여·상여 검토", "임원보상", payroll_score, payroll_reasons,
        ["임원보수규정", "정관", "주주총회·이사회 의사록", "급여대장"],
        ["임원보수 한도를 주주총회에서 결의했나요?", "성과급 산정 기준이 사전에 정해져 있나요?"],
        payroll_paths, payroll_ratio,
        "임원보수의 손금 인정은 지급 근거, 한도 및 사전 확정 여부를 별도로 검토해야 합니다.",
    ))

    vehicle_amount, vehicle_paths = _sum_accounts(accounts, ["차량유지비", "업무용승용차", "차량비", "리스료"])
    vehicle_ratio = _ratio(vehicle_amount, sales)
    vehicle_score = 20 + (24 if vehicle_amount > 0 else 0)
    if vehicle_ratio is not None and vehicle_ratio >= 5:
        vehicle_score += 35
    vehicle_reasons = [f"차량 관련 계정 {vehicle_amount:,.0f}원이 확인됨"] if vehicle_amount > 0 else ["차량 관련 계정 상세가 확인되지 않음"]
    if vehicle_ratio is not None:
        vehicle_reasons.append(f"매출액 대비 약 {vehicle_ratio:.1f}%")
    results.append(_signal(
        "업무용승용차 비용 검토", "차량", vehicle_score, vehicle_reasons,
        ["업무용승용차 명세서", "운행기록부", "보험가입증명", "리스·렌트 계약서"],
        ["임직원전용보험에 가입돼 있나요?", "업무용 운행기록을 작성하고 있나요?"],
        vehicle_paths, vehicle_ratio,
    ))

    results.sort(key=lambda item: (-item["score"], item["name"]))
    high = sum(1 for item in results if item["severity"] == "높음")
    medium = sum(1 for item in results if item["severity"] == "보통")
    overall = int(round(sum(item["score"] for item in results) / max(1, len(results))))
    confidence_signals = [bool(financial), bool(accounts), sales is not None, assets is not None, employees is not None]
    confidence = 30 + sum(12 for flag in confidence_signals if flag)
    confidence = min(90, confidence)

    return {
        "items": results,
        "overall_score": overall,
        "overall_level": _severity(overall),
        "confidence": confidence,
        "high_count": high,
        "medium_count": medium,
        "source": financial.get("_source", "고객DB") if financial else "고객DB",
        "disclaimer": "AI 이상징후는 검토 대상을 선별하는 보조지표입니다. 계정별원장·증빙·세무조정 내역을 확인하기 전에는 사적사용, 손금불산입 또는 탈루로 단정하지 않습니다.",
    }
