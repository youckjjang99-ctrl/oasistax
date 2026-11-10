"""Synthetic registry rows only: never use customer PDF contents as fixtures."""
from pathlib import Path

import pytest

from registry_worker import parse_registry_text


HEADER = "상호 합성법인 본점 서울 공고방법 홈페이지 법인등록번호 000000-0000000\n"


def row(shares, capital, changed="2020.01.01", registered="2020.01.02", par=None):
    result = f"발행주식의 총수 {shares:,}주\n자본금의 액 금 {capital:,}원\n"
    if par is not None:
        result += f"1주의 금액 금 {par:,}원\n"
    if changed:
        result += f"{changed} 변경\n"
    if registered:
        result += f"{registered} 등기\n"
    return result


@pytest.mark.parametrize("reverse", [False, True])
def test_latest_dated_row_replaces_first_match_and_retains_old_values(reverse):
    rows = [row(100, 1_000_000, par=10_000), row(200, 2_000_000, "2026.08.01", "2026.08.10", par=10_000)]
    result = parse_registry_text(HEADER + "\n".join(rows[::-1] if reverse else rows))
    assert result["발행주식총수"] == 200
    assert result["자본금"] == 2_000_000
    assert result["1주당액면가액"] == 10_000
    assert {item["shares"] for item in result["capital_history"]} == {100, 200}
    assert not result["capital_history_review_required"]
    event = result["capital_events"][0]
    assert event["before_shares"] == 100 and event["after_shares"] == 200
    assert event["before_capital"] == 1_000_000 and event["after_capital"] == 2_000_000
    assert event["effective_date"] == "2026-08-01"
    assert event["registered_at"] == "2026-08-10"
    assert event["kind"] == "unknown" and event["issue_price"] is None


def test_undated_original_count_is_not_inferred_as_zero_or_formation_date():
    result = parse_registry_text(HEADER + "회사성립연월일 2015년 1월 1일\n"
                                 + row(100, 1_000_000, "", "")
                                 + row(200, 2_000_000, "2026.08.01", "2026.08.10"))
    assert result["발행주식총수"] == 200
    assert result["capital_history"][0]["effective_date"] == ""
    assert result["capital_history_review_required"]
    assert result["capital_events"][0]["before_shares"] == 100
    assert result["capital_events"][0]["review_required"]
    assert len(result["capital_events"]) == 1


def test_undated_later_conflicting_row_is_not_automatically_current():
    result = parse_registry_text(HEADER + row(100, 1_000_000) + row(200, 2_000_000, "", ""))
    assert result["발행주식총수"] is None
    assert result["자본금"] is None
    assert result["capital_history_review_required"]
    assert result["capital_history_warnings"]
    assert len(result["capital_history"]) == 2
    assert result["capital_events"][0]["before_shares"] == 100
    assert result["capital_events"][0]["after_shares"] == 200
    assert result["capital_events"][0]["effective_date"] == ""
    assert result["capital_events"][0]["review_required"]


def test_registration_only_date_is_not_used_as_effective_or_payment_date():
    result = parse_registry_text(HEADER + row(100, 1_000_000, "", "2020.01.02")
                                 + row(200, 2_000_000, "", "2026.08.10"))
    assert result["발행주식총수"] is None
    assert all(item["effective_date"] == "" for item in result["capital_history"])
    assert result["capital_events"][0]["issue_price"] is None
    assert result["capital_events"][0]["review_required"]


def test_same_effective_date_conflict_preserves_both_candidates_and_blocks_current():
    result = parse_registry_text(HEADER + row(100, 1_000_000) + row(200, 2_000_000))
    assert result["발행주식총수"] is None
    assert result["자본금"] is None
    assert len(result["capital_history"]) == 2
    assert all(item["review_required"] for item in result["capital_history"])


def test_authorized_shares_are_never_used_as_issued_shares():
    result = parse_registry_text(HEADER + "발행할 주식의 총수 900,000주\n" + row(100, 1_000_000))
    assert result["발행할주식총수"] == 900_000
    assert result["발행주식총수"] == 100
    assert [item["shares"] for item in result["capital_history"]] == [100]


def test_snapshot_without_changes_retains_legacy_fields_and_no_increase_event():
    result = parse_registry_text(HEADER + "발행할주식의총수 1,000주\n"
                                 + "발행주식의총수 100주\n보통주식 100주\n자본금 금 1,000,000원\n1주의금액 금 10,000원\n")
    assert result["발행주식총수"] == 100
    assert result["자본금"] == 1_000_000
    assert result["1주당액면가액"] == 10_000
    assert result["capital_events"] == []
    assert result["capital_history_review_required"]


