from __future__ import annotations

import html
import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from consulting_report import render_ai_consulting_report_page
from tax_diagnosis import render_tax_diagnosis_page
from consultation_journal import load_company_consultation_context
from cloud_sync import (
    load_financial_snapshot,
    load_registry_snapshot,
    load_stock_valuations,
)
from enterprise_documents import load_enterprise_document_context
from matching_preferences import get_matching_preferences
from consulting_priority_engine import build_priority_recommendations
from data_safety_storage import (
    feature_enabled,
    load_copilot_assets,
    migrate_local_copilot_assets,
    write_copilot_asset,
)
from consultation_scenario_engine import (
    analyze_representative_answer,
    build_scenario_brief,
)
from registered_policy_match import (
    build_customer_labels,
    load_registered_customers,
)
from utils import get_user_cumulative_db_path, get_user_dirs


TOPIC_RULES = [
    {
        "topic": "정책자금",
        "keywords": [
            "운전자금", "시설자금", "기계", "설비", "공장", "증설",
            "차량", "수출", "판로", "연구개발", "R&D", "스마트공장",
            "채용", "고용", "자금부족",
        ],
        "questions": [
            "향후 12개월 내 시설·기계·차량 투자계획이 있습니까?",
            "현재 필요한 자금의 용도와 예상금액은 얼마입니까?",
            "기존 정책자금·보증기관 대출 잔액과 만기는 어떻게 됩니까?",
            "신규채용 또는 고용유지 계획이 있습니까?",
        ],
        "documents": [
            "최근 3개년 재무제표",
            "부가세 과세표준증명",
            "국세·지방세 납세증명",
            "시설·기계 견적서",
            "기존 대출현황",
        ],
    },
    {
        "topic": "가지급금 정리",
        "keywords": [
            "가지급금", "대표자 대여금", "임시인출", "업무무관", "가수금",
        ],
        "questions": [
            "가지급금 발생 원인과 실제 사용처를 확인했습니까?",
            "최근 3년간 가지급금 증감내역이 있습니까?",
            "대표자 상환능력과 배당·급여·퇴직금 활용 가능성을 검토했습니까?",
        ],
        "documents": [
            "가지급금 계정별원장",
            "대표자 거래내역",
            "주주명부",
            "정관",
            "임원보수·퇴직금 규정",
        ],
    },
    {
        "topic": "이익소각·자기주식",
        "keywords": [
            "이익소각", "자기주식", "자사주", "미처분이익잉여금", "배당",
        ],
        "questions": [
            "자기주식 취득 목적과 소각계획이 명확합니까?",
            "배당가능이익과 최근 주식가치를 확인했습니까?",
            "특수관계인 거래와 주주 간 이해관계를 검토했습니까?",
        ],
        "documents": [
            "최근 재무제표",
            "주주명부",
            "정관",
            "법인등기",
            "주식가치 평가자료",
        ],
    },
    {
        "topic": "가업승계",
        "keywords": [
            "가업승계", "상속", "증여", "후계자", "자녀", "승계", "상속세",
        ],
        "questions": [
            "승계 예정자와 희망시기를 확인했습니까?",
            "현재 주식가치와 향후 상승요인을 확인했습니까?",
            "대표자 유고 시 상속세·운영자금 재원을 준비했습니까?",
        ],
        "documents": [
            "주주명부",
            "법인등기",
            "가족관계증명",
            "최근 3개년 재무제표",
            "주식가치 평가자료",
        ],
    },
    {
        "topic": "정관개정",
        "keywords": [
            "정관", "임원퇴직금", "유족보상금", "배당", "주식양도제한",
            "주주총회", "이사회",
        ],
        "questions": [
            "현재 정관의 최종 개정일과 실제 운영규정을 확인했습니까?",
            "임원퇴직금·유족보상금·배당 규정이 목적에 맞게 정비돼 있습니까?",
            "최근 상법·세법 개정사항이 반영돼 있습니까?",
        ],
        "documents": [
            "현행 정관",
            "법인등기",
            "주주명부",
            "최근 주총·이사회 의사록",
            "임원보수 규정",
        ],
    },
    {
        "topic": "법인보험·퇴직재원",
        "keywords": [
            "CEO보험", "경영인정기", "대표자보장", "퇴직재원",
            "상속재원", "유고재원", "법인보험",
        ],
        "questions": [
            "대표자 유고 시 필요한 운영·상속재원 규모는 얼마입니까?",
            "예상 퇴직금과 현재 준비된 재원을 비교했습니까?",
            "보험 목적과 회계·세무처리 방식을 설명했습니까?",
        ],
        "documents": [
            "정관",
            "임원퇴직금 규정",
            "최근 재무제표",
            "기존 보험증권",
            "대표자 보수자료",
        ],
    },
    {
        "topic": "세액공제·경정청구",
        "keywords": [
            "세액공제", "경정청구", "고용세액", "투자세액", "연구개발비",
            "기계투자", "고용증가",
        ],
        "questions": [
            "최근 5개 사업연도 세액공제 적용내역을 확인했습니까?",
            "직원수 증가와 시설투자 내역을 연도별로 확인했습니까?",
            "이미 반영된 공제와 누락 가능성을 구분했습니까?",
        ],
        "documents": [
            "법인세 신고서",
            "세액공제조정명세서",
            "원천세 신고자료",
            "고용보험 가입자명부",
            "유형자산 명세",
        ],
    },
]


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _tokens(value: Any) -> set[str]:
    text = re.sub(
        r"[^0-9A-Za-z가-힣]+",
        " ",
        _clean(value).lower(),
    )
    return {
        token
        for token in text.split()
        if len(token) >= 2
    }


def _base_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"]


def _memory_path(user_id: str) -> Path:
    return _base_path(user_id) / "consulting_copilot_memory.json"


def _success_path(user_id: str) -> Path:
    return _base_path(user_id) / "consulting_success_cases.json"


def _checklist_path(user_id: str) -> Path:
    return _base_path(user_id) / "consulting_checklists.json"


def _sync_meta_path(user_id: str) -> Path:
    return _base_path(user_id) / "consulting_copilot_sync_meta.json"


def _conflict_path(user_id: str) -> Path:
    return _base_path(user_id) / "consulting_copilot_conflicts.json"


class CopilotLocalDataCorruptionError(RuntimeError):
    """Raised without replacing a malformed local Copilot asset."""


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise CopilotLocalDataCorruptionError(
            "AI 코파일럿 로컬 자산을 읽지 못했습니다. 원본 파일은 덮어쓰지 않았습니다."
        ) from exc
    if isinstance(default, dict) and not isinstance(data, dict):
        raise CopilotLocalDataCorruptionError(
            "AI 코파일럿 로컬 자산 형식이 올바르지 않습니다. 원본 파일은 덮어쓰지 않았습니다."
        )
    if isinstance(default, list) and not isinstance(data, list):
        raise CopilotLocalDataCorruptionError(
            "AI 코파일럿 로컬 자산 형식이 올바르지 않습니다. 원본 파일은 덮어쓰지 않았습니다."
        )
    return data


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


