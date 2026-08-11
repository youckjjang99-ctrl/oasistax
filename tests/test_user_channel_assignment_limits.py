from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import prospect_db_center as prospect


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260811191104_user_channel_assignment_limits.sql"
)


class UserChannelAssignmentLimitsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_settings_store_separate_channel_limits_with_safe_defaults(self):
        self.assertIn("max_landline_db integer not null default 30", self.sql)
        self.assertIn("max_mobile_db integer not null default 30", self.sql)
        self.assertIn("max_landline_db between 1 and 1000", self.sql)
        self.assertIn("max_mobile_db between 1 and 1000", self.sql)

    def test_atomic_assignment_uses_user_specific_channel_limits(self):
        self.assertIn("v_total_limit := v_landline_limit + v_mobile_limit", self.sql)
        self.assertIn("v_active_total >= v_total_limit", self.sql)
        self.assertIn("v_active_channel >= v_channel_limit", self.sql)
        self.assertNotIn("v_active_channel >= 30", self.sql)
        self.assertIn("pg_advisory_xact_lock", self.sql)
        self.assertIn("mobile_limit_reached", self.sql)
        self.assertIn("landline_limit_reached", self.sql)

    def test_admin_only_read_and_update_rpcs_are_service_role_only(self):
        self.assertIn("oasis_sales_actor_is_admin", self.sql)
        self.assertIn("oasis_get_sales_user_limits", self.sql)
        self.assertIn("oasis_admin_set_sales_user_limit", self.sql)
        self.assertIn("from public, anon, authenticated", self.sql)
        self.assertIn("to service_role", self.sql)

    def test_admin_ui_loads_and_edits_all_three_limits(self):
        source = inspect.getsource(prospect.render_company_assignment_admin)
        self.assertIn("get_user_limits", source)
        self.assertIn("미접촉 배정 한도", source)
        self.assertIn("일반전화 DB 한도", source)
        self.assertIn("핸드폰 DB 한도", source)
        self.assertIn("max_landline_db", source)
        self.assertIn("max_mobile_db", source)

    def test_mobile_admin_surfaces_the_first_atomic_limit_failure(self):
        source = inspect.getsource(prospect._render_mobile_db_admin)
        self.assertIn("first_failure", source)
        self.assertIn('save_result.get("results")', source)
        self.assertIn('first_failure.get("message")', source)


if __name__ == "__main__":
    unittest.main()
