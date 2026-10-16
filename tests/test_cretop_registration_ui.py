from __future__ import annotations

from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import enterprise_documents
import enterprise_registration_ui as ui
import utils


BUSINESS = "-".join(("123", "45", "67890"))
OTHER = "-".join(("234", "56", "78901"))


def basic_data():
    return {"업체명": "검증용 법인", "대표자명": "테스트", "사업자등록번호": BUSINESS,
            "사업장 소재지": "검증용 주소", "업종명": "제조업", "종업원수": None,
            "매출액": None, "당기순이익": None, "재무연도별": []}


def test_file_change_resets_previous_company_and_optional_settings():
    state = {"cretop_extracted_data": basic_data(), "cretop_pdf_save_path": "old.pdf",
             "cretop_edit_업체명": "old", "cretop_matching_keywords": "old",
             "cretop_confirm_update": True, "cretop_save_result": {"saved": True},
             "cretop_pdf_uploader": "new", "cretop_manager_name": "manager", "other": 1}
    ui.reset_cretop_upload_state(state)
    assert state == {"cretop_pdf_uploader": "new", "cretop_manager_name": "manager", "other": 1}


@pytest.mark.parametrize("data,expected", [
    ({"매출액": None}, False), ({"재무연도별": [{"연도": 2025, "매출액": None}]}, False),
    ({"당기순이익": "-"}, False), ({"당기순이익": float("nan")}, False),
    ({"매출액": 0}, True), ({"당기순이익": -5}, True),
    ({"재무연도별": [{"연도": 2025, "매출액": "1,000"}]}, True),
])
def test_only_real_financial_values_count(data, expected):
    assert ui.has_financial_data(data) is expected


def test_basic_information_without_financials_is_valid():
    result, error = ui.prepare_registration_data(basic_data(), {"종업원수": ""})
    assert not error
    assert result["당기순이익"] is None
    assert result["종업원수"] == ""
    assert result["사업자유형"] == "법인사업자"


def test_review_corrections_update_derived_fields():
    result, error = ui.prepare_registration_data(
        {**basic_data(), "설립년도": "2020", "시도": "경기도", "시군구": "과천시", "상시근로자수": 7},
        {"설립일": "2026-01-01", "사업장 소재지": "서울특별시 강남구 예시로", "종업원수": "3"},
    )
    assert not error
    assert result["설립년도"] == "2026"
    assert result["상시근로자수"] == 3
    assert result["시도"] == "서울"
    assert result["시군구"] == "강남구"


def test_editing_matching_preferences_preserves_saved_recommendations(monkeypatch):
    import matching_preferences as preferences
    original = {"저장정책자금_목록": [{"id": "policy-1"}], "기타기존정보": "유지"}
    monkeypatch.setattr(preferences, "_load_all", lambda _: {BUSINESS: original})
    monkeypatch.setattr(preferences, "_save_all", Mock())
    monkeypatch.setattr(preferences, "sync_matching_preferences", Mock())
    record = preferences.save_matching_preferences("test-user", BUSINESS, matching_keywords="설비")
    assert record["저장정책자금_목록"] == original["저장정책자금_목록"]
    assert record["기타기존정보"] == "유지"
    assert record["매칭키워드"] == ["설비"]


@pytest.mark.parametrize("edits", [{"업체명": ""}, {"사업자등록번호": "123"}, {"종업원수": "-1"}])
def test_invalid_identity_or_employee_is_rejected(edits):
    _, error = ui.prepare_registration_data(basic_data(), edits)
    assert error


