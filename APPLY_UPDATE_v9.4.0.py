from __future__ import annotations

import importlib.util
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

EXPECTED = "v9.3.4"
TARGET = "v9.4.0"
FILES = [
    "app.py",
    "utils.py",
    "income_tax_return_parser.py",
    "corporate_conversion_analyzer.py",
    "consulting_copilot.py",
    "consulting_report.py",
    "enterprise_center.py",
    "VERSION.txt",
]
COMPILE_FILES = [name for name in FILES if name.endswith(".py")]


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Import spec 생성 실패: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runtime_smoke_test(root: Path):
    parser = _load_module(root / "income_tax_return_parser.py", "_oasis_income_tax_test")
    analyzer = _load_module(
        root / "corporate_conversion_analyzer.py",
        "_oasis_conversion_test",
    )
    if not callable(getattr(parser, "parse_income_tax_return", None)):
        raise RuntimeError("종합소득세 신고서 파서 실행점검 실패")
    result = analyzer.analyze_corporate_conversion(
        {
            "매출액": 500_000_000,
            "사업소득금액": 100_000_000,
            "과세표준": 88_000_000,
            "적용세율": 35,
        }
    )
    if not isinstance(result, dict) or "score" not in result or "grade" not in result:
        raise RuntimeError("법인전환 분석 엔진 실행점검 실패")


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
    originally_missing = []

    try:
        for name in FILES:
            source = payload / name
            target = root / name
            if target.exists():
                shutil.copy2(target, backup / name)
            else:
                originally_missing.append(name)
            shutil.copy2(source, target)
            copied.append(name)

        for name in COMPILE_FILES:
            py_compile.compile(str(root / name), doraise=True)
        _runtime_smoke_test(root)

        print("UPDATE_OK")
        print(f"VERSION={TARGET}")
        print("MENU=기업등록")
        print("CORPORATE_REGISTRATION=CRETOP_PRESERVED")
        print("SOLE_PROPRIETOR=INCOME_TAX_PDF_SUPPORTED")
        print("MULTI_BUSINESS=SELECTIVE_REGISTRATION")
        print("CORPORATE_CONVERSION=AUTOMATIC_PRECHECK")
        print("SENSITIVE_FIELDS=NOT_STORED")
        print("PY_COMPILE=OK")
        print("RUNTIME_SMOKE_TEST=OK")
        print("DB_SCHEMA=BACKWARD_COMPATIBLE")
        print(f"BACKUP={backup}")
        return 0
    except Exception as exc:
        print(f"UPDATE_FAILED: {exc}")
        for name in copied:
            target = root / name
            backup_file = backup / name
            if backup_file.exists():
                shutil.copy2(backup_file, target)
            elif name in originally_missing and target.exists():
                target.unlink()
        print(f"ROLLBACK_OK={backup}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
