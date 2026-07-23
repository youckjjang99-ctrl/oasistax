from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st

from contact_enrichment import api_statuses, enrich_company, test_connections
from contact_matching import normalize_phone
from prospect_collection_service import collect_contactable_growth_companies
from public_data_api import (
    NPS_BASE_URL,
    REGION_CODES,
    fetch_nps_workplaces,
    service_key_status,
    test_nps_connection,
)
from prospect_db_repository import (
    contact_table_status,
    list_contacts_for_prospects,
    list_prospects,
    list_search_history,
    prospect_table_status,
    remove_existing_customers,
    save_prospect_memo,
    save_search_history,
    save_sales_analysis,
    save_prospect_contacts,
    save_prospects,
    search_history_table_status,
)
from sales_intelligence import analyze_sales_candidate, merge_analysis


BASE_DIR = Path(__file__).resolve().parent
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
BUSINESS_TYPE_OPTIONS = {
    "주식회사": "stock",
    "개인사업자 후보": "individual",
    "전체": "all",
}
INDUSTRY_FILTER_OPTIONS = (
    "병원·의원",
    "음식점",
    "서비스업",
    "도소매업",
    "제조업",
    "건설업",
    "기타",
)
BUSINESS_TYPE_LABELS = {
    "stock": "주식회사",
    "individual": "개인사업자 후보",
    "all": "전체",
}
GROWTH_BASIS_LABELS = {
    "combined": "통합 고용 증가 신호",
    "none": "고용 증가 필터 미사용",
}


def _is_stock_company(value: object) -> bool:
    name = str(value or "").replace(" ", "")
    if any(marker in name for marker in EXCLUDED_LEGAL_MARKERS):
        return False
    return any(marker in name for marker in STOCK_COMPANY_MARKERS)


def _business_type_label(value: object) -> str:
    if _is_stock_company(value):
        return "주식회사"
    name = str(value or "").replace(" ", "")
    if any(
        marker in name
        for marker in EXCLUDED_LEGAL_MARKERS + OTHER_LEGAL_ENTITY_MARKERS
    ):
        return "기타 법인·단체"
    return "개인사업자 후보"


def _contact_status_label(value: object) -> str:
    status = str(value or "").upper()
    labels = {
        "FOUND": "대표전화 확인",
        "NOT_FOUND": "공개 대표전화 미확인",
        "ERROR": "조회 재시도 필요",
    }
    return labels.get(status, str(value or "분석 전"))


def _employment_value(value: object) -> int | str:
    if value in (None, ""):
        return "확인 불가"
    try:
        return int(value)
    except (TypeError, ValueError):
        return "확인 불가"


def _display_frame(items: list[dict]) -> pd.DataFrame:
    rows = []
    for item in items:
        row = {
            "선택": bool(item.get("선택", True)),
            "사업장명": item.get("사업장명", ""),
            "사업자유형": (
                item.get("사업자유형")
                or _business_type_label(item.get("사업장명"))
            ),
            "사업자등록번호": item.get("사업자등록번호", ""),
            "지역": item.get("지역", ""),
            "주소": item.get("주소", ""),
            "대표전화": item.get("대표전화", ""),
            "전화출처": item.get("전화출처", ""),
            "연락처상태": _contact_status_label(
                item.get("연락처상태", "분석 전")
            ),
            "연락처조회이력": " · ".join(
                f"{row.get('stage', '')}:{row.get('status', '')}"
                for row in (item.get("연락처조회이력") or [])
            ),
            "업종분류": item.get("업종분류", ""),
            "업종명": item.get("업종명", ""),
            "자료생성년월": item.get("자료생성년월", ""),
            "가입자수": int(item.get("가입자수") or 0),
            "전년가입자수": _employment_value(
                item.get("전년가입자수")
            ),
            "전년대비고용증가": _employment_value(
                item.get("전년대비고용증가")
            ),
            "신규취득자수": _employment_value(
                item.get("신규취득자수")
            ),
            "상실가입자수": _employment_value(
                item.get("상실가입자수")
            ),
            "최근월순취득": _employment_value(
                item.get("순고용증가")
            ),
            "고용판정": item.get("고용증가판정", ""),
            "고용자료상태": item.get("고용자료상태", ""),
            "영업주제": item.get("영업주제", "분석 전"),
            "추천등급": item.get("추천등급", ""),
            "우선순위점수": int(item.get("우선순위점수") or 0),
            "추천사유": " · ".join(item.get("추천사유") or []),
            "초회전화스크립트": item.get("초회전화스크립트", ""),
            "source_key": item.get("source_key", ""),
        }
        rows.append(row)
    columns = [
        "선택",
        "사업장명",
        "사업자유형",
        "사업자등록번호",
        "지역",
        "주소",
        "대표전화",
        "전화출처",
        "연락처상태",
        "연락처조회이력",
        "업종분류",
        "업종명",
        "자료생성년월",
        "가입자수",
        "전년가입자수",
        "전년대비고용증가",
        "신규취득자수",
        "상실가입자수",
        "최근월순취득",
        "고용판정",
        "고용자료상태",
        "영업주제",
        "추천등급",
        "우선순위점수",
        "추천사유",
        "초회전화스크립트",
        "source_key",
    ]
    return pd.DataFrame(rows, columns=columns)


def _analyze_candidate_batch(
    items: list[dict],
    *,
    limit: int = 100,
) -> tuple[list[dict], list[dict]]:
    targets = items[: max(1, int(limit))]
    analysis_by_key: dict[str, dict] = {}
    failures: list[dict] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_map = {
            executor.submit(analyze_sales_candidate, item): item
            for item in targets
        }
        for future in as_completed(future_map):
            item = future_map[future]
            source_key = str(item.get("source_key") or "")
            try:
                analysis_by_key[source_key] = future.result()
            except Exception as exc:
                failures.append(
                    {
                        "사업장명": item.get("사업장명", ""),
                        "실패사유": f"{type(exc).__name__}: {exc}",
                    }
                )
    merged = [
        (
            merge_analysis(item, analysis_by_key[str(item.get("source_key") or "")])
            if str(item.get("source_key") or "") in analysis_by_key
            else item
        )
        for item in items
    ]
    return merged, failures


def _saved_sales_analysis(row: dict) -> dict:
    source_data = row.get("source_data") or {}
    if not isinstance(source_data, dict):
        return {}
    analysis = source_data.get("sales_intelligence_v971") or {}
    return analysis if isinstance(analysis, dict) else {}