@pytest.fixture
def services(monkeypatch):
    calls = {}
    for name, return_value in (
        ("check_user_customer_duplicate", False),
        ("append_cretop_to_user_customer_db", (Path("unused"), 1, "등록 완료", {}, pd.DataFrame())),
        ("refresh_existing_customer_from_cretop", (True, "갱신 완료", 1)),
        ("sync_customer_snapshot", (True, "완료")),
        ("save_customer_snapshot", {}),
        ("get_matching_preferences", {}),
        ("save_matching_preferences", {}),
    ):
        calls[name] = Mock(return_value=return_value)
        monkeypatch.setattr(ui, name, calls[name])
    calls["document"] = Mock(return_value={})
    monkeypatch.setattr(enterprise_documents, "register_existing_enterprise_document", calls["document"])
    # The financial saver is imported lazily; no-finance tests must never invoke it.
    import stock_valuation
    calls["financial"] = Mock(return_value=True)
    monkeypatch.setattr(stock_valuation, "save_cretop_financial_snapshot", calls["financial"])
    monkeypatch.setattr(ui, "write_runtime_error", Mock())
    monkeypatch.setattr(ui, "enrich_address_fields", lambda data: data)
    return calls


def test_basic_only_save_skips_financial_overwrite(services):
    result = ui.save_reviewed_registration("test-user", "manager", "test.pdf", basic_data())
    assert result["saved"] and result["cloud_saved"]
    services["append_cretop_to_user_customer_db"].assert_called_once()
    services["financial"].assert_not_called()
    services["document"].assert_called_once()


def test_existing_customer_is_not_modified_without_confirmation(services):
    services["check_user_customer_duplicate"].return_value = True
    result = ui.save_reviewed_registration("test-user", "manager", "test.pdf", basic_data())
    assert result["needs_confirmation"]
    services["refresh_existing_customer_from_cretop"].assert_not_called()
    services["sync_customer_snapshot"].assert_not_called()
    result = ui.save_reviewed_registration("test-user", "manager", "test.pdf", basic_data(), confirm_update=True)
    assert result["saved"]
    services["refresh_existing_customer_from_cretop"].assert_called_once()
    services["append_cretop_to_user_customer_db"].assert_not_called()


def test_failed_cloud_save_is_not_reported_as_cloud_success(services):
    services["sync_customer_snapshot"].return_value = (False, "대기")
    result = ui.save_reviewed_registration("test-user", "manager", "test.pdf", basic_data())
    assert result["saved"] and not result["cloud_saved"] and result["warnings"]


def test_failed_base_save_does_not_create_snapshots(services):
    services["append_cretop_to_user_customer_db"].return_value = (Path("unused"), 0, "저장 안 됨", {}, pd.DataFrame())
    result = ui.save_reviewed_registration("test-user", "manager", "test.pdf", basic_data())
    assert not result["saved"]
    services["save_customer_snapshot"].assert_not_called()
    services["document"].assert_not_called()


def test_last_mile_guard_prevents_empty_row(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "get_user_cumulative_db_path", lambda _: tmp_path / "customers.xlsx")
    write = Mock()
    monkeypatch.setattr(utils, "_write_cumulative_customer_db", write)
    _, count, message, _, _ = utils.append_cretop_to_user_customer_db("unused", "test-user", extracted_data={"매출액": None})
    assert count == 0 and message
    write.assert_not_called()


def test_basic_only_company_is_really_written_and_duplicate_is_not_appended(tmp_path, monkeypatch):
    path = tmp_path / "synthetic-customers.xlsx"
    monkeypatch.setattr(utils, "get_user_cumulative_db_path", lambda _: path)
    monkeypatch.setattr(utils, "find_customer_template", lambda: None)
    _, count, _, _, _ = utils.append_cretop_to_user_customer_db("unused", "test-user", extracted_data=basic_data())
    assert count == 1
    frame = pd.read_excel(path, sheet_name="고객DB", dtype=str)
    assert len(frame) == 1
    assert frame.loc[0, "업체명"] == "검증용 법인"
    assert frame.loc[0, "사업자등록번호"] == BUSINESS
    assert pd.isna(frame.loc[0, "당기순이익"])
    assert pd.isna(frame.loc[0, "종업원수"])
    _, count, _, _, _ = utils.append_cretop_to_user_customer_db("unused", "test-user", extracted_data=basic_data())
    assert count == 0
    assert len(pd.read_excel(path, sheet_name="고객DB")) == 1


