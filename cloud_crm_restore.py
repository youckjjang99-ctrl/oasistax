from __future__ import annotations

from typing import Any

from cloud_db import (
    CloudDatabase,
    TABLE_CRM,
    TABLE_CUSTOMERS,
    cloud_is_configured,
)
from crm import (
    load_crm_data,
    make_customer_key,
    save_crm_data,
)
from crm_enhancements import save_crm_profiles_bulk


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return text


def _timeline_signature(item: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _clean(item.get("title", item.get("제목", ""))),
        _clean(item.get("detail", item.get("내용", ""))),
        _clean(item.get("created_at", item.get("at", item.get("일시", "")))),
    )


def restore_crm_from_cloud(user_id: str) -> dict[str, Any]:
    """
    Streamlit Cloud 재배포 후 Supabase CRM 자료를 기존 로컬 CRM 구조로 복원한다.

    - Supabase CRM을 우선 조회한다.
    - 기존 로컬 CRM 저장 함수만 사용해 호환성을 유지한다.
    - 기존 고객DB는 수정하지 않는다.
    """
    result = {
        "restored": 0,
        "profiles": 0,
        "timelines": 0,
        "message": "",
    }

    if not user_id:
        result["message"] = "사용자 ID가 없습니다."
        return result

    local_data = load_crm_data(user_id)
    local_customers = local_data.get("customers", {})
    if isinstance(local_customers, dict) and local_customers:
        result["message"] = "기존 로컬 CRM 자료를 사용합니다."
        return result

    if not cloud_is_configured():
        result["message"] = "Supabase가 설정되지 않아 CRM 자동복원을 건너뛰었습니다."
        return result

    try:
        database = CloudDatabase()
        customer_rows = database.select_all(
            TABLE_CUSTOMERS,
            filters={"owner_user_id": user_id},
            columns="business_no,company_name,customer_data",
            order="id.asc",
            max_rows=50000,
        )
        crm_rows = database.select_all(
            TABLE_CRM,
            filters={"owner_user_id": user_id},
            columns="business_no,crm_data,updated_at",
            order="id.asc",
            max_rows=50000,
        )
    except Exception as exc:
        result["message"] = f"Supabase CRM 조회 실패: {exc}"
        return result

    company_map: dict[str, str] = {}
    for row in customer_rows:
        business_no = _clean(row.get("business_no"))
        customer_data = row.get("customer_data", {})
        if not isinstance(customer_data, dict):
            customer_data = {}
        company_name = (
            _clean(row.get("company_name"))
            or _clean(customer_data.get("업체명"))
        )
        if business_no:
            company_map[business_no] = company_name

    local_customers = local_data.setdefault("customers", {})
    profile_updates: dict[str, dict[str, Any]] = {}

    for row in crm_rows:
        business_no = _clean(row.get("business_no"))
        crm_data = row.get("crm_data", {})
        if not isinstance(crm_data, dict):
            continue

        company_name = (
            company_map.get(business_no, "")
            or _clean(crm_data.get("company_name"))
            or _clean(crm_data.get("업체명"))
        )
        customer_key = make_customer_key(company_name, business_no)

        status = _clean(crm_data.get("status")) or "신규"
        next_action = _clean(crm_data.get("next_action")) or "없음"
        next_date = _clean(
            crm_data.get("next_date", crm_data.get("next_action_date", ""))
        )
        memo = _clean(crm_data.get("memo"))

        local_record = local_customers.get(customer_key, {})
        if not isinstance(local_record, dict):
            local_record = {}
        local_record.update({
            "company_name": company_name,
            "business_no": business_no,
            "status": status,
            "next_action": next_action,
            "next_date": next_date,
            "memo": memo,
            "updated_at": _clean(row.get("updated_at")),
        })
        local_customers[customer_key] = local_record
        result["restored"] += 1

        profile = crm_data.get("_v44_profile", {})
        if isinstance(profile, dict) and profile:
            profile_updates[customer_key] = dict(profile)
            result["profiles"] += 1

        cloud_timeline = crm_data.get(
            "timeline",
            crm_data.get("timelines", []),
        )
        if not isinstance(cloud_timeline, list):
            continue

        local_timeline = local_record.get(
            "timeline",
            local_record.get("timelines", []),
        )
        if not isinstance(local_timeline, list):
            local_timeline = []

        existing_signatures = {
            _timeline_signature(item)
            for item in local_timeline
            if isinstance(item, dict)
        }

        for item in cloud_timeline:
            if not isinstance(item, dict):
                continue
            signature = _timeline_signature(item)
            if signature in existing_signatures:
                continue

            title = signature[0] or "상담이력"
            detail = signature[1]
            if not detail:
                continue

            local_timeline.insert(0, {
                "at": signature[2],
                "title": title,
                "detail": detail,
            })
            result["timelines"] += 1
            existing_signatures.add(signature)

        local_record["timeline"] = local_timeline[:80]
        local_customers[customer_key] = local_record

    if crm_rows:
        save_crm_data(user_id, local_data)
    if profile_updates:
        save_crm_profiles_bulk(user_id, profile_updates)

    result["message"] = (
        f"Supabase CRM {result['restored']}건, "
        f"확장정보 {result['profiles']}건을 자동 복원했습니다."
    )
    return result
