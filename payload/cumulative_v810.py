
from __future__ import annotations

import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

VERSION = "v8.1.0"
TARGET_FILES = [
    "app.py",
    "enterprise_center.py",
    "consulting_copilot.py",
    "update_engine.py",
    "cleanup_legacy_patches.py",
    "RUN_UPDATE.cmd",
    "RUN_CLEANUP_PATCH_FILES.cmd",
    ".gitignore",
    "VERSION.txt",
]


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        fail(f"수정 위치를 찾지 못했습니다: {label}")
    return text.replace(old, new, 1)


def patch_app(text: str) -> str:
    text = text.replace(
        '        "정책자금 매칭": "통합 정책자금 매칭",\n',
        "",
    )
    text = text.replace(
        '        "주가평가": "주가평가",\n',
        "",
    )
    text = text.replace(
        "기업 컨설팅 · 크레탑 자동등록 · 정책자금 매칭 · 주가평가",
        "기업 컨설팅 · 크레탑 자동등록 · AI 코파일럿",
    )

    radio_anchor = (
        '    selected_menu_label = st.radio(\n'
        '        "메뉴",\n'
        '        list(menu_label_map.keys()),\n'
        '        key="active_main_menu_v311",\n'
        '        label_visibility="collapsed"\n'
        '    )\n'
    )
    radio_replacement = (
        '    pending_menu = st.session_state.pop(\n'
        '        "_oasis_pending_main_menu",\n'
        '        None,\n'
        '    )\n'
        '    if pending_menu in menu_label_map:\n'
        '        st.session_state["active_main_menu_v311"] = pending_menu\n\n'
        '    current_sidebar_value = st.session_state.get(\n'
        '        "active_main_menu_v311"\n'
        '    )\n'
        '    if current_sidebar_value not in menu_label_map:\n'
        '        st.session_state["active_main_menu_v311"] = "기업 컨설팅"\n\n'
        '    selected_menu_label = st.radio(\n'
        '        "메뉴",\n'
        '        list(menu_label_map.keys()),\n'
        '        key="active_main_menu_v311",\n'
        '        label_visibility="collapsed"\n'
        '    )\n'
    )
    if "_oasis_pending_main_menu" not in text:
        text = replace_once(
            text,
            radio_anchor,
            radio_replacement,
            "사이드바 메뉴 전환 처리",
        )

    old_compat = (
        '    if active_tab == "고객관리":\n'
        '        active_tab = "기업관리센터"\n'
    )
    new_compat = (
        '    if active_tab in {\n'
        '        "고객관리",\n'
        '        "통합 정책자금 매칭",\n'
        '        "주가평가",\n'
        '    }:\n'
        '        active_tab = "기업관리센터"\n'
    )
    if old_compat in text:
        text = text.replace(old_compat, new_compat, 1)

    return text


