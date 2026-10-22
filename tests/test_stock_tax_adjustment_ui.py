"""Offline review/apply guards; no customer documents or provider requests."""
from copy import deepcopy
from datetime import date

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import stock_valuation as stock
from tax_adjustment_valuation import COMPONENTS


BUSINESS = "-".join(("123", "45", "67890"))


def _candidate(year=2025):
    values = {item["key"]: 0 for item in COMPONENTS}
    values.update(income=100_000_000, deduct_taxes=20_000_000)
    return {
        "year": year, "period_start": f"{year}-01-01", "period_end": f"{year}-12-31",
        "values": values, "financial_fields": {"자산총계": 900_000_000},
        "sources": [{"record_id": "synthetic-source", "filename": "synthetic.pdf", "uploaded_at": "synthetic-revision"}],
        "warnings": [], "conflicts": [],
    }


def _arguments():
    candidate = _candidate()
    return {
        "business_no": BUSINESS, "valuation_date": "2026-10-22",
        "target_years": ["2025", "2024", "2023"], "candidates": [candidate],
        "reviewed_values": {2025: candidate["values"]},
        "reviewed_periods": {2025: {"period_start": "2025-01-01", "period_end": "2025-12-31", "shares": "10,000"}},
        "review_confirmed": True, "normal_periods_confirmed": True,
    }


def test_prepare_retains_annual_loss_and_does_not_prepare_historical_nav():
    args = _arguments()
    args["reviewed_values"][2025]["income"] = -100_000_000
    updates, review = stock._prepare_tax_adjustment_application(**args)
    assert updates == {"stock_net_income_1": "-120,000,000", "stock_shares_1": "10,000"}
    assert review["annual_rows"][0]["sources"][0]["record_id"] == "synthetic-source"
    assert review["scope"] == "adjusted_income_and_year_end_shares_only"


@pytest.mark.parametrize("key", ["review_confirmed", "normal_periods_confirmed"])
def test_reflection_requires_both_legal_review_confirmations(key):
    args = _arguments()
    args[key] = False
    with pytest.raises(ValueError, match="검토"):
        stock._prepare_tax_adjustment_application(**args)


def test_missing_deduction_is_not_an_implicit_zero():
    args = _arguments()
    args["reviewed_values"][2025]["deduct_taxes"] = ""
    with pytest.raises(ValueError, match="미확인"):
        stock._prepare_tax_adjustment_application(**args)


@pytest.mark.parametrize("shares", ["", "0", "-1", "1.5", "NaN", "Infinity"])
def test_year_end_shares_never_fall_back_to_current_shares(shares):
    args = _arguments()
    args["reviewed_periods"][2025]["shares"] = shares
    with pytest.raises(ValueError, match="주식총수"):
        stock._prepare_tax_adjustment_application(**args)


def test_short_period_requires_separate_annualization_instead_of_approval():
    args = _arguments()
    args["candidates"][0]["period_start"] = "2025-04-01"
    args["reviewed_periods"][2025]["period_start"] = "2025-04-01"
    with pytest.raises(ValueError, match="연환산"):
        stock._prepare_tax_adjustment_application(**args)


def test_reviewed_period_cannot_silently_replace_an_explicit_source_period():
    args = _arguments()
    args["candidates"][0]["period_start"] = "2025-04-01"
    with pytest.raises(ValueError, match="원문"):
        stock._prepare_tax_adjustment_application(**args)


@pytest.mark.parametrize("target_years", [["2024", "2025", "2023"], ["2025", "2025", "2023"], ["2025", "", "2023"]])
def test_target_years_must_be_three_distinct_latest_first_years(target_years):
    args = _arguments()
    args["target_years"] = target_years
    with pytest.raises(ValueError):
        stock._prepare_tax_adjustment_application(**args)


def test_nonconsecutive_or_stale_ordinary_target_years_are_blocked():
    args = _arguments()
    args["target_years"] = ["2025", "2023", "2020"]
    with pytest.raises(ValueError, match="연속"):
        stock._prepare_tax_adjustment_application(**args)
    args["target_years"] = ["2024", "2023", "2022"]
    args["candidates"][0].update(year=2024, period_start="2024-01-01", period_end="2024-12-31")
    args["reviewed_values"][2024] = args["reviewed_values"].pop(2025)
    args["reviewed_periods"] = {2024: {"period_start": "2024-01-01", "period_end": "2024-12-31", "shares": "10000"}}
    with pytest.raises(ValueError, match="직전 완료"):
        stock._prepare_tax_adjustment_application(**args)


