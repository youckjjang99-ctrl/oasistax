"""Read-only company-scoped valuation history queries using synthetic rows."""
from __future__ import annotations

from unittest.mock import Mock

import pytest

import cloud_sync

OWNER = "synthetic-owner"
BUSINESS = "-".join(("123", "45", "67890"))
DIGITS = "".join(("123", "45", "67890"))
OTHER = "-".join(("987", "65", "43210"))


def row(index=1, business=BUSINESS, owner=OWNER, **payload):
    return {"id": f"row-{index}", "record_id": f"version-{index}",
            "owner_user_id": owner, "business_no": business,
            "created_at": f"2026-10-23T00:{index % 60:02d}:00Z",
            "valuation_data": {"record_id": f"version-{index}", "business_no": business,
                               "owner_user_id": owner, **payload}}


@pytest.fixture
def database(monkeypatch):
    database = Mock()
    database.select.return_value = []
    database.select_all.return_value = []
    monkeypatch.setattr(cloud_sync, "CloudDatabase", lambda: database)
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: True)
    return database


def test_default_owner_query_keeps_legacy_limit(database):
    database.select.return_value = [row()]
    records = cloud_sync.load_stock_valuations(OWNER)
    assert len(records) == 1
    assert database.select.call_args.kwargs["filters"] == {"owner_user_id": OWNER}
    assert database.select.call_args.kwargs["limit"] == 200
    database.select_all.assert_not_called()


def test_company_filter_applies_on_server_before_each_limit(database):
    database.select.side_effect = [[row()], [row()]]
    records = cloud_sync.load_stock_valuations(OWNER, 50, business_no=DIGITS)
    assert len(records) == 1  # Legacy and normalized representations deduplicate.
    assert [call.kwargs["filters"] for call in database.select.call_args_list] == [
        {"owner_user_id": OWNER, "business_no": BUSINESS},
        {"owner_user_id": OWNER, "business_no": DIGITS},
    ]
    assert all(call.kwargs["limit"] == 50 for call in database.select.call_args_list)


def test_full_company_history_reads_past_owner_wide_two_hundred_cap(database):
    database.select_all.side_effect = [[row(index) for index in range(250)], []]
    records = cloud_sync.load_stock_valuations(OWNER, limit=None, business_no=BUSINESS)
    assert len(records) == 250
    database.select.assert_not_called()
    assert all(call.kwargs["order"] == "created_at.desc,id.desc" for call in database.select_all.call_args_list)
    assert all("limit" not in call.kwargs for call in database.select_all.call_args_list)


@pytest.mark.parametrize("bad", [
    row(2, owner="different-owner"),
    row(3, owner_user_id="different-owner"),
    row(4, business_no=OTHER),
    row(5, business=OTHER),
    row(6, record_id="mismatched-version"),
])
def test_wrong_owner_business_or_payload_identity_is_rejected(database, bad):
    database.select_all.side_effect = [[row(), bad], []]
    records = cloud_sync.load_stock_valuations(OWNER, limit=None, business_no=BUSINESS)
    assert [record["record_id"] for record in records] == ["version-1"]


def test_authoritative_row_identity_fills_missing_payload_values(database):
    value = row()
    value["valuation_data"] = {"saved_at": "2026-10-23", "current_shares": 100}
    database.select_all.side_effect = [[value], []]
    records = cloud_sync.load_stock_valuations(OWNER, limit=None, business_no=BUSINESS)
    assert records[0]["record_id"] == value["record_id"]
    assert records[0]["business_no"] == BUSINESS
    assert records[0]["owner_user_id"] == OWNER
    assert records[0]["current_shares"] == 100


def test_unlimited_owner_read_and_legacy_payload_only_records(database):
    database.select_all.return_value = [{"record_id": "legacy", "valuation_data": {"current_shares": 5}}]
    assert cloud_sync.load_stock_valuations(OWNER, None)[0]["record_id"] == "legacy"
    assert database.select_all.call_args.kwargs["filters"] == {"owner_user_id": OWNER}


def test_network_failure_is_safe_empty_result(database):
    database.select_all.side_effect = RuntimeError("synthetic failure")
    assert cloud_sync.load_stock_valuations(OWNER, None, business_no=BUSINESS) == []


@pytest.mark.parametrize("owner,business,limit", [("", BUSINESS, None), (OWNER, "invalid", None), (OWNER, BUSINESS, 0)])
def test_invalid_query_does_not_contact_database(database, owner, business, limit):
    assert cloud_sync.load_stock_valuations(owner, limit, business_no=business) == []
    database.select.assert_not_called()
    database.select_all.assert_not_called()
