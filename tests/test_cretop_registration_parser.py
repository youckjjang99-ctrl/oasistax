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


def test_representative_table_header_is_not_a_person_and_cover_is_preserved():
    text = "대표자명 | 거래비중 | 결산년도 자본금 자산총계 매출액 순이익\n대표자: 예시대표\n"
    assert worker.extract_identity(text)["대표자명"] == "예시대표"
    assert worker.extract_identity(text.split("\n대표자:")[0])["대표자명"] == ""


@pytest.mark.parametrize("name", ["예시대표 외 1명", "王小明", "Jean O'Neil"])
def test_representative_guard_preserves_joint_and_international_names(name):
    assert worker.extract_identity(f"대표자명: {name}")["대표자명"] == name


def test_certification_panel_maps_headers_and_statuses_by_column():
    result = worker.extract_certifications("""
기술력 기업인증ㆍ산업재산권 현황
기업인증
( 벤처 ) ( 이노비즈 ) ( 메인비즈 ) (연구개발전담부서) ( 부설연구소 )
미인증 인증 인증 인증 미인증
산업재산권
주요 주주
""")
    assert {key: result[key] for key in ("벤처", "이노비즈", "메인비즈", "연구개발전담부서", "기업부설연구소")} == {
        "벤처": "N", "이노비즈": "Y", "메인비즈": "Y", "연구개발전담부서": "Y", "기업부설연구소": "N",
    }


@pytest.mark.parametrize("headers,statuses", [
    ("벤처 이노비즈 메인비즈 부설연구소", "인증 미인증 인증 인증 미인증"),
    ("벤처 이노비즈 메인비즈 연구개발전담부서 부설연구소", "인증 인증 인증 미인증"),
    ("벤처 이노비즈 읽기실패 연구개발전담부서 부설연구소", "인증 인증 인증 미인증"),
])
def test_incomplete_certification_rows_do_not_shift_or_guess_values(headers, statuses):
    result = worker.extract_certifications(f"기업인증\n{headers}\n{statuses}\n산업재산권")
    assert all(result[key] == "" for key in ("벤처", "이노비즈", "메인비즈", "연구개발전담부서", "기업부설연구소"))


def test_conflicting_certifications_and_other_companies_are_not_used():
    assert worker.extract_certifications("기업인증\n이노비즈 인증\n이노비즈 미인증\n산업재산권")["이노비즈"] == ""
    assert worker.extract_certifications("기업인증\n주요 구매처\n메인비즈 인증")["메인비즈"] == ""
    assert worker.extract_certifications("기업인증\n벤처 이노비즈 메인비즈\n미인증 인증\n심사 별도정보\n인증")["메인비즈"] == ""


def test_certification_last_column_does_not_borrow_first_column_status():
    result = worker.extract_certifications("벤처 이노비즈 메인비즈\n미인증 인증 인증")
    assert result["메인비즈"] == "Y"


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


def test_ocr_retries_incomplete_overview_even_when_company_name_was_read(monkeypatch):
    state = fake_document(monkeypatch, ["", ""])
    def ocr(document, index, *, psm=6, **kwargs):
        state["ocr_calls"].append((index, psm))
        if index == 0:
            return f"기업명: (주)예시테스트\n사업자번호: {BUSINESS}\n대표자: 예시대표"
        return OVERVIEW if psm == 4 else f"기업명: (주)예시테스트\n사업자번호: {BUSINESS}"
    monkeypatch.setattr(worker, "_ocr_pdf_page", ocr)
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert (1, 4) in state["ocr_calls"]
    assert result["법인등록번호"] == CORPORATE
    assert result["설립일"] == "2026-05-08"
    assert result["종업원수"] == ""


def test_optional_layout_retry_failure_preserves_existing_ocr(monkeypatch):
    state = fake_document(monkeypatch, [""])
    def ocr(document, index, *, psm=6, **kwargs):
        if psm != 6:
            raise TimeoutError("private source detail")
        return f"기업명: 예시회사\n사업자번호: {BUSINESS}"
    monkeypatch.setattr(worker, "_ocr_pdf_page", ocr)
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert result["사업자등록번호"] == BUSINESS
    assert state["closed"] is True


CERT_PANEL = "기업인증\n벤처 이노비즈 메인비즈 연구개발전담부서 부설연구소\n미인증 인증 인증 인증 미인증\n산업재산권"


def test_ocr_retry_cannot_replace_known_company_with_another_company(monkeypatch):
    import time
    original = f"기업명: 다른예시회사\n사업자번호: {BUSINESS}"
    monkeypatch.setattr(worker, "_ocr_pdf_page", lambda *args, **kwargs: OVERVIEW)
    assert worker._improve_identity_ocr(None, 1, original, mode="full", deadline=time.monotonic()+30) == original


