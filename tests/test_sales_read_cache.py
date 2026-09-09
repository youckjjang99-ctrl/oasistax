from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock

import pytest
import streamlit as st

import company_sales_assignment as sales
import direct_sales_customer_repository as direct
import performance_cache
import prospect_db_center as prospect
import sales_read_cache as cache
import work_inbox
import work_task_repository as tasks

OWNER = "cache-owner-a"
OTHER = "cache-owner-b"
UID = "source:" + "a" * 64


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    cache._CACHE.clear()
    performance_cache._GENERATIONS.clear()
    token = cache._SCOPE.set(None)
    access = Mock(side_effect=lambda owner: ({"user_id": owner, "status": "approved", "role": "member"}, "ok"))
    monkeypatch.setattr(cache, "_load_access", access)
    monkeypatch.setattr(st, "session_state", {})
    yield access
    cache._SCOPE.reset(token)
    cache._CACHE.clear()
    performance_cache._GENERATIONS.clear()


def counts(value=1):
    return {"ok": True, "metrics": {"total_count": value}}


def test_actor_checked_per_render_not_per_cached_summary(isolated):
    loader = Mock(return_value=counts())
    @cache.scoped_render("owner")
    def render(owner):
        return [cache.count_summary("dashboard", owner, loader),
                cache.count_summary("dashboard", owner, loader)]
    render(OWNER)
    assert isolated.call_count == 1 and loader.call_count == 1
    render(OWNER)
    assert isolated.call_count == 2 and loader.call_count == 1


@pytest.mark.parametrize("access", [({}, "not_found"), ({}, "unavailable"),
    ({"user_id": OWNER, "status": "suspended", "role": "member"}, "ok"),
    ({"user_id": OTHER, "status": "approved", "role": "admin"}, "ok")])
def test_warm_cache_never_bypasses_fresh_access(monkeypatch, access):
    loader = Mock(return_value=counts())
    cache.count_summary("dashboard", OWNER, loader)
    monkeypatch.setattr(cache, "_load_access", lambda owner: access)
    assert cache.count_summary("dashboard", OWNER, loader)["ok"] is False
    assert loader.call_count == 1


def test_role_change_separates_cache(monkeypatch):
    loader = Mock(return_value=counts())
    cache.count_summary("dashboard", OWNER, loader)
    monkeypatch.setattr(cache, "_load_access", lambda owner: ({"user_id": owner, "status": "approved", "role": "admin"}, "ok"))
    cache.count_summary("dashboard", OWNER, loader)
    assert loader.call_count == 2


def test_user_mutation_keeps_unrelated_user_warm_and_inventory_refreshes():
    load_a, load_b, inventory_b = [Mock(return_value=counts()) for _ in range(3)]
    cache.count_summary("user", OWNER, load_a)
    cache.count_summary("user", OTHER, load_b)
    cache.count_summary("inventory", OTHER, inventory_b, inventory=True)
    cache.invalidate_sales_reads(OWNER, inventory=True)
    cache.count_summary("user", OWNER, load_a)
    cache.count_summary("user", OTHER, load_b)
    cache.count_summary("inventory", OTHER, inventory_b, inventory=True)
    assert (load_a.call_count, load_b.call_count, inventory_b.call_count) == (2, 1, 2)


@pytest.mark.parametrize("bad", [{"ok": False}, {"ok": True, "metrics": {}, "warning": "partial"},
    {"ok": True, "metrics": {"total": "unknown"}}, {"ok": True, "metrics": {"total": -1}},
    {"ok": True, "metrics": {"total": True}}, {"ok": True, "metrics": {}, "warnings": ["partial"]}])
def test_failures_and_partial_results_are_not_cached(bad):
    loader = Mock(side_effect=[bad, counts(2)])
    cache.count_summary("dashboard", OWNER, loader)
    assert cache.count_summary("dashboard", OWNER, loader) == counts(2)
    assert loader.call_count == 2


def test_ttl_expiry_copy_isolation_and_counts_only(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock[0])
    loader = Mock(return_value={**counts(), "notes": "must not persist", "source_data": {"private": True}})
    result = cache.count_summary("dashboard", OWNER, loader)
    result["metrics"]["total_count"] = 999
    assert cache.count_summary("dashboard", OWNER, loader) == counts()
    assert all(set(entry[1]) == {"ok", "metrics"} for entry in cache._CACHE.values())
    clock[0] += 20
    cache.count_summary("dashboard", OWNER, loader)
    assert loader.call_count == 2


def test_mutation_during_summary_load_does_not_store_stale_snapshot():
    def load():
        cache.invalidate_sales_reads(OWNER)
        return counts()
    cache.count_summary("dashboard", OWNER, load)
    assert not cache._CACHE


