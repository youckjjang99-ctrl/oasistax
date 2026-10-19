from __future__ import annotations

from copy import deepcopy
import math

import pytest

import stock_valuation as stock


def _annual_rows(incomes=(300_000_000, 200_000_000, 100_000_000)):
    return [
        {"year": str(year), "adjusted_net_income": income, "shares": 10_000}
        for year, income in zip((2025, 2024, 2023), incomes)
    ]


def _stock_inputs(**overrides):
    values = {
        "company_name": "합성 테스트 법인",
        "business_no": "synthetic-business",
        "corporate_no": "synthetic-corporate",
        "address": "테스트 주소",
        "establishment": "2018-01-01",
        "valuation_date": "2026-10-16",
        "current_shares": 10_000,
        "par_value": 5_000,
        "capital": 50_000_000,
        "authorized_shares": 100_000,
        "share_classes": [{"kind": "보통주", "shares": 10_000}],
        "valuation_type": "일반법인(3:2)",
        "annual_rows": _annual_rows(),
        "total_assets": 800_000_000,
        "total_liabilities": 300_000_000,
        "asset_additions": 0,
        "asset_deductions": 0,
        "liability_additions": 0,
        "liability_deductions": 0,
        "other_adjustments": 0,
        "goodwill": 0,
        "capitalization_rate": 0.10,
        "nav_floor_rate": 0.80,
        "premium_exempt": True,
        "premium_rate": 0.20,
    }
    values.update(overrides)
    return values


def test_goodwill_matches_excel_reference_formula_and_parameters():
    result = stock.calculate_goodwill(
        _annual_rows(), 500_000_000, acquired_intangible_remaining=20_000_000
    )

    assert result["raw_weighted_income"] == pytest.approx(233_333_333.3333333)
    assert result["weighted_income"] == pytest.approx(233_333_333.3333333)
    assert result["excess_earnings"] == pytest.approx(66_666_666.66666666)
    assert result["discounted_value"] == pytest.approx(252_720_000)
    assert result["acquired_intangible_remaining"] == 20_000_000
    assert result["amount"] == pytest.approx(232_720_000)
    assert result["discount_factor"] == 3.7908
    assert result["normal_return_rate"] == 0.10
    assert result["income_rate"] == 0.50
    assert result["years"] == 5
    assert result["formula_version"] == "excel-base-v1"
    assert isinstance(result["warnings"], list)


def test_goodwill_uses_whole_company_income_not_per_share_income():
    rows = _annual_rows()
    before = deepcopy(rows)
    expected = stock.calculate_goodwill(rows, 500_000_000)
    for row, shares in zip(rows, (1, 20_000, 75_000)):
        row["shares"] = shares
    actual = stock.calculate_goodwill(rows, 500_000_000)

    assert actual["amount"] == expected["amount"]
    assert before == _annual_rows()
    assert [row["shares"] for row in rows] == [1, 20_000, 75_000]


def test_goodwill_accepts_numeric_years_and_formatted_income():
    rows = _annual_rows()
    for row in rows:
        row["year"] = int(row["year"])
        row["adjusted_net_income"] = f"{row['adjusted_net_income']:,}"
    result = stock.calculate_goodwill(rows, 500_000_000)
    assert result["amount"] == pytest.approx(252_720_000)


@pytest.mark.parametrize("incomes", [(0, 0, 0), (-300_000_000, -200_000_000, -100_000_000)])
def test_goodwill_zero_and_loss_income_produce_zero_not_negative_value(incomes):
    result = stock.calculate_goodwill(_annual_rows(incomes), 500_000_000)
    assert result["weighted_income"] == 0
    assert result["amount"] == 0
    assert result["raw_weighted_income"] <= 0


def test_goodwill_clamps_negative_excess_earnings_to_zero_value():
    result = stock.calculate_goodwill(_annual_rows((10, 10, 10)), 1_000)
    assert result["excess_earnings"] < 0
    assert result["amount"] == 0


def test_goodwill_purchased_intangible_deduction_cannot_make_value_negative():
    result = stock.calculate_goodwill(
        _annual_rows(), 500_000_000, acquired_intangible_remaining=999_000_000
    )
    assert result["amount"] == 0


