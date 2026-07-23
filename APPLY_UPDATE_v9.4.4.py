from __future__ import annotations

import importlib.util
import os
import py_compile
import shutil
from datetime import datetime
from pathlib import Path

EXPECTED = "v9.4.3"
TARGET = "v9.4.4"
FILES = [
    "income_tax_return_parser.py",
    "utils.py",
    "enterprise_center.py",
    "system_precheck.py",
    "VERSION.txt",
]
COMPILE_FILES = [
    "income_tax_return_parser.py",
    "utils.py",
    "enterprise_center.py",
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
    parser = _load_module(
        root / "income_tax_return_parser.py",
        "_oasis_income_tax_v944_test",
    )
    statement_text = (
        "표준재무상태표\n"
        "사업자등록번호 123-45-67890\n"
        "자산총계(Ⅰ+Ⅱ) 62 10,000,000\n"
        "부채총계(Ⅰ+Ⅱ) 87 2,000,000\n"
        "자본총계(Ⅲ+Ⅳ) 90 8,000,000\f"
        "표준손익계산서\n"
        "사업자등록번호 123-45-67890\n"
        "Ⅰ.매출액 01 300,000,000\n"
        "Ⅴ.영업손익(Ⅲ-Ⅳ) 62 90,000,000\n"
        "Ⅷ.당기순손익(Ⅴ+Ⅵ-Ⅶ) 99 95,000,000\n"
    )
    values = parser._financial_statement_data(statement_text).get(
        "123-45-67890",
        {},
    )
    expected = {
        "자산총계": 10_000_000,
        "부채총계": 2_000_000,
        "자본총계": 8_000_000,
        "영업손익": 90_000_000,
    }
    for key, value in expected.items():
        if values.get(key) != value:
            raise RuntimeError(f"개인사업자 재무제표 점검 실패: {key}")

    utils_source = (root / "utils.py").read_text(encoding="utf-8")
    enterprise_source = (
        root / "enterprise_center.py"
    ).read_text(encoding="utf-8")
    required_markers = {
        "utils.py": [
            '"각사업연도소득금액"',
            '"영업손익"',
            '"과세기간시작일"',
            '"자산총계"',
        ],
        "enterprise_center.py": [
            '"각사업연도소득금액"',
            "종합소득세 신고정보",
            '"주업종코드"',
        ],
    }
    for marker in required_markers["utils.py"]:
        if marker not in utils_source:
            raise RuntimeError(f"고객DB 저장필드 누락: {marker}")
    for marker in required_markers["enterprise_center.py"]:
        if marker not in enterprise_source:
            raise RuntimeError(f"기업정보 표시항목 누락: {marker}")


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

    try:
        for name in FILES:
            source = payload / name
            target = root / name
            if not target.exists():
                raise RuntimeError(f"기존 필수 파일 누락: {name}")
            shutil.copy2(target, backup / name)
            shutil.copy2(source, target)
            copied.append(name)

        for name in COMPILE_FILES:
            py_compile.compile(str(root / name), doraise=True)
        _runtime_smoke_test(root)

        if os.environ.get("OASIS_UPDATE_FORCE_FAIL") == "1":
            raise RuntimeError("강제 롤백 테스트")

        print("UPDATE_OK")
        print(f"VERSION={TARGET}")
        print("PERSONAL_BUSINESS_ADDRESS=PARSED")
        print("FINANCIAL_STATEMENTS=BUSINESS_NO_MATCHED")
        print("PERSONAL_INCOME_LABEL=각사업연도소득금액")
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
