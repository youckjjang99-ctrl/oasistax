"""Synthetic table fixtures only; no customer document text or amounts."""
import time
from decimal import Decimal

import pytest
from PIL import Image, ImageDraw

import cretop_ocr_tables as tables


def summary(kind="income", *, unit="백만원", header="- - 2024", rows=None):
    if rows is None:
        rows = (["매출액 - - 123", "영업이익 - - (12)", "당기순이익 - - -7"]
                if kind == "income" else
                ["자산총계 - - 900", "부채총계 - - 400", "자본금 - - 100", "자본총계 - - 500"])
    return "\n".join([tables._TITLES[kind], "단위: " + unit, "구분 " + header, *rows])


def heading_data(kind="income", *, top=200, line_num=1):
    title = tables._TITLES[kind].split()
    return {"text": title, "block_num": [1, 1], "par_num": [1, 1],
            "line_num": [line_num, line_num], "top": [top, top], "height": [30, 30]}


def page_image(kind="income", *, full_width=False, chart=False, grid=True):
    image = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(image)
    end = 980 if full_width else 480
    rows = [270, 330, 390, 450, 510] + ([570] if kind == "balance" else [])
    if grid:
        draw.rectangle((15, rows[0], end, rows[1]), fill=(220, 220, 220))
        for y in rows:
            draw.line((15, y, end, y), fill=(100, 100, 100), width=2)
        for x in [15, 130, 245, 360, end]:
            draw.line((x, rows[0], x, rows[-1]), fill=(150, 150, 150), width=2)
    if chart:
        # A separate right-hand chart is safe only with a real midpoint gutter.
        for y in rows:
            draw.line((525, y, 980, y), fill=(100, 100, 100), width=2)
    return image


def stub_ocr(monkeypatch, *, kind="income", image=None, results=None):
    rendered = image if image is not None else page_image(kind)
    monkeypatch.setattr(tables, "_render_page", lambda document, index: rendered)
    monkeypatch.setattr(tables, "_ocr_data", lambda image, timeout: heading_data(kind))
    calls = []
    iterator = iter(results) if results is not None else None

    def read(image, psm, timeout):
        assert 0 < timeout <= 12
        calls.append(psm)
        return next(iterator) if iterator is not None else summary(kind)

    monkeypatch.setattr(tables, "_ocr_text", read)
    return calls


@pytest.mark.parametrize("kind", ["income", "balance"])
def test_verified_summary_preserves_all_three_slots_and_negative_values(kind):
    source = summary(kind)
    result = tables._agreeing_summary(source, source, kind)
    assert result is not None
    parsed = tables._parse_summary(result, kind)
    assert parsed["columns"] == (None, None, "2024")
    assert parsed == tables._parse_summary(source, kind)
    if kind == "income":
        assert parsed["values"]["영업이익"] == (None, None, Decimal(-12))
        assert parsed["values"]["당기순이익"] == (None, None, Decimal(-7))


@pytest.mark.parametrize("unit", ["원", "천원", "백만원"])
def test_explicit_supported_unit_is_kept(unit):
    value = summary(unit=unit)
    assert tables._parse_summary(tables._agreeing_summary(value, value, "income"), "income")["unit"] == unit


@pytest.mark.parametrize("change", [
    lambda text: text.replace("단위: 백만원", ""),
    lambda text: text.replace("백만원", "억원"),
    lambda text: text.replace("요약 손익계산서", "요약 현금흐름분석"),
    lambda text: text.replace("요약 손익계산서", "상세 손익계산서"),
    lambda text: text.replace("구분 - - 2024", "구분 2024"),
    lambda text: text.replace("구분 - - 2024", "구분 - - -"),
    lambda text: text.replace("구분 - - 2024", "구분 2023 2024 2024"),
    lambda text: text.replace("구분 - - 2024", "구분 - - 2999"),
    lambda text: text.replace("구분 - - 2024", "구분 - - 2024.02.30"),
    lambda text: text.replace("구분 - - 2024", "구분 - 2022 2023 2024"),
    lambda text: text.replace("매출액 - - 123", "매출액 - 123"),
    lambda text: text.replace("매출액 - - 123", "매출액 - - 123 1000 0"),
    lambda text: text.replace("매출액 - - 123", "매출액 - _ 123"),
    lambda text: text.replace("매출액 - - 123", "매출액 - - 1,23"),
    lambda text: text.replace("매출액 - - 123", "매출액 - - l23"),
    lambda text: text.replace("매출액 - - 123", "매출액\n- - 123"),
    lambda text: text.replace("당기순이익 - - -7", ""),
    lambda text: text + "\n매출액 - - 123",
    lambda text: text + "\n1000 0",
    lambda text: text + "\n요약 현금흐름분석\n단위: 백만원",
])
def test_malformed_same_in_both_passes_still_rejected(change):
    text = change(summary())
    assert tables._agreeing_summary(text, text, "income") is None


