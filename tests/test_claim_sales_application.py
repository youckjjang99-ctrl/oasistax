from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from claim_sales_application import (
    ClaimSalesApplicationCipher,
    ClaimSalesApplicationError,
    ClaimSalesApplicationRepository,
    render_claim_sales_application,
    validate_application,
)


VALID = {
    "name": "홍길동",
    "birth_date": "900101-1234567",
    "contact": "010-1234-5678",
    "email": "Sales@Example.com",
    "bank_name": "국민은행",
    "account_number": "123-456-789012",
    "desired_title": "팀장",
    "desired_admin_id": "sales.hong",
    "english_name": "Gildong Hong",
}


class FakeDatabase:
    def __init__(self) -> None:
        self.table = ""
        self.rows: list[dict] = []
        self.selected: list[dict] = []
        self.select_arguments: dict = {}
        self.update_arguments: dict = {}

    def insert(self, table: str, rows: list[dict]) -> list[dict]:
        self.table = table
        self.rows = rows
        return [{"id": "application-1", **rows[0]}]

    def select(self, table: str, **kwargs) -> list[dict]:
        self.table = table
        self.select_arguments = kwargs
        return self.selected

    def update(
        self,
        table: str,
        filters: dict,
        values: dict,
    ) -> list[dict]:
        self.table = table
        self.update_arguments = {"filters": filters, "values": values}
        return [{"id": filters["id"], **values}]


