from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
FILES = (
    "consultation_journal.py",
    "VERSION.txt",
    "CHANGELOG_v5.3.0.md",
)

def pause():
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass

def main():
    print("=" * 72)
    print(" OASIS v5.3.0 NOISE REDUCTION AND CONSULTING TOPICS UPDATE")
    print("=" * 72)

    if not (ROOT / "app.py").exists():
        print("[ERROR] Extract this patch into the project root folder.")
        pause()
        return 1

    backup = ROOT / "_oasis_backups" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v530"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in FILES:
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    packages_path = ROOT / "packages.txt"
    existing = []
    if packages_path.exists():
        existing = [
            line.strip()
            for line in packages_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    if "ffmpeg" not in existing:
        existing.append("ffmpeg")
    packages_path.write_text("\n".join(existing) + "\n", encoding="utf-8")

    print("[SUCCESS] v5.3.0 update completed.")
    print("Reboot Streamlit Cloud after git push.")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
