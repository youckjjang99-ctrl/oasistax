from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"

FILES = (
    "app.py",
    "multi_source_policy.py",
    "VERSION.txt",
    "CHANGELOG_v6.0.0.md",
)

def pause():
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass

def main():
    print("=" * 76)
    print(" OASIS v6.0.0 MULTI SOURCE POLICY MATCH UPDATE")
    print("=" * 76)

    if not (ROOT / "app.py").exists():
        print("[ERROR] Extract this patch into the project root folder.")
        pause()
        return 1

    missing = [
        name for name in FILES
        if not (PAYLOAD / name).exists()
    ]
    if missing:
        print("[ERROR] Missing patch files:", ", ".join(missing))
        pause()
        return 1

    backup = ROOT / "_oasis_backups" / (
        datetime.now().strftime("%Y%m%d_%H%M%S")
        + "_before_v600"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in FILES:
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    print("[SUCCESS] v6.0.0 update completed.")
    print("Configure K-Startup/KOSMES Secrets if API keys are available.")
    print("Then git push and reboot Streamlit Cloud.")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
