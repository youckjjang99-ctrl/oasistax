"""Secret-free voice deployment diagnostics; no calling or database writes.

The default mode is entirely offline. ``--check-network`` only retrieves the
configured model's metadata from OpenAI. It never opens a Realtime session,
creates a call, accepts billing, or changes an environment flag. Model metadata
visibility is not proof of Realtime access, available credit, or call quality.

Reference: https://developers.openai.com/api/reference/resources/models/methods/retrieve
"""
from __future__ import annotations

import argparse
import json
import re
from typing import Any
from urllib.parse import quote

from voice_calling import VoiceSettings


_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_CHECK_IDS = {
    "실제 발신 활성화": "dispatch_enabled",
    "전화 서비스 연결": "provider_credentials_configured",
    "등록된 발신번호 설정": "caller_configured",
    "HTTPS 음성 서버 주소": "gateway_url_configured",
    "음성 AI API 연결 설정": "openai_settings_configured",
    "전용 보안키 설정": "stream_secret_configured",
    "ClawOps 별도 웹훅 서명키": "provider_signing_secret_configured",
    "외부 음성 연결 과금 조건 확인": "billing_operator_confirmed",
    "내부 시험번호 또는 실통화 검증 승인": "test_scope_configured",
}


def _key_configured(key: Any) -> bool:
    return (isinstance(key, str) and 1 <= len(key) <= 4096
            and all(33 <= ord(c) <= 126 for c in key))


def _model_probe(settings: VoiceSettings, client: Any = None) -> dict[str, Any]:
    """GET only, fixed host, bounded timeout, no redirects or response logging."""
    key = settings.openai_api_key
    if not _key_configured(key):
        return {"ok": False, "code": "OPENAI_KEY_NOT_CONFIGURED"}
    if not isinstance(settings.realtime_model, str) or not _MODEL_NAME.fullmatch(settings.realtime_model):
        return {"ok": False, "code": "INVALID_MODEL_CONFIGURATION"}
    try:
        import httpx

        request = {
            "headers": {"Authorization": "Bearer " + key},
            "timeout": httpx.Timeout(10.0, connect=5.0),
            "follow_redirects": False,
        }
        url = "https://api.openai.com/v1/models/" + quote(settings.realtime_model, safe="")
        if client is None:
            # Do not inherit ambient proxy configuration or authentication.
            with httpx.Client(trust_env=False) as session:
                response = session.get(url, **request)
        else:
            response = client.get(url, **request)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, dict) and data.get("object") == "model" and data.get("id") == settings.realtime_model:
                return {"ok": True, "code": "MODEL_VISIBLE_REALTIME_NOT_VERIFIED"}
            return {"ok": False, "code": "OPENAI_UNEXPECTED_RESPONSE"}
        code = {
            401: "OPENAI_AUTHENTICATION_FAILED",
            403: "OPENAI_MODEL_PERMISSION_DENIED",
            404: "OPENAI_MODEL_UNAVAILABLE",
            429: "OPENAI_RATE_OR_QUOTA_LIMIT",
        }.get(response.status_code, "OPENAI_REQUEST_FAILED")
        return {"ok": False, "code": code}
    except Exception:
        # Exceptions may contain authorization headers, a response or URL. Never
        # print their text and never retry this diagnostic automatically.
        return {"ok": False, "code": "OPENAI_CHECK_UNAVAILABLE"}


def build_preflight(config: VoiceSettings | None = None, *, check_network: bool = False,
                    client: Any = None) -> dict[str, Any]:
    """Return only fixed labels/codes and booleans, never configuration values."""
    settings = config if config is not None else VoiceSettings.from_environment()
    readiness = settings.readiness()
    checks = [
        {"id": _CHECK_IDS.get(item.get("label"), "additional_requirement"), "ok": bool(item.get("ok"))}
        for item in readiness.get("checks", [])
    ]
    checks.extend([
        {"id": "openai_key_format", "ok": _key_configured(settings.openai_api_key)},
        {"id": "openai_model_format", "ok": bool(isinstance(settings.realtime_model, str) and _MODEL_NAME.fullmatch(settings.realtime_model))},
    ])
    configuration_complete = bool(checks) and all(c["ok"] for c in checks if c["id"] != "dispatch_enabled")
    model_probe = (_model_probe(settings, client) if check_network else
                   {"ok": None, "code": "NETWORK_CHECK_NOT_REQUESTED"})
    return {
        "mode": "read_only_network" if check_network else "configuration_only",
        "configuration_complete": configuration_complete,
        "dispatch_enabled": bool(settings.enabled),
        "dispatch_configuration_ready": bool(readiness.get("ready")),
        "provider": settings.provider if settings.provider in {"twilio", "clawops"} else "unsupported",
        "checks": checks,
        "openai_model_metadata": model_probe,
        "realtime_session": {"ok": None, "code": "REALTIME_SESSION_NOT_TESTED"},
        "provider_account": {"ok": None, "code": "PROVIDER_ACCOUNT_NOT_TESTED"},
        "gateway_reachability": {"ok": None, "code": "GATEWAY_NOT_TESTED"},
        "queue_schema": {"ok": None, "code": "DATABASE_NOT_TESTED"},
        "live_test_operator_confirmed": bool(settings.clawops_live_verified) if settings.provider == "clawops" else False,
        "end_to_end_verified": False,
        "actual_calls_started": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Voice configuration diagnostics; never starts calls")
    parser.add_argument("--check-network", action="store_true", help="Read-only OpenAI model metadata check; does not test paid Realtime")
    args = parser.parse_args(argv)
    report = build_preflight(check_network=args.check_network)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    verified = not args.check_network or report["openai_model_metadata"]["ok"] is True
    return 0 if report["configuration_complete"] and verified else 1


if __name__ == "__main__":
    raise SystemExit(main())
