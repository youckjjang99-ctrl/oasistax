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
NPS_PERIOD_URL = (
    "https://apis.data.go.kr/B552015/"
    "NpsBplcInfoInqireServiceV2/getPdAcctoSttusInfoSearchV2"
)
REGION_CODES = {
    "서울특별시": "11",
    "부산광역시": "26",
    "대구광역시": "27",
    "인천광역시": "28",
    "광주광역시": "29",
    "대전광역시": "30",
    "울산광역시": "31",
    "세종특별자치시": "36",
    "경기도": "41",
    "강원특별자치도": "42",
    "충청북도": "43",
    "충청남도": "44",
    "전북특별자치도": "45",
    "전라남도": "46",
    "경상북도": "47",
    "경상남도": "48",
    "제주특별자치도": "50",
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
INDUSTRY_CATEGORY_KEYWORDS = {
    "병원·의원": (
        "병원",
        "의원",
        "치과",
        "한의원",
        "의료",
        "요양",
    ),
    "음식점": (
        "음식",
        "한식",
        "중식",
        "일식",
        "양식",
        "분식",
        "카페",
        "커피",
        "제과",
        "주점",
    ),
    "서비스업": (
        "서비스",
        "미용",
        "세탁",
        "수리",
        "교육",
        "학원",
        "스포츠",
        "여행",
        "광고",
        "컨설팅",
        "임대",
    ),
    "도소매업": ("도매", "소매", "판매", "유통", "전자상거래"),
    "제조업": ("제조", "가공", "생산"),
    "건설업": ("건설", "공사", "설비", "토목", "인테리어"),
}


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


def industry_category(value: Any) -> str:
    name = re.sub(r"\s+", "", str(value or ""))
    for category, keywords in INDUSTRY_CATEGORY_KEYWORDS.items():
        if any(keyword in name for keyword in keywords):
            return category
    return "기타"


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
                headers={"User-Agent": "OASIS-CRM/9.8.4"},
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


def _optional_integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    digits = re.sub(r"[^0-9-]", "", str(value))
    if not digits or digits == "-":
        return None
    try:
        return int(digits)
    except (TypeError, ValueError):
        return None


def _previous_year_month(value: Any) -> str:
    text = re.sub(r"[^0-9]", "", str(value or ""))
    if len(text) != 6:
        return ""
    try:
        year = int(text[:4]) - 1
        month = int(text[4:])
    except ValueError:
        return ""
    if year < 2000 or not 1 <= month <= 12:
        return ""
    return f"{year:04d}{month:02d}"


def _company_identity(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"(주식회사|㈜|（주）|\(주\))", "", text)
    return re.sub(r"[^0-9a-z가-힣]", "", text)


def _address_identity(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return re.sub(r"[^0-9a-z가-힣]", "", text)[:36]


def _business_no(value: Any) -> str:
    text = str(value or "").strip()
    digits = re.sub(r"[^0-9]", "", text)
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return text


def _region_from_address(address: str, region_code: str = "") -> str:
    compact = re.sub(r"\s+", " ", str(address or "")).strip()
    region_prefixes = {
        "서울특별시": ("서울", "서울특별시"),
        "부산광역시": ("부산", "부산광역시"),
        "대구광역시": ("대구", "대구광역시"),
        "인천광역시": ("인천", "인천광역시"),
        "광주광역시": ("광주", "광주광역시"),
        "대전광역시": ("대전", "대전광역시"),
        "울산광역시": ("울산", "울산광역시"),
        "세종특별자치시": ("세종", "세종특별자치시"),
        "경기도": ("경기", "경기도"),
        "강원특별자치도": ("강원", "강원도", "강원특별자치도"),
        "충청북도": ("충북", "충청북도"),
        "충청남도": ("충남", "충청남도"),
        "전북특별자치도": ("전북", "전라북도", "전북특별자치도"),
        "전라남도": ("전남", "전라남도"),
        "경상북도": ("경북", "경상북도"),
        "경상남도": ("경남", "경상남도"),
        "제주특별자치도": ("제주", "제주도", "제주특별자치도"),
    }
    for region_name, prefixes in region_prefixes.items():
        if compact.startswith(prefixes):
            return region_name
    if compact:
        return ""
    return next(
        (
            region_name
            for region_name, code in REGION_CODES.items()
            if code == region_code
        ),
        "",
    )


def _priority(
    employee_count: int,
    new_count: int | None,
    lost_count: int | None,
) -> tuple[int, list[str]]:
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
    if new_count is not None and new_count > 0:
        score += min(25, 10 + new_count * 3)
        reasons.append(f"최근 신규취득자 {new_count}명")
    if lost_count is not None and lost_count > 0:
        score += min(10, lost_count * 2)
        reasons.append(f"최근 상실가입자 {lost_count}명")
    if employee_count >= 10 and new_count is not None and new_count >= 2:
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
    new_count = _optional_integer(
        _first(item, "nwAcqzrCnt", "nw_acqzr_cnt", "신규취득자수")
    )
    lost_count = _optional_integer(
        _first(item, "lssJnngpCnt", "lss_jnngp_cnt", "상실가입자수")
    )
    score, reasons = _priority(employee_count, new_count, lost_count)
    industry_name = str(
        _first(item, "vldtVlKrnNm", "vldt_vl_krn_nm", "업종명")
    ).strip()
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
        "업종명": industry_name,
        "업종분류": industry_category(industry_name),
        "가입자수": employee_count,
        "신규취득자수": new_count,
        "상실가입자수": lost_count,
        "순고용증가": (
            new_count - lost_count
            if new_count is not None and lost_count is not None
            else None
        ),
        "전년가입자수": None,
        "전년대비고용증가": None,
        "고용자료상태": "NOT_CHECKED",
        "고용증가판정": "고용자료 미조회",
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


def fetch_nps_period_status(
    sequence: Any,
    *,
    data_created_ym: str = "",
    timeout: int = 15,
    retries: int = 1,
) -> dict[str, Any]:
    """기간별 API에서 최근 월 신규취득·상실 인원을 확인한다."""
    key = _service_key()
    sequence = str(sequence or "").strip()
    if not key:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"{SERVICE_KEY_ENV}가 없습니다.",
            "api_attempt_count": 0,
        }
    if not sequence:
        return {
            "ok": False,
            "status": "SEQ_MISSING",
            "message": "사업장 순번(seq)이 없습니다.",
            "api_attempt_count": 0,
        }
    params: dict[str, Any] = {
        "serviceKey": key,
        "seq": sequence,
        "pageNo": 1,
        "numOfRows": 10,
        "dataType": "json",
    }
    if str(data_created_ym or "").strip():
        params["dataCrtYm"] = str(data_created_ym).strip()
    response, error_status, error_message, attempts = _get_with_retry(
        NPS_PERIOD_URL,
        params=params,
        timeout=timeout,
        retries=retries,
    )
    if response is None:
        return {
            "ok": False,
            "status": error_status,
            "message": error_message,
            "api_attempt_count": attempts,
        }
    text = response.text or ""
    content_type = response.headers.get("content-type", "").lower()
    if not (
        "json" in content_type
        or text.lstrip().startswith(("{", "["))
    ):
        code, message = _xml_error(text)
        return {
            "ok": False,
            "status": code or f"HTTP_{response.status_code}",
            "message": message or "기간별 조회 응답을 해석하지 못했습니다.",
            "api_attempt_count": attempts,
        }
    try:
        payload = response.json()
    except ValueError:
        return {
            "ok": False,
            "status": "INVALID_JSON",
            "message": "기간별 조회 JSON 해석에 실패했습니다.",
            "api_attempt_count": attempts,
        }
    code, message, _total_count, items = _items_from_json(payload)
    if not response.ok or code not in {"00", "0", ""}:
        return {
            "ok": False,
            "status": code or f"HTTP_{response.status_code}",
            "message": message or "기간별 조회에 실패했습니다.",
            "api_attempt_count": attempts,
        }
    if not items:
        return {
            "ok": False,
            "status": "NO_PERIOD_DATA",
            "message": "최근 월 신규취득·상실 자료가 없습니다.",
            "api_attempt_count": attempts,
        }
    target_ym = str(data_created_ym or "").strip()
    selected = next(
        (
            row
            for row in items
            if not target_ym
            or str(_first(row, "dataCrtYm", "data_crt_ym")).strip()
            == target_ym
        ),
        items[0],
    )
    new_count = _optional_integer(
        _first(selected, "nwAcqzrCnt", "nw_acqzr_cnt")
    )
    lost_count = _optional_integer(
        _first(selected, "lssJnngpCnt", "lss_jnngp_cnt")
    )
    if new_count is None or lost_count is None:
        return {
            "ok": False,
            "status": "INCOMPLETE_PERIOD_DATA",
            "message": "신규취득자수 또는 상실가입자수가 비어 있습니다.",
            "api_attempt_count": attempts,
        }
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": "최근 월 순취득 자료를 확인했습니다.",
        "data_created_ym": str(
            _first(selected, "dataCrtYm", "data_crt_ym")
        ).strip(),
        "new_count": new_count,
        "lost_count": lost_count,
        "net_growth": new_count - lost_count,
        "api_attempt_count": attempts,
    }


