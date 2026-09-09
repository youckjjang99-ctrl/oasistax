"""Synthetic-only integration checks for versioned CRM saves and the editor."""
from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy

import pytest

import cloud_sync
import crm
import crm_cloud_store
import crm_editor
import crm_sync_protocol as protocol
import sync_outbox


OWNER = "synthetic-owner"
# Synthetic fixture only; these parts do not originate from customer records.
SYNTHETIC_BUSINESS_PARTS = ("111", "11", "11111")
BIZ = "-".join(SYNTHETIC_BUSINESS_PARTS)
KEY = "biz:" + "".join(SYNTHETIC_BUSINESS_PARTS)


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(crm, "USER_DATA_DIR", tmp_path / "crm")
    monkeypatch.setattr(cloud_sync, "_queue_path", lambda owner: tmp_path / owner / "queue.json")
    monkeypatch.setattr(crm_cloud_store, "cloud_is_configured", lambda: False)
    monkeypatch.setattr(sync_outbox, "cloud_is_configured", lambda: False)
    monkeypatch.setattr(sync_outbox, "durable_outbox_enabled", lambda: False)
    crm._load_crm_data_cached.cache_clear()
    return tmp_path


def snapshot(**changes):
    value = {
        "company_name": "synthetic", "business_no": BIZ,
        "status": "상담중", "memo": "sent", "next_action": "전화", "next_date": "",
        "timeline": [], "_local_revision": 1, "_cloud_version": 3,
        "_cloud_base": {"memo": "old", "status": "상담중", "timeline": []},
        "_sync_state": "pending",
    }
    value.update(changes)
    return value


def seed(record=None, *, owner=OWNER):
    record = snapshot() if record is None else record
    crm.save_crm_data(owner, {"customers": {KEY: deepcopy(record)}})
    return deepcopy(record)


class FakeDatabase:
    def __init__(self, response, before_response=None):
        self.response = response
        self.before_response = before_response
        self.calls = []

    def rpc(self, name, parameters):
        self.calls.append((name, deepcopy(parameters)))
        if self.before_response:
            self.before_response()
        if isinstance(self.response, Exception):
            raise self.response
        response = deepcopy(self.response)
        if isinstance(response, dict):
            response.setdefault("owner_user_id", parameters["p_owner_user_id"])
            response.setdefault("request_id", parameters["p_request_id"])
        return response


def test_request_id_is_stable_and_payload_excludes_local_metadata():
    record = snapshot(_cloud_latest={"secret": "local-only"}, _sync_error="local-error")
    first = protocol.make_save_parameters(OWNER, BIZ, record)
    changed_order = dict(reversed(list(record.items())))
    changed_order.update({"updated_at": "new-clock", "_local_revision": 90, "_sync_state": "retry"})
    second = protocol.make_save_parameters(OWNER, BIZ, changed_order)
    assert first == second
    assert first["p_expected_version"] == 3
    assert first["p_patch"]["memo"] == "sent"
    assert not set(first["p_patch"]).intersection(protocol.LOCAL_KEYS | protocol.VOLATILE_KEYS)
    assert "local-only" not in protocol.canonical(first)
    assert first["p_request_id"] != protocol.make_save_parameters("other-owner", BIZ, record)["p_request_id"]


def test_explicit_empty_fields_are_patches_but_missing_fields_are_not_deletions():
    record = snapshot(memo="", _cloud_base={"memo": "old", "unknown": "keep"})
    parameters = protocol.make_save_parameters(OWNER, BIZ, record)
    assert parameters["p_patch"]["memo"] == ""
    assert "unknown" not in parameters["p_patch"]


def test_only_new_events_are_sent_and_existing_full_fields_survive():
    old = {"id": "existing", "at": "2026-01-01", "title": "old", "detail": "kept", "attachment": {"id": "legacy"}}
    new = {"id": "new", "at": "2026-02-01", "title": "new", "detail": "added"}
    record = snapshot(timeline=[new, old], _cloud_base={"timeline": [old]})
    assert protocol.make_save_parameters(OWNER, BIZ, record)["p_events"] == [new]
    assert protocol.merge_events([old], [old])[0] == old


def test_legacy_event_variants_with_extra_fields_are_not_lost():
    one = {"at": "2026-01-01", "title": "call", "detail": "same", "source": "journal-a"}
    two = {**one, "source": "journal-b"}
    assert len(protocol.merge_events([one, two])) == 2


