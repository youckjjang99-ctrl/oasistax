from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

import consultation_journal as journals
import consultation_audio_storage as audio
from crm_file_store import CrmStorageError


BUSINESS = "-".join(("111", "11", "11111"))
OTHER_BUSINESS = "-".join(("222", "22", "22222"))
OWNER = "synthetic-owner"


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(journals, "get_user_dirs", lambda owner: {"base": tmp_path})
    monkeypatch.setattr(journals, "cloud_is_configured", lambda: False)
    monkeypatch.setattr(journals, "require_owner_context", lambda owner, **kwargs: owner)
    return tmp_path / "consultation_journals.json"


def record(identifier="journal-a", business=BUSINESS, **extra):
    return {"journal_id": identifier, "business_no": business,
            "company_name": "합성 동일상호", "saved_at": "2026-10-23 10:00:00",
            "summary": "합성 상담내용", **extra}


def write_rows(path, rows):
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")


def test_company_context_has_only_one_effective_definition():
    source = Path(journals.__file__).read_text(encoding="utf-8")
    assert sum(isinstance(node, ast.FunctionDef) and node.name == "load_company_consultation_context"
               for node in ast.parse(source).body) == 1


def test_company_context_never_joins_same_name_with_conflicting_identity(store):
    write_rows(store, [record(), record("journal-b", OTHER_BUSINESS),
                       record("journal-unknown", "")])
    result = journals.load_company_consultation_context(OWNER, BUSINESS, "합성 동일상호")
    assert [row["journal_id"] for row in result] == ["journal-a"]


@pytest.mark.parametrize("business", ["", "bad", "123"])
def test_missing_or_invalid_identity_cannot_fall_back_to_company_name(store, business):
    write_rows(store, [record()])
    assert journals.load_company_consultation_context(OWNER, business, "합성 동일상호") == []


def test_legacy_business_key_works_only_when_no_conflicting_explicit_number(store):
    write_rows(store, [record("legacy", "", customer_key="biz:" + BUSINESS),
                       record("conflict", OTHER_BUSINESS, customer_key="biz:" + BUSINESS)])
    assert [row["journal_id"] for row in journals.load_company_consultation_context(OWNER, BUSINESS)] == ["legacy"]


def test_local_foreign_owner_is_hidden_but_not_deleted(store):
    write_rows(store, [record(owner_user_id=OWNER),
                       record("foreign", owner_user_id="other-owner")])
    original = store.read_bytes()
    assert [row["journal_id"] for row in journals._load_journals(OWNER)] == ["journal-a"]
    assert store.read_bytes() == original


@pytest.mark.parametrize("target", ["_load_local_journals", "_load_cloud_journals", "_load_journals",
                                     "load_company_consultation_context", "_save_journals", "_save_cloud_journal"])
def test_all_store_boundaries_require_trusted_owner(monkeypatch, store, target):
    def deny(*args, **kwargs):
        raise PermissionError("synthetic denied")
    monkeypatch.setattr(journals, "require_owner_context", deny)
    args = {"load_company_consultation_context": (OWNER, BUSINESS),
            "_save_journals": (OWNER, [record()]), "_save_cloud_journal": (OWNER, record())}
    with pytest.raises(PermissionError):
        getattr(journals, target)(*args.get(target, (OWNER,)))
    assert not store.exists()


