from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from cloud_db import CloudDatabase, TABLE_FINANCIALS, cloud_is_configured
from utils import get_user_dirs


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return re.sub(r"\s+", " ", text)


def _number(value: Any) -> float | None:
    raw = _clean(value).replace(",", "")
    if not raw:
        return None

    multiplier = 1.0
    if "억원" in raw or raw.endswith("억"):
        multiplier = 100_000_000.0
    elif "백만원" in raw:
        multiplier = 1_000_000.0
    elif "천만원" in raw:
        multiplier = 10_000_000.0
    elif "만원" in raw:
        multiplier = 10_000.0
    elif "천원" in raw:
        multiplier = 1_000.0

    text = re.sub(r"[^0-9.\-]", "", raw)
    if not text:
        return None
    try:
        return float(text) * multiplier
    except ValueError:
        return None

def _normalize_business_no(value: Any) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _financial_snapshot(user_id: str, business_no: str) -> dict[str, Any]:
    digits = _normalize_business_no(business_no)

    if digits and cloud_is_configured():
        try:
            rows = CloudDatabase().select(
                TABLE_FINANCIALS,
                filters={
                    "owner_user_id": user_id,
                    "business_no": (
                        f"{digits[:3]}-{digits[3:5]}-{digits[5:]}"
                        if len(digits) == 10 else business_no
                    ),
                },
                columns="financial_data",
                limit=1,
            )
            if rows and isinstance(rows[0].get("financial_data"), dict):
                snapshot = dict(rows[0]["financial_data"])
                snapshot["_source"] = "Supabase 재무자료"
                return snapshot
        except Exception:
            pass

    base = get_user_dirs(user_id)["base"]
    cache = _load_json(base / "stock_financial_cache.json", {})
    if not isinstance(cache, dict):
        return {}
    for key, value in cache.items():
        if _normalize_business_no(key) == digits and isinstance(value, dict):
            snapshot = dict(value)
            snapshot["_source"] = "크레탑 재무 캐시"
            return snapshot
    return {}


def _latest_year(financial: dict[str, Any]) -> str:
    years = []
    for row in financial.get("재무연도별", []) or []:
        if isinstance(row, dict):
            year = _clean(row.get("연도", ""))
            if year:
                years.append(year)
    return sorted(years, reverse=True)[0] if years else ""


def _has_positive_signal(text: str, words: list[str]) -> bool:
    cleaned = _clean(text).lower()
    for word in words:
        token = word.lower()
        if token not in cleaned:
            continue
        negative_patterns = [
            rf"{re.escape(token)}\s*(없음|없다|미보유|해당없음|계획없음|하지 않음)",
            rf"(없음|없다|미보유|해당없음|계획없음)\s*{re.escape(token)}",
        ]
        if not any(re.search(pattern, cleaned) for pattern in negative_patterns):
            return True
    return False


def _evidence(source: str, year: str, message: str) -> str:
    label = source or "고객DB"
    if year:
        label += f"·{year}년"
    return f"[{label}] {message}"

def _value(customer: pd.Series, financial: dict[str, Any], *keys: str) -> Any:
    for source in (customer, financial):
        for key in keys:
            try:
                value = source.get(key, "")
            except Exception:
                value = ""
            if _clean(value):
                return value
    return ""


def _contains(text: str, words: list[str]) -> bool:
    lowered = text.lower()
    return any(word.lower() in lowered for word in words)


def _item(name: str, category: str, status: str, reasons: list[str], missing: list[str], questions: list[str], documents: list[str], caution: str = "") -> dict[str, Any]:
    priority = {"검토 우선": 3, "자료 확인": 2, "가능성 낮음": 1}.get(status, 0)
    return {
        "name": name,
        "category": category,
        "status": status,
        "priority": priority,
        "reasons": reasons,
        "missing": missing,
        "questions": questions,
        "documents": documents,
        "caution": caution,
    }


