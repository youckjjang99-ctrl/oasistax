"""Real Streamlit rendering with synthetic rows; no network or dialer is used."""
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

import voice_calling


def dashboard(monkeypatch, *, admin=True, ready=False, count=2, source_only=False, duplicate_uid=False):
    monkeypatch.setattr(voice_calling.VoiceSettings, "from_environment", classmethod(lambda cls: SimpleNamespace(readiness=lambda: {"ready": ready, "checks": [], "provider": "clawops", "test_only": True})))
    script = '''
import streamlit as st
import voice_calling_repository
st.session_state.setdefault("mutations", [])
st.session_state.setdefault("reads", [])
st.session_state.setdefault("repository_created", 0)
class FakeRepository:
    def __init__(self):
        st.session_state["repository_created"] += 1
    def action(self, actor, action, payload):
        if action in {"catalog", "legacy_jobs", "list_campaigns", "campaign_stats", "campaign_jobs"}:
            st.session_state["reads"].append((action, payload))
        if action == "catalog":
            if st.session_state.get("deny_catalog"):
                return {"ok":False, "code":"NOT_AUTHORIZED"}
            if st.session_state.get("catalog_error"):
                return {"ok":False, "code":st.session_state["catalog_error"]}
            rows = [{"row_id":f"source:{i}","company_uid":None if SOURCE_ONLY else f"fixture:{i//2 if DUPLICATE_UID else i}", "company_name":f"Synthetic company {i:03d}", "phone_masked":"" if SOURCE_ONLY else "***-****-0000", "eligible":not SOURCE_ONLY, "permission_valid":not SOURCE_ONLY,
                "business_type":["individual","corporate","unknown"][i%3], "region":"RegionA" if i%2 else "RegionB", "industry":"Synthetic industry", "employee_count":None if i%2 else 4,"employment_change":i-3, "discovery_type":"employment_growth", "mobile_phone_masked":"***-****-0000" if i%2==0 else "", "landline_phone_masked":"***-****-0001" if i%3==0 else "", "blocked_reason":"SOURCE_NOT_LINKED" if SOURCE_ONLY else ""} for i in range(COUNT)]
            for field, column in [("query","company_name"),("region","region"),("industry","industry")]:
                rows = [row for row in rows if payload.get(field, "").lower() in row[column].lower()]
            if payload.get("business_type", "all") != "all":
                rows = [row for row in rows if row["business_type"] == payload["business_type"]]
            if payload.get("phone_type", "all") == "mobile":
                rows = [row for row in rows if row["mobile_phone_masked"]]
            elif payload.get("phone_type", "all") == "landline":
                rows = [row for row in rows if row["landline_phone_masked"]]
            elif payload.get("phone_type", "all") == "both":
                rows = [row for row in rows if row["mobile_phone_masked"] and row["landline_phone_masked"]]
            elif payload.get("phone_type", "all") == "none":
                rows = [row for row in rows if not row["mobile_phone_masked"] and not row["landline_phone_masked"]]
            offset = (payload.get("cursor") or {}).get("offset", 0)
            limit = payload["limit"]
            more = offset + limit < len(rows)
            return {"ok": True, "rows": rows[offset:offset+limit], "has_more":more, "next_cursor":{"offset":offset+limit} if more else None}
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
        if action in {"grant_permission", "do_not_call"}:
            return {"ok":True}
        raise AssertionError("Unexpected mutation")
voice_calling_repository.VoiceRepository=FakeRepository
from voice_calling_ui import render_voice_calling
render_voice_calling("fixture-actor", is_admin_user=ADMIN)
'''.replace("COUNT", str(count)).replace("ADMIN", str(admin)).replace("SOURCE_ONLY", str(source_only)).replace("DUPLICATE_UID", str(duplicate_uid))
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


def test_member_direct_render_is_blocked_before_any_repository_access(monkeypatch):
    app = dashboard(monkeypatch, admin=False, count=35)
    assert not app.exception
    assert app.session_state["repository_created"] == 0
    assert app.session_state["reads"] == app.session_state["mutations"] == []
    assert not app.tabs and not app.button and not app.dataframe
    assert any("관리자 전용" in item.value for item in app.warning)


