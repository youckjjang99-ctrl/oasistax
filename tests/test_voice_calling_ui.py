from __future__ import annotations

from pathlib import Path
from datetime import date
import unittest

import voice_calling_ui as ui


class FakeRepository:
    def __init__(self):
        self.calls = []

    def action(self, actor, action, payload):
        self.calls.append((actor, action, payload))
        return {"ok": True, "rows": []}


class FakeStreamlit:
    def __init__(self, clicked="", selected=None):
        self.clicked = clicked
        self.selected = selected or []
        self.buttons = []
        self.messages = []
        self.session_state = {}
        self.column_config = type("ColumnConfig", (), {"CheckboxColumn": staticmethod(lambda *_args, **_kwargs: {}), "TextColumn": staticmethod(lambda *_args, **_kwargs: {})})

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def expander(self, *_args, **_kwargs):
        return self

    form = expander

    def columns(self, count):
        return [self] * (count if isinstance(count, int) else len(count))

    def button(self, label, **kwargs):
        self.buttons.append((label, kwargs))
        return self.clicked == label and not kwargs.get("disabled")

    def multiselect(self, *_args, **_kwargs):
        return self.selected

    form_submit_button = button

    def checkbox(self, *_args, **kwargs):
        return kwargs.get("value", False)

    def data_editor(self, rows, **_kwargs):
        return [{**row, "선택": index < len(self.selected)} for index, row in enumerate(rows)]

    def text_input(self, *_args, **kwargs):
        return kwargs.get("value", "")

    def selectbox(self, _label, values, **kwargs):
        return values[kwargs.get("index", 0)]

    def date_input(self, *_args, **kwargs):
        return kwargs.get("value", date(2030, 1, 1))

    def dataframe(self, *_args, **_kwargs):
        pass

    def caption(self, message):
        self.messages.append(message)

    info = caption
    warning = caption
    error = caption

    def rerun(self):
        pass


