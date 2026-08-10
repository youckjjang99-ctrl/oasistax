"""Fail-closed server-side adapters for salesperson-authored outreach.

The public functions in this module deliberately return only redacted status
objects.  Recipient addresses, message bodies, credentials, and provider
responses must never be logged or surfaced to the UI.

External sends are disabled unless ``OUTREACH_ENABLED`` is explicitly true.
There is intentionally no mock mode in production code; tests inject a fake
``requests.Session`` instead.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from solapi_alimtalk_client import (
    SolapiAlimtalkClient,
    SolapiAlimtalkConfig,
    SolapiAlimtalkError,
    environment_readiness as solapi_environment_readiness,
)


CHANNEL_EMAIL = "email"
CHANNEL_SMS = "sms"
CHANNEL_KAKAO = "kakao"

OUTREACH_ENABLED_ENV = "OUTREACH_ENABLED"
OUTREACH_COMPLIANCE_CONFIRMED_ENV = "OUTREACH_COMPLIANCE_CONFIRMED"
OUTREACH_TIMEOUT_SECONDS_ENV = "OUTREACH_HTTP_TIMEOUT_SECONDS"
OUTREACH_SMS_FREE_OPT_OUT_NUMBER_ENV = "OUTREACH_SMS_FREE_OPT_OUT_NUMBER"
OUTREACH_EMAIL_OPT_OUT_TEXT_ENV = "OUTREACH_EMAIL_OPT_OUT_TEXT"
OUTREACH_SENDER_NAME_ENV = "OUTREACH_SENDER_NAME"
OUTREACH_SENDER_EMAIL_ENV = "OUTREACH_SENDER_EMAIL"
OUTREACH_SENDER_PHONE_ENV = "OUTREACH_SENDER_PHONE"
OUTREACH_SENDER_ADDRESS_ENV = "OUTREACH_SENDER_ADDRESS"
LEGACY_CONTACT_PHONE_HASH_KEY_ENV = "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY"
SOLAPI_CLAIM_AUTH_TEMPLATE_ENV = "SOLAPI_TEMPLATE_AUTH_START_ID"
SOLAPI_CLAIM_AUTH_TEMPLATE_LABEL = "경정청구 자료수집 인증안내"
SOLAPI_ALIMTALK_DEFAULT_TEMPLATE_CODE = "auth_start"
SOLAPI_ALIMTALK_TEMPLATE_SPECS = {
    SOLAPI_ALIMTALK_DEFAULT_TEMPLATE_CODE: {
        "label": SOLAPI_CLAIM_AUTH_TEMPLATE_LABEL,
        "env_name": SOLAPI_CLAIM_AUTH_TEMPLATE_ENV,
    },
    "auth_resume": {
        "label": "경정청구 자료수집 재안내",
        "env_name": "SOLAPI_TEMPLATE_AUTH_RESUME_ID",
    },
    "next_auth": {
        "label": "경정청구 추가 인증 안내",
        "env_name": "SOLAPI_TEMPLATE_NEXT_AUTH_ID",
    },
    "complete": {
        "label": "경정청구 자료수집 완료 안내",
        "env_name": "SOLAPI_TEMPLATE_COMPLETE_ID",
    },
    "failed": {
        "label": "경정청구 자료수집 확인 필요 안내",
        "env_name": "SOLAPI_TEMPLATE_FAILED_ID",
    },
}

SMSKOREA_USER_ID_ENV = "SMSKOREA_USER_ID"
SMSKOREA_SEC_API_KEY_ENV = "SMSKOREA_SEC_API_KEY"
SMSKOREA_SENDER_ENV = "SMSKOREA_SENDER"
SMSKOREA_MESSAGE_SECRET_MODE_ENV = "SMSKOREA_MESSAGE_SECRET_MODE"
SMSKOREA_SECRET_MODE_INCLUDE = "include"
SMSKOREA_SECRET_MODE_OMIT = "omit"

HIWORKS_OFFICE_TOKEN_ENV = "HIWORKS_OFFICE_TOKEN"
HIWORKS_USER_ID_ENV = "HIWORKS_USER_ID"

KAKAO_BIZ_CLIENT_ID_ENV = "KAKAO_BIZ_CLIENT_ID"
KAKAO_BIZ_CLIENT_SECRET_ENV = "KAKAO_BIZ_CLIENT_SECRET"
KAKAO_BIZ_SENDER_KEY_ENV = "KAKAO_BIZ_SENDER_KEY"
KAKAO_BIZ_SENDER_NO_ENV = "KAKAO_BIZ_SENDER_NO"
KAKAO_BIZ_TEMPLATE_CODE_ENV = "KAKAO_BIZ_TEMPLATE_CODE"
KAKAO_BIZ_CONTRACT_CONFIRMED_ENV = "KAKAO_BIZ_CONTRACT_CONFIRMED"

SMSKOREA_TOKEN_URL = "https://api.smsko.co.kr/api/v1/token"
SMSKOREA_MESSAGE_URL = "https://api.smsko.co.kr/api/v1/message"
HIWORKS_SEND_MAIL_URL = (
    "https://api.hiworks.com/office/v2/webmail/sendMail"
)
KAKAO_BIZ_BASE_URL = "https://bizmsg-web.kakaoenterprise.com"
KAKAO_BIZ_TOKEN_URL = f"{KAKAO_BIZ_BASE_URL}/v2/oauth/token"
KAKAO_BIZ_SEND_URL = f"{KAKAO_BIZ_BASE_URL}/v2/send/kakao"

_DEFAULT_TIMEOUT_SECONDS = 10.0
_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)
_PHONE_ALLOWED_PATTERN = re.compile(r"^[+0-9() .-]+$")
_SAFE_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_AUTH_LINK_HOST_LABEL_PATTERN = re.compile(
    r"^(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)$"
)

_CHANNEL_ENV_NAMES = {
    CHANNEL_EMAIL: (
        HIWORKS_OFFICE_TOKEN_ENV,
        HIWORKS_USER_ID_ENV,
        OUTREACH_EMAIL_OPT_OUT_TEXT_ENV,
        OUTREACH_SENDER_NAME_ENV,
        OUTREACH_SENDER_EMAIL_ENV,
        OUTREACH_SENDER_PHONE_ENV,
        OUTREACH_SENDER_ADDRESS_ENV,
        LEGACY_CONTACT_PHONE_HASH_KEY_ENV,
    ),
    CHANNEL_SMS: (
        SMSKOREA_USER_ID_ENV,
        SMSKOREA_SEC_API_KEY_ENV,
        SMSKOREA_SENDER_ENV,
        SMSKOREA_MESSAGE_SECRET_MODE_ENV,
        OUTREACH_SMS_FREE_OPT_OUT_NUMBER_ENV,
        OUTREACH_SENDER_NAME_ENV,
        LEGACY_CONTACT_PHONE_HASH_KEY_ENV,
    ),
    CHANNEL_KAKAO: (
        KAKAO_BIZ_CLIENT_ID_ENV,
        KAKAO_BIZ_CLIENT_SECRET_ENV,
        KAKAO_BIZ_SENDER_KEY_ENV,
        KAKAO_BIZ_SENDER_NO_ENV,
        KAKAO_BIZ_TEMPLATE_CODE_ENV,
        KAKAO_BIZ_CONTRACT_CONFIRMED_ENV,
        LEGACY_CONTACT_PHONE_HASH_KEY_ENV,
    ),
}

_PROVIDER_NAMES = {
    CHANNEL_EMAIL: "hiworks",
    CHANNEL_SMS: "smskorea",
    CHANNEL_KAKAO: "kakao_i_connect",
}


def _status(
    ok: bool,
    code: str,
    message: str,
    provider_id: str = "",
) -> dict[str, Any]:
    """Build the only status shape permitted to cross the UI boundary."""

    # Provider identifiers are not required by the UI and can contain a
    # recipient address or other provider-controlled PII. Never return them.
    del provider_id
    return {
        "ok": bool(ok),
        "code": str(code),
        "message": str(message),
        "provider_id": "",
    }


def _enabled(value: Any) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _timeout_seconds(source: Mapping[str, str]) -> float:
    raw = str(source.get(OUTREACH_TIMEOUT_SECONDS_ENV, "") or "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SECONDS
    if not 0.5 <= value <= 30.0:
        return _DEFAULT_TIMEOUT_SECONDS
    return value


def channel_readiness(channel: str) -> dict[str, Any]:
    """Return readiness using environment *names* only, never values.

    Kakao i Connect Message requires a separate provider contract.  The
    contract-confirmation flag is therefore required in addition to the
    credentials.  문자코리아's current official PHP/GAS and Python/Java
    examples disagree about sending ``sec_apiKey`` in the message request.
    ``SMSKOREA_MESSAGE_SECRET_MODE`` must be explicitly set to ``include`` or
    ``omit`` according to the approved account manual; an unset/invalid value
    keeps the adapter disabled.
    """

    clean_channel = str(channel or "").strip().lower()
    if clean_channel not in _CHANNEL_ENV_NAMES:
        return {
            "channel": clean_channel,
            "provider": "",
            "ready": False,
            "send_enabled": False,
            "external_send_allowed": False,
            "required_env_names": [],
            "missing_env_names": [],
            "code": "UNSUPPORTED_CHANNEL",
            "message": "지원하지 않는 발송 방식입니다.",
        }

    source = os.environ
    channel_env_names = _CHANNEL_ENV_NAMES[clean_channel]
    missing = [
        name
        for name in channel_env_names
        if not str(source.get(name, "") or "").strip()
    ]

    code = "READY"
    message = "발송 설정이 준비되었습니다."

    if len(
        str(source.get(LEGACY_CONTACT_PHONE_HASH_KEY_ENV, "") or "").strip()
    ) < 32:
        if LEGACY_CONTACT_PHONE_HASH_KEY_ENV not in missing:
            missing.append(LEGACY_CONTACT_PHONE_HASH_KEY_ENV)
        code = "OUTREACH_HMAC_KEY_REQUIRED"
        message = "발송 중복방지 보안키 설정이 필요합니다."

    if clean_channel == CHANNEL_SMS:
        secret_mode = str(
            source.get(SMSKOREA_MESSAGE_SECRET_MODE_ENV, "") or ""
        ).strip().lower()
        if secret_mode not in {
            SMSKOREA_SECRET_MODE_INCLUDE,
            SMSKOREA_SECRET_MODE_OMIT,
        }:
            if SMSKOREA_MESSAGE_SECRET_MODE_ENV not in missing:
                missing.append(SMSKOREA_MESSAGE_SECRET_MODE_ENV)
            code = "SMSKOREA_SCHEMA_CONFIRMATION_REQUIRED"
            message = (
                "문자코리아 승인 문서에 맞는 요청 형식 확인이 필요합니다."
            )

    if clean_channel == CHANNEL_KAKAO and not _enabled(
        source.get(KAKAO_BIZ_CONTRACT_CONFIRMED_ENV, "")
    ):
        if KAKAO_BIZ_CONTRACT_CONFIRMED_ENV not in missing:
            missing.append(KAKAO_BIZ_CONTRACT_CONFIRMED_ENV)
        code = "KAKAO_CONTRACT_REQUIRED"
        message = "카카오 i Connect Message 계약 확인이 필요합니다."

    send_enabled = _enabled(source.get(OUTREACH_ENABLED_ENV, ""))
    if not send_enabled:
        if OUTREACH_ENABLED_ENV not in missing:
            missing.append(OUTREACH_ENABLED_ENV)
        if code == "READY":
            code = "OUTREACH_DISABLED"
            message = "외부 발송 기능이 비활성화되어 있습니다."

    compliance_confirmed = _enabled(
        source.get(OUTREACH_COMPLIANCE_CONFIRMED_ENV, "")
    )
    if not compliance_confirmed:
        if OUTREACH_COMPLIANCE_CONFIRMED_ENV not in missing:
            missing.append(OUTREACH_COMPLIANCE_CONFIRMED_ENV)
        if code == "READY":
            code = "COMPLIANCE_CONFIRMATION_REQUIRED"
            message = "광고 수신동의·무료 수신거부 운영 준비 확인이 필요합니다."

    if clean_channel == CHANNEL_SMS:
        opt_out_digits = re.sub(
            r"\D",
            "",
            str(source.get(OUTREACH_SMS_FREE_OPT_OUT_NUMBER_ENV, "") or ""),
        )
        if not re.fullmatch(r"080\d{7}", opt_out_digits):
            if OUTREACH_SMS_FREE_OPT_OUT_NUMBER_ENV not in missing:
                missing.append(OUTREACH_SMS_FREE_OPT_OUT_NUMBER_ENV)
            code = "FREE_OPT_OUT_CONFIGURATION_REQUIRED"
            message = "등록된 무료 수신거부 번호 설정이 필요합니다."

    if clean_channel == CHANNEL_EMAIL:
        invalid_sender_fields: list[str] = []
        if not _validate_email(
            str(source.get(OUTREACH_SENDER_EMAIL_ENV, "") or "")
        ):
            invalid_sender_fields.append(OUTREACH_SENDER_EMAIL_ENV)
        sender_phone_digits = re.sub(
            r"\D",
            "",
            str(source.get(OUTREACH_SENDER_PHONE_ENV, "") or ""),
        )
        if not 8 <= len(sender_phone_digits) <= 15:
            invalid_sender_fields.append(OUTREACH_SENDER_PHONE_ENV)
        sender_address = str(
            source.get(OUTREACH_SENDER_ADDRESS_ENV, "") or ""
        ).strip()
        if len(sender_address) < 5:
            invalid_sender_fields.append(OUTREACH_SENDER_ADDRESS_ENV)
        if invalid_sender_fields:
            for name in invalid_sender_fields:
                if name not in missing:
                    missing.append(name)
            code = "SENDER_CONFIGURATION_REQUIRED"
            message = "이메일 전송자 표시 정보를 확인해 주세요."

    if missing and code == "READY":
        code = "CONFIGURATION_MISSING"
        message = "발송 서비스 환경 설정을 확인해 주세요."

    required = [
        OUTREACH_ENABLED_ENV,
        OUTREACH_COMPLIANCE_CONFIRMED_ENV,
        *channel_env_names,
    ]
    ready = not missing
    return {
        "channel": clean_channel,
        "provider": _PROVIDER_NAMES[clean_channel],
        "ready": ready,
        "send_enabled": send_enabled,
        "external_send_allowed": ready,
        "required_env_names": list(dict.fromkeys(required)),
        "missing_env_names": list(dict.fromkeys(missing)),
        "code": code if not ready else "READY",
        "message": message if not ready else "발송 설정이 준비되었습니다.",
    }


def claim_auth_alimtalk_templates() -> tuple[dict[str, str], ...]:
    """Return the approved template allowlist without provider identifiers."""

    return tuple(
        {
            "code": code,
            "label": str(spec["label"]),
            "env_name": str(spec["env_name"]),
        }
        for code, spec in SOLAPI_ALIMTALK_TEMPLATE_SPECS.items()
    )


def _claim_auth_template_spec(template_code: Any) -> dict[str, str] | None:
    clean_code = str(template_code or "").strip().lower()
    spec = SOLAPI_ALIMTALK_TEMPLATE_SPECS.get(clean_code)
    if not isinstance(spec, Mapping):
        return None
    return {
        "code": clean_code,
        "label": str(spec.get("label") or "").strip(),
        "env_name": str(spec.get("env_name") or "").strip(),
    }


def claim_auth_alimtalk_readiness(
    template_code: Any = SOLAPI_ALIMTALK_DEFAULT_TEMPLATE_CODE,
) -> dict[str, Any]:
    """Return redacted readiness for one allowlisted Solapi template."""

    source = os.environ
    template = _claim_auth_template_spec(template_code)
    if template is None:
        return {
            "channel": CHANNEL_KAKAO,
            "provider": "solapi",
            "ready": False,
            "send_enabled": _enabled(source.get(OUTREACH_ENABLED_ENV, "")),
            "external_send_allowed": False,
            "required_env_names": [],
            "missing_env_names": [],
            "code": "TEMPLATE_NOT_ALLOWED",
            "message": "선택할 수 없는 알림톡 템플릿입니다.",
        }
    provider = solapi_environment_readiness(
        source,
        required_template_env_names=(template["env_name"],),
    )
    missing = list(provider.get("missing_env_names") or [])
    code = "READY"
    message = "Solapi 알림톡 발송 설정이 준비되었습니다."

    if len(
        str(source.get(LEGACY_CONTACT_PHONE_HASH_KEY_ENV, "") or "").strip()
    ) < 32:
        if LEGACY_CONTACT_PHONE_HASH_KEY_ENV not in missing:
            missing.append(LEGACY_CONTACT_PHONE_HASH_KEY_ENV)
        code = "OUTREACH_HMAC_KEY_REQUIRED"
        message = "발송 중복방지 보안키 설정이 필요합니다."

    send_enabled = _enabled(source.get(OUTREACH_ENABLED_ENV, ""))
    if not send_enabled:
        if OUTREACH_ENABLED_ENV not in missing:
            missing.append(OUTREACH_ENABLED_ENV)
        if code == "READY":
            code = "OUTREACH_DISABLED"
            message = "외부 발송 기능이 비활성화되어 있습니다."

    compliance_confirmed = _enabled(
        source.get(OUTREACH_COMPLIANCE_CONFIRMED_ENV, "")
    )
    if not compliance_confirmed:
        if OUTREACH_COMPLIANCE_CONFIRMED_ENV not in missing:
            missing.append(OUTREACH_COMPLIANCE_CONFIRMED_ENV)
        if code == "READY":
            code = "COMPLIANCE_CONFIRMATION_REQUIRED"
            message = "수신동의·수신거부 운영 준비 확인이 필요합니다."

    if missing and code == "READY":
        code = "CONFIGURATION_MISSING"
        message = "Solapi 알림톡 환경 설정을 확인해 주세요."

    ready = not missing
    required = [
        OUTREACH_ENABLED_ENV,
        OUTREACH_COMPLIANCE_CONFIRMED_ENV,
        LEGACY_CONTACT_PHONE_HASH_KEY_ENV,
        *list(provider.get("required_env_names") or []),
    ]
    return {
        "channel": CHANNEL_KAKAO,
        "provider": "solapi",
        "ready": ready,
        "send_enabled": send_enabled,
        "external_send_allowed": ready,
        "required_env_names": list(dict.fromkeys(required)),
        "missing_env_names": list(dict.fromkeys(missing)),
        "code": code if not ready else "READY",
        "message": message if not ready else "Solapi 알림톡 발송 설정이 준비되었습니다.",
    }


def claim_auth_alimtalk_template_preview(
    template_code: Any = SOLAPI_ALIMTALK_DEFAULT_TEMPLATE_CODE,
    *,
    client: SolapiAlimtalkClient | None = None,
) -> dict[str, Any]:
    """Load display-safe content for one approved Solapi template."""

    template = _claim_auth_template_spec(template_code)
    if template is None:
        return {
            "ok": False,
            "code": "TEMPLATE_NOT_ALLOWED",
            "message": "선택할 수 없는 알림톡 템플릿입니다.",
            "label": "",
            "content": "",
            "status": "",
            "buttons": [],
        }
    source = os.environ
    provider_readiness = solapi_environment_readiness(
        source,
        required_template_env_names=(template["env_name"],),
    )
    if not provider_readiness.get("ready"):
        return {
            "ok": False,
            "code": "CONFIGURATION_MISSING",
            "message": "승인 템플릿 내용을 불러오기 위한 설정이 필요합니다.",
            "label": template["label"],
            "content": "",
            "status": "",
            "buttons": [],
        }
    try:
        provider = client or SolapiAlimtalkClient(
            SolapiAlimtalkConfig.from_env(source)
        )
        preview = provider.get_template_preview(
            str(source.get(template["env_name"], "") or "")
        )
    except SolapiAlimtalkError as exc:
        return {
            "ok": False,
            "code": re.sub(
                r"[^A-Z0-9_-]",
                "_",
                str(exc.code or "TEMPLATE_LOOKUP_FAILED").upper(),
            )[:80],
            "message": "승인 템플릿 내용을 불러오지 못했습니다.",
            "label": template["label"],
            "content": "",
            "status": "",
            "buttons": [],
        }
    except Exception:
        return {
            "ok": False,
            "code": "TEMPLATE_LOOKUP_FAILED",
            "message": "승인 템플릿 내용을 불러오지 못했습니다.",
            "label": template["label"],
            "content": "",
            "status": "",
            "buttons": [],
        }
    return {
        "ok": True,
        "code": "READY",
        "message": "승인 템플릿 내용을 불러왔습니다.",
        "label": template["label"],
        "content": str(preview.get("content") or ""),
        "status": str(preview.get("status") or ""),
        "buttons": [
            {
                "name": str(button.get("name") or ""),
                "mobile_url": str(button.get("mobile_url") or ""),
            }
            for button in preview.get("buttons") or []
            if isinstance(button, Mapping)
        ],
    }


def render_claim_auth_alimtalk_preview(
    preview: Mapping[str, Any],
    customer_name: Any,
    auth_link: Any,
) -> dict[str, Any]:
    """Apply the two approved variables to display-only template content."""

    def clean_value(value: Any, fallback: str, limit: int) -> str:
        clean = "".join(
            character
            for character in str(value or "").strip()
            if ord(character) >= 32 or character in {"\n", "\t"}
        )[:limit]
        return clean or fallback

    values = {
        "#{고객명}": clean_value(customer_name, "[고객이름 입력값]", 50),
        "#{인증링크}": clean_value(auth_link, "[인증주소 입력값]", 500),
    }

    def apply_values(value: Any) -> str:
        rendered = str(value or "")
        for placeholder, replacement in values.items():
            rendered = rendered.replace(placeholder, replacement)
        return rendered

    return {
        "content": apply_values(preview.get("content")),
        "buttons": [
            {
                "name": apply_values(button.get("name")),
                "mobile_url": apply_values(button.get("mobile_url")),
            }
            for button in preview.get("buttons") or []
            if isinstance(button, Mapping)
        ],
    }


def _normalize_local_mobile(recipient: str) -> str | None:
    raw = str(recipient or "").strip()
    if not raw or not _PHONE_ALLOWED_PATTERN.fullmatch(raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    if len(digits) not in {10, 11}:
        return None
    if not re.fullmatch(r"01(?:0|1|6|7|8|9)\d{7,8}", digits):
        return None
    return digits


def _normalize_kakao_mobile(recipient: str) -> str | None:
    local = _normalize_local_mobile(recipient)
    if local is None:
        return None
    return "82" + local[1:]


def _normalize_claim_auth_link(value: Any) -> str | None:
    """Validate the scheme-free value required by the approved template."""

    clean = str(value or "").strip()
    if (
        not clean
        or len(clean) > 500
        or "://" in clean
        or clean.startswith("//")
        or any(character.isspace() for character in clean)
    ):
        return None
    parsed = urlsplit("https://" + clean)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    hostname = str(parsed.hostname).rstrip(".")
    labels = hostname.split(".")
    if (
        len(labels) < 2
        or any(
            not label
            or len(label) > 63
            or not _AUTH_LINK_HOST_LABEL_PATTERN.fullmatch(label)
            for label in labels
        )
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and not 1 <= port <= 65535:
        return None
    return clean


def validate_claim_auth_alimtalk(
    recipient: Any,
    customer_name: Any,
    auth_link: Any,
    *,
    template_code: Any = SOLAPI_ALIMTALK_DEFAULT_TEMPLATE_CODE,
) -> dict[str, Any]:
    """Validate allowlisted template inputs without echoing personal data."""

    if _claim_auth_template_spec(template_code) is None:
        return _status(
            False,
            "TEMPLATE_NOT_ALLOWED",
            "선택할 수 없는 알림톡 템플릿입니다.",
        )

    if _normalize_local_mobile(str(recipient or "")) is None:
        return _status(
            False,
            "INVALID_RECIPIENT",
            "유효한 휴대전화 번호를 확인해 주세요.",
        )
    clean_name = str(customer_name or "").strip()
    if (
        not clean_name
        or len(clean_name) > 50
        or any(ord(character) < 32 for character in clean_name)
    ):
        return _status(
            False,
            "CUSTOMER_NAME_REQUIRED",
            "고객이름을 50자 이내로 입력해 주세요.",
        )
    if _normalize_claim_auth_link(auth_link) is None:
        return _status(
            False,
            "AUTH_LINK_INVALID",
            "http:// 또는 https://를 제외하고 인증 주소만 입력해 주세요.",
        )
    return _status(True, "VALID", "알림톡 입력값을 확인했습니다.")


def _validate_email(recipient: str) -> bool:
    clean = str(recipient or "").strip()
    return bool(
        3 <= len(clean) <= 254
        and "\r" not in clean
        and "\n" not in clean
        and _EMAIL_PATTERN.fullmatch(clean)
    )


def _sms_body_bytes(body: str) -> int | None:
    try:
        return len(str(body).encode("euc-kr"))
    except UnicodeEncodeError:
        return None


def _prepare_compliant_message(
    channel: str,
    subject: str,
    body: str,
    source: Mapping[str, str],
) -> tuple[str, str]:
    """Add mandatory advertising disclosures around free-form user content."""

    clean_channel = str(channel or "").strip().lower()
    clean_subject = str(subject or "").strip()
    clean_body = str(body or "").strip()
    if not _enabled(source.get(OUTREACH_COMPLIANCE_CONFIRMED_ENV, "")):
        return clean_subject, clean_body

    if clean_channel == CHANNEL_EMAIL:
        subject_text = re.sub(
            r"^\s*\(광고\)\s*",
            "",
            clean_subject,
            count=1,
        )
        sender_name = str(
            source.get(OUTREACH_SENDER_NAME_ENV, "") or ""
        ).strip()
        sender_email = str(
            source.get(OUTREACH_SENDER_EMAIL_ENV, "") or ""
        ).strip()
        sender_phone = str(
            source.get(OUTREACH_SENDER_PHONE_ENV, "") or ""
        ).strip()
        sender_address = str(
            source.get(OUTREACH_SENDER_ADDRESS_ENV, "") or ""
        ).strip()
        opt_out = str(
            source.get(OUTREACH_EMAIL_OPT_OUT_TEXT_ENV, "") or ""
        ).strip()
        footer = (
            "\n\n---\n"
            f"전송자: {sender_name}\n"
            f"이메일: {sender_email}\n"
            f"전화: {sender_phone}\n"
            f"주소: {sender_address}\n"
            f"{opt_out}"
        )
        return f"(광고) {subject_text}".strip(), clean_body + footer

    if clean_channel == CHANNEL_SMS:
        free_body = re.sub(
            r"^\s*\(광고\)\s*",
            "",
            clean_body,
            count=1,
        )
        sender_name = str(
            source.get(OUTREACH_SENDER_NAME_ENV, "") or ""
        ).strip()
        sender_phone = str(
            source.get(SMSKOREA_SENDER_ENV, "") or ""
        ).strip()
        opt_out = str(
            source.get(OUTREACH_SMS_FREE_OPT_OUT_NUMBER_ENV, "") or ""
        ).strip()
        prepared_body = (
            f"(광고){sender_name}\n{free_body}\n{sender_phone}\n"
            f"무료수신거부 {opt_out}"
        )
        prepared_subject = clean_subject
        body_bytes = _sms_body_bytes(prepared_body)
        if body_bytes is not None and body_bytes > 90 and not prepared_subject:
            prepared_subject = f"{sender_name} 안내"
        return prepared_subject, prepared_body

    return clean_subject, clean_body


def validate_message(
    channel: str,
    recipient: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    """Validate an outbound message without exposing its content."""

    clean_channel = str(channel or "").strip().lower()
    clean_subject = str(subject or "").strip()
    clean_body = str(body or "")

    if clean_channel not in _CHANNEL_ENV_NAMES:
        return _status(
            False,
            "UNSUPPORTED_CHANNEL",
            "지원하지 않는 발송 방식입니다.",
        )
    if not clean_body.strip():
        return _status(False, "BODY_REQUIRED", "발송 내용을 입력해 주세요.")
    if clean_channel == CHANNEL_EMAIL and not clean_subject:
        return _status(
            False,
            "SUBJECT_REQUIRED",
            "이메일 제목을 입력해 주세요.",
        )

    clean_subject, clean_body = _prepare_compliant_message(
        clean_channel,
        clean_subject,
        clean_body,
        os.environ,
    )

    if clean_channel == CHANNEL_EMAIL:
        if not _validate_email(recipient):
            return _status(
                False,
                "INVALID_RECIPIENT",
                "유효한 이메일 주소를 확인해 주세요.",
            )
        if not clean_subject:
            return _status(
                False,
                "SUBJECT_REQUIRED",
                "이메일 제목을 입력해 주세요.",
            )
        if len(clean_subject) > 200:
            return _status(
                False,
                "SUBJECT_TOO_LONG",
                "이메일 제목이 너무 깁니다.",
            )
        if len(clean_body) > 100_000:
            return _status(
                False,
                "BODY_TOO_LONG",
                "이메일 내용이 너무 깁니다.",
            )
    elif clean_channel == CHANNEL_SMS:
        if _normalize_local_mobile(recipient) is None:
            return _status(
                False,
                "INVALID_RECIPIENT",
                "유효한 휴대전화 번호를 확인해 주세요.",
            )
        body_bytes = _sms_body_bytes(clean_body)
        if body_bytes is None:
            return _status(
                False,
                "SMS_ENCODING_UNSUPPORTED",
                "문자코리아에서 지원하는 문자로 내용을 입력해 주세요.",
            )
        if body_bytes > 2_000:
            return _status(
                False,
                "BODY_TOO_LONG",
                "문자 내용이 발송 한도를 초과했습니다.",
            )
        if body_bytes > 90 and not clean_subject:
            return _status(
                False,
                "SUBJECT_REQUIRED",
                "장문 문자 제목을 입력해 주세요.",
            )
        if len(clean_subject) > 100:
            return _status(
                False,
                "SUBJECT_TOO_LONG",
                "문자 제목이 너무 깁니다.",
            )
    elif clean_channel == CHANNEL_KAKAO:
        if _normalize_kakao_mobile(recipient) is None:
            return _status(
                False,
                "INVALID_RECIPIENT",
                "유효한 휴대전화 번호를 확인해 주세요.",
            )
        if len(clean_body) > 1_000:
            return _status(
                False,
                "BODY_TOO_LONG",
                "카카오톡 내용이 발송 한도를 초과했습니다.",
            )
        if len(clean_subject) > 50:
            return _status(
                False,
                "SUBJECT_TOO_LONG",
                "카카오톡 제목이 너무 깁니다.",
            )

    return _status(True, "VALID", "발송 내용을 확인했습니다.")


def _valid_idempotency_key(value: str) -> bool:
    return bool(_SAFE_IDEMPOTENCY_PATTERN.fullmatch(str(value or "").strip()))


def outreach_send_window(
    channel: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the public send-window status in Asia/Seoul."""

    clean_channel = str(channel or "").strip().lower()
    if clean_channel not in {CHANNEL_SMS, CHANNEL_KAKAO}:
        return {
            "allowed": True,
            "code": "READY",
            "message": "현재 발송할 수 있습니다.",
        }
    seoul = ZoneInfo("Asia/Seoul")
    current = now or datetime.now(seoul)
    if current.tzinfo is None:
        current = current.replace(tzinfo=seoul)
    else:
        current = current.astimezone(seoul)
    allowed = 8 <= current.hour < 21
    return {
        "allowed": allowed,
        "code": "READY" if allowed else "NIGHT_SEND_BLOCKED",
        "message": (
            "현재 발송할 수 있습니다."
            if allowed
            else "문자·카카오톡 영업 발송은 오전 8시부터 오후 9시 전까지만 가능합니다."
        ),
    }


