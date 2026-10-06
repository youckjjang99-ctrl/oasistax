"""Pure presentation helpers for the owner-scoped, non-dialing voice dashboard."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


CAMPAIGN_STATUS_LABELS = {
    "draft": "시작 전 · 승인 대기",
    "running": "자동발신 진행",
    "paused": "일시정지",
    "cancelled": "종료 · 남은 요청 취소",
    "completed": "처리 완료",
}
METRIC_LABELS = {
    "total": "전체 요청",
    "queued": "승인 대기",
    "approved": "발신 대기",
    "active": "발신·통화 중",
    "completed": "통화 종료",
    "failed": "실패",
    "cancelled": "취소",
    "unknown": "결과 확인 필요",
    "visit_requests": "방문 요청",
}
SKIP_LABELS = {
    "ALREADY_QUEUED": "이미 접수된 요청",
    "DUPLICATE": "중복 요청",
    "DUPLICATE_REQUEST": "중복 요청",
    "ACTIVE_JOB": "이미 진행 중인 요청",
    "ALREADY_ACTIVE": "이미 진행 중인 요청",
    "DO_NOT_CALL": "수신거부",
    "NO_PERMISSION": "전화 안내 동의 확인 필요",
    "PERMISSION_REQUIRED": "전화 안내 동의 확인 필요",
    "CONSENT_REQUIRED": "전화 안내 동의 확인 필요",
    "CONSENT_EXPIRED": "전화 안내 동의 만료",
    "NOT_ASSIGNED": "담당 배정 확인 필요",
    "NOT_OWNER": "담당 배정 확인 필요",
    "NOT_AUTHORIZED": "접근 권한·담당 배정 확인 필요",
    "TARGET_CHANGED": "대상 정보·동의 조건 변경",
    "NOT_READY": "발신 조건 확인 필요",
    "INVALID_INPUT": "대상 정보 확인 필요",
    "NO_PHONE": "발신 가능한 번호 없음",
    "PHONE_INVALID": "번호 확인 필요",
    "INELIGIBLE": "발신 조건 확인 필요",
}


def skipped_summary(rows: Any) -> str:
    counts: dict[str, int] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping):
                label = SKIP_LABELS.get(str(row.get("code") or "").upper(), "상태 재확인 필요")
                counts[label] = counts.get(label, 0) + 1
    return " · ".join(f"{label} {count}건" for label, count in counts.items())


def selection_limit(admin: bool) -> int:
    return 100 if admin else 30


def safe_count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def duration_label(value: Any) -> str:
    if value is None:
        return "-"
    seconds = safe_count(value)
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}분 {seconds:02d}초" if minutes else f"{seconds}초"


def campaign_status_label(row: Mapping[str, Any]) -> str:
    total = safe_count(row.get("total"))
    unfinished = sum(safe_count(row.get(key)) for key in ("queued", "approved", "active", "unknown"))
    terminal = sum(safe_count(row.get(key)) for key in ("completed", "failed", "cancelled"))
    if row.get("status") != "cancelled" and total > 0 and terminal >= total and not unfinished:
        return "처리 완료"
    return CAMPAIGN_STATUS_LABELS.get(str(row.get("status") or ""), "확인 필요")


def campaign_display(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "캠페인": str(row.get("name") or "이름 없는 캠페인"),
            "상태": campaign_status_label(row),
            **{label: safe_count(row.get(key)) for key, label in METRIC_LABELS.items()},
        }
        for row in rows
    ]


def merged_selection(previous: Sequence[str], visible: Sequence[str], checked: Sequence[str]) -> list[str]:
    """Only the visible editor rows change; selections on other pages survive."""
    visible_set = set(visible)
    return list(dict.fromkeys([uid for uid in previous if uid not in visible_set] + [uid for uid in checked if uid in visible_set]))


def filtered_candidates(rows: Sequence[Mapping[str, Any]], query: str, eligible_only: bool, eligibility: Any) -> list[dict[str, Any]]:
    needle = str(query or "").strip().casefold()
    return [dict(row) for row in rows if (not needle or needle in str(row.get("company_name") or "").casefold()) and (not eligible_only or eligibility(row))]
