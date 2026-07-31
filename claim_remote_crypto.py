from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class ClaimRemoteCryptoError(RuntimeError):
    """Raised when remote-claim secrets or encrypted payloads are invalid."""


def _secret(name: str) -> str:
    value = str(os.environ.get(name, "") or "").strip()
    if value:
        return value
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return ""


def _urlsafe_b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _urlsafe_unb64(value: str) -> bytes:
    text = str(value or "").strip()
    if not text or any(
        not character.isascii()
        or not (character.isalnum() or character in "-_")
        for character in text
    ):
        raise ClaimRemoteCryptoError("인증 링크 형식이 올바르지 않습니다.")
    padding = "=" * ((4 - len(text) % 4) % 4)
    try:
        decoded = base64.b64decode(
            f"{text}{padding}".encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, UnicodeEncodeError, ValueError, TypeError) as exc:
        raise ClaimRemoteCryptoError("인증 링크 형식이 올바르지 않습니다.") from exc
    # Reject alternate/non-canonical encodings whose decoded bytes happen to
    # match a valid signature. This prevents last-character substitution from
    # being accepted when only unused Base64 pad bits differ.
    if _urlsafe_b64(decoded) != text:
        raise ClaimRemoteCryptoError("인증 링크 형식이 올바르지 않습니다.")
    return decoded


def _fernet_key(raw_secret: str) -> bytes:
    raw = str(raw_secret or "").strip()
    if not raw:
        raise ClaimRemoteCryptoError(
            "CLAIM_JOB_ENCRYPTION_KEY 환경변수가 필요합니다."
        )
    try:
        decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
        if len(decoded) == 32:
            return raw.encode("ascii")
    except (ValueError, TypeError):
        pass
    if len(raw) < 32:
        raise ClaimRemoteCryptoError(
            "CLAIM_JOB_ENCRYPTION_KEY는 32자 이상의 무작위 값이어야 합니다."
        )
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


@dataclass(frozen=True)
class ClaimRemoteCrypto:
    cipher: Fernet
    link_pepper: bytes
    session_secret: bytes

    @classmethod
    def from_environment(cls) -> "ClaimRemoteCrypto":
        encryption_secret = _secret("CLAIM_JOB_ENCRYPTION_KEY")
        link_secret = _secret("CLAIM_LINK_PEPPER")
        session_secret = _secret("CLAIM_SESSION_SECRET")
        if len(link_secret) < 32:
            raise ClaimRemoteCryptoError(
                "CLAIM_LINK_PEPPER는 32자 이상의 무작위 값이어야 합니다."
            )
        if len(session_secret) < 32:
            raise ClaimRemoteCryptoError(
                "CLAIM_SESSION_SECRET은 32자 이상의 무작위 값이어야 합니다."
            )
        return cls(
            cipher=Fernet(_fernet_key(encryption_secret)),
            link_pepper=link_secret.encode("utf-8"),
            session_secret=session_secret.encode("utf-8"),
        )

    @staticmethod
    def generate_invite_token() -> str:
        return secrets.token_urlsafe(32)

    @staticmethod
    def generate_csrf_token() -> str:
        return secrets.token_urlsafe(24)

    def invite_token_hash(self, token: str) -> str:
        clean = str(token or "").strip()
        if len(clean) < 32:
            raise ClaimRemoteCryptoError("인증 링크 토큰이 올바르지 않습니다.")
        return hmac.new(
            self.link_pepper,
            clean.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def encrypt_json(self, payload: dict[str, Any]) -> str:
        serialized = json.dumps(
            dict(payload or {}),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.cipher.encrypt(serialized).decode("ascii")

    def decrypt_json(self, ciphertext: str) -> dict[str, Any]:
        try:
            decoded = self.cipher.decrypt(
                str(ciphertext or "").encode("ascii")
            )
            value = json.loads(decoded.decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ClaimRemoteCryptoError(
                "임시 인증정보를 안전하게 복구하지 못했습니다."
            ) from exc
        if not isinstance(value, dict):
            raise ClaimRemoteCryptoError("임시 인증정보 형식이 올바르지 않습니다.")
        return value

    def create_session_token(
        self,
        *,
        invite_id: str,
        csrf_token: str,
        expires_at: int,
    ) -> str:
        payload = {
            "invite_id": str(invite_id or "").strip(),
            "csrf": str(csrf_token or "").strip(),
            "exp": int(expires_at),
        }
        encoded = _urlsafe_b64(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature = hmac.new(
            self.session_secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded}.{_urlsafe_b64(signature)}"

    def verify_session_token(self, token: str) -> dict[str, Any]:
        parts = str(token or "").strip().split(".")
        if len(parts) != 2:
            raise ClaimRemoteCryptoError("고객 인증 세션이 올바르지 않습니다.")
        encoded, signature_text = parts
        actual = _urlsafe_unb64(signature_text)
        expected = hmac.new(
            self.session_secret,
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(actual, expected):
            raise ClaimRemoteCryptoError("고객 인증 세션을 확인하지 못했습니다.")
        try:
            payload = json.loads(_urlsafe_unb64(encoded).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ClaimRemoteCryptoError(
                "고객 인증 세션 형식이 올바르지 않습니다."
            ) from exc
        if not isinstance(payload, dict):
            raise ClaimRemoteCryptoError("고객 인증 세션이 올바르지 않습니다.")
        if int(payload.get("exp", 0) or 0) <= int(time.time()):
            raise ClaimRemoteCryptoError("고객 인증 세션이 만료되었습니다.")
        if not str(payload.get("invite_id", "") or "").strip():
            raise ClaimRemoteCryptoError("고객 인증 요청을 확인하지 못했습니다.")
        if not str(payload.get("csrf", "") or "").strip():
            raise ClaimRemoteCryptoError("고객 인증 보안값을 확인하지 못했습니다.")
        return payload


def remote_claim_crypto_readiness() -> tuple[bool, str]:
    try:
        ClaimRemoteCrypto.from_environment()
        return True, "원격 인증 암호화 설정 완료"
    except ClaimRemoteCryptoError as exc:
        return False, str(exc)
