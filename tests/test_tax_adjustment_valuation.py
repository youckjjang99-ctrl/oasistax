from datetime import date

import pytest

import tax_adjustment_valuation as tax


def all_values(**overrides):
    values = {component["key"]: 0 for component in tax.COMPONENTS}
    return {**values, **overrides}


def record(text, record_id="synthetic-record"):
    return {"document_type": "tax_adjustment", "record_id": record_id,
            "filename": "synthetic.pdf", "uploaded_at": "2026-10-22T10:00:00",
            "extracted_fields": tax.analyze_tax_adjustment_text(text)}


def test_existing_financial_fields_remain_compatible():
    result = tax.analyze_tax_adjustment_text("2025 사업연도 법인세 세무조정계산서\n당기순이익 82,000,000원\n자산총계 830백만원\n부채총계 649,000,000원")
    assert result["recognized"]
    assert result["financial_fields"] == {"당기순이익": 82_000_000, "자산총계": 830_000_000, "부채총계": 649_000_000}
    assert result["years"] == ["2025"]


def test_income_is_not_accounting_profit_or_taxable_base():
    result = tax.analyze_tax_adjustment_text("2025 사업연도 세무조정계산서\n과세표준 10,000원\n당기순이익 20,000원\n법인세비용 1,000원\n결정세액 2,000원")
    period = result["valuation_periods"][0]
    assert "income" not in period["values"]
    assert "deduct_taxes" not in period["values"]


def test_fiscal_range_and_shared_units_negative_and_explicit_zero():
    result = tax.analyze_tax_adjustment_text("법인세 세무조정계산서 (단위: 천원)\n사업연도 2024.04.01 ~ 2025.03.31\n각 사업연도 소득금액 (101) △12,345\n수입배당금 익금불산입액 0\n당기순이익 (2,000)")
    period = result["valuation_periods"][0]
    assert period["year"] == 2025
    assert period["period_start"] == "2024-04-01"
    assert period["period_end"] == "2025-03-31"
    assert period["values"]["income"] == -12_345_000
    assert period["values"]["add_dividend_exclusion"] == 0
    assert period["financial_fields"]["당기순이익"] == -2_000_000
    assert period["evidence"]["income"][0]["unit"] == "천원"


def test_arbitrary_legal_dates_do_not_become_fiscal_years():
    result = tax.analyze_tax_adjustment_text("세무조정계산서\n작성일 2026.10.22 법률 개정일 2025.12.30\n각사업연도소득금액 100,000원")
    assert result["years"] == []
    assert result["valuation_periods"] == []
    assert tax.build_tax_adjustment_candidates([record("세무조정계산서 2025.10.22")], date(2026, 10, 22)) == []


@pytest.mark.parametrize("heading", ["2025 사업연도", "2025년 사업연도", "사업연도: 2025", "사업연도 2025년"])
def test_unambiguous_year_heading_supported_without_inventing_dates(heading):
    result = tax.analyze_tax_adjustment_text(heading + "\n세무조정계산서\n각사업연도소득금액 10원")
    assert result["years"] == ["2025"]
    candidate = tax.build_tax_adjustment_candidates([record(heading + "\n각사업연도소득금액 10원")], date(2026, 10, 22))[0]
    assert not candidate["period_verified"]
    assert not candidate["period_end"]


def test_multiple_years_and_units_are_not_flattened_to_latest_upload():
    source = "세무조정계산서 (단위:천원)\n2024 사업연도\n각사업연도소득금액 10\n2025 사업연도\n각사업연도소득금액 20"
    result = tax.build_tax_adjustment_candidates([record(source)], date(2026, 10, 22))
    assert [item["year"] for item in result] == [2025, 2024]
    assert [item["values"]["income"] for item in result] == [20_000, 10_000]


def test_no_implicit_won_or_column_guess():
    for tail in ["1000", "1000원 2000원 3000원", "1,23원"]:
        result = tax.analyze_tax_adjustment_text("2025 사업연도 세무조정계산서\n각사업연도소득금액 " + tail)
        period = result["valuation_periods"][0]
        assert "income" not in period["values"]
        assert "income" in period["ambiguous_fields"]


