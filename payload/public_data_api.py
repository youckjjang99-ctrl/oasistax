from __future__ import annotations

import os
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import requests


SERVICE_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
NPS_BASE_URL = (
    "https://apis.data.go.kr/B552015/"
    "NpsBplcInfoInqireServiceV2/getBassInfoSearchV2"
)
REGION_CODES = {
    "서울특별시": "11",
    "경기도": "41",
}


def _service_key() -> str:
    raw = os.environ.get(SERVICE_KEY_ENV, "").strip()
    if not raw:
        return ""
    # 공공데이터포털의 Encoding 키가 입력된 경우 requests가 다시
    # 인코딩하지 않도록 한 번만 원문으로 되돌린다.
    return unquote(raw)


def service_key_status() -> dict[str, Any]:
    key = _service_key()
    if not key:
        return {
            "configured": False,
            "masked": "미등록",
            "length": 0,
            "env_name": SERVICE_KEY_ENV,
        }
    visible = key[:4] if len(key) >= 4 else ""
    return {
        "configured": True,
        "masked": f"{visible}{'*' * min(max(len(key) - 4, 8), 24)}",
        "length": len(key),
        "env_name": SERVICE_KEY_ENV,
    }


def _xml_error(text: str) -> tuple[str, str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return "", ""
    code = (
        root.findtext(".//resultCode")
        or root.findtext(".//returnReasonCode")
        or ""
    ).strip()
    message = (
        root.findtext(".//resultMsg")
        or root.findtext(".//returnAuthMsg")
        or root.findtext(".//errMsg")
        or ""
    ).strip()
    return code, message


def _items_from_json(payload: dict[str, Any]) -> tuple[str, str, int, list[dict]]:
    response = payload.get("response", payload)
    header = response.get("header", {}) if isinstance(response, dict) else {}
    body = response.get("body", {}) if isinstance(response, dict) else {}
    code = str(header.get("resultCode", "")).strip()
    message = str(header.get("resultMsg", "")).strip()
    total_count = int(body.get("totalCount") or 0) if isinstance(body, dict) else 0
    items_block = body.get("items", {}) if isinstance(body, dict) else {}
    if isinstance(items_block, dict):
        items = items_block.get("item", [])
    else:
        items = items_block
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []
    return code, message, total_count, [
        item for item in items if isinstance(item, dict)
    ]


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return ""


def _integer(value: Any) -> int:
    digits = re.sub(r"[^0-9-]", "", str(value or ""))
    try:
        return int(digits)
    except (TypeError, ValueError):
        return 0


def _business_no(value: Any) -> str:
    text = str(value or "").strip()
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return text


def _region_from_address(address: str, region_code: str = "") -> str:
    compact = re.sub(r"\s+", " ", str(address or "")).strip()
    if compact.startswith(("서울", "서울특별시")):
        return "서울특별시"
    if compact.startswith(("경기", "경기도")):
        return "경기도"
    if compact:
        return ""
    if region_code == "11":
        return "서울특별시"
    if region_code == "41":
        return "경기도"
    return ""


def _priority(employee_count: int, new_count: int, lost_count: int) -> tuple[int, list[str]]:
    score = 10
    reasons: list[str] = []
    if 5 <= employee_count <= 49:
        score += 30
        reasons.append("고용지원금 상담 적정 인원구간")
    elif 50 <= employee_count <= 299:
        score += 22
        reasons.append("중소기업 고용지원 검토 가능")
    elif employee_count >= 3:
        score += 12
        reasons.append("국민연금 가입자 3인 이상")
    if new_count > 0:
        score += min(25, 10 + new_count * 3)
        reasons.append(f"최근 신규취득자 {new_count}명")
    if lost_count > 0:
        score += min(10, lost_count * 2)
        reasons.append(f"최근 상실가입자 {lost_count}명")
    if employee_count >= 10 and new_count >= 2:
        score += 10
        reasons.append("채용활동이 확인되는 사업장")
    return min(score, 100), reasons


def normalize_nps_workplace(
    item: dict[str, Any],
    requested_region_code: str = "",
) -> dict[str, Any]:
    company_name = str(
        _first(item, "wkplNm", "wkpl_nm", "사업장명")
    ).strip()
    business_no = _business_no(
        _first(item, "bzowrRgstNo", "bzowr_rgst_no", "사업자등록번호")
    )
    address = str(
        _first(
            item,
            "wkplRoadNmDtlAddr",
            "wkpl_road_nm_dtl_addr",
            "wkplAddr",
            "wkpl_addr",
            "사업장도로명상세주소",
            "사업장주소",
        )
    ).strip()
    returned_region_code = str(
        _first(
            item,
            "ldongAddrMgplDgCd",
            "ldong_addr_mgpl_dg_cd",
        )
    ).strip()
    region_code = returned_region_code or requested_region_code
    employee_count = _integer(
        _first(item, "jnngpCnt", "jnngp_cnt", "가입자수")
    )
    new_count = _integer(
        _first(item, "nwAcqzrCnt", "nw_acqzr_cnt", "신규취득자수")
    )
    lost_count = _integer(
        _first(item, "lssJnngpCnt", "lss_jnngp_cnt", "상실가입자수")
    )
    score, reasons = _priority(employee_count, new_count, lost_count)
    sequence = str(_first(item, "seq", "자료순번")).strip()
    identity = "|".join((company_name, business_no, address))
    source_key = sequence or hashlib.sha256(
        identity.encode("utf-8")
    ).hexdigest()[:32]
    region = _region_from_address(address, region_code)

    return {
        "선택": True,
        "source": "nps_workplace_v2",
        "source_key": source_key,
        "사업장명": company_name,
        "사업자등록번호": business_no,
        "주소": address,
        "지역": region,
        "지역코드": region_code,
        "업종코드": str(
            _first(item, "wkplIntpCd", "wkpl_intp_cd", "사업장업종코드")
        ).strip(),
        "업종명": str(
            _first(item, "vldtVlKrnNm", "vldt_vl_krn_nm", "업종명")
        ).strip(),
        "가입자수": employee_count,
        "신규취득자수": new_count,
        "상실가입자수": lost_count,
        "당월고지금액": _integer(
            _first(item, "crrmmNtcAmt", "crrmm_ntc_amt", "당월고지금액")
        ),
        "자료생성년월": str(
            _first(item, "dataCrtYm", "data_crt_ym", "자료생성년월")
        ).strip(),
        "우선순위점수": score,
        "추천사유": reasons,
        "원본데이터": item,
    }


def fetch_nps_workplaces(
    region_code: str,
    *,
    page_no: int = 1,
    rows: int = 50,
    sigungu_code: str = "",
    emd_code: str = "",
    timeout: int = 30,
) -> dict[str, Any]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    key = _service_key()
    if not key:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"Railway 환경변수 {SERVICE_KEY_ENV}가 없습니다.",
            "checked_at": checked_at,
        }
    if region_code not in REGION_CODES.values():
        return {
            "ok": False,
            "status": "INVALID_REGION",
            "message": "서울·경기 지역코드만 조회할 수 있습니다.",
            "checked_at": checked_at,
        }
    page_no = max(1, int(page_no))
    rows = min(100, max(1, int(rows)))
    params = {
        "serviceKey": key,
        "pageNo": page_no,
        "numOfRows": rows,
        "dataType": "json",
        "ldongAddrMgplDgCd": region_code,
    }
    if str(sigungu_code).strip():
        params["ldongAddrMgplSgguCd"] = str(sigungu_code).strip()
    if str(emd_code).strip():
        params["ldongAddrMgplSgguEmdCd"] = str(emd_code).strip()

    try:
        response = requests.get(
            NPS_BASE_URL,
            params=params,
            timeout=timeout,
            headers={"User-Agent": "OASIS-CRM/9.6.0"},
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "국민연금 API 응답시간이 초과되었습니다.",
            "checked_at": checked_at,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": f"국민연금 API 연결 실패: {type(exc).__name__}",
            "checked_at": checked_at,
        }

    common = {
        "http_status": response.status_code,
        "checked_at": checked_at,
        "region_code": region_code,
        "page_no": page_no,
        "rows": rows,
    }
    text = response.text or ""
    content_type = response.headers.get("content-type", "").lower()
    if "json" in content_type or text.lstrip().startswith(("{", "[")):
        try:
            payload = response.json()
        except ValueError:
            return {
                **common,
                "ok": False,
                "status": "INVALID_JSON",
                "message": "API가 해석할 수 없는 JSON을 반환했습니다.",
            }
        if not isinstance(payload, dict):
            return {
                **common,
                "ok": False,
                "status": "INVALID_RESPONSE",
                "message": "API 응답 구조가 예상과 다릅니다.",
            }
        code, message, total_count, items = _items_from_json(payload)
        success = response.ok and code in {"00", "0", ""}
        normalized = [
            normalize_nps_workplace(item, region_code) for item in items
        ]
        target_names = {
            name for name, code_value in REGION_CODES.items()
            if code_value == region_code
        }
        in_region = [
            item for item in normalized if item.get("지역") in target_names
        ]
        return {
            **common,
            "ok": success,
            "status": "CONNECTED" if success else (code or "API_ERROR"),
            "message": (
                "국민연금 사업장 데이터를 조회했습니다."
                if success
                else (message or "API 인증 또는 요청조건을 확인해 주세요.")
            ),
            "result_code": code,
            "result_message": message,
            "total_count": total_count,
            "received_count": len(normalized),
            "filtered_out_count": len(normalized) - len(in_region),
            "items": in_region,
            "response_format": "JSON",
        }

    code, message = _xml_error(text)
    return {
        **common,
        "ok": False,
        "status": code or "API_ERROR",
        "message": message or f"API가 HTTP {response.status_code}을 반환했습니다.",
        "result_code": code,
        "result_message": message,
        "total_count": 0,
        "received_count": 0,
        "filtered_out_count": 0,
        "items": [],
        "response_format": "XML",
    }


