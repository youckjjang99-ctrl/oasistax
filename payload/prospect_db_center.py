from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from public_data_api import (
    NPS_BASE_URL,
    REGION_CODES,
    fetch_nps_workplaces,
    service_key_status,
    test_nps_connection,
)
from prospect_db_repository import (
    list_prospects,
    prospect_table_status,
    remove_existing_customers,
    save_prospects,
)


BASE_DIR = Path(__file__).resolve().parent


def _display_frame(items: list[dict]) -> pd.DataFrame:
    rows = []
    for item in items:
        row = {
            "선택": bool(item.get("선택", True)),
            "사업장명": item.get("사업장명", ""),
            "사업자등록번호": item.get("사업자등록번호", ""),
            "지역": item.get("지역", ""),
            "주소": item.get("주소", ""),
            "업종명": item.get("업종명", ""),
            "가입자수": int(item.get("가입자수") or 0),
            "신규취득자수": int(item.get("신규취득자수") or 0),
            "상실가입자수": int(item.get("상실가입자수") or 0),
            "우선순위점수": int(item.get("우선순위점수") or 0),
            "추천사유": " · ".join(item.get("추천사유") or []),
            "source_key": item.get("source_key", ""),
        }
        rows.append(row)
    return pd.DataFrame(rows)


def render_prospect_db_center(owner_user_id: str = "") -> None:
    st.markdown("## 영업후보DB")
    st.caption(
        "공공데이터를 활용해 서울·경기 사업장 후보DB를 생성하기 위한 "
        "관리자 전용 화면입니다."
    )

    st.markdown("### 1. 공공데이터 API 연결 점검")
    st.info(
        "인증키와 응답구조를 확인한 뒤 서울·경기 사업장을 "
        "최대 100건씩 미리보기로 수집합니다."
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
        st.caption(
            f"마지막 점검: {result.get('checked_at', '-')} · "
            f"호출 시도: {result.get('attempt_count', 1)}회"
        )

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
    st.markdown("### 2. 서울·경기 사업장 수집 미리보기")
    st.caption(
        "기본조회에서 사업장 순번을 받은 뒤 상세조회를 자동 실행합니다. "
        "조회 50건은 기본조회 1회와 상세조회 최대 50회가 사용됩니다."
    )

    with st.form("prospect_collection_form_v960"):
        collect_col1, collect_col2, collect_col3 = st.columns(3)
        collect_region = collect_col1.selectbox(
            "수집 지역",
            list(REGION_CODES.keys()),
            key="prospect_collect_region_v960",
        )
        collect_page = collect_col2.number_input(
            "조회 페이지",
            min_value=1,
            max_value=100000,
            value=1,
            step=1,
        )
        collect_rows = collect_col3.selectbox(
            "조회 건수",
            [10, 30, 50, 100],
            index=1,
        )
        filter_col1, filter_col2, filter_col3 = st.columns(3)
        minimum_employees = filter_col1.number_input(
            "최소 가입자 수",
            min_value=0,
            max_value=10000,
            value=3,
            step=1,
        )
        sigungu_code = filter_col2.text_input(
            "시·군·구 법정동 코드",
            placeholder="선택사항",
            help="공공데이터포털 명세의 시군구 코드를 알고 있을 때만 입력합니다.",
        )
        emd_code = filter_col3.text_input(
            "읍·면·동 법정동 코드",
            placeholder="선택사항",
            help="공공데이터포털 명세의 읍면동 코드를 알고 있을 때만 입력합니다.",
        )
        collect_clicked = st.form_submit_button(
            "사업장 미리보기 수집",
            type="primary",
            use_container_width=True,
            disabled=not key_status["configured"],
        )

    if collect_clicked:
        with st.spinner(
            "기본 사업장을 조회하고 상세정보를 확인하고 있습니다. "
            "조회 건수에 따라 시간이 걸릴 수 있습니다..."
        ):
            collection = fetch_nps_workplaces(
                REGION_CODES[collect_region],
                page_no=int(collect_page),
                rows=int(collect_rows),
                sigungu_code=sigungu_code,
                emd_code=emd_code,
            )
            if collection.get("ok"):
                items = [
                    item for item in collection.get("items", [])
                    if int(item.get("가입자수") or 0)
                    >= int(minimum_employees)
                ]
                duplicate_count = 0
                duplicate_warning = ""
                try:
                    items, duplicate_count = remove_existing_customers(items)
                except Exception as exc:
                    duplicate_warning = str(exc)
                collection["items"] = items
                collection["existing_customer_count"] = duplicate_count
                collection["duplicate_warning"] = duplicate_warning
            st.session_state["prospect_collection_v960"] = collection

    collection = st.session_state.get("prospect_collection_v960")
    if collection:
        if collection.get("ok"):
            summary_cols = st.columns(6)
            summary_cols[0].metric(
                "기본조회",
                f"{collection.get('basic_received_count', 0):,}건",
            )
            summary_cols[1].metric(
                "상세조회 성공",
                f"{collection.get('detail_success_count', 0):,}건",
            )
            summary_cols[2].metric(
                "상세조회 실패",
                f"{collection.get('detail_failed_count', 0):,}건",
            )
            summary_cols[3].metric(
                "지역 외 제외",
                f"{collection.get('filtered_out_count', 0):,}건",
            )
            summary_cols[4].metric(
                "기존 고객 제외",
                f"{collection.get('existing_customer_count', 0):,}건",
            )
            summary_cols[5].metric(
                "최종 후보",
                f"{len(collection.get('items', [])):,}건",
            )
            st.caption(
                f"페이지 {collection.get('page_no', 1):,} · "
                f"실제 API 호출 시도 {collection.get('api_attempt_count', 1):,}회"
            )
            if collection.get("duplicate_warning"):
                st.warning(
                    "기존 고객DB 중복확인을 완료하지 못했습니다: "
                    f"{collection['duplicate_warning']}"
                )
        else:
            st.error(collection.get("message", "사업장 조회 실패"))

        detail_failures = collection.get("detail_failures", [])
        if detail_failures:
            st.warning(
                f"상세조회에 실패한 사업장 {len(detail_failures):,}건은 "
                "가입자 수 필터를 적용하지 않고 저장대상에서 제외했습니다. "
                "같은 페이지를 다시 조회하면 자동으로 재시도합니다."
            )
            failure_rows = [
                {
                    "사업장명": item.get("사업장명", ""),
                    "지역코드": item.get("지역코드", ""),
                    "사업장순번": item.get("source_key", ""),
                    "실패사유": item.get("상세조회메시지", ""),
                }
                for item in detail_failures
            ]
            with st.expander("상세조회 실패 사업장 보기"):
                st.dataframe(
                    pd.DataFrame(failure_rows),
                    use_container_width=True,
                    hide_index=True,
                )

        prospects = collection.get("items", [])
        if collection.get("ok") and not prospects:
            if detail_failures:
                st.warning(
                    "상세조회 성공 사업장 중 현재 조건에 맞는 후보가 없습니다. "
                    "같은 페이지를 재시도하거나 최소 가입자 수를 확인해 주세요."
                )
            else:
                st.warning(
                    "현재 페이지에서 조건에 맞는 서울·경기 사업장이 없습니다. "
                    "다음 페이지를 조회하거나 시·군·구 법정동 코드를 입력해 주세요."
                )
        elif prospects:
            display = _display_frame(prospects)
            edited = st.data_editor(
                display,
                use_container_width=True,
                hide_index=True,
                disabled=[
                    column for column in display.columns
                    if column not in {"선택"}
                ],
                column_config={
                    "선택": st.column_config.CheckboxColumn(
                        "저장",
                        help="영업후보DB에 저장할 업체를 선택합니다.",
                    ),
                    "source_key": None,
                },
                key="prospect_editor_v960",
            )
            selected_keys = set(
                edited.loc[edited["선택"] == True, "source_key"].tolist()
            )
            selected_items = [
                item for item in prospects
                if item.get("source_key") in selected_keys
            ]
            st.caption(f"저장 선택: {len(selected_items):,}건")

            if st.button(
                "선택한 업체를 영업후보DB에 저장",
                type="primary",
                use_container_width=True,
                disabled=not selected_items,
                key="save_selected_prospects_v960",
            ):
                table_ok, table_message = prospect_table_status()
                if not table_ok:
                    st.error(table_message)
                else:
                    try:
                        saved_count = save_prospects(
                            selected_items,
                            owner_user_id,
                        )
                        st.success(
                            f"영업후보DB에 {saved_count:,}건을 저장했습니다."
                        )
                        st.session_state.pop("prospect_saved_list_v960", None)
                    except Exception as exc:
                        st.error(str(exc))

    st.divider()
    st.markdown("### 3. Supabase 영업후보DB")
    status_col, action_col = st.columns([2, 1])
    table_status = st.session_state.get("prospect_table_status_v960")
    if action_col.button(
        "테이블 연결 확인",
        use_container_width=True,
        key="check_prospect_table_v960",
    ):
        table_status = prospect_table_status()
        st.session_state["prospect_table_status_v960"] = table_status
    if table_status:
        if table_status[0]:
            status_col.success(table_status[1])
        else:
            status_col.warning(table_status[1])
    else:
        status_col.info("테이블 생성 후 연결 확인을 눌러 주세요.")

    sql_path = BASE_DIR / "supabase_v960_prospect_db.sql"
    if sql_path.exists():
        st.download_button(
            "Supabase 영업후보DB 생성 SQL 다운로드",
            data=sql_path.read_bytes(),
            file_name="supabase_v960_prospect_db.sql",
            mime="text/plain",
            use_container_width=True,
        )
    st.caption(
        "SQL은 Supabase SQL Editor에서 한 번만 실행합니다. "
        "기존 고객DB 테이블은 변경하지 않습니다."
    )

    if table_status and table_status[0]:
        if st.button(
            "저장된 영업후보 새로고침",
            use_container_width=True,
            key="refresh_saved_prospects_v960",
        ):
            try:
                st.session_state["prospect_saved_list_v960"] = list_prospects()
            except Exception as exc:
                st.error(str(exc))
        saved_rows = st.session_state.get("prospect_saved_list_v960", [])
        if saved_rows:
            st.dataframe(
                pd.DataFrame(saved_rows),
                use_container_width=True,
                hide_index=True,
            )
