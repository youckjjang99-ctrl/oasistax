"""One central, non-destructive view of OASIS follow-up work."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import crm
import work_task_repository


SEOUL = ZoneInfo("Asia/Seoul")
_AUTOMATED_PAGE_SIZE = 500
_SALES_PAGE_SIZE = 1000
_MAX_PAGE_COUNT = 100
_STATUS_LABELS = {
    "scheduled": "예정",
    "pending": "대기",
    "in_progress": "진행 중",
    "completed": "완료",
    "cancelled": "취소",
}
_TASK_TYPE_LABELS = {
    "guidance_followup": "검토신청 후속",
    "claim_tax_review": "경정청구 검토",
}


def _base_date(value: date | datetime | str | None) -> date:
    if isinstance(value, datetime):
        return value.astimezone(SEOUL).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if value:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    return datetime.now(SEOUL).date()


def _parse_due(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(
                datetime.strptime(raw[:10], "%Y-%m-%d").date(),
                time.min,
            )
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SEOUL)
    return parsed.astimezone(SEOUL)


def _due_bucket(due: datetime, base: date) -> str:
    selected = due.astimezone(SEOUL).date()
    if selected < base:
        return "overdue"
    if selected == base:
        return "today"
    if selected <= base + timedelta(days=7):
        return "week"
    return "upcoming"


def _safe_company_name(value: Any) -> str:
    return str(value or "").strip()[:200]


def _automated_items(
    tasks: list[Mapping[str, Any]],
    base: date,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for task in tasks:
        due = _parse_due(task.get("due_at"))
        if due is None:
            continue
        task_type = str(task.get("task_type") or "").strip()
        status = str(task.get("status") or "").strip().lower()
        items.append(
            {
                "source": "automated",
                "title": str(task.get("title") or "자동 생성 업무").strip()[:200],
                "category": _TASK_TYPE_LABELS.get(task_type, "자동 생성 업무"),
                "company_name": "",
                "status": status,
                "status_label": _STATUS_LABELS.get(status, status),
                "priority": str(task.get("priority") or "normal").strip(),
                "due_at": due.isoformat(),
                "due_bucket": _due_bucket(due, base),
                "task_id": str(task.get("task_id") or "").strip(),
                "task_version": int(task.get("task_version") or 0),
                "task_type": task_type,
                "route": (
                    "경정청구" if task_type == "claim_tax_review" else "DB발굴"
                ),
            }
        )
    return items


def _crm_items(
    due_summary: Mapping[str, Any],
    base: date,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for bucket_name in ("overdue", "today", "week"):
        rows = due_summary.get(bucket_name, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            due = _parse_due(row.get("next_date"))
            if due is None:
                continue
            action = str(row.get("next_action") or "후속관리").strip()[:100]
            items.append(
                {
                    "source": "crm",
                    "title": f"CRM {action}",
                    "category": "고객 CRM",
                    "company_name": _safe_company_name(row.get("company_name")),
                    "status": str(row.get("status") or "").strip()[:80],
                    "status_label": str(row.get("status") or "").strip()[:80],
                    "priority": "normal",
                    "due_at": due.isoformat(),
                    "due_bucket": _due_bucket(due, base),
                    "route": "기업 컨설팅",
                }
            )
    return items


def _sales_items(
    assignments: list[Mapping[str, Any]],
    base: date,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for assignment in assignments:
        due = _parse_due(assignment.get("next_contact_at"))
        if due is None:
            continue
        items.append(
            {
                "source": "sales",
                "title": "영업 재연락",
                "category": "DB발굴 재연락",
                "company_name": _safe_company_name(
                    assignment.get("company_name")
                ),
                "status": "follow_up",
                "status_label": "재연락 예정",
                "priority": "normal",
                "due_at": due.isoformat(),
                "due_bucket": _due_bucket(due, base),
                "route": "DB발굴",
            }
        )
    return items


def _local_counts(items: list[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "open_count": len(items),
        "overdue_count": sum(item.get("due_bucket") == "overdue" for item in items),
        "today_count": sum(item.get("due_bucket") == "today" for item in items),
        "week_count": sum(item.get("due_bucket") == "week" for item in items),
        "upcoming_count": sum(item.get("due_bucket") == "upcoming" for item in items),
        "in_progress_count": 0,
        "completed_today_count": 0,
    }


def _automated_counts(items: list[Mapping[str, Any]]) -> dict[str, int]:
    return {
        "open_count": len(items),
        "overdue_count": sum(item.get("due_bucket") == "overdue" for item in items),
        "today_count": sum(item.get("due_bucket") == "today" for item in items),
        "week_count": sum(item.get("due_bucket") == "week" for item in items),
        "upcoming_count": sum(item.get("due_bucket") == "upcoming" for item in items),
        "in_progress_count": sum(
            item.get("status") == "in_progress" for item in items
        ),
        "completed_today_count": 0,
    }


def _load_automated_tasks(user_id: str, *, db: Any = None) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    overlap_found = False
    total_count = 0
    offset = 0

    for _ in range(_MAX_PAGE_COUNT):
        result = work_task_repository.list_my_work_tasks(
            user_id,
            limit=_AUTOMATED_PAGE_SIZE,
            offset=offset,
            db=db,
        )
        if not result.get("ok"):
            return {**result, "tasks": tasks, "total_count": total_count}

        page = [
            dict(row)
            for row in (result.get("tasks") or [])
            if isinstance(row, Mapping)
        ]
        try:
            total_count = max(total_count, int(result.get("total_count") or 0))
        except (TypeError, ValueError):
            total_count = max(total_count, len(tasks) + len(page))
        if not page:
            return {
                "ok": (
                    not overlap_found
                    and offset >= total_count
                    and len(tasks) >= total_count
                ),
                "tasks": tasks,
                "total_count": max(total_count, len(tasks)),
            }

        new_page = []
        for row in page:
            task_id = str(row.get("task_id") or "")
            if task_id in seen_task_ids:
                overlap_found = True
                continue
            seen_task_ids.add(task_id)
            new_page.append(row)
        if not new_page:
            return {"ok": False, "tasks": tasks, "total_count": total_count}
        tasks.extend(new_page)
        offset += len(page)
        if len(page) < _AUTOMATED_PAGE_SIZE or offset >= total_count:
            return {
                "ok": not overlap_found and len(tasks) >= total_count,
                "tasks": tasks,
                "total_count": max(total_count, offset),
            }

    return {"ok": False, "tasks": tasks, "total_count": max(total_count, offset)}


def _load_sales_followups(user_id: str, *, db: Any = None) -> dict[str, Any]:
    assignments: list[dict[str, Any]] = []
    seen_assignment_ids: set[str] = set()
    overlap_found = False
    cursor_at: str | None = None
    cursor_id: str | None = None

    for _ in range(_MAX_PAGE_COUNT):
        result = work_task_repository.list_my_sales_followups(
            user_id,
            limit=_SALES_PAGE_SIZE,
            after_next_contact_at=cursor_at,
            after_assignment_id=cursor_id,
            db=db,
        )
        if not result.get("ok"):
            return {**result, "assignments": assignments}

        page = [
            dict(row)
            for row in (result.get("assignments") or [])
            if isinstance(row, Mapping)
        ]
        if not page:
            return {
                "ok": not overlap_found,
                "assignments": assignments,
            }

        new_page = []
        for row in page:
            assignment_id = str(row.get("assignment_id") or "")
            if assignment_id in seen_assignment_ids:
                overlap_found = True
                continue
            seen_assignment_ids.add(assignment_id)
            new_page.append(row)
        if not new_page:
            return {"ok": False, "assignments": assignments}
        assignments.extend(new_page)
        cursor_at = str(page[-1].get("next_contact_at") or "")
        cursor_id = str(page[-1].get("assignment_id") or "")
        if not cursor_at or not cursor_id:
            return {"ok": False, "assignments": assignments}
        if len(page) < _SALES_PAGE_SIZE:
            return {
                "ok": not overlap_found,
                "assignments": assignments,
            }

    return {"ok": False, "assignments": assignments}


def build_work_inbox(
    user_id: str,
    *,
    db: Any = None,
    today: date | datetime | str | None = None,
    crm_restore_ok: bool | None = None,
) -> dict[str, Any]:
    """Compose automated work, local CRM dates, and sales follow-ups.

    CRM and sales rows remain in their existing source systems. This function
    reads them without writing, re-keying, or rebuilding their queues.
    """

    base = _base_date(today)
    today_text = base.isoformat()
    warnings: list[str] = []

    automated_result = _load_automated_tasks(user_id, db=db)
    automated_summary_result = work_task_repository.get_my_work_task_summary(
        user_id,
        db=db,
    )
    if not automated_result.get("ok"):
        warnings.append("자동 생성 업무를 불러오지 못했습니다.")
    if not automated_summary_result.get("ok") and not warnings:
        warnings.append("자동 생성 업무 요약을 불러오지 못했습니다.")

    crm_ok = True
    if crm_restore_ok is False:
        crm_ok = False
        warnings.append("CRM 클라우드 복원을 확인하지 못했습니다.")
    try:
        crm_due = crm.get_due_action_summary(user_id, today=today_text)
        if not isinstance(crm_due, Mapping):
            crm_due = {}
            crm_ok = False
            warnings.append("CRM 후속일정을 불러오지 못했습니다.")
    except Exception:
        crm_due = {}
        crm_ok = False
        warnings.append("CRM 후속일정을 불러오지 못했습니다.")

    sales_result = _load_sales_followups(user_id, db=db)
    if not sales_result.get("ok"):
        warnings.append("영업 재연락 일정을 불러오지 못했습니다.")

    automated = _automated_items(
        list(automated_result.get("tasks") or []),
        base,
    )
    local = _crm_items(crm_due, base)
    local.extend(
        _sales_items(list(sales_result.get("assignments") or []), base)
    )
    items = [*automated, *local]
    items.sort(
        key=lambda item: (
            0 if item.get("priority") == "high" else 1,
            str(item.get("due_at") or ""),
            str(item.get("source") or ""),
            str(item.get("title") or ""),
        )
    )

    automatic_fallback = _automated_counts(automated)
    automatic_summary = (
        dict(automated_summary_result.get("summary") or {})
        if automated_summary_result.get("ok")
        else automatic_fallback
    )
    local_summary = _local_counts(local)
    summary = {
        key: max(0, int(automatic_summary.get(key) or 0))
        + int(local_summary.get(key) or 0)
        for key in (
            "open_count",
            "overdue_count",
            "today_count",
            "week_count",
            "in_progress_count",
            "completed_today_count",
        )
    }
    summary["upcoming_count"] = (
        int(automatic_fallback["upcoming_count"])
        + int(local_summary["upcoming_count"])
    )

    return {
        "ok": bool(
            automated_result.get("ok")
            and automated_summary_result.get("ok")
            and crm_ok
            and sales_result.get("ok")
        ),
        "items": items,
        "summary": summary,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _format_due(value: Any) -> str:
    parsed = _parse_due(value)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else "일정 확인 필요"


def _default_navigate(target: str) -> None:
    import streamlit as st

    st.session_state["active_main_menu_v1020"] = target
    st.session_state["sidebar_menu_group_v1020"] = "주요업무"


def _defer_until_tomorrow() -> datetime:
    tomorrow = datetime.now(SEOUL).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(hour=9), tzinfo=SEOUL)


def render_work_inbox_page(
    user_id: str,
    user_name: str = "",
    *,
    navigate: Callable[[str], None] | None = None,
    crm_restore_ok: bool | None = None,
) -> None:
    import streamlit as st

    navigate_to = navigate or _default_navigate
    flash = st.session_state.pop("_work_inbox_flash_v912", None)
    if isinstance(flash, Mapping):
        message = str(flash.get("message") or "")
        if flash.get("ok"):
            st.success(message)
        else:
            st.error(message)

    st.markdown("### 중앙 업무함")
    st.caption(
        "자동 생성 업무, 고객 CRM 예정일, DB발굴 재연락 일정을 한곳에서 확인합니다. "
        "원본 고객·영업·수집 대기열은 변경하지 않습니다."
    )

    inbox = build_work_inbox(user_id, crm_restore_ok=crm_restore_ok)
    for warning in inbox.get("warnings", []):
        st.warning(str(warning))

    summary = dict(inbox.get("summary") or {})
    metric_columns = st.columns(4)
    metric_columns[0].metric("기한 경과", f"{int(summary.get('overdue_count', 0)):,}건")
    metric_columns[1].metric("오늘", f"{int(summary.get('today_count', 0)):,}건")
    metric_columns[2].metric("향후 7일", f"{int(summary.get('week_count', 0)):,}건")
    metric_columns[3].metric(
        "진행 중", f"{int(summary.get('in_progress_count', 0)):,}건"
    )

    items = list(inbox.get("items") or [])
    automated = [item for item in items if item.get("source") == "automated"]
    local_items = [item for item in items if item.get("source") != "automated"]

    st.markdown("#### 자동 생성 업무")
    if not automated:
        st.info(f"{user_name or '담당자'}님의 처리 대기 자동 업무가 없습니다.")
    for item in automated:
        task_id = str(item.get("task_id") or "")
        task_version = int(item.get("task_version") or 0)
        with st.container(border=True):
            st.markdown(f"**{item.get('title', '자동 생성 업무')}**")
            st.caption(
                f"{item.get('category', '자동 생성 업무')} · "
                f"{item.get('status_label', '')} · {_format_due(item.get('due_at'))}"
            )
            action_columns = st.columns(4)
            if action_columns[0].button(
                "업무 시작",
                key=f"work_start_{task_id}",
                disabled=item.get("status") == "in_progress",
                use_container_width=True,
            ):
                result = work_task_repository.start_work_task(
                    user_id, task_id, task_version
                )
                st.session_state["_work_inbox_flash_v912"] = result
                st.rerun()
            if action_columns[1].button(
                "완료",
                key=f"work_complete_{task_id}",
                use_container_width=True,
            ):
                result = work_task_repository.complete_work_task(
                    user_id, task_id, task_version
                )
                st.session_state["_work_inbox_flash_v912"] = result
                st.rerun()
            if action_columns[2].button(
                "내일 09시",
                key=f"work_defer_{task_id}",
                use_container_width=True,
            ):
                result = work_task_repository.defer_work_task(
                    user_id,
                    task_id,
                    task_version,
                    _defer_until_tomorrow(),
                )
                st.session_state["_work_inbox_flash_v912"] = result
                st.rerun()
            action_columns[3].button(
                "원본 업무 열기",
                key=f"work_route_{task_id}",
                on_click=navigate_to,
                args=(str(item.get("route") or "홈"),),
                use_container_width=True,
            )

    st.markdown("#### CRM·영업 후속일정")
    st.caption(
        "이 항목은 기존 CRM과 영업배정의 원본 일정입니다. 상태 변경은 원본 화면에서 처리합니다."
    )
    if local_items:
        display_rows = [
            {
                "구분": item.get("category", ""),
                "업체": item.get("company_name", ""),
                "업무": item.get("title", ""),
                "예정": _format_due(item.get("due_at")),
                "상태": item.get("status_label", ""),
            }
            for item in local_items
        ]
        st.dataframe(display_rows, hide_index=True, use_container_width=True)
        route_columns = st.columns(2)
        route_columns[0].button(
            "기업 컨설팅 CRM 열기",
            key="work_inbox_open_crm_v912",
            on_click=navigate_to,
            args=("기업 컨설팅",),
            use_container_width=True,
        )
        route_columns[1].button(
            "DB발굴 재연락 열기",
            key="work_inbox_open_sales_v912",
            on_click=navigate_to,
            args=("DB발굴",),
            use_container_width=True,
        )
    else:
        st.info("오늘부터 향후 일정에 등록된 CRM·영업 후속업무가 없습니다.")


__all__ = ["build_work_inbox", "render_work_inbox_page"]
