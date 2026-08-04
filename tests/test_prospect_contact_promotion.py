from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest.mock import patch

import prospect_db_repository


ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "supabase_v1032_company_sales_assignments.sql").read_text(
    encoding="utf-8"
)
RLS = (
    ROOT / "supabase_v1032_company_sales_assignments_rls.sql"
).read_text(encoding="utf-8")
CENTER = (ROOT / "prospect_db_center.py").read_text(encoding="utf-8")
BACKUP_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804113337_assignment_release_backup_store.sql"
).read_text(encoding="utf-8")
CONFLICT_FIX_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804132543_fix_prospect_contact_conflict_ambiguity.sql"
).read_text(encoding="utf-8")
V911_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804020000_v911_customer_information_integration.sql"
).read_text(encoding="utf-8")
V911_SERVICE_GRANT_MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260804133529_v911_normalizer_acl.sql"
).read_text(encoding="utf-8")

SYNTHETIC_MOBILE = "".join(("010", "1234", "5678"))
SYNTHETIC_LANDLINE = "".join(("02", "1234", "5678"))
SYNTHETIC_BUSINESS_NO = "-".join(("111", "11", "11111"))
SYNTHETIC_EMAIL = "@".join(("SALES", "EXAMPLE.COM"))
SYNTHETIC_OWNER_ID = "@".join(("sales-a", "example.invalid"))


class _PromotionDatabase:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, parameters: dict):
        self.calls.append((name, parameters))
        if name != "oasis_claim_save_and_promote_prospect_contacts":
            raise AssertionError(f"unexpected RPC: {name}")
        return [
            {
                "success": True,
                "code": "ASSIGNED",
                "company_uid": parameters["p_company_uid"],
                "prospect_id": "00000000-0000-0000-0000-000000000001",
                "promoted_contact_count": len(
                    parameters["p_contact_candidates"]
                ),
                "status": "assigned",
            }
        ]


def _candidate() -> dict:
    return {
        "사업장명": "테스트 업체",
        "사업자등록번호": SYNTHETIC_BUSINESS_NO,
        "주소": "서울특별시 강남구 테헤란로 1",
        "대표전화": "+82 " + SYNTHETIC_MOBILE[1:],
        "휴대전화": SYNTHETIC_MOBILE,
        "일반전화": SYNTHETIC_LANDLINE,
        "이메일": SYNTHETIC_EMAIL,
        "인스타그램": "test_company",
        "인스타그램URL": "https://www.instagram.com/test_company/",
        "source": "nps_monthly",
        "source_key": "promotion-1",
    }


