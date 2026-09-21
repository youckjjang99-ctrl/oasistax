"""Synthetic cover layouts only; never copy customer source text or identifiers."""
import time

import pytest

import cretop_worker as worker
from test_cretop_registration_parser import BUSINESS, OVERVIEW, fake_document


@pytest.mark.parametrize("value", [
    "", "-", "-사업자번호", "- 사업자번호 :", "-대표자", "영문기업명",
    BUSINESS, "조회된 자료가 없습니다.", "거래비중 결산년도 매출액", "(주)",
])
def test_company_validator_rejects_label_placeholder_and_identifier_values(value):
    assert not worker.valid_company_name(value)


@pytest.mark.parametrize("value", [
    "(주)합성기업", "주식회사 합성 123", "3M Example", "Jean's Co., Ltd.",
    "王小明商事", "A-B & Partners", "㈜123",
])
def test_company_validator_preserves_unfamiliar_legal_names(value):
    assert worker.valid_company_name(value)


@pytest.mark.parametrize("bullet", ["- ", "-", "• "])
def test_detached_cover_labels_do_not_borrow_values_or_conflict_with_overview(bullet):
    cover = (
        f"{bullet}기업명 :\n{bullet}사업자번호 :\n{bullet}대표자 :\n"
        f"(주)합성표지기업\n{BUSINESS}\n합성표지대표\n"
    )
    assert worker.extract_identity(cover)["업체명"] == ""
    assert worker.extract_identity(cover)["대표자명"] == ""
    result = worker.parse_document_text(cover + "기업개요\n" + OVERVIEW)
    assert result["업체명"] == "(주)예시테스트"
    assert result["대표자명"] == "테스트대표"
    assert worker._representative_resolution(cover + OVERVIEW)[1] == ""


@pytest.mark.parametrize("bad_name", ["-사업자번호", "-사업자번호 " + BUSINESS])
def test_malformed_company_value_is_skipped_for_later_overview(bad_name):
    result = worker.extract_identity(f"기업명 {bad_name}\n" + OVERVIEW)
    assert result["업체명"] == "(주)예시테스트"


def test_identity_mode_reads_overview_after_detached_cover(monkeypatch):
    cover = f"- 기업명 :\n- 사업자번호 :\n- 대표자 :\n(주)합성표지기업\n{BUSINESS}\n합성표지대표"
    state = fake_document(monkeypatch, [cover, OVERVIEW])
    result = worker.extract_document("synthetic.pdf", "identity", progress=lambda *args: None)
    assert result["업체명"] == "(주)예시테스트"
    assert result["대표자명"] == "테스트대표"
    assert result["_extraction"]["processed_pages"] == 2
    assert result["_extraction"]["warnings"] == []
    assert not state["ocr_calls"]


def test_detached_cover_without_overview_stays_partial_not_falsely_complete(monkeypatch):
    cover = f"- 기업명 :\n- 사업자번호 :\n- 대표자 :\n(주)합성표지기업\n{BUSINESS}\n합성표지대표"
    fake_document(monkeypatch, [cover])
    result = worker.extract_document("synthetic.pdf", "identity", progress=lambda *args: None)
    assert result["사업자등록번호"] == BUSINESS
    assert result["업체명"] == result["대표자명"] == ""


def test_invalid_first_ocr_company_does_not_block_valid_retry(monkeypatch):
    original = f"기업명 -사업자번호\n사업자번호 {BUSINESS}"
    monkeypatch.setattr(worker, "_ocr_pdf_page", lambda *args, **kwargs: OVERVIEW)
    result = worker._improve_identity_ocr(None, 1, original, mode="full", deadline=time.monotonic() + 30)
    assert result == OVERVIEW


def test_company_identity_does_not_borrow_from_a_related_party():
    result = worker.extract_identity(f"사업자번호 {BUSINESS}\n주요 구매처\n기업명 합성거래처\n대표자명 합성거래처대표")
    assert result["업체명"] == result["대표자명"] == ""
    assert not worker.has_company_identity({"업체명": "-사업자번호"})
    assert worker.has_company_identity({"사업자등록번호": BUSINESS})


