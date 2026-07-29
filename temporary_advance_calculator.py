from __future__ import annotations

from datetime import datetime
from math import inf
from typing import Any, Mapping


# 2026-07-29 시행 법령·공단 공지 기준. 화면에서 사용자가 실제 신고값으로
# 바꿀 수 있도록 이자율과 선택형 보험요율은 입력값으로도 받는다.
DEFAULT_RECOGNIZED_INTEREST_RATE = 4.6
NATIONAL_PENSION_RATE_2026 = 0.095
NATIONAL_PENSION_MONTHLY_FLOOR_2026 = 410_000
NATIONAL_PENSION_MONTHLY_CAP_2026 = 6_590_000
HEALTH_AND_LTC_RATE_2026 = 0.081348
EMPLOYMENT_WORKER_RATE = 0.009
EMPLOYMENT_EMPLOYER_RATE = 0.009
LOCAL_INCOME_TAX_RATIO = 0.10

PERSONAL_INCOME_TAX_BRACKETS = (
    (14_000_000, 0.06),
    (50_000_000, 0.15),
    (88_000_000, 0.24),
    (150_000_000, 0.35),
    (300_000_000, 0.38),
    (500_000_000, 0.40),
    (1_000_000_000, 0.42),
    (inf, 0.45),
)

CORPORATE_TAX_BRACKETS_2026 = (
    (200_000_000, 0.10),
    (20_000_000_000, 0.20),
    (300_000_000_000, 0.22),
    (inf, 0.25),
)

TEMPORARY_ADVANCE_KEYS = (
    "가지급금",
    "주주임원종업원단기대여금",
    "단기대여금",
    "대표자대여금",
    "임원대여금",
)

LEGAL_BASIS = (
    {
        "title": "법인세법 시행령 제89조 제3항",
        "summary": "금전 대여의 시가는 원칙적으로 가중평균차입이자율이며, 요건에 따라 당좌대출이자율을 적용합니다.",
        "url": "https://law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=1000117898",
    },
    {
        "title": "법인세법 시행규칙 제43조 제2항",
        "summary": "2026년 현재 당좌대출이자율은 연 4.6%입니다.",
        "url": "https://www.law.go.kr/LSW/lsLawLinkInfo.do?chrClsCd=010202&lsJoLnkSeq=1000088739",
    },
    {
        "title": "법인세법 시행령 제88조",
        "summary": "시가와 거래가액의 차이가 3억원 이상이거나 시가의 5% 이상이면 부당행위계산 부인 기준을 검토합니다.",
        "url": "https://www.law.go.kr/법령/법인세법시행령/제88조",
    },
    {
        "title": "법인세법 시행령 제53조",
        "summary": "업무무관 대여금이 있으면 차입금 지급이자의 일부가 손금불산입될 수 있습니다.",
        "url": "https://law.go.kr/LSW/lsLinkCommonInfo.do?lspttninfSeq=59053",
    },
    {
        "title": "소득세법 제55조",
        "summary": "대표자 상여처분 시 개인의 기존 과세표준을 포함한 누진세율을 적용합니다.",
        "url": "https://www.law.go.kr/법령/소득세법/제55조",
    },
    {
        "title": "법인세법 제55조",
        "summary": "2026년 법인세율 10%·20%·22%·25%를 기존 법인 과세표준에 누진 적용합니다.",
        "url": "https://www.law.go.kr/법령/법인세법/제55조",
    },
    {
        "title": "지방세법 제92조",
        "summary": "개인지방소득세는 종합소득 과세표준 구간별 세율로 계산합니다.",
        "url": "https://www.law.go.kr/법령/지방세법/제92조",
    },
    {
        "title": "지방세법 제103조의20",
        "summary": "법인지방소득세는 법인소득 과세표준 구간별 세율로 계산합니다.",
        "url": "https://www.law.go.kr/법령/지방세법/제103조의20",
    },
    {
        "title": "국세법령정보시스템 법령해석",
        "summary": "미수 인정이자의 소득처분은 실제 회수 여부와 회수기한 등 사실관계를 확인해야 합니다.",
        "url": "https://taxlaw.nts.go.kr/qt/USEQTA002P.do?ntstDcmId=200000000000009085",
    },
    {
        "title": "국민연금공단 2026 보험료 안내",
        "summary": "보험료율 9.5%, 2026.7~2027.6 기준소득월액 상한 659만원·하한 41만원입니다.",
        "url": "https://www.nps.or.kr/pnsinfo/ntpsklg/getOHAF0097M0.do",
    },
    {
        "title": "국민건강보험공단 2026 제도 안내",
        "summary": "건강보험 7.19%와 장기요양보험 0.9448%를 합산한 보수 대비 요율은 8.1348%입니다.",
        "url": "https://www.nhis.or.kr/renewal_popup/poster/20260204_poster_longdesc_1.html",
    },
    {
        "title": "고용노동부 고용보험료 안내",
        "summary": "실업급여 보험료율은 근로자와 사업주 각 0.9%이며 사업주는 기업 규모별 고용안정 요율을 추가 부담합니다.",
        "url": "https://moel.go.kr/info/astmgmt/employ/employList.do",
    },
    {
        "title": "고용노동부 2026 산재보험료율",
        "summary": "산재보험은 실제 업종요율과 대표자의 근로자성 또는 특례가입 여부를 확인해야 합니다.",
        "url": "https://moel.go.kr/news/enews/report/enewsView.do?news_seq=18810",
    },
)


