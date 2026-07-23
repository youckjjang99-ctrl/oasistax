from __future__ import annotations

import importlib.util
import os
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

EXPECTED = "v9.4.1"
TARGET = "v9.4.2"
FILES = [
    "employee_status.py",
    "encrypted_excel_reader.py",
    "requirements.txt",
    "system_precheck.py",
    "VERSION.txt",
]
COMPILE_FILES = [
    "employee_status.py",
    "encrypted_excel_reader.py",
    "system_precheck.py",
]


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Import spec 생성 실패: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_smoke_test(root: Path) -> None:
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    if "msoffcrypto-tool==6.0.0" not in requirements:
        raise RuntimeError("암호화 Excel 의존성 누락")

    reader = _load_module(
        root / "encrypted_excel_reader.py",
        "_oasis_encrypted_excel_v942_test",
    )
    sample = b"ordinary-unencrypted-workbook-test"
    result, metadata = reader.decrypt_excel_bytes(sample)
    if result != sample or metadata.get("decrypted"):
        raise RuntimeError("일반 Excel 보존 점검 실패")

    employee_source = (root / "employee_status.py").read_text(
        encoding="utf-8"
    )
    required_markers = [
        "decrypt_excel_bytes",
        "encrypted_excel_auto_decrypt",
        "industrial_acquisition_col",
        "industrial_loss_col",
    ]
    missing = [
        marker for marker in required_markers
        if marker not in employee_source
    ]
    if missing:
        raise RuntimeError(
            "직원현황 연동 점검 실패: " + ", ".join(missing)
        )


def main() -> int:
    root = Path(__file__).resolve().parent
    payload = root / "payload"
    version_file = root / "VERSION.txt"
    current = (
        version_file.read_text(encoding="utf-8-sig").strip()
        if version_file.exists()
        else ""
    )

    if current != EXPECTED:
        print(
            f"UPDATE_FAILED: Expected {EXPECTED} "
            f"but found {current or 'UNKNOWN'}"
        )
        return 1

    missing = [name for name in FILES if not (payload / name).exists()]
    if missing:
        print(f"UPDATE_FAILED: Missing payload: {', '.join(missing)}")
        return 1

    backup = (
        root
        / "_update_backups"
        / f"{EXPECTED}_before_{TARGET}_{datetime.now():%Y%m%d_%H%M%S}"
    )
    backup.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    originally_present: dict[str, bool] = {}

    try:
        for name in FILES:
            source = payload / name
            target = root / name
            originally_present[name] = target.exists()
            if target.exists():
                backup_target = backup / name
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_target)
            shutil.copy2(source, target)
            copied.append(name)

        for name in COMPILE_FILES:
            py_compile.compile(str(root / name), doraise=True)
        _runtime_smoke_test(root)

        if os.environ.get("OASIS_UPDATE_FORCE_FAIL") == "1":
            raise RuntimeError("강제 롤백 테스트")

        print("UPDATE_OK")
        print(f"VERSION={TARGET}")
        print("ENCRYPTED_EMPLOYMENT_EXCEL=AUTO_DECRYPT_1111")
        print("DECRYPTED_FILE_STORAGE=DISABLED")
        print("TWO_ROW_HEADER=AUTO_DETECT")
        print("PY_COMPILE=OK")
        print("RUNTIME_SMOKE_TEST=OK")
        print("DB_SCHEMA=PRESERVED")
        print(f"BACKUP={backup}")
        return 0
    except Exception as exc:
        print(f"UPDATE_FAILED: {exc}")
        for name in copied:
            target = root / name
            backup_file = backup / name
            if originally_present.get(name) and backup_file.exists():
                shutil.copy2(backup_file, target)
            elif not originally_present.get(name) and target.exists():
                target.unlink()
        print(f"ROLLBACK_OK={backup}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
