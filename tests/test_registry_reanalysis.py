from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

import registry_reanalysis as subject


OWNER = "synthetic-owner"
BUSINESS = "-".join(("123", "45", "67890"))
OTHER_BUSINESS = "-".join(("987", "65", "43210"))
CORPORATE = "-".join(("110111", "1234567"))
OTHER_CORPORATE = "-".join(("220222", "7654321"))
CONTENT = b"%PDF-1.4 synthetic registered original"
CHECKSUM = hashlib.sha256(CONTENT).hexdigest()
RECORD_ID = hashlib.sha256(f"{BUSINESS}|corporate_registry|{CHECKSUM}".encode()).hexdigest()[:32]


def original_record():
    return {
        "record_id": RECORD_ID, "owner_user_id": OWNER, "business_no": BUSINESS,
        "document_type": "corporate_registry", "filename": "합성_등록등기.pdf",
        "sha256": CHECKSUM, "size_bytes": len(CONTENT), "note": "기존 검토 메모",
        "extracted_fields": {"발행주식총수": 1},
    }


@pytest.fixture
def context(monkeypatch):
    import enterprise_documents
    import registry_runner
    import stock_valuation as stock

    customer = pd.Series({"업체명": "합성검증법인", "사업자등록번호": BUSINESS,
                          "법인등록번호": CORPORATE, "_customer_id": "synthetic-customer"})
    records = [original_record()]
    monkeypatch.setattr(subject, "require_owner_context", lambda owner: owner)
    customers = Mock(return_value=pd.DataFrame([customer.to_dict()]))
    monkeypatch.setattr(stock, "_read_customers", customers)
    loader = Mock(side_effect=lambda *args, **kwargs: {"records": deepcopy(records)})
    monkeypatch.setattr(enterprise_documents, "load_enterprise_document_context", loader)
    reader = Mock(side_effect=lambda owner, business, record: subject._validate_original(CONTENT, record, business))
    monkeypatch.setattr(subject, "_read_registered_original", reader)
    parsed = {"법인명": "합성검증법인", "법인등록번호": CORPORATE,
              "발행주식총수": 2400, "자본금": 240000, "1주당액면가액": 100,
              "registry_parser_version": "synthetic-current"}
    worker = Mock(return_value=(parsed, ""))
    monkeypatch.setattr(registry_runner, "run_registry_worker", worker)
    saved = []

    def save(owner, business, data, *, storage_result):
        saved.append(deepcopy(data))
        storage_result.update(cloud_saved=True, registry_data={**deepcopy(data), "saved_at": "synthetic-new-version"})
        return business

    saver = Mock(side_effect=save)
    monkeypatch.setattr(stock, "_save_registry_cache", saver)
    registered = Mock(return_value={
        "record_id": RECORD_ID,
        "asset_storage_status": {"cloud_saved": True, "metadata_saved": True},
        "history_storage_status": {"cloud_saved": True},
    })
    monkeypatch.setattr(enterprise_documents, "register_enterprise_document_bytes", registered)
    apply = Mock(side_effect=AssertionError("source reanalysis must never apply valuation inputs"))
    monkeypatch.setattr(stock, "_apply_registry_data", apply)
    state = {"stock_current_shares": "1", "stock_net_income_1": "777",
             "stock_last_result": {"total_equity_value": 987}, "stock_business_no": BUSINESS}
    monkeypatch.setattr(stock, "st", SimpleNamespace(session_state=state))
    return SimpleNamespace(customer=customer, records=records, customers=customers, loader=loader,
                           reader=reader, worker=worker, parsed=parsed, saver=saver, saved=saved,
                           registered=registered, state=state, apply=apply)


def test_reanalysis_saves_same_original_new_analysis_without_changing_valuation(context):
    before = deepcopy(context.state)
    result = subject.reanalyze_registered_registry(OWNER, BUSINESS, RECORD_ID, context.customer)
    assert result["registry_data"]["발행주식총수"] == 2400
    assert result["cloud_saved"] and result["original_cloud_saved"] and result["history_cloud_saved"]
    assert result["document_registered"] and result["record_id"] == RECORD_ID
    assert result["source_sha256"] == CHECKSUM and not result["warnings"]
    assert context.state == before
    context.apply.assert_not_called()
    context.customers.assert_called_once_with(OWNER)
    assert context.loader.call_args.kwargs["include_remote_types"] == ("corporate_registry",)
    call = context.registered.call_args
    assert call.args[:4] == (OWNER, BUSINESS, "합성검증법인", "corporate_registry")
    assert call.args[5] == CONTENT and call.kwargs["note"] == "기존 검토 메모"
    assert call.kwargs["customer_id"] == "synthetic-customer"
    assert context.saved[0]["source_record_id"] == RECORD_ID
    temporary = context.worker.call_args.args[0]
    assert isinstance(temporary, Path) and not temporary.exists()
    assert context.worker.call_args.kwargs["timeout"] == 90


