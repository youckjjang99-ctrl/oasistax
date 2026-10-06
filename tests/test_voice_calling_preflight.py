import json
from dataclasses import replace

import httpx
import pytest

from voice_calling import VoiceSettings
from voice_calling_preflight import build_preflight, main


def settings(**overrides):
    values = dict(
        enabled=False, provider="clawops", clawops_account_id="ACfixture_preflight",
        clawops_api_key="synthetic-carrier-key", clawops_signing_secret="s" * 40,
        caller_id="".join(("070", "0000", "0000")),
        public_base_url="https://voice.example.test", openai_api_key="synthetic-openai-key",
        stream_ticket_secret="t" * 40, clawops_billing_confirmed=True,
        clawops_test_numbers=("".join(("010", "0000", "0001")),),
    )
    return VoiceSettings(**{**values, **overrides})


class NoNetwork:
    def get(self, *args, **kwargs):
        pytest.fail("Offline preflight must not access the network")


def test_default_offline_configuration_does_not_claim_end_to_end_success():
    result = build_preflight(settings(), client=NoNetwork())
    assert result["configuration_complete"]
    assert not result["dispatch_enabled"] and not result["dispatch_configuration_ready"]
    assert result["openai_model_metadata"]["ok"] is None
    assert not result["end_to_end_verified"] and not result["actual_calls_started"]
    for field in ("provider_account", "realtime_session", "gateway_reachability", "queue_schema"):
        assert result[field]["ok"] is None


def test_all_reports_exclude_secret_values_phone_numbers_hostnames_and_account_ids():
    config = settings()
    result = json.dumps(build_preflight(config))
    for value in (config.openai_api_key, config.clawops_api_key, config.clawops_account_id,
                  config.clawops_signing_secret, config.stream_ticket_secret,
                  config.caller_id, config.public_base_url, *config.clawops_test_numbers):
        assert value not in result


def test_network_check_only_uses_get_metadata_fixed_origin_and_no_redirects():
    seen = []
    config = settings(enabled=True)
    def handle(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url == "https://api.openai.com/v1/models/gpt-realtime-2.1"
        assert request.headers["Authorization"] == "Bearer " + config.openai_api_key
        assert not request.content
        return httpx.Response(200, json={"id": config.realtime_model, "object": "model"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = build_preflight(config, check_network=True, client=client)
    assert len(seen) == 1
    assert result["openai_model_metadata"] == {"ok": True, "code": "MODEL_VISIBLE_REALTIME_NOT_VERIFIED"}
    assert not result["end_to_end_verified"]
    assert result["realtime_session"]["ok"] is None


@pytest.mark.parametrize("status,code", [
    (401, "OPENAI_AUTHENTICATION_FAILED"), (403, "OPENAI_MODEL_PERMISSION_DENIED"),
    (404, "OPENAI_MODEL_UNAVAILABLE"), (429, "OPENAI_RATE_OR_QUOTA_LIMIT"),
    (503, "OPENAI_REQUEST_FAILED"), (302, "OPENAI_REQUEST_FAILED"),
])
def test_http_errors_sanitized_without_retry_or_redirect(status, code):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(status, headers={"Location": "https://untrusted.example.test"},
                              json={"message": "SECRET_RESPONSE_SHOULD_NOT_APPEAR"})
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        result = build_preflight(settings(), check_network=True, client=client)
    assert len(seen) == 1
    assert result["openai_model_metadata"] == {"ok": False, "code": code}
    assert "SECRET_RESPONSE" not in str(result)


@pytest.mark.parametrize("body", [[], {"id": "different", "object": "model"}, {"id": "gpt-realtime-2.1"}])
def test_model_success_requires_exact_id_and_model_object(body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as client:
        result = build_preflight(settings(), check_network=True, client=client)
    assert result["openai_model_metadata"]["code"] == "OPENAI_UNEXPECTED_RESPONSE"


def test_network_exception_is_safe_and_never_retried():
    class Client:
        calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            raise RuntimeError("secret key and customer information")
    client = Client()
    result = build_preflight(settings(), check_network=True, client=client)
    assert client.calls == 1
    assert result["openai_model_metadata"]["code"] == "OPENAI_CHECK_UNAVAILABLE"
    assert "secret key" not in str(result)


@pytest.mark.parametrize("field,value,code", [
    ("openai_api_key", "", "OPENAI_KEY_NOT_CONFIGURED"),
    ("openai_api_key", "invalid\nheader", "OPENAI_KEY_NOT_CONFIGURED"),
    ("realtime_model", "../../customers?key=unsafe", "INVALID_MODEL_CONFIGURATION"),
    ("realtime_model", "", "INVALID_MODEL_CONFIGURATION"),
])
def test_bad_config_cannot_send_requests(field, value, code):
    result = build_preflight(settings(**{field: value}), check_network=True, client=NoNetwork())
    assert result["openai_model_metadata"]["code"] == code
    assert not result["configuration_complete"]


def test_live_verified_flag_does_not_claim_probe_or_end_to_end_success():
    result = build_preflight(settings(clawops_live_verified=True, clawops_test_numbers=()))
    assert result["live_test_operator_confirmed"]
    assert not result["end_to_end_verified"]


def test_unsupported_provider_is_not_echoed():
    result = build_preflight(settings(provider="SENSITIVE_INVALID_VALUE"))
    assert result["provider"] == "unsupported"
    assert "SENSITIVE_INVALID_VALUE" not in str(result)


def test_additional_security_checks_fail_closed_without_echoing_unknown_labels(monkeypatch):
    original = VoiceSettings.readiness
    def extra_check(config):
        result = original(config)
        result["checks"].append({"label": "sensitive-new-check-value", "ok": False})
        return result
    monkeypatch.setattr(VoiceSettings, "readiness", extra_check)
    result = build_preflight(settings())
    assert not result["configuration_complete"]
    assert "sensitive-new-check-value" not in str(result)


def test_cli_is_configuration_only_by_default(monkeypatch, capsys):
    monkeypatch.setattr(VoiceSettings, "from_environment", lambda: settings())
    monkeypatch.setattr(httpx.Client, "get", NoNetwork().get)
    assert main([]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["mode"] == "configuration_only" and not result["actual_calls_started"]


def test_cli_returns_failure_when_config_incomplete(monkeypatch, capsys):
    monkeypatch.setattr(VoiceSettings, "from_environment", lambda: replace(settings(), openai_api_key=""))
    assert main([]) == 1
    assert not json.loads(capsys.readouterr().out)["configuration_complete"]
