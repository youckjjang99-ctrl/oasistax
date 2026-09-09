"""Focused CRETOP upload flow: analyse, review, then explicitly save.

No customer or snapshot writes take place while analysing a document.
"""
from __future__ import annotations

import hashlib
import math
import re
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

from address_tools import enrich_address_fields
from cloud_sync import sync_customer_snapshot
from cretop_runner import run_cretop_worker
from customer_history import save_customer_snapshot
from matching_preferences import INTEREST_OPTIONS, get_matching_preferences, save_matching_preferences
from runtime_error_log import safe_public_error, write_runtime_error
from utils import (
    append_cretop_to_user_customer_db, check_user_customer_duplicate,
    get_user_cumulative_db_path,
    make_upload_filename, normalize_business_no, refresh_existing_customer_from_cretop,
)

FINANCIAL_FIELDS = (
    "매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계",
    "가지급금", "단기대여금", "장기대여금", "가수금",
)
BASIC_FIELDS = ("업체명", "대표자명", "사업자등록번호", "업종명", "사업장 소재지")
EXTRA_FIELDS = ("법인등록번호", "설립일", "종업원수", "기업유형", "기업규모")


def reset_cretop_upload_state(state=None):
    """Uploader callback; changing/removing a file invalidates every draft value."""
    state = st.session_state if state is None else state
    keep = {"cretop_pdf_uploader", "cretop_manager_name"}
    for key in list(state):
        if str(key).startswith("cretop_") and key not in keep:
            state.pop(key, None)


def has_financial_data(data):
    def numeric(value):
        try:
            return not isinstance(value, bool) and math.isfinite(float(str(value).replace(",", "")))
        except (ValueError, TypeError):
            return False
    rows = [data] + [row for row in (data.get("재무연도별") or []) if isinstance(row, dict)]
    return any(numeric(row.get(key)) for row in rows for key in FINANCIAL_FIELDS)


def prepare_registration_data(original, edits):
    """Only reviewed identity fields are editable; unknowns stay unknown."""
    data = dict(original or {})
    for key in BASIC_FIELDS + EXTRA_FIELDS:
        if key in edits:
            data[key] = str(edits[key] if edits[key] is not None else "").strip()
    data["사업자등록번호"] = normalize_business_no(data.get("사업자등록번호", ""))
    if not str(data.get("업체명") or "").strip():
        return data, "업체명을 확인해 주세요. 빈 기업은 등록하지 않습니다."
    if len(re.sub(r"\D", "", data["사업자등록번호"])) != 10:
        return data, "사업자등록번호 10자리를 확인해 주세요."
    employee = data.get("종업원수")
    if employee not in (None, ""):
        text = str(employee).replace(",", "").strip()
        if not re.fullmatch(r"\d+", text):
            return data, "종업원수는 0 이상의 정수로 입력하거나 공란으로 두세요."
        data["종업원수"] = int(text)
    if "종업원수" in edits:
        data["상시근로자수"] = data.get("종업원수", "")
    if "설립일" in edits:
        establishment = str(data.get("설립일") or "")
        data["설립년도"] = establishment[:4] if re.match(r"^\d{4}[-./]", establishment) else ""
    if "사업장 소재지" in edits and edits["사업장 소재지"] != (original or {}).get("사업장 소재지"):
        data.pop("시도", None)
        data.pop("시군구", None)
        data = enrich_address_fields(data)
    data["사업자유형"] = "법인사업자"
    return data, ""


