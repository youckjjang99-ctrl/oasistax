from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"

FILES = (
    "enterprise_center.py",
    "consultation_journal.py",
    "VERSION.txt",
    "CHANGELOG_v5.2.0.md",
)

def pause():
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass

def ensure_ffmpeg_package():
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
    packages_path.write_text(
        "\n".join(existing) + "\n",
        encoding="utf-8",
    )

def main():
    print("=" * 70)
    print(" OASIS v5.2.0 AUDIO CONSULTATION JOURNAL UPDATE")
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
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v520"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in FILES + ("packages.txt",):
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    ensure_ffmpeg_package()

    print("[SUCCESS] v5.2.0 update completed.")
    print("Set OPENAI_API_KEY in Streamlit Secrets.")
    print("Then reboot Streamlit Cloud.")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