@pytest.mark.parametrize("second", [
    summary(unit="천원"),
    summary(header="- - 2023"),
    summary(header="- 2024 -"),
    summary().replace("- - 123", "- - 124"),
    summary().replace("- - (12)", "- - 12"),
    summary().replace("- - (12)", "- - -"),
    summary().replace("- - -7", "- - 0"),
])
def test_psm_value_unit_year_sign_or_column_mismatch_is_rejected(second):
    assert tables._agreeing_summary(summary(), second, "income") is None


def test_all_historical_cells_are_compared_not_only_latest():
    a = summary(header="2022 2023 2024", rows=["매출액 1 2 3", "영업이익 1 2 3", "당기순이익 1 2 3"])
    b = a.replace("매출액 1 2 3", "매출액 9 2 3")
    assert tables._agreeing_summary(a, b, "income") is None


def test_decimal_comparison_does_not_round_away_disagreement():
    a = summary().replace("123", "12345678901234567890")
    b = summary().replace("123", "12345678901234567891")
    assert tables._agreeing_summary(a, b, "income") is None


def test_empty_and_zero_values_are_distinct():
    a = summary().replace("- - -7", "- - 0")
    b = a.replace("당기순이익 - - 0", "당기순이익 - - -")
    assert tables._agreeing_summary(a, b, "income") is None


def test_only_gridless_verified_alphabetic_label_aliases_are_corrected():
    a = summary("balance")
    b = a.replace("재무상태표", "재무싱태표").replace("부채총계", "부채종계").replace("자본총계", "자본종계")
    assert tables._agreeing_summary(a, b, "balance") is None
    assert tables._agreeing_summary(a, b, "balance", label_corrections=True) is not None
    assert tables._agreeing_summary(a, b.replace("900", "9OO"), "balance", label_corrections=True) is None


def test_date_headers_are_validated_and_full_dates_must_agree():
    a = summary(header="2022.12.31 2023.12.31 2024.12.31")
    assert tables._agreeing_summary(a, a, "income") is not None
    assert tables._agreeing_summary(a, a.replace("2024.12.31", "2024.06.30"), "income") is None


@pytest.mark.parametrize("kind", ["income", "balance"])
def test_real_left_grid_with_chart_gutter_is_cropped_by_title_not_page_number(kind):
    page = page_image(kind, chart=True)
    crop = tables._table_crop(page, heading_data(kind), kind)
    assert crop is not None
    assert crop.width == page.width // 2
    assert crop.height < page.height / 2


@pytest.mark.parametrize("kind", ["income", "balance"])
def test_full_width_table_is_never_cut_into_false_three_column_summary(kind):
    assert tables._table_crop(page_image(kind, full_width=True), heading_data(kind), kind) is None


def test_graph_only_or_missing_table_rules_is_rejected():
    assert tables._table_crop(page_image(chart=True, grid=False), heading_data(), "income") is None


def test_horizontal_chart_rules_without_four_table_columns_are_rejected():
    image = page_image()
    draw = ImageDraw.Draw(image)
    for x in (130, 245, 360):
        draw.line((x, 271, x, 510), fill="white", width=4)
    assert tables._table_crop(image, heading_data(), "income") is None


def test_incomplete_column_grid_is_not_assumed_to_be_three_year_slots():
    image = page_image()
    draw = ImageDraw.Draw(image)
    draw.line((245, 271, 245, 510), fill="white", width=4)
    assert tables._table_crop(image, heading_data(), "income") is None


def test_full_width_rule_with_a_tiny_pixel_gap_still_rejected():
    image = page_image(full_width=True)
    draw = ImageDraw.Draw(image)
    draw.line((500, 270, 500, 510), fill="white", width=1)
    assert tables._table_crop(image, heading_data(), "income") is None


def test_other_title_and_duplicate_titles_do_not_select_an_arbitrary_table():
    assert tables._table_crop(page_image(), heading_data("balance"), "income") is None
    one, two = heading_data(), heading_data(top=600, line_num=2)
    duplicate = {key: one[key] + two[key] for key in one}
    assert tables._table_crop(page_image(), duplicate, "income") is None


def test_gridless_preserves_title_and_shaded_header():
    crop = tables._table_crop(page_image(), heading_data(), "income")
    draw = ImageDraw.Draw(crop)
    draw.rectangle((10, 3, 100, 20), fill=(0, 0, 0))
    result = tables._without_thin_rules(crop)
    assert result.getpixel((50, 10)) == 0
    assert result.getpixel((50, 110)) < 255


