import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

import auth


SESSION_SECRET = "synthetic-dedicated-session-key-for-tests-only"


class PersistentSessionKeyIsolationTests(unittest.TestCase):
    def _secret_reader(self, values):
        return lambda name, default="": values.get(name, default)

    def test_no_database_or_admin_password_fallback(self):
        values = {
            "SUPABASE_SECRET_KEY": "synthetic-database-key-" * 3,
            "SUPABASE_SERVICE_ROLE_KEY": "synthetic-legacy-database-key-" * 3,
            "APP_LOGIN_PW": "synthetic-admin-password-" * 3,
        }
        with (
            patch.dict(auth.os.environ, {}, clear=True),
            patch("auth.get_secret", side_effect=self._secret_reader(values)) as read,
        ):
            self.assertIsNone(auth._persistent_login_cipher())
            self.assertEqual(auth._encode_persistent_login("owner", "token"), "")
            self.assertIsNone(auth._decode_persistent_login("untrusted-cookie"))
        self.assertTrue(read.call_args_list)
        self.assertTrue(
            all(call.args[0] == "APP_SESSION_SECRET" for call in read.call_args_list)
        )

    def test_short_dedicated_key_fails_closed_without_fallback(self):
        for secret in ("", "short", "x" * 31, " " * 40):
            with (
                self.subTest(length=len(secret)),
                patch.dict(auth.os.environ, {"APP_SESSION_SECRET": secret}, clear=True),
                patch("auth.get_secret", return_value=SESSION_SECRET) as read,
            ):
                self.assertIsNone(auth._persistent_login_cipher())
                read.assert_not_called()

    def test_environment_key_takes_precedence_over_streamlit_file(self):
        with (
            patch.dict(
                auth.os.environ, {"APP_SESSION_SECRET": SESSION_SECRET}, clear=True
            ),
            patch("auth.get_secret", return_value="different-file-key" * 3) as read,
        ):
            cookie = auth._encode_persistent_login("owner", "session-token")
            self.assertEqual(
                auth._decode_persistent_login(cookie), ("owner", "session-token")
            )
            read.assert_not_called()

    def test_streamlit_secret_remains_supported_when_environment_key_absent(self):
        with (
            patch.dict(auth.os.environ, {}, clear=True),
            patch("auth.get_secret", return_value=SESSION_SECRET),
        ):
            cookie = auth._encode_persistent_login("owner", "session-token")
            self.assertEqual(
                auth._decode_persistent_login(cookie), ("owner", "session-token")
            )

    def test_database_key_rotation_does_not_invalidate_dedicated_cookie(self):
        values = {
            "APP_SESSION_SECRET": SESSION_SECRET,
            "SUPABASE_SECRET_KEY": "old-synthetic-database-key" * 2,
        }
        with (
            patch.dict(auth.os.environ, {}, clear=True),
            patch("auth.get_secret", side_effect=self._secret_reader(values)),
        ):
            cookie = auth._encode_persistent_login("owner", "session-token")
            values["SUPABASE_SECRET_KEY"] = "new-synthetic-database-key" * 2
            values["SUPABASE_SERVICE_ROLE_KEY"] = "other-synthetic-database-key" * 2
            self.assertEqual(
                auth._decode_persistent_login(cookie), ("owner", "session-token")
            )

    def test_dedicated_key_rotation_invalidates_old_cookie(self):
        with patch.dict(
            auth.os.environ, {"APP_SESSION_SECRET": SESSION_SECRET}, clear=True
        ):
            cookie = auth._encode_persistent_login("owner", "session-token")
            auth.os.environ["APP_SESSION_SECRET"] = "new-synthetic-session-key" * 3
            self.assertIsNone(auth._decode_persistent_login(cookie))

    def test_legacy_database_derived_cookie_is_never_accepted(self):
        legacy_secret = "synthetic-old-database-key" * 3
        digest = hashlib.sha256(
            f"oasis-persistent-login-v1\0{legacy_secret}".encode("utf-8")
        ).digest()
        legacy_cipher = Fernet(base64.urlsafe_b64encode(digest))
        cookie = legacy_cipher.encrypt(
            json.dumps(
                {"version": 1, "user_id": "owner", "session_token": "session-token"}
            ).encode("utf-8")
        ).decode("ascii")
        with patch.dict(
            auth.os.environ,
            {
                "APP_SESSION_SECRET": SESSION_SECRET,
                "SUPABASE_SECRET_KEY": legacy_secret,
                "SUPABASE_SERVICE_ROLE_KEY": legacy_secret,
                "APP_LOGIN_PW": legacy_secret,
            },
            clear=True,
        ):
            self.assertIsNone(auth._decode_persistent_login(cookie))


if __name__ == "__main__":
    unittest.main()
