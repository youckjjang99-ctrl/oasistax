from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NavigationVisibilityTests(unittest.TestCase):
    def test_sidebar_does_not_repeat_selected_group_badges(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")

        self.assertIn('selected_group = st.pills(\n            "업무 구분"', source)
        self.assertIn('selected_menu_label = st.radio(\n            "세부 메뉴"', source)
        self.assertGreaterEqual(source.count('label_visibility="visible"'), 2)
        self.assertNotIn("sidebar-nav-heading", source)
        self.assertNotIn("<small>1단계</small>", source)
        self.assertNotIn("<small>{html.escape(selected_group)}</small>", source)
        self.assertIn('"경정청구": "경정청구"', source)

    def test_enterprise_tools_are_visible_in_one_tab_row(self):
        source = (ROOT / "enterprise_center.py").read_text(encoding="utf-8")
        expected_labels = [
            "기업정보",
            "CRM",
            "정책자금",
            "주가평가·등기",
            "정관검토",
            "기업히스토리",
            "직원현황",
            "가지급금 계산기",
        ]

        for label in expected_labels:
            self.assertIn(f'"{label}"', source)
        self.assertNotIn("tab_diagnosis_group", source)
        self.assertNotIn("tab_consulting_group", source)
        self.assertNotIn("tab_documents_group", source)


if __name__ == "__main__":
    unittest.main()
