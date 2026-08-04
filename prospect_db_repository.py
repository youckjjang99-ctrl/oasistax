from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
from datetime import datetime, timezone
from typing import Any

import requests

from cloud_db import CloudDatabase, get_cloud_config


TABLE_PROSPECTS = "oasis_prospect_companies"
TABLE_CUSTOMERS = "oasis_customers"
TABLE_CONTACTS = "oasis_prospect_contacts"
TABLE_CONTACT_CONTROLS = "oasis_company_kakao_contact_controls"
LEGACY_CONTACT_PHONE_HASH_KEY_ENV = "OASIS_KAKAO_GUIDANCE_PHONE_HASH_KEY"
TABLE_SEARCH_HISTORY = "oasis_prospect_search_history"
TABLE_EMPLOYEE_SNAPSHOTS = "oasis_nps_employee_snapshots"
TABLE_NPS_GROWTH = "oasis_nps_growth_leads"
TABLE_COMWEL_GROWTH = "oasis_comwel_annual_growth"


class GrowthSearchTimeoutError(RuntimeError):
    """Raised when the precomputed growth search exceeds a safe time limit."""


SUPABASE_PROVINCE_NAMES = {
    "11": "서울특별시",
    "26": "부산광역시",
    "27": "대구광역시",
    "28": "인천광역시",
    "29": "광주광역시",
    "30": "대전광역시",
    "31": "울산광역시",
    "36": "세종특별자치시",
    "41": "경기도",
    "51": "강원특별자치도",
    "43": "충청북도",
    "44": "충청남도",
    "52": "전북특별자치도",
    "46": "전라남도",
    "47": "경상북도",
    "48": "경상남도",
    "50": "제주특별자치도",
}
SUPABASE_PROVINCE_CODE_ALIASES = {
    "42": "51",
    "45": "52",
}
SUPABASE_PROVINCE_CODES = {
    name: code for code, name in SUPABASE_PROVINCE_NAMES.items()
}


def _business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return str(value or "").strip()


def _company_address_key(company_name: Any, address: Any) -> str:
    """마스킹 사업자번호를 보완할 사업장 단위 식별값이다."""
    name = re.sub(r"[^0-9a-z가-힣]", "", str(company_name or "").lower())
    place = re.sub(r"[^0-9a-z가-힣]", "", str(address or "").lower())
    if not name or not place:
        return ""
    return f"{name}|{place}"


_REVIEW_EMAIL_PATTERN = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)


def _normalize_review_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00820"):
        digits = "0" + digits[5:]
    elif digits.startswith("0082"):
        digits = "0" + digits[4:]
    elif digits.startswith("820"):
        digits = "0" + digits[3:]
    elif digits.startswith("82"):
        digits = "0" + digits[2:]
    if not re.fullmatch(r"0[0-9]{8,10}", digits):
        return ""
    return digits


