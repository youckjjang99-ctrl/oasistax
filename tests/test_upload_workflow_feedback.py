"""Upload UI regressions with synthetic files; no network or customer data."""
from __future__ import annotations

from io import BytesIO
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import articles_review
import consultation_journal
import employee_status
import enterprise_documents


BUSINESS = "-".join(("123", "45", "67890"))


def uploaded_file(name="synthetic.pdf"):
    uploaded = BytesIO(b"synthetic fixture only")
    uploaded.name, uploaded.size = name, 22
    uploaded.type = "application/octet-stream"
    return uploaded


def card_text(app):
    return "\n".join(item.value for item in app.markdown)


@pytest.mark.parametrize("attachment_failure", [False, True])
def test_articles_result_survives_rerun_and_discloses_attachment_failure(monkeypatch, attachment_failure):
    file = uploaded_file()
    monkeypatch.setattr(articles_review.st, "file_uploader", lambda *a, **kw: file)
    monkeypatch.setattr(articles_review, "extract_articles_text", Mock(return_value=("검토용 정관 조항 " * 30, {})))
    monkeypatch.setattr(articles_review, "analyze_articles", Mock(return_value={"score": 90}))
    saved = Mock()
    monkeypatch.setattr(articles_review, "save_articles_review", saved)
    monkeypatch.setattr(articles_review, "get_latest_articles_review", lambda *a: {})
    attachment = Mock(side_effect=RuntimeError("synthetic failure") if attachment_failure else None)
    monkeypatch.setattr(enterprise_documents, "register_enterprise_document_bytes", attachment)
    app = AppTest.from_string(
        "from articles_review import render_articles_review\n"
        f"render_articles_review('test-user', {BUSINESS!r}, '검증용 기업')\n"
    ).run()
    app.button(key=f"articles_analyze_{BUSINESS}").click().run()
    assert not app.exception
    saved.assert_called_once()
    attachment.assert_called_once()
    text = card_text(app)
    if attachment_failure:
        assert "첨부자료 연결은 완료하지 못했습니다" in text
        assert 'data-state="warning"' in text
    else:
        assert "정관 분석과 기업히스토리 저장을 완료" in text
    app.run()
    assert not app.exception
    saved.assert_called_once()  # Rerender must not submit the upload again.


@pytest.mark.parametrize("fails", [False, True])
def test_generic_document_outcome_is_truthful_and_persists(monkeypatch, fails):
    monkeypatch.setattr(enterprise_documents.st, "file_uploader", lambda *a, **kw: uploaded_file())
    monkeypatch.setattr(articles_review, "extract_articles_text", Mock(return_value=("검증용 세무조정 자료 " * 20, {})))
    save = Mock(side_effect=RuntimeError("synthetic failure") if fails else None)
    monkeypatch.setattr(enterprise_documents, "register_enterprise_document_bytes", save)
    app = AppTest.from_string(
        "import pandas as pd\n"
        "from enterprise_documents import _render_generic_upload\n"
        f"_render_generic_upload('test-user', pd.Series({{'사업자등록번호': {BUSINESS!r}, '업체명': '검증용 기업'}}), 'tax_adjustment')\n"
    ).run()
    app.button(key=f"enterprise_doc_analyze_tax_adjustment_{BUSINESS}").click().run()
    assert not app.exception
    save.assert_called_once()
    text = card_text(app)
    assert ("기업자료 분석·등록에 실패" in text) if fails else ("해당 기업에 연결해 저장" in text)
    assert "클라우드 저장 완료" not in text
    if fails:
        assert 'data-state="error"' in text


def employee_review_row():
    return {
        "포함": True, "연번": 1, "사업장명": "검증용 기업", "사업장관리번호": "",
        "성명": "검증직원", "생년월일6자리": "900101", "재직상태": "가입중", "고용종료일": "",
    }


