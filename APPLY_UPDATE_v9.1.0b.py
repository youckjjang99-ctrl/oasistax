from __future__ import annotations

import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

EXPECTED = {"v9.1.0a"}
TARGET = "v9.1.0b"
IMPORT_LINE = "from financial_anomaly import build_financial_anomaly"
IMPORT_ANCHOR = "from tax_diagnosis import build_tax_diagnosis"
BLOCK_MARKER = "# v9.1.0b 재무 이상징후 분석"
BLOCK_ANCHOR = '        st.caption(tax_core.get("disclaimer", ""))\n\n    source_columns = st.columns(5, gap="medium")'
BLOCK = '''        st.caption(tax_core.get("disclaimer", ""))

    # v9.1.0b 재무 이상징후 분석
    try:
        anomaly_result = build_financial_anomaly(user_id, customer)
    except Exception:
        anomaly_result = {}
    if anomaly_result:
        st.markdown("### 재무 이상징후")
        ac1, ac2, ac3, ac4 = st.columns(4, gap="medium")
        ac1.metric("종합 위험도", f"{anomaly_result.get('overall_score', 0)}점", anomaly_result.get("overall_level", ""))
        ac2.metric("AI 신뢰도", f"{anomaly_result.get('confidence', 0)}%", anomaly_result.get("source", ""))
        ac3.metric("높음", f"{anomaly_result.get('high_count', 0)}건", "우선 원장 확인")
        ac4.metric("보통", f"{anomaly_result.get('medium_count', 0)}건", "증빙 보완 검토")
        anomaly_rows = []
        for anomaly in anomaly_result.get("items", []):
            ratio = anomaly.get("ratio")
            anomaly_rows.append({
                "이상징후": anomaly.get("name", ""),
                "분류": anomaly.get("category", ""),
                "위험도": anomaly.get("severity", ""),
                "점수": anomaly.get("score", 0),
                "매출·자산 대비": f"{ratio:.1f}%" if isinstance(ratio, (int, float)) else "자료 부족",
                "우선 확인자료": " / ".join(anomaly.get("documents", [])[:2]),
            })
        if anomaly_rows:
            st.dataframe(pd.DataFrame(anomaly_rows), hide_index=True, use_container_width=True)
        with st.expander("이상징후 판단 근거·확인자료", expanded=False):
            for anomaly in anomaly_result.get("items", []):
                st.markdown(f"**{anomaly.get('name')} · {anomaly.get('severity')} · {anomaly.get('score', 0)}점**")
                for reason in anomaly.get("reasons", [])[:4]:
                    st.write(f"✓ {reason}")
                if anomaly.get("account_paths"):
                    st.caption("탐지 계정: " + ", ".join(anomaly.get("account_paths", [])[:5]))
                st.caption("확인자료: " + ", ".join(anomaly.get("documents", [])[:4]))
                for question in anomaly.get("questions", [])[:2]:
                    st.write(f"• {question}")
                if anomaly.get("caution"):
                    st.warning(anomaly.get("caution"))
        st.caption(anomaly_result.get("disclaimer", ""))

    source_columns = st.columns(5, gap="medium")'''


def normalize_version(raw: str) -> str:
    return raw.replace("\\n", "").replace("\\r", "").strip()


def main() -> int:
    root = Path(__file__).resolve().parent
    payload = root / "payload"
    version_path = root / "VERSION.txt"
    report_path = root / "consulting_report.py"
    engine_target = root / "financial_anomaly.py"

    current_raw = version_path.read_text(encoding="utf-8") if version_path.exists() else ""
    current = normalize_version(current_raw)
    if current == TARGET:
        print("UPDATE_OK")
        print(f"VERSION={TARGET}")
        print("ALREADY_APPLIED=YES")
        return 0
    if current not in EXPECTED:
        raise RuntimeError(f"Expected {sorted(EXPECTED)} but found {current_raw!r} (normalized={current!r}).")
    if not report_path.exists():
        raise FileNotFoundError("consulting_report.py not found")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = root / "backup" / f"before_{TARGET}_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    for path in (version_path, report_path, engine_target):
        if path.exists():
            shutil.copy2(path, backup / path.name)

    try:
        source = report_path.read_text(encoding="utf-8")
        if IMPORT_LINE not in source:
            if IMPORT_ANCHOR not in source:
                raise RuntimeError("tax_diagnosis import anchor not found")
            source = source.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + "\n" + IMPORT_LINE, 1)
        if BLOCK_MARKER not in source:
            if BLOCK_ANCHOR not in source:
                raise RuntimeError("AI tax core UI anchor not found")
            source = source.replace(BLOCK_ANCHOR, BLOCK, 1)

        shutil.copy2(payload / "financial_anomaly.py", engine_target)
        report_path.write_text(source, encoding="utf-8")
        version_path.write_text(TARGET + "\n", encoding="utf-8")

        py_compile.compile(str(engine_target), doraise=True)
        py_compile.compile(str(report_path), doraise=True)
        if IMPORT_LINE not in source or BLOCK_MARKER not in source:
            raise RuntimeError("post-update marker validation failed")
    except Exception:
        for name in ("VERSION.txt", "consulting_report.py", "financial_anomaly.py"):
            saved = backup / name
            target = root / name
            if saved.exists():
                shutil.copy2(saved, target)
            elif target.exists() and name == "financial_anomaly.py":
                target.unlink()
        raise

    print("UPDATE_OK")
    print(f"VERSION={TARGET}")
    print(f"BACKUP={backup}")
    print("FINANCIAL_ANOMALY_ENGINE=ENABLED")
    print("HIDDEN_ADVANCE_PAYMENT_SIGNAL=ENABLED")
    print("EXPENSE_RATIO_REVIEW=ENABLED")
    print("EXECUTIVE_COMPENSATION_REVIEW=ENABLED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"UPDATE_FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
