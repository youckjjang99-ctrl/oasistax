from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import requests


SOLAPI_API_BASE_URL = "https://api.solapi.com"
SOLAPI_SEND_DETAIL_PATH = "/messages/v4/send-many/detail"
SOLAPI_TEMPLATE_LIST_PATH = "/kakao/v2/templates/"

SOLAPI_API_KEY_ENV = "SOLAPI_API_KEY"
SOLAPI_API_SECRET_ENV = "SOLAPI_API_SECRET"
SOLAPI_KAKAO_CHANNEL_ID_ENV = "SOLAPI_KAKAO_CHANNEL_ID"
SOLAPI_SMS_FROM_ENV = "SOLAPI_SMS_FROM"
KAKAO_GUIDANCE_SEND_ENABLED_ENV = (
    "OASIS_KAKAO_GUIDANCE_SEND_ENABLED"
)
KAKAO_GUIDANCE_MOCK_MODE_ENV = "OASIS_KAKAO_GUIDANCE_MOCK_MODE"
OASIS_RUNTIME_ENV_NAMES = (
    "OASIS_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_NAME",
    "ENVIRONMENT",
)

_PLACEHOLDER_PATTERN = re.compile(r"^#\{[^{}]+\}$")


class SolapiAlimtalkError(RuntimeError):
    """A deliberately redacted SOLAPI client error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class SolapiConfigurationError(SolapiAlimtalkError):
    """Raised when required SOLAPI configuration is missing or invalid."""


@dataclass(frozen=True)
class SolapiAlimtalkConfig:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    pf_id: str = field(repr=False)
    sms_from: str = field(default="", repr=False)
    timeout_seconds: float = 10.0
    base_url: str = SOLAPI_API_BASE_URL

    def __post_init__(self) -> None:
        values = dict(
            zip(
                ("api_key", "api_secret", "pf_id"),
                (self.api_key, self.api_secret, self.pf_id),
            )
        )
        missing = [name for name, value in values.items() if not str(value).strip()]
        if missing:
            raise SolapiConfigurationError(
                "CONFIGURATION_MISSING",
                "SOLAPI 알림톡 필수 설정이 누락되었습니다: "
                + ", ".join(missing),
            )
        if float(self.timeout_seconds) <= 0:
            raise SolapiConfigurationError(
                "CONFIGURATION_INVALID",
                "SOLAPI 요청 제한 시간은 0보다 커야 합니다.",
            )
        base_url = str(self.base_url).strip().rstrip("/")
        if not base_url.startswith("https://"):
            raise SolapiConfigurationError(
                "CONFIGURATION_INVALID",
                "SOLAPI API 주소는 HTTPS여야 합니다.",
            )
        object.__setattr__(self, "api_key", str(self.api_key).strip())
        object.__setattr__(self, "api_secret", str(self.api_secret).strip())
        object.__setattr__(self, "pf_id", str(self.pf_id).strip())
        object.__setattr__(self, "sms_from", str(self.sms_from).strip())
        object.__setattr__(self, "base_url", base_url)

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float = 10.0,
    ) -> "SolapiAlimtalkConfig":
        source = os.environ if environ is None else environ
        readiness = environment_readiness(source)
        if not readiness["ready"]:
            raise SolapiConfigurationError(
                "CONFIGURATION_MISSING",
                "SOLAPI 알림톡 환경변수가 누락되었습니다: "
                + ", ".join(readiness["missing_env_names"]),
            )
        config_values = dict(
            zip(
                ("api_key", "api_secret", "pf_id", "sms_from"),
                (
                    str(source.get(SOLAPI_API_KEY_ENV, "")),
                    str(source.get(SOLAPI_API_SECRET_ENV, "")),
                    str(source.get(SOLAPI_KAKAO_CHANNEL_ID_ENV, "")),
                    str(source.get(SOLAPI_SMS_FROM_ENV, "")),
                ),
            )
        )
        return cls(**config_values, timeout_seconds=timeout_seconds)


@dataclass(frozen=True)
class AlimtalkSendResult:
    group_id: str
    message_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "group_id": self.group_id,
            "message_id": self.message_id,
        }


def environment_readiness(
    environ: Mapping[str, str] | None = None,
    *,
    required_template_env_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Return readiness flags without returning any environment values."""

    source = os.environ if environ is None else environ
    template_env_names = tuple(
        dict.fromkeys(
            name.strip()
            for name in required_template_env_names
            if str(name).strip()
        )
    )
    required_env_names = (
        SOLAPI_API_KEY_ENV,
        SOLAPI_API_SECRET_ENV,
        SOLAPI_KAKAO_CHANNEL_ID_ENV,
        *template_env_names,
    )
    configured = {
        name: bool(str(source.get(name, "")).strip())
        for name in required_env_names
    }
    missing = [name for name in required_env_names if not configured[name]]
    return {
        "ready": not missing,
        "configured": not missing,
        "missing_env_names": missing,
        "required_env_names": list(required_env_names),
        "api_key_configured": configured[SOLAPI_API_KEY_ENV],
        "api_secret_configured": configured[SOLAPI_API_SECRET_ENV],
        "channel_configured": configured[SOLAPI_KAKAO_CHANNEL_ID_ENV],
        "template_ids_configured": all(
            configured[name] for name in template_env_names
        ),
        "sms_fallback_sender_configured": bool(
            str(source.get(SOLAPI_SMS_FROM_ENV, "")).strip()
        ),
    }


