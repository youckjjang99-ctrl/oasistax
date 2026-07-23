from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import requests

from cloud_db import CloudDatabase, get_cloud_config


TABLE_PROSPECTS = "oasis_prospect_companies"
TABLE_CUSTOMERS = "oasis_customers"
TABLE_CONTACTS = "oasis_prospect_contacts"


def _business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return str(value or "").strip()


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


def existing_prospect_identities(limit: int = 5000) -> tuple[set[str], set[str]]:
    """Return saved prospect source keys and business numbers."""
    db = CloudDatabase()
    rows = db.select(
        TABLE_PROSPECTS,
        columns="source_key,business_no",
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
    return source_keys, business_nos


def remove_existing_prospects(
    prospects: list[dict[str, Any]],
    *,
    source_keys: set[str] | None = None,
    business_nos: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    if source_keys is None or business_nos is None:
        source_keys, business_nos = existing_prospect_identities()
    filtered = [
        item
        for item in prospects
        if str(item.get("source_key") or "").strip() not in source_keys
        and _business_no(item.get("사업자등록번호")) not in business_nos
    ]
    return filtered, len(prospects) - len(filtered)


def _database_row(
    prospect: dict[str, Any],
    owner_user_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    source_data = dict(prospect.get("원본데이터") or {})
    sales_analysis = prospect.get("영업분석")
    if isinstance(sales_analysis, dict):
        source_data["sales_intelligence_v971"] = sales_analysis
    return {
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


def save_prospects(
    prospects: list[dict[str, Any]],
    owner_user_id: str,
) -> int:
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


def list_prospects(limit: int = 300) -> list[dict[str, Any]]:
    db = CloudDatabase()
    return db.select(
        TABLE_PROSPECTS,
        columns=(
            "id,source,source_key,company_name,business_no,address,region,"
            "industry_name,"
            "employee_count,new_employee_count,lost_employee_count,"
            "priority_score,priority_reasons,status,data_created_ym,"
            "owner_user_id,source_data,updated_at"
        ),
        order="priority_score.desc,updated_at.desc",
        limit=min(1000, max(1, int(limit))),
    )


def list_contacts_for_prospects(
    prospect_ids: list[str],
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
    config = get_cloud_config()
    response = requests.get(
        f"{config.url}/rest/v1/{TABLE_CONTACTS}",
        headers=_rest_headers(),
        params={
            "select": (
                "id,prospect_id,contact_type,contact_value,contact_label,"
                "source_type,source_url,confidence,verification_status,"
                "is_primary,do_not_contact,collected_at,verified_at,updated_at"
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
        if str(item.get("contact_type") or "") in {"phone", "email", "website"}
        and str(item.get("contact_value") or "").strip()
        and str(item.get("verification_status") or "") != "rejected"
    ]
    if not valid_contacts:
        return 0

    existing = list_contacts_for_prospects([prospect_id])
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
