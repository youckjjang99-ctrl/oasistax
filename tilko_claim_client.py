from __future__ import annotations

import base64
import binascii
import json
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
HOMETAX_BUSINESS_INFO = "/api/v2.0/HometaxSimpleAuth/MyBizInfo"
HOMETAX_TAX_PAYMENT_CERTIFICATE = (
    "/api/v2.0/HometaxSimpleAuth/UTERDAAA04"
)
HOMETAX_BUSINESS_REGISTRATION_CERTIFICATE = (
    "/api/v2.0/HometaxSimpleAuth/UTEABGAA21"
)
COMWEL_SIMPLE_AUTH_REQUEST = (
    "/api/v2.0/KcomwelSimpleAuth/SimpleAuthRequest"
)
COMWEL_SIMPLE_AUTH_CHECK = "/api/v2.0/KcomwelSimpleAuth/LoginCheck"
COMWEL_TOTAL_REMUNERATION = (
    "/api/v2.0/KcomwelSimpleAuth/SelectBosuJeopsuList"
)
COMWEL_MANAGEMENT_NUMBERS = "/api/v2.0/KcomwelSimpleAuth/MyBizInfo"
COMWEL_WORKPLACE_RATE = "/api/v2.0/KcomwelSimpleAuth/T100110021005"
SESSION_FIELDS = ("Token", "CxId", "TxId", "ReqTxId")
TRANSIENT_PROVIDER_ERROR_CODES = frozenset({"OACX_NO_USER"})
MAX_COLLECTED_DOCUMENT_BYTES = 20 * 1024 * 1024


