from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v7.0.1"
TARGETS = [
    "articles_review.py",
    "document_preprocessor.py",
    "VERSION.txt",
]


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def main() -> None:
    root = Path.cwd()
    if not (root / "document_preprocessor.py").exists():
        fail("Run this patch from the OASIS project root folder.")

    version_path = root / "VERSION.txt"
    current = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists()
        else ""
    )
    if current and current not in {
        "v7.0.0", "7.0.0", "v7.0.1", "7.0.1"
    }:
        fail(f"Expected v7.0.0 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v7.0.1_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)

    for name in TARGETS:
        src = root / name
        if src.exists():
            shutil.copy2(src, backup / name)

    for relative in [
        "articles_review.py",
        "document_preprocessor.py",
    ]:
        src = root / "payload" / relative
        if not src.exists():
            fail(f"payload/{relative} is missing.")
        shutil.copy2(src, root / relative)

    version_path.write_text(VERSION + "\n", encoding="utf-8")

    changelog_src = root / "payload" / "CHANGELOG_v7.0.1.md"
    if changelog_src.exists():
        shutil.copy2(
            changelog_src,
            root / "CHANGELOG_v7.0.1.md",
        )

    for name in [
        "articles_review.py",
        "document_preprocessor.py",
    ]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=NO")
    print(
        "RESULT=Korean-first multi-mode OCR and "
        "English artifact rejection enabled."
    )
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
