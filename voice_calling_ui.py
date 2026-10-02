"""Owner-scoped AI visit calling workspace; this module never places a call.

UI role flags only control presentation. Every read and mutation is authorized
again by VoiceRepository and the database RPC.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
import hashlib
import re
import uuid
from typing import Any, Mapping, MutableMapping, Sequence
from zoneinfo import ZoneInfo


SEOUL = ZoneInfo("Asia/Seoul")
MAX_SELECTION = 30
_PREFIX = "oasis_voice_visit_"
_STATUS_LABELS = {
    "pending_approval": "관리자 승인 대기",
    "queued": "관리자 승인 대기",
    "approved": "승인됨 · 발신 대기",
    "dispatching": "발신 요청 중",
    "accepted": "전화 서비스 접수됨",
    "calling": "통화 중",
    "in_progress": "통화 중",
    "completed": "통화 종료",
    "failed": "실패 · 확인 필요",
    "cancelled": "취소",
    "canceled": "취소",
    "unknown": "발신 결과 확인 필요",
    "visit_requested": "방문 요청 · 담당자 확인 필요",
    "visit_confirmed": "방문 확정",
    "do_not_call": "수신거부",
}
_RESULT_LABELS = {
    "visit_requested": "방문 요청",
    "callback_requested": "재통화 요청",
    "not_interested": "관심 없음",
    "declined": "관심 없음",
    "not_representative": "대표자 아님",
    "do_not_call": "수신거부",
    "wrong_number": "번호 오류",
    "no_answer": "부재중",
    "busy": "통화 중",
    "completed": "안내 종료",
}


def _rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    value = result.get("rows")
    return [dict(row) for row in value if isinstance(row, Mapping)] if isinstance(value, list) else []


def masked_phone(value: Any) -> str:
    """Never reveal a full source contact, even if an RPC changes its shape."""
    raw = str(value or "").strip()
    if not raw:
        return "없음"
    digits = re.sub(r"\D", "", raw)
    if "*" in raw:
        return "***-****-" + digits[-4:] if len(digits) >= 4 else "***-****-****"
    if digits.startswith("82") and len(digits) > 10:
        digits = "0" + digits[2:]
    return "***-****-" + digits[-4:] if len(digits) >= 8 else "***-****-****"


def _datetime_label(value: Any) -> str:
    if not value:
        return "-"
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=SEOUL)
        return result.astimezone(SEOUL).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return "확인 필요"


def candidate_display(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Allowlist table fields instead of rendering raw provider or RPC data."""
    return [
        {
            "업체": str(row.get("company_name") or "업체명 없음"),
            "연락처": masked_phone(row.get("phone_masked") or row.get("phone")),
            "전화 동의": "확인됨" if row.get("permission_valid") else "등록 필요",
            "발신 가능": "접수 가능" if _eligible(row) else "확인 필요",
            "수신거부": "연락 금지" if row.get("do_not_call") else "-",
        }
        for row in rows
    ]


def _eligible(row: Mapping[str, Any]) -> bool:
    return bool(
        row.get("permission_valid")
        and row.get("phone_masked")
        and row.get("eligible", True)
        and not row.get("do_not_call")
        and row.get("status") not in {"do_not_call", "closed", "permanently_excluded", "wrong_number"}
    )


def job_display(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "업체": str(row.get("company_name") or "업체"),
            "진행": _STATUS_LABELS.get(str(row.get("status") or ""), "상태 확인 필요"),
            "결과": _RESULT_LABELS.get(str(row.get("outcome") or ""), "-"),
            "요청 시각": _datetime_label(row.get("created_at")),
            "방문 희망": _datetime_label(row.get("visit_at")),
            "담당자 확인": "확정" if row.get("visit_confirmed_at") else "미확정",
            "추가 확인": "CRM 연락이력 반영 확인 필요" if row.get("safe_error_code") == "CONTACT_SYNC_PENDING" else ("관리자 확인 필요" if row.get("safe_error_code") else "-"),
        }
        for row in rows
    ]