_CLOUD_SOURCE_TIMES: dict[tuple[str, str, str], str] = {}
_MIGRATED_LOCAL_USERS: set[str] = set()


def _timestamp_rank(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except Exception:
        return 0.0


def _file_timestamp(path: Path) -> str:
    try:
        return datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        ).isoformat()
    except Exception:
        return ""


def _record_sync_conflict(
    user_id: str,
    asset_type: str,
    asset_key: str,
    local_payload: dict[str, Any],
    cloud_payload: dict[str, Any],
    local_updated_at: str,
    cloud_updated_at: str,
    chosen_source: str,
) -> None:
    differing = sorted(
        key
        for key in set(local_payload).intersection(cloud_payload)
        if local_payload.get(key) != cloud_payload.get(key)
    )
    if not differing:
        return
    material = json.dumps(
        {
            "asset_type": asset_type,
            "asset_key": asset_key,
            "local_updated_at": local_updated_at,
            "cloud_updated_at": cloud_updated_at,
            "local": local_payload,
            "cloud": cloud_payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    conflict_id = hashlib.sha256(material.encode("utf-8")).hexdigest()
    path = _conflict_path(user_id)
    try:
        conflicts = _load_json(path, [])
        if any(
            isinstance(item, dict)
            and item.get("conflict_id") == conflict_id
            for item in conflicts
        ):
            return
        conflicts.append(
            {
                "conflict_id": conflict_id,
                "asset_type": asset_type,
                "asset_key": asset_key,
                "local_updated_at": local_updated_at,
                "cloud_updated_at": cloud_updated_at,
                "chosen_source": chosen_source,
                "differing_fields": differing,
                "local_payload": dict(local_payload),
                "cloud_payload": dict(cloud_payload),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        _save_json(path, conflicts)
    except CopilotLocalDataCorruptionError:
        # Preserve the malformed conflict file.  Failure to append an audit
        # copy must not make customer memory unreadable.
        return


def _merge_local_cloud_payload(
    *,
    user_id: str,
    asset_type: str,
    asset_key: str,
    local_payload: dict[str, Any],
    cloud_payload: dict[str, Any],
    local_updated_at: str,
    cloud_updated_at: str,
) -> tuple[dict[str, Any], str]:
    if not cloud_payload:
        return dict(local_payload), local_updated_at
    if not local_payload:
        return dict(cloud_payload), cloud_updated_at
    cloud_is_newer = (
        _timestamp_rank(cloud_updated_at)
        > _timestamp_rank(local_updated_at)
    )
    if cloud_is_newer:
        merged = dict(local_payload)
        merged.update(cloud_payload)
        chosen_source = "cloud"
        chosen_timestamp = cloud_updated_at
    else:
        # Local wins ties and unknown timestamps so a stale cloud fallback can
        # never silently roll back a recent local edit.
        merged = dict(cloud_payload)
        merged.update(local_payload)
        chosen_source = "local"
        chosen_timestamp = local_updated_at
    _record_sync_conflict(
        user_id,
        asset_type,
        asset_key,
        local_payload,
        cloud_payload,
        local_updated_at,
        cloud_updated_at,
        chosen_source,
    )
    return merged, chosen_timestamp


def _cloud_asset_payloads(
    user_id: str,
    asset_type: str,
) -> dict[str, dict[str, Any]]:
    for identity in [
        identity
        for identity in _CLOUD_SOURCE_TIMES
        if identity[0] == user_id and identity[1] == asset_type
    ]:
        _CLOUD_SOURCE_TIMES.pop(identity, None)
    payloads: dict[str, dict[str, Any]] = {}
    for row in load_copilot_assets(
        owner_user_id=user_id,
        asset_type=asset_type,
    ):
        if not isinstance(row, dict):
            continue
        key = str(row.get("asset_key", "") or "").strip()
        payload = row.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if key and isinstance(payload, dict):
            if key not in payloads:
                payloads[key] = payload
                _CLOUD_SOURCE_TIMES[(user_id, asset_type, key)] = str(
                    row.get("source_updated_at", "") or ""
                )
    return payloads


def _load_sync_meta(user_id: str) -> dict[str, str]:
    try:
        value = _load_json(_sync_meta_path(user_id), {})
    except CopilotLocalDataCorruptionError:
        return {}
    return {
        str(key): str(item or "")
        for key, item in value.items()
        if isinstance(key, str)
    }


def _sync_meta_key(asset_type: str, asset_key: str) -> str:
    return f"{asset_type}:{asset_key}"


def _local_asset_timestamp(
    user_id: str,
    asset_type: str,
    asset_key: str,
    payload: dict[str, Any],
    path: Path,
) -> str:
    for field in ("updated_at", "saved_at", "source_updated_at"):
        value = str(payload.get(field, "") or "").strip()
        if value:
            return value
    return _load_sync_meta(user_id).get(
        _sync_meta_key(asset_type, asset_key),
        "",
    ) or _file_timestamp(path)


def _set_local_asset_timestamp(
    user_id: str,
    asset_type: str,
    asset_key: str,
    source_updated_at: str,
) -> None:
    path = _sync_meta_path(user_id)
    try:
        meta = _load_json(path, {})
    except CopilotLocalDataCorruptionError:
        # Never replace malformed metadata. The primary customer asset remains
        # usable and the corrupt source remains available for recovery.
        return
    meta[_sync_meta_key(asset_type, asset_key)] = str(
        source_updated_at or datetime.now(timezone.utc).isoformat()
    )
    _save_json(path, meta)


def _collect_local_copilot_assets(user_id: str) -> list[dict[str, Any]]:
    assets: list[dict[str, Any]] = []
    memory_path = _memory_path(user_id)
    memory = _load_json(memory_path, {})
    for key, payload in memory.items():
        if not isinstance(payload, dict):
            continue
        assets.append(
            {
                "asset_type": "memory",
                "asset_key": str(key),
                "payload": dict(payload),
                "source_updated_at": _local_asset_timestamp(
                    user_id, "memory", str(key), payload, memory_path
                ),
            }
        )

    success_path = _success_path(user_id)
    cases = _load_json(success_path, [])
    for case in cases:
        if not isinstance(case, dict):
            continue
        key = str(case.get("case_id", "") or "").strip() or hashlib.sha256(
            json.dumps(case, ensure_ascii=False, sort_keys=True, default=str).encode(
                "utf-8"
            )
        ).hexdigest()
        assets.append(
            {
                "asset_type": "success_case",
                "asset_key": key,
                "payload": dict(case),
                "source_updated_at": _local_asset_timestamp(
                    user_id, "success_case", key, case, success_path
                ),
            }
        )

    checklist_path = _checklist_path(user_id)
    checklists = _load_json(checklist_path, {})
    for key, payload in checklists.items():
        if not isinstance(payload, dict):
            continue
        assets.append(
            {
                "asset_type": "checklist",
                "asset_key": str(key),
                "payload": dict(payload),
                "source_updated_at": _local_asset_timestamp(
                    user_id, "checklist", str(key), payload, checklist_path
                ),
            }
        )
    return assets


def _ensure_local_copilot_migrated(user_id: str) -> None:
    if user_id in _MIGRATED_LOCAL_USERS:
        return
    if not feature_enabled("OASIS_CLOUD_COPILOT_V1", default=False):
        return
    try:
        result = migrate_local_copilot_assets(
            owner_user_id=user_id,
            assets=_collect_local_copilot_assets(user_id),
        )
    except CopilotLocalDataCorruptionError:
        return
    if result.get("enabled") and not result.get("degraded"):
        _MIGRATED_LOCAL_USERS.add(user_id)


def _write_result(
    record: dict[str, Any],
    status: Any,
    *,
    return_status: bool,
) -> dict[str, Any] | None:
    if not return_status:
        return None
    return {
        "record": record,
        "storage_status": status.as_dict(),
    }


def _show_storage_result(result: dict[str, Any] | None) -> bool:
    status = (result or {}).get("storage_status", {})
    if isinstance(status, dict) and status.get("degraded"):
        message = str(
            status.get("error_summary") or "클라우드 저장 상태를 확인해 주세요."
        )
        st.session_state["_oasis_copilot_storage_warning"] = message
        st.warning(message)
        return True
    return False


def _business_key(company_name: str, business_no: str) -> str:
    digits = re.sub(r"[^0-9]", "", business_no)
    return digits or company_name.strip()


def get_company_memory(
    user_id: str,
    company_name: str,
    business_no: str,
) -> dict[str, Any]:
    _ensure_local_copilot_migrated(user_id)
    memory_path = _memory_path(user_id)
    data = _load_json(memory_path, {})
    if not isinstance(data, dict):
        data = {}
    business_key = _business_key(company_name, business_no)
    local_value = data.get(business_key, {})
    if not isinstance(local_value, dict):
        local_value = {}
    cloud_value = _cloud_asset_payloads(user_id, "memory").get(
        business_key,
        {},
    )
    local_updated_at = _local_asset_timestamp(
        user_id,
        "memory",
        business_key,
        local_value,
        memory_path,
    )
    cloud_updated_at = _CLOUD_SOURCE_TIMES.get(
        (user_id, "memory", business_key),
        str(cloud_value.get("updated_at", "") or "")
        if isinstance(cloud_value, dict)
        else "",
    )
    merged, chosen_updated_at = _merge_local_cloud_payload(
        user_id=user_id,
        asset_type="memory",
        asset_key=business_key,
        local_payload=local_value,
        cloud_payload=cloud_value if isinstance(cloud_value, dict) else {},
        local_updated_at=local_updated_at,
        cloud_updated_at=cloud_updated_at,
    )
    if merged and merged != local_value:
        data[business_key] = merged
        _save_json(memory_path, data)
    if merged and chosen_updated_at:
        _set_local_asset_timestamp(
            user_id, "memory", business_key, chosen_updated_at
        )
    return merged


def save_company_memory(
    user_id: str,
    company_name: str,
    business_no: str,
    memory: dict[str, Any],
    *,
    return_status: bool = False,
) -> dict[str, Any] | None:
    data = _load_json(_memory_path(user_id), {})
    if not isinstance(data, dict):
        data = {}

    record = dict(memory or {})
    record.update(
        {
            "company_name": company_name,
            "business_no": business_no,
            "updated_at": datetime.now().isoformat(
                timespec="seconds"
            ),
        }
    )
    business_key = _business_key(company_name, business_no)
    data[business_key] = record
    _save_json(_memory_path(user_id), data)
    _set_local_asset_timestamp(
        user_id,
        "memory",
        business_key,
        str(record["updated_at"]),
    )
    status = write_copilot_asset(
        owner_user_id=user_id,
        asset_type="memory",
        asset_key=business_key,
        payload=record,
        source_updated_at=str(record["updated_at"]),
    )
    if status.degraded:
        _MIGRATED_LOCAL_USERS.discard(user_id)
    return _write_result(
        record,
        status,
        return_status=return_status,
    )


def load_success_cases(user_id: str) -> list[dict[str, Any]]:
    _ensure_local_copilot_migrated(user_id)
    success_path = _success_path(user_id)
    value = _load_json(success_path, [])
    local_cases = value if isinstance(value, list) else []
    cloud_map = _cloud_asset_payloads(user_id, "success_case")
    merged_by_id: dict[str, dict[str, Any]] = {}
    for case in local_cases:
        if not isinstance(case, dict):
            continue
        identity = str(case.get("case_id", "") or "").strip() or hashlib.sha256(
            json.dumps(case, ensure_ascii=False, sort_keys=True, default=str).encode(
                "utf-8"
            )
        ).hexdigest()
        merged_by_id[identity] = dict(case)
    for identity, cloud_case in cloud_map.items():
        local_case = merged_by_id.get(identity, {})
        local_updated_at = _local_asset_timestamp(
            user_id,
            "success_case",
            identity,
            local_case,
            success_path,
        )
        cloud_updated_at = _CLOUD_SOURCE_TIMES.get(
            (user_id, "success_case", identity),
            str(cloud_case.get("saved_at", "") or ""),
        )
        chosen, chosen_updated_at = _merge_local_cloud_payload(
            user_id=user_id,
            asset_type="success_case",
            asset_key=identity,
            local_payload=local_case,
            cloud_payload=cloud_case,
            local_updated_at=local_updated_at,
            cloud_updated_at=cloud_updated_at,
        )
        merged_by_id[identity] = chosen
        if chosen_updated_at:
            _set_local_asset_timestamp(
                user_id, "success_case", identity, chosen_updated_at
            )
    merged = list(merged_by_id.values())
    merged.sort(
        key=lambda item: str(item.get("saved_at", "")),
        reverse=True,
    )
    if cloud_map and merged != local_cases:
        _save_json(success_path, merged)
    return merged


def save_success_case(
    user_id: str,
    case: dict[str, Any],
    *,
    return_status: bool = False,
) -> dict[str, Any] | None:
    cases = load_success_cases(user_id)
    record = dict(case)
    record["case_id"] = datetime.now().strftime(
        "%Y%m%d%H%M%S%f"
    )
    record["saved_at"] = datetime.now().isoformat(
        timespec="seconds"
    )
    cases.insert(0, record)
    # Local storage is a durable fallback/cache; never truncate existing cases.
    _save_json(_success_path(user_id), cases)
    _set_local_asset_timestamp(
        user_id,
        "success_case",
        str(record["case_id"]),
        str(record["saved_at"]),
    )
    status = write_copilot_asset(
        owner_user_id=user_id,
        asset_type="success_case",
        asset_key=str(record["case_id"]),
        payload=record,
        source_updated_at=str(record["saved_at"]),
    )
    if status.degraded:
        _MIGRATED_LOCAL_USERS.discard(user_id)
    return _write_result(
        record,
        status,
        return_status=return_status,
    )


def _normalize_business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return str(value or "").strip()


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text.lower() in {"", "none", "nan", "nat", "-"}:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _format_money(value: Any) -> str:
    number = _safe_number(value)
    if number is None:
        return "-"
    return f"{number:,.0f}원"


@st.cache_data(ttl=45, show_spinner=False)
def _load_integrated_company_context(
    user_id: str,
    company_name: str,
    business_no: str,
) -> dict[str, Any]:
    normalized_no = _normalize_business_no(business_no)

    stock_records = []
    for record in load_stock_valuations(user_id, limit=300):
        if not isinstance(record, dict):
            continue
        record_no = _normalize_business_no(record.get("business_no", ""))
        record_name = _clean(record.get("company_name", ""))
        if (
            normalized_no and record_no and normalized_no == record_no
        ) or (
            company_name and record_name and company_name == record_name
        ):
            stock_records.append(dict(record))

    stock_records.sort(
        key=lambda item: str(
            item.get("saved_at", "") or item.get("valuation_date", "")
        ),
        reverse=True,
    )

    journals = load_company_consultation_context(
        user_id,
        business_no,
        company_name=company_name,
        limit=20,
    )

    try:
        financial = load_financial_snapshot(user_id, normalized_no)
    except Exception:
        financial = {}
    if not isinstance(financial, dict):
        financial = {}

    try:
        registry = load_registry_snapshot(
            user_id,
            normalized_no,
            company_name=company_name,
        )
    except Exception:
        registry = {}
    try:
        from employee_status import get_latest_employee_status

        employee_status = get_latest_employee_status(
            user_id,
            normalized_no,
            company_name,
        )
    except Exception:
        employee_status = {}
    try:
        from articles_review import get_latest_articles_review

        articles_review = get_latest_articles_review(
            user_id,
            normalized_no,
            company_name,
        )
    except Exception:
        articles_review = {}
    try:
        enterprise_documents = load_enterprise_document_context(
            user_id,
            normalized_no,
            company_name,
        )
    except Exception:
        enterprise_documents = {}
    for key, value in dict(
        enterprise_documents.get("financial_fields", {}) or {}
    ).items():
        current = financial.get(key)
        if current is None or str(current).strip().lower() in {
            "", "-", "none", "nan", "nat", "<na>"
        }:
            financial[key] = value

    journal_text_parts: list[str] = []
    transcript_count = 0
    for journal in journals:
        if not isinstance(journal, dict):
            continue
        for field in (
            "consultation_title",
            "summary",
            "key_needs",
            "consultation_summary",
            "consultant_notes",
            "next_action",
            "follow_up",
            "representative_needs",
        ):
            value = _clean(journal.get(field, ""))
            if value:
                journal_text_parts.append(value)

        transcript = _clean(journal.get("transcript", ""))
        if transcript:
            transcript_count += 1
            journal_text_parts.append(transcript[:8000])

    stock_text_parts: list[str] = []
    latest_stock = stock_records[0] if stock_records else {}
    if latest_stock:
        result = latest_stock.get("result", {})
        inputs = latest_stock.get("inputs", {})
        if not isinstance(result, dict):
            result = {}
        if not isinstance(inputs, dict):
            inputs = {}

        stock_text_parts.extend([
            _clean(latest_stock.get("company_name", "")),
            _clean(latest_stock.get("valuation_date", "")),
            _clean(latest_stock.get("saved_at", "")),
            _clean(result.get("final_value_per_share", "")),
            _clean(result.get("total_equity_value", "")),
            _clean(result.get("adjusted_net_asset_value", "")),
            _clean(result.get("net_asset_value", "")),
            _clean(inputs.get("valuation_type", "")),
        ])

    registered_source_tags: list[str] = []
    if registry:
        registered_source_tags.append("법인등기 등록됨")
    if employee_status:
        registered_source_tags.append("4대보험 가입자명부 등록됨")
    if articles_review:
        registered_source_tags.append("정관 등록됨")
    document_types = {
        str(item.get("document_type", ""))
        for item in (enterprise_documents.get("records", []) or [])
        if isinstance(item, dict)
    }
    if "rnd_certificate" in document_types:
        registered_source_tags.append("연구개발부서 인정서 등록됨")
    if "tax_adjustment" in document_types:
        registered_source_tags.append("세무조정계산서 등록됨")
    combined_text = " ".join(
        part
        for part in [
            *stock_text_parts,
            *journal_text_parts,
            *registered_source_tags,
        ]
        if part
    )

    return {
        "stock_records": stock_records,
        "latest_stock": latest_stock,
        "financial": financial,
        "registry": registry,
        "employee_status": employee_status,
        "articles_review": articles_review,
        "enterprise_documents": enterprise_documents,
        "journals": journals,
        "transcript_count": transcript_count,
        "combined_text": combined_text,
    }


def _customer_text(
    customer: pd.Series,
    preferences: dict[str, Any],
    memory: dict[str, Any],
    integrated_context: dict[str, Any] | None = None,
) -> str:
    fields = [
        _clean(customer.get("업체명", "")),
        _clean(customer.get("업종명", "")),
        _clean(customer.get("사업장 소재지", "")),
        _clean(customer.get("기업규모", "")),
        _clean(customer.get("매출액", "")),
        _clean(customer.get("종업원수", "")),
        ", ".join(preferences.get("매칭키워드", []) or []),
        ", ".join(preferences.get("관심지원분야", []) or []),
        _clean(preferences.get("자금사용목적", "")),
        " ".join(
            _clean(item.get("title", ""))
            + " "
            + _clean(item.get("summary", ""))
            + " "
            + " ".join(item.get("evidence", []) or [])
            for item in (preferences.get("저장정책자금", []) or [])
            if isinstance(item, dict)
        ),
        _clean(memory.get("key_needs", "")),
        _clean(memory.get("consultant_notes", "")),
        _clean(memory.get("next_focus", "")),
        _clean((integrated_context or {}).get("combined_text", "")),
    ]
    return " ".join(field for field in fields if field)


def _case_similarity(
    current_text: str,
    case: dict[str, Any],
) -> float:
    current = _tokens(current_text)
    case_text = " ".join(
        _clean(case.get(field, ""))
        for field in [
            "industry",
            "company_profile",
            "consulting_topic",
            "trigger_keywords",
            "result_summary",
        ]
    )
    other = _tokens(case_text)

    if not current or not other:
        return 0.0

    intersection = len(current & other)
    union = len(current | other)
    jaccard = intersection / union if union else 0

    topic_bonus = 0.0
    for keyword in _tokens(case.get("trigger_keywords", "")):
        if keyword in current:
            topic_bonus += 0.04

    return min(jaccard + topic_bonus, 1.0)


def find_similar_success_cases(
    user_id: str,
    current_text: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    scored = []
    for case in load_success_cases(user_id):
        similarity = _case_similarity(current_text, case)
        if similarity <= 0:
            continue
        scored.append(
            {
                **case,
                "similarity": round(similarity * 100),
            }
        )

    scored.sort(
        key=lambda item: item["similarity"],
        reverse=True,
    )
    return scored[:limit]


def build_topic_recommendations(
    customer: pd.Series,
    preferences: dict[str, Any],
    memory: dict[str, Any],
    integrated_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    result = build_priority_recommendations(
        customer,
        preferences,
        memory,
        integrated_context,
        TOPIC_RULES,
    )
    return result.get("recommendations", [])


def _load_checklist(
    user_id: str,
    business_key: str,
) -> dict[str, bool]:
    _ensure_local_copilot_migrated(user_id)
    checklist_path = _checklist_path(user_id)
    data = _load_json(checklist_path, {})
    if not isinstance(data, dict):
        data = {}
    value = data.get(business_key, {})
    local_value = value if isinstance(value, dict) else {}
    cloud_value = _cloud_asset_payloads(user_id, "checklist").get(
        business_key,
        {},
    )
    local_updated_at = _local_asset_timestamp(
        user_id,
        "checklist",
        business_key,
        local_value,
        checklist_path,
    )
    cloud_updated_at = _CLOUD_SOURCE_TIMES.get(
        (user_id, "checklist", business_key),
        "",
    )
    merged, chosen_updated_at = _merge_local_cloud_payload(
        user_id=user_id,
        asset_type="checklist",
        asset_key=business_key,
        local_payload=local_value,
        cloud_payload=cloud_value if isinstance(cloud_value, dict) else {},
        local_updated_at=local_updated_at,
        cloud_updated_at=cloud_updated_at,
    )
    if merged and merged != local_value:
        data[business_key] = merged
        _save_json(checklist_path, data)
    if merged and chosen_updated_at:
        _set_local_asset_timestamp(
            user_id, "checklist", business_key, chosen_updated_at
        )
    return {
        str(key): bool(item)
        for key, item in merged.items()
    }


def _save_checklist(
    user_id: str,
    business_key: str,
    checklist: dict[str, bool],
    *,
    return_status: bool = False,
) -> dict[str, Any] | None:
    data = _load_json(_checklist_path(user_id), {})
    if not isinstance(data, dict):
        data = {}
    record = {
        str(key): bool(value)
        for key, value in dict(checklist or {}).items()
    }
    data[business_key] = record
    _save_json(_checklist_path(user_id), data)
    updated_at = datetime.now().isoformat(timespec="seconds")
    _set_local_asset_timestamp(
        user_id,
        "checklist",
        business_key,
        updated_at,
    )
    status = write_copilot_asset(
        owner_user_id=user_id,
        asset_type="checklist",
        asset_key=business_key,
        payload=record,
        source_updated_at=updated_at,
    )
    if status.degraded:
        _MIGRATED_LOCAL_USERS.discard(user_id)
    return _write_result(
        record,
        status,
        return_status=return_status,
    )


def render_copilot_page(
    user_id: str,
    user_name: str,
) -> None:
    pending_storage_warning = st.session_state.pop(
        "_oasis_copilot_storage_warning",
        "",
    )
    if pending_storage_warning:
        st.warning(str(pending_storage_warning))
    st.markdown("## AI 컨설팅 코파일럿")
    st.caption(
        "오아시스 내부 직원이 고객별 상담목표·필수질문·필요서류·"
        "누락사항·유사 성공사례를 한 화면에서 확인합니다."
    )

    customers = load_registered_customers(
        get_user_cumulative_db_path(user_id)
    )
    if customers.empty:
        st.info(
            "등록 고객이 없습니다. 기업등록에서 고객을 먼저 등록해주세요."
        )
        return

    labels, row_map = build_customer_labels(customers)

    explicit_business_no = str(
        st.session_state.pop("_oasis_copilot_business_no", "") or ""
    )
    explicit_company_name = str(
        st.session_state.pop("_oasis_copilot_company_name", "") or ""
    )

    active_business_no = str(
        st.session_state.get(
            "_oasis_active_company_business_no",
            "",
        )
        or ""
    )
    active_company_name = str(
        st.session_state.get(
            "_oasis_active_company_name",
            "",
        )
        or ""
    )

    active_key = (
        re.sub(r"[^0-9]", "", active_business_no)
        or active_company_name.strip()
    )
    consumed_active_key = str(
        st.session_state.get(
            "_oasis_copilot_consumed_active_key",
            "",
        )
        or ""
    )

    # Apply the enterprise-center selection only once.
    # After the first handoff, the user can freely select another company
    # inside AI Copilot without the value being overwritten on every rerun.
    should_apply_handoff = bool(
        explicit_business_no
        or explicit_company_name
        or (active_key and active_key != consumed_active_key)
    )

    if should_apply_handoff:
        prefill_business_no = (
            explicit_business_no or active_business_no
        )
        prefill_company_name = (
            explicit_company_name or active_company_name
        )
        normalized_prefill = re.sub(
            r"[^0-9]",
            "",
            prefill_business_no,
        )

        for candidate_label, candidate_index in row_map.items():
            candidate = customers.loc[candidate_index]
            candidate_business = re.sub(
                r"[^0-9]",
                "",
                str(candidate.get("사업자등록번호", "") or ""),
            )
            candidate_name = _clean(candidate.get("업체명", ""))
            if (
                normalized_prefill
                and candidate_business == normalized_prefill
            ) or (
                prefill_company_name
                and candidate_name == prefill_company_name
            ):
                st.session_state["copilot_customer"] = candidate_label
                break

        if active_key:
            st.session_state[
                "_oasis_copilot_consumed_active_key"
            ] = active_key

    if st.session_state.get("copilot_customer") not in labels:
        st.session_state.pop("copilot_customer", None)

    selected_label = st.selectbox(
        "상담할 기업",
        labels,
        key="copilot_customer",
    )
    customer = customers.loc[row_map[selected_label]]

    company_name = _clean(customer.get("업체명", ""))
    business_no = _clean(
        customer.get("사업자등록번호", "")
    )
    business_key = _business_key(
        company_name,
        business_no,
    )

    preferences = get_matching_preferences(
        user_id,
        business_no,
    )
    memory = get_company_memory(
        user_id,
        company_name,
        business_no,
    )

    integrated_context = _load_integrated_company_context(
        user_id,
        company_name,
        business_no,
    )

    priority_analysis = build_priority_recommendations(
        customer,
        preferences,
        memory,
        integrated_context,
        TOPIC_RULES,
    )
    recommendations = priority_analysis.get("recommendations", [])

    current_text = _customer_text(
        customer,
        preferences,
        memory,
        integrated_context,
    )
    similar_cases = find_similar_success_cases(
        user_id,
        current_text,
    )

    safe_company_name = html.escape(company_name or "기업명 미확인")
    safe_business_no = html.escape(business_no or "-")
    safe_user_name = html.escape(user_name or "-")
    st.markdown(
        f"""
        <div style="
            padding:20px 24px;
            border-radius:18px;
            background:linear-gradient(135deg,#172554,#2563eb);
            color:white;
            margin:8px 0 16px 0;
        ">
            <div style="font-size:1.45rem;font-weight:800;">
                {safe_company_name}
            </div>
            <div style="margin-top:6px;opacity:.9;">
                사업자번호 {safe_business_no} · 담당 {safe_user_name}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    stock_records = integrated_context.get("stock_records", [])
    journals = integrated_context.get("journals", [])
    transcript_count = int(
        integrated_context.get("transcript_count", 0) or 0
    )
    latest_stock = integrated_context.get("latest_stock", {})
    if not isinstance(latest_stock, dict):
        latest_stock = {}
    financial = integrated_context.get("financial", {}) or {}
    registry = integrated_context.get("registry", {}) or {}
    employee_status = integrated_context.get("employee_status", {}) or {}
    articles_review = integrated_context.get("articles_review", {}) or {}
    enterprise_documents = integrated_context.get("enterprise_documents", {}) or {}

    st.markdown("### AI 분석 반영자료")
    source_columns = st.columns(4, gap="medium")
    source_columns[0].metric("고객 기본정보", "반영")
    source_columns[1].metric("재무·크레탑", "반영" if financial else "미등록")
    source_columns[2].metric("법인등기", "반영" if registry else "미등록")
    source_columns[3].metric("4대보험 명부", "반영" if employee_status else "미등록")
    source_columns = st.columns(4, gap="medium")
    source_columns[0].metric("정관", "반영" if articles_review else "미등록")
    source_columns[1].metric(
        "추가 기업자료",
        f"{len(enterprise_documents.get('records', []) or [])}건",
    )
    source_columns[2].metric("주가평가", f"{len(stock_records)}건")
    source_columns[3].metric(
        "상담·녹취",
        f"{len(journals)}건 / {transcript_count}건",
    )

    if not any([
        stock_records,
        journals,
        financial,
        registry,
        employee_status,
        articles_review,
        enterprise_documents.get("records", []),
    ]):
        st.warning(
            "이 기업의 사업자등록번호 또는 법인명이 저장자료와 연결되지 않았습니다. "
            "기업컨설팅에서 동일 기업을 선택해 주가평가·상담일지를 저장했는지 확인해주세요."
        )
    else:
        with st.expander("AI에 반영된 기업컨설팅 자료 확인", expanded=False):
            if latest_stock:
                result = latest_stock.get("result", {})
                if not isinstance(result, dict):
                    result = {}
                st.markdown("#### 최근 주가평가")
                s1, s2, s3 = st.columns(3)
                s1.metric(
                    "평가기준일",
                    str(latest_stock.get("valuation_date", "") or "-"),
                )
                s2.metric(
                    "1주당 평가액",
                    _format_money(result.get("final_value_per_share")),
                )
                s3.metric(
                    "전체 주식가치",
                    _format_money(result.get("total_equity_value")),
                )

            if journals:
                st.markdown("#### 최근 상담일지")
                for journal in journals[:5]:
                    title = _clean(
                        journal.get("consultation_title", "")
                    ) or "녹음 상담일지"
                    saved_at = _clean(journal.get("saved_at", ""))
                    summary = _clean(
                        journal.get("summary", "")
                        or journal.get("consultation_summary", "")
                    )
                    st.markdown(
                        f"**{title}** · {saved_at or '일자 미확인'}"
                    )
                    if summary:
                        st.caption(summary[:1000])

            connected_records = enterprise_documents.get("records", []) or []
            if connected_records:
                st.markdown("#### 기업정보등록 첨부자료")
                st.dataframe(
                    pd.DataFrame([
                        {
                            "자료": item.get("document_label", ""),
                            "등록일": str(item.get("uploaded_at", ""))[:19],
                            "분석": item.get("analysis_summary", ""),
                        }
                        for item in connected_records[:20]
                    ]),
                    hide_index=True,
                    use_container_width=True,
                )

    saved_policy_items = preferences.get("저장정책자금", []) or []
    saved_policy_items = [
        item for item in saved_policy_items if isinstance(item, dict)
    ]
    if saved_policy_items:
        st.markdown("### 저장된 정책자금 추천")
        st.caption(
            f"기업컨설팅에서 확정 저장한 추천 {len(saved_policy_items)}건 · "
            f"최소점수 {preferences.get('저장정책자금_최소점수', '-')}점"
        )
        policy_rows = []
        for item in saved_policy_items[:20]:
            policy_rows.append(
                {
                    "점수": item.get("score", ""),
                    "분류": item.get("category", ""),
                    "공고명": item.get("title", ""),
                    "기관": item.get("agency", ""),
                    "신청종료": item.get("end_date", ""),
                }
            )
        st.dataframe(
            pd.DataFrame(policy_rows),
            hide_index=True,
            use_container_width=True,
        )

    top = recommendations[:3]
    st.markdown("### 이번 상담 핵심 우선순위")
    stage_columns = st.columns([1, 1, 2], gap="medium")
    stage_columns[0].metric(
        "기업 성장단계",
        priority_analysis.get("stage", "판단보류"),
    )
    stage_columns[1].metric(
        "기업규모 점수",
        f"{priority_analysis.get('scale_score', 0)}점",
    )
    stage_columns[2].caption(
        priority_analysis.get("stage_reason", "")
        + "\n\n점수기준: "
        + priority_analysis.get("method", "")
    )

    st.markdown(
        """
        <style>
        .copilot-priority-card {min-height:140px;padding:18px;border:1px solid #d9e3f0;border-radius:15px;background:linear-gradient(145deg,#ffffff,#f6f9fd);box-shadow:0 5px 16px rgba(15,42,80,.07);position:relative;overflow:hidden;margin-bottom:7px;}
        .copilot-priority-card:before {content:"";position:absolute;left:0;top:0;right:0;height:4px;background:#1e5bd7;}
        .copilot-priority-topic {font-weight:800;color:#344054;font-size:.92rem;margin-top:4px;min-height:34px;}
        .copilot-priority-score {font-size:1.75rem;font-weight:800;color:#0b2b5b;letter-spacing:-.04em;margin:5px 0;}
        .copilot-priority-note {font-size:.78rem;color:#5b687b;line-height:1.45;}
        </style>
        """,
        unsafe_allow_html=True,
    )
    columns = st.columns(min(len(top), 5), gap="medium")
    for index, item in enumerate(top):
        evidence_items = item.get("evidence", []) or []
        penalty_items = item.get("penalties", []) or []
        evidence = (
            "근거: " + " / ".join(evidence_items[:2])
            if evidence_items
            else "확인된 직접 근거가 부족합니다."
        )
        penalty = (
            "감점: " + " / ".join(penalty_items[:1])
            if penalty_items
            else ""
        )
        safe_topic = html.escape(str(item.get("topic", "")))
        safe_score = html.escape(str(item.get("score", 0)))
        safe_status = html.escape(str(item.get("status", "")))
        safe_confidence = html.escape(
            str(item.get("confidence", 0))
        )
        safe_evidence = html.escape(evidence)
        safe_penalty = html.escape(penalty)
        card_html = (
            '<div class="copilot-priority-card">'
            f'<div class="copilot-priority-topic">{safe_topic}</div>'
            f'<div class="copilot-priority-score">{safe_score}점</div>'
            '<div class="copilot-priority-note">'
            f'{safe_status} · 확신도 {safe_confidence}점<br>'
            f'{safe_evidence}<br>{safe_penalty}'
            '</div></div>'
        )
        with columns[index]:
            st.markdown(card_html, unsafe_allow_html=True)

    with st.expander("우선순위 점수 산정근거", expanded=False):
        st.caption(
            "단순 키워드 개수가 아니라 기업규모·실제 금액·재무비율·성장단계·"
            "대표 의도·자료충족도·실행가능성을 함께 반영합니다."
        )
        for rank, item in enumerate(recommendations, start=1):
            st.markdown(
                f"**{rank}. {item.get('topic', '')} — "
                f"{item.get('score', 0)}점 / 확신도 "
                f"{item.get('confidence', 0)}점**"
            )
            for value in item.get("evidence", []) or []:
                st.write(f"- 가점근거: {value}")
            for value in item.get("penalties", []) or []:
                st.write(f"- 감점근거: {value}")
            components = item.get("components", {}) or {}
            if components:
                st.caption(
                    "점수 구성: "
                    + ", ".join(
                        f"{name} {score:+d}"
                        for name, score in components.items()
                    )
                )

    (
        tab_report,
        tab_tax,
        tab_playbook,
        tab_memory,
        tab_success,
        tab_review,
    ) = st.tabs(
        [
            "AI 상담보고서",
            "AI 절세진단",
            "상담 플레이북",
            "기업 메모리",
            "성공사례",
            "미팅 종료점검",
        ]
    )

    with tab_report:
        st.caption(
            "선택한 기업의 고객DB·재무·등기·주가평가·매칭설정을 "
            "한 번에 결합한 상담 사전진단입니다."
        )
        render_ai_consulting_report_page(
            user_id,
            user_name,
            customer=customer,
            embedded=True,
            key_prefix=f"copilot_report_{business_key}",
        )

    with tab_tax:
        render_tax_diagnosis_page(
            user_id,
            customer,
            key_prefix=f"copilot_tax_{business_key}",
        )

    with tab_playbook:
        scenario_brief = build_scenario_brief(
            recommendations,
            memory,
        )

        st.markdown("#### AI 실시간 상담 시나리오")
        st.caption(
            "대표 답변을 입력하면 감지된 니즈를 바탕으로 "
            "다음 질문과 연결 가능한 컨설팅 주제를 추천합니다."
        )

        opening_questions = scenario_brief.get(
            "opening_questions",
            [],
        )
        if opening_questions:
            st.markdown("##### 추천 시작 질문")
            for rank, item in enumerate(opening_questions, start=1):
                st.markdown(
                    f"**{rank}. [{item.get('topic', '기타')}] "
                    f"{item.get('question', '')}**"
                )
                st.caption(item.get("reason", ""))

        default_question_options = [
            item.get("question", "")
            for item in opening_questions
            if item.get("question")
        ]
        if not default_question_options:
            default_question_options = [
                "올해 가장 해결하고 싶은 경영상 문제는 무엇입니까?"
            ]

        scenario_question = st.selectbox(
            "현재 질문",
            default_question_options,
            key=f"scenario_question_{business_key}",
        )
        representative_answer = st.text_area(
            "대표 답변",
            placeholder=(
                "예: 올해 하반기에 직원 3명을 채용하고 "
                "기계설비도 약 2억원 정도 도입할 예정입니다."
            ),
            height=120,
            key=f"scenario_answer_{business_key}",
        )

        scenario_result_key = f"scenario_result_{business_key}"
        if st.button(
            "답변 분석하고 다음 질문 추천",
            type="primary",
            use_container_width=True,
            key=f"analyze_scenario_{business_key}",
        ):
            st.session_state[scenario_result_key] = (
                analyze_representative_answer(
                    representative_answer,
                    current_topic=scenario_question,
                    recommendations=recommendations,
                )
            )

        scenario_result = st.session_state.get(
            scenario_result_key,
            {},
        )
        if scenario_result:
            sr1, sr2, sr3 = st.columns(3, gap="medium")
            sr1.metric(
                "대표 반응",
                scenario_result.get("intent", "-"),
            )
            sr2.metric(
                "분석 확신도",
                f"{scenario_result.get('confidence', 0)}점",
            )
            sr3.metric(
                "연결 제안",
                f"{len(scenario_result.get('services', []))}개",
            )
            st.info(scenario_result.get("summary", ""))

            detected = scenario_result.get("signals", []) or []
            if detected:
                st.markdown("##### 감지된 대표 니즈")
                signal_rows = [
                    {
                        "니즈": item.get("name", ""),
                        "확신도": item.get("score", 0),
                        "감지어": ", ".join(
                            item.get("matched", []) or []
                        ),
                    }
                    for item in detected
                ]
                st.dataframe(
                    pd.DataFrame(signal_rows),
                    hide_index=True,
                    use_container_width=True,
                )

            q_col, p_col = st.columns(2, gap="large")
            with q_col:
                st.markdown("##### 다음 추천 질문")
                for index, question in enumerate(
                    scenario_result.get("next_questions", []) or [],
                    start=1,
                ):
                    st.write(f"{index}. {question}")
            with p_col:
                st.markdown("##### 연결 가능한 제안")
                for service in (
                    scenario_result.get("services", []) or []
                ):
                    st.write(f"- {service}")

            points = scenario_result.get("talking_points", []) or []
            if points:
                st.markdown("##### 영업사원 설명 포인트")
                for point in points:
                    st.success(point)

            if st.button(
                "시나리오 분석 초기화",
                use_container_width=True,
                key=f"clear_scenario_{business_key}",
            ):
                st.session_state.pop(scenario_result_key, None)
                st.session_state.pop(
                    f"scenario_answer_{business_key}",
                    None,
                )
                st.rerun()

        st.divider()

        checklist = _load_checklist(
            user_id,
            business_key,
        )

        selected_topics = st.multiselect(
            "이번 상담에서 다룰 주제",
            [item["topic"] for item in recommendations],
            default=[
                item["topic"]
                for item in recommendations[:3]
            ],
            key=f"copilot_topics_{business_key}",
        )

        question_items = []
        document_items = []

        for item in recommendations:
            if item["topic"] not in selected_topics:
                continue
            for question in item["questions"]:
                question_items.append(
                    (item["topic"], question)
                )
            for document in item["documents"]:
                document_items.append(
                    (item["topic"], document)
                )

        st.markdown("#### 필수 질문 체크리스트")
        updated = {}
        completed = 0

        for topic, question in question_items:
            key = f"Q|{topic}|{question}"
            checked = st.checkbox(
                f"[{topic}] {question}",
                value=bool(checklist.get(key, False)),
                key=f"copilot_{business_key}_{abs(hash(key))}",
            )
            updated[key] = checked
            completed += int(checked)

        total = len(question_items)
        progress = (
            completed / total
            if total
            else 0
        )
        st.progress(progress)
        st.caption(
            f"질문 진행률 {completed}/{total} "
            f"({progress * 100:.0f}%)"
        )

        st.markdown("#### 요청서류 체크리스트")
        for topic, document in list(
            dict.fromkeys(document_items)
        ):
            key = f"D|{topic}|{document}"
            checked = st.checkbox(
                f"[{topic}] {document}",
                value=bool(checklist.get(key, False)),
                key=f"copilot_{business_key}_{abs(hash(key))}",
            )
            updated[key] = checked

        if st.button(
            "체크리스트 저장",
            type="primary",
            use_container_width=True,
            key=f"save_copilot_checklist_{business_key}",
        ):
            storage_result = _save_checklist(
                user_id,
                business_key,
                updated,
                return_status=True,
            )
            _show_storage_result(storage_result)
            st.success("상담 체크리스트를 저장했습니다.")

    with tab_memory:
        st.info(
            "기업별 메모리는 다음 상담에서 우선 질문과 추천순서를 만드는 데 사용됩니다."
        )

        key_needs = st.text_area(
            "핵심 니즈",
            value=_clean(memory.get("key_needs", "")),
            height=110,
            key=f"memory_needs_{business_key}",
        )
        decision_style = st.selectbox(
            "의사결정 성향",
            [
                "미확인",
                "빠른 편",
                "신중한 편",
                "자료 중심",
                "가격 민감",
                "관계 중심",
            ],
            index=(
                [
                    "미확인",
                    "빠른 편",
                    "신중한 편",
                    "자료 중심",
                    "가격 민감",
                    "관계 중심",
                ].index(
                    memory.get(
                        "decision_style",
                        "미확인",
                    )
                )
                if memory.get(
                    "decision_style",
                    "미확인",
                )
                in [
                    "미확인",
                    "빠른 편",
                    "신중한 편",
                    "자료 중심",
                    "가격 민감",
                    "관계 중심",
                ]
                else 0
            ),
            key=f"memory_style_{business_key}",
        )
        positive_topics = st.text_input(
            "반응이 좋았던 주제",
            value=_clean(
                memory.get("positive_topics", "")
            ),
            key=f"memory_positive_{business_key}",
        )
        resistance_topics = st.text_input(
            "거부감·주의 주제",
            value=_clean(
                memory.get("resistance_topics", "")
            ),
            key=f"memory_resistance_{business_key}",
        )
        next_focus = st.text_area(
            "다음 상담의 우선 확인사항",
            value=_clean(memory.get("next_focus", "")),
            height=110,
            key=f"memory_next_{business_key}",
        )
        consultant_notes = st.text_area(
            "내부 컨설턴트 메모",
            value=_clean(
                memory.get("consultant_notes", "")
            ),
            height=130,
            key=f"memory_notes_{business_key}",
        )

        if st.button(
            "기업 메모리 저장",
            use_container_width=True,
            key=f"save_memory_{business_key}",
        ):
            storage_result = save_company_memory(
                user_id,
                company_name,
                business_no,
                {
                    "key_needs": key_needs,
                    "decision_style": decision_style,
                    "positive_topics": positive_topics,
                    "resistance_topics": resistance_topics,
                    "next_focus": next_focus,
                    "consultant_notes": consultant_notes,
                },
                return_status=True,
            )
            _show_storage_result(storage_result)
            st.success(
                "기업 메모리를 저장했습니다. 다음 상담 추천에 반영됩니다."
            )

    with tab_success:
        st.markdown("#### 유사한 내부 성공사례")
        if similar_cases:
            for case in similar_cases:
                with st.expander(
                    f"{case.get('consulting_topic', '성공사례')} "
                    f"· 유사도 {case['similarity']}%",
                    expanded=False,
                ):
                    st.write(
                        f"**업종:** {case.get('industry', '-')}"
                    )
                    st.write(
                        f"**기업 특성:** {case.get('company_profile', '-')}"
                    )
                    st.write(
                        f"**성공요인:** {case.get('success_factors', '-')}"
                    )
                    st.write(
                        f"**결과:** {case.get('result_summary', '-')}"
                    )
                    st.write(
                        f"**추천 질문:** {case.get('best_questions', '-')}"
                    )
        else:
            st.info(
                "등록된 성공사례가 없습니다. 아래에서 첫 사례를 등록해주세요."
            )

        st.markdown("#### 성공사례 등록")
        s1, s2 = st.columns(2)
        with s1:
            case_industry = st.text_input(
                "업종",
                value=_clean(customer.get("업종명", "")),
                key=f"case_industry_{business_key}",
            )
            consulting_topic = st.text_input(
                "계약·성공 주제",
                key=f"case_topic_{business_key}",
            )
            trigger_keywords = st.text_input(
                "핵심 키워드",
                placeholder="기계투자, 신규채용, 가지급금 등",
                key=f"case_keywords_{business_key}",
            )
        with s2:
            company_profile = st.text_area(
                "기업 특성",
                height=90,
                key=f"case_profile_{business_key}",
            )
            success_factors = st.text_area(
                "성공요인",
                height=90,
                key=f"case_factors_{business_key}",
            )

        result_summary = st.text_area(
            "결과 요약",
            height=90,
            key=f"case_result_{business_key}",
        )
        best_questions = st.text_area(
            "효과적이었던 질문·설명 순서",
            height=90,
            key=f"case_questions_{business_key}",
        )

        if st.button(
            "성공사례 저장",
            use_container_width=True,
            key=f"save_success_case_{business_key}",
        ):
            storage_result = save_success_case(
                user_id,
                {
                    "source_company_name": company_name,
                    "industry": case_industry,
                    "company_profile": company_profile,
                    "consulting_topic": consulting_topic,
                    "trigger_keywords": trigger_keywords,
                    "success_factors": success_factors,
                    "result_summary": result_summary,
                    "best_questions": best_questions,
                },
                return_status=True,
            )
            _show_storage_result(storage_result)
            st.success(
                "내부 성공사례를 저장했습니다. 이후 유사 기업 추천에 사용됩니다."
            )
            st.rerun()

    with tab_review:
        checklist = _load_checklist(
            user_id,
            business_key,
        )
        selected_topics = [
            item["topic"]
            for item in recommendations[:3]
        ]

        required_questions = []
        for item in recommendations:
            if item["topic"] in selected_topics:
                required_questions.extend(
                    [
                        (item["topic"], question)
                        for question in item["questions"]
                    ]
                )

        missed = []
        completed = []

        for topic, question in required_questions:
            key = f"Q|{topic}|{question}"
            if checklist.get(key, False):
                completed.append(
                    f"[{topic}] {question}"
                )
            else:
                missed.append(
                    f"[{topic}] {question}"
                )

        total = len(required_questions)
        score = round(
            len(completed) / total * 100
        ) if total else 0

        m1, m2, m3 = st.columns(3)
        m1.metric("상담 완성도", f"{score}점")
        m2.metric("완료 질문", f"{len(completed)}개")
        m3.metric("누락 질문", f"{len(missed)}개")

        if missed:
            st.warning("다음 상담에서 확인할 누락사항")
            for item in missed:
                st.write(f"- {item}")
        else:
            st.success(
                "우선 상담주제의 필수질문을 모두 확인했습니다."
            )

        follow_up = "\n".join(
            f"- {item}"
            for item in missed[:7]
        )
        st.text_area(
            "다음 상담 TODO",
            value=follow_up,
            height=180,
            key=f"copilot_followup_{business_key}",
        )
