from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"

FILES = (
    "app.py",
    "stock_valuation.py",
    "matching_preferences.py",
    "cloud_db.py",
    "cloud_sync.py",
    "supabase_v420_upgrade.sql",
    "VERSION.txt",
    "CHANGELOG_v4.2.0.md",
)

def pause():
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass

def main():
    print("=" * 66)
    print(" OASIS v4.2.0 DUAL STORAGE UPDATE")
    print("=" * 66)

    if not (ROOT / "app.py").exists():
        print("[ERROR] Extract this patch into the project root folder.")
        pause()
        return 1

    missing = [name for name in FILES if not (PAYLOAD / name).exists()]
    if missing:
        print("[ERROR] Missing patch files:", ", ".join(missing))
        pause()
        return 1

    backup = ROOT / "_oasis_backups" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v420"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in FILES:
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    print("[SUCCESS] v4.2.0 update completed.")
    print("Run supabase_v420_upgrade.sql in Supabase SQL Editor.")
    print("Then: streamlit run app.py")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