def test_ocr_retry_accepts_equivalent_legal_company_notation(monkeypatch):
    import time
    original = f"기업명: 주식회사 예시테스트\n사업자번호: {BUSINESS}"
    monkeypatch.setattr(worker, "_ocr_pdf_page", lambda *args, **kwargs: OVERVIEW)
    assert worker._improve_identity_ocr(None, 1, original, mode="full", deadline=time.monotonic()+30) == OVERVIEW


def test_ocr_retry_does_not_discard_previously_read_business_number(monkeypatch):
    import time
    original = f"기업명: (주)예시테스트\n사업자번호: {BUSINESS}"
    candidate = OVERVIEW.replace(BUSINESS, "")
    monkeypatch.setattr(worker, "_ocr_pdf_page", lambda *args, **kwargs: candidate)
    assert worker._improve_identity_ocr(None, 1, original, mode="full", deadline=time.monotonic()+30) == original


def test_conflicting_supplemental_certification_is_unknown_not_overwritten(monkeypatch):
    import time
    original = "기업인증\n메인비즈 미인증\n산업재산권"
    monkeypatch.setattr(worker, "_ocr_pdf_page", lambda *args, **kwargs: CERT_PANEL)
    evidence, warning = worker._certification_ocr_evidence(None, 0, original, deadline=time.monotonic()+30)
    assert evidence["메인비즈"] == ""
    assert warning == "certification_evidence_conflict"
    assert worker.parse_document_text(original, certification_evidence=evidence)["메인비즈"] == ""
    assert worker.parse_document_text(original, certification_evidence={"메인비즈":"Y"})["메인비즈"] == ""


def test_original_certification_conflict_cannot_be_resolved_by_ocr(monkeypatch):
    import time
    original = "기업인증\n메인비즈 인증\n메인비즈 미인증\n산업재산권"
    monkeypatch.setattr(worker, "_ocr_pdf_page", lambda *args, **kwargs: CERT_PANEL)
    evidence, warning = worker._certification_ocr_evidence(None, 0, original, deadline=time.monotonic()+30)
    assert evidence["메인비즈"] == ""
    assert warning == "certification_evidence_conflict"
    assert worker.parse_document_text(original, certification_evidence={"메인비즈":"Y"})["메인비즈"] == ""


def test_certificate_ocr_supplement_does_not_replace_financial_page(monkeypatch):
    fake_document(monkeypatch, ["", "", ""])
    calls = []
    def ocr(document, index, *, remove_table_borders=False, **kwargs):
        calls.append((index, remove_table_borders))
        if index < 2:
            return OVERVIEW
        if remove_table_borders:
            return CERT_PANEL + "\n요약 손익계산서 단위: 백만원\n매출액 999\n요약 현금흐름"
        return "기업인증\n미인증 인증 인증 인증 미인증\n산업재산권\n요약 손익계산서 단위: 백만원\n매출액 25\n요약 현금흐름"
    monkeypatch.setattr(worker, "_ocr_pdf_page", ocr)
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert (2, True) in calls
    assert result["매출액"] == 25_000_000
    assert result["메인비즈"] == result["이노비즈"] == "Y"
    assert "메인비즈" in result["키워드메모"]


def test_unreadable_certificate_ocr_stays_unknown_with_safe_warning(monkeypatch):
    fake_document(monkeypatch, [""])
    monkeypatch.setattr(worker, "_ocr_pdf_page", lambda *args, **kwargs: OVERVIEW + "\n기업인증\n미인증 인증 인증 인증 미인증\n산업재산권")
    result = worker.extract_document("synthetic.pdf", progress=lambda *args: None)
    assert result["메인비즈"] == result["이노비즈"] == ""
    assert "certification_ocr_incomplete" in result["_extraction"]["warnings"]


def test_blue_outline_removal_preserves_letters_black_status_and_original():
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (1000, 1000), "white")
    draw = ImageDraw.Draw(image)
    blue = (0, 100, 255)
    draw.rectangle((100, 100, 220, 125), outline=blue, width=2)
    draw.rectangle((120, 108, 123, 116), fill=blue)  # small disconnected letter
    draw.rectangle((130, 138, 140, 150), fill="black")
    result = worker._remove_blue_table_outlines(image)
    assert result.getpixel((100, 110)) == (255, 255, 255)
    assert result.getpixel((121, 110)) == blue
    assert result.getpixel((135, 140)) == (0, 0, 0)
    assert image.getpixel((100, 110)) == blue


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
