"""Financial parser regression fixtures are entirely synthetic, never client data."""
import pytest

import cretop_worker as worker


@pytest.mark.parametrize("cells, expected", [
    ("- - 1,234", 1234),
    ("900 - 1,234", 1234),
    ("-\t-\t1,234", 1234),
    ("– — 1,234", 1234),
    ("100 200 -123", -123),
    ("100 200 −123", -123),
    ("100 200 (123)", -123),
    ("100 200 ( 1,234.5 )", -1234.5),
    ("100 200 0", 0),
    ("100 200 -0", 0),
    ("100 200 12.5", 12.5),
    ("100 200 +123", 123),
    ("100 200 -", None),
    ("100 200 —", None),
    ("- - -", None),
    ("", None),
])
def test_latest_cells_keep_missing_negative_parentheses_and_zero(cells, expected):
    assert worker.parse_latest_number_from_line("매출액", f"매출액 {cells}\n") == expected


@pytest.mark.parametrize("cells", ["100 OCR 200", "100 1,23", "100 200-123", "100 2.3.4", "9" * 400])
def test_malformed_or_nonfinite_cells_are_not_silently_coerced(cells):
    assert worker.parse_latest_number_from_line("매출액", f"매출액 {cells}") is None


def test_account_annotation_does_not_consume_parenthesized_loss():
    assert worker.parse_latest_number_from_line("영업이익", "영업이익(손실) 10 20 (30)") == -30
    assert worker.parse_latest_number_from_line("영업이익", "영업이익 (30)") == -30


def test_newline_is_not_a_cell_separator():
    assert worker.parse_latest_number_from_line("매출액", "매출액\n2023 2024 2025\n") is None


@pytest.mark.parametrize("cells, expected", [
    ("- - 123", 123_000_000),
    ("- - -123", -123_000_000),
    ("- - (123)", -123_000_000),
    ("- - 0", 0),
    ("100 200 -", None),
])
def test_annual_and_latest_parsers_agree(cells, expected):
    block = f"2023 2024 2025\n매출액 {cells}\n"
    values = worker._extract_row_values(block, ["매출액"], [2023, 2024, 2025])
    latest = worker.latest_financial_amount("매출액", "요약 손익계산서 단위: 백만원\n" + block)
    assert values["2025"] == expected
    assert latest == (expected if expected is not None else "")


def test_latest_missing_summary_cell_does_not_fall_back_to_my_or_detail():
    text = """손익계산서 단위: 백만원
2023 2024 2025
매출액 30 40 50
상세 손익계산서 단위: 천원
2023 2024 2025
매출액 400 500 600
요약 손익계산서 단위: 백만원
2023 2024 2025
매출액 10 20 -
당기순이익 1 2 3
요약 현금흐름
"""
    assert worker.latest_financial_amount("매출액", text) == ""
    assert worker.latest_financial_amount("당기순이익", text) == 3_000_000
    assert worker.extract_annual_financial_history(text)[0]["매출액"] is None


def test_selected_summary_does_not_fill_an_absent_account_from_other_sources():
    text = """손익계산서 단위: 백만원
매출액 999
영업이익 88
당기순이익 77
요약 손익계산서 단위: 백만원
매출액 123
당기순이익 12
요약 현금흐름
"""
    assert worker.latest_financial_amount("매출액", text) == 123_000_000
    assert worker.latest_financial_amount("영업이익", text) == ""
    assert worker.latest_financial_amount("당기순이익", text) == 12_000_000
    assert worker._financial_statement_metadata(text)["kind"] == "summary"


def test_wholly_unreadable_summary_may_use_one_complete_detail_source():
    text = """손익계산서 단위: 백만원
매출액 999
당기순이익 99
요약 손익계산서 단위: 백만원
매출액 OCR
당기순이익 OCR
상세 손익계산서 단위: 천원
2023 2024 2025
매출액 - - 123
당기순이익 - - (12)
"""
    assert worker.latest_financial_amount("매출액", text) == 123_000
    assert worker.latest_financial_amount("당기순이익", text) == -12_000
    assert worker.extract_annual_financial_history(text)[0]["매출액"] == 123_000
    assert worker._financial_statement_metadata(text)["kind"] == "detail"


