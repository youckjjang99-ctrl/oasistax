import inspect
import unittest

from claim_correction_center import (
    _render_auto_claim_monitor,
    render_claim_correction_center,
)
from prospect_db_center import (
    _merge_result_page_selection,
    _result_page_window,
    render_prospect_db_center,
)


class PartialRenderingPerformanceTests(unittest.TestCase):
    def test_claim_center_renders_only_the_selected_section(self):
        source = inspect.getsource(render_claim_correction_center)

        self.assertNotIn("st.tabs(", source)
        self.assertIn("st.segmented_control(", source)
        self.assertIn(
            '["인증 요청", "진행상황", "수집결과", "수집 항목"]',
            source,
        )
        self.assertIn('if active_section == "인증 요청":', source)
        self.assertIn('elif active_section == "진행상황":', source)
        self.assertIn('elif active_section == "수집결과":', source)

    def test_claim_monitor_uses_three_second_fragment_interval(self):
        source = inspect.getsource(_render_auto_claim_monitor)

        self.assertIn('@st.fragment(run_every="3s")', source)

    def test_result_page_window_clamps_page_and_limits_rows(self):
        self.assertEqual(_result_page_window(0, 10, 50), (1, 1, 0, 0))
        self.assertEqual(
            _result_page_window(120, 3, 50),
            (3, 3, 100, 120),
        )
        self.assertEqual(
            _result_page_window(120, 99, 25),
            (5, 5, 100, 120),
        )

    def test_page_selection_merge_preserves_other_pages(self):
        selected = {"page-1-a", "page-2-a", "page-3-a"}
        merged = _merge_result_page_selection(
            selected,
            {"page-2-a", "page-2-b"},
            {"page-2-b"},
        )

        self.assertEqual(
            merged,
            {"page-1-a", "page-2-b", "page-3-a"},
        )

    def test_prospect_editor_only_receives_the_visible_page(self):
        source = inspect.getsource(render_prospect_db_center)

        self.assertIn("PROSPECT_RESULT_PAGE_SIZE_OPTIONS", source)
        self.assertIn("page_display[visible_columns]", source)
        self.assertIn("_merge_result_page_selection(", source)
        self.assertIn("이번 발굴결과 엑셀 다운로드", source)


if __name__ == "__main__":
    unittest.main()