def _number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    text = str(value).replace(",", "").replace("원", "").strip()
    if not text or text.lower() in {"nan", "none", "nat"}:
        return default
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def _non_negative(value: Any) -> float:
    return max(0.0, _number(value))


def _mapping_get(source: Any, key: str) -> Any:
    if source is None:
        return None
    try:
        return source.get(key)
    except (AttributeError, TypeError):
        return None


def extract_temporary_advance_balance(*sources: Any) -> tuple[float, str]:
    """Return the first positive 가지급금 계정 to avoid double-counting aliases."""
    for source in sources:
        for key in TEMPORARY_ADVANCE_KEYS:
            value = _non_negative(_mapping_get(source, key))
            if value > 0:
                return value, key
    return 0.0, ""


def progressive_tax(
    taxable_base: Any,
    brackets: tuple[tuple[float, float], ...],
) -> float:
    base = _non_negative(taxable_base)
    tax = 0.0
    lower = 0.0
    for upper, rate in brackets:
        if base <= lower:
            break
        band = min(base, upper) - lower
        tax += max(0.0, band) * rate
        lower = upper
    return tax


def incremental_progressive_tax(
    current_taxable_base: Any,
    additional_taxable_base: Any,
    brackets: tuple[tuple[float, float], ...],
) -> float:
    current = _non_negative(current_taxable_base)
    additional = _non_negative(additional_taxable_base)
    return max(
        0.0,
        progressive_tax(current + additional, brackets)
        - progressive_tax(current, brackets),
    )


def _pension_increment(
    current_monthly_standard_income: float,
    annual_bonus: float,
) -> float:
    if annual_bonus <= 0:
        return 0.0

    current = _non_negative(current_monthly_standard_income)
    current_base = (
        min(
            NATIONAL_PENSION_MONTHLY_CAP_2026,
            max(NATIONAL_PENSION_MONTHLY_FLOOR_2026, current),
        )
        if current > 0
        else 0.0
    )
    new_monthly_income = current + annual_bonus / 12
    new_base = min(
        NATIONAL_PENSION_MONTHLY_CAP_2026,
        max(NATIONAL_PENSION_MONTHLY_FLOOR_2026, new_monthly_income),
    )
    return max(0.0, new_base - current_base) * 12 * NATIONAL_PENSION_RATE_2026


