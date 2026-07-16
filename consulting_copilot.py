from __future__ import annotations

import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from matching_preferences import get_matching_preferences
from registered_policy_match import (
    build_customer_labels,
    load_registered_customers,
)
from utils import get_user_cumulative_db_path, get_user_dirs


TOPIC_RULES = [
    {
        "topic": "정책자금",
        "keywords": [
            "운전자금", "시설자금", "기계", "설비", "공장", "증설",
            "차량", "수출", "판로", "연구개발", "R&D", "스마트공장",
            "채용", "고용", "자금부족",
        ],
        "questions": [
            "향후 12개월 내 시설·기계·차량 투자계획이 있습니까?",
            "현재 필요한 자금의 용도와 예상금액은 얼마입니까?",
            "기존 정책자금·보증기관 대출 잔액과 만기는 어떻게 됩니까?",
            "신규채용 또는 고용유지 계획이 있습니까?",
        ],
        "documents": [
            "최근 3개년 재무제표",
            "부가세 과세표준증명",
            "국세·지방세 납세증명",
            "시설·기계 견적서",
            "기존 대출현황",
        ],
    },
    {
        "topic": "가지급금 정리",
        "keywords": [
            "가지급금", "대표자 대여금", "임시인출", "업무무관", "가수금",
        ],
        "questions": [
            "가지급금 발생 원인과 실제 사용처를 확인했습니까?",
            "최근 3년간 가지급금 증감내역이 있습니까?",
            "대표자 상환능력과 배당·급여·퇴직금 활용 가능성을 검토했습니까?",
        ],
        "documents": [
            "가지급금 계정별원장",
            "대표자 거래내역",
            "주주명부",
            "정관",
            "임원보수·퇴직금 규정",
        ],
    },
    {
        "topic": "이익소각·자기주식",
        "keywords": [
            "이익소각", "자기주식", "자사주", "미처분이익잉여금", "배당",
        ],
        "questions": [
            "자기주식 취득 목적과 소각계획이 명확합니까?",
            "배당가능이익과 최근 주식가치를 확인했습니까?",
            "특수관계인 거래와 주주 간 이해관계를 검토했습니까?",
        ],
        "documents": [
            "최근 재무제표",
            "주주명부",
            "정관",
            "법인등기",
            "주식가치 평가자료",
        ],
    },
    {
        "topic": "가업승계",
        "keywords": [
            "가업승계", "상속", "증여", "후계자", "자녀", "승계", "상속세",
        ],
        "questions": [
            "승계 예정자와 희망시기를 확인했습니까?",
            "현재 주식가치와 향후 상승요인을 확인했습니까?",
            "대표자 유고 시 상속세·운영자금 재원을 준비했습니까?",
        ],
        "documents": [
            "주주명부",
            "법인등기",
            "가족관계증명",
            "최근 3개년 재무제표",
            "주식가치 평가자료",
        ],
    },
    {
        "topic": "정관개정",
        "keywords": [
            "정관", "임원퇴직금", "유족보상금", "배당", "주식양도제한",
            "주주총회", "이사회",
        ],
        "questions": [
            "현재 정관의 최종 개정일과 실제 운영규정을 확인했습니까?",
            "임원퇴직금·유족보상금·배당 규정이 목적에 맞게 정비돼 있습니까?",
            "최근 상법·세법 개정사항이 반영돼 있습니까?",
        ],
        "documents": [
            "현행 정관",
            "법인등기",
            "주주명부",
            "최근 주총·이사회 의사록",
            "임원보수 규정",
        ],
    },
    {
        "topic": "법인보험·퇴직재원",
        "keywords": [
            "CEO보험", "경영인정기", "대표자보장", "퇴직재원",
            "상속재원", "유고재원", "법인보험",
        ],
        "questions": [
            "대표자 유고 시 필요한 운영·상속재원 규모는 얼마입니까?",
            "예상 퇴직금과 현재 준비된 재원을 비교했습니까?",
            "보험 목적과 회계·세무처리 방식을 설명했습니까?",
        ],
        "documents": [
            "정관",
            "임원퇴직금 규정",
            "최근 재무제표",
            "기존 보험증권",
            "대표자 보수자료",
        ],
    },
    {
        "topic": "세액공제·경정청구",
        "keywords": [
            "세액공제", "경정청구", "고용세액", "투자세액", "연구개발비",
            "기계투자", "고용증가",
        ],
        "questions": [
            "최근 5개 사업연도 세액공제 적용내역을 확인했습니까?",
            "직원수 증가와 시설투자 내역을 연도별로 확인했습니까?",
            "이미 반영된 공제와 누락 가능성을 구분했습니까?",
        ],
        "documents": [
            "법인세 신고서",
            "세액공제조정명세서",
            "원천세 신고자료",
            "고용보험 가입자명부",
            "유형자산 명세",
        ],
    },
]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _tokens(value: Any) -> set[str]:
    text = re.sub(
        r"[^0-9A-Za-z가-힣]+",
        " ",
        _clean(value).lower(),
    )
    return {
        token
        for token in text.split()
        if len(token) >= 2
    }


