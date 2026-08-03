from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
import streamlit as st

from cloud_db import (
    CloudDatabase,
    TABLE_BACKUP_RUNS,
    TABLE_RESTORE_DRILLS,
    cloud_is_configured,
)
from cloud_sync import get_cloud_sync_status
from customer_lifecycle import (
    archive_customer,
    list_archived_customers,
    reactivate_customer,
)
from sync_outbox import cloud_outbox_status


DATA_SAFETY_FEATURE_FLAG = "OASIS_DATA_SAFETY_V1"
_SAFE_SUMMARY_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SAFE_TARGET_NAMES = {
    "database",
    "auth",
    "storage",
    "documents",
    "audio",
    "crm",
    "customers",
    "knowledge",
    "automation",
    "audit_log",
    "history",
}


def data_safety_enabled() -> bool:
    return str(os.environ.get(DATA_SAFETY_FEATURE_FLAG, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _safe_numeric_summary(value: Any) -> dict[str, int | float | bool]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, int | float | bool] = {}
    for key, item in value.items():
        safe_key = str(key)
        if not _SAFE_SUMMARY_KEY.fullmatch(safe_key):
            continue
        if safe_key in {"missing", "missing_targets"}:
            continue
        if isinstance(item, bool):
            result[safe_key] = item
        elif isinstance(item, (int, float)) and item >= 0:
            result[safe_key] = item
        if len(result) >= 30:
            break
    return result


def _safe_missing_targets(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "미기록"
    raw = value.get("missing_targets", value.get("missing"))
    if raw is None:
        return "없음"
    if isinstance(raw, str):
        candidates: Sequence[Any] = [raw]
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        candidates = raw
    else:
        return "미기록"
    targets = sorted(
        {
            str(item).strip().lower()
            for item in candidates
            if str(item).strip().lower() in _SAFE_TARGET_NAMES
        }
    )
    return ", ".join(targets) if targets else "없음"


def _backup_integrity_status(row: Mapping[str, Any]) -> str:
    status = str(row.get("status") or "").lower()
    if status == "failed":
        return "실패"
    if status in {"pending", "running"}:
        return "진행 중"
    if status == "completed" and row.get("checksum_sha256"):
        return "체크섬 기록됨"
    if status == "completed":
        return "검증 증거 없음"
    return "미확인"


def _safe_backup_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = (
        "backup_type",
        "status",
        "started_at",
        "completed_at",
        "retention_until",
        "created_at",
    )
    safe_rows: list[dict[str, Any]] = []
    for row in rows:
        safe = {key: row.get(key) for key in allowed}
        safe["integrity_status"] = _backup_integrity_status(row)
        safe["size_bytes"] = row.get("size_bytes")
        safe["record_counts"] = _safe_numeric_summary(row.get("record_counts"))
        safe["missing_targets"] = _safe_missing_targets(row.get("record_counts"))
        safe_rows.append(safe)
    return safe_rows


def _safe_restore_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = (
        "environment_label",
        "status",
        "integrity_verified",
        "started_at",
        "completed_at",
        "created_at",
    )
    safe_rows: list[dict[str, Any]] = []
    for row in rows:
        safe = {key: row.get(key) for key in allowed}
        summary = row.get("result_summary")
        safe["result_summary"] = _safe_numeric_summary(summary)
        safe["missing_targets"] = _safe_missing_targets(summary)
        safe_rows.append(safe)
    return safe_rows


def _admin_sync_summary(current_user_id: str) -> dict[str, Any]:
    """Return safe global durable counts plus this instance's local fallback.

    The global durable queue deliberately has no owner filter so an administrator
    can see the operational backlog for the whole service.  Paths, payloads and
    error text are never copied into the returned summary.
    """
    local_status = get_cloud_sync_status(current_user_id)
    local = local_status.get("local") or {}
    try:
        durable = cloud_outbox_status(None)
        global_unavailable = False
    except Exception:
        durable = {}
        global_unavailable = True

    return {
        "global_queued": max(0, int(durable.get("queued", 0) or 0)),
        "global_dead_letter": max(
            0, int(durable.get("dead_letter", 0) or 0)
        ),
        "global_total": max(0, int(durable.get("total", 0) or 0)),
        "global_unavailable": global_unavailable,
        "durable_enabled": bool(durable.get("enabled")),
        "local_queued": max(0, int(local.get("queued", 0) or 0)),
        "local_dead_letter": max(0, int(local.get("dead_letter", 0) or 0)),
        "local_corrupted": bool(local.get("corrupted")),
    }


def _safe_customer_archive_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Allow only non-sensitive archive metadata in the operations table."""
    safe_rows: list[dict[str, Any]] = []
    for row in rows:
        customer_id = str(row.get("id") or "").strip()
        owner_id = str(row.get("owner_user_id") or "").strip()
        safe_rows.append(
            {
                "고객 ID": customer_id[:8] if customer_id else "-",
                "업체명": str(row.get("company_name") or "")[:120],
                "소유자": (
                    f"{owner_id[:3]}***{owner_id[-3:]}"
                    if len(owner_id) > 6
                    else "***"
                ),
                "상태": str(row.get("lifecycle_status") or ""),
                "보관일": row.get("archived_at"),
            }
        )
    return safe_rows


def render_data_safety_status(current_user_id: str) -> None:
    """Read-only operations panel; never exposes paths, payloads, or secrets."""
    from auth import is_admin

    if not is_admin(current_user_id):
        st.error("관리자 권한이 필요합니다.")
        return

    st.markdown("### 데이터 안전성 · 복구 준비")
    sync_status = _admin_sync_summary(current_user_id)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전사 재시도 대기", f"{sync_status['global_queued']}건")
    c2.metric("전사 관리자 확인", f"{sync_status['global_dead_letter']}건")
    c3.metric("현재 인스턴스 임시대기", f"{sync_status['local_queued']}건")
    c4.metric(
        "영속 대기열",
        "준비됨" if sync_status.get("durable_enabled") else "운영 적용 전",
    )

    if sync_status.get("global_unavailable"):
        st.warning(
            "전사 영속 대기열 집계를 읽지 못했습니다. 현재 인스턴스의 "
            "임시 대기열만 확인할 수 있습니다."
        )
    if sync_status.get("local_corrupted"):
        st.error(
            "로컬 복구 대기열 형식을 확인해야 합니다. 원본 대기열은 덮어쓰지 않았습니다."
        )

    if not data_safety_enabled():
        st.info(
            "v9.9.0 데이터 안전성 마이그레이션은 아직 운영 적용 전입니다. "
            "승인 후 DB 마이그레이션과 기능 플래그를 순서대로 활성화합니다."
        )
        return
    if not cloud_is_configured():
        st.warning("Supabase 연결 설정을 확인해 주세요.")
        return

    try:
        db = CloudDatabase()
        backups = db.select(
            TABLE_BACKUP_RUNS,
            columns=(
                "backup_type,status,started_at,completed_at,"
                "retention_until,checksum_sha256,size_bytes,record_counts,created_at"
            ),
            order="created_at.desc",
            limit=10,
        )
        drills = db.select(
            TABLE_RESTORE_DRILLS,
            columns=(
                "environment_label,status,integrity_verified,started_at,"
                "completed_at,result_summary,created_at"
            ),
            order="created_at.desc",
            limit=10,
        )
    except Exception:
        st.warning(
            "백업·복구 상태 테이블을 읽지 못했습니다. 운영 마이그레이션 적용 여부를 확인해 주세요."
        )
        return

    left, right = st.columns(2)
    with left:
        st.markdown("#### 최근 백업")
        safe_backups = _safe_backup_rows(backups)
        if safe_backups:
            st.dataframe(pd.DataFrame(safe_backups), hide_index=True, width="stretch")
        else:
            st.caption("기록된 백업 실행이 없습니다.")
    with right:
        st.markdown("#### 최근 격리 복구시험")
        safe_drills = _safe_restore_rows(drills)
        if safe_drills:
            st.dataframe(pd.DataFrame(safe_drills), hide_index=True, width="stretch")
        else:
            st.caption("기록된 복구시험이 없습니다.")

    st.caption(
        "데이터베이스 백업과 Storage 문서 백업은 별도 대상으로 관리합니다. "
        "복구시험은 운영 프로젝트가 아닌 격리 환경에서 수행합니다."
    )

    st.markdown("#### 고객 장기보존 · 아카이브")
    st.caption(
        "이 기능은 고객정보를 삭제하지 않습니다. 일반 검색에서 보관 상태로 "
        "분리하고, 모든 상태 변경은 감사 이벤트로 남깁니다."
    )
    try:
        archived_rows = list_archived_customers(limit=100, db=db)
    except Exception:
        st.warning(
            "고객 아카이브 목록을 읽지 못했습니다. v9.9.0 마이그레이션 적용 "
            "여부를 확인해 주세요."
        )
        return

    safe_archived_rows = _safe_customer_archive_rows(archived_rows)
    if safe_archived_rows:
        st.dataframe(
            pd.DataFrame(safe_archived_rows),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("현재 아카이브 상태인 고객이 없습니다.")

    with st.expander("고객 아카이브 또는 재활성화"):
        st.caption(
            "고객 상세화면에서 확인한 내부 고객 ID와 소유자 ID를 입력합니다. "
            "사업자번호·전화번호·인증정보는 입력하지 마세요."
        )
        customer_id = st.text_input(
            "고객 ID",
            key="v990_customer_lifecycle_customer_id",
        ).strip()
        owner_user_id = st.text_input(
            "소유자 ID",
            key="v990_customer_lifecycle_owner_id",
        ).strip()
        reason = st.text_input(
            "변경 사유",
            key="v990_customer_lifecycle_reason",
        ).strip()
        archive_col, reactivate_col = st.columns(2)
        if archive_col.button(
            "아카이브로 전환",
            key="v990_archive_customer",
            width="stretch",
        ):
            if not customer_id or not owner_user_id or not reason:
                st.error("고객 ID, 소유자 ID, 변경 사유를 모두 입력해 주세요.")
            else:
                try:
                    archive_customer(
                        customer_id=customer_id,
                        owner_user_id=owner_user_id,
                        actor_user_id=current_user_id,
                        reason=reason,
                        db=db,
                    )
                except Exception:
                    st.error(
                        "아카이브 전환에 실패했습니다. 대상·권한·마이그레이션 "
                        "상태를 확인해 주세요."
                    )
                else:
                    st.success("고객정보를 삭제하지 않고 아카이브로 전환했습니다.")
                    st.rerun()
        if reactivate_col.button(
            "활성 상태로 복원",
            key="v990_reactivate_customer",
            width="stretch",
        ):
            if not customer_id or not owner_user_id or not reason:
                st.error("고객 ID, 소유자 ID, 변경 사유를 모두 입력해 주세요.")
            else:
                try:
                    reactivate_customer(
                        customer_id=customer_id,
                        owner_user_id=owner_user_id,
                        actor_user_id=current_user_id,
                        reason=reason,
                        db=db,
                    )
                except Exception:
                    st.error(
                        "재활성화에 실패했습니다. 대상·권한·마이그레이션 "
                        "상태를 확인해 주세요."
                    )
                else:
                    st.success("기존 이력을 유지한 채 고객을 재활성화했습니다.")
                    st.rerun()
