from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

import pandas as pd

import prospect_db_center as prospect


def _business_number(*parts: str) -> str:
    return "".join(parts)


def _email(local_part: str) -> str:
    return "@".join((local_part, "example.invalid"))


def _phone(*parts: str) -> str:
    return "-".join(parts)


class SavedProspectOutreachUiTests(unittest.TestCase):
    def _frame(self, *, blocked: bool = False) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "업체명": "테스트 업체",
                    "사업자번호": _business_number("000", "00", "00000"),
                    "사업자유형": "개인사업자 후보",
                    "대표전화": _phone("010", "0000", "0000"),
                    "휴대전화": _phone("010", "0000", "0000"),
                    "일반전화": "",
                    "이메일": _email("sales"),
                    "인스타그램": "@sample",
                    "인스타그램URL": "https://example.invalid/sample",
                    "업종명": "서비스업",
                    "업체별 진행상황": "신규 배정",
                    "메모": "",
                    "_prospect_id": "prospect-1",
                    "_company_uid": "source:" + ("a" * 64),
                    "_assignment_id": "assignment-1",
                    "_assignment_status": "assigned",
                    "_do_not_contact": blocked,
                    "_canonical_mobile_available": True,
                    "_canonical_email_available": True,
                }
            ]
        )

    def test_compact_table_has_exact_requested_columns_in_order(self):
        compact = prospect._saved_prospect_table_frame(
            self._frame(),
            can_view_mobile=True,
        )

        self.assertEqual(
            tuple(compact.columns),
            prospect.SAVED_PROSPECT_VISIBLE_COLUMNS,
        )
        self.assertEqual(
            compact.loc[0, "연락처"],
            _phone("010", "0000", "0000"),
        )
        self.assertNotIn("이메일", compact.columns)
        self.assertNotIn("인스타", compact.columns)
        self.assertNotIn("이메일보내기", compact.columns)
        self.assertNotIn("가입자", compact.columns)
        self.assertNotIn("고용증가값", compact.columns)
        self.assertEqual(compact.loc[0, "업체별 진행상황"], "신규 배정")
        self.assertEqual(compact.loc[0, "이력관리"], "📄")
        self.assertEqual(compact.loc[0, "문자보내기"], "💬")
        self.assertEqual(compact.loc[0, "카카오톡보내기"], "🟡")

    def test_source_phone_stays_visible_when_contact_table_has_no_rows(self):
        phone = _phone("010", "0000", "0000")
        candidate = {
            "id": "prospect-1",
            "company_name": "테스트 업체",
            "business_no": _business_number("000", "00", "00000"),
            "industry_name": "서비스업",
            "employee_count": 12,
            "source_data": {
                "sales_intelligence_v971": {
                    "phone": phone,
                    "email": _email("sales"),
                    "instagram": "@sample",
                    "instagram_url": "https://example.invalid/sample",
                }
            },
        }
        display = prospect._saved_candidate_frame(
            [candidate],
            [],
            can_view_mobile=True,
        ).assign(
            _company_uid="source:" + ("a" * 64),
            _assignment_id="assignment-1",
            _assignment_status="assigned",
        )
        compact = prospect._saved_prospect_table_frame(
            display,
            can_view_mobile=True,
        )

        self.assertEqual(compact.loc[0, "연락처"], phone)
        self.assertFalse(bool(display.loc[0, "_canonical_mobile_available"]))
        self.assertFalse(bool(display.loc[0, "_canonical_email_available"]))
        for column in prospect.OUTREACH_COLUMN_CHANNELS:
            self.assertTrue(pd.isna(compact.loc[0, column]))

        captured: dict[str, pd.DataFrame] = {}

        class _StopAfterSavedProspectTable(RuntimeError):
            pass

        def capture_table(frame, **_kwargs):
            captured["frame"] = frame.copy()
            raise _StopAfterSavedProspectTable

        with patch.object(
            prospect,
            "_show_outreach_result_notice",
        ), patch.object(
            prospect,
            "_assignment_feature_status",
            return_value=(False, "not ready"),
        ), patch.object(
            prospect,
            "list_prospects",
            return_value=[candidate],
        ), patch.object(
            prospect,
            "contact_table_status",
            return_value=(True, "ready"),
        ), patch.object(
            prospect,
            "list_contacts_for_prospects",
            return_value=[],
        ), patch.object(
            prospect,
            "_excel_bytes",
            return_value=b"fixture",
        ), patch.object(
            prospect.st,
            "markdown",
        ), patch.object(
            prospect.st,
            "caption",
        ), patch.object(
            prospect.st,
            "download_button",
        ), patch.object(
            prospect.st,
            "dataframe",
            side_effect=capture_table,
        ):
            with self.assertRaises(_StopAfterSavedProspectTable):
                prospect._render_clean_saved_prospects(
                    "owner-1",
                    can_view_mobile=True,
                )

        self.assertEqual(captured["frame"].loc[0, "연락처"], phone)
        for column in prospect.OUTREACH_COLUMN_CHANNELS:
            self.assertTrue(pd.isna(captured["frame"].loc[0, column]))

    def test_dnc_row_has_no_send_buttons(self):
        compact = prospect._saved_prospect_table_frame(
            self._frame(blocked=True),
            can_view_mobile=True,
        )

        for column in prospect.OUTREACH_COLUMN_CHANNELS:
            self.assertTrue(pd.isna(compact.loc[0, column]))

    def test_explicit_opt_out_timestamp_blocks_every_channel(self):
        candidate = {
            "id": "prospect-1",
            "company_name": "테스트 업체",
            "business_no": _business_number("000", "00", "00000"),
            "source_data": {},
        }
        contacts = [
            {
                "prospect_id": "prospect-1",
                "contact_type": "email",
                "contact_value": _email("sales"),
                "opt_out_at": "2026-08-04T00:00:00+00:00",
            }
        ]

        frame = prospect._saved_candidate_frame(
            [candidate],
            contacts,
            can_view_mobile=True,
        )

        self.assertTrue(bool(frame.loc[0, "_do_not_contact"]))
        self.assertEqual(frame.loc[0, "이메일"], "")

    def test_mobile_actions_require_mobile_visibility(self):
        compact = prospect._saved_prospect_table_frame(
            self._frame(),
            can_view_mobile=False,
        )

        self.assertTrue(pd.isna(compact.loc[0, "문자보내기"]))
        self.assertTrue(pd.isna(compact.loc[0, "카카오톡보내기"]))

    def test_blocked_assignment_status_has_no_send_buttons(self):
        frame = self._frame()
        frame.loc[0, "_assignment_status"] = "wrong_number"

        compact = prospect._saved_prospect_table_frame(
            frame,
            can_view_mobile=True,
        )

        for column in prospect.OUTREACH_COLUMN_CHANNELS:
            self.assertTrue(pd.isna(compact.loc[0, column]))

    def test_rejected_assignment_status_stays_blocked(self):
        frame = self._frame()
        frame.loc[0, "_assignment_status"] = "rejected"

        compact = prospect._saved_prospect_table_frame(
            frame,
            can_view_mobile=True,
        )

        for column in prospect.OUTREACH_COLUMN_CHANNELS:
            self.assertTrue(pd.isna(compact.loc[0, column]))

    def test_rejected_contact_is_never_selected(self):
        candidate = {
            "id": "prospect-1",
            "company_name": "테스트 업체",
            "source_data": {
                "sales_intelligence_v971": {
                    "phone": _phone("010", "9999", "9999"),
                    "email": _email("legacy"),
                }
            },
        }
        contacts = [
            {
                "prospect_id": "prospect-1",
                "contact_type": "phone",
                "contact_value": _phone("010", "0000", "0000"),
                "verification_status": "rejected",
                "is_primary": True,
            },
            {
                "prospect_id": "prospect-1",
                "contact_type": "email",
                "contact_value": _email("rejected"),
                "verification_status": "rejected",
            },
        ]

        frame = prospect._saved_candidate_frame(
            [candidate],
            contacts,
            can_view_mobile=True,
            canonical_contacts_only=True,
        )

        self.assertEqual(frame.loc[0, "휴대전화"], "")
        self.assertEqual(frame.loc[0, "이메일"], "")
        compact = prospect._saved_prospect_table_frame(
            frame.assign(
                _assignment_id="assignment-1",
                _company_uid="source:" + ("a" * 64),
                _assignment_status="assigned",
            ),
            can_view_mobile=True,
        )
        self.assertTrue(pd.isna(compact.loc[0, "문자보내기"]))

    def test_resolver_honors_legacy_phone_hash_opt_out(self):
        request = {
            "channel": "sms",
            "prospect_id": "prospect-1",
            "company_uid": "business:" + _business_number("123", "45", "67890"),
            "assignment_id": "assignment-1",
        }
        assignment = {
            "assignment_id": "assignment-1",
            "company_id": "prospect-1",
            "company_uid": request["company_uid"],
            "status": "assigned",
        }
        current_frame = pd.DataFrame(
            [
                {
                    "_do_not_contact": False,
                    "휴대전화": _phone("010", "0000", "0000"),
                    "업체명": "테스트 업체",
                    "_company_uid": request["company_uid"],
                    "_assignment_id": "assignment-1",
                    "_canonical_mobile_available": True,
                    "_canonical_mobile_contact_id": "contact-1",
                    "_canonical_mobile_contact_updated_at": (
                        "2026-08-04T00:00:00+00:00"
                    ),
                }
            ]
        )
        with patch.object(
            prospect.sales_assignments,
            "list_user_assignments",
            return_value={"ok": True, "assignments": [assignment]},
        ), patch.object(
            prospect,
            "contact_table_status",
            return_value=(True, "ready"),
        ), patch.object(
            prospect,
            "company_contact_is_suppressed",
            return_value=False,
        ), patch.object(
            prospect,
            "legacy_phone_contact_is_suppressed",
            return_value=True,
        ) as legacy_control, patch.object(
            prospect,
            "list_contacts_for_prospects",
            return_value=[],
        ), patch.object(
            prospect,
            "_saved_candidate_frame",
            return_value=current_frame,
        ):
            result = prospect._resolve_outreach_target(
                "owner-1",
                request,
                can_view_mobile=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DO_NOT_CONTACT")
        legacy_control.assert_called_once_with(
            request["company_uid"],
            _phone("010", "0000", "0000"),
        )

    def test_button_click_creates_only_opaque_request_metadata(self):
        rows = prospect._outreach_action_rows(
            self._frame(),
            can_view_mobile=True,
        )

        with patch.object(
            prospect.secrets,
            "token_urlsafe",
            return_value="opaque-request",
        ):
            request = prospect._outreach_request_from_click(
                {"row": 0, "label": "📧"},
                "email",
                rows,
            )

        self.assertEqual(request["request_id"], "opaque-request")
        self.assertEqual(request["channel"], "email")
        self.assertEqual(
            set(request),
            {
                "request_id",
                "channel",
                "prospect_id",
                "company_uid",
                "assignment_id",
            },
        )
        rendered = repr(request)
        self.assertNotIn("example.invalid", rendered)
        self.assertNotIn("010-", rendered)
        self.assertNotIn("테스트 업체", rendered)

    def test_unavailable_or_invalid_click_is_ignored(self):
        rows = prospect._outreach_action_rows(
            self._frame(blocked=True),
            can_view_mobile=True,
        )

        self.assertEqual(
            prospect._outreach_request_from_click(
                {"row": 0, "label": "💬"},
                "sms",
                rows,
            ),
            {},
        )

    def test_activity_document_click_uses_only_assignment_id(self):
        rows = prospect._outreach_action_rows(
            self._frame(),
            can_view_mobile=True,
        )

        self.assertEqual(
            prospect._activity_assignment_id_from_click({"row": 0}, rows),
            "assignment-1",
        )
        self.assertEqual(
            prospect._activity_assignment_id_from_click({"row": 99}, rows),
            "",
        )
        self.assertEqual(
            prospect._outreach_request_from_click(
                {"row": 100, "label": "📧"},
                "email",
                rows,
            ),
            {},
        )
        self.assertEqual(
            prospect._outreach_request_from_click(
                {"row": -1, "label": "📧"},
                "email",
                rows,
            ),
            {},
        )

    def test_browser_session_claim_blocks_same_request_twice(self):
        state = {}

        self.assertTrue(
            prospect._claim_outreach_attempt(state, "request-12345678")
        )
        self.assertFalse(
            prospect._claim_outreach_attempt(state, "request-12345678")
        )
        prospect._release_outreach_attempt(state, "request-12345678")
        self.assertTrue(
            prospect._claim_outreach_attempt(state, "request-12345678")
        )

    def test_marketing_compliance_requires_explicit_target_confirmation(self):
        self.assertIn(
            "수신 동의",
            prospect._outreach_compliance_error(
                "email",
                "일반 제목",
                "본문",
                confirmed=False,
            ),
        )
        self.assertEqual(
            prospect._outreach_compliance_error(
                "sms",
                "",
                "담당자 자유 입력 본문",
                confirmed=True,
            ),
            "",
        )

    def test_removed_guidance_ui_code_is_absent_from_saved_prospect_module(self):
        source = inspect.getsource(prospect)

        for removed_marker in (
            "_load_company_kakao_guidance",
            "_show_guidance_send_dialog",
            "_render_guidance_admin_readonly",
            "prospect_guidance_message_type",
            "개인사업자 카카오톡 검토신청 안내",
            "카카오톡 검토신청 안내 운영 현황",
        ):
            with self.subTest(removed_marker=removed_marker):
                self.assertNotIn(removed_marker, source)

    def test_durable_reservation_precedes_every_provider_send(self):
        source = inspect.getsource(prospect._show_outreach_dialog)

        reserve_at = source.index("reserve_outreach_attempt(")
        begin_at = source.index("begin_outreach_dispatch(")
        send_at = source.index("sales_outreach.send_outreach(")
        finalize_at = source.index("finalize_outreach_attempt(")
        crm_at = source.index("sales_assignments.record_contact(")
        self.assertLess(reserve_at, begin_at)
        self.assertLess(begin_at, send_at)
        self.assertLess(send_at, finalize_at)
        self.assertLess(finalize_at, crm_at)
        self.assertNotIn("_release_outreach_attempt", source)

    def test_composer_keeps_one_confirmation_without_evidence_upload(self):
        source = inspect.getsource(prospect._show_outreach_dialog)

        self.assertEqual(source.count("st.checkbox("), 1)
        self.assertNotIn("file_uploader", source)
        self.assertNotIn("recording", source.lower())
        self.assertNotIn("evidence", source.lower())

    def test_kakao_composer_selects_allowlisted_template_and_send_number(self):
        source = inspect.getsource(prospect._show_outreach_dialog)

        self.assertIn("claim_auth_alimtalk_readiness", source)
        self.assertIn("claim_auth_alimtalk_templates", source)
        self.assertIn('"알림톡 템플릿"', source)
        self.assertIn('"발송할 휴대폰 번호"', source)
        self.assertIn("저장된 업체 연락처는 변경하지 않습니다", source)
        self.assertIn('"고객이름"', source)
        self.assertIn('"인증링크"', source)
        self.assertIn("http:// 또는 https://는 입력하지 말고", source)
        self.assertIn("final_recipient", source)
        self.assertIn("template_code=selected_template_code", source)
        self.assertIn("발송번호 별도 지정", source)
        self.assertIn("validate_claim_auth_alimtalk", source)
        self.assertIn("send_claim_auth_alimtalk", source)
        self.assertIn("_claim_auth_template_preview", source)
        self.assertIn(
            "claim_auth_alimtalk_template_preview",
            inspect.getsource(prospect._claim_auth_template_preview),
        )
        self.assertIn("render_claim_auth_alimtalk_preview", source)
        self.assertIn('st.markdown("**발송 예시**")', source)
        self.assertIn("outreach_send_window", source)
        self.assertIn('not bool(send_window.get("allowed"))', source)
        self.assertIn('result.get("message")', source)

    def test_saved_prospect_table_forces_stable_column_order(self):
        source = inspect.getsource(prospect._render_clean_saved_prospects)

        self.assertIn(
            "column_order=list(SAVED_PROSPECT_VISIBLE_COLUMNS)",
            source,
        )
        self.assertIn("key=_SAVED_PROSPECT_TABLE_KEY", source)
        self.assertIn('"이력관리": st.column_config.ButtonColumn(', source)
        self.assertIn("_queue_activity_from_button", source)
        self.assertIn("sales_assignments.list_company_contacts(", source)
        self.assertIn("latest_contact_by_uid=latest_contact_by_uid", source)
        self.assertNotIn('"selection_mode": "single-row"', source)

    def test_saved_prospect_progress_uses_latest_contact_result(self):
        self.assertEqual(
            prospect._saved_prospect_progress_label(
                {"status": "assigned"},
                {"contact_result": "missed"},
            ),
            "부재중",
        )
        self.assertEqual(
            prospect._saved_prospect_progress_label(
                {"status": "assigned"},
                None,
            ),
            "신규 배정",
        )

    def test_saved_db_dashboard_has_all_cards_and_server_side_filtering(self):
        cards = prospect.SAVED_DB_DASHBOARD_CARDS
        self.assertEqual(
            [label for _key, label, _metric in cards],
            [
                "총 DB 수량",
                "일반전화 DB",
                "핸드폰번호 DB",
                "신규 배정 DB",
                "연락중인 DB",
                "연락완료 DB",
            ],
        )
        loader_source = inspect.getsource(
            prospect._load_user_dashboard_assignment_rows
        )
        self.assertIn("list_user_db_assignments", loader_source)
        self.assertIn("dashboard_filter=dashboard_filter", loader_source)
        self.assertNotIn("list_user_assignments", loader_source)

    def test_saved_db_filter_and_page_survive_regular_reruns(self):
        selector_source = inspect.getsource(
            prospect._select_saved_db_dashboard_filter
        )
        page_source = inspect.getsource(
            prospect._set_saved_db_dashboard_page
        )
        renderer_source = inspect.getsource(
            prospect._render_clean_saved_prospects
        )
        self.assertIn("_SAVED_DB_DASHBOARD_FILTER_KEY", selector_source)
        self.assertIn("_SAVED_DB_DASHBOARD_PAGE_KEY", selector_source)
        self.assertIn("_CONTACT_RESULTS_SELECTION_KEY", selector_source)
        self.assertIn("_CONTACT_RESULTS_SELECTION_KEY", page_source)
        self.assertIn("_SAVED_PROSPECT_TABLE_KEY", selector_source)
        self.assertIn("_SAVED_PROSPECT_TABLE_KEY", page_source)
        self.assertIn("_ACTIVITY_DIALOG_REQUEST_KEY", selector_source)
        self.assertIn("_ACTIVITY_DIALOG_REQUEST_KEY", page_source)
        self.assertIn("_saved_db_dashboard_filter()", renderer_source)
        self.assertNotIn("현재 목록:", renderer_source)
        self.assertNotIn('saved_db_dashboard_reset_v1100', renderer_source)
        self.assertIn("_set_saved_db_dashboard_page", renderer_source)
        self.assertIn('dashboard_result.get("legacy_fallback")', renderer_source)
        self.assertIn("list_prospects(", renderer_source)
        self.assertIn("목록 조회를 중단했습니다", renderer_source)

    def test_dashboard_refreshes_without_client_cache(self):
        source = inspect.getsource(prospect._load_user_db_dashboard)
        self.assertIn("get_user_db_dashboard", source)
        self.assertNotIn("cache_data", source)

    def test_resolver_rejects_a_stale_or_unowned_assignment(self):
        request = {
            "channel": "email",
            "prospect_id": "prospect-1",
            "company_uid": "source:" + ("a" * 64),
            "assignment_id": "assignment-1",
        }
        with patch.object(
            prospect.sales_assignments,
            "list_user_assignments",
            return_value={"ok": True, "assignments": []},
        ), patch.object(
            prospect,
            "list_contacts_for_prospects",
        ) as contacts:
            result = prospect._resolve_outreach_target(
                "owner-1",
                request,
                can_view_mobile=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "TARGET_NOT_OWNED")
        contacts.assert_not_called()

    def test_resolver_rechecks_latest_do_not_contact_state(self):
        request = {
            "channel": "sms",
            "prospect_id": "prospect-1",
            "company_uid": "source:" + ("a" * 64),
            "assignment_id": "assignment-1",
        }
        assignment = {
            "assignment_id": "assignment-1",
            "company_id": "prospect-1",
            "company_uid": request["company_uid"],
            "status": "assigned",
        }
        blocked_frame = pd.DataFrame(
            [
                {
                    "_do_not_contact": True,
                    "휴대전화": _phone("010", "0000", "0000"),
                }
            ]
        )
        with patch.object(
            prospect.sales_assignments,
            "list_user_assignments",
            return_value={"ok": True, "assignments": [assignment]},
        ), patch.object(
            prospect,
            "contact_table_status",
            return_value=(True, "ready"),
        ), patch.object(
            prospect,
            "company_contact_is_suppressed",
            return_value=False,
        ), patch.object(
            prospect,
            "list_contacts_for_prospects",
            return_value=[],
        ), patch.object(
            prospect,
            "_saved_candidate_frame",
            return_value=blocked_frame,
        ):
            result = prospect._resolve_outreach_target(
                "owner-1",
                request,
                can_view_mobile=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DO_NOT_CONTACT")

    def test_resolver_uses_latest_recipient_without_storing_it_in_request(self):
        request = {
            "channel": "email",
            "prospect_id": "prospect-1",
            "company_uid": "source:" + ("a" * 64),
            "assignment_id": "assignment-1",
        }
        assignment = {
            "assignment_id": "assignment-1",
            "company_id": "prospect-1",
            "company_uid": request["company_uid"],
            "status": "assigned",
        }
        current_frame = pd.DataFrame(
            [
                {
                    "_do_not_contact": False,
                    "이메일": _email("current"),
                    "업체명": "테스트 업체",
                    "_company_uid": request["company_uid"],
                    "_assignment_id": "assignment-1",
                    "_canonical_email_available": True,
                    "_canonical_email_contact_id": "contact-2",
                    "_canonical_email_contact_updated_at": (
                        "2026-08-04T00:00:00+00:00"
                    ),
                }
            ]
        )
        with patch.object(
            prospect.sales_assignments,
            "list_user_assignments",
            return_value={"ok": True, "assignments": [assignment]},
        ), patch.object(
            prospect,
            "contact_table_status",
            return_value=(True, "ready"),
        ), patch.object(
            prospect,
            "company_contact_is_suppressed",
            return_value=False,
        ), patch.object(
            prospect,
            "list_contacts_for_prospects",
            return_value=[],
        ), patch.object(
            prospect,
            "_saved_candidate_frame",
            return_value=current_frame,
        ):
            result = prospect._resolve_outreach_target(
                "owner-1",
                request,
                can_view_mobile=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["recipient"], _email("current"))
        self.assertNotIn("recipient", request)

    def test_resolver_blocks_suppression_on_a_duplicate_company_row(self):
        request = {
            "channel": "kakao",
            "prospect_id": "prospect-1",
            "company_uid": "source:" + ("a" * 64),
            "assignment_id": "assignment-1",
        }
        assignment = {
            "assignment_id": "assignment-1",
            "company_id": "prospect-1",
            "company_uid": request["company_uid"],
            "status": "assigned",
        }
        with patch.object(
            prospect.sales_assignments,
            "list_user_assignments",
            return_value={"ok": True, "assignments": [assignment]},
        ), patch.object(
            prospect,
            "contact_table_status",
            return_value=(True, "ready"),
        ), patch.object(
            prospect,
            "company_contact_is_suppressed",
            return_value=True,
        ), patch.object(
            prospect,
            "list_contacts_for_prospects",
        ) as contacts:
            result = prospect._resolve_outreach_target(
                "owner-1",
                request,
                can_view_mobile=True,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DO_NOT_CONTACT")
        contacts.assert_not_called()


if __name__ == "__main__":
    unittest.main()