def build_tax_diagnosis(user_id: str, customer: pd.Series) -> dict[str, Any]:
    business_no = _clean(customer.get("사업자등록번호", ""))
    financial = _financial_snapshot(user_id, business_no)
    company_name = _clean(customer.get("업체명", "")) or "선택 기업"
    industry = _clean(_value(customer, financial, "업종명", "업태", "종목"))
    address = _clean(_value(customer, financial, "사업장 소재지", "주소", "본점소재지"))
    establishment = _clean(_value(customer, financial, "설립일", "설립년도", "개업일"))
    employees = _number(_value(customer, financial, "종업원수", "상시근로자수", "직원수"))
    sales = _number(_value(customer, financial, "매출액", "연매출", "전년도매출"))
    tangible = _number(_value(customer, financial, "유형자산", "유형자산합계", "기계장치"))
    rnd = _number(_value(customer, financial, "연구개발비", "경상연구개발비"))
    tax = _number(_value(customer, financial, "법인세비용", "법인세", "당기법인세"))
    retained = _number(_value(customer, financial, "이익잉여금", "미처분이익잉여금"))
    loans = _number(_value(customer, financial, "가지급금", "단기대여금", "장기대여금"))
    customer_text = " ".join(_clean(v) for v in customer.astype(str).values)
    financial_text = " ".join(_clean(v) for key, v in financial.items() if not str(key).startswith("_"))
    text = f"{customer_text} {financial_text}"
    financial_source = _clean(financial.get("_source", ""))
    reference_year = _latest_year(financial)

    manufacturing = _contains(industry, ["제조", "가공", "생산", "공장"])
    construction = _contains(industry, ["건설", "공사", "토목", "전기공사", "설비공사"])
    tech_signal = _has_positive_signal(customer_text, ["연구소", "연구전담", "벤처", "특허", "r&d", "연구개발"])
    hiring_signal = _has_positive_signal(customer_text, ["신규채용", "직원증가", "청년", "고용증가", "채용계획"])
    investment_signal = tangible not in (None, 0) or _has_positive_signal(customer_text, ["기계", "설비", "시설투자", "스마트공장", "장비"])

    items: list[dict[str, Any]] = []

    reasons = []
    if manufacturing or construction:
        reasons.append(_evidence("고객DB", "", f"투자 발생 가능성이 높은 업종({industry or '업종정보'})"))
    if investment_signal:
        reasons.append(_evidence(financial_source or "고객DB", reference_year, "유형자산·설비 관련 정보가 확인됨"))
    status = "검토 우선" if investment_signal and (manufacturing or construction) else "자료 확인"
    items.append(_item(
        "통합투자세액공제", "세액공제", status, reasons or ["업종 및 자산정보만으로 적용 여부를 확정할 수 없음"],
        ["최근 5년 자산 취득내역", "투자자산 종류", "취득일·사용개시일", "신품·중고 여부", "기존 공제 신청내역"],
        ["최근 5년간 기계·설비·업무용 장비를 구입하셨나요?", "구입 자산이 신품인가요, 중고인가요?", "이미 세액공제를 신청한 적이 있나요?"],
        ["고정자산등록대장", "감가상각명세서", "세금계산서·계약서", "세액공제조정명세서"],
        "차량·건물·중고자산 등은 자산별 배제 여부를 별도로 확인해야 합니다.",
    ))

    reasons = []
    if tech_signal:
        reasons.append(_evidence("고객DB/상담정보", "", "연구소·특허·연구개발 관련 긍정 신호가 확인됨"))
    if rnd not in (None, 0):
        reasons.append(_evidence(financial_source or "고객DB", reference_year, "연구개발비 계정이 확인됨"))
    items.append(_item(
        "연구·인력개발비 세액공제", "세액공제", "검토 우선" if reasons else "자료 확인", reasons or ["연구개발 활동 여부가 등록자료에서 확인되지 않음"],
        ["연구개발 과제", "전담인력 현황", "인건비·재료비 명세", "기업부설연구소·전담부서 인정 여부"],
        ["제품·공정 개선을 위해 별도 연구개발을 수행하나요?", "연구전담 인력이나 기업부설연구소가 있나요?", "연구개발비를 별도 계정으로 관리하나요?"],
        ["연구개발계획서·과제기록", "연구원 인사자료", "급여대장", "연구개발비 명세"],
        "형식적인 연구기록만으로는 인정되기 어려우므로 실제 활동과 비용 귀속을 함께 검토해야 합니다.",
    ))

    reasons = []
    if employees is not None:
        reasons.append(_evidence(financial_source or "고객DB", reference_year, f"종업원수 {int(employees)}명 정보가 확인됨"))
    if hiring_signal:
        reasons.append(_evidence("고객DB/상담정보", "", "채용·고용증가 관련 긍정 신호가 확인됨"))
    items.append(_item(
        "고용 관련 세액공제", "고용", "검토 우선" if hiring_signal else "자료 확인", reasons or ["연도별 상시근로자 증감 자료가 없음"],
        ["최근 3~5년 월별 상시근로자 수", "청년·장애인 등 인적구성", "입퇴사일", "관계기업 포함 여부", "기존 고용공제 신청내역"],
        ["전년보다 직원 수가 늘었나요?", "신규 입사자 중 청년이나 경력단절 인력이 있나요?", "최근 2년 내 퇴사자가 많았나요?"],
        ["고용보험 가입자명부", "근로소득 원천징수자료", "급여대장", "세액공제조정명세서"],
        "고용공제는 인원 계산과 사후 고용유지 요건이 중요하므로 월별 자료 확인이 필요합니다.",
    ))

    items.append(_item(
        "중소기업 특별세액감면", "세액감면", "자료 확인", [_evidence("고객DB", "", f"업종: {industry or '미확인'}"), _evidence("고객DB", "", f"사업장: {address or '미확인'}")],
        ["중소기업 해당 여부", "정확한 주업종 코드", "사업장별 소득", "수도권 소재 여부", "기존 감면 적용내역"],
        ["법인세 신고 시 중소기업 특별세액감면을 적용받고 있나요?", "사업장이 여러 곳인가요?", "실제 주된 매출 업종은 무엇인가요?"],
        ["법인세 신고서", "중소기업기준검토표", "사업자등록증", "업종별 매출자료"],
        "업종·지역·기업규모와 다른 감면의 중복 적용 제한을 함께 확인해야 합니다.",
    ))

    startup_reason = _evidence("고객DB", "", f"설립정보: {establishment}") if establishment else "[고객DB] 설립일 정보가 부족함"
    items.append(_item(
        "창업·지역 관련 세액감면", "세액감면", "자료 확인", [startup_reason, _evidence("고객DB", "", f"사업장: {address or '미확인'}")],
        ["실질 창업 여부", "대표자의 기존 사업 이력", "업종 승계·법인전환 여부", "창업 당시 소재지", "연령·고용 요건"],
        ["신규 창업인가요, 개인사업의 법인전환인가요?", "대표님이 같은 업종 사업을 이전에 운영했나요?", "설립 이후 본점이나 공장을 이전했나요?"],
        ["법인등기", "사업자등록 이력", "대표자 사업자등록 사실증명", "주주명부"],
        "단순 법인전환·사업승계·분할은 창업으로 인정되지 않을 수 있습니다.",
    ))

    risk_reasons = []
    if loans not in (None, 0):
        risk_reasons.append(_evidence(financial_source or "고객DB", reference_year, "대여금·가지급금 관련 계정이 확인됨"))
    if retained not in (None, 0):
        risk_reasons.append(_evidence(financial_source or "고객DB", reference_year, "이익잉여금 누적 여부를 검토할 필요가 있음"))
    if tax is not None:
        risk_reasons.append(_evidence(financial_source or "고객DB", reference_year, "법인세 계정이 확인되어 신고자료 대조가 가능함"))
    items.append(_item(
        "법인 세무리스크 점검", "세무리스크", "검토 우선" if risk_reasons else "자료 확인", risk_reasons or ["재무계정 상세자료가 부족함"],
        ["가지급금·가수금 원장", "임원 보수·퇴직금 규정", "업무용승용차 내역", "특수관계인 거래", "최근 5년 신고조정사항"],
        ["대표자 개인 사용으로 처리된 법인자금이 있나요?", "임원 보수와 퇴직금 규정이 정관 및 결의서에 있나요?", "법인 차량 운행기록을 작성하나요?"],
        ["계정별원장", "정관·주총의사록", "업무용승용차 명세", "법인세 세무조정계산서"],
        "절세 기회와 별도로 가산세·상여처분 등 잠재 리스크를 점검하는 항목입니다.",
    ))

    items.sort(key=lambda x: (-x["priority"], x["name"]))
    immediate = sum(1 for x in items if x["status"] == "검토 우선")
    verify = sum(1 for x in items if x["status"] == "자료 확인")
    low = sum(1 for x in items if x["status"] == "가능성 낮음")

    # 준비도는 확정 점수가 아니라 현재 확보 자료의 범위를 5단계로 표시한다.
    signals = [bool(industry), bool(address), bool(establishment), sales is not None, employees is not None, tangible is not None, rnd is not None, tax is not None]
    filled = sum(signals)
    stars = min(5, max(1, round(filled / len(signals) * 5)))

    return {
        "company_name": company_name,
        "items": items,
        "immediate": immediate,
        "verify": verify,
        "low": low,
        "stars": stars,
        "basis": (
            f"고객DB·{financial_source} 기준" if financial_source
            else "현재 저장된 고객DB 기준"
        ),
        "reference_year": reference_year,
        "data_source": financial_source or "고객DB",
    }