def _within_standard_send_hours() -> bool:
    return bool(outreach_send_window(CHANNEL_KAKAO).get("allowed"))


def _response_json(response: Any) -> dict[str, Any] | None:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _http_failure(status_code: int | None) -> dict[str, Any]:
    if status_code in {401, 403}:
        return _status(
            False,
            "PROVIDER_AUTH_FAILED",
            "발송 서비스 인증을 확인해 주세요.",
        )
    if status_code == 429:
        return _status(
            False,
            "PROVIDER_RATE_LIMITED",
            "발송 요청이 많아 잠시 후 다시 시도해 주세요.",
        )
    return _status(
        False,
        "PROVIDER_HTTP_ERROR",
        "외부 발송 서비스가 요청을 처리하지 못했습니다.",
    )


def _post(
    session: requests.Session,
    url: str,
    *,
    timeout: float,
    delivery_ambiguous: bool = False,
    expected_status_codes: set[int] | None = None,
    **kwargs: Any,
) -> tuple[Any | None, dict[str, Any] | None]:
    network_failure = _status(
        False,
        "DELIVERY_UNKNOWN" if delivery_ambiguous else "PROVIDER_NETWORK_ERROR",
        (
            "발송 접수 여부를 확인할 수 없습니다. 공급자 관리 화면에서 확인해 주세요."
            if delivery_ambiguous
            else "외부 발송 서비스에 연결하지 못했습니다."
        ),
    )
    try:
        response = session.post(url, timeout=timeout, **kwargs)
    except requests.RequestException:
        return None, network_failure
    except Exception:
        # Injected/custom sessions can raise non-requests transport errors.
        # Never include the exception because it may contain request secrets.
        return None, network_failure
    status_code = getattr(response, "status_code", None)
    status_ok = bool(
        isinstance(status_code, int)
        and 200 <= status_code < 300
        and (
            expected_status_codes is None
            or status_code in expected_status_codes
        )
    )
    if not status_ok:
        if delivery_ambiguous and (
            not isinstance(status_code, int)
            or status_code == 408
            or status_code >= 500
            or 200 <= status_code < 400
        ):
            return None, _status(
                False,
                "DELIVERY_UNKNOWN",
                "발송 접수 여부를 확인할 수 없습니다. 공급자 관리 화면에서 확인해 주세요.",
            )
        return None, _http_failure(status_code)
    return response, None