class ClaimSalesApplicationTests(unittest.TestCase):
    def test_app_uses_sales_application_instead_of_collection_center(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("SHOW_CLAIM_SALES_APPLICATION_MENU = False", source)
        self.assertIn("if SHOW_CLAIM_SALES_APPLICATION_MENU:", source)
        self.assertIn('elif active_tab == "경정청구 영업신청":', source)
        self.assertIn("render_claim_sales_application(", source)
        self.assertNotIn("render_claim_correction_center(", source)

    def test_retired_public_gateway_has_no_collection_routes(self):
        source = (
            Path(__file__).resolve().parents[1] / "claim_public_gateway.py"
        ).read_text(encoding="utf-8")
        self.assertIn('feature": "retired"', source)
        self.assertIn("status_code=410", source)
        self.assertNotIn("claim_remote_service", source)
        self.assertNotIn("resident", source.lower())

    def test_validation_normalizes_without_plaintext_expansion(self):
        clean = validate_application(VALID)
        self.assertEqual(clean["birth_date"], "9001011234567")
        self.assertEqual(clean["contact"], "01012345678")
        self.assertEqual(clean["email"], "sales@example.com")
        self.assertEqual(clean["bank_name"], "국민은행")
        self.assertEqual(clean["account_number"], "123456789012")

    def test_structurally_valid_number_is_not_rejected_by_legacy_checksum(self):
        values = {**VALID, "birth_date": "900101-1234568"}
        clean = validate_application(values)
        self.assertEqual(clean["birth_date"], "9001011234568")

    def test_invalid_resident_birth_date_is_rejected(self):
        values = {**VALID, "birth_date": "901332-1234567"}
        with self.assertRaises(ClaimSalesApplicationError):
            validate_application(values)

    def test_repository_stores_only_ciphertext(self):
        db = FakeDatabase()
        cipher = ClaimSalesApplicationCipher.from_secret("x" * 32)
        repository = ClaimSalesApplicationRepository(db=db, cipher=cipher)
        result = repository.submit(
            owner_user_id="member@example.com",
            values=VALID,
            consented=True,
        )
        self.assertEqual(result["status"], "submitted")
        self.assertEqual(db.table, "oasis_claim_sales_applications")
        stored = db.rows[0]
        self.assertNotIn("name", stored)
        self.assertNotIn("contact", stored)
        self.assertNotIn("bank_name", stored)
        self.assertNotIn("account_number", stored)
        ciphertext = stored["secure_payload_ciphertext"]
        self.assertNotIn("홍길동", ciphertext)
        self.assertNotIn("01012345678", ciphertext)
        self.assertNotIn("국민은행", ciphertext)
        self.assertEqual(cipher.decrypt(ciphertext)["name"], "홍길동")
        self.assertEqual(cipher.decrypt(ciphertext)["bank_name"], "국민은행")

    def test_consent_is_required(self):
        repository = ClaimSalesApplicationRepository(
            db=FakeDatabase(),
            cipher=ClaimSalesApplicationCipher.from_secret("x" * 32),
        )
        with self.assertRaises(ClaimSalesApplicationError):
            repository.submit(
                owner_user_id="member@example.com",
                values=VALID,
                consented=False,
            )

    def test_ui_contains_requested_application_fields(self):
        source = inspect.getsource(render_claim_sales_application)
        for label in (
            "이름",
            "주민등록번호",
            "연락처",
            "이메일",
            "은행명",
            "계좌번호",
            "희망 직함",
            "관리자 페이지 희망 ID",
            "영문이름",
            "영업신청 제출",
        ):
            self.assertIn(label, source)
        self.assertNotIn("직원등록이 확정되었으며", source)

    def test_user_result_query_is_owner_scoped_and_excludes_ciphertext(self):
        db = FakeDatabase()
        repository = ClaimSalesApplicationRepository(db=db)
        repository.list_for_user(" Member@Example.com ")

        self.assertEqual(
            db.select_arguments["filters"],
            {"owner_user_id": "member@example.com"},
        )
        columns = db.select_arguments["columns"]
        self.assertIn("management_homepage_url", columns)
        self.assertIn("sales_code", columns)
        self.assertIn("sales_homepage_url", columns)
        self.assertNotIn("secure_payload_ciphertext", columns)
        self.assertNotIn("owner_user_id", columns)

    def test_non_admin_cannot_list_or_save_results(self):
        repository = ClaimSalesApplicationRepository(db=FakeDatabase())
        with self.assertRaises(ClaimSalesApplicationError):
            repository.list_for_admin(
                current_user_id="member@example.com",
                is_admin_user=False,
            )
        with self.assertRaises(ClaimSalesApplicationError):
            repository.save_result(
                application_id="68c9e4bd-41e3-4c3d-a3ba-2396b886a3a2",
                current_user_id="member@example.com",
                is_admin_user=False,
                management_homepage_url="manage.example.com",
                sales_code="SALES_01",
                sales_homepage_url="sales.example.com",
            )

    def test_admin_can_save_result_with_normalized_urls(self):
        db = FakeDatabase()
        repository = ClaimSalesApplicationRepository(db=db)
        application_id = "68c9e4bd-41e3-4c3d-a3ba-2396b886a3a2"
        result = repository.save_result(
            application_id=application_id,
            current_user_id=" Admin@Example.com ",
            is_admin_user=True,
            management_homepage_url="manage.example.com/path",
            sales_code="SALES_01",
            sales_homepage_url="https://sales.example.com",
        )

        self.assertEqual(db.update_arguments["filters"], {"id": application_id})
        values = db.update_arguments["values"]
        self.assertEqual(
            values["management_homepage_url"],
            "https://manage.example.com/path",
        )
        self.assertEqual(values["sales_homepage_url"], "https://sales.example.com")
        self.assertEqual(values["sales_code"], "SALES_01")
        self.assertEqual(values["status"], "approved")
        self.assertEqual(values["reviewed_by_user_id"], "admin@example.com")
        self.assertTrue(values["reviewed_at"])
        self.assertEqual(result["id"], application_id)

    def test_result_validation_rejects_invalid_url_and_code(self):
        repository = ClaimSalesApplicationRepository(db=FakeDatabase())
        base = {
            "application_id": "68c9e4bd-41e3-4c3d-a3ba-2396b886a3a2",
            "current_user_id": "admin@example.com",
            "is_admin_user": True,
            "management_homepage_url": "manage.example.com",
            "sales_code": "SALES_01",
            "sales_homepage_url": "sales.example.com",
        }
        with self.assertRaises(ClaimSalesApplicationError):
            repository.save_result(**{**base, "sales_code": "공백 코드"})
        with self.assertRaises(ClaimSalesApplicationError):
            repository.save_result(
                **{**base, "management_homepage_url": "https:///missing-host"}
            )

    def test_ui_has_application_and_result_tabs(self):
        module_source = (
            Path(__file__).resolve().parents[1] / "claim_sales_application.py"
        ).read_text(encoding="utf-8")
        self.assertIn('st.tabs(["영업신청", "신청결과"])', module_source)
        self.assertIn('from auth import is_admin', module_source)
        for label in (
            "관리 홈페이지 주소",
            "영업코드",
            "영업용 홈페이지 주소",
            "신청결과 저장",
        ):
            self.assertIn(label, module_source)

    def test_result_migration_adds_only_non_sensitive_result_columns(self):
        migration = (
            Path(__file__).resolve().parents[1]
            / "supabase"
            / "migrations"
            / "20260811093421_claim_sales_application_results.sql"
        ).read_text(encoding="utf-8")
        for column in (
            "management_homepage_url",
            "sales_code",
            "sales_homepage_url",
        ):
            self.assertIn(column, migration)
        self.assertNotIn("secure_payload_ciphertext", migration)
        self.assertNotIn("result_updated_by_user_id", migration)


if __name__ == "__main__":
    unittest.main()