def test_existing_identical_legacy_occurrences_and_new_delta_are_preserved():
    event = {"at": "2026-01-01", "title": "call", "detail": "same"}
    assert protocol.merge_events([event, event], [event]) == [event, event]
    record = snapshot(timeline=[event, event], _cloud_base={"timeline": [event]})
    assert protocol.make_save_parameters(OWNER, BIZ, record)["p_events"] == [event]


def test_later_identical_legacy_event_stays_pending_after_ack():
    event = {"at": "2026-01-01", "title": "call", "detail": "same"}
    sent = snapshot(timeline=[event])
    current = snapshot(timeline=[event, event], _local_revision=2)
    response = {"status": "applied", "version": 4, "crm_data": protocol.cloud_content(sent)}
    result = protocol.acknowledge_record(current, sent, response)
    assert result["timeline"] == [event, event]
    assert result["_sync_state"] == "pending"


@pytest.mark.parametrize("identity", [
    {"owner_user_id": "other-owner"}, {"request_id": "wrong-request"},
])
def test_response_must_belong_to_requested_owner_and_operation(identity):
    sent = seed()
    response = {"status": "applied", "version": 4, "crm_data": protocol.cloud_content(sent), **identity}
    assert not crm_cloud_store.save_crm_to_cloud(OWNER, BIZ, sent, db=FakeDatabase(response))[0]
    current = crm.get_customer_record(OWNER, KEY)
    assert current["memo"] == "sent"
    assert current["_cloud_version"] == 3


def test_ack_preserves_later_local_edits_events_and_cloud_only_fields():
    sent = snapshot()
    event = {"id": "later-event", "at": "2026-01-02", "title": "later", "detail": "new"}
    current = snapshot(memo="edited-during-request", timeline=[event], _local_revision=2)
    response = {"status": "applied", "version": 4, "crm_data": {**protocol.cloud_content(sent), "server_extra": {"keep": True}}}
    result = protocol.acknowledge_record(current, sent, response)
    assert result["memo"] == "edited-during-request"
    assert result["server_extra"] == {"keep": True}
    assert result["timeline"] == [event]
    assert result["_sync_state"] == "pending"
    assert result["_cloud_base"]["memo"] == "sent"
    assert result["_cloud_version"] == 4
    assert result["_local_revision"] > 2


def test_ack_preserves_later_legacy_plural_timeline_events():
    sent = snapshot()
    later = {"id": "later-legacy", "at": "2026-01-02", "title": "later", "detail": "new"}
    current = snapshot(timelines=[later], _local_revision=2)
    response = {"status": "applied", "version": 4, "crm_data": protocol.cloud_content(sent)}
    result = protocol.acknowledge_record(current, sent, response)
    assert later in protocol.merge_events(result.get("timeline"), result.get("timelines"))
    assert result["_sync_state"] == "pending"


def test_version_conflict_keeps_local_draft_and_exposes_remote_without_queue_retry():
    sent = seed()
    db = FakeDatabase({"status": "conflict", "version": 4, "crm_data": {"memo": "other-device", "timeline": []}})
    ok, message = crm_cloud_store.save_crm_to_cloud(OWNER, BIZ, sent, db=db)
    current = crm.get_customer_record(OWNER, KEY)
    assert not ok
    assert "충돌" in message
    assert current["memo"] == "sent"
    assert current["_cloud_latest"]["memo"] == "other-device"
    assert current["_cloud_latest_version"] == 4
    assert current["_sync_state"] == "conflict"
    assert sync_outbox.load_local_outbox(cloud_sync._queue_path(OWNER)) == []


