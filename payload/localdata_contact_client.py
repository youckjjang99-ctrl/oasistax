from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from urllib.parse import unquote

import requests

from contact_matching import (
    contact_match_score,
    is_mobile_phone,
    normalize_phone,
    search_company_name,
)


SERVICE_KEY_ENV = "DATA_GO_KR_SERVICE_KEY"
DATA_GO_BASE = "https://apis.data.go.kr/1741000"

SERVICES: dict[str, dict[str, str]] = {
    "printing_shops": {
        "label": "인쇄사",
        "url": f"{DATA_GO_BASE}/printing_shops/info",
        "catalog": "https://www.data.go.kr/data/15155014/openapi.do",
        "keywords": "인쇄 출판 출력",
    },
    "international_logistics_forwarders": {
        "label": "국제물류주선업",
        "url": f"{DATA_GO_BASE}/international_logistics_forwarders/info",
        "catalog": "https://www.data.go.kr/data/15155056/openapi.do",
        "keywords": "국제물류 물류주선 수출입 화물운송",
    },
    "logistics_warehouses": {
        "label": "물류창고업체",
        "url": f"{DATA_GO_BASE}/logistics_warehouses/info",
        "catalog": "https://www.data.go.kr/data/15155066/openapi.do",
        "keywords": "물류창고 창고 보관 물류",
    },
    "medical_device_sales_rental": {
        "label": "의료기기판매(임대)업",
        "url": f"{DATA_GO_BASE}/medical_device_sales_rental/info",
        "catalog": "https://www.data.go.kr/data/15154923/openapi.do",
        "keywords": "의료기기 보건 의료 임대",
    },
    "ecommerce_businesses": {
        "label": "통신판매업",
        "url": f"{DATA_GO_BASE}/ecommerce_businesses/info",
        "catalog": "https://www.data.go.kr/data/15154963/openapi.do",
        "keywords": "통신판매 전자상거래 온라인 쇼핑몰 인터넷판매",
    },
    "food_manufacturing_processors": {
        "label": "식품제조가공업",
        "url": f"{DATA_GO_BASE}/food_manufacturing_processors/info",
        "catalog": "https://www.data.go.kr/data/15155150/openapi.do",
        "keywords": "식품 제조 가공 도시락 음료 제과",
    },
}


def _service_key() -> str:
    return unquote(os.environ.get(SERVICE_KEY_ENV, "").strip())


def key_status() -> dict[str, Any]:
    key = _service_key()
    return {
        "configured": bool(key),
        "env_name": SERVICE_KEY_ENV,
        "masked": f"{key[:4]}{'*' * 12}" if key else "미등록",
        "service_count": len(SERVICES),
    }


