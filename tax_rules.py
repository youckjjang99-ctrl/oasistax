from __future__ import annotations

"""AI 절세진단 표시용 기준표.

확정 세액계산이 아니라 사전 검토 범위를 안내한다. 실제 적용은 사업연도,
기업규모, 업종, 소재지, 자산·인력 요건 및 중복공제 제한을 세무사가 확인한다.
"""

TAX_RULE_BASIS_DATE = "2026-07-20"
TAX_RULE_SOURCE = "국세청 2026년 공제·감면 안내 기준"

TAX_RULES = {
    "통합투자세액공제": {
        "rate_label": "예상 공제율",
        "rate_range": "10~30%",
        "rate_note": "중소기업 기본공제 기준. 일반 10%, 신성장·원천기술 12%, 국가전략기술 25%, 반도체 기술 30%; 증액투자는 별도 추가공제 검토",
        "calc_type": "rate",
        "low_rate": 0.10,
        "high_rate": 0.30,
        "input_label": "공제대상 예상 투자금액",
    },
    "연구·인력개발비 세액공제": {
        "rate_label": "예상 공제율",
        "rate_range": "비용·증가분 방식별 산정",
        "rate_note": "연구개발 유형, 기업규모, 당기분·증가분 방식에 따라 달라져 비용자료 확인 후 산정",
        "calc_type": "unavailable",
        "input_label": "공제대상 연구·인력개발비",
    },
    "고용 관련 세액공제": {
        "rate_label": "예상 공제액",
        "rate_range": "1인당 연 400~2,000만원",
        "rate_note": "2026~2028년 중소기업 통합고용세액공제 기준. 수도권 여부·청년등 해당 여부·공제연차에 따라 차등",
        "calc_type": "per_head",
        "low_amount": 4_000_000,
        "high_amount": 20_000_000,
        "input_label": "예상 고용증가 인원",
    },
    "중소기업 특별세액감면": {
        "rate_label": "예상 감면율",
        "rate_range": "5~30%",
        "rate_note": "기업규모·업종·사업장 소재지에 따라 차등, 감면한도 및 중복감면 제한 별도 검토",
        "calc_type": "rate",
        "low_rate": 0.05,
        "high_rate": 0.30,
        "input_label": "감면대상 산출세액",
    },
    "창업·지역 관련 세액감면": {
        "rate_label": "예상 감면율",
        "rate_range": "25~100%",
        "rate_note": "2026년 이후 창업분은 지역·청년/생계형 여부에 따라 기본감면율이 달라지며 고용증가 추가감면 가능",
        "calc_type": "rate",
        "low_rate": 0.25,
        "high_rate": 1.00,
        "input_label": "감면대상 산출세액",
    },
    "법인 세무리스크 점검": {
        "rate_label": "예상 공제율",
        "rate_range": "해당 없음",
        "rate_note": "세액공제가 아니라 잠재 세무리스크를 확인하는 항목",
        "calc_type": "none",
    },
}
