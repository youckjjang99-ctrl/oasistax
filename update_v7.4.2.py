from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v7.4.2"
INVALID = 'vertical_alignment="bottom"'
VALID = 'vertical_alignment="bottom"'


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def main() -> None:
    root = Path.cwd()
    enterprise_path = root / "enterprise_center.py"

    if not enterprise_path.exists():
        fail(
            "Run this patch from the OASIS project root folder. "
            "enterprise_center.py was not found."
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
        "v7.4.1",
        "7.4.1",
        "v7.4.2",
        "7.4.2",
    }:
        fail(f"Expected v7.4.1 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v7.4.2_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)

    for relative in [
        "enterprise_center.py",
        "VERSION.txt",
    ]:
        src = root / relative
        if src.exists():
            dst = backup / relative
            dst.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            shutil.copy2(src, dst)

    text = enterprise_path.read_text(encoding="utf-8")

    if INVALID in text:
        text = text.replace(INVALID, VALID)

    enterprise_path.write_text(
        text,
        encoding="utf-8",
        newline="\n",
    )

    # Scan all Python files to prevent the same invalid Streamlit value.
    modified_files = []
    for path in root.glob("*.py"):
        source = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )
        if INVALID in source:
            source = source.replace(INVALID, VALID)
            path.write_text(
                source,
                encoding="utf-8",
                newline="\n",
            )
            modified_files.append(path.name)

    version_path.write_text(
        VERSION + "\n",
        encoding="utf-8",
    )

    changelog_src = (
        root / "payload" / "CHANGELOG_v7.4.2.md"
    )
    if changelog_src.exists():
        shutil.copy2(
            changelog_src,
            root / "CHANGELOG_v7.4.2.md",
        )

    # Validate the actual app import chain files.
    for name in [
        "enterprise_center.py",
        "enterprise_customer_management.py",
        "employee_status.py",
        "app.py",
    ]:
        path = root / name
        if path.exists():
            py_compile.compile(
                str(path),
                doraise=True,
            )

    final_text = enterprise_path.read_text(
        encoding="utf-8"
    )
    if INVALID in final_text:
        fail(
            "Invalid vertical_alignment value remains "
            "in enterprise_center.py."
        )

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=NO")
    print(
        "RESULT=Invalid Streamlit vertical alignment "
        "changed from end to bottom."
    )
    if modified_files:
        print(
            "ADDITIONAL_FILES="
            + ",".join(modified_files)
        )
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
