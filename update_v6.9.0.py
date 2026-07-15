from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.9.0"
TARGETS = [
    "articles_review.py",
    "articles_editor.py",
    "articles_pdf.py",
    "data/articles_amendment_templates.json",
    "requirements.txt",
    "packages.txt",
    "VERSION.txt",
]


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def main() -> None:
    root = Path.cwd()
    if not (root / "articles_review.py").exists():
        fail("Run this patch from the OASIS project root folder.")

    version_path = root / "VERSION.txt"
    current = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists()
        else ""
    )
    if current and current not in {
        "v6.8.0",
        "6.8.0",
        "v6.9.0",
        "6.9.0",
    }:
        fail(f"Expected v6.8.0 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v6.9.0_"
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
        "articles_review.py",
        "articles_editor.py",
        "articles_pdf.py",
        "data/articles_amendment_templates.json",
    ]:
        src = root / "payload" / relative
        if not src.exists():
            fail(f"payload/{relative} is missing.")
        dst = root / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    requirements = root / "requirements.txt"
    req_text = requirements.read_text(encoding="utf-8")
    if "reportlab" not in req_text.lower():
        requirements.write_text(
            req_text.rstrip() + "\nreportlab==4.2.5\n",
            encoding="utf-8",
        )

    packages = root / "packages.txt"
    package_lines = []
    if packages.exists():
        package_lines = [
            line.strip()
            for line in packages.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
    if "fonts-nanum" not in package_lines:
        package_lines.append("fonts-nanum")
    packages.write_text(
        "\n".join(package_lines) + "\n",
        encoding="utf-8",
    )

    version_path.write_text(VERSION + "\n", encoding="utf-8")

    changelog_src = root / "payload" / "CHANGELOG_v6.9.0.md"
    if changelog_src.exists():
        shutil.copy2(
            changelog_src,
            root / "CHANGELOG_v6.9.0.md",
        )

    for name in [
        "articles_review.py",
        "articles_editor.py",
        "articles_pdf.py",
    ]:
        py_compile.compile(str(root / name), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=supabase_v690_upgrade.sql")
    print(
        "RESULT=Articles amendment editor, versioning, "
        "and Korean PDF export enabled."
    )
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
