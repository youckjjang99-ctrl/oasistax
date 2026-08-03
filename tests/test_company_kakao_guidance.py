from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from company_kakao_guidance import (
    GUIDANCE_BUTTON_LABELS,
    GUIDANCE_MESSAGE_PREVIEWS,
    CompanyKakaoGuidanceError,
    check_guidance_send_ready,
    evaluate_guidance_eligibility,
    evaluate_send_eligibility,
    guidance_environment_readiness,
    notify_guidance_outbox_status,
    request_guidance_send,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260803090000_v910_kakao_guidance.sql"
)
HASH_ENV = {
    "OASIS_KAKAO_GUIDANCE_PROVIDER_MODE": "mock",
    "OASIS_KAKAO_GUIDANCE_MOCK_MODE": "true",
    "OASIS_KAKAO_GUIDANCE_SEND_ENABLED": "false",
    "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY": "x" * 32,
}
LIVE_ENV = {
    "OASIS_KAKAO_GUIDANCE_PROVIDER_MODE": "live",
    "OASIS_KAKAO_GUIDANCE_SEND_ENABLED": "true",
    "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY": "x" * 32,
    "SOLAPI_API_KEY": "test-api-key",
    "SOLAPI_API_SECRET": "test-api-secret",
    "SOLAPI_KAKAO_CHANNEL_ID": "test-channel-id",
    "SOLAPI_TEMPLATE_GUIDANCE_EMPLOYMENT_SUPPORT_ID": "template-employment",
    "SOLAPI_TEMPLATE_GUIDANCE_POLICY_FUNDING_ID": "template-funding",
    "SOLAPI_TEMPLATE_GUIDANCE_TAX_CREDIT_ID": "template-tax-credit",
}
TEST_BUSINESS_UID = "business:" + "".join(("123", "45", "67890"))

COMPANY = {
    "id": "8d0b0f77-c4f3-4e92-a665-81ace9cb5e0b",
    "company_uid": TEST_BUSINESS_UID,
    "business_type": "개인사업자",
    "mobile_phone": "-".join(("010", "1234", "5678")),
}
ASSIGNMENT = {
    "id": "37fb1710-1497-4ca4-8218-9a81ddc2f45c",
    "company_uid": TEST_BUSINESS_UID,
    "assigned_user_id": "seller",
    "status": "assigned",
}
CANONICAL_COMPANY_ID = "8d0b0f77-c4f3-4e92-a665-81ace9cb5e0b"
CANONICAL_CONTACT_ID = "2d4da9ee-f3a9-48cc-b506-29d8ca88ca18"
CANONICAL_PHONE = "".join(("010", "9876", "5432"))
CANONICAL_CONTACT_UPDATED_AT = "2026-08-03T00:30:00+00:00"


class FakeDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, params: dict):
        self.calls.append((name, dict(params)))
        if name == "oasis_resolve_company_kakao_guidance_mobile":
            return [{
                "success": True,
                "code": "OK",
                "message": "ok",
                "company_id": CANONICAL_COMPANY_ID,
                "assignment_id": ASSIGNMENT["id"],
                "contact_id": CANONICAL_CONTACT_ID,
                "mobile_phone": CANONICAL_PHONE,
                "contact_updated_at": CANONICAL_CONTACT_UPDATED_AT,
            }]
        if name == "oasis_check_company_kakao_guidance_eligibility":
            return [{"eligible": True, "code": "ELIGIBLE", "message": "ok", "assignment_id": ASSIGNMENT["id"]}]
        if name == "oasis_reserve_company_kakao_guidance":
            return [{"success": True, "message_id": "8f2011a9-931e-4518-8ac5-84ba7948276b", "status": "queued"}]
        if name == "oasis_attach_company_kakao_guidance_invite":
            return [{"success": True, "message_id": params["p_message_id"], "invite_id": params["p_invite_id"]}]
        if name == "oasis_finalize_company_kakao_guidance":
            return [{"success": True, "message_id": params["p_message_id"], "status": params["p_status"]}]
        if name == "oasis_cancel_company_kakao_guidance":
            return [{"success": True, "message_id": params["p_message_id"], "status": "cancelled"}]
        if name == "oasis_check_company_kakao_guidance_send_ready":
            return [{"allowed": True, "code": "READY"}]
        raise AssertionError(name)


