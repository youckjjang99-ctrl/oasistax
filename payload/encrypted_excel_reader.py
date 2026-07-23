from __future__ import annotations

import io
import os
from typing import Any


DEFAULT_EMPLOYMENT_EXCEL_PASSWORD = "1111"


class EncryptedExcelError(ValueError):
    """암호화된 Excel 파일을 안전하게 열 수 없을 때 발생합니다."""


def _password() -> str:
    return (
        os.environ.get("OASIS_EMPLOYMENT_EXCEL_PASSWORD", "").strip()
        or DEFAULT_EMPLOYMENT_EXCEL_PASSWORD
    )


def is_encrypted_office(data: bytes) -> bool:
    """Office 암호화 컨테이너인지 확인합니다."""
    if not data:
        return False

    # 암호화된 OOXML은 OLE 컨테이너 안에 두 스트림을 포함합니다.
    if data.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        if b"EncryptedPackage" in data and b"EncryptionInfo" in data:
            return True

    try:
        import msoffcrypto

        office_file = msoffcrypto.OfficeFile(io.BytesIO(data))
        return bool(office_file.is_encrypted())
    except Exception:
        return False


def decrypt_excel_bytes(
    data: bytes,
    password: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """필요한 경우 Excel을 메모리에서 복호화하고 원본/복호화 바이트를 반환합니다."""
    if not data:
        raise EncryptedExcelError("업로드한 Excel 파일이 비어 있습니다.")

    if not is_encrypted_office(data):
        return data, {
            "encrypted": False,
            "decrypted": False,
            "storage": "memory_only",
        }

    try:
        import msoffcrypto
    except ImportError as exc:
        raise EncryptedExcelError(
            "암호화 Excel 처리 모듈이 설치되지 않았습니다. "
            "requirements.txt 설치 후 다시 시도해주세요."
        ) from exc

    try:
        office_file = msoffcrypto.OfficeFile(io.BytesIO(data))
        office_file.load_key(password=password or _password())
        output = io.BytesIO()
        office_file.decrypt(output)
        decrypted = output.getvalue()
    except Exception as exc:
        raise EncryptedExcelError(
            "암호화된 고용정보 Excel을 열지 못했습니다. "
            "파일 손상 여부 또는 문서 암호를 확인해주세요."
        ) from exc

    if not decrypted.startswith(b"PK\x03\x04"):
        raise EncryptedExcelError(
            "암호 해제 후 Excel 통합문서 형식을 확인하지 못했습니다."
        )

    return decrypted, {
        "encrypted": True,
        "decrypted": True,
        "storage": "memory_only",
    }
