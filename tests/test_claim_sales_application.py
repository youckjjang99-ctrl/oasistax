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
    "account_number": "123-456-789012",
    "desired_title": "팀장",
    "desired_admin_id": "sales.hong",
    "english_name": "Gildong Hong",
}


class FakeDatabase:
    def __init__(self) -> None:
        self.table = ""
        self.rows: list[dict] = []

    def insert(self, table: str, rows: list[dict]) -> list[dict]:
        self.table = table
        self.rows = rows
        return [{"id": "application-1", **rows[0]}]


class ClaimSalesApplicationTests(unittest.TestCase):
    def test_app_uses_sales_application_instead_of_collection_center(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"경정청구 영업신청": "경정청구 영업신청"', source)
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
        self.assertEqual(clean["account_number"], "123456789012")

    def test_invalid_resident_number_is_rejected(self):
        values = {**VALID, "birth_date": "900101-1234568"}
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
        self.assertNotIn("account_number", stored)
        ciphertext = stored["secure_payload_ciphertext"]
        self.assertNotIn("홍길동", ciphertext)
        self.assertNotIn("01012345678", ciphertext)
        self.assertEqual(cipher.decrypt(ciphertext)["name"], "홍길동")

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
            "계좌번호",
            "희망 직함",
            "관리자 페이지 희망 ID",
            "영문이름",
            "영업신청 제출",
        ):
            self.assertIn(label, source)


if __name__ == "__main__":
    unittest.main()
