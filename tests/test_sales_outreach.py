from __future__ import annotations

import hashlib
import os
import unittest
from unittest.mock import patch

import requests

from sales_outreach import (
    CHANNEL_EMAIL,
    CHANNEL_KAKAO,
    CHANNEL_SMS,
    HIWORKS_OFFICE_TOKEN_ENV,
    HIWORKS_SEND_MAIL_URL,
    HIWORKS_USER_ID_ENV,
    KAKAO_BIZ_CLIENT_ID_ENV,
    KAKAO_BIZ_CLIENT_SECRET_ENV,
    KAKAO_BIZ_CONTRACT_CONFIRMED_ENV,
    KAKAO_BIZ_SEND_URL,
    KAKAO_BIZ_SENDER_KEY_ENV,
    KAKAO_BIZ_SENDER_NO_ENV,
    KAKAO_BIZ_TEMPLATE_CODE_ENV,
    KAKAO_BIZ_TOKEN_URL,
    LEGACY_CONTACT_PHONE_HASH_KEY_ENV,
    OUTREACH_ENABLED_ENV,
    OUTREACH_COMPLIANCE_CONFIRMED_ENV,
    OUTREACH_EMAIL_OPT_OUT_TEXT_ENV,
    OUTREACH_SENDER_ADDRESS_ENV,
    OUTREACH_SENDER_EMAIL_ENV,
    OUTREACH_SENDER_NAME_ENV,
    OUTREACH_SENDER_PHONE_ENV,
    OUTREACH_SMS_FREE_OPT_OUT_NUMBER_ENV,
    SMSKOREA_MESSAGE_SECRET_MODE_ENV,
    SMSKOREA_MESSAGE_URL,
    SMSKOREA_SEC_API_KEY_ENV,
    SMSKOREA_SENDER_ENV,
    SMSKOREA_TOKEN_URL,
    SMSKOREA_USER_ID_ENV,
    SOLAPI_ALIMTALK_DEFAULT_TEMPLATE_CODE,
    SOLAPI_CLAIM_AUTH_TEMPLATE_ENV,
    claim_auth_alimtalk_templates,
    claim_auth_alimtalk_readiness,
    channel_readiness,
    send_claim_auth_alimtalk,
    send_outreach,
    validate_claim_auth_alimtalk,
    validate_message,
)
from solapi_alimtalk_client import (
    SOLAPI_API_KEY_ENV,
    SOLAPI_API_SECRET_ENV,
    SOLAPI_KAKAO_CHANNEL_ID_ENV,
    SolapiAlimtalkError,
)


def _test_phone(*parts: str, separator: str = "") -> str:
    return separator.join(parts)


def _test_email(local: str, domain: str) -> str:
    return "@".join((local, domain))


_RECIPIENT_EMAIL = _test_email("person", "example.com")
_SENDER_EMAIL = _test_email("sender", "example.invalid")
_MOBILE = _test_phone("010", "1234", "5678")
_FORMATTED_MOBILE = _test_phone("010", "1234", "5678", separator="-")
_INTERNATIONAL_MOBILE = "+82 " + _test_phone(
    "10", "1234", "5678", separator="-"
)
_KAKAO_MOBILE = _test_phone("82", "10", "1234", "5678")
_LANDLINE = _test_phone("02", "1234", "5678", separator="-")
_LANDLINE_DIGITS = _test_phone("02", "1234", "5678")
_FREE_OPT_OUT = _test_phone("080", "000", "0000", separator="-")
_SENDER_PHONE = _test_phone("02", "0000", "0000", separator="-")


