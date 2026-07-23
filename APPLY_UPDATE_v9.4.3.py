from __future__ import annotations

import ast
import os
import py_compile
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

EXPECTED = "v9.4.2"
TARGET = "v9.4.3"
FILES = [
    "employee_status.py",
    "employment_support_2026.py",
    "enterprise_center.py",
    "system_precheck.py",
    "VERSION.txt",
]
COMPILE_FILES = [
    "employee_status.py",
    "employment_support_2026.py",
    "enterprise_center.py",
    "system_precheck.py",
]


def _address_classifier(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        (
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "region_type_from_address"
        ),
        None,
    )
    if function is None:
        raise RuntimeError("주소 기반 수도권 판정 함수 누락")
    namespace: dict[str, Any] = {"re": re, "Any": Any}
    exec(
        compile(
            ast.Module(body=[function], type_ignores=[]),
            str(path),
            "exec",
        ),
        namespace,
    )
    return namespace["region_type_from_address"]


def _runtime_smoke_test(root: Path) -> None:
    employee_source = (root / "employee_status.py").read_text(
        encoding="utf-8"
    )
    for marker in (
        '"재직상태": employee.get("status", "가입중")',
        '"고용종료일": employee.get("loss_date", "")',
        '_review_date(row.get("고용종료일", ""))',
    ):
        if marker not in employee_source:
            raise RuntimeError(f"재직상태 보존 점검 실패: {marker}")

    support_source = (
        root / "employment_support_2026.py"
    ).read_text(encoding="utf-8")
    for marker in (
        "with st.form(",
        "st.form_submit_button(",
        "추가정보 확인 후 통합진단",
        '"region_source": "company_address"',
    ):
        if marker not in support_source:
            raise RuntimeError(f"추가정보 일괄진단 점검 실패: {marker}")
    if '"사업장 지역",' in support_source:
        raise RuntimeError("수동 수도권 선택란 제거 점검 실패")

    classify = _address_classifier(root / "employment_support_2026.py")
    cases = {
        "서울특별시 강남구": "수도권",
        "경기도 평택시": "수도권",
        "인천광역시 남동구": "수도권",
        "충청남도 천안시": "비수도권",
        "": "확인필요",
    }
    for address, expected in cases.items():
        if classify(address) != expected:
            raise RuntimeError(f"주소 자동판정 실패: {address or '빈 주소'}")


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
        print("ACTIVE_EMPLOYEE_COUNT=LOSS_DATE_AWARE")
        print("REGION_TYPE=COMPANY_ADDRESS_AUTO")
        print("EXTRA_INPUT=FORM_BATCH_SUBMIT")
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