@pytest.mark.parametrize("count", [0, 1, 2, 4])
def test_goodwill_requires_exactly_three_annual_rows(count):
    rows = (_annual_rows() + [{"year": "2022", "adjusted_net_income": 10}])[:count]
    with pytest.raises(ValueError):
        stock.calculate_goodwill(rows, 500_000_000)


@pytest.mark.parametrize("missing", [None, "", "-", "nan", math.nan, math.inf, -math.inf])
def test_goodwill_rejects_missing_or_nonfinite_annual_income(missing):
    rows = _annual_rows()
    rows[1]["adjusted_net_income"] = missing
    with pytest.raises(ValueError):
        stock.calculate_goodwill(rows, 500_000_000)


@pytest.mark.parametrize(
    "years",
    [
        [2025, 2025, 2023],
        [2025, 2023, 2022],
        [2023, 2024, 2025],
        [2025, None, 2023],
        [2025, "", 2023],
        [2025, "2024-12-31", 2023],
        [2025, 2024.5, 2023],
        [2025, math.inf, 2023],
    ],
)
def test_goodwill_rejects_ambiguous_nonconsecutive_or_nonnumeric_years(years):
    rows = _annual_rows()
    for row, year in zip(rows, years):
        row["year"] = year
    with pytest.raises(ValueError):
        stock.calculate_goodwill(rows, 500_000_000)


@pytest.mark.parametrize("equity", [-1, None, math.nan, math.inf, -math.inf])
def test_goodwill_requires_finite_nonnegative_equity_for_manual_review(equity):
    with pytest.raises(ValueError):
        stock.calculate_goodwill(_annual_rows(), equity)


@pytest.mark.parametrize("remaining", [-1, math.nan, math.inf, -math.inf])
def test_goodwill_rejects_negative_or_nonfinite_acquired_intangible_value(remaining):
    with pytest.raises(ValueError):
        stock.calculate_goodwill(
            _annual_rows(), 500_000_000, acquired_intangible_remaining=remaining
        )


def test_goodwill_zero_equity_is_known_value_and_valid():
    result = stock.calculate_goodwill(_annual_rows(), 0)
    assert result["amount"] == pytest.approx(442_260_000)


def test_stock_automatic_goodwill_includes_all_pre_goodwill_net_asset_adjustments():
    values = _stock_inputs(
        goodwill_mode="auto",
        goodwill=999_000_000,
        goodwill_acquired_intangible_remaining=20_000_000,
        asset_additions=40_000_000,
        asset_deductions=10_000_000,
        liability_additions=20_000_000,
        liability_deductions=5_000_000,
        other_adjustments=15_000_000,
    )
    before = deepcopy(values)
    result = stock.calculate_stock_valuation(values)

    assert result["net_assets_before_goodwill"] == 530_000_000
    assert result["goodwill_mode"] == "auto"
    assert result["goodwill"] == pytest.approx(221_347_600)
    assert result["goodwill_details"]["amount"] == result["goodwill"]
    assert result["adjusted_net_assets"] == pytest.approx(751_347_600)
    assert result["nav_per_share"] == pytest.approx(75_134.76)
    assert values == before


def test_stock_automatic_goodwill_uses_same_company_earnings_when_share_counts_change():
    values = _stock_inputs(goodwill_mode="auto")
    expected = stock.calculate_stock_valuation(values)
    values["current_shares"] = 20_000
    for row in values["annual_rows"]:
        row["shares"] = 20_000
    actual = stock.calculate_stock_valuation(values)

    assert actual["goodwill"] == expected["goodwill"]
    assert actual["nav_per_share"] == expected["nav_per_share"] / 2


@pytest.mark.parametrize("manual_amount", [0, 70_000_000])
def test_stock_manual_goodwill_preserves_explicit_amount_including_zero(manual_amount):
    result = stock.calculate_stock_valuation(
        _stock_inputs(goodwill_mode="manual", goodwill=manual_amount)
    )
    assert result["goodwill_mode"] == "manual"
    assert result["goodwill"] == manual_amount
    assert result["adjusted_net_assets"] == 500_000_000 + manual_amount


def test_stock_legacy_inputs_without_mode_remain_manual():
    result = stock.calculate_stock_valuation(_stock_inputs(goodwill=70_000_000))
    assert result["goodwill_mode"] == "manual"
    assert result["goodwill"] == 70_000_000
    assert result["adjusted_net_assets"] == 570_000_000