def _saved_candidate_frame(
    rows: list[dict],
    contacts: list[dict],
) -> pd.DataFrame:
    phone_by_id: dict[str, str] = {}
    email_by_id: dict[str, str] = {}
    for contact in contacts:
        prospect_id = str(contact.get("prospect_id") or "")
        if contact.get("do_not_contact"):
            continue
        if (
            contact.get("contact_type") == "phone"
            and prospect_id not in phone_by_id
        ):
            phone_by_id[prospect_id] = str(contact.get("contact_value") or "")
        if (
            contact.get("contact_type") == "email"
            and prospect_id not in email_by_id
        ):
            email_by_id[prospect_id] = str(contact.get("contact_value") or "")
    display: list[dict] = []
    for row in rows:
        prospect_id = str(row.get("id") or "")
        analysis = _saved_sales_analysis(row)
        source_data = (
            row.get("source_data")
            if isinstance(row.get("source_data"), dict)
            else {}
        )
        employment = (
            source_data.get("employment_growth")
            if isinstance(source_data.get("employment_growth"), dict)
            else {}
        )
        selected_growth = employment.get("selected_growth")
        display.append(
            {
                "업체명": row.get("company_name", ""),
                "사업자유형": (
                    source_data.get("business_type")
                )
                or _business_type_label(row.get("company_name")),
                "대표전화": (
                    phone_by_id.get(prospect_id)
                    or analysis.get("phone", "")
                ),
                "전화출처": analysis.get("phone_source", ""),
                "연락처상태": _contact_status_label(
                    analysis.get("contact_status", "분석 전")
                ),
                "이메일": email_by_id.get(prospect_id, ""),
                "업종분류": (
                    source_data.get("industry_category")
                ),
                "업종명": row.get("industry_name", ""),
                "가입자": int(row.get("employee_count") or 0),
                "고용증가기준": GROWTH_BASIS_LABELS.get(
                    str(employment.get("basis") or ""),
                    "기존 저장자료",
                ),
                "고용증가값": _employment_value(selected_growth),
                "고용판정": str(employment.get("judgement") or ""),
                "_고용정렬": (
                    int(selected_growth)
                    if selected_growth not in (None, "")
                    else -1000000
                ),
                "영업주제": " · ".join(
                    analysis.get("sales_topics") or []
                ),
                "추천등급": analysis.get("recommendation_grade", ""),
                "초회전화스크립트": analysis.get(
                    "first_call_script",
                    "",
                ),
                "메모": str(row.get("memo") or ""),
                "_prospect_id": prospect_id,
            }
        )
    return pd.DataFrame(display)


def _excel_bytes(frame: pd.DataFrame, sheet_name: str) -> bytes:
    output = BytesIO()
    safe_sheet_name = str(sheet_name or "DB발굴")[:31]
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name=safe_sheet_name, index=False)
    return output.getvalue()


def _render_search_history(owner_user_id: str) -> int:
    with st.expander("내 검색 페이지 이력", expanded=False):
        try:
            history_ok, history_message = search_history_table_status()
        except Exception as exc:
            history_ok = False
            history_message = f"검색 이력 연결 확인 실패: {exc}"
        if not history_ok:
            st.info(
                f"{history_message} 관리자 설정의 v9.8.4 SQL을 한 번 "
                "실행하면 이후 검색 구간이 사용자별로 자동 저장됩니다."
            )
            return 1
        try:
            rows = list_search_history(owner_user_id, limit=50)
        except Exception as exc:
            st.warning(f"검색 이력을 불러오지 못했습니다: {exc}")
            return 1
        if not rows:
            st.caption(
                "아직 저장된 검색 이력이 없습니다. 검색을 완료하면 "
                "지역·사업자 유형·페이지 구간이 자동 기록됩니다."
            )
            return 1
        display_rows = []
        for row in rows:
            categories = row.get("industry_categories") or []
            display_rows.append(
                {
                    "검색일시": str(row.get("searched_at") or "").replace(
                        "T", " "
                    )[:19],
                    "지역": row.get("region", ""),
                    "사업자 유형": BUSINESS_TYPE_LABELS.get(
                        str(row.get("business_type") or ""),
                        row.get("business_type", ""),
                    ),
                    "조회 페이지": (
                        f"{int(row.get('start_page') or 1):,}"
                        f"~{int(row.get('end_page') or 1):,}"
                    ),
                    "업종 필터": (
                        " · ".join(categories) if categories else "전체 업종"
                    ),
                    "고용 기준": GROWTH_BASIS_LABELS.get(
                        str(row.get("growth_basis") or "combined"),
                        str(row.get("growth_basis") or ""),
                    ),
                    "발굴": f"{int(row.get('found_count') or 0):,}건",
                    "검색시간": (
                        f"{float(row.get('elapsed_seconds') or 0):,.1f}초"
                    ),
                }
            )
        st.dataframe(
            pd.DataFrame(display_rows),
            use_container_width=True,
            hide_index=True,
        )
        latest = rows[0]
        next_page = min(
            100000,
            int(latest.get("end_page") or 0) + 1,
        )
        st.caption(
            "최근 조회 다음 권장 페이지: "
            f"{next_page:,} · 검색 입력값에 자동 반영됩니다."
        )
        return next_page


def _contact_result_row(result: dict) -> dict:
    contacts = result.get("contacts") or []
    return {
        "업체명": result.get("company_name", ""),
        "판정": result.get("status", ""),
        "전화": " · ".join(
            row.get("contact_value", "")
            for row in contacts
            if row.get("contact_type") == "phone"
        ),
        "이메일": " · ".join(
            row.get("contact_value", "")
            for row in contacts
            if row.get("contact_type") == "email"
        ),
        "홈페이지": result.get("website_url", ""),
        "저장": int(result.get("saved_count") or 0),
    }


