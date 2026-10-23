from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import customer_history
import customer_asset_storage
import enterprise_documents as documents


BUSINESS = "-".join(("123", "45", "67890"))
OTHER_BUSINESS = "-".join(("987", "65", "43210"))


@pytest.fixture
def store(tmp_path, monkeypatch):
    def directories(owner):
        base = tmp_path / owner
        return {"base": base, "uploads": base / "uploads"}
    monkeypatch.setattr(documents, "get_user_dirs", directories)
    monkeypatch.setattr(documents, "save_customer_event", Mock(return_value={"storage_status": {"cloud_saved": True}}))
    monkeypatch.setattr(customer_history, "get_customer_history", lambda *_, **kwargs: [])
    monkeypatch.setattr(customer_asset_storage, "store_private_customer_asset", lambda *a, **k: customer_asset_storage.CustomerAssetWriteResult(True, False, False, False))
    return directories


def register(*, owner="owner", business=BUSINESS, kind="articles", content=b"synthetic document", **kwargs):
    return documents.register_enterprise_document_bytes(
        owner, business, "검증 기업", kind, "synthetic.pdf", content, **kwargs,
    )


def test_reanalysis_preserves_missing_fields_notes_and_all_previous_versions(store):
    first = register(note="기존 검토 메모", analysis_summary="최초 분석", extracted_fields={"shares": 100, "capital": {"face_value": 500, "paid": 50000}})
    second = register(extracted_fields={"shares": 120, "capital": {"face_value": ""}})
    assert second["note"] == "기존 검토 메모"
    assert second["analysis_summary"] == "최초 분석"
    assert second["extracted_fields"] == {"shares": 120, "capital": {"face_value": 500, "paid": 50000}}
    assert len(second["analysis_versions"]) == 2
    assert second["analysis_versions"][0]["extracted_fields"]["shares"] == 100
    assert first["first_uploaded_at"] == second["first_uploaded_at"]
    assert second["local_path"] == first["local_path"]
    assert Path(first["local_path"]).read_bytes() == b"synthetic document"


def test_same_original_in_another_company_or_user_has_independent_metadata(store):
    first = register(extracted_fields={"tag": "first"})
    other_company = register(business=OTHER_BUSINESS, extracted_fields={"tag": "second"})
    other_owner = register(owner="second-owner", extracted_fields={"tag": "third"})
    assert first["record_id"] != other_company["record_id"]
    assert first["local_path"] != other_owner["local_path"]
    for owner, business, tag in (("owner", BUSINESS, "first"), ("owner", OTHER_BUSINESS, "second"), ("second-owner", BUSINESS, "third")):
        context = documents.load_enterprise_document_context(owner, business)
        assert len(context["records"]) == 1
        assert context["records"][0]["extracted_fields"]["tag"] == tag


def test_concurrent_distinct_registration_does_not_drop_records(store):
    with ThreadPoolExecutor(max_workers=8) as executor:
        records = list(executor.map(lambda n: register(content=f"synthetic-{n}".encode()), range(20)))
    loaded = documents.load_enterprise_document_context("owner", BUSINESS)["records"]
    assert len(loaded) == len(records) == 20
    assert len({item["record_id"] for item in loaded}) == 20


def test_concurrent_same_original_preserves_every_analysis(store):
    with ThreadPoolExecutor(max_workers=6) as executor:
        records = list(executor.map(lambda n: register(extracted_fields={"revision": n}), range(12)))
    loaded = documents.load_enterprise_document_context("owner", BUSINESS)["records"]
    assert len(loaded) == 1
    assert {item["extracted_fields"]["revision"] for item in loaded[0]["analysis_versions"]} == set(range(12))
    assert len({item["local_path"] for item in records}) == 1


