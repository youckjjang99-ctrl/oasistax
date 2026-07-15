from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st

from articles_pdf import (
    build_comparison_pdf,
    build_revised_articles_pdf,
    build_review_report_pdf,
)
from cloud_db import CloudDatabase, cloud_is_configured
from customer_history import save_customer_event
from utils import ROOT_DIR, get_user_dirs

TEMPLATE_PATH = ROOT_DIR / "data" / "articles_amendment_templates.json"
TABLE_ARTICLES_VERSIONS = "oasis_articles_versions"


def _normalize_business_no(value: Any) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def _safe_key(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_-]", "_", str(value or ""))


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _versions_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "articles_versions.json"


def _templates() -> dict[str, Any]:
    data = _load_json(TEMPLATE_PATH, {})
    return data if isinstance(data, dict) else {}


def _company_key(business_no: str, company_name: str) -> str:
    return _normalize_business_no(business_no) or company_name


def _clause_map(review: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id", "")): item
        for item in review.get("items", []) or []
        if isinstance(item, dict)
    }


def build_suggestions(
    review: dict[str, Any],
    profile_name: str,
) -> list[dict[str, Any]]:
    library = _templates()
    profile = library.get("profiles", {}).get(profile_name, {})
    recommended = profile.get("recommended", []) or []
    clauses = library.get("clauses", {}) or {}
    review_map = _clause_map(review)

    suggestions = []
    for clause_id in recommended:
        template = clauses.get(clause_id, {})
        if not template:
            continue
        current = review_map.get(clause_id, {})
        status = str(current.get("status", "미확인") or "미확인")
        suggestions.append(
            {
                "id": clause_id,
                "title": template.get("title", current.get("title", clause_id)),
                "category": template.get("category", current.get("category", "")),
                "status": status,
                "before": current.get("excerpt", "") or "관련 조항 미확인",
                "after": template.get("draft", ""),
                "reason": template.get("reason", ""),
                "checks": template.get("checks", []) or [],
                "decision": "적용" if status != "충분" else "유지",
            }
        )
    return suggestions


def compose_revised_text(
    original_text: str,
    suggestions: list[dict[str, Any]],
) -> str:
    applied = [
        item for item in suggestions
        if item.get("decision") == "적용"
        and str(item.get("after", "")).strip()
    ]
    if not applied:
        return original_text.strip()

    section = ["", "", "정관 개정 권고 조항", ""]
    for index, item in enumerate(applied, start=1):
        section.append(
            f"[개정 권고 {index}] {item.get('title', '')}"
        )
        section.append(str(item.get("after", "")).strip())
        section.append("")

    original = original_text.strip()
    match = re.search(r"\n\s*부\s*칙", original)
    if match:
        return (
            original[: match.start()].rstrip()
            + "\n"
            + "\n".join(section)
            + "\n"
            + original[match.start():].lstrip()
        )
    return original + "\n" + "\n".join(section)


def _load_local_versions(
    user_id: str,
    business_no: str,
    company_name: str,
) -> list[dict[str, Any]]:
    data = _load_json(_versions_path(user_id), {})
    key = _company_key(business_no, company_name)
    values = data.get(key, []) if isinstance(data, dict) else []
    return values if isinstance(values, list) else []


def _save_local_version(
    user_id: str,
    business_no: str,
    company_name: str,
    record: dict[str, Any],
) -> None:
    path = _versions_path(user_id)
    data = _load_json(path, {})
    if not isinstance(data, dict):
        data = {}
    key = _company_key(business_no, company_name)
    items = data.get(key, [])
    if not isinstance(items, list):
        items = []
    items.insert(0, record)
    data[key] = items[:50]
    _save_json(path, data)


def _cloud_save(record: dict[str, Any]) -> str:
    if not cloud_is_configured():
        return "로컬 저장"
    try:
        CloudDatabase().upsert(
            TABLE_ARTICLES_VERSIONS,
            [
                {
                    "version_id": record["version_id"],
                    "owner_user_id": record["owner_user_id"],
                    "business_no": record["business_no"],
                    "company_name": record["company_name"],
                    "profile_name": record["profile_name"],
                    "version_name": record["version_name"],
                    "status": record["status"],
                    "original_text": record["original_text"],
                    "final_text": record["final_text"],
                    "comparison_data": record["comparisons"],
                    "created_at": record["created_at"],
                }
            ],
            "version_id",
        )
        return "로컬·Supabase 저장"
    except Exception as exc:
        return f"로컬 저장(Supabase 동기화 보류: {exc})"