def _render_prospect_db_center_legacy(owner_user_id: str = "") -> None:
    st.markdown("## 영업후보DB")
    st.caption(
        "서울·경기 주식회사를 찾고 연락처·고용변화를 분석해 "
        "전화할 이유와 초회 스크립트까지 준비합니다."
    )
    guide_cols = st.columns(3)
    guide_cols[0].info("① 서울·경기 주식회사 수집")
    guide_cols[1].info("② 연락처·고용 자동분석")
    guide_cols[2].info("③ 후보 저장 후 초회전화")

    st.markdown("### 1. 데이터 연결 상태")
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

    with st.expander("국민연금 연결 설정", expanded=False):
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

    st.markdown("#### 영업정보 데이터")
    contact_api_status = api_statuses()
    api_cols = st.columns(3)
    api_cols[0].metric(
        "카카오 로컬",
        "등록됨" if contact_api_status["kakao"]["configured"] else "미등록",
    )
    api_cols[1].metric(
        "네이버 검색",
        "등록됨" if contact_api_status["naver"]["configured"] else "미등록",
    )
    api_cols[2].metric(
        "승인 인허가 API",
        (
            f"{contact_api_status['localdata']['service_count']}종"
            if contact_api_status["localdata"]["configured"]
            else "미등록"
        ),
    )
    with st.expander("외부 데이터 연결 설정"):
        st.code(
            "KAKAO_REST_API_KEY\n"
            "NAVER_CLIENT_ID\n"
            "NAVER_CLIENT_SECRET\n"
            "DATA_GO_KR_SERVICE_KEY",
            language="text",
        )
        contact_test_clicked = st.button(
            "외부 데이터 연결 점검",
            use_container_width=True,
            disabled=not all(
                row.get("configured")
                for row in contact_api_status.values()
            ),
            key="contact_api_connection_test_v970",
        )
    if contact_test_clicked:
        with st.spinner(
            "카카오·네이버·인허가 API를 점검하고 있습니다..."
        ):
            connection_result = test_connections()
            st.session_state["contact_api_test_result_v970"] = (
                connection_result
            )

    contact_test = st.session_state.get("contact_api_test_result_v970")
    if contact_test:
        sources = contact_test.get("sources") or {}
        test_rows = []
        for key, label in (
            ("kakao", "카카오 로컬"),
            ("naver", "네이버 검색"),
            ("localdata", "승인 인허가 API"),
        ):
            source = sources.get(key) or {}
            test_rows.append(
                {
                    "연결": label,
                    "상태": source.get("status", "-"),
                    "결과": source.get("message", "-"),
                }
            )
        if contact_test.get("ok"):
            st.success("연락처 보강 API 연결 점검을 완료했습니다.")
        else:
            st.warning("일부 API 연결을 확인하지 못했습니다.")
        st.dataframe(pd.DataFrame(test_rows), use_container_width=True, hide_index=True)
        local_services = (sources.get("localdata") or {}).get("services") or []
        if local_services:
            with st.expander("승인 인허가 API 6종 상세"):
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "서비스": row.get("label", ""),
                                "상태": row.get("status", ""),
                                "응답": row.get("message", ""),
                            }
                            for row in local_services
                        ]
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

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
        auto_sales_analysis = st.checkbox(
            "대표전화 확인 후 고용변화·영업주제 자동생성",
            value=True,
            disabled=True,
            help=(
                "대표전화가 확인되지 않은 업체는 영업후보로 표시하거나 저장하지 않습니다."
            ),
        )
        collect_clicked = st.form_submit_button(
            "사업장 수집 및 영업정보 자동완성",
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
                employee_filtered_items = [
                    item for item in collection.get("items", [])
                    if int(item.get("가입자수") or 0)
                    >= int(minimum_employees)
                ]
                items = [
                    item
                    for item in employee_filtered_items
                    if _is_stock_company(item.get("사업장명"))
                ]
                collection["non_stock_company_count"] = (
                    len(employee_filtered_items) - len(items)
                )
                duplicate_count = 0
                duplicate_warning = ""
                try:
                    items, duplicate_count = remove_existing_customers(items)
                except Exception as exc:
                    duplicate_warning = str(exc)
                analysis_failures: list[dict] = []
                if items:
                    items, analysis_failures = _analyze_candidate_batch(
                        items,
                        limit=len(items),
                    )
                contact_ready_items = [
                    item for item in items
                    if str(item.get("대표전화") or "").strip()
                ]
                collection["contact_missing_count"] = (
                    len(items) - len(contact_ready_items)
                )
                collection["contact_analysis_attempted_count"] = len(items)
                collection["contact_missing_items"] = [
                    item
                    for item in items
                    if not str(item.get("대표전화") or "").strip()
                ]
                items = contact_ready_items
                collection["items"] = items
                collection["existing_customer_count"] = duplicate_count
                collection["duplicate_warning"] = duplicate_warning
                collection["sales_analysis_count"] = sum(
                    1 for item in items if item.get("영업분석")
                )
                collection["sales_analysis_failures"] = analysis_failures
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
                f"실제 API 호출 시도 {collection.get('api_attempt_count', 1):,}회 · "
                f"주식회사 외 제외 {collection.get('non_stock_company_count', 0):,}건 · "
                f"연락처 분석 {collection.get('contact_analysis_attempted_count', 0):,}건 · "
                f"번호 확인 {collection.get('sales_analysis_count', 0):,}건 · "
                f"연락처 미확인 제외 {collection.get('contact_missing_count', 0):,}건"
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
        contact_missing_items = collection.get("contact_missing_items") or []
        if contact_missing_items:
            with st.expander(
                f"대표전화 미확인 제외 사유 {len(contact_missing_items):,}건"
            ):
                st.dataframe(
                    _display_frame(contact_missing_items)[
                        [
                            "사업장명",
                            "주소",
                            "업종명",
                            "연락처상태",
                            "연락처조회이력",
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )
        if collection.get("ok") and not prospects:
            if detail_failures:
                st.warning(
                    "상세조회 성공 사업장 중 현재 조건에 맞는 후보가 없습니다. "
                    "같은 페이지를 재시도하거나 최소 가입자 수를 확인해 주세요."
                )
            else:
                st.warning(
                    "현재 페이지에서 대표전화까지 확인된 서울·경기 주식회사가 없습니다. "
                    "다음 페이지를 조회하면 다른 사업장을 확인합니다."
                )
        elif prospects:
            action_col1, action_col2 = st.columns([2, 1])
            action_col1.info(
                "아래 표에서 대표전화·전화출처·연락처상태·순고용증가·영업주제와 "
                "초회 전화 스크립트를 한 번에 확인할 수 있습니다."
            )
            reanalyze_clicked = action_col2.button(
                "영업정보 다시 분석",
                use_container_width=True,
                key="reanalyze_candidates_v971",
            )
            if reanalyze_clicked:
                with st.spinner(
                    "대표전화·공식홈페이지·고용변화와 영업주제를 분석하고 있습니다..."
                ):
                    analyzed_items, analysis_failures = (
                        _analyze_candidate_batch(prospects, limit=len(prospects))
                    )
                    contact_ready_items = [
                        item for item in analyzed_items
                        if str(item.get("대표전화") or "").strip()
                    ]
                    collection["contact_missing_count"] = (
                        collection.get("contact_missing_count", 0)
                        + len(analyzed_items) - len(contact_ready_items)
                    )
                    collection["contact_analysis_attempted_count"] = len(
                        analyzed_items
                    )
                    collection["contact_missing_items"] = [
                        item
                        for item in analyzed_items
                        if not str(item.get("대표전화") or "").strip()
                    ]
                    collection["items"] = contact_ready_items
                    collection["sales_analysis_count"] = sum(
                        1 for item in analyzed_items if item.get("영업분석")
                    )
                    collection["sales_analysis_failures"] = analysis_failures
                    st.session_state["prospect_collection_v960"] = collection
                    prospects = contact_ready_items

            analysis_failures = collection.get("sales_analysis_failures") or []
            if analysis_failures:
                with st.expander(
                    f"영업정보를 확인하지 못한 업체 {len(analysis_failures)}건"
                ):
                    st.dataframe(
                        pd.DataFrame(analysis_failures),
                        use_container_width=True,
                        hide_index=True,
                    )

            with st.form("prospect_sales_filter_v971"):
                filter_view_col1, filter_view_col2 = st.columns(2)
                hiring_only = filter_view_col1.checkbox(
                    "순고용 증가업체만",
                    key="prospect_hiring_only_v971",
                )
                grade_filter = filter_view_col2.selectbox(
                    "추천등급",
                    ["전체", "A", "B", "C"],
                    key="prospect_grade_filter_v971",
                )
                st.form_submit_button(
                    "조건 적용",
                    use_container_width=True,
                )

            visible_prospects = [
                item
                for item in prospects
                if (
                    not hiring_only
                    or int(item.get("순고용증가") or 0) > 0
                )
                and (
                    grade_filter == "전체"
                    or item.get("추천등급") == grade_filter
                )
            ]
            display = _display_frame(visible_prospects)
            if display.empty:
                st.warning("선택한 조건에 맞는 영업후보가 없습니다.")
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
                    "대표전화": st.column_config.TextColumn(
                        "대표전화",
                        width="medium",
                    ),
                    "영업주제": st.column_config.TextColumn(
                        "영업주제",
                        width="large",
                    ),
                    "초회전화스크립트": st.column_config.TextColumn(
                        "초회 전화 스크립트",
                        width="large",
                    ),
                    "source_key": None,
                },
                key=(
                    "prospect_editor_v971_"
                    f"{int(hiring_only)}_{grade_filter}"
                ),
            )
            selected_keys = set(
                edited.loc[edited["선택"] == True, "source_key"].tolist()
            )
            selected_items = [
                item for item in visible_prospects
                if item.get("source_key") in selected_keys
            ]
            st.caption(f"저장 선택: {len(selected_items):,}건")

            script_options = {
                str(item.get("사업장명") or item.get("source_key")): item
                for item in visible_prospects
                if item.get("초회전화스크립트")
            }
            if script_options:
                with st.expander("초회 영업전화 스크립트 크게 보기"):
                    script_company = st.selectbox(
                        "업체 선택",
                        list(script_options.keys()),
                        key="preview_call_script_company_v971",
                    )
                    selected_script_item = script_options[script_company]
                    st.text_area(
                        "전화 스크립트",
                        value=selected_script_item.get(
                            "초회전화스크립트",
                            "",
                        ),
                        height=180,
                        disabled=True,
                        key="preview_call_script_v971",
                    )

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
    st.markdown("### 3. 저장된 영업후보 관리")
    st.caption(
        "기술적인 DB 설정은 아래 관리자 설정 안에 모았습니다. "
        "평소에는 업체를 선택하고 분석 또는 정밀 연락처 보강만 누르면 됩니다."
    )

    if "prospect_table_status_v960" not in st.session_state:
        st.session_state["prospect_table_status_v960"] = prospect_table_status()
    if "contact_table_status_v970" not in st.session_state:
        st.session_state["contact_table_status_v970"] = contact_table_status()
    table_status = st.session_state["prospect_table_status_v960"]
    saved_contact_status = st.session_state["contact_table_status_v970"]

    setup_ok = bool(table_status[0] and saved_contact_status[0])
    with st.expander(
        "관리자 설정 · DB 연결 상태",
        expanded=not setup_ok,
    ):
        setup_col1, setup_col2 = st.columns(2)
        if table_status[0]:
            setup_col1.success("영업후보 저장 준비 완료")
        else:
            setup_col1.warning("영업후보 테이블 설정 필요")
        if saved_contact_status[0]:
            setup_col2.success("연락처 저장 준비 완료")
        else:
            setup_col2.warning("연락처 테이블 설정 필요")

        if st.button(
            "DB 연결상태 새로 확인",
            use_container_width=True,
            key="refresh_db_status_v971",
        ):
            st.session_state["prospect_table_status_v960"] = (
                prospect_table_status()
            )
            st.session_state["contact_table_status_v970"] = (
                contact_table_status()
            )
            table_status = st.session_state["prospect_table_status_v960"]
            saved_contact_status = st.session_state[
                "contact_table_status_v970"
            ]

        sql_path = BASE_DIR / "supabase_v960_prospect_db.sql"
        contact_sql_path = BASE_DIR / "supabase_v970_contact_enrichment.sql"
        if not table_status[0] and sql_path.exists():
            st.info(
                "① 아래 영업후보DB SQL을 Supabase SQL Editor에서 "
                "먼저 한 번 실행합니다."
            )
            st.download_button(
                "① 영업후보DB 설정파일 다운로드",
                data=sql_path.read_bytes(),
                file_name="supabase_v960_prospect_db.sql",
                mime="text/plain",
                use_container_width=True,
                key="download_prospect_sql_v971",
            )
        if not saved_contact_status[0] and contact_sql_path.exists():
            st.info(
                "② 영업후보DB 설정 후 아래 연락처 SQL을 "
                "Supabase SQL Editor에서 한 번 실행합니다."
            )
            st.download_button(
                "② 연락처DB 설정파일 다운로드",
                data=contact_sql_path.read_bytes(),
                file_name="supabase_v970_contact_enrichment.sql",
                mime="text/plain",
                use_container_width=True,
                key="download_contact_sql_v971",
            )
        st.caption(
            "두 SQL은 최초 1회만 필요합니다. v9.7.2도 기존 "
            "source_data에 영업분석을 추가하므로 새 SQL이 없습니다."
        )

    if not table_status[0]:
        st.warning(
            "관리자 설정에서 ① 영업후보DB 설정을 완료하면 "
            "저장된 영업후보 관리 화면이 열립니다."
        )
        return

    load_col1, load_col2 = st.columns([3, 1])
    load_col1.success("영업후보DB 연결 완료")
    refresh_saved = load_col2.button(
        "저장목록 새로고침",
        use_container_width=True,
        key="refresh_saved_prospects_v971",
    )
    if refresh_saved or "prospect_saved_list_v960" not in st.session_state:
        try:
            st.session_state["prospect_saved_list_v960"] = list_prospects()
        except Exception as exc:
            st.error(str(exc))
            st.session_state["prospect_saved_list_v960"] = []
    all_saved_rows = st.session_state.get("prospect_saved_list_v960", [])
    stock_company_rows = [
        row
        for row in all_saved_rows
        if _is_stock_company(row.get("company_name"))
    ]
    hidden_non_stock_count = len(all_saved_rows) - len(stock_company_rows)
    if hidden_non_stock_count:
        st.info(
            f"기존 저장자료 중 주식회사 외 {hidden_non_stock_count:,}건은 "
            "삭제하지 않고 이 영업후보 화면에서만 숨겼습니다."
        )
    contact_rows: list[dict] = []
    if stock_company_rows and saved_contact_status[0]:
        try:
            contact_rows = list_contacts_for_prospects(
                [str(row.get("id")) for row in stock_company_rows]
            )
            st.session_state["prospect_contacts_v970"] = contact_rows
        except Exception as exc:
            st.warning(f"연락처 목록 확인 실패: {exc}")
            contact_rows = st.session_state.get("prospect_contacts_v970", [])

    phone_prospect_ids = {
        str(row.get("prospect_id") or "")
        for row in contact_rows
        if row.get("contact_type") == "phone"
        and row.get("contact_value")
        and not row.get("do_not_contact")
    }
    saved_rows = [
        row
        for row in stock_company_rows
        if str(_saved_sales_analysis(row).get("phone") or "").strip()
        or str(row.get("id") or "") in phone_prospect_ids
    ]
    hidden_no_phone_count = len(stock_company_rows) - len(saved_rows)
    if hidden_no_phone_count:
        st.info(
            f"대표전화가 확인되지 않은 저장자료 {hidden_no_phone_count:,}건은 "
            "삭제하지 않고 영업 목록에서만 숨겼습니다."
        )

    if not saved_rows:
        st.info(
            "저장된 주식회사 영업후보가 없습니다. 위 후보표에서 업체를 선택해 "
            "영업후보DB에 저장해 주세요."
        )
        return

    saved_frame = _saved_candidate_frame(saved_rows, contact_rows)
    st.dataframe(
        saved_frame,
        use_container_width=True,
        hide_index=True,
        column_config={
            "초회전화스크립트": st.column_config.TextColumn(
                "초회 전화 스크립트",
                width="large",
            )
        },
    )
    with st.expander("기존 영업후보 원본데이터 보기"):
        st.dataframe(
            pd.DataFrame(saved_rows),
            use_container_width=True,
            hide_index=True,
        )

    with st.expander("저장된 연락처 상세보기"):
        if not saved_contact_status[0]:
            st.info(
                "관리자 설정에서 연락처 테이블 연결을 먼저 완료해 주세요."
            )
        else:
            if st.button(
                "저장된 연락처 새로고침",
                use_container_width=True,
                key="refresh_prospect_contacts_v970",
            ):
                try:
                    contact_rows = list_contacts_for_prospects(
                        [str(row.get("id")) for row in saved_rows]
                    )
                    st.session_state["prospect_contacts_v970"] = contact_rows
                except Exception as exc:
                    st.error(str(exc))
            if contact_rows:
                company_by_id = {
                    str(row.get("id")): row.get("company_name", "")
                    for row in saved_rows
                }
                display_contacts = [
                    {
                        "업체명": company_by_id.get(
                            str(row.get("prospect_id")),
                            "",
                        ),
                        "구분": row.get("contact_type", ""),
                        "연락처": row.get("contact_value", ""),
                        "설명": row.get("contact_label", ""),
                        "출처": row.get("source_type", ""),
                        "신뢰도": row.get("confidence", 0),
                        "검증상태": row.get("verification_status", ""),
                        "수신거부": row.get("do_not_contact", False),
                    }
                    for row in contact_rows
                ]
                st.dataframe(
                    pd.DataFrame(display_contacts),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("아직 저장된 연락처가 없습니다.")

    label_to_id = {
        (
            f"{row.get('company_name', '(업체명 없음)')} | "
            f"{row.get('address', '')} | {str(row.get('id', ''))[-8:]}"
        ): str(row.get("id") or "")
        for row in saved_rows
        if row.get("id")
    }
    row_by_id = {
        str(row.get("id")): row
        for row in saved_rows
        if row.get("id")
    }
    with st.form("saved_sales_action_form_v971"):
        selected_labels = st.multiselect(
            "작업할 영업후보 선택",
            list(label_to_id.keys()),
            max_selections=10,
            help="한 번에 최대 10개 업체를 선택합니다.",
        )
        button_col1, button_col2 = st.columns(2)
        analyze_saved_clicked = button_col1.form_submit_button(
            "대표전화·고용주제 분석",
            type="primary",
            use_container_width=True,
            disabled=not selected_labels,
        )
        enrich_saved_clicked = button_col2.form_submit_button(
            "이메일·홈페이지 정밀 보강",
            use_container_width=True,
            disabled=(
                not selected_labels or not saved_contact_status[0]
            ),
        )
        st.caption(
            "첫 번째 버튼은 카카오·인허가 API에서 번호를 찾지 못하면 "
            "네이버와 공식 홈페이지까지 자동 확인해 전화할 이유와 스크립트를 만듭니다. "
            "두 번째 버튼은 네이버와 공식 홈페이지까지 확인해 "
            "이메일·홈페이지를 연락처DB에 저장합니다. 공개된 사업용 "
            "연락처만 사용하며 실제 연락 전에는 수신거부 여부를 확인합니다."
        )

    selected_ids = [
        label_to_id[label]
        for label in selected_labels
        if label in label_to_id
    ]
    if analyze_saved_clicked:
        sales_results: list[dict] = []
        progress = st.progress(0, text="영업정보 분석을 시작합니다.")
        for index, prospect_id in enumerate(selected_ids, start=1):
            prospect = row_by_id[prospect_id]
            try:
                analysis = analyze_sales_candidate(prospect)
                save_sales_analysis(prospect_id, analysis)
                sales_results.append(
                    {
                        "업체명": prospect.get("company_name", ""),
                        "대표전화": analysis.get("phone", ""),
                        "전화출처": analysis.get("phone_source", ""),
                        "연락처상태": _contact_status_label(
                            analysis.get("contact_status", "")
                        ),
                        "순고용증가": analysis.get("net_hiring", 0),
                        "영업주제": " · ".join(
                            analysis.get("sales_topics") or []
                        ),
                        "추천등급": analysis.get(
                            "recommendation_grade",
                            "",
                        ),
                        "결과": "저장 완료",
                    }
                )
            except Exception as exc:
                sales_results.append(
                    {
                        "업체명": prospect.get("company_name", ""),
                        "결과": f"실패: {exc}",
                    }
                )
            progress.progress(
                index / max(1, len(selected_ids)),
                text=f"{index}/{len(selected_ids)} 업체 분석 완료",
            )
        st.session_state["sales_analysis_results_v971"] = sales_results
        try:
            st.session_state["prospect_saved_list_v960"] = list_prospects()
            saved_rows = st.session_state["prospect_saved_list_v960"]
        except Exception as exc:
            st.warning(f"분석 후 목록 새로고침 실패: {exc}")
        st.rerun()

    if enrich_saved_clicked:
        enrichment_results: list[dict] = []
        progress = st.progress(0, text="정밀 연락처 보강을 시작합니다.")
        for index, prospect_id in enumerate(selected_ids, start=1):
            prospect = row_by_id[prospect_id]
            try:
                result = enrich_company(prospect)
                result["prospect_id"] = prospect_id
                result["saved_count"] = save_prospect_contacts(
                    prospect_id,
                    result.get("contacts") or [],
                    owner_user_id,
                )
            except Exception as exc:
                result = {
                    "ok": False,
                    "prospect_id": prospect_id,
                    "company_name": prospect.get("company_name", ""),
                    "status": "error",
                    "contacts": [],
                    "saved_count": 0,
                    "trace": [
                        {
                            "stage": "error",
                            "status": type(exc).__name__,
                            "message": str(exc),
                        }
                    ],
                }
            enrichment_results.append(result)
            progress.progress(
                index / max(1, len(selected_ids)),
                text=f"{index}/{len(selected_ids)} 업체 보강 완료",
            )
        st.session_state["contact_enrichment_results_v970"] = (
            enrichment_results
        )
        st.session_state.pop("prospect_contacts_v970", None)
        st.rerun()

    sales_results = st.session_state.get("sales_analysis_results_v971", [])
    if sales_results:
        st.markdown("#### 최근 영업정보 분석 결과")
        st.dataframe(
            pd.DataFrame(sales_results),
            use_container_width=True,
            hide_index=True,
        )
    enrichment_results = st.session_state.get(
        "contact_enrichment_results_v970",
        [],
    )
    if enrichment_results:
        st.markdown("#### 최근 정밀 연락처 보강 결과")
        st.dataframe(
            pd.DataFrame(
                [_contact_result_row(row) for row in enrichment_results]
            ),
            use_container_width=True,
            hide_index=True,
        )
        with st.expander("업체별 연락처 수집 경로 보기"):
            for result in enrichment_results:
                st.markdown(f"**{result.get('company_name', '')}**")
                st.dataframe(
                    pd.DataFrame(result.get("trace") or []),
                    use_container_width=True,
                    hide_index=True,
                )

    script_rows = [
        row for row in saved_rows if _saved_sales_analysis(row).get(
            "first_call_script"
        )
    ]
    if script_rows:
        st.markdown("#### 초회 영업전화 준비")
        script_labels = {
            (
                f"{row.get('company_name', '')} | "
                f"{str(row.get('id', ''))[-8:]}"
            ): row
            for row in script_rows
        }
        selected_script_label = st.selectbox(
            "전화할 업체",
            list(script_labels.keys()),
            key="saved_call_script_company_v971",
        )
        script_analysis = _saved_sales_analysis(
            script_labels[selected_script_label]
        )
        script_col1, script_col2 = st.columns([1, 2])
        script_col1.metric(
            "추천등급",
            script_analysis.get("recommendation_grade", "-"),
        )
        script_col1.write(
            "영업주제: "
            + " · ".join(script_analysis.get("sales_topics") or [])
        )
        script_col2.text_area(
            "초회 전화 스크립트",
            value=script_analysis.get("first_call_script", ""),
            height=200,
            disabled=True,
            key="saved_call_script_v971",
        )


def _render_clean_saved_prospects(owner_user_id: str) -> None:
    st.markdown("### 저장된 영업후보")
    st.caption(
        "모든 사용자가 함께 사용하는 영업DB입니다. 대표전화 또는 "
        "휴대전화가 확인된 업체를 선택한 고용 증가 기준 순으로 표시하며, "
        "업체별 메모를 바로 기록할 수 있습니다."
    )
    try:
        rows = list_prospects(limit=1000)
    except Exception as exc:
        st.warning(f"저장목록을 불러오지 못했습니다: {exc}")
        return
    if not rows:
        st.info("저장된 영업후보가 없습니다.")
        return

    contacts: list[dict] = []
    try:
        if contact_table_status()[0]:
            contacts = list_contacts_for_prospects(
                [str(row.get("id") or "") for row in rows]
            )
    except Exception:
        contacts = []

    frame = _saved_candidate_frame(rows, contacts)
    if not frame.empty:
        frame["대표전화"] = frame["대표전화"].map(normalize_phone)
        frame = frame[frame["대표전화"] != ""].reset_index(drop=True)
        frame = frame.sort_values(
            by=["_고용정렬", "가입자"],
            ascending=[False, False],
            kind="stable",
        ).reset_index(drop=True)
    if frame.empty:
        st.info("유효한 대표전화가 확인된 저장 업체가 없습니다.")
        return

    export_frame = frame.drop(
        columns=["_prospect_id", "_고용정렬"],
        errors="ignore",
    )
    st.download_button(
        "저장된 영업후보 엑셀 다운로드",
        data=_excel_bytes(export_frame, "저장된 영업후보"),
        file_name="OASIS_저장된_영업후보.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True,
        key="saved_prospect_excel_v984",
    )
    visible_columns = [
        "업체명",
        "사업자유형",
        "대표전화",
        "전화출처",
        "연락처상태",
        "이메일",
        "업종분류",
        "업종명",
        "가입자",
        "고용증가기준",
        "고용증가값",
        "고용판정",
        "영업주제",
        "추천등급",
        "초회전화스크립트",
        "메모",
        "_prospect_id",
    ]
    original_memos = {
        str(row["_prospect_id"]): str(row.get("메모") or "")
        for row in frame[visible_columns].to_dict("records")
    }
    edited = st.data_editor(
        frame[visible_columns],
        use_container_width=True,
        hide_index=True,
        disabled=[
            column for column in visible_columns if column != "메모"
        ],
        column_config={
            "초회전화스크립트": st.column_config.TextColumn(
                "초회 전화 스크립트",
                width="large",
            ),
            "메모": st.column_config.TextColumn(
                "업체 메모",
                width="large",
                help="담당자·통화 결과·다음 연락일 등을 기록합니다.",
            ),
            "_prospect_id": None,
        },
        key="saved_prospect_memo_editor_v984",
    )
    changed_memos = [
        (
            str(row.get("_prospect_id") or ""),
            str(row.get("메모") or ""),
        )
        for row in edited.to_dict("records")
        if str(row.get("메모") or "")
        != original_memos.get(str(row.get("_prospect_id") or ""), "")
    ]
    if st.button(
        f"변경한 메모 {len(changed_memos):,}건 저장",
        type="primary",
        use_container_width=True,
        disabled=not changed_memos,
        key="save_prospect_memos_v984",
    ):
        try:
            for prospect_id, memo in changed_memos:
                save_prospect_memo(prospect_id, memo)
            st.success(f"업체 메모 {len(changed_memos):,}건을 저장했습니다.")
            st.rerun()
        except Exception as exc:
            st.error(
                "메모를 저장하지 못했습니다. 관리자 설정에서 v9.8.4 "
                f"SQL 실행 여부를 확인해 주세요: {exc}"
            )


def render_prospect_db_center(owner_user_id: str = "") -> None:
    st.markdown("## DB발굴")
    st.caption(
        "전년 동월 가입자 변동(축적된 경우)과 최근 월 순취득을 함께 보며, "
        "전화 또는 휴대전화가 확인된 업체를 찾습니다."
    )
    recommended_start_page = _render_search_history(owner_user_id)

    with st.form("prospect_search_v984"):
        col1, col2, col3, col4 = st.columns(4)
        region_name = col1.selectbox(
            "수집 지역",
            list(REGION_CODES.keys()),
            key="prospect_region_v984",
        )
        business_type_name = col2.selectbox(
            "사업자 유형",
            list(BUSINESS_TYPE_OPTIONS.keys()),
            key="prospect_business_type_v984",
        )
        target_count = col3.selectbox(
            "필요한 업체 수",
            [10, 20, 30, 50],
            index=2,
            key="prospect_target_v984",
        )
        minimum_employees = col4.number_input(
            "최소 가입자 수",
            min_value=1,
            max_value=300,
            value=3,
            step=1,
            key="prospect_min_employee_v984",
        )
        with st.expander("검색 범위 조정", expanded=True):
            page_col1, page_col2 = st.columns(2)
            start_page = page_col1.number_input(
                "시작 페이지",
                min_value=1,
                max_value=100000,
                value=int(recommended_start_page),
                step=1,
                help=(
                    "이미 1~10페이지를 조회했다면 11을 입력할 수 있습니다."
                ),
                key="prospect_start_page_v984",
            )
            end_page = page_col2.number_input(
                "종료 페이지",
                min_value=1,
                max_value=100000,
                value=min(100000, int(recommended_start_page) + 9),
                step=1,
                help="시작 페이지부터 종료 페이지까지 순서대로 조회합니다.",
                key="prospect_end_page_v984",
            )
            growth_only = st.checkbox(
                "고용 증가 신호 사업장만 표시",
                value=True,
                help=(
                    "전년 동월 가입자 증가가 확인됐거나 최근 월 순취득이 "
                    "1명 이상인 사업장만 연락처 검색 대상으로 사용합니다."
                ),
                key="prospect_growth_only_v984",
            )
            industry_categories = st.multiselect(
                "업종 필터",
                list(INDUSTRY_FILTER_OPTIONS),
                default=[],
                help=(
                    "선택하지 않으면 전체 업종을 검색합니다. 개인사업자 "
                    "후보에서 병원·음식점·서비스업 등을 골라 검색할 수 "
                    "있습니다."
                ),
                key="prospect_industry_categories_v984",
            )
        search_clicked = st.form_submit_button(
            f"연락 가능한 성장기업 {target_count}개 찾기",
            type="primary",
            use_container_width=True,
            disabled=not service_key_status()["configured"],
        )
        st.caption(
            "목표 업체 수를 채우거나 지정한 종료 페이지에 도달할 때까지 "
            "검색합니다. 최근 월 고용 신호를 먼저 확인하므로 이전 버전보다 "
            "불필요한 과거 사업장 조회가 줄어듭니다."
        )

    business_type = BUSINESS_TYPE_OPTIONS[business_type_name]
    growth_basis = "combined"
    effective_growth_only = bool(growth_only)
    page_state_key = (
        f"prospect_next_page_v984_{owner_user_id}_{region_name}_{business_type}"
    )
    page_count = int(end_page) - int(start_page) + 1
    if search_clicked and page_count <= 0:
        st.error("종료 페이지는 시작 페이지보다 크거나 같아야 합니다.")
        search_clicked = False
    if search_clicked and page_count > 100:
        st.error("한 번에 조회할 수 있는 범위는 최대 100페이지입니다.")
        search_clicked = False
    if search_clicked:
        progress_bar = st.progress(
            0,
            text="검색 준비 중입니다.",
        )
        status_box = st.empty()
        progress_state = {"value": 0.0}

        def _progress(event: dict) -> None:
            stage = event.get("stage")
            if stage == "nps":
                current = int(event.get("pages_scanned") or 0)
                ratio = min(
                    0.55,
                    (current + 1) / max(1, page_count) * 0.55,
                )
                progress_state["value"] = max(progress_state["value"], ratio)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        f"국민연금 {event.get('page', '')}페이지 기본·상세조회 중"
                    ),
                )
            elif stage == "nps_complete":
                current = int(event.get("pages_scanned") or 0)
                ratio = min(
                    0.60,
                    current / max(1, page_count) * 0.60,
                )
                progress_state["value"] = max(progress_state["value"], ratio)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        f"국민연금 {event.get('page', '')}페이지 확인 완료"
                    ),
                )
            elif stage == "employment":
                progress_state["value"] = max(progress_state["value"], 0.62)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        "최근 월 고용 증가 신호 확인 중 "
                        f"({event.get('page', '')}페이지)"
                    ),
                )
            elif stage == "employment_complete":
                progress_state["value"] = max(progress_state["value"], 0.68)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        f"고용자료 확인 {event.get('checked', 0)}건 · "
                        f"확인 불가 {event.get('unavailable', 0)}건"
                    ),
                )
            elif stage == "quick_contact":
                progress_state["value"] = max(progress_state["value"], 0.72)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        "카카오·네이버 공개검색·인허가 대표전화 확인 "
                        f"{event.get('checked', 0)}건"
                    ),
                )
            elif stage == "full_contact":
                progress_state["value"] = max(progress_state["value"], 0.88)
                progress_bar.progress(
                    progress_state["value"],
                    text=(
                        "공식 홈페이지 정밀 확인 "
                        f"{event.get('checked', 0)}건"
                    ),
                )
            status_box.caption(
                f"현재 확인된 대표전화 업체: {event.get('found', 0)}건"
            )

        with st.spinner(
            "기존 고객·저장 영업후보를 제외하고 성장기업을 찾고 있습니다."
        ):
            result = collect_contactable_growth_companies(
                REGION_CODES[region_name],
                target_count=int(target_count),
                start_page=int(start_page),
                max_pages=page_count,
                minimum_employees=int(minimum_employees),
                business_type=business_type,
                growth_only=effective_growth_only,
                growth_basis=growth_basis,
                industry_categories=list(industry_categories),
                progress=_progress,
            )
        progress_bar.progress(1.0, text="검색을 완료했습니다.")
        status_box.empty()
        try:
            result_stats = result.get("stats") or {}
            save_search_history(
                owner_user_id,
                region=region_name,
                region_code=REGION_CODES[region_name],
                business_type=business_type,
                start_page=int(
                    result.get("searched_start_page") or start_page
                ),
                end_page=int(
                    result.get("searched_end_page") or end_page
                ),
                target_count=int(target_count),
                minimum_employees=int(minimum_employees),
                growth_only=effective_growth_only,
                growth_basis=growth_basis,
                industry_categories=list(industry_categories),
                found_count=int(result.get("found_count") or 0),
                pages_scanned=int(result_stats.get("pages_scanned") or 0),
                elapsed_seconds=float(
                    result_stats.get("elapsed_seconds") or 0
                ),
            )
        except Exception as exc:
            result["history_warning"] = str(exc)
        st.session_state["prospect_result_v984"] = result
        st.session_state[page_state_key] = int(
            result.get("next_page") or int(end_page) + 1
        )

    result = st.session_state.get("prospect_result_v984")
    if result:
        stats = result.get("stats") or {}
        metric_cols = st.columns(5)
        metric_cols[0].metric(
            "확인한 사업장",
            f"{stats.get('basic_received', 0):,}건",
        )
        metric_cols[1].metric(
            "고용 증가 신호",
            f"{stats.get('growth_candidates', 0):,}건",
        )
        metric_cols[2].metric(
            "기존 DB 제외",
            f"{stats.get('saved_prospect_excluded', 0):,}건",
        )
        metric_cols[3].metric(
            "전화 확인",
            f"{result.get('found_count', 0):,}건",
        )
        metric_cols[4].metric(
            "다음 검색 페이지",
            f"{result.get('next_page', 1):,}",
        )
        st.caption(
            f"우선순위: {result.get('priority_basis', '')} · "
            f"조회 페이지 {result.get('searched_start_page', start_page)}"
            f"~{result.get('searched_end_page', end_page)} · "
            f"상세조회 대상 {stats.get('detail_targets', 0):,}건 · "
            f"고용자료 확인 {stats.get('employment_checked', 0):,}건 · "
            f"고용자료 확인 불가 "
            f"{stats.get('employment_unavailable', 0):,}건 · "
            f"연락처 확인 대상 {stats.get('contact_checked', 0):,}건 · "
            f"업종 제외 {stats.get('industry_excluded', 0):,}건 · "
            f"검색시간 {stats.get('elapsed_seconds', 0):,.1f}초"
        )
        st.info(
            f"다음 검색 권장 시작 페이지는 "
            f"{result.get('next_page', int(end_page) + 1):,}입니다. "
            "모든 사용자가 저장한 기존 영업후보는 자동 제외됩니다."
        )
        if result.get("duplicate_warning"):
            st.warning(
                "기존 DB 중복확인 일부를 완료하지 못했습니다: "
                f"{result['duplicate_warning']}"
            )
        if result.get("history_warning"):
            st.warning(
                "검색 결과는 정상입니다. 페이지 이력은 저장하지 "
                "못했습니다. 관리자 설정에서 v9.8.4 SQL 실행 여부를 "
                f"확인해 주세요: {result['history_warning']}"
            )

        items = list(result.get("items") or [])
        if not items:
            st.info(
                "선택한 범위에서 고용 증가 신호와 공개 대표전화 조건을 "
                "모두 충족한 업체를 확인하지 못했습니다. 다음 페이지를 "
                "이어 조회하거나 고용 증가 신호 필터를 해제해 비교할 수 있습니다."
            )
        else:
            st.markdown("### 이번에 찾은 영업후보")
            display = _display_frame(items)
            display["대표전화"] = display["대표전화"].map(normalize_phone)
            display = display[display["대표전화"] != ""].reset_index(drop=True)
            excel_columns = [
                column
                for column in display.columns
                if column not in {"선택", "source_key"}
            ]
            st.download_button(
                "이번 발굴결과 엑셀 다운로드",
                data=_excel_bytes(
                    display[excel_columns],
                    "DB발굴 결과",
                ),
                file_name=(
                    f"OASIS_DB발굴_{region_name}_"
                    f"{result.get('searched_start_page', start_page)}-"
                    f"{result.get('searched_end_page', end_page)}.xlsx"
                ),
                mime=(
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                use_container_width=True,
                key=(
                    "prospect_result_excel_v984_"
                    f"{result.get('next_page', 1)}"
                ),
            )
            visible_columns = [
                "선택",
                "사업장명",
                "사업자유형",
                "대표전화",
                "전화출처",
                "지역",
                "주소",
                "업종분류",
                "업종명",
                "자료생성년월",
                "가입자수",
                "전년가입자수",
                "전년대비고용증가",
                "신규취득자수",
                "상실가입자수",
                "최근월순취득",
                "고용판정",
                "고용자료상태",
                "영업주제",
                "추천등급",
                "초회전화스크립트",
                "source_key",
            ]
            edited = st.data_editor(
                display[visible_columns],
                use_container_width=True,
                hide_index=True,
                disabled=[
                    column
                    for column in visible_columns
                    if column != "선택"
                ],
                column_config={
                    "선택": st.column_config.CheckboxColumn("저장"),
                    "초회전화스크립트": st.column_config.TextColumn(
                        "초회 전화 스크립트",
                        width="large",
                    ),
                    "source_key": None,
                },
                key=f"prospect_editor_v984_{result.get('next_page', 1)}",
            )
            selected_keys = set(
                edited.loc[edited["선택"] == True, "source_key"].tolist()
            )
            selected_items = [
                item
                for item in items
                if item.get("source_key") in selected_keys
                and normalize_phone(item.get("대표전화"))
            ]
            if st.button(
                f"선택한 {len(selected_items):,}개 업체 영업DB에 저장",
                type="primary",
                use_container_width=True,
                disabled=not selected_items,
                key="save_prospects_v984",
            ):
                try:
                    saved_count = save_prospects(
                        selected_items,
                        owner_user_id,
                    )
                    st.success(f"{saved_count:,}개 업체를 저장했습니다.")
                    st.session_state.pop("prospect_result_v984", None)
                    st.rerun()
                except Exception as exc:
                    st.error(f"영업후보 저장 실패: {exc}")

        failures = result.get("failures") or []
        if failures:
            with st.expander("검색 중 확인하지 못한 항목"):
                st.dataframe(
                    pd.DataFrame(failures),
                    use_container_width=True,
                    hide_index=True,
                )

    st.divider()
    _render_clean_saved_prospects(owner_user_id)


def render_prospect_admin_settings() -> None:
    st.markdown("## 영업후보 데이터 연결 관리")
    st.caption(
        "국민연금·카카오·네이버·인허가 API와 Supabase 테이블을 "
        "관리자가 점검하는 화면입니다."
    )
    nps_status = service_key_status()
    contact_status = api_statuses()
    status_cols = st.columns(4)
    status_cols[0].metric(
        "국민연금",
        "키 등록" if nps_status["configured"] else "미등록",
    )
    status_cols[1].metric(
        "카카오",
        "키 등록" if contact_status["kakao"]["configured"] else "미등록",
    )
    status_cols[2].metric(
        "네이버",
        "키 등록" if contact_status["naver"]["configured"] else "미등록",
    )
    status_cols[3].metric(
        "인허가",
        (
            f"{contact_status['localdata'].get('service_count', 0)}종"
            if contact_status["localdata"]["configured"]
            else "미등록"
        ),
    )

    test_col1, test_col2 = st.columns(2)
    nps_test = test_col1.button(
        "국민연금 연결 점검",
        use_container_width=True,
        disabled=not nps_status["configured"],
        key="admin_nps_test_v980",
    )
    contact_test = test_col2.button(
        "연락처 API 연결 점검",
        use_container_width=True,
        disabled=not all(
            row.get("configured") for row in contact_status.values()
        ),
        key="admin_contact_test_v980",
    )
    if nps_test:
        st.session_state["admin_nps_result_v980"] = test_nps_connection("11")
    if contact_test:
        st.session_state["admin_contact_result_v980"] = test_connections()

    nps_result = st.session_state.get("admin_nps_result_v980")
    if nps_result:
        st.success(nps_result.get("message", "연결 완료")) if nps_result.get(
            "ok"
        ) else st.error(nps_result.get("message", "연결 실패"))
    contact_result = st.session_state.get("admin_contact_result_v980")
    if contact_result:
        rows = []
        for key, label in (
            ("kakao", "카카오 로컬"),
            ("naver", "네이버 검색"),
            ("localdata", "승인 인허가 API"),
        ):
            source = (contact_result.get("sources") or {}).get(key) or {}
            rows.append(
                {
                    "연결": label,
                    "상태": source.get("status", "-"),
                    "결과": source.get("message", "-"),
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown("### Supabase 영업후보 테이블")
    prospect_status = prospect_table_status()
    contacts_status = contact_table_status()
    history_status = search_history_table_status()
    db_cols = st.columns(3)
    db_cols[0].success(prospect_status[1]) if prospect_status[0] else db_cols[
        0
    ].warning(prospect_status[1])
    db_cols[1].success(contacts_status[1]) if contacts_status[0] else db_cols[
        1
    ].warning(contacts_status[1])
    db_cols[2].success(history_status[1]) if history_status[0] else db_cols[
        2
    ].warning(history_status[1])

    for path_name, label in (
        ("supabase_v960_prospect_db.sql", "영업후보DB SQL 다운로드"),
        (
            "supabase_v970_contact_enrichment.sql",
            "연락처DB SQL 다운로드",
        ),
        (
            "supabase_v984_db_discovery.sql",
            "DB발굴 검색이력·메모 SQL 다운로드",
        ),
    ):
        path = BASE_DIR / path_name
        if path.exists():
            st.download_button(
                label,
                data=path.read_bytes(),
                file_name=path_name,
                mime="text/plain",
                use_container_width=True,
                key=f"admin_download_{path_name}",
            )
