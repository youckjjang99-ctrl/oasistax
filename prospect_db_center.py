from __future__ import annotations

import pandas as pd
import streamlit as st

from public_data_api import (
    NPS_BASE_URL,
    REGION_CODES,
    service_key_status,
    test_nps_connection,
)


def render_prospect_db_center() -> None:
    st.markdown("## 영업후보DB")
    st.caption(
        "공공데이터를 활용해 서울·경기 사업장 후보DB를 생성하기 위한 "
        "관리자 전용 화면입니다."
    )

    st.markdown("### 1. 공공데이터 API 연결 점검")
    st.info(
        "이번 버전은 인증과 응답구조만 확인합니다. "
        "조회 결과는 고객DB나 Supabase에 저장하지 않습니다."
    )

    key_status = service_key_status()
    col1, col2, col3 = st.columns(3)
    col1.metric(
        "Railway 인증키",
        "등록됨" if key_status["configured"] else "미등록",
    )
    col2.metric("인증키 마스킹", key_status["masked"])
    col3.metric("대상 API", "국민연금 사업장")

    with st.expander("연결 설정", expanded=True):
        st.code("DATA_GO_KR_SERVICE_KEY", language="text")
        st.caption(
            "호출주소: "
            f"{NPS_BASE_URL.replace('https://', '')}"
        )
        region_name = st.selectbox(
            "테스트 지역",
            list(REGION_CODES.keys()),
            key="prospect_db_test_region_v950",
        )
        test_clicked = st.button(
            "국민연금 API 연결 테스트",
            type="primary",
            use_container_width=True,
            disabled=not key_status["configured"],
            key="prospect_db_connection_test_v950",
        )

    if not key_status["configured"]:
        st.warning(
            "Railway의 앱 서비스 Variables에 "
            "DATA_GO_KR_SERVICE_KEY를 등록하고 재배포해 주세요."
        )

    if test_clicked:
        with st.spinner("국민연금 사업장 API 연결을 확인하고 있습니다..."):
            result = test_nps_connection(REGION_CODES[region_name])
        st.session_state["prospect_db_api_test_result_v950"] = result

    result = st.session_state.get("prospect_db_api_test_result_v950")
    if result:
        if result.get("ok"):
            st.success(result.get("message", "연결 성공"))
        else:
            st.error(result.get("message", "연결 실패"))

        result_cols = st.columns(4)
        result_cols[0].metric("상태", result.get("status", "-"))
        result_cols[1].metric("HTTP", result.get("http_status", "-"))
        result_cols[2].metric("전체 건수", f"{result.get('total_count', 0):,}")
        result_cols[3].metric("응답형식", result.get("response_format", "-"))
        st.caption(f"마지막 점검: {result.get('checked_at', '-')}")

        samples = result.get("sample") or []
        if samples:
            st.markdown("#### 응답 샘플 1건")
            st.dataframe(
                pd.DataFrame(samples),
                use_container_width=True,
                hide_index=True,
            )

        if not result.get("ok"):
            status = result.get("status", "")
            if status in {"SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "30"}:
                st.warning(
                    "인증키가 아직 API 게이트웨이에 반영되지 않았거나 "
                    "해당 API 활용승인이 완료되지 않은 상태일 수 있습니다."
                )
            elif status in {"LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR", "22"}:
                st.warning("개발계정의 일일 호출한도를 확인해 주세요.")

    st.divider()
    st.markdown("### 2. 다음 구축 단계")
    st.markdown(
        """
        1. 서울·경기 법정동별 사업장 분할 수집
        2. 법인사업장 우선 분류 및 중복 제거
        3. 기존 고객DB와 분리된 영업후보DB 저장
        4. 직원 수·신규취득·상실 인원을 활용한 상담 우선순위 분석
        5. 선택한 업체만 기존 CRM 고객으로 등록
        """
    )