def _prior_workplace_match(
    current: dict[str, Any],
    rows: list[dict[str, Any]],
    target_ym: str,
) -> dict[str, Any] | None:
    current_name = _company_identity(current.get("사업장명"))
    current_address = _address_identity(current.get("주소"))
    current_business_digits = re.sub(
        r"[^0-9]",
        "",
        str(current.get("사업자등록번호") or ""),
    )
    candidates = []
    for row in rows:
        row_ym = str(_first(row, "dataCrtYm", "data_crt_ym")).strip()
        if row_ym and row_ym != target_ym:
            continue
        if _company_identity(_first(row, "wkplNm", "wkpl_nm")) != current_name:
            continue
        row_business_digits = re.sub(
            r"[^0-9]",
            "",
            str(_first(row, "bzowrRgstNo", "bzowr_rgst_no")),
        )
        if (
            current_business_digits
            and row_business_digits
            and current_business_digits[:6] != row_business_digits[:6]
        ):
            continue
        row_address = _address_identity(
            _first(
                row,
                "wkplRoadNmDtlAddr",
                "wkpl_road_nm_dtl_addr",
                "wkplAddr",
                "wkpl_addr",
            )
        )
        address_score = int(
            bool(
                current_address
                and row_address
                and (
                    current_address.startswith(row_address[:18])
                    or row_address.startswith(current_address[:18])
                )
            )
        )
        candidates.append((address_score, row))
    if not candidates:
        return None
    candidates.sort(key=lambda value: value[0], reverse=True)
    return candidates[0][1]