def _enabled_flag(value: Any) -> bool:
    """Accept only explicit affirmative values for external side effects."""

    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def guidance_send_readiness(
    environ: Mapping[str, str] | None = None,
    *,
    required_template_env_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a fail-closed readiness snapshot for DB-discovery guidance.

    This deliberately does not expose any configured value.  The existing
    claim-correction Alimtalk flow is not governed by this feature-specific
    switch, so rolling out DB-discovery guidance cannot disable it.
    """

    source = os.environ if environ is None else environ
    base = environment_readiness(
        source,
        required_template_env_names=required_template_env_names,
    )
    send_enabled = _enabled_flag(
        source.get(KAKAO_GUIDANCE_SEND_ENABLED_ENV, "")
    )
    runtime_name = next(
        (
            str(source.get(name, "") or "").strip().lower()
            for name in OASIS_RUNTIME_ENV_NAMES
            if str(source.get(name, "") or "").strip()
        ),
        "",
    )
    production = runtime_name in {"prod", "production"}
    requested_mock = _enabled_flag(
        source.get(KAKAO_GUIDANCE_MOCK_MODE_ENV, "")
    )
    mock_mode = requested_mock and not production
    return {
        **base,
        "send_enabled": send_enabled,
        "external_send_allowed": bool(base["ready"] and send_enabled),
        "mock_mode": mock_mode,
        "mock_mode_blocked_in_production": bool(
            requested_mock and production
        ),
        "runtime_is_production": production,
    }


def build_hmac_authorization(
    api_key: str,
    api_secret: str,
    *,
    date: str | None = None,
    salt: str | None = None,
) -> str:
    """Build SOLAPI's HMAC-SHA256 Authorization header value."""

    clean_api_key = str(api_key).strip()
    clean_api_secret = str(api_secret).strip()
    if not clean_api_key or not clean_api_secret:
        raise SolapiConfigurationError(
            "CONFIGURATION_MISSING",
            "SOLAPI API 인증정보가 누락되었습니다.",
        )

    request_date = date or (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    request_salt = salt or secrets.token_hex(16)
    salt_size = len(request_salt.encode("utf-8"))
    if not 12 <= salt_size <= 64:
        raise SolapiConfigurationError(
            "AUTHENTICATION_INVALID",
            "SOLAPI 인증 salt는 12~64바이트여야 합니다.",
        )

    signature = hmac.new(
        clean_api_secret.encode("utf-8"),
        f"{request_date}{request_salt}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        "HMAC-SHA256 "
        + "apiKey"
        + f"={clean_api_key}, "
        f"date={request_date}, "
        f"salt={request_salt}, "
        f"signature={signature}"
    )


def _normalize_phone(value: Any, *, field_label: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if not 8 <= len(digits) <= 15:
        raise SolapiAlimtalkError(
            "INVALID_REQUEST",
            f"{field_label} 형식이 올바르지 않습니다.",
        )
    return digits


def _normalize_variables(
    variables: Mapping[str, Any] | None,
) -> dict[str, str]:
    if variables is None:
        return {}
    if not isinstance(variables, Mapping):
        raise SolapiAlimtalkError(
            "INVALID_REQUEST",
            "알림톡 템플릿 변수는 객체 형식이어야 합니다.",
        )

    normalized: dict[str, str] = {}
    for key, value in variables.items():
        key_text = str(key)
        if not _PLACEHOLDER_PATTERN.fullmatch(key_text):
            raise SolapiAlimtalkError(
                "INVALID_REQUEST",
                "알림톡 템플릿 변수 키 형식이 올바르지 않습니다.",
            )
        if isinstance(value, (dict, list, tuple, set)):
            raise SolapiAlimtalkError(
                "INVALID_REQUEST",
                "알림톡 템플릿 변수 값은 문자열 또는 숫자여야 합니다.",
            )
        normalized[key_text] = "" if value is None else str(value)
    return normalized


def _response_identifiers(payload: Mapping[str, Any]) -> tuple[str, str]:
    group_info = payload.get("groupInfo")
    group_id = ""
    if isinstance(group_info, Mapping):
        group_id = str(group_info.get("groupId") or "").strip()
    if not group_id:
        group_id = str(payload.get("groupId") or "").strip()

    message_list = payload.get("messageList")
    message_id = ""
    if isinstance(message_list, list):
        for item in message_list:
            if not isinstance(item, Mapping):
                continue
            message_id = str(item.get("messageId") or "").strip()
            if message_id:
                break
    elif isinstance(message_list, Mapping):
        direct_id = str(message_list.get("messageId") or "").strip()
        if direct_id:
            message_id = direct_id
        else:
            for key, item in message_list.items():
                if not isinstance(item, Mapping):
                    continue
                message_id = str(item.get("messageId") or key or "").strip()
                if message_id:
                    break
    return group_id, message_id


class SolapiAlimtalkClient:
    def __init__(self, config: SolapiAlimtalkConfig) -> None:
        self.config = config

    def send_alimtalk(
        self,
        to: str,
        template_id: str,
        *,
        variables: Mapping[str, Any] | None = None,
        pf_id: str | None = None,
        disable_sms: bool = True,
        from_number: str | None = None,
    ) -> AlimtalkSendResult:
        clean_template_id = str(template_id or "").strip()
        clean_pf_id = str(pf_id or self.config.pf_id).strip()
        if not clean_template_id or not clean_pf_id:
            raise SolapiAlimtalkError(
                "INVALID_REQUEST",
                "알림톡 채널 또는 템플릿 설정이 누락되었습니다.",
            )
        if not isinstance(disable_sms, bool):
            raise SolapiAlimtalkError(
                "INVALID_REQUEST",
                "disable_sms 값은 참 또는 거짓이어야 합니다.",
            )

        message: dict[str, Any] = {
            "to": _normalize_phone(to, field_label="수신번호"),
            "type": "ATA",
            "kakaoOptions": {
                "pfId": clean_pf_id,
                "templateId": clean_template_id,
                "disableSms": disable_sms,
            },
        }
        normalized_variables = _normalize_variables(variables)
        if normalized_variables:
            message["kakaoOptions"]["variables"] = normalized_variables

        if not disable_sms:
            fallback_sender = from_number or self.config.sms_from
            if not str(fallback_sender or "").strip():
                raise SolapiAlimtalkError(
                    "INVALID_REQUEST",
                    "SMS 대체 발송에는 등록된 발신번호가 필요합니다.",
                )
            message["from"] = _normalize_phone(
                fallback_sender,
                field_label="발신번호",
            )

        headers = {
            "Authorization": build_hmac_authorization(
                self.config.api_key,
                self.config.api_secret,
            ),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        request_body = {
            "messages": [message],
            "showMessageList": True,
        }
        try:
            response = requests.post(
                self.config.base_url + SOLAPI_SEND_DETAIL_PATH,
                headers=headers,
                json=request_body,
                timeout=self.config.timeout_seconds,
            )
        except requests.Timeout:
            raise SolapiAlimtalkError(
                "TIMEOUT",
                "SOLAPI 알림톡 요청 시간이 초과되었습니다.",
            ) from None
        except requests.RequestException:
            raise SolapiAlimtalkError(
                "NETWORK_ERROR",
                "SOLAPI 알림톡 서버에 연결하지 못했습니다.",
            ) from None

        if not response.ok:
            http_status = int(response.status_code)
            raise SolapiAlimtalkError(
                "HTTP_ERROR",
                f"SOLAPI 알림톡 요청이 실패했습니다. HTTP {http_status}",
                http_status=http_status,
            )
        try:
            response_payload = response.json()
        except ValueError:
            raise SolapiAlimtalkError(
                "INVALID_RESPONSE",
                "SOLAPI 알림톡 응답을 확인할 수 없습니다.",
                http_status=int(response.status_code),
            ) from None
        if not isinstance(response_payload, Mapping):
            raise SolapiAlimtalkError(
                "INVALID_RESPONSE",
                "SOLAPI 알림톡 응답 형식이 올바르지 않습니다.",
                http_status=int(response.status_code),
            )
        if response_payload.get("failedMessageList"):
            raise SolapiAlimtalkError(
                "MESSAGE_REJECTED",
                "SOLAPI가 알림톡 발송 요청을 거부했습니다.",
                http_status=int(response.status_code),
            )

        group_id, message_id = _response_identifiers(response_payload)
        if not group_id or not message_id:
            raise SolapiAlimtalkError(
                "INVALID_RESPONSE",
                "SOLAPI 알림톡 발송 식별자를 확인할 수 없습니다.",
                http_status=int(response.status_code),
            )
        return AlimtalkSendResult(
            group_id=group_id,
            message_id=message_id,
        )

    def get_template_preview(self, template_id: str) -> dict[str, Any]:
        """Return an approved template's display-safe content without IDs."""

        clean_template_id = str(template_id or "").strip()
        if not clean_template_id:
            raise SolapiAlimtalkError(
                "INVALID_REQUEST",
                "알림톡 템플릿 설정이 누락되었습니다.",
            )
        headers = {
            "Authorization": build_hmac_authorization(
                self.config.api_key,
                self.config.api_secret,
            ),
            "Accept": "application/json",
        }
        try:
            response = requests.get(
                self.config.base_url + SOLAPI_TEMPLATE_LIST_PATH,
                headers=headers,
                params={
                    "templateId": clean_template_id,
                    "channelId": self.config.pf_id,
                    "limit": 1,
                },
                timeout=self.config.timeout_seconds,
            )
        except requests.Timeout:
            raise SolapiAlimtalkError(
                "TIMEOUT",
                "SOLAPI 템플릿 조회 시간이 초과되었습니다.",
            ) from None
        except requests.RequestException:
            raise SolapiAlimtalkError(
                "NETWORK_ERROR",
                "SOLAPI 템플릿을 조회하지 못했습니다.",
            ) from None
        if not response.ok:
            raise SolapiAlimtalkError(
                "HTTP_ERROR",
                "SOLAPI 템플릿 조회 요청이 실패했습니다.",
                http_status=int(response.status_code),
            )
        try:
            payload = response.json()
        except ValueError:
            raise SolapiAlimtalkError(
                "INVALID_RESPONSE",
                "SOLAPI 템플릿 응답을 확인할 수 없습니다.",
                http_status=int(response.status_code),
            ) from None
        if not isinstance(payload, Mapping):
            raise SolapiAlimtalkError(
                "INVALID_RESPONSE",
                "SOLAPI 템플릿 응답 형식이 올바르지 않습니다.",
                http_status=int(response.status_code),
            )
        rows = payload.get("templateList")
        if not isinstance(rows, list) or not rows:
            raise SolapiAlimtalkError(
                "TEMPLATE_NOT_FOUND",
                "등록된 알림톡 템플릿을 찾지 못했습니다.",
                http_status=int(response.status_code),
            )
        row = next(
            (
                item
                for item in rows
                if isinstance(item, Mapping)
                and str(item.get("templateId") or "").strip()
                == clean_template_id
            ),
            None,
        )
        if not isinstance(row, Mapping):
            raise SolapiAlimtalkError(
                "TEMPLATE_NOT_FOUND",
                "등록된 알림톡 템플릿을 찾지 못했습니다.",
                http_status=int(response.status_code),
            )
        content = str(row.get("content") or "").strip()
        if not content or len(content) > 4_000:
            raise SolapiAlimtalkError(
                "INVALID_RESPONSE",
                "알림톡 템플릿 본문을 확인할 수 없습니다.",
                http_status=int(response.status_code),
            )
        status = str(row.get("status") or "").strip().upper()
        if status not in {"APPROVED", "PENDING", "INSPECTING", "REJECTED"}:
            status = "UNKNOWN"
        buttons: list[dict[str, str]] = []
        raw_buttons = row.get("buttons")
        if isinstance(raw_buttons, list):
            for raw_button in raw_buttons[:5]:
                if not isinstance(raw_button, Mapping):
                    continue
                name = str(
                    raw_button.get("buttonName")
                    or raw_button.get("name")
                    or ""
                ).strip()[:100]
                mobile_url = str(
                    raw_button.get("linkMo")
                    or raw_button.get("linkMobile")
                    or ""
                ).strip()[:1_000]
                if name:
                    buttons.append(
                        {
                            "name": name,
                            "mobile_url": mobile_url,
                        }
                    )
        return {
            "content": content,
            "status": status,
            "buttons": buttons,
        }
