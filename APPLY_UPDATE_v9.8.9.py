from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


VERSION = "v9.8.9"
EXPECTED_PATCH_TESTS = 71
ROOT = Path(__file__).resolve().parent
PYTHON_FILES = (
    "app.py",
    "company_sales_assignment.py",
    "prospect_collection_service.py",
    "prospect_db_center.py",
    "prospect_db_repository.py",
    "scheduled_employment_contact_enrichment.py",
)
REQUIRED_FILES = (
    *PYTHON_FILES,
    "supabase_v1032_company_sales_assignments.sql",
    "supabase_v1032_company_sales_assignments_rls.sql",
    "tests/test_company_sales_assignments.py",
    "tests/test_company_sales_assignment_integration.py",
    "tests/test_company_sales_assignment_migration.py",
    "tests/test_scheduled_employment_contact_enrichment.py",
    "CHANGELOG_v9.8.9.md",
    "GITHUB_UPLOAD_COMMANDS_v9.8.9.txt",
    "IMPLEMENTATION_REPORT_v9.8.9.md",
    "PATCH_MANIFEST_v9.8.9.txt",
    "README_UPDATE_v9.8.9.md",
    "RUN_v9.8.9.bat",
    "VERSION.txt",
)
EXPECTED_PATCH_FILES = (
    "APPLY_UPDATE_v9.8.9.py",
    "CHANGELOG_v9.8.9.md",
    "GITHUB_UPLOAD_COMMANDS_v9.8.9.txt",
    "IMPLEMENTATION_REPORT_v9.8.9.md",
    "PATCH_MANIFEST_v9.8.9.txt",
    "README_UPDATE_v9.8.9.md",
    "RUN_v9.8.9.bat",
    "VERSION.txt",
    "app.py",
    "company_sales_assignment.py",
    "prospect_collection_service.py",
    "prospect_db_center.py",
    "prospect_db_repository.py",
    "scheduled_employment_contact_enrichment.py",
    "supabase_v1032_company_sales_assignments.sql",
    "supabase_v1032_company_sales_assignments_rls.sql",
    "tests/test_company_sales_assignment_integration.py",
    "tests/test_company_sales_assignment_migration.py",
    "tests/test_company_sales_assignments.py",
    "tests/test_scheduled_employment_contact_enrichment.py",
)


def validate_files() -> None:
    missing = [name for name in REQUIRED_FILES if not (ROOT / name).is_file()]
    if missing:
        raise RuntimeError("Missing patch files: " + ", ".join(missing))
    version = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()
    if version != VERSION:
        raise RuntimeError(f"Expected {VERSION}, found {version or 'empty'}")
    manifest = tuple(
        line.strip()
        for line in (ROOT / "PATCH_MANIFEST_v9.8.9.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    )
    if len(manifest) != len(set(manifest)):
        raise RuntimeError("Patch manifest contains duplicate entries.")
    if set(manifest) != set(EXPECTED_PATCH_FILES):
        missing = sorted(set(EXPECTED_PATCH_FILES) - set(manifest))
        extra = sorted(set(manifest) - set(EXPECTED_PATCH_FILES))
        raise RuntimeError(
            "Patch manifest mismatch; missing="
            + repr(missing)
            + ", extra="
            + repr(extra)
        )
    missing_manifest_files = [name for name in manifest if not (ROOT / name).is_file()]
    if missing_manifest_files:
        raise RuntimeError(
            "Patch manifest files missing: " + ", ".join(missing_manifest_files)
        )

    run_text = (ROOT / "RUN_v9.8.9.bat").read_text(encoding="utf-8").lower()
    migration_name = "supabase_v1032_company_sales_assignments.sql"
    rls_name = "supabase_v1032_company_sales_assignments_rls.sql"
    if migration_name not in run_text or rls_name not in run_text:
        raise RuntimeError("RUN instructions must mention both migration SQL files.")
    if run_text.index(migration_name) > run_text.index(rls_name):
        raise RuntimeError("RUN instructions must list migration SQL before RLS SQL.")

    upload_text = (ROOT / "GITHUB_UPLOAD_COMMANDS_v9.8.9.txt").read_text(
        encoding="utf-8"
    )
    upload_lines = tuple(line.strip() for line in upload_text.splitlines())
    if "git add ." in upload_lines:
        raise RuntimeError("GitHub commands must stage the exact patch file list.")
    if ".codex-remote-attachments" in upload_text:
        raise RuntimeError("GitHub commands must not include private attachments.")
    missing_upload_files = [
        name for name in EXPECTED_PATCH_FILES if name not in upload_text
    ]
    if missing_upload_files:
        raise RuntimeError(
            "GitHub upload commands missing: " + ", ".join(missing_upload_files)
        )


def validate_python() -> None:
    for name in PYTHON_FILES:
        source = (ROOT / name).read_text(encoding="utf-8")
        compile(source, name, "exec")


def validate_migration() -> None:
    sql = (ROOT / "supabase_v1032_company_sales_assignments.sql").read_text(
        encoding="utf-8"
    ).lower()
    required_tokens = (
        "oasis_company_sales_assignments",
        "oasis_claim_company_sales_assignment",
        "oasis_claim_and_save_company_sales_assignment",
        "oasis_record_company_sales_contact",
        "oasis_release_expired_company_assignments",
        "oasis_list_company_assignment_admin_metrics",
        "oasis_consultation_journals",
        "oasis_resolve_candidate_company_uids",
        "oasis_employment_contacts",
        "strong_identifier_conflict",
        "source_identity_conflict",
        "wrong_number_reactivated_after_phone_change",
        "oasis_company_assignment_audit_logs",
        "row level security",
    )
    missing = [token for token in required_tokens if token not in sql]
    if missing:
        raise RuntimeError("Migration contract missing: " + ", ".join(missing))
    if "all sequences in schema public" in sql:
        raise RuntimeError(
            "Migration must not change permissions on existing project sequences."
        )
    rls_sql = (
        ROOT / "supabase_v1032_company_sales_assignments_rls.sql"
    ).read_text(encoding="utf-8").lower()
    for role in ("anon", "authenticated", "service_role"):
        if role not in rls_sql:
            raise RuntimeError(f"RLS contract missing role: {role}")


def run_tests() -> None:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_company_sales_assignments.py",
        "tests/test_company_sales_assignment_integration.py",
        "tests/test_company_sales_assignment_migration.py",
        "tests/test_scheduled_employment_contact_enrichment.py",
        "tests/test_prospect_display_frame.py",
        (
            "tests/test_prospect_mobile_access.py::"
            "ProspectMobileAccessTests::"
            "test_member_receives_landline_but_not_mobile"
        ),
        "tests/test_auth_security.py",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    match = re.search(r"(\d+) passed", completed.stdout or "")
    passed = int(match.group(1)) if match else 0
    if passed != EXPECTED_PATCH_TESTS:
        raise RuntimeError(
            f"Expected {EXPECTED_PATCH_TESTS} passing patch tests, found {passed}."
        )


def main() -> int:
    validate_files()
    validate_python()
    validate_migration()
    run_tests()
    print(f"OASIS CRM {VERSION} patch validation passed.")
    print("No Supabase or Railway production data was changed by this validator.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
