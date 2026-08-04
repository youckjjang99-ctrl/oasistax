import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_source(file_name: str, function_name: str) -> str:
    source = (ROOT / file_name).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function not found: {file_name}:{function_name}")


class RerunReductionTests(unittest.TestCase):
    def test_enterprise_crm_save_reuses_fresh_record_without_second_rerun(self):
        source = _function_source(
            "enterprise_center.py",
            "render_enterprise_management_center",
        )
        self.assertNotIn("st.rerun()", source)
        self.assertIn("crm_record = updated_crm", source)
        self.assertIn("crm_profile = profile", source)

    def test_saved_prospect_analysis_renders_results_in_same_run(self):
        source = _function_source(
            "prospect_db_center.py",
            "_render_prospect_db_center_legacy",
        )
        analysis_start = source.index("if analyze_saved_clicked:")
        enrichment_start = source.index("if enrich_saved_clicked:", analysis_start)
        analysis_block = source[analysis_start:enrichment_start]
        self.assertIn('st.session_state["sales_analysis_results_v971"]', analysis_block)
        self.assertNotIn("st.rerun()", analysis_block)

    def test_prospect_search_completion_does_not_trigger_second_full_rerun(self):
        source = _function_source(
            "prospect_db_center.py",
            "render_prospect_db_center",
        )
        marker = 'st.session_state["_prospect_workflow_step_pending_v1020"]'
        start = source.index(marker)
        end = source.index('if workflow_step != "② 검색 결과":', start)
        completion_block = source[start:end]
        self.assertIn('workflow_step = "② 검색 결과"', completion_block)
        self.assertNotIn("st.rerun()", completion_block)

    def test_customer_crm_mutations_do_not_start_a_second_full_rerun(self):
        source = _function_source("app.py", "render_customer_management_page")
        # The one retained rerun refreshes the workbook-backed customer edit.
        self.assertEqual(source.count("st.rerun()"), 1)
        crm_start = source.index("if submitted:")
        timeline_start = source.index('with st.expander("타임라인 추가"', crm_start)
        timeline_end = source.index('st.markdown("##### 상담 타임라인")', timeline_start)
        self.assertNotIn("st.rerun()", source[crm_start:timeline_end])
        self.assertIn("crm_record = updated_crm", source[crm_start:timeline_start])

    def test_member_approval_uses_callback_before_natural_rerun(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        callback_source = _function_source("app.py", "_handle_pending_user_action")
        self.assertIn("approve_user(", callback_source)
        self.assertIn("reject_user(", callback_source)
        self.assertNotIn("st.rerun()", callback_source)

        admin_start = app_source.index(
            'elif CURRENT_USER_IS_ADMIN and active_tab == "회원 승인 관리":'
        )
        admin_end = app_source.index(
            'elif CURRENT_USER_IS_ADMIN and active_tab == "시스템 관리":',
            admin_start,
        )
        admin_source = app_source[admin_start:admin_end]
        self.assertEqual(
            admin_source.count("on_click=_handle_pending_user_action"),
            2,
        )
        self.assertNotIn("st.rerun()", admin_source)

    def test_only_required_explicit_reruns_remain_in_audited_files(self):
        expected_counts = {
            "app.py": 1,  # workbook-backed customer edit refresh
            "enterprise_center.py": 0,
            # Three existing data refreshes, two intentional free-form
            # outreach exits, and two contact-result refreshes after a
            # successful record or temporary-assignment release.
            "prospect_db_center.py": 7,
            # Auth refresh, two soft-delete dialog exits, and the existing
            # dialog close.  These are user actions, not polling reruns.
            "claim_correction_center.py": 4,
        }
        for file_name, expected in expected_counts.items():
            with self.subTest(file_name=file_name):
                source = (ROOT / file_name).read_text(encoding="utf-8")
                self.assertEqual(source.count("st.rerun()"), expected)


if __name__ == "__main__":
    unittest.main()
