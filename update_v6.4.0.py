from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.4.0"
TARGETS = [
    "consultation_journal.py",
    "enterprise_center.py",
    "consulting_report.py",
    "VERSION.txt",
]


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        fail(f"Patch point not found: {label}")
    return text.replace(old, new, 1)


def main() -> None:
    root = Path.cwd()
    if not (root / "consultation_journal.py").exists():
        fail("Run this file from the OASIS project root folder.")

    version_path = root / "VERSION.txt"
    current_version = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists()
        else ""
    )
    if current_version and current_version not in {
        "v6.3.3", "6.3.3", "v6.4.0", "6.4.0"
    }:
        fail(f"Expected v6.3.3 but found {current_version}.")

    backup = root / "_oasis_backups" / (
        "before_v6.4.0_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)

    for name in TARGETS:
        src = root / name
        if src.exists():
            shutil.copy2(src, backup / name)

    path = root / "consultation_journal.py"
    text = path.read_text(encoding="utf-8")

    old = '''def _policy_signal(journal: dict[str, Any]) -> dict[str, Any]:
    value = journal.get(
        "policy_fund_recommendation",
        {},
    )
    return value if isinstance(value, dict) else {}
'''
    new = '''_POLICY_KEYWORD_RULES = {
    "운전자금": ["운전자금", "운영자금", "원재료", "인건비", "매입자금", "매출채권"],
    "시설자금": ["시설자금", "시설투자", "공장신축", "공장증축"],
    "기계·설비 구입": ["기계", "설비", "장비", "생산라인", "자동화설비"],
    "차량 구입": ["차량", "화물차", "트럭", "영업용차량"],
    "수출": ["수출", "해외진출", "해외판로", "바이어"],
    "연구개발": ["연구개발", "R&D", "연구소", "기업부설연구소"],
    "특허·인증": ["특허", "상표", "벤처", "이노비즈", "메인비즈", "인증"],
    "신규채용": ["신규채용", "채용계획", "직원채용", "인원충원"],
    "고용유지": ["고용유지", "고용안정", "휴업", "근로시간단축"],
    "창업·사업화": ["창업", "사업화", "신사업", "신제품"],
    "온라인 마케팅": ["온라인마케팅", "온라인 마케팅", "광고", "홍보"],
    "판로·유통": ["판로", "유통", "입점", "납품", "거래처확대"],
}


def _journal_search_text(journal: dict[str, Any]) -> str:
    values = [
        journal.get("summary", ""),
        journal.get("consultation_title", ""),
        journal.get("transcript", ""),
        journal.get("crm_memo", ""),
        journal.get("client_needs", []),
        journal.get("key_discussions", []),
        journal.get("recommended_solutions", []),
        journal.get("next_actions", []),
    ]
    return json.dumps(values, ensure_ascii=False).lower()


def _fallback_policy_signal(journal: dict[str, Any]) -> dict[str, Any]:
    text = _journal_search_text(journal)
    keywords: list[str] = []
    interests: list[str] = []

    for interest, aliases in _POLICY_KEYWORD_RULES.items():
        matched = [alias for alias in aliases if alias.lower() in text]
        if not matched:
            continue
        if interest not in interests:
            interests.append(interest)
        for alias in matched:
            if alias not in keywords:
                keywords.append(alias)

    policy_terms = [
        "정책자금", "중진공", "소진공", "신용보증기금", "기술보증기금",
        "지역신보", "보증서", "융자", "대출", "지원금", "정부지원",
    ]
    policy_hits = [term for term in policy_terms if term.lower() in text]
    for term in policy_hits:
        if term not in keywords:
            keywords.append(term)

    eligible = bool(interests or policy_hits)
    confidence = min(95, 55 + len(interests) * 8 + len(policy_hits) * 5) if eligible else 0
    return {
        "eligible": eligible,
        "confidence": confidence,
        "matching_keywords": keywords,
        "interest_fields": interests,
        "source": "rule_fallback",
    }


def _policy_signal(journal: dict[str, Any]) -> dict[str, Any]:
    value = journal.get("policy_fund_recommendation", {})
    if isinstance(value, dict):
        has_keywords = bool(value.get("matching_keywords") or value.get("interest_fields"))
        if bool(value.get("eligible", False)) and has_keywords:
            return value

    fallback = _fallback_policy_signal(journal)
    if isinstance(value, dict) and value:
        merged = dict(value)
        if not merged.get("matching_keywords"):
            merged["matching_keywords"] = fallback.get("matching_keywords", [])
        if not merged.get("interest_fields"):
            merged["interest_fields"] = fallback.get("interest_fields", [])
        if not merged.get("eligible"):
            merged["eligible"] = fallback.get("eligible", False)
        if int(merged.get("confidence", 0) or 0) < int(fallback.get("confidence", 0) or 0):
            merged["confidence"] = fallback.get("confidence", 0)
        return merged
    return fallback
'''
    text = replace_once(text, old, new, "policy signal fallback")

    text = replace_once(
        text,
        '''    if bool(record.get("_auto_policy_keywords", False)):
        try:
            policy_result = merge_policy_matching_preferences(
''',
        '''    # v6.4.0: 상담일지 저장 시 정책자금 신호가 있으면 항상 자동 반영
    try:
        policy_result = merge_policy_matching_preferences(
''',
        "always apply policy keywords",
    )

    text = replace_once(
        text,
        '''                record,
            )
        except Exception as exc:
            policy_result = {
                "updated": False,
                "message": (
                    "상담일지는 저장했지만 정책자금 키워드 자동반영 중 "
                    f"오류가 발생했습니다: {exc}"
                ),
            }

    message = "상담일지와 CRM 내용을 저장했습니다."
''',
        '''            record,
        )
    except Exception as exc:
        policy_result = {
            "updated": False,
            "message": (
                "상담일지는 저장했지만 정책자금 키워드 자동반영 중 "
                f"오류가 발생했습니다: {exc}"
            ),
        }

    message = "상담일지·CRM·기업히스토리를 저장했습니다."
''',
        "policy try block",
    )

    text = replace_once(
        text,
        '''    append_timeline_event(
        user_id,
        customer_key,
        title,
        _journal_to_timeline_detail(record),
    )
''',
        '''    history_title = f"{str(record.get('saved_at', ''))[:10]} 상담 · {title}"
    append_timeline_event(
        user_id,
        customer_key,
        history_title,
        _journal_to_timeline_detail(record),
    )
''',
        "history title",
    )

    context_block = '''

def get_company_consultation_context(
    user_id: str,
    business_no: str,
    company_name: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    # AI 종합진단에 사용할 기업별 상담 맥락
    normalized = normalize_business_no(business_no)
    journals = _load_journals(user_id)
    matched = []

    for item in journals:
        if not isinstance(item, dict):
            continue
        item_no = normalize_business_no(item.get("business_no", ""))
        item_company = str(item.get("company_name", "") or "").strip()
        if normalized and item_no == normalized:
            matched.append(item)
        elif not normalized and company_name and item_company == company_name.strip():
            matched.append(item)

    matched = sorted(
        matched,
        key=lambda row: str(row.get("saved_at", "")),
        reverse=True,
    )[:max(1, int(limit))]

    def unique(values: list[Any]) -> list[str]:
        result: list[str] = []
        for value in values:
            source = value if isinstance(value, list) else [value]
            for item in source:
                cleaned = str(item or "").strip()
                if cleaned and cleaned not in result:
                    result.append(cleaned)
        return result

    if not matched:
        return {
            "count": 0,
            "latest_saved_at": "",
            "latest_summary": "",
            "matching_keywords": [],
            "interest_fields": [],
            "client_needs": [],
            "key_discussions": [],
            "next_actions": [],
            "journals": [],
        }

    keywords, interests, needs, discussions, actions = [], [], [], [], []
    for item in matched:
        signal = _policy_signal(item)
        keywords.extend(_normalize_keyword_list(signal.get("matching_keywords", [])))
        interests.extend(_normalize_interest_fields(signal.get("interest_fields", [])))
        needs.append(item.get("client_needs", []))
        discussions.append(item.get("key_discussions", []))
        actions.append(item.get("next_actions", []))

    latest = matched[0]
    return {
        "count": len(matched),
        "latest_saved_at": str(latest.get("saved_at", "") or ""),
        "latest_summary": str(latest.get("summary", "") or ""),
        "matching_keywords": unique(keywords),
        "interest_fields": unique(interests),
        "client_needs": unique(needs),
        "key_discussions": unique(discussions),
        "next_actions": unique(actions),
        "journals": matched,
    }
'''
    marker = "\ndef render_saved_consultation_journals(\n"
    if marker not in text:
        fail("Patch point not found: consultation context insertion")
    text = text.replace(marker, context_block + marker, 1)
    path.write_text(text, encoding="utf-8", newline="\n")

    path = root / "enterprise_center.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''from consultation_journal import (
    render_audio_consultation_journal,
    render_saved_consultation_journals,
)
''',
        '''from consultation_journal import (
    get_company_consultation_context,
    render_audio_consultation_journal,
    render_saved_consultation_journals,
)
''',
        "enterprise import",
    )
    text = replace_once(
        text,
        '''        consulting_analysis = build_consulting_analysis(
            selected_row,
            financial,
            registry,
            stock,
            preferences,
        )
