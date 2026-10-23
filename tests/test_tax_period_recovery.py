"""Synthetic recovery of legacy fiscal metadata, without changing original files."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import customer_asset_storage
import customer_history
import enterprise_documents as documents
import stock_valuation as stock
from tax_adjustment_valuation import build_tax_adjustment_candidates


OWNER = "synthetic-owner"
BUSINESS = "-".join(("123", "45", "67890"))
OTHER_BUSINESS = "-".join(("987", "65", "43210"))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "get_user_dirs", lambda owner: {
        "base": tmp_path / owner, "uploads": tmp_path / owner / "uploads",
    })
    history = Mock(return_value={"storage_status": {"cloud_saved": True}})
    monkeypatch.setattr(documents, "save_customer_event", history)
    monkeypatch.setattr(customer_history, "get_customer_history", lambda *a, **k: [])
    monkeypatch.setattr(customer_asset_storage, "store_private_customer_asset", lambda *a, **k:
                        customer_asset_storage.CustomerAssetWriteResult(True, False, False, False))
    original = documents.register_enterprise_document_bytes(
        OWNER, BUSINESS, "합성 기업", "tax_adjustment", "synthetic-2023.pdf",
        b"synthetic original tax statement", note="preserved note",
        extracted_fields={"years": ["2024", "2023", "2022"], "financial_fields": {"당기순이익": 123}},
    )
    history.reset_mock()
    return original, history


def review(original, **overrides):
    args = {"user_id": OWNER, "business_no": BUSINESS, "record_id": original["record_id"],
            "year": 2023, "period_start": "2023-01-01", "period_end": "2023-12-31",
            "confirmed": True, "company_name": "합성 기업"}
    args.update(overrides)
    return documents.review_tax_document_period(**args)


def test_period_review_adds_new_analysis_and_preserves_original_and_legacy_values(store):
    original, history = store
    content = Path(original["local_path"]).read_bytes()
    saved = review(original)
    assert saved["record_id"] == original["record_id"]
    assert saved["sha256"] == original["sha256"]
    assert saved["first_uploaded_at"] == original["first_uploaded_at"]
    assert saved["note"] == "preserved note"
    assert Path(saved["local_path"]).read_bytes() == content
    assert saved["extracted_fields"]["financial_fields"] == {"당기순이익": 123}
    assert saved["analysis_versions"][0]["extracted_fields"] == original["extracted_fields"]
    assert len(saved["analysis_versions"]) == 2
    candidate = build_tax_adjustment_candidates([saved], "2026-10-23")[0]
    assert candidate["year"] == 2023 and candidate["period_verified"]
    assert all(value is None for value in candidate["values"].values())
    assert candidate["financial_fields"]["당기순이익"] is None
    assert saved["extracted_fields"]["fiscal_period_review"]["monetary_components_confirmed"] is False
    assert saved["period_review_storage_status"]["cloud_saved"] is True
    event = history.call_args.kwargs["extra_data"]
    assert event["자료ID"] == saved["record_id"]
    assert event["원본SHA256"] == original["sha256"]
    assert event["추출정보"]["valuation_periods"][0]["year"] == 2023


@pytest.mark.parametrize("overrides", [
    {"confirmed": False}, {"business_no": OTHER_BUSINESS}, {"user_id": "another-owner"},
    {"record_id": "unknown-source"}, {"year": 2024},
    {"period_start": "2024-01-01"}, {"period_end": "invalid"},
])
def test_unconfirmed_invalid_or_different_source_never_rewrites_original(store, overrides):
    original, history = store
    before = documents._metadata_path(OWNER).read_bytes()
    content = Path(original["local_path"]).read_bytes()
    with pytest.raises((ValueError, PermissionError)):
        review(original, **overrides)
    assert documents._metadata_path(OWNER).read_bytes() == before
    assert Path(original["local_path"]).read_bytes() == content
    history.assert_not_called()


def test_history_failure_reports_local_only_without_erasing_review_or_original(store):
    original, history = store
    history.return_value = {"storage_status": {"local_saved": True, "cloud_saved": False}}
    saved = review(original)
    assert saved["period_review_storage_status"]["cloud_saved"] is False
    local = documents.load_enterprise_document_context(OWNER, BUSINESS)["records"][0]
    assert local["extracted_fields"]["valuation_periods"][0]["year"] == 2023
    assert Path(original["local_path"]).is_file()


def test_remote_original_analysis_versions_survive_first_local_review(store, monkeypatch):
    original, _ = store
    # A source restored after redeployment carries older revisions in history.
    remote = deepcopy(original)
    remote["record_id"] = "remote-only-original"
    remote["analysis_versions"] = [
        {"revision": "old-a", "extracted_fields": {"tag": "a"}},
        {"revision": "old-b", "extracted_fields": {"tag": "b"}},
    ]
    monkeypatch.setattr(documents, "load_enterprise_document_context", lambda *a, **k: {"records": [remote]})
    saved = review(remote)
    versions = saved["analysis_versions"]
    assert {"old-a", "old-b"}.issubset({item["revision"] for item in versions})


def test_concurrent_normal_analysis_blocks_stale_manual_period_recovery(store, monkeypatch):
    original, history = store
    loader = documents.load_enterprise_document_context
    normal_period = {"year": 2023, "period_start": "2023-01-01", "period_end": "2023-12-31",
                     "values": {"income": 42}}
    def load_then_another_request_finishes(*args, **kwargs):
        previous = loader(*args, **kwargs)
        documents._save_record(OWNER, {**original, "extracted_fields": {"valuation_periods": [normal_period]}})
        return previous
    monkeypatch.setattr(documents, "load_enterprise_document_context", load_then_another_request_finishes)
    with pytest.raises(ValueError, match="다른 작업"):
        review(original)
    assert documents._load_records(OWNER)[0]["extracted_fields"]["valuation_periods"] == [normal_period]
    history.assert_not_called()
    assert Path(original["local_path"]).read_bytes() == b"synthetic original tax statement"


@pytest.mark.parametrize("reverse", [False, True])
def test_concurrent_remote_normal_period_outranks_newer_manual_event(monkeypatch, reverse):
    normal_period = {"year": 2023, "period_start": "2023-01-01", "period_end": "2023-12-31",
                     "values": {"income": 42}}
    manual_period = {"year": 2022, "period_start": "2022-01-01", "period_end": "2022-12-31",
                     "values": {}, "manual_period_review": True}
    def event(stamp, period):
        return {"owner_user_id": OWNER, "business_no": BUSINESS, "captured_at": stamp,
                "data": {"문서유형": "tax_adjustment", "자료ID": "same-original",
                         "파일명": "synthetic.pdf", "추출정보": {"valuation_periods": [period]}}}
    events = [event("2026-10-22T10:00:00", normal_period), event("2026-10-23T10:00:00", manual_period)]
    monkeypatch.setattr(documents, "_load_records", lambda owner: [])
    monkeypatch.setattr(customer_history, "get_customer_history", lambda *a, **k: events[::-1] if reverse else events)
    record = documents.load_enterprise_document_context(OWNER, BUSINESS, include_remote_tax=True)["records"][0]
    assert record["extracted_fields"]["valuation_periods"] == [normal_period]
    assert len(record["analysis_versions"]) == 2


def test_tax_picker_restores_upload_older_than_200_history_events(monkeypatch):
    monkeypatch.setattr(documents, "_load_records", lambda owner: [])
    older = {"owner_user_id": OWNER, "business_no": BUSINESS, "captured_at": "2025-01-01",
             "data": {"문서유형": "tax_adjustment", "자료ID": "old-tax", "파일명": "synthetic.pdf",
                      "추출정보": {"valuation_periods": [{"year": 2023, "values": {}}]}}}
    recent = [{"owner_user_id": OWNER, "business_no": BUSINESS, "captured_at": "2026-01-01",
               "data": {"히스토리유형": "상담", "이벤트ID": f"synthetic-{index}"}} for index in range(230)]
    history = Mock(side_effect=lambda *a, **k: recent + [older] if k.get("limit", 200) is None else recent[:200])
    monkeypatch.setattr(customer_history, "get_customer_history", history)
    context = documents.load_enterprise_document_context(OWNER, BUSINESS, include_remote_tax=True)
    assert [item["record_id"] for item in context["records"]] == ["old-tax"]
    assert [item["year"] for item in build_tax_adjustment_candidates(context["records"], "2026-10-23")] == [2023]
    history.assert_called_once_with(OWNER, BUSINESS, limit=None)


def test_recovery_ui_makes_2023_selectable_only_after_explicit_period_confirmation(monkeypatch):
    original = {"owner_user_id": OWNER, "business_no": BUSINESS, "document_type": "tax_adjustment",
                "record_id": "legacy-2023", "filename": "synthetic-2023.pdf", "uploaded_at": "2026-10-22",
                "extracted_fields": {"years": ["2023"], "financial_fields": {"당기순이익": 123}}}
    context = Mock(return_value={"records": [original]})
    monkeypatch.setattr(documents, "load_enterprise_document_context", context)
    def save(*args, **kwargs):
        from tax_adjustment_valuation import reviewed_tax_period_record
        assert kwargs["confirmed"] is True
        value = reviewed_tax_period_record(original, args[3], args[4], args[5])
        value["period_review_storage_status"] = {"cloud_saved": True}
        return value
    writer = Mock(side_effect=save)
    monkeypatch.setattr(documents, "review_tax_document_period", writer)
    app = AppTest.from_string(
        "import streamlit as st\nfrom stock_valuation import _render_stock_tax_adjustment\n"
        f"st.session_state.update(stock_business_no={BUSINESS!r},stock_company_name='합성 기업',"
        "stock_valuation_date='2026-10-23',stock_year_1='2025',stock_year_2='2024',stock_year_3='2023')\n"
        f"_render_stock_tax_adjustment({OWNER!r})"
    ).run()
    assert not app.exception
    assert not app.multiselect
    app.selectbox[0].select(0).run()
    assert not app.exception
    for label, value in (("확인한 사업연도(종료연도)", "2023"),
                         ("원본의 사업연도 시작일", "2023-01-01"),
                         ("원본의 사업연도 종료일", "2023-12-31")):
        next(item for item in app.text_input if item.label == label).set_value(value)
    app.checkbox[0].check()
    next(item for item in app.button if item.label == "확인한 사업연도 저장·목록에 추가").click().run()
    assert not app.exception
    assert app.multiselect[0].options == ["2023 사업연도"]
    writer.assert_called_once()
    assert "stock_net_income_3" not in app.session_state
