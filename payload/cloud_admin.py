from __future__ import annotations

import pandas as pd
import streamlit as st

from cloud_db import (
    CloudDatabase,
    TABLE_CRM,
    TABLE_CUSTOMERS,
    TABLE_FINANCIALS,
    TABLE_REGISTRY,
    TABLE_STOCK,
    cloud_is_configured,
    get_cloud_config,
)
from cloud_migration import collect_migration_preview, migrate_user_data


def render_cloud_database_page(
    current_user_id: str,
    current_user_name: str = "",
) -> None:
    st.markdown("## 클라우드 DB 관리")
    st.caption(
        "기존 고객DB와 JSON 파일을 삭제하거나 수정하지 않고 "
        "Supabase에 복사하는 v4.0 1단계 화면입니다."
    )

    config = get_cloud_config()
    if not cloud_is_configured():
        st.error("Supabase Secrets가 아직 설정되지 않았습니다.")
        st.code(
            'SUPABASE_URL = "https://프로젝트ID.supabase.co"\n'
            'SUPABASE_SECRET_KEY = "sb_secret_..."',
            language="toml",
        )
        st.info(
            "Streamlit Cloud 앱 설정의 Secrets에 위 형식으로 입력하세요. "
            "Secret Key는 GitHub와 채팅에 올리지 마세요."
        )
        return

    masked_key = (
        config.secret_key[:10] + "..." + config.secret_key[-4:]
        if len(config.secret_key) > 18
        else "설정됨"
    )

    c1, c2 = st.columns(2)
    c1.metric("Supabase URL", config.url.replace("https://", ""))
    c2.metric("Secret Key", masked_key)

    if st.button("Supabase 연결 테스트", type="primary", use_container_width=True):
        try:
            ok, message = CloudDatabase(config).health_check()
            if ok:
                st.success(message)
            else:
                st.error(message)
        except Exception as exc:
            st.error(str(exc))

    st.markdown("### 기존 자료 이관 미리보기")
    preview = collect_migration_preview(current_user_id)
    preview_df = pd.DataFrame(
        [
            ["고객", preview["customers"]],
            ["CRM", preview["crm"]],
            ["크레탑 재무캐시", preview["financials"]],
            ["등기캐시", preview["registry"]],
            ["주가평가", preview["stock_valuations"]],
        ],
        columns=["자료", "건수"],
    )
    st.dataframe(preview_df, hide_index=True, use_container_width=True)

    st.warning(
        "이관은 기존 파일을 읽어서 Supabase에 복사만 합니다. "
        "기존 고객리스트·엑셀·JSON은 수정하거나 삭제하지 않습니다."
    )

    confirm = st.checkbox(
        "기존 파일을 보존한 채 현재 사용자 자료를 Supabase에 복사하는 것에 동의합니다."
    )

    if st.button(
        "현재 사용자 자료 Supabase로 복사",
        disabled=not confirm,
        use_container_width=True,
    ):
        with st.spinner("Supabase로 자료를 복사하고 있습니다..."):
            try:
                result = migrate_user_data(
                    current_user_id,
                    manager_name=current_user_name,
                )
            except Exception as exc:
                st.error(f"이관 실행 실패: {exc}")
                return

        if result.get("errors"):
            st.warning("일부 자료는 복사되지 않았습니다.")
            for error in result["errors"]:
                st.write(f"- {error}")
        else:
            st.success("기존 자료 복사가 완료되었습니다.")

        st.json(result)

    st.markdown("### 클라우드 저장 건수")
    if st.button("클라우드 건수 새로고침", use_container_width=True):
        try:
            db = CloudDatabase(config)
            counts = {
                "고객": db.count(TABLE_CUSTOMERS, current_user_id),
                "CRM": db.count(TABLE_CRM, current_user_id),
                "재무": db.count(TABLE_FINANCIALS, current_user_id),
                "등기": db.count(TABLE_REGISTRY, current_user_id),
                "주가평가": db.count(TABLE_STOCK, current_user_id),
            }
            st.dataframe(
                pd.DataFrame(counts.items(), columns=["자료", "클라우드 건수"]),
                hide_index=True,
                use_container_width=True,
            )
        except Exception as exc:
            st.error(str(exc))

    with st.expander("v4.0 전환 상태"):
        st.markdown(
            "- 현재 단계: 기존 파일 → Supabase 안전 복사\n"
            "- 고객리스트 원본: 계속 읽기 전용 보존\n"
            "- 아직 하지 않는 것: 클라우드 자료를 기본 저장소로 강제 전환\n"
            "- 다음 단계: 복사 결과 검증 후 신규 입력을 파일+클라우드에 동시 저장"
        )
