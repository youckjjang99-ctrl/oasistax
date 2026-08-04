"""Safe client for the assignee-scoped OASIS central work inbox RPCs.

Only fixed task metadata crosses this boundary. Raw Supabase responses and
exception bodies are never returned to the UI because transport errors can
contain request or database details.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Mapping, Sequence
import uuid

from cloud_db import CloudDatabase


RPC_FEATURE_READY = "oasis_work_inbox_feature_ready"
RPC_LIST = "oasis_list_my_work_tasks"
RPC_SALES_FOLLOWUPS = "oasis_list_my_sales_followups"
RPC_SUMMARY = "oasis_get_my_work_task_summary"
RPC_TRANSITION = "oasis_transition_my_work_task"

OPEN_STATUSES = ("scheduled", "pending", "in_progress")
ALL_STATUSES = frozenset((*OPEN_STATUSES, "completed", "cancelled"))
ALLOWED_ACTIONS = frozenset(("start", "complete", "defer"))

_SAFE_CODE_PATTERN = re.compile(r"^[A-Z0-9_]{1,80}$")
_TASK_FIELDS = frozenset(
    (
        "task_id",
        "task_type",
        "title",
        "priority",
        "status",
        "due_at",
        "completed_at",
        "task_version",
        "updated_at",
        "total_count",
    )
)
_SUMMARY_FIELDS = frozenset(
    (
        "open_count",
        "overdue_count",
        "today_count",
        "week_count",
        "in_progress_count",
        "completed_today_count",
    )
)
_SALES_FOLLOWUP_FIELDS = frozenset(
    ("assignment_id", "company_name", "next_contact_at")
)
_FAILURE_MESSAGES = {
    "FEATURE_NOT_READY": "중앙 업무함이 아직 데이터베이스에 적용되지 않았습니다.",
    "PERMISSION_DENIED": "이 업무를 조회하거나 변경할 권한이 없습니다.",
    "SERVICE_UNAVAILABLE": "중앙 업무함에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.",
    "INVALID_INPUT": "업무 처리값을 확인해 주세요.",
    "MALFORMED_RESPONSE": "업무 처리 결과를 확인하지 못했습니다.",
    "NOT_FOUND": "업무가 없거나 현재 담당 업무가 아닙니다.",
    "STALE_TASK": "업무가 다른 화면에서 변경되었습니다. 새로고침 후 다시 시도해 주세요.",
    "TASK_TERMINAL": "이미 완료되거나 취소된 업무입니다.",
    "INVALID_REQUEST": "업무 처리값을 확인해 주세요.",
}
_SUCCESS_MESSAGES = {
    "STARTED": "업무를 진행 중으로 변경했습니다.",
    "COMPLETED": "업무를 완료했습니다.",
    "DEFERRED": "업무 예정일을 변경했습니다.",
    "ALREADY_IN_PROGRESS": "이미 진행 중인 업무입니다.",
    "ALREADY_COMPLETED": "이미 완료된 업무입니다.",
    "ALREADY_DEFERRED": "이미 같은 예정일로 변경된 업무입니다.",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _user_id(value: Any) -> str:
    selected = _text(value).lower()
    if not selected or len(selected) > 200:
        raise ValueError("invalid user")
    return selected


def _task_id(value: Any) -> str:
    selected = _text(value)
    try:
        parsed = uuid.UUID(selected)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid task") from exc
    canonical = str(parsed)
    if selected.lower() != canonical:
        raise ValueError("invalid task")
    return canonical


def _positive_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("invalid version")
    if isinstance(value, int):
        selected = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value.strip()):
        selected = int(value.strip())
    else:
        raise ValueError("invalid version")
    if selected < 1:
        raise ValueError("invalid version")
    return selected


def _utc_iso(value: datetime | str | None) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = _text(value)
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _rows(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [dict(row) for row in raw if isinstance(row, Mapping)]
    if isinstance(raw, Mapping):
        for key in ("rows", "items", "tasks", "results"):
            nested = raw.get(key)
            if isinstance(nested, list):
                return [
                    dict(row) for row in nested if isinstance(row, Mapping)
                ]
        return [dict(raw)]
    return []


def _first_row(raw: Any) -> dict[str, Any] | None:
    rows = _rows(raw)
    return rows[0] if rows else None


def _safe_code(value: Any, default: str) -> str:
    selected = _text(value).upper()
    return selected if _SAFE_CODE_PATTERN.fullmatch(selected) else default


def _safe_failure(exc: Exception) -> dict[str, Any]:
    marker = str(exc or "").upper()
    if (
        "PGRST202" in marker
        or "COULD NOT FIND THE FUNCTION" in marker
        or "SCHEMA CACHE" in marker
    ):
        code = "FEATURE_NOT_READY"
    elif "42501" in marker or "PERMISSION_DENIED" in marker:
        code = "PERMISSION_DENIED"
    else:
        code = "SERVICE_UNAVAILABLE"
    return {
        "ok": False,
        "code": code,
        "message": _FAILURE_MESSAGES[code],
        "warning": _FAILURE_MESSAGES[code],
        "fallback_required": code == "FEATURE_NOT_READY",
    }


def _rpc(
    function_name: str,
    parameters: dict[str, Any],
    *,
    db: CloudDatabase | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    try:
        return (db or CloudDatabase()).rpc(function_name, parameters), None
    except Exception as exc:
        return None, _safe_failure(exc)


def _invalid_result() -> dict[str, Any]:
    code = "INVALID_INPUT"
    return {
        "ok": False,
        "code": code,
        "message": _FAILURE_MESSAGES[code],
        "warning": _FAILURE_MESSAGES[code],
        "fallback_required": False,
    }


def work_inbox_feature_ready(
    *,
    db: CloudDatabase | None = None,
) -> tuple[bool, str]:
    raw, error = _rpc(RPC_FEATURE_READY, {}, db=db)
    if error:
        return False, str(error["message"])
    if isinstance(raw, bool):
        return raw, "" if raw else _FAILURE_MESSAGES["FEATURE_NOT_READY"]
    row = _first_row(raw)
    ready = bool(row and row.get("oasis_work_inbox_feature_ready") is True)
    if row and row.get("ready") is True:
        ready = True
    return ready, "" if ready else _FAILURE_MESSAGES["FEATURE_NOT_READY"]


def list_my_work_tasks(
    current_user_id: str,
    *,
    statuses: Sequence[str] | None = None,
    limit: int = 100,
    offset: int = 0,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    try:
        actor = _user_id(current_user_id)
        selected_statuses = list(OPEN_STATUSES if statuses is None else statuses)
        normalized_statuses = [_text(value).lower() for value in selected_statuses]
        if any(value not in ALL_STATUSES for value in normalized_statuses):
            return {**_invalid_result(), "tasks": [], "total_count": 0}
        selected_limit = max(1, min(int(limit), 500))
        selected_offset = max(0, int(offset))
    except (TypeError, ValueError):
        return {**_invalid_result(), "tasks": [], "total_count": 0}

    raw, error = _rpc(
        RPC_LIST,
        {
            "p_current_user_id": actor,
            "p_statuses": normalized_statuses,
            "p_limit": selected_limit,
            "p_offset": selected_offset,
        },
        db=db,
    )
    if error:
        return {**error, "tasks": [], "total_count": 0}

    tasks: list[dict[str, Any]] = []
    total_count = 0
    for raw_row in _rows(raw):
        task = {
            key: value for key, value in raw_row.items() if key in _TASK_FIELDS
        }
        task_id = _text(task.get("task_id"))
        task_status = _text(task.get("status")).lower()
        try:
            task_version = _positive_int(task.get("task_version"))
            _task_id(task_id)
        except ValueError:
            continue
        if task_status not in ALL_STATUSES:
            continue
        task["task_id"] = task_id
        task["status"] = task_status
        task["task_version"] = task_version
        try:
            row_total = max(0, int(task.get("total_count") or 0))
        except (TypeError, ValueError):
            row_total = 0
        total_count = max(total_count, row_total)
        tasks.append(task)

    return {
        "ok": True,
        "code": "OK",
        "message": "내 자동 업무를 불러왔습니다.",
        "warning": "",
        "fallback_required": False,
        "tasks": tasks,
        "total_count": total_count or len(tasks),
    }


def get_my_work_task_summary(
    current_user_id: str,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    try:
        actor = _user_id(current_user_id)
    except ValueError:
        return {**_invalid_result(), "summary": {}}
    raw, error = _rpc(
        RPC_SUMMARY,
        {"p_current_user_id": actor},
        db=db,
    )
    if error:
        return {**error, "summary": {}}
    row = _first_row(raw)
    if row is None:
        code = "MALFORMED_RESPONSE"
        return {
            "ok": False,
            "code": code,
            "message": _FAILURE_MESSAGES[code],
            "warning": _FAILURE_MESSAGES[code],
            "fallback_required": False,
            "summary": {},
        }
    summary: dict[str, int] = {}
    for key in _SUMMARY_FIELDS:
        try:
            summary[key] = max(0, int(row.get(key) or 0))
        except (TypeError, ValueError):
            summary[key] = 0
    return {
        "ok": True,
        "code": "OK",
        "message": "업무 요약을 불러왔습니다.",
        "warning": "",
        "fallback_required": False,
        "summary": summary,
    }


def list_my_sales_followups(
    current_user_id: str,
    *,
    limit: int = 1000,
    after_next_contact_at: datetime | str | None = None,
    after_assignment_id: str | None = None,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    try:
        actor = _user_id(current_user_id)
        selected_limit = max(1, min(int(limit), 1000))
        selected_after_at = _utc_iso(after_next_contact_at)
        selected_after_id = (
            _task_id(after_assignment_id) if after_assignment_id else None
        )
        if (selected_after_at is None) != (selected_after_id is None):
            raise ValueError("invalid cursor")
    except (TypeError, ValueError):
        return {**_invalid_result(), "assignments": []}

    raw, error = _rpc(
        RPC_SALES_FOLLOWUPS,
        {
            "p_current_user_id": actor,
            "p_limit": selected_limit,
            "p_after_next_contact_at": selected_after_at,
            "p_after_assignment_id": selected_after_id,
        },
        db=db,
    )
    if error:
        return {**error, "assignments": []}

    assignments: list[dict[str, Any]] = []
    for raw_row in _rows(raw):
        assignment = {
            key: value
            for key, value in raw_row.items()
            if key in _SALES_FOLLOWUP_FIELDS
        }
        try:
            assignment_id = _task_id(assignment.get("assignment_id"))
            next_contact_at = _utc_iso(assignment.get("next_contact_at"))
            if next_contact_at is None:
                raise ValueError("missing follow-up time")
        except ValueError:
            continue
        assignment["assignment_id"] = assignment_id
        assignment["company_name"] = _text(
            assignment.get("company_name")
        )[:500]
        assignment["next_contact_at"] = next_contact_at
        assignments.append(assignment)

    return {
        "ok": True,
        "code": "OK",
        "message": "영업 재연락 일정을 불러왔습니다.",
        "warning": "",
        "fallback_required": False,
        "assignments": assignments,
    }


def transition_my_work_task(
    current_user_id: str,
    task_id: str,
    action: str,
    expected_version: int,
    *,
    defer_until: datetime | str | None = None,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    try:
        actor = _user_id(current_user_id)
        selected_task = _task_id(task_id)
        selected_action = _text(action).lower()
        selected_version = _positive_int(expected_version)
        if selected_action not in ALLOWED_ACTIONS:
            raise ValueError("invalid action")
        selected_defer = _utc_iso(defer_until)
        if (selected_action == "defer") != (selected_defer is not None):
            raise ValueError("invalid defer")
    except ValueError:
        return {**_invalid_result(), "task": {}}

    raw, error = _rpc(
        RPC_TRANSITION,
        {
            "p_current_user_id": actor,
            "p_task_id": selected_task,
            "p_action": selected_action,
            "p_expected_version": selected_version,
            "p_defer_until": selected_defer,
        },
        db=db,
    )
    if error:
        return {**error, "task": {}}
    row = _first_row(raw)
    if row is None:
        code = "MALFORMED_RESPONSE"
        return {
            "ok": False,
            "code": code,
            "message": _FAILURE_MESSAGES[code],
            "warning": _FAILURE_MESSAGES[code],
            "fallback_required": False,
            "task": {},
        }
    ok_value = row.get("success")
    ok = ok_value is True or _text(ok_value).lower() in {"true", "t", "1"}
    code = _safe_code(row.get("code"), "OK" if ok else "INVALID_REQUEST")
    task = {key: value for key, value in row.items() if key in _TASK_FIELDS}
    message = (
        _SUCCESS_MESSAGES.get(code, "업무 상태를 반영했습니다.")
        if ok
        else _FAILURE_MESSAGES.get(
            code,
            "업무를 처리하지 못했습니다. 새로고침 후 다시 시도해 주세요.",
        )
    )
    return {
        "ok": ok,
        "code": code,
        "message": message,
        "warning": "" if ok else message,
        "fallback_required": False,
        "task": task,
    }


def start_work_task(
    current_user_id: str,
    task_id: str,
    expected_version: int,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    return transition_my_work_task(
        current_user_id,
        task_id,
        "start",
        expected_version,
        db=db,
    )


def complete_work_task(
    current_user_id: str,
    task_id: str,
    expected_version: int,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    return transition_my_work_task(
        current_user_id,
        task_id,
        "complete",
        expected_version,
        db=db,
    )


def defer_work_task(
    current_user_id: str,
    task_id: str,
    expected_version: int,
    defer_until: datetime | str,
    *,
    db: CloudDatabase | None = None,
) -> dict[str, Any]:
    return transition_my_work_task(
        current_user_id,
        task_id,
        "defer",
        expected_version,
        defer_until=defer_until,
        db=db,
    )


__all__ = [
    "ALL_STATUSES",
    "OPEN_STATUSES",
    "RPC_FEATURE_READY",
    "RPC_LIST",
    "RPC_SALES_FOLLOWUPS",
    "RPC_SUMMARY",
    "RPC_TRANSITION",
    "complete_work_task",
    "defer_work_task",
    "get_my_work_task_summary",
    "list_my_sales_followups",
    "list_my_work_tasks",
    "start_work_task",
    "transition_my_work_task",
    "work_inbox_feature_ready",
]