@pytest.mark.parametrize("response", [
    {"status": "applied"},
    {"status": "applied", "version": 0, "crm_data": {}},
    {"status": "applied", "version": 4, "crm_data": []},
    {"status": "applied", "version": True, "crm_data": {}},
    {"status": "applied", "version": 4},
    {"status": "applied", "version": 4, "crm_data": {"timeline": {}}},
    {"status": "applied", "version": 4, "crm_data": {"timeline": ["invalid-event"]}},
    {"status": "applied", "version": 4, "crm_data": {"timeline": [], "timelines": None}},
    {"status": "applied", "version": 4, "crm_data": {"timelines": [None]}},
])
def test_incomplete_or_invalid_ack_never_claims_success_or_overwrites_local(response):
    sent = seed()
    db = FakeDatabase(response)
    ok, _ = crm_cloud_store.save_crm_to_cloud(OWNER, BIZ, sent, db=db)
    assert not ok
    assert crm.get_customer_record(OWNER, KEY)["memo"] == "sent"
    assert crm.get_customer_record(OWNER, KEY)["_cloud_version"] == 3
    jobs = sync_outbox.load_local_outbox(cloud_sync._queue_path(OWNER))
    assert len(jobs) == 1
    assert jobs[0]["payload"]["parameters"] == db.calls[0][1]


def test_ack_during_later_local_save_preserves_new_draft_and_owner_scope():
    sent = seed()
    seed(snapshot(memo="other-owner-private"), owner="other-owner")

    def edit_during_request():
        ok, _ = crm.upsert_customer_record(OWNER, KEY, memo="later-local", expected_revision=1)
        assert ok

    db = FakeDatabase({"status": "applied", "version": 4, "crm_data": protocol.cloud_content(sent)}, edit_during_request)
    ok, message = crm_cloud_store.save_crm_to_cloud(OWNER, BIZ, sent, db=db)
    assert not ok
    assert "이전 요청은 클라우드에 저장" in message
    assert "동기화 대기" in message
    current = crm.get_customer_record(OWNER, KEY)
    assert current["memo"] == "later-local"
    assert current["_sync_state"] == "pending"
    assert crm.get_customer_record("other-owner", KEY)["memo"] == "other-owner-private"
    assert sync_outbox.load_local_outbox(cloud_sync._queue_path(OWNER)) == []


def test_append_during_cloud_save_is_reported_as_pending_without_requeue():
    sent = seed()

    def append_during_request():
        assert crm.append_timeline_event(OWNER, KEY, "later", "extra-event")[0]

    db = FakeDatabase({"status": "applied", "version": 4, "crm_data": protocol.cloud_content(sent)}, append_during_request)
    ok, message = crm_cloud_store.save_crm_to_cloud(OWNER, BIZ, sent, db=db)
    assert not ok
    assert "이전 요청은 클라우드에 저장" in message
    assert "동기화 대기" in message
    current = crm.get_customer_record(OWNER, KEY)
    assert current["_cloud_version"] == 4
    assert current["_sync_state"] == "pending"
    assert current["timeline"][0]["detail"] == "extra-event"
    assert sync_outbox.load_local_outbox(cloud_sync._queue_path(OWNER)) == []


@pytest.mark.parametrize("status", ["applied", "conflict"])
def test_late_response_cannot_regress_a_newer_acknowledged_version(status):
    old_sent = snapshot(memo="older", _cloud_version=3)
    current = seed(snapshot(memo="latest", _cloud_version=5, _cloud_base={"memo": "latest"}, _sync_state="synced"))
    crm_cloud_store._remember_result(OWNER, BIZ, old_sent, {"status": status, "version": 4, "crm_data": {"memo": "older", "timeline": []}})
    saved = crm.get_customer_record(OWNER, KEY)
    assert saved["_cloud_version"] == 5
    assert saved["_cloud_base"] == current["_cloud_base"]
    assert saved["_sync_state"] == "synced"
    assert saved["memo"] == "latest"


@pytest.mark.parametrize("status", ["applied", "conflict"])
def test_late_response_cannot_regress_newer_observed_conflict_version(status):
    current = seed(snapshot(
        memo="local-draft", _cloud_version=3, _sync_state="conflict",
        _cloud_latest={"memo": "newest-remote", "timeline": []},
        _cloud_latest_version=10,
    ))
    sent = snapshot(memo="old-request", _cloud_version=3)
    returned_state = crm_cloud_store._remember_result(
        OWNER, BIZ, sent,
        {"status": status, "version": 9, "crm_data": {"memo": "older-remote", "timeline": []}},
    )
    assert returned_state == "conflict"
    assert crm.get_customer_record(OWNER, KEY) == current
    assert protocol.known_cloud_version(current) == 10


def test_pure_ack_fences_against_observed_conflict_version():
    current = snapshot(_cloud_version=3, _cloud_latest_version=10, _sync_state="conflict", _cloud_latest={"memo": "known-newest"})
    response = {"status": "applied", "version": 9, "crm_data": {"memo": "out-of-order"}}
    result = protocol.acknowledge_record(current, snapshot(), response)
    assert result == current
    assert result is not current


