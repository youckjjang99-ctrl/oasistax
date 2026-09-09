"""Version-checked CRM writes; never fall back to an unguarded whole-row upsert."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from cloud_db import CloudDatabase, cloud_is_configured, normalize_business_no
from crm_sync_protocol import CRM_SAVE_RPC, acknowledge_record, known_cloud_version, make_save_parameters, validate_save_response


def _remember_result(owner: str, business_no: str, sent: dict, response: dict) -> str:
    from crm import make_customer_key, mutate_crm_data

    key = make_customer_key(sent.get("company_name", ""), business_no)
    def update(data: dict) -> str:
        current = data.setdefault("customers", {}).get(key)
        if not isinstance(current, dict):
            return "untracked"
        if known_cloud_version(current) > int(response.get("version", 0) or 0):
            return str(current.get("_sync_state") or "synced")
        if response.get("status") == "applied":
            data["customers"][key] = acknowledge_record(current, sent, response)
        elif response.get("status") == "conflict":
            current["_sync_state"] = "conflict"
            current["_cloud_latest"] = deepcopy(response.get("crm_data") or {})
            current["_cloud_latest_version"] = int(response.get("version", 0) or 0)
        return str(data["customers"][key].get("_sync_state") or "synced")
    return mutate_crm_data(owner, update)


def save_crm_to_cloud(owner: str, business_no: Any, record: dict[str, Any], *, db=None) -> tuple[bool, str]:
    owner = str(owner or "").strip()
    business_no = normalize_business_no(business_no)
    if not owner or not business_no:
        return False, "식별정보가 없어 CRM은 로컬 임시저장 상태입니다."
    sent = deepcopy(record)
    parameters = make_save_parameters(owner, business_no, sent)
    try:
        if db is None and not cloud_is_configured():
            raise RuntimeError("crm_cloud_not_configured")
        response = validate_save_response((db or CloudDatabase()).rpc(CRM_SAVE_RPC, parameters), parameters)
        if response.get("status") == "conflict":
            _remember_result(owner, business_no, sent, response)
            return False, "다른 기기에서 변경된 CRM과 충돌했습니다. 입력 초안은 보존했습니다. 최신 내용 확인 후 다시 저장해 주세요."
        local_state = _remember_result(owner, business_no, sent, response)
        if local_state == "conflict":
            return False, "이전 요청은 클라우드에서 확인했지만 이후 편집과 원격 변경을 함께 확인해야 합니다. 로컬 초안을 보존했으니 최신 내용과 비교해 주세요."
        if local_state == "pending":
            return False, "이전 요청은 클라우드에 저장했습니다. 저장 중 추가한 편집은 로컬에 보존되어 있으며 아직 동기화 대기 중입니다."
        return True, "CRM 클라우드 저장 완료"
    except Exception:
        # The exact operation/version is retained, so a delayed replay cannot
        # overwrite a newer record and a lost HTTP response cannot duplicate it.
        from cloud_sync import _queue_path
        from sync_outbox import enqueue_rpc_outbox
        try:
            location, _ = enqueue_rpc_outbox(
                _queue_path(owner), owner, "crm_versioned", CRM_SAVE_RPC,
                parameters, error="crm_versioned_sync_unconfirmed", db=db,
            )
        except Exception:
            return False, "CRM 클라우드 저장과 재전송 등록을 확인하지 못했습니다. 로컬 초안을 보존했으니 다시 확인해 주세요."
        return False, (
            "CRM 로컬 임시저장·동기화 대기 "
            + ("(클라우드 대기열 보관)" if location == "cloud" else "(로컬 대기열 보관)")
        )
