from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st

from public_procurement import (
    ProcurementAPIError,
    ProcurementConfigError,
    load_procurement_summary,
    refresh_procurement_summary,
)


def _integer(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _period_label(value: Any) -> str:
    text = str(value or "")
    if len(text) == 6 and text.isdigit():
        return f"{text[:4]}.{text[4:]}"
    return text


def _collected_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone(timedelta(hours=9))).strftime(
            "%Y-%m-%d %H:%M"
        )
    except ValueError:
        return text


def _render_summary(summary: dict[str, Any]) -> None:
    status = str(summary.get("match_status") or "")
    if status == "not_registered":
        st.info("나라장터 조달업체 등록정보가 확인되지 않습니다.")
        return
    if status == "not_found":
        st.info("해당 조회기간의 공공조달 계약실적이 없습니다.")
        return
    if status == "ambiguous":
        st.warning(
            "같은 업체명으로 여러 조달기업이 확인되어 실적을 합산하지 않았습니다. "
            "사업자번호와 조달업체 등록정보를 확인해 주세요."
        )
        return
    if status != "matched":
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("계약 건수", f"{_integer(summary.get('total_count')):,}건")
    c2.metric("계약 금액", f"{_integer(summary.get('total_amount')):,}원")
    c3.metric("연계 조달시스템", f"{len(summary.get('source_systems') or []):,}개")

    breakdown = pd.DataFrame(
        [
            ["물품", _integer(summary.get("product_count")), _integer(summary.get("product_amount"))],
            ["공사", _integer(summary.get("construction_count")), _integer(summary.get("construction_amount"))],
            ["일반용역", _integer(summary.get("general_service_count")), _integer(summary.get("general_service_amount"))],
            ["기술용역", _integer(summary.get("technical_service_count")), _integer(summary.get("technical_service_amount"))],
            ["미분류", _integer(summary.get("unclassified_count")), _integer(summary.get("unclassified_amount"))],
        ],
        columns=["계약 구분", "건수", "금액"],
    )
    st.dataframe(
        breakdown,
        hide_index=True,
        use_container_width=True,
        column_config={
            "건수": st.column_config.NumberColumn(format="%d건"),
            "금액": st.column_config.NumberColumn(format="%d원"),
        },
    )


def render_procurement_summary_panel(
    owner_user_id: str,
    business_no: Any,
) -> None:
    st.markdown("#### 나라장터 계약실적")
    st.caption(
        "나라장터를 포함한 공공 전자조달시스템의 최근 3년 계약실적을 "
        "사업자번호 기준으로 확인합니다."
    )
    try:
        summary = load_procurement_summary(owner_user_id, business_no)
        load_error = ""
    except Exception:
        summary = {}
        load_error = "저장된 계약실적을 불러오지 못했습니다."

    key_source = f"{owner_user_id}|{business_no}".encode("utf-8")
    button_key = hashlib.sha256(key_source).hexdigest()[:16]
    if st.button(
        "나라장터 계약실적 새로 조회",
        key=f"refresh_procurement_{button_key}",
        use_container_width=True,
    ):
        try:
            with st.spinner("나라장터 계약실적을 확인하고 있습니다..."):
                summary = refresh_procurement_summary(
                    owner_user_id,
                    business_no,
                )
            load_error = ""
            st.success("나라장터 계약실적을 갱신했습니다.")
        except ProcurementConfigError as exc:
            st.warning(str(exc))
        except ProcurementAPIError as exc:
            st.error(str(exc))
        except ValueError as exc:
            st.warning(str(exc))
        except Exception:
            st.error("나라장터 계약실적을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.")

    if load_error:
        st.caption(load_error)
    if not summary:
        st.info("아직 조회된 나라장터 계약실적이 없습니다.")
        return

    supplier_name = str(summary.get("supplier_name") or "").strip()
    if supplier_name:
        st.caption(f"조달업체 확인명: {supplier_name}")
    _render_summary(summary)
    start = _period_label(summary.get("query_start_ym"))
    end = _period_label(summary.get("query_end_ym"))
    st.caption(
        f"조회기간: {start} ~ {end} · 마지막 확인: "
        f"{_collected_label(summary.get('collected_at'))}"
    )
