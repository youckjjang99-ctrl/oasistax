from __future__ import annotations

import multiprocessing
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

import crm
import crm_enhancements
from crm_file_store import CrmConflictError, CrmStorageError, locked_json


# Synthetic fixture only; never copied from a customer or contact record.
SYNTHETIC_CUSTOMER_KEY = "biz:" + "".join(("111", "11", "11111"))


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(crm, "USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(crm_enhancements, "_path", lambda user: tmp_path / user / "profiles.json")
    monkeypatch.setattr(crm_enhancements, "cloud_is_configured", lambda: False)
    crm._load_crm_data_cached.cache_clear()
    return tmp_path


def _append_process(root: str, worker: int, ready) -> None:
    crm.USER_DATA_DIR = Path(root)
    if not ready.wait(10):
        raise RuntimeError("test_start_timeout")
    for number in range(12):
        ok, message = crm.append_timeline_event("owner", SYNTHETIC_CUSTOMER_KEY, "event", f"{worker}-{number}")
        assert ok, message


def test_simultaneous_process_appends_preserve_every_event(store, monkeypatch):
    # Streamlit AppTest temporarily replaces __main__.__file__. Windows spawn
    # must import this fixture module, not rerun an earlier UI test's app script.
    monkeypatch.setattr(sys.modules["__main__"], "__file__", __file__)
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    processes = [context.Process(target=_append_process, args=(str(store), worker, ready)) for worker in range(4)]
    for process in processes:
        process.start()
    ready.set()
    try:
        for process in processes:
            process.join(20)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
    record = crm.get_customer_record("owner", SYNTHETIC_CUSTOMER_KEY)
    assert len(record["timeline"]) == 48
    assert len({event["id"] for event in record["timeline"]}) == 48
    assert record["_local_revision"] == 48


def test_thread_updates_to_different_customers_preserve_all_records(store):
    ready = threading.Barrier(8)

    def save(index):
        ready.wait(timeout=5)
        return crm.upsert_customer_record("owner", f"company:{index}", memo=f"memo-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(ok for ok, _ in pool.map(save, range(8)))
    records = crm.load_crm_data("owner")["customers"]
    assert len(records) == 8
    assert all(records[f"company:{i}"]["memo"] == f"memo-{i}" for i in range(8))


def test_stale_form_is_rejected_and_attempt_preserved(store):
    assert crm.upsert_customer_record("owner", "company:test", memo="first", expected_revision=0)[0]
    before = crm.get_customer_record("owner", "company:test")
    ok, message = crm.upsert_customer_record("owner", "company:test", memo="stale-input", expected_revision=0, profile={"priority": "5"})
    assert not ok
    assert "충돌 이력" in message
    assert crm.get_customer_record("owner", "company:test") == before
    conflicts = crm.load_crm_data("owner")["_local_conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["attempted_changes"]["memo"] == "stale-input"
    assert conflicts[0]["attempted_changes"]["_v44_profile"]["priority"] == "5"


def test_simultaneous_same_revision_allows_one_writer(store):
    crm.upsert_customer_record("owner", "company:test", memo="initial")
    ready = threading.Barrier(2)

    def save(memo):
        ready.wait(timeout=5)
        return crm.upsert_customer_record("owner", "company:test", memo=memo, expected_revision=1)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ["a", "b"]))
    assert sorted(ok for ok, _ in results) == [False, True]
    assert crm.get_customer_record("owner", "company:test")["_local_revision"] == 2
    assert len(crm.load_crm_data("owner")["_local_conflicts"]) == 1


def test_stale_whole_file_save_cannot_overwrite_newer_record(store):
    first = crm.load_crm_data("owner")
    stale = crm.load_crm_data("owner")
    first["customers"]["company:first"] = {"memo": "first"}
    crm.save_crm_data("owner", first)
    stale["customers"]["company:second"] = {"memo": "second"}
    with pytest.raises(CrmConflictError):
        crm.save_crm_data("owner", stale)
    with pytest.raises(CrmConflictError):
        crm.save_crm_data("owner", {"customers": {}})
    assert crm.load_crm_data("owner")["customers"] == {"company:first": {"memo": "first"}}


@pytest.mark.parametrize("contents", ['{"customers":', '[]', '{"customers": []}', '{"customers":{},"customers":{}}', '{"customers":{},"x":NaN}'])
def test_corruption_is_not_treated_as_empty_or_overwritten(store, contents):
    path = crm.get_crm_file_path("owner")
    path.write_text(contents, encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(CrmStorageError):
        crm.load_crm_data("owner")
    with pytest.raises(CrmStorageError):
        crm.save_crm_data("owner", {"customers": {}})
    ok, message = crm.upsert_customer_record("owner", "company:test", memo="new")
    assert not ok
    assert contents not in message
    assert path.read_bytes() == original


def test_existing_timeline_over_eighty_and_unknown_fields_are_preserved(store):
    events = [{"at": "legacy", "title": "old", "detail": str(i)} for i in range(125)]
    crm.save_crm_data("owner", {"customers": {"company:test": {
        "timeline": events, "custom": {"keep": True},
        "_cloud_base": {"memo": "cloud"}, "_cloud_version": 7,
    }}, "legacy_metadata": {"keep": True}})
    assert crm.append_timeline_event("owner", "company:test", "new", "append")[0]
    assert crm.upsert_customer_record("owner", "company:test", event_detail="edit")[0]
    snapshot = crm.load_crm_data("owner")
    record = snapshot["customers"]["company:test"]
    assert len(record["timeline"]) == 127
    assert record["timeline"][2:] == events
    assert record["custom"] == {"keep": True}
    assert record["_cloud_base"] == {"memo": "cloud"}
    assert record["_cloud_version"] == 7
    assert record["_sync_state"] == "pending"
    assert snapshot["legacy_metadata"] == {"keep": True}


def test_profile_and_crm_fields_commit_in_one_revision(store):
    assert crm.upsert_customer_record("owner", "company:test", memo="saved", expected_revision=0, profile={"priority": "5"})[0]
    profile = crm_enhancements.get_crm_profile("owner", "company:test")
    assert profile == {"priority": "5"}
    profile["priority"] = "1"
    record = crm.get_customer_record("owner", "company:test")
    assert record["_v44_profile"]["priority"] == "5"
    assert record["_local_revision"] == 1


def test_invalid_record_revision_is_rejected_without_overwrite(store):
    crm.save_crm_data("owner", {"customers": {"company:test": {"_local_revision": "bad-value"}}})
    path = crm.get_crm_file_path("owner")
    original = path.read_bytes()
    assert not crm.upsert_customer_record("owner", "company:test", memo="new")[0]
    assert not crm.append_timeline_event("owner", "company:test", "new", "event")[0]
    assert path.read_bytes() == original


def test_legacy_profile_api_writes_canonical_without_erasing_crm(store):
    crm.upsert_customer_record("owner", "company:test", memo="keep")
    saved = crm_enhancements.save_crm_profile("owner", "company:test", "상담", "4", "manager")
    record = crm.get_customer_record("owner", "company:test")
    assert record["memo"] == "keep"
    assert record["_v44_profile"] == saved
    assert record["_local_revision"] == 2


def test_profile_upsert_only_overwrites_editable_fields_and_keeps_extensions(store):
    existing = {
        "pipeline_stage": "old-stage", "priority": "2", "assigned_manager": "old-manager",
        "custom_extension": {"source": "original", "history": ["keep"]},
    }
    crm.save_crm_data("owner", {"customers": {"company:test": {"_v44_profile": existing}}})
    changes = {"pipeline_stage": "new-stage", "priority": "5", "assigned_manager": "new-manager",
               "custom_extension": {"source": "stale-form"}, "new_extension": {"keep": True}}
    assert crm.upsert_customer_record("owner", "company:test", profile=changes, expected_revision=0)[0]
    profile = crm.get_customer_record("owner", "company:test")["_v44_profile"]
    assert profile["custom_extension"] == existing["custom_extension"]
    assert profile["new_extension"] == {"keep": True}
    for name in ("pipeline_stage", "priority", "assigned_manager"):
        assert profile[name] == changes[name]
    assert changes["custom_extension"] == {"source": "stale-form"}
    assert crm.upsert_customer_record("owner", "company:test", profile={})[0]
    assert crm.get_customer_record("owner", "company:test")["_v44_profile"] == profile


def test_standalone_profile_save_returns_preserved_extensions_without_aliasing(store):
    existing = {"priority": "2", "custom_extension": {"history": ["keep"]}}
    crm.save_crm_data("owner", {"customers": {"company:test": {"_v44_profile": existing}}})
    saved = crm_enhancements.save_crm_profile("owner", "company:test", "new-stage", "4", "new-manager")
    assert saved["custom_extension"] == existing["custom_extension"]
    assert saved["priority"] == "4"
    saved["custom_extension"]["history"].append("caller-mutation")
    assert crm.get_customer_record("owner", "company:test")["_v44_profile"]["custom_extension"] == existing["custom_extension"]


def test_profile_record_merge_preserves_extensions_and_both_inputs(store):
    original = {"memo": "keep", "_v44_profile": {"priority": "2", "custom": {"items": ["original"]}}}
    changes = {"priority": "5", "custom": {"items": ["stale"]}}
    merged = crm_enhancements.merge_profile_into_crm_record(original, changes)
    assert merged["_v44_profile"] == {"priority": "5", "custom": {"items": ["original"]}}
    merged["_v44_profile"]["custom"]["items"].append("caller-mutation")
    assert original["_v44_profile"]["custom"] == {"items": ["original"]}
    assert changes["custom"] == {"items": ["stale"]}


@pytest.mark.parametrize("invalid", [None, [], "invalid-profile", 42])
def test_invalid_existing_profile_blocks_replacement_and_preserves_original(store, invalid):
    crm.save_crm_data("owner", {"customers": {"company:test": {"_v44_profile": invalid}}})
    path = crm.get_crm_file_path("owner")
    original = path.read_bytes()
    ok, message = crm.upsert_customer_record("owner", "company:test", memo="new", profile={"priority": "5"})
    assert not ok
    assert "원본" in message
    with pytest.raises(CrmStorageError):
        crm_enhancements.save_crm_profile("owner", "company:test", "new-stage", "5", "manager")
    with pytest.raises(CrmStorageError):
        crm_enhancements.merge_profile_into_crm_record({"_v44_profile": invalid}, {"priority": "5"})
    with pytest.raises(CrmStorageError):
        crm_enhancements.get_crm_profile("owner", "company:test")
    assert path.read_bytes() == original


@pytest.mark.parametrize("invalid", [[], "invalid-profile", 42])
def test_invalid_incoming_profile_cannot_silently_erase_existing_extension(store, invalid):
    crm.upsert_customer_record("owner", "company:test", profile={"priority": "3", "custom": {"keep": True}})
    path = crm.get_crm_file_path("owner")
    original = path.read_bytes()
    assert not crm.upsert_customer_record("owner", "company:test", profile=invalid)[0]
    with pytest.raises(CrmStorageError):
        crm_enhancements.merge_profile_into_crm_record(crm.get_customer_record("owner", "company:test"), invalid)
    assert path.read_bytes() == original


def test_legacy_profile_bulk_merge_is_atomic_and_corruption_safe(store):
    assert crm_enhancements.save_crm_profiles_bulk("owner", {"company:a": {"priority": "2"}}) == 1
    assert crm_enhancements.save_crm_profiles_bulk("owner", {"company:b": {"priority": "4"}}) == 1
    assert set(crm_enhancements._load_all("owner")) == {"company:a", "company:b"}
    path = crm_enhancements._path("owner")
    path.write_text("invalid-json", encoding="utf-8")
    with pytest.raises(CrmStorageError):
        crm_enhancements.save_crm_profiles_bulk("owner", {"company:c": {"priority": "5"}})
    assert path.read_text(encoding="utf-8") == "invalid-json"


def test_atomic_replace_failure_keeps_original_and_cleans_temp(store):
    crm.upsert_customer_record("owner", "company:test", memo="original")
    path = crm.get_crm_file_path("owner")
    original = path.read_bytes()
    with patch("crm_file_store.os.replace", side_effect=OSError("private-test-error")):
        ok, message = crm.upsert_customer_record("owner", "company:test", memo="failed")
    assert not ok
    assert "private-test-error" not in message
    assert path.read_bytes() == original
    assert list(path.parent.glob("*.tmp")) == []


def test_transient_windows_replace_denial_retries_same_atomic_payload(store):
    import crm_file_store
    if crm_file_store.os.name != "nt":
        pytest.skip("Windows-specific short sharing/access denial")
    crm.upsert_customer_record("owner", "company:test", memo="original")
    original_replace = crm_file_store.os.replace
    calls = []
    denied = PermissionError(13, "synthetic-sharing-denial")
    denied.winerror = 5

    def fail_once(source, destination):
        calls.append((source, destination))
        if len(calls) == 1:
            raise denied
        return original_replace(source, destination)

    with patch("crm_file_store.os.replace", side_effect=fail_once):
        ok, _ = crm.upsert_customer_record("owner", "company:test", memo="after-retry")
    assert ok
    assert calls[0] == calls[1]
    assert crm.get_customer_record("owner", "company:test")["memo"] == "after-retry"


def test_mutation_callback_failure_aborts_and_nested_lock_is_reentrant(store):
    path = crm.get_crm_file_path("owner")
    with locked_json(path):
        with locked_json(path):
            assert crm.upsert_customer_record("owner", "company:test", memo="original")[0]
    original = path.read_bytes()

    def fail(data):
        data["customers"].clear()
        raise ValueError("abort")

    with pytest.raises(ValueError, match="abort"):
        crm.mutate_crm_data("owner", fail)
    assert path.read_bytes() == original


def test_contended_thread_lock_has_bounded_timeout(store):
    path = crm.get_crm_file_path("owner")
    ready = threading.Event()
    release = threading.Event()

    def hold():
        with locked_json(path):
            ready.set()
            release.wait(5)

    thread = threading.Thread(target=hold)
    thread.start()
    try:
        assert ready.wait(5)
        with pytest.raises(CrmStorageError):
            with locked_json(path, timeout=0.02):
                pytest.fail("lock must not be acquired")
    finally:
        release.set()
        thread.join(5)
