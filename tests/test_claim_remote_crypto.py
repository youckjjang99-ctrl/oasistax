from __future__ import annotations

import base64
import os
import time
from unittest.mock import patch

import pytest

from claim_remote_crypto import ClaimRemoteCrypto, ClaimRemoteCryptoError


def _crypto() -> ClaimRemoteCrypto:
    key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    with patch.dict(
        os.environ,
        {
            "CLAIM_JOB_ENCRYPTION_KEY": key,
            "CLAIM_LINK_PEPPER": "p" * 48,
            "CLAIM_SESSION_SECRET": "s" * 48,
        },
        clear=False,
    ):
        return ClaimRemoteCrypto.from_environment()


def test_remote_crypto_encrypts_payload_and_hashes_tokens() -> None:
    crypto = _crypto()
    token = crypto.generate_invite_token()
    payload = {
        "identity_number": "9001011234567",
        "cellphone": "01012345678",
    }

    encrypted = crypto.encrypt_json(payload)

    assert "9001011234567" not in encrypted
    assert crypto.decrypt_json(encrypted) == payload
    assert crypto.invite_token_hash(token) == crypto.invite_token_hash(token)
    assert crypto.invite_token_hash(token) != crypto.invite_token_hash(
        crypto.generate_invite_token()
    )


def test_remote_crypto_session_is_signed_and_expires() -> None:
    crypto = _crypto()
    session = crypto.create_session_token(
        invite_id="invite-id",
        csrf_token="csrf-token",
        expires_at=int(time.time()) + 60,
    )
    payload = crypto.verify_session_token(session)

    assert payload["invite_id"] == "invite-id"
    assert payload["csrf"] == "csrf-token"

    with pytest.raises(ClaimRemoteCryptoError):
        crypto.verify_session_token(f"{session[:-1]}x")

    expired = crypto.create_session_token(
        invite_id="invite-id",
        csrf_token="csrf-token",
        expires_at=int(time.time()) - 1,
    )
    with pytest.raises(ClaimRemoteCryptoError, match="만료"):
        crypto.verify_session_token(expired)


def test_remote_crypto_requires_independent_long_secrets() -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")
    with patch.dict(
        os.environ,
        {
            "CLAIM_JOB_ENCRYPTION_KEY": key,
            "CLAIM_LINK_PEPPER": "short",
            "CLAIM_SESSION_SECRET": "short",
        },
        clear=False,
    ):
        with pytest.raises(ClaimRemoteCryptoError):
            ClaimRemoteCrypto.from_environment()
