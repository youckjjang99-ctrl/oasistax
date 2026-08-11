from __future__ import annotations

import base64
import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

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
    # 2020-10 이후 신규·변경 번호는 성별 뒤 6자리가 임의번호이므로
    # 종전의 지역·순번·검증번호 규칙을 강제하면 정상 번호도 거절된다.
    return True


def validate_application(values: dict[str, Any]) -> dict[str, str]:
    clean = {
        key: str(values.get(key, "") or "").strip()
        for key in (
            "name",
            "birth_date",
            "contact",
            "email",
            "bank_name",
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
        "bank_name": "은행명",
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

    if not 2 <= len(clean["bank_name"]) <= 40 or re.search(
        r"[\x00-\x1f\x7f]",
        clean["bank_name"],
    ):
        raise ClaimSalesApplicationError("은행명을 정확히 입력해주세요.")
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
        self.cipher = cipher

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
        cipher = self.cipher or ClaimSalesApplicationCipher.from_environment()
        ciphertext = cipher.encrypt(clean)
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

    def list_for_user(self, owner_user_id: str) -> list[dict[str, Any]]:
        owner = str(owner_user_id or "").strip().lower()
        if not owner:
            return []
        return self.db.select(
            TABLE_CLAIM_SALES_APPLICATIONS,
            filters={"owner_user_id": owner},
            columns=(
                "id,status,management_homepage_url,sales_code,"
                "sales_homepage_url,reviewed_at,created_at"
            ),
            order="created_at.desc",
            limit=100,
        )

    def list_for_admin(
        self,
        *,
        current_user_id: str,
        is_admin_user: bool,
    ) -> list[dict[str, Any]]:
        if not is_admin_user or not str(current_user_id or "").strip():
            raise ClaimSalesApplicationError("관리자만 신청결과를 관리할 수 있습니다.")
        return self.db.select(
            TABLE_CLAIM_SALES_APPLICATIONS,
            columns=(
                "id,owner_user_id,status,management_homepage_url,sales_code,"
                "sales_homepage_url,reviewed_at,created_at"
            ),
            order="created_at.desc",
            limit=500,
        )

    def save_result(
        self,
        *,
        application_id: str,
        current_user_id: str,
        is_admin_user: bool,
        management_homepage_url: str,
        sales_code: str,
        sales_homepage_url: str,
    ) -> dict[str, Any]:
        if not is_admin_user:
            raise ClaimSalesApplicationError("관리자만 신청결과를 입력할 수 있습니다.")
        try:
            clean_id = str(uuid.UUID(str(application_id or "")))
        except ValueError as exc:
            raise ClaimSalesApplicationError("수정할 신청 건을 확인해주세요.") from exc
        admin_id = str(current_user_id or "").strip().lower()
        if not admin_id:
            raise ClaimSalesApplicationError("관리자 로그인을 확인해주세요.")
        management_url = _normalize_result_url(management_homepage_url)
        sales_url = _normalize_result_url(sales_homepage_url)
        code = str(sales_code or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", code):
            raise ClaimSalesApplicationError(
                "영업코드는 영문, 숫자, 밑줄, 하이픈으로 입력해주세요."
            )
        now = datetime.now(timezone.utc).isoformat()
        rows = self.db.update(
            TABLE_CLAIM_SALES_APPLICATIONS,
            {"id": clean_id},
            {
                "management_homepage_url": management_url,
                "sales_code": code,
                "sales_homepage_url": sales_url,
                "status": "approved",
                "reviewed_by_user_id": admin_id,
                "reviewed_at": now,
                "updated_at": now,
            },
        )
        if not rows:
            raise ClaimSalesApplicationError("수정할 신청 건을 찾지 못했습니다.")
        return dict(rows[0])


def _normalize_result_url(value: Any) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ClaimSalesApplicationError("홈페이지 주소를 입력해주세요.")
    if "://" not in clean:
        clean = f"https://{clean}"
    parsed = urlparse(clean)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ClaimSalesApplicationError("홈페이지 주소 형식을 확인해주세요.")
    if len(clean) > 2048:
        raise ClaimSalesApplicationError("홈페이지 주소가 너무 깁니다.")
    return clean


def _application_label(row: dict[str, Any], include_owner: bool = False) -> str:
    created = str(row.get("created_at") or "").replace("T", " ")[:16]
    status = str(row.get("status") or "submitted")
    owner = str(row.get("owner_user_id") or "")
    prefix = f"{owner} · " if include_owner else ""
    return f"{prefix}{created or '신청일 미확인'} · {status}"


def _render_result_fields(row: dict[str, Any]) -> None:
    import streamlit as st

    left, middle, right = st.columns(3)
    left.text_input(
        "관리 홈페이지 주소",
        value=str(row.get("management_homepage_url") or ""),
        disabled=True,
        key=f"claim_result_management_{row.get('id')}",
    )
    middle.text_input(
        "영업코드",
        value=str(row.get("sales_code") or ""),
        disabled=True,
        key=f"claim_result_code_{row.get('id')}",
    )
    right.text_input(
        "영업용 홈페이지 주소",
        value=str(row.get("sales_homepage_url") or ""),
        disabled=True,
        key=f"claim_result_sales_{row.get('id')}",
    )


def _render_application_result(current_user_id: str) -> None:
    import streamlit as st
    from auth import is_admin

    repository = ClaimSalesApplicationRepository()
    admin_user = bool(is_admin(current_user_id))
    try:
        rows = (
            repository.list_for_admin(
                current_user_id=current_user_id,
                is_admin_user=True,
            )
            if admin_user
            else repository.list_for_user(current_user_id)
        )
    except Exception:
        st.error("신청결과를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.")
        return
    if not rows:
        st.info("확인할 영업신청이 없습니다.")
        return

    selected_id = st.selectbox(
        "신청 건 선택",
        [str(row.get("id") or "") for row in rows],
        format_func=lambda value: _application_label(
            next(row for row in rows if str(row.get("id") or "") == value),
            include_owner=admin_user,
        ),
        key="claim_sales_result_application_v1",
    )
    selected = next(
        row for row in rows if str(row.get("id") or "") == selected_id
    )
    if not admin_user:
        _render_result_fields(selected)
        if not any(
            str(selected.get(key) or "").strip()
            for key in (
                "management_homepage_url",
                "sales_code",
                "sales_homepage_url",
            )
        ):
            st.info("관리자가 신청결과를 준비 중입니다.")
        return

    st.caption("관리자 전용 입력 화면입니다.")
    with st.form(f"claim_sales_result_admin_{selected_id}"):
        management_url = st.text_input(
            "관리 홈페이지 주소",
            value=str(selected.get("management_homepage_url") or ""),
        )
        sales_code = st.text_input(
            "영업코드",
            value=str(selected.get("sales_code") or ""),
            max_chars=80,
        )
        sales_url = st.text_input(
            "영업용 홈페이지 주소",
            value=str(selected.get("sales_homepage_url") or ""),
        )
        save = st.form_submit_button(
            "신청결과 저장",
            type="primary",
            use_container_width=True,
        )
    if not save:
        return
    try:
        repository.save_result(
            application_id=selected_id,
            current_user_id=current_user_id,
            is_admin_user=True,
            management_homepage_url=management_url,
            sales_code=sales_code,
            sales_homepage_url=sales_url,
        )
    except ClaimSalesApplicationError as exc:
        st.error(str(exc))
    except Exception:
        st.error("신청결과를 저장하지 못했습니다. 잠시 후 다시 시도해주세요.")
    else:
        st.success("신청결과를 저장했습니다.")


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
        "주민등록번호와 계좌정보는 직원등록·4대보험·세무 신고를 위한 "
        "목적으로만 수집하며, 원문을 별도 열에 남기지 않고 암호화해 저장합니다."
    )

    application_tab, result_tab = st.tabs(["영업신청", "신청결과"])
    with result_tab:
        _render_application_result(current_user_id)

    with application_tab, st.form(
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
            bank_name = st.text_input(
                "은행명",
                placeholder="예: 국민은행",
                max_chars=40,
            )
            account_number = st.text_input(
                "계좌번호",
                type="password",
                placeholder="계좌번호를 입력해주세요.",
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
            "4대보험·원천징수 등 법정 신고를 위한 "
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
                "bank_name": bank_name,
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
