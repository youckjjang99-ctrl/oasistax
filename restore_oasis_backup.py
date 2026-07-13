from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP_ROOT = ROOT / "_oasis_backups"


def copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def main() -> int:
    print("=" * 52)
    print(" OASIS 백업 복원")
    print("=" * 52)

    if not BACKUP_ROOT.exists():
        print("백업 폴더가 없습니다.")
        input("\n엔터를 누르면 종료됩니다.")
        return 1

    backups = sorted(
        [path for path in BACKUP_ROOT.iterdir() if path.is_dir()],
        key=lambda path: path.name,
        reverse=True,
    )
    if not backups:
        print("복원 가능한 백업이 없습니다.")
        input("\n엔터를 누르면 종료됩니다.")
        return 1

    for index, backup in enumerate(backups, start=1):
        version = ""
        manifest_path = backup / "backup_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                version = manifest.get("target_version") or manifest.get("version") or ""
            except Exception:
                pass
        print(f"{index}. {backup.name} {version}")

    choice = input("\n복원할 백업 번호를 입력하세요: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(backups)):
        print("올바른 번호가 아닙니다.")
        input("\n엔터를 누르면 종료됩니다.")
        return 1

    selected = backups[int(choice) - 1]
    confirm = input(f"{selected.name} 백업으로 복원할까요? (YES 입력): ").strip()
    if confirm != "YES":
        print("복원을 취소했습니다.")
        input("\n엔터를 누르면 종료됩니다.")
        return 0

    restored = 0
    for item in selected.iterdir():
        if item.name == "backup_manifest.json":
            continue
        copy_path(item, ROOT / item.name)
        restored += 1

    print(f"\n복원 완료: {restored}개 항목")
    print("Streamlit 앱을 다시 실행해주세요.")
    input("\n엔터를 누르면 종료됩니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
