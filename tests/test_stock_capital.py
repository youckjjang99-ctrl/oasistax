"""Synthetic, offline checks for capital-history valuation gates."""
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest

from stock_capital import capital_source_signature, build_capital_share_plan


PERIODS = [{"year": year, "period_start": f"{year}-01-01", "period_end": f"{year}-12-31"}
           for year in (2023, 2024, 2025)]


def snapshot(shares, when, *, capital=None, par_value=100):
    return {"shares": shares, "capital": shares * par_value if capital is None else capital,
            "par_value": par_value, "effective_date": when, "review_required": False}


def change(before=10_000, after=20_000, when="2026-06-01", kind="paid", **extra):
    return {"before_shares": before, "after_shares": after,
            "before_capital": before * 100, "after_capital": after * 100,
            "par_value": 100, "effective_date": when, "kind": kind,
            "source": "registry", "registered_at": "2026-07-01", "issue_price": None,
            "review_required": False, **extra}


def registry(event=None):
    result = {"capital_history": [snapshot(10_000, "2020-01-01")], "capital_events": []}
    if event:
        result["capital_events"] = [deepcopy(event)]
        result["capital_history"].append(snapshot(event["after_shares"], event["effective_date"], capital=event["after_capital"]))
    return result


def plan(source=None, **kwargs):
    return build_capital_share_plan(source or registry(), "2026-10-24", PERIODS,
                                    history_confirmed=True, **kwargs)


def test_source_signature_is_order_independent_and_changes_on_original_change():
    source = registry(change())
    assert capital_source_signature(source) == capital_source_signature(deepcopy(source))
    source["capital_events"][0]["effective_date"] = "2026-07-01"
    assert capital_source_signature(source) != capital_source_signature(registry(change()))


def test_confirmation_is_mandatory():
    with pytest.raises(ValueError, match="확정"):
        build_capital_share_plan(registry(), "2026-10-24", PERIODS)


def test_unchanged_history_uses_actual_year_end_counts():
    result = plan()
    assert result["current_shares"] == 10_000
    assert result["current_capital"] == 1_000_000
    assert [row["shares"] for row in result["annual_rows"]] == [10_000] * 3
    assert result["can_calculate"]
    assert not result["requires_income_adjustment"]


def test_current_year_paid_increase_does_not_rewrite_prior_years_or_infer_cash():
    source = registry(change())
    before = deepcopy(source)
    result = plan(source)
    assert result["current_shares"] == 20_000
    assert result["current_capital"] == 2_000_000
    assert [row["source_shares"] for row in result["annual_rows"]] == [10_000] * 3
    assert [row["shares"] for row in result["annual_rows"]] == [10_000] * 3
    assert result["can_calculate"]
    assert result["events"][0]["issue_price"] is None
    assert source == before


def test_current_year_bonus_change_is_not_silently_treated_like_paid_change():
    result = plan(registry(change(kind="bonus")))
    assert not result["can_calculate"]
    assert result["unsupported"] and result["requires_expert_confirmation"]
    assert any("당해" in message for message in result["blocking_reasons"])


def test_unknown_current_event_blocks_final_calculation():
    result = plan(registry(change(kind="unknown")))
    assert not result["can_calculate"]
    assert result["current_shares"] == 20_000
    assert any("종류" in message for message in result["blocking_reasons"])


def test_single_prior_bonus_event_converts_only_pre_event_year_ends():
    result = plan(registry(change(when="2024-06-01", kind="bonus")))
    assert [row["source_shares"] for row in result["annual_rows"]] == [10_000, 20_000, 20_000]
    assert [row["shares"] for row in result["annual_rows"]] == [20_000] * 3
    assert result["can_calculate"]
    assert not result["requires_income_adjustment"]


def test_prior_paid_event_requires_expert_income_adjustment_even_with_known_issue_price():
    event = change(when="2024-06-01", issue_price=1000)
    result = plan(registry(event))
    assert [row["shares"] for row in result["annual_rows"]] == [20_000] * 3
    assert [row["requires_income_adjustment"] for row in result["annual_rows"]] == [True, True, False]
    assert not result["can_calculate"]
    assert result["requires_income_adjustment"]
    assert result["events"][0]["issue_price"] == 1000
    assert "income_adjustment_amount" not in result


