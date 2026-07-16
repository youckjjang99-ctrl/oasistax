from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path

VERSION = "v7.4.4"
TARGET_FILES = [
    "system_precheck.py",
    "update_safety.py",
    "maintenance.py",
    "VERSION.txt",
]

MAINTENANCE_SECTION = '    st.markdown("#### 배포 사전점검")\n    precheck_report = run_precheck(project_root, save_report=True)\n    p1, p2, p3 = st.columns(3)\n    p1.metric(\n        "배포 가능 여부",\n        "가능" if precheck_report.get("status") == "PASS" else "차단",\n    )\n    p2.metric("오류", f"{precheck_report.get(\'error_count\', 0)}개")\n    p3.metric("경고", f"{precheck_report.get(\'warning_count\', 0)}개")\n\n    failed_checks = [\n        row for row in precheck_report.get("checks", [])\n        if not row.get("ok")\n    ]\n    if failed_checks:\n        st.dataframe(\n            failed_checks,\n            hide_index=True,\n            use_container_width=True,\n        )\n    else:\n        st.success(\n            "문법·Import·필수심볼·Streamlit 옵션 검사 결과 배포 가능합니다."\n        )\n\n'


def fail(message: str) -> None:
    print("UPDATE_FAILED")
    print(message)
    input("Press Enter to close...")
    raise SystemExit(1)


def patch_maintenance(text: str) -> str:
    import_anchor = "from collector import sync_bizinfo_cache\n"
    if "from system_precheck import run_precheck" not in text:
        if import_anchor not in text:
            raise RuntimeError("maintenance.py import 위치를 찾지 못했습니다.")
        text = text.replace(
            import_anchor,
            import_anchor + "from system_precheck import run_precheck\n",
            1,
        )

    section_anchor = '    st.markdown("#### 프로젝트 상태")\n'
    if "#### 배포 사전점검" not in text:
        if section_anchor not in text:
            raise RuntimeError("maintenance.py 프로젝트 상태 위치를 찾지 못했습니다.")
        text = text.replace(
            section_anchor,
            MAINTENANCE_SECTION + section_anchor,
            1,
        )
    return text


def main() -> None:
    root = Path.cwd()
    if not (root / "app.py").exists():
        fail("app.py가 있는 OASIS 프로젝트 폴더에서 실행해주세요.")

    version_path = root / "VERSION.txt"
    current = (
        version_path.read_text(encoding="utf-8-sig").strip()
        if version_path.exists() else ""
    )
    if current and current not in {
        "v7.4.2", "7.4.2", "v7.4.3", "7.4.3", "v7.4.4", "7.4.4"
    }:
        fail(f"Expected v7.4.2 or v7.4.3 but found {current}.")

    sys.path.insert(0, str(root / "payload"))
    from update_safety import create_update_backup, rollback_update

    backup_dir = create_update_backup(root, VERSION, TARGET_FILES)

    try:
        for relative in ["system_precheck.py", "update_safety.py"]:
            source = root / "payload" / relative
            if not source.exists():
                raise FileNotFoundError(f"payload/{relative} 누락")
            shutil.copy2(source, root / relative)

        maintenance_path = root / "maintenance.py"
        text = maintenance_path.read_text(encoding="utf-8")
        maintenance_path.write_text(
            patch_maintenance(text),
            encoding="utf-8",
            newline="\n",
        )

        version_path.write_text(VERSION + "\n", encoding="utf-8")

        changelog = root / "payload" / "CHANGELOG_v7.4.4.md"
        if changelog.exists():
            shutil.copy2(changelog, root / "CHANGELOG_v7.4.4.md")

        for name in [
            "system_precheck.py", "update_safety.py", "maintenance.py",
            "app.py", "enterprise_center.py",
            "enterprise_customer_management.py",
            "employee_status.py", "multi_source_policy.py",
        ]:
            path = root / name
            if path.exists():
                py_compile.compile(str(path), doraise=True)

        sys.path.insert(0, str(root))
        from system_precheck import run_precheck

        report = run_precheck(root, save_report=True)
        if report.get("status") != "PASS":
            errors = [
                row for row in report.get("checks", [])
                if not row.get("ok") and row.get("level") == "error"
            ]
            summary = "; ".join(
                f"{row.get('item')}: {row.get('message')}"
                for row in errors[:8]
            )
            raise RuntimeError("사전점검 실패: " + summary)

    except Exception as exc:
        restored = rollback_update(root, backup_dir, TARGET_FILES)
        print("UPDATE_ROLLED_BACK")
        print(f"BACKUP={backup_dir}")
        print("RESTORED=" + ",".join(restored))
        fail(f"{type(exc).__name__}: {exc}")

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print(f"BACKUP={backup_dir}")
    print("PRECHECK=PASS")
    print("AUTO_ROLLBACK=ENABLED")
    print("SQL_REQUIRED=NO")
    input("Press Enter to close...")


if __name__ == "__main__":
    main()
