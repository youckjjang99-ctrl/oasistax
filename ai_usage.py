from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from cloud_db import CloudDatabase, cloud_is_configured


TABLE_AI_USAGE = "oasis_ai_usage"
LOCAL_USAGE_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "ai_usage_events.json"
)


def _setting(name: str, default: float) -> float:
    value = os.environ.get(name, "")
    if not value:
        try:
            value = str(st.secrets.get(name, ""))
        except Exception:
            value = ""
    try:
        return float(value)
    except Exception:
        return float(default)


def usd_krw_rate() -> float:
    return _setting("AI_USAGE_USD_KRW_RATE", 1400.0)


def transcription_rate(model: str) -> float:
    custom = _setting(
        "OPENAI_TRANSCRIPTION_USD_PER_MINUTE",
        -1,
    )
    if custom >= 0:
        return custom
    return {
        "gpt-4o-mini-transcribe": 0.003,
        "gpt-4o-transcribe": 0.006,
    }.get(str(model or ""), 0.003)


def estimate_transcription_cost(
    minutes: float,
    model: str,
) -> float:
    return max(float(minutes or 0), 0) * transcription_rate(model)


def estimate_summary_cost(
    input_tokens: int,
    output_tokens: int,
    model: str,
) -> float:
    input_rate = _setting(
        "OPENAI_SUMMARY_INPUT_USD_PER_1M",
        0.25,
    )
    output_rate = _setting(
        "OPENAI_SUMMARY_OUTPUT_USD_PER_1M",
        2.0,
    )
    return (
        max(int(input_tokens or 0), 0)
        / 1_000_000
        * input_rate
        + max(int(output_tokens or 0), 0)
        / 1_000_000
        * output_rate
    )


