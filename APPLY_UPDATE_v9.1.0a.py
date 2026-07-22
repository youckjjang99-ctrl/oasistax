from __future__ import annotations
import ast
import shutil
import sys
from datetime import datetime
from pathlib import Path

TARGET_VERSION = "v9.1.0a"
ALLOWED_VERSIONS = {"v9.0.0"}
FILES = ["consulting_report.py", "tax_diagnosis.py", "VERSION.txt"]

def normalize_version(text: str) -> str:
    return text.replace("\\n", "").replace("\\r", "").strip()

def validate_python(path: Path) -> None:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

def main() -> int:
    patch_root = Path(__file__).resolve().parent
    project_root = patch_root
    payload = patch_root / "payload"
    version_file = project_root / "VERSION.txt"
    print("OASIS v9.1.0a AI TAX DIAGNOSIS CORE")
    print(f"PROJECT_ROOT={project_root}")
    print("Existing DB, uploads, Supabase and user data are preserved.")
    print("=" * 54)
    if not version_file.exists():
        raise RuntimeError("VERSION.txt not found. Extract the patch into the project root.")
    current_raw = version_file.read_text(encoding="utf-8", errors="ignore")
    current = normalize_version(current_raw)
    if current == TARGET_VERSION:
        print("UPDATE_OK: already applied")
        print(f"VERSION={TARGET_VERSION}")
        return 0
    if current not in ALLOWED_VERSIONS:
        raise RuntimeError(f"Expected {sorted(ALLOWED_VERSIONS)} but found {current_raw!r} (normalized={current!r}).")
    for name in FILES:
        src = payload / name
        if not src.exists():
            raise RuntimeError(f"Payload missing: {name}")
        if src.suffix == ".py":
            validate_python(src)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = project_root / "backup" / f"before_{TARGET_VERSION}_{stamp}"
    backup.mkdir(parents=True, exist_ok=True)
    touched = []
    try:
        for name in FILES:
            dst = project_root / name
            if dst.exists():
                shutil.copy2(dst, backup / name)
            shutil.copy2(payload / name, dst)
            touched.append(name)
        for name in ("consulting_report.py", "tax_diagnosis.py"):
            validate_python(project_root / name)
        version = normalize_version((project_root / "VERSION.txt").read_text(encoding="utf-8"))
        if version != TARGET_VERSION:
            raise RuntimeError(f"Version verification failed: {version}")
        report_text = (project_root / "consulting_report.py").read_text(encoding="utf-8")
        tax_text = (project_root / "tax_diagnosis.py").read_text(encoding="utf-8")
        required = ["AI 절세진단 Core", "overall_score", "overall_confidence", "priority_items"]
        combined = report_text + tax_text
        missing = [token for token in required if token not in combined]
        if missing:
            raise RuntimeError(f"Feature verification failed: {missing}")
    except Exception:
        for name in touched:
            backup_file = backup / name
            dst = project_root / name
            if backup_file.exists():
                shutil.copy2(backup_file, dst)
        raise
    print("UPDATE_OK")
    print(f"VERSION={TARGET_VERSION}")
    print("AI_TAX_OPPORTUNITY_SCORE=ENABLED")
    print("AI_CONFIDENCE_BY_ITEM=ENABLED")
    print("EVIDENCE_AND_MISSING_DOCUMENTS=ENABLED")
    print("TAX_PRIORITY_ACTION_ITEMS=ENABLED")
    print(f"BACKUP={backup}")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"UPDATE_FAILED: {type(exc).__name__}: {exc}")
        raise SystemExit(1)
