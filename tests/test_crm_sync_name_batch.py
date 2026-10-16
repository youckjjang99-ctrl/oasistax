"""Synthetic-only name batching; no production data or network requests."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import uuid

import pytest

import cloud_crm_restore as pull
import cloud_db
import crm


OWNER = "synthetic-name-owner"
STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def customer_name(key, name="synthetic-company", owner=OWNER):
    return {"owner_user_id": owner, "business_no": key, "company_name": name}


def crm_row(index, name=None):
    key = f"synthetic-business-{index}"
    content = {"business_no": key, "memo": "preserve", "timeline": []}
    if name is not None:
        content["company_name"] = name
    return {"id": str(uuid.UUID(int=index)), "owner_user_id": OWNER,
            "business_no": key, "crm_version": 1,
            "updated_at": (STAMP + timedelta(seconds=index)).isoformat(),
            "crm_data": content}


@pytest.fixture
def http(monkeypatch):
    client = Mock()
    monkeypatch.setattr(cloud_db, "_http_client", lambda: client)
    database = cloud_db.CloudDatabase(
        cloud_db.CloudConfig("https://example.invalid", "synthetic-test-key"))

    def respond(pages):
        responses = []
        for page in pages:
            response = Mock(ok=True)
            response.json.return_value = page
            responses.append(response)
        client.get.side_effect = responses

    return database, client, respond


def test_exact_owner_scope_and_escaped_keys(http):
    database, client, respond = http
    key = 'synthetic,"quoted"\\key'
    respond([[customer_name(key)], []])
    assert database.select_customer_names(OWNER, [key, key]) == {key: "synthetic-company"}
    params = client.get.call_args_list[0].kwargs["params"]
    assert params["owner_user_id"] == "eq." + OWNER
    assert params["business_no"] == 'in.("synthetic,\\"quoted\\"\\\\key")'
    assert params["select"] == "owner_user_id,business_no,company_name"
    assert params["limit"] == "1" and params["order"] == "id.asc"
    assert client.get.call_args_list[1].kwargs["params"]["offset"] == "1"


def test_many_missing_names_use_bounded_batches(http):
    database, client, respond = http
    keys = [f"synthetic-{index}" for index in range(205)]
    respond([[customer_name(key) for key in keys[:100]], [],
             [customer_name(key) for key in keys[100:200]], [],
             [customer_name(key) for key in keys[200:]], []])
    assert len(database.select_customer_names(OWNER, keys)) == 205
    assert client.get.call_count == 6
    assert [int(call.kwargs["params"]["limit"]) for call in client.get.call_args_list] == [100, 100, 100, 100, 5, 5]
    assert all(call.kwargs["params"]["owner_user_id"] == "eq." + OWNER
               for call in client.get.call_args_list)


def test_server_short_pages_are_not_treated_as_eof(http):
    database, client, respond = http
    respond([[customer_name("synthetic-one")], [customer_name("synthetic-two")], []])
    assert len(database.select_customer_names(OWNER, ["synthetic-one", "synthetic-two"])) == 2
    assert [call.kwargs["params"]["offset"] for call in client.get.call_args_list] == ["0", "1", "2"]


@pytest.mark.parametrize("bad", [
    {"error": "not-list"}, [None],
    [customer_name("synthetic", owner="different-owner")],
    [customer_name("unrequested")],
    [{"owner_user_id": OWNER, "business_no": [], "company_name": "invalid"}],
    [customer_name("synthetic", name={"invalid": True})],
])
def test_malformed_or_foreign_name_rows_fail_closed(http, bad):
    database, _client, respond = http
    respond([bad])
    with pytest.raises(RuntimeError):
        database.select_customer_names(OWNER, ["synthetic"])


def test_ambiguous_identity_across_pages_fails_closed(http):
    database, _client, respond = http
    respond([[customer_name("synthetic", "first")], [customer_name("synthetic", "second")]])
    with pytest.raises(RuntimeError, match="ambiguous_identity"):
        database.select_customer_names(OWNER, ["synthetic"])


def test_empty_request_never_uses_http_and_invalid_inputs_rejected(http):
    database, client, _respond = http
    assert database.select_customer_names(OWNER, []) == {}
    for owner, keys in [("", ["synthetic"]), (OWNER, [""]), (OWNER, [None]), (OWNER, "synthetic")]:
        with pytest.raises(ValueError):
            database.select_customer_names(owner, keys)
    client.get.assert_not_called()


def test_transport_failure_has_no_private_error_text(http):
    database, client, _respond = http
    response = Mock(ok=False, text="private-fixture-error")
    client.get.side_effect = None
    client.get.return_value = response
    with pytest.raises(RuntimeError) as exc:
        database.select_customer_names(OWNER, ["synthetic"])
    assert "private-fixture" not in str(exc.value)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(crm, "USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(pull, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(pull, "load_legacy_profiles", lambda owner: {})
    monkeypatch.setattr(pull.time, "time", lambda: 100000.0)
    database = Mock()
    database.select_crm_sync_page.side_effect = [[]]
    database.select_customer_names.return_value = {}
    monkeypatch.setattr(pull, "CloudDatabase", lambda: database)
    crm._load_crm_data_cached.cache_clear()
    return database


def test_restore_looks_up_only_missing_remote_and_local_names(isolated):
    database = isolated
    rows = [crm_row(1, "remote-name"), crm_row(2), crm_row(3), crm_row(4)]
    local_key = crm.make_customer_key(business_no=rows[1]["business_no"])
    crm.save_crm_data(OWNER, {"customers": {local_key: {
        "business_no": rows[1]["business_no"], "company_name": "local-name",
        "memo": "preserve", "timeline": []}}})
    database.select_crm_sync_page.side_effect = [rows, []]
    database.select_customer_names.return_value = {rows[2]["business_no"]: "batch-name"}
    result = pull.restore_crm_from_cloud(OWNER)
    assert result["ok"]
    database.select_customer_names.assert_called_once_with(
        OWNER, [rows[2]["business_no"], rows[3]["business_no"]])
    database.select.assert_not_called()
    saved = crm.load_crm_data(OWNER)
    assert saved["customers"][crm.make_customer_key(business_no=rows[0]["business_no"])]["company_name"] == "remote-name"
    assert saved["customers"][local_key]["company_name"] == "local-name"
    assert saved["customers"][crm.make_customer_key(business_no=rows[2]["business_no"])]["company_name"] == "batch-name"
    assert "company_name" not in saved["customers"][crm.make_customer_key(business_no=rows[3]["business_no"])]
    assert saved["_cloud_pull"]["checkpoint"]["id"] == rows[-1]["id"]


def test_names_failure_preserves_customer_history_and_checkpoint(isolated):
    database = isolated
    own = {"company_name": "untouched", "memo": "preserve", "timeline": [{"detail": "keep"}]}
    checkpoint = {"id": str(uuid.UUID(int=10)), "updated_at": STAMP.isoformat()}
    crm.save_crm_data(OWNER, {"customers": {"existing": deepcopy(own)},
                              "_cloud_pull": {"checkpoint": checkpoint}})
    database.select_crm_sync_page.side_effect = [[crm_row(1)], []]
    database.select_customer_names.side_effect = RuntimeError("private-fixture-error")
    assert not pull.restore_crm_from_cloud(OWNER)["ok"]
    saved = crm.load_crm_data(OWNER)
    assert saved["customers"] == {"existing": own}
    assert saved["_cloud_pull"]["checkpoint"] == checkpoint


def test_no_missing_names_and_ttl_need_no_name_lookup(isolated):
    database = isolated
    database.select_crm_sync_page.side_effect = [[crm_row(1, "remote-name")], []]
    assert pull.restore_crm_from_cloud(OWNER)["ok"]
    assert pull.restore_crm_from_cloud(OWNER)["status"] == "cached"
    database.select_customer_names.assert_not_called()
    assert database.select_crm_sync_page.call_count == 2
