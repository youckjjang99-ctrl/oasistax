from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
BACKUP_ROOT = ROOT / "_oasis_backups"
FILES = (
    "app.py",
    "utils.py",
    "cretop_worker.py",
    "cretop_runner.py",
    "requirements.txt",
    "VERSION.txt",
    "CHANGELOG_v3.6.0.md",
)


def pause():
    try:
        input("\n엔터를 누르면 창이 닫힙니다.")
    except EOFError:
        pass


def main():
    print("=" * 66)
    print(" OASIS v3.6.0 ISOLATED CRETOP ENGINE UPDATE")
    print("=" * 66)

    if not (ROOT / "app.py").exists() or not (ROOT / "utils.py").exists():
        print("[ERROR] 프로젝트 최상위 폴더에 압축을 풀어주세요.")
        pause()
        return 1

    missing = [name for name in FILES if not (PAYLOAD / name).exists()]
    if missing:
        print("[ERROR] 업데이트 구성파일 누락:", ", ".join(missing))
        pause()
        return 1

    backup = BACKUP_ROOT / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_before_v360"
    backup.mkdir(parents=True, exist_ok=False)

    for name in ("app.py", "utils.py", "requirements.txt", "VERSION.txt", "cretop_worker.py", "cretop_runner.py"):
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
        "버전": "v3.6.0",
        "적용일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "업데이트": "크레탑 PDF 분석 프로세스 분리·전체 페이지 동적 탐색",
        "백업폴더": backup.name,
    })
    history_path.write_text(json.dumps(history[:100], ensure_ascii=False, indent=2), encoding="utf-8")

    print("[SUCCESS] v3.6.0 업데이트 완료")
    print("다음 실행: streamlit run app.py")
    pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
