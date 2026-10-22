from __future__ import annotations

import json
import math
import os
import re
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from runtime_error_log import safe_public_error, write_runtime_error
from upload_progress import UploadProgress, render_upload_feedback, render_upload_ready

from cloud_sync import (
    sync_financial_snapshot,
    sync_registry_snapshot,
    sync_stock_valuation,
    load_financial_snapshot,
    load_registry_snapshot,
    load_stock_valuations,
)
from registered_policy_match import load_registered_customers
from utils import get_user_cumulative_db_path, get_user_dirs


LAW_BASE_DATE = "2026-07-13"
CAPITALIZATION_RATE_DEFAULT = 0.10
NAV_FLOOR_RATE_DEFAULT = 0.80
MAX_SHAREHOLDER_PREMIUM_DEFAULT = 0.20
GOODWILL_DISCOUNT_FACTOR = 3.7908  # Source workbook: 영업권!B25 (5 years, 10%).
GOODWILL_LAW_BASE_DATE = "2026-10-16"
GOODWILL_MODE_LABELS = {
    "auto": "자동계산(엑셀 기준)",
    "manual": "수동입력(기존 방식)",
    "excluded": "불산입(검토 후 적용)",
}
GOODWILL_STATE_KEYS = (
    "stock_goodwill_mode",
    "stock_goodwill_acquired_intangible_remaining",
    "stock_goodwill_exclusion_reason",
)


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text.lower() in {"", "none", "nan", "nat", "-"}:
        return None
    try:
        number = float(text)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _ensure_text_state(key: str, default: Any = "") -> None:
    """Streamlit text_input state must always be str or None."""
    if key not in st.session_state:
        st.session_state[key] = "" if default is None else str(default)
        return

    value = st.session_state.get(key)
    if value is None:
        st.session_state[key] = ""
    elif not isinstance(value, str):
        st.session_state[key] = str(value)


def _format_number(value: Any, decimals: int = 0) -> str:
    number = _safe_number(value)
    if number is None:
        return ""
    if decimals == 0:
        return f"{int(round(number)):,}"
    return f"{number:,.{decimals}f}"


def _normalize_business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
    return str(value or "").strip()


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None and str(value).strip().lower() not in {
            "", "none", "nan", "nat", "-"
        }:
            return value
    return None


def _normalize_financial_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    # 크레탑 추출기의 컬럼명 차이를 주가평가 표준키로 정규화합니다.
    source = dict(data or {})
    source["자산총계"] = _first_value(
        source, "자산총계", "총자산", "자산합계", "장부상총자산"
    )
    source["부채총계"] = _first_value(
        source, "부채총계", "총부채", "부채합계", "장부상총부채"
    )
    source["당기순이익"] = _first_value(
        source, "당기순이익", "순이익", "법인세차감후순이익", "당기순손익"
    )
    history = source.get("재무연도별", [])
    if not isinstance(history, list):
        history = []
    normalized_history = []
    for row in history:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item["당기순이익"] = _first_value(
            item, "당기순이익", "순이익", "법인세차감후순이익", "당기순손익"
        )
        item["자산총계"] = _first_value(item, "자산총계", "총자산", "자산합계")
        item["부채총계"] = _first_value(item, "부채총계", "총부채", "부채합계")
        normalized_history.append(item)
    source["재무연도별"] = normalized_history
    return source


def _financial_cache_path(user_id: str) -> Path:
    dirs = get_user_dirs(user_id)
    return dirs["base"] / "stock_financial_cache.json"