def test_selected_business_does_not_fall_back_to_namesake(context):
    row = context.customer.copy()
    row["사업자등록번호"] = OTHER_BUSINESS
    with pytest.raises(PermissionError):
        subject.reanalyze_registered_registry(OWNER, BUSINESS, RECORD_ID, row)
    context.customers.assert_not_called()
    context.saver.assert_not_called()


@pytest.mark.parametrize("mutation", ["unowned_customer", "duplicate_customer", "changed_corporation"])
def test_authoritative_owner_company_must_resolve_exactly(context, mutation):
    if mutation == "unowned_customer":
        context.customers.return_value = pd.DataFrame()
    elif mutation == "duplicate_customer":
        context.customers.return_value = pd.DataFrame([context.customer.to_dict()] * 2)
    else:
        row = context.customer.to_dict()
        row["법인등록번호"] = OTHER_CORPORATE
        context.customers.return_value = pd.DataFrame([row])
    with pytest.raises(PermissionError):
        subject.reanalyze_registered_registry(OWNER, BUSINESS, RECORD_ID, context.customer)
    context.loader.assert_not_called()
    context.saver.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("owner_user_id", "other-owner"), ("user_id", "other-owner"),
    ("business_no", OTHER_BUSINESS), ("document_type", "tax_adjustment"),
    ("source_type", "enterprise_document:tax_adjustment"),
])
def test_foreign_or_wrong_source_metadata_rejected_before_file_access(context, field, value):
    context.records[0][field] = value
    with pytest.raises(PermissionError):
        subject.reanalyze_registered_registry(OWNER, BUSINESS, RECORD_ID, context.customer)
    context.reader.assert_not_called()
    context.worker.assert_not_called()
    context.saver.assert_not_called()


@pytest.mark.parametrize("records", [[], [original_record(), original_record()]])
def test_missing_or_duplicate_source_record_rejected(context, records):
    context.records[:] = records
    with pytest.raises(PermissionError):
        subject.reanalyze_registered_registry(OWNER, BUSINESS, RECORD_ID, context.customer)
    context.reader.assert_not_called()
    context.saver.assert_not_called()


def test_parsed_different_corporation_does_not_save(context):
    context.worker.return_value = ({**context.parsed, "법인등록번호": OTHER_CORPORATE}, "")
    with pytest.raises(ValueError):
        subject.reanalyze_registered_registry(OWNER, BUSINESS, RECORD_ID, context.customer)
    context.saver.assert_not_called()
    context.registered.assert_not_called()


def test_worker_error_is_not_raw_document_or_provider_output(context):
    context.worker.return_value = ({}, "synthetic private path or document content")
    with pytest.raises(ValueError) as error:
        subject.reanalyze_registered_registry(OWNER, BUSINESS, RECORD_ID, context.customer)
    assert "synthetic private" not in str(error.value)
    context.saver.assert_not_called()


def test_partial_cloud_or_archive_failure_reports_truthfully(context):
    context.registered.side_effect = RuntimeError("synthetic provider private details")
    result = subject.reanalyze_registered_registry(OWNER, BUSINESS, RECORD_ID, context.customer)
    assert result["cloud_saved"] and not result["document_registered"]
    assert not result["original_cloud_saved"] and not result["history_cloud_saved"]
    assert result["registry_data"]["발행주식총수"] == 2400
    assert result["warnings"] and "private details" not in str(result)


@pytest.mark.parametrize("field", ["original", "history", "metadata"])
def test_archive_partial_success_never_claims_full_completion(context, field):
    record = deepcopy(context.registered.return_value)
    if field == "history":
        record["history_storage_status"]["cloud_saved"] = False
    else:
        record["asset_storage_status"]["cloud_saved" if field == "original" else "metadata_saved"] = False
    context.registered.return_value = record
    result = subject.reanalyze_registered_registry(OWNER, BUSINESS, RECORD_ID, context.customer)
    assert result["document_registered"] and result["warnings"]
    assert not (result["original_cloud_saved"] and result["history_cloud_saved"])