def test_nps_connection(
    region_code: str = "11",
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    key = _service_key()
    if not key:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"Railway 환경변수 {SERVICE_KEY_ENV}가 없습니다.",
            "checked_at": checked_at,
        }
    if region_code not in REGION_CODES.values():
        return {
            "ok": False,
            "status": "INVALID_REGION",
            "message": "허용되지 않은 지역코드입니다.",
            "checked_at": checked_at,
        }

    params = {
        "serviceKey": key,
        "pageNo": 1,
        "numOfRows": 1,
        "dataType": "json",
        "ldongAddrMgplDgCd": region_code,
    }
    try:
        response = requests.get(
            NPS_BASE_URL,
            params=params,
            timeout=timeout,
            headers={"User-Agent": "OASIS-CRM/9.5.0"},
        )
    except requests.Timeout:
        return {
            "ok": False,
            "status": "TIMEOUT",
            "message": "국민연금 API 응답시간이 초과되었습니다.",
            "checked_at": checked_at,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": f"국민연금 API 연결 실패: {type(exc).__name__}",
            "checked_at": checked_at,
        }

    common = {
        "http_status": response.status_code,
        "checked_at": checked_at,
        "region_code": region_code,
    }
    text = response.text or ""
    content_type = response.headers.get("content-type", "").lower()

    if "json" in content_type or text.lstrip().startswith(("{", "[")):
        try:
            payload = response.json()
        except ValueError:
            return {
                **common,
                "ok": False,
                "status": "INVALID_JSON",
                "message": "API가 해석할 수 없는 JSON을 반환했습니다.",
            }
        if not isinstance(payload, dict):
            return {
                **common,
                "ok": False,
                "status": "INVALID_RESPONSE",
                "message": "API 응답 구조가 예상과 다릅니다.",
            }
        code, message, total_count, items = _items_from_json(payload)
        success = response.ok and code in {"00", "0", ""}
        return {
            **common,
            "ok": success,
            "status": "CONNECTED" if success else (code or "API_ERROR"),
            "message": (
                "국민연금 사업장 API 연결에 성공했습니다."
                if success
                else (message or "API 인증 또는 요청조건을 확인해 주세요.")
            ),
            "result_code": code,
            "result_message": message,
            "total_count": total_count,
            "sample": items[:1],
            "response_format": "JSON",
        }

    code, message = _xml_error(text)
    success = response.ok and code in {"00", "0"} and not message
    return {
        **common,
        "ok": success,
        "status": "CONNECTED" if success else (code or "API_ERROR"),
        "message": (
            "국민연금 사업장 API 연결에 성공했습니다."
            if success
            else (message or f"API가 HTTP {response.status_code}을 반환했습니다.")
        ),
        "result_code": code,
        "result_message": message,
        "total_count": 0,
        "sample": [],
        "response_format": "XML",
    }
