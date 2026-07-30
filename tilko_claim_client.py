from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, Iterable

import requests
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric_padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import (
    load_der_public_key,
    load_pem_public_key,
)


HOMETAX_HOST = "https://api.tilko.net"
COMWEL_HOST = "https://api24.tilko.net"
HOMETAX_SIMPLE_AUTH_REQUEST = (
    "/api/v2.0/HometaxSimpleAuth/SimpleAuthRequest"
)
HOMETAX_SIMPLE_AUTH_CHECK = "/api/v2.0/HometaxSimpleAuth/LoginCheck"
COMWEL_SIMPLE_AUTH_REQUEST = (
    "/api/v2.0/KcomwelSimpleAuth/SimpleAuthRequest"
)
COMWEL_SIMPLE_AUTH_CHECK = "/api/v2.0/KcomwelSimpleAuth/LoginCheck"
SESSION_FIELDS = ("Token", "CxId", "TxId", "ReqTxId")


class ClaimProviderError(RuntimeError):
    """A provider error whose text is safe to show in the app."""


def _read_secret(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    if value:
        return str(value).strip()
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return default


def _enabled(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TilkoClaimConfig:
    api_key: str
    rsa_public_key: str
    collection_enabled: bool
    hometax_host: str = HOMETAX_HOST
    comwel_host: str = COMWEL_HOST
    timeout_seconds: int = 60

    @property
    def simple_auth_ready(self) -> bool:
        return bool(
            self.collection_enabled
            and self.api_key
            and self.rsa_public_key
            and self.hometax_host.rstrip("/") == HOMETAX_HOST
            and self.comwel_host.rstrip("/") == COMWEL_HOST
        )

    @property
    def corporate_auth_ready(self) -> bool:
        # 공동인증서 연동은 요청 건별 state와 공급사 콜백 서명 검증이
        # 구현되기 전에는 활성화하지 않는다.
        return False


def get_tilko_claim_config() -> TilkoClaimConfig:
    return TilkoClaimConfig(
        api_key=_read_secret("TILKO_API_KEY"),
        rsa_public_key=_read_secret("TILKO_RSA_PUBLIC_KEY"),
        collection_enabled=_enabled(
            _read_secret("CLAIM_COLLECTION_ENABLED", "false")
        ),
        hometax_host=_read_secret(
            "TILKO_HOMETAX_HOST",
            HOMETAX_HOST,
        ).rstrip("/"),
        comwel_host=_read_secret(
            "TILKO_COMWEL_HOST",
            COMWEL_HOST,
        ).rstrip("/"),
    )


def provider_readiness(
    config: TilkoClaimConfig | None = None,
) -> dict[str, object]:
    selected = config or get_tilko_claim_config()
    missing: list[str] = []
    if not selected.api_key:
        missing.append("TILKO_API_KEY")
    if not selected.rsa_public_key:
        missing.append("TILKO_RSA_PUBLIC_KEY")
    if not selected.collection_enabled:
        missing.append("CLAIM_COLLECTION_ENABLED")
    if selected.hometax_host.rstrip("/") != HOMETAX_HOST:
        missing.append("TILKO_HOMETAX_HOST")
    if selected.comwel_host.rstrip("/") != COMWEL_HOST:
        missing.append("TILKO_COMWEL_HOST")
    return {
        "simple_auth_ready": selected.simple_auth_ready,
        "corporate_auth_ready": selected.corporate_auth_ready,
        "missing": missing,
    }


def _load_public_key(value: str):
    clean = str(value or "").strip()
    if not clean:
        raise ClaimProviderError("중계 API 공개키가 설정되지 않았습니다.")
    try:
        if "BEGIN PUBLIC KEY" in clean:
            key = load_pem_public_key(clean.encode("utf-8"))
        else:
            key = load_der_public_key(base64.b64decode(clean, validate=True))
    except Exception as exc:
        raise ClaimProviderError(
            "중계 API 공개키 형식을 확인해주세요."
        ) from exc
    if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
        raise ClaimProviderError("중계 API RSA 2048비트 공개키를 확인해주세요.")
    return key


def _aes_encrypt(aes_key: bytes, plain_text: Any) -> str:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(str(plain_text or "").encode("utf-8"))
    padded += padder.finalize()
    encryptor = Cipher(
        algorithms.AES(aes_key),
        modes.CBC(bytes(16)),
    ).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(encrypted).decode("ascii")


def _encrypt_payload(
    payload: dict[str, Any],
    aes_key: bytes,
    encrypted_paths: Iterable[str],
) -> dict[str, Any]:
    result = {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in payload.items()
    }
    for path in encrypted_paths:
        parts = path.split(".")
        current: dict[str, Any] = result
        for part in parts[:-1]:
            nested = current.get(part)
            if not isinstance(nested, dict):
                raise ClaimProviderError("인증 요청값 구성이 올바르지 않습니다.")
            current = nested
        leaf = parts[-1]
        current[leaf] = _aes_encrypt(aes_key, current.get(leaf, ""))
    return result


def extract_session_reference(response_data: dict[str, Any]) -> dict[str, str]:
    result = response_data.get("ResultData")
    if not isinstance(result, dict):
        result = response_data.get("Result")
    if not isinstance(result, dict):
        result = {}

    candidates = [result]
    credential = result.get("Credential")
    if isinstance(credential, dict):
        candidates.append(credential)
    session: dict[str, str] = {}
    for candidate in candidates:
        values = {
            key: str(candidate.get(key, "") or "").strip()
            for key in SESSION_FIELDS
        }
        if all(values.values()):
            session = values
            break
    if not all(session.values()):
        raise ClaimProviderError(
            "인증 요청은 처리됐지만 완료 확인값을 받지 못했습니다."
        )
    return session


def _response_error(response_data: dict[str, Any]) -> str:
    error_code = str(response_data.get("ErrorCode", "") or "").strip()
    target_code = str(response_data.get("TargetCode", "") or "").strip()
    code = target_code or error_code or "UNKNOWN"
    return f"중계 API 요청이 거절되었습니다. 오류코드: {code}"


def _boolean_result(response_data: dict[str, Any]) -> bool:
    result = response_data.get("Result")
    if result is True:
        return True
    if result is False:
        return False
    raise ClaimProviderError("인증 완료 응답 형식을 확인해주세요.")


class TilkoClaimClient:
    def __init__(self, config: TilkoClaimConfig | None = None):
        self.config = config or get_tilko_claim_config()
        if not self.config.simple_auth_ready:
            raise ClaimProviderError(
                "경정청구 인증 연동이 아직 활성화되지 않았습니다."
            )

    def _post(
        self,
        host: str,
        endpoint: str,
        payload: dict[str, Any],
        encrypted_paths: Iterable[str],
    ) -> dict[str, Any]:
        expected_host = (
            HOMETAX_HOST
            if endpoint.startswith("/api/v2.0/HometaxSimpleAuth/")
            else COMWEL_HOST
        )
        if host.rstrip("/") != expected_host:
            raise ClaimProviderError("공식 인증 중계 서버 주소를 확인해주세요.")
        aes_key = os.urandom(16)
        public_key = _load_public_key(self.config.rsa_public_key)
        try:
            enc_key = public_key.encrypt(
                aes_key,
                asymmetric_padding.PKCS1v15(),
            )
        except Exception as exc:
            raise ClaimProviderError(
                "중계 API 공개키로 요청을 암호화하지 못했습니다."
            ) from exc
        encrypted_payload = _encrypt_payload(
            payload,
            aes_key,
            encrypted_paths,
        )
        try:
            response = requests.post(
                f"{host.rstrip('/')}{endpoint}",
                headers={
                    "Content-Type": "application/json",
                    "API-KEY": self.config.api_key,
                    "ENC-KEY": base64.b64encode(enc_key).decode("ascii"),
                },
                json=encrypted_payload,
                timeout=self.config.timeout_seconds,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise ClaimProviderError(
                "인증 중계 서버에 연결하지 못했습니다. 잠시 후 다시 시도해주세요."
            ) from exc

        if not response.ok:
            raise ClaimProviderError(
                f"인증 중계 서버 응답 오류: HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise ClaimProviderError(
                "인증 중계 서버가 올바른 응답을 반환하지 않았습니다."
            ) from exc
        if not isinstance(data, dict):
            raise ClaimProviderError(
                "인증 중계 서버 응답 형식을 확인해주세요."
            )
        error_code = str(data.get("ErrorCode", "0") or "0").strip()
        if error_code not in {"", "0"}:
            raise ClaimProviderError(_response_error(data))
        return data

    def request_hometax_kakao(
        self,
        *,
        birth_date: str,
        user_name: str,
        cellphone: str,
    ) -> dict[str, str]:
        response = self._post(
            self.config.hometax_host,
            HOMETAX_SIMPLE_AUTH_REQUEST,
            {
                "BirthDate": birth_date,
                "PrivateAuthType": "0",
                "UserName": user_name,
                "UserCellphoneNumber": cellphone,
            },
            (
                "BirthDate",
                "UserName",
                "UserCellphoneNumber",
            ),
        )
        return extract_session_reference(response)

    def request_comwel_kakao(
        self,
        *,
        identity_number: str,
        user_name: str,
        cellphone: str,
    ) -> dict[str, str]:
        response = self._post(
            self.config.comwel_host,
            COMWEL_SIMPLE_AUTH_REQUEST,
            {
                "IdentityNumber": identity_number,
                "PrivateAuthType": "0",
                "UserName": user_name,
                "UserCellphoneNumber": cellphone,
            },
            (
                "IdentityNumber",
                "UserName",
                "UserCellphoneNumber",
            ),
        )
        return extract_session_reference(response)

    def check_hometax_kakao(
        self,
        *,
        birth_date: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
    ) -> bool:
        auth = {
            "BirthDate": birth_date,
            "PrivateAuthType": "0",
            "UserName": user_name,
            "UserCellphoneNumber": cellphone,
            **{key: session.get(key, "") for key in SESSION_FIELDS},
        }
        response = self._post(
            self.config.hometax_host,
            HOMETAX_SIMPLE_AUTH_CHECK,
            {"Auth": auth},
            tuple(
                f"Auth.{key}"
                for key in (
                    "BirthDate",
                    "UserName",
                    "UserCellphoneNumber",
                )
            ),
        )
        return _boolean_result(response)

    def check_comwel_kakao(
        self,
        *,
        identity_number: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
    ) -> bool:
        auth = {
            "IdentityNumber": identity_number,
            "PrivateAuthType": "0",
            "UserName": user_name,
            "UserCellphoneNumber": cellphone,
            **{key: session.get(key, "") for key in SESSION_FIELDS},
        }
        response = self._post(
            self.config.comwel_host,
            COMWEL_SIMPLE_AUTH_CHECK,
            {
                "Auth": auth,
                "UserGroupFlag": "1",
                "IndividualFlag": "1",
            },
            tuple(
                f"Auth.{key}"
                for key in (
                    "IdentityNumber",
                    "UserName",
                    "UserCellphoneNumber",
                )
            ),
        )
        return _boolean_result(response)