def test_name_match_cannot_replace_a_different_valid_business_number(tmp_path, monkeypatch):
    path = tmp_path / "customers.xlsx"
    path.touch()
    frame = pd.DataFrame([{**basic_data(), "사업자등록번호": OTHER, "벤처": "Y"}])
    monkeypatch.setattr(utils, "get_user_cumulative_db_path", lambda _: path)
    monkeypatch.setattr(utils, "_read_cumulative_customer_db", lambda *_: frame.copy())
    monkeypatch.setattr(utils, "_read_business_numbers_only", lambda _: {OTHER})
    write = Mock()
    monkeypatch.setattr(utils, "_write_cumulative_customer_db", write)
    assert not utils.check_user_customer_duplicate("test-user", BUSINESS, "검증용 법인", "테스트")
    ok, _, _ = utils.refresh_existing_customer_from_cretop("test-user", basic_data())
    assert not ok
    write.assert_not_called()


def test_refresh_keeps_unknown_certifications_and_financials(tmp_path, monkeypatch):
    path = tmp_path / "customers.xlsx"
    path.touch()
    columns = utils.get_customer_db_columns()
    row = {key: "" for key in columns}
    row.update({**basic_data(), "벤처": "Y", "당기순이익": 200, "사업장 소재지": "이전 주소"})
    frame = pd.DataFrame([row], columns=columns)
    monkeypatch.setattr(utils, "get_user_cumulative_db_path", lambda _: path)
    monkeypatch.setattr(utils, "_read_cumulative_customer_db", lambda *_: frame.copy())
    write = Mock()
    monkeypatch.setattr(utils, "_write_cumulative_customer_db", write)
    ok, _, _ = utils.refresh_existing_customer_from_cretop("test-user", basic_data())
    assert ok
    saved_frame = write.call_args.args[1]
    assert saved_frame.loc[0, "벤처"] == "Y"
    assert saved_frame.loc[0, "당기순이익"] == 200


def test_reviewed_existing_name_changes_are_applied_locally(tmp_path, monkeypatch):
    path = tmp_path / "customers.xlsx"
    path.touch()
    columns = utils.get_customer_db_columns()
    row = {key: "" for key in columns}
    row.update(basic_data())
    frame = pd.DataFrame([row], columns=columns)
    monkeypatch.setattr(utils, "get_user_cumulative_db_path", lambda _: path)
    monkeypatch.setattr(utils, "_read_cumulative_customer_db", lambda *_: frame.copy())
    write = Mock()
    monkeypatch.setattr(utils, "_write_cumulative_customer_db", write)
    incoming = {**basic_data(), "업체명": "수정한 검증용 법인", "대표자명": "검증대표"}
    ok, _, _ = utils.refresh_existing_customer_from_cretop("test-user", incoming, reviewed_fields=ui.BASIC_FIELDS)
    assert ok
    saved = write.call_args.args[1]
    assert saved.loc[0, "업체명"] == incoming["업체명"]
    assert saved.loc[0, "대표자명"] == incoming["대표자명"]
    assert saved.loc[0, "사업자등록번호"] == BUSINESS


def make_app(tmp_path, monkeypatch, data, error=""):
    file = BytesIO(b"synthetic pdf bytes")
    file.name = "synthetic.pdf"
    monkeypatch.setattr(ui.st, "file_uploader", lambda *args, **kwargs: file)
    monkeypatch.setattr(ui, "run_cretop_worker", Mock(return_value=(data, error, [])))
    return AppTest.from_string(
        "from enterprise_registration_ui import render_cretop_registration\n"
        f"render_cretop_registration('test-user', '담당자', {str(tmp_path)!r})\n",
        default_timeout=15,
    ).run()


def test_analyse_then_review_then_save_app_flow(tmp_path, monkeypatch, services):
    app = make_app(tmp_path, monkeypatch, basic_data())
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert app.text_input(key="cretop_edit_업체명").value == "검증용 법인"
    assert any("기본정보만 등록" in item.value for item in app.info)
    services["append_cretop_to_user_customer_db"].assert_not_called()
    services["save_customer_snapshot"].assert_not_called()
    services["document"].assert_not_called()
    app.button[-1].click().run()
    assert not app.exception
    assert any("클라우드 저장 완료" in item.value for item in app.success)
    services["append_cretop_to_user_customer_db"].assert_called_once()
    services["save_matching_preferences"].assert_not_called()