def test_strict_individual_mobile_and_assignment_rules() -> None:
    assert evaluate_guidance_eligibility(COMPANY, current_user_id="seller", assignment=ASSIGNMENT)["eligible"]

    for business_type in ("개인사업자 후보", "미확인", "법인사업자", ""):
        row = {**COMPANY, "business_type": business_type}
        result = evaluate_guidance_eligibility(row, current_user_id="seller", assignment=ASSIGNMENT)
        assert not result["eligible"]
        assert result["code"] == "INDIVIDUAL_ONLY"

    landline = {**COMPANY, "mobile_phone": "-".join(("02", "123", "4567"))}
    assert evaluate_guidance_eligibility(landline, current_user_id="seller", assignment=ASSIGNMENT)["code"] == "MOBILE_REQUIRED"
    foreign = {**ASSIGNMENT, "assigned_user_id": "other"}
    assert evaluate_guidance_eligibility(COMPANY, current_user_id="seller", assignment=foreign)["code"] == "ASSIGNED_TO_OTHER"
    dnc = {**COMPANY, "do_not_contact": True}
    assert evaluate_guidance_eligibility(dnc, current_user_id="seller", assignment=ASSIGNMENT)["code"] == "DO_NOT_CONTACT"


def test_recent_success_is_blocked_for_seven_days() -> None:
    now = datetime(2026, 8, 3, tzinfo=timezone.utc)
    result = evaluate_guidance_eligibility(
        COMPANY, current_user_id="seller", assignment=ASSIGNMENT,
        recent_success_at=now - timedelta(days=6), now=now,
    )
    assert result["code"] == "DUPLICATE_WITHIN_7_DAYS"


def test_composite_check_hides_phone_and_hash() -> None:
    db = FakeDB()
    result = evaluate_send_eligibility(
        COMPANY, current_user_id="seller", assignment=ASSIGNMENT,
        message_type="employment_support", db=db, environ=HASH_ENV,
    )
    assert result["eligible"]
    assert "phone" not in result and "recipient_phone_hash" not in result
    call = db.calls[0][1]
    assert len(call["p_recipient_phone_hash"]) == 64
    assert "010" not in call["p_recipient_phone_hash"]


def test_mock_mode_never_creates_external_outbox() -> None:
    db = FakeDB()
    outbox_called = False
    invite_arguments: dict = {}

    def invite_factory(**kwargs):
        invite_arguments.update(kwargs)
        return {
            "invite_id": "70a0c885-4c54-445d-a7f2-02c1b130e476",
            "invite_url": "https://example.invalid/review/opaque",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }

    def outbox_factory(_owner):
        nonlocal outbox_called
        outbox_called = True
        raise AssertionError("mock mode must not create a provider outbox")

    result = request_guidance_send(
        current_user_id="seller", requested_by="seller", company=COMPANY,
        assignment=ASSIGNMENT, message_type="employment_support",
        idempotency_key="test-idempotency", environ=HASH_ENV, db=db,
        invite_factory=invite_factory, outbox_repository_factory=outbox_factory,
    )
    assert result["code"] == "SIMULATED"
    assert result["status"] == "simulated"
    assert result["external_send_enabled"] is False
    assert outbox_called is False
    assert "phone" not in invite_arguments
    assert "mobile_phone" not in invite_arguments
    assert "recipient_phone" not in invite_arguments
    finalize_calls = [
        params
        for name, params in db.calls
        if name == "oasis_finalize_company_kakao_guidance"
    ]
    assert finalize_calls[-1]["p_status"] == "simulated"