def scoped_metrics(candidates: Sequence[Mapping[str, Any]], jobs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """These are counts of loaded rows, never claims about the entire CRM."""
    return {
        "candidates": len(candidates),
        "eligible": sum(_eligible(row) for row in candidates),
        "pending_approval": sum(row.get("status") in {"pending_approval", "queued"} for row in jobs),
        "visit_requests": sum(row.get("outcome") == "visit_requested" for row in jobs),
    }


def enqueue_request_key(state: MutableMapping[str, Any], actor: str, ids: Sequence[str]) -> str:
    """Keep the same key across reruns/double clicks for the same selection."""
    fingerprint = hashlib.sha256((actor + "\n" + "\n".join(sorted(set(ids)))).encode()).hexdigest()
    existing = state.get(_PREFIX + "queue_request", {})
    if existing.get("fingerprint") != fingerprint:
        existing = {"fingerprint": fingerprint, "key": str(uuid.uuid4())}
        state[_PREFIX + "queue_request"] = existing
    return str(existing["key"])


def validate_selection(ids: Sequence[str], candidates: Sequence[Mapping[str, Any]], *, require_eligible: bool) -> str:
    selected = set(ids)
    if not selected:
        return "업체를 선택해 주세요."
    if len(selected) > MAX_SELECTION:
        return "한 번에 최대 30개 업체까지 선택해 주세요."
    by_id = {str(row.get("company_uid") or ""): row for row in candidates}
    if any(item not in by_id for item in selected):
        return "목록이 변경되었습니다. 새로고침한 후 선택해 주세요."
    if require_eligible and any(not _eligible(by_id[item]) for item in selected):
        return "선택한 업체 중 동의 확인 또는 연락제외 확인이 필요한 업체가 있습니다."
    return ""


def _action(repo: Any, actor: str, action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        result = repo.action(actor, action, dict(payload or {}))
        if isinstance(result, dict):
            return result
    except Exception:
        # Provider credentials, raw phone numbers and RPC payloads stay out of UI.
        pass
    return {"ok": False, "code": "UNAVAILABLE", "message": "전화상담 저장소에 연결하지 못했습니다. 설정과 마이그레이션 상태를 확인해 주세요."}


def _notice(st: Any, result: Mapping[str, Any], success: str) -> None:
    if result.get("ok"):
        st.session_state[_PREFIX + "notice"] = success
        st.rerun()
    else:
        # Fixed UI wording deliberately avoids echoing raw database error details.
        st.error("요청을 반영하지 못했습니다. 동의·담당 배정·진행 상태를 확인하고 새로고침해 주세요.")


def render_voice_calling(current_user_id: str, is_admin_user: bool = False) -> None:
    import streamlit as st
    from voice_calling import OBJECTION_RESPONSES, SAMPLE_DIALOGUE, VoiceSettings
    from voice_calling_repository import VoiceRepository

    if not current_user_id:
        st.warning("로그인 후 이용해 주세요.")
        return
    st.markdown("### AI 방문상담")
    st.caption("짧은 안내 → 방문 희망 접수 → 담당 전문가 확인. 이 화면에서 직접 전화를 걸지는 않습니다.")
    notice = st.session_state.pop(_PREFIX + "notice", "")
    if notice:
        st.success(notice)
    readiness = VoiceSettings.from_environment().readiness()
    if not readiness.get("ready"):
        st.info("준비 모드입니다. 발신 회선·서버 설정과 관리자 승인 전에는 실제 전화가 발신되지 않습니다.")
    try:
        repo = VoiceRepository()
    except Exception:
        repo = None
    page_key = _PREFIX + "candidate_page_" + current_user_id
    page = int(st.session_state.get(page_key, 0))
    job_page_key = _PREFIX + "job_page_" + current_user_id
    job_page = int(st.session_state.get(job_page_key, 0))
    candidates_result = _action(repo, current_user_id, "candidates", {"limit": 100, "offset": page * 100})
    jobs_result = _action(repo, current_user_id, "list_jobs", {"limit": 100, "offset": job_page * 100})
    candidates, jobs = _rows(candidates_result), _rows(jobs_result)
    if not candidates_result.get("ok") or not jobs_result.get("ok"):
        st.warning("전화상담 데이터 연결을 확인하지 못했습니다. 아래 상담안은 확인할 수 있으며, 데이터 연결 전에는 접수할 수 없습니다.")
    metrics = scoped_metrics(candidates, jobs)
    columns = st.columns(4)
    for column, label, key in zip(columns, ("현재 목록", "발신 가능", "승인 대기", "방문 요청"), metrics):
        column.metric(label, f"{metrics[key]}건")
    st.caption("위 숫자는 현재 권한으로 불러온 목록 기준입니다. 전체 CRM 누적 통계가 아닙니다.")
    if st.button("목록 새로고침", key=_PREFIX + "refresh"):
        st.rerun()
    preparation, targets, queue, results, dialogue = st.tabs(["준비 상태", "내 영업DB 대상", "승인·발신 대기", "방문 요청·결과", "상담 흐름"])
    with preparation:
        _render_preparation(st, readiness)
    with targets:
        _render_targets(st, repo, current_user_id, candidates, bool(candidates_result.get("ok")), is_admin_user, selection_scope=str(page))
        previous, following = st.columns(2)
        if previous.button("이전 대상 목록", disabled=page == 0, key=_PREFIX + "previous"):
            st.session_state[page_key] = max(0, page - 1)
            st.rerun()
        if following.button("다음 대상 목록", disabled=not candidates_result.get("has_more"), key=_PREFIX + "next"):
            st.session_state[page_key] = page + 1
            st.rerun()
    with queue:
        st.caption(f"요청 목록 {job_page + 1}페이지 · 최대 100개씩 표시합니다. 결과 탭에도 같은 범위가 적용됩니다.")
        previous, following = st.columns(2)
        if previous.button("이전 요청 목록", disabled=job_page == 0, key=_PREFIX + "jobs_previous"):
            st.session_state[job_page_key] = max(0, job_page - 1)
            st.rerun()
        if following.button("다음 요청 목록", disabled=not jobs_result.get("has_more"), key=_PREFIX + "jobs_next"):
            st.session_state[job_page_key] = job_page + 1
            st.rerun()
        _render_queue(st, repo, current_user_id, jobs, is_admin_user, bool(readiness.get("ready")))
    with results:
        _render_results(st, repo, current_user_id, jobs)
    with dialogue:
        st.info("첫 인사에서 AI 상담원임을 밝힙니다. 지원 확정·승인 보장으로 안내하지 않으며, 상세 자격 판단은 방문 전문가가 진행합니다.")
        for speaker, line in SAMPLE_DIALOGUE:
            st.write(f"**{speaker}** · {line}")
        st.markdown("#### 자주 묻는 질문과 응대")
        for objection, response in OBJECTION_RESPONSES.items():
            with st.expander(str(objection)):
                st.write(response)


def _render_preparation(st: Any, readiness: Mapping[str, Any]) -> None:
    st.markdown("#### 실제 발신 전 확인")
    st.caption("아래는 설정 유무·형식 검사입니다. 실제 회선 개통, 국내 발신번호 표시 및 음성 품질은 본인 테스트 번호로 별도 확인해야 합니다.")
    for check in readiness.get("checks", []):
        st.write(("✅ " if check.get("ok") else "○ ") + str(check.get("label") or "설정 확인"))
    st.markdown("1. 발신 가능한 회선과 번호를 개설합니다.\n2. 관리자에게 서버 연결·발신 한도 설정을 요청합니다.\n3. 동의 근거가 등록된 내 영업DB를 선택해 대기에 넣습니다.\n4. 관리자가 건별 승인하면 운영 시간 안에 작업자가 처리합니다.")
    st.caption("API 키와 발신 보안키는 서버 환경변수에서 관리하며 이 화면에는 표시하지 않습니다. 방문 일정은 고객의 희망 일정이며 전문가가 확인한 뒤 확정됩니다.")


def _render_targets(st: Any, repo: Any, actor: str, candidates: list[dict[str, Any]], connected: bool, admin: bool, *, selection_scope: str = "0") -> None:
    st.caption(("관리자는 배정된 영업DB를 조회할 수 있습니다. " if admin else "현재 담당 중인 영업DB를 표시합니다. ") + "공개 연락처의 수집 여부와 전화 안내 동의 근거는 별도로 관리합니다.")
    if not candidates:
        st.info("표시할 내 영업DB가 없습니다. 담당 배정 및 저장소 연결 상태를 확인해 주세요.")
        return
    st.dataframe(candidate_display(candidates), hide_index=True, use_container_width=True)
    by_id = {str(row.get("company_uid") or ""): row for row in candidates if row.get("company_uid")}
    selected = st.multiselect("대상 업체 선택 (최대 30개)", list(by_id), format_func=lambda item: str(by_id[item].get("company_name") or "업체") + " · " + masked_phone(by_id[item].get("phone_masked") or by_id[item].get("phone")), key=_PREFIX + "targets_" + actor + "_" + selection_scope, max_selections=MAX_SELECTION)
    st.caption("선택 후 접수하면 관리자 승인 대기에 저장됩니다. 접수만으로 통화가 시작되지는 않습니다.")
    if st.button("선택 업체 AI 방문상담 대기에 넣기", type="primary", disabled=not connected or not selected, key=_PREFIX + "enqueue"):
        error = validate_selection(selected, candidates, require_eligible=True)
        if error:
            st.warning(error)
        else:
            result = _action(repo, actor, "enqueue", {"company_uids": selected, "request_id": enqueue_request_key(st.session_state, actor, selected)})
            _notice(st, result, "상담 요청을 저장했습니다. 관리자 승인 후 발신됩니다.")
    with st.expander("전화 안내 동의 근거 등록 / 수신거부"):
        st.caption("동의가 확보된 업체는 근거를 한 번 입력해 선택 업체에 함께 등록할 수 있습니다. 수신거부 업체는 동의 등록만으로 다시 발신할 수 없습니다.")
        if admin:
            with st.form(_PREFIX + "permission_form"):
                basis = st.selectbox("동의 유형", ["explicit_consent", "callback_request"], format_func=lambda item: {"explicit_consent": "전화 안내 수신동의", "callback_request": "고객이 요청한 재통화"}[item])
                source = st.text_input("동의 출처", placeholder="예: 신청서 / 동의서 / 상담 접수")
                evidence = st.text_input("증빙 참조번호", placeholder="보관 문서 ID 또는 내부 기록번호 (원문 개인정보 입력 금지)")
                consent_day = st.date_input("동의 확인일", value=datetime.now(SEOUL).date(), max_value=datetime.now(SEOUL).date())
                expiry = st.date_input("이번 발신에 적용할 동의 유효 종료일", value=datetime.now(SEOUL).date() + timedelta(days=30), min_value=datetime.now(SEOUL).date())
                confirmed = st.checkbox("선택 업체의 전화 안내 동의 근거를 확인했습니다.")
                submit = st.form_submit_button("선택 업체 동의 근거 일괄 등록", disabled=not connected or not selected)
            if submit:
                error = validate_selection(selected, candidates, require_eligible=False)
                if error or not confirmed or not source.strip() or not evidence.strip():
                    st.warning(error or "동의 출처·증빙 참조번호를 입력하고 확인란을 선택해 주세요.")
                else:
                    result = _action(repo, actor, "grant_permission", {"company_uids": selected, "kind": basis, "evidence_ref": source.strip() + " / " + evidence.strip(), "granted_at": datetime.combine(consent_day, time.min, SEOUL).isoformat(), "expires_at": datetime.combine(expiry, time.max, SEOUL).isoformat()})
                    _notice(st, result, "선택 업체의 전화 안내 동의 근거를 저장했습니다.")
        else:
            st.info("동의 근거 일괄 등록은 관리자에게 요청해 주세요.")
        block_reason = st.text_input("수신거부 처리 사유", key=_PREFIX + "dnc_reason", placeholder="고객이 전화 안내 중단 요청")
        if st.button("선택 업체 수신거부 등록", disabled=not connected or not selected or not block_reason.strip(), key=_PREFIX + "dnc"):
            outcomes = [_action(repo, actor, "do_not_call", {"company_uid": uid, "reason": block_reason.strip()}) for uid in selected]
            if all(result.get("ok") for result in outcomes):
                _notice(st, {"ok": True}, "선택 업체의 수신거부를 등록했습니다. 대기 중 요청도 서버에서 다시 확인합니다.")
            else:
                st.warning(f"{sum(bool(result.get('ok')) for result in outcomes)}/{len(outcomes)}건 반영했습니다. 실패한 업체는 새로고침 후 다시 확인해 주세요.")


def _render_queue(st: Any, repo: Any, actor: str, jobs: list[dict[str, Any]], admin: bool, ready: bool) -> None:
    active = [row for row in jobs if row.get("status") in {"pending_approval", "queued", "approved", "dispatching", "accepted", "calling", "in_progress", "unknown"}]
    if not active:
        st.info("현재 불러온 목록에 대기 중인 상담 요청이 없습니다.")
        return
    st.dataframe(job_display(active), hide_index=True, use_container_width=True)
    if not ready:
        st.caption("발신 준비가 완료되어야 승인할 수 있습니다. 미설정 상태에서는 대기 접수만 가능합니다.")
    for row in active:
        job_id = str(row.get("id") or row.get("job_id") or "")
        if not job_id:
            continue
        with st.expander(str(row.get("company_name") or "업체") + " · " + _STATUS_LABELS.get(str(row.get("status")), "확인 필요")):
            st.caption("관리자 승인 후 자동 발신될 수 있습니다. 대상과 동의 근거를 확인해 주세요.")
            if admin and row.get("status") in {"pending_approval", "queued"}:
                if st.button("이 요청 발신 승인", disabled=not ready, key=_PREFIX + "approve_" + job_id):
                    _notice(st, _action(repo, actor, "approve", {"job_id": job_id}), "발신을 승인했습니다. 작업자가 발신 직전 동의·담당·수신거부를 다시 확인합니다.")
            if row.get("status") in {"pending_approval", "queued", "approved"}:
                if st.button("대기 요청 취소", key=_PREFIX + "cancel_" + job_id):
                    _notice(st, _action(repo, actor, "cancel", {"job_id": job_id}), "대기 요청을 취소했습니다.")
            if row.get("status") == "unknown":
                st.warning("통화 결과가 불명확해 추가 발신을 중지했습니다. 전화 서비스 관리화면에서 통화 종료 여부를 확인해 주세요. 자동 재시도하지 않습니다.")
                if admin:
                    with st.form(_PREFIX + "reconcile_" + job_id):
                        resolution = st.selectbox("서비스에서 확인한 결과", ["confirmed_ended", "confirmed_not_sent"], format_func=lambda x: {"confirmed_ended": "통화가 종료됨", "confirmed_not_sent": "전화가 생성되지 않음"}[x])
                        evidence = st.text_input("확인 근거 (관리화면 기록 참조번호)")
                        verified = st.checkbox("서비스 기록을 확인했고 진행 중인 통화가 없음을 확인했습니다.")
                        submit = st.form_submit_button("확인 결과 기록 · 차단된 작업 종료")
                    if submit:
                        if not verified or len(evidence.strip()) < 3:
                            st.warning("종료 확인과 근거를 입력해 주세요.")
                        else:
                            _notice(st, _action(repo, actor, "reconcile", {"job_id": job_id, "resolution": resolution, "reason": evidence.strip()}), "확인 결과를 기록했습니다. 이 작업을 다시 발신하지 않으며 수신거부는 유지됩니다.")


def _render_results(st: Any, repo: Any, actor: str, jobs: list[dict[str, Any]]) -> None:
    completed = [row for row in jobs if row.get("outcome") or row.get("status") in {"completed", "failed", "cancelled", "canceled"}]
    if not completed:
        st.info("아직 불러온 통화 결과가 없습니다. AI가 접수한 방문 희망은 이곳에서 전문가가 확인합니다.")
        return
    st.dataframe(job_display(completed), hide_index=True, use_container_width=True)
    for row in completed:
        job_id = str(row.get("id") or row.get("job_id") or "")
        if not job_id or row.get("outcome") != "visit_requested" or row.get("visit_confirmed_at"):
            continue
        with st.expander(str(row.get("company_name") or "업체") + " · 방문 희망 확인"):
            st.caption("AI 접수만으로 일정이 확정되지 않습니다. 아래 희망 일정과 장소를 고객 및 방문 전문가에게 확인해 주세요.")
            st.write("방문 희망: " + _datetime_label(row.get("visit_at")))
            st.write("방문 장소: " + str(row.get("address") or "확인 필요"))
            st.write("상담 요약: " + str(row.get("summary") or "-"))
            with st.form(_PREFIX + "visit_" + job_id):
                note = st.text_input("확인 메모", placeholder="개인정보 원문 대신 필요한 업무 내용만 입력", key=_PREFIX + "note_" + job_id)
                confirm = st.checkbox("고객 및 담당 전문가와 일정을 확인했습니다.", key=_PREFIX + "checked_" + job_id)
                submit = st.form_submit_button("방문 일정 확정")
            if submit:
                if not confirm or not note.strip():
                    st.warning("확인 메모를 입력하고 일정 확인란을 선택해 주세요.")
                else:
                    _notice(st, _action(repo, actor, "confirm_visit", {"job_id": job_id, "reason": note.strip()}), "방문 일정을 확정했습니다.")