class _Response:
    def __init__(self, payload, *, status_code: int = 200, text: str = ""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class _Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected network call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _sms_env(*, mode: str = "omit") -> dict[str, str]:
    return {
        OUTREACH_ENABLED_ENV: "true",
        OUTREACH_COMPLIANCE_CONFIRMED_ENV: "true",
        OUTREACH_SMS_FREE_OPT_OUT_NUMBER_ENV: _FREE_OPT_OUT,
        OUTREACH_SENDER_NAME_ENV: "오아시스",
        SMSKOREA_USER_ID_ENV: "private-user-id",
        SMSKOREA_SEC_API_KEY_ENV: "private-secret-key",
        SMSKOREA_SENDER_ENV: _LANDLINE,
        SMSKOREA_MESSAGE_SECRET_MODE_ENV: mode,
        LEGACY_CONTACT_PHONE_HASH_KEY_ENV: "private-hash-key-with-at-least-32-characters",
    }


def _email_env() -> dict[str, str]:
    return {
        OUTREACH_ENABLED_ENV: "true",
        OUTREACH_COMPLIANCE_CONFIRMED_ENV: "true",
        OUTREACH_EMAIL_OPT_OUT_TEXT_ENV: "수신거부: 회신",
        OUTREACH_SENDER_NAME_ENV: "오아시스",
        OUTREACH_SENDER_EMAIL_ENV: _SENDER_EMAIL,
        OUTREACH_SENDER_PHONE_ENV: _SENDER_PHONE,
        OUTREACH_SENDER_ADDRESS_ENV: "서울시 테스트구 테스트로 1",
        HIWORKS_OFFICE_TOKEN_ENV: "private-office-token",
        HIWORKS_USER_ID_ENV: "private-user-id",
        LEGACY_CONTACT_PHONE_HASH_KEY_ENV: (
            "private-hash-key-with-at-least-32-characters"
        ),
    }


def _kakao_env() -> dict[str, str]:
    return {
        OUTREACH_ENABLED_ENV: "true",
        OUTREACH_COMPLIANCE_CONFIRMED_ENV: "true",
        KAKAO_BIZ_CLIENT_ID_ENV: "private-client-id",
        KAKAO_BIZ_CLIENT_SECRET_ENV: "private-client-secret",
        KAKAO_BIZ_SENDER_KEY_ENV: "private-sender-key",
        KAKAO_BIZ_SENDER_NO_ENV: _LANDLINE,
        KAKAO_BIZ_TEMPLATE_CODE_ENV: "private-template-code",
        KAKAO_BIZ_CONTRACT_CONFIRMED_ENV: "true",
        LEGACY_CONTACT_PHONE_HASH_KEY_ENV: "private-hash-key-with-at-least-32-characters",
    }


def _solapi_claim_auth_env() -> dict[str, str]:
    return {
        OUTREACH_ENABLED_ENV: "true",
        OUTREACH_COMPLIANCE_CONFIRMED_ENV: "true",
        LEGACY_CONTACT_PHONE_HASH_KEY_ENV: (
            "private-hash-key-with-at-least-32-characters"
        ),
        SOLAPI_API_KEY_ENV: "private-solapi-key",
        SOLAPI_API_SECRET_ENV: "private-solapi-secret",
        SOLAPI_KAKAO_CHANNEL_ID_ENV: "private-solapi-channel",
        SOLAPI_CLAIM_AUTH_TEMPLATE_ENV: "approved-claim-auth-template",
    }


def _marketing_sms(value: str) -> str:
    return value


def _marketing_email(value: str) -> str:
    return value


def _prepared_sms(value: str) -> str:
    return (
        f"(광고)오아시스\n{value}\n{_LANDLINE}\n"
        f"무료수신거부 {_FREE_OPT_OUT}"
    )


def _prepared_email(value: str) -> str:
    return (
        f"{value}\n\n---\n전송자: 오아시스\n"
        f"이메일: {_SENDER_EMAIL}\n전화: {_SENDER_PHONE}\n"
        "주소: 서울시 테스트구 테스트로 1\n수신거부: 회신"
    )


class SalesOutreachTests(unittest.TestCase):
    def setUp(self):
        self.send_hours = patch(
            "sales_outreach._within_standard_send_hours",
            return_value=True,
        )
        self.send_hours.start()

    def tearDown(self):
        self.send_hours.stop()

    def assert_safe_result(self, result):
        self.assertEqual(
            set(result),
            {"ok", "code", "message", "provider_id"},
        )

    def test_hard_gate_is_off_by_default_and_no_network_runs(self):
        session = _Session()
        with patch.dict(os.environ, _email_env(), clear=True):
            os.environ.pop(OUTREACH_ENABLED_ENV)
            readiness = channel_readiness(CHANNEL_EMAIL)
            result = send_outreach(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "subject",
                "body",
                "email-1",
                session=session,
            )

        self.assertFalse(readiness["external_send_allowed"])
        self.assertIn(OUTREACH_ENABLED_ENV, readiness["missing_env_names"])
        self.assertEqual(result["code"], "OUTREACH_DISABLED")
        self.assertEqual(session.calls, [])
        self.assert_safe_result(result)

    def test_readiness_returns_env_names_without_secret_values(self):
        values = _kakao_env()
        with patch.dict(os.environ, values, clear=True):
            readiness = channel_readiness(CHANNEL_KAKAO)

        self.assertTrue(readiness["ready"])
        self.assertTrue(readiness["external_send_allowed"])
        readiness_text = repr(readiness)
        for value in values.values():
            if value != "true":
                self.assertNotIn(value, readiness_text)

    def test_claim_auth_readiness_uses_solapi_not_legacy_kakao_provider(self):
        values = _solapi_claim_auth_env()
        with patch.dict(os.environ, values, clear=True):
            readiness = claim_auth_alimtalk_readiness()

        self.assertTrue(readiness["ready"])
        self.assertEqual(readiness["provider"], "solapi")
        self.assertNotIn(
            KAKAO_BIZ_CLIENT_ID_ENV,
            readiness["required_env_names"],
        )
        readiness_text = repr(readiness)
        for value in values.values():
            if value != "true":
                self.assertNotIn(value, readiness_text)

    def test_alimtalk_template_options_are_allowlisted_and_redacted(self):
        templates = claim_auth_alimtalk_templates()

        self.assertEqual(
            [item["code"] for item in templates],
            ["auth_start", "auth_resume", "next_auth", "complete", "failed"],
        )
        self.assertEqual(
            templates[0]["code"],
            SOLAPI_ALIMTALK_DEFAULT_TEMPLATE_CODE,
        )
        self.assertNotIn("KA01", repr(templates))

    def test_selected_template_readiness_requires_only_its_template_id(self):
        values = _solapi_claim_auth_env()
        values.pop(SOLAPI_CLAIM_AUTH_TEMPLATE_ENV)
        values["SOLAPI_TEMPLATE_AUTH_RESUME_ID"] = "approved-resume-template"

        with patch.dict(os.environ, values, clear=True):
            resume = claim_auth_alimtalk_readiness("auth_resume")
            start = claim_auth_alimtalk_readiness("auth_start")

        self.assertTrue(resume["ready"])
        self.assertFalse(start["ready"])
        self.assertIn(
            SOLAPI_CLAIM_AUTH_TEMPLATE_ENV,
            start["missing_env_names"],
        )
        self.assertNotIn("approved-resume-template", repr(resume))

    def test_claim_auth_inputs_require_scheme_free_address(self):
        accepted = validate_claim_auth_alimtalk(
            _MOBILE,
            "고객 이름",
            "claim.example.test/c/token?step=1",
        )
        http_rejected = validate_claim_auth_alimtalk(
            _MOBILE,
            "고객 이름",
            "http://claim.example.test/c/token",
        )
        https_rejected = validate_claim_auth_alimtalk(
            _MOBILE,
            "고객 이름",
            "https://claim.example.test/c/token",
        )

        self.assertTrue(accepted["ok"])
        self.assertEqual(http_rejected["code"], "AUTH_LINK_INVALID")
        self.assertEqual(https_rejected["code"], "AUTH_LINK_INVALID")
        self.assertNotIn("claim.example.test", repr(http_rejected))

    def test_claim_auth_send_uses_fixed_template_and_exact_variables(self):
        class _Solapi:
            def __init__(self):
                self.calls = []

            def send_alimtalk(self, to, template_id, **kwargs):
                self.calls.append((to, template_id, kwargs))

        client = _Solapi()
        values = _solapi_claim_auth_env()
        with patch.dict(os.environ, values, clear=True):
            result = send_claim_auth_alimtalk(
                _FORMATTED_MOBILE,
                "고객 이름",
                "claim.example.test/c/token",
                "claim-auth-request-1",
                client=client,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "ACCEPTED")
        self.assertEqual(
            client.calls,
            [
                (
                    _FORMATTED_MOBILE,
                    values[SOLAPI_CLAIM_AUTH_TEMPLATE_ENV],
                    {
                        "variables": {
                            "#{고객명}": "고객 이름",
                            "#{인증링크}": "claim.example.test/c/token",
                        },
                        "disable_sms": True,
                    },
                )
            ],
        )
        self.assert_safe_result(result)

    def test_claim_auth_send_uses_selected_allowlisted_template(self):
        class _Solapi:
            def __init__(self):
                self.calls = []

            def send_alimtalk(self, to, template_id, **kwargs):
                self.calls.append((to, template_id, kwargs))

        client = _Solapi()
        values = _solapi_claim_auth_env()
        values["SOLAPI_TEMPLATE_AUTH_RESUME_ID"] = "approved-resume-template"
        with patch.dict(os.environ, values, clear=True):
            result = send_claim_auth_alimtalk(
                _FORMATTED_MOBILE,
                "고객 이름",
                "claim.example.test/c/token",
                "claim-auth-request-selected",
                template_code="auth_resume",
                client=client,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(client.calls[0][1], "approved-resume-template")
        self.assertNotEqual(
            client.calls[0][1],
            values[SOLAPI_CLAIM_AUTH_TEMPLATE_ENV],
        )

    def test_unknown_alimtalk_template_is_rejected_before_provider_call(self):
        class _Solapi:
            def __init__(self):
                self.calls = []

            def send_alimtalk(self, *args, **kwargs):
                self.calls.append((args, kwargs))

        client = _Solapi()
        with patch.dict(os.environ, _solapi_claim_auth_env(), clear=True):
            result = send_claim_auth_alimtalk(
                _MOBILE,
                "고객 이름",
                "claim.example.test/c/token",
                "claim-auth-request-unknown",
                template_code="user-supplied-template",
                client=client,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "TEMPLATE_NOT_ALLOWED")
        self.assertEqual(client.calls, [])
        self.assert_safe_result(result)

    def test_claim_auth_timeout_is_delivery_unknown_and_redacted(self):
        class _TimeoutSolapi:
            def send_alimtalk(self, *_args, **_kwargs):
                raise SolapiAlimtalkError(
                    "TIMEOUT",
                    "private provider detail",
                )

        with patch.dict(os.environ, _solapi_claim_auth_env(), clear=True):
            result = send_claim_auth_alimtalk(
                _MOBILE,
                "고객 이름",
                "claim.example.test/c/token",
                "claim-auth-request-2",
                client=_TimeoutSolapi(),
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DELIVERY_UNKNOWN")
        self.assertNotIn("private", repr(result))
        self.assert_safe_result(result)

    def test_compliance_confirmation_is_required_before_network(self):
        values = _email_env()
        values.pop(OUTREACH_COMPLIANCE_CONFIRMED_ENV)
        session = _Session()
        with patch.dict(os.environ, values, clear=True):
            readiness = channel_readiness(CHANNEL_EMAIL)
            result = send_outreach(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "(광고) subject",
                _marketing_email("body"),
                "email-compliance-gate",
                session=session,
            )

        self.assertFalse(readiness["ready"])
        self.assertEqual(
            readiness["code"],
            "COMPLIANCE_CONFIRMATION_REQUIRED",
        )
        self.assertEqual(result["code"], readiness["code"])
        self.assertEqual(session.calls, [])

    def test_email_sender_identity_must_be_complete_and_valid(self):
        values = _email_env()
        values[OUTREACH_SENDER_EMAIL_ENV] = "not-an-email"
        session = _Session()
        with patch.dict(os.environ, values, clear=True):
            readiness = channel_readiness(CHANNEL_EMAIL)
            result = send_outreach(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "subject",
                "body",
                "email-sender-gate",
                session=session,
            )

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["code"], "SENDER_CONFIGURATION_REQUIRED")
        self.assertIn(
            OUTREACH_SENDER_EMAIL_ENV,
            readiness["missing_env_names"],
        )
        self.assertEqual(result["code"], readiness["code"])
        self.assertEqual(session.calls, [])

    def test_free_form_content_validates_without_manual_legal_footer(self):
        with patch.dict(os.environ, _email_env(), clear=True):
            email = validate_message(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "subject",
                "담당자 자유 입력 본문",
            )
        with patch.dict(os.environ, _sms_env(), clear=True):
            sms = validate_message(
                CHANNEL_SMS,
                _MOBILE,
                "",
                "담당자 자유 입력 본문",
            )

        self.assertTrue(email["ok"])
        self.assertTrue(sms["ok"])

    def test_email_still_requires_a_user_written_subject(self):
        with patch.dict(os.environ, _email_env(), clear=True):
            result = validate_message(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "",
                "담당자 자유 입력 본문",
            )

        self.assertEqual(result["code"], "SUBJECT_REQUIRED")

    def test_sms_and_kakao_are_blocked_during_night_hours(self):
        sms_session = _Session()
        kakao_session = _Session()
        with patch(
            "sales_outreach._within_standard_send_hours",
            return_value=False,
        ), patch.dict(os.environ, _sms_env(), clear=True):
            sms_result = send_outreach(
                CHANNEL_SMS,
                _MOBILE,
                "",
                _marketing_sms("body"),
                "sms-night-block",
                session=sms_session,
            )
        with patch(
            "sales_outreach._within_standard_send_hours",
            return_value=False,
        ), patch.dict(os.environ, _kakao_env(), clear=True):
            kakao_result = send_outreach(
                CHANNEL_KAKAO,
                _MOBILE,
                "",
                "body",
                "kakao-night-block",
                session=kakao_session,
            )

        self.assertEqual(sms_result["code"], "NIGHT_SEND_BLOCKED")
        self.assertEqual(kakao_result["code"], "NIGHT_SEND_BLOCKED")
        self.assertEqual(sms_session.calls, [])
        self.assertEqual(kakao_session.calls, [])

    def test_email_remains_available_during_night_hours(self):
        session = _Session(
            _Response(
                {
                    "code": "SUC",
                    "result": {
                        "successList": [_RECIPIENT_EMAIL],
                        "wrongList": [],
                    },
                }
            )
        )
        with patch(
            "sales_outreach._within_standard_send_hours",
            return_value=False,
        ), patch.dict(os.environ, _email_env(), clear=True):
            result = send_outreach(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "(광고) subject",
                _marketing_email("body"),
                "email-night-allowed",
                session=session,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(session.calls), 1)

    def test_sms_schema_mode_must_be_explicit(self):
        values = _sms_env()
        values.pop(SMSKOREA_MESSAGE_SECRET_MODE_ENV)
        session = _Session()
        with patch.dict(os.environ, values, clear=True):
            readiness = channel_readiness(CHANNEL_SMS)
            result = send_outreach(
                CHANNEL_SMS,
                _FORMATTED_MOBILE,
                "",
                "short body",
                "sms-1",
                session=session,
            )

        self.assertFalse(readiness["ready"])
        self.assertEqual(
            readiness["code"],
            "SMSKOREA_SCHEMA_CONFIRMATION_REQUIRED",
        )
        self.assertIn(
            SMSKOREA_MESSAGE_SECRET_MODE_ENV,
            readiness["missing_env_names"],
        )
        self.assertEqual(result["code"], readiness["code"])
        self.assertEqual(session.calls, [])

    def test_validation_is_redacted_and_channel_specific(self):
        invalid_email = validate_message(
            CHANNEL_EMAIL,
            "private invalid address",
            "subject",
            "private body",
        )
        invalid_phone = validate_message(
            CHANNEL_KAKAO,
            "010-12",
            "",
            "private body",
        )
        missing_subject = validate_message(
            CHANNEL_EMAIL,
            _RECIPIENT_EMAIL,
            "",
            "private body",
        )

        for result in (invalid_email, invalid_phone, missing_subject):
            self.assertFalse(result["ok"])
            self.assertNotIn("private", repr(result))
            self.assert_safe_result(result)

    def test_sms_uses_euc_kr_size_and_requires_lms_title(self):
        exactly_sms = "가" * 45
        too_long_for_sms = "가" * 46

        self.assertTrue(
            validate_message(
                CHANNEL_SMS,
                _MOBILE,
                "",
                exactly_sms,
            )["ok"]
        )
        self.assertEqual(
            validate_message(
                CHANNEL_SMS,
                _MOBILE,
                "",
                too_long_for_sms,
            )["code"],
            "SUBJECT_REQUIRED",
        )
        self.assertEqual(
            validate_message(
                CHANNEL_SMS,
                _MOBILE,
                "title",
                "emoji \U0001f680",
            )["code"],
            "SMS_ENCODING_UNSUPPORTED",
        )

    def test_sms_token_and_message_requests_match_official_python_shape(self):
        session = _Session(
            _Response({"result": {"accessToken": "private-access-token"}}),
            _Response({"result": "accepted"}),
        )
        values = _sms_env(mode="omit")
        with patch.dict(os.environ, values, clear=True):
            result = send_outreach(
                CHANNEL_SMS,
                _FORMATTED_MOBILE,
                "",
                _marketing_sms("안내 문자"),
                "sms-2",
                session=session,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["code"], "ACCEPTED")
        self.assertEqual(result["provider_id"], "")
        self.assertEqual([call[0] for call in session.calls], [
            SMSKOREA_TOKEN_URL,
            SMSKOREA_MESSAGE_URL,
        ])
        token_request = session.calls[0][1]
        self.assertEqual(
            token_request["json"],
            {
                "userId": "private-user-id",
                "sec_apiKey": "private-secret-key",
            },
        )
        message_request = session.calls[1][1]
        self.assertEqual(
            message_request["json"],
            {
                "userId": "private-user-id",
                "sender": _LANDLINE_DIGITS,
                "receiver": [_MOBILE],
                "title": "",
                "message": _prepared_sms("안내 문자"),
                "messageType": "sms",
            },
        )
        self.assertEqual(
            message_request["headers"]["Authorization"],
            "Bearer private-access-token",
        )
        self.assertEqual(message_request["timeout"], 10.0)
        self.assert_safe_result(result)

    def test_sms_include_mode_matches_official_php_and_gas_shape(self):
        session = _Session(
            _Response({"result": {"accessToken": "private-access-token"}}),
            _Response({}),
        )
        with patch.dict(os.environ, _sms_env(mode="include"), clear=True):
            result = send_outreach(
                CHANNEL_SMS,
                _MOBILE,
                "long title",
                _marketing_sms("가" * 46),
                "sms-3",
                session=session,
            )

        payload = session.calls[1][1]["json"]
        self.assertTrue(result["ok"])
        self.assertEqual(payload["messageType"], "lms")
        self.assertEqual(payload["sec_apiKey"], "private-secret-key")

    def test_hiworks_uses_bearer_and_multipart_form_data(self):
        session = _Session(
            _Response(
                {
                    "code": "SUC",
                    "result": {
                        "successList": [_RECIPIENT_EMAIL],
                        "dupList": [],
                        "wrongList": [],
                    },
                }
            )
        )
        with patch.dict(os.environ, _email_env(), clear=True):
            result = send_outreach(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "(광고) 영업 안내",
                _marketing_email("자유 입력 body 본문"),
                "email-2",
                session=session,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(session.calls[0][0], HIWORKS_SEND_MAIL_URL)
        request = session.calls[0][1]
        self.assertEqual(
            request["headers"]["Authorization"],
            "Bearer private-office-token",
        )
        self.assertNotIn("data", request)
        fields = request["files"]
        self.assertEqual(
            set(fields),
            {
                "to",
                "user_id",
                "cc",
                "bcc",
                "subject",
                "content",
                "save_sent_mail",
            },
        )
        self.assertEqual(fields["to"], (None, _RECIPIENT_EMAIL))
        self.assertEqual(fields["subject"], (None, "(광고) 영업 안내"))
        self.assertEqual(
            fields["content"],
            (None, _prepared_email("자유 입력 body 본문")),
        )
        self.assertEqual(fields["save_sent_mail"], (None, "Y"))
        self.assert_safe_result(result)

    def test_hiworks_success_code_without_recipient_success_fails_closed(self):
        session = _Session(
            _Response(
                {
                    "code": "SUC",
                    "result": {
                        "successList": [],
                        "wrongList": [_RECIPIENT_EMAIL],
                    },
                }
            )
        )
        with patch.dict(os.environ, _email_env(), clear=True):
            result = send_outreach(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "(광고) subject",
                _marketing_email("body"),
                "email-rejected-1",
                session=session,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "PROVIDER_REJECTED")
        self.assertNotIn(_RECIPIENT_EMAIL, repr(result))

    def test_kakao_friendtalk_has_no_sms_fallback_and_hashes_cid(self):
        session = _Session(
            _Response(
                {
                    "code": "200",
                    "access_token": "private-oauth-token",
                    "token_type": "bearer",
                }
            ),
            _Response({"code": "200", "uid": "safe-provider-uid"}),
        )
        idempotency_key = "kakao-unique-1"
        with patch.dict(os.environ, _kakao_env(), clear=True):
            result = send_outreach(
                CHANNEL_KAKAO,
                _INTERNATIONAL_MOBILE,
                "",
                "담당자 자유 입력 내용",
                idempotency_key,
                session=session,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider_id"], "")
        self.assertEqual([call[0] for call in session.calls], [
            KAKAO_BIZ_TOKEN_URL,
            KAKAO_BIZ_SEND_URL,
        ])
        token_request = session.calls[0][1]
        self.assertEqual(
            token_request["headers"]["Authorization"],
            "Basic private-client-id private-client-secret",
        )
        self.assertEqual(
            token_request["data"],
            {"grant_type": "client_credentials"},
        )

        payload = session.calls[1][1]["json"]
        self.assertEqual(
            payload,
            {
                "message_type": "FT",
                "sender_key": "private-sender-key",
                "cid": (
                    "oasis-"
                    + hashlib.sha256(idempotency_key.encode()).hexdigest()[:32]
                ),
                "template_code": "private-template-code",
                "phone_number": _KAKAO_MOBILE,
                "sender_no": _LANDLINE_DIGITS,
                "message": "담당자 자유 입력 내용",
                "ad_flag": "Y",
                "fall_back_yn": False,
            },
        )
        self.assertNotIn("fall_back_message", payload)
        self.assertNotIn("fall_back_message_type", payload)
        self.assertNotIn(idempotency_key, repr(payload))

    def test_kakao_contract_confirmation_is_required(self):
        values = _kakao_env()
        values[KAKAO_BIZ_CONTRACT_CONFIRMED_ENV] = "false"
        session = _Session()
        with patch.dict(os.environ, values, clear=True):
            readiness = channel_readiness(CHANNEL_KAKAO)
            result = send_outreach(
                CHANNEL_KAKAO,
                _MOBILE,
                "",
                "body",
                "kakao-2",
                session=session,
            )

        self.assertFalse(readiness["ready"])
        self.assertEqual(readiness["code"], "KAKAO_CONTRACT_REQUIRED")
        self.assertEqual(result["code"], "KAKAO_CONTRACT_REQUIRED")
        self.assertEqual(session.calls, [])

    def test_network_and_http_failures_never_echo_sensitive_data(self):
        secrets = (
            "private-office-token",
            _RECIPIENT_EMAIL,
            "private message body",
        )
        sessions = (
            _Session(requests.ConnectionError(" ".join(secrets))),
            _Session(
                _Response(
                    {"message": " ".join(secrets)},
                    status_code=401,
                    text=" ".join(secrets),
                )
            ),
        )
        with patch.dict(os.environ, _email_env(), clear=True):
            results = [
                send_outreach(
                    CHANNEL_EMAIL,
                    secrets[1],
                    "(광고) subject",
                    _marketing_email(secrets[2]),
                    f"email-failure-{index}",
                    session=session,
                )
                for index, session in enumerate(sessions)
            ]

        for result in results:
            self.assertFalse(result["ok"])
            for secret in secrets:
                self.assertNotIn(secret, repr(result))
            self.assert_safe_result(result)

        self.assertEqual(results[0]["code"], "DELIVERY_UNKNOWN")
        self.assertEqual(results[1]["code"], "PROVIDER_AUTH_FAILED")

    def test_sms_send_network_failure_is_delivery_unknown(self):
        session = _Session(
            _Response({"result": {"accessToken": "private-access-token"}}),
            requests.Timeout("private message may have been accepted"),
        )
        with patch.dict(os.environ, _sms_env(), clear=True):
            result = send_outreach(
                CHANNEL_SMS,
                _MOBILE,
                "",
                _marketing_sms("body"),
                "sms-unknown-1",
                session=session,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DELIVERY_UNKNOWN")
        self.assertNotIn("private", repr(result))
        self.assertEqual(len(session.calls), 2)

    def test_hiworks_send_network_failure_is_delivery_unknown(self):
        session = _Session(
            requests.ConnectionError("private email may have been accepted")
        )
        with patch.dict(os.environ, _email_env(), clear=True):
            result = send_outreach(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "(광고) subject",
                _marketing_email("body"),
                "email-unknown-1",
                session=session,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DELIVERY_UNKNOWN")
        self.assertNotIn(_RECIPIENT_EMAIL, repr(result))
        self.assertEqual(len(session.calls), 1)

    def test_kakao_send_network_failure_is_delivery_unknown(self):
        session = _Session(
            _Response({"code": "200", "access_token": "private-token"}),
            requests.ConnectionError("private kakao may have been accepted"),
        )
        with patch.dict(os.environ, _kakao_env(), clear=True):
            result = send_outreach(
                CHANNEL_KAKAO,
                _MOBILE,
                "",
                "body",
                "kakao-unknown-1",
                session=session,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DELIVERY_UNKNOWN")
        self.assertNotIn("private", repr(result))
        self.assertEqual(len(session.calls), 2)

    def test_auth_token_network_failure_remains_retryable(self):
        session = _Session(requests.Timeout("private token request"))
        with patch.dict(os.environ, _kakao_env(), clear=True):
            result = send_outreach(
                CHANNEL_KAKAO,
                _MOBILE,
                "",
                "body",
                "kakao-auth-1",
                session=session,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "PROVIDER_NETWORK_ERROR")
        self.assertNotIn("private", repr(result))
        self.assertEqual(len(session.calls), 1)

    def test_provider_id_is_sanitized_before_returning(self):
        session = _Session(
            _Response({"code": "200", "access_token": "private-token"}),
            _Response(
                {
                    "code": "200",
                    "uid": "unsafe id " + _FORMATTED_MOBILE,
                }
            ),
        )
        with patch.dict(os.environ, _kakao_env(), clear=True):
            result = send_outreach(
                CHANNEL_KAKAO,
                _MOBILE,
                "",
                "body",
                "kakao-3",
                session=session,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider_id"], "")
        self.assertNotIn("010", repr(result))

    def test_all_provider_ids_are_omitted_even_when_phone_shaped(self):
        session = _Session(
            _Response({"code": "200", "access_token": "private-token"}),
            _Response({"code": "200", "uid": _LANDLINE_DIGITS}),
        )
        with patch.dict(os.environ, _kakao_env(), clear=True):
            result = send_outreach(
                CHANNEL_KAKAO,
                _MOBILE,
                "",
                "body",
                "kakao-landline-provider-id",
                session=session,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider_id"], "")
        self.assertNotIn(_LANDLINE_DIGITS, repr(result))

    def test_sms_requires_documented_http_200_acceptance(self):
        session = _Session(
            _Response({"result": {"accessToken": "private-access-token"}}),
            _Response({}, status_code=204),
        )
        with patch.dict(os.environ, _sms_env(), clear=True):
            result = send_outreach(
                CHANNEL_SMS,
                _MOBILE,
                "",
                _marketing_sms("body"),
                "sms-unexpected-2xx",
                session=session,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DELIVERY_UNKNOWN")

    def test_malformed_success_response_is_not_safe_to_retry(self):
        session = _Session(_Response({}))
        with patch.dict(os.environ, _email_env(), clear=True):
            result = send_outreach(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "(광고) subject",
                _marketing_email("body"),
                "email-malformed-response",
                session=session,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "DELIVERY_UNKNOWN")

    def test_phone_shaped_provider_id_is_never_returned(self):
        session = _Session(
            _Response({"code": "200", "access_token": "private-token"}),
            _Response({"code": "200", "uid": _MOBILE}),
        )
        with patch.dict(os.environ, _kakao_env(), clear=True):
            result = send_outreach(
                CHANNEL_KAKAO,
                _MOBILE,
                "",
                "body",
                "kakao-4",
                session=session,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider_id"], "")
        self.assertNotIn(_MOBILE, repr(result))

    def test_invalid_idempotency_key_blocks_network(self):
        session = _Session()
        with patch.dict(os.environ, _email_env(), clear=True):
            result = send_outreach(
                CHANNEL_EMAIL,
                _RECIPIENT_EMAIL,
                "(광고) subject",
                _marketing_email("body"),
                "contains private spaces",
                session=session,
            )

        self.assertEqual(result["code"], "IDEMPOTENCY_KEY_REQUIRED")
        self.assertEqual(session.calls, [])
        self.assertNotIn("private", repr(result))


if __name__ == "__main__":
    unittest.main()