MUTATIONS = [
    ("claim_company", {"current_user_id": OWNER, "company_id": "entity", "company_uid": UID}),
    ("claim_and_save_company", {"current_user_id": OWNER, "company_uid": UID, "company_payload": {}}),
    ("record_contact", {"current_user_id": OWNER, "company_id": "entity", "company_uid": UID, "contact_method": "phone", "contact_result": "연결됨"}),
    ("release_assignment", {"current_user_id": OWNER, "company_id": "entity", "company_uid": UID, "reason": "review"}),
    ("save_user_note", {"current_user_id": OWNER, "company_uid": UID, "memo": "synthetic note"}),
    ("admin_set_user_limit", {"admin_user_id": "admin", "target_user_id": OWNER, "max_uncontacted": 30, "max_landline_db": 30, "max_mobile_db": 30, "reason": "review"}),
    ("submit_mobile_db_request", {"current_user_id": OWNER, "region": "region"}),
    ("submit_specific_company_db_request", {"current_user_id": OWNER, "business_no": "000" + "00" + "00000"}),
]


@pytest.mark.parametrize("name,arguments", MUTATIONS, ids=[value[0] for value in MUTATIONS])
@pytest.mark.parametrize("ok", [True, False])
def test_repository_mutations_invalidate_only_success(monkeypatch, name, arguments, ok):
    monkeypatch.setattr(sales, "_rpc", lambda *args, **kwargs: ({"success": ok, "code": "OK"}, None))
    before_a, before_b = cache.generation_key(OWNER), cache.generation_key(OTHER)
    result = getattr(sales, name)(**arguments)
    assert result["ok"] is ok
    assert (cache.generation_key(OWNER) != before_a) is ok
    assert cache.generation_key(OTHER) == before_b


@pytest.mark.parametrize("name,extra", [
    ("admin_change_assignee", {"new_assigned_user_id": OTHER}),
    ("admin_release_assignment", {}), ("admin_reactivate", {}), ("admin_permanent_exclude", {}),
])
def test_admin_mutations_without_old_owner_invalidate_both(monkeypatch, name, extra):
    monkeypatch.setattr(sales, "_rpc", lambda *args, **kwargs: ({"success": True, "code": "OK"}, None))
    before = [cache.generation_key(owner) for owner in (OWNER, OTHER)]
    result = getattr(sales, name)("admin", "entity", UID, reason="review", **extra)
    assert result["ok"]
    assert all(cache.generation_key(owner) != old for owner, old in zip((OWNER, OTHER), before))


@pytest.mark.parametrize("name,extra", [("record_contact", {"contact_method": "phone", "contact_result": "연결됨"}),
                                       ("release_assignment", {"reason": "review"})])
def test_admin_acting_through_shared_mutation_invalidates_unknown_owner(monkeypatch, name, extra):
    monkeypatch.setattr(cache, "_load_access", lambda owner: ({"user_id": owner, "status": "approved", "role": "admin"}, "ok"))
    monkeypatch.setattr(sales, "_rpc", lambda *args, **kwargs: ({"success": True}, None))
    old = cache.generation_key(OTHER)
    getattr(sales, name)("admin", "entity", UID, **extra)
    assert cache.generation_key(OTHER) != old


@pytest.mark.parametrize("released", [0, 2])
def test_expiry_invalidates_all_only_when_rows_released(monkeypatch, released):
    monkeypatch.setattr(sales, "_rpc", lambda *args, **kwargs: (released, None))
    before = cache.generation_key(OTHER)
    sales.release_expired_assignments(OWNER)
    assert (cache.generation_key(OTHER) != before) is bool(released)


@pytest.mark.parametrize("owner", [OWNER, ""])
def test_admin_request_uses_safe_returned_owner_or_global_fallback(monkeypatch, owner):
    monkeypatch.setattr(sales, "_rpc", lambda *args, **kwargs: ({"success": True, "requested_user_id": owner}, None))
    before_a, before_b = cache.generation_key(OWNER), cache.generation_key(OTHER)
    sales.admin_update_mobile_db_request("admin", "request", "approve")
    assert cache.generation_key(OWNER) != before_a
    assert (cache.generation_key(OTHER) != before_b) is (not bool(owner))


def test_batch_partial_success_invalidates_successful_claim(monkeypatch):
    responses = iter([({"success": True}, None), ({"success": False}, None)])
    monkeypatch.setattr(sales, "_rpc", lambda *args, **kwargs: next(responses))
    old = cache.generation_key(OWNER)
    sales.claim_and_save_companies(OWNER, [{"company_uid": UID}, {"company_uid": "source:" + "b" * 64}], db=object())
    assert cache.generation_key(OWNER) != old


def inbox():
    return {"ok": True, "items": [], "summary": {"today_count": 1}, "warnings": []}


def test_other_browser_work_inbox_invalidated_by_owner_generation(monkeypatch):
    browser_a, browser_b = {}, {}
    build = Mock(return_value=inbox())
    monkeypatch.setattr(work_inbox, "build_work_inbox", build)
    for browser in (browser_a, browser_b):
        monkeypatch.setattr(st, "session_state", browser)
        work_inbox.get_cached_work_inbox(OWNER)
    monkeypatch.setattr(st, "session_state", browser_a)
    work_inbox.invalidate_work_inbox_cache(OWNER)
    monkeypatch.setattr(st, "session_state", browser_b)
    work_inbox.get_cached_work_inbox(OWNER)
    assert build.call_count == 3