class VoiceCallingUiTests(unittest.TestCase):
    def candidate(self, **updates):
        row = {"company_uid": "fixture:one", "company_name": "테스트 업체", "phone_masked": "***-****-0000", "permission_valid": True, "status": "assigned"}
        row.update(updates)
        return row

    def test_contact_display_never_exposes_full_phone_or_raw_rpc_fields(self):
        row = self.candidate(phone_masked="", phone="+82" + "10" + "0000" + "0000", secret="do-not-render", evidence_ref="private")
        display = ui.candidate_display([row])[0]
        self.assertEqual(display["발신 연락처"], "***-****-0000")
        self.assertNotIn("secret", display)
        self.assertNotIn("evidence_ref", display)
        self.assertNotIn("+82", str(display))
        self.assertEqual(ui.masked_phone(None), "없음")
        self.assertEqual(ui.masked_phone("unknown"), "***-****-****")

    def test_selection_requires_ownership_scope_consent_phone_and_no_dnc(self):
        good = self.candidate()
        self.assertEqual(ui.validate_selection(["fixture:one"], [good], require_eligible=True), "")
        for updates in ({"permission_valid": False}, {"phone_masked": ""}, {"do_not_call": True}, {"status": "closed"}, {"eligible": False}):
            self.assertTrue(ui.validate_selection(["fixture:one"], [self.candidate(**updates)], require_eligible=True))
        self.assertTrue(ui.validate_selection(["other-owner"], [good], require_eligible=True))
        self.assertTrue(ui.validate_selection([], [good], require_eligible=False))

    def test_batch_limit_is_enforced_before_server_mutation(self):
        candidates = [self.candidate(company_uid=f"fixture:{i}") for i in range(31)]
        self.assertIn("30", ui.validate_selection([r["company_uid"] for r in candidates], candidates, require_eligible=True))

    def test_queue_key_is_stable_on_rerun_reorder_and_changes_with_actor(self):
        state = {}
        first = ui.enqueue_request_key(state, "owner-a", ["b", "a"])
        self.assertEqual(first, ui.enqueue_request_key(state, "owner-a", ["a", "b", "b"]))
        self.assertNotEqual(first, ui.enqueue_request_key(state, "owner-b", ["a", "b"]))

    def test_metrics_only_count_loaded_scope(self):
        metrics = ui.scoped_metrics([self.candidate(), self.candidate(company_uid="fixture:two", permission_valid=False)], [{"status": "pending_approval"}, {"status": "completed", "outcome": "visit_requested"}])
        self.assertEqual(metrics, {"candidates": 2, "eligible": 1, "pending_approval": 1, "visit_requests": 1})

    def test_repository_exception_is_never_echoed(self):
        class BrokenRepository:
            def action(self, *_args):
                raise RuntimeError("private-provider-token-and-phone")

        result = ui._action(BrokenRepository(), "owner-a", "candidates")
        self.assertFalse(result["ok"])
        self.assertNotIn("private-provider", str(result))

    def test_member_cannot_see_approval_and_disabled_setup_cannot_approve(self):
        jobs = [{"id": "job-one", "status": "pending_approval", "company_name": "테스트"}]
        repo = FakeRepository()
        member = FakeStreamlit(clicked="이 요청 발신 승인")
        ui._render_queue(member, repo, "owner-a", jobs, False, True)
        self.assertFalse(any(label == "이 요청 발신 승인" for label, _ in member.buttons))
        admin = FakeStreamlit(clicked="이 요청 발신 승인")
        ui._render_queue(admin, repo, "admin", jobs, True, False)
        self.assertTrue(next(options["disabled"] for label, options in admin.buttons if label == "이 요청 발신 승인"))
        self.assertEqual(repo.calls, [])

    def test_admin_approval_is_repository_action_only(self):
        repo = FakeRepository()
        screen = FakeStreamlit(clicked="이 요청 발신 승인")
        ui._render_queue(screen, repo, "admin", [{"id": "job-one", "status": "pending_approval"}], True, True)
        self.assertEqual(repo.calls, [("admin", "approve", {"job_id": "job-one"})])

    def test_queue_rejects_missing_consent_before_repository(self):
        repo = FakeRepository()
        screen = FakeStreamlit(clicked="발신 가능 0개 업체 캠페인 저장", selected=["fixture:one"])
        ui._render_targets(screen, repo, "admin", [self.candidate(permission_valid=False)], True, True)
        self.assertEqual(repo.calls, [])
        self.assertTrue(any("발신 조건" in text for text in screen.messages))

    def test_valid_selection_queues_without_dialing(self):
        repo = FakeRepository()
        screen = FakeStreamlit(clicked="발신 가능 1개 업체 캠페인 저장", selected=["fixture:one"])
        ui._render_targets(screen, repo, "admin", [self.candidate()], True, True)
        self.assertEqual(len(repo.calls), 1)
        actor, action, payload = repo.calls[0]
        self.assertEqual((actor, action), ("admin", "create_campaign"))
        self.assertEqual(payload["company_uids"], ["fixture:one"])
        self.assertTrue(payload["name"])
        self.assertTrue(payload["request_id"])

    def test_private_target_renderer_blocks_non_admin_before_any_mutation(self):
        repo = FakeRepository()
        screen = FakeStreamlit(clicked="발신 가능 1개 업체 캠페인 저장", selected=["fixture:one"])
        ui._render_targets(screen, repo, "member", [self.candidate()], True, False)
        self.assertEqual(repo.calls, [])
        self.assertEqual(screen.buttons, [])
        self.assertIn("관리자 전용", screen.messages[0])

    def test_fragment_direct_entry_blocks_non_admin_before_repository(self):
        from unittest.mock import patch
        screen = FakeStreamlit()
        with patch("voice_calling_repository.VoiceRepository", side_effect=AssertionError("Must not instantiate")):
            ui._render_dashboard(screen, "member", False)
        self.assertIn("관리자 전용", screen.messages[0])

    def test_source_row_missing_company_uid_cannot_become_eligible(self):
        self.assertFalse(ui._eligible(self.candidate(row_id="source:1", company_uid=None)))

    def test_rich_display_preserves_unknown_and_negative_employment(self):
        row = self.candidate(row_id="source:1", company_uid=None, business_type="unknown", employee_count=None, employment_change=-3, industry="Synthetic", discovery_type="employment_growth", blocked_reason="SOURCE_NOT_LINKED", eligible=False)
        display = ui.candidate_display([row])[0]
        self.assertEqual(display["사업자 구분"], "미확인")
        self.assertEqual(display["고용인원"], "미확인")
        self.assertEqual(display["고용 증감"], "-3명")
        self.assertEqual(display["발굴유형"], "고용인원 증가")
        self.assertIn("연결", display["발신 제한 사유"])
        self.assertEqual(display["발신번호 수신거부"], "미확인")

    def test_campaign_jobs_never_receive_individual_approval_controls(self):
        repo = FakeRepository()
        screen = FakeStreamlit(clicked="이 요청 발신 승인")
        ui._render_queue(screen, repo, "admin", [{"id": "job-one", "campaign_id": "campaign-one", "status": "queued"}], True, True)
        self.assertEqual(repo.calls, [])
        self.assertFalse(any(label == "이 요청 발신 승인" for label, _ in screen.buttons))

    def test_admin_batch_limit_allows_100_not_101(self):
        rows = [self.candidate(company_uid=f"fixture:{i}") for i in range(101)]
        self.assertEqual(ui.validate_selection([row["company_uid"] for row in rows[:100]], rows, require_eligible=True, max_selection=100), "")
        self.assertIn("100", ui.validate_selection([row["company_uid"] for row in rows], rows, require_eligible=True, max_selection=100))

    def test_app_menu_wires_lazy_renderer_and_authorized_actor(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        self.assertIn('"AI 방문상담": "주요업무"', source)
        self.assertIn('primary_menu["AI 방문상담"]', source)
        self.assertIn('if CURRENT_USER_IS_ADMIN:\n        primary_menu["AI 방문상담"]', source)
        route = source.split('elif active_tab == "AI 방문상담":', 1)[1].split("elif active_tab", 1)[0]
        self.assertIn("from voice_calling_ui import render_voice_calling", route)
        self.assertIn("CURRENT_USER_ID", route)
        self.assertIn("CURRENT_USER_IS_ADMIN", route)
        self.assertIn("if not CURRENT_USER_IS_ADMIN:", route)


if __name__ == "__main__":
    unittest.main()
