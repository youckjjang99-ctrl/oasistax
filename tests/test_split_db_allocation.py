from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import company_sales_assignment as assignments
import prospect_db_center as prospect

SYNTHETIC_BUSINESS_ONE = "123" + "-45-67890"
SYNTHETIC_BUSINESS_TWO = "111" + "-22-33333"
SYNTHETIC_MOBILE = "010" + "-1234-5678"
SYNTHETIC_LANDLINE_ONE = "02" + "-123-4567"
SYNTHETIC_LANDLINE_TWO = "02" + "-987-6543"


class _FakeDatabase:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, parameters: dict):
        self.calls.append((name, parameters))
        return self.responses.get(name)


def _candidate(
    business_no: str,
    *,
    mobile: str = "",
    landline: str = "",
    employees: int = 1,
    source_data: dict | None = None,
    company_name: str = "테스트 업체",
) -> dict:
    return {
        "source": "test",
        "source_key": business_no,
        "사업자등록번호": business_no,
        "사업장명": company_name,
        "주소": "서울특별시 중구",
        "휴대전화": mobile,
        "일반전화": landline,
        "가입자수": employees,
        "원본데이터": source_data or {},
    }


class SplitCandidateTests(unittest.TestCase):
    @patch.object(
        prospect,
        "_krx_listed_company_names",
        return_value=frozenset(),
    )
    @patch.object(prospect, "collect_other_companies")
    @patch.object(prospect, "collect_recent_opening_companies")
    @patch.object(prospect, "collect_contactable_growth_companies")
    def test_landline_pool_reserves_companies_that_also_have_mobile(
        self,
        growth,
        recent,
        other,
        _listed_names,
    ):
        growth.return_value = {
            "ok": True,
            "items": [
                _candidate(
                    SYNTHETIC_BUSINESS_ONE,
                    mobile=SYNTHETIC_MOBILE,
                    landline=SYNTHETIC_LANDLINE_ONE,
                ),
                _candidate(
                    SYNTHETIC_BUSINESS_TWO,
                    landline=SYNTHETIC_LANDLINE_TWO,
                ),
            ],
        }
        recent.return_value = {"ok": True, "items": []}
        other.return_value = {"ok": True, "items": []}

        rows, warnings = prospect._collect_allocation_candidates(
            "서울특별시",
            "전체",
            "all",
            "landline",
        )

        self.assertFalse(warnings)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["사업자등록번호"], SYNTHETIC_BUSINESS_TWO)
        self.assertEqual(rows[0]["배정경로"], "landline")

    @patch.object(
        prospect,
        "_krx_listed_company_names",
        return_value=frozenset(),
    )
    @patch.object(prospect, "collect_other_companies")
    @patch.object(prospect, "collect_recent_opening_companies")
    @patch.object(prospect, "collect_contactable_growth_companies")
    def test_duplicate_growth_and_recent_company_gets_combined_label(
        self,
        growth,
        recent,
        other,
        _listed_names,
    ):
        row = _candidate(SYNTHETIC_BUSINESS_ONE, mobile=SYNTHETIC_MOBILE)
        growth.return_value = {"ok": True, "items": [row]}
        recent.return_value = {"ok": True, "items": [dict(row)]}
        other.return_value = {"ok": True, "items": []}

        rows, _warnings = prospect._collect_allocation_candidates(
            "서울특별시",
            "전체",
            "stock",
            "mobile",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["발굴유형"], "growth_recent")
        self.assertEqual(rows[0]["배정경로"], "mobile")

    @patch.object(
        prospect,
        "_krx_listed_company_names",
        return_value=frozenset({"공식상장사"}),
    )
    @patch.object(prospect, "collect_other_companies")
    @patch.object(prospect, "collect_recent_opening_companies")
    @patch.object(prospect, "collect_contactable_growth_companies")
    def test_employee_range_and_listed_markers_are_enforced_after_search(
        self,
        growth,
        recent,
        other,
        _listed_names,
    ):
        growth.return_value = {
            "ok": True,
            "items": [
                _candidate(
                    SYNTHETIC_BUSINESS_ONE,
                    landline=SYNTHETIC_LANDLINE_ONE,
                    employees=12,
                ),
                _candidate(
                    SYNTHETIC_BUSINESS_TWO,
                    landline=SYNTHETIC_LANDLINE_TWO,
                    employees=10,
                    source_data={"stock_code": "123456", "market_type": "KOSDAQ"},
                ),
                _candidate(
                    "222-33-44444",
                    landline="02-111-2222",
                    employees=51,
                ),
                _candidate(
                    "333-44-55555",
                    landline="02-333-4444",
                    employees=20,
                    company_name="(주)공식상장사",
                ),
            ],
        }
        recent.return_value = {"ok": True, "items": []}
        other.return_value = {"ok": True, "items": []}

        rows, warnings = prospect._collect_allocation_candidates(
            "서울특별시",
            "전체",
            "stock",
            "landline",
            minimum_employees=5,
            maximum_employees=50,
        )

        self.assertFalse(warnings)
        self.assertEqual(
            [row["사업자등록번호"] for row in rows],
            [SYNTHETIC_BUSINESS_ONE],
        )
        growth.assert_called_once()
        self.assertEqual(growth.call_args.kwargs["minimum_employees"], 5)
        self.assertEqual(growth.call_args.kwargs["maximum_employees"], 50)

    def test_mobile_assignment_grants_only_row_scoped_visibility(self):
        contact = {
            "prospect_id": "company-1",
            "contact_type": "phone",
            "contact_value": SYNTHETIC_MOBILE,
            "verification_status": "review_required",
            "is_primary": True,
        }
        mobile_row = {
            "id": "company-1",
            "company_name": "모바일 배정 업체",
            "source_data": {
                "allocation_channel": "mobile",
                "discovery_type": "growth",
            },
        }
        legacy_row = {
            "id": "company-1",
            "company_name": "기존 업체",
            "source_data": {"allocation_channel": "legacy"},
        }

        visible = prospect._saved_candidate_frame(
            [mobile_row], [contact], can_view_mobile=False
        )
        hidden = prospect._saved_candidate_frame(
            [legacy_row], [contact], can_view_mobile=False
        )

        self.assertEqual(visible.iloc[0]["휴대전화"], SYNTHETIC_MOBILE)
        self.assertTrue(visible.iloc[0]["_can_view_mobile"])
        self.assertEqual(visible.iloc[0]["발굴유형"], "고용증가기업")
        self.assertEqual(hidden.iloc[0]["휴대전화"], "")
        self.assertFalse(hidden.iloc[0]["_can_view_mobile"])