def save_version(
    user_id: str,
    business_no: str,
    company_name: str,
    profile_name: str,
    version_name: str,
    status: str,
    original_text: str,
    final_text: str,
    comparisons: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    created_at = datetime.now().isoformat(timespec="seconds")
    digest = hashlib.sha256(
        (
            user_id
            + "|"
            + _normalize_business_no(business_no)
            + "|"
            + created_at
            + "|"
            + final_text[:300]
        ).encode("utf-8")
    ).hexdigest()[:24]
    record = {
        "version_id": digest,
        "owner_user_id": user_id,
        "business_no": business_no,
        "company_name": company_name,
        "profile_name": profile_name,
        "version_name": version_name,
        "status": status,
        "original_text": original_text,
        "final_text": final_text,
        "comparisons": comparisons,
        "created_at": created_at,
    }
    _save_local_version(
        user_id,
        business_no,
        company_name,
        record,
    )
    message = _cloud_save(record)
    save_customer_event(
        user_id=user_id,
        business_no=business_no,
        company_name=company_name,
        event_id=f"articles-version-{digest}",
        event_title=f"{created_at[:10]} 정관 {status} 저장",
        event_detail=(
            f"버전명: {version_name}\n"
            f"표준유형: {profile_name}\n"
            f"반영조항: {len(comparisons)}건"
        ),
        occurred_at=created_at,
        source="articles_editor",
    )
    return record, message


def _current_comparisons(
    suggestions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "title": item.get("title"),
            "before": item.get("before"),
            "after": item.get("after"),
            "reason": item.get("reason"),
            "checks": item.get("checks", []),
        }
        for item in suggestions
        if item.get("decision") == "적용"
    ]


