import tempfile
import unittest
from pathlib import Path

from tools import privacy_guard


class PrivacyGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write(self, relative_path: str, content: str | bytes) -> Path:
        path = self.repo_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def test_existing_legacy_file_does_not_fail_when_not_in_changed_paths(self):
        self._write("고객DB.xlsx", b"legacy-private-workbook")
        self._write("safe.py", "print('safe')\n")

        findings = privacy_guard.scan_paths(self.repo_root, ["safe.py"])

        self.assertEqual(findings, [])

    def test_changed_customer_workbook_is_blocked(self):
        self._write("고객DB.xlsx", b"changed-private-workbook")

        findings = privacy_guard.scan_paths(self.repo_root, ["고객DB.xlsx"])

        self.assertIn("private-binary-artifact", {item.rule for item in findings})

    def test_public_policy_workbook_and_json_are_allowlisted(self):
        self._write("data/기업마당_공고DB.xlsx", b"public-policy-workbook")
        phone = "02" + "-123-" + "4567"
        self._write("data/bizinfo_programs.json", '{"contact": "' + phone + '"}')

        findings = privacy_guard.scan_paths(
            self.repo_root,
            ["data/기업마당_공고DB.xlsx", "data/bizinfo_programs.json"],
        )

        self.assertEqual(findings, [])

    def test_new_private_data_text_is_blocked_without_echoing_value(self):
        phone = "010" + "-1234-" + "5678"
        self._write("exports/new_export.csv", "name,phone\nexample," + phone)

        findings = privacy_guard.scan_paths(
            self.repo_root,
            ["exports/new_export.csv"],
        )

        self.assertIn("phone-number", {item.rule for item in findings})
        self.assertNotIn(phone, repr(findings))

    def test_hardcoded_known_token_is_blocked_but_placeholder_is_allowed(self):
        token = "ghp_" + ("a" * 36)
        self._write("unsafe.py", 'TOKEN = "' + token + '"\n')
        self._write("safe_config.yml", "API_KEY: ${{ secrets.API_KEY }}\n")

        unsafe = privacy_guard.scan_paths(self.repo_root, ["unsafe.py"])
        safe = privacy_guard.scan_paths(self.repo_root, ["safe_config.yml"])

        self.assertIn("known-secret-token", {item.rule for item in unsafe})
        self.assertEqual(safe, [])

    def test_secrets_configuration_file_is_blocked_by_name(self):
        self._write(".streamlit/secrets.toml", 'API_KEY = "placeholder"')

        findings = privacy_guard.scan_paths(
            self.repo_root,
            [".streamlit/secrets.toml"],
        )

        self.assertIn("sensitive-config", {item.rule for item in findings})

    def test_certificate_and_local_database_artifacts_are_blocked(self):
        self._write("credentials/client.pfx", b"certificate")
        self._write("runtime/cache.sqlite", b"sqlite")

        findings = privacy_guard.scan_paths(
            self.repo_root,
            ["credentials/client.pfx", "runtime/cache.sqlite"],
        )

        rules = [item.rule for item in findings]
        self.assertEqual(rules.count("private-binary-artifact"), 2)

    def test_only_exact_workbook_templates_are_allowlisted(self):
        self._write(
            "templates/#Uace0#Uac1dDB_#Uc591#Uc2ddv2.xlsx",
            b"approved-template",
        )
        self._write("templates/customer_upload.xlsx", b"unapproved-template")

        approved = privacy_guard.scan_paths(
            self.repo_root,
            ["templates/#Uace0#Uac1dDB_#Uc591#Uc2ddv2.xlsx"],
        )
        blocked = privacy_guard.scan_paths(
            self.repo_root,
            ["templates/customer_upload.xlsx"],
        )

        self.assertEqual(approved, [])
        self.assertIn("private-binary-artifact", {item.rule for item in blocked})

    def test_pii_is_detected_in_markdown_and_sql(self):
        email = "person" + "@example.kr"
        business_number = "123" + "45" + "67890"
        self._write("notes.md", "contact: " + email)
        self._write("migration.sql", "-- business=" + business_number)

        findings = privacy_guard.scan_paths(
            self.repo_root,
            ["notes.md", "migration.sql"],
        )

        rules = {item.rule for item in findings}
        self.assertIn("email-address", rules)
        self.assertIn("business-number", rules)

    def test_env_example_is_scanned_for_unquoted_real_secret(self):
        secret = "actual" + "-credential-123456789"
        self._write(".env.example", "SERVICE_ROLE_KEY=" + secret + "\n")

        findings = privacy_guard.scan_paths(self.repo_root, [".env.example"])

        self.assertIn("hardcoded-secret", {item.rule for item in findings})
        self.assertNotIn(secret, repr(findings))

    def test_runtime_secret_reference_is_not_treated_as_hardcoded_value(self):
        self._write(
            "client.py",
            'headers = {"apikey": self.config.secret_key}\n',
        )

        findings = privacy_guard.scan_paths(self.repo_root, ["client.py"])

        self.assertEqual(findings, [])

    def test_070_and_0507_numbers_are_detected(self):
        internet_phone = "070" + "-1234-5678"
        representative_phone = "0507" + "-1234-5678"
        self._write(
            "contacts.md",
            internet_phone + "\n" + representative_phone,
        )

        findings = privacy_guard.scan_paths(self.repo_root, ["contacts.md"])

        self.assertGreaterEqual(
            sum(item.rule == "phone-number" for item in findings),
            1,
        )
        self.assertNotIn(internet_phone, repr(findings))
        self.assertNotIn(representative_phone, repr(findings))

    def test_git_text_override_scans_only_newly_added_lines(self):
        legacy_phone = "010" + "-1111-2222"
        added_phone = "070" + "-3333-4444"
        self._write("module.py", "LEGACY = '" + legacy_phone + "'\n")

        safe = privacy_guard.scan_paths(
            self.repo_root,
            ["module.py"],
            text_overrides={"module.py": "print('safe')"},
        )
        blocked = privacy_guard.scan_paths(
            self.repo_root,
            ["module.py"],
            text_overrides={"module.py": "CONTACT = '" + added_phone + "'"},
        )

        self.assertEqual(safe, [])
        self.assertIn("phone-number", {item.rule for item in blocked})

    def test_git_text_override_does_not_bypass_binary_path_rules(self):
        self._write("new/client.p12", b"certificate")

        findings = privacy_guard.scan_paths(
            self.repo_root,
            ["new/client.p12"],
            text_overrides={"new/client.p12": ""},
        )

        self.assertIn("private-binary-artifact", {item.rule for item in findings})

    def test_diff_parser_returns_only_added_content(self):
        diff_text = (
            "diff --git a/example.py b/example.py\n"
            "--- a/example.py\n"
            "+++ b/example.py\n"
            "@@ -1 +1,2 @@\n"
            " legacy\n"
            "+added = 'safe'\n"
            "+print(added)\n"
        )

        self.assertEqual(
            privacy_guard._extract_added_text(diff_text),
            "added = 'safe'\nprint(added)",
        )


if __name__ == "__main__":
    unittest.main()
