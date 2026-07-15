from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v7.4.1"
MARKER = "# v7.4.1 compatibility layer"


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def main() -> None:
    root = Path.cwd()
    target = root / "enterprise_customer_management.py"
    if not target.exists():
        fail(
            "Run this patch from the OASIS project root folder. "
            "enterprise_customer_management.py was not found."
        )

    version_path = root / "VERSION.txt"
    current = (
        version_path.read_text(
            encoding="utf-8-sig"
        ).strip()
        if version_path.exists()
        else ""
    )
    if current and current not in {
        "v7.4.0",
        "7.4.0",
        "v7.4.1",
        "7.4.1",
    }:
        fail(f"Expected v7.4.0 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v7.4.1_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)

    for relative in [
        "enterprise_customer_management.py",
        "VERSION.txt",
    ]:
        source = root / relative
        if source.exists():
            destination = backup / relative
            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            shutil.copy2(source, destination)

    target_text = target.read_text(encoding="utf-8")
    append_path = (
        root
        / "payload"
        / "enterprise_customer_compat_append.py.txt"
    )
    if not append_path.exists():
        fail(
            "payload/enterprise_customer_compat_append.py.txt "
            "was not found."
        )

    if MARKER not in target_text:
        compatibility_code = append_path.read_text(
            encoding="utf-8"
        )
        target.write_text(
            target_text.rstrip()
            + "\n\n"
            + compatibility_code.strip()
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    version_path.write_text(
        VERSION + "\n",
        encoding="utf-8",
    )

    changelog = (
        root / "payload" / "CHANGELOG_v7.4.1.md"
    )
    if changelog.exists():
        shutil.copy2(
            changelog,
            root / "CHANGELOG_v7.4.1.md",
        )

    # Validate the actual project import chain.
    for name in [
        "enterprise_customer_management.py",
        "enterprise_center.py",
        "app.py",
        "employee_status.py",
    ]:
        path = root / name
        if path.exists():
            py_compile.compile(
                str(path),
                doraise=True,
            )

    updated_text = target.read_text(encoding="utf-8")
    for function_name in [
        "def filter_active_customers(",
        "def confirm_delete_dialog(",
        "def render_customer_trash_page(",
    ]:
        if function_name not in updated_text:
            fail(
                f"Compatibility function was not added: "
                f"{function_name}"
            )

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=NO")
    print(
        "RESULT=Missing customer search, delete dialog, "
        "and trash page imports restored."
    )
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
