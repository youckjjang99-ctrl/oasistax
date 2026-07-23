from __future__ import annotations

import ast
import importlib.util
import json
import py_compile
import re
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent

CORE_FILES = [
    "app.py", "auth.py", "enterprise_center.py",
    "enterprise_customer_management.py", "employee_status.py",
    "encrypted_excel_reader.py",
    "employment_support_2026.py",
    "multi_source_policy.py", "integrated_policy_repository.py",
    "maintenance.py", "VERSION.txt",
]

REQUIRED_SYMBOLS = {
    "enterprise_customer_management.py": [
        "confirm_delete_dialog",
        "filter_active_customers",
        "render_customer_trash_page",
    ],
    "employee_status.py": [
        "render_employee_status",
        "employee_matching_context",
    ],
    "encrypted_excel_reader.py": [
        "is_encrypted_office",
        "decrypt_excel_bytes",
    ],
    "employment_support_2026.py": [
        "render_employment_support_analysis",
        "region_type_from_address",
    ],
    "auth.py": ["render_password_change"],
}

IMPORT_CHECK_FILES = [
    "enterprise_customer_management.py",
    "employee_status.py",
    "employment_support_2026.py",
    "enterprise_center.py",
    "multi_source_policy.py",
    "maintenance.py",
]


def _result(category: str, item: str, ok: bool, message: str, level: str = "error") -> dict[str, Any]:
    return {
        "category": category,
        "item": item,
        "ok": bool(ok),
        "status": "정상" if ok else ("경고" if level == "warning" else "오류"),
        "message": message,
        "level": level,
    }


def _python_files(root: Path) -> list[Path]:
    ignored = {".git", ".venv", "venv", "__pycache__", "_oasis_backups", "payload"}
    return [
        path for path in root.rglob("*.py")
        if not any(part in ignored for part in path.parts)
    ]


