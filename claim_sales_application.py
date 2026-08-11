from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet

from cloud_db import CloudDatabase


TABLE_CLAIM_SALES_APPLICATIONS = "oasis_claim_sales_applications"
PAYLOAD_KEY_VERSION = "sales-application-v1"
CONSENT_VERSION = "claim-sales-application-2026-08"
RETENTION_DAYS = 90


class ClaimSalesApplicationError(RuntimeError):
    """Safe, user-facing error raised while submitting an application."""


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


def _fernet(secret: str) -> Fernet:
    raw = str(secret or "").strip()
    if len(raw) < 32:
        raise ClaimSalesApplicationError(
            "영업신청 암호화 설정을 확인해주세요."
        )
    key = base64.urlsafe_b64encode(
        hashlib.sha256(raw.encode("utf-8")).digest()
    )
    return Fernet(key)


@dataclass(frozen=True)
class ClaimSalesApplicationCipher:
    cipher: Fernet

    @classmethod
    def from_environment(cls) -> "ClaimSalesApplicationCipher":
        # The dedicated key can be rotated independently. Existing production
        # installs may safely fall back to the already provisioned claim key.
        secret = _secret("SALES_APPLICATION_ENCRYPTION_KEY") or _secret(
            "CLAIM_JOB_ENCRYPTION_KEY"
        )
        return cls(_fernet(secret))

    @classmethod
    def from_secret(cls, secret: str) -> "ClaimSalesApplicationCipher":
        return cls(_fernet(secret))

    def encrypt(self, payload: dict[str, Any]) -> str:
        import json

        raw = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.cipher.encrypt(raw).decode("ascii")

    def decrypt(self, ciphertext: str) -> dict[str, Any]:
        import json

        value = json.loads(
            self.cipher.decrypt(str(ciphertext).encode("ascii")).decode(
                "utf-8"
            )
        )
        if not isinstance(value, dict):
            raise ClaimSalesApplicationError(
                "영업신청 정보를 안전하게 확인하지 못했습니다."
            )
        return value


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _valid_resident_number(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 13 or digits[6] not in "1234":
        return False
    century = 1900 if digits[6] in "12" else 2000
    try:
        datetime.strptime(f"{century + int(digits[:2]):04d}{digits[2:6]}", "%Y%m%d")
    except ValueError:
        return False
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    check_digit = (11 - sum(int(n) * w for n, w in zip(digits[:12], weights))) % 10
    return check_digit == int(digits[-1])


def validate_application(values: dict[str, Any]) -> dict[str, str]:
    clean = {
        key: str(values.get(key, "") or "").strip()
        for key in (
            "name",
            "birth_date",
            "contact",
            "email",
            "account_number",
            "desired_title",
            "desired_admin_id",
            "english_name",
        )
    }
    required_labels = {
        "name": "이름",
        "birth_date": "주민등록번호",
        "contact": "연락처",
        "email": "이메일",
        "account_number": "계좌번호",
        "desired_title": "희망 직함",
        "desired_admin_id": "관리자 페이지 희망 ID",
        "english_name": "영문이름",
    }
    missing = [label for key, label in required_labels.items() if not clean[key]]
    if missing:
        raise ClaimSalesApplicationError(
            f"다음 항목을 입력해주세요: {', '.join(missing)}"
        )

    birth_digits = _digits(clean["birth_date"])
    if not _valid_resident_number(birth_digits):
        raise ClaimSalesApplicationError("주민등록번호를 정확히 입력해주세요.")

    phone_digits = _digits(clean["contact"])
    if not 9 <= len(phone_digits) <= 11:
        raise ClaimSalesApplicationError("연락처를 정확히 입력해주세요.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", clean["email"]):
        raise ClaimSalesApplicationError("이메일 형식을 확인해주세요.")

    account_digits = _digits(clean["account_number"])
    if not 6 <= len(account_digits) <= 20:
        raise ClaimSalesApplicationError("계좌번호를 정확히 입력해주세요.")
    if not re.fullmatch(r"[A-Za-z0-9._-]{4,30}", clean["desired_admin_id"]):
        raise ClaimSalesApplicationError(
            "희망 ID는 영문, 숫자, 마침표, 밑줄, 하이픈으로 4~30자 입력해주세요."
        )
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,79}", clean["english_name"]):
        raise ClaimSalesApplicationError("영문이름을 영문으로 입력해주세요.")

    clean["birth_date"] = birth_digits
    clean["contact"] = phone_digits
    clean["account_number"] = account_digits
    clean["email"] = clean["email"].lower()
    return clean


