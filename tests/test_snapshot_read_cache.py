"""No network or real customer data: cache reuse always checks ownership."""
from copy import deepcopy
from types import SimpleNamespace
import uuid

import pytest

import cloud_sync
from cloud_db import TABLE_FINANCIALS, TABLE_REGISTRY
import snapshot_read_cache as cache
from performance_cache import cache_generation


def snapshot(owner="fixture-owner-a", version=1, value="fixture-content"):
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, owner)), "owner_user_id": owner,
            "business_no": "fixture-business", "updated_at": f"2026-01-01T00:00:{version:02d}+00:00",
            "financial_data": {"fixture": value}, "registry_data": {"fixture": value}}


class Database:
    def __init__(self):
        self.config = SimpleNamespace(url="https://example.invalid")
        self.rows = {owner: snapshot(owner) for owner in ("fixture-owner-a", "fixture-owner-b")}
        self.full_reads, self.metadata_reads = [], []
        self.failure = False
        self.during_metadata = None

    def select(self, table, filters, columns="*", limit=None):
        assert table in (TABLE_FINANCIALS, TABLE_REGISTRY) and limit == 1
        if self.failure:
            raise RuntimeError("synthetic-read-error")
        self.metadata_reads.append((table, deepcopy(filters), columns))
        if self.during_metadata:
            callback, self.during_metadata = self.during_metadata, None
            callback()
        row = self.rows.get(filters["owner_user_id"])
        if not row or row["id"] != filters.get("id"):
            return []
        assert columns == "id,owner_user_id,updated_at"
        return [{key: row[key] for key in columns.split(",")}]

    def fetch(self, owner):
        self.full_reads.append(owner)
        if self.failure:
            raise RuntimeError("synthetic-read-error")
        return deepcopy(self.rows.get(owner, {}))


@pytest.fixture(autouse=True)
def isolated_cache():
    cache.clear_snapshot_cache()
    yield
    cache.clear_snapshot_cache()


def read(database, owner="fixture-owner-a", table=TABLE_FINANCIALS, key=("fixture",)):
    field = "financial_data" if table == TABLE_FINANCIALS else "registry_data"
    return cache.read_snapshot(database, table, owner, key, field, lambda: database.fetch(owner))


def test_repeated_payload_reads_use_small_fresh_owner_checks():
    database = Database()
    for _ in range(10):
        assert read(database) == {"fixture": "fixture-content"}
    assert len(database.full_reads) == 1 and len(database.metadata_reads) == 9
    assert all(filters["owner_user_id"] == "fixture-owner-a" and "id" in filters
               for _, filters, _ in database.metadata_reads)


def test_updated_timestamp_fetches_latest_without_waiting_for_ttl():
    database = Database()
    read(database)
    database.rows["fixture-owner-a"] = snapshot(version=2, value="new-fixture")
    assert read(database)["fixture"] == "new-fixture"
    assert len(database.full_reads) == 2


def test_transferred_row_is_not_served_to_previous_owner():
    database = Database()
    read(database)
    moved = database.rows.pop("fixture-owner-a")
    moved["owner_user_id"] = "fixture-owner-b"
    database.rows["fixture-owner-b"] = moved
    assert read(database) == {}
    assert read(database, "fixture-owner-b") == {"fixture": "fixture-content"}


def test_wrong_owner_metadata_is_rejected_and_cache_evicted():
    database = Database()
    read(database)
    database.rows["fixture-owner-a"]["owner_user_id"] = "fixture-owner-b"
    with pytest.raises(RuntimeError, match="snapshot_owner_unconfirmed"):
        read(database)
    assert not cache._CACHE


def test_read_failure_is_not_cached_and_retry_can_recover():
    database = Database()
    read(database)
    database.failure = True
    with pytest.raises(RuntimeError):
        read(database)
    assert not cache._CACHE
    database.failure = False
    assert read(database)
    assert len(database.full_reads) == 2


def test_empty_result_is_not_cached():
    database = Database()
    database.rows.clear()
    assert read(database) == read(database) == {}
    assert len(database.full_reads) == 2 and not cache._CACHE


def test_payload_and_logical_source_keys_are_isolated_and_detached():
    database = Database()
    output = read(database)
    output["fixture"] = "caller-edit"
    assert read(database)["fixture"] == "fixture-content"
    read(database, "fixture-owner-b")
    read(database, table=TABLE_REGISTRY)
    read(database, key=("different-identity",))
    assert len(database.full_reads) == 4


def test_project_and_owner_generation_are_separate():
    database = Database()
    read(database)
    read(database, "fixture-owner-b")
    cache.invalidate_snapshot_reads("fixture-owner-a")
    read(database)
    read(database, "fixture-owner-b")
    assert database.full_reads == ["fixture-owner-a", "fixture-owner-b", "fixture-owner-a"]
    database.config.url = "https://different.example.invalid"
    read(database)
    assert len(database.full_reads) == 4


def test_invalidation_during_metadata_check_does_not_return_stale_payload():
    database = Database()
    read(database)
    database.during_metadata = lambda: cache.invalidate_snapshot_reads("fixture-owner-a")
    read(database)
    assert len(database.full_reads) == 2


def test_ttl_expiry_and_bounded_memory(monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cache, "SNAPSHOT_CACHE_LIMIT", 2)
    database = Database()
    read(database)
    clock[0] += cache.SNAPSHOT_CACHE_TTL
    read(database)
    assert len(database.full_reads) == 2 and not database.metadata_reads
    read(database, key=("two",))
    read(database, key=("three",))
    assert len(cache._CACHE) == 2


@pytest.mark.parametrize("change", [{"id": "bad-id"}, {"updated_at": "invalid"},
    {"updated_at": "2026-01-01"}, {"financial_data": []}])
def test_malformed_rows_are_never_cached(change):
    database = Database()
    database.rows["fixture-owner-a"].update(change)
    with pytest.raises(RuntimeError):
        read(database)
    assert not cache._CACHE


@pytest.mark.parametrize("kind", ["financial", "registry"])
def test_snapshot_write_invalidates_only_successful_owner(monkeypatch, kind):
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: False)
    monkeypatch.setattr(cloud_sync, "_safe_upsert", lambda *args: (True, "fixture-ok"))
    writer = getattr(cloud_sync, f"sync_{kind}_snapshot")
    owner = "fixture-owner-a"
    before = cache_generation("snapshot_reads", owner)
    other_before = cache_generation("snapshot_reads", "fixture-owner-b")
    synthetic_business = "".join(("111", "11", "11111"))
    assert writer(owner, synthetic_business, {})[0]
    assert cache_generation("snapshot_reads", owner) == before + 1
    assert cache_generation("snapshot_reads", "fixture-owner-b") == other_before
    monkeypatch.setattr(cloud_sync, "_safe_upsert", lambda *args: (False, "fixture-pending"))
    assert not writer(owner, synthetic_business, {})[0]
    assert cache_generation("snapshot_reads", owner) == before + 1
