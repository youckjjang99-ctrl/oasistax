from __future__ import annotations
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

TARGET_VERSION = "v9.2.0"
EXPECTED_VERSION = "v9.1.0b"
IMPORT_LINE = "from enterprise_health import build_enterprise_health\n"
IMPORT_ANCHOR = "from financial_anomaly import build_financial_anomaly\n"
UI_MARKER = '    source_columns = st.columns(5, gap="medium")\n'
UI_COMMENT = "# v9.2.0 AI 기업 건강진단 통합 대시보드"


def restore(backup: Path, root: Path) -> None:
    for name in ("consulting_report.py", "VERSION.txt", "enterprise_health.py"):
        saved = backup / name
        target = root / name
        if saved.exists():
            shutil.copy2(saved, target)
        elif name == "enterprise_health.py" and target.exists():
            target.unlink()


def main() -> None:
    root = Path(__file__).resolve().parent
    version_file = root / "VERSION.txt"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    if current != EXPECTED_VERSION:
        print(f"UPDATE_FAILED: Expected {EXPECTED_VERSION} but found {current or 'UNKNOWN'}")
        sys.exit(1)

    source = root / "consulting_report.py"
    payload_engine = root / "payload" / "enterprise_health.py"
    payload_ui = root / "payload" / "ui_block.txt"
    if not source.exists() or not payload_engine.exists() or not payload_ui.exists():
        print("UPDATE_FAILED: required patch files missing")
        sys.exit(1)

    backup = root / "backup" / f"before_v9.2.0_{datetime.now():%Y%m%d_%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)
    for target in (source, version_file, root / "enterprise_health.py"):
        if target.exists():
            shutil.copy2(target, backup / target.name)

    try:
        text = source.read_text(encoding="utf-8")
        if IMPORT_LINE.strip() not in text:
            if IMPORT_ANCHOR not in text:
                raise RuntimeError("enterprise health import anchor not found")
            text = text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_LINE, 1)
        if UI_COMMENT not in text:
            if UI_MARKER not in text:
                raise RuntimeError("enterprise health UI insertion marker not found")
            ui_block = payload_ui.read_text(encoding="utf-8")
            text = text.replace(UI_MARKER, ui_block + UI_MARKER, 1)
        source.write_text(text, encoding="utf-8")
        shutil.copy2(payload_engine, root / "enterprise_health.py")
        version_file.write_text(TARGET_VERSION + "\n", encoding="utf-8")
        py_compile.compile(str(source), doraise=True)
        py_compile.compile(str(root / "enterprise_health.py"), doraise=True)
    except Exception as exc:
        restore(backup, root)
        print(f"UPDATE_FAILED: {exc}")
        print(f"ROLLBACK={backup}")
        sys.exit(1)

    print("UPDATE_OK")
    print("VERSION=v9.2.0")
    print(f"BACKUP={backup}")
    print("ENTERPRISE_HEALTH_DASHBOARD=ENABLED")
    print("INTEGRATED_ACTION_PRIORITY=ENABLED")
    print("EXISTING_DB_STRUCTURE=PRESERVED")


if __name__ == "__main__":
    main()
