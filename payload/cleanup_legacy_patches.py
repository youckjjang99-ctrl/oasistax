from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PROTECTED_FILES = {
    "RUN_UPDATE.cmd",
    "RUN_PRECHECK.cmd",
    "update_engine.py",
    "system_precheck.py",
    "update_safety.py",
    "VERSION.txt",
    "requirements.txt",
}

PATTERNS = [
    re.compile(r"^RUN_V\d.*_UPDATE\.(cmd|bat)$", re.I),
    re.compile(r"^update_v\d.*\.py$", re.I),
    re.compile(r"^README_v\d.*적용방법\.md$", re.I),
    re.compile(r"^CHANGELOG_v\d.*\.md$", re.I),
]

EXACT_FILES = {
    "data/system_precheck_report.json",
}


def find_targets() -> list[Path]:
    targets: list[Path] = []

    for path in ROOT.iterdir():
        if not path.is_file():
            continue
        if path.name in PROTECTED_FILES:
            continue
        if any(pattern.match(path.name) for pattern in PATTERNS):
            targets.append(path)

    for relative in EXACT_FILES:
        path = ROOT / relative
        if path.exists() and path.is_file():
            targets.append(path)

    # payload 안의 과거 패치 제작자료만 정리
    payload = ROOT / "payload"
    if payload.exists():
        for path in payload.iterdir():
            if not path.is_file():
                continue
            if (
                path.name.startswith("CHANGELOG_v")
                or path.name in {
                    "PATCH_MANIFEST.json",
                    "VERSION.txt",
                    "system_precheck.py",
                    "update_safety.py",
                }
            ):
                targets.append(path)

    unique = {}
    for path in targets:
        unique[str(path.resolve())] = path
    return sorted(
        unique.values(),
        key=lambda item: str(item.relative_to(ROOT)),
    )


def main() -> None:
    targets = find_targets()

    print("================================================")
    print("OASIS LEGACY PATCH CLEANUP")
    print("================================================")

    if not targets:
        print("정리할 과거 패치파일이 없습니다.")
        input("Press Enter to close...")
        return

    print("다음 파일을 실행 폴더에서 정리합니다.")
    print("파일은 영구삭제하지 않고 보관 폴더로 이동합니다.")
    print("------------------------------------------------")
    for path in targets:
        print(path.relative_to(ROOT))
    print("------------------------------------------------")

    answer = input(
        "계속하려면 YES를 입력하세요: "
    ).strip().upper()
    if answer != "YES":
        print("CLEANUP_CANCELLED")
        input("Press Enter to close...")
        return

    archive = (
        ROOT
        / "_legacy_patch_archive"
        / datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    archive.mkdir(
        parents=True,
        exist_ok=False,
    )

    moved = []
    for path in targets:
        relative = path.relative_to(ROOT)
        destination = archive / relative
        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        shutil.move(str(path), str(destination))
        moved.append(str(relative))

    print("CLEANUP_OK")
    print(f"ARCHIVE={archive}")
    print(f"MOVED={len(moved)}")
    print(
        "GitHub에 반영하려면 git add . 후 commit/push 하세요."
    )
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
