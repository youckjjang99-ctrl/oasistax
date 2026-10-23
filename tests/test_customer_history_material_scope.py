from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import pytest

import customer_history as history


OWNER = "synthetic-owner"
BUSINESS = "-".join(("123", "45", "67890"))
OTHER = "-".join(("987", "65", "43210"))


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "get_user_dirs", lambda _: {"base": tmp_path})
    monkeypatch.setattr(history, "require_owner_context", lambda owner, **kw: owner)
    monkeypatch.setattr(history, "cloud_is_configured", lambda: False)
    return tmp_path / "customer_history.json"


def event(index, business=BUSINESS):
    return {"business_no": business, "captured_at": f"2026-10-23T10:{index:04}",
            "source": "enterprise_document:articles", "data": {"이벤트ID": f"synthetic-{index}"}}


def test_explicit_material_read_returns_all_local_versions_without_deleting_old(store):
    history._save_all(OWNER, {BUSINESS: [event(i) for i in range(245)]})
    assert len(history.get_customer_history(OWNER, BUSINESS)) == 200
    assert len(history.get_customer_history(OWNER, BUSINESS, limit=None)) == 245
    assert len(json.loads(store.read_text(encoding="utf-8"))[BUSINESS]) == 245


def test_unbounded_material_read_filters_cloud_before_paging_and_rejects_bad_rows(store, monkeypatch):
    rows = [{**event(i), "owner_user_id": OWNER, "snapshot_data": {"이벤트ID": f"remote-{i}"}}
            for i in range(230)]
    rows.extend([{**rows[0], "owner_user_id": "another-owner"}, {**rows[0], "business_no": OTHER}])
    calls = []
    def select_all(table, **kwargs):
        calls.append((table, kwargs))
        return rows
    monkeypatch.setattr(history, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(history, "CloudDatabase", lambda: SimpleNamespace(select_all=select_all))
    result = history.get_customer_history(OWNER, BUSINESS, limit=None)
    assert len(result) == 230
    assert calls[0][1]["filters"] == {"owner_user_id": OWNER, "business_no": BUSINESS}
    assert calls[0][1]["page_size"] == 500


def test_same_name_other_company_and_owner_local_events_do_not_leak(store):
    history._save_all(OWNER, {BUSINESS: [event(1), event(2, OTHER), {**event(3), "owner_user_id": "another-owner"}]})
    result = history.get_customer_history(OWNER, BUSINESS, limit=None)
    assert [item["data"]["이벤트ID"] for item in result] == ["synthetic-1"]


def test_history_atomic_merge_retains_all_concurrent_uploads(store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: history._save_all(OWNER, {BUSINESS: [event(i)]}), range(16)))
    assert len(json.loads(store.read_text(encoding="utf-8"))[BUSINESS]) == 16


def test_corrupt_history_is_never_replaced(store):
    original = b"{unreadable history"
    store.write_bytes(original)
    with pytest.raises(history.CustomerHistoryCorruptionError):
        history._save_all(OWNER, {BUSINESS: [event(1)]})
    assert store.read_bytes() == original
