from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from cloud_db import CloudDatabase, cloud_is_configured
from crm import (
    get_crm_file_path,
    load_crm_data,
    make_customer_key,
)
from customer_history import save_customer_event
from utils import get_user_dirs

TABLE_CUSTOMER_TRASH = "oasis_customer_trash"


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return text


def _business_digits(value: Any) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def _normalize_for_search(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def customer_uid(row: pd.Series | dict[str, Any]) -> str:
    business_no = _business_digits(row.get("사업자등록번호", ""))
    if business_no:
        return f"biz:{business_no}"

    company = _normalize_for_search(row.get("업체명", ""))
    representative = _normalize_for_search(row.get("대표자명", ""))
    return f"company:{company}|rep:{representative}"


def _trash_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "customer_trash.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _load_local_trash(user_id: str) -> dict[str, dict[str, Any]]:
    data = _load_json(_trash_path(user_id), {})
    return data if isinstance(data, dict) else {}


def _save_local_trash(
    user_id: str,
    records: dict[str, dict[str, Any]],
) -> None:
    _save_json(_trash_path(user_id), records)


def load_trash_records(
    user_id: str,
    sync_cloud: bool = True,
) -> dict[str, dict[str, Any]]:
    records = _load_local_trash(user_id)

    if sync_cloud and cloud_is_configured():
        try:
            rows = CloudDatabase().select(
                TABLE_CUSTOMER_TRASH,
                filters={"owner_user_id": user_id},
                order="updated_at.desc",
                limit=5000,
            )
            for row in rows:
                uid = str(row.get("customer_uid", "") or "")
                if uid:
                    records[uid] = dict(row)
            _save_local_trash(user_id, records)
        except Exception:
            pass

    return records


def _upsert_trash_record(
    user_id: str,
    record: dict[str, Any],
) -> tuple[bool, str]:
    records = _load_local_trash(user_id)
    records[record["customer_uid"]] = record
    _save_local_trash(user_id, records)

    if not cloud_is_configured():
        return True, "로컬 휴지통에 저장했습니다."

    try:
        CloudDatabase().upsert(
            TABLE_CUSTOMER_TRASH,
            [record],
            "owner_user_id,customer_uid",
        )
        return True, "로컬과 Supabase 휴지통에 저장했습니다."
    except Exception as exc:
        return True, (
            "로컬 휴지통에 저장했습니다. "
            f"Supabase 동기화는 보류되었습니다: {exc}"
        )


def active_customers(
    user_id: str,
    customers: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    records = load_trash_records(user_id)
    deleted_uids = {
        uid
        for uid, record in records.items()
        if bool(record.get("is_deleted", False))
    }
    if not deleted_uids:
        return customers.copy(), records

    mask = [
        customer_uid(row) not in deleted_uids
        for _, row in customers.iterrows()
    ]
    return customers.loc[mask].copy(), records


def soft_delete_customer(
    user_id: str,
    user_name: str,
    selected_row: pd.Series,
    reason: str,
) -> tuple[bool, str]:
    company_name = _clean(selected_row.get("업체명", ""))
    business_no = _clean(selected_row.get("사업자등록번호", ""))
    representative = _clean(selected_row.get("대표자명", ""))
    uid = customer_uid(selected_row)
    now = datetime.now().isoformat(timespec="seconds")

    snapshot = {
        str(key): (
            None
            if pd.isna(value)
            else value
        )
        for key, value in selected_row.to_dict().items()
    }

    record = {
        "owner_user_id": user_id,
        "customer_uid": uid,
        "business_no": business_no,
        "company_name": company_name,
        "representative_name": representative,
        "is_deleted": True,
        "delete_reason": reason.strip(),
        "deleted_by": user_name or user_id,
        "deleted_at": now,
        "restored_at": None,
        "snapshot_data": snapshot,
        "updated_at": now,
    }
    success, message = _upsert_trash_record(user_id, record)

    if success:
        try:
            save_customer_event(
                user_id=user_id,
                business_no=business_no,
                company_name=company_name,
                event_id=f"customer-trash-{uid}-{now}",
                event_title=f"{now[:10]} 고객 휴지통 이동",
                event_detail=(
                    f"처리자: {user_name or user_id}\n"
                    f"사유: {reason.strip()}\n"
                    "고객DB 원본과 상담·CRM·정관·히스토리는 보존"
                ),
                occurred_at=now,
                source="customer_trash",
            )
        except Exception:
            pass

    return success, message


def restore_customer(
    user_id: str,
    user_name: str,
    record: dict[str, Any],
) -> tuple[bool, str]:
    now = datetime.now().isoformat(timespec="seconds")
    restored = dict(record)
    restored.update(
        {
            "owner_user_id": user_id,
            "is_deleted": False,
            "restored_at": now,
            "updated_at": now,
        }
    )
    success, message = _upsert_trash_record(user_id, restored)

    if success:
        try:
            save_customer_event(
                user_id=user_id,
                business_no=str(record.get("business_no", "") or ""),
                company_name=str(record.get("company_name", "") or ""),
                event_id=(
                    f"customer-restore-"
                    f"{record.get('customer_uid', '')}-{now}"
                ),
                event_title=f"{now[:10]} 고객 휴지통 복원",
                event_detail=f"복원 처리자: {user_name or user_id}",
                occurred_at=now,
                source="customer_trash",
            )
        except Exception:
            pass

    return success, message


def _profile_map(user_id: str) -> dict[str, dict[str, Any]]:
    path = get_user_dirs(user_id)["base"] / "customer_crm_profiles.json"
    data = _load_json(path, {})
    return data if isinstance(data, dict) else {}


def _record_last_activity(
    crm_record: dict[str, Any],
    profile: dict[str, Any],
) -> datetime | None:
    candidates = [
        crm_record.get("updated_at", ""),
        profile.get("updated_at", ""),
    ]
    timeline = crm_record.get("timeline", []) or []
    if timeline and isinstance(timeline[0], dict):
        candidates.append(timeline[0].get("at", ""))

    parsed = []
    for value in candidates:
        text = str(value or "").strip()
        if not text:
            continue
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                parsed.append(datetime.strptime(text[:19], fmt))
                break
            except ValueError:
                continue
    return max(parsed) if parsed else None


def _customer_directory(
    user_id: str,
    customers: pd.DataFrame,
) -> list[dict[str, Any]]:
    crm_customers = (
        load_crm_data(user_id).get("customers", {})
        if get_crm_file_path(user_id)
        else {}
    )
    profiles = _profile_map(user_id)
    today = date.today()
    directory = []

    for index, row in customers.iterrows():
        company = _clean(row.get("업체명", ""))
        business_no = _clean(row.get("사업자등록번호", ""))
        key = make_customer_key(company, business_no)
        crm_record = crm_customers.get(key, {})
        if not isinstance(crm_record, dict):
            crm_record = {}
        profile = profiles.get(key, {})
        if not isinstance(profile, dict):
            profile = {}

        next_date_text = str(crm_record.get("next_date", "") or "")[:10]
        next_date = None
        try:
            next_date = datetime.strptime(
                next_date_text,
                "%Y-%m-%d",
            ).date()
        except ValueError:
            pass

        last_activity = _record_last_activity(crm_record, profile)
        stale_days = (
            (datetime.now() - last_activity).days
            if last_activity
            else None
        )
        priority = str(profile.get("priority", "3") or "3")
        manager = _clean(profile.get("assigned_manager", ""))
        status = _clean(crm_record.get("status", "신규")) or "신규"
        stage = _clean(profile.get("pipeline_stage", "신규")) or "신규"

        search_values = [
            company,
            row.get("대표자명", ""),
            business_no,
            row.get("업종명", ""),
            row.get("사업장 소재지", ""),
            row.get("전화번호", ""),
            row.get("휴대전화", ""),
            row.get("연락처", ""),
            row.get("담당자", ""),
            row.get("이메일", ""),
            manager,
            status,
            stage,
        ]

        directory.append(
            {
                "index": index,
                "company_name": company,
                "business_no": business_no,
                "representative": _clean(row.get("대표자명", "")),
                "status": status,
                "stage": stage,
                "priority": priority,
                "manager": manager,
                "next_action": _clean(crm_record.get("next_action", "없음")),
                "next_date": next_date,
                "last_activity": last_activity,
                "stale_days": stale_days,
                "search_text": _normalize_for_search(
                    " ".join(str(value or "") for value in search_values)
                ),
                "is_today": next_date == today,
                "is_overdue": bool(next_date and next_date < today),
                "is_week": bool(
                    next_date
                    and today < next_date <= today + timedelta(days=7)
                ),
            }
        )

    return directory


def render_customer_directory(
    user_id: str,
    user_name: str,
    customers: pd.DataFrame,
) -> pd.DataFrame:
    active_df, trash_records = active_customers(user_id, customers)
    deleted_records = [
        record
        for record in trash_records.values()
        if bool(record.get("is_deleted", False))
    ]
    directory = _customer_directory(user_id, active_df)

    today_count = sum(1 for item in directory if item["is_today"])
    overdue_count = sum(1 for item in directory if item["is_overdue"])
    high_priority_count = sum(
        1
        for item in directory
        if str(item["priority"]) in {"4", "5"}
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("활성 고객", len(active_df))
    m2.metric("오늘 조치", today_count)
    m3.metric("기한 경과", overdue_count)
    m4.metric("휴지통", len(deleted_records))

    search = st.text_input(
        "기업 검색",
        placeholder=(
            "회사명·대표자·사업자번호·전화번호·지역·업종·담당자로 검색"
        ),
        key="enterprise_customer_search_v710",
    )

    statuses = sorted(
        {item["status"] for item in directory if item["status"]}
    )
    stages = sorted(
        {item["stage"] for item in directory if item["stage"]}
    )
    managers = sorted(
        {item["manager"] for item in directory if item["manager"]}
    )

    f1, f2, f3, f4 = st.columns(4)
    with f1:
        status_filter = st.selectbox(
            "CRM 상태",
            ["전체"] + statuses,
            key="enterprise_status_filter_v710",
        )
    with f2:
        stage_filter = st.selectbox(
            "진행단계",
            ["전체"] + stages,
            key="enterprise_stage_filter_v710",
        )
    with f3:
        priority_filter = st.selectbox(
            "중요도",
            ["전체", "5", "4", "3", "2", "1"],
            format_func=lambda value: (
                "전체" if value == "전체" else "★" * int(value)
            ),
            key="enterprise_priority_filter_v710",
        )
    with f4:
        manager_filter = st.selectbox(
            "담당자",
            ["전체"] + managers,
            key="enterprise_manager_filter_v710",
        )

    quick_filter = st.radio(
        "빠른 보기",
        [
            "전체 고객",
            "오늘 조치",
            "기한 경과",
            "향후 7일",
            "7일 이상 미접촉",
            "중요도 4~5",
            "진행 중 고객",
            "계약완료",
        ],
        horizontal=True,
        key="enterprise_quick_filter_v710",
    )

    search_key = _normalize_for_search(search)
    matched = []
    for item in directory:
        if search_key and search_key not in item["search_text"]:
            continue
        if (
            status_filter != "전체"
            and item["status"] != status_filter
        ):
            continue
        if (
            stage_filter != "전체"
            and item["stage"] != stage_filter
        ):
            continue
        if (
            priority_filter != "전체"
            and item["priority"] != priority_filter
        ):
            continue
        if (
            manager_filter != "전체"
            and item["manager"] != manager_filter
        ):
            continue

        if quick_filter == "오늘 조치" and not item["is_today"]:
            continue
        if quick_filter == "기한 경과" and not item["is_overdue"]:
            continue
        if quick_filter == "향후 7일" and not item["is_week"]:
            continue
        if quick_filter == "7일 이상 미접촉":
            if item["stale_days"] is None or item["stale_days"] < 7:
                continue
        if (
            quick_filter == "중요도 4~5"
            and item["priority"] not in {"4", "5"}
        ):
            continue
        if quick_filter == "진행 중 고객":
            if item["stage"] in {"신규", "보류", "계약완료"}:
                continue
        if (
            quick_filter == "계약완료"
            and item["stage"] != "계약완료"
            and item["status"] != "계약완료"
        ):
            continue

        matched.append(item)

    matched.sort(
        key=lambda item: (
            0 if item["is_overdue"] else 1,
            0 if item["is_today"] else 1,
            -int(item["priority"])
            if str(item["priority"]).isdigit()
            else -3,
            item["company_name"],
        )
    )

    st.caption(
        f"검색 결과 {len(matched)}개 · "
        f"고우선순위 {high_priority_count}개 · "
        "휴지통 이동 시 원본 고객DB와 연계자료는 삭제되지 않습니다."
    )

    with st.expander(
        f"휴지통 관리 · {len(deleted_records)}개",
        expanded=False,
    ):
        if not deleted_records:
            st.info("휴지통에 이동된 고객이 없습니다.")
        else:
            options = {
                (
                    f"{record.get('company_name', '기업명 미확인')} · "
                    f"{record.get('business_no', '-')} · "
                    f"{str(record.get('deleted_at', ''))[:19]}"
                ): record
                for record in deleted_records
            }
            selected_label = st.selectbox(
                "복원할 고객",
                list(options),
                key="enterprise_trash_customer_v710",
            )
            selected = options[selected_label]
            st.write(
                f"**삭제사유:** "
                f"{selected.get('delete_reason', '-')}"
            )
            st.write(
                f"**처리자:** {selected.get('deleted_by', '-')} · "
                f"**처리일:** {str(selected.get('deleted_at', ''))[:19]}"
            )
            if st.button(
                "선택 고객 복원",
                use_container_width=True,
                key="enterprise_restore_customer_v710",
            ):
                success, message = restore_customer(
                    user_id,
                    user_name,
                    selected,
                )
                if success:
                    st.success(message)
                    st.rerun()
                st.error(message)

    if not matched:
        return active_df.iloc[0:0].copy()

    return active_df.loc[
        [item["index"] for item in matched]
    ].copy()


def render_selected_customer_delete(
    user_id: str,
    user_name: str,
    selected_row: pd.Series,
) -> None:
    company_name = _clean(selected_row.get("업체명", ""))
    business_no = _clean(
        selected_row.get("사업자등록번호", "")
    )

    with st.expander(
        "선택 고객 관리·휴지통 이동",
        expanded=False,
    ):
        st.warning(
            "휴지통으로 이동하면 기업컨설팅 목록에서는 숨겨집니다. "
            "고객DB 원본, CRM, 상담일지, 정관, 정책자금 결과와 "
            "기업히스토리는 삭제되지 않으며 언제든 복원할 수 있습니다."
        )
        reason = st.text_input(
            "휴지통 이동 사유",
            placeholder="예: 중복 등록, 상담 종료, 테스트 고객",
            key=f"enterprise_delete_reason_v710_{customer_uid(selected_row)}",
        )
        confirmation = st.text_input(
            f"확인을 위해 업체명 `{company_name}` 입력",
            key=f"enterprise_delete_confirm_v710_{customer_uid(selected_row)}",
        )

        disabled = (
            not reason.strip()
            or confirmation.strip() != company_name
        )
        if st.button(
            "선택 고객 휴지통으로 이동",
            type="secondary",
            use_container_width=True,
            disabled=disabled,
            key=f"enterprise_delete_button_v710_{customer_uid(selected_row)}",
        ):
            success, message = soft_delete_customer(
                user_id,
                user_name,
                selected_row,
                reason,
            )
            if success:
                st.success(message)
                st.rerun()
            st.error(message)