def test_stock_excluded_goodwill_does_not_apply_manual_or_automatic_amount():
    result = stock.calculate_stock_valuation(
        _stock_inputs(
            goodwill_mode="excluded",
            goodwill=70_000_000,
            goodwill_exclusion_reason="합성 테스트상 별도 검토 완료",
        )
    )
    assert result["goodwill_mode"] == "excluded"
    assert result["goodwill"] == 0
    assert result["adjusted_net_assets"] == 500_000_000


def test_stock_automatic_goodwill_rejects_negative_pre_goodwill_equity():
    with pytest.raises(ValueError):
        stock.calculate_stock_valuation(
            _stock_inputs(
                goodwill_mode="auto", total_assets=100_000_000, total_liabilities=300_000_000
            )
        )


def test_stock_excluded_goodwill_requires_review_reason():
    with pytest.raises(ValueError):
        stock.calculate_stock_valuation(_stock_inputs(goodwill_mode="excluded"))


def test_stock_goodwill_rejects_unknown_mode():
    with pytest.raises(ValueError):
        stock.calculate_stock_valuation(_stock_inputs(goodwill_mode="unrecognized"))


@pytest.mark.parametrize("mode", ["manual", "excluded"])
@pytest.mark.parametrize("valuation_type", ["일반법인(3:2)", "부동산과다보유법인(2:3)", "순자산가치만 적용"])
def test_stock_negative_income_and_net_assets_are_floored_but_raw_values_preserved(mode, valuation_type):
    result = stock.calculate_stock_valuation(
        _stock_inputs(
            goodwill_mode=mode,
            goodwill=70_000_000,
            goodwill_exclusion_reason="합성 검토 사유",
            valuation_type=valuation_type,
            annual_rows=_annual_rows((-300_000_000, -200_000_000, -100_000_000)),
            total_assets=100_000_000,
            total_liabilities=300_000_000,
        )
    )

    assert result["raw_weighted_per_share_income"] == pytest.approx(-23_333.33333333)
    assert result["weighted_per_share_income"] == 0
    assert result["earnings_value_per_share"] == 0
    assert result["net_assets_before_goodwill"] == -200_000_000
    assert result["raw_adjusted_net_assets"] == (-130_000_000 if mode == "manual" else -200_000_000)
    assert result["adjusted_net_assets"] == 0
    assert result["nav_per_share"] == 0
    assert result["final_value_per_share"] == 0
    assert result["total_equity_value"] == 0


def test_stock_negative_income_floor_does_not_erase_positive_net_asset_value():
    result = stock.calculate_stock_valuation(
        _stock_inputs(annual_rows=_annual_rows((-300_000_000, -200_000_000, -100_000_000)))
    )

    assert result["weighted_per_share_income"] == 0
    assert result["adjusted_net_assets"] == 500_000_000
    assert result["nav_per_share"] == 50_000
    assert result["final_value_per_share"] == 40_000


def test_stock_negative_net_asset_floor_does_not_erase_positive_earnings_value():
    result = stock.calculate_stock_valuation(
        _stock_inputs(total_assets=100_000_000, total_liabilities=300_000_000)
    )

    assert result["raw_adjusted_net_assets"] == -200_000_000
    assert result["adjusted_net_assets"] == 0
    assert result["earnings_value_per_share"] == pytest.approx(233_333.33333333)
    assert result["final_value_per_share"] == pytest.approx(140_000)


def test_loaded_legacy_evaluation_keeps_manual_goodwill_and_discards_old_result(monkeypatch):
    state = {
        "stock_goodwill_mode": "auto",
        "stock_goodwill_acquired_intangible_remaining": "9,000",
        "stock_goodwill_exclusion_reason": "다른 기업의 사유",
        "stock_last_result": {"old": True},
        "stock_last_inputs": {"old": True},
    }
    monkeypatch.setattr(stock.st, "session_state", state)

    stock._apply_loaded_record({"record_id": "synthetic-legacy", "goodwill": 70_000_000})

    assert state["stock_goodwill_mode"] == "manual"
    assert state["stock_goodwill"] == "70,000,000"
    assert stock._safe_number(state.get("stock_goodwill_acquired_intangible_remaining")) in (None, 0)
    assert not state.get("stock_goodwill_exclusion_reason")
    assert not state.get("stock_last_result")
    assert not state.get("stock_last_inputs")