def test_unit_from_unrelated_previous_table_is_not_carried_to_next_year():
    source = "세무조정계산서\n2024 사업연도\n(단위:천원)\n각사업연도소득금액 10\n자산총계 1백만원\n(단위:원)\n다른 표의 내용\n2025 사업연도\n각사업연도소득금액 20"
    periods = tax.analyze_tax_adjustment_text(source)["valuation_periods"]
    assert periods[0]["values"]["income"] == 10_000
    assert "income" not in periods[1]["values"]


def test_distinct_unit_directly_adjacent_to_year_header_is_explicit():
    source = "세무조정계산서 (단위:천원)\n2024 사업연도\n각사업연도소득금액 10\n(단위:백만원)\n2025 사업연도\n각사업연도소득금액 20"
    periods = tax.analyze_tax_adjustment_text(source)["valuation_periods"]
    assert periods[0]["values"]["income"] == 10_000
    assert periods[1]["values"]["income"] == 20_000_000


def test_same_year_conflicting_sources_are_explicit_and_unresolved():
    candidates = tax.build_tax_adjustment_candidates([
        record("2025 사업연도\n각사업연도소득금액 100원", "source-a"),
        record("2025 사업연도\n각사업연도소득금액 200원", "source-b"),
    ], date(2026, 10, 22))
    assert candidates[0]["values"]["income"] is None
    assert "income" in candidates[0]["conflicts"]
    assert {item["record_id"] for item in candidates[0]["sources"]} == {"source-a", "source-b"}


def test_business_identity_normalized_without_guessing_company_name():
    number = "-".join(("123", "45", "67890"))
    result = tax.analyze_tax_adjustment_text("세무조정계산서 사업자등록번호: " + number)
    assert result["business_no"] == number
    assert result["business_numbers"] == [number]


def test_refund_principal_is_not_confused_with_refund_interest():
    result = tax.analyze_tax_adjustment_text("2025 사업연도 세무조정계산서\n법인세 환급금 1,000,000원\n국세 지방세 과오납금 환급금에 대한 이자 10,000원")
    assert result["valuation_periods"][0]["values"]["add_refunded_tax"] == 10_000


def test_candidates_retain_per_field_source_and_evidence():
    candidate = tax.build_tax_adjustment_candidates([record("2025 사업연도\n각사업연도소득금액 100원", "source-a")], date(2026, 10, 22))[0]
    evidence = candidate["field_sources"]["income"][0]
    assert evidence["record_id"] == "source-a"
    assert evidence["evidence"][0]["unit"] == "원"


def test_all_statutory_components_require_explicit_values():
    result = tax.calculate_tax_adjusted_income({"income": 100_000})
    assert not result["complete"]
    assert result["adjusted_net_income"] is None
    assert "deduct_taxes" in result["missing"]
    assert "capital_adjustment" in result["missing"]
    assert tax.calculate_tax_adjusted_income(all_values())["adjusted_net_income"] == 0


def test_statutory_additions_deductions_and_signed_corrections():
    result = tax.calculate_tax_adjusted_income(all_values(
        income=100_000, add_dividend_exclusion=1000, deduct_taxes=12_000,
        deduct_limits_interest=2000, deduct_depreciation_gap=-500,
        capital_adjustment=-700,
    ))
    assert result["complete"]
    assert result["adjusted_net_income"] == 86_800
    assert result["law_base_date"] == "2026-10-22"


def test_annual_loss_is_not_clamped_before_weighted_average():
    result = tax.calculate_tax_adjusted_income(all_values(income=-100_000, deduct_taxes=100))
    assert result["adjusted_net_income"] == -100_100


@pytest.mark.parametrize("component", [item["key"] for item in tax.COMPONENTS if item["key"] not in {"income", "deduct_depreciation_gap", "capital_adjustment"}])
def test_ordinary_adjustment_magnitudes_cannot_reverse_sign(component):
    result = tax.calculate_tax_adjusted_income(all_values(**{component: -100}))
    assert not result["complete"]
    assert result["adjusted_net_income"] is None
    assert component in result["invalid"]
    assert component in result["missing"]


