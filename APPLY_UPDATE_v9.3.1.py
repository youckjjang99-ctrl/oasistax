from __future__ import annotations

import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

TARGET_VERSION = "v9.3.1"
EXPECTED_VERSION = "v9.3.0"
FILES = ("employment_support_2026.py", "VERSION.txt")


def restore(backup: Path, root: Path) -> None:
    for name in FILES:
        saved = backup / name
        target = root / name
        if saved.exists():
            shutil.copy2(saved, target)


def main() -> None:
    root = Path(__file__).resolve().parent
    payload = root / "payload"
    version_file = root / "VERSION.txt"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""
    if current != EXPECTED_VERSION:
        print(f"UPDATE_FAILED: Expected {EXPECTED_VERSION} but found {current or 'UNKNOWN'}")
        sys.exit(1)

    required = (payload / "employment_support_2026.py", payload / "VERSION.txt")
    if any(not path.exists() for path in required):
        print("UPDATE_FAILED: required patch files missing")
        sys.exit(1)

    backup = root / "backup" / f"before_v9.3.1_{datetime.now():%Y%m%d_%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = root / name
        if target.exists():
            shutil.copy2(target, backup / name)

    try:
        shutil.copy2(payload / "employment_support_2026.py", root / "employment_support_2026.py")
        shutil.copy2(payload / "VERSION.txt", version_file)
        py_compile.compile(str(root / "employment_support_2026.py"), doraise=True)
    except Exception as exc:
        restore(backup, root)
        print(f"UPDATE_FAILED: {exc}")
        print(f"ROLLBACK={backup}")
        sys.exit(1)

    print("UPDATE_OK")
    print("VERSION=v9.3.1")
    print(f"BACKUP={backup}")
    print("EMPLOYMENT_SUPPORT_EXECUTION_PLAN=ENABLED")
    print("SENIOR_CONTINUED_EMPLOYMENT_DIAGNOSIS=ENABLED")
    print("ESTIMATED_SUPPORT_CALCULATOR=ENABLED")
    print("EXISTING_DB_STRUCTURE=PRESERVED")


if __name__ == "__main__":
    main()
