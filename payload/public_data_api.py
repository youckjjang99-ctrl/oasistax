from __future__ import annotations

import os
import hashlib
import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import requests


SERVICE_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
NPS_BASE_URL = (
    "https://apis.data.go.kr/B552015/"
    "NpsBplcInfoInqireServiceV2/getBassInfoSearchV2"
)
NPS_DETAIL_URL = (
    "https://apis.data.go.kr/B552015/"
    "NpsBplcInfoInqireServiceV2/getDetailInfoSearchV2"
)
REGION_CODES = {
    "서울특별시": "11",
    "경기도": "41",
}
STOCK_COMPANY_MARKERS = ("주식회사", "(주)", "㈜", "（주）")
EXCLUDED_LEGAL_MARKERS = (
    "농업회사법인",
    "유한회사",
    "합자회사",
    "합명회사",
    "영농조합법인",
    "사단법인",
    "재단법인",
)
OTHER_LEGAL_ENTITY_MARKERS = (
    "의료법인",
    "사회복지법인",
    "학교법인",
    "법무법인",
    "세무법인",
    "회계법인",
    "특허법인",
    "협동조합",
)


def is_stock_company_name(value: Any) -> bool:
    name = re.sub(r"\s+", "", str(value or ""))
    if any(marker in name for marker in EXCLUDED_LEGAL_MARKERS):
        return False
    return any(marker in name for marker in STOCK_COMPANY_MARKERS)


def is_individual_business_candidate(value: Any) -> bool:
    """법인 형태가 이름에 표시되지 않은 사업장을 개인사업자 후보로 분류."""
    name = re.sub(r"\s+", "", str(value or ""))
    if len(name) < 2:
        return False
    legal_markers = (
        STOCK_COMPANY_MARKERS
        + EXCLUDED_LEGAL_MARKERS
        + OTHER_LEGAL_ENTITY_MARKERS
    )
    return not any(marker in name for marker in legal_markers)


def business_type_label(value: Any) -> str:
    if is_stock_company_name(value):
        return "주식회사"
    if is_individual_business_candidate(value):
        return "개인사업자 후보"
    return "기타 법인·단체"


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
    if not items and isinstance(body, dict):
        items = body.get("item", [])
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []
    return code, message, total_count, [
        item for item in items if isinstance(item, dict)
    ]


def _get_with_retry(
    url: str,
    *,
    params: dict[str, Any],
    timeout: int = 30,
    retries: int = 2,
) -> tuple[Any | None, str, str, int]:
    last_status = "NETWORK_ERROR"
    last_message = "API 연결에 실패했습니다."
    attempts = max(1, int(retries) + 1)
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                params=params,
                timeout=max(5, int(timeout)),
                headers={"User-Agent": "OASIS-CRM/9.6.1"},
            )
            if response.status_code >= 500 and attempt + 1 < attempts:
                last_status = f"HTTP_{response.status_code}"
                last_message = "공공데이터 서버가 일시적으로 응답하지 않습니다."
                time.sleep(0.6 * (attempt + 1))
                continue
            return response, "", "", attempt + 1
        except requests.Timeout:
            last_status = "TIMEOUT"
            last_message = "국민연금 API 응답시간이 초과되었습니다."
        except requests.RequestException as exc:
            last_status = "NETWORK_ERROR"
            last_message = f"국민연금 API 연결 실패: {type(exc).__name__}"
        if attempt + 1 < attempts:
            time.sleep(0.6 * (attempt + 1))
    return None, last_status, last_message, attempts


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
    *,
    detail_status: str = "SUCCESS",
    detail_message: str = "",
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
        "선택": detail_status == "SUCCESS",
        "source": "nps_workplace_v2",
        "source_key": source_key,
        "사업장명": company_name,
        "사업자유형": business_type_label(company_name),
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
        "상세조회상태": detail_status,
        "상세조회메시지": detail_message,
        "원본데이터": item,
    }


def _fetch_nps_detail(
    basic_item: dict[str, Any],
    *,
    key: str,
    timeout: int,
    retries: int,
) -> tuple[dict[str, Any], bool, str, int]:
    sequence = str(_first(basic_item, "seq", "자료순번")).strip()
    if not sequence:
        return basic_item, False, "사업장 순번(seq)이 없습니다.", 0

    response, error_status, error_message, attempts = _get_with_retry(
        NPS_DETAIL_URL,
        params={
            "serviceKey": key,
            "seq": sequence,
            "dataType": "json",
        },
        timeout=timeout,
        retries=retries,
    )
    if response is None:
        return basic_item, False, f"{error_status}: {error_message}", attempts

    text = response.text or ""
    content_type = response.headers.get("content-type", "").lower()
    if not (
        "json" in content_type
        or text.lstrip().startswith(("{", "["))
    ):
        code, message = _xml_error(text)
        return (
            basic_item,
            False,
            message or code or f"HTTP {response.status_code}",
            attempts,
        )
    try:
        payload = response.json()
    except ValueError:
        return basic_item, False, "상세조회 JSON 해석 실패", attempts
    if not isinstance(payload, dict):
        return basic_item, False, "상세조회 응답구조 오류", attempts
    code, message, _total_count, detail_items = _items_from_json(payload)
    if not response.ok or code not in {"00", "0", ""}:
        return (
            basic_item,
            False,
            message or code or f"HTTP {response.status_code}",
            attempts,
        )
    if not detail_items:
        return basic_item, False, "상세정보가 비어 있습니다.", attempts

    merged = dict(basic_item)
    merged.update(detail_items[0])
    return merged, True, "", attempts


