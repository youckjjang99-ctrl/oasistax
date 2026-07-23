"""개인사업자의 법인전환 '검토 필요도'를 설명 가능한 규칙으로 분석합니다."""

from __future__ import annotations


def _number(value, default=0):
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return float(default)


def analyze_corporate_conversion(business, all_businesses=None):
    """세액 확정값이 아닌 상담 우선순위와 추가 확인자료를 반환합니다."""
    all_businesses = all_businesses or [business]
    revenue = _number(business.get("매출액"))
    income = _number(business.get("사업소득금액"))
    taxable = _number(business.get("과세표준"))
    tax_rate = _number(business.get("적용세율"))
    margin = (income / revenue * 100) if revenue > 0 else 0

    score = 0
    reasons = []
    cautions = []
    checks = []

    if revenue >= 1_000_000_000:
        score += 25
        reasons.append("연 매출이 10억원 이상으로 법인 운영구조 비교의 실익이 큽니다.")
    elif revenue >= 500_000_000:
        score += 18
        reasons.append("연 매출이 5억원 이상으로 성장·자금조달 구조를 함께 검토할 단계입니다.")
    elif revenue >= 300_000_000:
        score += 10
        reasons.append("연 매출이 3억원 이상으로 향후 성장 시 법인전환 비교가 필요합니다.")

    if income >= 200_000_000:
        score += 30
        reasons.append("사업소득금액이 2억원 이상으로 개인·법인 세부담 비교 필요성이 높습니다.")
    elif income >= 100_000_000:
        score += 24
        reasons.append("사업소득금액이 1억원 이상으로 급여·배당·퇴직금 구조 비교가 필요합니다.")
    elif income >= 70_000_000:
        score += 15
        reasons.append("사업소득금액이 7천만원 이상으로 법인전환 손익분기 검토가 유효합니다.")
    elif income >= 40_000_000:
        score += 7

    if taxable >= 150_000_000 or tax_rate >= 38:
        score += 20
        reasons.append("종합소득 과세표준 또는 적용세율이 높은 구간으로 확인됩니다.")
    elif taxable >= 88_000_000 or tax_rate >= 35:
        score += 15
        reasons.append("개인 종합소득세의 높은 누진세율 구간이 적용된 것으로 확인됩니다.")
    elif taxable >= 50_000_000 or tax_rate >= 24:
        score += 8

    if margin >= 30:
        score += 12
        reasons.append(f"신고서 기준 소득률이 {margin:.1f}%로 높아 이익유보 구조를 비교할 가치가 있습니다.")
    elif margin >= 20:
        score += 8

    unique_businesses = {
        str(item.get("사업자등록번호", "") or item.get("업체명", ""))
        for item in all_businesses
    }
    if len(unique_businesses) >= 2:
        score += 8
        reasons.append("여러 사업장이 확인되어 통합 운영·법인별 분리 운영 여부를 검토할 필요가 있습니다.")

    if "외부조정" in str(business.get("신고유형", "")):
        score += 5
        reasons.append("외부조정 신고 사업자로 회계·세무 관리체계가 이미 일정 수준 갖춰져 있습니다.")

    score = min(100, int(round(score)))
    if score >= 75:
        grade = "적극 검토"
        summary = "법인전환 전후 세부담·현금흐름·자산이전 비용을 정밀 비교할 우선순위가 높습니다."
    elif score >= 50:
        grade = "검토 권장"
        summary = "법인전환에 따른 절세와 운영비 증가를 함께 비교해볼 구간입니다."
    elif score >= 30:
        grade = "조건부 검토"
        summary = "성장계획·고용계획·대표자 인출액에 따라 법인전환 효과가 달라질 수 있습니다."
    else:
        grade = "현 단계 유지 검토"
        summary = "현재 신고자료만으로는 즉시 전환보다 개인사업자 유지와 자료 보완이 우선입니다."

    if _number(business.get("세액감면")) > 0:
        cautions.append("현재 적용 중인 세액감면이 법인전환 후 승계·재적용되는지 별도 확인해야 합니다.")
    cautions.extend(
        [
            "법인전환은 개인세율과 법인세율의 단순 비교만으로 결정할 수 없습니다.",
            "대표자 급여·배당·퇴직금, 4대보험, 법인 유지비와 자금 인출계획을 함께 반영해야 합니다.",
            "사업용 부동산·차량·재고·채무가 있으면 이전 방식과 취득세·양도세 영향을 확인해야 합니다.",
        ]
    )
    checks.extend(
        [
            "최근 3개년 종합소득세 신고서와 부가가치세 과세표준증명",
            "사업용 자산·부채·임대차·대출 현황",
            "대표자 연간 필요 생활자금과 향후 배당·퇴직 계획",
            "향후 3년 매출·채용·투자 계획",
            "현재 적용 중인 세액감면·세액공제의 승계 가능 여부",
        ]
    )
    action_plan = [
        "1단계: 최근 3개년 매출·소득·세액 추이를 확인합니다.",
        "2단계: 개인사업자 유지안과 법인전환안의 세금·4대보험·운영비를 비교합니다.",
        "3단계: 자산·부채 이전방식과 영업권 평가 필요성을 검토합니다.",
        "4단계: 대표자 급여·배당·퇴직금 및 유보이익 사용계획을 설계합니다.",
        "5단계: 감면 승계와 인허가·계약·대출 명의변경을 확인한 후 전환시기를 결정합니다.",
    ]

    return {
        "score": score,
        "grade": grade,
        "summary": summary,
        "reasons": reasons or ["신고자료만으로 뚜렷한 전환 요인을 확인하지 못했습니다."],
        "cautions": cautions,
        "required_checks": checks,
        "action_plan": action_plan,
        "metrics": {
            "매출액": int(revenue),
            "사업소득금액": int(income),
            "과세표준": int(taxable),
            "적용세율": tax_rate,
            "소득률": round(margin, 2),
            "사업장수": len(unique_businesses),
        },
        "basis": "2026년 기준 신고자료 기반 사전 검토",
    }