class MobileRequestRpcTests(unittest.TestCase):
    def test_submit_and_list_use_bounded_service_rpcs(self):
        database = _FakeDatabase(
            {
                assignments.RPC_SUBMIT_MOBILE_DB_REQUEST: [
                    {
                        "success": True,
                        "code": "requested",
                        "request_id": "request-1",
                        "status": "pending",
                        "requested_count": 30,
                        "allocated_count": 0,
                    }
                ],
                assignments.RPC_LIST_USER_MOBILE_DB_REQUESTS: [
                    {
                        "request_id": "request-1",
                        "requested_user_id": "sales-user",
                        "region": "서울특별시",
                        "status": "pending",
                        "private_column": "must-not-leak",
                    }
                ],
            }
        )
        submitted = assignments.submit_mobile_db_request(
            " SALES-USER ",
            "서울특별시",
            "강남구",
            "stock",
            minimum_employees=5,
            maximum_employees=50,
            db=database,
        )
        listed = assignments.list_user_mobile_db_requests(
            "sales-user", db=database
        )

        self.assertTrue(submitted["ok"])
        self.assertEqual(submitted["request"]["request_id"], "request-1")
        self.assertTrue(listed["ok"])
        self.assertNotIn("private_column", listed["requests"][0])
        self.assertEqual(
            database.calls[0][1]["p_current_user_id"], "sales-user"
        )
        self.assertEqual(database.calls[0][1]["p_minimum_employees"], 5)
        self.assertEqual(database.calls[0][1]["p_maximum_employees"], 50)

    def test_submit_rejects_inverted_employee_range_before_rpc(self):
        database = _FakeDatabase({})

        result = assignments.submit_mobile_db_request(
            "sales-user",
            "서울특별시",
            minimum_employees=50,
            maximum_employees=5,
            db=database,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "INVALID_INPUT")
        self.assertFalse(database.calls)


class MobileRequestMigrationTests(unittest.TestCase):
    def test_migration_is_service_only_and_stores_no_contact_values(self):
        root = Path(__file__).resolve().parents[1]
        sql = (
            root
            / "supabase"
            / "migrations"
            / "20260810165733_split_landline_mobile_db_allocation.sql"
        ).read_text(encoding="utf-8").lower()

        self.assertIn("enable row level security", sql)
        self.assertIn("oasis_mobile_db_requests_one_open", sql)
        self.assertIn("revoke execute on function", sql)
        self.assertIn("to service_role", sql)
        table_section = sql.split(
            "create table if not exists public.oasis_mobile_db_requests", 1
        )[1].split(");", 1)[0]
        self.assertNotIn("phone", table_section)
        self.assertNotIn("company_name", table_section)
        self.assertNotIn("business_no", table_section)

    def test_employee_range_migration_updates_requests_and_rpc_contracts(self):
        root = Path(__file__).resolve().parents[1]
        sql = (
            root
            / "supabase"
            / "migrations"
            / "20260811041435_restore_db_request_employee_filter.sql"
        ).read_text(encoding="utf-8").lower()

        self.assertIn("minimum_employees integer not null default 1", sql)
        self.assertIn("maximum_employees integer not null default 300", sql)
        self.assertIn("p_minimum_employees integer", sql)
        self.assertIn("p_maximum_employees integer", sql)
        self.assertIn("to service_role", sql)
        self.assertIn("from public, anon, authenticated", sql)


if __name__ == "__main__":
    unittest.main()
