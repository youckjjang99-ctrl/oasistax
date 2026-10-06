from dataclasses import replace

import pytest

from voice_calling import VoiceSettings

SYNTHETIC_CALLER = "".join(("070", "0000", "0000"))
SYNTHETIC_MOBILE = "".join(("010", "0000", "0001"))
SYNTHETIC_SPACED_MOBILE = "+82 " + " ".join(("10", "0000", "0002"))


def environment():
    return {
        "OASIS_VOICE_PROVIDER": "clawops", "OASIS_VOICE_CALLS_ENABLED": "true",
        "CLAWOPS_ACCOUNT_ID": "ACfixture_12345678", "CLAWOPS_API_KEY": "synthetic-key-no-network",
        "CLAWOPS_WEBHOOK_SIGNING_SECRET": "s" * 40,
        "OASIS_VOICE_STREAM_TICKET_SECRET": "t" * 40,
        "OASIS_VOICE_CALLER_ID": SYNTHETIC_CALLER,
        "OASIS_VOICE_PUBLIC_BASE_URL": "https://voice.example.test",
        "OPENAI_API_KEY": "synthetic-ai-key-no-network",
        "OASIS_VOICE_CLAWOPS_BILLING_CONFIRMED": "true",
        "OASIS_VOICE_TEST_NUMBERS": SYNTHETIC_MOBILE + ", " + SYNTHETIC_SPACED_MOBILE,
    }


def test_default_environment_never_enables_clawops_or_billing():
    settings = VoiceSettings.from_environment({"OASIS_VOICE_PROVIDER": "clawops"})
    assert not settings.enabled and not settings.clawops_billing_confirmed
    assert not settings.clawops_live_verified and not settings.readiness()["ready"]


def test_environment_parsing_internal_mode_and_secret_free_readiness():
    settings = VoiceSettings.from_environment(environment())
    result = settings.readiness()
    assert result["ready"] and result["test_only"] and result["provider"] == "clawops"
    for value in (settings.clawops_api_key, settings.clawops_signing_secret, settings.openai_api_key,
                  settings.stream_ticket_secret, settings.caller_id, *settings.clawops_test_numbers):
        assert value not in str(result)


@pytest.mark.parametrize("key,value", [
    ("OASIS_VOICE_CALLS_ENABLED", "false"), ("OASIS_VOICE_CLAWOPS_BILLING_CONFIRMED", "false"),
    ("CLAWOPS_WEBHOOK_SIGNING_SECRET", "short"), ("CLAWOPS_API_KEY", ""),
    ("CLAWOPS_ACCOUNT_ID", "bad"), ("OASIS_VOICE_TEST_NUMBERS", ""),
    ("OASIS_VOICE_TEST_NUMBERS", "invalid"),
    ("OASIS_VOICE_TEST_NUMBERS", ",".join([SYNTHETIC_MOBILE] * 6)),
    ("OASIS_VOICE_CALLER_ID", SYNTHETIC_MOBILE), ("OASIS_VOICE_PUBLIC_BASE_URL", "http://example.test"),
    ("OASIS_VOICE_STREAM_TICKET_SECRET", ""), ("OPENAI_API_KEY", ""),
])
def test_missing_prerequisite_is_not_ready(key, value):
    assert not VoiceSettings.from_environment({**environment(), key: value}).readiness()["ready"]


def test_live_verification_does_not_override_billing_or_kill_switch():
    settings = VoiceSettings.from_environment({**environment(), "OASIS_VOICE_CLAWOPS_LIVE_VERIFIED": "true", "OASIS_VOICE_TEST_NUMBERS": ""})
    assert settings.readiness()["ready"] and not settings.readiness()["test_only"]
    assert not replace(settings, enabled=False).readiness()["ready"]
    assert not replace(settings, clawops_billing_confirmed=False).readiness()["ready"]


def test_clawops_preparation_message_does_not_render_numbers_or_keys():
    from voice_calling_ui import _render_preparation

    class Screen:
        def __init__(self):
            self.messages = []
        def write(self, text):
            self.messages.append(text)
        markdown = caption = info = write

    screen = Screen()
    settings = VoiceSettings.from_environment(environment())
    _render_preparation(screen, settings.readiness())
    assert any("ClawOps 070" in x for x in screen.messages)
    assert any("시험번호 외에는 발신할 수 없습니다" in x for x in screen.messages)
    assert settings.clawops_api_key not in str(screen.messages)
    assert settings.caller_id not in str(screen.messages)
