from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v7.1.0"
TARGETS = [
    "enterprise_center.py",
    "enterprise_customer_management.py",
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
        version_path.read_text(
            encoding="utf-8-sig"
        ).strip()
        if version_path.exists()
        else ""
    )
    if current and current not in {
        "v7.0.1",
        "7.0.1",
        "v7.1.0",
        "7.1.0",
    }:
        fail(f"Expected v7.0.1 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v7.1.0_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)

    for name in TARGETS:
        src = root / name
        if src.exists():
            dst = backup / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    for relative in [
        "enterprise_center.py",
        "enterprise_customer_management.py",
    ]:
        src = root / "payload" / relative
        if not src.exists():
            fail(f"payload/{relative} is missing.")
        shutil.copy2(src, root / relative)

    version_path.write_text(
        VERSION + "\n",
        encoding="utf-8",
    )

    changelog_src = (
        root / "payload" / "CHANGELOG_v7.1.0.md"
    )
    if changelog_src.exists():
        shutil.copy2(
            changelog_src,
            root / "CHANGELOG_v7.1.0.md",
        )

    for name in [
        "enterprise_center.py",
        "enterprise_customer_management.py",
    ]:
        py_compile.compile(
            str(root / name),
            doraise=True,
        )

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=supabase_v710_upgrade.sql")
    print(
        "RESULT=Enterprise customer search, filters, "
        "trash deletion, and restore enabled."
    )
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
