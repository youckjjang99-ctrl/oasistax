from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP = ROOT / "_oasis_backups" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_before_v351"

FILES = (
    "bizinfo_cache.py",
    "collector.py",
    "main.py",
    "maintenance.py",
    "VERSION.txt",
    "CHANGELOG_v3.5.1.md",
)


def main() -> int:
    print("=" * 58)
    print(" OASIS v3.5.1 BIZINFO MODULE HOTFIX")
    print("=" * 58)

    if not (ROOT / "app.py").exists():
        print("[ERROR] This file must be inside the project root folder.")
        input("\nPress Enter to close.")
        return 1

    BACKUP.mkdir(parents=True, exist_ok=False)

    # The update ZIP is extracted directly into the project root.
    # Back up current files before confirming the fix.
    for name in ("main.py", "maintenance.py", "VERSION.txt", "bizinfo_cache.py", "collector.py"):
        path = ROOT / name
        if path.exists():
            shutil.copy2(path, BACKUP / name)

    missing = [name for name in FILES if not (ROOT / name).exists()]
    workflow = ROOT / ".github" / "workflows" / "update_bizinfo_db.yml"
    if not workflow.exists():
        missing.append(str(workflow.relative_to(ROOT)))

    if missing:
        print("[ERROR] Missing update files:")
        for name in missing:
            print(f" - {name}")
        input("\nPress Enter to close.")
        return 1

    print("[SUCCESS] Required Bizinfo modules are now in the project root.")
    print("Version: v3.5.1")
    print("Next command: streamlit run app.py")
    input("\nPress Enter to close.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