def test_cloud_loader_uses_scoped_paginated_queries_and_trusts_row_identity(monkeypatch, store):
    calls = []
    cloud_rows = [
        {"owner_user_id": OWNER, "business_no": BUSINESS, "journal_id": "cloud-safe",
         "company_name": "합성 신상호", "saved_at": "2026-10-23 11:00:00",
         "journal_data": record("payload-id", OTHER_BUSINESS, owner_user_id="other-owner")},
        {"owner_user_id": "other-owner", "business_no": BUSINESS, "journal_id": "foreign",
         "journal_data": record()},
        {"owner_user_id": OWNER, "business_no": OTHER_BUSINESS, "journal_id": "wrong-company",
         "journal_data": record()},
        {"owner_user_id": OWNER, "journal_id": "missing-identity",
         "journal_data": record(customer_key="biz:" + BUSINESS)},
        {"owner_user_id": OWNER, "business_no": "", "journal_id": "blank-identity",
         "journal_data": record(customer_key="biz:" + BUSINESS)},
    ]
    class FakeDatabase:
        def select_all(self, table, **kwargs):
            calls.append((table, kwargs))
            return cloud_rows if len(calls) == 1 else []
    monkeypatch.setattr(journals, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(journals, "CloudDatabase", FakeDatabase)
    result = journals._load_journals(OWNER, BUSINESS)
    assert len(result) == 1
    assert result[0]["journal_id"] == "cloud-safe"
    assert result[0]["business_no"] == BUSINESS
    assert result[0]["owner_user_id"] == OWNER
    assert result[0]["company_name"] == "합성 신상호"
    assert len(calls) == 2
    assert {item[1]["filters"]["business_no"] for item in calls} == {BUSINESS, BUSINESS.replace("-", "")}
    for table, kwargs in calls:
        assert table == journals.CONSULTATION_JOURNAL_TABLE
        assert kwargs["filters"]["owner_user_id"] == OWNER
        assert kwargs["max_rows"] > 500
        assert kwargs["page_size"] <= 1000
        assert kwargs["order"] == "saved_at.desc,journal_id.asc"


@pytest.mark.parametrize("payload", ["broken-json", [], None, '{"summary":"합성내용"}'])
def test_cloud_bad_payload_cannot_override_database_identity(monkeypatch, store, payload):
    class FakeDatabase:
        def select_all(self, table, **kwargs):
            return [{"owner_user_id": OWNER, "business_no": BUSINESS, "journal_id": "row-id",
                     "journal_data": payload}]
    monkeypatch.setattr(journals, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(journals, "CloudDatabase", FakeDatabase)
    rows = journals._load_cloud_journals(OWNER, BUSINESS)
    assert rows and all(row["journal_id"] == "row-id" and row["business_no"] == BUSINESS for row in rows)


def test_history_save_does_not_truncate_at_500_or_replace_same_id_versions(store):
    originals = [record(f"history-{index}") for index in range(650)]
    write_rows(store, originals)
    journals._save_journals(OWNER, [record("history-0", summary="수정된 합성내용")])
    saved = journals._read_journal_rows(store)
    assert len(saved) == 651
    assert {row["summary"] for row in saved if row["journal_id"] == "history-0"} == {"합성 상담내용", "수정된 합성내용"}
    journals._save_journals(OWNER, originals)
    assert len(journals._read_journal_rows(store)) == 651


def test_concurrent_appends_preserve_all_companies_and_older_rows(store):
    write_rows(store, [record("original")])
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda index: journals._save_journals(OWNER, [record(f"concurrent-{index}",
                     business=BUSINESS if index % 2 else OTHER_BUSINESS)]), range(24)))
    assert {row["journal_id"] for row in journals._read_journal_rows(store)} == {"original", *(f"concurrent-{index}" for index in range(24))}


@pytest.mark.parametrize("content", ['{broken', '{}', '[null]', '[1]', '[{"journal_id":"a","journal_id":"b"}]', '[{"value":NaN}]'])
def test_corrupt_original_is_never_treated_as_empty_or_overwritten(store, content):
    store.write_text(content, encoding="utf-8")
    original = store.read_bytes()
    with pytest.raises(CrmStorageError):
        journals._save_journals(OWNER, [record()])
    assert store.read_bytes() == original


def test_atomic_write_failure_preserves_original(monkeypatch, store):
    write_rows(store, [record()])
    original = store.read_bytes()
    def fail(*args, **kwargs):
        raise CrmStorageError("synthetic write failure")
    monkeypatch.setattr(journals, "atomic_write_json", fail)
    with pytest.raises(CrmStorageError):
        journals._save_journals(OWNER, [record("new")])
    assert store.read_bytes() == original


@pytest.mark.parametrize("target", ["_save_journals", "_save_cloud_journal"])
def test_foreign_owner_cannot_be_saved(monkeypatch, store, target):
    write_rows(store, [record()])
    original = store.read_bytes()
    foreign = record(owner_user_id="other-owner")
    with pytest.raises(PermissionError):
        getattr(journals, target)(OWNER, [foreign] if target == "_save_journals" else foreign)
    assert store.read_bytes() == original


def test_aggregate_context_keeps_exact_company_and_no_name_fallback(store):
    write_rows(store, [record(), record("foreign", OTHER_BUSINESS)])
    assert journals.get_company_consultation_context(OWNER, BUSINESS, "합성 동일상호")["count"] == 1
    assert journals.get_company_consultation_context(OWNER, "", "합성 동일상호")["count"] == 0


def test_name_only_relink_stops_before_any_write(monkeypatch, store):
    write_rows(store, [record()])
    original = store.read_bytes()
    mutation = Mock(side_effect=AssertionError("must not mutate"))
    for name in ["_save_journal_relink_state", "merge_policy_matching_preferences", "append_timeline_event", "save_customer_event"]:
        monkeypatch.setattr(journals, name, mutation)
    result = journals.relink_saved_consultation_journals(OWNER, "name-only", "", "합성 동일상호")
    assert result["journal_count"] == 0 and result["errors"]
    assert store.read_bytes() == original
    mutation.assert_not_called()


@pytest.mark.parametrize("row_business", ["", "invalid", OTHER_BUSINESS])
def test_audio_never_joins_name_only_or_conflicting_identity(row_business):
    assert not audio._same_company({"company_name": "합성 동일상호", "business_no": row_business},
                                   "합성 동일상호", BUSINESS)


def test_audio_exact_identity_matches_even_after_company_rename():
    assert audio._same_company({"company_name": "합성 구상호", "business_no": BUSINESS.replace("-", "")},
                              "합성 신상호", BUSINESS)