def _defined_symbols(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    symbols: set[str] = set()

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    symbols.add(target.id)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    for element in target.elts:
                        if isinstance(element, ast.Name):
                            symbols.add(element.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            symbols.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symbols.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    symbols.add(alias.asname or alias.name)
    return symbols


def check_required_files(root: Path) -> list[dict[str, Any]]:
    return [
        _result("필수파일", name, (root / name).exists(),
                "파일 존재" if (root / name).exists() else "필수 파일 누락")
        for name in CORE_FILES
    ]


def check_runtime_dependencies() -> list[dict[str, Any]]:
    available = importlib.util.find_spec("msoffcrypto") is not None
    return [
        _result(
            "의존성",
            "msoffcrypto-tool",
            available,
            (
                "암호화 Excel 자동 해제 모듈 사용 가능"
                if available
                else "requirements.txt 설치 필요"
            ),
        )
    ]


def check_syntax(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in _python_files(root):
        relative = str(path.relative_to(root))
        try:
            py_compile.compile(str(path), doraise=True)
            rows.append(_result("문법", relative, True, "Python 문법 정상"))
        except Exception as exc:
            rows.append(_result("문법", relative, False, f"{type(exc).__name__}: {exc}"))
    return rows


def check_required_symbols(root: Path) -> list[dict[str, Any]]:
    rows = []
    for relative, required in REQUIRED_SYMBOLS.items():
        path = root / relative
        if not path.exists():
            for symbol in required:
                rows.append(_result("필수심볼", f"{relative}:{symbol}", False, "파일 누락"))
            continue

        try:
            symbols = _defined_symbols(path)
        except Exception as exc:
            for symbol in required:
                rows.append(_result("필수심볼", f"{relative}:{symbol}", False, f"분석 실패: {exc}"))
            continue

        for symbol in required:
            rows.append(_result(
                "필수심볼",
                f"{relative}:{symbol}",
                symbol in symbols,
                "심볼 존재" if symbol in symbols else "호출 대상 심볼 누락",
            ))
    return rows


def check_static_imports(root: Path) -> list[dict[str, Any]]:
    rows = []
    local_modules = {path.stem for path in root.glob("*.py")}

    for relative in IMPORT_CHECK_FILES:
        path = root / relative
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue

            module_root = node.module.split(".")[0]
            if module_root not in local_modules:
                continue

            target = root / f"{module_root}.py"
            if not target.exists():
                rows.append(_result("Import", f"{relative} → {node.module}", False, "로컬 모듈 파일 누락"))
                continue

            symbols = _defined_symbols(target)
            for alias in node.names:
                if alias.name == "*":
                    continue
                exists = alias.name in symbols
                rows.append(_result(
                    "Import",
                    f"{relative} → {node.module}.{alias.name}",
                    exists,
                    "Import 대상 존재" if exists else "Import 대상 심볼 누락",
                ))
    return rows


def check_streamlit_options(root: Path) -> list[dict[str, Any]]:
    rows = []
    for path in _python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func_name = node.func.attr if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if func_name != "columns":
                continue

            for keyword in node.keywords:
                if keyword.arg != "vertical_alignment":
                    continue
                if isinstance(keyword.value, ast.Constant):
                    value = keyword.value.value
                    valid = value in {"top", "center", "bottom"}
                    rows.append(_result(
                        "Streamlit옵션",
                        f'{path.name}:vertical_alignment="{value}"',
                        valid,
                        "허용된 정렬값" if valid else "top·center·bottom만 허용됩니다.",
                    ))
    return rows


def check_duplicate_streamlit_keys(root: Path) -> list[dict[str, Any]]:
    pattern = re.compile(r"""key\s*=\s*["']([^"']+)["']""")
    occurrences: dict[str, list[str]] = {}

    for path in _python_files(root):
        source = path.read_text(encoding="utf-8", errors="ignore")
        relative = str(path.relative_to(root))
        for key in pattern.findall(source):
            occurrences.setdefault(key, []).append(relative)

    rows = []
    for key, files in sorted(occurrences.items()):
        duplicate = len(files) > 1
        rows.append(_result(
            "WidgetKey",
            key,
            not duplicate,
            "고정 widget key 중복 가능성: " + ", ".join(sorted(set(files)))
            if duplicate else f"사용 파일: {files[0]}",
            level="warning",
        ))
    return rows


def run_precheck(project_root: Path | str | None = None, save_report: bool = True) -> dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT).resolve()
    checks: list[dict[str, Any]] = []
    checks.extend(check_required_files(root))
    checks.extend(check_runtime_dependencies())
    checks.extend(check_syntax(root))
    checks.extend(check_required_symbols(root))
    checks.extend(check_static_imports(root))
    checks.extend(check_streamlit_options(root))
    checks.extend(check_duplicate_streamlit_keys(root))

    errors = [row for row in checks if not row["ok"] and row["level"] == "error"]
    warnings = [row for row in checks if not row["ok"] and row["level"] == "warning"]

    report = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "version": (
            (root / "VERSION.txt").read_text(encoding="utf-8-sig").strip()
            if (root / "VERSION.txt").exists() else "확인불가"
        ),
        "status": "PASS" if not errors else "FAIL",
        "error_count": len(errors),
        "warning_count": len(warnings),
        "check_count": len(checks),
        "checks": checks,
    }

    if save_report:
        report_path = root / "data" / "system_precheck_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    report = run_precheck()
    print("================================================")
    print("OASIS DEPLOY PRECHECK")
    print("================================================")
    print(f"VERSION={report['version']}")
    print(f"STATUS={report['status']}")
    print(f"ERRORS={report['error_count']}")
    print(f"WARNINGS={report['warning_count']}")
    print(f"CHECKS={report['check_count']}")

    for row in [item for item in report["checks"] if not item["ok"]][:50]:
        print(f"[{row['status']}] {row['category']} / {row['item']}")
        print(f"  {row['message']}")

    print("DEPLOY_READY" if report["status"] == "PASS" else "DEPLOY_BLOCKED")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
