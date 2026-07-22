from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from utils import get_user_dirs


CATALOG_VERSION = "2026.07"
SETTINGS_FILE = "employment_support_2026_settings.json"

EMPLOYMENT_SUPPORT_CATALOG: list[dict[str, Any]] = [
    {
        "id": "employment_promotion",
        "category": "신규채용",
        "name": "고용촉진장려금",
        "support": "취업취약계층을 정규직으로 고용하고 6개월 이상 유지한 사업주 지원",
        "confirm": [
            "근로자의 취업지원프로그램 이수·중증장애·가족부양 여성 등 대상자격",
            "채용 전 구직등록",
            "6개월 이상 고용유지",
            "사업주·근로자 제외요건 및 고용조정 제한",
        ],
        "source": "2026 고용장려금 지원제도 Ⅱ",
    },
    {
        "id": "youth_leap_capital",
        "category": "청년",
        "name": "청년일자리도약장려금 기업지원금 · 수도권",
        "support": "수도권 기업이 취업애로청년을 정규직 채용하고 요건을 충족하면 기업지원",
        "confirm": [
            "사업장 소재지가 수도권인지",
            "취업애로청년 해당 여부",
            "정규직·주 28시간 이상·최저임금 이상·월평균 급여 요건",
            "채용 전 또는 정해진 기한 내 사업 참여 신청",
            "2026년 4월 개정 지침 최종 확인",
        ],
        "source": "2026 청년일자리도약장려금 지침 PART 1",
    },
    {
        "id": "youth_leap_noncapital",
        "category": "청년",
        "name": "청년일자리도약장려금 기업지원금 · 비수도권",
        "support": "비수도권 우선지원대상기업·일부 산업단지 중견기업의 청년채용에 최장 1년 최대 720만원",
        "confirm": [
            "비수도권 소재 여부",
            "통상 5인 이상 여부와 1인 이상 예외업종 해당 여부",
            "정규직·6개월 이상 근속·주 28시간 이상 등",
            "기준 피보험자수와 지원한도",
            "2026년 4월 개정 지침 최종 확인",
        ],
        "source": "2026 청년일자리도약장려금 지침 PART 2",
    },
    {
        "id": "youth_long_service",
        "category": "청년",
        "name": "비수도권 청년장기근속인센티브",
        "support": "비수도권 참여청년이 6개월 이상 근속하면 2년간 최대 480만~720만원을 청년에게 지원",
        "confirm": [
            "기업이 비수도권 도약장려금 기업지원금 1회차 이상 지급받았는지",
            "청년 정규직 채용·6개월 이상 근속",
            "일반·우대·특별지원지역 구분",
            "청년 본인의 고용24 신청",
        ],
        "source": "2026 청년일자리도약장려금 지침 PART 3",
    },
    {
        "id": "senior_employment",
        "category": "고령자",
        "name": "고령자 고용지원금",
        "support": "근속 1년 초과 60세 이상 근로자 수가 증가한 기업에 증가인원 1명당 분기 30만원, 최대 2년",
        "confirm": [
            "고용보험 성립 후 1년 이상 사업 운영",
            "과거 기준기간 대비 60세 이상 근로자 수 증가",
            "분기별 최초·계속 신청기간",
            "지원한도와 제외 근로자",
        ],
        "source": "2026 고령자 고용안정지원금 지급규정·고용24",
        "added_because_missing": True,
    },
    {
        "id": "senior_continued",
        "category": "고령자",
        "name": "고령자 계속고용장려금",
        "support": "정년연장·정년폐지·재고용으로 정년 도달자를 계속고용하면 1명당 분기 90만원, 비수도권 120만원, 최대 3년",
        "confirm": [
            "정년제도를 1년 이상 운영했는지",
            "취업규칙·단체협약에 계속고용제도를 명시했는지",
            "정년 도달자와 정년 전 피보험기간 요건",
            "배우자·직계존비속·일부 외국인·저임금자 제외",
        ],
        "source": "2026 고용장려금 지원제도 Ⅴ",
    },
    {
        "id": "worklife_45",
        "category": "고용안정",
        "name": "워라밸일자리 장려금 · 워라밸+4.5 프로젝트",
        "support": "노사합의로 임금감소 없이 주4.5일제 등 실근로시간을 단축한 기업 지원",
        "confirm": ["노사합의", "임금감소 없는 실근로시간 단축", "사업 참여신청·승인"],
        "source": "2026 고용장려금 지원제도 Ⅲ",
    },
    {
        "id": "worklife_reduced",
        "category": "고용안정",
        "name": "워라밸일자리 장려금 · 소정근로시간 단축",
        "support": "전일제 근로자의 필요에 따라 소정근로시간 단축을 허용한 사업주 지원",
        "confirm": ["단축 전·후 소정근로시간", "단축 사유와 기간", "임금·근태 증빙"],
        "source": "2026 고용장려금 지원제도 Ⅲ",
    },
    {
        "id": "flexible_work",
        "category": "고용안정",
        "name": "일·가정 양립 환경개선 지원",
        "support": "재택·원격·선택·시차출퇴근 등 유연근무를 활용하는 사업주 지원",
        "confirm": ["유연근무 유형·활용일수", "근태관리 체계", "2026 수정공고 지급주기"],
        "source": "2026 고용장려금 지원제도 Ⅲ·2026 수정공고",
    },
    {
        "id": "regular_conversion",
        "category": "고용안정",
        "name": "정규직 전환 지원금",
        "support": "기간제 근로자를 정규직으로 전환하거나 직접고용한 사업주 지원",
        "confirm": ["전환 전 고용형태", "전환 후 임금·근로조건", "사업계획 승인과 전환 시점"],
        "source": "2026 고용장려금 지원제도 Ⅲ",
    },
    {
        "id": "parental_support",
        "category": "출산육아",
        "name": "출산육아기 고용안정장려금",
        "support": "육아휴직·육아기 근로시간 단축, 대체인력, 업무분담자 지원",
        "confirm": ["휴직·단축 사용기간", "대체인력 여부", "업무분담자 금전지원", "감원방지의무"],
        "source": "2026 고용장려금 지원제도 Ⅲ",
    },
    {
        "id": "employment_maintenance_paid",
        "category": "고용유지",
        "name": "유급 고용유지지원금",
        "support": "경영악화 사업주가 휴업·휴직으로 고용을 유지할 때 수당 일부 지원",
        "confirm": ["고용조정 불가피성", "계획 사전신고", "수당 지급", "고용조정 제한"],
        "source": "2026 고용장려금 지원제도 Ⅳ",
    },
    {
        "id": "employment_maintenance_unpaid",
        "category": "고용유지",
        "name": "무급 고용유지지원금",
        "support": "무급휴업·휴직으로 고용을 유지하는 경우 근로자에게 평균임금 50% 범위 지원",
        "confirm": ["30일 이상", "규모별 최소 참여인원", "노동위원회 승인 또는 합의", "사전 유급조치"],
        "source": "2026 고용장려금 지원제도 Ⅳ",
    },
    {
        "id": "workplace_nursery_operation",
        "category": "고용환경",
        "name": "직장어린이집 인건비 및 운영비 지원",
        "support": "직장어린이집 보육교직원 인건비와 운영비 지원",
        "confirm": ["설치·운영 여부", "보육교직원·원아 현황", "공단 지원요건"],
        "source": "2026 고용장려금 지원제도 Ⅵ",
    },
    {
        "id": "workplace_nursery_install",
        "category": "고용환경",
        "name": "직장어린이집 설치비 지원",
        "support": "직장어린이집 신규 설치·이전·증축 등에 필요한 설치비 지원",
        "confirm": ["설치계획·부지·인가", "단독·공동 구분", "공사 착수 전 신청"],
        "source": "2026 고용장려금 지원제도 Ⅵ",
    },
    {
        "id": "regional_employment",
        "category": "지역고용",
        "name": "지역고용촉진지원금",
        "support": "고용위기지역 이전·신설·증설 후 지역 구직자를 채용하면 최대 1년간 임금 일부 지원",
        "confirm": ["현재 지정지역", "물적 투자", "지역고용계획 사전신고", "지역 거주자 6개월 이상 채용"],
        "source": "2026 고용장려금 지원제도 Ⅶ·고용24",
    },
]


