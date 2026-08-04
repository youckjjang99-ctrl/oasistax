import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import crm
import utils
import cloud_crm_restore
from cloud_db import CloudDatabase
from performance_cache import cache_generation, invalidate_cache


ROOT = Path(__file__).resolve().parents[1]


class CrmPerformanceTests(unittest.TestCase):
    def test_app_does_not_eager_import_heavy_menu_modules(self):
        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        top_level_modules = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top_level_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level_modules.add(node.module)

        heavy_modules = {
            "claim_correction_center",
            "consulting_copilot",
            "enterprise_center",
            "prospect_db_center",
            "registered_policy_match",
            "multi_source_policy",
            "stock_valuation",
            "cloud_admin",
            "maintenance",
        }
        self.assertFalse(top_level_modules.intersection(heavy_modules))

    def test_home_navigation_uses_callbacks_without_explicit_rerun(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        start = source.index("def render_home_page")
        end = source.index("def format_customer_display_value", start)
        home_source = source[start:end]
        self.assertIn("on_click=_navigate_to_main_menu", home_source)
        self.assertNotIn("st.rerun()", home_source)

    def test_current_customer_workbook_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "customers.xlsx"
            columns = utils.get_customer_db_columns()
            row = {column: "" for column in columns}
            row["업체명"] = "성능테스트"
            utils._write_cumulative_customer_db(
                path,
                pd.DataFrame([row]),
                columns,
            )
            before = path.stat().st_mtime_ns

            with patch("utils.get_user_cumulative_db_path", return_value=path):
                resolved, count, converted = utils.ensure_user_cumulative_db_format(
                    "test-user"
                )

            self.assertEqual(resolved, path)
            self.assertEqual(count, 1)
            self.assertFalse(converted)
            self.assertEqual(before, path.stat().st_mtime_ns)

    def test_crm_file_cache_is_invalidated_for_only_saved_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "crm.json"
            path.write_text(json.dumps({"customers": {}}), encoding="utf-8")
            with patch("crm.get_crm_file_path", return_value=path):
                self.assertEqual(crm.load_crm_data("owner")["customers"], {})
                crm.save_crm_data(
                    "owner",
                    {"customers": {"company:test": {"status": "신규"}}},
                )
                loaded = crm.load_crm_data("owner")
            self.assertIn("company:test", loaded["customers"])

    def test_generation_invalidation_is_scope_specific(self):
        before_a = cache_generation("customers", "a")
        before_b = cache_generation("customers", "b")
        invalidate_cache("customers", "a")
        self.assertEqual(cache_generation("customers", "a"), before_a + 1)
        self.assertEqual(cache_generation("customers", "b"), before_b)

    def test_select_all_uses_bounded_server_pages(self):
        database = object.__new__(CloudDatabase)
        batches = [[{"id": 1}, {"id": 2}], [{"id": 3}]]
        with patch.object(database, "select", side_effect=batches) as select:
            rows = database.select_all(
                "example",
                order="id.asc",
                page_size=2,
                max_rows=10,
            )
        self.assertEqual([row["id"] for row in rows], [1, 2, 3])
        self.assertEqual(select.call_args_list[0].kwargs["offset"], 0)
        self.assertEqual(select.call_args_list[1].kwargs["offset"], 2)

    def test_select_all_honors_max_rows_below_page_size(self):
        database = object.__new__(CloudDatabase)
        with patch.object(
            database,
            "select",
            return_value=[{"id": 1}, {"id": 2}, {"id": 3}],
        ) as select:
            rows = database.select_all(
                "example",
                order="id.asc",
                page_size=1000,
                max_rows=3,
            )
        self.assertEqual(len(rows), 3)
        self.assertEqual(select.call_args.kwargs["limit"], 3)

    def test_mobile_stale_tree_does_not_fade_to_white(self):
        source = (ROOT / "ui.py").read_text(encoding="utf-8")
        self.assertIn('[data-stale="true"]', source)
        self.assertIn("opacity: 1 !important", source)

    def test_existing_local_crm_skips_cloud_restore_queries(self):
        with patch(
            "cloud_crm_restore.load_crm_data",
            return_value={"customers": {"company:a": {"status": "상담중"}}},
        ), patch("cloud_crm_restore.CloudDatabase") as database:
            result = cloud_crm_restore.restore_crm_from_cloud("owner")
        self.assertEqual(result["restored"], 0)
        database.assert_not_called()

    def test_cloud_crm_restore_failure_does_not_expose_raw_error(self):
        private_error = "private-service-token-detail"
        with patch(
            "cloud_crm_restore.load_crm_data",
            return_value={"customers": {}},
        ), patch(
            "cloud_crm_restore.cloud_is_configured",
            return_value=True,
        ), patch(
            "cloud_crm_restore.CloudDatabase",
            side_effect=RuntimeError(private_error),
        ):
            result = cloud_crm_restore.restore_crm_from_cloud("owner")

        self.assertEqual(result["message"], "Supabase CRM 조회 실패")
        self.assertNotIn(private_error, repr(result))

    def test_customer_template_never_falls_back_to_live_root_database(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        start = app_source.index("def get_customer_template_download")
        end = app_source.index("def validate_customer_workbook", start)
        function_source = app_source[start:end]
        self.assertIn("find_customer_template()", function_source)
        self.assertNotIn('BASE_DIR / "고객DB.xlsx"', function_source)

        utils_source = (ROOT / "utils.py").read_text(encoding="utf-8")
        start = utils_source.index("def find_customer_template")
        end = utils_source.index("def make_basic_customer_template_bytes", start)
        function_source = utils_source[start:end]
        self.assertNotIn("ROOT_DIR.glob", function_source)
        self.assertNotIn('ROOT_DIR / "고객DB.xlsx"', function_source)


if __name__ == "__main__":
    unittest.main()