def test_partial_ocr_can_be_corrected_but_blank_ocr_cannot_save(tmp_path, monkeypatch, services):
    partial = {**basic_data(), "사업자등록번호": "123"}
    app = make_app(tmp_path, monkeypatch, partial, "기본정보를 확인해 주세요.")
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert app.text_input(key="cretop_edit_사업자등록번호").value == "123"
    app.text_input(key="cretop_edit_사업자등록번호").set_value(BUSINESS)
    app.button[-1].click().run()
    assert not app.exception
    services["append_cretop_to_user_customer_db"].assert_called_once()
    app = make_app(tmp_path, monkeypatch, {"업체명": "", "사업자등록번호": ""})
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert app.error
    assert not [button for button in app.button if "확인한 기업정보 저장" in button.label]


def test_asset_route_is_lazy_and_does_not_default_to_first_customer(monkeypatch):
    monkeypatch.setattr(enterprise_documents, "load_registered_customers", lambda *a, **k: pd.DataFrame([basic_data()]))
    monkeypatch.setattr(enterprise_documents, "get_user_cumulative_db_path", lambda _: Path("unused"))
    overview = Mock()
    monkeypatch.setattr(enterprise_documents, "_load_enterprise_source_overview_cached", overview)
    app = AppTest.from_string(
        "from enterprise_documents import render_enterprise_information_assets\n"
        "render_enterprise_information_assets('test-user', '담당자')",
        default_timeout=15,
    ).run()
    assert not app.exception
    assert app.selectbox(key="enterprise_information_asset_customer").value is None
    overview.assert_not_called()


def test_app_keeps_both_registration_modes_but_defers_attachment_loading():
    source = Path("app.py").read_text(encoding="utf-8")
    branch = source.split('elif active_tab == "기업등록":', 1)[1].split('elif active_tab == "실행이력":', 1)[0]
    assert 'if registration_route == "등록기업에 자료 추가":' in branch
    assert branch.count("render_enterprise_information_assets(CURRENT_USER_ID") == 1
    assert "render_personal_business_registration(" in branch
    assert "render_cretop_registration(" in branch


def test_uncertain_representative_has_specific_warning_and_can_be_edited(tmp_path, monkeypatch, services):
    data = {**basic_data(), "대표자명": "", "_extraction": {"warnings": ["representative_conflict"]}}
    app = make_app(tmp_path, monkeypatch, data)
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert any("대표자명 인식 결과" in item.value for item in app.warning)
    assert app.text_input(key="cretop_edit_대표자명").value == ""
    app.text_input(key="cretop_edit_대표자명").set_value("검증대표")
    app.button[-1].click().run()
    saved = services["append_cretop_to_user_customer_db"].call_args.kwargs["extracted_data"]
    assert saved["대표자명"] == "검증대표"


def test_review_mobile_and_ksic_inputs_are_optional_separate_and_saved(tmp_path, monkeypatch, services):
    mobile = "-".join(("010", "0000", "0000"))
    data = {**basic_data(), "표준산업분류코드": "C24321", "표준산업분류차수": "11차", "주업종코드": "123456"}
    app = make_app(tmp_path, monkeypatch, data)
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert app.text_input(key="cretop_edit_대표자 휴대전화").value == ""
    assert app.text_input(key="cretop_edit_표준산업분류코드").value == "C24321"
    app.text_input(key="cretop_edit_대표자 휴대전화").set_value("+82 " + mobile[1:].replace("-", " "))
    app.text_input(key="cretop_edit_표준산업분류코드").set_value("C24322")
    app.button[-1].click().run()
    assert not app.exception
    saved = services["append_cretop_to_user_customer_db"].call_args.kwargs["extracted_data"]
    assert saved["대표자 휴대전화"] == mobile
    assert saved["표준산업분류코드"] == "C24322"
    assert saved["표준산업분류차수"] == "11차"
    assert saved["주업종코드"] == "123456"
    assert services["sync_customer_snapshot"].call_args.args[1]["대표자 휴대전화"] == mobile