def _company_key(business_no: str, company_name: str) -> str:
    return re.sub(r"[^0-9]", "", business_no) or company_name.strip()


def _settings_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / SETTINGS_FILE


def _load_all(user_id: str) -> dict[str, Any]:
    path = _settings_path(user_id)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _load_settings(user_id: str, business_no: str, company_name: str) -> dict[str, Any]:
    return _load_all(user_id).get(_company_key(business_no, company_name), {}) or {}


def _save_settings(user_id: str, business_no: str, company_name: str, settings: dict[str, Any]) -> None:
    path = _settings_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_all(user_id)
    data[_company_key(business_no, company_name)] = {
        **settings,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _metrics(latest: dict[str, Any]) -> dict[str, Any]:
    employees = latest.get("employees", []) or []
    active = [e for e in employees if bool(e.get("active", True))]
    today = date.today()
    recent = []
    recent_youth = []
    senior_long = []
    for employee in active:
        try:
            acquired = datetime.strptime(str(employee.get("acquisition_date", "")), "%Y-%m-%d").date()
        except Exception:
            acquired = None
        if acquired and 0 <= (today - acquired).days <= 184:
            recent.append(employee)
            if employee.get("age_group") == "청년":
                recent_youth.append(employee)
        tenure = employee.get("tenure_months")
        if employee.get("age_group") == "고령자" and isinstance(tenure, int) and tenure >= 12:
            senior_long.append(employee)
    summary = latest.get("summary", {}) or {}
    return {
        "active_count": len(active),
        "recent_count": len(recent),
        "recent_youth_count": len(recent_youth),
        "youth_count": int(summary.get("youth_count", 0) or 0),
        "senior_count": int(summary.get("senior_count", 0) or 0),
        "senior_long_count": len(senior_long),
    }


def _score(item: dict[str, Any], metrics: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    score = 15
    evidence: list[str] = []
    item_id = item["id"]
    region = settings.get("region_type", "확인필요")

    if item_id in {"youth_leap_capital", "youth_leap_noncapital"}:
        score += min(metrics["recent_youth_count"] * 18, 54)
        if metrics["recent_youth_count"]:
            evidence.append(f"최근 6개월 취득 청년 {metrics['recent_youth_count']}명")
        if settings.get("planned_youth_hire"):
            score += 18
            evidence.append("청년 신규채용 계획")
        if item_id.endswith("capital") and region == "수도권":
            score += 18
            evidence.append("수도권 사업장")
        if item_id.endswith("noncapital") and region == "비수도권":
            score += 18
            evidence.append("비수도권 사업장")
    elif item_id == "youth_long_service":
        score += min(metrics["youth_count"] * 5, 25)
        if region == "비수도권":
            score += 25
            evidence.append("비수도권 사업장")
        if settings.get("received_youth_leap"):
            score += 30
            evidence.append("도약장려금 기업지원 진행")
    elif item_id == "senior_employment":
        score += min(metrics["senior_long_count"] * 15, 60)
        if metrics["senior_long_count"]:
            evidence.append(f"60세 이상·근속 1년 초과 {metrics['senior_long_count']}명")
        if settings.get("senior_increase"):
            score += 20
            evidence.append("고령자 수 증가")
    elif item_id == "senior_continued":
        score += min(metrics["senior_count"] * 12, 48)
        if metrics["senior_count"]:
            evidence.append(f"고령자 추정 {metrics['senior_count']}명")
        if settings.get("continued_employment_system"):
            score += 30
            evidence.append("계속고용제도 운영")
        if region == "비수도권":
            score += 8
            evidence.append("비수도권 우대 가능")
    elif item_id == "employment_promotion":
        score += min(metrics["recent_count"] * 8, 32)
        if metrics["recent_count"]:
            evidence.append(f"최근 6개월 취득 {metrics['recent_count']}명")
        if settings.get("vulnerable_hire"):
            score += 35
            evidence.append("취업취약계층 채용")
    elif item_id in {"worklife_45", "worklife_reduced"} and settings.get("reduced_hours"):
        score += 65
        evidence.append("근로시간 단축 시행·계획")
    elif item_id == "flexible_work" and settings.get("flexible_work"):
        score += 65
        evidence.append("유연근무 시행·계획")
    elif item_id == "regular_conversion" and settings.get("regular_conversion"):
        score += 65
        evidence.append("정규직 전환 계획")
    elif item_id == "parental_support" and settings.get("parental_leave"):
        score += 65
        evidence.append("출산육아기 지원 사유")
    elif item_id in {"employment_maintenance_paid", "employment_maintenance_unpaid"}:
        if settings.get("employment_difficulty"):
            score += 55
            evidence.append("경영상 고용유지 필요")
        if item_id.endswith("unpaid") and settings.get("unpaid_leave"):
            score += 25
            evidence.append("무급휴업·휴직 계획")
    elif item_id.startswith("workplace_nursery") and settings.get("workplace_nursery"):
        score += 65
        evidence.append("직장어린이집 관련 계획")
    elif item_id == "regional_employment" and settings.get("regional_move_invest"):
        score += 65
        evidence.append("지정지역 이전·신설·증설 계획")

    score = max(0, min(score, 100))
    grade = "유력 검토" if score >= 70 else "추가 확인" if score >= 40 else "현재 정보 부족"
    return {**item, "score": score, "grade": grade, "evidence": evidence}


def render_employment_support_analysis(
    user_id: str,
    business_no: str,
    company_name: str,
    latest: dict[str, Any],
) -> None:
    st.divider()
    st.markdown("#### 2026 고용지원금 자동 분석")
    st.caption(
        "정책자금과 분리된 전용 분석입니다. 직원 나이·취득일·근속기간을 자동 반영하고, "
        "명부에서 알 수 없는 조건만 추가 입력합니다."
    )

    metrics = _metrics(latest)
    current = _load_settings(user_id, business_no, company_name)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("가입중", f"{metrics['active_count']}명")
    c2.metric("최근 6개월 취득", f"{metrics['recent_count']}명")
    c3.metric("최근취득 청년", f"{metrics['recent_youth_count']}명")
    c4.metric("60세 이상 장기근속", f"{metrics['senior_long_count']}명")

    with st.expander("명부만으로 확인할 수 없는 추가조건 입력", expanded=not bool(current)):
        options = ["확인필요", "수도권", "비수도권"]
        region_value = current.get("region_type", "확인필요")
        region_type = st.radio(
            "사업장 지역",
            options,
            index=options.index(region_value) if region_value in options else 0,
            horizontal=True,
            key=f"employment_region_{business_no}",
        )
        left, right = st.columns(2)
        with left:
            planned_youth_hire = st.checkbox("청년 신규채용 계획", value=bool(current.get("planned_youth_hire")), key=f"emp_youth_{business_no}")
            vulnerable_hire = st.checkbox("취업취약계층 채용", value=bool(current.get("vulnerable_hire")), key=f"emp_vulnerable_{business_no}")
            received_youth_leap = st.checkbox("비수도권 도약장려금 기업지원 진행", value=bool(current.get("received_youth_leap")), key=f"emp_leap_{business_no}")
            senior_increase = st.checkbox("60세 이상 근로자 수 증가", value=bool(current.get("senior_increase")), key=f"emp_senior_inc_{business_no}")
            continued_employment_system = st.checkbox("정년연장·폐지·재고용 제도 운영", value=bool(current.get("continued_employment_system")), key=f"emp_continue_{business_no}")
        with right:
            reduced_hours = st.checkbox("주4.5일제·근로시간 단축", value=bool(current.get("reduced_hours")), key=f"emp_hours_{business_no}")
            flexible_work = st.checkbox("재택·원격·선택·시차출퇴근", value=bool(current.get("flexible_work")), key=f"emp_flexible_{business_no}")
            regular_conversion = st.checkbox("기간제 등의 정규직 전환", value=bool(current.get("regular_conversion")), key=f"emp_regular_{business_no}")
            parental_leave = st.checkbox("육아휴직·대체인력·업무분담", value=bool(current.get("parental_leave")), key=f"emp_parental_{business_no}")
            employment_difficulty = st.checkbox("경영상 휴업·휴직 고용유지 필요", value=bool(current.get("employment_difficulty")), key=f"emp_maintain_{business_no}")
            unpaid_leave = st.checkbox("무급휴업·무급휴직 계획", value=bool(current.get("unpaid_leave")), key=f"emp_unpaid_{business_no}")
            workplace_nursery = st.checkbox("직장어린이집 설치·운영", value=bool(current.get("workplace_nursery")), key=f"emp_nursery_{business_no}")
            regional_move_invest = st.checkbox("고용위기지역 이전·신설·증설", value=bool(current.get("regional_move_invest")), key=f"emp_regionmove_{business_no}")

        settings = {
            "region_type": region_type,
            "planned_youth_hire": planned_youth_hire,
            "vulnerable_hire": vulnerable_hire,
            "received_youth_leap": received_youth_leap,
            "senior_increase": senior_increase,
            "continued_employment_system": continued_employment_system,
            "reduced_hours": reduced_hours,
            "flexible_work": flexible_work,
            "regular_conversion": regular_conversion,
            "parental_leave": parental_leave,
            "employment_difficulty": employment_difficulty,
            "unpaid_leave": unpaid_leave,
            "workplace_nursery": workplace_nursery,
            "regional_move_invest": regional_move_invest,
        }
        if st.button("추가조건 저장 후 다시 분석", type="primary", use_container_width=True, key=f"emp_save_{business_no}"):
            _save_settings(user_id, business_no, company_name, settings)
            st.success("고용지원금 분석조건을 저장했습니다.")
            st.rerun()

    settings = _load_settings(user_id, business_no, company_name)
    results = sorted(
        [_score(item, metrics, settings) for item in EMPLOYMENT_SUPPORT_CATALOG],
        key=lambda item: item["score"],
        reverse=True,
    )
    likely = [item for item in results if item["score"] >= 40]

    st.markdown(f"##### 자동 추천 {len(likely)}건")
    if likely:
        st.dataframe(
            pd.DataFrame([
                {
                    "점수": item["score"],
                    "판정": item["grade"],
                    "분류": item["category"],
                    "지원제도": item["name"],
                    "자동근거": " / ".join(item["evidence"]) or "추가정보 기반",
                }
                for item in likely
            ]),
            hide_index=True,
            use_container_width=True,
        )
        for index, item in enumerate(likely, start=1):
            with st.expander(f"{index}. {item['name']} · {item['score']}점", expanded=index <= 3):
                st.write(f"**지원개요:** {item['support']}")
                if item["evidence"]:
                    st.markdown("**자동 분석 근거**")
                    for evidence in item["evidence"]:
                        st.write(f"- {evidence}")
                st.markdown("**최종 신청 전 확인사항**")
                for check in item["confirm"]:
                    st.write(f"- {check}")
                st.caption(f"자료기준: {item['source']}")
    else:
        st.info("추가조건을 입력하면 관련 지원금을 자동으로 다시 분석합니다.")

    with st.expander(f"2026 고용지원금 전체 목록 {len(EMPLOYMENT_SUPPORT_CATALOG)}개", expanded=False):
        st.dataframe(
            pd.DataFrame([
                {
                    "분류": item["category"],
                    "제도명": item["name"],
                    "지원개요": item["support"],
                    "추가수록": "안내책자 누락 보완" if item.get("added_because_missing") else "",
                }
                for item in EMPLOYMENT_SUPPORT_CATALOG
            ]),
            hide_index=True,
            use_container_width=True,
        )
        st.warning(
            "자동 분석은 사전 검토용입니다. 취업취약계층 자격, 실업기간, 임금, 근로시간, "
            "고용조정, 정년제도, 기준 피보험자수와 신청기한은 고용24·관할 고용센터에서 최종 확인해야 합니다."
        )
