from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v7.0.0"
TARGETS = [
    "articles_review.py",
    "document_preprocessor.py",
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
        "v6.9.0", "6.9.0", "v7.0.0", "7.0.0"
    }:
        fail(f"Expected v6.9.0 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v7.0.0_"
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
        "document_preprocessor.py",
    ]:
        src = root / "payload" / relative
        if not src.exists():
            fail(f"payload/{relative} is missing.")
        shutil.copy2(src, root / relative)

    requirements = root / "requirements.txt"
    req_text = requirements.read_text(encoding="utf-8")
    if "opencv-python-headless" not in req_text.lower():
        requirements.write_text(
            req_text.rstrip()
            + "\nopencv-python-headless==4.10.0.84\n",
            encoding="utf-8",
        )

    packages = root / "packages.txt"
    package_lines = []
    if packages.exists():
        package_lines = [
            line.strip()
            for line in packages.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    for package in [
        "tesseract-ocr",
        "tesseract-ocr-kor",
    ]:
        if package not in package_lines:
            package_lines.append(package)
    packages.write_text(
        "\n".join(package_lines) + "\n",
        encoding="utf-8",
    )

    version_path.write_text(VERSION + "\n", encoding="utf-8")

    changelog_src = root / "payload" / "CHANGELOG_v7.0.0.md"
    if changelog_src.exists():
        shutil.copy2(
            changelog_src,
            root / "CHANGELOG_v7.0.0.md",
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
        "RESULT=Automatic document rotation, deskew, "
        "OCR quality validation, and Korean structure restoration enabled."
    )
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