@pytest.mark.parametrize("approved", [False, True])
def test_uncertain_financials_require_explicit_review(tmp_path, monkeypatch, services, approved):
    data = {**basic_data(), "매출액": 100, "연매출": 100, "당기순이익": -5,
            "재무연도별": [{"연도": 2025, "매출액": 100, "당기순이익": -5}],
            "_extraction": {"warnings": ["financial_alternate_source", "financial_year_unconfirmed"],
                            "financial_sources": {"income": {"kind": "alternate", "years": []}}}}
    app = make_app(tmp_path, monkeypatch, data)
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert any("결산연도를 확인하지 못했습니다" in item.value for item in app.warning)
    assert app.checkbox(key="cretop_confirm_financial").value is False
    if approved:
        app.checkbox(key="cretop_confirm_financial").check()
    app.button[-1].click().run()
    assert not app.exception
    saved = services["append_cretop_to_user_customer_db"].call_args.kwargs["extracted_data"]
    assert saved["매출액"] == (100 if approved else "")
    assert saved["당기순이익"] == (-5 if approved else "")
    assert bool(saved["재무연도별"]) is approved
    assert saved["업체명"] == data["업체명"]


@pytest.mark.parametrize("code,expected", [
    ("financial_year_unconfirmed", "결산연도를 확인하지 못했습니다"),
    ("financial_alternate_source", "다른 재무표에서 읽은 값"),
    ("financial_source_conflict", "재무표 인식 결과가 서로 달라"),
    ("financial_table_ocr_incomplete", "재무표의 일부 항목"),
    ("representative_conflict", "대표자명 인식 결과"),
    ("representative_ocr_uncertain", "대표자명 인식 결과"),
    ("certification_evidence_conflict", "인증정보 인식 결과가 서로 달라"),
    ("certification_ocr_incomplete", "인증정보의 일부 항목"),
])
def test_review_warning_does_not_claim_pages_were_unreadable(code, expected):
    messages = ui._extraction_warning_messages({
        "warnings": [code], "processed_pages": 8, "page_count": 8,
    })
    assert len(messages) == 1
    assert expected in messages[0]
    assert "페이지" not in messages[0]


@pytest.mark.parametrize("metadata,expected", [
    ({"warnings": ["analysis_time_limit"], "processed_pages": 3, "page_count": 8}, "분석 시간이 초과"),
    ({"warnings": ["ocr_unavailable"]}, "문자 인식 기능을 사용할 수 없어"),
    ({"warnings": ["page_3_ocr_failed", "page_5_ocr_failed"]}, "이미지 페이지의 문자 인식에 실패"),
    ({"warnings": [], "processed_pages": 3, "page_count": 8}, "전체 8페이지 중 3페이지"),
])
def test_actual_page_processing_failure_has_specific_warning(metadata, expected):
    messages = ui._extraction_warning_messages(metadata)
    assert len(messages) == 1
    assert expected in messages[0]


def test_unknown_extraction_warning_requests_review_without_exposing_raw_code():
    code = "unrecognized_internal_detail"
    messages = ui._extraction_warning_messages({"warnings": [code]})
    assert len(messages) == 1
    assert "추가 검토" in messages[0]
    assert "페이지" not in messages[0]
    assert code not in messages[0]


def test_financial_warning_in_app_does_not_claim_page_failure(tmp_path, monkeypatch, services):
    data = {**basic_data(), "매출액": 100,
            "_extraction": {"warnings": ["financial_year_unconfirmed"],
                            "page_count": 8, "processed_pages": 8}}
    app = make_app(tmp_path, monkeypatch, data)
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert any("결산연도를 확인하지 못했습니다" in item.value for item in app.warning)
    assert not any("페이지" in item.value for item in app.warning)
    assert app.checkbox(key="cretop_confirm_financial").value is False


