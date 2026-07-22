from __future__ import annotations

import re
from typing import Any, Iterable


SCENARIO_SIGNALS = [
    {
        "signal": "시설·설비 투자",
        "keywords": ["시설", "설비", "기계", "장비", "공장", "이전", "증설", "차량", "인테리어", "자동화"],
        "services": ["정책자금", "시설자금", "보증기관 자금"],
        "questions": [
            "투자 시기와 예상 금액은 어느 정도로 보고 계십니까?",
            "견적서나 투자계획서가 이미 준비되어 있습니까?",
            "자기자금과 외부조달 자금의 비중은 어떻게 계획하고 계십니까?",
        ],
        "talking_points": [
            "시설투자는 집행 전에 자금 가능성과 세액공제 여부를 함께 검토해야 선택지가 넓어집니다.",
            "정확한 견적과 투자시점이 확인되면 운전자금과 시설자금을 분리해 검토할 수 있습니다.",
        ],
    },
    {
        "signal": "채용·고용",
        "keywords": ["채용", "직원", "인원", "고용", "청년", "증원", "퇴사", "인건비", "4대보험"],
        "services": ["고용지원금", "고용 관련 세액공제", "운전자금"],
        "questions": [
            "채용 예정 인원과 시기는 언제입니까?",
            "채용 대상의 연령과 경력 조건은 어떻게 됩니까?",
            "최근 1년간 고용보험 인원 변동은 어떻게 됩니까?",
        ],
        "talking_points": [
            "고용지원금은 채용 후가 아니라 채용 전 요건 확인이 중요합니다.",
            "지원금과 세액공제는 적용 기준이 달라 각각 확인해야 합니다.",
        ],
    },
    {
        "signal": "자금 부족·대환",
        "keywords": ["자금", "대출", "금리", "이자", "상환", "한도", "대환", "운전자금", "현금", "부족"],
        "services": ["정책자금", "신용보증", "대환·금융구조 점검"],
        "questions": [
            "현재 대출 잔액과 월 상환 부담은 어느 정도입니까?",
            "필요한 자금의 용도와 희망 실행시기는 언제입니까?",
            "최근 연체·세금 체납 또는 보증기관 이용이력이 있습니까?",
        ],
        "talking_points": [
            "자금은 필요금액보다 사용목적과 상환구조를 먼저 정리해야 승인 가능성을 판단할 수 있습니다.",
            "기존 대출이 있어도 자금 용도와 보증 여력에 따라 별도 검토가 가능합니다.",
        ],
    },
    {
        "signal": "세금·경정청구",
        "keywords": ["세금", "법인세", "소득세", "경정", "환급", "세액공제", "절세", "세무", "기장"],
        "services": ["경정청구", "세액공제 점검", "기장 검토"],
        "questions": [
            "최근 5개년 세액공제 적용내역을 확인해 보셨습니까?",
            "직원 증가나 설비투자가 있었던 연도는 언제입니까?",
            "현재 세무대리인에게 정기적인 세액공제 검토를 받고 계십니까?",
        ],
        "talking_points": [
            "납부세액만으로 누락 여부를 단정할 수 없어 신고서와 세액공제 명세를 함께 봐야 합니다.",
            "경정청구는 환급 가능성뿐 아니라 사후 소명자료까지 함께 검토해야 합니다.",
        ],
    },
    {
        "signal": "가지급금·재무구조",
        "keywords": ["가지급금", "가수금", "대표자대여금", "인출", "부채", "재무", "이익잉여금", "배당"],
        "services": ["가지급금 정리", "재무구조 개선", "정관·보수체계 점검"],
        "questions": [
            "가지급금의 발생 원인과 현재 잔액을 알고 계십니까?",
            "대표자 급여·배당·퇴직금 규정이 현재 상황에 맞게 정비돼 있습니까?",
            "향후 주식이동이나 승계 계획이 있습니까?",
        ],
        "talking_points": [
            "가지급금은 단일 방법으로 없애기보다 원인과 상환재원을 먼저 확인해야 합니다.",
            "정리 방법에 따라 소득세·법인세·상법상 절차가 달라질 수 있습니다.",
        ],
    },
    {
        "signal": "승계·대표자 리스크",
        "keywords": ["승계", "상속", "증여", "자녀", "후계자", "은퇴", "퇴직", "유고", "보험"],
        "services": ["가업승계", "주식가치 평가", "대표자 보장·퇴직재원"],
        "questions": [
            "승계 대상자와 희망 시기는 정해져 있습니까?",
            "현재 주식가치와 향후 가치 상승요인을 확인해 보셨습니까?",
            "대표자 유고 시 운영자금과 상속세 재원은 준비되어 있습니까?",
        ],
        "talking_points": [
            "승계는 실행 시점보다 수년 전부터 주식가치와 지배구조를 관리하는 것이 중요합니다.",
            "대표자 보장과 퇴직재원은 정관·보수규정과 함께 검토해야 합니다.",
        ],
    },
    {
        "signal": "연구개발·인증",
        "keywords": ["연구소", "연구개발", "R&D", "특허", "인증", "벤처", "이노비즈", "메인비즈", "기술"],
        "services": ["R&D 지원사업", "정책자금 우대", "연구개발 세액공제"],
        "questions": [
            "전담 연구인력과 연구개발 비용을 별도로 관리하고 계십니까?",
            "보유 특허·인증의 유효기간과 실제 사업 연계성을 확인했습니까?",
            "향후 개발하려는 제품이나 기술의 일정이 있습니까?",
        ],
        "talking_points": [
            "인증 자체보다 기술개발 계획과 매출 연결성이 지원사업 평가에서 중요합니다.",
            "연구개발비는 지원사업과 세액공제의 증빙 기준을 함께 관리하는 것이 좋습니다.",
        ],
    },
]

