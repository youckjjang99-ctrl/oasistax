from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"

def pause():
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass

def main():
    print("=" * 60)
    print(" OASIS v4.0.1 CLOUD MENU HOTFIX")
    print("=" * 60)

    if not (ROOT / "app.py").exists():
        print("[ERROR] Extract this patch into the project root folder.")
        pause()
        return 1

    backup = ROOT / "_oasis_backups" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v401"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in ("app.py", "VERSION.txt"):
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

    for name in ("app.py", "VERSION.txt", "CHANGELOG_v4.0.1.md"):
        shutil.copy2(PAYLOAD / name, ROOT / name)

    print("[SUCCESS] v4.0.1 update completed.")
    print("Next: streamlit run app.py")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