@pytest.mark.parametrize("mutation", ["sha", "size", "record", "not_pdf"])
def test_original_identity_and_integrity_are_checked_before_worker(context, mutation):
    if mutation == "sha":
        context.records[0]["sha256"] = "0" * 64
    elif mutation == "size":
        context.records[0]["size_bytes"] = len(CONTENT) + 1
    elif mutation == "record":
        context.records[0]["record_id"] = "arbitrary-unbound-source"
    else:
        context.reader.side_effect = lambda owner, business, record: subject._validate_original(b"not a pdf", record, business)
    requested = context.records[0]["record_id"]
    with pytest.raises(ValueError):
        subject.reanalyze_registered_registry(OWNER, BUSINESS, requested, context.customer)
    context.worker.assert_not_called()
    context.saver.assert_not_called()


@pytest.fixture
def reader(monkeypatch, tmp_path):
    import cloud_db
    import company_materials
    import utils

    monkeypatch.setattr(utils, "get_user_dirs", lambda owner: {"base": tmp_path})
    local = Mock(side_effect=FileNotFoundError("no local copy"))
    monkeypatch.setattr(company_materials, "read_owned_material_file", local)
    cloud = Mock(return_value="https://synthetic.supabase.co/storage/v1/object/sign/private/source.pdf?token=synthetic")
    monkeypatch.setattr(company_materials, "material_cloud_url", cloud)
    monkeypatch.setattr(cloud_db, "CloudDatabase", lambda: SimpleNamespace(config=SimpleNamespace(url="https://synthetic.supabase.co")))
    response = SimpleNamespace(status_code=200, headers={"Content-Length": str(len(CONTENT))},
                               iter_content=Mock(return_value=iter([CONTENT[:5], b"", CONTENT[5:]])), close=Mock())
    get = Mock(return_value=response)
    monkeypatch.setattr(subject.requests, "get", get)
    return SimpleNamespace(local=local, cloud=cloud, get=get, response=response, root=tmp_path)


def test_private_original_stream_is_bounded_and_verified(reader):
    assert subject._read_registered_original(OWNER, BUSINESS, original_record()) == CONTENT
    assert reader.get.call_args.kwargs == {"timeout": (10, 45), "stream": True, "allow_redirects": False}
    reader.response.close.assert_called_once()


@pytest.mark.parametrize("url", [
    "http://synthetic.supabase.co/storage/v1/object/sign/a", "https://other.invalid/storage/v1/object/sign/a",
    "https://synthetic.supabase.co:444/storage/v1/object/sign/a", "https://secret" + "@" + "synthetic.supabase.co/storage/v1/object/sign/a",
    "https://synthetic.supabase.co/rest/v1/private",
])
def test_private_link_rejects_unexpected_origin_or_route_before_request(reader, url):
    reader.cloud.return_value = url
    with pytest.raises(PermissionError):
        subject._read_registered_original(OWNER, BUSINESS, original_record())
    reader.get.assert_not_called()


def test_cloud_redirect_is_rejected_and_response_closed(reader):
    reader.response.status_code = 302
    with pytest.raises(RuntimeError):
        subject._read_registered_original(OWNER, BUSINESS, original_record())
    reader.response.close.assert_called_once()


@pytest.mark.parametrize("declared", [True, False])
def test_cloud_oversize_rejected_with_or_without_content_length(reader, monkeypatch, declared):
    monkeypatch.setattr(subject, "MAX_REGISTRY_BYTES", 10)
    reader.response.headers = {"Content-Length": "100"} if declared else {}
    with pytest.raises(ValueError):
        subject._read_registered_original(OWNER, BUSINESS, original_record())
    reader.response.close.assert_called_once()


def test_existing_local_bad_checksum_never_falls_back_to_cloud(reader):
    reader.local.side_effect = ValueError("local integrity rejected")
    with pytest.raises(ValueError):
        subject._read_registered_original(OWNER, BUSINESS, original_record())
    reader.cloud.assert_not_called()
    reader.get.assert_not_called()


def test_verified_local_original_avoids_all_network(reader):
    reader.local.side_effect = None
    reader.local.return_value = CONTENT
    assert subject._read_registered_original(OWNER, BUSINESS, original_record()) == CONTENT
    reader.cloud.assert_not_called()
    reader.get.assert_not_called()


def test_owner_session_guard_precedes_all_access(context, monkeypatch):
    def deny(owner):
        raise PermissionError("synthetic cross-user session")
    monkeypatch.setattr(subject, "require_owner_context", deny)
    with pytest.raises(PermissionError):
        subject.reanalyze_registered_registry("other-owner", BUSINESS, RECORD_ID, context.customer)
    context.customers.assert_not_called()
