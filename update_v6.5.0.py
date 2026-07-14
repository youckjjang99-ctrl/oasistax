from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.5.0"
TARGETS = [
    "enterprise_center.py",
    "enterprise_consulting_engine.py",
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
    current = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists()
        else ""
    )
    if current and current not in {
        "v6.4.2", "6.4.2", "v6.5.0", "6.5.0"
    }:
        fail(f"Expected v6.4.2 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v6.5.0_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)
    for name in TARGETS:
        src = root / name
        if src.exists():
            shutil.copy2(src, backup / name)

    payload = root / "payload"
    engine_src = payload / "enterprise_consulting_engine.py"
    if not engine_src.exists():
        fail("payload/enterprise_consulting_engine.py is missing.")
    shutil.copy2(engine_src, root / "enterprise_consulting_engine.py")

    path = root / "enterprise_center.py"
    text = path.read_text(encoding="utf-8")

    import_anchor = '''from consulting_report import (
    build_consulting_analysis,
    build_consulting_excel_report,
)
'''
    import_block = '''from consulting_report import (
    build_consulting_analysis,
    build_consulting_excel_report,
)
from enterprise_consulting_engine import (
    reconcile_enterprise_consulting_context,
)
'''
    if "reconcile_enterprise_consulting_context" not in text:
        text = replace_once(
            text,
            import_anchor,
            import_block,
            "integration engine import",
        )

    old_load = '''    financial = _financial_snapshot(user_id, business_no)
    registry = _registry_snapshot(user_id, business_no)
    stock = _stock_record(user_id, business_no)
    preferences = get_matching_preferences(
        user_id,
        business_no,
    )
'''
    new_load = '''    integration = reconcile_enterprise_consulting_context(
        user_id=user_id,
        business_no=business_no,
        company_name=company_name,
    )
    financial = _financial_snapshot(user_id, business_no)
    registry = _registry_snapshot(user_id, business_no)
    stock = _stock_record(user_id, business_no)
    preferences = integration.get("preferences", {}) or {}
'''
    text = replace_once(
        text,
        old_load,
        new_load,
        "enterprise data integration",
    )

    old_policy = '''    with tab_policy:
        st.markdown("#### 고객별 정책자금 매칭설정")
        matching_keywords = st.text_area(
            "매칭키워드",
            value=", ".join(
                preferences.get("매칭키워드", []) or []
            ),
            key="enterprise_match_keywords",
        )
        interest_fields = st.multiselect(
            "관심지원분야",
            INTEREST_OPTIONS,
            default=[
                item
                for item in (
                    preferences.get("관심지원분야", []) or []
                )
                if item in INTEREST_OPTIONS
            ],
            key="enterprise_interest_fields",
        )
        exclusion_keywords = st.text_area(
            "제외키워드",
            value=", ".join(
                preferences.get("제외키워드", []) or []
            ),
            key="enterprise_exclusion_keywords",
        )
'''
    new_policy = '''    with tab_policy:
        st.markdown("#### 고객별 정책자금 매칭설정")
        added_keywords = integration.get("added_keywords", []) or []
        added_interests = integration.get("added_interests", []) or []
        consultation_count = int(
            integration.get("consultation_context", {}).get("count", 0) or 0
        )
        st.caption(
            f"상담일지 {consultation_count}건 연동 · "
            f"자동추가 키워드 {len(added_keywords)}개 · "
            f"자동추가 관심분야 {len(added_interests)}개"
        )

        widget_suffix = (
            business_no.replace("-", "")
            if business_no
            else company_name
        )
        matching_keywords = st.text_area(
            "매칭키워드",
            value=", ".join(
                preferences.get("매칭키워드", []) or []
            ),
            key=f"enterprise_match_keywords_v650_{widget_suffix}",
        )
        interest_fields = st.multiselect(
            "관심지원분야",
            INTEREST_OPTIONS,
            default=[
                item
                for item in (
                    preferences.get("관심지원분야", []) or []
                )
                if item in INTEREST_OPTIONS
            ],
            key=f"enterprise_interest_fields_v650_{widget_suffix}",
        )
        exclusion_keywords = st.text_area(
            "제외키워드",
            value=", ".join(
                preferences.get("제외키워드", []) or []
            ),
            key=f"enterprise_exclusion_keywords_v650_{widget_suffix}",
        )
'''
    text = replace_once(
        text,
        old_policy,
        new_policy,
        "policy widget refresh",
    )

    text = text.replace(
        'key="enterprise_fund_purpose",',
        'key=f"enterprise_fund_purpose_v650_{widget_suffix}",',
        1,
    )
    text = text.replace(
        'key="enterprise_planned_amount",',
        'key=f"enterprise_planned_amount_v650_{widget_suffix}",',
        1,
    )
    text = text.replace(
        'key="enterprise_planned_timing",',
        'key=f"enterprise_planned_timing_v650_{widget_suffix}",',
        1,
    )
    text = text.replace(
        'key="enterprise_save_preferences",',
        'key=f"enterprise_save_preferences_v650_{widget_suffix}",',
        1,
    )

    old_ai = '''        consultation_context = get_company_consultation_context(
            user_id=user_id,
            business_no=business_no,
            company_name=company_name,
        )
        consulting_analysis = build_consulting_analysis(
'''
    new_ai = '''        consultation_context = integration.get(
            "consultation_context",
            {},
        )
        consulting_analysis = build_consulting_analysis(
'''
    text = replace_once(
        text,
        old_ai,
        new_ai,
        "AI integrated context",
    )

    path.write_text(text, encoding="utf-8", newline="\n")
    version_path.write_text(VERSION + "\n", encoding="utf-8")

    changelog_src = payload / "CHANGELOG_v6.5.0.md"
    if changelog_src.exists():
        shutil.copy2(changelog_src, root / "CHANGELOG_v6.5.0.md")

    for name in [
        "enterprise_center.py",
        "enterprise_consulting_engine.py",
    ]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("RESULT=Consultation keywords, history and AI context integrated.")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