def test_expert_checkbox_does_not_bypass_short_year_annualization_guard():
    args = _arguments()
    args.update(normal_periods_confirmed=False, special_adjustments_reviewed=True, special_adjustment_note="합성 연환산 검토")
    args["candidates"][0]["period_start"] = "2025-04-01"
    args["reviewed_periods"][2025]["period_start"] = "2025-04-01"
    with pytest.raises(ValueError, match="연환산"):
        stock._prepare_tax_adjustment_application(**args)


def test_no_source_no_reflection():
    args = _arguments()
    args["candidates"][0]["sources"] = []
    with pytest.raises(ValueError, match="원본"):
        stock._prepare_tax_adjustment_application(**args)


def test_special_capital_or_short_period_requires_expert_note():
    args = _arguments()
    args.update(normal_periods_confirmed=False, special_adjustments_reviewed=True)
    with pytest.raises(ValueError, match="전문가 검토 근거"):
        stock._prepare_tax_adjustment_application(**args)
    args["special_adjustment_note"] = "합성 검토자: 유상증자 월할 순손익·환산주식수 확인"
    args["reviewed_values"][2025]["capital_adjustment"] = 5_000_000
    args["reviewed_periods"][2025].update(expert_final_income="85000000", expert_final_shares="15000")
    updates, review = stock._prepare_tax_adjustment_application(**args)
    assert updates["stock_net_income_1"] == "85,000,000"
    assert review["special_adjustments_reviewed"] is True
    assert review["special_adjustment_note"] == args["special_adjustment_note"]
    assert updates["stock_shares_1"] == "15,000"
    assert review["annual_rows"][0]["source_year_end_shares"] == "10,000"


def test_expert_checkbox_and_note_without_explicit_final_values_cannot_apply():
    args = _arguments()
    args.update(normal_periods_confirmed=False, special_adjustments_reviewed=True, special_adjustment_note="합성 전문가 검토")
    with pytest.raises(ValueError, match="주식총수"):
        stock._prepare_tax_adjustment_application(**args)
    args["reviewed_periods"][2025]["expert_final_shares"] = "15000"
    with pytest.raises(ValueError, match="최종 순손익액"):
        stock._prepare_tax_adjustment_application(**args)
    args["reviewed_periods"][2025]["expert_final_income"] = "123"
    with pytest.raises(ValueError, match="구성항목 합계"):
        stock._prepare_tax_adjustment_application(**args)


@pytest.mark.parametrize("component", ["capital_adjustment", "reserve_allocation"])
def test_nonzero_special_adjustment_cannot_bypass_expert_final_review(component):
    args = _arguments()
    args["reviewed_values"][2025][component] = 5_000_000
    with pytest.raises(ValueError, match="전문가 검토 경로"):
        stock._prepare_tax_adjustment_application(**args)


def test_multiple_business_identity_source_requires_its_own_confirmation():
    args = _arguments()
    args["candidates"][0]["identity_review_required"] = True
    with pytest.raises(ValueError, match="복수 사업자번호"):
        stock._prepare_tax_adjustment_application(**args)
    args["identity_confirmed"] = True
    _, review = stock._prepare_tax_adjustment_application(**args)
    assert review["identity_confirmed"] is True


def test_negative_ordinary_adjustment_magnitude_is_not_silently_double_subtracted():
    args = _arguments()
    args["reviewed_values"][2025]["deduct_taxes"] = -100
    with pytest.raises(ValueError, match="부호"):
        stock._prepare_tax_adjustment_application(**args)


def test_later_valuation_requires_explicit_law_change_review_without_daily_expiry(monkeypatch):
    monkeypatch.setattr(stock, "_stock_legal_today", lambda: date(2026, 11, 10))
    args = _arguments()
    args["valuation_date"] = "2026-11-01"
    with pytest.raises(ValueError, match="법령 변경"):
        stock._prepare_tax_adjustment_application(**args)
    args["law_changes_reviewed"] = True
    _, review = stock._prepare_tax_adjustment_application(**args)
    assert review["law_changes_reviewed"] is True