def _load_financial_cache(user_id: str) -> dict[str, Any]:
    path = _financial_cache_path(user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_financial_cache(user_id: str, data: dict[str, Any]) -> None:
    path = _financial_cache_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def save_cretop_financial_snapshot(
    user_id: str,
    extracted_data: dict[str, Any],
) -> bool:
    """
    Cretop analysis result is saved separately for stock valuation.
    Missing identity fields preserve the previous identity. Financial amounts
    and annual rows retain their existing replacement semantics; no year merge
    is inferred here. Existing customer DB is never modified.
    """
    from copy import deepcopy

    def known(value):
        if value is None:
            return False
        if isinstance(value, (dict, list)):
            return bool(value)
        return str(value).strip().lower() not in {"", "none", "nan", "nat", "<na>", "-", "미확인"}

    data = _normalize_financial_snapshot(extracted_data or {})
    business_no = _normalize_business_no(
        data.get("사업자등록번호", data.get("사업자번호", ""))
    )
    if len(re.sub(r"[^0-9]", "", business_no)) != 10:
        return False

    cache = _load_financial_cache(user_id)
    existing = cache.get(business_no, {})
    if not isinstance(existing, dict):
        return False
    incoming = {
        "업체명": data.get("업체명", ""),
        "대표자명": data.get("대표자명", ""),
        "사업자등록번호": business_no,
        "법인등록번호": data.get("법인등록번호", ""),
        "사업장 소재지": data.get("사업장 소재지", ""),
        "설립일": data.get("설립일", ""),
        "자산총계": data.get("자산총계"),
        "부채총계": data.get("부채총계"),
        "자본총계": data.get("자본총계"),
        "매출액": data.get("매출액"),
        "영업이익": data.get("영업이익"),
        "당기순이익": data.get("당기순이익"),
        "법인세비용": data.get("법인세비용", data.get("법인세")),
        "유형자산": data.get("유형자산", data.get("유형자산합계")),
        "토지": data.get("토지"),
        "건물": data.get("건물"),
        "기계장치": data.get("기계장치"),
        "차량운반구": data.get("차량운반구"),
        "감가상각비": data.get("감가상각비"),
        "연구개발비": data.get("연구개발비", data.get("경상연구개발비")),
        "이익잉여금": data.get("이익잉여금", data.get("미처분이익잉여금")),
        "가지급금": data.get("가지급금"),
        "단기대여금": data.get("단기대여금"),
        "장기대여금": data.get("장기대여금"),
        "가수금": data.get("가수금"),
        "종업원수": data.get("종업원수", data.get("상시근로자수")),
        "재무연도별": data.get("재무연도별", []),
        "PDF추출일시": data.get("PDF추출일시", ""),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    for field in ("업체명", "대표자명", "법인등록번호", "사업장 소재지", "설립일"):
        if not known(incoming[field]):
            incoming[field] = deepcopy(existing.get(field)) if known(existing.get(field)) else ""
    cache[business_no] = incoming
    _save_financial_cache(user_id, cache)
    sync_financial_snapshot(user_id, business_no, cache[business_no])
    return True


def _apply_customer_financial_data(
    user_id: str,
    row: pd.Series,
) -> tuple[bool, str]:
    """
    Load stock valuation inputs from existing customer DB and the separate
    Cretop financial cache.
    """
    defaults = _customer_defaults(row)
    business_no = _normalize_business_no(defaults.get("사업자등록번호", ""))

    st.session_state["stock_company_name"] = str(defaults.get("법인명", "") or "")
    st.session_state["stock_business_no"] = business_no
    st.session_state["stock_corporate_no"] = str(
        defaults.get("법인등록번호", "") or ""
    )
    st.session_state["stock_address"] = str(defaults.get("본점소재지", "") or "")
    st.session_state["stock_establishment"] = str(
        defaults.get("설립일", "") or ""
    )
    st.session_state["stock_total_assets"] = _format_number(
        defaults.get("총자산")
    )
    st.session_state["stock_total_liabilities"] = _format_number(
        defaults.get("총부채")
    )

    cache = _load_financial_cache(user_id)
    # Supabase를 먼저 확인해, 과거에 저장된 빈/오래된 로컬 캐시가 최신
    # 크레탑 재무자료를 가리지 않도록 합니다. 클라우드가 일시적으로
    # unavailable하면 로컬 캐시로 안전하게 fallback합니다.
    snapshot = load_financial_snapshot(user_id, business_no) if business_no else {}
    if snapshot:
        snapshot = _normalize_financial_snapshot(snapshot)
        cache[business_no] = snapshot
        _save_financial_cache(user_id, cache)
    else:
        snapshot = cache.get(business_no, {}) if business_no else {}
        if snapshot:
            snapshot = _normalize_financial_snapshot(snapshot)

    if snapshot:
        st.session_state["stock_company_name"] = str(
            snapshot.get("업체명") or st.session_state["stock_company_name"]
        )
        st.session_state["stock_corporate_no"] = str(
            snapshot.get("법인등록번호")
            or st.session_state["stock_corporate_no"]
        )
        st.session_state["stock_address"] = str(
            snapshot.get("사업장 소재지")
            or st.session_state["stock_address"]
        )
        st.session_state["stock_establishment"] = str(
            snapshot.get("설립일")
            or st.session_state["stock_establishment"]
        )
        st.session_state["stock_total_assets"] = _format_number(
            snapshot.get("자산총계")
        ) or st.session_state["stock_total_assets"]
        st.session_state["stock_total_liabilities"] = _format_number(
            snapshot.get("부채총계")
        ) or st.session_state["stock_total_liabilities"]

        history = snapshot.get("재무연도별", [])
        def history_year(item: dict[str, Any]) -> float:
            year = _safe_number(item.get("연도"))
            return year if year is not None else float("-inf")

        # 크레탑 보고서에는 최신 기준연도(예: 2026년)의 재무상태표는
        # 있지만 손익계산서가 비어 있는 행이 포함될 수 있습니다. 그 행을
        # 최근 3개년으로 잘못 선택하면 순손익 입력란이 공란으로 보이므로,
        # 실제 당기순이익이 있는 연도만 주가평가 입력 대상으로 사용합니다.
        history = sorted(
            [
                item
                for item in history
                if isinstance(item, dict)
                and _safe_number(item.get("당기순이익")) is not None
            ],
            key=history_year,
            reverse=True,
        )[:3]
        if not history and snapshot.get("당기순이익") not in (None, ""):
            history = [{
                "연도": datetime.now().year - 1,
                "당기순이익": snapshot.get("당기순이익"),
            }]

        for index in range(1, 4):
            st.session_state[f"stock_year_{index}"] = ""
            st.session_state[f"stock_net_income_{index}"] = ""
            st.session_state[f"stock_shares_{index}"] = ""

        for index, item in enumerate(history, start=1):
            st.session_state[f"stock_year_{index}"] = str(
                item.get("연도", "") or ""
            )
            st.session_state[f"stock_net_income_{index}"] = _format_number(
                item.get("당기순이익")
            )

        return True, "기존 고객DB와 크레탑 재무 캐시에서 정보를 불러왔습니다."

    # Legacy customer: use only latest flat financial values.
    latest_net_income = defaults.get("최근당기순이익")
    if latest_net_income not in (None, ""):
        st.session_state["stock_year_1"] = str(datetime.now().year - 1)
        st.session_state["stock_net_income_1"] = _format_number(
            latest_net_income
        )

    return (
        True,
        "기존 고객DB에서 기본 재무정보를 불러왔습니다. "
        "과거 3개년 정보가 없으면 크레탑 PDF를 한 번 분석해주세요.",
    )


def _storage_path(user_id: str) -> Path:
    dirs = get_user_dirs(user_id)
    return dirs["base"] / "stock_valuations.json"


@st.cache_data(ttl=60, show_spinner=False)
def _read_json_records_cached(path_text: str, modified_time: float) -> list[dict[str, Any]]:
    del modified_time
    path = Path(path_text)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


@st.cache_data(ttl=45, show_spinner=False)
def _load_cloud_records_cached(user_id: str) -> list[dict[str, Any]]:
    return load_stock_valuations(user_id)


def _merge_records(local_records: list[dict[str, Any]], cloud_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in [*cloud_records, *local_records]:
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("record_id", "") or "").strip()
        if not record_id:
            record_id = "|".join([str(record.get("business_no", "") or ""), str(record.get("valuation_date", "") or ""), str(record.get("saved_at", "") or "")])
        if not record_id:
            continue
        existing = merged.get(record_id)
        if existing is None or str(record.get("saved_at", "") or "") >= str(existing.get("saved_at", "") or ""):
            merged[record_id] = dict(record)
    return sorted(merged.values(), key=lambda item: str(item.get("saved_at", "") or ""), reverse=True)


def _load_records(user_id: str) -> list[dict[str, Any]]:
    path = _storage_path(user_id)
    local_records: list[dict[str, Any]] = []
    if path.exists():
        try:
            local_records = _read_json_records_cached(str(path), path.stat().st_mtime)
        except OSError:
            local_records = []
    cloud_records = _load_cloud_records_cached(user_id)
    return _merge_records(local_records, cloud_records)


def _save_records(user_id: str, records: list[dict[str, Any]]) -> None:
    path = _storage_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _read_json_records_cached.clear()


@st.cache_data(ttl=60, show_spinner=False)
def _read_customer_excel_cached(path_text: str, modified_time: float) -> pd.DataFrame:
    del modified_time
    try:
        return pd.read_excel(path_text, sheet_name="고객DB")
    except Exception:
        return pd.DataFrame()


def _read_customers(user_id: str) -> pd.DataFrame:
    path = get_user_cumulative_db_path(user_id)

    # 고객 등록/크레탑 업로드 결과는 Supabase의 고객 스냅샷을 우선합니다.
    # 로컬 Excel만 읽으면 최근 등록된 사업자번호와 재무 필드가 누락될 수
    # 있어 주가평가 화면에서 해당 기업을 선택해도 입력값이 공란으로 남습니다.
    try:
        cloud_df = load_registered_customers(path, user_id)
    except Exception:
        cloud_df = pd.DataFrame()
    if not cloud_df.empty:
        return cloud_df.dropna(how="all").copy()

    if not path.exists():
        return pd.DataFrame()
    try:
        df = _read_customer_excel_cached(str(path), path.stat().st_mtime)
    except OSError:
        return pd.DataFrame()
    if df.empty:
        return df
    return df.dropna(how="all").copy()


def _customer_defaults(row: pd.Series | None) -> dict[str, Any]:
    if row is None:
        return {}

    def get(*names: str) -> Any:
        for name in names:
            if name in row.index:
                value = row.get(name)
                if value is not None and str(value).strip().lower() not in {
                    "", "nan", "none", "nat"
                }:
                    return value
        return ""

    return {
        "법인명": get("업체명", "기업명"),
        "사업자등록번호": get(
            "사업자등록번호",
            "사업자번호",
            "business_no",
            "사업자등록번호(10자리)",
        ),
        "법인등록번호": get("법인등록번호"),
        "본점소재지": get("사업장 소재지", "본점소재지", "주소"),
        "설립일": get("설립일", "설립년도"),
        "총자산": get("자산총계", "총자산", "자산합계", "장부상총자산"),
        "총부채": get("부채총계", "총부채", "부채합계", "장부상총부채"),
        "자본총계": get("자본총계"),
        "최근당기순이익": get(
            "당기순이익",
            "순이익",
            "법인세차감후순이익",
            "당기순손익",
        ),
    }


def _record_label(record: dict[str, Any]) -> str:
    name = record.get("company_name", "회사명 없음")
    base_date = record.get("valuation_date", "")
    saved_at = record.get("saved_at", "")
    return f"{name} · 기준일 {base_date} · 저장 {saved_at}"


def calculate_goodwill(
    annual_rows: list[dict[str, Any]],
    equity_before_goodwill: Any,
    acquired_intangible_remaining: Any = 0,
) -> dict[str, Any]:
    """Reference workbook's base goodwill sheet, using whole-company income.

    Tax-adjusted inputs and inclusion eligibility remain a professional review.
    Negative equity requires manual review rather than an unverified tax rule.
    """
    if len(annual_rows) != 3:
        raise ValueError("영업권 자동계산에는 최근 3개년 순손익액이 필요합니다.")
    incomes, years = [], []
    for row in annual_rows:
        income = _safe_number(row.get("adjusted_net_income"))
        year = _safe_number(row.get("year"))
        if income is None:
            raise ValueError("영업권 자동계산에 필요한 3개년 순손익액을 확인해주세요.")
        if year is None or not year.is_integer() or not 1900 <= year <= 9999:
            raise ValueError("영업권 계산 연도를 최근 연도부터 정확히 입력해주세요.")
        incomes.append(income)
        years.append(int(year))
    if years != [years[0], years[0] - 1, years[0] - 2]:
        raise ValueError("영업권 계산은 최근 연도부터 연속된 3개년 순서여야 합니다.")
    equity = _safe_number(equity_before_goodwill)
    remaining = _safe_number(acquired_intangible_remaining)
    if equity is None:
        raise ValueError("영업권 가산 전 조정 순자산가액을 확인해주세요.")
    if equity < 0:
        raise ValueError(
            "영업권 가산 전 자기자본이 음수입니다. 세무 검토 후 수동입력 또는 불산입을 선택해주세요."
        )
    if remaining is None or remaining < 0:
        raise ValueError("매입 무체재산권 미상각잔액을 0 이상의 금액으로 입력해주세요.")
    raw_weighted_income = sum(value * weight for value, weight in zip(incomes, (3, 2, 1))) / 6
    # §59(3) incorporates §56(1)'s negative weighted-income floor.
    weighted_income = max(raw_weighted_income, 0.0)
    excess_earnings = weighted_income * 0.5 - equity * 0.1
    discounted_value = excess_earnings * GOODWILL_DISCOUNT_FACTOR
    amount = max(discounted_value - remaining, 0.0)
    if not all(math.isfinite(value) for value in (raw_weighted_income, excess_earnings, discounted_value, amount)):
        raise ValueError("영업권 계산 금액이 허용 범위를 벗어났습니다.")
    warnings = []
    if raw_weighted_income < 0:
        warnings.append("순손익 가중평균액이 음수여서 영업권 계산에는 0원을 적용했습니다.")
    return {
        "raw_weighted_income": raw_weighted_income,
        "weighted_income": weighted_income,
        "equity_before_goodwill": equity,
        "excess_earnings": excess_earnings,
        "discounted_value": discounted_value,
        "acquired_intangible_remaining": remaining,
        "amount": amount,
        "discount_factor": GOODWILL_DISCOUNT_FACTOR,
        "normal_return_rate": 0.1,
        "income_rate": 0.5,
        "years": 5,
        "formula_version": "excel-base-v1",
        "law_base_date": GOODWILL_LAW_BASE_DATE,
        "warnings": warnings,
    }


def calculate_stock_valuation(inputs: dict[str, Any]) -> dict[str, Any]:
    current_shares = _safe_number(inputs.get("current_shares"))
    if not current_shares or current_shares <= 0:
        raise ValueError("현재 발행주식총수를 입력해주세요.")

    annual_rows = inputs.get("annual_rows", [])
    weighted_sum = 0.0
    total_weight = 0.0
    annual_details = []

    for weight, row in zip((3, 2, 1), annual_rows):
        net_income = _safe_number(row.get("adjusted_net_income"))
        annual_shares = _safe_number(row.get("shares")) or current_shares
        year = row.get("year")

        if net_income is None:
            annual_details.append({
                "year": year,
                "net_income": None,
                "shares": annual_shares,
                "per_share_income": None,
                "weight": weight,
            })
            continue

        if not annual_shares or annual_shares <= 0:
            raise ValueError(f"{year}년 발행주식총수를 확인해주세요.")

        per_share_income = net_income / annual_shares
        weighted_sum += per_share_income * weight
        total_weight += weight
        annual_details.append({
            "year": year,
            "net_income": net_income,
            "shares": annual_shares,
            "per_share_income": per_share_income,
            "weight": weight,
        })

    if total_weight != 6:
        raise ValueError(
            "최근 3개 사업연도의 세법상 조정 후 순손익액을 모두 입력해주세요."
        )

    capitalization_rate = (
        _safe_number(inputs.get("capitalization_rate"))
        or CAPITALIZATION_RATE_DEFAULT
    )
    if capitalization_rate <= 0:
        raise ValueError("순손익가치 환원율은 0보다 커야 합니다.")

    raw_weighted_per_share_income = weighted_sum / 6
    weighted_per_share_income = max(raw_weighted_per_share_income, 0.0)
    earnings_value_per_share = weighted_per_share_income / capitalization_rate

    total_assets = _safe_number(inputs.get("total_assets"))
    total_liabilities = _safe_number(inputs.get("total_liabilities"))

    if total_assets is None or total_liabilities is None:
        raise ValueError("평가기준일 현재 총자산과 총부채를 입력해주세요.")

    asset_additions = _safe_number(inputs.get("asset_additions")) or 0
    asset_deductions = _safe_number(inputs.get("asset_deductions")) or 0
    liability_additions = _safe_number(inputs.get("liability_additions")) or 0
    liability_deductions = _safe_number(inputs.get("liability_deductions")) or 0
    other_adjustments = _safe_number(inputs.get("other_adjustments")) or 0

    adjusted_assets = total_assets + asset_additions - asset_deductions
    adjusted_liabilities = (
        total_liabilities + liability_additions - liability_deductions
    )
    net_assets_before_goodwill = adjusted_assets - adjusted_liabilities + other_adjustments
    goodwill_mode = inputs.get("goodwill_mode", "manual")
    goodwill_details = None
    if goodwill_mode == "auto":
        goodwill_details = calculate_goodwill(
            annual_rows,
            net_assets_before_goodwill,
            inputs.get("goodwill_acquired_intangible_remaining", 0),
        )
        goodwill = goodwill_details["amount"]
    elif goodwill_mode == "excluded":
        if not str(inputs.get("goodwill_exclusion_reason") or "").strip():
            raise ValueError("영업권 불산입을 선택한 검토 사유를 입력해주세요.")
        goodwill = 0.0
    elif goodwill_mode == "manual":
        goodwill = _safe_number(inputs.get("goodwill")) or 0
    else:
        raise ValueError("영업권 계산 방식을 다시 선택해주세요.")
    raw_adjusted_net_assets = net_assets_before_goodwill + goodwill
    adjusted_net_assets = max(raw_adjusted_net_assets, 0.0)
    nav_per_share = adjusted_net_assets / current_shares

    valuation_type = inputs.get("valuation_type", "일반법인(3:2)")
    if valuation_type == "부동산과다보유법인(2:3)":
        earnings_weight, nav_weight = 0.40, 0.60
    elif valuation_type == "순자산가치만 적용":
        earnings_weight, nav_weight = 0.00, 1.00
    else:
        earnings_weight, nav_weight = 0.60, 0.40

    weighted_value = (
        earnings_value_per_share * earnings_weight
        + nav_per_share * nav_weight
    )

    nav_floor_rate = (
        _safe_number(inputs.get("nav_floor_rate"))
        or NAV_FLOOR_RATE_DEFAULT
    )
    nav_floor = nav_per_share * nav_floor_rate

    if valuation_type == "순자산가치만 적용":
        basic_value_per_share = nav_per_share
    else:
        basic_value_per_share = max(weighted_value, nav_floor)

    premium_rate = _safe_number(inputs.get("premium_rate")) or 0
    if inputs.get("premium_exempt", True):
        premium_rate = 0

    final_value_per_share = basic_value_per_share * (1 + premium_rate)
    total_equity_value = final_value_per_share * current_shares

    return {
        "annual_details": annual_details,
        "raw_weighted_per_share_income": raw_weighted_per_share_income,
        "weighted_per_share_income": weighted_per_share_income,
        "earnings_value_per_share": earnings_value_per_share,
        "adjusted_assets": adjusted_assets,
        "adjusted_liabilities": adjusted_liabilities,
        "net_assets_before_goodwill": net_assets_before_goodwill,
        "goodwill": goodwill,
        "goodwill_mode": goodwill_mode,
        "goodwill_details": goodwill_details,
        "raw_adjusted_net_assets": raw_adjusted_net_assets,
        "adjusted_net_assets": adjusted_net_assets,
        "nav_per_share": nav_per_share,
        "earnings_weight": earnings_weight,
        "nav_weight": nav_weight,
        "weighted_value": weighted_value,
        "nav_floor": nav_floor,
        "basic_value_per_share": basic_value_per_share,
        "premium_rate": premium_rate,
        "final_value_per_share": final_value_per_share,
        "total_equity_value": total_equity_value,
    }


def _matching_stock_result(inputs: dict[str, Any]) -> dict[str, Any] | None:
    """Never pair an old result with a different company or new input values."""
    if st.session_state.get("stock_last_inputs") != inputs:
        return None
    return st.session_state.get("stock_last_result")


def _build_stock_record(
    inputs: dict[str, Any],
    result: dict[str, Any],
    *,
    record_id: str,
    saved_by: str,
    saved_at: str,
) -> dict[str, Any]:
    if calculate_stock_valuation(inputs) != result:
        raise ValueError("입력값과 평가결과가 다릅니다. 주가평가를 다시 계산해주세요.")
    record = deepcopy(inputs)
    record.update({
        "record_id": record_id,
        "business_no": _normalize_business_no(inputs.get("business_no", "")),
        "goodwill_manual": inputs.get("goodwill"),
        "goodwill": result["goodwill"],
        "goodwill_mode": result["goodwill_mode"],
        "goodwill_details": deepcopy(result["goodwill_details"]),
        "result": deepcopy(result),
        "saved_by": saved_by,
        "saved_at": saved_at,
        "law_base_date": LAW_BASE_DATE,
        "goodwill_law_base_date": GOODWILL_LAW_BASE_DATE,
    })
    return record


def _registry_cache_path(user_id: str) -> Path:
    dirs = get_user_dirs(user_id)
    return dirs["base"] / "registry_cache.json"


class RegistryCacheReadError(RuntimeError):
    """Block writes when an existing registry source cannot be safely read."""


def _load_registry_cache(user_id: str, *, strict: bool = False) -> dict[str, Any]:
    path = _registry_cache_path(user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(not isinstance(item, dict) for item in data.values()):
            if strict:
                raise RegistryCacheReadError("기존 등기정보 형식을 확인하지 못해 덮어쓰지 않았습니다.")
            return {}
        return data
    except RegistryCacheReadError:
        raise
    except Exception as exc:
        if strict:
            raise RegistryCacheReadError("기존 등기정보를 읽지 못해 덮어쓰지 않았습니다.") from exc
        return {}


def _normalize_company_name(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", str(value or "").lower())


def _resolve_registry_business_no(
    user_id: str,
    business_no: Any,
    data: dict[str, Any],
) -> str:
    """Resolve a registry upload to exactly one registered customer."""
    candidates = [
        _normalize_business_no(business_no),
        _normalize_business_no(data.get("사업자등록번호", "")),
    ]
    for candidate in candidates:
        if len(re.sub(r"[^0-9]", "", candidate)) == 10:
            return candidate

    customers = _read_customers(user_id)
    if customers.empty:
        return ""

    corporate_digits = re.sub(
        r"[^0-9]", "", str(data.get("법인등록번호", "") or "")
    )
    company_name = _normalize_company_name(
        data.get("법인명") or data.get("업체명")
    )
    matches: list[str] = []
    for _, row in customers.iterrows():
        row_corporate_digits = re.sub(
            r"[^0-9]", "", str(row.get("법인등록번호", "") or "")
        )
        row_company_name = _normalize_company_name(
            row.get("업체명") or row.get("기업명")
        )
        corporate_match = bool(
            corporate_digits
            and row_corporate_digits
            and corporate_digits == row_corporate_digits
        )
        name_match = bool(
            company_name and row_company_name and company_name == row_company_name
        )
        if not corporate_match and not name_match:
            continue
        resolved = _normalize_business_no(
            row.get("사업자등록번호", row.get("사업자번호", ""))
        )
        if len(re.sub(r"[^0-9]", "", resolved)) == 10:
            matches.append(resolved)

    unique_matches = list(dict.fromkeys(matches))
    return unique_matches[0] if len(unique_matches) == 1 else ""


def _save_registry_cache(
    user_id: str,
    business_no: str,
    data: dict[str, Any],
    *,
    storage_result: dict[str, Any] | None = None,
) -> str:
    cache = _load_registry_cache(user_id, strict=True)
    key = _resolve_registry_business_no(user_id, business_no, data)
    if not key:
        return ""

    # A partial extraction must never erase previously confirmed registry values.
    merged = dict(cache.get(key, {}) or {})
    merged.update({
        field: value for field, value in dict(data or {}).items()
        if value not in (None, "", [], {})
    })
    cache[key] = {
        **merged,
        "사업자등록번호": key,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = _registry_cache_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)
    try:
        result = sync_registry_snapshot(user_id, key, cache[key])
        cloud_saved = bool(isinstance(result, tuple) and result and result[0])
    except Exception as exc:
        write_runtime_error("registry_snapshot_sync", exc)
        cloud_saved = False
    if storage_result is not None:
        storage_result.update({
            "cloud_saved": cloud_saved,
            "registry_data": dict(cache[key]),
        })
    return key


def _apply_registry_data(data: dict[str, Any]) -> None:
    st.session_state["stock_company_name"] = str(
        data.get("법인명")
        or st.session_state.get("stock_company_name", "")
    )
    st.session_state["stock_corporate_no"] = str(
        data.get("법인등록번호")
        or st.session_state.get("stock_corporate_no", "")
    )
    st.session_state["stock_address"] = str(
        data.get("본점소재지")
        or st.session_state.get("stock_address", "")
    )
    st.session_state["stock_establishment"] = str(
        data.get("법인설립일")
        or st.session_state.get("stock_establishment", "")
    )
    st.session_state["stock_current_shares"] = _format_number(
        data.get("발행주식총수")
    ) or st.session_state.get("stock_current_shares", "")
    st.session_state["stock_par_value"] = _format_number(
        data.get("1주당액면가액")
    ) or st.session_state.get("stock_par_value", "")
    st.session_state["stock_capital"] = _format_number(
        data.get("자본금")
    ) or st.session_state.get("stock_capital", "")
    st.session_state["stock_authorized_shares"] = _format_number(
        data.get("발행할주식총수")
    ) or st.session_state.get("stock_authorized_shares", "")
    if data.get("주식종류"):
        st.session_state["stock_share_classes"] = data["주식종류"]


def _restore_registry_for_business(
    user_id: str,
    business_no: Any,
    company_name: Any = "",
    corporate_no: Any = "",
) -> dict[str, Any]:
    # 재접속 시 로컬 또는 Supabase에서 등기 업로드 내역을 복원합니다.
    key = _normalize_business_no(business_no)
    if not key:
        return {}

    try:
        cache = _load_registry_cache(user_id, strict=True)
    except RegistryCacheReadError:
        st.warning("기존 등기정보 파일을 확인하지 못해 자동 복원을 중단했습니다. 원본은 그대로 보존했으며 관리자 확인이 필요합니다.")
        return {}
    local = cache.get(key, {})
    cloud = load_registry_snapshot(
        user_id,
        key,
        company_name,
        corporate_no,
    )
    data = dict(local or {})
    for field, value in dict(cloud or {}).items():
        if value not in (None, "", [], {}):
            data[field] = value

    if not data:
        target_company = _normalize_company_name(company_name)
        target_corporate = re.sub(r"[^0-9]", "", str(corporate_no or ""))
        legacy_matches: list[dict[str, Any]] = []
        for legacy_key, legacy_data in cache.items():
            if not isinstance(legacy_data, dict):
                continue
            legacy_business = _normalize_business_no(
                legacy_data.get("사업자등록번호", legacy_key)
            )
            legacy_company = _normalize_company_name(
                legacy_data.get("법인명") or legacy_data.get("업체명")
            )
            legacy_corporate = re.sub(
                r"[^0-9]", "", str(legacy_data.get("법인등록번호", "") or "")
            )
            if legacy_business == key or (
                target_corporate
                and legacy_corporate
                and target_corporate == legacy_corporate
            ) or (
                target_company
                and legacy_company
                and target_company == legacy_company
            ):
                legacy_matches.append(dict(legacy_data))
        if len(legacy_matches) == 1:
            data = legacy_matches[0]

    if data:
        data["사업자등록번호"] = key
        cache[key] = {
            **dict(data),
            "saved_at": str(
                data.get("saved_at")
                or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ),
        }
        path = _registry_cache_path(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        if not cloud:
            sync_registry_snapshot(user_id, key, cache[key])

    if data:
        _apply_registry_data(data)
        st.session_state["stock_last_registry_data"] = data
        issued = _format_number(data.get("발행주식총수"))
        if issued:
            for index in range(1, 4):
                key_name = f"stock_shares_{index}"
                if not st.session_state.get(key_name):
                    st.session_state[key_name] = issued
    return data


def _capture_stock_financial_inputs() -> dict[str, Any]:
    keys = [
        "stock_total_assets",
        "stock_total_liabilities",
        "stock_asset_additions",
        "stock_asset_deductions",
        "stock_liability_additions",
        "stock_liability_deductions",
        "stock_goodwill",
        "stock_other_adjustments",
        *GOODWILL_STATE_KEYS,
    ]
    for index in range(1, 4):
        keys.extend([
            f"stock_year_{index}",
            f"stock_net_income_{index}",
        ])
    return {
        key: st.session_state.get(key)
        for key in keys
        if key in st.session_state
    }


def _restore_stock_financial_inputs(values: dict[str, Any]) -> None:
    for key, value in (values or {}).items():
        if value is not None:
            st.session_state[key] = value


def _apply_loaded_record(record: dict[str, Any]) -> None:
    st.session_state.pop("stock_last_result", None)
    st.session_state.pop("stock_last_inputs", None)
    st.session_state["stock_loaded_record_id"] = record.get("record_id", "")
    st.session_state["stock_tax_adjustment_review"] = deepcopy(
        record.get("tax_adjustment_review", [])
    )
    st.session_state["stock_company_name"] = record.get("company_name", "")
    st.session_state["stock_business_no"] = record.get("business_no", "")
    st.session_state["stock_corporate_no"] = record.get("corporate_no", "")
    st.session_state["stock_address"] = record.get("address", "")
    st.session_state["stock_establishment"] = record.get("establishment", "")
    st.session_state["stock_valuation_date"] = record.get(
        "valuation_date",
        date.today().isoformat(),
    )
    st.session_state["stock_current_shares"] = _format_number(record.get("current_shares"))
    st.session_state["stock_par_value"] = _format_number(record.get("par_value"))
    st.session_state["stock_capital"] = _format_number(record.get("capital"))
    st.session_state["stock_authorized_shares"] = _format_number(
        record.get("authorized_shares")
    )
    st.session_state["stock_share_classes"] = record.get("share_classes", [])
    st.session_state["stock_valuation_type"] = record.get(
        "valuation_type",
        "일반법인(3:2)",
    )
    st.session_state["stock_cap_rate"] = record.get(
        "capitalization_rate",
        CAPITALIZATION_RATE_DEFAULT,
    )
    st.session_state["stock_nav_floor"] = record.get(
        "nav_floor_rate",
        NAV_FLOOR_RATE_DEFAULT,
    )
    st.session_state["stock_premium_exempt"] = record.get(
        "premium_exempt",
        True,
    )
    st.session_state["stock_premium_rate"] = record.get(
        "premium_rate",
        MAX_SHAREHOLDER_PREMIUM_DEFAULT,
    )

    saved_rows = record.get("annual_rows", [])[:3]
    for index in range(1, 4):
        row = saved_rows[index - 1] if index <= len(saved_rows) else {}
        st.session_state[f"stock_year_{index}"] = str(row.get("year", "") or "")
        st.session_state[f"stock_net_income_{index}"] = _format_number(
            row.get("adjusted_net_income")
        )
        st.session_state[f"stock_shares_{index}"] = _format_number(
            row.get("shares")
        )

    for key in [
        "total_assets",
        "total_liabilities",
        "asset_additions",
        "asset_deductions",
        "liability_additions",
        "liability_deductions",
        "goodwill",
        "other_adjustments",
    ]:
        st.session_state[f"stock_{key}"] = _format_number(record.get(key))
    # Legacy saved evaluations must never silently acquire an automatic amount.
    mode = record.get("goodwill_mode", "manual")
    st.session_state["stock_goodwill_mode"] = mode if mode in GOODWILL_MODE_LABELS else "manual"
    st.session_state["stock_goodwill"] = _format_number(
        record.get("goodwill_manual", record.get("goodwill"))
    )
    st.session_state["stock_goodwill_acquired_intangible_remaining"] = _format_number(
        record.get("goodwill_acquired_intangible_remaining", 0)
    )
    st.session_state["stock_goodwill_exclusion_reason"] = record.get("goodwill_exclusion_reason", "")


def _find_stock_customer_row(
    customers: pd.DataFrame,
    *,
    business_no: Any = "",
    company_name: Any = "",
) -> pd.Series | None:
    """Resolve exactly one registered customer for the valuation context."""
    if not isinstance(customers, pd.DataFrame) or customers.empty:
        return None

    target_business = _normalize_business_no(business_no)
    if target_business:
        business_matches = [
            row
            for _, row in customers.iterrows()
            if _normalize_business_no(row.get("사업자등록번호", ""))
            == target_business
        ]
        if len(business_matches) == 1:
            return business_matches[0]
        if len(business_matches) > 1:
            return None

    target_company = _normalize_company_name(company_name)
    if not target_company:
        return None
    company_matches = [
        row
        for _, row in customers.iterrows()
        if _normalize_company_name(
            row.get("업체명") or row.get("기업명")
        )
        == target_company
    ]
    return company_matches[0] if len(company_matches) == 1 else None


def _clear_stock_customer_state() -> None:
    """Clear values that must never carry over to another company."""
    keys = [
        "stock_company_name",
        "stock_business_no",
        "stock_corporate_no",
        "stock_address",
        "stock_establishment",
        "stock_current_shares",
        "stock_par_value",
        "stock_capital",
        "stock_authorized_shares",
        "stock_share_classes",
        "stock_total_assets",
        "stock_total_liabilities",
        "stock_asset_additions",
        "stock_asset_deductions",
        "stock_liability_additions",
        "stock_liability_deductions",
        "stock_goodwill",
        "stock_other_adjustments",
        *GOODWILL_STATE_KEYS,
        "stock_last_registry_data",
        "stock_registry_restored_key",
        "stock_last_result",
        "stock_last_inputs",
        "stock_loaded_record_id",
        "stock_tax_adjustment_review",
        "stock_tax_adjustment_applied_message",
    ]
    for index in range(1, 4):
        keys.extend(
            [
                f"stock_year_{index}",
                f"stock_net_income_{index}",
                f"stock_shares_{index}",
            ]
        )
    for key in keys:
        st.session_state.pop(key, None)


def _load_stock_customer_context(
    user_id: str,
    customers: pd.DataFrame,
    *,
    selected_business_no: Any = "",
    selected_company_name: Any = "",
) -> pd.Series | None:
    explicit_business = _normalize_business_no(selected_business_no)
    explicit_company = str(selected_company_name or "").strip()
    state_business = _normalize_business_no(
        st.session_state.get("stock_business_no", "")
    )
    state_company = str(
        st.session_state.get("stock_company_name", "") or ""
    ).strip()
    selected_row = _find_stock_customer_row(
        customers,
        business_no=explicit_business or state_business,
        company_name=explicit_company or state_company,
    )
    if selected_row is None:
        return None

    resolved_business = _normalize_business_no(
        selected_row.get("사업자등록번호", "")
    )
    customer_revision = str(
        selected_row.get("_cloud_updated_at", "") or ""
    )
    customer_id = str(selected_row.get("_customer_id", "") or "")
    context_key = f"{resolved_business}:{customer_id}:{customer_revision}"
    if st.session_state.get("stock_customer_context_key") != context_key:
        loaded_record_matches = bool(
            st.session_state.get("stock_loaded_record_id")
            and _normalize_business_no(
                st.session_state.get("stock_business_no", "")
            )
            == resolved_business
        )
        if not loaded_record_matches:
            _clear_stock_customer_state()
        st.session_state["stock_customer_context_key"] = context_key
        if loaded_record_matches:
            st.session_state["stock_auto_loaded_customer_key"] = context_key

    if st.session_state.get("stock_auto_loaded_customer_key") != context_key:
        loaded, _ = _apply_customer_financial_data(user_id, selected_row)
        if loaded:
            business_no = st.session_state.get("stock_business_no", "")
            _restore_registry_for_business(
                user_id,
                business_no,
                company_name=st.session_state.get("stock_company_name", ""),
                corporate_no=st.session_state.get("stock_corporate_no", ""),
            )
            st.session_state["stock_registry_restored_key"] = (
                _normalize_business_no(business_no)
            )
            st.session_state["stock_auto_loaded_customer_key"] = context_key
    return selected_row


def _registry_identity_error(data: dict[str, Any], selected_row: Any) -> str:
    """Do not attach an unrelated company's registry to the selected customer."""
    from registry_runner import registry_result_error

    error = registry_result_error(data)
    if error:
        return error

    def digits(value: Any) -> str:
        return re.sub(r"[^0-9]", "", str(value or ""))

    def company_key(value: Any) -> str:
        text = re.sub(r"주식회사|유한회사|유한책임회사|\(주\)|\(유\)", "", str(value or ""))
        return _normalize_company_name(text)

    current_corporate = digits(selected_row.get("법인등록번호", ""))
    incoming_corporate = digits(data.get("법인등록번호", ""))
    current_business = digits(selected_row.get("사업자등록번호", ""))
    incoming_business = digits(data.get("사업자등록번호", ""))
    business_match = bool(
        len(current_business) == len(incoming_business) == 10
        and current_business == incoming_business
    )
    if len(current_business) == len(incoming_business) == 10 and current_business != incoming_business:
        return "선택한 기업과 등기자료의 사업자등록번호가 다릅니다. 올바른 기업·파일을 선택해주세요."
    if len(current_corporate) == len(incoming_corporate) == 13:
        if current_corporate != incoming_corporate:
            return "선택한 기업과 등기자료의 법인등록번호가 다릅니다. 기존 정보는 변경하지 않았습니다."
        return ""  # Same corporation can have a historical company name.
    current_name = company_key(selected_row.get("업체명", ""))
    incoming_name = company_key(data.get("법인명", ""))
    if current_name and incoming_name and current_name != incoming_name:
        return "선택한 기업과 등기자료의 법인명이 다릅니다. 기업 선택 또는 원본을 확인해주세요."
    if business_match or (current_name and incoming_name and current_name == incoming_name):
        return ""
    return (
        "선택한 기업과 등기자료가 같은 법인인지 확인할 식별정보가 부족합니다. "
        "등록 기업의 법인등록번호를 보완하거나 법인명이 읽히는 원본 PDF로 다시 시도해주세요. "
        "기존 정보는 변경하지 않았습니다."
    )


def _render_registry_upload(
    user_id: str,
    selected_row: pd.Series | None,
) -> None:
    st.markdown("### 등기사항증명서 업로드")
    current_business_no = _normalize_business_no(
        st.session_state.get("stock_business_no", "")
    )
    current_company_name = str(
        st.session_state.get("stock_company_name", "") or ""
    ).strip()
    has_customer_context = bool(
        selected_row is not None and current_business_no
    )
    if has_customer_context:
        st.caption(
            f"현재 평가기업: {current_company_name or '선택된 등록 고객'} · "
            "등록된 크레탑·등기정보는 자동으로 불러옵니다."
        )
    else:
        st.info(
            "기업컨설팅에서 업체를 먼저 선택해주세요. "
            "업체가 확인되어야 등기자료를 안전하게 연결할 수 있습니다."
        )

    restore_key = current_business_no if has_customer_context else ""
    if (
        restore_key
        and st.session_state.get("stock_registry_restored_key")
        != restore_key
    ):
        restored = _restore_registry_for_business(
            user_id,
            restore_key,
            company_name=current_company_name,
            corporate_no=st.session_state.get("stock_corporate_no", ""),
        )
        st.session_state["stock_registry_restored_key"] = restore_key
        if restored:
            st.session_state["stock_last_registry_data"] = restored

    progress_key = f"registry_upload:{user_id}:{restore_key}"
    render_upload_feedback(progress_key, ui=st)
    registry_pdf = st.file_uploader(
        "법인 등기사항증명서 PDF",
        type=["pdf"],
        key="stock_registry_pdf",
        disabled=not has_customer_context,
    )
    st.caption(
        "법인명, 법인등록번호, 본점소재지, 설립일, 자본금, "
        "발행주식총수, 1주의 금액 등을 자동 추출합니다."
    )
    if registry_pdf is not None:
        render_upload_ready(registry_pdf, ui=st)

    if registry_pdf is not None and st.button(
        "등기자료 분석·등록",
        key="stock_analyze_registry",
        use_container_width=True,
        disabled=not has_customer_context,
    ):
        from registry_runner import run_registry_worker
        completed = False
        with UploadProgress(
            progress_key, "등기자료 분석·등록 중",
            steps=("파일 확인", "등기정보 분석", "기업 연결·저장", "등록 완료"), ui=st,
        ) as task:
            try:
                task.step("파일 확인", "선택한 파일을 확인하고 안전하게 보관하고 있습니다.")
                content = registry_pdf.getvalue()
                if not content or b"%PDF-" not in content[:1024]:
                    task.error("읽을 수 있는 PDF 파일을 선택해주세요.", "기존 등기정보와 재무 입력값은 변경하지 않았습니다.")
                    return
                dirs = get_user_dirs(user_id)
                dirs["uploads"].mkdir(parents=True, exist_ok=True)
                registry_path = dirs["uploads"] / (
                    f"등기자료_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}.pdf"
                )
                registry_path.write_bytes(content)
                task.step("등기정보 분석", "법인명·자본금·주식정보를 추출하고 있습니다. 최대 90초가 걸릴 수 있습니다.")
                registry_data, registry_error = run_registry_worker(registry_path, timeout=90)
                registry_error = registry_error or _registry_identity_error(registry_data, selected_row)
                if registry_error:
                    task.error(registry_error, "기업·등기정보는 변경하지 않았습니다. 파일을 확인한 뒤 다시 시도해주세요.")
                    return
                task.step("기업 연결·저장", "분석 결과를 현재 기업에 연결하고 저장 상태를 확인하고 있습니다.")
                storage_result: dict[str, Any] = {}
                saved_registry_business_no = _save_registry_cache(
                    user_id, current_business_no, registry_data, storage_result=storage_result,
                )
                if not saved_registry_business_no:
                    task.error("분석 결과를 현재 기업과 안전하게 연결하지 못했습니다.", "기업을 다시 선택한 후 재시도해주세요. 기존 정보는 유지됩니다.")
                    return
                registry_data = dict(storage_result.get("registry_data") or registry_data)
                registry_data["사업자등록번호"] = saved_registry_business_no
                preserved_financial_inputs = _capture_stock_financial_inputs()
                _apply_registry_data(registry_data)
                _restore_stock_financial_inputs(preserved_financial_inputs)
                st.session_state["stock_last_registry_data"] = registry_data
                st.session_state["stock_registry_restored_key"] = saved_registry_business_no
                issued = _format_number(registry_data.get("발행주식총수"))
                if issued:
                    for index in range(1, 4):
                        share_key = f"stock_shares_{index}"
                        if not st.session_state.get(share_key):
                            st.session_state[share_key] = issued
                attachment_saved = True
                try:
                    from enterprise_documents import register_existing_enterprise_document

                    register_existing_enterprise_document(
                        registry_path, user_id=user_id,
                        business_no=saved_registry_business_no,
                        company_name=current_company_name,
                        document_type="corporate_registry",
                        customer_id=str(selected_row.get("_customer_id", "") or ""),
                        analysis_summary="법인 등기사항증명서 분석을 완료했습니다.",
                        extracted_fields=dict(registry_data),
                    )
                    from enterprise_documents import _load_enterprise_source_overview_cached
                    _load_enterprise_source_overview_cached.clear()
                except Exception as exc:
                    write_runtime_error("registry_document_link", exc)
                    attachment_saved = False
                detail = "기존 당기순이익과 재무 입력값은 유지했습니다. 기업컨설팅의 주가평가에서 등기정보를 확인할 수 있습니다."
                if not storage_result.get("cloud_saved"):
                    detail += " 분석 결과는 로컬에 보관했으며 클라우드 동기화 상태를 확인해야 합니다."
                if not attachment_saved:
                    detail += " 첨부자료·기업히스토리 연결은 완료하지 못했습니다. 같은 파일로 다시 등록할 수 있습니다."
                if storage_result.get("cloud_saved") and attachment_saved:
                    task.complete("등기자료 분석·등록이 완료되었습니다.", detail, persist=True)
                else:
                    task.warning("등기정보는 보관했지만 일부 저장을 확인해야 합니다.", detail, persist=True)
                completed = True
            except Exception as exc:
                write_runtime_error("registry_upload", exc)
                task.error(
                    safe_public_error(exc, "등기자료 분석·등록을 완료하지 못했습니다."),
                    "원본과 기존 재무정보는 삭제하지 않았습니다. 파일을 그대로 두고 다시 시도해주세요.",
                )
        if completed:
            st.rerun()

def _render_stock_history(user_id: str) -> None:
    st.divider()
    st.markdown("### 주가평가 히스토리")
    records = _load_records(user_id)
    with st.expander("저장된 주가평가 불러오기·수정", expanded=False):
        if not records:
            st.info(
                "저장된 주가평가 자료가 없습니다. 평가자료를 저장하면 "
                "이 메뉴에서 다시 불러올 수 있습니다."
            )
            return

        st.caption(
            f"저장된 평가자료 {len(records)}건 · 로컬 및 Supabase 통합 조회"
        )
        options = ["선택 안 함"] + [
            _record_label(record) for record in records
        ]
        selected_record_label = st.selectbox(
            "저장 기록",
            options,
            key="stock_saved_record_selector",
        )
        if selected_record_label == "선택 안 함":
            return

        record = records[options.index(selected_record_label) - 1]
        preview_result = record.get("result", {})
        if isinstance(preview_result, dict):
            final_value = _safe_number(
                preview_result.get("final_value_per_share")
            )
            total_value = _safe_number(
                preview_result.get("total_equity_value")
            )
            if final_value is not None or total_value is not None:
                p1, p2 = st.columns(2)
                p1.metric(
                    "저장된 1주당 평가액",
                    f"{final_value:,.0f}원"
                    if final_value is not None
                    else "-",
                )
                p2.metric(
                    "저장된 전체 주식가치",
                    f"{total_value:,.0f}원"
                    if total_value is not None
                    else "-",
                )
        if st.button(
            "선택 평가 불러오기",
            key="stock_load_record",
            use_container_width=True,
        ):
            _apply_loaded_record(record)
            st.success("저장된 평가자료를 불러왔습니다.")
            st.rerun()


def render_registry_upload_for_customer(
    user_id: str,
    business_no: Any,
    company_name: Any = "",
) -> None:
    """Render the existing registry workflow for one registered customer."""
    customers = _read_customers(user_id)
    selected_row = _load_stock_customer_context(
        user_id,
        customers,
        selected_business_no=business_no,
        selected_company_name=company_name,
    )
    _render_registry_upload(user_id, selected_row)


def _stock_legal_today() -> date:
    return datetime.now(ZoneInfo("Asia/Seoul")).date()


def _prepare_tax_adjustment_application(
    *, business_no: str, valuation_date: str, target_years: list[str],
    candidates: list[dict[str, Any]], reviewed_values: dict[int, dict[str, Any]],
    reviewed_periods: dict[int, dict[str, Any]], review_confirmed: bool,
    normal_periods_confirmed: bool,
    special_adjustments_reviewed: bool = False, special_adjustment_note: str = "",
    law_changes_reviewed: bool = False,
    identity_confirmed: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate a complete review before changing any existing valuation input."""
    from tax_adjustment_valuation import calculate_tax_adjusted_income, LAW_BASE_DATE, LAW_EFFECTIVE_DATE

    if not review_confirmed or not (normal_periods_confirmed or special_adjustments_reviewed):
        raise ValueError("원문·법정 조정항목과 사업연도·주식수 검토를 먼저 확인해주세요.")
    if special_adjustments_reviewed and not str(special_adjustment_note).strip():
        raise ValueError("연환산·증감자 조정 등의 전문가 검토 근거를 입력해주세요.")
    if len(re.sub(r"\D", "", business_no)) != 10:
        raise ValueError("자료를 반영할 등록 기업을 먼저 선택해주세요.")
    try:
        evaluated = date.fromisoformat(str(valuation_date))
        years = [int(str(value)) for value in target_years]
    except (ValueError, TypeError):
        raise ValueError("평가기준일과 적용 대상 사업연도를 확인해주세요.") from None
    if not date.fromisoformat(LAW_EFFECTIVE_DATE) <= evaluated <= _stock_legal_today():
        raise ValueError("평가기준일이 확인한 현행법령 적용 범위 밖입니다. 해당 시점 법령을 별도로 검토한 후 수동 반영해주세요.")
    if evaluated > date.fromisoformat(LAW_BASE_DATE) and not law_changes_reviewed:
        raise ValueError("법령 확인 기준일 이후 평가입니다. 평가기준일의 법령 변경 여부를 확인해주세요.")
    if len(years) != 3 or len(set(years)) != 3 or years != sorted(years, reverse=True):
        raise ValueError("주가평가 계산 탭에 최근 3개 사업연도를 최신순으로 설정해주세요.")
    if years != [years[0], years[0] - 1, years[0] - 2]:
        raise ValueError("최근 3개 사업연도는 누락 없이 연속되어야 합니다.")
    if not candidates:
        raise ValueError("반영할 사업연도를 선택해주세요.")
    if any(item.get("identity_review_required") for item in candidates) and not identity_confirmed:
        raise ValueError("복수 사업자번호 문서의 항목이 선택한 기업에 속하는지 확인해주세요.")
    updates: dict[str, Any] = {}
    reviewed = []
    fiscal_end_patterns = set()
    for candidate in candidates:
        year = int(candidate["year"])
        if year not in years:
            raise ValueError("선택한 자료 연도가 현재 주가평가의 3개 사업연도와 일치하지 않습니다.")
        period = reviewed_periods.get(year, {})
        try:
            start = date.fromisoformat(str(period.get("period_start", "")))
            end = date.fromisoformat(str(period.get("period_end", "")))
            anniversary = start.replace(year=start.year + 1)
        except (ValueError, TypeError):
            raise ValueError("원문에서 확인한 사업연도 시작일·종료일을 YYYY-MM-DD로 입력해주세요.") from None
        if end.year != year or end >= evaluated or end < start:
            raise ValueError("사업연도와 평가기준일의 선후관계를 확인해주세요.")
        if end + timedelta(days=1) != anniversary:
            raise ValueError("1년 미만·초과 사업연도는 연환산 등 별도 세법 검토 후 수동 반영해야 합니다.")
        fiscal_end_patterns.add((end.month, end.day))
        try:
            current_year_end = date(evaluated.year, end.month, end.day)
        except ValueError:
            raise ValueError("결산일 변경·윤년 사업연도는 별도 검토 후 수동 반영해주세요.") from None
        latest_completed_year = evaluated.year if current_year_end < evaluated else evaluated.year - 1
        if years[0] != latest_completed_year:
            raise ValueError("평가기준일 직전 완료된 최근 3개 사업연도인지 확인해주세요.")
        for field in ("period_start", "period_end"):
            original = candidate.get(field)
            if original and str(original) != str(period.get(field)):
                raise ValueError("입력한 사업연도 기간이 연결 원문의 기간과 다릅니다. 원문을 다시 확인해주세요.")
        shares = _safe_number(period.get("expert_final_shares") if special_adjustments_reviewed else period.get("shares"))
        if shares is None or shares <= 0 or not shares.is_integer():
            raise ValueError("각 사업연도 말의 발행주식총수를 양의 정수로 입력해주세요.")
        values = dict(reviewed_values.get(year, {}))
        calculation = calculate_tax_adjusted_income(values)
        if calculation.get("invalid"):
            raise ValueError("가산·차감 대상 금액은 음수로 중복 차감하지 않도록 부호를 확인해주세요.")
        if not calculation.get("complete") or calculation.get("adjusted_net_income") is None:
            raise ValueError("미확인 법정 조정항목을 원문으로 확인해 입력해주세요. 해당 없음도 0원을 명시해야 합니다.")
        if not special_adjustments_reviewed and any(
            item.get("key") in {"capital_adjustment", "reserve_allocation"}
            and item.get("value") not in (None, 0)
            for item in calculation.get("components", [])
        ):
            raise ValueError(
                "증감자 조정액 또는 일시환입 안분액이 있으면 전문가 검토 경로에서 "
                "최종 순손익액·환산주식수와 검토 근거를 별도로 확인해야 합니다."
            )
        if special_adjustments_reviewed:
            expert_income = _safe_number(period.get("expert_final_income"))
            if expert_income is None or not expert_income.is_integer():
                raise ValueError("전문가가 확정한 최종 순손익액을 원 단위 정수로 별도 입력해주세요.")
            if int(expert_income) != calculation["adjusted_net_income"]:
                raise ValueError("전문가 최종 순손익액과 위 법정 구성항목 합계가 다릅니다. 증감자 조정액 등을 다시 확인해주세요.")
        sources = candidate.get("sources", [])
        if not sources or any(not str(source.get("record_id", "")).strip() for source in sources):
            raise ValueError("추적 가능한 원본 자료가 없어 자동 반영할 수 없습니다.")
        index = years.index(year) + 1
        updates[f"stock_net_income_{index}"] = _format_number(calculation["adjusted_net_income"])
        updates[f"stock_shares_{index}"] = _format_number(shares)
        reviewed.append({
            "year": year, "period_start": start.isoformat(), "period_end": end.isoformat(),
            "shares": int(shares), "values": values,
            "source_year_end_shares": period.get("shares"),
            "expert_final_income": period.get("expert_final_income") if special_adjustments_reviewed else None,
            "expert_final_shares": period.get("expert_final_shares") if special_adjustments_reviewed else None,
            "adjusted_net_income": calculation["adjusted_net_income"],
            "sources": deepcopy(sources), "resolved_conflicts": list(candidate.get("conflicts", [])),
        })
    if len(reviewed) != len({row["year"] for row in reviewed}):
        raise ValueError("동일 사업연도 자료를 중복 반영할 수 없습니다.")
    if len(fiscal_end_patterns) > 1:
        raise ValueError("결산일이 서로 다른 자료는 사업연도 변경 여부를 전문가가 확인해야 합니다.")
    return updates, {
        "business_no": _normalize_business_no(business_no), "valuation_date": evaluated.isoformat(),
        "law_base_date": LAW_BASE_DATE, "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        "annual_rows": reviewed, "scope": "adjusted_income_and_year_end_shares_only",
        "special_adjustments_reviewed": special_adjustments_reviewed,
        "special_adjustment_note": str(special_adjustment_note).strip(),
        "law_changes_reviewed": law_changes_reviewed,
        "identity_confirmed": identity_confirmed,
    }


def _apply_reviewed_tax_adjustment(
    updates: dict[str, Any], review: dict[str, Any], *, user_id: str,
) -> None:
    """Apply an already validated draft atomically and retain its provenance."""
    if _normalize_business_no(st.session_state.get("stock_business_no", "")) != review["business_no"]:
        raise ValueError("기업 선택이 변경되었습니다. 자료를 다시 확인해주세요.")
    previous = {key: st.session_state.get(key) for key in updates}
    history = list(st.session_state.get("stock_tax_adjustment_review", []) or [])
    history.append({**deepcopy(review), "reviewed_by": user_id, "previous_inputs": previous})
    st.session_state.update(updates)
    st.session_state["stock_tax_adjustment_review"] = history
    st.session_state.pop("stock_last_result", None)
    st.session_state.pop("stock_last_inputs", None)
    st.session_state["stock_tax_adjustment_applied_message"] = (
        f"검토한 {len(review['annual_rows'])}개 사업연도의 순손익액·연도 말 주식수를 반영했습니다. "
        "주가평가를 다시 계산한 후 ‘평가자료 저장’을 눌러 반영 근거와 함께 저장해주세요."
    )


def _render_stock_tax_adjustment(user_id: str) -> None:
    from enterprise_documents import load_enterprise_document_context
    from tax_adjustment_valuation import (
        COMPONENTS, LAW_BASE_DATE, build_tax_adjustment_candidates, calculate_tax_adjusted_income,
    )

    st.markdown("### 세무조정계산서 반영")
    st.caption(f"법령 확인 기준: {LAW_BASE_DATE} · 금액 단위: 원 · 원본자료는 변경하지 않습니다.")
    st.info(
        "각 사업연도소득에 상증세법 시행령 제56조의 가산·차감 항목을 반영합니다. "
        "과세표준·회계상 당기순이익·법인세비용을 법정 순손익액으로 그대로 사용하지 않습니다."
    )
    business_no = _normalize_business_no(st.session_state.get("stock_business_no", ""))
    if len(re.sub(r"\D", "", business_no)) != 10:
        st.warning("기업컨설팅에서 자료를 연결한 기업을 먼저 선택해주세요.")
        return
    valuation_date = str(st.session_state.get("stock_valuation_date") or date.today().isoformat())
    target_years = [str(st.session_state.get(f"stock_year_{index}", "")) for index in range(1, 4)]
    st.caption("적용 대상: " + " / ".join(f"{year or '미설정'} 사업연도" for year in target_years))
    st.caption("평가기준일·최근 3개 사업연도는 ‘주가평가 계산’ 탭에서 먼저 설정합니다. 일부 연도만 반영하면 나머지 입력값은 유지됩니다.")
    try:
        context = load_enterprise_document_context(
            user_id, business_no, st.session_state.get("stock_company_name", ""),
            include_remote_tax=True,
        )
        candidates = build_tax_adjustment_candidates(context.get("records", []), valuation_date)
    except Exception as exc:
        st.error(safe_public_error(exc, "연결된 세무조정 자료를 불러오지 못했습니다."))
        return
    if not candidates:
        st.info("반영할 사업연도가 확인된 세무조정계산서가 없습니다. 기업정보등록에서 자료를 올리고 사업연도를 확인해주세요.")
        return
    year_map = {int(item["year"]): item for item in candidates}
    years = sorted(year_map, reverse=True)
    scope = f"{user_id}:{business_no}"
    selected_years = st.multiselect(
        "반영할 사업연도 선택", years, default=[], key=f"stock_tax_years:{scope}",
        format_func=lambda year: f"{year} 사업연도",
    )
    if not selected_years:
        st.caption("자료를 검토할 사업연도를 선택하세요. 업로드만으로 기존 평가값이 바뀌지 않습니다.")
        return
    selected = [year_map[year] for year in sorted(selected_years, reverse=True)]
    revision = "|".join(
        str(source.get("record_id", "")) + str(source.get("uploaded_at", ""))
        for candidate in selected for source in candidate.get("sources", [])
    )
    import hashlib
    token = hashlib.sha256((
        scope + revision + repr(selected_years) + valuation_date + repr(target_years)
        + repr([(item.get("values"), item.get("period_start"), item.get("period_end"), item.get("identity_review_required")) for item in selected])
    ).encode()).hexdigest()[:16]
    component_labels = {item["key"]: item["label"] for item in COMPONENTS}
    for candidate in selected:
        with st.expander(f"{candidate['year']} 사업연도 · 원본 및 추출 근거", expanded=False):
            st.dataframe(pd.DataFrame(candidate.get("sources", [])), hide_index=True, use_container_width=True)
            for warning in candidate.get("warnings", []):
                st.warning(str(warning))
            if candidate.get("conflicts"):
                st.warning("충돌·중복 확인: " + ", ".join(component_labels.get(key, key) for key in candidate["conflicts"]))
            evidence_rows = []
            source_ids = {source.get("record_id") for source in candidate.get("sources", [])}
            for record in context.get("records", []):
                if record.get("record_id") not in source_ids:
                    continue
                for period in (record.get("extracted_fields") or {}).get("valuation_periods", []):
                    if str(period.get("year")) != str(candidate["year"]):
                        continue
                    for key, value in period.get("values", {}).items():
                        evidence = period.get("evidence", {}).get(key, [])
                        evidence_rows.append({
                            "원본": record.get("filename", ""), "항목": component_labels.get(key, key),
                            "추출금액(원)": _format_number(value),
                            "인식 근거": "; ".join(f"{item.get('line', '?')}행 · {item.get('unit', '단위 미확인')}" for item in evidence),
                        })
            if evidence_rows:
                st.dataframe(pd.DataFrame(evidence_rows), hide_index=True, use_container_width=True)
            financial = candidate.get("financial_fields", {})
            if financial:
                st.dataframe(pd.DataFrame([{"참고항목": key, "금액": _format_number(value)} for key, value in financial.items()]), hide_index=True, use_container_width=True)
    st.warning(
        "공란은 0원이 아닙니다. 원문을 확인해 모든 법정 항목을 입력하고, 해당 없음은 0을 명시하세요. "
        "동일 연도 자료 간 충돌은 원문을 대조해 직접 해결해야 합니다."
    )
    st.caption(
        "차감세액 합계는 회계상 법인세비용이나 결정세액 한 항목이 아닙니다. "
        "시행령 제56조제4항제2호가목의 법인세액, 해당 외국법인세액, 법인세 감면액, "
        "과세표준에 부과되는 농어촌특별세액 및 지방소득세액을 원문과 대조해 검토합니다."
    )
    component_rows = []
    for component in COMPONENTS:
        row = {"법정 항목": component["label"], "법령": component["legal_ref"]}
        for candidate in selected:
            row[str(candidate["year"])] = _format_number(candidate.get("values", {}).get(component["key"]))
        component_rows.append(row)
    edited_components = st.data_editor(
        pd.DataFrame(component_rows), hide_index=True, use_container_width=True,
        disabled=["법정 항목", "법령"], key=f"stock_tax_components:{token}",
    )
    st.markdown("#### 사업연도·연도 말 발행주식수 확인")
    period_rows = []
    for candidate in selected:
        year = str(candidate["year"])
        index = target_years.index(year) + 1 if year in target_years else None
        period_rows.append({
            "사업연도": year, "시작일": candidate.get("period_start") or "",
            "종료일": candidate.get("period_end") or "",
            "연도 말·법정 환산 주식수": str(st.session_state.get(f"stock_shares_{index}", "")) or "" if index else "",
        })
    edited_periods = st.data_editor(
        pd.DataFrame(period_rows), hide_index=True, use_container_width=True,
        disabled=["사업연도"], key=f"stock_tax_periods:{token}",
    )
    values_by_year = {
        int(candidate["year"]): {component["key"]: edited_components.iloc[index][str(candidate["year"])] for index, component in enumerate(COMPONENTS)}
        for candidate in selected
    }
    periods_by_year = {
        int(row["사업연도"]): {"period_start": row["시작일"], "period_end": row["종료일"], "shares": row["연도 말·법정 환산 주식수"]}
        for _, row in edited_periods.iterrows()
    }
    preview = []
    for candidate in selected:
        calculation = calculate_tax_adjusted_income(values_by_year[int(candidate["year"])])
        preview.append({"사업연도": candidate["year"], "조정 후 순손익액": _format_number(calculation.get("adjusted_net_income")), "미확인 항목 수": len(calculation.get("missing", [])), "부호 확인 항목 수": len(calculation.get("invalid", []))})
    st.dataframe(pd.DataFrame(preview), hide_index=True, use_container_width=True)
    st.caption("과거 재무상태표의 총자산·총부채는 참고만 표시합니다. 평가기준일 현재 자산 시가·부채 조정과 영업권은 계산 탭에서 별도로 검토합니다.")
    reviewed = st.checkbox(
        "원문·연결 기업·각 사업연도소득·모든 법정 가산차감 항목과 충돌 내용을 확인했습니다.",
        key=f"stock_tax_review:{token}",
    )
    special_review = st.selectbox(
        "사업연도·증감자 등 특수 조정 검토", [
            "검토 상태를 선택하세요",
            "12개월 사업연도이며 증감자·일시 환입 등 별도 조정 사유 없음",
            "증감자 환산·순손익 조정의 전문가 검토 완료",
        ], key=f"stock_tax_special:{token}",
    )
    normal_periods = special_review == "12개월 사업연도이며 증감자·일시 환입 등 별도 조정 사유 없음"
    expert_reviewed = special_review == "증감자 환산·순손익 조정의 전문가 검토 완료"
    expert_note = ""
    if expert_reviewed:
        st.warning("이 화면은 증감자 산식을 자동 계산하지 않습니다. 위 금액과 주식수를 법정 조정이 완료된 값으로 직접 수정해야 합니다. 1년 미만·초과 사업연도는 이 반영 기능을 사용하지 않고 별도 연환산 검토 후 계산 탭에 수동 입력합니다.")
        expert_note = st.text_area("전문가 검토 근거 · 필수", key=f"stock_tax_expert_note:{token}", placeholder="증자일·납입가액·환산주식수·월할 계산·연환산 등 검토 근거와 확인자를 기록하세요.")
        final_review = st.data_editor(
            pd.DataFrame([{"사업연도": str(item["year"]), "전문가 최종 순손익액(원)": "", "전문가 확정 환산주식수": ""} for item in selected]),
            hide_index=True, use_container_width=True, disabled=["사업연도"], key=f"stock_tax_final_review:{token}",
        )
        for _, row in final_review.iterrows():
            periods_by_year[int(row["사업연도"])].update({
                "expert_final_income": row["전문가 최종 순손익액(원)"],
                "expert_final_shares": row["전문가 확정 환산주식수"],
            })
        st.caption("전문가 최종 순손익액은 위 법정 구성항목 합계와 일치해야 합니다. 빈 확정값은 자동으로 채우지 않습니다.")
    identity_reviewed = False
    if any(item.get("identity_review_required") for item in selected):
        identity_reviewed = st.checkbox(
            "복수 사업자번호가 포함된 원문에서 선택 기업에 속하는 사업연도와 금액만 추출되었음을 확인했습니다.",
            key=f"stock_tax_identity:{token}",
        )
    law_reviewed = False
    if valuation_date > LAW_BASE_DATE:
        law_reviewed = st.checkbox(
            f"법령 확인 기준일({LAW_BASE_DATE}) 이후 평가기준일까지의 법령 변경 여부와 적용요건을 확인했습니다.",
            key=f"stock_tax_law_review:{token}",
        )
    st.caption(
        "최근 3개 사업연도 선정과 연도 말 주식수를 확인하세요. 증감자 조정액 또는 일시환입 안분액이 "
        "0원이 아니면 전문가 검토가 필수입니다. 특수 조정은 자동 판단하지 않으며, 검토 근거를 평가자료와 함께 저장합니다."
    )
    can_apply = reviewed and (normal_periods or (expert_reviewed and bool(expert_note.strip())))
    if valuation_date > LAW_BASE_DATE:
        can_apply = can_apply and law_reviewed
    if any(item.get("identity_review_required") for item in selected):
        can_apply = can_apply and identity_reviewed
    if st.button("검토한 순손익액·주식수 반영", type="primary", use_container_width=True, disabled=not can_apply, key=f"stock_tax_apply:{token}"):
        try:
            updates, review = _prepare_tax_adjustment_application(
                business_no=business_no, valuation_date=valuation_date, target_years=target_years,
                candidates=selected, reviewed_values=values_by_year, reviewed_periods=periods_by_year,
                review_confirmed=reviewed, normal_periods_confirmed=normal_periods,
                special_adjustments_reviewed=expert_reviewed, special_adjustment_note=expert_note,
                law_changes_reviewed=law_reviewed,
                identity_confirmed=identity_reviewed,
            )
            _apply_reviewed_tax_adjustment(updates, review, user_id=user_id)
        except ValueError as exc:
            st.error(str(exc))
        except Exception as exc:
            st.error(safe_public_error(exc, "세무조정계산서 반영을 완료하지 못했습니다."))
        else:
            st.success(st.session_state["stock_tax_adjustment_applied_message"])
    st.markdown("[순손익액·주식수 법령: 상속세 및 증여세법 시행령 제56조](https://www.law.go.kr/법령/상속세및증여세법시행령/제56조)")


def render_stock_valuation_page(
    user_id: str,
    user_name: str = "",
    *,
    selected_business_no: Any = "",
    selected_company_name: Any = "",
) -> None:
    st.markdown("## 주가평가")
    st.caption(
        "비상장주식 보충적 평가를 위한 상담용 계산 화면입니다. "
        "기존 고객DB는 읽기만 하며 평가자료는 별도 파일에 저장합니다."
    )

    st.info(
        "세법상 순손익액과 순자산가액은 회계상 당기순이익·장부가액과 다를 수 있습니다. "
        "크레탑 값은 초안으로 불러오며, 노란색 안내 항목은 사용자가 확인·수정해야 합니다."
    )

    customers = _read_customers(user_id)
    _load_stock_customer_context(
        user_id,
        customers,
        selected_business_no=selected_business_no,
        selected_company_name=selected_company_name,
    )

    view = st.radio(
        "주가평가 세부 탭", ["주가평가 계산", "세무조정계산서 반영"], horizontal=True,
        key="stock_valuation_detail_view", label_visibility="collapsed",
    )
    if view == "세무조정계산서 반영":
        _render_stock_tax_adjustment(user_id)
        return
    applied_message = st.session_state.get("stock_tax_adjustment_applied_message", "")
    if applied_message:
        st.success(applied_message)

    with st.form(
        "stock_valuation_input_form_v855",
        clear_on_submit=False,
    ):
        st.markdown("### 1. 기본정보")
        b1, b2 = st.columns(2)

        with b1:
            company_name = st.text_input(
                "법인명",
                key="stock_company_name",
            )
            business_no = st.text_input(
                "사업자등록번호",
                key="stock_business_no",
            )
            corporate_no = st.text_input(
                "법인등록번호",
                key="stock_corporate_no",
            )
            address = st.text_area(
                "본점소재지",
                key="stock_address",
                height=90,
            )
            establishment = st.text_input(
                "법인설립일",
                key="stock_establishment",
                placeholder="YYYY-MM-DD",
            )

        with b2:
            valuation_date = st.text_input(
                "평가기준일",
                value=st.session_state.get(
                    "stock_valuation_date",
                    date.today().isoformat(),
                ),
                key="stock_valuation_date",
            )
            current_shares_text = st.text_input(
                "현재 발행주식총수(주)",
                key="stock_current_shares",
                placeholder="크레탑에 없으면 법인등기·주주명부를 확인",
            )
            par_value_text = st.text_input(
                "1주당 액면가액(원)",
                key="stock_par_value",
            )
            capital_text = st.text_input(
                "등기상 자본금(원)",
                key="stock_capital",
                placeholder="등기자료 업로드 시 자동 입력",
            )
            authorized_shares_text = st.text_input(
                "발행할 주식의 총수(주)",
                key="stock_authorized_shares",
                placeholder="등기자료 업로드 시 자동 입력",
            )
            valuation_type = st.selectbox(
                "평가유형",
                [
                    "일반법인(3:2)",
                    "부동산과다보유법인(2:3)",
                    "순자산가치만 적용",
                ],
                key="stock_valuation_type",
            )

        st.markdown("### 2. 최근 3개년 순손익액")
        if st.session_state.get("stock_tax_adjustment_review"):
            st.info(
                "세무조정계산서 검토 반영 이력이 있습니다. 반영하지 않은 연도와 직접 수정한 값은 "
                "별도로 재확인하고, 평가자료를 저장하면 원본 연결·검토 근거도 함께 보존합니다."
            )
        else:
            st.warning(
                "자동 입력값은 회계상 당기순이익입니다. "
                "상증세법상 가산·차감 조정을 반영한 순손익액으로 직접 수정해주세요."
            )

        current_year = datetime.now().year - 1
        annual_rows = []
        annual_columns = st.columns(3)

        for index, column in enumerate(annual_columns, start=1):
            with column:
                year_key = f"stock_year_{index}"
                _ensure_text_state(
                    year_key,
                    str(current_year - index + 1),
                )
                year = st.text_input(
                    f"{index}번째 연도",
                    key=year_key,
                )
                net_income_key = f"stock_net_income_{index}"
                shares_key = f"stock_shares_{index}"
                _ensure_text_state(net_income_key, "")
                _ensure_text_state(shares_key, "")

                adjusted_net_income = st.text_input(
                    "세법상 조정 후 순손익액(원)",
                    key=net_income_key,
                    placeholder="공란이면 직접 입력",
                )
                shares = st.text_input(
                    "해당 연도 발행주식총수(주)",
                    key=shares_key,
                    placeholder="공란이면 현재 주식수 적용",
                )
                annual_rows.append({
                    "year": year,
                    "adjusted_net_income": _safe_number(adjusted_net_income),
                    "shares": _safe_number(shares),
                })

        st.markdown("### 3. 순자산가액 및 세법상 조정")
        n1, n2 = st.columns(2)

        with n1:
            total_assets_text = st.text_input(
                "장부상 총자산(원)",
                key="stock_total_assets",
            )
            asset_additions_text = st.text_input(
                "자산 가산조정(원)",
                key="stock_asset_additions",
                placeholder="시가평가 증액, 누락자산 등",
            )
            asset_deductions_text = st.text_input(
                "자산 차감조정(원)",
                key="stock_asset_deductions",
                placeholder="회수불능자산, 평가차감 등",
            )
            goodwill_text = st.text_input(
                "영업권 수동 평가액(원)",
                key="stock_goodwill",
                placeholder="수동입력 모드에서만 반영",
            )

        with n2:
            total_liabilities_text = st.text_input(
                "장부상 총부채(원)",
                key="stock_total_liabilities",
            )
            liability_additions_text = st.text_input(
                "부채 가산조정(원)",
                key="stock_liability_additions",
                placeholder="미계상 채무, 퇴직급여 등",
            )
            liability_deductions_text = st.text_input(
                "부채 차감조정(원)",
                key="stock_liability_deductions",
            )
            other_adjustments_text = st.text_input(
                "기타 순자산 조정(원)",
                key="stock_other_adjustments",
                placeholder="가산은 양수, 차감은 음수",
            )

        with st.expander("영업권 자동계산 · 수동입력 설정", expanded=True):
            if "stock_goodwill_mode" not in st.session_state:
                st.session_state["stock_goodwill_mode"] = "auto"
            goodwill_mode = st.selectbox(
                "영업권 반영 방식",
                list(GOODWILL_MODE_LABELS),
                format_func=GOODWILL_MODE_LABELS.get,
                key="stock_goodwill_mode",
            )
            st.caption(
                "자동계산: MAX((3개년 회사 전체 순손익 가중평균 × 50% − "
                "영업권 가산 전 조정 순자산 × 10%) × 3.7908 − 매입 무체재산권 잔액, 0). "
                "최신 연도부터 3:2:1 가중, 5년·10% 할인계수는 원본 엑셀과 같습니다."
            )
            _ensure_text_state("stock_goodwill_acquired_intangible_remaining", "0")
            acquired_intangible_text = st.text_input(
                "순자산에 이미 포함된 영업권 해당 매입 무체재산권 미상각잔액(원)",
                key="stock_goodwill_acquired_intangible_remaining",
                help="해당 없음은 0원. 자동계산 모드에서만 반영하며, 일반 무형자산 전체를 입력하지 않습니다.",
            )
            _ensure_text_state("stock_goodwill_exclusion_reason", "")
            goodwill_exclusion_reason = st.text_input(
                "영업권 불산입 검토 사유",
                key="stock_goodwill_exclusion_reason",
                placeholder="불산입 모드에서 필수 입력",
            )
            st.caption(
                "위 잔액의 기본 0원은 '해당 없음' 가정입니다. 매입 무체재산권이 있으면 확인해 수정하세요. "
                "수동 평가액은 수동입력 모드에서만, 불산입 사유는 불산입 모드에서만 사용합니다. "
                "변경사항은 '주가평가 계산'을 눌러 반영합니다."
            )
            st.warning(
                "자동계산은 상담용 참고값입니다. 업력 3년 미만·휴폐업·계속 결손·부동산 비중 등 "
                "영업권 불산입 요건은 별도 검토하고, 해당하면 불산입을 선택하세요. "
                "평가유형의 '순자산가치만 적용'만으로 영업권을 자동 제외하지 않습니다."
            )

        st.markdown("### 4. 법령 적용 설정")
        l1, l2 = st.columns(2)

        with l1:
            cap_rate_pct = st.number_input(
                "순손익가치 환원율(%)",
                min_value=0.1,
                max_value=100.0,
                value=float(
                    st.session_state.get(
                        "stock_cap_rate",
                        CAPITALIZATION_RATE_DEFAULT,
                    )
                    * 100
                ),
                step=0.1,
                key="stock_cap_rate_pct",
            )
            nav_floor_pct = st.number_input(
                "순자산가치 하한 비율(%)",
                min_value=0.0,
                max_value=100.0,
                value=float(
                    st.session_state.get(
                        "stock_nav_floor",
                        NAV_FLOOR_RATE_DEFAULT,
                    )
                    * 100
                ),
                step=1.0,
                key="stock_nav_floor_pct",
            )

        with l2:
            saved_premium_exempt = bool(
                st.session_state.get("stock_premium_exempt", True)
            )
            saved_premium_pct = float(
                st.session_state.get(
                    "stock_premium_rate",
                    MAX_SHAREHOLDER_PREMIUM_DEFAULT,
                )
                * 100
            )

            if saved_premium_exempt or saved_premium_pct == 0:
                premium_default_label = "미적용"
            elif saved_premium_pct in (10.0, 15.0, 20.0):
                premium_default_label = f"{int(saved_premium_pct)}% 적용"
            else:
                premium_default_label = "직접입력"

            premium_options = [
                "미적용",
                "10% 적용",
                "15% 적용",
                "20% 적용",
                "직접입력",
            ]
            premium_default_index = premium_options.index(
                premium_default_label
            )

            premium_selection = st.selectbox(
                "최대주주 할증평가",
                premium_options,
                index=premium_default_index,
                key="stock_premium_selection",
                help=(
                    "중소기업 해당 여부, 평가기준일 및 최대주주 요건을 "
                    "확인한 뒤 선택합니다."
                ),
            )

            if premium_selection == "직접입력":
                premium_pct = st.number_input(
                    "직접 입력 할증률(%)",
                    min_value=0.0,
                    max_value=100.0,
                    value=saved_premium_pct,
                    step=1.0,
                    key="stock_premium_custom_pct",
                )
            elif premium_selection == "미적용":
                premium_pct = 0.0
            else:
                premium_pct = float(
                    premium_selection.replace("% 적용", "")
                )

            premium_exempt = premium_selection == "미적용"
            if premium_exempt:
                st.caption("최종 평가액에는 최대주주 할증을 적용하지 않습니다.")
            else:
                st.caption(
                    f"할증 전 평가액에 {premium_pct:.0f}%를 적용합니다."
                )

        inputs = {
            "company_name": company_name,
            "business_no": business_no,
            "corporate_no": corporate_no,
            "address": address,
            "establishment": establishment,
            "valuation_date": valuation_date,
            "current_shares": _safe_number(current_shares_text),
            "par_value": _safe_number(par_value_text),
            "capital": _safe_number(capital_text),
            "authorized_shares": _safe_number(authorized_shares_text),
            "share_classes": st.session_state.get("stock_share_classes", []),
            "valuation_type": valuation_type,
            "annual_rows": annual_rows,
            "total_assets": _safe_number(total_assets_text),
            "total_liabilities": _safe_number(total_liabilities_text),
            "asset_additions": _safe_number(asset_additions_text),
            "asset_deductions": _safe_number(asset_deductions_text),
            "liability_additions": _safe_number(liability_additions_text),
            "liability_deductions": _safe_number(liability_deductions_text),
            "goodwill": _safe_number(goodwill_text),
            "goodwill_mode": goodwill_mode,
            "goodwill_acquired_intangible_remaining": _safe_number(acquired_intangible_text),
            "goodwill_exclusion_reason": goodwill_exclusion_reason,
            "other_adjustments": _safe_number(other_adjustments_text),
            "capitalization_rate": cap_rate_pct / 100,
            "nav_floor_rate": nav_floor_pct / 100,
            "premium_exempt": premium_exempt,
            "premium_rate": premium_pct / 100,
            "tax_adjustment_review": deepcopy(st.session_state.get("stock_tax_adjustment_review", [])),
        }

        capital_number = _safe_number(capital_text)
        issued_shares_number = _safe_number(current_shares_text)
        par_value_number = _safe_number(par_value_text)

        if capital_number and issued_shares_number and par_value_number:
            calculated_capital = issued_shares_number * par_value_number
            difference = abs(calculated_capital - capital_number)
            tolerance = max(1, capital_number * 0.01)
            if difference > tolerance:
                st.warning(
                    "등기상 자본금과 `발행주식총수 × 1주당 액면가액`이 일치하지 않습니다. "
                    "변경등기 이력이나 종류주식 여부를 확인해주세요."
                )
            else:
                st.success(
                    "등기상 자본금과 발행주식수·액면가액 계산값이 일치합니다."
                )

        calculate_clicked = st.form_submit_button(
            "주가평가 계산",
            type="primary",
            use_container_width=True,
        )

    result = None
    if calculate_clicked:
        calculation_status = st.status(
            "주가평가 중입니다...",
            expanded=True,
            state="running",
        )
        try:
            calculation_status.write("입력값과 발행주식수를 확인하고 있습니다.")
            calculation_status.write(
                "순손익가치와 순자산가치를 계산하고 있습니다."
            )
            result = calculate_stock_valuation(inputs)
            st.session_state["stock_last_result"] = deepcopy(result)
            st.session_state["stock_last_inputs"] = deepcopy(inputs)
            calculation_status.update(
                label="주가평가가 완료되었습니다.",
                state="complete",
                expanded=False,
            )
        except ValueError as exc:
            st.session_state.pop("stock_last_result", None)
            st.session_state.pop("stock_last_inputs", None)
            calculation_status.update(
                label="주가평가를 완료하지 못했습니다.",
                state="error",
                expanded=True,
            )
            st.error(str(exc))
        except Exception as exc:
            st.session_state.pop("stock_last_result", None)
            st.session_state.pop("stock_last_inputs", None)
            calculation_status.update(
                label="주가평가 중 오류가 발생했습니다.",
                state="error",
                expanded=True,
            )
            st.error(safe_public_error(exc, "주가평가 중 오류가 발생했습니다."))

    if result is None:
        result = _matching_stock_result(inputs)
        if result is None and st.session_state.get("stock_last_result"):
            st.info("입력값이 변경되었습니다. 다시 계산하면 평가결과를 저장할 수 있습니다.")

    if result:
        st.markdown("### 5. 평가결과")
        if result["raw_weighted_per_share_income"] < 0 or result["raw_adjusted_net_assets"] < 0:
            st.warning(
                "음수인 순손익 가중평균 또는 최종 조정 순자산에는 세법상 0원 하한을 적용했습니다. "
                "영업권·결손 및 평가유형 적용 요건을 별도 검토해주세요."
            )
        c1, c2, c3 = st.columns(3)
        c1.metric(
            "1주당 순손익가치",
            f"{result['earnings_value_per_share']:,.0f}원",
        )
        c2.metric(
            "1주당 순자산가치",
            f"{result['nav_per_share']:,.0f}원",
        )
        c3.metric(
            "1주당 최종 평가액",
            f"{result['final_value_per_share']:,.0f}원",
        )

        c4, c5, c6 = st.columns(3)
        c4.metric(
            "가중평균 평가액",
            f"{result['weighted_value']:,.0f}원",
        )
        c5.metric(
            "순자산가치 80% 하한",
            f"{result['nav_floor']:,.0f}원",
        )
        c6.metric(
            "총 주식가치",
            f"{result['total_equity_value']:,.0f}원",
        )

        summary_rows = [
            ["순손익 가중평균액(1주당)", result["weighted_per_share_income"]],
            ["순손익가치 환원 후(1주당)", result["earnings_value_per_share"]],
            ["영업권 가산 전 조정 순자산", result["net_assets_before_goodwill"]],
            ["이번 평가에 가산한 영업권", result["goodwill"]],
            ["조정 순자산가액", result["adjusted_net_assets"]],
            ["순자산가치(1주당)", result["nav_per_share"]],
            ["법인유형 가중평균액(1주당)", result["weighted_value"]],
            ["순자산가치 하한액(1주당)", result["nav_floor"]],
            ["할증 전 평가액(1주당)", result["basic_value_per_share"]],
            ["최종 평가액(1주당)", result["final_value_per_share"]],
            ["기업 전체 주식가치", result["total_equity_value"]],
        ]
        summary_df = pd.DataFrame(
            summary_rows,
            columns=["구분", "금액"],
        )
        summary_df["금액"] = summary_df["금액"].map(
            lambda value: f"{value:,.0f}원"
        )
        st.dataframe(
            summary_df,
            hide_index=True,
            use_container_width=True,
        )

        st.caption(f"영업권 반영 방식: {GOODWILL_MODE_LABELS[result['goodwill_mode']]}")
        goodwill_details = result.get("goodwill_details")
        if goodwill_details:
            with st.expander("영업권 계산 근거", expanded=False):
                goodwill_rows = [
                    ["3개년 순손익 가중평균(회사 전체)", goodwill_details["weighted_income"]],
                    ["영업권 가산 전 자기자본", goodwill_details["equity_before_goodwill"]],
                    ["연간 초과이익", goodwill_details["excess_earnings"]],
                    ["5년 할인액(계수 3.7908)", goodwill_details["discounted_value"]],
                    ["중복 가산 방지용 매입 무체재산권 잔액", goodwill_details["acquired_intangible_remaining"]],
                    ["순자산에 추가 가산할 영업권", goodwill_details["amount"]],
                ]
                goodwill_df = pd.DataFrame(goodwill_rows, columns=["항목", "금액"])
                goodwill_df["금액"] = goodwill_df["금액"].map(lambda value: f"{value:,.0f}원")
                st.dataframe(goodwill_df, hide_index=True, use_container_width=True)
                for warning in goodwill_details["warnings"]:
                    st.warning(warning)
                st.caption(
                    "5년·10%의 정밀 현가계수는 약 3.790787이지만, "
                    "원본 엑셀과 비교할 수 있도록 3.7908을 사용합니다. "
                    "매입 무체재산권 원래 금액은 기존 자산에 유지하고 중복 가산분만 차감합니다."
                )

        st.caption("저장은 마지막으로 성공한 계산의 입력값·영업권·결과를 함께 보존합니다.")
        save_label = (
            "수정 저장"
            if st.session_state.get("stock_loaded_record_id")
            else "평가자료 저장"
        )

        if st.button(
            save_label,
            key="stock_save_record",
            use_container_width=True,
        ):
            records = _load_records(user_id)
            loaded_id = st.session_state.get("stock_loaded_record_id", "")
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            record = _build_stock_record(
                st.session_state["stock_last_inputs"],
                result,
                record_id=loaded_id or datetime.now().strftime("%Y%m%d%H%M%S%f"),
                saved_by=user_name,
                saved_at=now,
            )

            replaced = False
            for idx, saved_record in enumerate(records):
                if saved_record.get("record_id") == record["record_id"]:
                    records[idx] = record
                    replaced = True
                    break
            if not replaced:
                records.insert(0, record)

            _save_records(user_id, records)
            sync_stock_valuation(user_id, record)
            _load_cloud_records_cached.clear()
            st.session_state["stock_loaded_record_id"] = record["record_id"]
            st.success(
                "주가평가 자료를 로컬 파일과 Supabase에 동시 저장했습니다."
            )

    with st.expander("평가 로직과 법령 적용 안내", expanded=False):
        st.markdown(
            f"""
**법령 검토 기준일:** {LAW_BASE_DATE}

- 일반 비상장법인: 순손익가치와 순자산가치를 **3:2**로 가중
- 부동산과다보유법인: **2:3**으로 가중
- 가중평균액이 순자산가치의 **80%**보다 낮으면 80% 하한 적용
- 순손익가치 환원율 기본값: **10%**
- 최대주주 할증: 적용대상인 경우 기본 **20%**, 중소기업 등 제외요건은 별도 확인
- 시행령 제54조제4항 요건을 검토하여 순자산가치만 적용하는 경우는 사용자가 선택
- 순손익 가중평균과 영업권 가산 후 최종 순자산가액이 음수이면 각각 0원 하한 적용
- 영업권 자동계산 검토 기준일: {GOODWILL_LAW_BASE_DATE}. 회사 전체 순손익을 사용하며 신규 영업권 자체를 자기자본에 다시 넣지 않습니다.
- 기본 엑셀 `영업권` 시트의 3:2:1 가중, 50%, 10%, 5년 계수 3.7908을 적용합니다. `영업권3`의 다른 수식은 사용하지 않습니다.
- 순손익 가중평균이 음수이면 영업권 계산에는 0원 적용. 자기자본이 음수인 경우 자동계산을 차단하고 별도 검토를 요청합니다.
- 영업권 불산입 대상 여부는 자동 확정하지 않습니다. 불산입 모드는 사유를 함께 기록하며 기존 수동입력도 유지합니다.

**참고 법령**
- 상속세 및 증여세법 제63조
- 상속세 및 증여세법 시행령 제54조·제55조·제56조·제59조
- 국가법령정보센터: https://www.law.go.kr
- [영업권 산식·회사 전체 순손익: 시행령 제59조](https://www.law.go.kr/법령/상속세및증여세법시행령/제59조)
- [영업권 가산 제외: 시행령 제55조](https://www.law.go.kr/법령/상속세및증여세법시행령/제55조)
- [순손익: 시행령 제56조](https://www.law.go.kr/법령/상속세및증여세법시행령/제56조)
- [현가 할인 방법: 시행규칙 제16조](https://www.law.go.kr/법령/상속세및증여세법시행규칙/제16조)

이 계산은 상담용 사전평가입니다. 실제 신고평가는 평가기준일의 법령,
세법상 순손익 조정, 자산·부채 시가조정, 영업권, 최대주주 할증 제외요건을
개별 검토해야 합니다.
"""
        )

    _render_stock_history(user_id)
