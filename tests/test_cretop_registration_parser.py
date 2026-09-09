"""Synthetic fixtures only: customer PDFs/identifiers must not enter Git."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

import cretop_runner
import cretop_worker as worker


BUSINESS = "-".join(("123", "45", "67890"))
CORPORATE = "-".join(("123456", "1234567"))

OVERVIEW = f"""
기업명 (주)예시테스트 영문기업명 EXAMPLE TEST
사업자번호 {BUSINESS} 법인(주민)번호 {CORPORATE}
대표자명 테스트대표 종업원수
설립형태 신규설립(개업) 설립년월 2026-05-08
기업유형 일반법인 기업규모
전화번호 팩스번호
주소 (00000) 서울 예시구 예시로 1, 2층
표준산업분류(10차) (C12345) 예시 제품 제조업
표준산업분류(11차) (C12345) 예시 제품 제조업
주요제품(상품)
"""


def test_basic_information_without_financials_is_extracted():
    result = worker.parse_document_text(OVERVIEW)
    assert result["업체명"] == "(주)예시테스트"
    assert result["대표자명"] == "테스트대표"
    assert result["사업자등록번호"] == BUSINESS
    assert result["법인등록번호"] == CORPORATE
    assert result["설립일"] == "2026-05-08"
    assert result["기업유형"] == "일반법인"
    assert result["사업장 소재지"].endswith("예시로 1, 2층")
    assert result["업종명"] == "예시 제품 제조업"
    assert result["기업규모"] == ""
    assert result["종업원수"] == ""
    assert result["재무연도별"] == []
    for field in ("매출액", "당기순이익", "영업이익", "자산총계", "부채총계", "자본총계"):
        assert result[field] == ""


def test_cover_colons_and_ocr_spaced_labels():
    spaced_number = BUSINESS.replace("-", " – ")
    result = worker.extract_identity(f"- 기 업 명 : 예시회사\n- 사 업 자 번 호 : {spaced_number}\n- 대 표 자 : 예시대표\n")
    assert result == {"업체명": "예시회사", "대표자명": "예시대표", "사업자등록번호": BUSINESS}


def test_labels_with_values_on_separate_lines_and_blank_employee():
    result = worker.parse_document_text("기업명\n예시회사\n영문기업명\nEXAMPLE\n대표자명\n예시대표\n종업원수\n설립형태 신규설립\n")
    assert result["업체명"] == "예시회사"
    assert result["대표자명"] == "예시대표"
    assert result["종업원수"] == ""


def test_existing_financial_summary_values_and_zero_remain_supported():
    result = worker.parse_document_text(OVERVIEW + """
요약 재무상태표 단위: 백만원
2023 2024 2025
자산총계 50 60 70
부채총계 20 10 0
자본총계 30 50 70
요약 손익계산서 단위: 백만원
2023 2024 2025
매출액 100 200 300
영업이익(손실) 10 20 -30
당기순이익 5 15 0
요약 현금흐름
""")
    assert result["매출액"] == 300_000_000
    assert result["영업이익"] == -30_000_000
    assert result["당기순이익"] == 0
    assert result["부채총계"] == 0
    assert result["재무연도별"][0]["연도"] == 2025
    assert result["재무연도별"][0]["당기순이익"] == 0


def test_blank_financial_rows_do_not_take_next_line_numbers_or_previous_year():
    assert worker.parse_latest_number_from_line("매출액", "매출액\n2025 2024 2023\n") is None
    assert worker.parse_latest_number_from_line("매출액", "매출액 123 456 -\n") is None
    assert worker.parse_latest_number_from_line("매출액", "매출액 - - -\n") is None
    result = worker.parse_document_text(OVERVIEW + "\n요약 손익계산서\n2023 2024 2025\n매출액 - - -\n당기순이익 - - -\n요약 현금흐름")
    assert result["매출액"] == ""
    assert result["재무연도별"] == []


def test_unrecognized_certifications_are_unknown_and_explicit_values_preserved():
    assert all(value == "" for value in worker.extract_certifications("").values())
    result = worker.extract_certifications("벤처 인증 이노비즈 미인증 메인비즈 미보유 부설연구소 보유")
    assert result["벤처"] == "Y"
    assert result["이노비즈"] == "N"
    assert result["메인비즈"] == "N"
    assert result["기업부설연구소"] == "Y"
    assert result["특허보유"] == ""


def test_missing_company_financials_never_borrow_peer_sales():
    result = worker.parse_document_text(OVERVIEW + """