@pytest.mark.parametrize("returned_owner", ["other-owner", "", None])
def test_audio_metadata_rejects_foreign_or_blank_returned_owner(monkeypatch, returned_owner):
    response = Mock(ok=True)
    response.json.return_value = [{"owner_user_id": returned_owner, "audio_id": "synthetic-audio"}]
    monkeypatch.setattr(audio, "storage_is_configured", lambda: True)
    monkeypatch.setattr(audio, "require_owner_context", lambda owner, **kwargs: owner)
    monkeypatch.setattr(audio, "_headers", lambda: {})
    monkeypatch.setattr(audio, "_rest_url", lambda path: "https://synthetic.invalid" + path)
    monkeypatch.setattr(audio.requests, "get", lambda *args, **kwargs: response)
    assert audio._select_owned_audio_metadata(OWNER, audio_id="synthetic-audio") is None


def test_audio_metadata_accepts_same_returned_owner(monkeypatch):
    row = {"owner_user_id": OWNER, "audio_id": "synthetic-audio"}
    response = Mock(ok=True)
    response.json.return_value = [row]
    monkeypatch.setattr(audio, "storage_is_configured", lambda: True)
    monkeypatch.setattr(audio, "require_owner_context", lambda owner, **kwargs: owner)
    monkeypatch.setattr(audio, "_headers", lambda: {})
    monkeypatch.setattr(audio, "_rest_url", lambda path: "https://synthetic.invalid" + path)
    monkeypatch.setattr(audio.requests, "get", lambda *args, **kwargs: response)
    assert audio._select_owned_audio_metadata(OWNER, audio_id="synthetic-audio") == row


def test_audio_library_returns_more_than_100_without_foreign_rows(monkeypatch):
    rows = [{"owner_user_id": OWNER, "business_no": BUSINESS, "audio_id": f"audio-{index}"}
            for index in range(125)]
    rows.extend([{"owner_user_id": "other-owner", "business_no": BUSINESS, "audio_id": "foreign"},
                 {"owner_user_id": OWNER, "business_no": OTHER_BUSINESS, "audio_id": "wrong-company"}])
    monkeypatch.setattr(audio, "require_owner_context", lambda owner, **kwargs: owner)
    reader = Mock(return_value=rows)
    monkeypatch.setattr(audio, "_select_audio_rows", reader)
    result = audio.list_company_audio(OWNER, BUSINESS, "합성 동일상호")
    assert len(result) == 125
    reader.assert_called_once_with(OWNER, limit=1000, business_no=BUSINESS)


def test_audio_pages_are_company_filtered_before_limit_and_continue_short_pages(monkeypatch):
    calls = []
    datasets = {
        ("business_no_normalized", BUSINESS.replace("-", "")): [
            {"owner_user_id": OWNER, "business_no": BUSINESS, "audio_id": f"audio-{index}"}
            for index in range(1205)],
        ("business_no", BUSINESS.replace("-", "")): [],
        ("business_no", BUSINESS): [],
    }
    def get(url, **kwargs):
        params = kwargs["params"]
        calls.append(dict(params))
        assert params["owner_user_id"] == "eq." + OWNER
        variant = next((key, params[key][3:]) for key in ("business_no_normalized", "business_no") if key in params)
        offset = int(params["offset"])
        response = Mock(ok=True)
        # A server may cap page size below the requested limit.
        response.json.return_value = datasets[variant][offset:offset + 480]
        return response
    monkeypatch.setattr(audio, "require_owner_context", lambda owner, **kwargs: owner)
    monkeypatch.setattr(audio, "storage_is_configured", lambda: True)
    monkeypatch.setattr(audio, "_headers", lambda: {})
    monkeypatch.setattr(audio, "_rest_url", lambda path: "https://synthetic.invalid" + path)
    monkeypatch.setattr(audio.requests, "get", get)
    result = audio.list_company_audio(OWNER, BUSINESS)
    assert len(result) == 1205
    assert [int(call["offset"]) for call in calls if "business_no_normalized" in call] == [0, 480, 960, 1205]
    assert all(int(call["limit"]) <= 1000 for call in calls)


def test_audio_server_foreign_rows_are_rejected_before_return(monkeypatch):
    response = Mock(ok=True)
    response.json.return_value = [{"owner_user_id": "other-owner", "business_no": BUSINESS}]
    monkeypatch.setattr(audio, "require_owner_context", lambda owner, **kwargs: owner)
    monkeypatch.setattr(audio, "storage_is_configured", lambda: True)
    monkeypatch.setattr(audio, "_headers", lambda: {})
    monkeypatch.setattr(audio, "_rest_url", lambda path: "https://synthetic.invalid" + path)
    monkeypatch.setattr(audio.requests, "get", lambda *args, **kwargs: response)
    assert audio._select_audio_rows(OWNER) == []
