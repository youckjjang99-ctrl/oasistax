from __future__ import annotations

import hashlib
import hmac
import unittest
from unittest.mock import patch

import requests

from solapi_alimtalk_client import (
    KAKAO_GUIDANCE_MOCK_MODE_ENV,
    KAKAO_GUIDANCE_SEND_ENABLED_ENV,
    SOLAPI_API_KEY_ENV,
    SOLAPI_API_SECRET_ENV,
    SOLAPI_KAKAO_CHANNEL_ID_ENV,
    SOLAPI_SMS_FROM_ENV,
    SolapiAlimtalkClient,
    SolapiAlimtalkConfig,
    SolapiAlimtalkError,
    SolapiConfigurationError,
    build_hmac_authorization,
    environment_readiness,
    guidance_send_readiness,
)


class _Response:
    def __init__(
        self,
        payload,
        *,
        ok: bool = True,
        status_code: int = 200,
        text: str = "",
    ) -> None:
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = text

    def json(self):
        if isinstance(self._payload, ValueError):
            raise self._payload
        return self._payload


def _client(
    *,
    api_key: str = "test-api-key",
    api_secret: str = "test-api-secret",
    sms_from: str = "",
) -> SolapiAlimtalkClient:
    return SolapiAlimtalkClient(
        SolapiAlimtalkConfig(
            api_key=api_key,
            api_secret=api_secret,
            pf_id="KA01PF-channel",
            sms_from=sms_from,
        )
    )