def test_single_prior_bonus_reduction_uses_exact_ratio():
    source = {"capital_history": [snapshot(20_000, "2020-01-01"), snapshot(10_000, "2024-06-01")],
              "capital_events": [change(20_000, 10_000, "2024-06-01", "bonus")]}
    result = plan(source)
    assert result["current_shares"] == 10_000
    assert [row["shares"] for row in result["annual_rows"]] == [10_000] * 3
    assert result["can_calculate"]


def test_single_event_conversion_uses_exact_fraction_instead_of_float():
    first = change(3, 5, "2023-06-01", "bonus")
    source = {"capital_history": [snapshot(3, "2020-01-01"), snapshot(5, "2023-06-01")],
              "capital_events": [first]}
    shifted = [{"year": year, "period_start": f"{year}-01-01", "period_end": f"{year}-12-31"}
               for year in (2022, 2023, 2024)]
    # The normal single, continuous event converts 3 * 5/3 exactly to 5.
    result = build_capital_share_plan(source, "2025-06-01", shifted, history_confirmed=True)
    assert result["annual_rows"][0]["exact_shares"] == {"numerator": 5, "denominator": 1}
    assert result["annual_rows"][0]["shares"] == 5
    assert result["can_calculate"]


@pytest.mark.parametrize("kind", ["paid_increase", "bonus_increase", "paid_decrease", "bonus_decrease"])
def test_ui_kind_aliases_are_understood(kind):
    increase = kind.endswith("increase")
    before, after = (10_000, 20_000) if increase else (20_000, 10_000)
    source = {"capital_history": [snapshot(before, "2020-01-01"), snapshot(after, "2024-06-01")],
              "capital_events": [change(before, after, "2024-06-01", kind)]}
    result = plan(source)
    assert result["events"][0]["kind"] == ("paid" if kind.startswith("paid") else "bonus")
    assert result["current_shares"] == after
    assert result["requires_income_adjustment"] == kind.startswith("paid")


def test_kind_direction_contradiction_blocks_calculation():
    result = plan(registry(change(kind="paid_decrease")))
    assert not result["can_calculate"]
    assert any("방향" in reason for reason in result["blocking_reasons"])


def test_par_only_snapshot_is_not_treated_as_a_share_count():
    source = registry()
    source["capital_history"].append({"par_value": 100, "effective_date": None})
    assert plan(source)["can_calculate"]


def test_par_only_change_requires_manual_review():
    source = registry()
    source["capital_history"].append({"par_value": 500, "effective_date": None})
    result = plan(source)
    assert not result["can_calculate"]
    assert any("액면가" in reason for reason in result["blocking_reasons"])


def test_capital_only_change_requires_manual_review_without_inventing_shares():
    source = registry()
    source["capital_history"].append({"capital": 2_000_000, "effective_date": "2026-06-01"})
    result = plan(source)
    assert result["current_shares"] == 10_000
    assert not result["can_calculate"]
    assert any("주식수가 없는" in reason for reason in result["blocking_reasons"])


def test_multiple_changes_do_not_chain_unsupported_ratios():
    source = registry(change(when="2024-06-01", kind="bonus"))
    source["capital_events"].append(change(20_000, 30_000, "2025-06-01", "paid"))
    source["capital_history"].append(snapshot(30_000, "2025-06-01"))
    result = plan(source)
    assert result["current_shares"] == 30_000
    assert not result["can_calculate"]
    assert any("복수" in warning for warning in result["blocking_reasons"])
    assert [row["shares"] for row in result["annual_rows"]] == [10_000, 20_000, 30_000]


def test_events_after_valuation_are_not_applied_to_current_state():
    result = plan(registry(change(when="2027-01-01")))
    assert result["current_shares"] == 10_000
    assert [row["shares"] for row in result["annual_rows"]] == [10_000] * 3
    assert result["can_calculate"]


def test_uses_effective_date_not_registration_date():
    event = change(when="2026-06-01", registered_at="2027-01-01")
    assert plan(registry(event))["current_shares"] == 20_000


def test_missing_effective_event_date_is_rejected_even_if_registered_date_exists():
    source = registry(change())
    source["capital_events"][0]["effective_date"] = ""
    with pytest.raises(ValueError, match="효력일"):
        plan(source)


@pytest.mark.parametrize("value", [0, -1, "NaN", float("inf"), "1.5", True])
def test_invalid_shares_are_rejected(value):
    source = registry(change())
    source["capital_events"][0]["after_shares"] = value
    with pytest.raises(ValueError, match="정수"):
        plan(source)


