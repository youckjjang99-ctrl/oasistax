from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260811150000_add_channel_limits_and_return_reason.sql"
)


class AssignmentChannelLimitsAndReturnsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sql = MIGRATION.read_text(encoding="utf-8").lower()

    def test_total_and_per_channel_limits_are_atomic(self):
        self.assertIn("alter column max_uncontacted set default 60", self.sql)
        self.assertIn("v_active_total >= 60", self.sql)
        self.assertIn("v_active_channel >= 30", self.sql)
        self.assertIn("'landline', 'mobile'", self.sql)
        self.assertIn("pg_advisory_xact_lock", self.sql)
        self.assertIn("total_db_limit_reached", self.sql)
        self.assertIn("landline_limit_reached", self.sql)
        self.assertIn("mobile_limit_reached", self.sql)

    def test_limit_count_excludes_inactive_assignments(self):
        self.assertIn("a.assigned_user_id = v_user_id", self.sql)
        self.assertIn("a.released_at is null", self.sql)
        self.assertIn("coalesce(a.permanently_excluded, false) is false", self.sql)
        self.assertIn("'unassigned', 'long_hold', 'permanently_excluded'", self.sql)
        self.assertIn("a.assignment_expires_at > now()", self.sql)

    def test_allocation_channel_is_forwarded_inside_transaction(self):
        self.assertIn("current_setting('oasis.allocation_channel', true)", self.sql)
        self.assertIn("set_config('oasis.allocation_channel', v_channel, true)", self.sql)
        self.assertIn("source_data ->> 'allocation_channel'", self.sql)

    def test_return_reason_is_required_and_audited(self):
        self.assertIn("p_return_reason text default null", self.sql)
        self.assertIn("return_reason_required", self.sql)
        self.assertIn("'return_reason', nullif(v_return_reason, '')", self.sql)
        self.assertIn("left(btrim(coalesce(p_return_reason, '')), 500)", self.sql)

    def test_rpc_access_remains_service_role_only(self):
        self.assertIn("from public, anon, authenticated", self.sql)
        self.assertIn("to service_role", self.sql)


if __name__ == "__main__":
    unittest.main()
