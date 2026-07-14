from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

VERSION = "v6.3.3"
TARGET_FILES = [
    "consultation_audio_storage.py",
    "consultation_journal.py",
    "VERSION.txt",
    "CHANGELOG_v6.3.3.md",
]
REQUIRED_ROOT_FILES = [
    "app.py",
    "consultation_audio_storage.py",
    "consultation_journal.py",
    "enterprise_center.py",
]


def main() -> int:
    root = Path(__file__).resolve().parent
    payload = root / "payload"

    missing_root = [name for name in REQUIRED_ROOT_FILES if not (root / name).exists()]
    if missing_root:
        raise RuntimeError("PROJECT_ROOT_CHECK_FAILED: " + ", ".join(missing_root))

    missing_payload = [name for name in TARGET_FILES if not (payload / name).exists()]
    if missing_payload:
        raise RuntimeError("PAYLOAD_MISSING: " + ", ".join(missing_payload))

    backup = root / "_oasis_backups" / f"before_{VERSION}_{datetime.now():%Y%m%d_%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)

    for name in TARGET_FILES:
        source = payload / name
        target = root / name
        if target.exists():
            shutil.copy2(target, backup / name)
        shutil.copy2(source, target)

    for name in ("consultation_audio_storage.py", "consultation_journal.py"):
        py_compile.compile(str(root / name), doraise=True)

    actual_version = (root / "VERSION.txt").read_text(encoding="utf-8-sig").strip()
    if actual_version != VERSION:
        raise RuntimeError(f"VERSION_CHECK_FAILED: {actual_version}")

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("NEXT_STEP=RUN_SUPABASE_SQL_AND_GIT_COMMANDS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("UPDATE_FAILED")
        print(str(exc))
        raise SystemExit(1)
