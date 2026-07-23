from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import requests

from cloud_db import CloudDatabase, get_cloud_config


TABLE_PROSPECTS = "oasis_prospect_companies"
TABLE_CUSTOMERS = "oasis_customers"


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


def _database_row(
    prospect: dict[str, Any],
    owner_user_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
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
        "source_data": prospect.get("원본데이터") or {},
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
            "id,company_name,business_no,address,region,industry_name,"
            "employee_count,new_employee_count,lost_employee_count,"
            "priority_score,priority_reasons,status,data_created_ym,updated_at"
        ),
        order="priority_score.desc,updated_at.desc",
        limit=min(1000, max(1, int(limit))),
    )

