import unittest
from unittest.mock import patch

import auth


class AuthSecurityTests(unittest.TestCase):
    def test_password_policy_requires_length_letters_and_numbers(self):
        self.assertTrue(auth._password_policy_error("short1"))
        self.assertTrue(auth._password_policy_error("a" * 12))
        self.assertTrue(auth._password_policy_error("1" * 12))
        self.assertEqual(
            auth._password_policy_error("SecurePass123"),
            "",
        )

    @patch("auth.is_admin", return_value=True)
    @patch("auth.get_secret")
    def test_mobile_numbers_are_limited_to_configured_owner(
        self,
        get_secret,
        _is_admin,
    ):
        get_secret.side_effect = lambda key, default="": {
            "MOBILE_PHONE_OWNER_ID": "owner",
            "APP_LOGIN_ID": "fallback",
        }.get(key, default)

        self.assertTrue(auth.can_view_mobile_numbers("owner"))
        self.assertFalse(auth.can_view_mobile_numbers("other-admin"))

    @patch("auth.CloudDatabase")
    @patch("auth.cloud_is_configured", return_value=True)
    def test_session_validation_fails_closed_when_database_errors(
        self,
        _configured,
        database,
    ):
        database.return_value.select.side_effect = RuntimeError("offline")

        self.assertFalse(auth._session_is_current("owner", "token"))

    @patch("auth.get_secret")
    def test_persistent_login_cookie_is_encrypted_and_round_trips(
        self,
        get_secret,
    ):
        get_secret.side_effect = lambda key, default="": {
            "APP_SESSION_SECRET": "test-session-secret-value-1234567890",
        }.get(key, default)

        encoded = auth._encode_persistent_login("owner", "session-token")

        self.assertTrue(encoded)
        self.assertNotIn("owner", encoded)
        self.assertNotIn("session-token", encoded)
        self.assertEqual(
            auth._decode_persistent_login(encoded),
            ("owner", "session-token"),
        )

    @patch("auth.get_secret")
    def test_persistent_login_cookie_rejects_tampering(self, get_secret):
        get_secret.side_effect = lambda key, default="": {
            "APP_SESSION_SECRET": "test-session-secret-value-1234567890",
        }.get(key, default)
        encoded = auth._encode_persistent_login("owner", "session-token")
        replacement = "A" if encoded[-1] != "A" else "B"

        self.assertIsNone(
            auth._decode_persistent_login(encoded[:-1] + replacement)
        )

    def test_logout_queues_cookie_cleanup_before_clearing_session(self):
        source = (auth.ROOT_DIR / "auth.py").read_text(encoding="utf-8")

        self.assertIn('st.session_state["_logout_requested_v1"] = True', source)
        self.assertIn("_clear_persistent_login_cookie()", source)
        self.assertIn("_restore_persistent_login()", source)


if __name__ == "__main__":
    unittest.main()
