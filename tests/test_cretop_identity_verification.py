"""Synthetic OCR evidence; never include customer source documents."""
import sys
import time
from types import SimpleNamespace

import pytest

import cretop_worker as worker
from test_cretop_registration_parser import BUSINESS, OVERVIEW, fake_document


def test_cover_overview_disagreement_does_not_prioritize_bas_label():
    assert worker.extract_identity("대표자: 예시대표\n기업개요\n대표자명 BAS")["대표자명"] == ""
    assert worker._representative_resolution("대표자명 BAS\n대표자: 예시대표")[1] == "representative_conflict"


def test_matching_cover_and_overview_and_related_parties():
    text = "대표자: 예시 대표\n기업개요\n대표자명 예시대표\n주요 구매처\n대표자명 다른대표"
    assert worker.extract_identity(text)["대표자명"] == "예시 대표"
    assert worker.extract_identity("대표자: 예시대표\n요약 손익계산서\n대표자명 다른대표")["대표자명"] == "예시대표"


@pytest.mark.parametrize("name", ["BAS", "Kim", "Jean O'Neil", "王小明", "예시대표 외 1명"])
def test_real_text_names_are_not_banned(name):
    assert worker.extract_identity(f"대표자명: {name}")["대표자명"] == name


def test_complete_overview_with_short_ocr_token_still_retries(monkeypatch):
    original = OVERVIEW.replace("테스트대표", "BAS")
    calls = []
    def ocr(*args, **kwargs):
        calls.append(kwargs["psm"])
        return OVERVIEW
    monkeypatch.setattr(worker, "_ocr_pdf_page", ocr)
    review = {}
    result = worker._improve_identity_ocr(None, 1, original, mode="full", deadline=time.monotonic()+60, review=review)
    assert calls == [4, 3]
    assert worker.extract_identity(result)["대표자명"] == "테스트대표"
    assert not review


def test_uncorroborated_ocr_name_is_not_auto_saved(monkeypatch):
    fake_document(monkeypatch, [""], ocr_text=OVERVIEW.replace("테스트대표", "BAS"))
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert result["대표자명"] == ""
    assert "representative_ocr_uncertain" in result["_extraction"]["warnings"]
    assert result["사업자등록번호"] == BUSINESS


def test_conflicting_cover_overview_warns_and_keeps_other_fields(monkeypatch):
    fake_document(monkeypatch, [OVERVIEW, OVERVIEW.replace("테스트대표", "BAS")])
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert result["대표자명"] == ""
    assert "representative_conflict" in result["_extraction"]["warnings"]
    assert result["법인등록번호"]


def test_identity_mode_also_holds_uncertain_ocr_name(monkeypatch):
    fake_document(monkeypatch, [""], ocr_text=OVERVIEW.replace("테스트대표", "BAS"))
    result = worker.extract_document("synthetic.pdf", "identity", progress=lambda *args: None)
    assert result["대표자명"] == ""
    assert "representative_ocr_uncertain" in result["_extraction"]["warnings"]


def test_missing_korean_language_cannot_silently_fall_back_to_english(monkeypatch):
    class TesseractError(Exception):
        pass
    worker._require_korean_ocr_languages.cache_clear()
    monkeypatch.setitem(sys.modules, "pytesseract", SimpleNamespace(get_languages=lambda **kw: ["eng"], TesseractError=TesseractError))
    with pytest.raises(TesseractError, match="required_ocr_language_unavailable"):
        worker._require_korean_ocr_languages()
    worker._require_korean_ocr_languages.cache_clear()


def test_required_languages_are_checked_once_per_worker(monkeypatch):
    calls = []
    worker._require_korean_ocr_languages.cache_clear()
    monkeypatch.setitem(sys.modules, "pytesseract", SimpleNamespace(get_languages=lambda **kw: calls.append(True) or ["kor", "eng"]))
    worker._require_korean_ocr_languages()
    worker._require_korean_ocr_languages()
    assert calls == [True]
    worker._require_korean_ocr_languages.cache_clear()


@pytest.mark.parametrize("name", ["Kim", "Lee", "Jean O'Neil"])
def test_clear_international_ocr_names_remain_supported(monkeypatch, name):
    fake_document(monkeypatch, [""], ocr_text=OVERVIEW.replace("테스트대표", name))
    assert worker.extract_document("synthetic.pdf", progress=lambda *args: None)["대표자명"] == name


def test_localized_language_banner_keeps_language_check(monkeypatch):
    import subprocess
    worker._require_korean_ocr_languages.cache_clear()
    def undecodable(**kwargs):
        raise UnicodeDecodeError("utf-8", b"\xc1", 0, 1, "localized banner")
    monkeypatch.setitem(sys.modules, "pytesseract", SimpleNamespace(get_languages=undecodable, pytesseract=SimpleNamespace(tesseract_cmd="tesseract")))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kw: SimpleNamespace(returncode=0, stdout=b"\xc1localized banner\neng\nkor\n"))
    worker._require_korean_ocr_languages()
    worker._require_korean_ocr_languages.cache_clear()


SUMMARY = "요약 손익계산서\n단위: 백만원\n구분 - - 2025\n매출액 - - 123\n영업이익 - - -12\n당기순이익 - - 0"


def test_financial_ocr_evidence_is_isolated_from_identity_and_certifications():
    source = OVERVIEW + "\n메인비즈 인증\n요약 손익계산서\n단위: 백만원\n매출액 - - -\n요약 현금흐름"
    result = worker.parse_document_text(source, financial_evidence={"income": SUMMARY + "\n대표자명 BAS\n메인비즈 미인증"})
    assert result["대표자명"] == "테스트대표"
    assert result["메인비즈"] == "Y"
    assert result["매출액"] == 123_000_000
    assert result["영업이익"] == -12_000_000
    assert result["당기순이익"] == 0
    assert result["재무연도별"][0]["연도"] == 2025


def test_supplemental_summary_conflict_is_not_saved(monkeypatch):
    fake_document(monkeypatch, ["", "", ""], ocr_text=OVERVIEW + "\n" + SUMMARY)
    calls = []
    def tables(document, index, text, **kwargs):
        calls.append(index)
        return {"income": SUMMARY.replace("123", str(123 + index))}, []
    monkeypatch.setitem(sys.modules, "cretop_ocr_tables", SimpleNamespace(get_verified_financial_tables=tables))
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert calls == [0, 1, 2]
    assert result["매출액"] == result["연매출"] == result["당기순이익"] == ""
    assert result["대표자명"] == "테스트대표"
    assert "financial_source_conflict" in result["_extraction"]["warnings"]
    assert result["_extraction"]["financial_sources"]["income"]["kind"] == "conflict"


def test_duplicate_account_conflict_has_safe_metadata_and_warning(monkeypatch):
    source = OVERVIEW + "\n" + SUMMARY + "\n매출액 - - 999"
    fake_document(monkeypatch, [source])
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert result["매출액"] == ""
    assert result["_extraction"]["financial_sources"]["income"]["conflicting_accounts"] == ["매출액"]
    assert "financial_source_conflict" in result["_extraction"]["warnings"]
