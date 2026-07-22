from __future__ import annotations
import py_compile
import re
import shutil
from datetime import datetime
from pathlib import Path

PATCH_VERSION = "v9.0.0"
ALLOWED = {"v8.8.0", "v8.8.1"}


def norm(raw: str) -> str:
    value = str(raw or "").replace("\ufeff", "").replace("\\r", "").replace("\\n", "").strip()
    match = re.search(r"v?\d+\.\d+\.\d+", value, re.I)
    if not match:
        return value
    version = match.group(0)
    return version if version.lower().startswith("v") else "v" + version


def rep(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 target block but found {count}. No files changed.")
    return text.replace(old, new, 1)


def patch(text: str) -> str:
    text = rep(
        text,
        """from comprehensive_financial_diagnosis import (\n    build_comprehensive_financial_diagnosis,\n)\n""",
        """from comprehensive_financial_diagnosis import (\n    build_comprehensive_financial_diagnosis,\n)\nfrom data_completeness_engine import build_data_completeness\nfrom company_health_engine import build_company_health\n""",
        "engine imports",
    )

    start = "    # 자료 충족도는 단순 필드 개수가 아니라 실제 컨설팅에 필요한 자료군을\n"
    end = """    missing_sources = [\n        name for name, _, is_ready in completeness_components if not is_ready\n    ]\n"""
    a = text.find(start)
    b = text.find(end, a)
    if a < 0 or b < 0:
        raise RuntimeError("completeness block not found")
    b += len(end)
    new_block = """    completeness_result = build_data_completeness(\n        business_no=business_no, industry=industry, address=address,\n        establishment=establishment, financial=financial, registry=registry,\n        stock_record=stock_record, consultation_context=consultation_context,\n        employee_context=employee_context, articles_review=articles_review,\n        preferences=preferences,\n    )\n    completeness = completeness_result[\"score\"]\n    completeness_status = completeness_result[\"status\"]\n    completeness_components = completeness_result[\"components\"]\n    missing_sources = completeness_result[\"missing_sources\"]\n    company_health = build_company_health(\n        sales=sales, operating_profit=operating_profit, net_income=net_income,\n        assets=assets, liabilities=liabilities, equity=equity, employees=employees,\n        completeness=completeness, confidence=completeness_result[\"confidence\"],\n        comprehensive_diagnosis=comprehensive_diagnosis,\n        consultation_context=consultation_context, stock_record=stock_record,\n    )\n    has_preferences = bool(preferences and any(preferences.get(k) for k in (\n        \"관심지원분야\", \"매칭키워드\", \"자금사용목적\", \"저장정책자금\"\n    )))\n"""
    text = text[:a] + new_block + text[b:]

    text = rep(
        text,
        """        \"completeness\": completeness,\n        \"completeness_status\": completeness_status,\n        \"completeness_components\": completeness_components,\n        \"missing_sources\": missing_sources,\n""",
        """        \"completeness\": completeness,\n        \"completeness_status\": completeness_status,\n        \"completeness_components\": completeness_components,\n        \"missing_sources\": missing_sources,\n        \"completeness_detail\": completeness_result,\n        \"ai_confidence\": completeness_result[\"confidence\"],\n        \"ai_confidence_status\": completeness_result[\"confidence_status\"],\n        \"company_health\": company_health,\n""",
        "extended return",
    )

    old_dashboard = """    _inject_report_css()\n    kpi_columns = st.columns(4, gap=\"medium\")\n    kpi_items = [\n        (\"자료 충족도\", f\"{analysis['completeness']}%\", analysis.get(\"completeness_status\", \"보완 필요\"), \"blue\"),\n        (\"매출액\", _format_money(analysis[\"sales\"]), \"최근 결산 기준\", \"green\"),\n        (\"영업이익\", _format_money(analysis[\"operating_profit\"]), \"본업 수익성\", \"purple\"),\n        (\"당기순이익\", _format_money(analysis[\"net_income\"]), \"세후 최종손익\", \"orange\"),\n    ]\n    for column, (label, value, note, tone) in zip(kpi_columns, kpi_items):\n        with column:\n            st.markdown(_metric_card(label, value, note, tone), unsafe_allow_html=True)\n\n    missing_sources = analysis.get(\"missing_sources\", []) or []\n    if missing_sources:\n        st.info(\n            \"자료 충족도에 반영되지 않은 항목: \" + \", \".join(missing_sources)\n            + \" · 해당 자료를 등록하면 진단 범위와 정확도가 높아집니다.\"\n        )\n"""
    new_dashboard = """    _inject_report_css()\n    health = analysis.get(\"company_health\", {}) or {}\n    kpi_columns = st.columns(5, gap=\"medium\")\n    kpi_items = [\n        (\"자료 충족도\", f\"{analysis['completeness']}%\", analysis.get(\"completeness_status\", \"자료 부족\"), \"blue\"),\n        (\"AI 신뢰도\", f\"{analysis.get('ai_confidence', 0)}%\", analysis.get(\"ai_confidence_status\", \"낮음\"), \"purple\"),\n        (\"기업 건강점수\", f\"{health.get('health_score', 0)}점\", health.get(\"stage\", \"판단보류\"), \"green\"),\n        (\"절세 가능성\", f\"{health.get('tax_opportunity_score', 0)}점\", health.get(\"tax_opportunity_level\", \"자료 부족\"), \"orange\"),\n        (\"세무 리스크\", f\"{health.get('tax_risk_score', 0)}점\", \"높을수록 추가 확인 필요\", \"red\"),\n    ]\n    for column, (label, value, note, tone) in zip(kpi_columns, kpi_items):\n        with column:\n            st.markdown(_metric_card(label, value, note, tone), unsafe_allow_html=True)\n    st.caption(f\"기업 성장단계: {health.get('stage','판단보류')} · {health.get('stage_reason','')}\")\n    detail = analysis.get(\"completeness_detail\", {}) or {}\n    actions = detail.get(\"next_actions\", []) or []\n    if actions:\n        st.info(\"현재 분석은 보수적으로 산정되었습니다. \" + \" / \".join(\n            f\"{x.get('name')} 등록 시 최대 +{x.get('gain',0)}%p\" for x in actions[:4]))\n    with st.expander(\"자료 충족도·AI 신뢰도 산정근거\", expanded=False):\n        rows = [{\n            \"자료군\": x.get(\"name\", \"\"), \"배점\": x.get(\"weight\", 0),\n            \"반영점수\": x.get(\"earned\", 0),\n            \"등록상태\": \"반영\" if x.get(\"ready\") else \"미등록\",\n            \"다음 조치\": x.get(\"next_action\", \"\"),\n        } for x in detail.get(\"components\", []) or []]\n        if rows:\n            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)\n        st.warning(\"크레탑은 요약재무로만 평가합니다. 계정별원장·법인세 신고자료 등 독립 증빙이 없으면 점수가 높게 올라가지 않습니다.\")\n        st.caption(health.get(\"disclaimer\", \"\"))\n"""
    return rep(text, old_dashboard, new_dashboard, "dashboard")


def main() -> None:
    root = Path(__file__).resolve().parent
    version_path = root / "VERSION.txt"
    report_path = root / "consulting_report.py"
    completeness_path = root / "data_completeness_engine.py"
    health_path = root / "company_health_engine.py"
    completeness_payload = root / "PATCH_DATA_COMPLETENESS_ENGINE.txt"
    health_payload = root / "PATCH_COMPANY_HEALTH_ENGINE.txt"

    raw = version_path.read_text(encoding="utf-8") if version_path.exists() else ""
    current = norm(raw)
    if current not in ALLOWED:
        raise RuntimeError(f"Expected {sorted(ALLOWED)} but found {raw!r} (normalized={current!r}).")
    for path in [report_path, completeness_payload, health_payload]:
        if not path.exists():
            raise FileNotFoundError(path.name)

    updated = patch(report_path.read_text(encoding="utf-8"))
    backup = root / "backup" / f"{PATCH_VERSION}_{datetime.now():%Y%m%d_%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)
    for path in [report_path, version_path, completeness_path, health_path]:
        if path.exists():
            shutil.copy2(path, backup / path.name)
    completeness_existed = completeness_path.exists()
    health_existed = health_path.exists()

    try:
        report_path.write_text(updated, encoding="utf-8")
        completeness_path.write_text(completeness_payload.read_text(encoding="utf-8"), encoding="utf-8")
        health_path.write_text(health_payload.read_text(encoding="utf-8"), encoding="utf-8")
        version_path.write_text(PATCH_VERSION + "\n", encoding="utf-8")
        for path in [report_path, completeness_path, health_path]:
            py_compile.compile(str(path), doraise=True)
    except Exception:
        shutil.copy2(backup / report_path.name, report_path)
        if (backup / version_path.name).exists():
            shutil.copy2(backup / version_path.name, version_path)
        if completeness_existed:
            shutil.copy2(backup / completeness_path.name, completeness_path)
        elif completeness_path.exists():
            completeness_path.unlink()
        if health_existed:
            shutil.copy2(backup / health_path.name, health_path)
        elif health_path.exists():
            health_path.unlink()
        raise
    finally:
        for path in [completeness_payload, health_payload]:
            if path.exists():
                path.unlink()

    print("UPDATE_OK")
    print("VERSION=v9.0.0")
    print(f"BASE_VERSION={current}")
    print(f"BACKUP_PATH={backup}")
    print("MODIFIED_FILES=consulting_report.py,data_completeness_engine.py,company_health_engine.py,VERSION.txt")
    print("CONSERVATIVE_COMPLETENESS=ENABLED")
    print("AI_CONFIDENCE=ENABLED")
    print("COMPANY_HEALTH_SCORE=ENABLED")
    print("COMPANY_LIFECYCLE_STAGE=ENABLED")
    print("TAX_OPPORTUNITY_SCORE=ENABLED")
    print("TAX_RISK_SCORE=ENABLED")
    print("VERSION_LITERAL_NEWLINE_FIX=ENABLED")
    print("DATABASE_SCHEMA=UNCHANGED")
    print("EXISTING_DATA=UNCHANGED")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"UPDATE_FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