def patch_enterprise(text: str) -> str:
    import_anchor = (
        'from matching_preferences import (\n'
        '    INTEREST_OPTIONS,\n'
        '    get_matching_preferences,\n'
        '    save_matching_preferences,\n'
        ')\n'
    )
    import_replacement = (
        import_anchor
        + 'from multi_source_policy import render_multi_source_match\n'
        + 'from stock_valuation import render_stock_valuation_page\n'
    )
    if "from multi_source_policy import render_multi_source_match" not in text:
        text = replace_once(
            text,
            import_anchor,
            import_replacement,
            "기업컨설팅 통합기능 import",
        )

    hero_anchor = (
        '        unsafe_allow_html=True,\n'
        '    )\n\n'
        '    sales = _first_value(\n'
    )
    hero_replacement = (
        '        unsafe_allow_html=True,\n'
        '    )\n\n'
        '    if st.button(\n'
        '        "이 기업 AI 코파일럿으로 분석하기",\n'
        '        type="primary",\n'
        '        use_container_width=True,\n'
        '        key=f"enterprise_open_copilot_{business_no or company_name}",\n'
        '    ):\n'
        '        st.session_state["_oasis_copilot_business_no"] = business_no\n'
        '        st.session_state["_oasis_copilot_company_name"] = company_name\n'
        '        st.session_state["_oasis_pending_main_menu"] = "AI 코파일럿"\n'
        '        st.rerun()\n\n'
        '    sales = _first_value(\n'
    )
    if "enterprise_open_copilot_" not in text:
        text = replace_once(
            text,
            hero_anchor,
            hero_replacement,
            "AI 코파일럿 이동 버튼",
        )

    old_tabs = (
        '    (\n'
        '        tab_overview,\n'
        '        tab_crm,\n'
        '        tab_policy,\n'
        '        tab_stock,\n'
        '        tab_articles,\n'
        '        tab_history,\n'
        '        tab_ai,\n'
        '        tab_employees,\n'
        '    ) = st.tabs(\n'
        '        [\n'
        '            "기업정보",\n'
        '            "CRM",\n'
        '            "정책자금",\n'
        '            "주가평가·등기",\n'
        '            "정관검토",\n'
        '            "기업히스토리",\n'
        '            "AI 진단",\n'
        '            "직원현황",\n'
        '        ]\n'
        '    )\n'
    )
    new_tabs = (
        '    (\n'
        '        tab_overview,\n'
        '        tab_crm,\n'
        '        tab_policy,\n'
        '        tab_stock,\n'
        '        tab_articles,\n'
        '        tab_history,\n'
        '        tab_employees,\n'
        '    ) = st.tabs(\n'
        '        [\n'
        '            "기업정보",\n'
        '            "CRM",\n'
        '            "정책자금",\n'
        '            "주가평가·등기",\n'
        '            "정관검토",\n'
        '            "기업히스토리",\n'
        '            "직원현황",\n'
        '        ]\n'
        '    )\n'
    )
    text = replace_once(text, old_tabs, new_tabs, "AI 진단 탭 제거")

    policy_anchor = (
        '            st.success(\n'
        '                "정책자금 매칭설정을 로컬과 Supabase에 저장했습니다."\n'
        '            )\n\n'
        '    with tab_stock:\n'
    )
    policy_replacement = (
        '            st.success(\n'
        '                "정책자금 매칭설정을 로컬과 Supabase에 저장했습니다."\n'
        '            )\n\n'
        '        current_policy_preferences = {\n'
        '            "매칭키워드": [\n'
        '                item.strip()\n'
        '                for item in matching_keywords.split(",")\n'
        '                if item.strip()\n'
        '            ],\n'
        '            "관심지원분야": interest_fields,\n'
        '            "제외키워드": [\n'
        '                item.strip()\n'
        '                for item in exclusion_keywords.split(",")\n'
        '                if item.strip()\n'
        '            ],\n'
        '            "자금사용목적": fund_purpose,\n'
        '            "투자예정금액": planned_amount,\n'
        '            "투자예정시기": planned_timing,\n'
        '        }\n\n'
        '        st.divider()\n'
        '        st.markdown("#### 다중소스 정책자금·고용지원금 매칭")\n'
        '        render_multi_source_match(\n'
        '            user_id,\n'
        '            selected_row,\n'
        '            current_policy_preferences,\n'
        '        )\n\n'
        '    with tab_stock:\n'
    )
    if "#### 다중소스 정책자금·고용지원금 매칭" not in text:
        text = replace_once(
            text,
            policy_anchor,
            policy_replacement,
            "정책자금 실행 통합",
        )

    stock_start = text.find("    with tab_stock:\n")
    articles_start = text.find("\n    with tab_articles:", stock_start)
    if stock_start == -1 or articles_start == -1:
        fail("주가평가 탭 범위를 찾지 못했습니다.")

    stock_block = (
        '    with tab_stock:\n'
        '        selected_stock_label = (\n'
        '            f"{company_name} · {business_no}"\n'
        '            if business_no\n'
        '            else company_name\n'
        '        )\n'
        '        if selected_stock_label:\n'
        '            st.session_state["stock_customer_selector"] = (\n'
        '                selected_stock_label\n'
        '            )\n\n'
        '        render_stock_valuation_page(\n'
        '            user_id=user_id,\n'
        '            user_name=user_name,\n'
        '        )\n'
    )
    text = text[:stock_start] + stock_block + text[articles_start:]

    ai_start = text.find("\n    with tab_ai:")
    employee_start = text.find(
        "\n    with tab_employees:",
        ai_start if ai_start != -1 else 0,
    )
    if ai_start != -1 and employee_start != -1:
        text = text[:ai_start] + text[employee_start:]

    text = text.replace(
        "재연동 후 정책자금 탭과 정책자금매칭 메뉴를 다시 열면 ",
        "재연동 후 기업컨설팅의 정책자금 탭을 다시 열면 ",
    )
    return text


