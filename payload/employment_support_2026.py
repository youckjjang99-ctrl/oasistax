from __future__ import annotations

import json
import math
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
        "support": "정년연장·정년폐지·재고용으로 정년 도달자를 계속고용하면 수도권 월 30만원, 비수도권 월 40만원, 최대 3년",
        "confirm": [
            "계속고용제도 시행 전 정년제도를 1년 이상 운영했는지",
            "취업규칙·단체협약에 정년연장·정년폐지·재고용 중 하나를 명시하고 신고했는지",
            "60세 이상 피보험자 비율이 직전연도 기준 30%를 넘지 않는지",
            "대상 근로자가 제도 시행일부터 5년 이내 종전 정년에 도달하는지",
            "정년 도달일까지 해당 사업장 피보험기간이 계속 2년 이상인지",
            "배우자·직계존비속, 일부 외국인, 월평균보수 124만원 미만 근로자 제외 여부",
        ],
        "source": "2026 고령자 계속고용장려금 가이드북·고용노동부 고시 제2025-115호",
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


def _real_employee_name(employee: dict[str, Any]) -> str:
    """Return a usable employee name while rejecting synthetic placeholder rows."""
    raw = str(
        employee.get("name")
        or employee.get("employee_name")
        or employee.get("성명")
        or employee.get("가입자명")
        or employee.get("피보험자명")
        or employee.get("근로자명")
        or employee.get("직원명")
        or employee.get("name_masked")
        or ""
    ).strip()
    if not raw:
        return ""

    compact = re.sub(r"\s+", "", raw)
    if re.fullmatch(r"(직원|employee|staff)[-_ ]*\d+", raw, flags=re.IGNORECASE):
        return ""
    if compact.lower() in {
        "unknown", "none", "nan", "미확인",
        "성명", "가입자명", "피보험자명",
    }:
        return ""

    # employee_status.py stores privacy-safe names such as 홍*동 in name_masked.
    # Masked Korean names are valid roster identities and must be accepted.
    if re.fullmatch(r"[가-힣][가-힣*○ㆍ·]{1,10}", compact):
        return compact
    if re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,50}", raw):
        return raw
    return raw