@pytest.fixture
def employee_app(monkeypatch):
    monkeypatch.setattr(employee_status.st, "file_uploader", lambda *a, **kw: [uploaded_file("synthetic.csv")])
    monkeypatch.setattr(employee_status, "parse_roster", Mock(return_value=([{}], {})))
    monkeypatch.setattr(employee_status, "_employees_to_review_rows", lambda *a, **kw: [employee_review_row()])
    monkeypatch.setattr(employee_status, "load_employee_versions", lambda *a, **kw: [])
    monkeypatch.setattr(enterprise_documents, "register_enterprise_document_bytes", Mock())
    monkeypatch.setattr(employee_status, "_review_rows_to_employees", lambda *a: ([{"name_masked": "검*"}], []))
    return AppTest.from_string(
        "from employee_status import render_employee_status\n"
        f"render_employee_status('test-user', {BUSINESS!r}, '검증용 기업')\n"
    ).run()


@pytest.mark.parametrize("cloud_saved", [False, True])
def test_employee_review_and_save_keep_cloud_outcomes_distinct(monkeypatch, employee_app, cloud_saved):
    saved = Mock(return_value=({}, "로컬·Supabase 저장" if cloud_saved else "로컬 저장"))
    monkeypatch.setattr(employee_status, "save_employee_roster", saved)
    app = employee_app
    app.button(key=f"employee_roster_analyze_{BUSINESS}").click().run()
    assert not app.exception
    assert "직원현황은 아직 저장 전" in card_text(app)
    saved.assert_not_called()
    save_button = next(button for button in app.button if button.label == "검수 완료 후 직원현황 저장")
    save_button.click().run()
    assert not app.exception
    saved.assert_called_once()
    text = card_text(app)
    assert ("로컬·Supabase 저장" in text) if cloud_saved else ("클라우드 저장은 확인되지 않았습니다" in text)


def test_employee_failed_save_preserves_review_for_retry(monkeypatch, employee_app):
    monkeypatch.setattr(employee_status, "save_employee_roster", Mock(side_effect=RuntimeError("synthetic failure")))
    app = employee_app
    app.button(key=f"employee_roster_analyze_{BUSINESS}").click().run()
    next(button for button in app.button if button.label == "검수 완료 후 직원현황 저장").click().run()
    assert not app.exception
    assert "직원현황을 저장하지 못했습니다" in card_text(app)
    state_key = f"employee_roster_review_{employee_status._company_key(BUSINESS, '검증용 기업')}"
    assert app.session_state[state_key]["rows"]


@pytest.mark.parametrize("stored", [False, True])
def test_audio_pipeline_reuses_existing_calls_and_retains_storage_outcome(monkeypatch, stored):
    monkeypatch.setattr(consultation_journal.st, "file_uploader", lambda *a, **kw: uploaded_file("synthetic.m4a"))
    monkeypatch.setattr(consultation_journal, "storage_is_configured", lambda: True)
    monkeypatch.setattr(consultation_journal, "_read_secret", lambda name, default="": "synthetic-key")
    transcribe = Mock(return_value=("검증용 녹취", False, "synthetic-cache"))
    summarize = Mock(return_value=({"summary": "검증용 상담일지"}, False, "synthetic-summary-cache"))
    upload = Mock(return_value={"stored": stored})
    monkeypatch.setattr(consultation_journal, "transcribe_audio_with_cache", transcribe)
    monkeypatch.setattr(consultation_journal, "summarize_consultation_with_cache", summarize)
    monkeypatch.setattr(consultation_journal, "upload_audio", upload)
    monkeypatch.setattr(consultation_journal, "_prepare_fast_cloud_audio", lambda content, name: (content, name, "audio/mp4", False))
    monkeypatch.setattr(consultation_journal, "list_company_audio", lambda *a, **kw: [])
    app = AppTest.from_string(
        "from consultation_journal import render_audio_consultation_journal\n"
        f"render_audio_consultation_journal('test-user', 'synthetic-customer', '검증용 기업', {BUSINESS!r}, '검증담당자', {{}})\n",
        default_timeout=15,
    ).run()
    app.button(key=f"generate_consultation_journal_{BUSINESS}").click().run()
    assert not app.exception
    transcribe.assert_called_once()
    summarize.assert_called_once()
    upload.assert_called_once()
    text = card_text(app)
    assert ("상담일지 초안 생성 완료" in text) if stored else ("클라우드 보관은 완료하지 못했습니다" in text)
    assert "검증용 상담일지" == app.text_area(key=f"journal_summary_{BUSINESS}").value