class SolapiAlimtalkClientTests(unittest.TestCase):
    def test_hmac_authorization_uses_date_plus_salt(self) -> None:
        date = "2026-07-31T01:02:03.456Z"
        salt = "".join(("01234", "56789", "abcdef"))
        expected_signature = hmac.new(
            b"secret-value",
            f"{date}{salt}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        authorization = build_hmac_authorization(
            "api-key-value",
            "secret-value",
            date=date,
            salt=salt,
        )

        self.assertEqual(
            authorization,
            (
                "HMAC-SHA256 apiKey=api-key-value, "
                f"date={date}, salt={salt}, "
                f"signature={expected_signature}"
            ),
        )

    @patch(
        "solapi_alimtalk_client.secrets.token_hex",
        return_value="".join(("01234", "56789", "abcdef") * 2),
    )
    @patch("solapi_alimtalk_client.requests.post")
    def test_sends_ata_with_default_sms_fallback_disabled(
        self,
        post,
        _token_hex,
    ) -> None:
        post.return_value = _Response(
            {
                "groupInfo": {"groupId": "G4V-group"},
                "messageList": {
                    "M4V-message": {
                        "messageId": "M4V-message",
                        "statusCode": "2000",
                    }
                },
                "failedMessageList": [],
            }
        )

        result = _client().send_alimtalk(
            "-".join(("010", "1234", "5678")),
            "KA01TP-template",
            variables={
                "#{고객명}": "홍길동",
                "#{token}": "one-time-token",
            },
        )

        self.assertEqual(result.group_id, "G4V-group")
        self.assertEqual(result.message_id, "M4V-message")
        self.assertEqual(
            result.as_dict(),
            {
                "group_id": "G4V-group",
                "message_id": "M4V-message",
            },
        )
        request = post.call_args
        self.assertEqual(
            request.args[0],
            "https://api.solapi.com/messages/v4/send-many/detail",
        )
        self.assertEqual(request.kwargs["timeout"], 10.0)
        self.assertTrue(
            request.kwargs["headers"]["Authorization"].startswith(
                "HMAC-SHA256 apiKey=test-api-key, "
            )
        )
        self.assertEqual(
            request.kwargs["json"],
            {
                "messages": [
                    {
                        "to": "".join(("010", "1234", "5678")),
                        "type": "ATA",
                        "kakaoOptions": {
                            "pfId": "KA01PF-channel",
                            "templateId": "KA01TP-template",
                            "disableSms": True,
                            "variables": {
                                "#{고객명}": "홍길동",
                                "#{token}": "one-time-token",
                            },
                        },
                    }
                ],
                "showMessageList": True,
            },
        )
        self.assertNotIn("from", request.kwargs["json"]["messages"][0])

    @patch("solapi_alimtalk_client.requests.post")
    def test_sms_fallback_requires_and_sends_registered_sender(
        self,
        post,
    ) -> None:
        post.return_value = _Response(
            {
                "groupInfo": {"groupId": "group-id"},
                "messageList": [
                    {"messageId": "message-id", "statusCode": "2000"}
                ],
            }
        )

        result = _client(sms_from="-".join(("02", "1234", "5678"))).send_alimtalk(
            "".join(("010", "1234", "5678")),
            "template-id",
            disable_sms=False,
        )

        message = post.call_args.kwargs["json"]["messages"][0]
        self.assertFalse(message["kakaoOptions"]["disableSms"])
        self.assertEqual(message["from"], "".join(("02", "1234", "5678")))
        self.assertEqual(result.group_id, "group-id")
        self.assertEqual(result.message_id, "message-id")

    @patch("solapi_alimtalk_client.requests.post")
    def test_response_message_id_can_come_from_message_list_key(
        self,
        post,
    ) -> None:
        post.return_value = _Response(
            {
                "groupInfo": {"groupId": "group-id"},
                "messageList": {
                    "message-id-from-key": {"statusCode": "2000"}
                },
            }
        )

        result = _client().send_alimtalk(
            "".join(("010", "1234", "5678")),
            "template-id",
        )

        self.assertEqual(result.message_id, "message-id-from-key")

    def test_sms_fallback_without_sender_fails_before_network(self) -> None:
        with patch("solapi_alimtalk_client.requests.post") as post:
            with self.assertRaises(SolapiAlimtalkError) as raised:
                _client().send_alimtalk(
                    "".join(("010", "1234", "5678")),
                    "template-id",
                    disable_sms=False,
                )

        self.assertEqual(raised.exception.code, "INVALID_REQUEST")
        post.assert_not_called()

    @patch("solapi_alimtalk_client.requests.post")
    def test_http_error_does_not_expose_request_or_response_secrets(
        self,
        post,
    ) -> None:
        secrets_to_protect = (
            "private-api-secret",
            "private-api-key",
            "".join(("010", "1234", "5678")),
            "one-time-private-token",
            "KA01TP-private-template",
        )
        post.return_value = _Response(
            {},
            ok=False,
            status_code=401,
            text=" ".join(secrets_to_protect),
        )

        with self.assertRaises(SolapiAlimtalkError) as raised:
            _client(
                api_key=secrets_to_protect[1],
                api_secret=secrets_to_protect[0],
            ).send_alimtalk(
                secrets_to_protect[2],
                secrets_to_protect[4],
                variables={"#{token}": secrets_to_protect[3]},
            )

        error_text = f"{raised.exception!r} {raised.exception}"
        self.assertEqual(raised.exception.code, "HTTP_ERROR")
        self.assertEqual(raised.exception.http_status, 401)
        for sensitive_value in secrets_to_protect:
            self.assertNotIn(sensitive_value, error_text)

    @patch("solapi_alimtalk_client.requests.post")
    def test_network_exception_is_redacted(self, post) -> None:
        post.side_effect = requests.ConnectionError(
            "private-api-secret one-time-private-token"
        )

        with self.assertRaises(SolapiAlimtalkError) as raised:
            _client(api_secret="private-api-secret").send_alimtalk(
                "".join(("010", "1234", "5678")),
                "template-id",
                variables={"#{token}": "one-time-private-token"},
            )

        self.assertEqual(raised.exception.code, "NETWORK_ERROR")
        self.assertNotIn("private-api-secret", str(raised.exception))
        self.assertNotIn("one-time-private-token", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)

    @patch("solapi_alimtalk_client.requests.post")
    def test_failed_message_list_is_a_safe_failure(self, post) -> None:
        post.return_value = _Response(
            {
                "groupInfo": {"groupId": "group-id"},
                "failedMessageList": [
                    {
                        "to": "".join(("010", "1234", "5678")),
                        "statusMessage": "one-time-private-token",
                    }
                ],
            }
        )

        with self.assertRaises(SolapiAlimtalkError) as raised:
            _client().send_alimtalk(
                "".join(("010", "1234", "5678")),
                "template-id",
                variables={"#{token}": "one-time-private-token"},
            )

        self.assertEqual(raised.exception.code, "MESSAGE_REJECTED")
        self.assertNotIn("".join(("010", "1234", "5678")), str(raised.exception))
        self.assertNotIn("one-time-private-token", str(raised.exception))

    @patch("solapi_alimtalk_client.requests.get")
    def test_get_template_preview_returns_content_without_identifiers(
        self,
        get,
    ) -> None:
        get.return_value = _Response(
            {
                "templateList": [
                    {
                        "templateId": "private-template-id",
                        "channelId": "private-channel-id",
                        "content": "#{고객명}님, https://#{인증링크}",
                        "status": "APPROVED",
                        "buttons": [
                            {
                                "buttonName": "인증하기",
                                "linkMo": "https://#{인증링크}",
                            }
                        ],
                    }
                ]
            }
        )

        preview = _client().get_template_preview("private-template-id")

        self.assertEqual(
            preview,
            {
                "content": "#{고객명}님, https://#{인증링크}",
                "status": "APPROVED",
                "buttons": [
                    {
                        "name": "인증하기",
                        "mobile_url": "https://#{인증링크}",
                    }
                ],
            },
        )
        self.assertNotIn("private-template-id", repr(preview))
        self.assertNotIn("private-channel-id", repr(preview))
        request = get.call_args
        self.assertEqual(
            request.args[0],
            "https://api.solapi.com/kakao/v2/templates/",
        )
        self.assertEqual(request.kwargs["params"]["limit"], 1)
        self.assertTrue(
            request.kwargs["headers"]["Authorization"].startswith(
                "HMAC-SHA256 apiKey=test-api-key, "
            )
        )

    @patch("solapi_alimtalk_client.requests.get")
    def test_get_template_preview_failure_is_redacted(self, get) -> None:
        get.return_value = _Response(
            {"private": "provider detail"},
            ok=False,
            status_code=403,
            text="private-api-secret private-template-id",
        )

        with self.assertRaises(SolapiAlimtalkError) as raised:
            _client(api_secret="private-api-secret").get_template_preview(
                "private-template-id"
            )

        self.assertEqual(raised.exception.code, "HTTP_ERROR")
        self.assertNotIn("private-api-secret", str(raised.exception))
        self.assertNotIn("private-template-id", str(raised.exception))

    def test_environment_readiness_reports_names_not_values(self) -> None:
        template_env = "SOLAPI_TEMPLATE_AUTH_RESUME_ID"
        missing = environment_readiness(
            {},
            required_template_env_names=(template_env,),
        )
        self.assertFalse(missing["ready"])
        self.assertEqual(
            missing["missing_env_names"],
            [
                SOLAPI_API_KEY_ENV,
                SOLAPI_API_SECRET_ENV,
                SOLAPI_KAKAO_CHANNEL_ID_ENV,
                template_env,
            ],
        )

        values = {
            SOLAPI_API_KEY_ENV: "private-api-key",
            SOLAPI_API_SECRET_ENV: "private-api-secret",
            SOLAPI_KAKAO_CHANNEL_ID_ENV: "private-channel-id",
            SOLAPI_SMS_FROM_ENV: "".join(("02", "1234", "5678")),
            template_env: "private-template-id",
        }
        ready = environment_readiness(
            values,
            required_template_env_names=(template_env,),
        )
        self.assertTrue(ready["ready"])
        self.assertTrue(ready["template_ids_configured"])
        self.assertTrue(ready["sms_fallback_sender_configured"])
        readiness_text = repr(ready)
        for value in values.values():
            self.assertNotIn(value, readiness_text)

    def test_from_env_and_config_repr_do_not_expose_credentials(self) -> None:
        values = {
            SOLAPI_API_KEY_ENV: "private-api-key",
            SOLAPI_API_SECRET_ENV: "private-api-secret",
            SOLAPI_KAKAO_CHANNEL_ID_ENV: "private-channel-id",
        }
        config = SolapiAlimtalkConfig.from_env(values)

        config_text = repr(config)
        for value in values.values():
            self.assertNotIn(value, config_text)

    def test_from_env_names_missing_configuration_safely(self) -> None:
        with self.assertRaises(SolapiConfigurationError) as raised:
            SolapiAlimtalkConfig.from_env(
                {SOLAPI_API_KEY_ENV: "private-api-key"}
            )

        self.assertEqual(raised.exception.code, "CONFIGURATION_MISSING")
        self.assertIn(SOLAPI_API_SECRET_ENV, str(raised.exception))
        self.assertIn(
            SOLAPI_KAKAO_CHANNEL_ID_ENV,
            str(raised.exception),
        )
        self.assertNotIn("private-api-key", str(raised.exception))

    def test_guidance_send_is_fail_closed_by_default(self) -> None:
        values = {
            SOLAPI_API_KEY_ENV: "private-api-key",
            SOLAPI_API_SECRET_ENV: "private-api-secret",
            SOLAPI_KAKAO_CHANNEL_ID_ENV: "private-channel-id",
            "SOLAPI_TEMPLATE_GUIDANCE_EMPLOYMENT_SUPPORT_ID": (
                "private-template-id"
            ),
        }

        readiness = guidance_send_readiness(
            values,
            required_template_env_names=(
                "SOLAPI_TEMPLATE_GUIDANCE_EMPLOYMENT_SUPPORT_ID",
            ),
        )

        self.assertTrue(readiness["ready"])
        self.assertFalse(readiness["send_enabled"])
        self.assertFalse(readiness["external_send_allowed"])
        self.assertNotIn("private", repr(readiness))

    def test_guidance_mock_mode_is_blocked_in_production(self) -> None:
        readiness = guidance_send_readiness(
            {
                SOLAPI_API_KEY_ENV: "key",
                SOLAPI_API_SECRET_ENV: "secret",
                SOLAPI_KAKAO_CHANNEL_ID_ENV: "channel",
                KAKAO_GUIDANCE_SEND_ENABLED_ENV: "true",
                KAKAO_GUIDANCE_MOCK_MODE_ENV: "true",
                "OASIS_ENVIRONMENT": "production",
            }
        )

        self.assertTrue(readiness["external_send_allowed"])
        self.assertFalse(readiness["mock_mode"])
        self.assertTrue(readiness["mock_mode_blocked_in_production"])


if __name__ == "__main__":
    unittest.main()
