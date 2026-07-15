from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v7.4.0"
TARGETS = [
    "employee_status.py",
    "enterprise_center.py",
    "multi_source_policy.py",
    "auth.py",
    "app.py",
    "VERSION.txt",
]


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def main() -> None:
    root = Path.cwd()
    if not (root / "enterprise_center.py").exists():
        fail("Run this patch from the OASIS project root folder.")

    version_path = root / "VERSION.txt"
    current = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists()
        else ""
    )
    if current and current not in {
        "v7.3.0",
        "7.3.0",
        "v7.4.0",
        "7.4.0",
    }:
        fail(f"Expected v7.3.0 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v7.4.0_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)

    for relative in TARGETS:
        src = root / relative
        if src.exists():
            dst = backup / relative
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    for relative in [
        "employee_status.py",
        "enterprise_center.py",
        "multi_source_policy.py",
        "auth.py",
        "app.py",
    ]:
        src = root / "payload" / relative
        if not src.exists():
            fail(f"payload/{relative} is missing.")
        dst = root / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    version_path.write_text(VERSION + "\n", encoding="utf-8")

    changelog_src = root / "payload" / "CHANGELOG_v7.4.0.md"
    if changelog_src.exists():
        shutil.copy2(
            changelog_src,
            root / "CHANGELOG_v7.4.0.md",
        )

    for name in [
        "employee_status.py",
        "enterprise_center.py",
        "multi_source_policy.py",
        "auth.py",
        "app.py",
    ]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=supabase_v740_upgrade.sql")
    print(
        "RESULT=Employee roster upload, employment-support matching, "
        "and password change enabled."
    )
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
