from pathlib import Path
from datetime import datetime
import shutil

ROOT = Path(__file__).resolve().parent
PAYLOAD = ROOT / "payload"

def pause():
    try:
        input("\n엔터를 누르면 창이 닫힙니다.")
    except EOFError:
        pass

def main():
    print("=" * 58)
    print(" OASIS v3.7.1 CRM HOTFIX")
    print("=" * 58)

    if not (ROOT / "app.py").exists():
        print("[ERROR] 이 파일들을 정책자금자동화 폴더에 풀어주세요.")
        pause()
        return 1

    backup_dir = ROOT / "_oasis_backups" / (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_before_v371"
    )
    backup_dir.mkdir(parents=True, exist_ok=False)

    for name in ("app.py", "VERSION.txt"):
        current = ROOT / name
        if current.exists():
            shutil.copy2(current, backup_dir / name)

    for name in ("app.py", "VERSION.txt", "CHANGELOG_v3.7.1.md"):
        shutil.copy2(PAYLOAD / name, ROOT / name)

    print("[SUCCESS] v3.7.1 수정이 완료되었습니다.")
    print("다음 실행: streamlit run app.py")
    pause()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
