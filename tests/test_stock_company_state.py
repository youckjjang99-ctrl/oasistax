"""Synthetic regression coverage for company-scoped valuation state.

No customer documents, network calls, or persistent stores are used here.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import stock_valuation as stock


OWNER = "synthetic-owner-a"
OTHER_OWNER = "synthetic-owner-b"
BUSINESS = "-".join(("123", "45", "67890"))
OTHER_BUSINESS = "-".join(("987", "65", "43210"))
CORPORATE = "-".join(("110111", "1234567"))
OTHER_CORPORATE = "-".join(("110111", "7654321"))


def _customer(business_no=BUSINESS, name="합성법인 A", revision="revision-1"):
    return {
        "업체명": name,
        "사업자등록번호": business_no,
        "법인등록번호": CORPORATE if business_no == BUSINESS else OTHER_CORPORATE,
        "_customer_id": "synthetic-id-a" if business_no == BUSINESS else "synthetic-id-b",
        "_cloud_updated_at": revision,
    }


def _inputs(business_no=BUSINESS, name="합성법인 A"):
    return {
        "company_name": name,
        "business_no": business_no,
        "corporate_no": CORPORATE,
        "address": "합성 주소",
        "establishment": "2020-01-01",
        "valuation_date": date.today().isoformat(),
        "current_shares": 100,
        "par_value": 5000,
        "capital": 500000,
        "authorized_shares": 1000,
        "share_classes": [],
        "valuation_type": "일반법인(3:2)",
        "annual_rows": [
            {"year": str(year), "adjusted_net_income": amount, "shares": 100}
            for year, amount in ((2025, 300000), (2024, 200000), (2023, 100000))
        ],
        "total_assets": 1000000,
        "total_liabilities": 200000,
        "asset_additions": None,
        "asset_deductions": None,
        "liability_additions": None,
        "liability_deductions": None,
        "other_adjustments": None,
        "goodwill": None,
        "goodwill_mode": "manual",
        "goodwill_acquired_intangible_remaining": 0,
        "goodwill_exclusion_reason": "",
        "capitalization_rate": 0.10,
        "nav_floor_rate": 0.80,
        "premium_exempt": True,
        "premium_rate": 0,
        "tax_adjustment_review": [{"business_no": business_no, "source_id": "synthetic-tax"}],
    }


def _record(business_no=BUSINESS, name="합성법인 A"):
    inputs = _inputs(business_no, name)
    result = stock.calculate_stock_valuation(inputs)
    return stock._build_stock_record(
        inputs, result, record_id=f"synthetic-{business_no}",
        saved_by="합성 작성자", saved_at="2026-10-23 10:00:00",
    )


@pytest.fixture(autouse=True)
def _offline_stores(monkeypatch):
    monkeypatch.setattr(stock, "_load_records", lambda _, **kw: [])
    monkeypatch.setattr(stock, "_save_records", lambda *args: None)
    monkeypatch.setattr(stock, "sync_stock_valuation", lambda *args: True)


def _context_services(monkeypatch, state):
    monkeypatch.setattr(stock.st, "session_state", state)

    def financial(user_id, row):
        business = row["사업자등록번호"]
        state.update({
            "stock_company_name": row["업체명"],
            "stock_business_no": business,
            "stock_corporate_no": row["법인등록번호"],
            "stock_total_assets": "1,000,000" if business == BUSINESS else "2,000,000",
            "stock_total_liabilities": "200,000",
        })
        return True, "synthetic financial source"

    financial_mock = Mock(side_effect=financial)

    def registry(user_id, business_no, **identity):
        data = {"사업자등록번호": business_no, "발행주식총수": 100,
                "1주당액면가액": 5000, "자본금": 500000}
        if identity.get("apply_to_state", True):
            state.update({"stock_current_shares": "100", "stock_par_value": "5,000",
                          "stock_capital": "500,000", "stock_last_registry_data": data})
        return data

    monkeypatch.setattr(stock, "_apply_customer_financial_data", financial_mock)
    monkeypatch.setattr(stock, "_restore_registry_for_business", Mock(side_effect=registry))
    return financial_mock


def test_company_bank_restores_widget_values_result_and_tax_review(monkeypatch):
    values = _inputs()
    result = stock.calculate_stock_valuation(values)
    state = {
        "stock_business_no": BUSINESS,
        "stock_company_name": "합성법인 A",
        "stock_current_shares": "100",
        "stock_par_value": "5,000",
        "stock_capital": "500,000",
        "stock_last_inputs": deepcopy(values),
        "stock_last_result": deepcopy(result),
        "stock_tax_adjustment_review": deepcopy(values["tax_adjustment_review"]),
        "unrelated_widget": "must remain unrelated",
    }
    monkeypatch.setattr(stock.st, "session_state", state)
    stock._remember_stock_company_state(OWNER, BUSINESS)
    for key in ("stock_business_no", "stock_company_name", "stock_current_shares",
                "stock_par_value", "stock_capital", "stock_last_inputs", "stock_last_result",
                "stock_tax_adjustment_review"):
        state.pop(key, None)

    assert stock._restore_stock_company_state(OWNER, BUSINESS)
    assert state["stock_business_no"] == BUSINESS
    assert state["stock_current_shares"] == "100"
    assert state["stock_par_value"] == "5,000"
    assert state["stock_capital"] == "500,000"
    assert state["stock_last_result"] == result
    assert state["stock_last_inputs"] == values
    assert state["stock_tax_adjustment_review"] == values["tax_adjustment_review"]
    assert state["unrelated_widget"] == "must remain unrelated"


def test_company_bank_is_scoped_to_owner_business_and_copies_mutable_values(monkeypatch):
    state = {"stock_business_no": BUSINESS, "stock_current_shares": "100",
             "stock_tax_adjustment_review": [{"source_id": "immutable-snapshot"}]}
    monkeypatch.setattr(stock.st, "session_state", state)
    stock._remember_stock_company_state(OWNER, BUSINESS)
    state["stock_tax_adjustment_review"][0]["source_id"] = "changed-in-live-form"
    assert not stock._restore_stock_company_state(OTHER_OWNER, BUSINESS)
    assert not stock._restore_stock_company_state(OWNER, OTHER_BUSINESS)
    assert stock._restore_stock_company_state(OWNER, BUSINESS)
    assert state["stock_tax_adjustment_review"][0]["source_id"] == "immutable-snapshot"


def test_stale_autoload_sentinel_does_not_leave_missing_registry_widgets_blank(monkeypatch):
    row = _customer()
    context_key = f"{BUSINESS}:{row['_customer_id']}:{row['_cloud_updated_at']}"
    state = {"stock_customer_context_key": context_key,
             "stock_auto_loaded_customer_key": context_key,
             "stock_registry_restored_key": BUSINESS}
    _context_services(monkeypatch, state)

    selected = stock._load_stock_customer_context(
        OWNER, pd.DataFrame([row]), selected_business_no=BUSINESS,
    )

    assert selected is not None
    assert state["stock_business_no"] == BUSINESS
    assert state["stock_current_shares"] == "100"
    assert state["stock_par_value"] == "5,000"
    assert state["stock_capital"] == "500,000"


def test_company_switch_restores_previous_company_but_does_not_mix_new_company(monkeypatch):
    state = {}
    _context_services(monkeypatch, state)
    customers = pd.DataFrame([_customer(), _customer(OTHER_BUSINESS, "합성법인 B")])
    stock._load_stock_customer_context(OWNER, customers, selected_business_no=BUSINESS)
    inputs = _inputs()
    result = stock.calculate_stock_valuation(inputs)
    state.update({"stock_net_income_1": "444,444", "stock_last_inputs": inputs,
                  "stock_last_result": result,
                  "stock_tax_adjustment_review": [{"source_id": "a-only"}],
                  "stock_premium_custom_pct": 17.0,
                  "stock_valuation_type": "순자산가치만 적용"})

    stock._load_stock_customer_context(OWNER, customers, selected_business_no=OTHER_BUSINESS)
    assert state["stock_business_no"] == OTHER_BUSINESS
    assert not state.get("stock_last_result")
    assert not state.get("stock_tax_adjustment_review")
    assert state.get("stock_premium_custom_pct") != 17.0
    assert state.get("stock_valuation_type") != "순자산가치만 적용"

    stock._load_stock_customer_context(OWNER, customers, selected_business_no=BUSINESS)
    assert state["stock_net_income_1"] == "444,444"
    assert state["stock_last_result"] == result
    assert state["stock_tax_adjustment_review"] == [{"source_id": "a-only"}]
    assert state["stock_premium_custom_pct"] == 17.0
    assert state["stock_valuation_type"] == "순자산가치만 적용"


def test_customer_revision_change_preserves_same_company_calculation(monkeypatch):
    state = {}
    _context_services(monkeypatch, state)
    stock._load_stock_customer_context(OWNER, pd.DataFrame([_customer()]), selected_business_no=BUSINESS)
    state.update({"stock_net_income_1": "reviewed-income",
                  "stock_last_result": {"synthetic_reviewed": True},
                  "stock_tax_adjustment_review": [{"source_id": "reviewed-tax"}]})

    stock._load_stock_customer_context(
        OWNER, pd.DataFrame([_customer(revision="revision-2")]), selected_business_no=BUSINESS,
    )
    assert state["stock_net_income_1"] == "reviewed-income"
    assert state["stock_last_result"] == {"synthetic_reviewed": True}
    assert state["stock_tax_adjustment_review"] == [{"source_id": "reviewed-tax"}]


def test_owner_change_for_same_company_does_not_reuse_previous_owner_draft(monkeypatch):
    state = {}
    _context_services(monkeypatch, state)
    customers = pd.DataFrame([_customer()])
    stock._load_stock_customer_context(OWNER, customers, selected_business_no=BUSINESS)
    state.update({"stock_net_income_1": "owner-a-private-review",
                  "stock_last_result": {"owner_a_private": True},
                  "stock_tax_adjustment_review": [{"source_id": "owner-a-only"}]})

    stock._load_stock_customer_context(OTHER_OWNER, customers, selected_business_no=BUSINESS)
    assert state["stock_business_no"] == BUSINESS
    assert state.get("stock_net_income_1") != "owner-a-private-review"
    assert not state.get("stock_last_result")
    assert not state.get("stock_tax_adjustment_review")


def test_no_resolvable_customer_clears_previous_company_widgets(monkeypatch):
    state = {"stock_business_no": BUSINESS, "stock_company_name": "이전 합성업체",
             "stock_current_shares": "100", "stock_last_result": {"old": True}}
    _context_services(monkeypatch, state)
    assert stock._load_stock_customer_context(OWNER, pd.DataFrame()) is None
    assert not state.get("stock_business_no")
    assert not state.get("stock_current_shares")
    assert not state.get("stock_last_result")


def test_bank_rejects_values_from_a_different_company(monkeypatch):
    state = {"stock_business_no": OTHER_BUSINESS, "stock_current_shares": "999"}
    monkeypatch.setattr(stock.st, "session_state", state)
    stock._remember_stock_company_state(OWNER, BUSINESS)
    assert not stock._restore_stock_company_state(OWNER, BUSINESS)


def test_explicit_unknown_business_cannot_fall_back_to_same_company_name(monkeypatch):
    state = {"stock_business_no": BUSINESS, "stock_current_shares": "old-company"}
    _context_services(monkeypatch, state)
    selected = stock._load_stock_customer_context(
        OWNER, pd.DataFrame([_customer()]), selected_business_no=OTHER_BUSINESS,
        selected_company_name="합성법인 A",
    )
    assert selected is None
    assert not state.get("stock_business_no")
    assert not state.get("stock_current_shares")


def test_all_company_specific_valuation_settings_are_cleared(monkeypatch):
    keys = ["stock_valuation_date", "stock_valuation_type", "stock_cap_rate",
            "stock_nav_floor", "stock_premium_exempt", "stock_premium_rate",
            "stock_cap_rate_pct", "stock_nav_floor_pct", "stock_premium_selection",
            "stock_premium_custom_pct", "stock_goodwill_mode", "stock_last_result"]
    state = {key: "synthetic-old-value" for key in keys}
    state["unrelated_widget"] = "keep"
    monkeypatch.setattr(stock.st, "session_state", state)
    stock._clear_stock_customer_state()
    assert all(key not in state for key in keys)
    assert state["unrelated_widget"] == "keep"


def test_loaded_record_restores_saved_result_and_actual_percentage_widgets(monkeypatch):
    record = _record()
    record["capitalization_rate"] = 0.12
    record["nav_floor_rate"] = 0.85
    record["premium_exempt"] = False
    record["premium_rate"] = 0.17
    inputs = {key: value for key, value in record.items() if key in _inputs()}
    record["result"] = stock.calculate_stock_valuation(inputs)
    state = {"stock_cap_rate_pct": 9.0, "stock_nav_floor_pct": 70.0,
             "stock_premium_selection": "미적용", "stock_premium_custom_pct": 0.0}
    monkeypatch.setattr(stock.st, "session_state", state)
    stock._apply_loaded_record(record)

    assert state["stock_last_result"] == record["result"]
    assert state["stock_last_inputs"]["business_no"] == BUSINESS
    assert state["stock_last_inputs"]["capitalization_rate"] == 0.12
    assert state["stock_cap_rate_pct"] == pytest.approx(12.0)
    assert state["stock_nav_floor_pct"] == pytest.approx(85.0)
    assert state["stock_premium_selection"] == "직접입력"
    assert state["stock_premium_custom_pct"] == pytest.approx(17.0)


def test_saved_version_labels_disambiguate_calculations_in_the_same_second():
    first = _record()
    second = {**deepcopy(first), "record_id": "synthetic-other-version"}
    assert first["saved_at"] == second["saved_at"]
    assert stock._record_label(first) != stock._record_label(second)


def _offline_app(monkeypatch, *, real_history=False, navigation=False):
    customers = pd.DataFrame([_customer(), _customer(OTHER_BUSINESS, "합성법인 B")])
    monkeypatch.setattr(stock, "_read_customers", lambda _: customers)

    def financial(user, row):
        stock.st.session_state.update({
            "stock_company_name": row["업체명"], "stock_business_no": row["사업자등록번호"],
            "stock_total_assets": "1,000,000", "stock_total_liabilities": "200,000",
            "stock_year_1": "2025", "stock_year_2": "2024", "stock_year_3": "2023",
            "stock_net_income_1": "300,000", "stock_net_income_2": "200,000",
            "stock_net_income_3": "100,000", "stock_goodwill_mode": "manual",
        })
        return True, "synthetic financial source"

    def registry(user, business, **identity):
        data = {"사업자등록번호": business, "발행주식총수": 100,
                "1주당액면가액": 5000, "자본금": 500000}
        if identity.get("apply_to_state", True):
            stock.st.session_state.update({
                "stock_current_shares": "100", "stock_par_value": "5,000",
                "stock_capital": "500,000", "stock_last_registry_data": data,
            })
        return data

    monkeypatch.setattr(stock, "_apply_customer_financial_data", financial)
    monkeypatch.setattr(stock, "_restore_registry_for_business", registry)
    monkeypatch.setattr(stock, "_persist_stock_calculation", lambda *args, **kwargs: {
        "local_saved": True, "cloud_saved": True, "record_id": "synthetic-offline-only",
    })
    monkeypatch.setattr(stock, "_render_stock_tax_adjustment", lambda _: stock.st.write("합성 세무조정 검토"))
    if not real_history:
        monkeypatch.setattr(stock, "_render_stock_history", lambda _: None)
    code = "import stock_valuation as stock\n"
    if navigation:
        code += (
            "import streamlit as st\n"
            "route = st.radio('합성 메뉴', ['주가평가', '다른 메뉴'], key='_test_nav')\n"
            "if route == '주가평가':\n"
            f"    stock.render_stock_valuation_page({OWNER!r}, selected_business_no={BUSINESS!r})\n"
        )
    else:
        code += f"stock.render_stock_valuation_page({OWNER!r}, selected_business_no={BUSINESS!r})\n"
    return AppTest.from_string(
        code,
        default_timeout=20,
    ).run()


def test_actual_ui_subtab_roundtrip_retains_registry_values_and_calculated_result(monkeypatch):
    app = _offline_app(monkeypatch)
    assert not app.exception
    assert app.text_input(key="stock_current_shares").value == "100"
    assert app.text_input(key="stock_par_value").value == "5,000"
    assert app.text_input(key="stock_capital").value == "500,000"
    next(button for button in app.button if button.label == "주가평가 계산").click().run()
    assert not app.exception
    saved_result = deepcopy(app.session_state["stock_last_result"])

    app.radio(key="stock_valuation_detail_view").set_value("세무조정계산서 반영").run()
    assert not app.exception
    app.radio(key="stock_valuation_detail_view").set_value("주가평가 계산").run()
    assert not app.exception
    assert app.text_input(key="stock_current_shares").value == "100"
    assert app.text_input(key="stock_par_value").value == "5,000"
    assert app.text_input(key="stock_capital").value == "500,000"
    assert app.session_state["stock_last_result"] == saved_result
    assert any(metric.label == "1주당 최종 평가액" for metric in app.metric)


def test_actual_ui_menu_roundtrip_preserves_calculation_without_explicit_save(monkeypatch):
    app = _offline_app(monkeypatch, navigation=True)
    assert not app.exception
    next(button for button in app.button if button.label == "주가평가 계산").click().run()
    assert not app.exception
    calculated = deepcopy(app.session_state["stock_last_result"])
    app.radio(key="_test_nav").set_value("다른 메뉴").run()
    assert not app.exception
    app.radio(key="_test_nav").set_value("주가평가").run()
    assert not app.exception
    assert app.text_input(key="stock_current_shares").value == "100"
    assert app.text_input(key="stock_par_value").value == "5,000"
    assert app.session_state["stock_last_result"] == calculated
    assert any(metric.label == "1주당 최종 평가액" for metric in app.metric)


def test_actual_ui_history_is_company_scoped_and_loads_without_widget_mutation_error(monkeypatch):
    records = [_record(), _record(OTHER_BUSINESS, "합성법인 B")]
    monkeypatch.setattr(stock, "_load_records", lambda _, **kw: deepcopy(records))
    app = _offline_app(monkeypatch, real_history=True)
    assert not app.exception
    selector = next(item for item in app.selectbox if item.label == "저장 기록")
    assert any("합성법인 A" in item for item in selector.options)
    assert not any("합성법인 B" in item for item in selector.options)
    selector.select_index(1).run()
    assert not app.exception
    next(button for button in app.button if button.label == "선택 평가 불러오기").click().run()
    assert not app.exception
    assert app.text_input(key="stock_business_no").value == BUSINESS
    assert app.session_state["stock_last_result"] == records[0]["result"]
    assert any(metric.label == "1주당 최종 평가액" for metric in app.metric)


def test_registry_refresh_updates_source_but_preserves_reviewed_calculation(monkeypatch):
    values = _inputs()
    result = stock.calculate_stock_valuation(values)
    state = {
        "stock_business_no": BUSINESS, "stock_company_name": "합성법인 A",
        "stock_corporate_no": CORPORATE, "stock_current_shares": "100",
        "stock_par_value": "5,000", "stock_capital": "500,000",
        "stock_authorized_shares": "1,000", "stock_total_assets": "2,000,000",
        "stock_total_liabilities": "400,000", "stock_shares_1": "80",
        "stock_shares_2": "70", "stock_shares_3": "60",
        "stock_net_income_1": "333,333", "stock_last_inputs": deepcopy(values),
        "stock_last_result": deepcopy(result),
        "stock_tax_adjustment_review": deepcopy(values["tax_adjustment_review"]),
    }
    original = deepcopy(state)
    latest = {
        "사업자등록번호": BUSINESS, "법인등록번호": CORPORATE,
        "발행주식총수": 250, "1주당액면가액": 10000,
        "자본금": 2500000, "발행할주식총수": 2000, "saved_at": "synthetic-new-source",
    }
    monkeypatch.setattr(stock.st, "session_state", state)
    restore = Mock(return_value=deepcopy(latest))
    monkeypatch.setattr(stock, "_restore_registry_for_business", restore)

    stock._refresh_stock_registry_context(OWNER)

    restore.assert_called_once_with(
        OWNER, BUSINESS, company_name="합성법인 A", corporate_no=CORPORATE,
        apply_to_state=False,
    )
    assert state["stock_last_registry_data"] == latest
    assert state["stock_registry_review_needed"] is True
    for key, value in original.items():
        assert state[key] == value


@pytest.mark.parametrize("blank", ["", None])
def test_registry_refresh_fills_only_blank_current_fields_not_historical_shares(monkeypatch, blank):
    state = {"stock_business_no": BUSINESS, "stock_current_shares": blank,
             "stock_par_value": blank, "stock_capital": blank,
             "stock_authorized_shares": blank, "stock_shares_1": "80",
             "stock_shares_2": "70", "stock_shares_3": "60",
             "stock_total_assets": "manual-assets", "stock_net_income_1": "manual-income",
             "stock_tax_adjustment_review": [{"source_id": "manual-review"}]}
    monkeypatch.setattr(stock.st, "session_state", state)
    monkeypatch.setattr(stock, "_restore_registry_for_business", Mock(return_value={
        "사업자등록번호": BUSINESS, "발행주식총수": 250,
        "1주당액면가액": 10000, "자본금": 2500000, "발행할주식총수": 2000,
    }))
    stock._refresh_stock_registry_context(OWNER)
    assert state["stock_current_shares"] == "250"
    assert state["stock_par_value"] == "10,000"
    assert state["stock_capital"] == "2,500,000"
    assert state["stock_authorized_shares"] == "2,000"
    assert [state[f"stock_shares_{index}"] for index in (1, 2, 3)] == ["80", "70", "60"]
    assert state["stock_total_assets"] == "manual-assets"
    assert state["stock_net_income_1"] == "manual-income"
    assert state["stock_tax_adjustment_review"] == [{"source_id": "manual-review"}]
    assert state["stock_registry_review_needed"] is False


def test_actual_ui_latest_evaluation_shortcut_displays_saved_result_immediately(monkeypatch):
    saved = _record()
    monkeypatch.setattr(stock, "_load_records", lambda _, **kw: [deepcopy(saved)])
    app = _offline_app(monkeypatch)
    assert not app.exception
    app.button(key="stock_open_latest_record").click().run()
    assert not app.exception
    assert app.session_state["stock_last_result"] == saved["result"]
    assert app.text_input(key="stock_current_shares").value == "100"
    assert app.text_input(key="stock_par_value").value == "5,000"
    assert any(metric.label == "1주당 최종 평가액" for metric in app.metric)
    assert any(metric.label == "저장 당시 1주당 평가액" for metric in app.metric)


def test_actual_ui_refresh_then_explicit_apply_preserves_financial_review_and_saved_version(monkeypatch):
    saved = _record()
    source_history = [deepcopy(saved)]
    monkeypatch.setattr(stock, "_load_records", lambda _, **kw: deepcopy(source_history))
    app = _offline_app(monkeypatch)
    app.button(key="stock_open_latest_record").click().run()
    assert not app.exception
    app.text_input(key="stock_total_assets").input("2,000,000")
    app.text_input(key="stock_shares_1").input("80")
    next(button for button in app.button if button.label == "주가평가 계산").click().run()
    assert not app.exception
    result = deepcopy(app.session_state["stock_last_result"])
    review = deepcopy(app.session_state["stock_tax_adjustment_review"])
    original_inputs = deepcopy(app.session_state["stock_last_inputs"])
    latest = {
        "사업자등록번호": BUSINESS, "법인명": "합성법인 A", "법인등록번호": CORPORATE,
        "발행주식총수": 250, "1주당액면가액": 10000,
        "자본금": 2500000, "발행할주식총수": 2000,
    }
    refresh = Mock(return_value=deepcopy(latest))
    monkeypatch.setattr(stock, "_restore_registry_for_business", refresh)

    app.button(key="stock_refresh_saved_registry").click().run()
    assert not app.exception
    assert refresh.call_args.kwargs["apply_to_state"] is False
    assert app.session_state["stock_registry_review_needed"] is True
    assert app.session_state["stock_last_result"] == result
    assert app.session_state["stock_last_inputs"] == original_inputs
    assert app.text_input(key="stock_current_shares").value == "100"
    assert app.text_input(key="stock_par_value").value == "5,000"
    assert app.text_input(key="stock_total_assets").value == "2,000,000"
    assert any("등록 등기와 현재 평가 입력값이 다릅니다" in warning.value for warning in app.warning)

    app.button(key="stock_apply_saved_registry").click().run()
    assert not app.exception
    assert app.text_input(key="stock_current_shares").value == "250"
    assert app.text_input(key="stock_par_value").value == "10,000"
    assert app.text_input(key="stock_capital").value == "2,500,000"
    assert app.text_input(key="stock_total_assets").value == "2,000,000"
    assert app.text_input(key="stock_shares_1").value == "80"
    assert app.session_state["stock_tax_adjustment_review"] == review
    assert "stock_last_result" not in app.session_state
    assert "stock_last_inputs" not in app.session_state
    assert app.session_state["stock_registry_review_needed"] is False
    assert app.session_state["stock_loaded_record_id"] == saved["record_id"]
    assert app.session_state["stock_saved_result_preview"]["result"] == saved["result"]
    assert source_history == [saved]
    assert any("다시 계산" in item.value for item in app.info)
