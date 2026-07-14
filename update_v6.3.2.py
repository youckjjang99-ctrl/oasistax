from __future__ import annotations

import py_compile
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

VERSION = "v6.3.2"
TARGET_FILES = [
    "consultation_journal.py",
    "enterprise_center.py",
    "VERSION.txt",
    "CHANGELOG_v6.3.2.md",
]
REQUIRED_ROOT_FILES = ["app.py", "consultation_journal.py", "enterprise_center.py"]


def run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, encoding="utf-8", errors="replace")
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()


def find_python() -> list[str]:
    for candidate in (["py", "-3"], ["python"], ["python3"]):
        try:
            code, _ = run(candidate + ["--version"], Path.cwd())
            if code == 0:
                return candidate
        except OSError:
            pass
    raise RuntimeError("Python was not found.")


def validate_root(root: Path) -> None:
    missing = [name for name in REQUIRED_ROOT_FILES if not (root / name).exists()]
    if missing:
        raise RuntimeError("Project root check failed. Missing: " + ", ".join(missing))


def main() -> int:
    package_dir = Path(__file__).resolve().parent
    root = package_dir
    payload = package_dir / "payload"
    validate_root(root)

    missing_payload = [name for name in TARGET_FILES if not (payload / name).exists()]
    if missing_payload:
        raise RuntimeError("Payload missing: " + ", ".join(missing_payload))

    backup_dir = root / "_oasis_backups" / f"before_{VERSION}_{datetime.now():%Y%m%d_%H%M%S}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for name in TARGET_FILES:
        source = payload / name
        target = root / name
        if target.exists():
            shutil.copy2(target, backup_dir / name)
        shutil.copy2(source, target)

    python_cmd = find_python()
    for name in ("consultation_journal.py", "enterprise_center.py"):
        py_compile.compile(str(root / name), doraise=True)

    if (root / "VERSION.txt").read_text(encoding="utf-8-sig").strip() != VERSION:
        raise RuntimeError("VERSION.txt verification failed.")

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup_dir}")

    git_dir = root / ".git"
    if git_dir.exists() and shutil.which("git"):
        code, output = run(["git", "add", "."], root)
        if code != 0:
            print("GIT_ADD_FAILED")
            print(output)
            return 0

        commit_message = "v6.3.2 상담일지 조회 및 정책자금 자동연동 통합복구"
        code, output = run(["git", "commit", "-m", commit_message], root)
        if code != 0 and "nothing to commit" not in output.lower():
            print("GIT_COMMIT_FAILED")
            print(output)
            return 0

        code, output = run(["git", "push", "origin", "main"], root)
        if code == 0:
            print("GIT_PUSH_OK")
        else:
            print("GIT_PUSH_SKIPPED_OR_FAILED")
            print(output)
    else:
        print("GIT_NOT_AVAILABLE_OR_NOT_A_REPOSITORY")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("UPDATE_FAILED")
        print(str(exc))
        raise SystemExit(1)
