from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

import articles_review
import enterprise_documents as docs
import customer_history


BUSINESS = "-".join(("123", "45", "67890"))
CUSTOMER = pd.Series({"사업자등록번호": BUSINESS, "업체명": "검증 기업"})


def upload(name, content=b"synthetic content"):
    file = BytesIO(content)
    file.name, file.size = name, len(content)
    return file


@pytest.fixture
def storage_mocks(monkeypatch):
    extractor = Mock(return_value=("2025 사업연도 세무조정계산서\n각 사업연도 소득금액 100원", {"method": "text"}))
    register = Mock(return_value={"record_id": "synthetic-record"})
    monkeypatch.setattr(articles_review, "extract_articles_text", extractor)
    monkeypatch.setattr(docs, "register_enterprise_document_bytes", register)
    return extractor, register


def test_batch_continues_after_failure_and_deduplicates(storage_mocks):
    extractor, register = storage_mocks
    files = [upload("one.pdf", b"one"), upload("empty.pdf", b""), upload("two.pdf", b"two"), upload("same.pdf", b"one")]
    progress = []
    result = docs.process_enterprise_document_batch("owner", CUSTOMER, "tax_adjustment", files, on_progress=lambda *args: progress.append(args))
    assert [row["status"] for row in result] == ["saved", "failed", "saved", "duplicate"]
    assert register.call_count == 2
    assert extractor.call_count == 2
    assert progress[0][2] == "파일 확인"
    assert all("progress_callback" in call.kwargs for call in extractor.call_args_list)
    assert result[0]["storage"] == "local"


def test_wrong_company_is_not_registered(storage_mocks):
    extractor, register = storage_mocks
    other = "-".join(("999", "99", "99999"))
    extractor.return_value = (f"2025 사업연도 세무조정계산서\n사업자등록번호 {other}\n각 사업연도 소득금액 100원", {})
    result = docs.process_enterprise_document_batch("owner", CUSTOMER, "tax_adjustment", [upload("wrong.pdf")])
    assert result[0]["status"] == "failed"
    assert "선택한 기업과 문서" in result[0]["message"]
    register.assert_not_called()


def test_batch_count_limit_is_checked_before_reading(storage_mocks):
    with pytest.raises(docs.EnterpriseDocumentValidationError, match="1~20"):
        docs.process_enterprise_document_batch("owner", CUSTOMER, "tax_adjustment", [object()] * 21)


def test_ocr_progress_includes_page_and_file(storage_mocks):
    extractor, _ = storage_mocks
    def extract(name, content, progress_callback):
        progress_callback(1, 3, "페이지 확인")
        return "2025 사업연도 세무조정계산서\n각 사업연도 소득금액 100원", {}
    extractor.side_effect = extract
    events = []
    docs.process_enterprise_document_batch("owner", CUSTOMER, "tax_adjustment", [upload("one.pdf")], on_progress=lambda *args: events.append(args))
    assert any("1/3쪽" in event[3] for event in events)


def test_cloud_storage_failure_keeps_local_success_and_warning(storage_mocks, monkeypatch):
    import customer_asset_storage
    _, register = storage_mocks
    register.return_value = {"record_id": "synthetic-record", "local_path": "synthetic-local-file"}
    monkeypatch.setattr(customer_asset_storage, "store_private_customer_asset", lambda *a, **kw: SimpleNamespace(cloud_saved=False, metadata_saved=False, cloud_enabled=True))
    result = docs.process_enterprise_document_batch("owner", CUSTOMER, "tax_adjustment", [upload("one.pdf")])
    assert result[0]["status"] == "saved"
    assert result[0]["storage"] == "warning"
    assert "클라우드 원본 보관 미완료" in result[0]["message"]


@pytest.mark.parametrize("history_saved,expected", [(False, "warning"), (True, "cloud")])
def test_cloud_success_requires_original_and_analysis_history(storage_mocks, monkeypatch, history_saved, expected):
    import customer_asset_storage
    _, register = storage_mocks
    register.return_value = {
        "record_id": "synthetic-record", "local_path": "synthetic-local-file",
        "history_storage_status": {"cloud_saved": history_saved},
    }
    monkeypatch.setattr(customer_asset_storage, "store_private_customer_asset", lambda *a, **kw: SimpleNamespace(cloud_saved=True, metadata_saved=True, cloud_enabled=True))
    result = docs.process_enterprise_document_batch("owner", CUSTOMER, "tax_adjustment", [upload("one.pdf")])
    assert result[0]["storage"] == expected


