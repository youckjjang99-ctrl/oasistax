from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.7.0"
TARGETS = [
    "enterprise_center.py",
    "consulting_report.py",
    "requirements.txt",
    "articles_review.py",
    "data/articles_review_checklist.json",
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
    if not (root / "enterprise_center.py").exists():
        fail("Run this patch from the OASIS project root folder.")

    version_path = root / "VERSION.txt"
    current = version_path.read_text(encoding="utf-8-sig").strip()
    if current not in {"v6.6.2", "6.6.2", "v6.7.0", "6.7.0"}:
        fail(f"Expected v6.6.2 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v6.7.0_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        src = root / name
        if src.exists():
            dst = backup / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    for relative in [
        "articles_review.py",
        "data/articles_review_checklist.json",
    ]:
        src = root / "payload" / relative
        if not src.exists():
            fail(f"payload/{relative} is missing.")
        dst = root / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    req = root / "requirements.txt"
    req_text = req.read_text(encoding="utf-8")
    additions = []
    if "python-docx" not in req_text:
        additions.append("python-docx==1.1.2")
    if "olefile" not in req_text:
        additions.append("olefile==0.47")
    if additions:
        req.write_text(req_text.rstrip() + "\n" + "\n".join(additions) + "\n", encoding="utf-8")

    path = root / "enterprise_center.py"
    text = path.read_text(encoding="utf-8")

    import_anchor = '''from consulting_report import (
    build_consulting_analysis,
    build_consulting_excel_report,
)
'''
    import_new = '''from consulting_report import (
    build_consulting_analysis,
    build_consulting_excel_report,
)
from articles_review import (
    get_latest_articles_review,
    render_articles_review,
)
'''
    if "render_articles_review" not in text:
        text = replace_once(text, import_anchor, import_new, "articles import")

    old_tabs = '''    tab_overview, tab_crm, tab_policy, tab_stock, tab_history, tab_ai = st.tabs(
        [
            "기업정보",
            "CRM",
            "정책자금",
            "주가평가·등기",
            "기업히스토리",
            "AI 진단",
        ]
    )
'''
    new_tabs = '''    (
        tab_overview,
        tab_crm,
        tab_policy,
        tab_stock,
        tab_articles,
        tab_history,
        tab_ai,
    ) = st.tabs(
        [
            "기업정보",
            "CRM",
            "정책자금",
            "주가평가·등기",
            "정관검토",
            "기업히스토리",
            "AI 진단",
        ]
    )
'''
    text = replace_once(text, old_tabs, new_tabs, "articles tab")

    history_marker = "    with tab_history:\n"
    articles_block = '''    with tab_articles:
        render_articles_review(
            user_id=user_id,
            business_no=business_no,
            company_name=company_name,
        )

'''
    if articles_block.strip() not in text:
        text = replace_once(
            text,
            history_marker,
            articles_block + history_marker,
            "articles tab content",
        )

    old_ai = '''        consulting_analysis = build_consulting_analysis(
            selected_row,
            financial,
            registry,
            stock,
            preferences,
            consultation_context=consultation_context,
        )
'''
    new_ai = '''        articles_review = get_latest_articles_review(
            user_id,
            business_no,
            company_name,
        )
        consulting_analysis = build_consulting_analysis(
            selected_row,
            financial,
            registry,
            stock,
            preferences,
            consultation_context=consultation_context,
            articles_review=articles_review,
        )
'''
    text = replace_once(text, old_ai, new_ai, "AI articles context")
    text = text.replace(
        "기업정보·재무정보·등기·주가평가·정책자금 매칭설정을 ",
        "기업정보·재무정보·등기·주가평가·정관·정책자금 매칭설정을 ",
        1,
    )
    path.write_text(text, encoding="utf-8", newline="\n")

    path = root / "consulting_report.py"
    text = path.read_text(encoding="utf-8")
    old_sig = '''    preferences: dict[str, Any],
    consultation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
'''
    new_sig = '''    preferences: dict[str, Any],
    consultation_context: dict[str, Any] | None = None,
    articles_review: dict[str, Any] | None = None,
) -> dict[str, Any]:
'''
    text = replace_once(text, old_sig, new_sig, "report signature")

    old_context = '''    consultation_actions = consultation_context.get("next_actions", []) or []

    sales_number = _number(sales)
'''
    new_context = '''    consultation_actions = consultation_context.get("next_actions", []) or []

    articles_review = (
        articles_review if isinstance(articles_review, dict) else {}
    )
    articles_score = int(articles_review.get("score", 0) or 0)
    articles_priorities = articles_review.get("priority_items", []) or []
    articles_opportunities = (
        articles_review.get("consulting_opportunities", []) or []
    )

    sales_number = _number(sales)
'''
    text = replace_once(text, old_context, new_context, "articles variables")

    marker = '''    if consultation_count:
'''
    articles_logic = '''    if articles_review:
        strengths.append(f"정관검토 결과 {articles_score}점이 반영되었습니다.")
        if articles_score < 70:
            cautions.append(
                "정관의 핵심 절세·퇴직·유족보상·승계 조항 보완이 필요합니다."
            )
        for item in articles_priorities[:4]:
            title = str(item.get("title", "") or "").strip()
            if title:
                strategy.append(f"정관 우선검토: {title}")
        for opportunity in articles_opportunities[:3]:
            strategy.append(f"연계 컨설팅: {opportunity}")
        questions.append(
            "정관 개정일, 주주총회 의사록, 별도 임원규정의 실제 제정 여부를 확인해 주세요."
        )
    else:
        cautions.append("저장된 정관검토 결과가 없어 정관 리스크는 미반영 상태입니다.")

'''
    if articles_logic.strip() not in text:
        text = replace_once(text, marker, articles_logic + marker, "articles diagnosis logic")

    text = text.replace(
        '''            "consultation": bool(consultation_count),
            "matching_preferences": bool(preferences),
''',
        '''            "consultation": bool(consultation_count),
            "articles_review": bool(articles_review),
            "matching_preferences": bool(preferences),
''',
        1,
    )
    path.write_text(text, encoding="utf-8", newline="\n")

    version_path.write_text(VERSION + "\n", encoding="utf-8")
    changelog_src = root / "payload" / "CHANGELOG_v6.7.0.md"
    if changelog_src.exists():
        shutil.copy2(changelog_src, root / "CHANGELOG_v6.7.0.md")

    for name in ["enterprise_center.py", "consulting_report.py", "articles_review.py"]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=NO")
    print("RESULT=Articles review menu and AI integration enabled.")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