def test_dates_in_unrelated_section_do_not_date_a_capital_snapshot():
    result = parse_registry_text(HEADER + row(100, 1_000_000, "", "")
                                 + "목적\n2026.08.01 변경\n2026.08.10 등기\n")
    assert result["capital_history"][0]["effective_date"] == ""
    assert result["capital_history"][0]["registered_at"] == ""


def test_invalid_and_reversed_dates_require_review_without_losing_values():
    result = parse_registry_text(HEADER + row(100, 1_000_000, "2020.02.30", "2020.03.01")
                                 + row(200, 2_000_000, "2026.08.10", "2026.08.01"))
    assert result["capital_history_review_required"]
    assert result["발행주식총수"] is None
    assert len(result["capital_history"]) == 2


def test_separately_extracted_columns_merge_only_identical_dated_row():
    result = parse_registry_text(HEADER + "발행주식의총수 100주\n2020.01.01 변경\n2020.01.02 등기\n"
                                 + "자본금의액 금 1,000,000원\n2020.01.01 변경\n2020.01.02 등기\n")
    assert len(result["capital_history"]) == 1
    assert result["capital_history"][0]["shares"] == 100
    assert result["capital_history"][0]["capital"] == 1_000_000


def test_prefix_date_labels_and_spaced_korean_field_names_are_supported():
    result = parse_registry_text(HEADER + "발 행 주 식 의 총 수 100주\n자 본 금 의 액 금 1,000,000원\n"
                                 + "변경일: 2026년 8월 1일\n등기일: 2026년 8월 10일\n")
    assert result["capital_history"][0]["effective_date"] == "2026-08-01"
    assert result["capital_history"][0]["registered_at"] == "2026-08-10"


def test_repeated_old_and_new_share_class_rows_are_not_summed():
    result = parse_registry_text(HEADER + "보통주식 100주 금 1,000,000원\n보통주식 200주 금 2,000,000원\n")
    assert result["발행주식총수"] is None
    assert result["capital_history_review_required"]


@pytest.mark.parametrize("reverse", [False, True])
def test_single_capital_column_heading_captures_each_dated_share_row(reverse):
    old = "발행주식의 총수 100주 보통주식 100주 금 1,000,000원 2020.01.01 변경 2020.01.02 등기\n"
    new = "발행주식의 총수 250주 보통주식 250주 금 2,500,000원 2026.08.01 변경 2026.08.10 등기\n"
    body = new + old if reverse else old + new
    result = parse_registry_text(HEADER + "1주의 금액 금 10,000원\n"
                                 + "발행주식의 총수와 그 종류 및 각각의 수 자본금의 액\n" + body
                                 + "목적 설비 매매대금 금 99,000,000원 2026.09.01 변경\n")
    assert result["발행주식총수"] == 250
    assert result["자본금"] == 2_500_000
    assert {item["capital"] for item in result["capital_history"] if item["shares"] is not None} == {1_000_000, 2_500_000}
    event = result["capital_events"][0]
    assert event["before_capital"] == 1_000_000
    assert event["after_capital"] == 2_500_000
    assert event["before_shares"] == 100 and event["after_shares"] == 250
    assert event["effective_date"] == "2026-08-01"
    assert event["issue_price"] is None


def test_unrelated_money_after_share_row_is_not_registry_capital():
    result = parse_registry_text(HEADER + "발행주식의 총수 100주 보통주식 100주\n"
                                 + "목적\n거래대금 금 9,000,000원 2026.08.01 변경\n")
    assert result["자본금"] is None
    assert result["capital_history"][0]["capital"] is None
    assert result["capital_history"][0]["effective_date"] == ""


def test_change_date_between_shares_and_capital_stays_in_same_snapshot():
    result = parse_registry_text(HEADER + "자본금의 액\n"
                                 + "발행주식의 총수 100주 보통주식 100주 금 10,000원\n"
                                 + "발행주식의 총수 200주 2026.08.01 변경\n"
                                 + "보통주식 200주 금 20,000원\n2026.08.10 등기\n")
    assert result["발행주식총수"] == 200
    assert result["자본금"] == 20_000
    assert len(result["capital_history"]) == 2
    current = result["capital_history"][1]
    assert current["shares"] == 200 and current["capital"] == 20_000
    assert current["effective_date"] == "2026-08-01"
    assert current["registered_at"] == "2026-08-10"
    assert result["capital_events"][0]["before_capital"] == 10_000
    assert result["capital_events"][0]["after_capital"] == 20_000


def test_payload_worker_remains_identical_to_root_worker():
    root = Path(__file__).resolve().parents[1]
    assert (root / "registry_worker.py").read_text(encoding="utf-8") == (root / "payload/registry_worker.py").read_text(encoding="utf-8")
    assert parse_registry_text(HEADER)["registry_parser_version"] == "capital-history-v1"