def fetch_nps_year_over_year(
    workplace: dict[str, Any],
    *,
    timeout: int = 15,
    retries: int = 1,
) -> dict[str, Any]:
    """동일 사업장의 전년 동월 가입자수와 현재 가입자수를 비교한다."""
    key = _service_key()
    if not key:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"{SERVICE_KEY_ENV}가 없습니다.",
            "api_attempt_count": 0,
        }
    current_ym = str(workplace.get("자료생성년월") or "").strip()
    target_ym = _previous_year_month(current_ym)
    current_count = _optional_integer(workplace.get("가입자수"))
    if not target_ym or current_count is None:
        return {
            "ok": False,
            "status": "CURRENT_DATA_MISSING",
            "message": "현재 기준월 또는 가입자수가 없습니다.",
            "api_attempt_count": 0,
        }
    company_name = str(workplace.get("사업장명") or "").strip()
    params: dict[str, Any] = {
        "serviceKey": key,
        "pageNo": 1,
        "numOfRows": 100,
        "dataType": "json",
        "wkplNm": company_name,
        "dataCrtYm": target_ym,
        "ldongAddrMgplDgCd": str(workplace.get("지역코드") or ""),
    }
    business_digits = re.sub(
        r"[^0-9]",
        "",
        str(workplace.get("사업자등록번호") or ""),
    )
    if len(business_digits) >= 6:
        params["bzowrRgstNo"] = business_digits[:6]
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
            "api_attempt_count": basic_attempts,
        }
    text = response.text or ""
    content_type = response.headers.get("content-type", "").lower()
    if not (
        "json" in content_type
        or text.lstrip().startswith(("{", "["))
    ):
        code, message = _xml_error(text)
        return {
            "ok": False,
            "status": code or f"HTTP_{response.status_code}",
            "message": message or "전년 동월 조회 응답을 해석하지 못했습니다.",
            "api_attempt_count": basic_attempts,
        }
    try:
        payload = response.json()
    except ValueError:
        return {
            "ok": False,
            "status": "INVALID_JSON",
            "message": "전년 동월 조회 JSON 해석에 실패했습니다.",
            "api_attempt_count": basic_attempts,
        }
    code, message, _total_count, rows = _items_from_json(payload)
    if not response.ok or code not in {"00", "0", ""}:
        return {
            "ok": False,
            "status": code or f"HTTP_{response.status_code}",
            "message": message or "전년 동월 기본조회에 실패했습니다.",
            "api_attempt_count": basic_attempts,
        }
    prior_basic = _prior_workplace_match(workplace, rows, target_ym)
    if prior_basic is None:
        return {
            "ok": False,
            "status": "PRIOR_WORKPLACE_NOT_FOUND",
            "message": "동일 사업장의 전년 동월 자료를 찾지 못했습니다.",
            "api_attempt_count": basic_attempts,
            "previous_data_created_ym": target_ym,
        }
    prior_detail, detail_ok, detail_message, detail_attempts = (
        _fetch_nps_detail(
            prior_basic,
            key=key,
            timeout=timeout,
            retries=retries,
        )
    )
    previous_count = _optional_integer(
        _first(prior_detail, "jnngpCnt", "jnngp_cnt", "가입자수")
    )
    if not detail_ok or previous_count is None:
        return {
            "ok": False,
            "status": "PRIOR_DETAIL_UNAVAILABLE",
            "message": detail_message or "전년 동월 가입자수가 없습니다.",
            "api_attempt_count": basic_attempts + detail_attempts,
            "previous_data_created_ym": target_ym,
        }
    return {
        "ok": True,
        "status": "SUCCESS",
        "message": "전년 동월 가입자수 비교를 완료했습니다.",
        "current_data_created_ym": current_ym,
        "previous_data_created_ym": target_ym,
        "current_employee_count": current_count,
        "previous_employee_count": previous_count,
        "year_over_year_growth": current_count - previous_count,
        "api_attempt_count": basic_attempts + detail_attempts,
    }


