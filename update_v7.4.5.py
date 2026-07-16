from pathlib import Path
import py_compile
import shutil
from datetime import datetime

VERSION = "v7.4.5"

def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)

def main() -> None:
    root = Path.cwd()
    target = root / "maintenance.py"
    if not target.exists():
        fail("maintenance.py가 있는 OASIS 프로젝트 폴더에서 실행해주세요.")

    version_path = root / "VERSION.txt"
    current = version_path.read_text(encoding="utf-8-sig").strip() if version_path.exists() else ""
    if current and current not in {"v7.4.4", "7.4.4", "v7.4.5", "7.4.5"}:
        fail(f"Expected v7.4.4 but found {current}.")

    backup = root / "_oasis_backups" / (
        "before_v7.4.5_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup / "maintenance.py")
    if version_path.exists():
        shutil.copy2(version_path, backup / "VERSION.txt")

    text = target.read_text(encoding="utf-8")
    old1 = 'p2.metric("오류", f"{precheck_report.get("error_count", 0)}개")'
    old2 = 'p3.metric("경고", f"{precheck_report.get("warning_count", 0)}개")'
    new1 = 'p2.metric("오류", f"{precheck_report.get(\'error_count\', 0)}개")'
    new2 = 'p3.metric("경고", f"{precheck_report.get(\'warning_count\', 0)}개")'

    changed = False
    if old1 in text:
        text = text.replace(old1, new1, 1)
        changed = True
    if old2 in text:
        text = text.replace(old2, new2, 1)
        changed = True

    if not changed and ("error_count" in text and "warning_count" in text):
        # already fixed or slightly different formatting
        pass
    elif not changed:
        fail("수정할 문법오류 위치를 찾지 못했습니다.")

    target.write_text(text, encoding="utf-8", newline="\n")
    version_path.write_text(VERSION + "\n", encoding="utf-8")

    py_compile.compile(str(target), doraise=True)
    py_compile.compile(str(root / "app.py"), doraise=True)

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup}")
    print("SQL_REQUIRED=NO")
    print("RESULT=maintenance.py f-string syntax fixed")
    input("Press Enter to close...")

if __name__ == "__main__":
    main()