@pytest.mark.parametrize("code,expected", [
    ("financial_table_ocr_incomplete", "재무표의 일부 항목"),
    ("certification_evidence_conflict", "인증정보 인식 결과가 서로 달라"),
    ("certification_ocr_incomplete", "인증정보의 일부 항목"),
])
def test_specific_ocr_review_warning_is_rendered(tmp_path, monkeypatch, services, code, expected):
    data = {**basic_data(), "매출액": 100, "_extraction": {"warnings": [code]}}
    app = make_app(tmp_path, monkeypatch, data)
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert any(expected in item.value for item in app.warning)
    if code == "financial_table_ocr_incomplete":
        assert app.checkbox(key="cretop_confirm_financial").value is False


@pytest.mark.parametrize("value,expected", [
    (None, "미확인"), ("", "미확인"), ("-", "미확인"), (True, "미확인"),
    (float("nan"), "미확인"), (float("inf"), "미확인"),
    (0, "0"), (0.0, "0"), (-1500.0, "-1,500"), ("1,250.50", "1,250.5"),
])
def test_financial_preview_keeps_unknown_zero_and_negative_distinct(value, expected):
    assert ui._format_financial_preview_value(value) == expected


def test_financial_history_preview_is_dated_latest_three_and_read_only():
    rows = [
        {"연도": "2022", "매출액": 10},
        {"연도": "2024", "매출액": 0, "당기순이익": -1500.0},
        {"연도": 2025, "매출액": 1234567, "자산총계": None},
        {"연도": "2023", "매출액": "-"},
        {"연도": None, "매출액": 999},
    ]
    preview = ui._financial_history_preview({"재무연도별": rows})
    assert [row["연도"] for row in preview] == ["2025", "2024", "2023"]
    assert preview[0]["매출액"] == "1,234,567"
    assert preview[0]["자산총계"] == "미확인"
    assert preview[1]["매출액"] == "0"
    assert preview[1]["당기순이익"] == "-1,500"
    assert preview[2]["매출액"] == "미확인"
    assert rows[0]["연도"] == "2022"
    assert rows[1]["당기순이익"] == -1500.0
    assert rows[2]["매출액"] == 1234567


def test_financial_history_table_and_existing_field_table_both_render(tmp_path, monkeypatch, services):
    data = {**basic_data(), "매출액": 1234567, "벤처": "N", "재무연도별": [
        {"연도": "2023", "매출액": None},
        {"연도": "2025", "매출액": 1234567, "당기순이익": -1500.0},
        {"연도": "2024", "매출액": 0, "당기순이익": 0},
    ]}
    app = make_app(tmp_path, monkeypatch, data)
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert len(app.dataframe) == 2
    history = app.dataframe[0].value
    assert history["연도"].tolist() == ["2025", "2024", "2023"]
    assert history["매출액"].tolist() == ["1,234,567", "0", "미확인"]
    assert history["당기순이익"].tolist() == ["-1,500", "0", "미확인"]
    flat = app.dataframe[1].value.set_index("항목")
    assert flat.loc["벤처", "추출값"] == "N"
    assert flat.loc["매출액", "추출값"] == "1234567"


def test_financial_preview_explains_original_units_without_changing_amounts(tmp_path, monkeypatch, services):
    data = {**basic_data(), "매출액": 123000000, "_extraction": {
        "warnings": [], "financial_sources": {
            "income": {"kind": "summary", "years": ["2025"], "unit_multiplier": 1000000},
            "balance": {"kind": "detail", "years": ["2025"], "unit_multiplier": 1000},
        },
    }}
    app = make_app(tmp_path, monkeypatch, data)
    app.button(key="cretop_analyze_button").click().run()
    assert not app.exception
    assert any("손익 원문 단위: 백만원" in item.value for item in app.caption)
    assert any("재무상태 원문 단위: 천원" in item.value for item in app.caption)
    assert not app.warning
    assert not any(item.key == "cretop_confirm_financial" for item in app.checkbox)
    flat = app.dataframe[0].value.set_index("항목")
    assert flat.loc["매출액", "추출값"] == "123000000"
