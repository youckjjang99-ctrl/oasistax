from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

import pytest

import stock_valuation as stock


BUSINESS = "-".join(("111", "11", "11111"))
OTHER_BUSINESS = "-".join(("222", "22", "22222"))
CORPORATE = "-".join(("110111", "1111111"))
OTHER_CORPORATE = "-".join(("220222", "2222222"))


@pytest.fixture
def snapshots(monkeypatch, tmp_path):
    monkeypatch.setattr(stock, "get_user_dirs", lambda owner: {"base": tmp_path})
    state = {}
    warnings = []
    monkeypatch.setattr(stock, "st", SimpleNamespace(session_state=state, warning=warnings.append))
    monkeypatch.setattr(stock, "_apply_registry_data", lambda data: state.update({"applied": dict(data)}))
    monkeypatch.setattr(stock, "load_registry_snapshot", lambda *args: {})
    monkeypatch.setattr(stock, "sync_registry_snapshot", lambda *args: (False, "pending"))
    path = tmp_path / "registry_cache.json"

    def write(data):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def read():
        return json.loads(path.read_text(encoding="utf-8"))

    return SimpleNamespace(path=path, state=state, warnings=warnings, write=write, read=read)


def test_newer_pending_local_registry_wins_but_retains_cloud_nonblank(monkeypatch, snapshots):
    snapshots.write({BUSINESS: {
        "법인명": "합성법인", "법인등록번호": CORPORATE, "발행주식총수": 2000,
        "saved_at": "2026-10-23T09:01:00+00:00", "본점소재지": "",
    }})
    monkeypatch.setattr(stock, "load_registry_snapshot", lambda *args: {
        "법인명": "합성법인", "법인등록번호": CORPORATE, "발행주식총수": 1000,
        "saved_at": "2026-10-23T09:00:00+00:00", "본점소재지": "합성주소",
    })
    restored = stock._restore_registry_for_business("owner", BUSINESS, "합성법인", CORPORATE)
    assert restored["발행주식총수"] == 2000
    assert restored["본점소재지"] == "합성주소"
    assert snapshots.read()[BUSINESS]["saved_at"] == "2026-10-23T09:01:00+00:00"


def test_newer_cloud_registry_wins_without_erasing_local_confirmed_fields(monkeypatch, snapshots):
    snapshots.write({BUSINESS: {"발행주식총수": 1000, "1주당액면가액": 5000,
                                "saved_at": "2026-10-23 09:00:00"}})
    monkeypatch.setattr(stock, "load_registry_snapshot", lambda *args: {
        "발행주식총수": 2000, "1주당액면가액": "", "saved_at": "2026-10-23T09:02:00Z",
    })
    restored = stock._restore_registry_for_business("owner", BUSINESS)
    assert restored["발행주식총수"] == 2000
    assert restored["1주당액면가액"] == 5000


@pytest.mark.parametrize("legacy", [
    {"사업자등록번호": OTHER_BUSINESS, "법인명": "동일상호", "법인등록번호": CORPORATE},
    {"법인명": "동일상호", "법인등록번호": OTHER_CORPORATE},
])
def test_same_name_never_overrides_conflicting_legacy_identifiers(snapshots, legacy):
    snapshots.write({"legacy": legacy})
    original = snapshots.path.read_bytes()
    assert stock._restore_registry_for_business("owner", BUSINESS, "동일상호", CORPORATE) == {}
    assert snapshots.path.read_bytes() == original
    assert "applied" not in snapshots.state


def test_same_corporation_can_restore_historical_name_and_keeps_original_key(snapshots):
    legacy = {"법인명": "구상호", "법인등록번호": CORPORATE, "발행주식총수": 1000}
    snapshots.write({CORPORATE: legacy})
    restored = stock._restore_registry_for_business("owner", BUSINESS, "신상호", CORPORATE)
    assert restored["발행주식총수"] == 1000
    assert restored["사업자등록번호"] == BUSINESS
    assert snapshots.read()[CORPORATE] == legacy