@pytest.mark.parametrize("field,first,last", [
    ("memo", "A", "C"),
    ("_v44_profile", {"priority": "3"}, {"priority": "5"}),
])
def test_aba_edit_during_lost_ack_preserves_full_local_draft(field, first, last):
    sent = snapshot(**{field: first})
    event = {"id": "draft-only", "at": "2026-01-01", "detail": "preserved"}
    current = snapshot(**{field: first}, _local_revision=3, timeline=[event], unknown={"keep": True})
    before = deepcopy(current)
    remote = protocol.cloud_content(sent)
    remote[field] = last
    result = protocol.acknowledge_record(current, sent, {
        "status": "applied", "version": 5, "crm_data": remote, "replayed": True,
    })
    assert result[field] == first
    assert result["timeline"] == [event]
    assert result["unknown"] == {"keep": True}
    assert result["_cloud_base"] == before["_cloud_base"]
    assert result["_cloud_version"] == 3
    assert result["_cloud_latest"][field] == last
    assert result["_cloud_latest_version"] == 5
    assert result["_local_revision"] == 4
    assert result["_sync_state"] == "conflict"
    assert current == before


def test_aba_replayed_ack_never_shows_success_or_requeues_confirmed_request():
    sent = snapshot(memo="A", _local_revision=1)
    seed(snapshot(memo="A", _local_revision=3))
    remote = protocol.cloud_content(sent)
    remote["memo"] = "C"
    database = FakeDatabase({"status": "applied", "version": 5, "crm_data": remote, "replayed": True})
    ok, message = crm_cloud_store.save_crm_to_cloud(OWNER, BIZ, sent, db=database)
    assert not ok
    assert "초안" in message
    current = crm.get_customer_record(OWNER, KEY)
    assert current["memo"] == "A"
    assert current["_sync_state"] == "conflict"
    assert current["_cloud_latest"]["memo"] == "C"
    assert sync_outbox.load_local_outbox(cloud_sync._queue_path(OWNER)) == []


def test_replayed_ack_can_refresh_remote_when_no_local_revision_changed():
    sent = snapshot(memo="A")
    remote = protocol.cloud_content(sent)
    remote["memo"] = "C"
    result = protocol.acknowledge_record(deepcopy(sent), sent, {
        "status": "applied", "version": 5, "crm_data": remote, "replayed": True,
    })
    assert result["memo"] == "C"
    assert result["_sync_state"] == "synced"


def test_same_version_replay_does_not_clear_an_unresolved_local_conflict():
    sent = snapshot(memo="draft")
    current = snapshot(memo="draft", _sync_state="conflict", _cloud_latest={"memo": "remote"}, _cloud_latest_version=5)
    result = protocol.acknowledge_record(current, sent, {"status": "applied", "version": 5, "crm_data": {"memo": "remote", "timeline": []}})
    assert result["memo"] == "draft"
    assert result["_sync_state"] == "conflict"
    assert result["_cloud_latest"]["memo"] == "remote"


def test_lost_response_replays_identical_operation_without_unversioned_upsert():
    sent = seed()
    requests = {}
    calls = []
    successful_applies = 0

    class LostResponseDatabase:
        def rpc(self, name, parameters):
            nonlocal successful_applies
            assert name == protocol.CRM_SAVE_RPC
            calls.append(deepcopy(parameters))
            key = parameters["p_request_id"]
            if key not in requests:
                successful_applies += 1
                requests[key] = {
                    "status": "applied", "version": 4,
                    "crm_data": protocol.cloud_content(sent),
                    "owner_user_id": parameters["p_owner_user_id"],
                    "request_id": key,
                }
                raise TimeoutError("synthetic_response_lost")
            return requests[key]

    db = LostResponseDatabase()
    assert not crm_cloud_store.save_crm_to_cloud(OWNER, BIZ, sent, db=db)[0]
    assert crm.get_customer_record(OWNER, KEY)["_sync_state"] == "pending"
    path = cloud_sync._queue_path(OWNER)

    def never_upsert(*_args):
        pytest.fail("versioned CRM must never use an unguarded upsert")

    result = sync_outbox.retry_local_outbox(path, never_upsert, rpc=db.rpc)
    assert result["success"] == 1
    assert successful_applies == 1
    assert calls[0] == calls[1]
    assert sync_outbox.load_local_outbox(path)[0]["status"] == "complete"