def _status_style(status: str) -> tuple[str, str]:
    return {
        "검토 우선": ("#EAF8F2", "#087A55"),
        "자료 확인": ("#FFF4E5", "#A45B08"),
        "가능성 낮음": ("#F2F4F7", "#475467"),
    }.get(status, ("#EEF4FF", "#2E5AAC"))


def render_tax_diagnosis_page(user_id: str, customer: pd.Series, key_prefix: str = "tax") -> None:
    result = build_tax_diagnosis(user_id, customer)
    st.markdown("### AI 절세진단")
    st.caption("세무 확정판정이 아니라 현재 자료에서 발견된 검토기회와 추가 확인사항을 정리합니다.")

    c1, c2, c3, c4 = st.columns(4, gap="medium")
    metrics = [
        ("절세자료 준비", "★" * result["stars"] + "☆" * (5 - result["stars"]), result["basis"]),
        ("발견 항목", f"{len(result['items'])}건", "세액공제·감면·리스크"),
        ("검토 우선", f"{result['immediate']}건", "증빙 확인을 먼저 권장"),
        ("자료 확인", f"{result['verify']}건", "질문 및 서류 보완 필요"),
    ]
    for col, (label, value, note) in zip((c1, c2, c3, c4), metrics):
        with col:
            st.markdown(f"""
            <div style="border:1px solid #dbe4ef;border-radius:16px;padding:17px;background:#fff;box-shadow:0 5px 15px rgba(15,42,80,.06);min-height:120px">
              <div style="font-size:.82rem;color:#667085;font-weight:750">{html.escape(label)}</div>
              <div style="font-size:1.65rem;color:#0b2b5b;font-weight:900;margin:9px 0">{html.escape(value)}</div>
              <div style="font-size:.74rem;color:#667085">{html.escape(note)}</div>
            </div>""", unsafe_allow_html=True)

    st.info("공제·감면의 적용 여부와 금액은 사업연도별 법령, 신고내역 및 증빙을 세무사가 최종 확인해야 합니다.")

    filters = st.multiselect(
        "표시 영역",
        ["세액공제", "세액감면", "고용", "세무리스크"],
        default=["세액공제", "세액감면", "고용", "세무리스크"],
        key=f"{key_prefix}_categories",
    )
    visible = [x for x in result["items"] if x["category"] in filters]

    for index, item in enumerate(visible):
        bg, color = _status_style(item["status"])
        with st.expander(f"{item['status']} · {item['name']}", expanded=index < 2):
            st.markdown(
                f"<span style='display:inline-block;padding:5px 10px;border-radius:999px;background:{bg};color:{color};font-weight:800;font-size:.78rem'>{item['status']}</span>",
                unsafe_allow_html=True,
            )
            left, right = st.columns([1, 1], gap="large")
            with left:
                st.markdown("**현재 확인된 근거**")
                for reason in item["reasons"]:
                    st.write(f"✓ {reason}")
                st.markdown("**추가 확인이 필요한 정보**")
                for missing in item["missing"]:
                    st.write(f"□ {missing}")
            with right:
                st.markdown("**대표님께 확인할 질문**")
                for q in item["questions"]:
                    st.write(f"• {q}")
                st.markdown("**요청할 자료**")
                for document in item["documents"]:
                    st.write(f"• {document}")
            if item.get("caution"):
                st.warning(item["caution"])