def _safe_provider_code(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if not raw or raw == "0":
        return None
    safe = "".join(
        character
        if (
            (character.isascii() and character.isalnum())
            or character in "_-"
        )
        else "_"
        for character in raw
    )
    safe = "_".join(part for part in safe.split("_") if part)
    return safe[:80] or None


class ClaimProviderError(RuntimeError):
    """A provider error whose text is safe to show in the app."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
    ):
        super().__init__(message)
        self.error_code = _safe_provider_code(error_code)

    @property
    def is_transient(self) -> bool:
        return self.error_code in TRANSIENT_PROVIDER_ERROR_CODES

    def has_error_code(self, error_code: str) -> bool:
        expected = _safe_provider_code(error_code)
        return bool(expected and self.error_code == expected)


def is_transient_provider_error(error: BaseException) -> bool:
    return bool(
        isinstance(error, ClaimProviderError)
        and error.is_transient
    )


@dataclass(frozen=True)
class CollectedClaimDocument:
    content: bytes
    file_name: str
    content_type: str
    provider_reference: str
    facts: dict[str, Any]


@dataclass(frozen=True)
class HometaxBusinessCandidate:
    business_number: str
    business_name: str = ""
    business_status: str = ""


@dataclass(frozen=True)
class HometaxBusinessDiscovery:
    document: CollectedClaimDocument
    candidates: tuple[HometaxBusinessCandidate, ...]


_HOMETAX_BUSINESS_NUMBER_FIELDS = (
    "txprDscmNo",
    "BusinessNumber",
)
_HOMETAX_BUSINESS_NAME_FIELDS = (
    "txprNm",
    "bmanNm",
    "sanghoNm",
    "BusinessName",
    "TradeName",
)
_HOMETAX_BUSINESS_STATUS_FIELDS = (
    "txprStatNm",
    "bmanSttsNm",
    "BusinessStatus",
    "StatusName",
)


def _hometax_business_number(value: Any) -> str:
    if isinstance(value, (bool, dict, list, tuple, set)):
        return ""
    compact = (
        str(value or "")
        .strip()
        .replace("-", "")
        .replace(" ", "")
    )
    if len(compact) != 10 or not compact.isascii() or not compact.isdigit():
        return ""
    return compact


def _safe_hometax_business_metadata(value: Any) -> str:
    if isinstance(value, (dict, list, tuple, set)):
        return ""
    text = " ".join(
        "".join(
            character if character.isprintable() else " "
            for character in str(value or "")
        ).split()
    )
    if not text or _looks_like_private_identifier(text):
        return ""
    return text[:120]


def _first_exact_mapping_value(
    mapping: dict[str, Any],
    field_names: Iterable[str],
) -> str:
    for field_name in field_names:
        if field_name not in mapping:
            continue
        value = _safe_hometax_business_metadata(mapping[field_name])
        if value:
            return value
    return ""


def _hometax_business_candidates(
    response_data: dict[str, Any],
) -> tuple[HometaxBusinessCandidate, ...]:
    result = response_data.get("Result")
    if not isinstance(result, (dict, list)):
        result = response_data.get("ResultData")
    if not isinstance(result, (dict, list)):
        raise ClaimProviderError(
            "홈택스 사업자정보 응답에 결과 데이터가 없습니다."
        )

    candidates_by_number: dict[str, HometaxBusinessCandidate] = {}
    for mapping in _iter_response_mappings(result):
        business_number = ""
        for field_name in _HOMETAX_BUSINESS_NUMBER_FIELDS:
            if field_name not in mapping:
                continue
            business_number = _hometax_business_number(mapping[field_name])
            if business_number:
                break
        if not business_number:
            continue

        business_name = _first_exact_mapping_value(
            mapping,
            _HOMETAX_BUSINESS_NAME_FIELDS,
        )
        business_status = _first_exact_mapping_value(
            mapping,
            _HOMETAX_BUSINESS_STATUS_FIELDS,
        )
        existing = candidates_by_number.get(business_number)
        if existing is not None:
            business_name = existing.business_name or business_name
            business_status = existing.business_status or business_status
        candidates_by_number[business_number] = HometaxBusinessCandidate(
            business_number=business_number,
            business_name=business_name,
            business_status=business_status,
        )
    return tuple(candidates_by_number.values())


def _masked_hometax_business_number(business_number: str) -> str:
    return f"{business_number[:3]}-**-*****"


def _hometax_business_discovery_document(
    response_data: dict[str, Any],
    candidates: tuple[HometaxBusinessCandidate, ...],
) -> CollectedClaimDocument:
    safe_businesses = [
        {
            "business_number_masked": _masked_hometax_business_number(
                candidate.business_number
            ),
            "business_name": candidate.business_name,
            "business_status": candidate.business_status,
        }
        for candidate in candidates
    ]
    content = json.dumps(
        {"businesses": safe_businesses},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return CollectedClaimDocument(
        content=content,
        file_name="hometax-business-registration-list.json",
        content_type="application/json",
        provider_reference=str(
            response_data.get("ApiTxKey", "") or ""
        ).strip(),
        facts={"record_count": len(candidates)},
    )


def _safe_document_name(
    raw_name: Any,
    *,
    fallback_stem: str,
    extension: str,
) -> str:
    requested_extension = f".{str(extension or '').lstrip('.').lower()}"
    basename = os.path.basename(
        str(raw_name or "").replace("\\", "/")
    )
    safe = "".join(
        character
        for character in basename
        if character.isalnum() or character in "._-() "
    ).strip(" .")
    if not safe:
        safe = fallback_stem
    current_extension = os.path.splitext(safe)[1].lower()
    if current_extension != requested_extension:
        safe = f"{os.path.splitext(safe)[0] or fallback_stem}{requested_extension}"
    if len(safe) > 160:
        stem = os.path.splitext(safe)[0][: 160 - len(requested_extension)]
        safe = f"{stem.rstrip(' ._-') or fallback_stem}{requested_extension}"
    return safe


def _iter_response_mappings(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_response_mappings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_response_mappings(nested)


def _response_result(response_data: dict[str, Any]) -> Any:
    for key in ("Result", "ResultData"):
        candidate = response_data.get(key)
        if isinstance(candidate, (dict, list)):
            return candidate
    for key in ("Result", "ResultData"):
        if response_data.get(key) is not None:
            return response_data[key]
    raise ClaimProviderError(
        "근로복지공단 문서 응답에 결과 데이터가 없습니다."
    )


def _first_response_value(value: Any, field_name: str) -> str:
    expected = field_name.casefold()
    for mapping in _iter_response_mappings(value):
        for key, candidate in mapping.items():
            if str(key).casefold() != expected:
                continue
            text = str(candidate or "").strip()
            if text:
                return text
    return ""


def _decode_provider_file(
    encoded_value: str,
    *,
    document_label: str,
) -> bytes:
    try:
        content = base64.b64decode(encoded_value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ClaimProviderError(
            f"{document_label} 파일 응답을 확인하지 못했습니다."
        ) from exc
    if not content:
        raise ClaimProviderError(f"{document_label} 파일이 비어 있습니다.")
    if len(content) > MAX_COLLECTED_DOCUMENT_BYTES:
        raise ClaimProviderError(
            f"{document_label} 파일이 허용 크기를 초과했습니다."
        )
    return content


def _structured_record_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        return 0
    nested_lists = [
        candidate
        for candidate in value.values()
        if isinstance(candidate, list)
    ]
    if nested_lists:
        return max(len(candidate) for candidate in nested_lists)
    return 1 if value else 0


def _safe_management_number(value: Any) -> str:
    return "".join(
        character
        for character in str(value or "").strip()
        if (
            character.isascii()
            and (character.isalnum() or character == "-")
        )
    )[:40]


def _management_numbers(value: Any) -> list[str]:
    found: list[str] = []
    for mapping in _iter_response_mappings(value):
        for key, candidate in mapping.items():
            if str(key).casefold() != "gwanrino":
                continue
            management_number = _safe_management_number(candidate)
            if management_number and management_number not in found:
                found.append(management_number)
    return found


_PRIVATE_JSON_REDACTED_KEYS = frozenset(
    {
        "birthdate",
        "cellphone",
        "identitynumber",
        "mobileno",
        "phone",
        "residentregistrationnumber",
        "resno",
        "ssn",
        "telno",
        "usercellphonenumber",
        "username",
    }
)
_PRIVATE_JSON_REDACTED_KEY_FRAGMENTS = (
    "birth",
    "cellphone",
    "ceoname",
    "daepyoname",
    "handphone",
    "hpno",
    "identityno",
    "identitynumber",
    "jumin",
    "mobileno",
    "mobilephone",
    "phoneno",
    "representativename",
    "residentregistration",
    "saupjuname",
    "socialsecurity",
    "telno",
    "username",
)


def _looks_like_private_identifier(value: Any) -> bool:
    text = str(value or "").strip()
    digits = "".join(character for character in text if character.isdigit())
    if len(digits) == 13:
        return True
    return (
        len(digits) in {10, 11}
        and digits.startswith("010")
    )


def _redact_private_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, candidate in value.items():
            normalized = "".join(
                character
                for character in str(key).casefold()
                if character.isalnum()
            )
            if (
                normalized in _PRIVATE_JSON_REDACTED_KEYS
                or any(
                    fragment in normalized
                    for fragment in _PRIVATE_JSON_REDACTED_KEY_FRAGMENTS
                )
            ):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_private_json(candidate)
        return redacted
    if isinstance(value, list):
        return [_redact_private_json(candidate) for candidate in value]
    if _looks_like_private_identifier(value):
        return "[REDACTED]"
    return value


def _document_year(value: Any) -> str:
    year = str(value or "").strip()
    if len(year) != 4 or not year.isdigit():
        raise ClaimProviderError("조회 연도는 4자리 숫자로 입력해주세요.")
    return year


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


def _response_error_code(response_data: dict[str, Any]) -> str | None:
    target_code = _safe_provider_code(response_data.get("TargetCode"))
    error_code = _safe_provider_code(response_data.get("ErrorCode"))
    return target_code or error_code


def _boolean_result(response_data: dict[str, Any]) -> bool:
    result = response_data.get("Result")
    if result is True:
        return True
    if result is False:
        return False
    raise ClaimProviderError("인증 완료 응답 형식을 확인해주세요.")


def _collected_pdf(
    response_data: dict[str, Any],
    *,
    fallback_name: str,
) -> CollectedClaimDocument:
    raw_result = response_data.get("Result")
    candidates = (
        raw_result
        if isinstance(raw_result, list)
        else [raw_result]
        if isinstance(raw_result, dict)
        else []
    )
    selected = next(
        (
            row
            for row in candidates
            if isinstance(row, dict)
            and str(row.get("PdfData", "") or "").strip()
        ),
        None,
    )
    if selected is None and str(
        response_data.get("PdfData", "") or ""
    ).strip():
        selected = response_data
    if selected is None:
        issued = next(
            (
                str(row.get("Issued", "") or "").strip()
                for row in candidates
                if isinstance(row, dict)
                and str(row.get("Issued", "") or "").strip()
            ),
            "",
        )
        suffix = f" ({issued})" if issued else ""
        raise ClaimProviderError(
            f"기관에서 발급된 PDF를 받지 못했습니다{suffix}."
        )

    try:
        content = base64.b64decode(
            str(selected.get("PdfData", "")).strip(),
            validate=True,
        )
    except (ValueError, binascii.Error) as exc:
        raise ClaimProviderError(
            "기관 PDF 응답을 확인하지 못했습니다."
        ) from exc
    if not content.startswith(b"%PDF-"):
        raise ClaimProviderError("기관 응답이 PDF 형식이 아닙니다.")
    if len(content) > MAX_COLLECTED_DOCUMENT_BYTES:
        raise ClaimProviderError("기관 PDF가 허용 크기를 초과했습니다.")

    raw_name = _first_response_value(response_data, "FileName")
    safe_name = _safe_document_name(
        raw_name,
        fallback_stem=fallback_name,
        extension="pdf",
    )
    provider_reference = str(
        selected.get("CerCvaIsnNo")
        or selected.get("CvaId")
        or response_data.get("ApiTxKey")
        or ""
    ).strip()
    return CollectedClaimDocument(
        content=content,
        file_name=safe_name,
        content_type="application/pdf",
        provider_reference=provider_reference,
        facts={
            "issued": _first_response_value(response_data, "Issued"),
            "provider_status": _first_response_value(
                response_data,
                "CvaDcumIsnStatCdNm",
            ),
        },
    )


def _collected_excel(
    response_data: dict[str, Any],
    *,
    fallback_name: str,
    facts: dict[str, Any],
) -> CollectedClaimDocument:
    encoded = _first_response_value(response_data, "ExcelData")
    if not encoded:
        raise ClaimProviderError(
            "근로복지공단 응답에서 엑셀 파일을 받지 못했습니다."
        )
    content = _decode_provider_file(
        encoded,
        document_label="근로복지공단 엑셀",
    )
    if content.startswith(b"PK\x03\x04"):
        extension = "xlsx"
        content_type = (
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        )
    elif content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        extension = "xls"
        content_type = "application/vnd.ms-excel"
    else:
        raise ClaimProviderError(
            "근로복지공단 응답이 엑셀 파일 형식이 아닙니다."
        )
    raw_name = _first_response_value(response_data, "FileName")
    result = _response_result(response_data)
    return CollectedClaimDocument(
        content=content,
        file_name=_safe_document_name(
            raw_name,
            fallback_stem=fallback_name,
            extension=extension,
        ),
        content_type=content_type,
        provider_reference=str(
            response_data.get("ApiTxKey", "") or ""
        ).strip(),
        facts={
            **facts,
            "record_count": _structured_record_count(result),
            "file_format": extension,
        },
    )


def _collected_private_json(
    response_data: dict[str, Any],
    *,
    fallback_name: str,
    facts: dict[str, Any],
) -> CollectedClaimDocument:
    result = _response_result(response_data)
    if not isinstance(result, (dict, list)):
        raise ClaimProviderError(
            "근로복지공단 사업장 응답 형식을 확인해주세요."
        )
    redacted = _redact_private_json(result)
    content = json.dumps(
        redacted,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(content) > MAX_COLLECTED_DOCUMENT_BYTES:
        raise ClaimProviderError(
            "근로복지공단 사업장 응답이 허용 크기를 초과했습니다."
        )
    return CollectedClaimDocument(
        content=content,
        file_name=_safe_document_name(
            "",
            fallback_stem=fallback_name,
            extension="json",
        ),
        content_type="application/json",
        provider_reference=str(
            response_data.get("ApiTxKey", "") or ""
        ).strip(),
        facts={
            **facts,
            "record_count": _structured_record_count(result),
        },
    )


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
            raise ClaimProviderError(
                _response_error(data),
                error_code=_response_error_code(data),
            )
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

    def _comwel_auth(
        self,
        *,
        identity_number: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
    ) -> dict[str, str]:
        return {
            "IdentityNumber": identity_number,
            "PrivateAuthType": "0",
            "UserName": user_name,
            "UserCellphoneNumber": cellphone,
            **{key: session.get(key, "") for key in SESSION_FIELDS},
        }

    def check_comwel_kakao(
        self,
        *,
        identity_number: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
    ) -> bool:
        response = self._post(
            self.config.comwel_host,
            COMWEL_SIMPLE_AUTH_CHECK,
            {
                "Auth": self._comwel_auth(
                    identity_number=identity_number,
                    user_name=user_name,
                    cellphone=cellphone,
                    session=session,
                ),
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

    def collect_comwel_total_remuneration(
        self,
        *,
        year: Any,
        identity_number: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
        business_number: str = "",
        management_number: str = "",
    ) -> CollectedClaimDocument:
        selected_year = _document_year(year)
        payload: dict[str, Any] = {
            "Auth": self._comwel_auth(
                identity_number=identity_number,
                user_name=user_name,
                cellphone=cellphone,
                session=session,
            ),
            "UserGroupFlag": "1",
            "IndividualFlag": "1",
            "BoheomYear": selected_year,
        }
        encrypted_paths = [
            "Auth.IdentityNumber",
            "Auth.UserName",
            "Auth.UserCellphoneNumber",
        ]
        selected_business_number = str(business_number or "").strip()
        selected_management_number = _safe_management_number(
            management_number
        )
        if selected_business_number:
            payload["BusinessNumber"] = selected_business_number
            encrypted_paths.append("BusinessNumber")
        if selected_management_number:
            payload["GwanriNo"] = selected_management_number
        response = self._post(
            self.config.comwel_host,
            COMWEL_TOTAL_REMUNERATION,
            payload,
            tuple(encrypted_paths),
        )
        facts: dict[str, Any] = {"year": selected_year}
        if selected_management_number:
            facts["management_numbers"] = [selected_management_number]
        return _collected_excel(
            response,
            fallback_name=f"comwel-total-remuneration-{selected_year}",
            facts=facts,
        )

    def collect_comwel_management_numbers(
        self,
        *,
        identity_number: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
        business_number: str,
    ) -> CollectedClaimDocument:
        selected_business_number = str(business_number or "").strip()
        if not selected_business_number:
            raise ClaimProviderError(
                "사업장관리번호 조회에는 사업자등록번호가 필요합니다."
            )
        payload = {
            "Auth": self._comwel_auth(
                identity_number=identity_number,
                user_name=user_name,
                cellphone=cellphone,
                session=session,
            ),
            "UserGroupFlag": "1",
            "IndividualFlag": "1",
            "BusinessNumber": selected_business_number,
        }
        response = self._post(
            self.config.comwel_host,
            COMWEL_MANAGEMENT_NUMBERS,
            payload,
            (
                "Auth.IdentityNumber",
                "Auth.UserName",
                "Auth.UserCellphoneNumber",
                "BusinessNumber",
            ),
        )
        result = _response_result(response)
        management_numbers = _management_numbers(result)
        return _collected_private_json(
            response,
            fallback_name="comwel-management-numbers",
            facts={"management_numbers": management_numbers},
        )

    def collect_comwel_workplace_rate(
        self,
        *,
        year: Any,
        identity_number: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
        management_number: str = "",
    ) -> CollectedClaimDocument:
        selected_year = _document_year(year)
        selected_management_number = _safe_management_number(
            management_number
        )
        payload: dict[str, Any] = {
            "Auth": self._comwel_auth(
                identity_number=identity_number,
                user_name=user_name,
                cellphone=cellphone,
                session=session,
            ),
            "UserGroupFlag": "1",
            "IndividualFlag": "1",
            "Year": selected_year,
        }
        if selected_management_number:
            payload["GwanriNo"] = selected_management_number
        response = self._post(
            self.config.comwel_host,
            COMWEL_WORKPLACE_RATE,
            payload,
            (
                "Auth.IdentityNumber",
                "Auth.UserName",
                "Auth.UserCellphoneNumber",
            ),
        )
        document = _collected_pdf(
            response,
            fallback_name=f"comwel-workplace-rate-{selected_year}",
        )
        facts = {
            "year": selected_year,
            **document.facts,
        }
        if selected_management_number:
            facts["management_numbers"] = [selected_management_number]
        return CollectedClaimDocument(
            content=document.content,
            file_name=document.file_name,
            content_type=document.content_type,
            provider_reference=document.provider_reference,
            facts=facts,
        )

    def _hometax_auth(
        self,
        *,
        birth_date: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
    ) -> dict[str, str]:
        return {
            "BirthDate": birth_date,
            "PrivateAuthType": "0",
            "UserName": user_name,
            "UserCellphoneNumber": cellphone,
            **{key: session.get(key, "") for key in SESSION_FIELDS},
        }

    def discover_hometax_businesses(
        self,
        *,
        birth_date: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
    ) -> HometaxBusinessDiscovery:
        response = self._post(
            self.config.hometax_host,
            HOMETAX_BUSINESS_INFO,
            {
                "Auth": self._hometax_auth(
                    birth_date=birth_date,
                    user_name=user_name,
                    cellphone=cellphone,
                    session=session,
                )
            },
            tuple(
                f"Auth.{key}"
                for key in (
                    "BirthDate",
                    "UserName",
                    "UserCellphoneNumber",
                )
            ),
        )
        candidates = _hometax_business_candidates(response)
        return HometaxBusinessDiscovery(
            document=_hometax_business_discovery_document(
                response,
                candidates,
            ),
            candidates=candidates,
        )

    def collect_hometax_business_registration_certificate(
        self,
        *,
        birth_date: str,
        user_name: str,
        cellphone: str,
        business_number: str,
        session: dict[str, str],
    ) -> CollectedClaimDocument:
        payload = {
            "Auth": self._hometax_auth(
                birth_date=birth_date,
                user_name=user_name,
                cellphone=cellphone,
                session=session,
            ),
            "BusinessNumber": business_number,
            "EnglCvaAplnYn": "N",
            "ResnoOpYn": "N",
            "IssueType": "99",
            "Organization": "99",
        }
        response = self._post(
            self.config.hometax_host,
            HOMETAX_BUSINESS_REGISTRATION_CERTIFICATE,
            payload,
            (
                "Auth.BirthDate",
                "Auth.UserName",
                "Auth.UserCellphoneNumber",
                "BusinessNumber",
            ),
        )
        return _collected_pdf(
            response,
            fallback_name="business-registration-certificate",
        )

    def collect_hometax_tax_payment_certificate(
        self,
        *,
        birth_date: str,
        user_name: str,
        cellphone: str,
        session: dict[str, str],
    ) -> CollectedClaimDocument:
        payload = {
            "Auth": self._hometax_auth(
                birth_date=birth_date,
                user_name=user_name,
                cellphone=cellphone,
                session=session,
            ),
            "IssueType": "B0007",
            "Organization": "99",
            "ResnoOpYn": "N",
        }
        response = self._post(
            self.config.hometax_host,
            HOMETAX_TAX_PAYMENT_CERTIFICATE,
            payload,
            (
                "Auth.BirthDate",
                "Auth.UserName",
                "Auth.UserCellphoneNumber",
            ),
        )
        return _collected_pdf(
            response,
            fallback_name="tax-payment-certificate",
        )