def test_public_api_does_not_attempt_ocr_without_exact_primary_summary_title(monkeypatch):
    monkeypatch.setattr(tables, "_render_page", lambda *args: pytest.fail("not a summary page"))
    assert tables.get_verified_financial_tables(None, 999, "상세 손익계산서", deadline=time.monotonic()+20) == ({}, [])


def test_public_api_skips_exhausted_deadline(monkeypatch):
    monkeypatch.setattr(tables, "_render_page", lambda *args: pytest.fail("deadline elapsed"))
    assert tables.get_verified_financial_tables(None, 0, summary(), deadline=time.monotonic()) == ({}, [tables.WARNING])


def test_public_api_success_returns_separate_evidence_and_runs_two_psms(monkeypatch):
    calls = stub_ocr(monkeypatch)
    evidence, warnings = tables.get_verified_financial_tables(None, 99, summary(), deadline=time.monotonic()+20)
    assert warnings == []
    assert list(evidence) == ["income"]
    assert calls == [4, 6]
    assert tables._parse_summary(evidence["income"], "income") == tables._parse_summary(summary(), "income")


def test_public_api_full_width_layout_rejected_even_when_mock_ocr_text_agrees(monkeypatch):
    calls = stub_ocr(monkeypatch, image=page_image(full_width=True))
    assert tables.get_verified_financial_tables(None, 1, summary(), deadline=time.monotonic()+20) == ({}, [tables.WARNING])
    assert calls == []


def test_gridless_retry_requires_complete_independent_pair(monkeypatch):
    good = summary("balance")
    corrected = good.replace("부채총계", "부채종계").replace("자본총계", "자본종계")
    calls = stub_ocr(monkeypatch, kind="balance", results=["unreadable", good, corrected, good])
    evidence, warnings = tables.get_verified_financial_tables(None, 1, good, deadline=time.monotonic()+20)
    assert list(evidence) == ["balance"] and warnings == []
    assert calls == [4, 6, 4, 6]


def test_disagreement_remains_warning_without_mixing_passes(monkeypatch):
    a = summary().replace("매출액 - - 123", "매출액 - - 124")
    calls = stub_ocr(monkeypatch, results=[summary(), a, a, summary()])
    assert tables.get_verified_financial_tables(None, 1, summary(), deadline=time.monotonic()+20) == ({}, [tables.WARNING])
    assert calls == [4, 6]


def test_a_valid_conflicting_preprocessing_read_cannot_be_outvoted(monkeypatch):
    bad = summary().replace("매출액 - - 123", "매출액 - - 124")
    calls = stub_ocr(monkeypatch, results=[summary(), "unreadable", bad, bad])
    assert tables.get_verified_financial_tables(None, 1, summary(), deadline=time.monotonic()+20) == ({}, [tables.WARNING])
    assert calls == [4, 6, 4, 6]


def test_one_additional_psm_can_verify_a_valid_shaded_header_read(monkeypatch):
    good = summary("balance")
    bad_header = good.replace("구분 - - 2024", "구분 = = 2024")
    calls = stub_ocr(monkeypatch, kind="balance", results=["unreadable", "unreadable", good, bad_header, good])
    evidence, warnings = tables.get_verified_financial_tables(None, 1, good, deadline=time.monotonic()+20)
    assert list(evidence) == ["balance"] and warnings == []
    assert calls == [4, 6, 4, 6, 3]


def test_additional_psm_disagreement_or_invalidity_is_not_coerced(monkeypatch):
    good = summary("balance")
    different = good.replace("부채총계 - - 400", "부채총계 - - 401")
    calls = stub_ocr(monkeypatch, kind="balance", results=["unreadable", "unreadable", good, "unreadable", different])
    assert tables.get_verified_financial_tables(None, 1, good, deadline=time.monotonic()+20) == ({}, [tables.WARNING])
    assert calls == [4, 6, 4, 6, 3]


def test_all_invalid_readings_do_not_trigger_additional_ocr(monkeypatch):
    calls = stub_ocr(monkeypatch, results=["unreadable"]*4)
    assert tables.get_verified_financial_tables(None, 1, summary(), deadline=time.monotonic()+20) == ({}, [tables.WARNING])
    assert calls == [4, 6, 4, 6]


def test_ocr_exception_is_safe_and_does_not_leak_exception_content(monkeypatch):
    stub_ocr(monkeypatch)
    def fail(*args):
        raise RuntimeError("synthetic_private_content_must_not_escape")
    monkeypatch.setattr(tables, "_ocr_data", fail)
    assert tables.get_verified_financial_tables(None, 1, summary(), deadline=time.monotonic()+20) == ({}, [tables.WARNING])
