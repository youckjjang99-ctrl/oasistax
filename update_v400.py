from pathlib import Path
from datetime import datetime
import json
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
FILES = (
    "app.py",
    "cloud_db.py",
    "cloud_migration.py",
    "cloud_admin.py",
    "supabase_schema.sql",
    "secrets_supabase_example.toml",
    "VERSION.txt",
    "CHANGELOG_v4.0.0.md",
)

def pause():
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass

def main():
    print("=" * 66)
    print(" OASIS v4.0.0 SUPABASE FOUNDATION UPDATE")
    print("=" * 66)

    if not (ROOT / "app.py").exists():
        print("[ERROR] Extract this patch into the project root folder.")
        pause()
        return 1

    backup = ROOT / "_oasis_backups" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v400"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in FILES:
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    print("[SUCCESS] v4.0.0 update completed.")
    print("Next: streamlit run app.py")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
