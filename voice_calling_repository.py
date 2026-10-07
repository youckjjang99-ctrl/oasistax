"""Service-role-only voice RPC adapter. Never return database error payloads."""
from __future__ import annotations

from typing import Any, Mapping

_ACTIONS = {"candidates", "list_jobs", "list_permissions", "grant_permission", "revoke_permission", "do_not_call", "enqueue", "approve", "cancel", "confirm_visit", "reconcile"}
_CATALOG_ACTIONS = {"catalog"}
_LAUNCH_ACTIONS = {"filtered_campaign": "oasis_voice_filtered_campaign", "manual_call": "oasis_voice_manual_call"}
_CAMPAIGN_ACTIONS = {"create_campaign", "list_campaigns", "campaign_jobs", "campaign_stats", "start_campaign", "pause_campaign", "cancel_campaign", "legacy_jobs"}
_WORKER_ACTIONS = {"claim", "get_job", "connect", "mark_dispatched", "mark_unknown", "mark_failed", "status", "result", "bridge_error"}
_MESSAGES = {
    "OK": "처리되었습니다.", "EMPTY": "발신 대기 작업이 없습니다.",
    "NOT_AUTHORIZED": "접근 권한 또는 현재 담당자를 확인해 주세요.",
    "INVALID_INPUT": "입력값을 확인해 주세요.", "NOT_READY": "전화상담 DB 연결·마이그레이션을 확인해 주세요.",
    "SEARCH_TIMEOUT": "검색 시간이 초과되었습니다. 지역·연락처 유형 등 조건을 좁힌 뒤 다시 조회해 주세요.",
    "INVALID_CURSOR": "검색 조건이나 페이지 정보가 변경되었습니다. 첫 페이지부터 다시 조회해 주세요.",
    "CONSENT_REQUIRED": "유효한 전화 안내 동의 근거가 필요합니다.",
    "TARGET_CHANGED": "담당자·연락처·수신거부 상태가 바뀌어 중단했습니다.",
    "DO_NOT_CALL": "수신거부 또는 연락제외된 대상입니다.",
    "DUPLICATE": "이미 접수되었거나 최근 연락한 대상입니다. 중복 발신하지 않았습니다.",
    "OUTSIDE_HOURS": "평일 오전 9시~오후 6시에만 발신합니다.",
    "DAILY_LIMIT": "오늘 설정된 발신 한도에 도달했습니다.",
    "BUSY": "다른 통화가 진행 중입니다.", "INVALID_STATE": "작업 상태가 변경되었습니다. 새로고침해 주세요.",
    "APPOINTMENT_CONFLICT": "같은 담당자의 방문 일정이 겹칩니다. 일정을 다시 협의해 주세요.",
    "INVALID_RESULT": "방문 의향·장소·정확한 일시를 확인해 주세요.",
    "PROVIDER_MISMATCH": "통화 결속정보가 일치하지 않아 중단했습니다.",
    "CAMPAIGN_NOT_RUNNING": "발신 묶음이 일시정지 또는 취소되어 추가 발신을 중단했습니다.",
    "NO_ELIGIBLE_TARGETS": "조건에 맞는 발신 가능 업체가 없습니다. 배정·번호별 동의·수신거부 상태를 확인해 주세요.",
    "IDEMPOTENCY_CONFLICT": "이미 접수된 요청과 입력 내용이 다릅니다. 새 요청으로 다시 확인해 주세요.",
    "CAMPAIGN_CREATE_FAILED": "발신 묶음을 저장하지 못했습니다. 같은 요청으로 다시 확인해 주세요.",
    "APPROVAL_EXPIRED": "발신 승인이 만료되었습니다. 결과보고에서 확인 후 다시 승인해 주세요.",
}


class VoiceRepository:
    def __init__(self, db: Any = None):
        self._db = db

    def _call(self, rpc: str, parameters: dict[str, Any]) -> dict[str, Any]:
        try:
            if self._db is None:
                from cloud_db import CloudDatabase
                self._db = CloudDatabase()
            result = self._db.rpc(rpc, parameters)
            if isinstance(result, list) and len(result) == 1:
                result = result[0]
            if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
                raise ValueError("MALFORMED")
            code = result.get("code", "OK" if result["ok"] else "NOT_READY")
            if code not in _MESSAGES:
                code = "NOT_READY"
            return {**result, "code": code, "message": _MESSAGES[code]}
        except Exception:
            # RPC exception text can contain customer data or credentials.
            return {"ok": False, "code": "NOT_READY", "message": _MESSAGES["NOT_READY"]}

    def action(self, actor: str, action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not actor or action not in _ACTIONS | _CAMPAIGN_ACTIONS | _CATALOG_ACTIONS | _LAUNCH_ACTIONS.keys():
            return {"ok": False, "code": "INVALID_INPUT", "message": _MESSAGES["INVALID_INPUT"]}
        if action in _LAUNCH_ACTIONS:
            return self._call(_LAUNCH_ACTIONS[action], {"p_current_user_id": actor, "p_payload": dict(payload or {})})
        if action in _CATALOG_ACTIONS:
            # Read-only catalog has its own authoritative, current-admin check.
            # Never fall back to assigned-only candidates or mutate source rows.
            return self._call("oasis_voice_catalog", {"p_current_user_id": actor, "p_payload": dict(payload or {})})
        rpc = "oasis_voice_campaign_action" if action in _CAMPAIGN_ACTIONS else "oasis_voice_action"
        return self._call(rpc, {"p_current_user_id": actor, "p_action": action, "p_payload": dict(payload or {})})

    def worker(self, action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if action not in _WORKER_ACTIONS:
            return {"ok": False, "code": "INVALID_INPUT", "message": _MESSAGES["INVALID_INPUT"]}
        return self._call("oasis_voice_worker", {"p_action": action, "p_payload": dict(payload or {})})
