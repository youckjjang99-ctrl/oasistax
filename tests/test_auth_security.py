import unittest
from unittest.mock import patch

import auth


class _SessionState(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class AuthSecurityTests(unittest.TestCase):
    def test_password_policy_requires_length_letters_and_numbers(self):
        self.assertTrue(auth._password_policy_error("short1"))
        self.assertTrue(auth._password_policy_error("Abcdefg1"))
        self.assertTrue(auth._password_policy_error("a" * 9))
        self.assertTrue(auth._password_policy_error("1" * 9))
        self.assertEqual(auth._password_policy_error("Abcdefgh1"), "")
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
        self.assertEqual(
            auth._session_validation_status("owner", "token"),
            auth.SESSION_UNAVAILABLE,
        )

    def test_recently_verified_session_gets_short_database_outage_grace(self):
        now = 1000.0

        self.assertTrue(auth._auth_outage_grace_active(now - 60, now))
        self.assertFalse(
            auth._auth_outage_grace_active(
                now - auth.AUTH_OUTAGE_GRACE_SECONDS - 1,
                now,
            )
        )
        self.assertFalse(auth._auth_outage_grace_active(0, now))

    def test_active_session_is_not_logged_out_by_transient_session_check_error(self):
        state = _SessionState(
            {
                "logged_in": True,
                "current_user_id": "owner",
                "current_user_name": "관리자",
                "current_user_role": "admin",
                "login_session_token": "known-token",
                "_auth_last_verified_key": "force-recheck",
                "_auth_last_verified_at": 900.0,
            }
        )

        with (
            patch.object(auth.st, "session_state", state),
            patch.object(auth.time, "monotonic", return_value=1000.0),
            patch("auth.ensure_default_admin"),
            patch("auth._signed_out_query_guard_active", return_value=False),
            patch(
                "auth._session_validation_status",
                return_value=auth.SESSION_UNAVAILABLE,
            ),
            patch("auth._clear_local_login_state") as clear_state,
        ):
            self.assertTrue(auth.check_login())

        clear_state.assert_not_called()
        self.assertTrue(state["logged_in"])
        self.assertEqual(state["current_user_id"], "owner")

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

    @patch("auth.get_secret")
    def test_signup_cannot_reuse_configured_admin_id(self, get_secret):
        get_secret.side_effect = lambda key, default="": {
            "APP_LOGIN_ID": "oasis-admin",
        }.get(key, default)

        ok, message = auth.create_user(
            "OASIS-ADMIN",
            "SecurePass123",
            "신규 사용자",
        )

        self.assertFalse(ok)
        self.assertIn("사용할 수 없는 아이디", message)

    def test_approval_preserves_password_and_writes_only_target_account(self):
        target = {
            "user_id": "new-member",
            "name": "신규 사용자",
            "salt": "s" * 32,
            "password_hash": "a" * 64,
            "role": "member",
            "status": "pending",
        }
        other = {
            "user_id": "other-member",
            "name": "기존 사용자",
            "salt": "t" * 32,
            "password_hash": "b" * 64,
            "role": "member",
            "status": "approved",
        }

        with (
            patch("auth.is_admin", return_value=True),
            patch("auth.ensure_default_admin"),
            patch(
                "auth.load_users",
                return_value={"new-member": target, "other-member": other},
            ),
            patch("auth._save_single_user") as save_one,
        ):
            ok, _message = auth.approve_user("new-member", "admin")

        self.assertTrue(ok)
        save_one.assert_called_once()
        saved_id, saved_user = save_one.call_args.args
        self.assertEqual(saved_id, "new-member")
        self.assertEqual(saved_user["password_hash"], "a" * 64)
        self.assertEqual(saved_user["salt"], "s" * 32)
        self.assertEqual(saved_user["status"], "approved")

    @patch("auth._restore_persistent_login")
    @patch("auth._clear_persistent_login_cookie")
    @patch("auth._signed_out_query_guard_active", return_value=True)
    def test_signed_out_guard_blocks_stale_admin_cookie_restore(
        self,
        _query_guard,
        clear_cookie,
        restore_login,
    ):
        state = _SessionState(
            {
                "logged_in": True,
                "current_user_id": "old-admin",
                "current_user_name": "관리자",
                "current_user_role": "admin",
                "login_session_token": "old-token",
            }
        )

        with patch.object(auth.st, "session_state", state):
            self.assertFalse(auth.check_login())

        restore_login.assert_not_called()
        clear_cookie.assert_called_once()
        self.assertFalse(state["logged_in"])
        self.assertEqual(state["current_user_id"], "")
        self.assertEqual(state["current_user_role"], "")

    def test_signup_success_activates_signed_out_guard(self):
        source = (auth.ROOT_DIR / "auth.py").read_text(encoding="utf-8")

        self.assertIn(
            "_activate_signed_out_guard(clear_all_state=False)",
            source,
        )
        self.assertIn(
            "_deactivate_signed_out_guard()",
            source,
        )


if __name__ == "__main__":
    unittest.main()
