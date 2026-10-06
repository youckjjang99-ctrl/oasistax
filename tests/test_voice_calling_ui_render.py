"""Real Streamlit rendering with synthetic rows; no network or dialer is used."""
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

import voice_calling


def dashboard(monkeypatch, *, admin=True, ready=False, count=2):
    monkeypatch.setattr(voice_calling.VoiceSettings, "from_environment", classmethod(lambda cls: SimpleNamespace(readiness=lambda: {"ready": ready, "checks": [], "provider": "clawops", "test_only": True})))
    script = '''
import streamlit as st
import voice_calling_repository
class FakeRepository:
    def action(self, actor, action, payload):
        if "mutations" not in st.session_state:
            st.session_state["mutations"] = []
        if action == "candidates":
            rows = [{"company_uid":f"fixture:{i}", "company_name":f"Synthetic company {i:03d}", "phone_masked":"***-****-0000", "eligible":True, "permission_valid":True} for i in range(COUNT)]
            return {"ok": True, "rows": rows[payload["offset"]:payload["offset"]+payload["limit"]], "has_more": payload["offset"]+payload["limit"] < len(rows)}
        if action == "legacy_jobs":
            return {"ok":True, "rows":[{"id":"fixture-job", "company_name":"Synthetic legacy company", "status":"queued", "created_at":"2030-01-07T01:00:00Z"}], "has_more":False}
        if action == "list_campaigns":
            return {"ok": True, "rows": [{"id":"fixture-campaign", "name":"Synthetic campaign", "status":st.session_state.get("campaign_status", "draft"), "total":1, "queued":1}], "has_more":False}
        if action == "campaign_stats":
            return {"ok":True, "metrics":{"total":1,"queued":1}}
        if action == "campaign_jobs":
            return {"ok":True, "rows":[{"id":"campaign-job", "campaign_id":"fixture-campaign", "company_name":"Synthetic company", "status":"queued"}], "has_more":False}
        st.session_state["mutations"].append((actor, action, payload))
        if action == "create_campaign":
            return {"ok":True,"campaign_id":"new-fixture","created_count":len(payload["company_uids"]),"skipped_count":0,"skipped":[]}
        if action == "start_campaign":
            st.session_state["campaign_status"] = "running"
            return {"ok":True,"approved_count":1}
        if action == "pause_campaign":
            st.session_state["campaign_status"] = "paused"
            return {"ok":True}
        raise AssertionError("Unexpected mutation")
voice_calling_repository.VoiceRepository=FakeRepository
from voice_calling_ui import render_voice_calling
render_voice_calling("fixture-actor", is_admin_user=ADMIN)
'''.replace("COUNT", str(count)).replace("ADMIN", str(admin))
    return AppTest.from_string(script).run(timeout=30)


def button(app, label):
    return next(item for item in app.button if item.label == label)


def checkbox(app, prefix):
    return next(item for item in app.checkbox if item.label.startswith(prefix))


def test_real_page_renders_and_disables_unconfigured_approval(monkeypatch):
    app = dashboard(monkeypatch)
    assert not app.exception
    assert len(app.tabs) == 5
    assert button(app, "이 요청 발신 승인").disabled
    assert button(app, "캠페인 자동발신 시작 / 재개").disabled
    assert any(item.value == "1건" for item in app.metric)
    assert app.session_state["mutations"] == []


def test_member_bulk_selection_caps_30_and_creates_draft_only(monkeypatch):
    app = dashboard(monkeypatch, admin=False, count=35)
    button(app, "현재 목록 발신 가능 업체 전체선택").click().run()
    assert not app.exception
    assert len(app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"]) == 30
    assert not any(item.label == "캠페인 자동발신 시작 / 재개" for item in app.button)
    button(app, "선택 30개 업체 캠페인 저장").click().run()
    assert not app.exception
    calls = app.session_state["mutations"]
    assert len(calls) == 1 and calls[0][1] == "create_campaign"
    assert len(calls[0][2]["company_uids"]) == 30
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == []
    assert any("접수 30건" in item.value for item in app.success)


def test_admin_can_select_100_and_clear_without_mutation(monkeypatch):
    app = dashboard(monkeypatch, count=100)
    button(app, "현재 목록 발신 가능 업체 전체선택").click().run()
    assert len(app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"]) == 100
    button(app, "선택 모두 해제").click().run()
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == []
    assert app.session_state["mutations"] == []
    assert not app.exception


def test_search_limits_select_all_to_displayed_companies(monkeypatch):
    app = dashboard(monkeypatch, count=5)
    next(item for item in app.text_input if item.label.startswith("업체명 검색")).set_value("003").run()
    button(app, "현재 목록 발신 가능 업체 전체선택").click().run()
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == ["fixture:3"]
    assert app.session_state["mutations"] == []


def test_start_requires_explicit_confirmation_and_admin_readiness(monkeypatch):
    app = dashboard(monkeypatch, ready=True)
    button(app, "캠페인 자동발신 시작 / 재개").click().run()
    assert app.session_state["mutations"] == []
    assert any("시작 확인란" in item.value for item in app.warning)
    checkbox(app, "대상·동의·비용").check()
    button(app, "캠페인 자동발신 시작 / 재개").click().run()
    assert not app.exception
    assert [(call[1], call[2]) for call in app.session_state["mutations"]] == [("start_campaign", {"campaign_id":"fixture-campaign"})]
    button(app, "이 캠페인 일시정지").click().run()
    assert app.session_state["mutations"][-1][1] == "pause_campaign"
    assert any("이미 발신 요청된 통화" in item.value for item in app.success)


def test_auto_refresh_toggle_does_not_start_any_call(monkeypatch):
    app = dashboard(monkeypatch)
    app.toggle[0].set_value(True).run()
    assert not app.exception
    assert app.session_state["mutations"] == []