''',
        '''        consultation_context = get_company_consultation_context(
            user_id=user_id,
            business_no=business_no,
            company_name=company_name,
        )
        consulting_analysis = build_consulting_analysis(
            selected_row,
            financial,
            registry,
            stock,
            preferences,
            consultation_context=consultation_context,
        )
''',
        "AI diagnosis consultation input",
    )
    path.write_text(text, encoding="utf-8", newline="\n")

    path = root / "consulting_report.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''def build_consulting_analysis(
    customer: pd.Series,
    financial: dict[str, Any],
    registry: dict[str, Any],
    stock_record: dict[str, Any],
    preferences: dict[str, Any],
) -> dict[str, Any]:
''',
        '''def build_consulting_analysis(
    customer: pd.Series,
    financial: dict[str, Any],
    registry: dict[str, Any],
    stock_record: dict[str, Any],
    preferences: dict[str, Any],
    consultation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
''',
        "consulting signature",
    )
    text = replace_once(
        text,
        '''    strengths: list[str] = []
    cautions: list[str] = []
    strategy: list[str] = []
    questions: list[str] = []
''',
        '''    strengths: list[str] = []
    cautions: list[str] = []
    strategy: list[str] = []
    questions: list[str] = []

    consultation_context = (
        consultation_context if isinstance(consultation_context, dict) else {}
    )
    consultation_count = int(consultation_context.get("count", 0) or 0)
    latest_consultation = _clean(consultation_context.get("latest_saved_at", ""))
    latest_summary = _clean(consultation_context.get("latest_summary", ""))
    consultation_keywords = consultation_context.get("matching_keywords", []) or []
    consultation_interests = consultation_context.get("interest_fields", []) or []
    consultation_needs = consultation_context.get("client_needs", []) or []
    consultation_actions = consultation_context.get("next_actions", []) or []
''',
        "consultation variables",
    )
    text = replace_once(
        text,
        '''    if not strategy:
        strategy.append(
            "운전자금·시설투자·채용계획을 확인한 뒤 지원사업 우선순위를 정하는 것이 좋습니다."
        )

    questions.extend([
''',
        '''    if consultation_count:
        strengths.append(
            f"최근 상담이 {consultation_count}건 축적되어 있으며 "
            f"최신 상담일은 {latest_consultation[:10] or '미확인'}입니다."
        )
        if latest_summary:
            strategy.append("최근 상담 요약 반영: " + latest_summary[:220])
        if consultation_keywords:
            strategy.append(
                "상담 기반 정책자금 키워드: "
                + ", ".join(str(item) for item in consultation_keywords[:12])
            )
        if consultation_interests:
            strategy.append(
                "상담 기반 관심지원분야: "
                + ", ".join(str(item) for item in consultation_interests[:8])
            )
        if consultation_needs:
            questions.append(
                "최근 상담에서 확인된 고객 니즈가 현재도 유효한지 확인해 주세요: "
                + ", ".join(str(item) for item in consultation_needs[:5])
            )
        if consultation_actions:
            strategy.append(
                "상담 후속조치: "
                + ", ".join(str(item) for item in consultation_actions[:5])
            )
    else:
        cautions.append(
            "저장된 상담일지가 없어 크레탑·등기·주가자료 중심으로만 진단했습니다."
        )

    if registry:
        strengths.append("등기정보가 종합진단에 반영되었습니다.")
    else:
        cautions.append("등기정보가 없어 지배구조·자본정보 진단이 제한됩니다.")

    if stock_record:
        strengths.append("최신 주가평가 결과가 종합진단에 반영되었습니다.")
    else:
        cautions.append("저장된 주가평가 결과가 없어 기업가치 진단이 제한됩니다.")

    if not strategy:
        strategy.append(
            "운전자금·시설투자·채용계획을 확인한 뒤 지원사업 우선순위를 정하는 것이 좋습니다."
        )

    questions.extend([
''',
        "consultation diagnosis logic",
    )
    text = replace_once(
        text,
        '''        "stock_summary": stock_summary,
''',
        '''        "stock_summary": stock_summary,
        "consultation_context": consultation_context,
        "data_sources": {
            "cretop": bool(len(customer.index)),
            "financial": bool(financial),
            "registry": bool(registry),
            "stock": bool(stock_record),
            "consultation": bool(consultation_count),
            "matching_preferences": bool(preferences),
        },
''',
        "diagnosis return sources",
    )
    path.write_text(text, encoding="utf-8", newline="\n")

    version_path.write_text(VERSION + "\n", encoding="utf-8")
    changelog_src = root / "payload" / "CHANGELOG_v6.4.0.md"
    if changelog_src.exists():
        shutil.copy2(changelog_src, root / "CHANGELOG_v6.4.0.md")

    import py_compile
    for name in ["consultation_journal.py", "enterprise_center.py", "consulting_report.py"]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("GIT=See README_v6.4.0_apply.md")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