def test_digits_only_legacy_recovery_remains_readable_after_rekey(snapshots):
    legacy = {"법인명": "합성법인", "발행주식총수": 1000}
    digits = BUSINESS.replace("-", "")
    snapshots.write({digits: legacy})
    for _ in range(2):
        restored = stock._restore_registry_for_business("owner", BUSINESS, "합성법인")
        assert restored["발행주식총수"] == 1000
    assert snapshots.read()[digits] == legacy


def test_ambiguous_legacy_same_name_is_not_adopted(snapshots):
    snapshots.write({"a": {"법인명": "동일 상호", "발행주식총수": 1000},
                     "b": {"법인명": "동일상호", "발행주식총수": 2000}})
    original = snapshots.path.read_bytes()
    assert stock._restore_registry_for_business("owner", BUSINESS, "동일상호") == {}
    assert snapshots.path.read_bytes() == original


def test_cloud_identity_mismatch_does_not_replace_valid_local(monkeypatch, snapshots):
    snapshots.write({BUSINESS: {"법인등록번호": CORPORATE, "발행주식총수": 1000}})
    monkeypatch.setattr(stock, "load_registry_snapshot", lambda *args: {
        "법인등록번호": OTHER_CORPORATE, "발행주식총수": 9999,
    })
    restored = stock._restore_registry_for_business("owner", BUSINESS, "합성법인", CORPORATE)
    assert restored["발행주식총수"] == 1000
    assert snapshots.warnings


def test_conflicting_direct_cache_is_not_overwritten_even_by_cloud(monkeypatch, snapshots):
    snapshots.write({BUSINESS: {"법인등록번호": OTHER_CORPORATE, "발행주식총수": 9999}})
    original = snapshots.path.read_bytes()
    monkeypatch.setattr(stock, "load_registry_snapshot", lambda *args: {
        "법인등록번호": CORPORATE, "발행주식총수": 1000,
    })
    assert stock._restore_registry_for_business("owner", BUSINESS, "합성법인", CORPORATE) == {}
    assert snapshots.path.read_bytes() == original


def test_restore_re_reads_after_cloud_fetch_and_preserves_intervening_save(monkeypatch, snapshots):
    snapshots.write({BUSINESS: {"발행주식총수": 1000, "saved_at": "2026-10-23T09:00:00Z"}})

    def fetch(*args):
        stock._save_registry_cache("owner", BUSINESS, {"발행주식총수": 2000})
        stock._save_registry_cache("owner", OTHER_BUSINESS, {"법인명": "다른합성법인", "자본금": 6000})
        return {"발행주식총수": 500, "saved_at": "2026-01-01T09:00:00Z"}

    monkeypatch.setattr(stock, "load_registry_snapshot", fetch)
    restored = stock._restore_registry_for_business("owner", BUSINESS)
    assert restored["발행주식총수"] == 2000
    assert snapshots.read()[OTHER_BUSINESS]["자본금"] == 6000


def test_concurrent_local_saves_lock_read_merge_without_losing_fields(snapshots):
    snapshots.write({BUSINESS: {"법인명": "합성법인"}})
    payloads = [{f"합성필드{index}": index + 1} for index in range(12)]
    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(lambda payload: stock._save_registry_cache("owner", BUSINESS, payload), payloads))
    saved = snapshots.read()[BUSINESS]
    assert saved["법인명"] == "합성법인"
    assert all(saved[key] == value for item in payloads for key, value in item.items())
    assert not list(snapshots.path.parent.glob("*.tmp"))


def test_save_rejects_existing_corporate_conflict_without_overwrite(snapshots):
    snapshots.write({BUSINESS: {"법인등록번호": CORPORATE, "발행주식총수": 1000}})
    original = snapshots.path.read_bytes()
    with pytest.raises(stock.RegistryCacheReadError):
        stock._save_registry_cache("owner", BUSINESS, {"법인등록번호": OTHER_CORPORATE})
    assert snapshots.path.read_bytes() == original


def test_save_rejects_incoming_business_conflict_without_creating_file(snapshots):
    with pytest.raises(stock.RegistryCacheReadError):
        stock._save_registry_cache("owner", BUSINESS, {"사업자등록번호": OTHER_BUSINESS})
    assert not snapshots.path.exists()
