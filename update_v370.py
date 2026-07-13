from pathlib import Path
from datetime import datetime
import shutil
import json

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
FILES = (
    "app.py",
    "utils.py",
    "cretop_worker.py",
    "crm.py",
    "requirements.txt",
    "VERSION.txt",
    "CHANGELOG_v3.7.0.md",
)

def pause():
    try:
        input("\n엔터를 누르면 창이 닫힙니다.")
    except EOFError:
        pass

def main():
    print("=" * 64)
    print(" OASIS v3.7.0 CRM UPDATE")
    print("=" * 64)

    if not (ROOT / "app.py").exists():
        print("[ERROR] 프로젝트 최상위 폴더에 압축을 풀어주세요.")
        pause()
        return 1

    missing = [name for name in FILES if not (PAYLOAD / name).exists()]
    if missing:
        print("[ERROR] 업데이트 구성파일 누락:", ", ".join(missing))
        pause()
        return 1

    backup = ROOT / "_oasis_backups" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v370"
    )
    backup.mkdir(parents=True, exist_ok=False)

    for name in ("app.py", "utils.py", "cretop_worker.py", "crm.py",
                 "requirements.txt", "VERSION.txt"):
        path = ROOT / name
        if path.exists():
            shutil.copy2(path, backup / name)

    for name in FILES:
        shutil.copy2(PAYLOAD / name, ROOT / name)

    history_path = ROOT / "update_history.json"
    try:
        history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
        if not isinstance(history, list):
            history = []
    except Exception:
        history = []

    history.insert(0, {
        "버전": "v3.7.0",
        "적용일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "업데이트": "CRM 고도화·크레탑 추출 정확도 개선",
        "백업폴더": backup.name,
    })
    history_path.write_text(
        json.dumps(history[:100], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[SUCCESS] v3.7.0 업데이트 완료")
    print("다음 실행: streamlit run app.py")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
