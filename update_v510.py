from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"

FILES = (
    "app.py",
    "enterprise_center.py",
    "consulting_report.py",
    "VERSION.txt",
    "CHANGELOG_v5.1.0.md",
)

def pause():
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass

def main():
    print("=" * 70)
    print(" OASIS v5.1.0 ENTERPRISE UI AND AI REPORT UPDATE")
    print("=" * 70)

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
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v510"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in FILES:
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    print("[SUCCESS] v5.1.0 update completed.")
    print("Next: streamlit run app.py")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