def test_live_guidance_phone_is_only_used_for_message_delivery() -> None:
    db = FakeDB()
    invite_arguments: dict = {}
    queued: dict = {}

    def invite_factory(**kwargs):
        invite_arguments.update(kwargs)
        return {
            "invite_id": "70a0c885-4c54-445d-a7f2-02c1b130e476",
            "invite_url": "https://example.invalid/review/opaque",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }

    class FakeOutbox:
        def enqueue_message(self, **kwargs):
            queued.update(kwargs)

    result = request_guidance_send(
        current_user_id="seller",
        requested_by="seller",
        company=COMPANY,
        assignment=ASSIGNMENT,
        message_type="employment_support",
        idempotency_key="live-separation-test",
        environ=LIVE_ENV,
        db=db,
        invite_factory=invite_factory,
        outbox_repository_factory=lambda _owner: FakeOutbox(),
    )

    assert result["code"] == "QUEUED"
    assert queued["secure_payload"]["to"] == CANONICAL_PHONE
    assert queued["secure_payload"]["canonical_contact_id"] == CANONICAL_CONTACT_ID
    serialized_invite = repr(invite_arguments)
    assert "".join(("010", "1234", "5678")) not in serialized_invite
    assert "recipient_phone" not in invite_arguments
    assert "mobile_phone" not in invite_arguments
    assert "phone" not in invite_arguments
    assert invite_arguments["guidance_type"] == "employment_support"

    reserve_call = next(
        params
        for name, params in db.calls
        if name == "oasis_reserve_company_kakao_guidance"
    )
    assert reserve_call["p_company_id"] == CANONICAL_COMPANY_ID
    assert reserve_call["p_assignment_id"] == ASSIGNMENT["id"]
    assert reserve_call["p_contact_id"] == CANONICAL_CONTACT_ID
    assert (
        reserve_call["p_recipient_contact_updated_at"]
        == CANONICAL_CONTACT_UPDATED_AT
    )


def test_idempotent_replay_reuses_existing_state_without_new_side_effects() -> None:
    message_id = "8f2011a9-931e-4518-8ac5-84ba7948276b"

    class ReplayDB(FakeDB):
        def rpc(self, name: str, params: dict):
            if name == "oasis_check_company_kakao_guidance_eligibility":
                self.calls.append((name, dict(params)))
                return [{
                    "eligible": False,
                    "code": "DUPLICATE_IN_PROGRESS",
                    "message": "이미 처리 중입니다.",
                    "assignment_id": ASSIGNMENT["id"],
                }]
            if name == "oasis_reserve_company_kakao_guidance":
                self.calls.append((name, dict(params)))
                return [{
                    "success": True,
                    "code": "IDEMPOTENT_REPLAY",
                    "message": "이미 처리된 요청입니다.",
                    "message_id": message_id,
                    "status": "queued",
                }]
            return super().rpc(name, params)

    def forbidden_invite(**_kwargs):
        # A replay regression would enter the catch block and cancel the
        # already queued message after this ordinary factory failure.
        raise RuntimeError("must not create an invite")

    def forbidden_outbox(_owner):
        raise RuntimeError("must not queue")

    db = ReplayDB()
    result = request_guidance_send(
        current_user_id="seller",
        requested_by="seller",
        company=COMPANY,
        assignment=ASSIGNMENT,
        message_type="employment_support",
        idempotency_key="existing-live-request",
        environ=LIVE_ENV,
        db=db,
        invite_factory=forbidden_invite,
        outbox_repository_factory=forbidden_outbox,
    )

    assert result == {
        "ok": True,
        "code": "IDEMPOTENT_REPLAY",
        "message": "이미 처리된 안내 발송 요청입니다.",
        "guidance_message_id": message_id,
        "invite_id": "",
        "status": "queued",
        "external_send_enabled": True,
    }
    assert CANONICAL_PHONE not in repr(result)
    assert [name for name, _params in db.calls] == [
        "oasis_resolve_company_kakao_guidance_mobile",
        "oasis_check_company_kakao_guidance_eligibility",
        "oasis_reserve_company_kakao_guidance",
    ]


def test_idempotent_replay_with_unknown_state_fails_closed_without_cancelling() -> None:
    class MalformedReplayDB(FakeDB):
        def rpc(self, name: str, params: dict):
            if name == "oasis_check_company_kakao_guidance_eligibility":
                self.calls.append((name, dict(params)))
                return [{
                    "eligible": False,
                    "code": "DUPLICATE_WITHIN_7_DAYS",
                    "message": "최근 발송된 요청입니다.",
                    "assignment_id": ASSIGNMENT["id"],
                }]
            if name == "oasis_reserve_company_kakao_guidance":
                self.calls.append((name, dict(params)))
                return [{
                    "success": True,
                    "code": "IDEMPOTENT_REPLAY",
                    "message_id": "8f2011a9-931e-4518-8ac5-84ba7948276b",
                    "status": "unknown",
                }]
            return super().rpc(name, params)

    db = MalformedReplayDB()
    with pytest.raises(CompanyKakaoGuidanceError) as exc_info:
        request_guidance_send(
            current_user_id="seller",
            requested_by="seller",
            company=COMPANY,
            assignment=ASSIGNMENT,
            message_type="employment_support",
            idempotency_key="malformed-existing-request",
            environ=LIVE_ENV,
            db=db,
            invite_factory=lambda **_kwargs: pytest.fail("must not create an invite"),
            outbox_repository_factory=lambda _owner: pytest.fail("must not queue"),
        )

    assert exc_info.value.code == "MALFORMED_RESPONSE"
    assert [name for name, _params in db.calls] == [
        "oasis_resolve_company_kakao_guidance_mobile",
        "oasis_check_company_kakao_guidance_eligibility",
        "oasis_reserve_company_kakao_guidance",
    ]


