from __future__ import annotations

import unittest

from claim_correction_center import (
    RemoteInviteUIError,
    _create_remote_claim_invite,
    _format_remote_invite_expiry,
    _remote_invite_runtime_readiness,
    _validate_remote_invite_input,
)


class _StorageReadinessError(RuntimeError):
    error_code = "REMOTE_STORAGE_NOT_CONFIGURED"


class ClaimRemoteInviteUITests(unittest.TestCase):
    def test_input_validation_normalizes_korean_name_and_phone(self) -> None:
        name, phone, errors = _validate_remote_invite_input(
            "  홍   길동  ",
            "010-1234-5678",
        )

        self.assertEqual(name, "홍 길동")
        self.assertEqual(phone, "01012345678")
        self.assertEqual(errors, [])

    def test_input_validation_rejects_non_korean_name_and_invalid_phone(
        self,
    ) -> None:
        _name, _phone, errors = _validate_remote_invite_input(
            "Hong",
            "02-123-4567",
        )

        self.assertEqual(len(errors), 2)
        self.assertIn("한글", errors[0])
        self.assertIn("010", errors[1])

    def test_create_invite_calls_durable_service_surface(self) -> None:
        captured: dict[str, str] = {}

        def creator(**kwargs):
            captured.update(kwargs)
            return {
                "invite_id": "invite-id",
                "status": "created",
                "expires_at": "2026-08-01T00:00:00+00:00",
                "message_queued": True,
            }

        result = _create_remote_claim_invite(
            owner_user_id=" Manager@Example.com ",
            requested_by="OASIS 관리자",
            customer_name="김 오아",
            customer_phone="010-9876-5432",
            invite_creator=creator,
        )

        self.assertEqual(captured["owner_user_id"], "manager@example.com")
        self.assertEqual(captured["requested_by"], "OASIS 관리자")
        self.assertEqual(captured["customer_name"], "김 오아")
        self.assertEqual(captured["customer_phone"], "01098765432")
        self.assertEqual(result["invite_id"], "invite-id")

    def test_service_error_is_redacted_for_operator(self) -> None:
        def creator(**_kwargs):
            raise _StorageReadinessError("secret=must-not-leak")

        with self.assertRaises(RemoteInviteUIError) as raised:
            _create_remote_claim_invite(
                owner_user_id="manager@example.com",
                requested_by="manager@example.com",
                customer_name="홍길동",
                customer_phone="01012345678",
                invite_creator=creator,
            )

        message = str(raised.exception)
        self.assertIn("저장소 연결", message)
        self.assertNotIn("must-not-leak", message)

    def test_expiry_is_rendered_in_korea_time(self) -> None:
        self.assertEqual(
            _format_remote_invite_expiry("2026-08-01T00:00:00+00:00"),
            "2026년 08월 01일 09:00",
        )

    def test_runtime_readiness_fails_closed_with_safe_message(self) -> None:
        ready, message = _remote_invite_runtime_readiness(
            lambda: {
                "ready": False,
                "missing_env_names": [
                    "SOLAPI_API_SECRET=must-not-leak",
                ],
            }
        )

        self.assertFalse(ready)
        self.assertIn("설정이 완료되지 않았습니다", message)
        self.assertNotIn("must-not-leak", message)


if __name__ == "__main__":
    unittest.main()
