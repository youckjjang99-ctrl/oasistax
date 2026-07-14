from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

VERSION = "v6.3.1"
TARGET_FILES = ["consultation_journal.py", "VERSION.txt", "CHANGELOG_v6.3.1.md"]


def project_root() -> Path:
    return Path.cwd()


def validate_root(root: Path) -> None:
    required = [root / "app.py", root / "consultation_journal.py"]
    missing = [str(p.name) for p in required if not p.exists()]
    if missing:
        raise RuntimeError("프로젝트 루트가 아닙니다. 누락: " + ", ".join(missing))


def main() -> int:
    root = project_root()
    package_dir = Path(__file__).resolve().parent
    payload = package_dir / "payload"
    validate_root(root)

    backup_dir = root / "_oasis_backups" / f"before_{VERSION}_{datetime.now():%Y%m%d_%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for name in TARGET_FILES:
        target = root / name
        source = payload / name
        if target.exists():
            shutil.copy2(target, backup_dir / name)
        if source.exists():
            shutil.copy2(source, target)

    print(f"[{VERSION}] 업데이트 성공")
    print(f"백업 위치: {backup_dir}")
    print("다음으로 supabase_v631_upgrade.sql을 Supabase SQL Editor에서 실행하세요.")
    print('Git: git add . && git commit -m "v6.3.1 녹음파일 클라우드 캐시 복원 및 중복분석 방지" && git push origin main')
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"업데이트 실패: {exc}")
        raise SystemExit(1)
