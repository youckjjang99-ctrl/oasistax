"""Synthetic in-memory snapshots only; no PDFs, customer files or network."""
from copy import deepcopy
from unittest.mock import Mock

import pandas as pd
import pytest

import stock_valuation as module


BUSINESS = "-".join(("111", "11", "11111"))  # constructed synthetic identifier
IDENTITY_FIELDS = ("업체명", "대표자명", "법인등록번호", "사업장 소재지", "설립일")


def previous():
    return {"업체명": "synthetic-company", "대표자명": "synthetic-representative",
            "법인등록번호": "-".join(("011111", "1111111")),
            "사업장 소재지": "synthetic-address", "설립일": "2020-01-02",
            "사업자등록번호": BUSINESS, "매출액": 100, "영업이익": 10, "당기순이익": 5,
            "자산총계": 300, "부채총계": 200, "종업원수": 12,
            "재무연도별": []}


@pytest.fixture
def storage(monkeypatch):
    cache = {BUSINESS: previous(), "synthetic-other": {"untouched": True}}
    writer, cloud = Mock(), Mock(return_value=(True, "synthetic-ok"))
    monkeypatch.setattr(module, "_load_financial_cache", lambda _: deepcopy(cache))
    monkeypatch.setattr(module, "_save_financial_cache", writer)
    monkeypatch.setattr(module, "sync_financial_snapshot", cloud)
    return cache, writer, cloud


def save(storage, changes):
    _, writer, cloud = storage
    assert module.save_cretop_financial_snapshot("synthetic-owner", {"사업자등록번호": BUSINESS, **changes})
    stored = writer.call_args.args[1]
    assert stored["synthetic-other"] == {"untouched": True}
    assert cloud.call_args.args[2] == stored[BUSINESS]
    return stored[BUSINESS]


@pytest.mark.parametrize("unknown", [None, "", " ", "미확인", float("nan"), pd.NaT, pd.NA])
def test_unknown_identity_values_preserve_existing(storage, unknown):
    before = deepcopy(storage[0])
    result = save(storage, {**{field: unknown for field in IDENTITY_FIELDS}, "매출액": 200})
    for field in IDENTITY_FIELDS:
        assert result[field] == before[BUSINESS][field]
    assert storage[0] == before


def test_explicit_zero_and_negative_amounts_and_corrected_name_are_applied(storage):
    result = save(storage, {"대표자명": "synthetic-corrected", "매출액": 0,
                            "영업이익": -30, "당기순이익": -7})
    assert result["대표자명"] == "synthetic-corrected"
    assert result["매출액"] == 0 and result["영업이익"] == -30 and result["당기순이익"] == -7


def test_missing_identity_is_preserved_without_inventing_financial_year_metadata(storage):
    result = save(storage, {"매출액": 150})
    assert all(result[field] == storage[0][BUSINESS][field] for field in IDENTITY_FIELDS)
    assert not any(key.startswith("_financial_") for key in result)


def test_explicit_new_identity_replaces_previous_identity(storage):
    changes = {field: "synthetic-corrected" for field in IDENTITY_FIELDS}
    result = save(storage, {**changes, "영업이익": -9})
    assert all(result[field] == changes[field] for field in IDENTITY_FIELDS)


def test_annual_rows_keep_their_original_years_zero_and_negative_values(storage):
    storage[0][BUSINESS]["재무연도별"] = [{"연도": 2024, "당기순이익": 9}]
    result = save(storage, {"재무연도별": [
        {"연도": 2025, "당기순이익": -8, "자산총계": 0},
        {"연도": 2023, "당기순이익": 0},
    ]})
    # This narrow identity fix does not introduce financial-history merging.
    assert [row["연도"] for row in result["재무연도별"]] == [2025, 2023]
    assert [row["당기순이익"] for row in result["재무연도별"]] == [-8, 0]
    assert result["재무연도별"][0]["자산총계"] == 0


def test_malformed_existing_snapshot_is_not_replaced(storage):
    cache, writer, cloud = storage
    cache[BUSINESS] = ["synthetic-malformed"]
    assert not module.save_cretop_financial_snapshot("synthetic-owner", {"사업자등록번호": BUSINESS})
    writer.assert_not_called()
    cloud.assert_not_called()