def save_reviewed_registration(user_id, manager_name, pdf_path, data, *, confirm_update=False):
    """Recheck duplicates at save time, and never silently update another record."""
    data, error = prepare_registration_data(data, {})
    if error:
        return {"saved": False, "message": error}
    duplicate = check_user_customer_duplicate(
        user_id, data["사업자등록번호"], company_name=data.get("업체명", ""),
        representative_name=data.get("대표자명", ""),
    )
    if duplicate and not confirm_update:
        return {"saved": False, "needs_confirmation": True,
                "message": "이미 등록된 기업입니다. 기존 기업 갱신에 동의한 뒤 저장해 주세요."}
    if duplicate:
        ok, message, _ = refresh_existing_customer_from_cretop(
            user_id, data, reviewed_fields=BASIC_FIELDS + EXTRA_FIELDS,
        )
        if not ok:
            return {"saved": False, "message": message}
        count = 0
    else:
        _, count, message, _, _ = append_cretop_to_user_customer_db(
            pdf_path, user_id, manager_name=manager_name, duplicate_action="skip", extracted_data=data,
        )
        if count <= 0:
            return {"saved": False, "message": message}

    # Auxiliary failures do not pretend the customer save failed or discard its data.
    warnings = []
    try:
        cloud_ok, _ = sync_customer_snapshot(
            user_id, data, source="cretop_existing_customer_refresh" if duplicate else "cretop_registration",
            manager_name=manager_name,
        )
    except Exception as exc:
        write_runtime_error("cretop_cloud_sync", exc)
        cloud_ok = False
    if not cloud_ok:
        warnings.append("고객 기본정보는 로컬에 저장됐지만 클라우드 저장은 확인되지 않았습니다. 관리자 동기화 상태를 확인해 주세요.")
    try:
        # A report with no finances must not replace an older financial snapshot with blanks.
        if has_financial_data(data):
            from stock_valuation import save_cretop_financial_snapshot
            save_cretop_financial_snapshot(user_id, data)
        save_customer_snapshot(user_id, data, source="cretop")
    except Exception as exc:
        write_runtime_error("cretop_snapshot_save", exc)
        warnings.append("고객은 저장됐지만 분석 이력 또는 재무자료 연결을 확인해 주세요.")
    try:
        from enterprise_documents import register_existing_enterprise_document
        register_existing_enterprise_document(
            pdf_path, user_id=user_id, business_no=data["사업자등록번호"],
            company_name=data["업체명"], document_type="cretop_report",
            analysis_summary="크레탑 보고서의 확인된 기업정보를 등록했습니다.",
            extracted_fields={key: data.get(key) for key in FINANCIAL_FIELDS},
        )
    except Exception as exc:
        write_runtime_error("cretop_document_link", exc)
        warnings.append("기업정보는 저장됐지만 PDF 첨부 연결은 완료되지 않았습니다. 등록기업 자료 추가에서 다시 연결해 주세요.")
    return {"saved": True, "new_count": count, "message": message,
            "cloud_saved": cloud_ok, "warnings": warnings, "data": data}


def _go_to_company_documents():
    st.session_state["enterprise_registration_route"] = "등록기업에 자료 추가"
    st.session_state["enterprise_information_preferred_business_no"] = st.session_state.get("cretop_saved_business_no", "")
    st.session_state.pop("enterprise_information_asset_customer", None)


def _render_saved_result(user_id):
    result = st.session_state.get("cretop_save_result")
    if not result:
        return
    if result.get("cloud_saved"):
        st.success("기업정보 클라우드 저장 완료 · " + result["message"])
    else:
        st.warning("기업정보 로컬 저장 완료 · 클라우드 상태 확인 필요")
    for warning in result.get("warnings", []):
        st.warning(warning)
    if st.session_state.get("cretop_preferences_error"):
        st.warning("기업은 저장됐지만 정책자금 매칭설정 저장에 실패했습니다. 기존 설정을 유지하고 다시 확인해 주세요.")
    left, right = st.columns(2)
    with left:
        st.button("이 기업에 추가자료 등록", on_click=_go_to_company_documents,
                  key="cretop_open_documents", width="stretch")
    with right:
        if st.button("최신 누적 고객DB 다운로드 준비", key="cretop_prepare_latest_download", width="stretch"):
            path = get_user_cumulative_db_path(user_id)
            if path.is_file():
                with path.open("rb") as file:
                    st.download_button("최신 내 누적 고객DB 다운로드", file,
                                       file_name="고객DB누적.xlsx", width="stretch",
                                       key="cretop_latest_download_after_save")


def _matching_fields(preferences):
    values = {}
    with st.expander("정책자금 매칭설정 · 선택사항", expanded=False):
        st.caption("기업 등록에 필수는 아닙니다. 기존 매칭·제외 기능은 그대로 사용할 수 있습니다.")
        apply_settings = st.checkbox("아래 매칭설정도 함께 저장", key="cretop_save_matching_preferences")
        for label, field, key in (
            ("매칭키워드", "매칭키워드", "matching_keywords"),
            ("제외키워드", "제외키워드", "exclusion_keywords"),
        ):
            values[key] = st.text_area(label, value=", ".join(preferences.get(field, []) or []),
                                       height=70, key="cretop_" + key)
        values["interest_fields"] = st.multiselect(
            "관심지원분야", INTEREST_OPTIONS,
            default=[v for v in (preferences.get("관심지원분야", []) or []) if v in INTEREST_OPTIONS],
            key="cretop_interest_fields",
        )
        for col, label, key in zip(st.columns(3), ("자금사용목적", "투자예정금액", "투자예정시기"),
                                   ("fund_purpose", "planned_amount", "planned_timing")):
            with col:
                values[key] = st.text_input(label, value=str(preferences.get(label) or ""), key="cretop_" + key)
    return values if apply_settings else None