def test_admin_can_select_100_and_clear_without_mutation(monkeypatch):
    app = dashboard(monkeypatch, count=100)
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    assert len(app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"]) == 100
    button(app, "선택 모두 해제").click().run()
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == []
    assert app.session_state["mutations"] == []
    assert not app.exception


def test_search_is_server_wide_and_resets_cursor_and_previous_selection(monkeypatch):
    app = dashboard(monkeypatch, count=200)
    button(app, "다음 대상 목록").click().run()
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    before = len(app.session_state["reads"])
    next(item for item in app.text_input if item.label.startswith("업체명 검색")).set_value("003")
    button(app, "전체 DB 검색 적용").click().run()
    assert not app.exception
    new_catalog_reads = [payload for action, payload in app.session_state["reads"][before:] if action == "catalog"]
    assert len(new_catalog_reads) == 1 and new_catalog_reads[0]["query"] == "003"
    assert app.session_state["oasis_voice_visit_candidate_page_fixture-actor"] == 0
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == []
    catalog_payload = [payload for action, payload in app.session_state["reads"] if action == "catalog"][-1]
    assert catalog_payload["query"] == "003" and catalog_payload["cursor"] is None and catalog_payload["limit"] == 100
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == ["source:3"]
    assert app.session_state["mutations"] == []


def test_next_page_select_all_replaces_first_hundred(monkeypatch):
    app = dashboard(monkeypatch, count=200)
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == [f"source:{i}" for i in range(100)]
    button(app, "다음 대상 목록").click().run()
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == [f"source:{i}" for i in range(100, 200)]
    button(app, "이전 대상 목록").click().run()
    assert [payload["cursor"] for action, payload in app.session_state["reads"] if action == "catalog"][-1] is None
    assert len(app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"]) == 100
    assert app.session_state["mutations"] == []


def test_catalog_source_only_rows_can_be_selected_but_not_enqueued_or_granted(monkeypatch):
    app = dashboard(monkeypatch, source_only=True, count=5)
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    assert len(app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"]) == 5
    assert button(app, "발신 가능 0개 업체 캠페인 저장").disabled
    assert button(app, "선택 업체 동의 근거 일괄 등록").disabled
    assert button(app, "선택 업체·발신 연락처 수신거부 등록").disabled
    assert app.session_state["mutations"] == []


def test_multiple_source_rows_for_one_company_create_only_one_job(monkeypatch):
    app = dashboard(monkeypatch, duplicate_uid=True, count=2)
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    button(app, "발신 가능 1개 업체 캠페인 저장").click().run()
    assert not app.exception
    calls = app.session_state["mutations"]
    assert len(calls) == 1 and calls[0][1] == "create_campaign"
    assert calls[0][2]["company_uids"] == ["fixture:0"]


def test_business_and_phone_filters_are_sent_to_server(monkeypatch):
    app = dashboard(monkeypatch, count=15)
    next(item for item in app.selectbox if item.label == "사업자 구분").set_value("corporate")
    next(item for item in app.selectbox if item.label == "연락처 보유 유형").set_value("mobile")
    button(app, "전체 DB 검색 적용").click().run()
    assert not app.exception
    payload = [payload for action, payload in app.session_state["reads"] if action == "catalog"][-1]
    assert payload["business_type"] == "corporate" and payload["phone_type"] == "mobile"
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == ["source:4", "source:10"]


def test_db_role_denial_discards_stale_selection_and_stops_all_other_reads(monkeypatch):
    app = dashboard(monkeypatch, count=2)
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    before = len(app.session_state["reads"])
    app.session_state["deny_catalog"] = True
    button(app, "목록 새로고침").click().run()
    assert not app.exception
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == []
    assert app.session_state["oasis_voice_visit_bulk_candidates_fixture-actor"] == {}
    assert [action for action, _payload in app.session_state["reads"]][before:] == ["catalog"]
    assert not app.tabs and not app.dataframe
    assert app.session_state["mutations"] == []


def test_search_timeout_keeps_readonly_recovery_filters_but_no_old_targets(monkeypatch):
    app = dashboard(monkeypatch, count=200)
    button(app, "다음 대상 목록").click().run()
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    app.session_state["catalog_error"] = "SEARCH_TIMEOUT"
    button(app, "목록 새로고침").click().run()
    assert not app.exception
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == []
    assert not app.dataframe and not app.tabs
    assert button(app, "전체 DB 검색 적용")
    assert button(app, "첫 페이지부터 다시 조회")
    assert any("검색 시간이 초과" in item.value for item in app.warning)
    app.session_state["catalog_error"] = ""
    button(app, "첫 페이지부터 다시 조회").click().run()
    assert not app.exception
    assert app.session_state["oasis_voice_visit_candidate_page_fixture-actor"] == 0
    assert app.session_state["mutations"] == []


def test_consent_and_dnc_mutations_deduplicate_linked_source_rows(monkeypatch):
    app = dashboard(monkeypatch, duplicate_uid=True, count=2)
    button(app, "현재 페이지 전체선택 · 기존 선택 교체").click().run()
    next(item for item in app.text_input if item.label == "동의 출처").set_value("Synthetic application")
    next(item for item in app.text_input if item.label == "증빙 참조번호").set_value("fixture-record")
    checkbox(app, "선택 업체의 전화 안내 동의").check()
    button(app, "선택 업체 동의 근거 일괄 등록").click().run()
    assert not app.exception
    assert app.session_state["mutations"][-1][1] == "grant_permission"
    assert app.session_state["mutations"][-1][2]["company_uids"] == ["fixture:0"]
    next(item for item in app.text_input if item.label == "수신거부 처리 사유").set_value("Synthetic withdrawal").run()
    button(app, "선택 업체·발신 연락처 수신거부 등록").click().run()
    assert not app.exception
    assert len([call for call in app.session_state["mutations"] if call[1] == "do_not_call"]) == 1


def test_timeout_recovery_filter_submit_queries_only_new_conditions_once(monkeypatch):
    app = dashboard(monkeypatch, count=200)
    app.session_state["catalog_error"] = "SEARCH_TIMEOUT"
    app.run()
    assert not app.exception and not app.dataframe
    before = len(app.session_state["reads"])
    app.session_state["catalog_error"] = ""
    next(item for item in app.text_input if item.label.startswith("업체명 검색")).set_value("004")
    button(app, "전체 DB 검색 적용").click().run()
    assert not app.exception
    catalog_reads = [payload for action, payload in app.session_state["reads"][before:] if action == "catalog"]
    assert len(catalog_reads) == 1
    assert catalog_reads[0]["query"] == "004" and catalog_reads[0]["cursor"] is None
    assert app.session_state["oasis_voice_visit_bulk_selected_fixture-actor"] == []
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


def test_catalog_failure_disables_auto_refresh_without_repeating_query(monkeypatch):
    app = dashboard(monkeypatch)
    app.toggle[0].set_value(True).run()
    before = len(app.session_state["reads"])
    app.session_state["catalog_error"] = "SEARCH_TIMEOUT"
    app.run()
    assert not app.exception
    assert app.toggle[0].value is False
    assert [action for action, _payload in app.session_state["reads"]][before:] == ["catalog"]
    assert button(app, "전체 DB 검색 적용")
    assert not app.dataframe and not app.tabs
    assert app.session_state["mutations"] == []