def test_reanalysis_preserves_original_and_creates_versioned_history(tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "get_user_dirs", lambda _: {"base": tmp_path, "uploads": tmp_path / "uploads"})
    history = Mock(return_value={"storage_status": {"cloud_saved": True}})
    monkeypatch.setattr(docs, "save_customer_event", history)
    original = b"synthetic tax statement"
    first = docs.register_enterprise_document_bytes("owner", BUSINESS, "검증 기업", "tax_adjustment", "one.pdf", original, extracted_fields={"revision": 1})
    same = docs.register_enterprise_document_bytes("owner", BUSINESS, "검증 기업", "tax_adjustment", "two.pdf", original, extracted_fields={"revision": 1})
    newer = docs.register_enterprise_document_bytes("owner", BUSINESS, "검증 기업", "tax_adjustment", "two.pdf", original, extracted_fields={"revision": 2})
    assert first["record_id"] == same["record_id"] == newer["record_id"]
    assert first["local_path"] == same["local_path"] == newer["local_path"]
    assert len(list((tmp_path / "uploads").rglob("*.pdf"))) == 1
    event_ids = [call.kwargs["event_id"] for call in history.call_args_list]
    assert event_ids[0] == event_ids[1] != event_ids[2]
    assert history.call_args.kwargs["return_status"] is True
    assert newer["history_storage_status"]["cloud_saved"] is True


def test_multi_upload_ui_retains_results_and_does_not_repeat_save(storage_mocks, monkeypatch):
    _, register = storage_mocks
    captured = {}
    def uploader(*args, **kwargs):
        captured.update(kwargs)
        return [upload("one.pdf", b"one"), upload("two.pdf", b"two")]
    monkeypatch.setattr(docs.st, "file_uploader", uploader)
    app = AppTest.from_string(
        "import pandas as pd\nfrom enterprise_documents import _render_generic_upload\n"
        f"_render_generic_upload('owner', pd.Series({{'사업자등록번호':{BUSINESS!r},'업체명':'검증 기업'}}),'tax_adjustment')"
    ).run()
    app.button(key=f"enterprise_doc_analyze_tax_adjustment_{BUSINESS}").click().run()
    assert not app.exception
    assert captured["accept_multiple_files"] is True
    assert captured["max_upload_size"] == 50
    assert register.call_count == 2
    assert len(app.dataframe[0].value) == 2
    assert any("등록 2개" in str(item.value) for item in app.markdown)
    app.run()
    assert not app.exception
    assert register.call_count == 2
    assert len(app.dataframe[0].value) == 2


def test_remote_tax_metadata_is_owner_company_scoped(monkeypatch):
    monkeypatch.setattr(docs, "_load_records", lambda _: [])
    source = {"business_no": BUSINESS, "captured_at": "2026-10-22", "data": {"문서유형": "tax_adjustment", "자료ID": "record", "파일명": "synthetic.pdf", "추출정보": {"valuation_periods": []}}}
    loader = Mock(return_value=[source, {**source, "business_no": ""}])
    monkeypatch.setattr(customer_history, "get_customer_history", loader)
    assert docs.load_enterprise_document_context("owner", BUSINESS)["records"] == []
    loader.assert_not_called()
    records = docs.load_enterprise_document_context("owner", BUSINESS, include_remote_tax=True)["records"]
    assert len(records) == 1
    loader.assert_called_once_with("owner", BUSINESS, limit=None)


def test_new_remote_analysis_replaces_stale_local_metadata_not_original(monkeypatch):
    local = {"record_id": "record", "business_no": BUSINESS, "document_type": "tax_adjustment", "uploaded_at": "2026-10-21T10:00:00", "extracted_fields": {"revision": 1}, "local_path": "retained-original.pdf"}
    monkeypatch.setattr(docs, "_load_records", lambda _: [local])
    remote = {"business_no": BUSINESS, "captured_at": "2026-10-22 10:00:00", "data": {"문서유형": "tax_adjustment", "자료ID": "record", "파일명": "synthetic.pdf", "추출정보": {"revision": 2}}}
    older = {**remote, "captured_at": "2026-10-20 10:00:00", "data": {**remote["data"], "추출정보": {"revision": 0}}}
    monkeypatch.setattr(customer_history, "get_customer_history", lambda *_, **kwargs: [older, remote])
    records = docs.load_enterprise_document_context("owner", BUSINESS, include_remote_tax=True)["records"]
    assert len(records) == 1
    assert records[0]["extracted_fields"] == {"revision": 2}
    assert records[0]["local_path"] == "retained-original.pdf"
    assert local["extracted_fields"] == {"revision": 1}