def _enrich_nps_details(
    items: list[dict[str, Any]],
    *,
    key: str,
    timeout: int,
    retries: int = 2,
    workers: int = 5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    if not items:
        return [], [], 0
    maximum_workers = min(5, max(1, int(workers)), len(items))
    successes: list[tuple[int, dict[str, Any]]] = []
    failures: list[tuple[int, dict[str, Any]]] = []
    api_attempts = 0

    with ThreadPoolExecutor(max_workers=maximum_workers) as executor:
        future_map = {
            executor.submit(
                _fetch_nps_detail,
                item,
                key=key,
                timeout=timeout,
                retries=retries,
            ): (index, item)
            for index, item in enumerate(items)
        }
        for future in as_completed(future_map):
            index, basic_item = future_map[future]
            try:
                merged, ok, message, attempts = future.result()
            except Exception as exc:
                merged = basic_item
                ok = False
                message = f"상세조회 처리오류: {type(exc).__name__}"
                attempts = 1
            api_attempts += max(1, attempts)
            if ok:
                successes.append((index, merged))
            else:
                failed = dict(merged)
                failed["_detail_error"] = message
                failures.append((index, failed))

    successes.sort(key=lambda row: row[0])
    failures.sort(key=lambda row: row[0])
    return (
        [item for _index, item in successes],
        [item for _index, item in failures],
        api_attempts,
    )


def fetch_nps_workplaces(
    region_code: str,
    *,
    page_no: int = 1,
    rows: int = 50,
    sigungu_code: str = "",
    emd_code: str = "",
    timeout: int = 30,
    retries: int = 2,
    detail_workers: int = 5,
    stock_company_only: bool = False,
    business_type: str = "all",
    exclude_source_keys: set[str] | None = None,
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

    response, error_status, error_message, basic_attempts = _get_with_retry(
        NPS_BASE_URL,
        params=params,
        timeout=timeout,
        retries=retries,
    )
    if response is None:
        return {
            "ok": False,
            "status": error_status,
            "message": error_message,
            "checked_at": checked_at,
            "api_attempt_count": basic_attempts,
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
        basic_received_count = len(items)
        excluded_keys = {
            str(value or "").strip()
            for value in (exclude_source_keys or set())
            if str(value or "").strip()
        }
        normalized_business_type = str(business_type or "all").strip().lower()
        if stock_company_only:
            normalized_business_type = "stock"
        if normalized_business_type == "stock":
            items = [
                item
                for item in items
                if is_stock_company_name(
                    _first(item, "wkplNm", "wkpl_nm", "사업장명")
                )
            ]
        elif normalized_business_type == "individual":
            items = [
                item
                for item in items
                if is_individual_business_candidate(
                    _first(item, "wkplNm", "wkpl_nm", "사업장명")
                )
            ]
        if excluded_keys:
            items = [
                item
                for item in items
                if str(_first(item, "seq", "자료순번")).strip()
                not in excluded_keys
            ]
        basic_pre_filtered_count = basic_received_count - len(items)
        success = response.ok and code in {"00", "0", ""}
        detail_items, detail_failures, detail_attempts = _enrich_nps_details(
            items,
            key=key,
            timeout=timeout,
            retries=retries,
            workers=detail_workers,
        )
        normalized = [
            normalize_nps_workplace(
                item,
                region_code,
                detail_status="SUCCESS",
            )
            for item in detail_items
        ]
        failed_normalized = [
            normalize_nps_workplace(
                item,
                region_code,
                detail_status="FAILED",
                detail_message=str(item.get("_detail_error", "")),
            )
            for item in detail_failures
        ]
        target_names = {
            name for name, code_value in REGION_CODES.items()
            if code_value == region_code
        }
        in_region = [
            item for item in normalized if item.get("지역") in target_names
        ]
        failed_in_region = [
            item
            for item in failed_normalized
            if item.get("지역") in target_names
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
            "basic_received_count": basic_received_count,
            "basic_detail_target_count": len(items),
            "basic_pre_filtered_count": basic_pre_filtered_count,
            "detail_success_count": len(normalized),
            "detail_failed_count": len(failed_normalized),
            "filtered_out_count": (
                len(normalized)
                + len(failed_normalized)
                - len(in_region)
                - len(failed_in_region)
            ),
            "api_attempt_count": basic_attempts + detail_attempts,
            "items": in_region,
            "detail_failures": failed_in_region,
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
    timeout: int = 30,
    retries: int = 2,
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
    response, error_status, error_message, attempts = _get_with_retry(
        NPS_BASE_URL,
        params=params,
        timeout=timeout,
        retries=retries,
    )
    if response is None:
        return {
            "ok": False,
            "status": error_status,
            "message": error_message,
            "checked_at": checked_at,
            "attempt_count": attempts,
        }

    common = {
        "http_status": response.status_code,
        "checked_at": checked_at,
        "region_code": region_code,
        "attempt_count": attempts,
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
