from __future__ import annotations

import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path

TARGET_VERSION = "v9.3.0"
EXPECTED_VERSION = "v9.2.0"
FILES = (
    "consulting_copilot.py",
    "consultation_scenario_engine.py",
    "VERSION.txt",
)


def restore(backup: Path, root: Path) -> None:
    for name in FILES:
        saved = backup / name
        target = root / name
        if saved.exists():
            shutil.copy2(saved, target)
        elif name == "consultation_scenario_engine.py" and target.exists():
            target.unlink()


def main() -> None:
    root = Path(__file__).resolve().parent
    payload = root / "payload"
    version_file = root / "VERSION.txt"
    current = (
        version_file.read_text(encoding="utf-8").strip()
        if version_file.exists()
        else ""
    )
    if current != EXPECTED_VERSION:
        print(
            f"UPDATE_FAILED: Expected {EXPECTED_VERSION} "
            f"but found {current or 'UNKNOWN'}"
        )
        sys.exit(1)

    required = (
        payload / "consulting_copilot.py",
        payload / "consultation_scenario_engine.py",
    )
    if any(not path.exists() for path in required):
        print("UPDATE_FAILED: required patch files missing")
        sys.exit(1)

    backup = (
        root
        / "backup"
        / f"before_v9.3.0_{datetime.now():%Y%m%d_%H%M%S}"
    )
    backup.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        target = root / name
        if target.exists():
            shutil.copy2(target, backup / name)

    try:
        shutil.copy2(
            payload / "consulting_copilot.py",
            root / "consulting_copilot.py",
        )
        shutil.copy2(
            payload / "consultation_scenario_engine.py",
            root / "consultation_scenario_engine.py",
        )
        version_file.write_text(TARGET_VERSION + "\n", encoding="utf-8")

        py_compile.compile(
            str(root / "consulting_copilot.py"),
            doraise=True,
        )
        py_compile.compile(
            str(root / "consultation_scenario_engine.py"),
            doraise=True,
        )
    except Exception as exc:
        restore(backup, root)
        print(f"UPDATE_FAILED: {exc}")
        print(f"ROLLBACK={backup}")
        sys.exit(1)

    print("UPDATE_OK")
    print("VERSION=v9.3.0")
    print(f"BACKUP={backup}")
    print("AI_CONSULTING_SCENARIO_ENGINE=ENABLED")
    print("NEXT_QUESTION_RECOMMENDATION=ENABLED")
    print("REPRESENTATIVE_ANSWER_ANALYSIS=ENABLED")
    print("EXISTING_DB_STRUCTURE=PRESERVED")


if __name__ == "__main__":
    main()
