"""Render the real Streamlit page using synthetic data and no network."""
from streamlit.testing.v1 import AppTest


def test_real_page_renders_preparation_and_disables_unconfigured_approval(monkeypatch):
    monkeypatch.setenv("OASIS_VOICE_CALLS_ENABLED", "false")
    script = '''
import voice_calling_repository
SYNTHETIC_COMPANY_UID = "business:" + "".join(("000", "00", "00001"))
class FakeRepository:
    def action(self, actor, action, payload):
        if action == "candidates":
            return {"ok": True, "rows": [{"company_uid":SYNTHETIC_COMPANY_UID, "company_name":"Synthetic company", "phone_masked":"***-****-0000", "eligible":True, "permission_valid":True}], "has_more":False}
        if action == "list_jobs":
            return {"ok":True, "rows":[{"id":"00000000-0000-4000-8000-000000000001", "company_name":"Synthetic company", "status":"queued", "created_at":"2030-01-07T01:00:00Z"}], "has_more":False}
        raise AssertionError("No mutation expected during render")
voice_calling_repository.VoiceRepository=FakeRepository
from voice_calling_ui import render_voice_calling
render_voice_calling("admin", is_admin_user=True)
'''
    app = AppTest.from_string(script).run(timeout=30)
    assert not app.exception
    assert len(app.tabs) == 5
    approvals = [button for button in app.button if button.label == "이 요청 발신 승인"]
    assert len(approvals) == 1 and approvals[0].disabled
    assert any(item.value == "1건" for item in app.metric)