def test_loaded_automatic_evaluation_restores_manual_override_separately(monkeypatch):
    state = {}
    monkeypatch.setattr(stock.st, "session_state", state)
    stock._apply_loaded_record(
        {
            "record_id": "synthetic-auto",
            "goodwill_mode": "auto",
            "goodwill": 232_720_000,
            "goodwill_manual": 5_000_000,
            "goodwill_acquired_intangible_remaining": 20_000_000,
            "goodwill_exclusion_reason": "검토 사유",
        }
    )

    assert state["stock_goodwill_mode"] == "auto"
    assert state["stock_goodwill"] == "5,000,000"
    assert state["stock_goodwill_acquired_intangible_remaining"] == "20,000,000"
    assert state["stock_goodwill_exclusion_reason"] == "검토 사유"


def test_goodwill_state_survives_financial_capture_restore_but_not_company_switch(monkeypatch):
    initial = {
        "stock_goodwill_mode": "auto",
        "stock_goodwill": "5,000",
        "stock_goodwill_acquired_intangible_remaining": "20,000",
        "stock_goodwill_exclusion_reason": "검토 사유",
    }
    state = dict(initial)
    monkeypatch.setattr(stock.st, "session_state", state)
    captured = stock._capture_stock_financial_inputs()
    assert all(captured.get(key) == value for key, value in initial.items())
    state.clear()
    stock._restore_stock_financial_inputs(captured)
    assert state == initial
    stock._clear_stock_customer_state()
    assert all(key not in state for key in initial)


def test_matching_result_requires_same_successful_calculation_inputs(monkeypatch):
    values = _stock_inputs(goodwill_mode="auto")
    result = stock.calculate_stock_valuation(values)
    state = {"stock_last_inputs": deepcopy(values), "stock_last_result": result}
    monkeypatch.setattr(stock.st, "session_state", state)

    assert stock._matching_stock_result(deepcopy(values)) == result
    changed = deepcopy(values)
    changed["goodwill_acquired_intangible_remaining"] = 20_000_000
    assert stock._matching_stock_result(changed) is None
    state.pop("stock_last_inputs")
    assert stock._matching_stock_result(values) is None


def test_build_stock_record_saves_effective_goodwill_and_independent_calculation_snapshot():
    values = _stock_inputs(
        goodwill_mode="auto",
        goodwill=5_000_000,
        goodwill_acquired_intangible_remaining=20_000_000,
        goodwill_exclusion_reason="별도 검토",
    )
    result = stock.calculate_stock_valuation(values)
    expected_inputs, expected_result = deepcopy(values), deepcopy(result)

    record = stock._build_stock_record(
        values,
        result,
        record_id="synthetic-record",
        saved_by="합성 작성자",
        saved_at="2026-10-16 10:00:00",
    )

    assert record["record_id"] == "synthetic-record"
    assert record["saved_by"] == "합성 작성자"
    assert record["saved_at"] == "2026-10-16 10:00:00"
    assert record["company_name"] == values["company_name"]
    assert record["valuation_date"] == values["valuation_date"]
    assert record["goodwill_mode"] == "auto"
    assert record["goodwill"] == pytest.approx(232_720_000)
    assert record["goodwill_manual"] == 5_000_000
    assert record["goodwill_details"] == result["goodwill_details"]
    assert record["annual_rows"] == values["annual_rows"]
    assert record["share_classes"] == values["share_classes"]
    assert record["result"] == result
    assert record["law_base_date"] == stock.LAW_BASE_DATE

    values["annual_rows"][0]["adjusted_net_income"] = -1
    values["share_classes"][0]["shares"] = -1
    result["goodwill_details"]["amount"] = -1

    assert record["annual_rows"] == expected_inputs["annual_rows"]
    assert record["share_classes"] == expected_inputs["share_classes"]
    assert record["goodwill_details"] == expected_result["goodwill_details"]
    assert record["result"] == expected_result


def test_build_stock_record_rejects_new_inputs_paired_with_previous_result():
    values = _stock_inputs(goodwill_mode="manual", goodwill=70_000_000)
    result = stock.calculate_stock_valuation(values)
    changed = deepcopy(values)
    changed["goodwill"] = 80_000_000

    with pytest.raises(ValueError):
        stock._build_stock_record(
            changed,
            result,
            record_id="synthetic-mismatch",
            saved_by="합성 작성자",
            saved_at="2026-10-16 10:00:00",
        )