def _send_sms(
    recipient: str,
    subject: str,
    body: str,
    *,
    session: requests.Session,
    source: Mapping[str, str],
    timeout: float,
) -> dict[str, Any]:
    token_response, failure = _post(
        session,
        SMSKOREA_TOKEN_URL,
        timeout=timeout,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={
            "userId": str(source[SMSKOREA_USER_ID_ENV]),
            "sec_apiKey": str(source[SMSKOREA_SEC_API_KEY_ENV]),
        },
    )
    if failure is not None:
        return failure
    token_payload = _response_json(token_response)
    token_result = (
        token_payload.get("result")
        if isinstance(token_payload, dict)
        else None
    )
    access_token = (
        str(token_result.get("accessToken", "")).strip()
        if isinstance(token_result, dict)
        else ""
    )
    if not access_token:
        return _status(
            False,
            "PROVIDER_RESPONSE_INVALID",
            "문자 발송 서비스 인증 응답을 확인해 주세요.",
        )

    body_bytes = _sms_body_bytes(body) or 0
    message_type = "sms" if body_bytes <= 90 else "lms"
    message_payload: dict[str, Any] = {
        "userId": str(source[SMSKOREA_USER_ID_ENV]),
        "sender": re.sub(r"\D", "", str(source[SMSKOREA_SENDER_ENV])),
        "receiver": [_normalize_local_mobile(recipient)],
        "title": str(subject or "").strip(),
        "message": str(body),
        "messageType": message_type,
    }
    secret_mode = str(
        source[SMSKOREA_MESSAGE_SECRET_MODE_ENV]
    ).strip().lower()
    if secret_mode == SMSKOREA_SECRET_MODE_INCLUDE:
        message_payload["sec_apiKey"] = str(
            source[SMSKOREA_SEC_API_KEY_ENV]
        )

    _message_response, failure = _post(
        session,
        SMSKOREA_MESSAGE_URL,
        timeout=timeout,
        delivery_ambiguous=True,
        expected_status_codes={200},
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=message_payload,
    )
    if failure is not None:
        return failure
    # 문자코리아's public guide does not document a stable provider message ID.
    # HTTP 200 means accepted, not final delivery, so do not claim delivery.
    return _status(
        True,
        "ACCEPTED",
        "문자코리아에 발송 요청이 접수되었습니다.",
    )