def render_cretop_registration(user_id, user_name, upload_dir):
    st.caption("① PDF 업로드·분석 → ② 기본정보 확인 → ③ 기업 등록")
    _render_saved_result(user_id)
    st.markdown("#### 1. 크레탑 보고서 업로드")
    uploaded = st.file_uploader("크레탑 기업종합보고서 PDF", type=["pdf"],
                                key="cretop_pdf_uploader", on_change=reset_cretop_upload_state)
    st.caption("스캔·이미지 PDF도 읽습니다. 재무자료가 없어도 기업 기본정보를 등록할 수 있습니다.")
    with st.expander("담당자 설정", expanded=False):
        manager = st.text_input("담당자명", value=user_name or "", key="cretop_manager_name")
    if uploaded is None:
        return
    if st.button("PDF 분석하기", type="primary", key="cretop_analyze_button", width="stretch"):
        reset_cretop_upload_state()
        try:
            path = Path(upload_dir) / make_upload_filename(uploaded.name).replace("업로드고객DB_", "크레탑PDF_")
            path.parent.mkdir(parents=True, exist_ok=True)
            uploaded.seek(0)
            with path.open("wb") as destination:
                shutil.copyfileobj(uploaded, destination, length=1024 * 1024)
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            with st.status("문서를 분석하고 있습니다. 이미지 PDF는 문자 인식에 시간이 걸릴 수 있습니다.", expanded=True) as status:
                st.write("분석 중에는 고객정보가 저장되거나 변경되지 않습니다.")
                # One full parse avoids reading/OCRing the identity pages twice.
                data, error, logs = run_cretop_worker(path, mode="full", timeout=240)
                if data:
                    data = enrich_address_fields(data)
                meaningful = any(str(data.get(key) or "").strip() for key in BASIC_FIELDS) if data else False
                if not error and not meaningful:
                    error = "기업 기본정보를 읽지 못했습니다. 빈 결과는 저장하지 않습니다."
                st.session_state["cretop_pdf_save_path"] = str(path)
                st.session_state["cretop_pdf_hash"] = digest
                st.session_state["cretop_extracted_data"] = data
                st.session_state["cretop_extract_error"] = error
                st.session_state["cretop_analysis_logs"] = logs[-20:]
                status.update(label="기본정보 확인이 필요합니다." if error else "분석 완료 · 아직 등록 전입니다.",
                              state="error" if error else "complete", expanded=bool(error))
        except Exception as exc:
            write_runtime_error("cretop_analysis", exc)
            st.session_state["cretop_extract_error"] = safe_public_error(exc, "PDF 분석을 완료하지 못했습니다. 파일을 확인해 주세요.")

    error = st.session_state.get("cretop_extract_error", "")
    data = st.session_state.get("cretop_extracted_data")
    if error:
        if data and any(str(data.get(key) or "").strip() for key in BASIC_FIELDS):
            st.warning(error + " 확인 가능한 기본정보를 아래에서 보완해 주세요.")
        else:
            st.error(error)
            st.info("빈 기업정보는 저장하지 않았습니다. 원본 PDF 또는 문자 인식 환경을 확인한 뒤 다시 분석해 주세요.")
            return
    if not data or st.session_state.get("cretop_save_result"):
        return
    st.markdown("#### 2. 기본정보 확인")
    metadata = data.get("_extraction") or {}
    if metadata.get("ocr_pages"):
        st.caption("이미지 문자를 인식한 결과입니다. 업체명과 사업자등록번호를 원본과 확인해 주세요.")
    if metadata.get("warnings"):
        st.warning("일부 페이지는 읽지 못했거나 추가 확인이 필요합니다. 아래 결과를 원본과 비교해 주세요.")
    if not has_financial_data(data):
        st.info("문서에서 재무정보가 확인되지 않았습니다. 재무값은 공란으로 두고 기본정보만 등록합니다.")
    prefs_key = "cretop_loaded_preferences"
    if prefs_key not in st.session_state:
        try:
            st.session_state[prefs_key] = get_matching_preferences(user_id, data.get("사업자등록번호", ""))
        except Exception:
            st.session_state[prefs_key] = None
    preferences = st.session_state[prefs_key]
    if preferences is None:
        st.warning("기존 정책자금 매칭설정을 읽지 못했습니다. 이번 등록에서는 기존 설정을 변경하지 않습니다.")
    if "cretop_needs_confirmation" not in st.session_state:
        st.session_state["cretop_needs_confirmation"] = check_user_customer_duplicate(
            user_id, data.get("사업자등록번호", ""), company_name=data.get("업체명", ""),
            representative_name=data.get("대표자명", ""),
        )
    with st.form("cretop_review_and_save"):
        edits = {}
        for pair in (("업체명", "대표자명"), ("사업자등록번호", "업종명")):
            for col, field in zip(st.columns(2), pair):
                with col:
                    edits[field] = st.text_input(field + (" *" if field in ("업체명", "사업자등록번호") else ""),
                                                value=str(data.get(field) or ""), key="cretop_edit_" + field)
        edits["사업장 소재지"] = st.text_input("사업장 소재지", value=str(data.get("사업장 소재지") or ""), key="cretop_edit_사업장 소재지")
        with st.expander("추가 기업정보·재무·인증정보 확인", expanded=False):
            for field in EXTRA_FIELDS:
                value = data.get(field)
                edits[field] = st.text_input(field, value="" if value is None else str(value), key="cretop_edit_" + field)
            st.dataframe(pd.DataFrame([
                {"항목": field, "추출값": "미확인" if data.get(field) in (None, "") else str(data[field])}
                for field in FINANCIAL_FIELDS + ("벤처", "이노비즈", "메인비즈", "기업부설연구소", "연구개발전담부서", "특허보유", "상표")
            ]), hide_index=True, width="stretch")
        preferences_values = _matching_fields(preferences) if preferences is not None else None
        confirm = False
        if st.session_state["cretop_needs_confirmation"]:
            st.info("기존 등록기업이 확인됐습니다. 새 행을 만들지 않고 검토한 기본정보와 확인된 재무값을 갱신합니다. 공란은 기존 값을 유지합니다.")
            confirm = st.checkbox("기존 기업의 정보 갱신에 동의합니다.", key="cretop_confirm_update")
        st.markdown("#### 3. 기업정보 저장")
        st.caption("업체명·사업자등록번호는 필수입니다. 확인한 뒤 저장 버튼을 눌러야 등록됩니다.")
        submitted = st.form_submit_button("확인한 기업정보 저장", type="primary", width="stretch")
    if not submitted:
        return
    if not manager.strip():
        st.warning("담당자명을 확인해 주세요.")
        return
    reviewed, validation_error = prepare_registration_data(data, edits)
    if validation_error:
        st.error(validation_error)
        return
    try:
        with st.spinner("확인한 기업정보를 저장하고 있습니다..."):
            result = save_reviewed_registration(user_id, manager.strip(),
                                                Path(st.session_state["cretop_pdf_save_path"]), reviewed,
                                                confirm_update=confirm)
        if result.get("needs_confirmation"):
            st.session_state["cretop_needs_confirmation"] = True
            st.rerun()
        if not result.get("saved"):
            st.error(result["message"])
            return
        if preferences_values is not None:
            # A corrected identity must not receive another company's loaded preferences.
            if normalize_business_no(reviewed["사업자등록번호"]) == normalize_business_no(data.get("사업자등록번호", "")):
                try:
                    save_matching_preferences(user_id, reviewed["사업자등록번호"],
                                              company_name=reviewed["업체명"], **preferences_values)
                except Exception as exc:
                    write_runtime_error("cretop_matching_preferences", exc)
                    st.session_state["cretop_preferences_error"] = True
        st.session_state["cretop_save_result"] = result
        st.session_state["cretop_saved_business_no"] = reviewed["사업자등록번호"]
        st.rerun()
    except Exception as exc:
        write_runtime_error("cretop_customer_registration", exc)
        st.error(safe_public_error(exc, "기업정보 저장을 완료하지 못했습니다. 다시 확인해 주세요."))