def test_integral_numeric_values_are_accepted_without_float_rounding():
    source = registry(change())
    source["capital_events"][0]["before_shares"] = "10,000"
    source["capital_events"][0]["after_shares"] = Decimal("20000.000")
    assert plan(source)["current_shares"] == 20_000


def test_discontinuous_share_chain_is_rejected():
    source = registry(change(when="2024-06-01"))
    source["capital_events"].append(change(15_000, 30_000, "2025-06-01"))
    with pytest.raises(ValueError, match="일치"):
        plan(source)


def test_discontinuous_capital_chain_is_rejected():
    source = registry(change(when="2024-06-01"))
    source["capital_events"].append(change(20_000, 30_000, "2025-06-01", before_capital=9_000_000))
    with pytest.raises(ValueError, match="자본금"):
        plan(source)


def test_snapshot_contradiction_cannot_be_silently_overridden():
    source = registry(change())
    altered = change(after=30_000)
    with pytest.raises(ValueError, match="원본"):
        plan(source, events=[altered])


def test_deleting_all_events_does_not_hide_original_changes():
    result = plan(registry(change()), events=[])
    assert not result["can_calculate"]
    assert any("누락" in message for message in result["blocking_reasons"])


def test_unknown_past_event_requires_manual_final_calculation():
    result = plan(registry(change(when="2024-06-01", kind="unknown")))
    assert not result["can_calculate"]
    assert all(row["unsupported"] for row in result["annual_rows"])


def test_short_year_is_visible_but_automatically_blocked():
    periods = deepcopy(PERIODS)
    periods[0]["period_start"] = "2023-04-01"
    result = build_capital_share_plan(registry(), "2026-10-24", periods, history_confirmed=True)
    assert not result["can_calculate"]
    assert any("연환산" in message for message in result["blocking_reasons"])


def test_non_calendar_fiscal_periods_are_supported():
    periods = [{"year": year, "period_start": f"{year-1}-04-01", "period_end": f"{year}-03-31"}
               for year in (2023, 2024, 2025)]
    result = build_capital_share_plan(registry(), date(2025, 10, 24), periods, history_confirmed=True)
    assert result["can_calculate"]


def test_missing_period_and_unconfirmed_snapshot_are_blocked():
    source = registry()
    source["capital_history"][0]["review_required"] = True
    result = build_capital_share_plan(source, "2026-10-24", PERIODS[:2], history_confirmed=True)
    assert not result["can_calculate"]


def test_missing_current_capital_is_not_inferred_from_par_value():
    source = registry()
    source["capital_history"][0]["capital"] = None
    result = plan(source)
    assert result["current_capital"] is None
    assert not result["can_calculate"]


def test_reviewed_events_can_replace_ambiguous_original_dates_without_mutation():
    source = registry(change(kind="unknown"))
    source["capital_history"][1]["effective_date"] = None
    source["capital_history"][1]["review_required"] = True
    before = deepcopy(source)
    result = plan(source, events=[change()])
    assert result["can_calculate"]
    assert result["current_shares"] == 20_000
    assert source == before


def test_ui_confirmed_events_do_not_stay_blocked_by_immutable_ocr_review_flag():
    event = change(kind="paid_increase", review_required=True)
    source = registry(event)
    source["capital_history"][1]["review_required"] = True
    reviewed = deepcopy(event)
    result = plan(source, events=[reviewed])
    assert result["can_calculate"]
    assert result["events"][0]["review_required"] is True  # Original evidence retained.
    assert not plan(source)["can_calculate"]


def test_ui_confirmation_does_not_excuse_unknown_event_kind():
    source = registry(change(kind="unknown", review_required=True))
    result = plan(source, events=deepcopy(source["capital_events"]))
    assert not result["can_calculate"]
    assert any("종류" in reason for reason in result["blocking_reasons"])


def test_capital_par_mismatch_blocks_without_inventing_issue_price():
    event = change(after_capital=2_000_001)
    result = plan(registry(event))
    assert not result["can_calculate"]
    assert result["events"][0]["issue_price"] is None


def test_input_periods_and_events_are_not_mutated():
    source, periods, events = registry(change()), deepcopy(PERIODS), [change()]
    before = deepcopy((source, periods, events))
    result = build_capital_share_plan(source, "2026-10-24", periods, events=events, history_confirmed=True)
    result["events"][0]["kind"] = "edited-result"
    assert (source, periods, events) == before
