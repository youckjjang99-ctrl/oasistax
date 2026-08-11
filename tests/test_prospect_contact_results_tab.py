import inspect
import unittest
from unittest.mock import patch

import prospect_db_center as prospect


class ProspectContactResultsTabTests(unittest.TestCase):
    def test_navigation_integrates_contact_results_and_keeps_admin_review(self):
        source = inspect.getsource(prospect.render_prospect_db_center)
        labels = (
            "① DB신청",
            "② 저장된 영업후보",
            "③ 반납DB 관리",
            "④ 핸드폰DB 관리",
        )

        positions = [source.index(f'"{label}"') for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("검색 결과\"", source)
        self.assertNotIn('"③ 연락결과 기록",', source)
        self.assertNotIn('if workflow_step == "③ 연락결과 기록":', source)
        self.assertNotIn("_render_contact_results(", source)
        self.assertIn("if is_admin_user:", source)
        self.assertIn('workflow_steps.append("③ 반납DB 관리")', source)
        self.assertIn('workflow_steps.append("④ 핸드폰DB 관리")', source)
        self.assertIn(
            'if workflow_step == "③ 반납DB 관리":',
            source,
        )
        self.assertIn("_render_return_db_admin(owner_user_id)", source)

    def test_saved_prospect_table_opens_integrated_activity_dialog(self):
        source = inspect.getsource(prospect._render_clean_saved_prospects)

        for retained_marker in (
            "저장된 영업후보 엑셀 다운로드",
            "_SAVED_PROSPECT_TABLE_KEY",
            "_show_outreach_dialog(",
            '"이력관리": st.column_config.ButtonColumn(',
            "_queue_activity_from_button",
            "_ACTIVITY_DIALOG_REQUEST_KEY",
            "_show_company_activity_dialog(",
        ):
            with self.subTest(retained_marker=retained_marker):
                self.assertIn(retained_marker, source)
        self.assertNotIn('"selection_mode": "single-row"', source)
        self.assertNotIn('st.expander("업체 연락결과 관리"', source)

    def test_contact_results_tab_contains_the_complete_moved_area_once(self):
        source = inspect.getsource(prospect._render_contact_results)

        for marker in (
            "### 연락결과 기록",
            "연락결과를 기록할 업체",
            '"DB 반납하기"',
            "연락방식",
            "연락결과",
            "다음 연락예정일 지정",
            "다음 연락예정일",
            "상담내용",
            "연락결과 저장",
            "자동 발송 이력",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

        self.assertEqual(source.count('"### 연락결과 기록"'), 1)
        self.assertIn("sales_assignments.record_contact(", source)
        self.assertIn("sales_assignments.list_company_contacts(", source)
        self.assertIn("sales_assignments.release_assignment(", source)
        self.assertIn('reason="contact_results_return"', source)
        self.assertIn("return_reason=return_reason.strip()", source)
        self.assertIn('"반납사유"', source)
        self.assertNotIn("sales_assignments.save_user_note(", source)
        self.assertNotIn("업체 메모", source)
        self.assertNotIn("메모 저장", source)
        self.assertNotIn("_show_outreach_dialog(", source)

    def test_dashboard_filter_rows_are_used_to_resolve_dialog_assignment(self):
        source = inspect.getsource(prospect._render_clean_saved_prospects)

        filter_load_at = source.index(
            "_load_user_dashboard_assignment_rows("
        )
        resolver_at = source.index("assignment_by_id = {", filter_load_at)
        dialog_at = source.index(
            "_show_company_activity_dialog(",
            resolver_at,
        )

        self.assertLess(filter_load_at, resolver_at)
        self.assertLess(resolver_at, dialog_at)
        self.assertIn("selected_filter", source[filter_load_at:resolver_at])
        self.assertIn("for row in rows", source[resolver_at:dialog_at])

    def test_activity_dialog_is_large_dismissible_and_uses_one_assignment(self):
        source = inspect.getsource(prospect._show_company_activity_dialog)

        for marker in (
            '@st.dialog(',
            '"업체 활동 관리"',
            'width="large"',
            "on_dismiss=_dismiss_company_activity_dialog",
            "assignment_rows=[assignment]",
            "selected_assignment_id=assignment_id",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_company_selector_is_an_aligned_single_row_table(self):
        source = inspect.getsource(prospect._render_contact_results)

        selector_at = source.index("selection_event = st.dataframe(")
        history_at = source.index('st.markdown("#### 업체 활동 이력")')

        self.assertLess(selector_at, history_at)
        self.assertIn(
            '["업체명", "기업유형", "연락현황", "연락처"]',
            source[selector_at:history_at],
        )
        self.assertIn('selection_mode="single-row"', source)
        self.assertIn('on_select="rerun"', source)

    def test_return_button_is_below_contact_save(self):
        source = inspect.getsource(prospect._render_contact_results)

        save_at = source.index('"연락결과 저장"')
        return_at = source.index('st.markdown("##### DB 반납")', save_at)
        button_at = source.index('"DB 반납하기"', return_at)
        release_at = source.index(
            "sales_assignments.release_assignment(",
            button_at,
        )

        self.assertLess(save_at, return_at)
        self.assertLess(return_at, button_at)
        self.assertLess(button_at, release_at)
        self.assertIn("_CONTACT_RESULTS_RESET_SELECTION_KEY", source)

    def test_next_contact_widgets_can_rerun_outside_the_form(self):
        source = inspect.getsource(prospect._render_contact_results)

        schedule_at = source.index("schedule_next_contact = st.checkbox(")
        date_at = source.index("next_contact_date = st.date_input(")
        form_at = source.index("with st.form(")

        self.assertLess(schedule_at, date_at)
        self.assertLess(date_at, form_at)
        self.assertIn("value=True", source[schedule_at:date_at])
        self.assertIn("disabled=not schedule_next_contact", source)

    def test_return_requires_explicit_confirmation_inside_activity_dialog(self):
        source = inspect.getsource(prospect._render_contact_results)

        self.assertIn("return_confirmed = st.checkbox(", source)
        self.assertIn("이 업체를 내 DB에서 반납하는 내용을 확인했습니다.", source)
        self.assertIn(
            "disabled=not selected_company_uid or not return_confirmed",
            source,
        )
        self.assertIn("_SAVED_PROSPECT_RESET_SELECTION_KEY", source)

    def test_assignment_loader_is_scoped_to_the_current_user(self):
        assignment = {
            "assignment_id": "assignment-1",
            "company_id": "company-1",
            "company_uid": "source:company-1",
            "company_name": "테스트 업체",
            "own_memo": "내 메모",
            "status": "assigned",
        }
        with patch.object(
            prospect,
            "_assignment_feature_status",
            return_value=(True, "ready"),
        ), patch.object(
            prospect,
            "_release_expired_assignments_if_due",
        ) as release_expired, patch.object(
            prospect.sales_assignments,
            "list_user_assignments",
            return_value={"ok": True, "assignments": [assignment]},
        ) as list_assignments:
            result = prospect._load_user_assignment_rows("owner-a")

        self.assertTrue(result["ok"])
        list_assignments.assert_called_once_with("owner-a", limit=1000)
        release_expired.assert_called_once_with("owner-a")
        self.assertEqual(result["rows"][0]["id"], "company-1")
        self.assertEqual(result["rows"][0]["memo"], "내 메모")

    def test_return_review_rows_include_only_quarantined_contact_returns(self):
        assignments = [
            {
                "assignment_id": "return-1",
                "company_uid": "source:return-1",
                "status": "long_hold",
                "released_reason": "contact_results_return",
                "released_at": "2026-08-05T10:00:00+09:00",
                "permanently_excluded": False,
            },
            {
                "assignment_id": "other-hold",
                "company_uid": "source:other-hold",
                "status": "long_hold",
                "released_reason": "manual_hold",
            },
            {
                "assignment_id": "already-reviewed",
                "company_uid": "source:reviewed",
                "status": "unassigned",
                "released_reason": "admin_reactivated:approved",
            },
        ]
        audit_rows = [
            {
                "company_uid": "source:return-1",
                "action": "admin_recall",
                "new_value": {
                    "reason": "contact_results_return",
                    "return_reason": "대상 조건 불일치",
                },
                "user_name": "영업담당자",
                "created_at": "2026-08-05T10:00:01+09:00",
            }
        ]

        rows = prospect._return_db_review_rows(assignments, audit_rows)

        self.assertEqual([row["assignment_id"] for row in rows], ["return-1"])
        self.assertEqual(rows[0]["_returned_by_name"], "영업담당자")
        self.assertEqual(
            rows[0]["_returned_at"],
            "2026-08-05T10:00:01+09:00",
        )
        self.assertEqual(rows[0]["_return_reason"], "대상 조건 불일치")

    def test_return_db_admin_uses_admin_guard_and_review_actions(self):
        source = inspect.getsource(prospect._render_return_db_admin)

        for marker in (
            "@st.fragment",
            "from auth import is_admin",
            "관리자만 반납 DB를 확인할 수 있습니다.",
            'statuses=["long_hold"]',
            "sales_assignments.list_admin_assignment_audit(",
            '"반납사유"',
            "st.data_editor(",
            '"선택": st.column_config.CheckboxColumn(',
            "_RETURN_DB_ADMIN_SELECTION_KEY",
            "_RETURN_DB_ADMIN_ROW_SIGNATURE_KEY",
            '"선택": False',
            "on_change=_sync_result_editor_selection",
            "set()",
            "재배정 허용",
            "영구 제외",
            "sales_assignments.admin_review_returned_batch(",
            "company_uids",
            "processed_count",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

    def test_contact_save_success_forces_fresh_assignment_and_history_queries(self):
        source = inspect.getsource(prospect._render_contact_results)
        record_at = source.index("sales_assignments.record_contact(")
        flash_at = source.index(
            "st.session_state[_CONTACT_RESULTS_FLASH_KEY]",
            record_at,
        )
        rerun_at = source.index("st.rerun()", flash_at)

        self.assertLess(record_at, flash_at)
        self.assertLess(flash_at, rerun_at)
        self.assertIn("_load_user_assignment_rows(owner_user_id)", source)
        self.assertIn("clear_on_submit=True", source)
        self.assertIn("key=_CONTACT_RESULTS_SELECTION_KEY", source)

    def test_return_success_is_shown_on_saved_list_as_banner_and_toast(self):
        contact_source = inspect.getsource(prospect._render_contact_results)
        saved_list_source = inspect.getsource(
            prospect._render_clean_saved_prospects
        )
        notice_source = inspect.getsource(prospect._show_contact_results_notice)

        self.assertIn("DB 반납이 완료되었습니다.", contact_source)
        self.assertIn(
            "_show_contact_results_notice(as_toast=True)",
            saved_list_source,
        )
        self.assertIn('st.toast(message, icon="✅")', notice_source)


if __name__ == "__main__":
    unittest.main()