def test_tampered_foreign_caller_phone_is_never_used_for_send_or_hash() -> None:
    db = FakeDB()
    queued: dict = {}
    caller_phone = "+1 " + "-".join(("212", "555", "0199"))

    def invite_factory(**_kwargs):
        return {
            "invite_id": "70a0c885-4c54-445d-a7f2-02c1b130e476",
            "invite_url": "https://example.invalid/review/opaque",
            "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        }

    class FakeOutbox:
        def enqueue_message(self, **kwargs):
            queued.update(kwargs)

    request_guidance_send(
        current_user_id="seller",
        requested_by="seller",
        company={
            **COMPANY,
            "mobile_phone": caller_phone,
            "phone": caller_phone,
            "대표전화": caller_phone,
        },
        assignment=ASSIGNMENT,
        message_type="policy_funding",
        idempotency_key="foreign-number-tampering",
        environ=LIVE_ENV,
        db=db,
        invite_factory=invite_factory,
        outbox_repository_factory=lambda _owner: FakeOutbox(),
    )

    assert queued["secure_payload"]["to"] == CANONICAL_PHONE
    normalized_caller_phone = "".join(("1", "212", "555", "0199"))
    assert normalized_caller_phone not in repr(db.calls)
    assert normalized_caller_phone not in repr(queued)
    reserve_call = next(
        params
        for name, params in db.calls
        if name == "oasis_reserve_company_kakao_guidance"
    )
    expected_hash = hmac.new(
        LIVE_ENV["OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY"].encode("utf-8"),
        CANONICAL_PHONE.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    tampered_hash = hmac.new(
        LIVE_ENV["OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY"].encode("utf-8"),
        normalized_caller_phone.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    assert reserve_call["p_recipient_phone_hash"] == expected_hash
    assert reserve_call["p_recipient_phone_hash"] != tampered_hash


def test_canonical_resolver_binding_failure_stops_before_queue() -> None:
    class ChangedAssignmentDB(FakeDB):
        def rpc(self, name: str, params: dict):
            if name == "oasis_resolve_company_kakao_guidance_mobile":
                self.calls.append((name, dict(params)))
                return [{
                    "success": True,
                    "company_id": CANONICAL_COMPANY_ID,
                    "assignment_id": "b1d8ec44-a935-471f-806b-282a6a196cf2",
                    "contact_id": CANONICAL_CONTACT_ID,
                    "mobile_phone": CANONICAL_PHONE,
                    "contact_updated_at": CANONICAL_CONTACT_UPDATED_AT,
                }]
            return super().rpc(name, params)

    db = ChangedAssignmentDB()
    with pytest.raises(CompanyKakaoGuidanceError) as exc_info:
        request_guidance_send(
            current_user_id="seller", requested_by="seller", company=COMPANY,
            assignment=ASSIGNMENT, message_type="employment_support",
            environ=LIVE_ENV, db=db,
            invite_factory=lambda **_kwargs: {},
            outbox_repository_factory=lambda _owner: pytest.fail("must not queue"),
        )
    assert exc_info.value.code == "ASSIGNMENT_CHANGED"
    assert [name for name, _params in db.calls] == [
        "oasis_resolve_company_kakao_guidance_mobile"
    ]


def test_send_ready_wrapper_is_strict_and_fail_closed() -> None:
    db = FakeDB()
    message_id = "8f2011a9-931e-4518-8ac5-84ba7948276b"
    phone_hash = "a" * 64
    assert check_guidance_send_ready(
        message_id,
        CANONICAL_CONTACT_ID,
        phone_hash,
        db=db,
    )["allowed"] is True
    assert db.calls[-1] == (
        "oasis_check_company_kakao_guidance_send_ready",
        {
            "p_message_id": message_id,
            "p_contact_id": CANONICAL_CONTACT_ID,
            "p_recipient_phone_hash": phone_hash,
        },
    )

    class MalformedDB:
        def rpc(self, _name: str, _params: dict):
            return [{"allowed": "false", "code": "BLOCKED"}]

    with pytest.raises(CompanyKakaoGuidanceError) as exc_info:
        check_guidance_send_ready(
            message_id,
            CANONICAL_CONTACT_ID,
            phone_hash,
            db=MalformedDB(),
        )
    assert exc_info.value.code == "MALFORMED_RESPONSE"


def test_canonical_resolver_requires_non_pii_contact_version() -> None:
    class MissingVersionDB(FakeDB):
        def rpc(self, name: str, params: dict):
            if name == "oasis_resolve_company_kakao_guidance_mobile":
                row = dict(super().rpc(name, params)[0])
                row.pop("contact_updated_at", None)
                return [row]
            return super().rpc(name, params)

    with pytest.raises(CompanyKakaoGuidanceError) as exc_info:
        request_guidance_send(
            current_user_id="seller",
            requested_by="seller",
            company=COMPANY,
            assignment=ASSIGNMENT,
            message_type="employment_support",
            environ=LIVE_ENV,
            db=MissingVersionDB(),
            invite_factory=lambda **_kwargs: {},
            outbox_repository_factory=lambda _owner: pytest.fail(
                "must not queue"
            ),
        )
    assert exc_info.value.code == "MALFORMED_RESPONSE"


def test_hash_key_is_required_even_in_mock_mode() -> None:
    readiness = guidance_environment_readiness({
        "OASIS_KAKAO_GUIDANCE_PROVIDER_MODE": "mock",
        "OASIS_KAKAO_GUIDANCE_MOCK_MODE": "true",
        "OASIS_KAKAO_GUIDANCE_SEND_ENABLED": "false",
    })
    assert readiness["ready"] is False
    assert "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY" in readiness["missing_env_names"]


def test_mock_mode_is_explicit_and_blocked_in_production() -> None:
    base = {
        "OASIS_KAKAO_GUIDANCE_PROVIDER_MODE": "mock",
        "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY": "x" * 32,
    }
    assert guidance_environment_readiness(base)["ready"] is False

    production = {
        **base,
        "OASIS_KAKAO_GUIDANCE_MOCK_MODE": "true",
        "OASIS_ENVIRONMENT": "production",
    }
    readiness = guidance_environment_readiness(production)
    assert readiness["ready"] is False
    assert readiness["mock_mode_blocked_in_production"] is True


def test_fixed_template_text_and_buttons() -> None:
    assert GUIDANCE_BUTTON_LABELS == {
        "employment_support": "고용지원금 검토 신청",
        "policy_funding": "정책자금 검토 신청",
        "tax_credit": "세액공제 검토 신청",
    }
    for preview in GUIDANCE_MESSAGE_PREVIEWS.values():
        assert "{{secure_review_url}}" in preview
        assert "오아시스 세무회계" in preview
        for prohibited in ("지원금 지급 확정", "환급 확정", "누락 확정", "즉시 수령 가능"):
            assert prohibited not in preview


def test_migration_contracts() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    for table in (
        "oasis_company_kakao_guidance_messages",
        "oasis_company_kakao_contact_controls",
        "oasis_company_kakao_guidance_history",
        "oasis_company_kakao_followup_outbox",
        "oasis_company_kakao_guidance_settings",
    ):
        assert f"create table if not exists public.{table}" in sql
        assert f"alter table public.{table} enable row level security" in sql
    assert "pg_advisory_xact_lock" in sql
    assert "interval '7 days'" in sql
    assert "oasis_record_company_sales_contact" in sql
    assert "oasis_claim_remote_cancel_invite" in sql
    assert "recipient_phone_hash" in sql
    assert "recipient_phone text" not in sql
    assert "source_data ->> 'business_type'" in sql
    assert "individual_only" in sql
    assert "mobile_required" in sql
    assert "from public, anon, authenticated" in sql
    assert "to service_role" in sql
    assert "send_enabled boolean not null default false" in sql
    assert "고객 self-input 인증번호와 비교하거나 연결하지 않는다" in sql
    assert "oasis-claim-auth-payload-expiry-v910" in sql
    assert "select public.oasis_claim_remote_expire_due();" in sql
    assert "create extension if not exists pg_cron" in sql
    assert "select cron.schedule(" in sql
    assert "oasis_claim_remote_retention_health" in sql
    assert "notify pgrst, 'reload schema'" in sql


def test_migration_enforces_sensitive_payload_expiry_with_mandatory_cron() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "add column if not exists sensitive_expires_at timestamptz" in sql
    assert "oasis_claim_remote_jobs_sensitive_expiry_lifecycle" in sql
    assert "oasis_claim_remote_jobs_sensitive_expiry_idx" in sql
    assert "p_sensitive_expires_at timestamptz default null" in sql
    assert "v_now + interval '10 minutes'" in sql

    expire_start = sql.index(
        "create or replace function public.oasis_claim_remote_expire_due"
    )
    expire_end = sql.index(
        "alter table public.oasis_company_kakao_guidance_settings enable row level security",
        expire_start,
    )
    expire_function = sql[expire_start:expire_end]
    assert "j.sensitive_expires_at <= v_now" in expire_function
    assert "secure_payload_ciphertext = ''" in expire_function
    assert "sensitive_expires_at = null" in expire_function

    cron_start = sql.index("do $oasis_guidance_auth_expiry_cron$")
    cron_block = sql[cron_start:]
    assert "select cron.schedule(" in cron_block
    assert "'* * * * *'" in cron_block
    assert "from cron.job c" in cron_block
    assert "c.database" in cron_block
    assert "current_database()" in cron_block
    assert "remote_retention_cron_registration_failed" in cron_block
    assert "when undefined_table" not in cron_block
    assert "insufficient_privilege" not in cron_block


def test_migration_uses_stage_aware_sensitive_ttl_and_rpc_reload() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    heartbeat_start = sql.index(
        "create or replace function public.oasis_claim_remote_heartbeat_job"
    )
    release_start = sql.index(
        "create or replace function public.oasis_claim_remote_release_job",
        heartbeat_start,
    )
    heartbeat = sql[heartbeat_start:release_start]
    release_end = sql.index(
        "create or replace function public.oasis_claim_remote_expire_due",
        release_start,
    )
    release = sql[release_start:release_end]

    assert "p_stage text default null" in heartbeat
    assert "v_stage = 'collecting'" in heartbeat
    assert "left(v_stage, 11) = 'collection_'" in heartbeat
    assert "v_sensitive_expires_at := v_job.hard_expires_at" in heartbeat
    assert "stage = v_stage" in heartbeat
    assert "left(v_stage, 11) = 'collection_'" in release
    assert "v_sensitive_expires_at := v_job.hard_expires_at" in release
    assert "drop function if exists public.oasis_claim_remote_heartbeat_job(\n    uuid,\n    text,\n    integer,\n    timestamptz\n);" in sql
    assert "notify pgrst, 'reload schema'" in sql


def test_migration_defines_helpers_before_callers() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    add_business_days = sql.index(
        "create or replace function public.oasis_company_kakao_add_business_days"
    )
    write_history = sql.index(
        "create or replace function public.oasis_company_kakao_write_history"
    )
    eligibility = sql.index(
        "create or replace function public.oasis_check_company_kakao_guidance_eligibility"
    )
    reserve = sql.index(
        "create or replace function public.oasis_reserve_company_kakao_guidance"
    )
    finalize = sql.index(
        "create or replace function public.oasis_finalize_company_kakao_guidance"
    )
    cancel_invite = sql.index(
        "create or replace function public.oasis_claim_remote_cancel_invite"
    )

    assert add_business_days < finalize
    assert eligibility < reserve
    assert write_history < reserve
    assert write_history < finalize
    assert write_history < cancel_invite
    assert sql.count(
        "create or replace function public.oasis_company_kakao_add_business_days"
    ) == 1
    assert sql.count(
        "create or replace function public.oasis_company_kakao_write_history"
    ) == 1
    assert sql.count(
        "create or replace function public.oasis_check_company_kakao_guidance_eligibility"
    ) == 1


def test_customer_cancel_is_atomic_and_pii_safe_in_sql() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    start = sql.index(
        "create or replace function public.oasis_claim_remote_cancel_invite"
    )
    end = sql.index(
        "alter table public.oasis_company_kakao_guidance_settings enable row level security",
        start,
    )
    block = sql[start:end]

    assert "secure_review_link_id = v_invite.id" in block
    assert "from public.oasis_company_kakao_guidance_messages" in block
    assert "for update" in block
    assert "insert into public.oasis_company_kakao_contact_controls" in block
    assert "on conflict (company_uid) do update" in block
    assert "recipient_phone_hash" in block
    assert "status = 'opted_out'" in block
    assert "update public.oasis_company_kakao_followup_outbox" in block
    assert "f.status in ('pending', 'running', 'retry')" in block
    assert "claim_invite_cancelled_and_opted_out" in block
    assert "reason = p_reason" not in block
    assert "recipient_phone text" not in block


def test_server_eligibility_blocks_all_contact_opt_out_flags() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "add column if not exists opt_out_at timestamptz" in sql
    assert "c.do_not_contact is true or c.opt_out_at is not null" in sql
    assert "c.do_not_contact is not true\n          and c.opt_out_at is null" in sql


def test_migration_remains_replay_safe() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table if not exists" in sql
    assert "create index if not exists" in sql
    assert "add column if not exists opt_out_at" in sql
    assert "drop constraint if exists oasis_company_kakao_guidance_messages_status_check" in sql
    assert "on conflict (singleton) do nothing" in sql
    assert "drop trigger if exists oasis_guidance_settings_updated_at" in sql
    assert sql.rstrip().endswith("commit;")


def test_live_delivery_is_bound_to_trusted_canonical_contact() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert (
        "function public.oasis_resolve_company_kakao_guidance_mobile" in sql
    )
    assert "p_contact_id uuid default null" in sql
    assert "c.id = p_contact_id" in sql
    assert "c.prospect_id = v_assignment.company_id" in sql
    assert "recipient_contact_id uuid" in sql
    assert "recipient_contact_updated_at timestamptz" in sql
    assert "p_recipient_contact_updated_at timestamptz default null" in sql
    assert "v_contact.updated_at" in sql
    assert (
        "c.updated_at is not distinct from\n"
        "              v_message.recipient_contact_updated_at"
        in sql
    )
    assert "oasis_guidance_prospect_contacts_updated_at" in sql
    assert "delivery_mode <> 'live' or recipient_contact_id is not null" in sql
    assert "v_mode = 'live' and (" in sql
    assert "p_contact_id is null" in sql
    assert "canonical_mobile_required" in sql
    assert "canonical_mobile_mismatch" in sql


def test_cancel_and_dnc_clear_actual_guidance_delivery_ciphertext() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    cancel_start = sql.index(
        "create or replace function public.oasis_cancel_company_kakao_guidance"
    )
    cancel_end = sql.index(
        "create or replace function public.oasis_cancel_company_kakao_guidance_for_invite",
        cancel_start,
    )
    cancel_block = sql[cancel_start:cancel_end]
    assert "update public.oasis_claim_remote_outbox" in cancel_block
    assert "secure_payload_ciphertext = ''" in cancel_block
    assert "o.status in ('pending', 'running', 'retry')" in cancel_block

    trigger_start = sql.index(
        "create or replace function public.oasis_cancel_company_kakao_delivery_for_control"
    )
    trigger_end = sql.index(
        "create or replace function public.oasis_set_company_kakao_contact_control",
        trigger_start,
    )
    trigger_block = sql[trigger_start:trigger_end]
    assert "new.status not in ('opted_out', 'admin_blocked')" in trigger_block
    assert "update public.oasis_claim_remote_outbox" in trigger_block
    assert "secure_payload_ciphertext = ''" in trigger_block
    assert "oasis_guidance_control_cancel_delivery" in trigger_block


def test_final_send_readiness_rpc_is_pii_free_and_fail_closed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    start = sql.index(
        "create or replace function public.oasis_check_company_kakao_guidance_send_ready"
    )
    end = sql.index(
        "create or replace function public.oasis_company_kakao_guidance_feature_ready",
        start,
    )
    block = sql[start:end]
    output_contract = block[block.index("returns table"):block.index("language plpgsql")]

    assert "p_message_id uuid" in block
    assert "p_contact_id uuid" in block
    assert "p_recipient_phone_hash text" in block
    assert "delivery_binding_mismatch" in block
    assert "allowed boolean" in output_contract
    assert "code text" in output_contract
    assert "message_id uuid" in output_contract
    assert "phone" not in output_contract
    assert "ciphertext" not in output_contract
    assert "recipient_phone_hash" not in output_contract
    for fail_closed_code in (
        "do_not_contact",
        "assignment_changed",
        "canonical_mobile_changed",
        "invite_not_active",
        "delivery_not_active",
    ):
        assert fail_closed_code in block


def test_guidance_idempotency_replay_is_bound_to_the_full_request() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    start = sql.index(
        "create or replace function public.oasis_reserve_company_kakao_guidance"
    )
    end = sql.index(
        "create or replace function public.oasis_attach_company_kakao_guidance_invite",
        start,
    )
    block = sql[start:end]

    assert block.count("idempotency_conflict") == 2
    for binding in (
        "company_id is distinct from p_company_id",
        "company_uid is distinct from v_uid",
        "assignment_id is distinct from p_assignment_id",
        "recipient_contact_id is distinct from p_contact_id",
        "recipient_contact_updated_at is distinct from",
        "recipient_phone_hash is distinct from v_phone_hash",
        "message_type is distinct from v_type",
        "template_key is distinct from v_template_key",
        "template_version is distinct from v_template_version",
        "delivery_mode is distinct from v_mode",
    ):
        assert block.count(binding) == 2


def test_opt_out_paths_use_control_first_lock_order() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    cancel_start = sql.index(
        "create or replace function public.oasis_cancel_company_kakao_guidance"
    )
    wrapper_start = sql.index(
        "create or replace function public.oasis_cancel_company_kakao_guidance_for_invite",
        cancel_start,
    )
    trigger_start = sql.index(
        "create or replace function public.oasis_cancel_company_kakao_delivery_for_control",
        wrapper_start,
    )
    claim_start = sql.index(
        "create or replace function public.oasis_claim_remote_cancel_invite"
    )
    claim_end = sql.index(
        "create or replace function public.oasis_claim_remote_get_session_status",
        claim_start,
    )

    cancel = sql[cancel_start:wrapper_start]
    wrapper = sql[wrapper_start:trigger_start]
    claim_cancel = sql[claim_start:claim_end]
    assert cancel.index(
        "insert into public.oasis_company_kakao_contact_controls"
    ) < cancel.index("for update;", cancel.index("if coalesce(p_opt_out"))
    assert "for update" not in wrapper
    assert claim_cancel.index(
        "insert into public.oasis_company_kakao_contact_controls"
    ) < claim_cancel.index("for update;", claim_cancel.index("insert into public.oasis_company_kakao_contact_controls"))


def test_terminal_outbox_reconciler_excludes_cancelled_guidance() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    reconcile_start = sql.index(
        "create or replace function public.oasis_reconcile_company_kakao_guidance_outbox"
    )
    reconcile_end = sql.index(
        "select public.oasis_reconcile_company_kakao_guidance_outbox(1000)",
        reconcile_start,
    )
    block = sql[reconcile_start:reconcile_end]

    assert "m.status in ('queued', 'sending', 'sent')" in block
    assert "m.status in ('queued', 'sending')" in block
    assert "m.status = 'sent'" in block
    assert "v_result.code = 'finalized'" in block
    assert "m.status <> 'delivered'" not in block


def test_job_active_provider_boundary_rpc_is_pii_free_and_fail_closed() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    start = sql.index(
        "create or replace function public.oasis_claim_remote_check_job_active"
    )
    end = sql.index(
        "create or replace function public.oasis_claim_remote_consume_invite",
        start,
    )
    block = sql[start:end]
    output_contract = block[block.index("returns table"):block.index("language plpgsql")]

    assert "allowed boolean" in output_contract
    assert "code text" in output_contract
    assert "job_id uuid" in output_contract
    assert "phone" not in output_contract
    assert "ciphertext" not in output_contract
    assert "v_job.status <> 'waiting'" in block
    assert "v_job.stage <> 'submission_reserved'" in block
    assert "v_job.status <> 'running'" in block
    assert "v_job.lease_owner is distinct from v_worker" in block
    assert "v_job.hard_expires_at <= v_now" in block
    assert "v_job.sensitive_expires_at <= v_now" in block