def build_review_contact_candidates(
    prospect: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the bounded contact set an operator can approve at save time.

    Approval only promotes public collection results into the canonical
    contact table with ``review_required`` status.  It is deliberately not a
    recipient marketing-consent record.
    """

    analysis = (
        prospect.get("영업분석")
        if isinstance(prospect.get("영업분석"), dict)
        else {}
    )
    candidates: list[dict[str, Any]] = []

    try:
        phone_confidence = int(analysis.get("phone_confidence") or 0)
    except (TypeError, ValueError):
        phone_confidence = 0
    primary_phone = _normalize_review_phone(
        prospect.get("대표전화") or analysis.get("phone")
    )
    for raw_phone in (
        prospect.get("휴대전화"),
        prospect.get("일반전화"),
        primary_phone,
    ):
        phone = _normalize_review_phone(raw_phone)
        if not phone:
            continue
        candidates.append(
            {
                "contact_type": "phone",
                "contact_value": phone,
                "source_url": "",
                "confidence": min(100, max(0, phone_confidence)),
                "is_primary": not primary_phone or phone == primary_phone,
            }
        )

    email = str(prospect.get("이메일") or analysis.get("email") or "").strip()
    if (
        3 <= len(email) <= 254
        and "\r" not in email
        and "\n" not in email
        and _REVIEW_EMAIL_PATTERN.fullmatch(email)
    ):
        candidates.append(
            {
                "contact_type": "email",
                "contact_value": email.casefold(),
                "source_url": "",
                "confidence": 0,
                "is_primary": True,
            }
        )

    instagram_url = str(
        prospect.get("인스타그램URL") or analysis.get("instagram_url") or ""
    ).strip()
    instagram = str(
        prospect.get("인스타그램") or analysis.get("instagram") or ""
    ).strip()
    instagram_value = instagram or instagram_url
    safe_instagram_url = (
        instagram_url
        if re.match(r"^https?://", instagram_url, flags=re.IGNORECASE)
        and len(instagram_url) <= 2000
        else ""
    )
    if instagram_value and len(instagram_value) <= 500:
        candidates.append(
            {
                "contact_type": "instagram",
                "contact_value": instagram_value,
                "source_url": safe_instagram_url,
                "confidence": 0,
                "is_primary": True,
            }
        )

    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        key = (
            str(candidate.get("contact_type") or ""),
            str(candidate.get("contact_value") or ""),
        )
        unique.setdefault(key, candidate)
    return list(unique.values())[:8]


def _recent_opening_source_key(row: dict[str, Any]) -> str:
    """사업자번호가 없어도 중복되지 않는 신규개업 후보 키를 만든다."""
    business_no = re.sub(
        r"[^0-9]",
        "",
        str(row.get("business_no") or ""),
    )
    if len(business_no) == 10:
        # 이미 저장된 후보와의 호환성을 위해 기존 키 형식을 유지한다.
        return f"recent_opening:{business_no}"

    source_type = str(row.get("source_type") or "nps_monthly").strip()
    source_record_key = str(row.get("source_record_key") or "").strip()
    if source_record_key:
        return f"recent_opening:{source_type}:{source_record_key}"

    place_key = _company_address_key(
        row.get("company_name"),
        row.get("address"),
    )
    if not place_key:
        return ""
    place_hash = hashlib.sha256(place_key.encode("utf-8")).hexdigest()
    return f"recent_opening:{source_type}:place:{place_hash}"


def _snapshot_identity(prospect: dict[str, Any]) -> str:
    """동일 사업장을 월별로 비교할 안정적인 식별값을 만든다.

    국민연금 기본조회는 개인사업자 사업자번호를 앞 6자리까지만 제공할
    수 있다. 따라서 번호 일부만으로 식별하면 서로 다른 사업장이 한
    스냅샷 행으로 충돌하므로, 상호·주소 해시를 함께 사용한다.
    """
    business_digits = re.sub(
        r"[^0-9]", "", str(prospect.get("사업자등록번호") or "")
    )
    place_key = _company_address_key(
        prospect.get("사업장명"), prospect.get("주소")
    )
    if not place_key:
        place_key = re.sub(
            r"\s+", "", str(prospect.get("source_key") or "")
        )
    place_hash = hashlib.sha256(place_key.encode("utf-8")).hexdigest()
    business_part = business_digits if business_digits else "unknown"
    return f"business:{business_part}|place:{place_hash}"


def _previous_year_month(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) != 6:
        return ""
    try:
        return f"{int(digits[:4]) - 1:04d}{int(digits[4:]):02d}"
    except ValueError:
        return ""


def load_prior_employee_snapshots(
    prospects: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """이번 조회의 전년 동월 가입자수 스냅샷을 일괄 조회한다."""
    targets: dict[str, set[str]] = {}
    for prospect in prospects:
        target_ym = _previous_year_month(prospect.get("자료생성년월"))
        if target_ym:
            targets.setdefault(target_ym, set()).add(
                _snapshot_identity(prospect)
            )
    if not targets:
        return {}
    config = get_cloud_config()
    if not config.configured:
        return {}
    found: dict[str, dict[str, Any]] = {}
    for target_ym, identities in targets.items():
        values = sorted(identities)
        for start in range(0, len(values), 100):
            group = values[start : start + 100]
            response = requests.get(
                f"{config.url}/rest/v1/{TABLE_EMPLOYEE_SNAPSHOTS}",
                headers=_rest_headers(),
                params={
                    "select": (
                        "snapshot_identity,data_created_ym,employee_count,"
                        "company_name,address"
                    ),
                    "snapshot_identity": f"in.({','.join(group)})",
                    "data_created_ym": f"eq.{target_ym}",
                },
                timeout=max(config.timeout, 30),
            )
            if not response.ok:
                raise RuntimeError(
                    "전년 동월 가입자 스냅샷 조회 실패 "
                    f"HTTP {response.status_code}: {response.text[:240]}"
                )
            for row in response.json() if response.text else []:
                if isinstance(row, dict):
                    found[str(row.get("snapshot_identity") or "")] = row
    return found


def _snapshot_rows(prospects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 월·같은 사업장 스냅샷은 POST 전에 한 행으로 정리한다."""
    unique_rows: dict[tuple[str, str], dict[str, Any]] = {}
    for prospect in prospects:
        data_created_ym = re.sub(
            r"[^0-9]", "", str(prospect.get("자료생성년월") or "")
        )
        if len(data_created_ym) != 6:
            continue
        try:
            employee_count = int(prospect.get("가입자수") or 0)
        except (TypeError, ValueError):
            continue
        if employee_count < 10:
            continue
        row = {
            "snapshot_identity": _snapshot_identity(prospect),
            "data_created_ym": data_created_ym,
            "employee_count": employee_count,
            "company_name": str(prospect.get("사업장명") or ""),
            "address": str(prospect.get("주소") or ""),
            "source_key": str(prospect.get("source_key") or ""),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        identity = (row["snapshot_identity"], row["data_created_ym"])
        previous = unique_rows.get(identity)
        if previous is None or row["employee_count"] >= previous["employee_count"]:
            unique_rows[identity] = row
    return list(unique_rows.values())


def save_employee_snapshots(prospects: list[dict[str, Any]]) -> int:
    """현재 국민연금 가입자수를 월별로 보관해 1년 뒤 비교에 사용한다."""
    rows = _snapshot_rows(prospects)
    if not rows:
        return 0
    config = get_cloud_config()
    if not config.configured:
        return 0
    response = requests.post(
        f"{config.url}/rest/v1/{TABLE_EMPLOYEE_SNAPSHOTS}",
        headers={
            **_rest_headers(),
            "Prefer": "resolution=merge-duplicates,return=representation",
        },
        params={"on_conflict": "snapshot_identity,data_created_ym"},
        data=json.dumps(rows, ensure_ascii=False),
        timeout=max(config.timeout, 30),
    )
    if not response.ok:
        raise RuntimeError(
            "가입자 스냅샷 저장 실패 "
            f"HTTP {response.status_code}: {response.text[:240]}"
        )
    return len(rows)


def prospect_table_status() -> tuple[bool, str]:
    try:
        db = CloudDatabase()
        db.select(TABLE_PROSPECTS, columns="id", limit=1)
        return True, "영업후보DB 테이블 연결 완료"
    except Exception as exc:
        message = str(exc)
        if "PGRST205" in message or "404" in message:
            return False, "Supabase에 영업후보DB 테이블을 먼저 생성해 주세요."
        return False, f"영업후보DB 연결 실패: {message[:240]}"


def contact_table_status() -> tuple[bool, str]:
    try:
        db = CloudDatabase()
        db.select(TABLE_CONTACTS, columns="id", limit=1)
        return True, "잠재고객 연락처 테이블 연결 완료"
    except Exception as exc:
        message = str(exc)
        if "PGRST205" in message or "404" in message:
            return False, "Supabase에 잠재고객 연락처 테이블을 먼저 생성해 주세요."
        return False, f"잠재고객 연락처 연결 실패: {message[:240]}"


def search_history_table_status() -> tuple[bool, str]:
    try:
        db = CloudDatabase()
        db.select(TABLE_SEARCH_HISTORY, columns="id", limit=1)
        return True, "사용자별 검색 이력 테이블 연결 완료"
    except Exception as exc:
        message = str(exc)
        if "PGRST205" in message or "404" in message:
            return False, "Supabase에 사용자별 검색 이력 테이블이 없습니다."
        return False, f"검색 이력 테이블 연결 실패: {message[:240]}"


def _rest_headers(*, representation: bool = False) -> dict[str, str]:
    config = get_cloud_config()
    if not config.configured:
        raise RuntimeError("Supabase 환경변수가 설정되지 않았습니다.")
    headers = {
        "apikey": config.secret_key,
        "Authorization": f"Bearer {config.secret_key}",
        "Content-Type": "application/json",
    }
    if representation:
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
    return headers


def existing_customer_business_nos(values: list[str]) -> set[str]:
    normalized = sorted(
        {
            _business_no(value)
            for value in values
            if len(re.sub(r"[^0-9]", "", str(value or ""))) == 10
        }
    )
    if not normalized:
        return set()

    config = get_cloud_config()
    if not config.configured:
        return set()
    headers = {
        "apikey": config.secret_key,
        "Authorization": f"Bearer {config.secret_key}",
    }
    quoted = ",".join(f'"{value}"' for value in normalized)
    response = requests.get(
        f"{config.url}/rest/v1/{TABLE_CUSTOMERS}",
        headers=headers,
        params={
            "select": "business_no",
            "business_no": f"in.({quoted})",
        },
        timeout=config.timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"기존 고객 중복확인 실패 HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
    rows = response.json() if response.text else []
    return {
        _business_no(row.get("business_no"))
        for row in rows
        if isinstance(row, dict)
    }


def remove_existing_customers(
    prospects: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    business_nos = [
        str(item.get("사업자등록번호", "")) for item in prospects
    ]
    existing = existing_customer_business_nos(business_nos)
    if not existing:
        return prospects, 0
    filtered = [
        item
        for item in prospects
        if _business_no(item.get("사업자등록번호")) not in existing
    ]
    return filtered, len(prospects) - len(filtered)


def load_fast_growth_candidates(
    region_code: str,
    *,
    minimum_employees: int = 1,
    maximum_employees: int | None = None,
    minimum_growth: int = 1,
    business_type: str = "all",
    district_name: str = "",
    source_mode: str = "combined",
    industry_categories: list[str] | None = None,
    contact_channels: list[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """연락처 캐시와 결합된 월별·연간 고용증가 후보를 즉시 조회."""
    config = get_cloud_config()
    if not config.configured:
        raise RuntimeError("Supabase 환경변수가 설정되지 않았습니다.")

    row_limit = max(1, min(500, int(limit)))
    minimum_count = max(1, int(minimum_employees))
    maximum_count = (
        10000
        if maximum_employees in (None, "")
        else max(minimum_count, int(maximum_employees))
    )
    district = str(district_name or "").strip()
    source_mode = str(source_mode or "combined").strip().lower()
    if source_mode not in {"combined", "nps_monthly", "comwel_annual"}:
        source_mode = "combined"
    normalized_business_type = str(
        business_type or "all"
    ).strip().lower()
    if normalized_business_type not in {"stock", "individual", "all"}:
        normalized_business_type = "all"
    selected_industries = sorted(
        {
            str(value or "").strip()
            for value in (industry_categories or [])
            if str(value or "").strip()
        }
    )
    selected_channels = sorted(
        {
            str(value or "").strip()
            for value in (contact_channels or [])
            if str(value or "").strip()
            in {"mobile_phone", "landline_phone", "email", "instagram"}
        }
    )
    normalized_region_code = re.sub(r"[^0-9]", "", str(region_code or ""))
    if normalized_region_code in {"0", "00"}:
        normalized_region_code = ""
    normalized_region_code = SUPABASE_PROVINCE_CODE_ALIASES.get(
        normalized_region_code[:2],
        normalized_region_code[:2],
    )
    province_name = SUPABASE_PROVINCE_NAMES.get(
        normalized_region_code,
        "",
    )

    payload = {
        "p_province_code": normalized_region_code,
        "p_province_name": province_name,
        "p_district": district,
        "p_min_employees": minimum_count,
        "p_max_employees": maximum_count,
        "p_industries": selected_industries,
        "p_contact_channels": selected_channels,
        "p_limit": row_limit,
        "p_business_type": normalized_business_type,
    }
    try:
        response = requests.post(
            f"{config.url}/rest/v1/rpc/oasis_search_employment_growth_v2",
            headers=_rest_headers(),
            data=json.dumps(payload, ensure_ascii=False),
            timeout=max(config.timeout, 60),
        )
    except requests.Timeout as exc:
        raise GrowthSearchTimeoutError(
            "성장기업 조회 시간이 초과되었습니다. 지역·업종 등 조회 조건을 "
            "선택한 뒤 다시 시도해 주세요."
        ) from exc
    if not response.ok:
        response_text = str(response.text or "")
        if (
            "57014" in response_text
            or "statement timeout" in response_text.lower()
        ):
            raise GrowthSearchTimeoutError(
                "성장기업 조회 시간이 초과되었습니다. 지역·업종 등 "
                "조회 조건을 선택한 뒤 다시 시도해 주세요."
            )
        raise RuntimeError(
            "고용증가·연락처 사전 계산 후보 조회 실패 "
            f"HTTP {response.status_code}: {response_text[:300]}"
        )
    rows = response.json() if response.text else []
    if not isinstance(rows, list):
        rows = []
    if source_mode != "combined":
        rows = [
            row
            for row in rows
            if str(row.get("source_type") or "") == source_mode
        ]

    results: list[dict[str, Any]] = []
    for row in rows:
        source_type = str(row.get("source_type") or "")
        current_count = int(row.get("current_employee_count") or 0)
        previous_count = int(row.get("previous_employee_count") or 0)
        growth = int(row.get("employee_growth") or 0)
        company_name = str(row.get("company_name") or "")
        address = str(row.get("address") or "")
        province = str(row.get("province_name") or "")
        district = str(row.get("district_name") or "")
        frequency = str(row.get("growth_frequency") or "")
        mobile_phone = str(row.get("mobile_phone") or "")
        landline_phone = str(row.get("landline_phone") or "")
        preferred_phone = mobile_phone or landline_phone
        growth_label = (
            f"전월대비 +{growth:,}명"
            if frequency == "monthly"
            else f"전년대비 +{growth:,}명"
        )
        results.append(
            {
                "source": source_type,
                "source_key": (
                    f"{source_type}:{row.get('source_record_key') or ''}"
                ),
                "사업자등록번호": str(row.get("business_no") or ""),
                "사업장명": company_name,
                "주소": address,
                "지역": " ".join(
                    value for value in (province, district) if value
                ),
                "업종코드": str(row.get("industry_code") or ""),
                "업종명": str(row.get("industry_name") or ""),
                "업종분류": str(
                    row.get("industry_category") or "기타"
                ),
                "휴대전화": mobile_phone,
                "일반전화": landline_phone,
                "대표전화": preferred_phone,
                "전화유형": (
                    "휴대전화"
                    if mobile_phone
                    else ("일반전화" if landline_phone else "")
                ),
                "전화출처": "사전 수집 연락처",
                "이메일": str(row.get("email") or ""),
                "인스타그램": str(row.get("instagram") or ""),
                "인스타그램URL": str(row.get("instagram_url") or ""),
                "연락처상태": str(row.get("contact_status") or ""),
                "연락처조회일": str(row.get("contact_checked_at") or ""),
                "영업분석": {
                    "phone": preferred_phone,
                    "phone_source": "employment_contact_cache",
                    "phone_confidence": 90 if preferred_phone else 0,
                    "email": str(row.get("email") or ""),
                    "instagram": str(row.get("instagram") or ""),
                    "instagram_url": str(
                        row.get("instagram_url") or ""
                    ),
                    "contact_status": str(
                        row.get("contact_status") or ""
                    ),
                    "public_contacts": [],
                },
                "가입자수": current_count,
                "이전가입자수": previous_count,
                "전월가입자수": (
                    previous_count if frequency == "monthly" else ""
                ),
                "전년가입자수": (
                    previous_count if frequency == "annual" else ""
                ),
                "전월대비고용증가": (
                    growth if frequency == "monthly" else ""
                ),
                "전년대비고용증가": (
                    growth if frequency == "annual" else ""
                ),
                "선택고용증가": growth,
                "고용증가신호": growth > 0,
                "고용증가기준": frequency,
                "고용증가구분": growth_label,
                "고용자료상태": "PRECOMPUTED",
                "고용자료메시지": "Supabase 사전 계산 결과",
                "고용증가판정": "INCREASED",
                "자료생성년월": str(row.get("current_period") or ""),
                "전년자료생성년월": str(row.get("previous_period") or ""),
                "신규업체": bool(row.get("is_new_company")),
                "원본데이터": dict(row),
            }
        )
    return results


def load_recent_opening_candidates(
    region_code: str,
    *,
    minimum_employees: int = 1,
    maximum_employees: int | None = None,
    recent_months: int = 6,
    include_comwel_annual: bool = True,
    business_type: str = "all",
    district_name: str = "",
    industry_categories: list[str] | None = None,
    contact_channels: list[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """국민연금·근로복지공단의 신규개업 추정 후보를 즉시 조회."""
    config = get_cloud_config()
    if not config.configured:
        raise RuntimeError("Supabase 환경변수가 설정되지 않았습니다.")

    row_limit = max(1, min(500, int(limit)))
    minimum_count = max(1, int(minimum_employees))
    maximum_count = (
        10000
        if maximum_employees in (None, "")
        else max(minimum_count, int(maximum_employees))
    )
    period_months = int(recent_months)
    if period_months not in {3, 6, 12}:
        period_months = 6
    district = str(district_name or "").strip()
    normalized_business_type = str(
        business_type or "all"
    ).strip().lower()
    if normalized_business_type not in {"stock", "individual", "all"}:
        normalized_business_type = "all"
    selected_industries = sorted(
        {
            str(value or "").strip()
            for value in (industry_categories or [])
            if str(value or "").strip()
        }
    )
    selected_channels = sorted(
        {
            str(value or "").strip()
            for value in (contact_channels or [])
            if str(value or "").strip()
            in {"mobile_phone", "landline_phone", "email", "instagram"}
        }
    )
    normalized_region_code = re.sub(r"[^0-9]", "", str(region_code or ""))
    if normalized_region_code in {"0", "00"}:
        normalized_region_code = ""
    normalized_region_code = SUPABASE_PROVINCE_CODE_ALIASES.get(
        normalized_region_code[:2],
        normalized_region_code[:2],
    )
    province_name = SUPABASE_PROVINCE_NAMES.get(
        normalized_region_code,
        "",
    )
    payload = {
        "p_province_code": normalized_region_code,
        "p_province_name": province_name,
        "p_district": district,
        "p_min_employees": minimum_count,
        "p_max_employees": maximum_count,
        "p_industries": selected_industries,
        "p_contact_channels": selected_channels,
        "p_recent_months": period_months,
        "p_include_comwel_annual": bool(include_comwel_annual),
        "p_limit": row_limit,
        "p_business_type": normalized_business_type,
    }
    response = requests.post(
        f"{config.url}/rest/v1/rpc/oasis_search_recent_openings_v2",
        headers=_rest_headers(),
        data=json.dumps(payload, ensure_ascii=False),
        timeout=max(config.timeout, 60),
    )
    if not response.ok:
        raise RuntimeError(
            "신규개업 추정·연락처 후보 조회 실패 "
            f"HTTP {response.status_code}: {response.text[:300]}"
        )
    rows = response.json() if response.text else []
    if not isinstance(rows, list):
        rows = []

    results: list[dict[str, Any]] = []
    for row in rows:
        business_no = re.sub(
            r"[^0-9]",
            "",
            str(row.get("business_no") or ""),
        )
        if len(business_no) != 10:
            business_no = ""
        source_key = _recent_opening_source_key(row)
        if not source_key:
            continue
        source_type = str(row.get("source_type") or "")
        company_name = str(row.get("company_name") or "")
        address = str(row.get("address") or "")
        province = str(row.get("province_name") or "")
        district = str(row.get("district_name") or "")
        mobile_phone = str(row.get("mobile_phone") or "")
        landline_phone = str(row.get("landline_phone") or "")
        preferred_phone = mobile_phone or landline_phone
        opening_date = str(row.get("opening_signal_date") or "")
        opening_year = int(row.get("opening_signal_year") or 0)
        if source_type == "nps_monthly":
            opening_label = (
                f"국민연금 적용일 {opening_date}"
                if opening_date
                else "국민연금 신규 적용"
            )
            opening_basis = "국민연금 사업장 적용일"
        else:
            opening_label = f"{opening_year or 2025}년 신규 추정"
            opening_basis = "근로복지공단 연간 자료 최초 등장"
        current_count = int(row.get("current_employee_count") or 0)
        results.append(
            {
                "source": source_type,
                "source_key": source_key,
                "사업자등록번호": business_no,
                "사업자번호상태": (
                    "확인" if business_no else "미확인"
                ),
                "사업장명": company_name,
                "주소": address,
                "지역": " ".join(
                    value for value in (province, district) if value
                ),
                "업종코드": str(row.get("industry_code") or ""),
                "업종명": str(row.get("industry_name") or ""),
                "업종분류": str(
                    row.get("industry_category") or "기타"
                ),
                "휴대전화": mobile_phone,
                "일반전화": landline_phone,
                "대표전화": preferred_phone,
                "전화유형": (
                    "휴대전화"
                    if mobile_phone
                    else ("일반전화" if landline_phone else "")
                ),
                "전화출처": "사전 수집 연락처",
                "이메일": str(row.get("email") or ""),
                "인스타그램": str(row.get("instagram") or ""),
                "인스타그램URL": str(row.get("instagram_url") or ""),
                "연락처상태": str(row.get("contact_status") or ""),
                "연락처조회일": str(row.get("contact_checked_at") or ""),
                "영업분석": {
                    "phone": preferred_phone,
                    "phone_source": "employment_contact_cache",
                    "phone_confidence": 90 if preferred_phone else 0,
                    "email": str(row.get("email") or ""),
                    "instagram": str(row.get("instagram") or ""),
                    "instagram_url": str(
                        row.get("instagram_url") or ""
                    ),
                    "contact_status": str(
                        row.get("contact_status") or ""
                    ),
                    "public_contacts": [],
                },
                "가입자수": current_count,
                "이전가입자수": "",
                "전월가입자수": "",
                "전년가입자수": "",
                "전월대비고용증가": "",
                "전년대비고용증가": "",
                "선택고용증가": "",
                "고용증가신호": False,
                "고용증가기준": "recent_opening",
                "고용증가구분": "",
                "고용자료상태": "PRECOMPUTED",
                "고용자료메시지": "Supabase 신규개업 추정 결과",
                "고용증가판정": "",
                "자료생성년월": str(row.get("source_period") or ""),
                "신규업체": True,
                "신규개업구분": opening_label,
                "신규추정일": opening_date,
                "신규추정연도": opening_year,
                "신규근거": opening_basis,
                "신규정밀도": str(
                    row.get("opening_signal_precision") or ""
                ),
                "원본데이터": dict(row),
            }
        )
    return results


def existing_prospect_identities(
    limit: int = 10000,
) -> tuple[set[str], set[str], set[str]]:
    """사용자 구분 없이 전체 영업후보의 중복 식별값을 반환."""
    db = CloudDatabase()
    rows = db.select(
        TABLE_PROSPECTS,
        columns="source_key,business_no,company_name,address",
        limit=min(10000, max(1, int(limit))),
    )
    source_keys = {
        str(row.get("source_key") or "").strip()
        for row in rows
        if str(row.get("source_key") or "").strip()
    }
    business_nos = {
        _business_no(row.get("business_no"))
        for row in rows
        if len(re.sub(r"[^0-9]", "", str(row.get("business_no") or ""))) == 10
    }
    company_address_keys = {
        _company_address_key(row.get("company_name"), row.get("address"))
        for row in rows
        if _company_address_key(row.get("company_name"), row.get("address"))
    }
    return source_keys, business_nos, company_address_keys


def remove_existing_prospects(
    prospects: list[dict[str, Any]],
    *,
    source_keys: set[str] | None = None,
    business_nos: set[str] | None = None,
    company_address_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    if (
        source_keys is None
        or business_nos is None
        or company_address_keys is None
    ):
        source_keys, business_nos, company_address_keys = (
            existing_prospect_identities()
        )
    filtered = [
        item
        for item in prospects
        if str(item.get("source_key") or "").strip() not in source_keys
        and _business_no(item.get("사업자등록번호")) not in business_nos
        and _company_address_key(item.get("사업장명"), item.get("주소"))
        not in company_address_keys
    ]
    return filtered, len(prospects) - len(filtered)


def _database_row(
    prospect: dict[str, Any],
    owner_user_id: str,
    *,
    include_assignment_fields: bool = False,
    include_review_contacts: bool = False,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    source_data = dict(prospect.get("원본데이터") or {})
    source_data["business_type"] = str(
        prospect.get("사업자유형") or ""
    )
    source_data["industry_category"] = str(
        prospect.get("업종분류") or ""
    )
    source_data["employment_growth"] = {
        "basis": str(prospect.get("고용증가기준") or ""),
        "status": str(prospect.get("고용자료상태") or ""),
        "message": str(prospect.get("고용자료메시지") or ""),
        "judgement": str(prospect.get("고용증가판정") or ""),
        "current_employee_count": prospect.get("가입자수"),
        "previous_employee_count": prospect.get("전년가입자수"),
        "year_over_year_growth": prospect.get("전년대비고용증가"),
        "new_employee_count": prospect.get("신규취득자수"),
        "lost_employee_count": prospect.get("상실가입자수"),
        "recent_net_growth": prospect.get("순고용증가"),
        "selected_growth": prospect.get("선택고용증가"),
        "current_data_created_ym": str(
            prospect.get("자료생성년월") or ""
        ),
        "previous_data_created_ym": str(
            prospect.get("전년자료생성년월") or ""
        ),
    }
    sales_analysis = prospect.get("영업분석")
    if isinstance(sales_analysis, dict):
        source_data["sales_intelligence_v971"] = sales_analysis
    corporate_registration_no = str(
        prospect.get("corporate_registration_no")
        or prospect.get("법인등록번호")
        or ""
    ).strip()
    nps_workplace_management_no = str(
        prospect.get("nps_workplace_management_no")
        or prospect.get("국민연금사업장관리번호")
        or prospect.get("사업장관리번호")
        or ""
    ).strip()
    row = {
        "source": str(prospect.get("source") or "nps_workplace_v2"),
        "source_key": str(prospect.get("source_key") or ""),
        "business_no": _business_no(prospect.get("사업자등록번호")),
        "company_name": str(prospect.get("사업장명") or ""),
        "address": str(prospect.get("주소") or ""),
        "region": str(prospect.get("지역") or ""),
        "industry_code": str(prospect.get("업종코드") or ""),
        "industry_name": str(prospect.get("업종명") or ""),
        "employee_count": int(prospect.get("가입자수") or 0),
        "new_employee_count": int(prospect.get("신규취득자수") or 0),
        "lost_employee_count": int(prospect.get("상실가입자수") or 0),
        "monthly_notice_amount": int(prospect.get("당월고지금액") or 0),
        "data_created_ym": str(prospect.get("자료생성년월") or ""),
        "priority_score": int(prospect.get("우선순위점수") or 0),
        "priority_reasons": prospect.get("추천사유") or [],
        "status": "candidate",
        "owner_user_id": str(owner_user_id or ""),
        "source_data": source_data,
        "collected_at": now,
        "updated_at": now,
    }
    if include_assignment_fields:
        from company_sales_assignment import build_company_uid

        row.update(
            {
                "company_uid": build_company_uid(prospect),
                "corporate_registration_no": corporate_registration_no,
                "nps_workplace_management_no": nps_workplace_management_no,
            }
        )
    if include_review_contacts:
        row["contact_candidates"] = build_review_contact_candidates(prospect)
    return row


def save_prospects(
    prospects: list[dict[str, Any]],
    owner_user_id: str,
) -> int:
    # 화면 외 경로에서 저장을 시도해도 기존 전사 영업후보의 담당자가
    # 바뀌지 않도록 한 번 더 공통 중복제외를 적용한다.
    prospects, _excluded_count = remove_existing_prospects(prospects)
    rows = [
        _database_row(item, owner_user_id)
        for item in prospects
        if str(item.get("source_key") or "").strip()
    ]
    if not rows:
        return 0

    config = get_cloud_config()
    if not config.configured:
        raise RuntimeError("Supabase 환경변수가 설정되지 않았습니다.")
    headers = {
        "apikey": config.secret_key,
        "Authorization": f"Bearer {config.secret_key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=representation",
    }
    response = requests.post(
        f"{config.url}/rest/v1/{TABLE_PROSPECTS}",
        headers=headers,
        params={"on_conflict": "source,source_key"},
        data=json.dumps(rows, ensure_ascii=False, default=str),
        timeout=max(config.timeout, 30),
    )
    if not response.ok:
        raise RuntimeError(
            f"영업후보DB 저장 실패 HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
    saved = response.json() if response.text else []
    return len(saved) if isinstance(saved, list) else len(rows)


def save_assigned_prospects(
    prospects: list[dict[str, Any]],
    owner_user_id: str,
    *,
    session_id: str = "",
    promote_review_contacts: bool = False,
) -> dict[str, Any]:
    """Atomically claim companies and mirror them to the existing sales DB.

    Both writes are performed inside one PostgreSQL RPC transaction.  This
    prevents a lost HTTP response or a second REST failure from leaving the
    company-wide assignment and the legacy owner row out of sync.
    """
    from company_sales_assignment import (
        build_company_uid,
        claim_and_save_companies,
    )

    owner_user_id = str(owner_user_id or "").strip().lower()
    if not owner_user_id:
        raise ValueError("로그인 사용자 정보가 없습니다.")

    prepared_by_uid: dict[str, dict[str, Any]] = {}
    invalid_results: list[dict[str, Any]] = []
    for item in prospects:
        candidate = dict(item)
        if (
            promote_review_contacts
            and not build_review_contact_candidates(candidate)
        ):
            invalid_results.append(
                {
                    "ok": False,
                    "code": "INVALID_INPUT",
                    "message": "승격할 연락처 후보가 없습니다.",
                    "assignment": {},
                }
            )
            continue
        try:
            uid = build_company_uid(candidate)
        except ValueError as exc:
            invalid_results.append(
                {
                    "ok": False,
                    "code": "INVALID_INPUT",
                    "message": str(exc),
                    "assignment": {},
                }
            )
            continue
        candidate["company_uid"] = uid
        # A batch can contain the same company from two public sources.  Keep
        # the first/highest-ranked row and make only one assignment request.
        prepared_by_uid.setdefault(uid, candidate)

    prepared = list(prepared_by_uid.values())
    if not prepared:
        return {
            "ok": False,
            "code": "INVALID_INPUT",
            "message": "저장할 수 있는 업체 식별정보가 없습니다.",
            "saved_count": 0,
            "success_count": 0,
            "failure_count": len(invalid_results),
            "results": invalid_results,
        }

    payloads = [
        _database_row(
            item,
            owner_user_id,
            include_assignment_fields=True,
            include_review_contacts=promote_review_contacts,
        )
        for item in prepared
    ]
    database = CloudDatabase()
    claim_result = claim_and_save_companies(
        owner_user_id,
        payloads,
        session_id=session_id,
        db=database,
    )
    claim_rows = list(claim_result.get("results") or [])
    success_count = 0
    newly_claimed_count = 0
    already_owned_count = 0
    for result in claim_rows:
        if not result.get("ok"):
            continue
        success_count += 1
        result_code = str(result.get("code") or "").upper()
        if result_code in {"ALREADY_OWNED", "OWNED", "ALREADY_MINE"}:
            already_owned_count += 1
        elif result_code in {
            "OK",
            "CLAIMED",
            "ASSIGNED",
            "CREATED",
        }:
            newly_claimed_count += 1

    results = [*claim_rows, *invalid_results]
    failure_count = sum(1 for result in results if not result.get("ok"))
    promoted_contact_count = sum(
        int((result.get("assignment") or {}).get("promoted_contact_count") or 0)
        for result in results
        if result.get("ok")
    )
    return {
        **claim_result,
        "ok": not invalid_results and bool(claim_result.get("ok")),
        # ALREADY_OWNED also repairs/refreshes the mirrored legacy row inside
        # the same RPC, so it counts as a successfully saved selection.
        "saved_count": success_count,
        "claimed_count": newly_claimed_count,
        "already_owned_count": already_owned_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "promoted_contact_count": promoted_contact_count,
        "results": results,
    }


def list_prospects(
    owner_user_id: str,
    *,
    limit: int = 300,
) -> list[dict[str, Any]]:
    """로그인 사용자가 직접 저장한 영업후보만 반환한다."""
    owner_user_id = str(owner_user_id or "").strip()
    if not owner_user_id:
        return []
    db = CloudDatabase()
    common_columns = (
        "id,source,source_key,company_name,business_no,address,region,"
        "industry_name,"
        "employee_count,new_employee_count,lost_employee_count,"
        "priority_score,priority_reasons,status,data_created_ym,"
        "owner_user_id,source_data,updated_at"
    )
    query_limit = min(1000, max(1, int(limit)))
    try:
        return db.select(
            TABLE_PROSPECTS,
            filters={"owner_user_id": owner_user_id},
            columns=f"{common_columns},memo",
            order="priority_score.desc,updated_at.desc",
            limit=query_limit,
        )
    except Exception as exc:
        message = str(exc)
        if (
            "memo" not in message.lower()
            and "PGRST204" not in message
            and "400" not in message
        ):
            raise
        rows = db.select(
            TABLE_PROSPECTS,
            filters={"owner_user_id": owner_user_id},
            columns=common_columns,
            order="priority_score.desc,updated_at.desc",
            limit=query_limit,
        )
        for row in rows:
            row["memo"] = ""
        return rows


def save_prospect_memo(
    prospect_id: str,
    memo: str,
    owner_user_id: str,
) -> bool:
    prospect_id = str(prospect_id or "").strip()
    owner_user_id = str(owner_user_id or "").strip()
    if not prospect_id:
        raise ValueError("메모를 저장할 영업후보 ID가 없습니다.")
    if not owner_user_id:
        raise ValueError("로그인 사용자 정보가 없습니다.")
    config = get_cloud_config()
    response = requests.patch(
        f"{config.url}/rest/v1/{TABLE_PROSPECTS}",
        headers={
            **_rest_headers(),
            "Prefer": "return=minimal",
        },
        params={
            "id": f"eq.{prospect_id}",
            "owner_user_id": f"eq.{owner_user_id}",
        },
        data=json.dumps(
            {
                "memo": str(memo or "").strip(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        ),
        timeout=max(config.timeout, 30),
    )
    if not response.ok:
        raise RuntimeError(
            f"업체 메모 저장 실패 HTTP {response.status_code}: "
            f"{response.text[:400]}"
        )
    return True


def save_search_history(
    owner_user_id: str,
    *,
    region: str,
    region_code: str,
    district: str,
    business_type: str,
    data_source: str,
    start_page: int,
    end_page: int,
    target_count: int,
    minimum_employees: int,
    maximum_employees: int,
    minimum_growth: int,
    growth_only: bool,
    growth_basis: str,
    industry_categories: list[str] | None,
    contact_channels: list[str] | None,
    discovery_type: str,
    recent_months: int,
    include_comwel_annual: bool,
    found_count: int,
    pages_scanned: int,
    elapsed_seconds: float,
) -> bool:
    owner_user_id = str(owner_user_id or "").strip()
    if not owner_user_id:
        return False
    db = CloudDatabase()
    rows = db.insert(
        TABLE_SEARCH_HISTORY,
        [
            {
                "owner_user_id": owner_user_id,
                "region": str(region or ""),
                "region_code": str(region_code or ""),
                "district": str(district or ""),
                "business_type": str(business_type or ""),
                "data_source": str(data_source or "combined"),
                "start_page": max(1, int(start_page)),
                "end_page": max(1, int(end_page)),
                "target_count": max(1, int(target_count)),
                "minimum_employees": max(1, int(minimum_employees)),
                "maximum_employees": max(
                    int(minimum_employees),
                    int(maximum_employees),
                ),
                "minimum_growth": max(1, int(minimum_growth)),
                "growth_only": bool(growth_only),
                "growth_basis": str(growth_basis or "year_over_year"),
                "industry_categories": [
                    str(value or "").strip()
                    for value in (industry_categories or [])
                    if str(value or "").strip()
                ],
                "contact_channels": [
                    str(value or "").strip()
                    for value in (contact_channels or [])
                    if str(value or "").strip()
                ],
                "discovery_type": (
                    "recent_opening"
                    if str(discovery_type or "") == "recent_opening"
                    else "growth"
                ),
                "recent_months": (
                    int(recent_months)
                    if int(recent_months) in {3, 6, 12}
                    else 6
                ),
                "include_comwel_annual": bool(include_comwel_annual),
                "found_count": max(0, int(found_count)),
                "pages_scanned": max(0, int(pages_scanned)),
                "elapsed_seconds": max(0.0, float(elapsed_seconds)),
            }
        ],
    )
    return bool(rows)


def list_search_history(
    owner_user_id: str,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    owner_user_id = str(owner_user_id or "").strip()
    if not owner_user_id:
        return []
    db = CloudDatabase()
    return db.select(
        TABLE_SEARCH_HISTORY,
        filters={"owner_user_id": owner_user_id},
        columns=(
            "id,owner_user_id,region,region_code,district,business_type,"
            "data_source,start_page,end_page,target_count,minimum_employees,"
            "maximum_employees,minimum_growth,"
            "growth_only,growth_basis,industry_categories,contact_channels,"
            "discovery_type,recent_months,include_comwel_annual,"
            "found_count,pages_scanned,"
            "elapsed_seconds,searched_at"
        ),
        order="searched_at.desc",
        limit=min(200, max(1, int(limit))),
    )


def list_contacts_for_prospects(
    prospect_ids: list[str],
    owner_user_id: str = "",
    *,
    is_admin_user: bool = False,
) -> list[dict[str, Any]]:
    normalized = sorted(
        {
            str(value or "").strip()
            for value in prospect_ids
            if str(value or "").strip()
        }
    )
    if not normalized:
        return []
    owner_user_id = str(owner_user_id or "").strip()
    config = get_cloud_config()
    if not is_admin_user:
        if not owner_user_id:
            return []
        ownership_response = requests.get(
            f"{config.url}/rest/v1/{TABLE_PROSPECTS}",
            headers=_rest_headers(),
            params={
                "select": "id",
                "id": f"in.({','.join(normalized)})",
                "owner_user_id": f"eq.{owner_user_id}",
            },
            timeout=max(config.timeout, 30),
        )
        if not ownership_response.ok:
            raise RuntimeError(
                "영업후보 소유권 확인 실패 "
                f"HTTP {ownership_response.status_code}: "
                f"{ownership_response.text[:300]}"
            )
        owned_rows = ownership_response.json() if ownership_response.text else []
        if not isinstance(owned_rows, list):
            owned_rows = []
        owned_ids = {
            str(row.get("id") or "").strip()
            for row in owned_rows
            if str(row.get("id") or "").strip()
        }
        normalized = [value for value in normalized if value in owned_ids]
        if not normalized:
            return []
    response = requests.get(
        f"{config.url}/rest/v1/{TABLE_CONTACTS}",
        headers=_rest_headers(),
        params={
            "select": (
                "id,prospect_id,contact_type,contact_value,contact_label,"
                "source_type,source_url,confidence,verification_status,"
                "is_primary,do_not_contact,opt_out_at,collected_at,"
                "verified_at,updated_at"
            ),
            "prospect_id": f"in.({','.join(normalized)})",
            "order": "prospect_id.asc,is_primary.desc,confidence.desc",
        },
        timeout=max(config.timeout, 30),
    )
    if not response.ok:
        raise RuntimeError(
            f"잠재고객 연락처 조회 실패 HTTP {response.status_code}: "
            f"{response.text[:400]}"
        )
    rows = response.json() if response.text else []
    return rows if isinstance(rows, list) else []


def company_contact_is_suppressed(
    company_uid: str,
    *,
    prospect_id: str = "",
) -> bool:
    """Check opt-out flags across every prospect row for one company UID.

    Only identifiers and suppression flags are selected. Contact values are
    deliberately excluded so this company-wide safety check cannot expose PII.
    Any lookup failure raises and the caller must fail closed.
    """

    clean_uid = str(company_uid or "").strip()
    clean_prospect_id = str(prospect_id or "").strip()
    if not clean_uid:
        raise ValueError("업체 공통 식별키가 없습니다.")
    config = get_cloud_config()
    company_response = requests.get(
        f"{config.url}/rest/v1/{TABLE_PROSPECTS}",
        headers=_rest_headers(),
        params={
            "select": "id",
            "company_uid": f"eq.{clean_uid}",
            "limit": "1000",
        },
        timeout=max(config.timeout, 30),
    )
    if not company_response.ok:
        raise RuntimeError("업체 수신거부 범위를 확인하지 못했습니다.")
    company_rows = company_response.json() if company_response.text else []
    if not isinstance(company_rows, list) or len(company_rows) >= 1000:
        raise RuntimeError("업체 수신거부 범위를 안전하게 확정하지 못했습니다.")
    prospect_ids = {
        str(row.get("id") or "").strip()
        for row in company_rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    }
    if clean_prospect_id:
        prospect_ids.add(clean_prospect_id)
    if not prospect_ids:
        raise RuntimeError("업체 수신거부 대상을 찾지 못했습니다.")
    contact_response = requests.get(
        f"{config.url}/rest/v1/{TABLE_CONTACTS}",
        headers=_rest_headers(),
        params={
            "select": "prospect_id,do_not_contact,opt_out_at",
            "prospect_id": f"in.({','.join(sorted(prospect_ids))})",
        },
        timeout=max(config.timeout, 30),
    )
    if not contact_response.ok:
        raise RuntimeError("업체 수신거부 상태를 확인하지 못했습니다.")
    contact_rows = contact_response.json() if contact_response.text else []
    if not isinstance(contact_rows, list):
        raise RuntimeError("업체 수신거부 응답을 확인하지 못했습니다.")
    contact_suppressed = any(
        isinstance(row, dict)
        and (bool(row.get("do_not_contact")) or bool(row.get("opt_out_at")))
        for row in contact_rows
    )
    if contact_suppressed:
        return True
    control_response = requests.get(
        f"{config.url}/rest/v1/{TABLE_CONTACT_CONTROLS}",
        headers=_rest_headers(),
        params={
            "select": "status",
            "company_uid": f"eq.{clean_uid}",
            "status": "in.(opted_out,admin_blocked)",
            "limit": "1",
        },
        timeout=max(config.timeout, 30),
    )
    if not control_response.ok:
        raise RuntimeError("기존 업체 연락차단 상태를 확인하지 못했습니다.")
    control_rows = control_response.json() if control_response.text else []
    if not isinstance(control_rows, list):
        raise RuntimeError("기존 업체 연락차단 응답을 확인하지 못했습니다.")
    return bool(control_rows)


def legacy_phone_contact_hash(recipient_phone: str) -> str:
    """Return the historical DNC HMAC without persisting a raw phone."""

    digits = re.sub(r"\D", "", str(recipient_phone or ""))
    if digits.startswith("82"):
        digits = "0" + digits[2:]
    if not re.fullmatch(r"01(?:0\d{8}|[16789]\d{7,8})", digits):
        raise ValueError("수신거부 확인용 휴대전화 형식이 올바르지 않습니다.")
    hash_key = str(
        os.environ.get(LEGACY_CONTACT_PHONE_HASH_KEY_ENV, "") or ""
    ).strip()
    if len(hash_key) < 32:
        raise RuntimeError("기존 수신거부 확인용 보안키가 설정되지 않았습니다.")
    phone_hash = hmac.new(
        hash_key.encode("utf-8"),
        digits.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return phone_hash


def legacy_phone_contact_is_suppressed(
    company_uid: str,
    recipient_phone: str,
) -> bool:
    """Honor historical phone-hash opt-outs even if a company UID changed.

    The canonical phone is HMAC-hashed locally with the same key used by the
    retired Kakao guidance flow. Only the hash and company UID reach Supabase.
    Missing or malformed inputs fail closed by raising to the caller.
    """

    clean_uid = str(company_uid or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9:_-]{1,180}", clean_uid):
        raise ValueError("업체 공통 식별키 형식을 확인할 수 없습니다.")
    phone_hash = legacy_phone_contact_hash(recipient_phone)

    config = get_cloud_config()
    response = requests.get(
        f"{config.url}/rest/v1/{TABLE_CONTACT_CONTROLS}",
        headers=_rest_headers(),
        params={
            "select": "status",
            "or": (
                f"(company_uid.eq.{clean_uid},"
                f"recipient_phone_hash.eq.{phone_hash})"
            ),
            "status": "in.(opted_out,admin_blocked)",
            "limit": "1",
        },
        timeout=max(config.timeout, 30),
    )
    if not response.ok:
        raise RuntimeError("기존 전화번호 수신거부 상태를 확인하지 못했습니다.")
    rows = response.json() if response.text else []
    if not isinstance(rows, list):
        raise RuntimeError("기존 전화번호 수신거부 응답을 확인하지 못했습니다.")
    return bool(rows)


def save_sales_analysis(
    prospect_id: str,
    analysis: dict[str, Any],
) -> bool:
    prospect_id = str(prospect_id or "").strip()
    if not prospect_id:
        raise ValueError("영업분석을 저장할 영업후보 ID가 없습니다.")
    config = get_cloud_config()
    response = requests.get(
        f"{config.url}/rest/v1/{TABLE_PROSPECTS}",
        headers=_rest_headers(),
        params={
            "select": "id,source_data",
            "id": f"eq.{prospect_id}",
            "limit": 1,
        },
        timeout=config.timeout,
    )
    if not response.ok:
        raise RuntimeError(
            f"기존 영업분석 조회 실패 HTTP {response.status_code}: "
            f"{response.text[:300]}"
        )
    rows = response.json() if response.text else []
    if not rows:
        raise RuntimeError("영업후보를 찾을 수 없습니다.")
    source_data = dict(rows[0].get("source_data") or {})
    source_data["sales_intelligence_v971"] = analysis
    update_response = requests.patch(
        f"{config.url}/rest/v1/{TABLE_PROSPECTS}",
        headers={
            **_rest_headers(),
            "Prefer": "return=minimal",
        },
        params={"id": f"eq.{prospect_id}"},
        data=json.dumps(
            {
                "source_data": source_data,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            default=str,
        ),
        timeout=max(config.timeout, 30),
    )
    if not update_response.ok:
        raise RuntimeError(
            f"영업분석 저장 실패 HTTP {update_response.status_code}: "
            f"{update_response.text[:400]}"
        )
    return True


def save_prospect_contacts(
    prospect_id: str,
    contacts: list[dict[str, Any]],
    owner_user_id: str = "",
) -> int:
    prospect_id = str(prospect_id or "").strip()
    if not prospect_id:
        raise ValueError("연락처를 저장할 영업후보 ID가 없습니다.")
    valid_contacts = [
        item
        for item in contacts
        if str(item.get("contact_type") or "")
        in {"phone", "email", "instagram", "website"}
        and str(item.get("contact_value") or "").strip()
        and str(item.get("verification_status") or "") != "rejected"
    ]
    if not valid_contacts:
        return 0

    existing = list_contacts_for_prospects(
        [prospect_id],
        owner_user_id,
    )
    existing_map = {
        (
            str(row.get("contact_type") or ""),
            str(row.get("contact_value") or ""),
        ): row
        for row in existing
    }
    protected_statuses = {"manual_verified", "auto_verified"}
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for item in valid_contacts:
        contact_type = str(item.get("contact_type") or "").strip()
        contact_value = str(item.get("contact_value") or "").strip()
        current = existing_map.get((contact_type, contact_value), {})
        incoming_status = str(
            item.get("verification_status") or "review_required"
        )
        current_status = str(current.get("verification_status") or "")
        verification_status = (
            current_status
            if current_status in protected_statuses
            else incoming_status
        )
        rows.append(
            {
                "prospect_id": prospect_id,
                "contact_type": contact_type,
                "contact_value": contact_value,
                "contact_label": str(item.get("contact_label") or ""),
                "source_type": str(item.get("source_type") or ""),
                "source_url": str(item.get("source_url") or ""),
                "confidence": max(
                    int(current.get("confidence") or 0),
                    int(item.get("confidence") or 0),
                ),
                "verification_status": verification_status,
                "is_primary": bool(
                    current.get("is_primary") or item.get("is_primary")
                ),
                "owner_user_id": str(owner_user_id or ""),
                "metadata": item.get("metadata") or {},
                "collected_at": str(item.get("collected_at") or now),
                "verified_at": (
                    current.get("verified_at")
                    or (
                        now
                        if verification_status
                        in {"manual_verified", "auto_verified"}
                        else None
                    )
                ),
                "updated_at": now,
            }
        )

    config = get_cloud_config()
    response = requests.post(
        f"{config.url}/rest/v1/{TABLE_CONTACTS}",
        headers=_rest_headers(representation=True),
        params={"on_conflict": "prospect_id,contact_type,contact_value"},
        data=json.dumps(rows, ensure_ascii=False, default=str),
        timeout=max(config.timeout, 30),
    )
    if not response.ok:
        raise RuntimeError(
            f"잠재고객 연락처 저장 실패 HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )
    saved = response.json() if response.text else []
    return len(saved) if isinstance(saved, list) else len(rows)