def enrich_employment_growth(
    workplaces: list[dict[str, Any]],
    *,
    basis: str = "combined",
    timeout: int = 15,
    retries: int = 1,
    workers: int = 8,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """최근 월 순취득과 보유한 전년 자료를 하나의 신호로 판정한다."""
    normalized_basis = str(basis or "combined").strip().lower()
    if normalized_basis not in {"combined", "none"}:
        normalized_basis = "combined"
    rows = [dict(item) for item in workplaces]
    stats = {
        "employment_checked": 0,
        "employment_unavailable": 0,
        "employment_failed": 0,
        "employment_api_attempts": 0,
    }
    if normalized_basis == "none":
        for row in rows:
            row["고용증가기준"] = "none"
            row["선택고용증가"] = None
            row["고용자료상태"] = "NOT_CHECKED"
            row["고용증가판정"] = "고용 증가 필터 미사용"
        return rows, stats

    def _lookup(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        raw = (
            row.get("원본데이터")
            if isinstance(row.get("원본데이터"), dict)
            else {}
        )
        sequence = _first(raw, "seq", "자료순번") or row.get("source_key")
        result = fetch_nps_period_status(
            sequence,
            data_created_ym=str(row.get("자료생성년월") or ""),
            timeout=timeout,
            retries=retries,
        )
        return row, result

    completed: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    maximum_workers = min(max(1, int(workers)), max(1, len(rows)))
    with ThreadPoolExecutor(max_workers=maximum_workers) as executor:
        future_map = {
            executor.submit(_lookup, row): index
            for index, row in enumerate(rows)
        }
        for future in as_completed(future_map):
            index = future_map[future]
            row = rows[index]
            try:
                _original, result = future.result()
            except Exception as exc:
                result = {
                    "ok": False,
                    "status": "LOOKUP_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                    "api_attempt_count": 0,
                }
            completed.append((index, row, result))

    for index, row, result in completed:
        stats["employment_api_attempts"] += int(
            result.get("api_attempt_count") or 0
        )
        row["고용증가기준"] = "combined"
        row["고용자료메시지"] = str(result.get("message") or "")
        if result.get("ok"):
            stats["employment_checked"] += 1
            row["고용자료상태"] = "CONFIRMED"
            row["신규취득자수"] = result["new_count"]
            row["상실가입자수"] = result["lost_count"]
            row["순고용증가"] = result["net_growth"]
            year_growth = _optional_integer(row.get("전년대비고용증가"))
            recent_growth = result["net_growth"]
            row["선택고용증가"] = max(
                recent_growth,
                year_growth if year_growth is not None else -1000000,
            )
            reasons: list[str] = []
            if year_growth is not None:
                reasons.append(
                    "전년 동월 가입자 "
                    + (f"{year_growth:+d}명" if year_growth else "변동 없음")
                )
            else:
                reasons.append("전년 동월 가입자 자료 축적 전")
            reasons.append(f"최근 월 순취득 {recent_growth:+d}명")
            row["고용증가판정"] = " · ".join(reasons)
            row["고용증가신호"] = (
                recent_growth > 0
                or (year_growth is not None and year_growth > 0)
            )
            if year_growth is None:
                row["전년동월상태"] = "스냅샷 축적 전"
            else:
                row["전년동월상태"] = "확인됨"
        else:
            status = str(result.get("status") or "UNAVAILABLE")
            row["고용자료상태"] = status
            row["선택고용증가"] = None
            row["고용증가신호"] = False
            row["전년동월상태"] = "스냅샷 축적 전"
            row["고용증가판정"] = "최근 월 고용자료 확인 불가"
            if status in {
                "NO_PERIOD_DATA",
                "INCOMPLETE_PERIOD_DATA",
            }:
                stats["employment_unavailable"] += 1
            else:
                stats["employment_failed"] += 1
        rows[index] = row
    return rows, stats


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
            "message": "지원하지 않는 시·도 지역코드입니다.",
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
