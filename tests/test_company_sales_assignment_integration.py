from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

import prospect_db_repository


class _FakeDatabase:
    def __init__(self, *, fail_atomic_rpc: bool = False):
        self.fail_atomic_rpc = fail_atomic_rpc
        self.rpc_calls: list[tuple[str, dict]] = []
        self.upsert_calls: list[tuple[str, list[dict], str]] = []

    def rpc(self, name: str, parameters: dict):
        self.rpc_calls.append((name, parameters))
        if name == "oasis_claim_and_save_company_sales_assignment":
            if self.fail_atomic_rpc:
                raise RuntimeError("atomic transaction failed")
            uid = parameters["p_company_uid"]
            if uid == "business:2222222222":
                return [
                    {
                        "success": False,
                        "code": "ASSIGNMENT_CONFLICT",
                        "company_uid": uid,
                    }
                ]
            return [
                {
                    "success": True,
                    "code": "ASSIGNED",
                    "company_uid": uid,
                    "status": "assigned",
                }
            ]
        raise AssertionError(f"unexpected RPC: {name}")

    def upsert(self, table: str, rows: list[dict], on_conflict: str):
        self.upsert_calls.append((table, rows, on_conflict))
        if self.fail_upsert:
            raise RuntimeError("legacy mirror failed")
        return [{"id": "saved-1", **row} for row in rows]


def _candidate(number: str, source_key: str) -> dict:
    return {
        "사업장명": f"테스트 업체 {source_key}",
        "사업자등록번호": number,
        "주소": "서울특별시 강남구 테헤란로 1",
        "대표전화": "02-1234-5678",
        "source": "nps_monthly",
        "source_key": source_key,
    }


class AssignmentRepositoryIntegrationTests(unittest.TestCase):
    def test_atomic_payload_never_forwards_client_supplied_owner(self):
        database = _FakeDatabase()
        candidate = _candidate("111-11-11111", "owner-injection")
        candidate["owner_user_id"] = "attacker@example.com"
        candidate["assigned_user_id"] = "attacker@example.com"

        with patch.object(
            prospect_db_repository,
            "CloudDatabase",
            return_value=database,
        ):
            result = prospect_db_repository.save_assigned_prospects(
                [candidate],
                "  SALES-A@EXAMPLE.COM ",
                session_id="session-1",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(database.rpc_calls), 1)
        rpc_name, parameters = database.rpc_calls[0]
        self.assertEqual(
            rpc_name,
            "oasis_claim_and_save_company_sales_assignment",
        )
        self.assertEqual(
            parameters["p_current_user_id"],
            "sales-a@example.com",
        )
        self.assertNotIn("owner_user_id", parameters["p_company_payload"])
        self.assertNotIn("assigned_user_id", parameters["p_company_payload"])
        self.assertNotIn("attacker@example.com", repr(parameters))

        sql = (
            Path(__file__).resolve().parents[1]
            / "supabase_v1032_company_sales_assignments.sql"
        ).read_text(encoding="utf-8")
        function_start = sql.index(
            "create or replace function "
            "public.oasis_claim_and_save_company_sales_assignment"
        )
        function_end = sql.index("\n$$;", function_start)
        function_sql = sql[function_start:function_end]
        self.assertIn("owner_user_id = p_current_user_id", function_sql)
        self.assertRegex(
            function_sql,
            r"(?s)owner_user_id,\s+source_data,.*?"
            r"p_current_user_id,\s+v_source_data",
        )
        self.assertNotIn("v_payload ->> 'owner_user_id'", function_sql)

    def test_save_assigned_prospects_uses_only_atomic_rpc_not_direct_upsert(self):
        source = inspect.getsource(
            prospect_db_repository.save_assigned_prospects
        )
        self.assertIn("claim_and_save_companies(", source)
        self.assertNotIn(".upsert(", source)
        self.assertNotIn("save_prospects(", source)

        database = _FakeDatabase()
        with patch.object(
            prospect_db_repository,
            "CloudDatabase",
            return_value=database,
        ):
            result = prospect_db_repository.save_assigned_prospects(
                [_candidate("111-11-11111", "atomic-only")],
                "sales-a",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(database.upsert_calls, [])
        self.assertEqual(
            [name for name, _parameters in database.rpc_calls],
            ["oasis_claim_and_save_company_sales_assignment"],
        )

    def test_only_atomic_claim_and_save_successes_are_reported(self):
        database = _FakeDatabase()
        with patch.object(
            prospect_db_repository,
            "CloudDatabase",
            return_value=database,
        ):
            result = prospect_db_repository.save_assigned_prospects(
                [
                    _candidate("111-11-11111", "a"),
                    _candidate("222-22-22222", "b"),
                ],
                "SALES-A",
                session_id="session-1",
            )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["success_count"], 1)
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(database.upsert_calls, [])
        first_payload = database.rpc_calls[0][1]["p_company_payload"]
        self.assertEqual(first_payload["company_uid"], "business:1111111111")
        self.assertNotIn("owner_user_id", first_payload)

    def test_duplicate_source_rows_make_one_claim_and_one_legacy_row(self):
        database = _FakeDatabase()
        duplicate = _candidate("111-11-11111", "same")
        with patch.object(
            prospect_db_repository,
            "CloudDatabase",
            return_value=database,
        ):
            result = prospect_db_repository.save_assigned_prospects(
                [duplicate, dict(duplicate)],
                "sales-a",
            )

        claim_calls = [
            call
            for call in database.rpc_calls
            if call[0] == "oasis_claim_and_save_company_sales_assignment"
        ]
        self.assertEqual(len(claim_calls), 1)
        self.assertEqual(result["saved_count"], 1)

    def test_already_owned_company_is_repaired_idempotently_by_atomic_rpc(self):
        database = _FakeDatabase()

        def already_owned(name: str, parameters: dict):
            database.rpc_calls.append((name, parameters))
            return [
                {
                    "success": True,
                    "code": "ALREADY_OWNED",
                    "company_uid": parameters["p_company_uid"],
                    "status": "assigned",
                }
            ]

        database.rpc = already_owned
        with patch.object(
            prospect_db_repository,
            "CloudDatabase",
            return_value=database,
        ):
            result = prospect_db_repository.save_assigned_prospects(
                [_candidate("111-11-11111", "new-source-row")],
                "sales-a",
            )

        self.assertEqual(result["saved_count"], 1)
        self.assertEqual(result["already_owned_count"], 1)
        self.assertEqual(database.upsert_calls, [])

    def test_atomic_rpc_failure_needs_no_client_compensating_release(self):
        database = _FakeDatabase(fail_atomic_rpc=True)
        with patch.object(
            prospect_db_repository,
            "CloudDatabase",
            return_value=database,
        ):
            result = prospect_db_repository.save_assigned_prospects(
                [_candidate("111-11-11111", "a")],
                "sales-a",
                session_id="session-1",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["saved_count"], 0)
        self.assertEqual(
            [name for name, _params in database.rpc_calls],
            ["oasis_claim_and_save_company_sales_assignment"],
        )


if __name__ == "__main__":
    unittest.main()
