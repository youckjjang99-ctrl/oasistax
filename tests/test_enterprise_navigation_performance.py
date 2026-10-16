"""Synthetic navigation/ownership tests; no production data or network."""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd
import pytest

import enterprise_center as center
import enterprise_documents as documents


def test_company_tools_share_one_fragment_not_the_main_selector():
    tree = ast.parse(Path(center.__file__).read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    fragment = functions["_render_enterprise_company_details"]
    assert any(ast.unparse(node) == "st.fragment" for node in fragment.decorator_list)
    fragment_source = ast.unparse(fragment)
    assert "st.pills(" in fragment_source
    assert 'enterprise_section_v1031' in fragment_source
    assert 'enterprise_center_customer' not in fragment_source
    assert 'restore_crm_from_cloud(user_id)' in fragment_source
    selector = ast.unparse(functions["render_enterprise_management_center"])
    assert 'enterprise_center_customer' in selector
    assert '_render_enterprise_company_details(' in selector
    # The app no longer synchronizes the same enterprise route twice.
    app_source = (Path(center.__file__).parent / "app.py").read_text(encoding="utf-8")
    assert 'if active_tab in {"홈", "내 누적 고객DB", "AI 코파일럿"}:' in app_source


class FullAppRerun(Exception):
    pass


@pytest.mark.parametrize("logged_in,current_owner,current_role,route_pending", [
    (False, "synthetic-owner", "member", False),
    (True, "different-owner", "member", False),
    (True, "synthetic-owner", "admin", False),
    (True, "synthetic-owner", "member", True),
])
def test_fragment_revalidates_identity_role_and_cross_route_before_reads(
    monkeypatch, logged_in, current_owner, current_role, route_pending,
):
    import auth
    import cloud_crm_restore

    monkeypatch.setattr(auth, "check_login", lambda: logged_in)
    monkeypatch.setattr(cloud_crm_restore, "restore_crm_from_cloud", lambda *args: pytest.fail("unexpected cloud pull"))
    namespace = dict(vars(center))

    def rerun(*, scope):
        assert scope == "app"
        raise FullAppRerun

    namespace["st"] = SimpleNamespace(session_state={
        "current_user_id": current_owner,
        "current_user_role": current_role,
        "_enterprise_full_app_navigation_v9148": route_pending,
    }, rerun=rerun)
    namespace["load_registered_customers"] = lambda *a, **kw: pytest.fail("unexpected customer read")
    tree = ast.parse(Path(center.__file__).read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                and n.name == "_render_enterprise_company_details")
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<guard-test>", "exec"), namespace)
    with pytest.raises(FullAppRerun):
        namespace[node.name]("synthetic-owner", "synthetic", pd.Series(dtype=object), expected_role="member")


def test_fresh_company_resolution_prefers_id_and_rejects_ambiguity():
    selected = pd.Series({"_customer_id": "synthetic-id", "업체명": "before"})
    fresh = pd.DataFrame([{"_customer_id": "synthetic-id", "업체명": "after"}])
    assert center._current_enterprise_row(fresh, selected)["업체명"] == "after"
    assert center._current_enterprise_row(pd.concat([fresh, fresh]), selected) is None
    assert center._current_enterprise_row(fresh.assign(_customer_id="other"), selected) is None


def test_legacy_rows_are_not_matched_by_company_name_alone():
    customers = pd.DataFrame([
        {"업체명": "synthetic-same-name", "주소": "synthetic-one"},
        {"업체명": "synthetic-same-name", "주소": "synthetic-two"},
    ])
    selected = customers.iloc[0]
    assert center._current_enterprise_row(customers, selected)["주소"] == "synthetic-one"
    assert center._current_enterprise_row(customers.iloc[1:], selected) is None


def test_overview_reuses_preloaded_context_without_second_document_read(monkeypatch):
    import cloud_sync
    monkeypatch.setattr(documents, "load_enterprise_document_context", lambda *a: pytest.fail("duplicate document read"))
    monkeypatch.setattr(cloud_sync, "load_registry_snapshot", lambda *a, **kw: {})
    monkeypatch.setitem(sys.modules, "consultation_journal", SimpleNamespace(load_company_consultation_context=lambda *a, **kw: []))
    monkeypatch.setitem(sys.modules, "employee_status", SimpleNamespace(get_latest_employee_status=lambda *a, **kw: {}))
    monkeypatch.setitem(sys.modules, "articles_review", SimpleNamespace(get_latest_articles_review=lambda *a, **kw: {}))
    result = documents.load_enterprise_source_overview(
        "synthetic-owner", "synthetic-key", document_context={"records": [], "latest_by_type": {}},
    )
    assert all(not item["available"] for item in result.values())


def test_overview_display_passes_owner_company_revision_and_context(monkeypatch):
    captured = []
    monkeypatch.setattr(documents, "_enterprise_source_overview_revision", lambda owner: (42, 100))
    monkeypatch.setattr(documents, "_load_enterprise_source_overview_cached", lambda *args, **kw: captured.append((args, kw)) or {})
    context = {"records": [], "latest_by_type": {}}
    documents.get_enterprise_source_overview_for_display("synthetic-owner", "synthetic-key", "synthetic-company", document_context=context)
    assert captured == [(("synthetic-owner", "synthetic-key", "synthetic-company", 42, 100), {"_document_context": context})]


def test_all_eight_company_submenus_render_and_preserve_copilot_navigation(monkeypatch, tmp_path):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import auth
    import cloud_crm_restore
    import crm_editor
    import customer_history

    customers = pd.DataFrame([{
        "_customer_id": "synthetic-id", "업체명": "synthetic-company",
        "사업자등록번호": "synthetic-business-key", "사업자유형": "법인사업자",
    }])
    monkeypatch.setattr(auth, "check_login", lambda: True)
    monkeypatch.setattr(cloud_crm_restore, "restore_crm_from_cloud", lambda *a: {"ok": True})
    monkeypatch.setattr(center, "load_registered_customers", lambda *a, **kw: customers.copy())
    monkeypatch.setattr(center, "filter_active_customers", lambda **kw: kw["customers"].copy())
    monkeypatch.setattr(center, "get_user_cumulative_db_path", lambda *a: tmp_path / "synthetic.xlsx")
    monkeypatch.setattr(center, "_financial_snapshot", lambda *a: {})
    monkeypatch.setattr(center, "get_customer_record", lambda *a: {"status": "신규", "timeline": []})
    monkeypatch.setattr(center, "get_crm_profile", lambda *a: {"priority": "3", "pipeline_stage": "신규"})
    monkeypatch.setattr(documents, "load_enterprise_document_context", lambda *a: {"records": [], "latest_by_type": {}})
    monkeypatch.setattr(documents, "get_enterprise_source_overview_for_display", lambda *a, **kw: {
        key: {"count": 0, "available": False} for key in documents.UPLOAD_DOCUMENT_TYPES
    })
    monkeypatch.setattr(crm_editor, "editor_guard", lambda *a: ("synthetic-editor", 0))
    monkeypatch.setattr(customer_history, "get_customer_history", lambda *a: [])

    def leaf(label):
        return lambda *a, **kw: st.write("synthetic-leaf:" + label)

    for name, functions in {
        "consultation_journal": {"render_saved_consultation_journals": leaf("녹음")},
        "enterprise_consulting_engine": {"reconcile_enterprise_consulting_context": lambda **kw: {"preferences": {}, "consultation_context": {}}},
        "multi_source_policy": {"render_multi_source_match": leaf("정책자금")},
        "stock_valuation": {"render_stock_valuation_page": leaf("주가평가")},
        "articles_review": {"render_articles_review": leaf("정관검토")},
        "employee_status": {"render_employee_status": leaf("직원현황")},
        "temporary_advance_ui": {"render_temporary_advance_calculator": leaf("가지급금 계산기")},
    }.items():
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(**functions))

    app = AppTest.from_string('''
import streamlit as st
from enterprise_center import render_enterprise_management_center
st.session_state.setdefault("current_user_id", "synthetic-owner")
st.session_state.setdefault("current_user_role", "member")
if st.session_state.get("active_main_menu_v1020") == "AI 코파일럿":
    st.write("synthetic-leaf:copilot")
else:
    render_enterprise_management_center("synthetic-owner", "synthetic")
''', default_timeout=15)
    for section in ["기업정보", "상담일지", "정책자금", "주가평가", "정관검토", "기업히스토리", "직원현황", "가지급금 계산기"]:
        app.session_state["enterprise_section_v1031"] = section
        app.run()
        assert not app.exception, (section, [str(item.value) for item in app.exception])
        assert app.session_state["enterprise_section_v1031"] == section
        if section in {"정책자금", "주가평가", "정관검토", "직원현황", "가지급금 계산기"}:
            assert any(item.value == "synthetic-leaf:" + section for item in app.markdown)
        elif section == "상담일지":
            assert any(item.label == "CRM 저장" for item in app.button)
        elif section == "기업히스토리":
            assert any("기업 히스토리가 없습니다" in item.value for item in app.info)
    app.button(key="enterprise_open_copilot_synthetic-business-key").click().run()
    assert not app.exception
    assert app.session_state["active_main_menu_v1020"] == "AI 코파일럿"
    assert any(item.value == "synthetic-leaf:copilot" for item in app.markdown)