def _send_email(
    recipient: str,
    subject: str,
    body: str,
    *,
    session: requests.Session,
    source: Mapping[str, str],
    timeout: float,
) -> dict[str, Any]:
    # Supplying (None, value) makes requests generate multipart/form-data with
    # the required boundary while keeping every field as plain form text.
    files = {
        "to": (None, str(recipient).strip()),
        "user_id": (None, str(source[HIWORKS_USER_ID_ENV])),
        "cc": (None, ""),
        "bcc": (None, ""),
        "subject": (None, str(subject).strip()),
        "content": (None, str(body)),
        "save_sent_mail": (None, "Y"),
    }
    response, failure = _post(
        session,
        HIWORKS_SEND_MAIL_URL,
        timeout=timeout,
        delivery_ambiguous=True,
        expected_status_codes={200},
        headers={
            "Accept": "application/json",
            "Authorization": (
                f"Bearer {str(source[HIWORKS_OFFICE_TOKEN_ENV])}"
            ),
        },
        files=files,
    )
    if failure is not None:
        return failure
    payload = _response_json(response)
    if not isinstance(payload, dict) or "code" not in payload:
        return _status(
            False,
            "DELIVERY_UNKNOWN",
            "메일 발송 접수 여부를 확인할 수 없습니다. 하이웍스 발송내역을 확인해 주세요.",
        )
    payload_result = (
        payload.get("result") if isinstance(payload, dict) else None
    )
    success_list = (
        payload_result.get("successList")
        if isinstance(payload_result, dict)
        else None
    )
    clean_recipient = str(recipient).strip().casefold()
    recipient_accepted = bool(
        isinstance(success_list, list)
        and any(
            str(item or "").strip().casefold() == clean_recipient
            for item in success_list
        )
    )
    if (
        not isinstance(payload, dict)
        or str(payload.get("code", "")) != "SUC"
        or not recipient_accepted
    ):
        return _status(
            False,
            "PROVIDER_REJECTED",
            "하이웍스가 메일 발송 요청을 승인하지 않았습니다.",
        )
    return _status(True, "ACCEPTED", "하이웍스 메일 발송을 접수했습니다.")