@pytest.mark.parametrize("invalid", [None, "", float("nan"), float("inf"), True, "1.5"])
def test_nonfinite_fractional_or_missing_won_cannot_complete_calculation(invalid):
    result = tax.calculate_tax_adjusted_income(all_values(income=invalid))
    assert not result["complete"]
    assert "income" in result["missing"]


def test_period_and_law_guards_are_exposed_to_ui():
    short = record("세무조정계산서\n사업연도 2025.07.01 ~ 2025.12.31\n각사업연도소득금액 100원")
    candidate = tax.build_tax_adjustment_candidates([short], date(2026, 10, 22))[0]
    assert candidate["short_period"]
    assert candidate["period_verified"]
    assert not candidate["legal_review_required"]
    assert tax.build_tax_adjustment_candidates([short], date(2027, 1, 1))[0]["legal_review_required"]
    assert tax.build_tax_adjustment_candidates([short], date(2026, 9, 1))[0]["legal_review_required"]


def test_fiscal_year_ending_in_current_calendar_year_is_valid_if_closed():
    item = record("세무조정계산서\n사업연도 2025.04.01 ~ 2026.03.31\n각사업연도소득금액 100원")
    candidate = tax.build_tax_adjustment_candidates([item], date(2026, 10, 22))[0]
    assert candidate["year"] == 2026
    assert candidate["eligible"]
    assert not candidate["short_period"]


def test_future_period_and_invalid_long_period_are_ineligible():
    future = record("세무조정계산서\n사업연도 2026.01.01 ~ 2026.12.31\n각사업연도소득금액 100원")
    long = record("세무조정계산서\n사업연도 2024.01.01 ~ 2025.12.31\n각사업연도소득금액 100원")
    assert not tax.build_tax_adjustment_candidates([future], date(2026, 10, 22))[0]["eligible"]
    assert not tax.build_tax_adjustment_candidates([long], date(2026, 10, 22))[0]["eligible"]


def test_legacy_attachment_with_explicit_different_company_is_excluded():
    selected = "-".join(("123", "45", "67890"))
    different = "-".join(("999", "99", "99999"))
    item = record("2025 사업연도 세무조정계산서\n사업자등록번호 " + different + "\n각사업연도소득금액 100원")
    item["business_no"] = selected
    assert tax.build_tax_adjustment_candidates([item], date(2026, 10, 22)) == []


def test_legacy_single_business_no_field_also_enforces_identity():
    selected = "-".join(("123", "45", "67890"))
    item = record("2025 사업연도 세무조정계산서\n각사업연도소득금액 100원")
    item["business_no"] = selected
    item["extracted_fields"]["business_no"] = "".join(("999", "99", "99999"))
    item["extracted_fields"].pop("business_numbers")
    assert tax.build_tax_adjustment_candidates([item], date(2026, 10, 22)) == []


def test_multi_business_document_including_selected_company_requires_review():
    selected = "-".join(("123", "45", "67890"))
    different = "-".join(("999", "99", "99999"))
    item = record("2025 사업연도 세무조정계산서\n사업자등록번호 " + selected + "\n사업자등록번호 " + different + "\n각사업연도소득금액 100원")
    item["business_no"] = selected.replace("-", "")
    candidate = tax.build_tax_adjustment_candidates([item], date(2026, 10, 22))[0]
    assert candidate["identity_review_required"]
    assert any("여러 사업자" in warning for warning in candidate["warnings"])


def test_matching_explicit_identity_preserves_candidate_without_review_flag():
    selected = "-".join(("123", "45", "67890"))
    item = record("2025 사업연도 세무조정계산서\n사업자등록번호 " + selected + "\n각사업연도소득금액 100원")
    item["business_no"] = selected
    candidate = tax.build_tax_adjustment_candidates([item], date(2026, 10, 22))[0]
    assert not candidate["identity_review_required"]
    assert candidate["values"]["income"] == 100