@pytest.mark.parametrize("unit, multiplier", [("백만원", 1_000_000), ("천원", 1_000), ("원", 1)])
def test_summary_explicit_units_apply_to_latest_and_history(unit, multiplier):
    text = f"요약 손익계산서 단위: {unit}\n2023 2024 2025\n매출액 11 22 33\n"
    assert worker.latest_financial_amount("매출액", text) == 33 * multiplier
    assert worker.extract_annual_financial_history(text)[0]["매출액"] == 33 * multiplier


def test_unsupported_units_are_not_assumed_to_be_millions():
    text = "요약 손익계산서 단위: 억원\n매출액 123\n"
    assert worker.latest_financial_amount("매출액", text) == ""


def test_amounts_that_look_like_years_do_not_replace_header_years():
    text = "요약 손익계산서 단위: 백만원\n2023 2024 2025\n매출액 2020 2021 2022\n당기순이익 1 2 3\n"
    history = worker.extract_annual_financial_history(text)
    assert [row["연도"] for row in history] == [2025, 2024, 2023]
    assert history[0]["매출액"] == 2_022_000_000


def test_descending_years_select_the_latest_year_not_rightmost_column():
    text = "요약 손익계산서 단위: 백만원\n계정과목 2025.12.31 2024.12.31 2023.12.31\n매출액 300 200 100\n"
    assert worker.latest_financial_amount("매출액", text) == 300_000_000
    assert worker.extract_annual_financial_history(text)[0]["매출액"] == 300_000_000


def test_missing_latest_descending_year_does_not_use_older_year():
    text = "요약 손익계산서 단위: 백만원\n2025 2024 2023\n매출액 - 200 100\n"
    assert worker.latest_financial_amount("매출액", text) == ""


def test_extra_chart_numbers_do_not_shift_annual_columns():
    text = "요약 손익계산서 단위: 백만원\n2023 2024 2025\n매출액 10 20 30 1000 0\n"
    assert worker.latest_financial_amount("매출액", text) == ""
    assert worker.extract_annual_financial_history(text) == []


def test_duplicate_year_header_is_unconfirmed_not_rightmost_fallback():
    text = "요약 손익계산서 단위: 백만원\n2023 2024 2024\n매출액 10 20 30\n"
    assert worker.latest_financial_amount("매출액", text) == ""
    assert worker.extract_annual_financial_history(text) == []


def test_four_year_columns_keep_latest_three_without_column_shift():
    text = "요약 손익계산서 단위: 백만원\n2022 2023 2024 2025\n매출액 10 20 30 40\n"
    history = worker.extract_annual_financial_history(text)
    assert [row["연도"] for row in history] == [2025, 2024, 2023]
    assert history[-1]["매출액"] == 20_000_000


def test_statement_end_prevents_borrowing_next_tables_units_or_amounts():
    text = "손익계산서\n매출액 OCR\n재무상태표 단위: 백만원\n매출액 999\n"
    assert worker.latest_financial_amount("매출액", text) == ""


def test_metadata_does_not_include_financial_row_values():
    text = "요약 손익계산서 단위: 천원\n2023 2024 2025\n매출액 10 20 30\n"
    assert worker._financial_statement_metadata(text) == {
        "kind": "summary", "unit_multiplier": 1000,
        "years": ["2023", "2024", "2025"],
        "year_columns": ["2023", "2024", "2025"], "year_header_valid": True,
        "conflicting_accounts": [],
    }


def test_single_year_heading_remains_supported():
    text = "요약 손익계산서 단위: 백만원\n2025\n매출액 33\n"
    assert worker.extract_annual_financial_history(text)[0]["연도"] == 2025


def test_line_wrapped_summary_heading_keeps_summary_priority():
    text = "손익계산서 단위: 백만원\n매출액 999\n요약\n손익계산서 단위: 백만원\n매출액 33\n"
    assert worker.latest_financial_amount("매출액", text) == 33_000_000


def test_explicit_empty_summary_row_is_not_filled_from_another_statement():
    text = "손익계산서 단위: 백만원\n매출액 999\n요약 손익계산서 단위: 백만원\n매출액\n"
    assert worker.latest_financial_amount("매출액", text) == ""


