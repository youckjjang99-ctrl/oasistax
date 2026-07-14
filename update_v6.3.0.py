from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

VERSION = "v6.3.0"
REQUIRED_ROOT_FILES = ["app.py", "enterprise_center.py", "consultation_journal.py"]
PAYLOAD_FILES = ["consultation_journal.py", "enterprise_center.py", "VERSION.txt", "CHANGELOG_v6.3.0.md"]


def fail(message: str) -> None:
    print(f"[실패] {message}")
    input("엔터키를 누르면 종료합니다...")
    raise SystemExit(1)


def main() -> None:
    package_dir = Path(__file__).resolve().parent
    project_root = package_dir
    payload_dir = package_dir / "payload"

    missing_root = [name for name in REQUIRED_ROOT_FILES if not (project_root / name).exists()]
    if missing_root:
        fail(
            "현재 폴더가 정책자금자동화 프로젝트 루트가 아닙니다. "
            f"누락 파일: {', '.join(missing_root)}"
        )

    missing_payload = [name for name in PAYLOAD_FILES if not (payload_dir / name).exists()]
    if missing_payload:
        fail(f"업데이트 payload 파일이 누락되었습니다: {', '.join(missing_payload)}")

    current_version = "확인불가"
    version_path = project_root / "VERSION.txt"
    if version_path.exists():
        current_version = version_path.read_text(encoding="utf-8-sig").strip()
    elif (project_root / "payload" / "VERSION.txt").exists():
        current_version = (project_root / "payload" / "VERSION.txt").read_text(encoding="utf-8-sig").strip()

    print(f"현재 버전: {current_version}")
    print(f"적용 버전: {VERSION}")

    backup_dir = project_root / "_oasis_backups" / f"{datetime.now():%Y%m%d_%H%M%S}_before_{VERSION.replace('.', '')}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    try:
        for name in ["consultation_journal.py", "enterprise_center.py", "VERSION.txt"]:
            target = project_root / name
            if target.exists():
                shutil.copy2(target, backup_dir / name)

        for name in PAYLOAD_FILES:
            source = payload_dir / name
            target = project_root / name
            shutil.copy2(source, target)

        sql_source = package_dir / "supabase_v630_upgrade.sql"
        sql_target = project_root / sql_source.name
        if sql_source.exists() and sql_source.resolve() != sql_target.resolve():
            shutil.copy2(sql_source, sql_target)

        print("\n[성공] OASIS 정책자금자동화 v6.3.0 업데이트가 완료되었습니다.")
        print(f"백업 폴더: {backup_dir}")
        print("\n[중요] Supabase SQL Editor에서 supabase_v630_upgrade.sql을 1회 실행하세요.")
        print("SQL 실행 전에도 로컬 상담일지는 유지되지만, 클라우드 영구 저장은 SQL 실행 후 활성화됩니다.")
        print("\nGit 반영 명령어:")
        print("  git add consultation_journal.py enterprise_center.py VERSION.txt CHANGELOG_v6.3.0.md supabase_v630_upgrade.sql")
        print('  git commit -m "feat: v6.3.0 상담일지 영구조회 및 정책자금 자동연동"')
        print("  git push")
    except Exception as exc:
        fail(f"파일 적용 중 오류가 발생했습니다: {exc}")

    input("\n엔터키를 누르면 종료합니다...")


if __name__ == "__main__":
    main()