def test_old_unguarded_crm_outbox_job_is_preserved_without_execution():
    path = cloud_sync._queue_path(OWNER)
    job = sync_outbox.make_outbox_job(OWNER, "crm", "oasis_crm", [{"owner_user_id": OWNER, "business_no": BIZ, "crm_data": {"memo": "old"}}], "owner_user_id,business_no")
    sync_outbox.enqueue_local_outbox(path, job)
    payload = deepcopy(job["payload"])
    result = sync_outbox.retry_local_outbox(path, lambda *_: pytest.fail("legacy CRM must be held for review"))
    stored = sync_outbox.load_local_outbox(path)[0]
    assert result["dead_letter"] == 1
    assert stored["last_error_code"] == "crm_review_required"
    assert stored["payload"] == payload


def test_outbox_rejects_cross_owner_versioned_rpc_before_execution():
    parameters = protocol.make_save_parameters("other-owner", BIZ, snapshot())
    payload = {"operation": "rpc", "function_name": protocol.CRM_SAVE_RPC, "parameters": parameters}
    with pytest.raises(ValueError):
        sync_outbox._dispatch_outbox_payload(payload, lambda *_: pytest.fail("no upsert"), lambda *_: pytest.fail("cross-owner RPC executed"), expected_owner_user_id=OWNER)


def test_outbox_does_not_complete_unconfirmed_versioned_ack():
    payload = {"operation": "rpc", "function_name": protocol.CRM_SAVE_RPC, "parameters": protocol.make_save_parameters(OWNER, BIZ, snapshot())}
    with pytest.raises(RuntimeError):
        sync_outbox._dispatch_outbox_payload(payload, lambda *_: None, lambda *_: {"status": "applied"}, expected_owner_user_id=OWNER)


class Rerun(Exception):
    pass


class FakeStreamlit:
    def __init__(self):
        self.session_state = {}
        self.pressed = set()
        self.rendered = []

    def button(self, _label, *, key):
        return key in self.pressed

    def warning(self, value):
        self.rendered.append(value)

    def caption(self, value):
        self.rendered.append(value)

    def write(self, value):
        self.rendered.append(value)

    def expander(self, *_args, **_kwargs):
        return nullcontext()

    def rerun(self):
        raise Rerun


def test_editor_revision_remains_frozen_across_reruns_until_explicit_reload():
    st = FakeStreamlit()
    first_widget, first_revision = crm_editor.editor_guard(st, OWNER, KEY, snapshot(_local_revision=1), "test")
    second_widget, second_revision = crm_editor.editor_guard(st, OWNER, KEY, snapshot(_local_revision=4), "test")
    assert (first_widget, first_revision) == (second_widget, second_revision)
    st.pressed.add(f"crm-edit:test:{OWNER}:{KEY}:reload")
    with pytest.raises(Rerun):
        crm_editor.editor_guard(st, OWNER, KEY, snapshot(_local_revision=4), "test")
    st.pressed.clear()
    new_widget, new_revision = crm_editor.editor_guard(st, OWNER, KEY, snapshot(_local_revision=4), "test")
    assert new_revision == 4
    assert new_widget != first_widget


def test_conflict_adoption_preserves_old_draft_and_other_customer_records():
    before = seed(snapshot(_sync_state="conflict", _cloud_latest={"memo": "cloud-latest", "timeline": []}, _cloud_latest_version=4))
    crm.mutate_crm_data(OWNER, lambda data: data["customers"].update({"company:unrelated": {"memo": "keep"}}))
    st = FakeStreamlit()
    st.pressed.add(f"crm-edit:test:{OWNER}:{KEY}:cloud")
    with pytest.raises(Rerun):
        crm_editor.editor_guard(st, OWNER, KEY, before, "test")
    data = crm.load_crm_data(OWNER)
    assert data["customers"][KEY]["memo"] == "cloud-latest"
    assert data["customers"][KEY]["_cloud_version"] == 4
    assert data["customers"]["company:unrelated"] == {"memo": "keep"}
    assert data["_local_conflicts"][0]["attempted_changes"]["memo"] == "sent"
