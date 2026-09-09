"""Synthetic-only cross-device pulls: never use actual credentials or remote DBs."""
from copy import deepcopy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import uuid

import pytest

import cloud_crm_restore as pull
import cloud_db
import crm
import crm_editor
from crm_cloud_merge import merge_cloud_record
from crm_file_store import CrmStorageError
from crm_sync_protocol import cloud_content

OWNER = "synthetic-sync-owner"
BIZ = "fixture-company"
KEY = crm.make_customer_key(business_no=BIZ)
STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)


def content(**changes):
    value = {"business_no": BIZ, "company_name": "synthetic-fixture", "memo": "original", "timeline": []}
    value.update(changes)
    return value


def record(base=None, **changes):
    base = content() if base is None else deepcopy(base)
    value = deepcopy(base)
    value.update(_cloud_base=deepcopy(base), _cloud_version=1, _local_revision=1, _sync_state="synced")
    value.update(changes)
    return value


def row(number=1, version=1, **changes):
    value = {"id": str(uuid.UUID(int=number)), "owner_user_id": OWNER, "business_no": BIZ,
             "crm_version": version, "updated_at": (STAMP + timedelta(seconds=number)).isoformat(),
             "crm_data": content()}
    value.update(changes)
    return value


class FakeDatabase:
    def __init__(self, pages=(), before_page=None):
        self.pages = list(pages)
        self.calls = []
        self.before_page = before_page

    def select_crm_sync_page(self, owner, **kwargs):
        self.calls.append((owner, deepcopy(kwargs)))
        if self.before_page:
            callback, self.before_page = self.before_page, None
            callback()
        page = self.pages.pop(0) if self.pages else []
        if isinstance(page, Exception):
            raise page
        return deepcopy(page)

    def select(self, *args, **kwargs):
        return []


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(crm, "USER_DATA_DIR", tmp_path)
    monkeypatch.setattr(pull, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(pull, "load_legacy_profiles", lambda owner: {})
    clock = [100000.0]
    monkeypatch.setattr(pull.time, "time", lambda: clock[0])
    db = FakeDatabase()
    monkeypatch.setattr(pull, "CloudDatabase", lambda: db)
    crm._load_crm_data_cached.cache_clear()
    return db, clock


def seed(value, owner=OWNER):
    crm.save_crm_data(owner, {"customers": {KEY: deepcopy(value)}})


def test_cold_pull_adopts_all_unknown_fields_and_history():
    events = [{"detail": "fixture", "extra": index % 2} for index in range(120)]
    merged, outcome = merge_cloud_record({}, content(timeline=events, extra={"keep": True}), 2)
    assert outcome == "updated" and merged["_sync_state"] == "synced"
    assert merged["timeline"] == events and merged["extra"] == {"keep": True}


def test_remote_change_updates_synced_record():
    merged, _ = merge_cloud_record(record(), content(memo="other-device"), 2)
    assert merged["memo"] == "other-device" and merged["_cloud_version"] == 2
    assert merged["_local_revision"] == 2 and merged["_sync_state"] == "synced"


def test_pending_disjoint_changes_are_merged_without_overwriting_own_draft():
    own = record(memo="own-draft", _sync_state="pending", _local_revision=3)
    merged, _ = merge_cloud_record(own, content(next_date="2026-02-01"), 2)
    assert merged["memo"] == "own-draft" and merged["next_date"] == "2026-02-01"
    assert merged["_sync_state"] == "pending" and merged["_cloud_base"]["memo"] == "original"


def test_pending_same_field_conflict_keeps_draft_and_remote_snapshot():
    own = record(memo="own-draft", _sync_state="pending")
    merged, outcome = merge_cloud_record(own, content(memo="remote-draft"), 2)
    assert outcome == "conflict" and merged["memo"] == "own-draft"
    assert merged["_cloud_latest"]["memo"] == "remote-draft" and merged["_cloud_version"] == 1


def test_pending_pull_ack_matches_remote_and_becomes_synced():
    own = record(memo="saved-remotely", _sync_state="pending")
    merged, _ = merge_cloud_record(own, content(memo="saved-remotely"), 2)
    assert merged["_sync_state"] == "synced" and merged["_cloud_version"] == 2


def test_pending_aba_does_not_silently_replace_last_edit():
    own = record(_sync_state="pending", _local_revision=5)
    merged, outcome = merge_cloud_record(own, content(memo="other-device"), 2)
    assert outcome == "conflict" and merged["memo"] == "original"


def test_same_server_version_different_content_is_conflict():
    merged, outcome = merge_cloud_record(record(), content(memo="unexpected"), 1)
    assert outcome == "conflict" and merged["memo"] == "original"


def test_legacy_differences_are_conflicts_but_equal_legacy_can_sync():
    own = content(memo="legacy-draft")
    merged, outcome = merge_cloud_record(own, content(), 1)
    assert outcome == "conflict" and merged["memo"] == "legacy-draft"
    matched, _ = merge_cloud_record(content(updated_at="old"), content(updated_at="new"), 1)
    assert matched["_sync_state"] == "synced"


def test_nested_profile_independent_fields_merge():
    base = content(_v44_profile={"priority": "3", "extension": "keep"})
    own = record(base, _v44_profile={"priority": "1", "extension": "keep"},
                 _sync_state="pending")
    remote = content(_v44_profile={"priority": "3", "pipeline_stage": "active", "extension": "keep"})
    merged, _ = merge_cloud_record(own, remote, 2)
    assert merged["_v44_profile"] == {"priority": "1", "pipeline_stage": "active", "extension": "keep"}
    assert merged["_sync_state"] == "pending"


def test_partial_aba_in_existing_profile_fields_keeps_entire_draft():
    base = content(_v44_profile={"priority": "3", "pipeline_stage": "new"})
    own = record(base, _v44_profile={"priority": "1", "pipeline_stage": "new"}, _sync_state="pending")
    remote = content(_v44_profile={"priority": "3", "pipeline_stage": "active"})
    merged, outcome = merge_cloud_record(own, remote, 2)
    assert outcome == "conflict" and merged["_v44_profile"] == own["_v44_profile"]


def test_unresolved_conflict_cannot_be_silently_adopted_on_next_pull():
    own = record(_sync_state="conflict", _cloud_latest=content(memo="remote"), _cloud_latest_version=2)
    merged, outcome = merge_cloud_record(own, content(memo="remote"), 2)
    assert outcome == "conflict" and merged["memo"] == "original" and merged["_sync_state"] == "conflict"


def test_unresolved_conflict_can_ack_real_convergence():
    own = record(memo="remote", _sync_state="conflict", _cloud_latest=content(memo="remote"), _cloud_latest_version=2)
    merged, _ = merge_cloud_record(own, content(memo="remote"), 2)
    assert merged["_sync_state"] == "synced"


def test_timeline_multiset_keeps_empty_details_extra_fields_and_aliases():
    event = {"at": "2026-01-01", "attachment": {"keep": "fixture"}}
    variant = dict(event, extra=True)
    own = record(timeline=[event, event], timelines=[variant], _sync_state="pending")
    remote = content(timeline=[event, variant])
    merged, _ = merge_cloud_record(own, remote, 2)
    assert merged["timeline"].count(event) == 2 and variant in merged["timeline"]
    assert merged["_sync_state"] == "pending"


@pytest.mark.parametrize("version", [None, 0, -1, True, "2"])
def test_invalid_cloud_version_is_not_accepted(version):
    with pytest.raises(CrmStorageError):
        merge_cloud_record(record(), content(), version)


@pytest.mark.parametrize("latest", [False, True])
def test_older_remote_version_cannot_regress_record_or_conflict(latest):
    own = record(_cloud_version=4)
    if latest:
        own.update(_cloud_version=1, _cloud_latest_version=4, _cloud_latest=content(memo="latest"), _sync_state="conflict")
    merged, outcome = merge_cloud_record(own, content(memo="older"), 3)
    assert merged == own and outcome == "unchanged"


def test_local_existing_rows_no_longer_skip_cloud_pull(isolated):
    db, _ = isolated
    seed(record())
    db.pages = [[row(version=2, crm_data=content(memo="new-device"))], []]
    result = pull.restore_crm_from_cloud(OWNER)
    assert result["ok"] and db.calls
    assert crm.get_customer_record(OWNER, KEY)["memo"] == "new-device"


def test_ttl_is_owner_scoped_and_force_bypasses_it(isolated):
    db, _ = isolated
    assert pull.restore_crm_from_cloud(OWNER)["ok"]
    count = len(db.calls)
    assert pull.restore_crm_from_cloud(OWNER)["status"] == "cached"
    assert len(db.calls) == count
    assert pull.restore_crm_from_cloud("different-fixture-owner")["ok"]
    assert len(db.calls) == count + 1
    assert pull.restore_crm_from_cloud(OWNER, force=True)["ok"]
    assert len(db.calls) == count + 2


def test_short_server_pages_require_explicit_empty_eof(isolated):
    db, _ = isolated
    second = row(2, business_no="fixture-second", crm_data=content(business_no="fixture-second"))
    second["updated_at"] = row()["updated_at"]
    db.pages = [[row()], [second], []]
    assert pull.restore_crm_from_cloud(OWNER, page_size=500)["ok"]
    assert len(db.calls) == 3 and db.calls[1][1]["after"] == (row()["updated_at"], row()["id"])
    assert len(crm.load_crm_data(OWNER)["customers"]) == 2


@pytest.mark.parametrize("bad", [
    {"owner_user_id": "different-fixture-owner"}, {"crm_version": None},
    {"crm_version": True}, {"crm_data": []}, {"updated_at": "invalid"}, {"id": "invalid"},
    {"business_no": ""}, {"crm_data": {"timeline": [None]}},
])
def test_invalid_response_never_changes_customer_or_checkpoint(isolated, bad):
    db, _ = isolated
    own = record()
    seed(own)
    db.pages = [[row(**bad)], []]
    result = pull.restore_crm_from_cloud(OWNER)
    assert not result["ok"]
    assert crm.load_crm_data(OWNER)["customers"][KEY] == own
    assert "checkpoint" not in crm.load_crm_data(OWNER)["_cloud_pull"]


@pytest.mark.parametrize("failure", ["cap", "exception", "duplicate"])
def test_incomplete_pages_never_advance_checkpoint(isolated, failure):
    db, _ = isolated
    seed(record())
    checkpoint = {"id": str(uuid.UUID(int=9)), "updated_at": STAMP.isoformat()}
    crm.mutate_crm_data(OWNER, lambda data: data.update(_cloud_pull={"checkpoint": checkpoint}))
    if failure == "cap":
        db.pages = [[row()], [row(2, business_no="fixture-second")]]
    elif failure == "exception":
        db.pages = [[row()], RuntimeError("private-error-never-display")]
    else:
        db.pages = [[row()], [row()]]
    result = pull.restore_crm_from_cloud(OWNER, max_rows=1 if failure == "cap" else 100)
    assert not result["ok"] and "private-error" not in repr(result)
    assert crm.load_crm_data(OWNER)["_cloud_pull"]["checkpoint"] == checkpoint
    assert crm.load_crm_data(OWNER)["customers"][KEY] == record()


def test_legacy_conflict_does_not_skip_other_customer(isolated):
    db, _ = isolated
    seed(content(memo="legacy-own"))
    second = row(2, business_no="fixture-second", crm_data=content(business_no="fixture-second"))
    db.pages = [[row(), second], []]
    result = pull.restore_crm_from_cloud(OWNER)
    assert result["ok"] and result["conflicts"] == 1
    assert crm.get_customer_record(OWNER, KEY)["memo"] == "legacy-own"
    assert crm.get_customer_record(OWNER, crm.make_customer_key(business_no="fixture-second"))["_sync_state"] == "synced"


def test_pull_rereads_locked_current_after_http_concurrent_edit(isolated):
    db, _ = isolated
    seed(record())
    def concurrent():
        crm.mutate_crm_data(OWNER, lambda data: data["customers"][KEY].update(
            memo="concurrent-local", _local_revision=7, _sync_state="pending"))
    db.before_page = concurrent
    db.pages = [[row(version=2, crm_data=content(memo="remote"))], []]
    assert pull.restore_crm_from_cloud(OWNER)["status"] == "conflict"
    saved = crm.get_customer_record(OWNER, KEY)
    assert saved["memo"] == "concurrent-local" and saved["_local_revision"] == 8


def test_corrupt_local_file_is_not_overwritten(isolated):
    path = crm.get_crm_file_path(OWNER)
    path.write_bytes(b"corrupt-json-fixture")
    result = pull.restore_crm_from_cloud(OWNER)
    assert not result["ok"] and path.read_bytes() == b"corrupt-json-fixture"
    assert isolated[0].calls == []


def test_delta_overlap_and_periodic_full_reconciliation(isolated):
    db, clock = isolated
    db.pages = [[row()], []]
    assert pull.restore_crm_from_cloud(OWNER)["ok"]
    clock[0] += 61
    assert pull.restore_crm_from_cloud(OWNER)["ok"]
    expected = (datetime.fromisoformat(row()["updated_at"]) - timedelta(seconds=300)).isoformat()
    assert db.calls[-1][1]["since"] == expected
    clock[0] += 86401
    assert pull.restore_crm_from_cloud(OWNER)["ok"]
    assert db.calls[-1][1]["since"] is None


def test_sql_reader_always_scopes_owner_and_strict_cursor(monkeypatch):
    response = Mock(ok=True)
    response.json.return_value = []
    client = Mock()
    client.get.return_value = response
    monkeypatch.setattr(cloud_db, "_http_client", lambda: client)
    db = cloud_db.CloudDatabase(cloud_db.CloudConfig("https://example.invalid", "synthetic-test-key"))
    db.select_crm_sync_page(OWNER, since=STAMP.isoformat(), after=(STAMP.isoformat(), str(uuid.UUID(int=1))))
    params = client.get.call_args.kwargs["params"]
    assert params["owner_user_id"] == "eq." + OWNER
    assert params["order"] == "updated_at.asc,id.asc" and "id.gt." in params["or"]
    response.json.return_value = {"error": "not-a-list"}
    with pytest.raises(RuntimeError):
        db.select_crm_sync_page(OWNER)
    with pytest.raises(ValueError):
        db.select_crm_sync_page("")


class Rerun(Exception):
    pass


class FakeUi:
    def __init__(self):
        self.session_state, self.pressed, self.messages, self.buttons = {}, set(), [], []

    def button(self, label, *, key):
        self.buttons.append(key)
        return key in self.pressed

    def warning(self, message):
        self.messages.append(message)

    caption = warning
    write = warning

    def expander(self, *args, **kwargs):
        return nullcontext()

    def rerun(self):
        raise Rerun


def test_automatic_pull_keeps_widget_epoch_and_edit_start_revision(isolated):
    db, _ = isolated
    seed(record())
    ui = FakeUi()
    first = crm_editor.editor_guard(ui, OWNER, KEY, crm.get_customer_record(OWNER, KEY), "enterprise")
    ui.session_state[f"enterprise_memo:{first[0]}"] = "unsaved-widget-fixture"
    db.pages = [[row(version=2, crm_data=content(memo="remote"))], []]
    assert pull.restore_crm_from_cloud(OWNER)["ok"]
    second = crm_editor.editor_guard(ui, OWNER, KEY, crm.get_customer_record(OWNER, KEY), "enterprise")
    assert first == second and ui.session_state[f"enterprise_memo:{first[0]}"] == "unsaved-widget-fixture"


def test_submitted_form_draft_is_preserved_before_force_pull(isolated, monkeypatch):
    seed(record(_v44_profile={"priority": "3", "unknown_extension": "keep"}))
    ui = FakeUi()
    calls = []
    def force_pull(owner, *, force):
        assert owner == OWNER and force
        draft = crm.load_crm_data(OWNER)["_local_conflicts"][-1]["attempted_changes"]
        assert draft["memo"] == "unsent-browser-fixture"
        assert draft["_v44_profile"] == {"priority": "1", "unknown_extension": "keep"}
        calls.append(True)
        crm.mutate_crm_data(OWNER, lambda data: data["customers"][KEY].update(
            _sync_state="conflict", _cloud_latest=content(memo="latest"), _cloud_latest_version=2))
        return {"ok": True}
    monkeypatch.setattr(pull, "restore_crm_from_cloud", force_pull)
    with pytest.raises(Rerun):
        crm_editor.reload_editor(ui, OWNER, KEY, "customer", changes={
            "memo": "unsent-browser-fixture", "_v44_profile": {"priority": "1"}})
    assert calls and crm.get_customer_record(OWNER, KEY)["memo"] == "latest"
    assert ui.session_state[f"crm-edit:customer:{OWNER}:{KEY}:epoch"] == 1


def test_failed_manual_reload_preserves_input_and_does_not_reset_epoch(isolated, monkeypatch):
    seed(record())
    ui = FakeUi()
    monkeypatch.setattr(pull, "restore_crm_from_cloud", lambda owner, force: {"ok": False})
    crm_editor.reload_editor(ui, OWNER, KEY, "customer", changes={"memo": "keep-unsent"})
    assert crm.load_crm_data(OWNER)["_local_conflicts"][-1]["attempted_changes"]["memo"] == "keep-unsent"
    assert ui.session_state == {} and ui.messages


def test_enterprise_reload_captures_server_known_widget_draft(isolated):
    db, _ = isolated
    seed(record())
    ui = FakeUi()
    scope = f"crm-edit:enterprise:{OWNER}:{KEY}"
    ui.session_state[f"enterprise_memo:{scope}:0"] = "unsaved-widget"
    ui.pressed.add(scope + ":reload")
    db.pages = [[row(version=2, crm_data=content(memo="remote"))], []]
    with pytest.raises(Rerun):
        crm_editor.editor_guard(ui, OWNER, KEY, crm.get_customer_record(OWNER, KEY), "enterprise")
    assert crm.load_crm_data(OWNER)["_local_conflicts"][-1]["attempted_changes"]["memo"] == "unsaved-widget"
    assert ui.session_state[scope + ":epoch"] == 1


def test_customer_form_has_no_outside_reload_that_discards_unsubmitted_input(isolated):
    seed(record(_sync_state="conflict"))
    ui = FakeUi()
    crm_editor.editor_guard(ui, OWNER, KEY, crm.get_customer_record(OWNER, KEY), "customer")
    assert not ui.buttons
    source = (pull.__file__ and crm_editor.__file__)
    from pathlib import Path
    app_source = (Path(source).parent / "app.py").read_text(encoding="utf-8")
    for field in ("status", "action", "next_date", "pipeline", "priority", "manager", "memo"):
        assert f"crm_{field}_{{editor_token}}" in app_source
    assert 'reload_submitted = st.form_submit_button("초안 보관 후 최신본 불러오기"' in app_source


def test_latest_adoption_preserves_local_only_actual_events_and_marks_pending(isolated):
    local_event = {"detail": "actual-local-attempt", "at": "2026-01-02"}
    remote_event = {"detail": "actual-remote-attempt", "at": "2026-01-01"}
    latest = content(memo="latest", timeline=[remote_event])
    seed(record(timeline=[local_event], _sync_state="conflict", _cloud_latest=latest, _cloud_latest_version=2))
    assert crm_editor._adopt_latest(OWNER, KEY)
    saved = crm.get_customer_record(OWNER, KEY)
    assert saved["memo"] == "latest" and saved["timeline"] == [local_event, remote_event]
    assert saved["_sync_state"] == "pending" and saved["_cloud_base"]["timeline"] == [remote_event]


def test_canonical_cloud_profile_is_available_without_rewriting_legacy_sidecar(isolated):
    db, _ = isolated
    db.pages = [[row(crm_data=content(_v44_profile={"priority": "2", "extension": "keep"}))], []]
    assert pull.restore_crm_from_cloud(OWNER)["ok"]
    import crm_enhancements
    profile = crm_enhancements.get_crm_profile(OWNER, KEY, BIZ)
    assert profile == {"priority": "2", "extension": "keep"}


def test_separate_customer_workbook_restore_entry_point_is_retained():
    from pathlib import Path
    app_source = (Path(crm.__file__).parent / "app.py").read_text(encoding="utf-8")
    assert "restore_customer_db_if_needed(CURRENT_USER_ID)" in app_source
    assert 'crm_sync_notice.get("ok")' in app_source


def test_corrupt_legacy_profile_is_never_replaced_in_saved_draft(isolated):
    own = record(_v44_profile="invalid-profile-fixture")
    seed(own)
    ui = FakeUi()
    crm_editor.reload_editor(ui, OWNER, KEY, "customer", changes={"_v44_profile": {"priority": "1"}})
    assert crm.load_crm_data(OWNER)["customers"][KEY] == own
    assert "_local_conflicts" not in crm.load_crm_data(OWNER)
    assert ui.messages and not isolated[0].calls
