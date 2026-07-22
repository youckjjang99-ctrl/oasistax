from __future__ import annotations

import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

EXPECTED = "v9.3.2"
TARGET = "v9.3.3"
FILES = ["employment_support_2026.py", "VERSION.txt"]

def main() -> int:
    root = Path(__file__).resolve().parent
    payload = root / "payload"
    version_file = root / "VERSION.txt"

    current = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    if current != EXPECTED:
        print(f"UPDATE_FAILED: Expected {EXPECTED} but found {current or 'UNKNOWN'}")
        return 1

    backup = root / "_update_backups" / f"{EXPECTED}_before_{TARGET}_{datetime.now():%Y%m%d_%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)

    copied = []
    try:
        for name in FILES:
            target = root / name
            source = payload / name
            if not source.exists():
                raise FileNotFoundError(f"Missing payload: {source}")
            if target.exists():
                shutil.copy2(target, backup / name)
            shutil.copy2(source, target)
            copied.append(name)

        py_compile.compile(str(root / "employment_support_2026.py"), doraise=True)

        print("UPDATE_OK")
        print(f"VERSION={TARGET}")
        print("EMPLOYMENT_SUPPORT_UNIFIED_DIAGNOSIS=ENABLED")
        print("SYNTHETIC_EMPLOYEE_ROWS=BLOCKED")
        print("SENIOR_CONTINUED_DETAIL=INSIDE_SUPPORT_CARD")
        print("DB_SCHEMA=PRESERVED")
        print(f"BACKUP={backup}")
        return 0
    except Exception as exc:
        print(f"UPDATE_FAILED: {exc}")
        for name in copied:
            backup_file = backup / name
            target = root / name
            if backup_file.exists():
                shutil.copy2(backup_file, target)
        print(f"ROLLBACK={backup}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