class ClaimSalesApplicationRepository:
    def __init__(
        self,
        db: CloudDatabase | None = None,
        cipher: ClaimSalesApplicationCipher | None = None,
    ) -> None:
        self.db = db or CloudDatabase()
        self.cipher = cipher or ClaimSalesApplicationCipher.from_environment()

    def submit(
        self,
        *,
        owner_user_id: str,
        values: dict[str, Any],
        consented: bool,
    ) -> dict[str, Any]:
        owner = str(owner_user_id or "").strip().lower()
        if not owner:
            raise ClaimSalesApplicationError(
                "로그인 정보를 확인한 뒤 다시 시도해주세요."
            )
        if not consented:
            raise ClaimSalesApplicationError(
                "개인정보 수집·이용 안내를 확인하고 동의해주세요."
            )
        clean = validate_application(values)
        ciphertext = self.cipher.encrypt(clean)
        now = datetime.now(timezone.utc)
        rows = self.db.insert(
            TABLE_CLAIM_SALES_APPLICATIONS,
            [
                {
                    "owner_user_id": owner,
                    "status": "submitted",
                    "secure_payload_ciphertext": ciphertext,
                    "payload_key_version": PAYLOAD_KEY_VERSION,
                    "consent_version": CONSENT_VERSION,
                    "consented_at": now.isoformat(),
                    "retention_expires_at": (
                        now + timedelta(days=RETENTION_DAYS)
                    ).isoformat(),
                }
            ],
        )
        return dict(rows[0]) if rows else {"status": "submitted"}


def render_claim_sales_application(
    current_user_id: str,
    current_user_name: str,
) -> None:
    import streamlit as st

    st.title("경정청구 영업신청")
    st.caption(
        "경정청구 영업 활동에 필요한 신청정보를 작성해 제출해주세요."
    )
    st.info(
        "주민등록번호와 계좌번호는 직원등록·4대보험·세무 신고를 위한 "
        "목적으로만 수집하며, 원문을 별도 열에 남기지 않고 암호화해 저장합니다."
    )

    with st.form(
        "claim_sales_application_form_v1",
        clear_on_submit=True,
        enter_to_submit=False,
    ):
        left, right = st.columns(2)
        with left:
            name = st.text_input(
                "이름",
                value=str(current_user_name or ""),
                max_chars=80,
            )
            birth_date = st.text_input(
                "주민등록번호",
                type="password",
                placeholder="앞 6자리와 뒤 7자리를 입력해주세요.",
                max_chars=14,
            )
            contact = st.text_input(
                "연락처",
                placeholder="숫자만 입력해도 됩니다.",
                max_chars=20,
            )
            email = st.text_input("이메일", max_chars=200)
        with right:
            account_number = st.text_input(
                "계좌번호",
                type="password",
                placeholder="은행명 없이 계좌번호만 입력해주세요.",
                max_chars=30,
            )
            desired_title = st.text_input("희망 직함", max_chars=80)
            desired_admin_id = st.text_input(
                "관리자 페이지 희망 ID",
                placeholder="영문·숫자 4~30자",
                max_chars=30,
            )
            english_name = st.text_input(
                "영문이름",
                placeholder="예: Gildong Hong",
                max_chars=80,
            )

        consented = st.checkbox(
            "직원등록이 확정되었으며, 4대보험·원천징수 등 법정 신고를 위한 "
            "주민등록번호 및 개인정보의 수집·이용과 암호화 저장을 확인했습니다."
        )
        submitted = st.form_submit_button(
            "영업신청 제출",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return
    try:
        ClaimSalesApplicationRepository().submit(
            owner_user_id=current_user_id,
            values={
                "name": name,
                "birth_date": birth_date,
                "contact": contact,
                "email": email,
                "account_number": account_number,
                "desired_title": desired_title,
                "desired_admin_id": desired_admin_id,
                "english_name": english_name,
            },
            consented=consented,
        )
    except ClaimSalesApplicationError as exc:
        st.error(str(exc))
    except Exception:
        st.error(
            "영업신청을 저장하지 못했습니다. 잠시 후 다시 시도해주세요."
        )
    else:
        st.success("경정청구 영업신청이 제출되었습니다.")


__all__ = [
    "ClaimSalesApplicationCipher",
    "ClaimSalesApplicationError",
    "ClaimSalesApplicationRepository",
    "render_claim_sales_application",
    "validate_application",
]
