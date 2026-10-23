"""Offline company materials aggregation, access controls and UI coverage."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import articles_review
import cloud_db
import cloud_sync
import company_materials as materials
import consultation_audio_storage
import consultation_journal
import customer_asset_storage
import employee_status
import enterprise_documents
import stock_valuation


OWNER = "synthetic-owner"
BUSINESS = "-".join(("123", "45", "67890"))
OTHER_BUSINESS = "-".join(("987", "65", "43210"))


def row(identity, **extra):
    return {"record_id": identity, "owner_user_id": OWNER,
            "business_no": BUSINESS, "company_name": "동일한 합성 업체명",
            "saved_at": "2026-10-23T10:00:00", **extra}


@pytest.fixture
def sources(monkeypatch, tmp_path):
    state = {"documents": [], "financial": {}, "remote_financial": {},
             "registry": {}, "remote_registry": {}, "employees": [],
             "articles": {}, "journals": [], "audio": [], "stock": []}
    monkeypatch.setattr(materials, "get_user_dirs", lambda owner: {"base": tmp_path / owner})
    monkeypatch.setattr(enterprise_documents, "load_enterprise_document_context", lambda *a, **k: {"records": deepcopy(state["documents"])})
    monkeypatch.setattr(stock_valuation, "_load_financial_cache", lambda *_: {BUSINESS: deepcopy(state["financial"])})
    monkeypatch.setattr(cloud_sync, "load_financial_snapshot", lambda *_: deepcopy(state["remote_financial"]))
    monkeypatch.setattr(stock_valuation, "_load_registry_cache", lambda *a, **k: {})
    monkeypatch.setattr(stock_valuation, "_registry_snapshot_from_cache", lambda *_: deepcopy(state["registry"]))
    monkeypatch.setattr(cloud_sync, "load_registry_snapshot", lambda *a, **k: deepcopy(state["remote_registry"]))
    monkeypatch.setattr(stock_valuation, "_merge_registry_snapshot_versions", lambda a, b: b or a)
    monkeypatch.setattr(employee_status, "load_employee_versions", lambda *_: deepcopy(state["employees"]))
    monkeypatch.setattr(articles_review, "get_latest_articles_review", lambda *_: deepcopy(state["articles"]))
    monkeypatch.setattr(consultation_journal, "load_company_consultation_context", lambda *a, **k: deepcopy(state["journals"]))
    monkeypatch.setattr(consultation_audio_storage, "list_company_audio", lambda *_: deepcopy(state["audio"]))
    monkeypatch.setattr(stock_valuation, "_records_for_stock_company", lambda *_: deepcopy(state["stock"]))
    monkeypatch.setattr(cloud_db.CloudDatabase, "select", Mock(side_effect=AssertionError("unexpected live database access")))
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", Mock(side_effect=AssertionError("network is disabled in synthetic tests")))
    return state


def test_aggregates_all_seven_material_types_and_specialized_sources(sources):
    sources["documents"] = [row(f"document-{kind}", document_type=kind, filename=f"{kind}.pdf")
                            for kind in enterprise_documents.DOCUMENT_TYPES]
    sources["documents"][0]["analysis_versions"] = [{"revision": "previous", "uploaded_at": "2025-01-01", "extracted_fields": {"amount": 1}}]
    sources["financial"] = row("financial", _versions=[row("old-financial")])
    sources["registry"] = row("registry", 발행주식총수=100)
    sources["employees"] = [row("employee-current"), row("employee-previous")]
    sources["articles"] = row("articles", _versions=[row("old-articles")])
    sources["journals"] = [row("journal", journal_id="synthetic-journal")]
    sources["audio"] = [row("audio", audio_id="synthetic-audio")]
    sources["stock"] = [row("valuation", result={"per_share": 1000})]
    before = deepcopy(sources)
    items, failures = materials.collect_company_materials(OWNER, BUSINESS)
    assert not failures
    assert set(enterprise_documents.DOCUMENT_TYPES).issubset({item["type"] for item in items})
    assert {"financial", "stock_valuation"}.issubset({item["type"] for item in items})
    for label in ("크레탑 재무 분석", "등기 분석 스냅샷", "직원명부 분석", "정관 분석", "상담일지·녹음 분석", "상담 녹음 원본", "주가평가 계산"):
        assert any(item["label"] == label for item in items)
    assert any("이전 분석" in item["label"] for item in items)
    assert any("이전 버전" in item["label"] for item in items)
    assert sources == before


@pytest.mark.parametrize("bad", [
    {"owner_user_id": "other-owner"}, {"user_id": "other-owner"},
    {"business_no": OTHER_BUSINESS}, {"사업자등록번호": OTHER_BUSINESS},
    {"business_no_normalized": OTHER_BUSINESS},
])
def test_every_source_rejects_other_owner_company_even_with_same_name(sources, bad):
    invalid = row("invalid", **bad)
    sources.update(documents=[{**invalid, "document_type": "articles"}], financial=invalid,
                   registry=invalid, employees=[invalid], articles=invalid,
                   journals=[invalid], audio=[invalid], stock=[invalid])
    items, failures = materials.collect_company_materials(OWNER, BUSINESS, "동일한 합성 업체명")
    assert items == []
    assert failures == []


@pytest.mark.parametrize("owner,business", [("", BUSINESS), (OWNER, ""), (OWNER, "invalid")])
def test_empty_context_fails_closed_before_loading(sources, monkeypatch, owner, business):
    loader = Mock(side_effect=AssertionError("should not read any source"))
    monkeypatch.setattr(enterprise_documents, "load_enterprise_document_context", loader)
    items, warnings = materials.collect_company_materials(owner, business)
    assert not items and warnings
    loader.assert_not_called()


def test_source_failure_keeps_other_materials_and_reports_safe_warning(sources, monkeypatch):
    sources["stock"] = [row("retained")]
    monkeypatch.setattr(enterprise_documents, "load_enterprise_document_context", Mock(side_effect=ValueError("synthetic internal path")))
    items, warnings = materials.collect_company_materials(OWNER, BUSINESS)
    assert any(item["type"] == "stock_valuation" for item in items)
    assert warnings == ["첨부자료 목록"]
    assert "synthetic internal path" not in str(warnings)


def test_distinct_local_and_remote_financial_analyses_are_both_reviewable(sources):
    sources["financial"] = row("local-financial", financial_fields={"profit": 200}, saved_at="2026-10-23")
    sources["remote_financial"] = row("remote-financial", financial_fields={"profit": 100}, saved_at="2026-10-22")
    items, failures = materials.collect_company_materials(OWNER, BUSINESS)
    assert failures == []
    assert {item["record"]["record_id"] for item in items if item["type"] == "financial"} == {"local-financial", "remote-financial"}


def test_remote_document_restore_requests_all_supported_types(sources, monkeypatch):
    loader = Mock(return_value={"records": []})
    monkeypatch.setattr(enterprise_documents, "load_enterprise_document_context", loader)
    materials.collect_company_materials(OWNER, BUSINESS)
    assert set(loader.call_args.kwargs["include_remote_types"]) == set(enterprise_documents.DOCUMENT_TYPES)


def test_filename_checksum_and_owner_scoped_local_download(sources, tmp_path):
    path = tmp_path / OWNER / "uploads" / "original.pdf"
    path.parent.mkdir(parents=True)
    content = b"synthetic original"
    path.write_bytes(content)
    record = row("original", local_path=str(path), sha256=hashlib.sha256(content).hexdigest())
    assert materials.read_owned_material_file(OWNER, BUSINESS, record) == content
    assert path.read_bytes() == content


@pytest.mark.parametrize("bad", [{"owner_user_id": "other-owner"}, {"business_no": OTHER_BUSINESS}, {"사업자등록번호": OTHER_BUSINESS}])
def test_local_download_rejects_cross_owner_and_company(sources, tmp_path, bad):
    path = tmp_path / OWNER / "original.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic original")
    with pytest.raises(PermissionError):
        materials.read_owned_material_file(OWNER, BUSINESS, row("original", local_path=str(path), **bad))


def test_download_rejects_path_traversal_and_keeps_outside_file(sources, tmp_path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"synthetic outside")
    traversal = tmp_path / OWNER / ".." / "outside.pdf"
    with pytest.raises(PermissionError):
        materials.read_owned_material_file(OWNER, BUSINESS, row("original", local_path=str(traversal)))
    assert outside.read_bytes() == b"synthetic outside"


def test_tampered_original_is_not_returned_or_changed(sources, tmp_path):
    path = tmp_path / OWNER / "original.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic changed")
    with pytest.raises(ValueError):
        materials.read_owned_material_file(OWNER, BUSINESS, row("original", local_path=str(path), sha256=hashlib.sha256(b"synthetic original").hexdigest()))
    assert path.read_bytes() == b"synthetic changed"


def test_missing_local_path_is_not_substituted_with_other_record(sources):
    with pytest.raises(FileNotFoundError):
        materials.read_owned_material_file(OWNER, BUSINESS, row("no-original"))


@pytest.fixture
def cloud_assets(sources, monkeypatch):
    db = SimpleNamespace(select=Mock(return_value=[{"asset_id": "synthetic-asset", "owner_user_id": OWNER, "source_id": "synthetic-record"}]))
    download = Mock(return_value="https://example.invalid/private-synthetic-link")
    monkeypatch.setattr(cloud_db, "CloudDatabase", lambda: db)
    monkeypatch.setattr(cloud_db, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(customer_asset_storage, "create_customer_asset_download_url", download)
    return db, download


def test_private_cloud_original_requires_owner_and_source_then_short_signed_url(cloud_assets):
    db, download = cloud_assets
    value = materials.material_cloud_url(OWNER, BUSINESS, row("synthetic-record", document_type="corporate_registry"))
    assert value == "https://example.invalid/private-synthetic-link"
    assert db.select.call_args.kwargs["filters"] == {"owner_user_id": OWNER, "source_type": "enterprise_document:corporate_registry", "source_id": "synthetic-record"}
    download.assert_called_once_with("synthetic-asset", owner_user_id=OWNER, expires_in=60)


@pytest.mark.parametrize("response", [[], [{"asset_id": "x", "owner_user_id": "other-owner", "source_id": "synthetic-record"}], [{"asset_id": "x", "owner_user_id": OWNER, "source_id": "other-record"}]])
def test_cloud_asset_result_owner_and_source_id_are_revalidated(cloud_assets, response):
    db, download = cloud_assets
    db.select.return_value = response
    with pytest.raises(FileNotFoundError):
        materials.material_cloud_url(OWNER, BUSINESS, row("synthetic-record", document_type="articles"))
    download.assert_not_called()


def test_cross_company_cloud_request_never_queries_private_assets(cloud_assets):
    db, download = cloud_assets
    with pytest.raises(PermissionError):
        materials.material_cloud_url(OWNER, BUSINESS, row("synthetic-record", business_no=OTHER_BUSINESS, document_type="articles"))
    db.select.assert_not_called()
    download.assert_not_called()


def test_audio_signed_url_matches_owned_company_audio(sources, monkeypatch):
    sources["audio"] = [row("wrong", business_no=OTHER_BUSINESS, audio_id="synthetic-audio", storage_path="bad"),
                        row("valid", audio_id="synthetic-audio", storage_path="synthetic-storage")]
    signer = Mock(return_value="https://example.invalid/private-audio")
    monkeypatch.setattr(consultation_audio_storage, "create_signed_audio_url", signer)
    assert materials.material_cloud_url(OWNER, BUSINESS, row("journal", audio_id="synthetic-audio")) == "https://example.invalid/private-audio"
    signer.assert_called_once_with("synthetic-storage", 60, owner_user_id=OWNER, audio_id="synthetic-audio")


def test_sensitive_fields_are_removed_recursively():
    hidden = {name: "synthetic-private" for name in ("주민등록번호", "생년월일", "휴대전화", "api_key", "password", "secret", "local_path", "file_path", "storage_path", "owner_user_id", "user_id", "sha256", "source_text", "raw_text", "transcript")}
    value = {"safe": "보존 값", "nested": [hidden, {"count": 1}]}
    assert materials._safe_fields(value) == {"safe": "보존 값", "nested": [{}, {"count": 1}]}
    assert value["nested"][0] == hidden


def test_english_access_tokens_and_session_credentials_are_removed():
    value = {"safe": 1, "access_token": "synthetic-token", "refresh_token": "synthetic-token", "session_id": "synthetic-session", "authorization": "synthetic-authorization", "credential": "synthetic-credential"}
    assert materials._safe_fields(value) == {"safe": 1}


def test_financial_change_invalidates_material_revision(sources, tmp_path):
    base = tmp_path / OWNER
    base.mkdir(parents=True)
    before = materials._material_revision(OWNER)
    (base / "stock_financial_cache.json").write_text('{"synthetic":true}', encoding="utf-8")
    assert materials._material_revision(OWNER) != before


def file_item(identity, **extra):
    return {"type": extra.get("document_type", "articles"), "record": row(identity, **extra)}


def test_original_projection_hides_analysis_only_and_keeps_input_untouched(sources):
    values = [file_item("file", document_type="articles", filename="original.pdf"),
              file_item("calculation", result={"amount": 100}),
              file_item("note", analysis_summary="private analysis")]
    before = deepcopy(values)
    items = materials.project_original_files(OWNER, BUSINESS, values)
    assert len(items) == 1
    assert items[0]["filename"] == "original.pdf"
    assert items[0]["label"] == "정관"
    assert items[0]["format"] == "PDF"
    assert values == before


def test_original_projection_deduplicates_reanalyses_but_not_different_sha(sources):
    values = [file_item("current", document_type="articles", filename="same.pdf", sha256="sha-original", first_uploaded_at="2025-01-01"),
              file_item("old-analysis", document_type="articles", filename="same.pdf", sha256="sha-original", local_path="synthetic-original-path"),
              file_item("another-file", document_type="articles", filename="same.pdf", sha256="sha-different")]
    items = materials.project_original_files(OWNER, BUSINESS, values)
    assert len(items) == 2
    original = next(item for item in items if item["record"]["sha256"] == "sha-original")
    assert original["saved_at"] == "2025-01-01"
    assert original["record"]["local_path"] == "synthetic-original-path"


@pytest.mark.parametrize("bad", [{"owner_user_id": "other-owner"}, {"business_no": OTHER_BUSINESS}, {"사업자등록번호": OTHER_BUSINESS}])
def test_original_projection_rejects_wrong_owner_or_company(sources, bad):
    assert materials.project_original_files(OWNER, BUSINESS, [file_item("forbidden", filename="private.pdf", **bad)]) == []


@pytest.mark.parametrize("name_field,path_field", [("filename", "local_path"), ("source_filename", "file_path"), ("original_filename", "audio_path")])
def test_original_filename_and_download_support_legacy_fields(sources, tmp_path, name_field, path_field):
    path = tmp_path / OWNER / "legacy.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"synthetic legacy")
    record = row("legacy", **{name_field: "legacy.pdf", path_field: str(path)})
    item = materials.project_original_files(OWNER, BUSINESS, [{"record": record}])[0]
    assert item["filename"] == "legacy.pdf"
    assert materials.read_owned_material_file(OWNER, BUSINESS, record) == b"synthetic legacy"


def test_file_collector_loads_archives_not_unrelated_analysis_stores(sources, monkeypatch):
    sources["documents"] = [row("original", document_type="tax_adjustment", filename="tax.pdf",
                                analysis_versions=[{"analysis_summary": "not a file"}])]
    sources["audio"] = [row("audio", original_filename="meeting.m4a", audio_id="audio")]
    forbidden = Mock(side_effect=AssertionError("analysis-only stores must not load"))
    monkeypatch.setattr(stock_valuation, "_records_for_stock_company", forbidden)
    monkeypatch.setattr(stock_valuation, "_load_financial_cache", forbidden)
    monkeypatch.setattr(employee_status, "load_employee_versions", forbidden)
    monkeypatch.setattr(articles_review, "get_latest_articles_review", forbidden)
    monkeypatch.setattr(consultation_journal, "load_company_consultation_context", forbidden)
    items, failures = materials.collect_company_files(OWNER, BUSINESS)
    assert failures == []
    assert {item["filename"] for item in items} == {"tax.pdf", "meeting.m4a"}
    forbidden.assert_not_called()


def _file_app(monkeypatch, values):
    projected = materials.project_original_files(OWNER, BUSINESS, values)
    loader = Mock(return_value=(projected, []))
    monkeypatch.setattr(materials, "_cached_company_files", loader)
    monkeypatch.setattr(materials, "_material_revision", lambda _: ())
    app = AppTest.from_string(
        "from company_materials import render_company_materials\n"
        f"render_company_materials({OWNER!r}, {BUSINESS!r}, '합성 기업')\n"
    ).run()
    return app, projected, loader


def test_file_library_filter_only_shows_file_metadata_and_no_analysis(sources, monkeypatch):
    values = [file_item("articles", document_type="articles", filename="articles.pdf", analysis_summary="private analysis", local_path="synthetic-secret-path"),
              file_item("tax", document_type="tax_adjustment", filename="tax.pdf")]
    original = Mock(side_effect=AssertionError("must not read original without click"))
    monkeypatch.setattr(materials, "read_owned_material_file", original)
    app, projected, _ = _file_app(monkeypatch, values)
    assert not app.exception
    assert len(app.dataframe[0].value) == 2
    assert set(app.dataframe[0].value.columns) == {"종류", "파일명", "형식", "등록일", "원본 상태"}
    app.selectbox[0].select("정관").run()
    assert not app.exception
    assert len(app.dataframe[0].value) == 1
    app.selectbox[1].select(next(item["key"] for item in projected if item["type"] == "articles")).run()
    assert not app.exception
    assert not app.get("json")
    assert "private analysis" not in str(app.dataframe[0].value)
    assert "synthetic-secret-path" not in str(app.dataframe[0].value)
    original.assert_not_called()


def test_selected_file_is_prepared_lazily_and_download_survives_rerun(sources, monkeypatch):
    original = Mock(return_value=b"synthetic original")
    monkeypatch.setattr(materials, "read_owned_material_file", original)
    app, values, _ = _file_app(monkeypatch, [file_item("one", filename="one.pdf"), file_item("two", filename="two.pdf")])
    original.assert_not_called()
    app.selectbox[1].select(values[0]["key"]).run()
    original.assert_not_called()
    next(button for button in app.button if button.label == "원본 다운로드 준비").click().run()
    assert not app.exception
    original.assert_called_once()
    assert len(app.get("download_button")) == 1
    assert app.session_state["_materials_prepared_download"]["content"] == b"synthetic original"
    app.run()
    assert not app.exception
    assert len(app.get("download_button")) == 1
    original.assert_called_once()
    app.selectbox[1].select(values[1]["key"]).run()
    assert not app.get("download_button")
    original.assert_called_once()
    assert "_materials_prepared_download" not in app.session_state


def test_missing_original_is_not_shown_as_downloadable_and_explains_reupload(sources, monkeypatch):
    monkeypatch.setattr(materials, "read_owned_material_file", Mock(side_effect=FileNotFoundError))
    monkeypatch.setattr(materials, "material_cloud_url", Mock(side_effect=FileNotFoundError))
    app, values, _ = _file_app(monkeypatch, [file_item("missing", filename="missing.pdf")])
    assert app.dataframe[0].value.iloc[0]["원본 상태"] == "원본 확인 필요"
    app.selectbox[1].select(values[0]["key"]).run()
    next(button for button in app.button if button.label == "원본 다운로드 준비").click().run()
    assert not app.exception
    assert not app.get("download_button")
    assert any("다시 업로드" in warning.value for warning in app.warning)


def test_tampered_download_does_not_fall_back_to_cloud(sources, monkeypatch):
    monkeypatch.setattr(materials, "read_owned_material_file", Mock(side_effect=ValueError("synthetic mismatch")))
    cloud = Mock()
    monkeypatch.setattr(materials, "material_cloud_url", cloud)
    app, values, _ = _file_app(monkeypatch, [file_item("tampered", filename="original.pdf")])
    app.selectbox[1].select(values[0]["key"]).run()
    next(button for button in app.button if button.label == "원본 다운로드 준비").click().run()
    assert not app.exception
    assert app.error
    assert not app.get("download_button")
    cloud.assert_not_called()


def test_selected_source_change_discards_prepared_payload(sources, monkeypatch, tmp_path):
    path = tmp_path / OWNER / "original.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original")
    app, values, _ = _file_app(monkeypatch, [file_item("file", filename="original.pdf", local_path=str(path))])
    app.selectbox[1].select(values[0]["key"]).run()
    next(button for button in app.button if button.label == "원본 다운로드 준비").click().run()
    assert len(app.get("download_button")) == 1
    path.write_bytes(b"changed original")
    app.run()
    assert not app.exception
    assert not app.get("download_button")
    assert "_materials_prepared_download" not in app.session_state


def test_remote_signed_link_persists_only_until_short_expiry(sources, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(materials.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(materials, "read_owned_material_file", Mock(side_effect=FileNotFoundError))
    cloud = Mock(return_value="https://example.invalid/synthetic-private-download")
    monkeypatch.setattr(materials, "material_cloud_url", cloud)
    app, values, _ = _file_app(monkeypatch, [file_item("cloud", filename="cloud.pdf", asset_id="synthetic-asset")])
    app.selectbox[1].select(values[0]["key"]).run()
    next(button for button in app.button if button.label == "원본 다운로드 준비").click().run()
    assert not app.exception
    assert len(app.get("link_button")) == 1
    app.run()
    assert len(app.get("link_button")) == 1
    cloud.assert_called_once()
    now[0] += 51
    app.run()
    assert not app.get("link_button")
    assert "_materials_prepared_download" not in app.session_state


@pytest.mark.parametrize("field,value", [("business", OTHER_BUSINESS), ("owner", "other-owner")])
def test_download_state_is_cleared_when_company_context_changes(sources, monkeypatch, field, value):
    monkeypatch.setattr(materials, "read_owned_material_file", Mock(return_value=b"synthetic original"))
    app, values, _ = _file_app(monkeypatch, [file_item("one", filename="one.pdf")])
    app.selectbox[1].select(values[0]["key"]).run()
    next(button for button in app.button if button.label == "원본 다운로드 준비").click().run()
    assert "_materials_prepared_download" in app.session_state
    app.session_state["_materials_prepared_download"][field] = value
    app.run()
    assert not app.get("download_button")
    assert "_materials_prepared_download" not in app.session_state


def test_original_metadata_change_invalidates_download_without_reading_another_file(sources, monkeypatch):
    reader = Mock(return_value=b"synthetic original")
    monkeypatch.setattr(materials, "read_owned_material_file", reader)
    app, values, _ = _file_app(monkeypatch, [file_item("file", filename="original.pdf")])
    app.selectbox[1].select(values[0]["key"]).run()
    next(button for button in app.button if button.label == "원본 다운로드 준비").click().run()
    assert app.get("download_button")
    values[0]["record"]["asset_id"] = "new-private-asset"
    app.run()
    assert not app.exception
    assert not app.get("download_button")
    reader.assert_called_once()


def test_original_filename_only_records_never_claim_verified_download(sources):
    items = materials.project_original_files(OWNER, BUSINESS, [file_item("metadata", filename="metadata-only.pdf")])
    assert materials._original_status(OWNER, BUSINESS, items[0]) == "원본 확인 필요"


def test_file_listing_failure_keeps_other_originals_and_redacts_error_details(sources, monkeypatch):
    monkeypatch.setattr(enterprise_documents, "load_enterprise_document_context", Mock(side_effect=ValueError("synthetic private path")))
    sources["audio"] = [row("audio", original_filename="meeting.m4a", audio_id="audio")]
    items, failures = materials.collect_company_files(OWNER, BUSINESS)
    assert [item["filename"] for item in items] == ["meeting.m4a"]
    assert failures == ["업로드 원본 목록"]
    assert "synthetic private path" not in str(failures)
