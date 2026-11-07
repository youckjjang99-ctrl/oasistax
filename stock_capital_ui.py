"""Company-scoped capital-history review; never a gift-tax calculation."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import re

import pandas as pd
import streamlit as st

from stock_capital import build_capital_share_plan, capital_source_signature


KINDS = {"unknown": "미확인", "paid_increase": "유상증자", "bonus_increase": "무상증자",
         "paid_decrease": "유상감자", "bonus_decrease": "무상감자"}


def capital_registry_source(registry):
    fields = ("registry_parser_version", "capital_history", "capital_events", "capital_history_warnings",
              "capital_history_review_required", "발행주식총수", "자본금", "1주당액면가액")
    return {key: deepcopy(registry[key]) for key in fields if key in registry}


def capital_review_required(source):
    return bool(source.get("capital_events") or source.get("capital_history")
                or source.get("capital_history_review_required"))


def _number(value):
    try:
        result = float(str(value).replace(",", ""))
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def capital_input_signature(inputs):
    fields = {key: inputs.get(key) for key in ("business_no", "valuation_date", "current_shares", "capital")}
    fields["annual_rows"] = [{key: row.get(key) for key in ("year", "shares", "adjusted_net_income")}
                             for row in inputs.get("annual_rows", [])]
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()


def validate_capital_inputs(inputs):
    source = inputs.get("capital_source") or {}
    if not capital_review_required(source):
        return
    review = inputs.get("capital_review") or {}
    if not review.get("confirmed") or review.get("business_no") != inputs.get("business_no"):
        raise ValueError("등록 등기의 자본변동·평가기준일별 주식수 검토를 먼저 완료해주세요.")
    if review.get("source_signature") != capital_source_signature(source):
        raise ValueError("등기 자본정보가 변경되었습니다. 기존 평가를 보존했으며 자본변동을 다시 검토해야 합니다.")
    if review.get("input_signature") != capital_input_signature(inputs):
        raise ValueError("평가기준일·주식수·순손익액이 변경되었습니다. 자본변동 검토를 다시 확인해주세요.")
    plan = review.get("plan") or {}
    rebuilt = build_capital_share_plan(source, inputs.get("valuation_date"), plan.get("annual_rows", []),
                                       events=plan.get("events"), history_confirmed=True)
    if not review.get("manual"):
        expected = {str(row["year"]): row.get("shares") for row in rebuilt["annual_rows"]}
        if (rebuilt.get("unsupported") or rebuilt.get("requires_income_adjustment")
                or inputs.get("current_shares") != rebuilt.get("current_shares")
                or any(row.get("shares") != expected.get(str(row.get("year"))) for row in inputs.get("annual_rows", []))):
            raise ValueError("등기·사업연도와 확정 주식수가 일치하지 않습니다. 자본변동 검토를 다시 진행해주세요.")
    if (plan.get("unsupported") or plan.get("requires_income_adjustment")) and not (
        review.get("manual") and len(str(review.get("note", "")).strip()) >= 8
    ):
        raise ValueError("자동 처리 범위 밖의 증감자는 전문가 확정값과 검토 근거가 필요합니다.")


def _state_inputs(state):
    return {"business_no": state.get("stock_business_no", ""),
            "valuation_date": str(state.get("stock_valuation_date", "")),
            "current_shares": _number(state.get("stock_current_shares")),
            "capital": _number(state.get("stock_capital")),
            "annual_rows": [{"year": str(state.get(f"stock_year_{i}", "")),
                             "shares": _number(state.get(f"stock_shares_{i}")),
                             "adjusted_net_income": _number(state.get(f"stock_net_income_{i}"))}
                            for i in range(1, 4)]}


def apply_capital_plan(state, *, user_id, business_no, source, plan, manual_rows=None, manual_current=None, note=""):
    """Prepare all updates before touching state; old saved versions stay intact."""
    if state.get("stock_business_no") != business_no or plan.get("valuation_date") != state.get("stock_valuation_date"):
        raise ValueError("기업 또는 평가기준일이 변경되었습니다. 다시 확인해주세요.")
    if plan.get("source_signature") != capital_source_signature(source):
        raise ValueError("검토한 등기 원본이 변경되었습니다. 다시 확인해주세요.")
    if isinstance(state.get("stock_last_registry_data"), dict) and capital_source_signature(
        capital_registry_source(state["stock_last_registry_data"])
    ) != capital_source_signature(source):
        raise ValueError("현재 기업의 등기 원천이 변경되었습니다. 다시 검토해주세요.")
    manual = manual_rows is not None
    if (plan.get("unsupported") or plan.get("requires_income_adjustment")) and not manual:
        raise ValueError("이 자본변동은 자동 반영할 수 없습니다. 전문가 확정 경로를 이용해주세요.")
    if manual and len(str(note).strip()) < 8:
        raise ValueError("전문가가 확인한 원문·주식수·순손익 조정 근거를 입력해주세요.")
    current = _number(manual_current if manual else plan.get("current_shares"))
    if current is None or current <= 0 or not current.is_integer():
        raise ValueError("평가기준일 주식수를 양의 정수로 확인해주세요.")
    rows = manual_rows if manual else plan.get("annual_rows", [])
    years = [str(state.get(f"stock_year_{i}", "")) for i in range(1, 4)]
    if len(set(years)) != 3 or not all(re.fullmatch(r"\d{4}", value) for value in years):
        raise ValueError("계산 탭에 최근 3개 사업연도를 먼저 설정해주세요.")
    mapped = {str(row.get("year")): row for row in rows}
    if len(mapped) != 3 or set(mapped) != set(years):
        raise ValueError("자본 검토 대상 3개 사업연도가 계산 탭과 다릅니다.")
    updates = {"stock_current_shares": f"{int(current):,}"}
    if plan.get("current_capital") is not None:
        updates["stock_capital"] = f"{int(plan['current_capital']):,}"
    for index, year in enumerate(years, 1):
        count = _number(mapped[year].get("shares"))
        if count is None or count <= 0 or not count.is_integer():
            raise ValueError("각 사업연도의 확정 주식수를 양의 정수로 입력해주세요. 소수 환산은 별도 검토가 필요합니다.")
        updates[f"stock_shares_{index}"] = f"{int(count):,}"
        if manual:
            income = _number(mapped[year].get("adjusted_net_income"))
            if income is None or not income.is_integer():
                raise ValueError("전문가 확정 순손익액은 원 단위 정수로 입력해주세요.")
            updates[f"stock_net_income_{index}"] = f"{int(income):,}"
    proposed = {**dict(state), **updates}
    review = {"confirmed": True, "business_no": business_no, "owner_user_id": str(user_id),
              "source_signature": capital_source_signature(source), "plan": deepcopy(plan),
              "manual": manual, "note": str(note).strip(), "reviewed_at": datetime.now().isoformat(timespec="seconds"),
              "input_signature": capital_input_signature(_state_inputs(proposed))}
    state.update(updates)
    state["stock_capital_review"] = review
    state.pop("stock_last_result", None)
    state.pop("stock_last_inputs", None)
    return review


def render_stock_capital_review(user_id):
    state = st.session_state
    registry = state.get("stock_last_registry_data") or {}
    source = capital_registry_source(registry)
    if not source:
        return
    business = state.get("stock_business_no", "")
    if not re.fullmatch(r"\d{3}-\d{2}-\d{5}", str(business)):
        return
    with st.expander("증자·감자 및 평가기준일별 주식수 검토", expanded=bool(source.get("capital_events"))):
        if not source.get("registry_parser_version"):
            st.info("이전 방식으로 분석된 등기입니다. 등록 자료의 ‘등기 원본 다시 분석’으로 최신 주식수와 변동 이력을 확인하세요.")
            return
        st.caption("현재 주식수와 과거 연도 말·법정 환산 주식수를 구분합니다. 증자대금을 자산에 자동 가산하지 않으며 기존 원본·평가를 덮어쓰지 않습니다.")
        token = hashlib.sha256((str(user_id) + str(business) + capital_source_signature(source)
                                + capital_input_signature(_state_inputs(state))).encode()).hexdigest()[:16]
        for warning in source.get("capital_history_warnings", []):
            st.warning(str(warning))
        events = deepcopy(source.get("capital_events") or [])
        if events:
            for event in events:
                event["kind"] = KINDS.get(event.get("kind"), "미확인")
            edited = st.data_editor(pd.DataFrame(events), key=f"stock_capital_events:{token}", hide_index=True,
                                    use_container_width=True,
                                    column_order=["effective_date", "kind", "before_shares", "after_shares", "before_capital", "after_capital", "registered_at"],
                                    column_config={"kind": st.column_config.SelectboxColumn("변동 종류", options=list(KINDS.values())),
                                                   "effective_date": "실제 효력일", "registered_at": "등기일",
                                                   "before_shares": "변경 전 주식수", "after_shares": "변경 후 주식수",
                                                   "before_capital": "변경 전 자본금", "after_capital": "변경 후 자본금"},
                                    disabled=[key for key in events[0] if key not in ("kind", "effective_date", "before_shares", "after_shares")])
            events = edited.astype(object).where(pd.notna(edited), None).to_dict("records")
            for event in events:
                event["kind"] = next((key for key, label in KINDS.items() if label == event.get("kind")), "unknown")
            st.caption("변동 종류는 원문으로 확인해주세요. 등기 변경일과 실제 효력 발생일이 다르면 확인 후 수정하세요.")
        else:
            st.caption("이 원본에서 복수 자본변동은 추출되지 않았습니다. 평가대상 기간의 누락 여부를 확인하세요.")
        periods = []
        reviewed_periods = {str(row.get("year")): row for review in state.get("stock_tax_adjustment_review", [])
                            for row in review.get("annual_rows", [])}
        for index in range(1, 4):
            year = str(state.get(f"stock_year_{index}", ""))
            previous = reviewed_periods.get(year, {})
            periods.append({"year": year, "period_start": previous.get("period_start") or f"{year}-01-01",
                            "period_end": previous.get("period_end") or f"{year}-12-31"})
        periods = st.data_editor(pd.DataFrame(periods), hide_index=True, disabled=["year"],
                                 column_config={"year": "사업연도", "period_start": "사업연도 시작일", "period_end": "사업연도 종료일"},
                                 use_container_width=True, key=f"stock_capital_periods:{token}").to_dict("records")
        st.caption("사업연도는 세무조정 원문으로 확인하세요. 기본 1월~12월 표시는 결산일을 확정한 값이 아닙니다.")
        confirmed = st.checkbox("원본의 자본변동 이력이 평가대상 기간을 포함하며, 변동 종류·효력일·사업연도에 누락이 없음을 확인했습니다.", key=f"stock_capital_confirm:{token}")
        manual = st.checkbox("복수 증감자·단기 사업연도 등은 전문가 확정값으로 검토", key=f"stock_capital_manual:{token}")
        note, final_rows, final_current = "", None, None
        if manual:
            st.warning("증감자 순손익 가산·차감, 월할 및 연환산은 자동 추정하지 않습니다. 법정 검토가 끝난 최종 순손익액과 환산 주식수를 입력하세요.")
            final_current = st.text_input("전문가 확정 평가기준일 주식수", value=str(state.get("stock_current_shares", "")), key=f"stock_capital_final_current:{token}")
            final_rows = st.data_editor(pd.DataFrame(_state_inputs(state)["annual_rows"]), hide_index=True,
                                       column_config={"year": "사업연도", "shares": "전문가 확정 주식수", "adjusted_net_income": "전문가 확정 순손익액(원)"},
                                       disabled=["year"], use_container_width=True, key=f"stock_capital_final_rows:{token}").to_dict("records")
            note = st.text_area("자본변동 전문가 검토 근거", key=f"stock_capital_note:{token}")
        if st.button("평가기준일별 주식수 확인·반영", disabled=not confirmed, key=f"stock_capital_apply:{token}", use_container_width=True):
            try:
                plan = build_capital_share_plan(source, str(state.get("stock_valuation_date", "")), periods,
                                               events=events, history_confirmed=True)
                if (plan.get("unsupported") or plan.get("requires_income_adjustment")) and not manual:
                    for reason in plan.get("blocking_reasons", []):
                        st.warning(str(reason))
                apply_capital_plan(state, user_id=user_id, business_no=business, source=source, plan=plan,
                                   manual_rows=final_rows, manual_current=final_current, note=note)
                st.success("현재 주식수와 연도별 주식수를 구분해 반영했습니다. 평가기준일 현재 자산·부채는 별도 확인 후 다시 계산하세요.")
            except ValueError as exc:
                st.error(str(exc))
        review = state.get("stock_capital_review") or {}
        if review.get("business_no") == business:
            try:
                validate_capital_inputs({**_state_inputs(state), "capital_source": source, "capital_review": review})
                st.caption("현재 입력값에 대한 자본변동 검토가 완료되었습니다. 새 평가를 계산·저장하면 검토 근거도 함께 보존됩니다.")
            except ValueError as exc:
                st.warning(str(exc))


def render_registry_reanalysis(user_id):
    """Load originals only after an explicit click, never during navigation."""
    import stock_valuation as stock
    business = st.session_state.get("stock_business_no", "")
    if not re.fullmatch(r"\d{3}-\d{2}-\d{5}", str(business)):
        return
    token = hashlib.sha256((str(user_id) + str(business)).encode()).hexdigest()[:16]
    with st.expander("등기 원본 다시 분석 · 기존 자료 보존", expanded=False):
        st.caption("과거 방식으로 읽은 등기를 새 파서로 다시 분석합니다. 원본·이전 분석·평가값은 유지하며 새 등기값은 별도 확인 후 반영합니다.")
        if not st.checkbox("등록된 등기 원본 불러오기", key=f"stock_registry_originals:{token}"):
            return
        try:
            from enterprise_documents import load_enterprise_document_context
            context = load_enterprise_document_context(user_id, business,
                st.session_state.get("stock_company_name", ""), include_remote_types=("corporate_registry",))
            records = [row for row in context.get("records", []) if row.get("document_type") == "corporate_registry"]
        except Exception as exc:
            st.error(stock.safe_public_error(exc, "현재 기업의 등기 원본 목록을 확인하지 못했습니다."))
            return
        if not records:
            st.info("연결된 등기 원본이 없습니다. 기업정보등록에서 같은 기업에 원본을 다시 올려주세요.")
            return
        index = st.selectbox("다시 분석할 등기 원본", range(len(records)),
            format_func=lambda i: str(records[i].get("filename") or "등록 등기 원본"), key=f"stock_registry_original:{token}")
        if st.button("등기 원본 다시 분석", key=f"stock_registry_reanalyze:{token}", use_container_width=True):
            from registry_reanalysis import reanalyze_registered_registry
            from upload_progress import UploadProgress
            with UploadProgress(f"registry_reanalysis:{token}", "등록 등기 원본 다시 분석 중",
                                steps=("원본 확인", "새 등기 분석", "버전 보존"), ui=st) as task:
                try:
                    task.step("원본 확인", "현재 기업의 원본·무결성을 확인한 뒤 새 분석 버전을 보존합니다.")
                    selected = stock._find_stock_customer_row(stock._read_customers(user_id), business_no=business)
                    task.step("새 등기 분석", "최신 주식수와 변경 전후 이력을 다시 확인하고 있습니다.")
                    result = reanalyze_registered_registry(user_id, business, records[index]["record_id"], selected)
                    task.step("버전 보존", "원본과 이전 분석·평가는 그대로 유지했습니다.")
                    st.session_state["stock_last_registry_data"] = deepcopy(result["registry_data"])
                    st.session_state["stock_registry_review_needed"] = True
                    detail = "새 등기정보를 확인한 뒤 ‘등록 등기값을 현재 평가에 반영’과 자본변동 검토를 진행하세요. 기존 평가 입력값은 바꾸지 않았습니다."
                    if all(result.get(key) for key in ("cloud_saved", "document_registered", "original_cloud_saved", "history_cloud_saved")):
                        task.complete("등기 원본 재분석과 클라우드 보존이 완료되었습니다.", detail, persist=True)
                    else:
                        task.warning("새 분석은 보존했지만 일부 클라우드 저장은 확인이 필요합니다.", detail, persist=True)
                except Exception as exc:
                    task.error(stock.safe_public_error(exc, "등기 원본을 다시 분석하지 못했습니다."), "원본·기존 평가값은 유지했습니다. 분석 보존 상태는 확인이 필요할 수 있습니다.")
                    return
            st.rerun()