def _metrics(latest: dict[str, Any]) -> dict[str, Any]:
    employees = latest.get("employees", []) or []
    active_raw = [e for e in employees if isinstance(e, dict) and bool(e.get("active", True))]

    # Only rows that contain a real employee name are used for automatic judgement.
    # This prevents parser samples such as "직원 1", "직원 2" from becoming real staff.
    active = []
    for employee in active_raw:
        name = _real_employee_name(employee)
        if not name:
            continue
        copied = dict(employee)
        copied["_verified_name"] = name
        copied["_name_source"] = (
            "name_masked" if employee.get("name_masked") else
            "name" if employee.get("name") else
            "employee_name" if employee.get("employee_name") else
            "legacy_alias"
        )
        active.append(copied)

    today = date.today()
    recent: list[dict[str, Any]] = []
    recent_youth: list[dict[str, Any]] = []
    senior_long: list[dict[str, Any]] = []

    for employee in active:
        acquired = _parse_date_value(employee.get("acquisition_date"))
        if acquired and 0 <= (today - acquired).days <= 184:
            recent.append(employee)
            if employee.get("age_group") == "청년":
                recent_youth.append(employee)

        tenure = employee.get("tenure_months")
        if employee.get("age_group") == "고령자" and isinstance(tenure, int) and tenure >= 12:
            senior_long.append(employee)

    summary = latest.get("summary", {}) or {}
    senior_rows: list[dict[str, Any]] = []
    for employee in active:
        if employee.get("age_group") != "고령자":
            continue
        tenure = employee.get("tenure_months")
        tenure_ok = isinstance(tenure, int) and tenure >= 24
        senior_rows.append({
            "성명": employee["_verified_name"],
            "근속개월": tenure if isinstance(tenure, int) else "",
            "근속 2년": "충족" if tenure_ok else "미충족" if isinstance(tenure, int) else "확인필요",
            "자동판정": "가능" if tenure_ok else "제외" if isinstance(tenure, int) else "확인필요",
            "판정근거": (
                "가입자명부의 실제 자격취득·근속정보 기준"
                if tenure_ok
                else "피보험기간 2년 미만"
                if isinstance(tenure, int)
                else "자격취득일 또는 근속기간 확인 필요"
            ),
        })

    active_count = len(active)
    senior_count = len(senior_rows)
    senior_ratio = (senior_count / active_count * 100) if active_count else None

    youth_count_from_rows = sum(1 for e in active if e.get("age_group") == "청년")
    youth_count = youth_count_from_rows or int(summary.get("youth_count", 0) or 0)

    return {
        "active_count": active_count,
        "raw_active_count": len(active_raw),
        "invalid_roster_rows": max(0, len(active_raw) - active_count),
        "recent_count": len(recent),
        "recent_youth_count": len(recent_youth),
        "youth_count": youth_count,
        "senior_count": senior_count,
        "senior_long_count": len(senior_long),
        "senior_ratio": senior_ratio,
        "senior_ratio_under_30": senior_ratio <= 30 if senior_ratio is not None else None,
        "continued_candidate_rows": senior_rows,
        "continued_auto_eligible_count": sum(1 for row in senior_rows if row["자동판정"] == "가능"),
        "continued_auto_review_count": sum(1 for row in senior_rows if row["자동판정"] == "확인필요"),
        "has_employee_roster": bool(active),
        "masked_name_count": sum(
            1 for employee in active
            if employee.get("_name_source") == "name_masked"
        ),
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



def _parse_date_value(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _continued_employment_diagnosis(
    metrics: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    region = settings.get("region_type", "확인필요")
    system_type = settings.get("continued_system_type", "미확인")
    retirement_start = _parse_date_value(settings.get("retirement_system_start_date"))
    continued_start = _parse_date_value(settings.get("continued_system_effective_date"))
    today = date.today()

    checks: list[dict[str, str]] = []

    def add_check(title: str, status: str, detail: str, action: str) -> None:
        checks.append({
            "요건": title,
            "판정": status,
            "현재상태": detail,
            "실행조치": action,
        })

    if retirement_start and continued_start:
        days = (continued_start - retirement_start).days
        if days >= 365:
            add_check("정년제도 1년 이상 운영", "충족", f"약 {days // 30}개월 운영", "기존 취업규칙·인사규정과 신고 접수증을 보관합니다.")
        else:
            add_check("정년제도 1년 이상 운영", "미충족", f"약 {max(days, 0) // 30}개월 운영", "1년 운영기간을 채운 뒤 계속고용제도를 시행하도록 일정 재설계가 필요합니다.")
    elif retirement_start:
        operated_days = (today - retirement_start).days
        status = "충족" if operated_days >= 365 else "미충족"
        add_check("정년제도 1년 이상 운영", status, f"현재까지 약 {max(operated_days, 0) // 30}개월 운영", "계속고용제도 시행 예정일을 입력해 정확한 선후관계를 확인합니다.")
    else:
        add_check("정년제도 1년 이상 운영", "확인필요", "정년 규정 시행일 미입력", "취업규칙·단체협약·인사규정에서 기존 정년 시행일을 확인합니다.")

    if system_type in {"정년 연장", "정년 폐지", "재고용"}:
        add_check("계속고용제도 유형", "충족", system_type, "선택한 유형에 맞춰 취업규칙 문구와 근로계약을 일치시킵니다.")
    else:
        add_check("계속고용제도 유형", "확인필요", "유형 미선택", "정년 연장·정년 폐지·퇴직 후 6개월 이내 재고용 중 하나를 결정합니다.")

    rules_reported = settings.get("continued_work_rules_reported")
    if rules_reported is True:
        add_check("취업규칙 명시·신고", "충족", "명시 및 신고 완료", "변경 전후 취업규칙과 신고 접수증을 신청서류에 첨부합니다.")
    elif rules_reported is False:
        add_check("취업규칙 명시·신고", "미충족", "미완료", "노사 의견청취 후 취업규칙을 변경하고 관할 지방노동관서에 신고합니다.")
    else:
        add_check("취업규칙 명시·신고", "확인필요", "확인되지 않음", "변경 취업규칙, 근로자 의견서, 신고 접수증 존재 여부를 확인합니다.")

    ratio_under = metrics.get("senior_ratio_under_30")
    if ratio_under is True:
        add_check("60세 이상 피보험자 비율 30% 이하", "충족", "30% 이하 확인", "직전연도 매월 말 피보험자 명부와 산정표를 보관합니다.")
    elif ratio_under is False:
        add_check("60세 이상 피보험자 비율 30% 이하", "미충족", "30% 초과", "계속고용장려금 대상 제외 가능성이 높으므로 고령자 고용지원금 등 대안을 검토합니다.")
    else:
        add_check("60세 이상 피보험자 비율 30% 이하", "확인필요", "비율 미산정", "직전연도 매월 말 전체 피보험자와 60세 이상 피보험자 수를 산정합니다.")

    employee_tenure = True if metrics.get("continued_auto_eligible_count", 0) > 0 and metrics.get("continued_auto_review_count", 0) == 0 else None
    add_check(
        "대상자 정년 전 피보험기간 2년 이상",
        "충족" if employee_tenure is True else "미충족" if employee_tenure is False else "확인필요",
        "확인 완료" if employee_tenure is True else "2년 미만" if employee_tenure is False else "대상자별 미확인",
        "고용보험 자격이력과 근로자별 입사일을 확인합니다.",
    )

    exclusions_ok = None
    add_check(
        "지원 제외 근로자 해당 없음",
        "충족" if exclusions_ok is True else "미충족" if exclusions_ok is False else "확인필요",
        "제외사유 없음" if exclusions_ok is True else "제외사유 존재" if exclusions_ok is False else "미확인",
        "대표자 가족관계, 국적·체류자격, 월평균보수 124만원 이상 여부를 대상자별로 점검합니다.",
    )

    eligible_count = max(0, int(metrics.get("continued_auto_eligible_count", 0) or 0))
    active_count = max(0, int(metrics.get("active_count", 0) or 0))
    if active_count < 10:
        headcount_cap = 3
    else:
        headcount_cap = min(30, math.floor(active_count * 0.3))
    estimated_supported_count = min(eligible_count, headcount_cap) if eligible_count else 0
    monthly_rate = 400_000 if region == "비수도권" else 300_000
    months = max(1, min(36, int(settings.get("continued_support_months", 36) or 36)))
    estimated_total = estimated_supported_count * monthly_rate * months
    estimated_quarter = estimated_supported_count * monthly_rate * 3

    failed = [row for row in checks if row["판정"] == "미충족"]
    unknown = [row for row in checks if row["판정"] == "확인필요"]
    readiness = max(0, round((len(checks) - len(failed) - len(unknown) * 0.5) / len(checks) * 100))
    overall = "진행 가능" if not failed and not unknown else "조건 보완 후 진행" if not failed else "현재 신청 곤란"

    action_plan = [
        {
            "단계": "1. 기초요건 진단",
            "시점": "즉시",
            "담당": "영업사원 + 인사담당자",
            "실행내용": "기존 정년 시행일, 60세 이상 피보험자 비율, 대상자의 근속·가족관계·보수 요건을 확정합니다.",
            "필요자료": "기존 취업규칙, 고용보험 사업장 명부, 대상자 자격이력, 임금대장",
        },
        {
            "단계": "2. 계속고용 방식 결정",
            "시점": "제도 시행 전",
            "담당": "대표자 + 인사·노무 담당",
            "실행내용": "정년 연장, 정년 폐지, 재고용 중 인력운영에 적합한 유형을 선택하고 시행일을 정합니다.",
            "필요자료": "대상자별 정년 예정일, 인력계획, 임금·직무 설계안",
        },
        {
            "단계": "3. 취업규칙 변경·신고",
            "시점": "계속고용 시행 전",
            "담당": "인사담당자 또는 노무사",
            "실행내용": "계속고용 유형과 시행일을 취업규칙 또는 단체협약에 명시하고 근로자 의견청취 후 신고합니다.",
            "필요자료": "변경 전후 취업규칙, 근로자 의견서, 신고서 및 접수증",
        },
        {
            "단계": "4. 대상자 계속고용 실행",
            "시점": "정년 도달 시",
            "담당": "인사담당자",
            "실행내용": "재고용형은 퇴직 후 6개월 이내 1년 이상 근로계약을 체결하고, 연장형은 변경된 정년까지 고용을 유지합니다.",
            "필요자료": "근로계약서, 급여대장, 출근부, 고용보험 자격취득·상실 자료",
        },
        {
            "단계": "5. 분기별 장려금 신청",
            "시점": "지원대상 근로자 발생 후 분기별",
            "담당": "사업주 또는 위임받은 담당자",
            "실행내용": "고용24 또는 관할 고용센터를 통해 신청하고 대상자 명부와 증빙을 제출합니다.",
            "필요자료": "신청서, 계속고용 명부, 취업규칙, 근로계약서, 임금대장, 사업주 명의 통장",
        },
        {
            "단계": "6. 사후관리",
            "시점": "지급기간 최대 3년",
            "담당": "인사담당자",
            "실행내용": "퇴사·임금변경·근로시간 변경과 다른 지원금 중복 여부를 분기마다 점검합니다.",
            "필요자료": "분기별 피보험자 현황, 임금대장, 다른 장려금 수급내역",
        },
    ]

    return {
        "checks": checks,
        "readiness": readiness,
        "overall": overall,
        "failed_count": len(failed),
        "unknown_count": len(unknown),
        "headcount_cap": headcount_cap,
        "eligible_count": eligible_count,
        "supported_count": estimated_supported_count,
        "monthly_rate": monthly_rate,
        "months": months,
        "estimated_quarter": estimated_quarter,
        "estimated_total": estimated_total,
        "action_plan": action_plan,
    }


def _render_continued_employment_execution_plan(
    business_no: str,
    metrics: dict[str, Any],
    settings: dict[str, Any],
) -> None:
    diagnosis = _continued_employment_diagnosis(metrics, settings)
    st.markdown("##### 고령자 계속고용장려금 실행계획")
    st.caption("2026년 가이드북 기준으로 지원 가능성과 실제 실행 순서를 함께 보여줍니다.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("신청 준비도", f"{diagnosis['readiness']}%")
    m2.metric("종합판정", diagnosis["overall"])
    m3.metric("예상 지원인원", f"{diagnosis['supported_count']}명", help=f"현재 피보험자 수 기준 추정 한도 {diagnosis['headcount_cap']}명")
    m4.metric("예상 총지원", f"{diagnosis['estimated_total']:,}원", help=f"월 {diagnosis['monthly_rate']:,}원 × {diagnosis['months']}개월 기준")

    if diagnosis["failed_count"]:
        st.error(f"미충족 요건 {diagnosis['failed_count']}개가 있어 현재 상태로는 신청이 어렵습니다. 아래 실행조치를 먼저 처리하세요.")
    elif diagnosis["unknown_count"]:
        st.warning(f"확인이 필요한 요건 {diagnosis['unknown_count']}개가 있습니다. 서류 확인 후 최종 판정해야 합니다.")
    else:
        st.success("입력된 정보 기준으로 핵심요건을 충족합니다. 대상자별 증빙과 신청기한을 최종 확인하세요.")

    st.dataframe(pd.DataFrame(diagnosis["checks"]), hide_index=True, use_container_width=True)

    if diagnosis["supported_count"]:
        st.info(
            f"예상 분기 지원액은 약 {diagnosis['estimated_quarter']:,}원이며, "
            f"최대 {diagnosis['months']}개월 기준 총 {diagnosis['estimated_total']:,}원입니다. "
            "실제 금액은 월 중 입·퇴사, 분기별 피보험자 수와 지원한도에 따라 달라질 수 있습니다."
        )

    with st.expander("구체적인 6단계 실행 로드맵", expanded=True):
        for row in diagnosis["action_plan"]:
            st.markdown(f"**{row['단계']} · {row['시점']}**")
            st.write(f"- 담당: {row['담당']}")
            st.write(f"- 실행: {row['실행내용']}")
            st.write(f"- 준비자료: {row['필요자료']}")

    with st.expander("대표님·인사담당자에게 확인할 질문", expanded=False):
        questions = [
            "현재 취업규칙에 정년이 몇 세로 규정되어 있고, 그 규정은 언제부터 시행됐나요?",
            "향후 1~3년 안에 정년에 도달하는 직원은 몇 명인가요?",
            "정년 연장, 정년 폐지, 퇴직 후 재고용 중 어떤 방식이 회사 운영에 적합한가요?",
            "가입자명부 자동분석 결과에서 근속기간 확인필요로 표시된 직원이 있나요?",
            "직전연도 월별 명부와 현재 업로드 명부의 60세 이상 비율이 동일한지 최종 확인했나요?",
            "대상자 중 대표자의 배우자·직계존비속 또는 지원 제외 외국인이 있나요?",
            "재고용형이라면 퇴직 후 6개월 이내, 1년 이상 계약이 가능한가요?",
        ]
        for q in questions:
            st.write(f"- {q}")

    with st.expander("신청 전 최종 서류 체크리스트", expanded=False):
        docs = [
            "변경 전·후 취업규칙 또는 단체협약",
            "취업규칙 변경 신고서와 접수증",
            "근로자 의견청취서 또는 노사합의 자료",
            "대상자별 고용보험 자격이력과 정년 도달일 자료",
            "재고용 근로계약서 또는 정년 연장 적용 자료",
            "최근 임금대장·급여이체내역·근태자료",
            "직전연도 월별 전체·60세 이상 피보험자 산정표",
            "사업주 또는 법인 명의 통장 사본",
            "다른 고용장려금 수급내역 및 중복지원 검토표",
        ]
        for doc in docs:
            st.write(f"- □ {doc}")

def _execution_plan_for_item(item_id: str) -> list[str]:
    plans: dict[str, list[str]] = {
        "employment_promotion": [
            "채용예정자의 취업지원프로그램 이수·구직등록·지원대상 자격을 채용 전에 확인합니다.",
            "사업주 및 근로자 지원제외 사유와 감원방지 의무 적용기간을 점검합니다.",
            "정규직 채용 후 고용보험 가입, 임금·근로시간 조건을 관리합니다.",
            "6개월 이상 고용을 유지한 뒤 고용24 또는 관할 고용센터에 신청합니다.",
        ],
        "youth_leap_capital": [
            "수도권 소재 여부, 기준 피보험자 수, 우선지원대상기업 여부를 확인합니다.",
            "채용예정 청년의 연령과 취업애로청년 유형을 채용 전에 확인합니다.",
            "사업참여 신청기한을 먼저 확정한 뒤 정규직으로 채용합니다.",
            "주 28시간 이상, 최저임금 이상, 월평균 급여 요건과 6개월 고용유지를 관리합니다.",
            "회차별 신청기한에 맞춰 임금대장·근로계약서·고용보험 자료를 제출합니다.",
        ],
        "youth_leap_noncapital": [
            "비수도권 소재, 5인 이상 요건 또는 1인 이상 예외업종 해당 여부를 확인합니다.",
            "청년의 연령·취업애로 유형과 기업의 매출·업종 요건을 점검합니다.",
            "사업참여 신청 후 정규직 채용 및 6개월 이상 고용을 유지합니다.",
            "주 28시간 이상, 최저임금 이상, 평균 월급여 상한을 관리합니다.",
            "기준 피보험자 수의 지원한도 안에서 회차별 기업지원금을 신청합니다.",
        ],
        "youth_long_service": [
            "비수도권 도약장려금 기업지원금 1회차 이상 지급 여부를 확인합니다.",
            "청년의 6·12·18·24개월 근속일을 일정에 등록합니다.",
            "일반·우대·특별지원지역에 따른 인센티브 금액을 구분합니다.",
            "청년 본인이 고용24에서 신청할 수 있도록 증빙과 신청일을 안내합니다.",
        ],
        "senior_employment": [
            "고용보험 성립 후 1년 이상 사업 운영 여부를 확인합니다.",
            "기준기간의 월평균 60세 이상 근로자 수와 현재 분기 수를 비교합니다.",
            "근속 1년 초과자와 지원제외 근로자를 구분합니다.",
            "증가 인원과 분기별 한도를 계산해 최초·계속 신청기한에 신청합니다.",
        ],
        "senior_continued": [
            "기존 정년규정의 시행일과 1년 이상 운영 여부를 확인합니다.",
            "정년 연장·정년 폐지·퇴직 후 재고용 중 운영방식을 선택합니다.",
            "취업규칙 또는 단체협약을 변경하고 근로자 의견청취 후 신고합니다.",
            "대상자의 정년 도달일, 2년 이상 피보험기간, 제외사유를 확인합니다.",
            "계속고용 실행 후 분기별로 장려금을 신청하고 최대 3년간 사후관리합니다.",
        ],
        "worklife_45": [
            "노사합의와 임금감소 없는 실근로시간 단축안을 설계합니다.",
            "사업계획과 근태관리 방법을 마련해 참여신청 및 승인을 받습니다.",
            "승인된 방식으로 근로시간을 운영하고 근태·임금 자료를 보관합니다.",
            "지급주기에 맞춰 장려금을 신청합니다.",
        ],
        "worklife_reduced": [
            "단축 대상 근로자의 사유, 단축 전후 근로시간과 기간을 확정합니다.",
            "근로계약서와 취업규칙을 정비하고 전자적 근태관리를 준비합니다.",
            "임금·근태 증빙을 월별로 보관하고 지급주기에 맞춰 신청합니다.",
        ],
        "flexible_work": [
            "재택·원격·선택·시차출퇴근 중 적용유형과 대상자를 정합니다.",
            "취업규칙·근로계약·근태관리 체계를 정비합니다.",
            "활용일수와 임금지급 자료를 관리해 지급주기에 신청합니다.",
        ],
        "regular_conversion": [
            "전환 대상자의 기존 고용형태와 재직기간을 확인합니다.",
            "사업계획 승인 필요 여부와 전환 가능시점을 먼저 확인합니다.",
            "정규직 전환 후 임금·복리후생·근로조건을 기준에 맞게 운영합니다.",
            "전환 및 고용유지 증빙을 갖춰 지원금을 신청합니다.",
        ],
        "parental_support": [
            "육아휴직·근로시간 단축·대체인력·업무분담 중 해당 유형을 구분합니다.",
            "사용기간, 대체인력 채용일, 업무분담자 금전지원 내역을 확정합니다.",
            "감원방지 의무를 관리하고 급여·근태·지급증빙을 보관합니다.",
            "각 유형의 신청 가능시점에 맞춰 신청합니다.",
        ],
        "employment_maintenance_paid": [
            "매출감소 등 고용조정이 불가피한 사유와 증빙을 확보합니다.",
            "휴업·휴직 계획을 시행 전에 신고하고 노사협의를 완료합니다.",
            "고용유지조치 기간 중 수당을 지급하고 감원 제한을 준수합니다.",
            "임금대장·출근부·지급내역으로 지원금을 신청합니다.",
        ],
        "employment_maintenance_unpaid": [
            "무급휴업·휴직 필요성과 사전 유급 고용유지조치 여부를 검토합니다.",
            "30일 이상 기간, 규모별 최소 참여인원, 노사합의 또는 승인을 준비합니다.",
            "시행 전 계획승인을 받고 고용조정 제한을 관리합니다.",
            "근로자별 지급자료를 갖춰 지원을 신청합니다.",
        ],
        "workplace_nursery_operation": [
            "직장어린이집 설치·인가 및 운영주체 요건을 확인합니다.",
            "보육교직원과 원아 현황, 임금·운영비 자료를 정리합니다.",
            "근로복지공단 신청기준에 따라 인건비·운영비를 신청합니다.",
        ],
        "workplace_nursery_install": [
            "부지·설치계획·인가 가능성과 단독·공동 설치 유형을 검토합니다.",
            "공사 착수 전에 지원한도와 자부담을 확인해 신청합니다.",
            "승인 후 공사·정산 증빙을 단계별로 관리합니다.",
        ],
        "regional_employment": [
            "현재 고용위기지역 등 지정지역 해당 여부를 확인합니다.",
            "이전·신설·증설 투자계획과 지역 고용계획을 사업 시행 전에 신고합니다.",
            "지역 거주 구직자를 채용하고 6개월 이상 고용을 유지합니다.",
            "투자·채용·임금 증빙으로 지원금을 신청합니다.",
        ],
    }
    return plans.get(item_id, [
        "지원대상 기업과 근로자 요건을 확인합니다.",
        "신청 전 필요한 계획승인·신고 여부를 확인합니다.",
        "근로계약·고용보험·임금·근태 증빙을 준비합니다.",
        "신청기한에 맞춰 고용24 또는 관할 기관에 신청합니다.",
    ])


def _result_bucket(score: int) -> str:
    if score >= 70:
        return "유력 검토"
    if score >= 40:
        return "조건 확인"
    return "현재 정보 부족"


def region_type_from_address(address: Any) -> str:
    compact = re.sub(r"\s+", "", str(address or ""))
    if not compact:
        return "확인필요"
    if re.match(r"^(서울|경기|인천)", compact):
        return "수도권"
    return "비수도권"


def render_employment_support_analysis(
    user_id: str,
    business_no: str,
    company_name: str,
    latest: dict[str, Any],
    company_address: str = "",
) -> None:
    st.divider()
    st.markdown("#### AI 고용지원금 통합진단")
    st.caption(
        "2026 고용장려금 종합안내, 청년일자리도약장려금 운영지침, "
        "고령자 계속고용장려금 가이드북을 함께 반영합니다."
    )

    metrics = _metrics(latest)
    saved_current = _load_settings(user_id, business_no, company_name)
    address_region_type = region_type_from_address(company_address)
    current = {
        **saved_current,
        "region_type": address_region_type,
    }

    if not metrics["has_employee_roster"]:
        st.warning(
            "실제 직원 성명과 자격정보가 확인되는 4대보험 가입자명부가 없습니다. "
            "직원현황에서 명부를 등록한 뒤 다시 분석해 주세요. "
            "명부가 없을 때는 직원 수·연령·근속기간·예상 지원금액을 임의 계산하지 않습니다."
        )
        if metrics.get("invalid_roster_rows"):
            st.caption(
                f"샘플 또는 식별 불가능한 직원 행 {metrics['invalid_roster_rows']}건은 자동분석에서 제외했습니다."
            )
    else:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("확인된 재직자", f"{metrics['active_count']}명")
        c2.metric("최근 6개월 취득", f"{metrics['recent_count']}명")
        c3.metric("확인된 청년", f"{metrics['youth_count']}명")
        c4.metric("60세 이상·근속 1년 초과", f"{metrics['senior_long_count']}명")
        if metrics.get("masked_name_count"):
            st.caption(
                f"직원현황의 개인정보 보호형 마스킹 성명 "
                f"{metrics['masked_name_count']}명을 정상 인식했습니다."
            )

    with st.expander("기업·채용계획 추가정보", expanded=not bool(saved_current)):
        st.caption(
            "가입자명부만으로 확인되지 않는 기업계획과 제도 운영 여부만 입력합니다. "
            "입력 중에는 분석값이 바뀌지 않으며 아래 통합진단 버튼을 누를 때 한 번 반영됩니다."
        )
        if address_region_type == "확인필요":
            st.warning(
                "등록된 기업 주소가 없어 수도권 여부를 자동 판정하지 못했습니다. "
                "기업등록에서 사업장 주소를 확인해주세요."
            )
        else:
            st.info(
                f"주소 자동판정: {address_region_type} · "
                f"{company_address or '주소 미표시'}"
            )

        with st.form(
            key=f"employment_support_form_{business_no or company_name}",
            clear_on_submit=False,
        ):
            left, right = st.columns(2)
            with left:
                planned_youth_hire = st.checkbox("청년 신규채용 계획", value=bool(current.get("planned_youth_hire")))
                vulnerable_hire = st.checkbox("취업취약계층 채용 계획·실적", value=bool(current.get("vulnerable_hire")))
                received_youth_leap = st.checkbox("비수도권 도약장려금 기업지원 진행", value=bool(current.get("received_youth_leap")))
                senior_increase = st.checkbox("기준기간 대비 60세 이상 근로자 증가", value=bool(current.get("senior_increase")))
                continued_employment_system = st.checkbox("정년연장·폐지·재고용 제도 운영·계획", value=bool(current.get("continued_employment_system")))
            with right:
                reduced_hours = st.checkbox("주4.5일제·근로시간 단축", value=bool(current.get("reduced_hours")))
                flexible_work = st.checkbox("재택·원격·선택·시차출퇴근", value=bool(current.get("flexible_work")))
                regular_conversion = st.checkbox("기간제 등의 정규직 전환", value=bool(current.get("regular_conversion")))
                parental_leave = st.checkbox("육아휴직·대체인력·업무분담", value=bool(current.get("parental_leave")))
                employment_difficulty = st.checkbox("경영상 휴업·휴직 고용유지 필요", value=bool(current.get("employment_difficulty")))
                unpaid_leave = st.checkbox("무급휴업·무급휴직 계획", value=bool(current.get("unpaid_leave")))
                workplace_nursery = st.checkbox("직장어린이집 설치·운영", value=bool(current.get("workplace_nursery")))
                regional_move_invest = st.checkbox("고용위기지역 이전·신설·증설", value=bool(current.get("regional_move_invest")))

            with st.expander("고령자 계속고용장려금 추가정보", expanded=False):
                st.caption("이 정보는 계속고용장려금 카드의 상세판정에만 사용합니다.")
                d1, d2 = st.columns(2)
                with d1:
                    system_options = ["미확인", "정년 연장", "정년 폐지", "재고용"]
                    saved_type = current.get("continued_system_type", "미확인")
                    continued_system_type = st.selectbox(
                        "계속고용제도 유형",
                        system_options,
                        index=system_options.index(saved_type) if saved_type in system_options else 0,
                    )
                    use_retirement_date = st.checkbox(
                        "기존 정년제도 시행일을 확인함",
                        value=bool(current.get("retirement_system_start_date")),
                    )
                    retirement_default = _parse_date_value(current.get("retirement_system_start_date")) or date.today()
                    retirement_system_start_date = st.date_input(
                        "기존 정년제도 시행일",
                        value=retirement_default,
                    )
                with d2:
                    use_continued_date = st.checkbox(
                        "계속고용제도 시행일·예정일을 확인함",
                        value=bool(current.get("continued_system_effective_date")),
                    )
                    continued_default = _parse_date_value(current.get("continued_system_effective_date")) or date.today()
                    continued_system_effective_date = st.date_input(
                        "계속고용제도 시행일 또는 예정일",
                        value=continued_default,
                    )
                    report_options = ["확인필요", "완료", "미완료"]
                    saved_report = current.get("continued_work_rules_reported")
                    report_label = "완료" if saved_report is True else "미완료" if saved_report is False else "확인필요"
                    report_choice = st.radio(
                        "취업규칙 명시·신고",
                        report_options,
                        index=report_options.index(report_label),
                        horizontal=True,
                    )
                    continued_work_rules_reported = True if report_choice == "완료" else False if report_choice == "미완료" else None
                    continued_support_months = st.number_input(
                        "예상 지원개월",
                        min_value=1,
                        max_value=36,
                        value=int(current.get("continued_support_months", 36) or 36),
                        step=1,
                    )

            submitted = st.form_submit_button(
                "추가정보 확인 후 통합진단",
                type="primary",
                use_container_width=True,
            )

        settings = {
            "region_type": address_region_type,
            "region_source": "company_address",
            "company_address": company_address,
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
            "continued_system_type": continued_system_type,
            "retirement_system_start_date": retirement_system_start_date.isoformat() if use_retirement_date else "",
            "continued_system_effective_date": continued_system_effective_date.isoformat() if use_continued_date else "",
            "continued_work_rules_reported": continued_work_rules_reported,
            "continued_support_months": int(continued_support_months),
        }
        if submitted:
            _save_settings(user_id, business_no, company_name, settings)
            st.success("추가정보를 저장하고 통합진단에 반영했습니다.")
            st.rerun()

    settings = _load_settings(user_id, business_no, company_name)
    settings = {
        **settings,
        "region_type": address_region_type,
    }
    results = sorted(
        [_score(item, metrics, settings) for item in EMPLOYMENT_SUPPORT_CATALOG],
        key=lambda item: item["score"],
        reverse=True,
    )

    for item in results:
        item["bucket"] = _result_bucket(item["score"])

    likely_count = sum(1 for item in results if item["bucket"] == "유력 검토")
    review_count = sum(1 for item in results if item["bucket"] == "조건 확인")
    insufficient_count = sum(1 for item in results if item["bucket"] == "현재 정보 부족")

    st.markdown("##### 기업별 고용지원금 종합결과")
    s1, s2, s3 = st.columns(3)
    s1.metric("유력 검토", f"{likely_count}개")
    s2.metric("조건 확인", f"{review_count}개")
    s3.metric("현재 정보 부족", f"{insufficient_count}개")

    if not metrics["has_employee_roster"]:
        st.info("직원명부가 없으므로 인원 기반 점수는 반영하지 않았습니다. 기업계획 정보만으로 임시 분류합니다.")

    summary_rows = [
        {
            "우선순위": idx,
            "판정": item["bucket"],
            "점수": item["score"],
            "분류": item["category"],
            "지원제도": item["name"],
            "자동근거": " / ".join(item["evidence"]) if item["evidence"] else "추가 확인 필요",
        }
        for idx, item in enumerate(results, start=1)
    ]
    st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)

    st.markdown("##### 지원금별 상세진단 및 실행방안")
    visible_results = [item for item in results if item["score"] >= 40]
    if not visible_results:
        st.info("기업·채용계획 추가정보를 입력하면 우선 검토할 지원금과 실행계획이 표시됩니다.")
    else:
        for index, item in enumerate(visible_results, start=1):
            with st.expander(
                f"{index}. [{item['bucket']}] {item['name']} · {item['score']}점",
                expanded=index <= 3,
            ):
                st.write(f"**지원개요:** {item['support']}")

                if item["evidence"]:
                    st.markdown("**현재 확인된 근거**")
                    for evidence in item["evidence"]:
                        st.write(f"- {evidence}")
                else:
                    st.warning("현재 CRM 정보만으로 자동 확인된 근거가 부족합니다.")

                st.markdown("**최종 신청 전 확인사항**")
                for check in item["confirm"]:
                    st.write(f"- □ {check}")

                st.markdown("**구체적인 실행방안**")
                for step_no, step in enumerate(_execution_plan_for_item(item["id"]), start=1):
                    st.write(f"{step_no}. {step}")

                if item["id"] == "senior_continued":
                    if not metrics["has_employee_roster"]:
                        st.warning("실제 가입자명부가 확인되기 전에는 60세 이상 비율·대상자·예상금액을 계산하지 않습니다.")
                    else:
                        rows = metrics.get("continued_candidate_rows", [])
                        if rows:
                            st.markdown("**가입자명부 기준 고령자 후보자**")
                            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                        else:
                            st.info("실제 가입자명부에서 고령자로 확인된 근로자가 없습니다.")

                    if (
                        settings.get("continued_employment_system")
                        or settings.get("continued_system_type") not in {None, "", "미확인"}
                    ):
                        _render_continued_employment_execution_plan(business_no, metrics, settings)
                    else:
                        st.info("계속고용제도 운영·계획을 입력하면 정년규정과 취업규칙 기준의 상세 실행계획이 열립니다.")

                st.caption(f"자료기준: {item['source']}")

    with st.expander(f"2026 고용지원금 전체 목록 {len(EMPLOYMENT_SUPPORT_CATALOG)}개", expanded=False):
        st.dataframe(
            pd.DataFrame([
                {
                    "분류": item["category"],
                    "지원제도": item["name"],
                    "지원개요": item["support"],
                    "자료기준": item["source"],
                }
                for item in EMPLOYMENT_SUPPORT_CATALOG
            ]),
            hide_index=True,
            use_container_width=True,
        )
