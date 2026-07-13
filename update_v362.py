from pathlib import Path
from datetime import datetime
import shutil
import json

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
FILES = (
    "app.py", "utils.py", "cretop_worker.py",
    "requirements.txt", "VERSION.txt", "CHANGELOG_v3.6.2.md"
)

def pause():
    try:
        input("\n엔터를 누르면 창이 닫힙니다.")
    except EOFError:
        pass

def main():
    print("=" * 60)
    print(" OASIS v3.6.2 DUPLICATE CUSTOMER FIX")
    print("=" * 60)

    if not (ROOT / "app.py").exists():
        print("[ERROR] 프로젝트 최상위 폴더에 압축을 풀어주세요.")
        pause()
        return 1

    backup = ROOT / "_oasis_backups" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v362"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in ("app.py", "utils.py", "cretop_worker.py",
                 "requirements.txt", "VERSION.txt"):
        path = ROOT / name
        if path.exists():
            shutil.copy2(path, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    history_path = ROOT / "update_history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    except Exception:
        history = []

    history.insert(0, {
        "버전": "v3.6.2",
        "적용일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "업데이트": "사업자번호 중복방지 복구",
        "백업폴더": backup.name,
    })
    history_path.write_text(
        json.dumps(history[:100], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[SUCCESS] v3.6.2 업데이트 완료")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