@pytest.mark.parametrize("namespace", ["crm", "sales_reads", "work_inbox"])
def test_work_inbox_uses_each_owner_generation(monkeypatch, namespace):
    build = Mock(return_value=inbox())
    monkeypatch.setattr(work_inbox, "build_work_inbox", build)
    work_inbox.get_cached_work_inbox(OWNER)
    performance_cache.invalidate_cache(namespace, OWNER)
    work_inbox.get_cached_work_inbox(OWNER)
    assert build.call_count == 2


def test_failed_or_partial_work_inbox_never_cached(monkeypatch):
    partial = {**inbox(), "ok": False, "warnings": ["partial"]}
    build = Mock(side_effect=[partial, inbox()])
    monkeypatch.setattr(work_inbox, "build_work_inbox", build)
    assert work_inbox.get_cached_work_inbox(OWNER)["ok"] is False
    assert work_inbox.get_cached_work_inbox(OWNER)["ok"] is True
    assert build.call_count == 2


def test_work_inbox_date_boundary_and_access_revocation(monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 9, 9, 23, 59, 59, tzinfo=work_inbox.SEOUL)
        @classmethod
        def now(cls, tz=None):
            return cls.current
    monkeypatch.setattr(work_inbox, "datetime", Clock)
    build = Mock(return_value=inbox())
    monkeypatch.setattr(work_inbox, "build_work_inbox", build)
    work_inbox.get_cached_work_inbox(OWNER)
    Clock.current = datetime(2026, 9, 10, 0, 0, 0, tzinfo=work_inbox.SEOUL)
    work_inbox.get_cached_work_inbox(OWNER)
    assert build.call_count == 2
    monkeypatch.setattr(cache, "_load_access", lambda owner: ({}, "not_found"))
    assert work_inbox.get_cached_work_inbox(OWNER)["items"] == []
    assert work_inbox._WORK_INBOX_CACHE_KEY not in st.session_state


def test_non_ui_work_transition_invalidates_inbox(monkeypatch):
    monkeypatch.setattr(tasks, "_rpc", lambda *args, **kwargs: ({"success": True, "code": "COMPLETED"}, None))
    old = performance_cache.cache_generation("work_inbox", OWNER)
    result = tasks.complete_work_task(OWNER, "00000000-0000-0000-0000-000000000001", 1)
    assert result["ok"]
    assert performance_cache.cache_generation("work_inbox", OWNER) > old


def test_details_and_contacts_remain_uncached():
    for function in (prospect._load_user_dashboard_assignment_rows, prospect._load_user_assignment_rows,
                     sales.list_company_contacts, sales.list_admin_assignment_audit):
        assert not hasattr(function, "clear")
    assert not cache._CACHE


@pytest.mark.parametrize("kind", ["direct", "inventory", "user"])
@pytest.mark.parametrize("malformed", ["empty", "missing", "multiple", "bool", "negative", "text", "float", "null"])
def test_count_services_reject_malformed_before_cache(kind, malformed):
    fields = {"direct": ("total_count", "registered_count", "contracted_count"),
              "inventory": tuple(sorted(sales._ASSIGNABLE_DB_INVENTORY_DASHBOARD_FIELDS)),
              "user": tuple(sorted(sales._USER_DB_DASHBOARD_FIELDS))}[kind]
    row = {field: 0 for field in fields}
    raw = [row]
    if malformed == "empty":
        raw = []
    elif malformed == "missing":
        row.pop(fields[0])
    elif malformed == "multiple":
        raw.append(dict(row))
    else:
        row[fields[0]] = {"bool": True, "negative": -1, "text": "0", "float": 0.0, "null": None}[malformed]
    database = Mock()
    database.rpc.side_effect = [raw, [{field: 0 for field in fields}]]
    function = {"direct": direct.get_direct_customer_summary,
                "inventory": sales.get_assignable_db_inventory_dashboard,
                "user": sales.get_user_db_dashboard}[kind]
    loader = lambda: function(OWNER, db=database)
    assert cache.count_summary(kind, OWNER, loader)["ok"] is False
    assert not cache._CACHE
    assert cache.count_summary(kind, OWNER, loader)["ok"] is True
    assert database.rpc.call_count == 2


@pytest.mark.parametrize("ok", [True, False])
def test_non_ui_direct_customer_save_invalidates_only_successful_owner(ok):
    database = Mock()
    database.rpc.return_value = [{"success": ok, "code": "REGISTERED" if ok else "INVALID_REQUEST"}]
    loaders = {owner: Mock(return_value={"ok": True, "total": 1, "registered": 1, "contracted": 0})
               for owner in (OWNER, OTHER)}
    for owner, loader in loaders.items():
        cache.count_summary("direct_customer_summary", owner, loader)
    result = direct.register_direct_customer(OWNER, {"company_name": "Synthetic"}, db=database)
    assert result["ok"] is ok
    for owner, loader in loaders.items():
        cache.count_summary("direct_customer_summary", owner, loader)
    assert loaders[OWNER].call_count == (2 if ok else 1)
    assert loaders[OTHER].call_count == 1