def _send_kakao(
    recipient: str,
    subject: str,
    body: str,
    idempotency_key: str,
    *,
    session: requests.Session,
    source: Mapping[str, str],
    timeout: float,
) -> dict[str, Any]:
    client_id = str(source[KAKAO_BIZ_CLIENT_ID_ENV])
    client_secret_value = str(source[KAKAO_BIZ_CLIENT_SECRET_ENV])
    token_response, failure = _post(
        session,
        KAKAO_BIZ_TOKEN_URL,
        timeout=timeout,
        headers={
            "Accept": "*/*",
            "Authorization": f"Basic {client_id} {client_secret_value}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
    )
    if failure is not None:
        return failure
    token_payload = _response_json(token_response)
    token_code = (
        str(token_payload.get("code", ""))
        if isinstance(token_payload, dict)
        else ""
    )
    access_token = (
        str(token_payload.get("access_token", "")).strip()
        if isinstance(token_payload, dict)
        else ""
    )
    if token_code not in {"200", "201"} or not access_token:
        return _status(
            False,
            "PROVIDER_RESPONSE_INVALID",
            "카카오톡 발송 서비스 인증 응답을 확인해 주세요.",
        )

    cid = "oasis-" + hashlib.sha256(
        str(idempotency_key).encode("utf-8")
    ).hexdigest()[:32]
    message_payload = {
        "message_type": "FT",
        "sender_key": str(source[KAKAO_BIZ_SENDER_KEY_ENV]),
        "cid": cid,
        "template_code": str(source[KAKAO_BIZ_TEMPLATE_CODE_ENV]),
        "phone_number": _normalize_kakao_mobile(recipient),
        "sender_no": re.sub(
            r"\D", "", str(source[KAKAO_BIZ_SENDER_NO_ENV])
        ),
        "message": str(body),
        "ad_flag": "Y",
        "fall_back_yn": False,
    }
    if str(subject or "").strip():
        message_payload["title"] = str(subject).strip()

    response, failure = _post(
        session,
        KAKAO_BIZ_SEND_URL,
        timeout=timeout,
        delivery_ambiguous=True,
        expected_status_codes={200},
        headers={
            "Accept": "*/*",
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        json=message_payload,
    )
    if failure is not None:
        return failure
    payload = _response_json(response)
    provider_code = (
        str(payload.get("code", "")) if isinstance(payload, dict) else ""
    )
    if not isinstance(payload, dict) or not provider_code:
        return _status(
            False,
            "DELIVERY_UNKNOWN",
            "카카오톡 발송 접수 여부를 확인할 수 없습니다. 공급자 발송내역을 확인해 주세요.",
        )
    if provider_code not in {"100", "200"}:
        return _status(
            False,
            "PROVIDER_REJECTED",
            "카카오톡 발송 요청이 승인되지 않았습니다.",
        )
    provider_id = str(payload.get("uid", "")) if isinstance(payload, dict) else ""
    return _status(
        True,
        "ACCEPTED",
        "카카오톡 발송 요청이 접수되었습니다.",
        provider_id,
    )


def send_claim_auth_alimtalk(
    recipient: Any,
    customer_name: Any,
    auth_link: Any,
    idempotency_key: Any,
    *,
    template_code: Any = SOLAPI_ALIMTALK_DEFAULT_TEMPLATE_CODE,
    client: SolapiAlimtalkClient | None = None,
) -> dict[str, Any]:
    """Send one approved Alimtalk template after the durable DB claim."""

    template = _claim_auth_template_spec(template_code)
    if template is None:
        return _status(
            False,
            "TEMPLATE_NOT_ALLOWED",
            "선택할 수 없는 알림톡 템플릿입니다.",
        )
    readiness = claim_auth_alimtalk_readiness(template["code"])
    if not readiness["external_send_allowed"]:
        return _status(
            False,
            str(readiness.get("code") or "CONFIGURATION_NOT_READY"),
            str(
                readiness.get("message")
                or "Solapi 알림톡 발송 설정을 확인해 주세요."
            ),
        )
    validation = validate_claim_auth_alimtalk(
        recipient,
        customer_name,
        auth_link,
        template_code=template["code"],
    )
    if not validation["ok"]:
        return validation
    if not _valid_idempotency_key(str(idempotency_key or "")):
        return _status(
            False,
            "IDEMPOTENCY_KEY_REQUIRED",
            "중복 발송 방지 키를 확인해 주세요.",
        )
    if not _within_standard_send_hours():
        return _status(
            False,
            "NIGHT_SEND_BLOCKED",
            "카카오톡 발송은 오전 8시부터 오후 9시 전까지만 가능합니다.",
        )

    clean_link = _normalize_claim_auth_link(auth_link)
    if clean_link is None:
        return _status(
            False,
            "AUTH_LINK_INVALID",
            "http:// 또는 https://를 제외하고 인증 주소만 입력해 주세요.",
        )
    source = os.environ
    provider_call_started = False
    try:
        provider = client or SolapiAlimtalkClient(
            SolapiAlimtalkConfig.from_env(source)
        )
        provider_call_started = True
        provider.send_alimtalk(
            str(recipient or ""),
            str(source.get(template["env_name"], "") or ""),
            variables={
                "#{고객명}": str(customer_name or "").strip(),
                "#{인증링크}": clean_link,
            },
            disable_sms=True,
        )
    except SolapiAlimtalkError as exc:
        ambiguous = provider_call_started and (
            exc.code in {"TIMEOUT", "NETWORK_ERROR", "INVALID_RESPONSE"}
            or (
                exc.code == "HTTP_ERROR"
                and (exc.http_status is None or exc.http_status >= 500)
            )
        )
        if ambiguous:
            return _status(
                False,
                "DELIVERY_UNKNOWN",
                "Solapi 접수 여부를 확인할 수 없습니다. 발송내역을 확인해 주세요.",
            )
        return _status(
            False,
            re.sub(r"[^A-Z0-9_-]", "_", str(exc.code or ""))[:80]
            or "PROVIDER_REJECTED",
            "Solapi가 알림톡 발송 요청을 접수하지 않았습니다.",
        )
    except Exception:
        return _status(
            False,
            "DELIVERY_UNKNOWN" if provider_call_started else "PROVIDER_REJECTED",
            (
                "Solapi 접수 여부를 확인할 수 없습니다. 발송내역을 확인해 주세요."
                if provider_call_started
                else "Solapi 알림톡 발송 설정을 확인해 주세요."
            ),
        )
    return _status(
        True,
        "ACCEPTED",
        "Solapi 알림톡 발송 요청이 접수되었습니다.",
    )


def send_outreach(
    channel: str,
    recipient: str,
    subject: str,
    body: str,
    idempotency_key: str,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Validate and submit one outbound message through the selected provider.

    The function never raises provider details to its caller and never performs
    network I/O unless both the global hard gate and all channel settings are
    explicitly ready.
    """

    clean_channel = str(channel or "").strip().lower()
    readiness = channel_readiness(clean_channel)
    if not readiness["external_send_allowed"]:
        return _status(
            False,
            str(readiness.get("code") or "CONFIGURATION_NOT_READY"),
            str(
                readiness.get("message")
                or "발송 서비스 설정을 확인해 주세요."
            ),
        )
    validation = validate_message(clean_channel, recipient, subject, body)
    if not validation["ok"]:
        return validation
    prepared_subject, prepared_body = _prepare_compliant_message(
        clean_channel,
        subject,
        body,
        os.environ,
    )
    if not _valid_idempotency_key(idempotency_key):
        return _status(
            False,
            "IDEMPOTENCY_KEY_REQUIRED",
            "중복 발송 방지 키를 확인해 주세요.",
        )

    if clean_channel in {CHANNEL_SMS, CHANNEL_KAKAO} and not (
        _within_standard_send_hours()
    ):
        return _status(
            False,
            "NIGHT_SEND_BLOCKED",
            "문자·카카오톡 영업 발송은 오전 8시부터 오후 9시 전까지만 가능합니다.",
        )

    source = os.environ
    timeout = _timeout_seconds(source)
    client = session or requests.Session()
    owns_session = session is None
    try:
        if clean_channel == CHANNEL_SMS:
            return _send_sms(
                recipient,
                prepared_subject,
                prepared_body,
                session=client,
                source=source,
                timeout=timeout,
            )
        if clean_channel == CHANNEL_EMAIL:
            return _send_email(
                recipient,
                prepared_subject,
                prepared_body,
                session=client,
                source=source,
                timeout=timeout,
            )
        return _send_kakao(
            recipient,
            prepared_subject,
            prepared_body,
            idempotency_key,
            session=client,
            source=source,
            timeout=timeout,
        )
    finally:
        if owns_session:
            try:
                client.close()
            except Exception:
                pass


__all__ = [
    "CHANNEL_EMAIL",
    "CHANNEL_SMS",
    "CHANNEL_KAKAO",
    "SOLAPI_ALIMTALK_DEFAULT_TEMPLATE_CODE",
    "SOLAPI_ALIMTALK_TEMPLATE_SPECS",
    "SOLAPI_CLAIM_AUTH_TEMPLATE_ENV",
    "SOLAPI_CLAIM_AUTH_TEMPLATE_LABEL",
    "claim_auth_alimtalk_templates",
    "claim_auth_alimtalk_readiness",
    "claim_auth_alimtalk_template_preview",
    "channel_readiness",
    "outreach_send_window",
    "render_claim_auth_alimtalk_preview",
    "send_claim_auth_alimtalk",
    "validate_message",
    "validate_claim_auth_alimtalk",
    "send_outreach",
]