def _first(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if item.get(key) not in (None, ""):
            return item.get(key)
    return ""


def _safe_int(value: Any) -> int:
    try:
        return int(str(value or "0").replace(",", "").strip() or "0")
    except (TypeError, ValueError):
        return 0


def _items(payload: Any) -> tuple[str, str, int, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return "", "", 0, []
    response = payload.get("response", payload)
    if not isinstance(response, dict):
        return "", "", 0, []
    header = response.get("header", {})
    body = response.get("body", {})
    if not isinstance(header, dict):
        header = {}
    if not isinstance(body, dict):
        body = {}
    code = str(
        _first(header, "resultCode", "RESULT_CODE", "result_code")
    ).strip()
    message = str(
        _first(header, "resultMsg", "RESULT_MSG", "result_msg")
    ).strip()
    total = _safe_int(body.get("totalCount") or body.get("total_count"))
    block = body.get("items", [])
    if isinstance(block, dict):
        block = block.get("item", block.get("items", []))
    if isinstance(block, dict):
        block = [block]
    if not isinstance(block, list):
        block = []
    return code, message, total, [row for row in block if isinstance(row, dict)]


def _active(item: dict[str, Any]) -> bool:
    code = str(
        _first(item, "SALS_STTS_CD", "salsSttsCd", "sals_stts_cd")
    ).strip()
    name = str(
        _first(item, "SALS_STTS_NM", "salsSttsNm", "sals_stts_nm")
    ).strip()
    if code in {"02", "03", "04"}:
        return False
    if any(word in name for word in ("폐업", "취소", "말소", "정지")):
        return False
    return True


def _ordered_services(industry_name: str, company_name: str) -> list[str]:
    haystack = f"{industry_name} {company_name}".lower()
    preferred = [
        key
        for key, config in SERVICES.items()
        if any(word in haystack for word in config["keywords"].split())
    ]
    return preferred + [key for key in SERVICES if key not in preferred]


def _search_service(
    service_key: str,
    company_name: str,
    address: str,
    *,
    timeout: int,
) -> dict[str, Any]:
    config = SERVICES[service_key]
    params = {
        "serviceKey": _service_key(),
        "pageNo": 1,
        "numOfRows": 30,
        "returnType": "json",
        "cond[BPLC_NM::LIKE]": search_company_name(company_name),
    }
    try:
        response = requests.get(config["url"], params=params, timeout=timeout)
    except requests.Timeout:
        return {
            "service": service_key,
            "label": config["label"],
            "ok": False,
            "status": "TIMEOUT",
            "message": "응답시간 초과",
            "candidates": [],
        }
    except requests.RequestException as exc:
        return {
            "service": service_key,
            "label": config["label"],
            "ok": False,
            "status": "NETWORK_ERROR",
            "message": type(exc).__name__,
            "candidates": [],
        }
    if not response.ok:
        return {
            "service": service_key,
            "label": config["label"],
            "ok": False,
            "status": f"HTTP_{response.status_code}",
            "message": response.text[:160],
            "candidates": [],
        }
    try:
        payload = response.json()
    except ValueError:
        return {
            "service": service_key,
            "label": config["label"],
            "ok": False,
            "status": "INVALID_JSON",
            "message": "JSON 해석 실패",
            "candidates": [],
        }
    code, message, total, rows = _items(payload)
    ok = code in {"", "00", "0"}
    candidates: list[dict[str, Any]] = []
    for item in rows:
        candidate_name = str(
            _first(item, "BPLC_NM", "bplcNm", "bplc_nm", "사업장명")
        ).strip()
        candidate_address = str(
            _first(
                item,
                "ROAD_NM_ADDR",
                "roadNmAddr",
                "road_nm_addr",
                "SITE_WHL_ADDR",
                "siteWhlAddr",
                "LOTNO_ADDR",
                "lotnoAddr",
                "소재지전체주소",
            )
        ).strip()
        phone = normalize_phone(
            _first(
                item,
                "SITE_TEL",
                "siteTel",
                "site_tel",
                "TEL_NO",
                "telNo",
                "전화번호",
            )
        )
        active = _active(item)
        score = contact_match_score(
            company_name,
            address,
            candidate_name,
            candidate_address,
            has_phone=bool(phone),
            active=active,
        )
        candidates.append(
            {
                "company_name": candidate_name,
                "address": candidate_address,
                "phone": phone,
                "phone_type": (
                    "mobile_unverified"
                    if is_mobile_phone(phone)
                    else "company_main"
                ),
                "active": active,
                "license_date": str(
                    _first(item, "LCPMT_YMD", "lcpmtYmd", "license_date")
                ),
                "close_date": str(
                    _first(item, "CLSBIZ_YMD", "clsbizYmd", "close_date")
                ),
                "source_type": f"localdata:{service_key}",
                "source_url": config["catalog"],
                "confidence": score,
                "raw": item,
            }
        )
    candidates.sort(key=lambda row: row["confidence"], reverse=True)
    return {
        "service": service_key,
        "label": config["label"],
        "ok": ok,
        "status": "SUCCESS" if ok else (code or "API_ERROR"),
        "message": message or f"{total}건 중 {len(candidates)}건 수신",
        "total_count": total,
        "candidates": candidates,
    }


def search_company(
    company_name: str,
    address: str,
    industry_name: str = "",
    *,
    timeout: int = 5,
    max_services: int | None = None,
) -> dict[str, Any]:
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"{SERVICE_KEY_ENV}가 없습니다.",
            "candidates": [],
            "services": [],
        }
    service_results: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    ordered_services = _ordered_services(industry_name, company_name)
    if max_services is not None:
        ordered_services = ordered_services[
            : max(1, min(len(ordered_services), int(max_services)))
        ]
    for service_key in ordered_services:
        result = _search_service(
            service_key,
            company_name,
            address,
            timeout=timeout,
        )
        service_results.append(result)
        candidates.extend(result.get("candidates", []))
        if any(
            row.get("phone")
            and row.get("active")
            and int(row.get("confidence") or 0) >= 85
            for row in result.get("candidates", [])
        ):
            break
    candidates.sort(key=lambda row: row["confidence"], reverse=True)
    return {
        "ok": any(row.get("ok") for row in service_results),
        "status": "SUCCESS",
        "message": f"인허가 API {len(service_results)}개 조회",
        "candidates": candidates,
        "services": service_results,
    }


def test_services(timeout: int = 10) -> dict[str, Any]:
    checked_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not key_status()["configured"]:
        return {
            "ok": False,
            "status": "KEY_MISSING",
            "message": f"{SERVICE_KEY_ENV}가 없습니다.",
            "checked_at": checked_at,
            "services": [],
        }

    def check(service_key: str) -> dict[str, Any]:
        return _search_service(
            service_key,
            f"OASIS연결점검{datetime.now():%H%M%S}",
            "",
            timeout=timeout,
        )

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {
            executor.submit(check, service_key): service_key
            for service_key in SERVICES
        }
        for future in as_completed(future_map):
            try:
                results.append(future.result())
            except Exception as exc:
                key = future_map[future]
                results.append(
                    {
                        "service": key,
                        "label": SERVICES[key]["label"],
                        "ok": False,
                        "status": "TEST_ERROR",
                        "message": type(exc).__name__,
                    }
                )
    order = {key: index for index, key in enumerate(SERVICES)}
    results.sort(key=lambda row: order.get(row.get("service", ""), 999))
    ok_count = sum(1 for row in results if row.get("ok"))
    return {
        "ok": ok_count == len(SERVICES),
        "status": "CONNECTED" if ok_count == len(SERVICES) else "PARTIAL",
        "message": f"승인 API {ok_count}/{len(SERVICES)}개 연결",
        "checked_at": checked_at,
        "services": results,
    }