def _load_local() -> list[dict[str, Any]]:
    if not LOCAL_USAGE_PATH.exists():
        return []
    try:
        data = json.loads(
            LOCAL_USAGE_PATH.read_text(encoding="utf-8")
        )
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_local(events: list[dict[str, Any]]) -> None:
    LOCAL_USAGE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    LOCAL_USAGE_PATH.write_text(
        json.dumps(
            events[-5000:],
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )


def record_ai_usage(
    user_id: str,
    user_name: str,
    feature: str,
    operation: str,
    model: str,
    company_name: str = "",
    business_no: str = "",
    cached: bool = False,
    audio_minutes: float = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    estimated_cost_usd: float = 0,
    saved_cost_usd: float = 0,
    metadata: dict[str, Any] | None = None,
) -> None:
    event = {
        "event_id": datetime.now().strftime(
            "%Y%m%d%H%M%S%f"
        ),
        "owner_user_id": str(user_id or ""),
        "user_name": str(user_name or ""),
        "feature": str(feature or ""),
        "operation": str(operation or ""),
        "model": str(model or ""),
        "company_name": str(company_name or ""),
        "business_no": str(business_no or ""),
        "cached": bool(cached),
        "audio_minutes": round(
            float(audio_minutes or 0),
            4,
        ),
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "estimated_cost_usd": round(
            float(estimated_cost_usd or 0),
            8,
        ),
        "saved_cost_usd": round(
            float(saved_cost_usd or 0),
            8,
        ),
        "metadata": dict(metadata or {}),
        "created_at": datetime.now().isoformat(
            timespec="seconds"
        ),
    }

    events = _load_local()
    events.append(event)
    _save_local(events)

    if cloud_is_configured():
        try:
            CloudDatabase().insert(
                TABLE_AI_USAGE,
                [event],
            )
        except Exception:
            pass


def _events() -> list[dict[str, Any]]:
    if cloud_is_configured():
        try:
            rows = CloudDatabase().select(
                TABLE_AI_USAGE,
                columns="*",
                order="created_at.desc",
                limit=5000,
            )
            if rows:
                return rows
        except Exception:
            pass
    return list(reversed(_load_local()))


def _frame() -> pd.DataFrame:
    rows = _events()
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    frame["created_at"] = pd.to_datetime(
        frame.get("created_at"),
        errors="coerce",
    )
    for column in [
        "estimated_cost_usd",
        "saved_cost_usd",
        "audio_minutes",
        "input_tokens",
        "output_tokens",
    ]:
        if column not in frame.columns:
            frame[column] = 0
        frame[column] = pd.to_numeric(
            frame[column],
            errors="coerce",
        ).fillna(0)
    if "cached" not in frame.columns:
        frame["cached"] = False
    return frame


def _krw(usd: float) -> str:
    return (
        f"{round(float(usd or 0) * usd_krw_rate()):,}원"
    )


def render_ai_usage_page(
    current_user_id: str,
    current_user_name: str,
) -> None:
    st.markdown("## AI 사용량 및 예상비용")
    st.caption(
        "녹취·상담일지 API 호출과 캐시 절감량을 보여줍니다. "
        "비용은 설정 단가 기준 예상치입니다."
    )

    frame = _frame()
    if frame.empty:
        st.info(
            "아직 기록된 사용량이 없습니다. "
            "녹음 상담일지를 생성하면 표시됩니다."
        )
        return

    today = date.today()
    c1, c2 = st.columns(2)
    with c1:
        start_date = st.date_input(
            "조회 시작일",
            value=today.replace(day=1),
            key="ai_usage_start",
        )
    with c2:
        end_date = st.date_input(
            "조회 종료일",
            value=today,
            key="ai_usage_end",
        )

    filtered = frame[
        frame["created_at"].dt.date.between(
            start_date,
            end_date,
        )
    ].copy()

    users = sorted(
        value
        for value in filtered[
            "user_name"
        ].astype(str).unique()
        if value.strip()
    )
    selected_users = st.multiselect(
        "회원 필터",
        users,
        default=users,
        key="ai_usage_users",
    )
    if selected_users:
        filtered = filtered[
            filtered["user_name"]
            .astype(str)
            .isin(selected_users)
        ]

    calls = len(filtered)
    cost = float(
        filtered["estimated_cost_usd"].sum()
    )
    saved = float(
        filtered["saved_cost_usd"].sum()
    )
    minutes = float(
        filtered["audio_minutes"].sum()
    )
    cache_hits = int(
        filtered["cached"].astype(bool).sum()
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("API 기록", f"{calls:,}건")
    m2.metric("녹취 시간", f"{minutes:,.1f}분")
    m3.metric("예상 사용비용", _krw(cost))
    m4.metric("캐시 절감액", _krw(saved))
    m5.metric(
        "캐시 적중률",
        f"{cache_hits / calls * 100 if calls else 0:.1f}%",
    )

    daily = filtered.copy()
    daily["날짜"] = daily["created_at"].dt.date
    daily = daily.groupby(
        "날짜",
        as_index=False,
    ).agg(
        호출건수=("event_id", "count"),
        녹취분=("audio_minutes", "sum"),
        예상비용USD=("estimated_cost_usd", "sum"),
        절감액USD=("saved_cost_usd", "sum"),
    )
    daily["예상비용"] = daily[
        "예상비용USD"
    ].map(_krw)
    daily["절감액"] = daily[
        "절감액USD"
    ].map(_krw)

    st.markdown("### 일별 사용 추이")
    st.dataframe(
        daily[
            [
                "날짜",
                "호출건수",
                "녹취분",
                "예상비용",
                "절감액",
            ]
        ],
        hide_index=True,
        use_container_width=True,
    )

    user_summary = filtered.groupby(
        ["user_name", "owner_user_id"],
        dropna=False,
        as_index=False,
    ).agg(
        호출건수=("event_id", "count"),
        녹취분=("audio_minutes", "sum"),
        입력토큰=("input_tokens", "sum"),
        출력토큰=("output_tokens", "sum"),
        예상비용USD=("estimated_cost_usd", "sum"),
        절감액USD=("saved_cost_usd", "sum"),
    )
    user_summary["예상비용"] = user_summary[
        "예상비용USD"
    ].map(_krw)
    user_summary["절감액"] = user_summary[
        "절감액USD"
    ].map(_krw)

    st.markdown("### 회원별 사용량")
    st.dataframe(
        user_summary[
            [
                "user_name",
                "owner_user_id",
                "호출건수",
                "녹취분",
                "입력토큰",
                "출력토큰",
                "예상비용",
                "절감액",
            ]
        ],
        hide_index=True,
        use_container_width=True,
    )

    model_summary = filtered.groupby(
        ["operation", "model", "cached"],
        dropna=False,
        as_index=False,
    ).agg(
        호출건수=("event_id", "count"),
        녹취분=("audio_minutes", "sum"),
        예상비용USD=("estimated_cost_usd", "sum"),
        절감액USD=("saved_cost_usd", "sum"),
    )
    model_summary["예상비용"] = model_summary[
        "예상비용USD"
    ].map(_krw)
    model_summary["절감액"] = model_summary[
        "절감액USD"
    ].map(_krw)

    st.markdown("### 작업·모델별 사용량")
    st.dataframe(
        model_summary[
            [
                "operation",
                "model",
                "cached",
                "호출건수",
                "녹취분",
                "예상비용",
                "절감액",
            ]
        ],
        hide_index=True,
        use_container_width=True,
    )

    with st.expander(
        "상세 사용기록",
        expanded=False,
    ):
        detail = filtered.copy()
        detail["예상비용"] = detail[
            "estimated_cost_usd"
        ].map(_krw)
        detail["절감액"] = detail[
            "saved_cost_usd"
        ].map(_krw)
        st.dataframe(
            detail[
                [
                    "created_at",
                    "user_name",
                    "company_name",
                    "operation",
                    "model",
                    "cached",
                    "audio_minutes",
                    "input_tokens",
                    "output_tokens",
                    "예상비용",
                    "절감액",
                ]
            ],
            hide_index=True,
            use_container_width=True,
        )

    st.markdown("### 예상단가 설정")
    st.code(
        "\n".join(
            [
                'AI_USAGE_USD_KRW_RATE = "1400"',
                'OPENAI_TRANSCRIPTION_USD_PER_MINUTE = "0.003"',
                'OPENAI_SUMMARY_INPUT_USD_PER_1M = "0.25"',
                'OPENAI_SUMMARY_OUTPUT_USD_PER_1M = "2.00"',
            ]
        ),
        language="toml",
    )
    st.caption(
        "환율이나 API 단가가 바뀌면 Streamlit Secrets의 숫자만 수정하세요."
    )
