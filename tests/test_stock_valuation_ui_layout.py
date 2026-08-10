from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd

import stock_valuation as stock


ROOT = Path(__file__).resolve().parents[1]


def _function_source(function_name: str) -> str:
    source = (ROOT / "stock_valuation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function not found: {function_name}")


def test_stock_customer_context_prefers_exact_business_number():
    customers = pd.DataFrame(
        [
            {
                "업체명": "동일 상호",
                "사업자등록번호": "123-45-67890",
            },
            {
                "업체명": "동일 상호",
                "사업자등록번호": "987-65-43210",
            },
        ]
    )

    selected = stock._find_stock_customer_row(
        customers,
        business_no="9876543210",
        company_name="동일 상호",
    )

    assert selected is not None
    assert selected["사업자등록번호"] == "987-65-43210"


def test_stock_customer_context_rejects_ambiguous_company_name():
    customers = pd.DataFrame(
        [
            {"업체명": "동일 상호", "사업자등록번호": "123-45-67890"},
            {"업체명": "동일상호", "사업자등록번호": "987-65-43210"},
        ]
    )

    selected = stock._find_stock_customer_row(
        customers,
        company_name="동일 상호",
    )

    assert selected is None


def test_loaded_history_is_preserved_when_context_matches(monkeypatch):
    customers = pd.DataFrame(
        [
            {
                "업체명": "테스트 법인",
                "사업자등록번호": "123-45-67890",
                "_customer_id": "customer-1",
                "_cloud_updated_at": "revision-1",
            }
        ]
    )
    state = {
        "stock_loaded_record_id": "record-1",
        "stock_business_no": "123-45-67890",
        "stock_company_name": "테스트 법인",
        "stock_net_income_1": "99,000",
    }
    monkeypatch.setattr(stock.st, "session_state", state)
    monkeypatch.setattr(
        stock,
        "_apply_customer_financial_data",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("matching saved history must not be overwritten")
        ),
    )

    selected = stock._load_stock_customer_context(
        "member",
        customers,
    )

    assert selected is not None
    assert state["stock_loaded_record_id"] == "record-1"
    assert state["stock_net_income_1"] == "99,000"
    assert state["stock_auto_loaded_customer_key"].startswith(
        "123-45-67890:customer-1:"
    )


def test_selected_company_auto_loads_financial_and_registry(monkeypatch):
    customers = pd.DataFrame(
        [
            {
                "업체명": "테스트 법인",
                "사업자등록번호": "123-45-67890",
                "법인등록번호": "110111-1234567",
                "_customer_id": "customer-1",
                "_cloud_updated_at": "revision-1",
            }
        ]
    )
    state = {"stock_last_result": {"old_company": True}}
    restored = []
    monkeypatch.setattr(stock.st, "session_state", state)

    def apply_financial(user_id, selected_row):
        assert user_id == "member"
        state["stock_business_no"] = selected_row["사업자등록번호"]
        state["stock_company_name"] = selected_row["업체명"]
        state["stock_corporate_no"] = selected_row["법인등록번호"]
        return True, "ok"

    monkeypatch.setattr(
        stock,
        "_apply_customer_financial_data",
        apply_financial,
    )
    monkeypatch.setattr(
        stock,
        "_restore_registry_for_business",
        lambda user_id, business_no, **identity: restored.append(
            (user_id, business_no, identity)
        )
        or {},
    )

    selected = stock._load_stock_customer_context(
        "member",
        customers,
        selected_business_no="1234567890",
        selected_company_name="테스트 법인",
    )

    assert selected is not None
    assert "stock_last_result" not in state
    assert state["stock_business_no"] == "123-45-67890"
    assert state["stock_registry_restored_key"] == "123-45-67890"
    assert restored[0][0:2] == ("member", "123-45-67890")


def test_stock_valuation_top_keeps_only_registry_registration():
    render_source = _function_source("render_stock_valuation_page")
    registry_source = _function_source("_render_registry_upload")

    assert "기존 고객에서 불러오기" not in render_source
    assert "기존 고객DB에서 재무정보 불러오기" not in render_source
    assert "크레탑 PDF에서 재무정보 불러오기" not in render_source
    assert "등록된 등기정보 불러오기" not in registry_source
    assert "등기사항증명서 업로드" in registry_source
    assert "등기자료 분석·등록" in registry_source

    upload_at = render_source.index("_render_registry_upload")
    form_at = render_source.index("with st.form")
    history_at = render_source.index("_render_stock_history(user_id)")
    legal_at = render_source.index("평가 로직과 법령 적용 안내")
    assert upload_at < form_at < legal_at < history_at


def test_enterprise_center_passes_selected_company_context():
    source = (ROOT / "enterprise_center.py").read_text(encoding="utf-8")
    route_start = source.index('elif selected_section == "주가평가·등기":')
    route_end = source.index('elif selected_section == "정관검토":')
    route = source[route_start:route_end]

    assert "stock_customer_selector" not in route
    assert "selected_business_no=business_no" in route
    assert "selected_company_name=company_name" in route
