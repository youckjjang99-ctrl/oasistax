from __future__ import annotations

import base64
import re
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from claim_remote_crypto import ClaimRemoteCrypto, ClaimRemoteCryptoError
from claim_remote_repository import (
    ClaimRemoteRepository,
    ClaimRemoteRepositoryError,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase_v1026_claim_remote_invites.sql"
RESERVATION_MIGRATION = (
    ROOT / "supabase_v1028_claim_remote_submission_reservation.sql"
)


class _FakeDatabase:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.outbox_by_key: dict[tuple[str, str], dict] = {}
        self.failures: dict[str, Exception] = {}
        self.global_invite_status = "sent"
        self.global_invite_id = str(uuid.uuid4())
        self.job_active_result: dict | None = None

    def rpc(self, name: str, parameters: dict):
        self.calls.append((name, parameters))
        if name in self.failures:
            raise self.failures[name]

        if name == "oasis_claim_remote_create_invite":
            row = dict(parameters["p_invite"])
            row.update(
                {
                    "status": "created",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            return [row]
        if name == "oasis_claim_remote_get_invite":
            return [
                {
                    "id": str(uuid.uuid4()),
                    "owner_user_id": parameters["p_owner_user_id"],
                    "token_hash": parameters["p_token_hash"],
                    "status": "sent",
                    "secure_payload_ciphertext": "encrypted",
                }
            ]
        if name == "oasis_claim_remote_resolve_invite":
            return [
                {
                    "id": self.global_invite_id,
                    "owner_user_id": "@".join(("owner", "example.com")),
                    "status": self.global_invite_status,
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(),
                }
            ]
        if name == "oasis_claim_remote_mark_invite_opened_global":
            status = (
                "expired"
                if self.global_invite_status == "expired"
                else "opened"
            )
            return [
                {
                    "id": self.global_invite_id,
                    "owner_user_id": "@".join(("owner", "example.com")),
                    "status": status,
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(),
                }
            ]
        if name == "oasis_claim_remote_mark_invite_opened":
            return [
                {
                    "id": str(uuid.uuid4()),
                    "status": "opened",
                    "token_hash": parameters["p_token_hash"],
                }
            ]
        if name == "oasis_claim_remote_cancel_invite":
            return [
                {
                    "id": self.global_invite_id,
                    "status": "cancelled",
                    "reason": parameters["p_reason"],
                }
            ]
        if name == "oasis_claim_remote_get_session_status":
            return [
                {
                    "invite_id": parameters["p_invite_id"],
                    "invite_status": "submitted",
                    "invite_expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(),
                    "case_id": str(uuid.uuid4()),
                    "job_stage": "hometax_check",
                    "job_status": "running",
                    "progress": 45,
                    "safe_message": "자료 확인 중",
                    "safe_error_code": "",
                    "job_updated_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }
            ]
        if name == "oasis_claim_remote_consume_invite":
            row = dict(parameters["p_job"])
            row.update(
                {
                    "owner_user_id": parameters["p_owner_user_id"],
                    "status": row.get("initial_status", "queued"),
                }
            )
            return [row]
        if name == "oasis_claim_remote_activate_reserved_job":
            return [
                {
                    "id": parameters["p_job_id"],
                    "case_id": parameters["p_case_id"],
                    "status": "queued",
                    "stage": parameters["p_stage"],
                    "secure_payload_ciphertext": parameters[
                        "p_secure_payload_ciphertext"
                    ],
                }
            ]
        if name == "oasis_claim_remote_fail_reserved_job":
            return [
                {
                    "id": parameters["p_job_id"],
                    "case_id": parameters["p_case_id"],
                    "status": "failed",
                    "stage": "submission_failed",
                    "secure_payload_ciphertext": "",
                }
            ]
        if name == "oasis_claim_remote_lease_jobs":
            return [
                {
                    "id": str(uuid.uuid4()),
                    "status": "running",
                    "lease_owner": parameters["p_worker_id"],
                }
            ]
        if name == "oasis_claim_remote_heartbeat_job":
            return [
                {
                    "id": parameters["p_job_id"],
                    "status": "running",
                    "lease_owner": parameters["p_worker_id"],
                }
            ]
        if name == "oasis_claim_remote_check_job_active":
            return [
                dict(self.job_active_result)
                if self.job_active_result is not None
                else {
                    "allowed": True,
                    "code": "ACTIVE",
                    "job_id": parameters["p_job_id"],
                }
            ]
        if name == "oasis_claim_remote_release_job":
            return [
                {
                    "id": parameters["p_job_id"],
                    "status": parameters["p_next_status"],
                    "secure_payload_ciphertext": parameters[
                        "p_secure_payload_ciphertext"
                    ],
                }
            ]
        if name == "oasis_claim_remote_enqueue_outbox":
            candidate = dict(parameters["p_message"])
            key = (
                candidate["owner_user_id"],
                candidate["idempotency_key"],
            )
            existing = self.outbox_by_key.setdefault(
                key,
                {
                    **candidate,
                    "status": "pending",
                },
            )
            return [dict(existing)]
        if name == "oasis_claim_remote_lease_outbox":
            return [
                {
                    "id": str(uuid.uuid4()),
                    "status": "running",
                    "lease_owner": parameters["p_worker_id"],
                }
            ]
        if name == "oasis_claim_remote_begin_guidance_dispatch":
            queued = next(
                (
                    row
                    for row in self.outbox_by_key.values()
                    if row.get("id") == parameters["p_message_id"]
                ),
                {},
            )
            return [{
                "success": True,
                "code": "GUIDANCE_DISPATCH_STARTED",
                "message_id": queued.get("guidance_message_id"),
            }]
        if name == "oasis_claim_remote_release_outbox":
            return [
                {
                    "id": parameters["p_message_id"],
                    "status": parameters["p_next_status"],
                    "secure_payload_ciphertext": parameters[
                        "p_secure_payload_ciphertext"
                    ],
                }
            ]
        if name == "oasis_claim_remote_expire_due":
            return {"invites": 1, "jobs": 2, "messages": 3}
        raise AssertionError(f"unexpected RPC: {name}")


class ClaimRemoteRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.database = _FakeDatabase()
        self.crypto = ClaimRemoteCrypto(
            cipher=Fernet(Fernet.generate_key()),
            link_pepper=b"p" * 48,
            session_secret=b"s" * 48,
        )
        self.repository = ClaimRemoteRepository(
            "@".join(("OWNER", "example.com")),
            database=self.database,
            crypto=self.crypto,
            key_version="test-v1",
        )

    def test_invite_persists_only_token_hash_and_encrypted_payload(self):
        private_payload = {
            "representative": "홍길동",
            "cellphone": "".join(("010", "1234", "5678")),
        }
        result = self.repository.create_invite(
            secure_payload=private_payload,
            recipient_name_masked="홍*동",
            recipient_phone_masked="010-****-5678",
        )

        self.assertGreaterEqual(len(result.token), 32)
        token_padding = "=" * ((4 - len(result.token) % 4) % 4)
        self.assertEqual(
            len(
                base64.urlsafe_b64decode(
                    f"{result.token}{token_padding}".encode("ascii")
                )
            ),
            32,
        )
        name, parameters = self.database.calls[-1]
        self.assertEqual(name, "oasis_claim_remote_create_invite")
        stored = parameters["p_invite"]
        self.assertNotIn("token", stored)
        self.assertEqual(len(stored["token_hash"]), 64)
        self.assertNotEqual(stored["token_hash"], result.token)
        serialized_parameters = repr(parameters)
        self.assertNotIn(result.token, serialized_parameters)
        self.assertNotIn("".join(("010", "1234", "5678")), serialized_parameters)
        self.assertNotIn("홍길동", serialized_parameters)
        self.assertEqual(
            self.repository.decrypt_payload(
                stored["secure_payload_ciphertext"]
            ),
            private_payload,
        )

    def test_same_token_hash_is_deterministic_and_peppered(self):
        token = "x" * 40
        first = self.repository.token_hash(token)
        second = self.repository.token_hash(token)
        plain_sha = __import__("hashlib").sha256(token.encode()).hexdigest()
        self.assertEqual(first, second)
        self.assertNotEqual(first, plain_sha)

    def test_ownerless_resolver_sends_only_hmac_hash(self):
        raw_token = "ownerless-token-" + ("x" * 32)
        resolved = ClaimRemoteRepository.resolve_invite(
            raw_token,
            database=self.database,
            crypto=self.crypto,
        )

        name, parameters = self.database.calls[-1]
        self.assertEqual(name, "oasis_claim_remote_resolve_invite")
        self.assertEqual(set(parameters), {"p_token_hash"})
        self.assertRegex(parameters["p_token_hash"], r"^[0-9a-f]{64}$")
        self.assertNotIn(raw_token, repr(parameters))
        self.assertEqual(resolved["id"], self.database.global_invite_id)
        self.assertEqual(resolved["owner_user_id"], "@".join(("owner", "example.com")))
        self.assertNotIn("token_hash", resolved)
        self.assertNotIn("secure_payload_ciphertext", resolved)

    def test_ownerless_open_uses_global_service_rpc(self):
        raw_token = "global-open-token-" + ("x" * 32)
        opened = ClaimRemoteRepository.mark_invite_opened_global(
            raw_token,
            database=self.database,
            crypto=self.crypto,
        )

        name, parameters = self.database.calls[-1]
        self.assertEqual(
            name,
            "oasis_claim_remote_mark_invite_opened_global",
        )
        self.assertEqual(set(parameters), {"p_token_hash"})
        self.assertNotIn(raw_token, repr(parameters))
        self.assertEqual(opened["status"], "opened")

    def test_ownerless_resolver_commits_purge_then_maps_expiry(self):
        self.database.global_invite_status = "expired"
        with self.assertRaises(ClaimRemoteRepositoryError) as raised:
            ClaimRemoteRepository.resolve_invite(
                "expired-global-token-" + ("x" * 32),
                database=self.database,
                crypto=self.crypto,
            )
        self.assertEqual(
            raised.exception.error_code,
            "REMOTE_INVITE_EXPIRED",
        )

    def test_session_status_uses_owner_and_invite_without_secrets(self):
        invite_id = str(uuid.uuid4())
        status = self.repository.get_session_status(invite_id)

        name, parameters = self.database.calls[-1]
        self.assertEqual(name, "oasis_claim_remote_get_session_status")
        self.assertEqual(
            parameters,
            {
                "p_owner_user_id": "@".join(("owner", "example.com")),
                "p_invite_id": invite_id,
            },
        )
        self.assertEqual(status["job_status"], "running")
        self.assertEqual(status["progress"], 45)
        serialized = repr(status)
        self.assertNotIn("token_hash", serialized)
        self.assertNotIn("ciphertext", serialized)
        self.assertNotIn("lease_", serialized)

    def test_cancel_invite_uses_only_hmac_hash_and_safe_reason(self):
        raw_token = "cancel-token-" + ("x" * 32)

        cancelled = self.repository.cancel_invite(
            raw_token,
            reason="customer_opt_out",
        )

        name, parameters = self.database.calls[-1]
        self.assertEqual(name, "oasis_claim_remote_cancel_invite")
        self.assertEqual(
            set(parameters),
            {"p_owner_user_id", "p_token_hash", "p_reason"},
        )
        self.assertEqual(parameters["p_owner_user_id"], "@".join(("owner", "example.com")))
        self.assertRegex(parameters["p_token_hash"], r"^[0-9a-f]{64}$")
        self.assertNotIn(raw_token, repr(parameters))
        self.assertEqual(parameters["p_reason"], "customer_opt_out")
        self.assertEqual(cancelled["status"], "cancelled")

    def test_consume_invite_sends_hash_and_ciphertext_to_atomic_rpc(self):
        raw_token = "remote-token-" + ("x" * 32)
        case_id = str(uuid.uuid4())
        hard_expires_at = datetime.now(timezone.utc) + timedelta(minutes=45)
        sensitive_expires_at = datetime.now(timezone.utc) + timedelta(
            minutes=10
        )
        result = self.repository.consume_invite(
            raw_token,
            case_id=case_id,
            secure_job_payload={
                "identity_number": "".join(("901019", "1", "234567")),
                "tilko": {"Token": "secret-token"},
            },
            hard_expires_at=hard_expires_at,
            sensitive_expires_at=sensitive_expires_at,
        )

        name, parameters = self.database.calls[-1]
        self.assertEqual(name, "oasis_claim_remote_consume_invite")
        self.assertEqual(parameters["p_owner_user_id"], "@".join(("owner", "example.com")))
        self.assertEqual(len(parameters["p_token_hash"]), 64)
        self.assertNotIn(raw_token, repr(parameters))
        self.assertNotIn("".join(("901019", "1", "234567")), repr(parameters))
        self.assertNotIn("secret-token", repr(parameters))
        self.assertEqual(
            parameters["p_job"]["sensitive_expires_at"],
            sensitive_expires_at.isoformat(),
        )
        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["case_id"], case_id)

    def test_reserved_job_is_waiting_then_activated_with_encrypted_payload(
        self,
    ):
        raw_token = "remote-token-" + ("x" * 32)
        case_id = str(uuid.uuid4())
        reservation = self.repository.consume_invite(
            raw_token,
            case_id=case_id,
            secure_job_payload={"identity_number": "".join(("901019", "1", "234567"))},
            stage="submission_reserved",
            initial_status="waiting",
            next_run_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        self.assertEqual(reservation["status"], "waiting")

        activated = self.repository.activate_reserved_job(
            reservation["id"],
            case_id=case_id,
            secure_payload={
                "identity_number": "".join(("901019", "1", "234567")),
                "hometax": {"Token": "secret-token"},
            },
            stage="hometax_pending",
        )
        name, parameters = self.database.calls[-1]
        self.assertEqual(
            name,
            "oasis_claim_remote_activate_reserved_job",
        )
        self.assertEqual(activated["status"], "queued")
        self.assertNotIn("".join(("901019", "1", "234567")), repr(parameters))
        self.assertNotIn("secret-token", repr(parameters))

    def test_terminal_job_release_clears_ciphertext(self):
        job_id = str(uuid.uuid4())
        released = self.repository.release_job(
            job_id,
            "worker-a",
            next_status="complete",
            stage="complete",
            secure_payload={"identity_number": "".join(("901019", "1", "234567"))},
            progress=100,
        )

        _, parameters = self.database.calls[-1]
        self.assertEqual(parameters["p_secure_payload_ciphertext"], "")
        self.assertEqual(released["secure_payload_ciphertext"], "")

    def test_retry_job_release_keeps_only_encrypted_payload(self):
        payload = {"identity_number": "".join(("901019", "1", "234567"))}
        self.repository.release_job(
            str(uuid.uuid4()),
            "worker-a",
            next_status="retry",
            stage="hometax_check",
            secure_payload=payload,
            next_run_at=datetime.now(timezone.utc) + timedelta(seconds=5),
        )

        _, parameters = self.database.calls[-1]
        ciphertext = parameters["p_secure_payload_ciphertext"]
        self.assertGreater(len(ciphertext), 40)
        self.assertNotIn("".join(("901019", "1", "234567")), repr(parameters))
        self.assertEqual(self.repository.decrypt_payload(ciphertext), payload)

    def test_worker_lease_values_are_bounded(self):
        jobs = self.repository.lease_jobs(
            "worker-a",
            limit=500,
            lease_seconds=2,
        )
        _, parameters = self.database.calls[-1]
        self.assertEqual(parameters["p_limit"], 50)
        self.assertEqual(parameters["p_lease_seconds"], 15)
        self.assertEqual(jobs[0]["lease_owner"], "worker-a")

    def test_heartbeat_and_retry_release_forward_sensitive_deadline(self):
        deadline = datetime.now(timezone.utc) + timedelta(minutes=10)
        job_id = str(uuid.uuid4())
        self.repository.heartbeat_job(
            job_id,
            "worker-a",
            sensitive_expires_at=deadline,
            stage="collecting",
        )
        _, heartbeat_parameters = self.database.calls[-1]
        self.assertEqual(
            heartbeat_parameters["p_sensitive_expires_at"],
            deadline.isoformat(),
        )
        self.assertEqual(
            heartbeat_parameters["p_stage"],
            "collecting",
        )

        self.repository.release_job(
            job_id,
            "worker-a",
            next_status="retry",
            stage="hometax_check",
            secure_payload={"identity_number": "".join(("901019", "1", "234567"))},
            sensitive_expires_at=deadline,
        )
        _, release_parameters = self.database.calls[-1]
        self.assertEqual(
            release_parameters["p_sensitive_expires_at"],
            deadline.isoformat(),
        )

    def test_job_active_check_supports_reservation_and_leased_modes(self):
        job_id = str(uuid.uuid4())

        leased = self.repository.check_job_active(
            job_id,
            mode="leased",
            worker_id="worker-a",
        )
        rpc_name, parameters = self.database.calls[-1]
        self.assertEqual(rpc_name, "oasis_claim_remote_check_job_active")
        self.assertEqual(
            parameters,
            {
                "p_job_id": job_id,
                "p_owner_user_id": "@".join(("owner", "example.com")),
                "p_mode": "leased",
                "p_worker_id": "worker-a",
            },
        )
        self.assertEqual(
            leased,
            {"allowed": True, "code": "ACTIVE", "job_id": job_id},
        )

        self.database.job_active_result = {
            "allowed": False,
            "code": "JOB_NOT_RUNNING",
            "job_id": job_id,
        }
        reserved = self.repository.check_job_active(
            job_id,
            mode="submission_reserved",
        )
        _, parameters = self.database.calls[-1]
        self.assertEqual(parameters["p_mode"], "submission_reserved")
        self.assertIsNone(parameters["p_worker_id"])
        self.assertIs(reserved["allowed"], False)
        self.assertEqual(reserved["code"], "JOB_NOT_RUNNING")

    def test_job_active_check_rejects_malformed_rpc_response(self):
        job_id = str(uuid.uuid4())
        self.database.job_active_result = {
            "allowed": "false",
            "code": "JOB_NOT_RUNNING",
            "job_id": job_id,
        }

        with self.assertRaises(ClaimRemoteRepositoryError) as raised:
            self.repository.check_job_active(
                job_id,
                mode="leased",
                worker_id="worker-a",
            )

        self.assertEqual(
            raised.exception.error_code,
            "REMOTE_JOB_ACTIVE_RESPONSE_INVALID",
        )

    def test_outbox_idempotency_reuses_existing_record(self):
        invite_id = str(uuid.uuid4())
        first = self.repository.enqueue_message(
            idempotency_key="invite:welcome:v1",
            event_type="CLAIM_INVITE",
            template_code="claim_invite_v1",
            secure_payload={"cellphone": "".join(("010", "1234", "5678"))},
            invite_id=invite_id,
        )
        second = self.repository.enqueue_message(
            idempotency_key="invite:welcome:v1",
            event_type="CLAIM_INVITE",
            template_code="claim_invite_v1",
            secure_payload={"cellphone": "".join(("010", "9999", "9999"))},
            invite_id=invite_id,
        )

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(
            len(self.database.outbox_by_key),
            1,
        )
        for _, parameters in self.database.calls[-2:]:
            serialized = repr(parameters)
            self.assertNotIn("".join(("010", "1234", "5678")), serialized)
            self.assertNotIn("".join(("010", "9999", "9999")), serialized)
            self.assertNotIn("cellphone", serialized.lower())

    def test_terminal_outbox_release_clears_ciphertext(self):
        message_id = str(uuid.uuid4())
        self.repository.release_message(
            message_id,
            "message-worker",
            next_status="sent",
            secure_payload={"cellphone": "".join(("010", "1234", "5678"))},
            provider_message_id="provider-1",
        )
        _, parameters = self.database.calls[-1]
        self.assertEqual(parameters["p_secure_payload_ciphertext"], "")

    def test_guidance_outbox_binds_clear_id_and_starts_at_most_once_dispatch(self):
        invite_id = str(uuid.uuid4())
        guidance_id = str(uuid.uuid4())
        queued = self.repository.enqueue_message(
            idempotency_key=f"guidance:{guidance_id}",
            event_type="GUIDANCE_POLICY_FUNDING",
            template_code="GUIDANCE_POLICY_FUNDING",
            secure_payload={"to": "".join(("010", "1234", "5678"))},
            invite_id=invite_id,
            guidance_message_id=guidance_id,
        )
        enqueue_name, enqueue_parameters = self.database.calls[-1]
        self.assertEqual(enqueue_name, "oasis_claim_remote_enqueue_outbox")
        self.assertEqual(
            enqueue_parameters["p_message"]["guidance_message_id"],
            guidance_id,
        )

        started = self.repository.begin_guidance_dispatch(
            queued["id"],
            "message-worker",
            canonical_contact_id=str(uuid.uuid4()),
            recipient_phone_hash="a" * 64,
        )
        self.assertIs(started["success"], True)
        self.assertEqual(started["message_id"], guidance_id)
        rpc_name, rpc_parameters = self.database.calls[-1]
        self.assertEqual(rpc_name, "oasis_claim_remote_begin_guidance_dispatch")
        self.assertEqual(rpc_parameters["p_message_id"], queued["id"])
        self.assertEqual(rpc_parameters["p_worker_id"], "message-worker")
        self.assertRegex(rpc_parameters["p_contact_id"], r"^[0-9a-f-]{36}$")
        self.assertEqual(rpc_parameters["p_recipient_phone_hash"], "a" * 64)

    def test_expire_due_normalizes_counts(self):
        self.assertEqual(
            self.repository.expire_due(),
            {"invites": 1, "jobs": 2, "messages": 3},
        )

    def test_backend_error_is_mapped_without_leaking_details(self):
        self.database.failures[
            "oasis_claim_remote_mark_invite_opened"
        ] = RuntimeError(
            "HTTP 400 secret backend detail REMOTE_INVITE_EXPIRED"
        )
        with self.assertRaises(ClaimRemoteRepositoryError) as raised:
            self.repository.mark_invite_opened(
                "expired-token-" + ("x" * 32)
            )
        self.assertEqual(
            raised.exception.error_code,
            "REMOTE_INVITE_EXPIRED",
        )
        self.assertNotIn("backend", str(raised.exception))

    def test_environment_crypto_configuration_error_is_safe(self):
        with patch(
            "claim_remote_repository.ClaimRemoteCrypto.from_environment",
            side_effect=ClaimRemoteCryptoError("secret details"),
        ), self.assertRaises(ClaimRemoteRepositoryError) as raised:
            ClaimRemoteRepository("owner", database=self.database)
        self.assertEqual(
            raised.exception.error_code,
            "REMOTE_CRYPTO_NOT_CONFIGURED",
        )
        self.assertNotIn("secret details", str(raised.exception))


class ClaimRemoteMigrationStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRATION.read_text(encoding="utf-8")
        cls.lower_sql = cls.sql.lower()

    def test_all_remote_tables_enable_rls_and_revoke_direct_access(self):
        for table in (
            "oasis_claim_remote_invites",
            "oasis_claim_remote_jobs",
            "oasis_claim_remote_outbox",
        ):
            self.assertIn(
                f"alter table public.{table} enable row level security;",
                self.lower_sql,
            )
            self.assertRegex(
                self.lower_sql,
                rf"revoke all on table public\.{table}\s+"
                r"from public, anon, authenticated, service_role;",
            )
            self.assertNotRegex(
                self.lower_sql,
                rf"grant\s+.*\s+on table public\.{table}",
            )

    def test_security_definer_functions_have_empty_search_path(self):
        function_blocks = re.findall(
            r"create or replace function public\."
            r"oasis_claim_remote_[\s\S]+?(?=\n\ncreate or replace function|\n\nalter table)",
            self.lower_sql,
        )
        self.assertGreaterEqual(len(function_blocks), 10)
        for block in function_blocks:
            self.assertIn("set search_path = ''", block)
            if "touch_updated_at" not in block:
                self.assertIn("security definer", block)

    def test_every_public_rpc_is_service_role_only(self):
        rpc_names = set(
            re.findall(
                r"create or replace function public\."
                r"(oasis_claim_remote_(?!touch_updated_at)[a-z0-9_]+)",
                self.lower_sql,
            )
        )
        self.assertGreaterEqual(len(rpc_names), 10)
        for name in rpc_names:
            self.assertRegex(
                self.lower_sql,
                rf"revoke execute\s+on function public\.{name}\("
                r"[\s\S]*?\)\s+from public, anon, authenticated, service_role;",
            )
            self.assertRegex(
                self.lower_sql,
                rf"grant execute\s+on function public\.{name}\("
                r"[\s\S]*?\)\s+to service_role;",
            )

    def test_global_invite_rpcs_resolve_hash_without_secret_output(self):
        resolve_start = self.lower_sql.index(
            "create or replace function public."
            "oasis_claim_remote_resolve_invite"
        )
        open_start = self.lower_sql.index(
            "create or replace function public."
            "oasis_claim_remote_mark_invite_opened_global"
        )
        owner_open_start = self.lower_sql.index(
            "create or replace function public."
            "oasis_claim_remote_mark_invite_opened("
        )
        blocks = (
            self.lower_sql[resolve_start:open_start],
            self.lower_sql[open_start:owner_open_start],
        )
        for block in blocks:
            self.assertIn("where i.token_hash =", block)
            self.assertIn("for update;", block)
            self.assertIn("remote_invite_already_consumed", block)
            self.assertIn("remote_invite_not_active", block)
            self.assertIn("status = 'expired'", block)
            self.assertIn("secure_payload_ciphertext = ''", block)
            output = re.search(
                r"returns table \(([\s\S]+?)\)\s*language plpgsql",
                block,
            )
            self.assertIsNotNone(output)
            output_fields = output.group(1)
            self.assertNotIn("token_hash", output_fields)
            self.assertNotIn("ciphertext", output_fields)
            self.assertNotIn("recipient_", output_fields)

    def test_session_status_rpc_returns_only_safe_status_fields(self):
        start = self.lower_sql.index(
            "create or replace function public."
            "oasis_claim_remote_get_session_status"
        )
        end = self.lower_sql.index(
            "create or replace function public."
            "oasis_claim_remote_consume_invite"
        )
        block = self.lower_sql[start:end]
        output = re.search(
            r"returns table \(([\s\S]+?)\)\s*language plpgsql",
            block,
        )
        self.assertIsNotNone(output)
        output_fields = output.group(1)
        for forbidden in (
            "token_hash",
            "ciphertext",
            "payload_key_version",
            "lease_owner",
            "lease_until",
            "attempt_count",
        ):
            self.assertNotIn(forbidden, output_fields)
        for required in (
            "invite_status",
            "case_id",
            "job_stage",
            "job_status",
            "progress",
            "safe_message",
            "safe_error_code",
            "job_updated_at",
        ):
            self.assertIn(required, output_fields)

    def test_token_column_is_hash_only(self):
        invite_table = self.lower_sql[
            self.lower_sql.index(
                "create table if not exists public.oasis_claim_remote_invites"
            ):
            self.lower_sql.index(
                "create table if not exists public.oasis_claim_remote_jobs"
            )
        ]
        self.assertIn("token_hash text not null unique", invite_table)
        self.assertNotRegex(invite_table, r"\btoken\s+text\b")
        self.assertIn("^[0-9a-f]{64}$", invite_table)

    def test_invite_consumption_is_locked_and_atomic_with_job_insert(self):
        consume = self.lower_sql[
            self.lower_sql.index(
                "create or replace function public."
                "oasis_claim_remote_consume_invite"
            ):
            self.lower_sql.index(
                "create or replace function public."
                "oasis_claim_remote_lease_jobs"
            )
        ]
        self.assertIn("for update;", consume)
        self.assertIn("remote_invite_already_consumed", consume)
        self.assertIn("remote_invite_expired", consume)
        self.assertIn(
            "insert into public.oasis_claim_remote_jobs",
            consume,
        )
        self.assertIn("status = 'submitted'", consume)
        self.assertIn("secure_payload_ciphertext = ''", consume)

    def test_job_and_outbox_leases_use_skip_locked(self):
        self.assertGreaterEqual(
            self.lower_sql.count("for update skip locked"),
            2,
        )
        self.assertIn("lease_until", self.lower_sql)
        self.assertIn("attempt_count", self.lower_sql)
        self.assertIn("hard_expires_at", self.lower_sql)

    def test_outbox_has_database_enforced_idempotency(self):
        self.assertIn(
            "unique (owner_user_id, idempotency_key)",
            self.lower_sql,
        )
        self.assertIn(
            "on conflict (owner_user_id, idempotency_key) do nothing",
            self.lower_sql,
        )
        self.assertIn(
            "remote_outbox_idempotency_conflict",
            self.lower_sql,
        )

    def test_terminal_states_clear_encrypted_payloads(self):
        self.assertIn(
            "oasis_claim_remote_jobs_ciphertext_lifecycle",
            self.lower_sql,
        )
        self.assertIn(
            "oasis_claim_remote_outbox_ciphertext_lifecycle",
            self.lower_sql,
        )
        self.assertIn(
            "oasis_claim_remote_invites_ciphertext_lifecycle",
            self.lower_sql,
        )
        self.assertIn(
            "create or replace function public."
            "oasis_claim_remote_expire_due",
            self.lower_sql,
        )


class ClaimRemoteReservationMigrationStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = RESERVATION_MIGRATION.read_text(encoding="utf-8")
        cls.lower_sql = cls.sql.lower()

    def test_reservation_is_atomic_and_not_immediately_leaseable(self):
        consume_start = self.lower_sql.index(
            "create or replace function public."
            "oasis_claim_remote_consume_invite"
        )
        activate_start = self.lower_sql.index(
            "create or replace function public."
            "oasis_claim_remote_activate_reserved_job"
        )
        consume = self.lower_sql[consume_start:activate_start]
        self.assertIn("for update;", consume)
        self.assertIn("remote_invite_already_consumed", consume)
        self.assertIn("insert into public.oasis_claim_remote_jobs", consume)
        self.assertIn("v_initial_status", consume)
        self.assertIn("'submission_reserved'", consume)
        self.assertIn("v_next_run_at", consume)
        self.assertIn("status = 'submitted'", consume)

    def test_reserved_job_activation_and_failure_are_service_role_only(self):
        for name in (
            "oasis_claim_remote_activate_reserved_job",
            "oasis_claim_remote_fail_reserved_job",
        ):
            self.assertIn(
                f"create or replace function public.{name}",
                self.lower_sql,
            )
            self.assertRegex(
                self.lower_sql,
                rf"revoke execute\s+on function public\.{name}\("
                r"[\s\S]*?\)\s+from public, anon, authenticated, service_role;",
            )
            self.assertRegex(
                self.lower_sql,
                rf"grant execute\s+on function public\.{name}\("
                r"[\s\S]*?\)\s+to service_role;",
            )

if __name__ == "__main__":
    unittest.main()