def test_ksic_prefers_explicit_11th_revision_with_its_own_description():
    result = worker.parse_document_text(
        "기업명 합성기업\n"
        "표준산업분류(10차) (C12345) 이전 합성 제조업\n"
        "표준산업분류(11차) (C54321) 최신 합성 제조업\n"
    )
    assert result["표준산업분류코드"] == "C54321"
    assert result["표준산업분류차수"] == "11차"
    assert result["업종명"] == "최신 합성 제조업"
    assert "주업종코드" not in result


@pytest.mark.parametrize("label,code", [("10차", "01234"), ("11차", "J0123")])
def test_ksic_single_revision_preserves_printed_code_and_leading_zero(label, code):
    result = worker.parse_document_text(f"기업명 합성기업\n표준산업분류({label}) ({code}) 합성업")
    assert result["표준산업분류코드"] == code
    assert result["표준산업분류차수"] == label
    assert result["업종명"] == "합성업"


def test_ksic_missing_code_is_not_inferred_from_description_or_tax_code():
    result = worker.parse_document_text("기업명 합성기업\n표준산업분류(11차) 합성 제조업\n주업종코드 999999")
    assert result["표준산업분류코드"] == ""
    assert result["표준산업분류차수"] == "11차"
    assert result["업종명"] == "합성 제조업"
    assert "주업종코드" not in result


def test_ksic_multiline_description_keeps_11th_revision_and_excludes_products():
    result = worker.extract_industry_fields(
        "표준산업분류(10차) (C12345) 이전 합성\n제품 제조업\n"
        "표준산업분류(11차) (C54321) 최신 합성\n제품 제조업\n"
        "주요제품(상품) 혼입금지 제품 설명\n"
    )
    assert result == {
        "업종명": "최신 합성 제품 제조업", "표준산업분류코드": "C54321",
        "표준산업분류차수": "11차",
    }


@pytest.mark.parametrize("boundary", [
    "주요제품(상품)혼입금지", "사업장 소재지 혼입금지", "기업명 혼입금지",
    "주요제품 혼입금지", "주요상품 혼입금지", "주요제품: 혼입금지", "주요상품： 혼입금지",
    "주요 구매처\n기업명 혼입금지", "주요 판매처\n기업명 혼입금지",
    "관계회사\n기업명 혼입금지", "기업신용등급 혼입금지", "기술력 기업인증 혼입금지",
    "요약 손익계산서\n매출액 혼입금지", "동종업계 비교\n기업명 혼입금지",
])
def test_ksic_multiline_name_stops_before_identity_fields_and_sections(boundary):
    result = worker.extract_industry_fields(
        "표준산업분류(10차)\n(C12345) 합성\n제품 제조업\n" + boundary
    )
    assert result == {
        "업종명": "합성 제품 제조업", "표준산업분류코드": "C12345",
        "표준산업분류차수": "10차",
    }


def test_ksic_label_code_and_description_can_each_start_on_separate_lines():
    result = worker.extract_industry_fields(
        "표준산업분류(11차)\n(C54321)\n합성 제품\n제조업\n주요제품(상품) 혼입금지"
    )
    assert result["업종명"] == "합성 제품 제조업"
    assert result["표준산업분류코드"] == "C54321"
    assert result["표준산업분류차수"] == "11차"


def test_missing_ksic_and_financials_stay_empty_for_registration_without_financials():
    result = worker.parse_document_text(f"기업명 합성기업\n사업자번호 {BUSINESS}\n대표자명 합성대표")
    assert result["표준산업분류코드"] == result["표준산업분류차수"] == result["업종명"] == ""
    assert result["재무연도별"] == []
    assert all(result[field] == "" for field in (
        "매출액", "연매출", "전년도매출", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계",
    ))
