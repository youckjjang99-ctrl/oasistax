"""Synthetic append-only financial cache regressions; no network or client data."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

import stock_valuation as stock

OWNER = "synthetic-owner"
BUSINESS = "-".join(("123", "45", "67890"))
OTHER_BUSINESS = "-".join(("987", "65", "43210"))


def financial(business=BUSINESS, stamp="2026-01-01 00:00:00", years=(2025, 2024, 2023), amount=100):
    return {"사업자등록번호": business, "업체명": "합성 법인", "saved_at": stamp,
            "매출액": amount,
            "재무연도별": [{"연도": year, "당기순이익": amount} for year in years]}


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(stock, "_financial_cache_path", lambda owner: tmp_path / f"financial-{owner}.json")
    monkeypatch.setattr(stock, "sync_financial_snapshot", lambda *a, **kw: (True, "synthetic"))
    monkeypatch.setattr(stock, "load_financial_snapshot", lambda *a, **kw: {})
    path = stock._financial_cache_path(OWNER)
    return path


@pytest.mark.parametrize("raw", [b'{"broken":', b'[]', b'null', json.dumps({BUSINESS: []}).encode()])
def test_corrupt_or_non_object_financial_cache_refuses_overwrite(cache, raw):
    cache.write_bytes(raw)
    with pytest.raises(ValueError, match="기존 재무자료"):
        stock._save_financial_cache(OWNER, {BUSINESS: financial()})
    assert cache.read_bytes() == raw


def test_concurrent_financial_updates_preserve_other_companies(cache):
    other = financial(OTHER_BUSINESS, amount=999)
    stock._save_financial_cache(OWNER, {OTHER_BUSINESS: other})
    def save(index):
        business = f"321-00-{index:05d}"
        stock._save_financial_cache(OWNER, {business: financial(business, amount=index)})
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(save, range(16)))
    stored = json.loads(cache.read_text(encoding="utf-8"))
    assert len(stored) == 17
    assert {key: value for key, value in stored[OTHER_BUSINESS].items() if key != "_versions"} == other


def test_latest_financial_version_replaces_years_without_blending_and_keeps_old(cache):
    original = financial(years=(2025, 2024, 2023), amount=100)
    new = financial(stamp="2026-02-01 00:00:00", years=(2026,), amount=200)
    stock._save_financial_cache(OWNER, {BUSINESS: original})
    stock._save_financial_cache(OWNER, {BUSINESS: new})
    stored = json.loads(cache.read_text(encoding="utf-8"))[BUSINESS]
    assert stored["재무연도별"] == new["재무연도별"]
    assert stored["매출액"] == 200
    assert original in stored["_versions"] and new in stored["_versions"]
    assert len(stored["_versions"]) == 2


def test_stale_cloud_cache_update_cannot_erase_new_local_or_other_keys(cache):
    stale = financial(stamp="2026-01-01 00:00:00", amount=10)
    local_new = financial(stamp="2026-03-01 00:00:00", amount=30)
    other = financial(OTHER_BUSINESS, amount=900)
    stock._save_financial_cache(OWNER, {BUSINESS: local_new, OTHER_BUSINESS: other})
    stock._save_financial_cache(OWNER, {BUSINESS: deepcopy(stale)})
    stored = json.loads(cache.read_text(encoding="utf-8"))
    assert stored[BUSINESS]["매출액"] == 30
    assert stored[OTHER_BUSINESS]["매출액"] == 900
    assert stale in stored[BUSINESS]["_versions"]
    assert local_new in stored[BUSINESS]["_versions"]


def test_mismatched_explicit_business_is_rejected_without_any_change(cache):
    stock._save_financial_cache(OWNER, {BUSINESS: financial()})
    before = cache.read_bytes()
    with pytest.raises(ValueError, match="식별번호"):
        stock._save_financial_cache(OWNER, {BUSINESS: financial(OTHER_BUSINESS)})
    assert cache.read_bytes() == before


def test_repeated_identical_financial_version_is_deduplicated(cache):
    record = financial()
    stock._save_financial_cache(OWNER, {BUSINESS: record})
    stock._save_financial_cache(OWNER, {BUSINESS: deepcopy(record)})
    stored = json.loads(cache.read_text(encoding="utf-8"))[BUSINESS]
    assert len(stored["_versions"]) == 1
