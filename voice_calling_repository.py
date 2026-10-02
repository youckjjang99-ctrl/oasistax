"""Service-role-only voice RPC adapter. Never return database error payloads."""
from __future__ import annotations

from typing import Any, Mapping

_ACTIONS = {"candidates", "list_jobs", "list_permissions", "grant_permission", "revoke_permission", "do_not_call", "enqueue", "approve", "cancel", "confirm_visit", "reconcile"}
_WORKER_ACTIONS = {"claim", "get_job", "connect", "mark_dispatched", "mark_unknown", "mark_failed", "status", "result", "bridge_error"}
_MESSAGES = {
    "OK": "처리되었습니다.", "EMPTY": "발신 대기 작업이 없습니다.",
    "NOT_AUTHORIZED": "접근 권한 또는 현재 담당자를 확인해 주세요.",
    "INVALID_INPUT": "입력값을 확인해 주세요.", "NOT_READY": "전화상담 DB 연결·마이그레이션을 확인해 주세요.",
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
        if not actor or action not in _ACTIONS:
            return {"ok": False, "code": "INVALID_INPUT", "message": _MESSAGES["INVALID_INPUT"]}
        return self._call("oasis_voice_action", {"p_current_user_id": actor, "p_action": action, "p_payload": dict(payload or {})})

    def worker(self, action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if action not in _WORKER_ACTIONS:
            return {"ok": False, "code": "INVALID_INPUT", "message": _MESSAGES["INVALID_INPUT"]}
        return self._call("oasis_voice_worker", {"p_action": action, "p_payload": dict(payload or {})})