요약 손익계산서 단위: 백만원
조회된 자료가 없습니다.
요약 현금흐름
조회된 자료가 없습니다.
동종업계 비교 단위: 백만원
매출액 1,000 2,000 3,000 4,000 5,000
당기순이익 10 20 30 40 50
""")
    assert result["매출액"] == ""
    assert result["당기순이익"] == ""


def test_peer_amounts_without_company_statement_heading_are_not_financials():
    result = worker.parse_document_text(OVERVIEW + "\n동종업계 비교 단위: 백만원\n매출액 100 200 300\n당기순이익 10 20 30\n")
    assert result["매출액"] == ""
    assert result["당기순이익"] == ""


def test_peer_heading_ends_empty_summary_even_without_another_statement():
    result = worker.parse_document_text(OVERVIEW + "\n요약 손익계산서 단위: 백만원\n동종업계 비교\n2023 2024 2025\n매출액 100 200 300\n당기순이익 10 20 30\n")
    assert result["매출액"] == ""
    assert result["당기순이익"] == ""
    assert result["재무연도별"] == []


def test_alternate_statement_title_with_explicit_units_is_supported():
    result = worker.parse_document_text(OVERVIEW + "\n포괄 손익현황 단위: 천원\n매출액 1,000 2,000 3,000\n당기순이익 100 200 300\n동종업계 비교 단위: 백만원\n매출액 9,999 9,999 9,999\n")
    assert result["매출액"] == 3_000_000
    assert result["당기순이익"] == 300_000


def fake_document(monkeypatch, embedded, ocr_text=OVERVIEW):
    pages = [SimpleNamespace(extract_text=lambda text=text: text) for text in embedded]
    monkeypatch.setitem(sys.modules, "pypdf", SimpleNamespace(PdfReader=lambda path: SimpleNamespace(pages=pages)))
    state = {"closed": False, "ocr_calls": []}
    class Document:
        def __getitem__(self, index):
            return SimpleNamespace(get_text=lambda kind: embedded[index])
        def close(self):
            state["closed"] = True
    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda path: Document()))
    def ocr(document, index, **kwargs):
        state["ocr_calls"].append(index)
        return ocr_text
    monkeypatch.setattr(worker, "_ocr_pdf_page", ocr)
    return state


def test_image_only_pdf_uses_ocr_and_identity_mode_stops_early(monkeypatch):
    state = fake_document(monkeypatch, [""] * 17)
    result = worker.extract_document("synthetic.pdf", "identity", progress=lambda *args: None)
    assert result["업체명"] == "(주)예시테스트"
    assert state["ocr_calls"] == [0]
    assert state["closed"] is True
    assert result["_extraction"]["method"] == "ocr"


def test_full_and_mixed_pdf_reads_scanned_pages_without_overwriting_embedded_text(monkeypatch):
    state = fake_document(monkeypatch, [OVERVIEW, ""], ocr_text="조회된 자료가 없습니다.")
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert result["사업자등록번호"] == BUSINESS
    assert result["매출액"] == ""
    assert state["ocr_calls"][0] == 1


def test_text_pdf_needs_no_ocr(monkeypatch):
    state = fake_document(monkeypatch, [OVERVIEW])
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert state["ocr_calls"] == []
    assert result["_extraction"]["method"] == "text"


def test_empty_ocr_is_an_error_and_missing_backend_has_safe_code(monkeypatch):
    fake_document(monkeypatch, [""], ocr_text="")
    with pytest.raises(worker.CretopExtractionError, match="identity_not_found"):
        worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    def unavailable(*args, **kwargs):
        raise ModuleNotFoundError("local-path-with-secret")
    monkeypatch.setattr(worker, "_ocr_pdf_page", unavailable)
    with pytest.raises(worker.CretopExtractionError, match="ocr_unavailable"):
        worker.extract_document("synthetic.pdf", progress=lambda *args: None)


def test_short_embedded_identity_is_preserved_when_ocr_is_unavailable(monkeypatch):
    fake_document(monkeypatch, [f"기업명: 예시회사\n사업자번호: {BUSINESS}"])
    def unavailable(*args, **kwargs):
        raise ModuleNotFoundError("dependency unavailable")
    monkeypatch.setattr(worker, "_ocr_pdf_page", unavailable)
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert result["업체명"] == "예시회사"
    assert result["_extraction"]["warnings"] == ["ocr_unavailable"]


def test_blank_cover_with_unavailable_ocr_still_reads_later_text_pages(monkeypatch):
    state = fake_document(monkeypatch, ["", OVERVIEW, ""])
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(True)
        raise ModuleNotFoundError("dependency unavailable")
    monkeypatch.setattr(worker, "_ocr_pdf_page", unavailable)
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert result["사업자등록번호"] == BUSINESS
    assert result["_extraction"]["processed_pages"] == 3
    assert result["_extraction"]["warnings"] == ["ocr_unavailable"]
    assert calls == [True]
    assert state["closed"] is True


def test_empty_ocr_does_not_erase_short_embedded_identity(monkeypatch):
    fake_document(monkeypatch, [f"기업명: 예시회사\n사업자번호: {BUSINESS}"], ocr_text="")
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert result["업체명"] == "예시회사"
    assert result["사업자등록번호"] == BUSINESS


def runner_process(monkeypatch, payload, *, returncode=0, stdout="", stderr=""):
    class Process:
        def __init__(self, command, **kwargs):
            self.returncode = returncode
            path = cretop_runner.Path(command[command.index("--output") + 1])
            path.write_text(json.dumps(payload), encoding="utf-8")
        def communicate(self, timeout=None):
            return stdout, stderr
    monkeypatch.setattr(cretop_runner.subprocess, "Popen", Process)


def test_runner_rejects_false_success_with_no_company_identity(monkeypatch):
    runner_process(monkeypatch, {"업체명": "", "사업자등록번호": "", "벤처": "N"})
    data, error, _ = cretop_runner.run_cretop_worker("synthetic.pdf")
    assert data == {}
    assert "업체명" in error


def test_runner_accepts_partial_identity_for_manual_confirmation(monkeypatch):
    runner_process(monkeypatch, {"업체명": "예시회사", "사업자등록번호": "", "당기순이익": ""})
    data, error, _ = cretop_runner.run_cretop_worker("synthetic.pdf")
    assert data["업체명"] == "예시회사"
    assert error == ""


def test_runner_does_not_expose_native_error_details(monkeypatch):
    runner_process(monkeypatch, {}, returncode=2, stdout='secret customer text\n{"type":"error","code":"ocr_unavailable"}', stderr="secret full document path")
    data, error, logs = cretop_runner.run_cretop_worker("synthetic.pdf")
    assert data == {}
    assert "한글 인식" in error
    assert "secret" not in error
    assert logs == []
