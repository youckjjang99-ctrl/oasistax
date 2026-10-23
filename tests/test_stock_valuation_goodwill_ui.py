"""Offline UI integration: synthetic inputs and in-memory persistence only."""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(autouse=True)
def _restore_offline_service_stubs(monkeypatch):
    import stock_valuation as stock

    for name in (
        "_read_customers", "_load_stock_customer_context", "_load_records",
        "_save_records", "sync_stock_valuation", "_render_stock_history",
    ):
        monkeypatch.setattr(stock, name, getattr(stock, name))


def _valuation_app(legacy=False):
    import pandas as pd
    import streamlit as st
    import stock_valuation as stock

    stock._read_customers = lambda _: pd.DataFrame()
    stock._load_stock_customer_context = lambda *args, **kwargs: None
    stock._load_records = lambda _, **kw: []
    stock._save_records = lambda _, records: st.session_state.__setitem__("test_saved_records", records)
    stock.sync_stock_valuation = lambda *args: True
    stock._render_stock_history = lambda _: None
    if not st.session_state.get("test_initialized"):
        st.session_state.update({
            "stock_company_name": "합성 평가업체",
            "stock_current_shares": "10000",
            "stock_total_assets": "1,000,000,000",
            "stock_total_liabilities": "500000000",
            "stock_year_1": "2025",
            "stock_year_2": "2024",
            "stock_year_3": "2023",
            "stock_net_income_1": "300000000",
            "stock_net_income_2": "200000000",
            "stock_net_income_3": "100,000,000",
        })
        if legacy:
            stock._apply_loaded_record({
                "record_id": "synthetic-legacy",
                "company_name": "합성 평가업체",
                "current_shares": 10000,
                "total_assets": 1_000_000_000,
                "total_liabilities": 500000000,
                "goodwill": 12000000,
                "annual_rows": [
                    {"year": 2025, "adjusted_net_income": 300000000},
                    {"year": 2024, "adjusted_net_income": 200000000},
                    {"year": 2023, "adjusted_net_income": 100_000_000},
                ],
            })
        st.session_state["test_initialized"] = True
    stock.render_stock_valuation_page("synthetic-user", "합성 사용자")


def _calculate(app):
    next(button for button in app.button if button.label == "주가평가 계산").click().run()
    assert not app.exception


def test_fresh_ui_automatically_calculates_displays_and_saves_goodwill():
    app = AppTest.from_function(_valuation_app, default_timeout=20).run()
    assert not app.exception
    assert app.selectbox(key="stock_goodwill_mode").value == "auto"
    _calculate(app)
    assert app.session_state["stock_last_result"]["goodwill"] == pytest.approx(252720000)
    assert any(expander.label == "영업권 계산 근거" for expander in app.expander)
    app.button(key="stock_save_record").click().run()
    assert not app.exception
    saved = app.session_state["test_saved_records"][0]
    assert saved["goodwill_mode"] == "auto"
    assert saved["goodwill"] == pytest.approx(252720000)
    assert saved["result"]["goodwill"] == saved["goodwill"]
    assert saved["goodwill_details"]["discount_factor"] == 3.7908


def test_form_mode_switch_preserves_manual_zero_and_exclusion_reason():
    app = AppTest.from_function(_valuation_app, default_timeout=20).run()
    _calculate(app)
    app.selectbox(key="stock_goodwill_mode").select("manual")
    app.text_input(key="stock_goodwill").input("0")
    _calculate(app)
    assert app.session_state["stock_last_result"]["goodwill"] == 0
    assert app.session_state["stock_last_result"]["goodwill_mode"] == "manual"
    app.selectbox(key="stock_goodwill_mode").select("excluded")
    _calculate(app)
    assert app.error
    assert "stock_last_result" not in app.session_state
    assert not any(button.key == "stock_save_record" for button in app.button)
    app.text_input(key="stock_goodwill_exclusion_reason").input("합성 불산입 검토 사유")
    _calculate(app)
    assert app.session_state["stock_last_result"]["goodwill_mode"] == "excluded"
    app.button(key="stock_save_record").click().run()
    assert app.session_state["test_saved_records"][0]["goodwill_exclusion_reason"] == "합성 불산입 검토 사유"


def test_legacy_saved_evaluation_stays_manual_in_actual_ui():
    app = AppTest.from_function(_valuation_app, args=(True,), default_timeout=20).run()
    assert not app.exception
    assert app.selectbox(key="stock_goodwill_mode").value == "manual"
    _calculate(app)
    assert app.session_state["stock_last_result"]["goodwill"] == 12000000


def test_failed_recalculation_does_not_allow_saving_previous_result():
    app = AppTest.from_function(_valuation_app, default_timeout=20).run()
    _calculate(app)
    app.text_input(key="stock_net_income_2").input("")
    _calculate(app)
    assert app.error
    assert "stock_last_inputs" not in app.session_state
    assert not any(button.key == "stock_save_record" for button in app.button)
