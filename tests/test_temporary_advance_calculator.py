from __future__ import annotations

import json
from io import BytesIO

from pypdf import PdfReader

from temporary_advance_calculator import (
    CORPORATE_TAX_BRACKETS_2026,
    PERSONAL_INCOME_TAX_BRACKETS,
    calculate_temporary_advance,
    company_burden_text,
    default_temporary_advance_inputs,
    extract_temporary_advance_balance,
    incremental_progressive_tax,
    representative_burden_text,
)
from temporary_advance_pdf import build_temporary_advance_pdf


def test_uncollected_interest_separates_representative_and_company_burden():
    inputs = default_temporary_advance_inputs(100_000_000)
    inputs["representative_tax_base"] = 50_000_000

    result = calculate_temporary_advance(inputs)

    assert round(result["company"]["recognized_interest"]) == 4_600_000
    assert round(result["representative"]["bonus_disposition"]) == 4_600_000
    assert round(result["representative"]["income_tax"]) == 1_104_000
    assert round(result["representative"]["local_income_tax"]) == 110_400
    assert result["company"]["adjustment_required"] is True
    assert round(result["company"]["corporate_income_tax"]) == 460_000


def test_actual_interest_collection_removes_bonus_disposition():
    inputs = default_temporary_advance_inputs(100_000_000)
    inputs.update(
        {
            "received_interest": 4_600_000,
            "disposition_mode": "인정이자 실제 회수·상여 없음",
        }
    )

    result = calculate_temporary_advance(inputs)

    assert result["company"]["interest_shortfall"] == 0
    assert result["company"]["tax_adjustment_interest"] == 0
    assert result["representative"]["bonus_disposition"] == 0
    assert result["representative"]["total_burden"] == 0


def test_disallowed_borrowing_interest_uses_balance_to_borrowing_ratio():
    inputs = default_temporary_advance_inputs(100_000_000)
    inputs.update(
        {
            "received_interest": 4_600_000,
            "disposition_mode": "인정이자 실제 회수·상여 없음",
            "total_borrowings": 200_000_000,
            "annual_interest_expense": 10_000_000,
        }
    )

    result = calculate_temporary_advance(inputs)

    assert round(result["company"]["disallowed_interest"]) == 5_000_000
    assert round(result["company"]["corporate_income_tax"]) == 500_000
    assert round(result["company"]["local_income_tax"]) == 50_000


def test_national_pension_increment_respects_2026_monthly_cap():
    inputs = default_temporary_advance_inputs(260_869_565)
    inputs["current_monthly_standard_income"] = 6_500_000
    inputs["current_monthly_standard_income_known"] = True
    inputs["insurance_status"] = "직장가입"
    inputs["nps_health_covered"] = True

    result = calculate_temporary_advance(inputs)
    pension = result["insurance_rows"][0]

    # 4.6% interest is approximately 12m bonus. Only the remaining 90,000
    # monthly gap to the 6.59m cap can increase the pension base.
    assert round(pension["대표자 부담"] + pension["법인 부담"]) == 102_600


def test_unknown_tax_bases_return_ranges_and_pending_insurance():
    result = calculate_temporary_advance(
        default_temporary_advance_inputs(100_000_000)
    )

    assert result["representative"]["tax_total_min"] < result[
        "representative"
    ]["tax_total_max"]
    assert result["company"]["tax_total_min"] < result["company"][
        "tax_total_max"
    ]
    assert result["representative"]["insurance_pending"] is True
    assert "보험료 미확인" in representative_burden_text(result)
    assert "보험료 미확인" in company_burden_text(result)


def test_confirmed_bases_collapse_estimate_ranges():
    inputs = default_temporary_advance_inputs(100_000_000)
    inputs.update(
        {
            "balance_confirmed": True,
            "representative_tax_base": 50_000_000,
            "representative_tax_base_known": True,
            "corporate_tax_base": 200_000_000,
            "corporate_tax_base_known": True,
            "insurance_status": "미가입",
        }
    )

    result = calculate_temporary_advance(inputs)

    assert result["representative"]["tax_total_min"] == result[
        "representative"
    ]["tax_total_max"]
    assert result["company"]["tax_total_min"] == result["company"][
        "tax_total_max"
    ]
    assert result["estimation"]["has_unknowns"] is False


def test_result_remains_json_serializable_for_supabase():
    inputs = default_temporary_advance_inputs(100_000_000)
    inputs["balance_confirmed"] = True

    result = calculate_temporary_advance(inputs)
    encoded = json.dumps(result, ensure_ascii=False)

    assert '"balance_confirmed": true' in encoded
    assert '"estimation"' in encoded


def test_five_percent_shortfall_threshold_is_applied():
    inputs = default_temporary_advance_inputs(100_000_000)
    inputs["received_interest"] = 4_370_000

    result = calculate_temporary_advance(inputs)

    assert round(result["company"]["interest_shortfall"]) == 230_000
    assert result["company"]["adjustment_required"] is True


def test_2026_progressive_tax_bracket_boundaries():
    assert incremental_progressive_tax(
        14_000_000,
        1_000_000,
        PERSONAL_INCOME_TAX_BRACKETS,
    ) == 150_000
    assert incremental_progressive_tax(
        200_000_000,
        1_000_000,
        CORPORATE_TAX_BRACKETS_2026,
    ) == 200_000


def test_extract_temporary_advance_uses_first_positive_alias_only():
    balance, source = extract_temporary_advance_balance(
        {"가지급금": 120_000_000, "단기대여금": 80_000_000}
    )

    assert balance == 120_000_000
    assert source == "가지급금"


def test_one_page_pdf_is_generated():
    result = calculate_temporary_advance(
        default_temporary_advance_inputs(100_000_000)
    )

    pdf_bytes = build_temporary_advance_pdf(
        company_name="오아시스테스트",
        business_no="123-45-67890",
        result=result,
        consultant_name="테스트",
    )
    reader = PdfReader(BytesIO(pdf_bytes))

    assert len(pdf_bytes) > 5_000
    assert len(reader.pages) == 1