@pytest.mark.parametrize("valuation_date", ["2026-09-30", "2026-10-23"])
def test_unverified_historical_or_future_valuation_date_is_blocked(valuation_date, monkeypatch):
    monkeypatch.setattr(stock, "_stock_legal_today", lambda: date(2026, 10, 22))
    args = _arguments()
    args.update(valuation_date=valuation_date, law_changes_reviewed=True)
    with pytest.raises(ValueError, match="적용 범위"):
        stock._prepare_tax_adjustment_application(**args)


def test_explicit_non_december_fiscal_year_is_supported():
    args = _arguments()
    args["candidates"][0].update(year=2026, period_start="2025-07-01", period_end="2026-06-30")
    args["target_years"] = ["2026", "2025", "2024"]
    args["reviewed_values"][2026] = args["reviewed_values"].pop(2025)
    args["reviewed_periods"] = {2026: {"period_start": "2025-07-01", "period_end": "2026-06-30", "shares": "10000"}}
    updates, review = stock._prepare_tax_adjustment_application(**args)
    assert updates["stock_net_income_1"] == "80,000,000"
    assert review["annual_rows"][0]["year"] == 2026


def test_apply_is_scoped_and_preserves_unselected_years_and_current_nav(monkeypatch):
    state = {"stock_business_no": BUSINESS, "stock_net_income_2": "existing-year", "stock_total_assets": "existing-nav", "stock_last_result": {"old": True}}
    monkeypatch.setattr(stock.st, "session_state", state)
    updates, review = stock._prepare_tax_adjustment_application(**_arguments())
    stock._apply_reviewed_tax_adjustment(updates, review, user_id="synthetic-owner")
    assert state["stock_net_income_1"] == "80,000,000"
    assert state["stock_net_income_2"] == "existing-year"
    assert state["stock_total_assets"] == "existing-nav"
    assert "stock_last_result" not in state
    assert state["stock_tax_adjustment_review"][0]["reviewed_by"] == "synthetic-owner"
    state["stock_business_no"] = "".join(("999", "99", "99999"))
    before = deepcopy(state)
    with pytest.raises(ValueError, match="기업 선택"):
        stock._apply_reviewed_tax_adjustment(updates, review, user_id="synthetic-owner")
    assert state == before


def _render_review_app():
    import pandas as pd
    import streamlit as st
    import enterprise_documents as documents
    import stock_valuation as stock
    import tax_adjustment_valuation as engine

    st.session_state.update({
        "stock_business_no": "-".join(("123", "45", "67890")),
        "stock_valuation_date": "2026-10-22", "stock_year_1": "2025",
        "stock_year_2": "2024", "stock_year_3": "2023", "stock_shares_1": "10000",
    })
    values = {item["key"]: 0 for item in engine.COMPONENTS}
    values["income"] = 100
    documents.load_enterprise_document_context = lambda *a, **k: {"records": [{
        "record_id": "synthetic-source", "business_no": st.session_state["stock_business_no"],
        "document_type": "tax_adjustment", "filename": "synthetic.pdf", "uploaded_at": "synthetic-revision",
        "extracted_fields": {"valuation_periods": [{"year": 2025, "period_start": "2025-01-01", "period_end": "2025-12-31", "values": values}]},
    }]}
    stock._render_stock_tax_adjustment("synthetic-owner")


def test_actual_review_ui_does_not_apply_without_explicit_confirmations(monkeypatch):
    import enterprise_documents as documents
    monkeypatch.setattr(documents, "load_enterprise_document_context", documents.load_enterprise_document_context)
    app = AppTest.from_function(_render_review_app, default_timeout=20).run()
    assert not app.exception
    assert "stock_net_income_1" not in app.session_state
    app.multiselect[0].set_value([2025]).run()
    assert not app.exception
    apply = next(button for button in app.button if button.label == "검토한 순손익액·주식수 반영")
    assert apply.disabled
    for checkbox in app.checkbox:
        checkbox.check()
    app.selectbox[0].select("12개월 사업연도이며 증감자·일시 환입 등 별도 조정 사유 없음")
    app.run()
    apply = next(button for button in app.button if button.label == "검토한 순손익액·주식수 반영")
    apply.click().run()
    assert not app.exception
    assert app.session_state["stock_net_income_1"] == "100"
    assert app.success
