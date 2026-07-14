from __future__ import annotations

from typing import Any

from consultation_journal import get_company_consultation_context
from customer_history import save_customer_event
from matching_preferences import (
    get_matching_preferences,
    save_matching_preferences,
    split_keywords,
)


def _unique(values: Any) -> list[str]:
    source = values if isinstance(values, list) else split_keywords(values)
    result: list[str] = []
    seen: set[str] = set()
    for value in source:
        item = str(value or "").strip()
        key = item.lower()
        if not item or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def reconcile_enterprise_consulting_context(
    user_id: str,
    business_no: str,
    company_name: str,
) -> dict[str, Any]:
    context = get_company_consultation_context(
        user_id=user_id,
        business_no=business_no,
        company_name=company_name,
        limit=30,
    )
    preferences = get_matching_preferences(user_id, business_no)
    if not isinstance(preferences, dict):
        preferences = {}

    current_keywords = _unique(preferences.get("매칭키워드", []))
    current_interests = _unique(preferences.get("관심지원분야", []))
    exclusions = _unique(preferences.get("제외키워드", []))

    derived_keywords = _unique(context.get("matching_keywords", []))
    derived_interests = _unique(context.get("interest_fields", []))

    keyword_keys = {item.lower() for item in current_keywords}
    added_keywords: list[str] = []
    for item in derived_keywords:
        if item.lower() in keyword_keys:
            continue
        current_keywords.append(item)
        keyword_keys.add(item.lower())
        added_keywords.append(item)

    added_interests: list[str] = []
    for item in derived_interests:
        if item in current_interests:
            continue
        current_interests.append(item)
        added_interests.append(item)

    if added_keywords or added_interests:
        preferences = save_matching_preferences(
            user_id=user_id,
            business_no=business_no,
            company_name=company_name,
            matching_keywords=current_keywords,
            interest_fields=current_interests,
            exclusion_keywords=exclusions,
            fund_purpose=str(preferences.get("자금사용목적", "") or ""),
            planned_amount=str(preferences.get("투자예정금액", "") or ""),
            planned_timing=str(preferences.get("투자예정시기", "") or ""),
        )
    else:
        preferences = get_matching_preferences(user_id, business_no)

    history_checked = 0
    for journal in context.get("journals", []) or []:
        if not isinstance(journal, dict):
            continue
        journal_id = str(journal.get("journal_id", "") or "").strip()
        if not journal_id:
            continue

        title = str(journal.get("consultation_title", "") or "녹음 상담일지")
        saved_at = str(journal.get("saved_at", "") or "")
        summary = str(journal.get("summary", "") or "")
        needs = ", ".join(str(v) for v in journal.get("client_needs", []) or [])
        discussions = ", ".join(
            str(v) for v in journal.get("key_discussions", []) or []
        )
        actions = ", ".join(str(v) for v in journal.get("next_actions", []) or [])

        detail_parts = [
            f"상담요약: {summary}" if summary else "",
            f"고객 니즈: {needs}" if needs else "",
            f"주요 논의: {discussions}" if discussions else "",
            (
                "정책자금 키워드: " + ", ".join(derived_keywords)
                if derived_keywords
                else ""
            ),
            f"다음 액션: {actions}" if actions else "",
        ]
        save_customer_event(
            user_id=user_id,
            business_no=business_no,
            company_name=company_name,
            event_id=journal_id,
            event_title=f"{saved_at[:10]} 상담 · {title}",
            event_detail="\n".join(part for part in detail_parts if part),
            occurred_at=saved_at,
            source="consultation",
        )
        history_checked += 1

    return {
        "consultation_context": context,
        "preferences": preferences,
        "added_keywords": added_keywords,
        "added_interests": added_interests,
        "history_checked": history_checked,
    }
