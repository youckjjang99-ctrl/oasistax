import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime_error_log


class RuntimeErrorLogTests(unittest.TestCase):
    def test_error_log_redacts_private_values_from_all_sections(self):
        phone = "010" + "-1234-" + "5678"
        resident_number = "900101" + "-1234567"
        business_number = "123" + "-45-" + "67890"
        unformatted_business_number = "12345" + "67890"
        api_key = "sk-" + ("a" * 32)
        jwt = "eyJ" + ("a" * 20) + "." + ("b" * 20) + "." + ("c" * 16)

        with tempfile.TemporaryDirectory() as temp_dir:
            fake_module_path = Path(temp_dir) / "runtime_error_log.py"
            with patch.object(runtime_error_log, "__file__", str(fake_module_path)):
                try:
                    raise RuntimeError(
                        f"phone={phone} resident_number={resident_number} "
                        f"business_number={business_number} token={jwt} "
                        f"identifier {unformatted_business_number}"
                    )
                except RuntimeError as exc:
                    log_path = runtime_error_log.write_runtime_error(
                        f"request phone={phone}",
                        exc,
                        details={
                            "api_key": api_key,
                            "credential": {"token": jwt},
                            "customer": {
                                "phone": phone,
                                "resident_number": resident_number,
                                "business_number": business_number,
                            },
                            "safe_status": "retrying",
                        },
                    )

            content = Path(log_path).read_text(encoding="utf-8")

        for private_value in (
            phone,
            resident_number,
            business_number,
            unformatted_business_number,
            api_key,
            jwt,
        ):
            self.assertNotIn(private_value, content)
        self.assertIn("[REDACTED]", content)
        self.assertIn("safe_status", content)
        self.assertIn("retrying", content)
        self.assertEqual(
            runtime_error_log._redact_text("caller " + phone),
            "caller [PHONE_REDACTED]",
        )

    def test_binary_details_are_described_without_dumping_contents(self):
        secret_bytes = b"private-binary-value"
        sanitized = runtime_error_log._sanitize_details(
            {"attachment": secret_bytes}
        )

        self.assertEqual(sanitized["attachment"], "[BINARY 20 bytes]")
        self.assertNotIn("private-binary-value", repr(sanitized))

    def test_customer_identity_fields_and_additional_phone_prefixes_are_redacted(self):
        internet_phone = "070" + "-1234-5678"
        representative_phone = "0507" + "-1234-5678"
        email = "customer" + "@example.kr"
        details = {
            "customer_name": "홍길동",
            "address": "서울특별시 테스트로 1",
            "email": email,
            "memo": "상담 원문",
            "public": internet_phone + " / " + representative_phone,
        }

        sanitized = runtime_error_log._sanitize_details(details)
        rendered = repr(sanitized)

        for private_value in (
            "홍길동",
            "서울특별시 테스트로 1",
            email,
            "상담 원문",
            internet_phone,
            representative_phone,
        ):
            self.assertNotIn(private_value, rendered)

    def test_inline_multiword_identity_and_bearer_values_are_fully_redacted(self):
        private_name = "Synthetic First Last"
        private_address = "Synthetic District Building 101"
        bearer_token = "syntheticBearerTokenAlphaBetaGamma"
        value = (
            f'customer_name="{private_name}", '
            f"address={private_address}; "
            f"Authorization: Bearer {bearer_token}"
        )

        sanitized = runtime_error_log._redact_text(value)

        self.assertNotIn(private_name, sanitized)
        self.assertNotIn("First Last", sanitized)
        self.assertNotIn(private_address, sanitized)
        self.assertNotIn("District Building 101", sanitized)
        self.assertNotIn(bearer_token, sanitized)
        self.assertGreaterEqual(sanitized.count("[REDACTED]"), 3)

    def test_public_error_never_uses_exception_message_or_path(self):
        private_path = "C:" + "\\private\\customer.json"
        email = "customer" + "@example.kr"
        private_message = "failed at " + private_path + " for " + email
        exc = RuntimeError(private_message)

        public_error = runtime_error_log.safe_public_error(
            exc,
            "자료 저장에 실패했습니다.",
        )
        sanitized = runtime_error_log.sanitize_public_text(private_message)

        self.assertEqual(
            public_error,
            "자료 저장에 실패했습니다. (오류 유형: RuntimeError)",
        )
        self.assertNotIn(private_message, public_error)
        self.assertNotIn(private_path, sanitized)
        self.assertNotIn(email, sanitized)

    def test_runtime_log_omits_raw_exception_message_and_full_path(self):
        private_path = "C:" + "\\private\\customer.json"
        raw_message = "cannot read " + private_path

        with tempfile.TemporaryDirectory() as temp_dir:
            fake_module_path = Path(temp_dir) / "runtime_error_log.py"
            with patch.object(runtime_error_log, "__file__", str(fake_module_path)):
                try:
                    raise RuntimeError(raw_message)
                except RuntimeError as exc:
                    log_path = runtime_error_log.write_runtime_error("load", exc)

            content = Path(log_path).read_text(encoding="utf-8")

        self.assertNotIn(raw_message, content)
        self.assertNotIn(private_path, content)
        self.assertIn("exception_message=[REDACTED]", content)
        self.assertIn("exception_type=RuntimeError", content)


if __name__ == "__main__":
    unittest.main()