@pytest.mark.parametrize("raw", ["{broken", "{}", '[{"record_id":"x","record_id":"y"}]', '[{"x":NaN}]', '[1]'])
def test_corrupt_metadata_remains_byte_identical(store, raw):
    path = store("owner")["base"] / "enterprise_documents.json"
    path.parent.mkdir(parents=True)
    path.write_text(raw, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(documents.EnterpriseDocumentCorruptionError):
        register()
    assert path.read_bytes() == before
    assert not list(store("owner")["uploads"].rglob("*.pdf"))


@pytest.mark.parametrize("name,value", [("analysis_versions", [None]), ("original_files", ["invalid"]), ("extracted_fields", [])])
def test_invalid_existing_substructure_blocks_overwrite(store, name, value):
    first = register()
    path = store("owner")["base"] / "enterprise_documents.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0][name] = value
    path.write_text(json.dumps(rows), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(documents.EnterpriseDocumentCorruptionError):
        register()
    assert path.read_bytes() == before
    assert Path(first["local_path"]).is_file()


def test_cross_company_extracted_identity_is_rejected(store):
    with pytest.raises(documents.EnterpriseDocumentCorruptionError):
        register(extracted_fields={"사업자등록번호": OTHER_BUSINESS})
    assert not (store("owner")["base"] / "enterprise_documents.json").exists()


def test_tampered_original_is_preserved_and_verified_new_copy_is_saved(store):
    first = register()
    original = Path(first["local_path"])
    original.write_bytes(b"changed synthetic content")
    second = register()
    assert second["local_path"] != first["local_path"]
    assert original.read_bytes() == b"changed synthetic content"
    assert hashlib.sha256(Path(second["local_path"]).read_bytes()).hexdigest() == second["sha256"]
    assert {item["local_path"] for item in second["original_files"]} == {first["local_path"], second["local_path"]}


def test_existing_source_outside_owner_root_is_not_imported(store, tmp_path):
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"synthetic")
    with pytest.raises(PermissionError):
        documents.register_existing_enterprise_document(outside, user_id="owner", business_no=BUSINESS, company_name="검증 기업", document_type="articles")
    assert not (store("owner")["base"] / "enterprise_documents.json").exists()


def test_remote_restores_all_requested_document_types_with_previous_analyses(store, monkeypatch):
    records = [{"business_no": BUSINESS, "owner_user_id": "owner", "captured_at": f"2026-10-23T10:00:0{index}",
                "data": {"문서유형": kind, "자료ID": f"synthetic-{kind}", "추출정보": {"revision": 1}}}
               for index, kind in enumerate(documents.DOCUMENT_TYPES)]
    latest = deepcopy(records[0])
    latest["captured_at"] = "2026-10-23T11:00:00"
    latest["data"]["추출정보"] = {"revision": 2}
    loader = Mock(return_value=records + [latest])
    monkeypatch.setattr(customer_history, "get_customer_history", loader)
    context = documents.load_enterprise_document_context("owner", BUSINESS, include_remote_types=tuple(documents.DOCUMENT_TYPES))
    assert set(context["latest_by_type"]) == set(documents.DOCUMENT_TYPES)
    loader.assert_called_once_with("owner", BUSINESS, limit=None)
    item = context["latest_by_type"][records[0]["data"]["문서유형"]]
    assert item["extracted_fields"]["revision"] == 2
    assert len(item["analysis_versions"]) == 2


@pytest.mark.parametrize("mutation", ["business", "owner", "user", "embedded_business"])
def test_remote_other_company_or_owner_is_not_exposed(store, monkeypatch, mutation):
    event = {"business_no": BUSINESS, "owner_user_id": "owner", "captured_at": "2026-10-23T10:00:00",
             "data": {"문서유형": "articles", "자료ID": "synthetic", "추출정보": {}}}
    if mutation == "business":
        event["business_no"] = OTHER_BUSINESS
    elif mutation == "owner":
        event["owner_user_id"] = "another-owner"
    elif mutation == "user":
        event["user_id"] = "another-owner"
    else:
        event["data"]["추출정보"]["사업자등록번호"] = OTHER_BUSINESS
    monkeypatch.setattr(customer_history, "get_customer_history", lambda *_, **kwargs: [event])
    assert documents.load_enterprise_document_context("owner", BUSINESS, include_remote_types=("articles",))["records"] == []


def test_new_remote_partial_analysis_keeps_local_fields_original_and_asset(store, monkeypatch):
    saved = register(note="보존 메모", extracted_fields={"shares": 100, "face_value": 500})
    remote = {"business_no": BUSINESS, "owner_user_id": "owner", "captured_at": "2099-01-01T10:00:00",
              "data": {"문서유형": "articles", "자료ID": saved["record_id"], "추출정보": {"shares": 120}}}
    monkeypatch.setattr(customer_history, "get_customer_history", lambda *_, **kwargs: [remote])
    context = documents.load_enterprise_document_context("owner", BUSINESS, include_remote_types=("articles",))
    record = context["records"][0]
    assert record["extracted_fields"] == {"shares": 120, "face_value": 500}
    assert record["note"] == "보존 메모"
    assert record["local_path"] == saved["local_path"]
    assert len(record["analysis_versions"]) == 2
    assert documents.load_enterprise_document_context("owner", BUSINESS)["records"][0]["extracted_fields"]["shares"] == 100


def test_ordinary_navigation_does_not_query_remote_history(store, monkeypatch):
    loader = Mock(side_effect=AssertionError("remote access must be explicit"))
    monkeypatch.setattr(customer_history, "get_customer_history", loader)
    assert documents.load_enterprise_document_context("owner", BUSINESS)["records"] == []
    assert documents.load_enterprise_document_context("", BUSINESS)["records"] == []
    loader.assert_not_called()


@pytest.mark.parametrize("kind", ["corporate_registry", "articles", "employee_roster", "consultation_audio", "cretop_report", "rnd_certificate", "tax_adjustment"])
def test_all_document_types_use_common_private_original_storage(store, monkeypatch, kind):
    storage = Mock(return_value=customer_asset_storage.CustomerAssetWriteResult(True, True, True, True, asset_id="synthetic-asset"))
    monkeypatch.setattr(customer_asset_storage, "store_private_customer_asset", storage)
    saved = register(kind=kind)
    assert saved["asset_id"] == "synthetic-asset"
    assert saved["asset_storage_status"]["cloud_saved"] is True
    assert storage.call_args.kwargs["source_type"] == f"enterprise_document:{kind}"
    assert storage.call_args.kwargs["source_id"] == saved["record_id"]
    loaded = documents.load_enterprise_document_context("owner", BUSINESS)["records"][0]
    assert loaded["asset_id"] == "synthetic-asset"
    event = documents.save_customer_event.call_args.kwargs["extra_data"]
    assert event["클라우드원본ID"] == "synthetic-asset"


def test_private_original_failure_retains_local_metadata_and_history(store, monkeypatch):
    monkeypatch.setattr(customer_asset_storage, "store_private_customer_asset", Mock(side_effect=RuntimeError("synthetic failure")))
    saved = register(kind="corporate_registry")
    assert Path(saved["local_path"]).read_bytes() == b"synthetic document"
    assert saved["asset_storage_status"]["cloud_saved"] is False
    assert saved["asset_storage_status"]["degraded"] is True
    assert "synthetic failure" not in json.dumps(saved)
    assert documents.save_customer_event.call_count == 1
    assert documents.load_enterprise_document_context("owner", BUSINESS)["records"]