@pytest.mark.parametrize("header, cells, latest, annual_years", [
    ("구분 - - 2025", "- - 123", 123_000_000, [2025]),
    ("구분 - 2024 2025", "999 22 33", 33_000_000, [2025, 2024]),
    ("구분 2025 2024 -", "33 22 999", 33_000_000, [2025, 2024]),
    ("구분 2025 - 2023", "33 999 11", 33_000_000, [2025, 2023]),
    ("구분 미확인 2024 2025", "999 22 33", 33_000_000, [2025, 2024]),
    ("구분 2025 - -", "(33) 999 999", -33_000_000, [2025]),
    ("구분 - - 2025", "999 999 0", 0, [2025]),
    ("구분 - - 2025", "999 999 -", "", []),
    ("구분 - - -", "111 222 333", "", []),
])
def test_empty_year_headers_preserve_real_column_positions(header, cells, latest, annual_years):
    text = f"요약 손익계산서 단위: 백만원\n{header}\n매출액 {cells}\n"
    assert worker.latest_financial_amount("매출액", text) == latest
    history = worker.extract_annual_financial_history(text)
    assert [row["연도"] for row in history] == annual_years
    if history:
        assert history[0]["매출액"] == latest
    assert "None" not in str([row["연도"] for row in history])


def test_header_metadata_exposes_known_years_and_preserves_empty_positions():
    text = "요약 손익계산서 단위: 백만원\n구분 - - 2025\n매출액 - - 123\n"
    metadata = worker._financial_statement_metadata(text)
    assert metadata["years"] == ["2025"]
    assert metadata["year_columns"] == [None, None, "2025"]
    assert metadata["year_header_valid"] is True


def test_fewer_years_without_explicit_empty_position_remains_unconfirmed():
    text = "요약 손익계산서 단위: 백만원\n구분 2024 2025\n매출액 11 22 33\n"
    assert worker.latest_financial_amount("매출액", text) == ""
    assert worker.extract_annual_financial_history(text) == []


def test_unreadable_first_account_row_does_not_hide_later_valid_summary_row():
    text = """손익계산서 단위: 백만원
구분 - - 2025
매출액 - - 999
요약 손익계산서 단위: 백만원
구분 - - 2025
매출액 OCR
매출액 - - 111
"""
    assert worker.latest_financial_amount("매출액", text) == 111_000_000
    assert worker.extract_annual_financial_history(text)[0]["매출액"] == 111_000_000
    assert worker._financial_statement_metadata(text)["kind"] == "summary"


@pytest.mark.parametrize("first, second", [
    ("매출액 - - 111", "매출액 - - 999"),
    ("매출액 - - 111", "매출 - - 999"),
    ("매출액 - - -", "매출액 - - 111"),
    ("매출액", "매출액 - - 111"),
])
def test_conflicting_account_rows_neither_confirm_first_nor_fall_back(first, second):
    text = f"""손익계산서 단위: 백만원
구분 - - 2025
매출액 - - 777
요약 손익계산서 단위: 백만원
구분 - - 2025
{first}
{second}
"""
    assert worker.latest_financial_amount("매출액", text) == ""
    assert worker.extract_annual_financial_history(text) == []
    assert worker._financial_statement_metadata(text)["kind"] == "summary"


def test_identical_repeated_rows_remain_supported_with_original_sign():
    text = "요약 손익계산서 단위: 백만원\n구분 - - 2025\n매출액 - - -111\n매출액 - - -111\n"
    assert worker.latest_financial_amount("매출액", text) == -111_000_000
    assert worker.extract_annual_financial_history(text)[0]["매출액"] == -111_000_000


def test_literal_row_cell_api_keeps_none_empty_and_list_contract():
    assert worker._financial_row_cells("매출액", "매출액 OCR\n") is None
    assert worker._financial_row_cells("매출액", "매출액\n") == []
    assert worker._financial_row_cells("매출액", "매출액 - - 11\n") == [None, None, 11]
    assert worker._financial_row_cells("매출액", "매출액 11\n매출액 22\n") is None
