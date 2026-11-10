"""Synthetic capital-source, valuation and UI regression. No provider calls."""
from copy import deepcopy
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import cloud_sync
import stock_valuation as stock
from stock_capital import build_capital_share_plan
import stock_capital_ui as capital


BUSINESS = "-".join(("123", "45", "67890"))
PERIODS = [{"year": year, "period_start": f"{year}-01-01", "period_end": f"{year}-12-31"}
           for year in (2023, 2024, 2025)]


def source(when="2026-06-01"):
    event = {"before_shares": 100, "after_shares": 500, "before_capital": 10000,
             "after_capital": 50000, "par_value": 100, "effective_date": when,
             "kind": "paid_increase", "issue_price": None, "review_required": False}
    return {"registry_parser_version": "capital-history-v1", "발행주식총수": 500,
            "자본금": 50000, "1주당액면가액": 100, "capital_events": [event],
            "capital_history": [
                {"shares": 100, "capital": 10000, "par_value": 100, "effective_date": "2020-01-01"},
                {"shares": 500, "capital": 50000, "par_value": 100, "effective_date": when}],
            "capital_history_review_required": False, "capital_history_warnings": []}


def state(registry=None):
    value = {"stock_business_no": BUSINESS, "stock_valuation_date": "2026-10-24",
             "stock_current_shares": "100", "stock_capital": "10,000",
             "stock_last_registry_data": deepcopy(registry or source()),
             "stock_last_result": {"old": "preserved saved version"}}
    for i, year in enumerate((2025, 2024, 2023), 1):
        value.update({f"stock_year_{i}": str(year), f"stock_shares_{i}": "100",
                      f"stock_net_income_{i}": "100,000"})
    return value


def apply(values, registry=None, **kwargs):
    original = registry or values["stock_last_registry_data"]
    plan = build_capital_share_plan(original, values["stock_valuation_date"], PERIODS, history_confirmed=True)
    return capital.apply_capital_plan(values, user_id="synthetic-owner", business_no=BUSINESS,
                                     source=original, plan=plan, **kwargs)


def inputs(values):
    return {**capital._state_inputs(values), "total_assets": 1000000, "total_liabilities": 200000,
            "capital_source": capital.capital_registry_source(values["stock_last_registry_data"]),
            "capital_review": deepcopy(values.get("stock_capital_review", {}))}


def test_current_year_paid_increase_changes_nav_count_not_past_eps_or_assets():
    values = state()
    old = deepcopy(values)
    review = apply(values)
    data = inputs(values)
    result = stock.calculate_stock_valuation(data)
    assert data["current_shares"] == 500
    assert [row["shares"] for row in result["annual_details"]] == [100] * 3
    assert result["adjusted_assets"] == 1000000
    assert result["nav_per_share"] == 800000 / 500
    assert not review["manual"] and not review["plan"]["requires_income_adjustment"]
    assert values["stock_last_registry_data"] == old["stock_last_registry_data"]
    assert [values[f"stock_net_income_{i}"] for i in range(1, 4)] == ["100,000"] * 3


@pytest.mark.parametrize("key,value", [("stock_valuation_date", "2026-10-23"), ("stock_current_shares", "600"),
                                       ("stock_net_income_1", "999"), ("stock_shares_2", "500")])
def test_review_is_invalidated_by_date_count_or_income_change(key, value):
    values = state()
    apply(values)
    values[key] = value
    with pytest.raises(ValueError, match="変更|변경"):
        stock.calculate_stock_valuation(inputs(values))


def test_changed_registry_or_company_cannot_reuse_review():
    values = state()
    apply(values)
    values["stock_last_registry_data"]["자본금"] = 999
    with pytest.raises(ValueError, match="등기"):
        stock.calculate_stock_valuation(inputs(values))
    values = state()
    apply(values)
    values["stock_business_no"] = "-".join(("987", "65", "43210"))
    with pytest.raises(ValueError, match="검토"):
        stock.calculate_stock_valuation(inputs(values))


