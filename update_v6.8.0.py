from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.8.0"
TARGETS = [
    "articles_review.py",
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
        version_path.read_text(
            encoding="utf-8-sig"
        ).strip()
        if version_path.exists()
        else ""
    )
    if current and current not in {
        "v6.7.0",
        "6.7.0",
        "v6.8.0",
        "6.8.0",
    }:
        fail(f"Expected v6.7.0 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v6.8.0_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)

    for name in TARGETS:
        src = root / name
        if src.exists():
            dst = backup / name
            dst.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            shutil.copy2(src, dst)

    payload_file = (
        root / "payload" / "articles_review.py"
    )
    if not payload_file.exists():
        fail("payload/articles_review.py is missing.")
    shutil.copy2(
        payload_file,
        root / "articles_review.py",
    )

    requirements = root / "requirements.txt"
    req_text = requirements.read_text(
        encoding="utf-8"
    )
    additions = []
    if "PyMuPDF" not in req_text:
        additions.append("PyMuPDF==1.25.3")
    if "pytesseract" not in req_text:
        additions.append("pytesseract==0.3.13")
    if additions:
        requirements.write_text(
            req_text.rstrip()
            + "\n"
            + "\n".join(additions)
            + "\n",
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

    version_path.write_text(
        VERSION + "\n",
        encoding="utf-8",
    )

    changelog_src = (
        root
        / "payload"
        / "CHANGELOG_v6.8.0.md"
    )
    if changelog_src.exists():
        shutil.copy2(
            changelog_src,
            root / "CHANGELOG_v6.8.0.md",
        )

    py_compile.compile(
        str(root / "articles_review.py"),
        doraise=True,
    )

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=NO")
    print(
        "STREAMLIT_PACKAGES="
        "tesseract-ocr,tesseract-ocr-kor"
    )
    print(
        "RESULT=Automatic Korean OCR and "
        "detailed articles review enabled."
    )
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
