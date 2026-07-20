from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from cretop_runner import run_cretop_worker
from registry_runner import run_registry_worker
from cloud_sync import (
    sync_financial_snapshot,
    sync_registry_snapshot,
    sync_stock_valuation,
    load_financial_snapshot,
    load_registry_snapshot,
)
from utils import get_user_cumulative_db_path, get_user_dirs


LAW_BASE_DATE = "2026-07-13"
CAPITALIZATION_RATE_DEFAULT = 0.10
NAV_FLOOR_RATE_DEFAULT = 0.80
MAX_SHAREHOLDER_PREMIUM_DEFAULT = 0.20


def _safe_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text.lower() in {"", "none", "nan", "nat", "-"}:
        return None
    try:
        return float(text)
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
    Existing customer DB is never modified.
    """
    data = _normalize_financial_snapshot(extracted_data or {})
    business_no = _normalize_business_no(
        data.get("사업자등록번호", data.get("사업자번호", ""))
    )
    if len(re.sub(r"[^0-9]", "", business_no)) != 10:
        return False

    cache = _load_financial_cache(user_id)
    cache[business_no] = {
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

    st.session_state["stock_company_name"] = str(defaults.get("법인명", "") or "")
    st.session_state["stock_business_no"] = str(
        defaults.get("사업자등록번호", "") or ""
    )
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

    business_no = _normalize_business_no(defaults.get("사업자등록번호", ""))
    cache = _load_financial_cache(user_id)
    snapshot = cache.get(business_no, {}) if business_no else {}
    if not snapshot and business_no:
        snapshot = load_financial_snapshot(user_id, business_no)
        if snapshot:
            snapshot = _normalize_financial_snapshot(snapshot)
            cache[business_no] = snapshot
            _save_financial_cache(user_id, cache)

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
        history = sorted(
            [item for item in history if isinstance(item, dict)],
            key=lambda item: item.get("연도", 0),
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


def _load_records(user_id: str) -> list[dict[str, Any]]:
    path = _storage_path(user_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_records(user_id: str, records: list[dict[str, Any]]) -> None:
    path = _storage_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _read_customers(user_id: str) -> pd.DataFrame:
    path = get_user_cumulative_db_path(user_id)
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_excel(path, sheet_name="고객DB")
    except Exception:
        return pd.DataFrame()

    if df.empty:
        return df

    df = df.dropna(how="all").copy()
    return df


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
        "사업자등록번호": get("사업자등록번호"),
        "법인등록번호": get("법인등록번호"),
        "본점소재지": get("사업장 소재지", "본점소재지", "주소"),
        "설립일": get("설립일", "설립년도"),
        "총자산": get("자산총계", "총자산"),
        "총부채": get("부채총계", "총부채"),
        "자본총계": get("자본총계"),
        "최근당기순이익": get("당기순이익"),
    }


def _record_label(record: dict[str, Any]) -> str:
    name = record.get("company_name", "회사명 없음")
    base_date = record.get("valuation_date", "")
    saved_at = record.get("saved_at", "")
    return f"{name} · 기준일 {base_date} · 저장 {saved_at}"


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

    weighted_per_share_income = weighted_sum / 6
    earnings_value_per_share = weighted_per_share_income / capitalization_rate

    total_assets = _safe_number(inputs.get("total_assets"))
    total_liabilities = _safe_number(inputs.get("total_liabilities"))

    if total_assets is None or total_liabilities is None:
        raise ValueError("평가기준일 현재 총자산과 총부채를 입력해주세요.")

    asset_additions = _safe_number(inputs.get("asset_additions")) or 0
    asset_deductions = _safe_number(inputs.get("asset_deductions")) or 0
    liability_additions = _safe_number(inputs.get("liability_additions")) or 0
    liability_deductions = _safe_number(inputs.get("liability_deductions")) or 0
    goodwill = _safe_number(inputs.get("goodwill")) or 0
    other_adjustments = _safe_number(inputs.get("other_adjustments")) or 0

    adjusted_assets = total_assets + asset_additions - asset_deductions
    adjusted_liabilities = (
        total_liabilities + liability_additions - liability_deductions
    )
    adjusted_net_assets = (
        adjusted_assets
        - adjusted_liabilities
        + goodwill
        + other_adjustments
    )
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
        "weighted_per_share_income": weighted_per_share_income,
        "earnings_value_per_share": earnings_value_per_share,
        "adjusted_assets": adjusted_assets,
        "adjusted_liabilities": adjusted_liabilities,
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


def _registry_cache_path(user_id: str) -> Path:
    dirs = get_user_dirs(user_id)
    return dirs["base"] / "registry_cache.json"


def _load_registry_cache(user_id: str) -> dict[str, Any]:
    path = _registry_cache_path(user_id)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_registry_cache(
    user_id: str,
    business_no: str,
    data: dict[str, Any],
) -> None:
    cache = _load_registry_cache(user_id)
    key = _normalize_business_no(business_no)
    if not key:
        key = str(data.get("법인등록번호", "") or "")
    if not key:
        return

    cache[key] = {
        **dict(data or {}),
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    path = _registry_cache_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    sync_registry_snapshot(user_id, business_no, cache[key])


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
    )
    st.session_state["stock_authorized_shares"] = _format_number(
        data.get("발행할주식총수")
    )
    st.session_state["stock_share_classes"] = data.get("주식종류", [])


def _restore_registry_for_business(
    user_id: str,
    business_no: Any,
) -> dict[str, Any]:
    # 재접속 시 로컬 또는 Supabase에서 등기 업로드 내역을 복원합니다.
    key = _normalize_business_no(business_no)
    if not key:
        return {}

    cache = _load_registry_cache(user_id)
    data = cache.get(key, {})
    if not data:
        data = load_registry_snapshot(user_id, key)
        if data:
            cache[key] = {
                **dict(data),
                "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            path = _registry_cache_path(user_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(cache, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

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


def _compare_values(label: str, left: Any, right: Any) -> dict[str, Any]:
    left_text = str(left or "").strip()
    right_text = str(right or "").strip()
    same = bool(left_text and right_text and left_text == right_text)
    return {
        "항목": label,
        "기존값": left_text,
        "등기값": right_text,
        "일치": "일치" if same else ("차이" if left_text and right_text else "확인"),
    }


def _apply_loaded_record(record: dict[str, Any]) -> None:
    st.session_state["stock_loaded_record_id"] = record.get("record_id", "")
    st.session_state["stock_company_name"] = record.get("company_name", "")
    st.session_state["stock_business_no"] = record.get("business_no", "")
    st.session_state["stock_corporate_no"] = record.get("corporate_no", "")
    st.session_state["stock_address"] = record.get("address", "")
    st.session_state["stock_establishment"] = record.get("establishment", "")
    st.session_state["stock_valuation_date"] = record.get(
        "valuation_date",
        date.today().isoformat(),
    )
    st.session_state["stock_current_shares"] = record.get("current_shares", "")
    st.session_state["stock_par_value"] = record.get("par_value", "")
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

    for index, row in enumerate(record.get("annual_rows", [])[:3], start=1):
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


def render_stock_valuation_page(user_id: str, user_name: str = "") -> None:
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
    selected_row = None

    if not customers.empty and "업체명" in customers.columns:
        labels = ["직접 입력"]
        row_map: dict[str, int] = {}

        for idx, row in customers.iterrows():
            name = str(row.get("업체명", "") or "").strip()
            business_no = _normalize_business_no(row.get("사업자등록번호", ""))
            label = f"{name} · {business_no}" if business_no else name
            if not label:
                continue
            labels.append(label)
            row_map[label] = idx

        selected_label = st.selectbox(
            "기존 고객에서 불러오기",
            labels,
            key="stock_customer_selector",
        )

        if selected_label != "직접 입력":
            selected_row = customers.loc[row_map[selected_label]]
            selector_key = f"{selected_label}:{row_map[selected_label]}"
            st.session_state["stock_last_customer_key"] = selector_key

            if st.button(
                "기존 고객DB에서 재무정보 불러오기",
                key="stock_load_customer_financials",
                use_container_width=True,
            ):
                loaded, message = _apply_customer_financial_data(
                    user_id,
                    selected_row,
                )
                if loaded:
                    _restore_registry_for_business(
                        user_id,
                        st.session_state.get("stock_business_no", ""),
                    )
                    st.success(message)
                    st.rerun()
                else:
                    st.error(message)

    with st.expander("크레탑 PDF에서 재무정보 불러오기", expanded=False):
        uploaded_pdf = st.file_uploader(
            "크레탑 PDF",
            type=["pdf"],
            key="stock_cretop_pdf",
        )
        if uploaded_pdf is not None and st.button(
            "크레탑 재무정보 분석",
            key="stock_analyze_pdf",
            use_container_width=True,
        ):
            dirs = get_user_dirs(user_id)
            pdf_path = (
                dirs["uploads"]
                / f"주가평가_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            )
            uploaded_pdf.seek(0)
            with open(pdf_path, "wb") as output:
                output.write(uploaded_pdf.read())

            with st.spinner("크레탑 보고서에서 최근 3개년 재무정보를 찾고 있습니다..."):
                data, error, logs = run_cretop_worker(
                    pdf_path,
                    mode="full",
                    timeout=180,
                )

            if error:
                st.error(error)
            else:
                data = _normalize_financial_snapshot(data)
                save_cretop_financial_snapshot(user_id, data)
                st.session_state["stock_company_name"] = data.get("업체명", "")
                st.session_state["stock_business_no"] = data.get(
                    "사업자등록번호", ""
                )
                st.session_state["stock_corporate_no"] = data.get(
                    "법인등록번호", ""
                )
                st.session_state["stock_address"] = data.get(
                    "사업장 소재지", ""
                )
                st.session_state["stock_establishment"] = data.get(
                    "설립일", ""
                )
                history = data.get("재무연도별", [])
                history = sorted(
                    [row for row in history if isinstance(row, dict)],
                    key=lambda row: row.get("연도", 0),
                    reverse=True,
                )
                latest_financial = history[0] if history else {}
                st.session_state["stock_total_assets"] = _format_number(
                    data.get("자산총계") or latest_financial.get("자산총계")
                )
                st.session_state["stock_total_liabilities"] = _format_number(
                    data.get("부채총계") or latest_financial.get("부채총계")
                )

                history = data.get("재무연도별", [])
                history = sorted(
                    history,
                    key=lambda row: row.get("연도", 0),
                    reverse=True,
                )[:3]
                for index, row in enumerate(history, start=1):
                    st.session_state[f"stock_year_{index}"] = str(row.get("연도", "") or "")
                    st.session_state[f"stock_net_income_{index}"] = _format_number(
                        row.get("당기순이익")
                    )

                st.success(
                    "크레탑 값을 불러왔습니다. 발행주식수와 세법상 조정사항을 확인해주세요."
                )
                st.rerun()

    with st.expander("법인 등기자료에서 주식정보 불러오기", expanded=False):
        registry_pdf = st.file_uploader(
            "법인 등기사항증명서 PDF",
            type=["pdf"],
            key="stock_registry_pdf",
        )
        st.caption(
            "법인명, 법인등록번호, 본점소재지, 설립일, 자본금, "
            "발행주식총수, 1주의 금액 등을 자동 추출합니다."
        )

        if registry_pdf is not None and st.button(
            "등기자료 분석",
            key="stock_analyze_registry",
            use_container_width=True,
        ):
            dirs = get_user_dirs(user_id)
            registry_path = (
                dirs["uploads"]
                / f"등기자료_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            )
            registry_pdf.seek(0)
            with open(registry_path, "wb") as output:
                output.write(registry_pdf.read())

            with st.spinner("등기자료에서 자본금과 주식정보를 찾고 있습니다..."):
                registry_data, registry_error = run_registry_worker(
                    registry_path,
                    timeout=90,
                )

            if registry_error:
                st.error(registry_error)
            else:
                preserved_financial_inputs = _capture_stock_financial_inputs()
                _apply_registry_data(registry_data)
                _restore_stock_financial_inputs(preserved_financial_inputs)
                _save_registry_cache(
                    user_id,
                    st.session_state.get("stock_business_no", ""),
                    registry_data,
                )
                st.session_state["stock_last_registry_data"] = registry_data
                issued = _format_number(registry_data.get("발행주식총수"))
                if issued:
                    for index in range(1, 4):
                        share_key = f"stock_shares_{index}"
                        if not st.session_state.get(share_key):
                            st.session_state[share_key] = issued
                st.success(
                    "등기자료를 저장했습니다. 기존 당기순이익과 재무 입력값은 유지됩니다."
                )
                st.rerun()

        registry_data = st.session_state.get("stock_last_registry_data", {})
        current_business_no = st.session_state.get("stock_business_no", "")
        restore_key = _normalize_business_no(current_business_no)
        if restore_key and st.session_state.get("stock_registry_restored_key") != restore_key:
            restored = _restore_registry_for_business(user_id, restore_key)
            st.session_state["stock_registry_restored_key"] = restore_key
            if restored:
                registry_data = restored
        if registry_data:
            st.markdown("#### 등기자료 추출값")
            registry_preview = pd.DataFrame([
                ["법인명", registry_data.get("법인명", "")],
                ["법인등록번호", registry_data.get("법인등록번호", "")],
                ["본점소재지", registry_data.get("본점소재지", "")],
                ["법인설립일", registry_data.get("법인설립일", "")],
                ["자본금", _format_number(registry_data.get("자본금"))],
                ["발행할 주식 총수", _format_number(registry_data.get("발행할주식총수"))],
                ["발행주식 총수", _format_number(registry_data.get("발행주식총수"))],
                ["1주의 금액", _format_number(registry_data.get("1주당액면가액"))],
            ], columns=["항목", "추출값"])
            st.dataframe(
                registry_preview,
                hide_index=True,
                use_container_width=True,
            )

            compare_rows = [
                _compare_values(
                    "법인명",
                    st.session_state.get("stock_company_name", ""),
                    registry_data.get("법인명", ""),
                ),
                _compare_values(
                    "법인등록번호",
                    st.session_state.get("stock_corporate_no", ""),
                    registry_data.get("법인등록번호", ""),
                ),
                _compare_values(
                    "본점소재지",
                    st.session_state.get("stock_address", ""),
                    registry_data.get("본점소재지", ""),
                ),
                _compare_values(
                    "설립일",
                    st.session_state.get("stock_establishment", ""),
                    registry_data.get("법인설립일", ""),
                ),
            ]
            st.markdown("#### 기존 정보와 비교")
            st.dataframe(
                pd.DataFrame(compare_rows),
                hide_index=True,
                use_container_width=True,
            )

    records = _load_records(user_id)
    if records:
        with st.expander("저장된 주가평가 불러오기·수정", expanded=False):
            options = ["선택 안 함"] + [_record_label(record) for record in records]
            selected_record_label = st.selectbox(
                "저장 기록",
                options,
                key="stock_saved_record_selector",
            )
            if selected_record_label != "선택 안 함":
                record = records[options.index(selected_record_label) - 1]
                if st.button(
                    "선택 평가 불러오기",
                    key="stock_load_record",
                    use_container_width=True,
                ):
                    _apply_loaded_record(record)
                    st.success("저장된 평가자료를 불러왔습니다.")
                    st.rerun()

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
                "영업권 평가액(원)",
                key="stock_goodwill",
                placeholder="해당하는 경우 입력",
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
            "other_adjustments": _safe_number(other_adjustments_text),
            "capitalization_rate": cap_rate_pct / 100,
            "nav_floor_rate": nav_floor_pct / 100,
            "premium_exempt": premium_exempt,
            "premium_rate": premium_pct / 100,
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
            st.session_state["stock_last_result"] = result
            st.session_state["stock_last_inputs"] = inputs
            calculation_status.update(
                label="주가평가가 완료되었습니다.",
                state="complete",
                expanded=False,
            )
        except ValueError as exc:
            calculation_status.update(
                label="주가평가를 완료하지 못했습니다.",
                state="error",
                expanded=True,
            )
            st.error(str(exc))
        except Exception as exc:
            calculation_status.update(
                label="주가평가 중 오류가 발생했습니다.",
                state="error",
                expanded=True,
            )
            st.error(f"주가평가 오류: {exc}")

    if result is None:
        result = st.session_state.get("stock_last_result")

    if result:
        st.markdown("### 5. 평가결과")
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

            record = {
                "record_id": loaded_id or datetime.now().strftime(
                    "%Y%m%d%H%M%S%f"
                ),
                "company_name": company_name,
                "business_no": _normalize_business_no(business_no),
                "corporate_no": corporate_no,
                "address": address,
                "establishment": establishment,
                "valuation_date": valuation_date,
                "current_shares": inputs["current_shares"],
                "par_value": inputs["par_value"],
                "capital": inputs.get("capital"),
                "authorized_shares": inputs.get("authorized_shares"),
                "share_classes": inputs.get("share_classes", []),
                "valuation_type": valuation_type,
                "annual_rows": annual_rows,
                "total_assets": inputs["total_assets"],
                "total_liabilities": inputs["total_liabilities"],
                "asset_additions": inputs["asset_additions"],
                "asset_deductions": inputs["asset_deductions"],
                "liability_additions": inputs["liability_additions"],
                "liability_deductions": inputs["liability_deductions"],
                "goodwill": inputs["goodwill"],
                "other_adjustments": inputs["other_adjustments"],
                "capitalization_rate": inputs["capitalization_rate"],
                "nav_floor_rate": inputs["nav_floor_rate"],
                "premium_exempt": inputs["premium_exempt"],
                "premium_rate": inputs["premium_rate"],
                "result": result,
                "saved_by": user_name,
                "saved_at": now,
                "law_base_date": LAW_BASE_DATE,
            }

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
- 업력·휴폐업·결손 등 사유로 순자산가치만 적용하는 경우는 사용자가 선택

**참고 법령**
- 상속세 및 증여세법 제63조
- 상속세 및 증여세법 시행령 제54조·제55조·제56조·제59조
- 국가법령정보센터: https://www.law.go.kr

이 계산은 상담용 사전평가입니다. 실제 신고평가는 평가기준일의 법령,
세법상 순손익 조정, 자산·부채 시가조정, 영업권, 최대주주 할증 제외요건을
개별 검토해야 합니다.
"""
        )