def _base_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"]


def _memory_path(user_id: str) -> Path:
    return _base_path(user_id) / "consulting_copilot_memory.json"


def _success_path(user_id: str) -> Path:
    return _base_path(user_id) / "consulting_success_cases.json"


def _checklist_path(user_id: str) -> Path:
    return _base_path(user_id) / "consulting_checklists.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data
    except Exception:
        return default


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def _business_key(company_name: str, business_no: str) -> str:
    digits = re.sub(r"[^0-9]", "", business_no)
    return digits or company_name.strip()


def get_company_memory(
    user_id: str,
    company_name: str,
    business_no: str,
) -> dict[str, Any]:
    data = _load_json(_memory_path(user_id), {})
    if not isinstance(data, dict):
        return {}
    return data.get(
        _business_key(company_name, business_no),
        {},
    ) or {}


def save_company_memory(
    user_id: str,
    company_name: str,
    business_no: str,
    memory: dict[str, Any],
) -> None:
    data = _load_json(_memory_path(user_id), {})
    if not isinstance(data, dict):
        data = {}

    record = dict(memory or {})
    record.update(
        {
            "company_name": company_name,
            "business_no": business_no,
            "updated_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }
    )
    data[
        _business_key(company_name, business_no)
    ] = record
    _save_json(_memory_path(user_id), data)


def load_success_cases(user_id: str) -> list[dict[str, Any]]:
    value = _load_json(_success_path(user_id), [])
    return value if isinstance(value, list) else []


def save_success_case(
    user_id: str,
    case: dict[str, Any],
) -> None:
    cases = load_success_cases(user_id)
    record = dict(case)
    record["case_id"] = datetime.now().strftime(
        "%Y%m%d%H%M%S%f"
    )
    record["saved_at"] = datetime.now().isoformat(
        timespec="seconds"
    )
    cases.insert(0, record)
    _save_json(_success_path(user_id), cases[:1000])


def _customer_text(
    customer: pd.Series,
    preferences: dict[str, Any],
    memory: dict[str, Any],
) -> str:
    fields = [
        _clean(customer.get("업체명", "")),
        _clean(customer.get("업종명", "")),
        _clean(customer.get("사업장 소재지", "")),
        _clean(customer.get("기업규모", "")),
        _clean(customer.get("매출액", "")),
        _clean(customer.get("종업원수", "")),
        ", ".join(preferences.get("매칭키워드", []) or []),
        ", ".join(preferences.get("관심지원분야", []) or []),
        _clean(preferences.get("자금사용목적", "")),
        _clean(memory.get("key_needs", "")),
        _clean(memory.get("consultant_notes", "")),
        _clean(memory.get("next_focus", "")),
    ]
    return " ".join(field for field in fields if field)


def _case_similarity(
    current_text: str,
    case: dict[str, Any],
) -> float:
    current = _tokens(current_text)
    case_text = " ".join(
        _clean(case.get(field, ""))
        for field in [
            "industry",
            "company_profile",
            "consulting_topic",
            "trigger_keywords",
            "result_summary",
        ]
    )
    other = _tokens(case_text)

    if not current or not other:
        return 0.0

    intersection = len(current & other)
    union = len(current | other)
    jaccard = intersection / union if union else 0

    topic_bonus = 0.0
    for keyword in _tokens(case.get("trigger_keywords", "")):
        if keyword in current:
            topic_bonus += 0.04

    return min(jaccard + topic_bonus, 1.0)


def find_similar_success_cases(
    user_id: str,
    current_text: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    scored = []
    for case in load_success_cases(user_id):
        similarity = _case_similarity(current_text, case)
        if similarity <= 0:
            continue
        scored.append(
            {
                **case,
                "similarity": round(similarity * 100),
            }
        )

    scored.sort(
        key=lambda item: item["similarity"],
        reverse=True,
    )
    return scored[:limit]


def build_topic_recommendations(
    customer: pd.Series,
    preferences: dict[str, Any],
    memory: dict[str, Any],
) -> list[dict[str, Any]]:
    text = _customer_text(
        customer,
        preferences,
        memory,
    )
    text_tokens = _tokens(text)
    results = []

    for rule in TOPIC_RULES:
        matched = []
        for keyword in rule["keywords"]:
            keyword_tokens = _tokens(keyword)
            if keyword_tokens and keyword_tokens & text_tokens:
                matched.append(keyword)

        score = 25
        score += min(len(matched) * 13, 55)

        if rule["topic"] in (
            preferences.get("관심지원분야", [])
            or []
        ):
            score += 15

        if rule["topic"] in _clean(
            memory.get("next_focus", "")
        ):
            score += 15

        score = min(score, 100)

        results.append(
            {
                "topic": rule["topic"],
                "score": score,
                "matched": list(dict.fromkeys(matched)),
                "questions": rule["questions"],
                "documents": rule["documents"],
            }
        )

    results.sort(
        key=lambda item: item["score"],
        reverse=True,
    )
    return results


def _load_checklist(
    user_id: str,
    business_key: str,
) -> dict[str, bool]:
    data = _load_json(_checklist_path(user_id), {})
    if not isinstance(data, dict):
        return {}
    value = data.get(business_key, {})
    return value if isinstance(value, dict) else {}


def _save_checklist(
    user_id: str,
    business_key: str,
    checklist: dict[str, bool],
) -> None:
    data = _load_json(_checklist_path(user_id), {})
    if not isinstance(data, dict):
        data = {}
    data[business_key] = checklist
    _save_json(_checklist_path(user_id), data)


def render_copilot_page(
    user_id: str,
    user_name: str,
) -> None:
    st.markdown("## AI 컨설팅 코파일럿")
    st.caption(
        "오아시스 내부 직원이 고객별 상담목표·필수질문·필요서류·"
        "누락사항·유사 성공사례를 한 화면에서 확인합니다."
    )

    customers = load_registered_customers(
        get_user_cumulative_db_path(user_id)
    )
    if customers.empty:
        st.info(
            "등록 고객이 없습니다. 크레탑 자동등록으로 고객을 먼저 등록해주세요."
        )
        return

    labels, row_map = build_customer_labels(customers)

    prefill_business_no = str(
        st.session_state.pop("_oasis_copilot_business_no", "") or ""
    )
    prefill_company_name = str(
        st.session_state.pop("_oasis_copilot_company_name", "") or ""
    )
    if prefill_business_no or prefill_company_name:
        normalized_prefill = re.sub(r"[^0-9]", "", prefill_business_no)
        for candidate_label, candidate_index in row_map.items():
            candidate = customers.loc[candidate_index]
            candidate_business = re.sub(
                r"[^0-9]",
                "",
                str(candidate.get("사업자등록번호", "") or ""),
            )
            candidate_name = _clean(candidate.get("업체명", ""))
            if (
                normalized_prefill
                and candidate_business == normalized_prefill
            ) or (
                prefill_company_name
                and candidate_name == prefill_company_name
            ):
                st.session_state["copilot_customer"] = candidate_label
                break

    if st.session_state.get("copilot_customer") not in labels:
        st.session_state.pop("copilot_customer", None)

    selected_label = st.selectbox(
        "상담할 기업",
        labels,
        key="copilot_customer",
    )
    customer = customers.loc[row_map[selected_label]]

    company_name = _clean(customer.get("업체명", ""))
    business_no = _clean(
        customer.get("사업자등록번호", "")
    )
    business_key = _business_key(
        company_name,
        business_no,
    )

    preferences = get_matching_preferences(
        user_id,
        business_no,
    )
    memory = get_company_memory(
        user_id,
        company_name,
        business_no,
    )

    recommendations = build_topic_recommendations(
        customer,
        preferences,
        memory,
    )

    current_text = _customer_text(
        customer,
        preferences,
        memory,
    )
    similar_cases = find_similar_success_cases(
        user_id,
        current_text,
    )

    st.markdown(
        f"""
        <div style="
            padding:20px 24px;
            border-radius:18px;
            background:linear-gradient(135deg,#172554,#2563eb);
            color:white;
            margin:8px 0 16px 0;
        ">
            <div style="font-size:1.45rem;font-weight:800;">
                {company_name or '기업명 미확인'}
            </div>
            <div style="margin-top:6px;opacity:.9;">
                사업자번호 {business_no or '-'} · 담당 {user_name or '-'}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    top = recommendations[:5]
    st.markdown("### 이번 상담 우선순위")

    columns = st.columns(min(len(top), 5))
    for index, item in enumerate(top):
        with columns[index]:
            st.metric(
                item["topic"],
                f"{item['score']}점",
            )
            if item["matched"]:
                st.caption(
                    "근거: "
                    + ", ".join(item["matched"][:3])
                )
            else:
                st.caption("기본 확인 필요")

    tab_playbook, tab_memory, tab_success, tab_review = st.tabs(
        [
            "상담 플레이북",
            "기업 메모리",
            "성공사례",
            "미팅 종료점검",
        ]
    )

    with tab_playbook:
        checklist = _load_checklist(
            user_id,
            business_key,
        )

        selected_topics = st.multiselect(
            "이번 상담에서 다룰 주제",
            [item["topic"] for item in recommendations],
            default=[
                item["topic"]
                for item in recommendations[:3]
            ],
            key=f"copilot_topics_{business_key}",
        )

        question_items = []
        document_items = []

        for item in recommendations:
            if item["topic"] not in selected_topics:
                continue
            for question in item["questions"]:
                question_items.append(
                    (item["topic"], question)
                )
            for document in item["documents"]:
                document_items.append(
                    (item["topic"], document)
                )

        st.markdown("#### 필수 질문 체크리스트")
        updated = {}
        completed = 0

        for topic, question in question_items:
            key = f"Q|{topic}|{question}"
            checked = st.checkbox(
                f"[{topic}] {question}",
                value=bool(checklist.get(key, False)),
                key=f"copilot_{business_key}_{abs(hash(key))}",
            )
            updated[key] = checked
            completed += int(checked)

        total = len(question_items)
        progress = (
            completed / total
            if total
            else 0
        )
        st.progress(progress)
        st.caption(
            f"질문 진행률 {completed}/{total} "
            f"({progress * 100:.0f}%)"
        )

        st.markdown("#### 요청서류 체크리스트")
        for topic, document in list(
            dict.fromkeys(document_items)
        ):
            key = f"D|{topic}|{document}"
            checked = st.checkbox(
                f"[{topic}] {document}",
                value=bool(checklist.get(key, False)),
                key=f"copilot_{business_key}_{abs(hash(key))}",
            )
            updated[key] = checked

        if st.button(
            "체크리스트 저장",
            type="primary",
            use_container_width=True,
            key=f"save_copilot_checklist_{business_key}",
        ):
            _save_checklist(
                user_id,
                business_key,
                updated,
            )
            st.success("상담 체크리스트를 저장했습니다.")

    with tab_memory:
        st.info(
            "기업별 메모리는 다음 상담에서 우선 질문과 추천순서를 만드는 데 사용됩니다."
        )

        key_needs = st.text_area(
            "핵심 니즈",
            value=_clean(memory.get("key_needs", "")),
            height=110,
            key=f"memory_needs_{business_key}",
        )
        decision_style = st.selectbox(
            "의사결정 성향",
            [
                "미확인",
                "빠른 편",
                "신중한 편",
                "자료 중심",
                "가격 민감",
                "관계 중심",
            ],
            index=(
                [
                    "미확인",
                    "빠른 편",
                    "신중한 편",
                    "자료 중심",
                    "가격 민감",
                    "관계 중심",
                ].index(
                    memory.get(
                        "decision_style",
                        "미확인",
                    )
                )
                if memory.get(
                    "decision_style",
                    "미확인",
                )
                in [
                    "미확인",
                    "빠른 편",
                    "신중한 편",
                    "자료 중심",
                    "가격 민감",
                    "관계 중심",
                ]
                else 0
            ),
            key=f"memory_style_{business_key}",
        )
        positive_topics = st.text_input(
            "반응이 좋았던 주제",
            value=_clean(
                memory.get("positive_topics", "")
            ),
            key=f"memory_positive_{business_key}",
        )
        resistance_topics = st.text_input(
            "거부감·주의 주제",
            value=_clean(
                memory.get("resistance_topics", "")
            ),
            key=f"memory_resistance_{business_key}",
        )
        next_focus = st.text_area(
            "다음 상담의 우선 확인사항",
            value=_clean(memory.get("next_focus", "")),
            height=110,
            key=f"memory_next_{business_key}",
        )
        consultant_notes = st.text_area(
            "내부 컨설턴트 메모",
            value=_clean(
                memory.get("consultant_notes", "")
            ),
            height=130,
            key=f"memory_notes_{business_key}",
        )

        if st.button(
            "기업 메모리 저장",
            use_container_width=True,
            key=f"save_memory_{business_key}",
        ):
            save_company_memory(
                user_id,
                company_name,
                business_no,
                {
                    "key_needs": key_needs,
                    "decision_style": decision_style,
                    "positive_topics": positive_topics,
                    "resistance_topics": resistance_topics,
                    "next_focus": next_focus,
                    "consultant_notes": consultant_notes,
                },
            )
            st.success(
                "기업 메모리를 저장했습니다. 다음 상담 추천에 반영됩니다."
            )

    with tab_success:
        st.markdown("#### 유사한 내부 성공사례")
        if similar_cases:
            for case in similar_cases:
                with st.expander(
                    f"{case.get('consulting_topic', '성공사례')} "
                    f"· 유사도 {case['similarity']}%",
                    expanded=False,
                ):
                    st.write(
                        f"**업종:** {case.get('industry', '-')}"
                    )
                    st.write(
                        f"**기업 특성:** {case.get('company_profile', '-')}"
                    )
                    st.write(
                        f"**성공요인:** {case.get('success_factors', '-')}"
                    )
                    st.write(
                        f"**결과:** {case.get('result_summary', '-')}"
                    )
                    st.write(
                        f"**추천 질문:** {case.get('best_questions', '-')}"
                    )
        else:
            st.info(
                "등록된 성공사례가 없습니다. 아래에서 첫 사례를 등록해주세요."
            )

        st.markdown("#### 성공사례 등록")
        s1, s2 = st.columns(2)
        with s1:
            case_industry = st.text_input(
                "업종",
                value=_clean(customer.get("업종명", "")),
                key=f"case_industry_{business_key}",
            )
            consulting_topic = st.text_input(
                "계약·성공 주제",
                key=f"case_topic_{business_key}",
            )
            trigger_keywords = st.text_input(
                "핵심 키워드",
                placeholder="기계투자, 신규채용, 가지급금 등",
                key=f"case_keywords_{business_key}",
            )
        with s2:
            company_profile = st.text_area(
                "기업 특성",
                height=90,
                key=f"case_profile_{business_key}",
            )
            success_factors = st.text_area(
                "성공요인",
                height=90,
                key=f"case_factors_{business_key}",
            )

        result_summary = st.text_area(
            "결과 요약",
            height=90,
            key=f"case_result_{business_key}",
        )
        best_questions = st.text_area(
            "효과적이었던 질문·설명 순서",
            height=90,
            key=f"case_questions_{business_key}",
        )

        if st.button(
            "성공사례 저장",
            use_container_width=True,
            key=f"save_success_case_{business_key}",
        ):
            save_success_case(
                user_id,
                {
                    "source_company_name": company_name,
                    "industry": case_industry,
                    "company_profile": company_profile,
                    "consulting_topic": consulting_topic,
                    "trigger_keywords": trigger_keywords,
                    "success_factors": success_factors,
                    "result_summary": result_summary,
                    "best_questions": best_questions,
                },
            )
            st.success(
                "내부 성공사례를 저장했습니다. 이후 유사 기업 추천에 사용됩니다."
            )
            st.rerun()

    with tab_review:
        checklist = _load_checklist(
            user_id,
            business_key,
        )
        selected_topics = [
            item["topic"]
            for item in recommendations[:3]
        ]

        required_questions = []
        for item in recommendations:
            if item["topic"] in selected_topics:
                required_questions.extend(
                    [
                        (item["topic"], question)
                        for question in item["questions"]
                    ]
                )

        missed = []
        completed = []

        for topic, question in required_questions:
            key = f"Q|{topic}|{question}"
            if checklist.get(key, False):
                completed.append(
                    f"[{topic}] {question}"
                )
            else:
                missed.append(
                    f"[{topic}] {question}"
                )

        total = len(required_questions)
        score = round(
            len(completed) / total * 100
        ) if total else 0

        m1, m2, m3 = st.columns(3)
        m1.metric("상담 완성도", f"{score}점")
        m2.metric("완료 질문", f"{len(completed)}개")
        m3.metric("누락 질문", f"{len(missed)}개")

        if missed:
            st.warning("다음 상담에서 확인할 누락사항")
            for item in missed:
                st.write(f"- {item}")
        else:
            st.success(
                "우선 상담주제의 필수질문을 모두 확인했습니다."
            )

        follow_up = "\n".join(
            f"- {item}"
            for item in missed[:7]
        )
        st.text_area(
            "다음 상담 TODO",
            value=follow_up,
            height=180,
            key=f"copilot_followup_{business_key}",
        )