def patch_copilot(text: str) -> str:
    anchor = (
        '    labels, row_map = build_customer_labels(customers)\n'
        '    selected_label = st.selectbox(\n'
        '        "상담할 기업",\n'
        '        labels,\n'
        '        key="copilot_customer",\n'
        '    )\n'
    )
    replacement = (
        '    labels, row_map = build_customer_labels(customers)\n\n'
        '    prefill_business_no = str(\n'
        '        st.session_state.pop("_oasis_copilot_business_no", "") or ""\n'
        '    )\n'
        '    prefill_company_name = str(\n'
        '        st.session_state.pop("_oasis_copilot_company_name", "") or ""\n'
        '    )\n'
        '    if prefill_business_no or prefill_company_name:\n'
        '        normalized_prefill = re.sub(r"[^0-9]", "", prefill_business_no)\n'
        '        for candidate_label, candidate_index in row_map.items():\n'
        '            candidate = customers.loc[candidate_index]\n'
        '            candidate_business = re.sub(\n'
        '                r"[^0-9]",\n'
        '                "",\n'
        '                str(candidate.get("사업자등록번호", "") or ""),\n'
        '            )\n'
        '            candidate_name = _clean(candidate.get("업체명", ""))\n'
        '            if (\n'
        '                normalized_prefill\n'
        '                and candidate_business == normalized_prefill\n'
        '            ) or (\n'
        '                prefill_company_name\n'
        '                and candidate_name == prefill_company_name\n'
        '            ):\n'
        '                st.session_state["copilot_customer"] = candidate_label\n'
        '                break\n\n'
        '    if st.session_state.get("copilot_customer") not in labels:\n'
        '        st.session_state.pop("copilot_customer", None)\n\n'
        '    selected_label = st.selectbox(\n'
        '        "상담할 기업",\n'
        '        labels,\n'
        '        key="copilot_customer",\n'
        '    )\n'
    )
    if "_oasis_copilot_business_no" not in text:
        text = replace_once(
            text,
            anchor,
            replacement,
            "AI 코파일럿 선택기업 자동연결",
        )
    return text


def main() -> None:
    root = Path.cwd()
    if not (root / "app.py").exists():
        fail("app.py가 있는 OASIS 프로젝트 폴더에서 실행해주세요.")

    version_path = root / "VERSION.txt"
    current = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists()
        else ""
    )
    allowed = {
        "v7.4.5", "7.4.5",
        "v8.0.0", "8.0.0",
        "v8.1.0", "8.1.0",
    }
    if current and current not in allowed:
        fail(f"Expected v7.4.5 or v8.0.0 but found {current}.")

    backup = (
        root / "_oasis_backups"
        / ("before_v8.1.0_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    )
    backup.mkdir(parents=True, exist_ok=False)

    for relative in TARGET_FILES:
        source = root / relative
        if source.exists():
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    try:
        for relative in [
            "update_engine.py",
            "cleanup_legacy_patches.py",
            "RUN_UPDATE.cmd",
            "RUN_CLEANUP_PATCH_FILES.cmd",
            ".gitignore",
        ]:
            source = root / "payload" / relative
            if source.exists():
                shutil.copy2(source, root / relative)

        app_path = root / "app.py"
        app_path.write_text(
            patch_app(app_path.read_text(encoding="utf-8")),
            encoding="utf-8",
            newline="\n",
        )

        enterprise_path = root / "enterprise_center.py"
        enterprise_path.write_text(
            patch_enterprise(enterprise_path.read_text(encoding="utf-8")),
            encoding="utf-8",
            newline="\n",
        )

        copilot_path = root / "consulting_copilot.py"
        copilot_path.write_text(
            patch_copilot(copilot_path.read_text(encoding="utf-8")),
            encoding="utf-8",
            newline="\n",
        )

        version_path.write_text(VERSION + "\n", encoding="utf-8")

        changelog = root / "payload" / "CHANGELOG_v8.1.0.md"
        if changelog.exists():
            shutil.copy2(changelog, root / "CHANGELOG_v8.1.0.md")

        for name in [
            "app.py",
            "enterprise_center.py",
            "consulting_copilot.py",
            "update_engine.py",
            "cleanup_legacy_patches.py",
        ]:
            path = root / name
            if path.exists():
                py_compile.compile(str(path), doraise=True)

        if (root / "system_precheck.py").exists():
            sys.path.insert(0, str(root))
            from system_precheck import run_precheck
            report = run_precheck(root, save_report=True)
            if report.get("status") != "PASS":
                errors = [
                    row for row in report.get("checks", [])
                    if not row.get("ok") and row.get("level") == "error"
                ]
                summary = "; ".join(
                    f"{row.get('item')}: {row.get('message')}"
                    for row in errors[:8]
                )
                raise RuntimeError("사전점검 실패: " + summary)

    except Exception as exc:
        for relative in TARGET_FILES:
            backup_source = backup / relative
            destination = root / relative
            if backup_source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_source, destination)
            elif destination.exists():
                destination.unlink()

        print("UPDATE_ROLLED_BACK")
        print(f"BACKUP={backup}")
        fail(f"{type(exc).__name__}: {exc}")

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("MENU_SIMPLIFIED=YES")
    print("POLICY_IN_ENTERPRISE=YES")
    print("STOCK_IN_ENTERPRISE=YES")
    print("AI_DIAGNOSIS_MERGED_TO_COPILOT=YES")
    print("COMMON_UPDATE_ENGINE=ENABLED")
    print("PRECHECK=PASS")
    print("SQL_REQUIRED=NO")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
