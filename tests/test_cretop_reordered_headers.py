"""Regression fixtures are synthetic; no customer PDF, identity, or amounts."""

import pytest

import cretop_worker as worker


def income_table(body, *, unit="백만원", kind="요약"):
    return f"{kind} 손익계산서 단위: {unit}\n{body}\n"


def assert_unconfirmed_header(body):
    text = income_table(body)
    assert worker.latest_financial_amount("매출액", text) == ""
    assert worker.extract_annual_financial_history(text) == []
    assert worker._financial_statement_metadata(text)["year_header_valid"] is False


def test_labeled_summary_header_after_rows_maps_latest_and_history():
    text = income_table(
        "매출액 11 22 33\n"
        "영업이익 3 4 5\n"
        "당기순이익 1 2 3\n"
        "구분 2021 2022 2023"
    )
    assert worker.latest_financial_amount("매출액", text) == 33_000_000
    assert worker.latest_financial_amount("영업이익", text) == 5_000_000
    assert worker.latest_financial_amount("당기순이익", text) == 3_000_000
    history = worker.extract_annual_financial_history(text)
    assert [row["연도"] for row in history] == [2023, 2022, 2021]
    assert [row["매출액"] for row in history] == [33_000_000, 22_000_000, 11_000_000]
    assert [row["당기순이익"] for row in history] == [3_000_000, 2_000_000, 1_000_000]
    metadata = worker._financial_statement_metadata(text)
    assert metadata["year_header_valid"] is True
    assert metadata["year_columns"] == ["2021", "2022", "2023"]


def test_income_and_balance_reordered_headers_keep_independent_years_and_units():
    text = income_table("매출액 11 22 33\n당기순이익 1 2 3\n구분 2021 2022 2023")
    text += (
        "요약 재무상태표 단위: 천원\n"
        "자산총계 90 80 70\n부채총계 30 20 10\n자본총계 60 60 60\n"
        "구분 2023 2022 2021\n요약 현금흐름\n"
    )
    latest = worker.extract_annual_financial_history(text)[0]
    assert latest["연도"] == 2023
    assert latest["매출액"] == 33_000_000
    assert latest["자산총계"] == 90_000
    assert latest["부채총계"] == 30_000
    assert latest["자본총계"] == 60_000


@pytest.mark.parametrize("label", ["구분", "계정과목", "계정명", "결산연도", "기준일"])
def test_explicit_header_labels_are_recognized_after_account_rows(label):
    text = income_table(f"매출액 11 22 33\n{label} 2021-12-31 2022-12-31 2023-12-31")
    assert worker._financial_statement_metadata(text)["years"] == ["2021", "2022", "2023"]
    assert worker.extract_annual_financial_history(text)[0]["매출액"] == 33_000_000


def test_unlabeled_year_like_numbers_after_first_account_are_not_headers():
    text = income_table("매출액 2021 2022 2023\n2021 2022 2023\n당기순이익 1 2 3")
    assert worker._financial_statement_metadata(text)["years"] == []
    assert worker.extract_annual_financial_history(text) == []


def test_amounts_that_look_like_years_cannot_override_reordered_explicit_header():
    text = income_table("매출액 2021 2022 2023\n당기순이익 1 2 3\n구분 2018 2019 2020")
    history = worker.extract_annual_financial_history(text)
    assert [row["연도"] for row in history] == [2020, 2019, 2018]
    assert history[0]["매출액"] == 2_023_000_000


@pytest.mark.parametrize("body", [
    "구분 2021 2022 2023\n매출액 11 22 33\n구분 2020 2021 2022",
    "매출액 11 22 33\n구분 2021 2022 2023\n계정명 2023 2022 2021",
    "매출액 11 22 33\n구분 2021 2022 2022",
    "매출액 11 22 33\n구분 2021 2021 2023",
])
def test_conflicting_or_duplicate_year_headers_remain_unconfirmed(body):
    assert_unconfirmed_header(body)


@pytest.mark.parametrize("invalid_date", [
    "2023-13-31", "2023-12-32", "2023-02-29", "2023-02-30", "2023-00-10",
])
def test_invalid_full_dates_are_not_treated_as_confirmed_years(invalid_date):
    assert_unconfirmed_header(
        f"매출액 11 22 33\n계정명 2021-12-31 2022-12-31 {invalid_date}"
    )


