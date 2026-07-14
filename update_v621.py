from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
FILES = (
    "consultation_journal.py",
    "consultation_audio_storage.py",
    "VERSION.txt",
    "CHANGELOG_v6.2.1.md",
)

def pause():
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass

def main():
    print("=" * 76)
    print(" OASIS v6.2.1 AUDIO ERROR DISPLAY HOTFIX")
    print("=" * 76)

    if not (ROOT / "app.py").exists():
        print("[ERROR] Extract this patch into the project root folder.")
        pause()
        return 1

    backup = ROOT / "_oasis_backups" / (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_before_v621"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in FILES:
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    print("[SUCCESS] v6.2.1 update completed.")
    print("Next: git push and reboot Streamlit Cloud.")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
