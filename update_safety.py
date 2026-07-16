from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable


def create_update_backup(project_root: Path, version: str, target_files: Iterable[str]) -> Path:
    backup_dir = (
        project_root / "_oasis_backups"
        / f"before_{version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)

    copied = []
    for relative in target_files:
        source = project_root / relative
        if not source.exists():
            continue
        destination = backup_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
        copied.append(relative)

    (backup_dir / "update_backup_manifest.json").write_text(
        json.dumps(
            {
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "version": version,
                "files": copied,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return backup_dir


def rollback_update(project_root: Path, backup_dir: Path, target_files: Iterable[str]) -> list[str]:
    restored = []
    for relative in target_files:
        backup_source = backup_dir / relative
        destination = project_root / relative

        if backup_source.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            if backup_source.is_dir():
                shutil.copytree(backup_source, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(backup_source, destination)
            restored.append(relative)
        elif destination.exists():
            if destination.is_dir():
                shutil.rmtree(destination)
            else:
                destination.unlink()
            restored.append(f"{relative} (신규파일 제거)")
    return restored