def render_articles_editor(
    user_id: str,
    business_no: str,
    company_name: str,
    review: dict[str, Any],
) -> None:
    st.divider()
    st.markdown("#### 정관 자동개정·편집")
    st.caption(
        "표준유형을 선택하면 미비 조항의 개정 초안을 자동 추천합니다. "
        "사용자가 적용할 조항과 문구를 최종 결정한 뒤 PDF로 저장합니다."
    )

    original_text = str(review.get("source_text", "") or "")
    if not original_text.strip():
        st.warning(
            "현재 저장된 분석결과에는 원문 정관 텍스트가 없습니다. "
            "정관을 한 번 다시 분석하면 자동개정 편집기를 사용할 수 있습니다."
        )
        return

    library = _templates()
    profiles = list((library.get("profiles", {}) or {}).keys())
    suffix = _safe_key(
        _normalize_business_no(business_no) or company_name
    )

    profile_name = st.selectbox(
        "비교할 표준정관 유형",
        profiles,
        key=f"articles_profile_v690_{suffix}",
    )
    profile_info = (
        library.get("profiles", {})
        .get(profile_name, {})
        .get("description", "")
    )
    st.info(profile_info)

    suggestion_state_key = f"articles_suggestions_v690_{suffix}_{profile_name}"
    if suggestion_state_key not in st.session_state:
        st.session_state[suggestion_state_key] = build_suggestions(
            review,
            profile_name,
        )

    suggestions = st.session_state[suggestion_state_key]
    edit_tab, full_tab, history_tab = st.tabs(
        ["조항별 개정안", "전체 개정본·PDF", "버전 이력"]
    )

    with edit_tab:
        st.markdown("##### 조항별 적용 여부와 문구 편집")
        for index, item in enumerate(suggestions):
            title = str(item.get("title", "") or "")
            status = str(item.get("status", "") or "")
            with st.expander(
                f"{title} · 현재상태 {status}",
                expanded=index < 3,
            ):
                left, right = st.columns(2)
                with left:
                    st.markdown("**현재 정관에서 확인된 문구**")
                    st.text_area(
                        "개정 전",
                        value=str(item.get("before", "") or ""),
                        height=180,
                        disabled=True,
                        key=f"articles_before_{suffix}_{profile_name}_{index}",
                        label_visibility="collapsed",
                    )
                with right:
                    st.markdown("**자동 추천 개정안**")
                    item["after"] = st.text_area(
                        "개정 후",
                        value=str(item.get("after", "") or ""),
                        height=240,
                        key=f"articles_after_{suffix}_{profile_name}_{index}",
                        label_visibility="collapsed",
                    )

                item["decision"] = st.radio(
                    "처리방식",
                    ["적용", "유지", "보류"],
                    index=(
                        ["적용", "유지", "보류"].index(
                            item.get("decision", "보류")
                        )
                        if item.get("decision", "보류")
                        in ["적용", "유지", "보류"]
                        else 2
                    ),
                    horizontal=True,
                    key=f"articles_decision_{suffix}_{profile_name}_{index}",
                )
                st.write(f"**개정 목적:** {item.get('reason', '')}")
                st.write(
                    "**최종 확인:** "
                    + ", ".join(item.get("checks", []) or [])
                )

        if st.button(
            "선택한 조항으로 전체 개정안 만들기",
            type="primary",
            use_container_width=True,
            key=f"articles_compose_v690_{suffix}",
        ):
            st.session_state[f"articles_final_text_v690_{suffix}"] = (
                compose_revised_text(original_text, suggestions)
            )
            st.success("선택한 개정조항을 전체 정관안에 반영했습니다.")

    with full_tab:
        final_key = f"articles_final_text_v690_{suffix}"
        if final_key not in st.session_state:
            st.session_state[final_key] = compose_revised_text(
                original_text,
                suggestions,
            )

        st.caption(
            "아래 전체 문서는 사용자가 직접 최종 수정할 수 있습니다. "
            "[사용자 입력] 표시와 조항번호를 확정한 뒤 저장하세요."
        )
        final_text = st.text_area(
            "최종 정관 개정안",
            value=st.session_state[final_key],
            height=700,
            key=f"articles_final_editor_v690_{suffix}",
        )
        st.session_state[final_key] = final_text

        name_col, status_col = st.columns(2)
        with name_col:
            version_name = st.text_input(
                "버전명",
                value=f"AI개정안_{datetime.now():%Y%m%d}",
                key=f"articles_version_name_v690_{suffix}",
            )
        with status_col:
            document_status = st.selectbox(
                "문서상태",
                ["AI 개정안", "사용자 수정본", "최종 확정본"],
                key=f"articles_document_status_v690_{suffix}",
            )

        comparisons = _current_comparisons(suggestions)

        if st.button(
            "현재 정관 버전 저장",
            use_container_width=True,
            key=f"articles_save_version_v690_{suffix}",
        ):
            _, message = save_version(
                user_id=user_id,
                business_no=business_no,
                company_name=company_name,
                profile_name=profile_name,
                version_name=version_name,
                status=document_status,
                original_text=original_text,
                final_text=final_text,
                comparisons=comparisons,
            )
            st.success(f"정관 버전을 {message}했습니다.")

        safe_company = re.sub(
            r'[\\/:*?"<>|]',
            "_",
            company_name or "기업",
        )
        revised_pdf = build_revised_articles_pdf(
            company_name,
            business_no,
            final_text,
            version_name,
        )
        comparison_pdf = build_comparison_pdf(
            company_name,
            business_no,
            comparisons,
            version_name,
        )
        report_pdf = build_review_report_pdf(
            company_name,
            business_no,
            review,
            comparisons,
            version_name,
        )

        d1, d2, d3 = st.columns(3)
        with d1:
            st.download_button(
                "개정 정관 PDF",
                data=revised_pdf,
                file_name=f"정관개정안_{safe_company}_{version_name}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"articles_pdf_revised_v690_{suffix}",
            )
        with d2:
            st.download_button(
                "신·구조문 대비표 PDF",
                data=comparison_pdf,
                file_name=f"신구조문대비표_{safe_company}_{version_name}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"articles_pdf_compare_v690_{suffix}",
            )
        with d3:
            st.download_button(
                "정관 검토보고서 PDF",
                data=report_pdf,
                file_name=f"정관검토보고서_{safe_company}_{version_name}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=f"articles_pdf_report_v690_{suffix}",
            )

    with history_tab:
        versions = _load_local_versions(
            user_id,
            business_no,
            company_name,
        )
        if not versions:
            st.info("저장된 정관 버전이 없습니다.")
        else:
            for version in versions:
                with st.expander(
                    f"{version.get('created_at', '')[:19]} · "
                    f"{version.get('version_name', '')} · "
                    f"{version.get('status', '')}"
                ):
                    st.write(
                        f"표준유형: {version.get('profile_name', '-')}"
                    )
                    st.write(
                        f"반영조항: {len(version.get('comparisons', []) or [])}건"
                    )
                    st.text_area(
                        "저장된 최종문구",
                        value=str(version.get("final_text", "") or ""),
                        height=300,
                        disabled=True,
                        key=f"articles_history_text_{version.get('version_id', '')}",
                    )