def calculate_temporary_advance(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate an advisory scenario, not a tax filing determination.

    The calculator intentionally separates the deemed-interest adjustment,
    representative bonus disposition, disallowed borrowing interest, and
    social-insurance scenarios. A balance alone is never treated as income.
    """
    balance = _non_negative(inputs.get("balance"))
    days = min(366, max(0, int(_number(inputs.get("days"), 365))))
    interest_rate_pct = max(
        0.0,
        _number(
            inputs.get("recognized_interest_rate_pct"),
            DEFAULT_RECOGNIZED_INTEREST_RATE,
        ),
    )
    received_interest = _non_negative(inputs.get("received_interest"))
    disposition_mode = str(
        inputs.get("disposition_mode", "미회수·대표자 상여처분 가정")
    )

    recognized_interest = balance * (interest_rate_pct / 100) * days / 365
    received_interest = min(received_interest, recognized_interest)
    interest_shortfall = max(0.0, recognized_interest - received_interest)
    shortfall_ratio = (
        interest_shortfall / recognized_interest
        if recognized_interest > 0
        else 0.0
    )
    adjustment_required = bool(
        interest_shortfall > 0
        and (
            interest_shortfall >= 300_000_000
            or shortfall_ratio >= 0.05
        )
    )
    tax_adjustment_interest = (
        interest_shortfall if adjustment_required else 0.0
    )
    bonus_disposition = (
        tax_adjustment_interest
        if disposition_mode == "미회수·대표자 상여처분 가정"
        else 0.0
    )

    representative_tax_base = _non_negative(
        inputs.get("representative_tax_base")
    )
    personal_income_tax = incremental_progressive_tax(
        representative_tax_base,
        bonus_disposition,
        PERSONAL_INCOME_TAX_BRACKETS,
    )
    personal_local_income_tax = personal_income_tax * LOCAL_INCOME_TAX_RATIO

    nps_health_covered = bool(inputs.get("nps_health_covered", True))
    employment_covered = bool(inputs.get("employment_covered", False))
    industrial_accident_covered = bool(
        inputs.get("industrial_accident_covered", False)
    )
    current_monthly_standard_income = _non_negative(
        inputs.get("current_monthly_standard_income")
    )
    employment_stability_rate = max(
        0.0,
        _number(inputs.get("employment_stability_rate_pct"), 0.25) / 100,
    )
    industrial_accident_rate = max(
        0.0,
        _number(inputs.get("industrial_accident_rate_pct"), 1.47) / 100,
    )

    pension_total = (
        _pension_increment(
            current_monthly_standard_income,
            bonus_disposition,
        )
        if nps_health_covered
        else 0.0
    )
    health_ltc_total = (
        bonus_disposition * HEALTH_AND_LTC_RATE_2026
        if nps_health_covered
        else 0.0
    )
    employment_worker = (
        bonus_disposition * EMPLOYMENT_WORKER_RATE
        if employment_covered
        else 0.0
    )
    employment_employer = (
        bonus_disposition
        * (EMPLOYMENT_EMPLOYER_RATE + employment_stability_rate)
        if employment_covered
        else 0.0
    )
    industrial_accident_employer = (
        bonus_disposition * industrial_accident_rate
        if industrial_accident_covered
        else 0.0
    )

    employee_insurance = (
        pension_total / 2
        + health_ltc_total / 2
        + employment_worker
    )
    employer_insurance = (
        pension_total / 2
        + health_ltc_total / 2
        + employment_employer
        + industrial_accident_employer
    )

    total_borrowings = _non_negative(inputs.get("total_borrowings"))
    annual_interest_expense = _non_negative(
        inputs.get("annual_interest_expense")
    )
    disallowed_interest = 0.0
    if total_borrowings > 0 and annual_interest_expense > 0:
        disallowed_interest = annual_interest_expense * (
            min(balance, total_borrowings) / total_borrowings
        )

    corporate_tax_base = _non_negative(inputs.get("corporate_tax_base"))
    corporate_taxable_increment = (
        tax_adjustment_interest + disallowed_interest
    )
    corporate_income_tax = incremental_progressive_tax(
        corporate_tax_base,
        corporate_taxable_increment,
        CORPORATE_TAX_BRACKETS_2026,
    )
    corporate_local_income_tax = (
        corporate_income_tax * LOCAL_INCOME_TAX_RATIO
    )

    representative_tax_total = (
        personal_income_tax + personal_local_income_tax
    )
    representative_total = representative_tax_total + employee_insurance
    company_tax_total = corporate_income_tax + corporate_local_income_tax
    company_tax_and_insurance = company_tax_total + employer_insurance
    company_cash_exposure = company_tax_and_insurance + interest_shortfall

    insurance_rows = [
        {
            "구분": "국민연금",
            "대표자 부담": pension_total / 2,
            "법인 부담": pension_total / 2,
            "적용": nps_health_covered,
            "기준": "2026년 9.5%, 노사 1/2·월 상하한 반영",
        },
        {
            "구분": "건강·장기요양",
            "대표자 부담": health_ltc_total / 2,
            "법인 부담": health_ltc_total / 2,
            "적용": nps_health_covered,
            "기준": "2026년 보수 대비 합계 8.1348%, 노사 1/2",
        },
        {
            "구분": "고용보험",
            "대표자 부담": employment_worker,
            "법인 부담": employment_employer,
            "적용": employment_covered,
            "기준": "실업급여 각 0.9% + 법인 고용안정요율",
        },
        {
            "구분": "산재보험",
            "대표자 부담": 0.0,
            "법인 부담": industrial_accident_employer,
            "적용": industrial_accident_covered,
            "기준": f"입력 업종요율 {industrial_accident_rate * 100:.3f}%",
        },
    ]

    warnings: list[str] = []
    if balance <= 0:
        warnings.append(
            "가지급금 잔액이 0원입니다. 크레탑 계정 또는 직접 입력액을 확인하세요."
        )
    if interest_rate_pct == DEFAULT_RECOGNIZED_INTEREST_RATE:
        warnings.append(
            "4.6%는 당좌대출이자율 가정입니다. 원칙인 회사의 가중평균차입이자율을 먼저 확인해야 합니다."
        )
    if bonus_disposition > 0:
        warnings.append(
            "대표자 세액은 상여처분액 전액이 과세표준에 더해지는 보수적 추정치입니다. 근로소득공제·세액공제·다른 소득에 따라 실제 세액이 달라집니다."
        )
    if nps_health_covered and bonus_disposition > 0:
        warnings.append(
            "국민연금·건강보험은 상여처분액이 보수총액에 반영되는 시나리오입니다. 실제 보수 신고·정산과 상한 적용은 공단 고지로 확정됩니다."
        )
    if not employment_covered or not industrial_accident_covered:
        warnings.append(
            "대표이사는 통상 고용·산재보험의 근로자성이 인정되지 않습니다. 실제 피보험자격이 있을 때만 해당 항목을 선택하세요."
        )
    if total_borrowings <= 0 or annual_interest_expense <= 0:
        warnings.append(
            "법인 차입금 또는 지급이자를 입력하지 않아 지급이자 손금불산입 추정액은 0원입니다."
        )

    return {
        "calculated_at": datetime.now().isoformat(timespec="seconds"),
        "inputs": {
            "balance": balance,
            "days": days,
            "recognized_interest_rate_pct": interest_rate_pct,
            "received_interest": received_interest,
            "disposition_mode": disposition_mode,
            "representative_tax_base": representative_tax_base,
            "corporate_tax_base": corporate_tax_base,
            "total_borrowings": total_borrowings,
            "annual_interest_expense": annual_interest_expense,
            "current_monthly_standard_income": current_monthly_standard_income,
            "nps_health_covered": nps_health_covered,
            "employment_covered": employment_covered,
            "industrial_accident_covered": industrial_accident_covered,
            "employment_stability_rate_pct": employment_stability_rate * 100,
            "industrial_accident_rate_pct": industrial_accident_rate * 100,
        },
        "representative": {
            "bonus_disposition": bonus_disposition,
            "income_tax": personal_income_tax,
            "local_income_tax": personal_local_income_tax,
            "tax_total": representative_tax_total,
            "insurance_total": employee_insurance,
            "total_burden": representative_total,
        },
        "company": {
            "recognized_interest": recognized_interest,
            "received_interest": received_interest,
            "interest_shortfall": interest_shortfall,
            "shortfall_ratio": shortfall_ratio,
            "adjustment_required": adjustment_required,
            "tax_adjustment_interest": tax_adjustment_interest,
            "disallowed_interest": disallowed_interest,
            "taxable_increment": corporate_taxable_increment,
            "corporate_income_tax": corporate_income_tax,
            "local_income_tax": corporate_local_income_tax,
            "tax_total": company_tax_total,
            "employer_insurance_total": employer_insurance,
            "tax_and_insurance_burden": company_tax_and_insurance,
            "cash_exposure_including_uncollected_interest": company_cash_exposure,
        },
        "insurance_rows": insurance_rows,
        "combined_tax_and_insurance_burden": (
            representative_total + company_tax_and_insurance
        ),
        "warnings": warnings,
        "legal_basis": list(LEGAL_BASIS),
    }


def default_temporary_advance_inputs(balance: Any = 0) -> dict[str, Any]:
    return {
        "balance": _non_negative(balance),
        "days": 365,
        "recognized_interest_rate_pct": DEFAULT_RECOGNIZED_INTEREST_RATE,
        "received_interest": 0,
        "disposition_mode": "미회수·대표자 상여처분 가정",
        "representative_tax_base": 0,
        "corporate_tax_base": 0,
        "total_borrowings": 0,
        "annual_interest_expense": 0,
        "current_monthly_standard_income": 0,
        "nps_health_covered": True,
        "employment_covered": False,
        "industrial_accident_covered": False,
        "employment_stability_rate_pct": 0.25,
        "industrial_accident_rate_pct": 1.47,
    }