@pytest.mark.parametrize("cells", ["11 22", "11 22 33 44"])
def test_reordered_header_column_count_mismatch_does_not_shift_values(cells):
    text = income_table(f"매출액 {cells}\n구분 2021 2022 2023")
    assert worker.latest_financial_amount("매출액", text) == ""
    assert worker.extract_annual_financial_history(text) == []


def test_legacy_unlabeled_header_before_body_remains_supported():
    text = income_table("2021 2022 2023\n매출액 11 22 33\n당기순이익 1 2 3")
    assert worker.latest_financial_amount("매출액", text) == 33_000_000
    assert [row["연도"] for row in worker.extract_annual_financial_history(text)] == [2023, 2022, 2021]


def test_reordered_descending_header_selects_latest_not_rightmost():
    text = income_table("매출액 33 22 11\n당기순이익 3 2 1\n구분 2023 2022 2021")
    assert worker.latest_financial_amount("매출액", text) == 33_000_000
    assert worker.extract_annual_financial_history(text)[0]["매출액"] == 33_000_000


def test_reordered_descending_header_preserves_missing_latest_value():
    text = income_table("매출액 - 22 11\n구분 2023 2022 2021")
    assert worker.latest_financial_amount("매출액", text) == ""
    history = worker.extract_annual_financial_history(text)
    assert history[0]["연도"] == 2022
    assert history[0]["매출액"] == 22_000_000


@pytest.mark.parametrize("header,cells,years,latest", [
    ("구분 - 2022 2023", "999 22 33", [2023, 2022], 33_000_000),
    ("구분 2023 - 2021", "33 999 11", [2023, 2021], 33_000_000),
    ("구분 - - 2023", "999 999 0", [2023], 0),
])
def test_reordered_explicit_empty_header_columns_preserve_positions(header, cells, years, latest):
    text = income_table(f"매출액 {cells}\n{header}")
    assert worker.latest_financial_amount("매출액", text) == latest
    history = worker.extract_annual_financial_history(text)
    assert [row["연도"] for row in history] == years
    assert history[0]["매출액"] == latest


def test_detail_star_annotations_and_account_name_header_are_recognized():
    text = income_table(
        "매출액(*) 110 220 330\n"
        "영업이익(손실)(*) 10 20 (30)\n"
        "당기순이익(순손실)(*) 7 8 (9)\n"
        "계정명 2021-12-31 2022-12-31 2023-12-31",
        unit="천원", kind="상세",
    )
    text += (
        "상세 재무상태표 단위: 천원\n"
        "자산(*) 400 500 600\n부채(*) 100 200 300\n자본(*) 300 300 300\n"
        "계정명 2021-12-31 2022-12-31 2023-12-31\n"
    )
    assert worker.latest_financial_amount("매출액", text) == 330_000
    assert worker.latest_financial_amount("영업이익", text) == -30_000
    assert worker.latest_financial_amount("당기순이익", text) == -9_000
    assert worker.latest_financial_amount("자산총계", text) == 600_000
    assert worker.latest_financial_amount("자본총계", text) == 300_000
    history = worker.extract_annual_financial_history(text)
    assert history[0]["매출액"] == 330_000
    assert history[0]["당기순이익"] == -9_000
    assert history[0]["자산총계"] == 600_000


@pytest.mark.parametrize("boundary", [
    "요약 현금흐름", "요약 재무비율", "동종업계 비교", "주요 구매처", "기업개요",
])
def test_reordered_header_cannot_be_borrowed_from_next_section(boundary):
    text = income_table(f"매출액 11 22 33\n{boundary}\n구분 2021 2022 2023")
    assert worker._financial_statement_metadata(text)["years"] == []
    assert worker.extract_annual_financial_history(text) == []


def test_next_statement_header_is_not_borrowed_by_preceding_statement():
    text = income_table("매출액 11 22 33")
    text += "요약 재무상태표 단위: 백만원\n구분 2021 2022 2023\n자산총계 OCR\n"
    assert worker._financial_statement_metadata(text)["years"] == []
    assert worker.extract_annual_financial_history(text) == []


def test_form_feed_page_boundary_stops_trailing_header_search():
    text = income_table("매출액 11 22 33\n\f\n구분 2021 2022 2023")
    assert worker._financial_statement_metadata(text)["years"] == []
    assert worker.extract_annual_financial_history(text) == []
