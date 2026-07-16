from __future__ import annotations

import json
import py_compile
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent
PACKAGE_DIR = PROJECT_ROOT / "update_package"
MANIFEST_PATH = PACKAGE_DIR / "update_manifest.json"


def _fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception as exc:
        _fail(f"업데이트 설정파일을 읽지 못했습니다: {exc}")
        return {}


def _current_version() -> str:
    path = PROJECT_ROOT / "VERSION.txt"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8-sig").strip()


def _create_backup(
    version: str,
    files: list[str],
) -> Path:
    backup = (
        PROJECT_ROOT
        / "_oasis_backups"
        / (
            f"before_{version}_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
    )
    backup.mkdir(parents=True, exist_ok=False)

    for relative in files:
        source = PROJECT_ROOT / relative
        if not source.exists():
            continue
        destination = backup / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(
                source,
                destination,
                dirs_exist_ok=True,
            )
        else:
            shutil.copy2(source, destination)

    return backup


def _rollback(
    backup: Path,
    files: list[str],
) -> None:
    for relative in files:
        backup_source = backup / relative
        destination = PROJECT_ROOT / relative

        if backup_source.exists():
            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            if backup_source.is_dir():
                shutil.copytree(
                    backup_source,
                    destination,
                    dirs_exist_ok=True,
                )
            else:
                shutil.copy2(
                    backup_source,
                    destination,
                )
        elif destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()


def _validate_python(files: list[str]) -> None:
    for relative in files:
        path = PROJECT_ROOT / relative
        if (
            path.exists()
            and path.is_file()
            and path.suffix.lower() == ".py"
        ):
            py_compile.compile(
                str(path),
                doraise=True,
            )


def _run_system_precheck() -> None:
    precheck_path = PROJECT_ROOT / "system_precheck.py"
    if not precheck_path.exists():
        return

    sys.path.insert(0, str(PROJECT_ROOT))
    from system_precheck import run_precheck

    report = run_precheck(
        PROJECT_ROOT,
        save_report=True,
    )
    if report.get("status") != "PASS":
        errors = [
            item
            for item in report.get("checks", [])
            if not item.get("ok")
            and item.get("level") == "error"
        ]
        summary = "; ".join(
            f"{item.get('item')}: {item.get('message')}"
            for item in errors[:10]
        )
        raise RuntimeError(
            "배포 사전점검 실패: " + summary
        )


def main() -> None:
    if not MANIFEST_PATH.exists():
        _fail(
            "update_package/update_manifest.json이 없습니다.\n"
            "새 업데이트 ZIP의 update_package 폴더를 "
            "프로젝트 폴더에 압축 해제한 뒤 다시 실행해주세요."
        )

    manifest = _read_json(MANIFEST_PATH)
    target_version = str(
        manifest.get("version", "")
    ).strip()
    allowed_versions = {
        str(value).strip()
        for value in manifest.get(
            "allowed_from_versions",
            [],
        )
    }
    files = [
        str(value).replace("\\", "/").strip("/")
        for value in manifest.get("files", [])
        if str(value).strip()
    ]

    if not target_version or not files:
        _fail(
            "업데이트 설정에 version 또는 files가 없습니다."
        )

    current = _current_version()
    if (
        allowed_versions
        and current not in allowed_versions
        and current != target_version
    ):
        _fail(
            f"현재 버전 {current or '확인불가'}에서는 "
            f"{target_version} 업데이트를 적용할 수 없습니다.\n"
            f"허용 버전: {', '.join(sorted(allowed_versions))}"
        )

    backup = _create_backup(
        target_version,
        files + ["VERSION.txt"],
    )

    try:
        for relative in files:
            source = PACKAGE_DIR / "files" / relative
            if not source.exists():
                raise FileNotFoundError(
                    f"업데이트 파일 누락: {relative}"
                )

            destination = PROJECT_ROOT / relative
            destination.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            if source.is_dir():
                shutil.copytree(
                    source,
                    destination,
                    dirs_exist_ok=True,
                )
            else:
                shutil.copy2(
                    source,
                    destination,
                )

        (PROJECT_ROOT / "VERSION.txt").write_text(
            target_version + "\n",
            encoding="utf-8",
        )

        _validate_python(files)
        _run_system_precheck()

    except Exception as exc:
        _rollback(
            backup,
            files + ["VERSION.txt"],
        )
        print("UPDATE_ROLLED_BACK")
        print(f"BACKUP={backup}")
        _fail(
            f"{type(exc).__name__}: {exc}"
        )

    archive = (
        PROJECT_ROOT
        / "_applied_update_packages"
        / (
            target_version.replace(".", "_")
            + "_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
    )
    archive.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    shutil.move(str(PACKAGE_DIR), str(archive))

    print("UPDATE_OK")
    print(f"VERSION={target_version}")
    print(f"BACKUP={backup}")
    print(f"PACKAGE_ARCHIVED={archive}")
    print("PRECHECK=PASS")
    print("AUTO_ROLLBACK=ENABLED")
    print("SQL_REQUIRED=" + str(
        manifest.get("sql_required", "NO")
    ))
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