def test_registered_capital_history_requires_review_and_explicit_annual_counts():
    with pytest.raises(ValueError, match="검토"):
        stock.calculate_stock_valuation(inputs(state()))
    values = state()
    data = inputs(values)
    data.pop("capital_source")
    data["annual_rows"][0]["shares"] = None
    with pytest.raises(ValueError, match="발행주식총수"):
        stock.calculate_stock_valuation(data)


def test_manual_prior_capital_adjustment_needs_explicit_final_values_and_note():
    registry = source("2024-06-01")
    values = state(registry)
    original = deepcopy(values)
    with pytest.raises(ValueError, match="전문가"):
        apply(values)
    assert values == original
    rows = [{"year": year, "shares": 500, "adjusted_net_income": 123456} for year in (2023, 2024, 2025)]
    with pytest.raises(ValueError, match="근거"):
        apply(values, manual_rows=rows, manual_current=500)
    assert values == original
    apply(values, manual_rows=rows, manual_current=500, note="합성 검토자: 원문과 월할 및 순손익 조정 확인")
    assert stock.calculate_stock_valuation(inputs(values))
    assert all(row["adjusted_net_income"] == 123456 for row in inputs(values)["annual_rows"])


def test_ambiguous_new_parser_snapshot_does_not_resurrect_old_counts(monkeypatch):
    old = {"발행주식총수": 100, "자본금": 10000, "1주당액면가액": 100,
           "_versions": [{"synthetic_version": "a"}]}
    incoming = {"registry_parser_version": "capital-history-v1", "발행주식총수": None,
                "자본금": None, "1주당액면가액": None, "capital_history": [], "capital_events": [],
                "capital_history_review_required": True, "_versions": [{"synthetic_version": "b"}]}
    merged = stock._merge_registry_capital_fields(old, incoming)
    assert merged["발행주식총수"] is None and merged["1주당액면가액"] is None
    assert all(item in merged["_versions"] for item in old["_versions"] + incoming["_versions"])
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(cloud_sync, "_select_snapshot_row", lambda *args: {"business_no": BUSINESS, "registry_data": old})
    captured = {}
    monkeypatch.setattr(cloud_sync, "_safe_upsert", lambda *args: captured.update(row=deepcopy(args[3][0])) or (True, "ok"))
    monkeypatch.setattr(cloud_sync, "invalidate_snapshot_reads", lambda *args: None)
    assert cloud_sync.sync_registry_snapshot("synthetic-owner", BUSINESS, incoming)[0]
    saved = captured["row"]["registry_data"]
    assert saved["발행주식총수"] is None and saved["자본금"] is None and saved["1주당액면가액"] is None
    assert all(item in saved["_versions"] for item in old["_versions"] + incoming["_versions"])
    assert {key: value for key, value in old.items() if key != "_versions"} in saved["_versions"]


def test_review_is_saved_and_restored_without_modifying_older_version(monkeypatch):
    values = state()
    apply(values)
    data = inputs(values)
    record = stock._build_stock_record(data, stock.calculate_stock_valuation(data), record_id="synthetic-id", saved_by="synthetic", saved_at="synthetic")
    before = deepcopy(record)
    target = {}
    monkeypatch.setattr(stock.st, "session_state", target)
    stock._apply_loaded_record(record)
    assert target["stock_capital_review"] == data["capital_review"]
    assert stock._stock_record_inputs(record)["capital_review"] == data["capital_review"]
    assert record == before


def test_capital_panel_renders_with_actual_streamlit_widget_api():
    registry = source()
    values = state(registry)
    app = AppTest.from_string("import streamlit as st\nfrom stock_capital_ui import render_stock_capital_review\n"
                             f"st.session_state.update({values!r})\nrender_stock_capital_review('synthetic-owner')").run()
    assert not app.exception
    assert any(button.label == "평가기준일별 주식수 확인·반영" for button in app.button)
    assert len(app.checkbox) == 2
