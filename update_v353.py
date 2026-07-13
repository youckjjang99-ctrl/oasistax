from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"
TARGET_VERSION = "v3.5.3"
BACKUP_ROOT = ROOT / "_oasis_backups"
FILES = ("app.py", "utils.py", "VERSION.txt", "CHANGELOG_v3.5.3.md")


def pause() -> None:
    try:
        input("\n엔터를 누르면 창이 닫힙니다.")
    except EOFError:
        pass


def main() -> int:
    print("=" * 64)
    print(" OASIS v3.5.3 FINANCIAL PARSER AND MENU UPDATE")
    print("=" * 64)

    if not (ROOT / "app.py").exists() or not (ROOT / "utils.py").exists():
        print("[ERROR] 업데이트 파일을 프로젝트 최상위 폴더에 풀어주세요.")
        pause()
        return 1

    missing = [name for name in FILES if not (PAYLOAD / name).exists()]
    if missing:
        print("[ERROR] 업데이트 구성파일 누락:")
        for name in missing:
            print(f" - {name}")
        pause()
        return 1

    backup = BACKUP_ROOT / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_before_v353"
    backup.mkdir(parents=True, exist_ok=False)

    for name in ("app.py", "utils.py", "VERSION.txt"):
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup / name)

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
        "버전": TARGET_VERSION,
        "적용일시": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "업데이트": "크레탑 재무정보 추출·숫자 콤마·메뉴 순서 개선",
        "백업폴더": backup.name,
    })
    history_path.write_text(
        json.dumps(history[:100], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("[SUCCESS] v3.5.3 업데이트가 완료되었습니다.")
    print("다음 실행: streamlit run app.py")
    pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
