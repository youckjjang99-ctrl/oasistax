from pathlib import Path
from datetime import datetime
import json
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
FILES = (
    "app.py",
    "stock_valuation.py",
    "VERSION.txt",
    "CHANGELOG_v3.8.1.md",
)

def pause():
    try:
        input("\nPress Enter to close.")
    except EOFError:
        pass

def main():
    print("=" * 62)
    print(" OASIS v3.8.1 STOCK VALUATION HOTFIX")
    print("=" * 62)

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
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v381"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in ("app.py", "stock_valuation.py", "VERSION.txt"):
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    history_path = ROOT / "update_history.json"
    try:
        history = (
            json.loads(history_path.read_text(encoding="utf-8"))
            if history_path.exists()
            else []
        )
        if not isinstance(history, list):
            history = []
    except Exception:
        history = []

    history.insert(0, {
        "버전": "v3.8.1",
        "적용일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "업데이트": "주가평가 입력 오류·고객DB 재무연동 개선",
        "백업폴더": backup.name,
    })
    history_path.write_text(
        json.dumps(history[:100], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[SUCCESS] v3.8.1 update completed.")
    print("Next: streamlit run app.py")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