class ProspectContactPromotionTests(unittest.TestCase):
    def test_candidates_are_normalized_deduplicated_and_bounded(self):
        candidates = prospect_db_repository.build_review_contact_candidates(
            _candidate()
        )
        values = {
            (row["contact_type"], row["contact_value"])
            for row in candidates
        }
        self.assertEqual(len(candidates), 4)
        self.assertIn(("phone", SYNTHETIC_MOBILE), values)
        self.assertIn(("phone", SYNTHETIC_LANDLINE), values)
        self.assertIn(("email", SYNTHETIC_EMAIL.casefold()), values)
        self.assertIn(("instagram", "test_company"), values)
        self.assertLessEqual(len(candidates), 8)
        self.assertNotIn("consent", repr(candidates).lower())

    def test_invalid_contact_candidates_are_not_promoted(self):
        candidate = _candidate()
        candidate.update(
            {
                "대표전화": "123",
                "휴대전화": "123",
                "일반전화": "",
                "이메일": "not-an-email",
                "인스타그램": "",
                "인스타그램URL": "",
            }
        )
        self.assertEqual(
            prospect_db_repository.build_review_contact_candidates(candidate),
            [],
        )

    def test_operator_approval_uses_atomic_promotion_rpc(self):
        database = _PromotionDatabase()
        with patch.object(
            prospect_db_repository,
            "CloudDatabase",
            return_value=database,
        ):
            result = prospect_db_repository.save_assigned_prospects(
                [_candidate()],
                SYNTHETIC_OWNER_ID,
                session_id="session-1",
                promote_review_contacts=True,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["promoted_contact_count"], 4)
        self.assertEqual(len(database.calls), 1)
        name, parameters = database.calls[0]
        self.assertEqual(
            name,
            "oasis_claim_save_and_promote_prospect_contacts",
        )
        self.assertEqual(
            parameters["p_current_user_id"],
            SYNTHETIC_OWNER_ID,
        )
        self.assertEqual(len(parameters["p_contact_candidates"]), 4)
        self.assertNotIn("contact_candidates", parameters["p_company_payload"])
        self.assertNotIn("owner_user_id", parameters["p_company_payload"])

    def test_sql_promotes_only_review_required_without_recipient_consent(self):
        start = SQL.index(
            "create or replace function "
            "public.oasis_claim_save_and_promote_prospect_contacts"
        )
        end = SQL.index("\n$$;", start)
        function_sql = SQL[start:end].lower()
        self.assertLess(
            function_sql.index(
                "oasis_claim_and_save_company_sales_assignment"
            ),
            function_sql.index("insert into public.oasis_prospect_contacts"),
        )
        self.assertIn("not between 1 and 8", function_sql)
        self.assertIn("'review_required'", function_sql)
        self.assertIn("'recipient_consent_recorded', false", function_sql)
        self.assertIn("'manual_verified'", function_sql)
        self.assertIn("'rejected'", function_sql)
        self.assertIn("do_not_contact", function_sql)
        self.assertNotIn("security definer", function_sql)
        self.assertIn(
            "oasis_claim_save_and_promote_prospect_contacts",
            RLS.lower(),
        )

    def test_contact_upsert_uses_named_constraint_without_output_ambiguity(self):
        expected = (
            "on conflict on constraint oasis_prospect_contacts_unique "
            "do update"
        )
        ambiguous = (
            "on conflict (prospect_id, contact_type, contact_value) "
            "do update"
        )
        for sql in (SQL, CONFLICT_FIX_MIGRATION):
            normalized = " ".join(sql.lower().split())
            self.assertIn(expected, normalized)
            self.assertNotIn(ambiguous, normalized)

        fix_sql = CONFLICT_FIX_MIGRATION.lower()
        self.assertIn(
            "create or replace function "
            "public.oasis_claim_save_and_promote_prospect_contacts",
            fix_sql,
        )
        self.assertIn("from public, anon, authenticated", fix_sql)
        self.assertIn("to service_role", fix_sql)

    def test_service_role_can_maintain_normalized_business_number_index(self):
        v911_sql = " ".join(V911_MIGRATION.lower().split())
        grant_sql = " ".join(V911_SERVICE_GRANT_MIGRATION.lower().split())
        helper = "public.oasis_v911_normalize_business_no(text)"

        self.assertIn(
            "create index if not exists "
            "idx_oasis_prospects_owner_normalized_business_no",
            v911_sql,
        )
        self.assertIn(
            "public.oasis_v911_normalize_business_no(business_no)",
            v911_sql,
        )
        self.assertIn(
            f"grant execute on function {helper} to service_role",
            grant_sql,
        )
        self.assertIn(
            f"revoke all on function {helper} "
            "from public, anon, authenticated, service_role",
            grant_sql,
        )
        self.assertNotIn(f"grant execute on function {helper} to anon", grant_sql)
        self.assertNotIn(
            f"grant execute on function {helper} to authenticated",
            grant_sql,
        )

    def test_ui_requires_explicit_approval_and_keeps_send_consent_separate(self):
        self.assertIn('@st.dialog("정규 연락처 승격 승인")', CENTER)
        self.assertIn("휴대전화 후보", CENTER)
        self.assertIn("검토 필요 상태의", CENTER)
        self.assertIn("승인하고 내 영업DB에 담기", CENTER)
        self.assertIn("promote_review_contacts=True", CENTER)
        self.assertIn("수신자의 광고성 정보 수신 동의로 기록되지", CENTER)

    def test_release_backup_store_is_private_and_service_only(self):
        migration = BACKUP_MIGRATION.lower()
        self.assertIn("create schema if not exists oasis_private", migration)
        self.assertIn("enable row level security", migration)
        self.assertIn("force row level security", migration)
        self.assertIn(
            "revoke all on schema oasis_private from public, anon, authenticated",
            migration,
        )
        self.assertIn(
            "from public, anon, authenticated",
            migration,
        )
        self.assertIn("to service_role", migration)

    def test_changed_python_files_parse(self):
        for filename in (
            "prospect_db_repository.py",
            "company_sales_assignment.py",
            "prospect_db_center.py",
        ):
            ast.parse((ROOT / filename).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
