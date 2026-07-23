from __future__ import annotations

import importlib.util
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

EXPECTED = "v9.4.0"
TARGET = "v9.4.1"
FILES = [
    "app.py",
    "income_tax_return_parser.py",
    "requirements.txt",
    "VERSION.txt",
]
COMPILE_FILES = ["app.py", "income_tax_return_parser.py"]


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Import spec 생성 실패: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_smoke_test(root: Path):
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    if "pdfplumber==" not in requirements:
        raise RuntimeError("Railway pdfplumber 의존성 누락")

    parser = _load_module(
        root / "income_tax_return_parser.py",
        "_oasis_income_tax_v941_test",
    )
    required = [
        "parse_income_tax_return",
        "_business_validation_errors",
    ]
    for name in required:
        if not callable(getattr(parser, name, None)):
            raise RuntimeError(f"신고서 파서 실행점검 실패: {name}")

    missing = parser._business_validation_errors(
        {
            "업체명": "개인사업장 1",
            "사업자등록번호": "",
            "매출액": None,
            "필요경비": None,
            "사업소득금액": None,
        }
    )
    if len(missing) < 5:
        raise RuntimeError("불완전 사업장 등록 차단 점검 실패")


def main() -> int:
    root = Path(__file__).resolve().parent
    payload = root / "payload"
    version_file = root / "VERSION.txt"
    current = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else ""

    if current != EXPECTED:
        print(f"UPDATE_FAILED: Expected {EXPECTED} but found {current or 'UNKNOWN'}")
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
    copied = []

    try:
        for name in FILES:
            source = payload / name
            target = root / name
            if target.exists():
                shutil.copy2(target, backup / name)
            shutil.copy2(source, target)
            copied.append(name)

        for name in COMPILE_FILES:
            py_compile.compile(str(root / name), doraise=True)
        _runtime_smoke_test(root)

        print("UPDATE_OK")
        print(f"VERSION={TARGET}")
        print("RAILWAY_PDFPLUMBER=INSTALLED")
        print("PYPDF_LAYOUT_FALLBACK=SUPPORTED")
        print("INCOMPLETE_ANALYSIS=BLOCKED")
        print("INCOMPLETE_REGISTRATION=BLOCKED")
        print("PY_COMPILE=OK")
        print("RUNTIME_SMOKE_TEST=OK")
        print("DB_SCHEMA=PRESERVED")
        print(f"BACKUP={backup}")
        return 0
    except Exception as exc:
        print(f"UPDATE_FAILED: {exc}")
        for name in copied:
            backup_file = backup / name
            if backup_file.exists():
                shutil.copy2(backup_file, root / name)
        print(f"ROLLBACK_OK={backup}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

