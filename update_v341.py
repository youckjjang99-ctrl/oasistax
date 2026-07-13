from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PAYLOAD_DIR = PACKAGE_ROOT / "payload"
TARGET_ROOT = PACKAGE_ROOT
TARGET_VERSION = "v3.4.1"
BACKUP_ROOT_NAME = "_oasis_backups"

REQUIRED_PROJECT_FILES = (
    "app.py",
    "auth.py",
    "crm.py",
    "history.py",
    "main.py",
    "ui.py",
    "utils.py",
    "requirements.txt",
)

PAYLOAD_FILES = (
    "app.py",
    "maintenance.py",
    "VERSION.txt",
    ".gitignore",
    "CHANGELOG_v3.4.1.md",
)


def pause() -> None:
    try:
        input("\n엔터를 누르면 창이 닫힙니다.")
    except EOFError:
        pass


def copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def validate() -> None:
    missing_project = [
        name for name in REQUIRED_PROJECT_FILES
        if not (TARGET_ROOT / name).exists()
    ]
    if missing_project:
        raise RuntimeError(
            "업데이트 파일을 정책자금자동화 프로젝트 최상위 폴더에 풀어주세요.\n"
            f"찾지 못한 파일: {', '.join(missing_project)}"
        )

    missing_payload = [
        name for name in PAYLOAD_FILES
        if not (PAYLOAD_DIR / name).exists()
    ]
    if missing_payload:
        raise RuntimeError(
            f"업데이트 구성파일이 누락되었습니다: {', '.join(missing_payload)}"
        )


def create_backup() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = TARGET_ROOT / BACKUP_ROOT_NAME / f"{timestamp}_before_v341"
    backup_dir.mkdir(parents=True, exist_ok=False)

    names = set(REQUIRED_PROJECT_FILES) | {
        "VERSION.txt",
        ".gitignore",
        "maintenance.py",
        "update_history.json",
        "data",
        "templates",
        "user_data",
    }
    for name in names:
        source = TARGET_ROOT / name
        if source.exists():
            copy_path(source, backup_dir / name)

    manifest = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "target_version": TARGET_VERSION,
        "backup_name": backup_dir.name,
        "files": sorted(
            str(path.relative_to(backup_dir))
            for path in backup_dir.rglob("*")
            if path.is_file()
        ),
    }
    (backup_dir / "backup_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return backup_dir


def apply_payload() -> None:
    for name in PAYLOAD_FILES:
        source = PAYLOAD_DIR / name
        destination = TARGET_ROOT / name
        copy_path(source, destination)


def update_history(backup_dir: Path) -> None:
    history_path = TARGET_ROOT / "update_history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
        if not isinstance(history, list):
            history = []
    except (OSError, json.JSONDecodeError):
        history = []

    history.insert(0, {
        "버전": TARGET_VERSION,
        "적용일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "업데이트": "원클릭 업데이트·자동 백업·복원·시스템 관리",
        "백업폴더": backup_dir.name,
    })
    history_path.write_text(
        json.dumps(history[:100], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    print("=" * 58)
    print(" OASIS 정책자금자동화 v3.4.1 업데이트")
    print("=" * 58)
    print(f"프로젝트 폴더: {TARGET_ROOT}")

    try:
        validate()
        print("[1/4] 프로젝트 검사 완료")

        backup_dir = create_backup()
        print(f"[2/4] 자동 백업 완료: {backup_dir.name}")

        apply_payload()
        print("[3/4] 업데이트 파일 적용 완료")

        update_history(backup_dir)
        print("[4/4] 버전 및 업데이트 기록 저장 완료")

        print("\n업데이트가 완료되었습니다.")
        print("현재 버전: v3.4.1")
        print("다음 실행: streamlit run app.py")
        print("GitHub 반영은 VS Code 소스컨트롤에서 Commit/Push 하시면 됩니다.")
        pause()
        return 0

    except Exception as exc:
        print(f"\n업데이트 실패: {type(exc).__name__}: {exc}")
        print("기존 프로젝트 파일은 가능한 범위에서 보존되었습니다.")
        pause()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
