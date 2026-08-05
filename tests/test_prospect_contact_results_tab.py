import inspect
import unittest
from unittest.mock import patch

import prospect_db_center as prospect


class ProspectContactResultsTabTests(unittest.TestCase):
    def test_navigation_exposes_four_steps_in_order(self):
        source = inspect.getsource(prospect.render_prospect_db_center)
        labels = (
            "① 조건 설정",
            "② 검색 결과",
            "③ 저장된 영업후보",
            "④ 연락결과 기록",
        )

        positions = [source.index(f'"{label}"') for label in labels]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(
            'if workflow_step == "④ 연락결과 기록":',
            source,
        )
        self.assertIn("_render_contact_results(owner_user_id)", source)

    def test_saved_prospect_tab_no_longer_renders_contact_management(self):
        source = inspect.getsource(prospect._render_clean_saved_prospects)

        for moved_marker in (
            "연락결과를 기록할 업체",
            "연락결과 저장",
            "내 연락이력",
            "자동 발송 이력",
            "미접촉 임시 배정 해제",
            "record_contact(",
            "list_company_contacts(",
            "release_assignment(",
        ):
            with self.subTest(moved_marker=moved_marker):
                self.assertNotIn(moved_marker, source)

        for retained_marker in (
            "저장된 영업후보 엑셀 다운로드",
            "saved_prospect_compact_table_v1040",
            "_show_outreach_dialog(",
            "업체 메모 관리",
        ):
            with self.subTest(retained_marker=retained_marker):
                self.assertIn(retained_marker, source)

    def test_contact_results_tab_contains_the_complete_moved_area_once(self):
        source = inspect.getsource(prospect._render_contact_results)

        for marker in (
            "### 연락결과 기록",
            "연락결과를 기록할 업체",
            '"반납"',
            "연락방식",
            "연락결과",
            "다음 연락예정일 지정",
            "다음 연락예정일",
            "상담내용",
            "연락결과 저장",
            "내 연락이력",
            "자동 발송 이력",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, source)

        self.assertEqual(source.count('st.markdown("### 연락결과 기록")'), 1)
        self.assertIn("sales_assignments.record_contact(", source)
        self.assertIn("sales_assignments.list_company_contacts(", source)
        self.assertIn("sales_assignments.release_assignment(", source)
        self.assertIn('reason="contact_results_return"', source)
        self.assertNotIn("_show_outreach_dialog(", source)

    def test_return_button_is_adjacent_to_assignment_selector(self):
        source = inspect.getsource(prospect._render_contact_results)

        columns_at = source.index("selection_col, return_col = st.columns(")
        selector_at = source.index("with selection_col:", columns_at)
        return_at = source.index("with return_col:", selector_at)
        button_at = source.index('"반납"', return_at)
        release_at = source.index(
            "sales_assignments.release_assignment(",
            button_at,
        )

        self.assertLess(columns_at, selector_at)
        self.assertLess(selector_at, return_at)
        self.assertLess(return_at, button_at)
        self.assertLess(button_at, release_at)
        self.assertIn("_CONTACT_RESULTS_RESET_SELECTION_KEY", source)

    def test_next_contact_widgets_can_rerun_outside_the_form(self):
        source = inspect.getsource(prospect._render_contact_results)

        schedule_at = source.index("schedule_next_contact = st.checkbox(")
        date_at = source.index("next_contact_date = st.date_input(")
        form_at = source.index(
            'with st.form("contact_results_record_form_v1050"',
        )

        self.assertLess(schedule_at, date_at)
        self.assertLess(date_at, form_at)
        self.assertIn("value=True", source[schedule_at:date_at])
        self.assertIn("disabled=not schedule_next_contact", source)

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


if __name__ == "__main__":
    unittest.main()