NEGATIVE_PATTERNS = ["없", "안 해", "안해", "필요 없", "관심 없", "계획 없", "모르", "아니"]
POSITIVE_PATTERNS = ["있", "예정", "필요", "검토", "하려", "생각", "부담", "어렵", "늘", "증가"]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "nan", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _contains(text: str, keywords: Iterable[str]) -> list[str]:
    lowered = text.lower()
    return [keyword for keyword in keywords if keyword.lower() in lowered]


def build_opening_questions(
    recommendations: list[dict[str, Any]],
    memory: dict[str, Any] | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Build a ranked, deduplicated set of opening questions."""
    memory = memory if isinstance(memory, dict) else {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    next_focus = _clean(memory.get("next_focus", ""))
    if next_focus:
        rows.append({
            "topic": "이전 상담 후속",
            "question": next_focus,
            "reason": "기업 메모리에 저장된 다음 상담 우선 확인사항",
            "score": 100,
        })
        seen.add(next_focus)

    for item in recommendations or []:
        if not isinstance(item, dict):
            continue
        topic = _clean(item.get("topic", "")) or "기타"
        score = int(item.get("score", 0) or 0)
        evidence = item.get("evidence", []) or []
        reason = " / ".join(str(x) for x in evidence[:2]) or "기업정보 기반 우선주제"
        for question in item.get("questions", []) or []:
            question = _clean(question)
            if not question or question in seen:
                continue
            seen.add(question)
            rows.append({
                "topic": topic,
                "question": question,
                "reason": reason,
                "score": score,
            })
            break

    rows.sort(key=lambda x: int(x.get("score", 0)), reverse=True)
    return rows[: max(1, int(limit or 5))]


def analyze_representative_answer(
    answer: str,
    current_topic: str = "",
    recommendations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Analyze a representative's answer with transparent deterministic rules."""
    text = _clean(answer)
    if not text:
        return {
            "answer": "",
            "intent": "답변 대기",
            "signals": [],
            "next_questions": [],
            "services": [],
            "talking_points": [],
            "summary": "대표 답변을 입력하면 다음 질문과 제안 순서를 추천합니다.",
            "confidence": 0,
        }

    lowered = text.lower()
    scored: list[dict[str, Any]] = []
    for rule in SCENARIO_SIGNALS:
        matched = _contains(lowered, rule["keywords"])
        if not matched:
            continue
        score = min(55 + len(matched) * 10, 95)
        if current_topic and any(token in current_topic for token in rule["services"] + [rule["signal"]]):
            score = min(score + 8, 98)
        scored.append({**rule, "matched": matched, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)

    negative = any(pattern in lowered for pattern in NEGATIVE_PATTERNS)
    positive = any(pattern in lowered for pattern in POSITIVE_PATTERNS)
    if negative and not positive:
        intent = "현재 계획 없음·소극적"
    elif positive:
        intent = "니즈 또는 실행계획 확인"
    else:
        intent = "추가 확인 필요"

    next_questions: list[str] = []
    services: list[str] = []
    talking_points: list[str] = []
    signals: list[dict[str, Any]] = []

    for item in scored[:3]:
        signals.append({
            "name": item["signal"],
            "score": item["score"],
            "matched": item["matched"],
        })
        for value in item["questions"]:
            if value not in next_questions:
                next_questions.append(value)
        for value in item["services"]:
            if value not in services:
                services.append(value)
        for value in item["talking_points"]:
            if value not in talking_points:
                talking_points.append(value)

    if negative:
        next_questions.insert(0, "현재 필요하지 않다고 판단하신 가장 큰 이유는 무엇입니까?")
        talking_points.insert(0, "당장 실행을 권하기보다, 향후 필요할 때 선택할 수 있도록 요건과 준비자료만 먼저 점검하겠다고 설명하세요.")

    if not scored:
        top_topic = ""
        if recommendations:
            top_topic = _clean((recommendations[0] or {}).get("topic", ""))
            top_questions = (recommendations[0] or {}).get("questions", []) or []
            next_questions.extend(_clean(x) for x in top_questions[:2] if _clean(x))
        next_questions.extend([
            "올해 가장 해결하고 싶은 경영상 문제는 무엇입니까?",
            "향후 12개월 안에 투자·채용·자금조달 계획이 있습니까?",
        ])
        talking_points.append("답변이 포괄적이므로 금액·시기·목적을 나누어 질문하세요.")
        if top_topic:
            services.append(top_topic)

    confidence = max([int(x.get("score", 0)) for x in signals], default=35)
    summary_parts = []
    if signals:
        summary_parts.append("감지된 니즈: " + ", ".join(x["name"] for x in signals))
    summary_parts.append("대표 반응: " + intent)

    return {
        "answer": text,
        "intent": intent,
        "signals": signals,
        "next_questions": list(dict.fromkeys(next_questions))[:6],
        "services": list(dict.fromkeys(services))[:6],
        "talking_points": list(dict.fromkeys(talking_points))[:4],
        "summary": " · ".join(summary_parts),
        "confidence": confidence,
    }


def build_scenario_brief(
    recommendations: list[dict[str, Any]],
    memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    openings = build_opening_questions(recommendations, memory, limit=5)
    topics = [x.get("topic", "") for x in openings if x.get("topic")]
    return {
        "opening_questions": openings,
        "recommended_topics": list(dict.fromkeys(topics)),
        "goal": "대표의 실제 계획·금액·시기를 확인하고 다음 상담 액션을 확정",
    }
