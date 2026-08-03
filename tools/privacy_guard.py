#!/usr/bin/env python3
"""Fail closed on newly changed files that may contain private data or secrets.

The guard intentionally scans *only* paths supplied by a Git diff (or explicit
paths in tests).  This lets the repository keep a known legacy baseline while
preventing new customer artifacts and credentials from being committed.
"""

from __future__ import annotations

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Mapping, Sequence


MAX_TEXT_BYTES = 5 * 1024 * 1024
_FULL_FILE = object()

PUBLIC_DATA_ALLOWLIST = (
    "data/기업마당_공고DB.xlsx",
    "data/bizinfo_programs.json",
    "data/bizinfo_metadata.json",
    "data/external_policy_programs.json",
    "data/external_policy_metadata.json",
    "data/internal_policy_seed.json",
    "data/localdata_api_catalog.json",
    "data/articles_amendment_templates.json",
    "data/articles_review_checklist.json",
    "지원사업DB_20260710.xlsx",
    "#Uc9c0#Uc6d0#Uc0ac#Uc5c5DB_20260708.xlsx",
    "templates/#Uace0#Uac1dDB_#Uc591#Uc2ddv2.xlsx",
)

SAFE_STATIC_ASSET_PATHS = (
    "assets/oasis_logo.png",
)

PROTECTED_BINARY_EXTENSIONS = {
    ".7z",
    ".aac",
    ".avi",
    ".bak",
    ".cer",
    ".crt",
    ".db",
    ".der",
    ".doc",
    ".docx",
    ".duckdb",
    ".feather",
    ".flac",
    ".jpeg",
    ".jks",
    ".jpg",
    ".keystore",
    ".m4a",
    ".mov",
    ".mp3",
    ".mp4",
    ".ogg",
    ".pdf",
    ".p12",
    ".pfx",
    ".p8",
    ".parquet",
    ".pickle",
    ".pkl",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".sqlite",
    ".sqlite3",
    ".wav",
    ".xls",
    ".xlsx",
    ".zip",
}

TEXT_EXTENSIONS = {
    ".bat",
    ".cfg",
    ".cmd",
    ".csv",
    ".env",
    ".ini",
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

SENSITIVE_DATA_NAME = re.compile(
    r"(?:customer|client|crm[_-]?data|user[_-]?data|resident|identity|"
    r"recording|transcript|consultation|upload|download|result|history|"
    r"고객|주민|사업자|상담|녹취|업로드|다운로드)",
    re.IGNORECASE,
)

SENSITIVE_CONFIG_NAME = re.compile(
    r"(?:^|[._-])(?:\.env|secrets?|credentials?|private[_-]?key|"
    r"service[_-]?role)(?:$|[._-])",
    re.IGNORECASE,
)

PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    re.IGNORECASE,
)
JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\b"
)
KNOWN_TOKEN_PATTERN = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|"
    r"github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16})\b"
)
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)(?:^|[,{\s])(?:['\"])?"
    r"(?:api[_-]?key|api[_-]?secret|secret[_-]?key|client[_-]?secret|"
    r"service[_-]?role[_-]?key|password|passwd|private[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|auth[_-]?token)"
    r"(?:['\"])?\s*[:=]\s*(?:"
    r"(?P<quote>['\"])(?P<quoted>[^'\"\r\n]{12,})(?P=quote)|"
    r"(?P<bare>[^\s,;}#]{12,})"
    r")"
)

RRN_PATTERN = re.compile(r"(?<!\d)\d{6}[-\s]?[1-8]\d{6}(?!\d)")
PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:"
    r"(?:\+?82[-.\s]?)?0?(?:1[016789]|70|50[2-8])[-.\s]?\d{3,4}[-.\s]?\d{4}|"
    r"(?:0?2|0?[3-6][1-5])[-.\s]\d{3,4}[-.\s]\d{4}"
    r")(?!\d)"
)
BUSINESS_NUMBER_PATTERN = re.compile(r"(?<!\d)(?:\d{3}[-\s]?\d{2}[-\s]?\d{5})(?!\d)")
EMAIL_PATTERN = re.compile(
    r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    path: str
    rule: str
    message: str


def _posix_path(value: str | Path) -> str:
    normalized = PurePosixPath(str(value).replace("\\", "/")).as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.lstrip("/")


def _matches_any(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _is_public_data(path: str) -> bool:
    return _matches_any(path, PUBLIC_DATA_ALLOWLIST)


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip().lower()
    markers = (
        "${{",
        "${",
        "example",
        "placeholder",
        "changeme",
        "dummy",
        "synthetic",
        "not-a-real",
        "test-",
        "xxxx",
        "os.getenv",
        "self.",
        "config.",
        "st.secrets",
    )
    return not lowered or any(marker in lowered for marker in markers)


def _scan_text(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    if PRIVATE_KEY_PATTERN.search(text):
        findings.append(Finding(path, "private-key", "개인키 본문이 포함되어 있습니다."))
    if JWT_PATTERN.search(text):
        findings.append(Finding(path, "jwt-token", "JWT 또는 service-role 토큰 형태가 포함되어 있습니다."))
    if KNOWN_TOKEN_PATTERN.search(text):
        findings.append(Finding(path, "known-secret-token", "실제 비밀키와 유사한 토큰 형식이 포함되어 있습니다."))

    is_test_path = path.startswith("tests/") or "/tests/" in f"/{path}"
    if not is_test_path:
        for match in SECRET_ASSIGNMENT_PATTERN.finditer(text):
            value = match.group("quoted") or match.group("bare") or ""
            if not _looks_like_placeholder(value):
                findings.append(
                    Finding(path, "hardcoded-secret", "하드코딩된 비밀값으로 보이는 설정이 포함되어 있습니다.")
                )
                break

    if not _is_public_data(path):
        pii_rules = (
            (RRN_PATTERN, "resident-number", "주민등록번호 형태가 포함되어 있습니다."),
            (PHONE_PATTERN, "phone-number", "전화번호 형태가 포함되어 있습니다."),
            (BUSINESS_NUMBER_PATTERN, "business-number", "사업자등록번호 형태가 포함되어 있습니다."),
            (EMAIL_PATTERN, "email-address", "이메일 주소 형태가 포함되어 있습니다."),
        )
        for pattern, rule, message in pii_rules:
            if pattern.search(text):
                findings.append(Finding(path, rule, message))

    return findings


def scan_path(
    repo_root: Path,
    relative_path: str | Path,
    *,
    text_override: str | object = _FULL_FILE,
) -> list[Finding]:
    path_text = _posix_path(relative_path)
    if not path_text:
        return []

    candidate = repo_root / Path(path_text)
    try:
        resolved_root = repo_root.resolve()
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(resolved_root)
    except (OSError, ValueError):
        return [Finding(path_text, "path-escape", "저장소 바깥을 가리키는 경로입니다.")]

    if candidate.is_symlink():
        return [Finding(path_text, "symlink", "외부 파일을 참조할 수 있는 심볼릭 링크입니다.")]
    if not candidate.exists() or not candidate.is_file():
        return []

    name = candidate.name
    suffix = candidate.suffix.lower()
    scan_suffix = ".env" if name == ".env.example" else suffix
    if SENSITIVE_CONFIG_NAME.search(name) and name not in {".env.example", "secrets_supabase_example.toml"}:
        return [Finding(path_text, "sensitive-config", "비밀정보 설정 파일명입니다.")]

    if suffix in PROTECTED_BINARY_EXTENSIONS:
        if _is_public_data(path_text):
            return []
        if suffix in {".png", ".jpg", ".jpeg"} and _matches_any(path_text, SAFE_STATIC_ASSET_PATHS):
            if not SENSITIVE_DATA_NAME.search(path_text):
                return []
        return [
            Finding(
                path_text,
                "private-binary-artifact",
                "고객 문서·녹취·결과물일 수 있는 바이너리 파일입니다. 비공개 Storage를 사용하세요.",
            )
        ]

    if scan_suffix in {".csv", ".env", ".json", ".jsonl", ".log", ".tsv", ".txt"} and SENSITIVE_DATA_NAME.search(path_text):
        return [
            Finding(
                path_text,
                "private-data-filename",
                "고객 또는 사용자별 원본 데이터로 보이는 파일명입니다.",
            )
        ]

    if scan_suffix not in TEXT_EXTENSIONS and name not in {"Dockerfile", "Procfile"}:
        return []
    if text_override is _FULL_FILE:
        if candidate.stat().st_size > MAX_TEXT_BYTES:
            return [Finding(path_text, "oversized-text", "보안 검사를 건너뛸 만큼 큰 텍스트 파일입니다.")]
        try:
            text = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return [Finding(path_text, "binary-content", "텍스트 확장자이지만 바이너리 내용입니다.")]
        except OSError as exc:
            return [Finding(path_text, "unreadable", f"파일을 안전하게 읽지 못했습니다: {type(exc).__name__}")]
    else:
        text = str(text_override)
        if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
            return [Finding(path_text, "oversized-added-text", "추가된 텍스트가 보안 검사 한도를 초과했습니다.")]
    return _scan_text(path_text, text)


def scan_paths(
    repo_root: Path,
    paths: Iterable[str | Path],
    *,
    text_overrides: Mapping[str, str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    seen: set[str] = set()
    for path in paths:
        normalized = _posix_path(path)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if text_overrides is None:
            findings.extend(scan_path(repo_root, normalized))
        else:
            findings.extend(
                scan_path(
                    repo_root,
                    normalized,
                    text_override=text_overrides.get(normalized, ""),
                )
            )
    return findings


def _run_git(repo_root: Path, arguments: Sequence[str]) -> list[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *arguments],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _run_git_text(repo_root: Path, arguments: Sequence[str]) -> str:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", *arguments],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout


def _extract_added_text(diff_text: str) -> str:
    added: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            continue
        if line.startswith("+"):
            added.append(line[1:])
    return "\n".join(added)


def _added_text_for_path(
    repo_root: Path,
    arguments: Sequence[str],
    path: str,
) -> str:
    diff_text = _run_git_text(
        repo_root,
        [*arguments, "--unified=0", "--no-color", "--", path],
    )
    return _extract_added_text(diff_text)


def _added_text_map(
    repo_root: Path,
    paths: Iterable[str],
    arguments: Sequence[str],
) -> dict[str, str]:
    return {
        path: _added_text_for_path(repo_root, arguments, path)
        for path in paths
    }


def changed_paths_between(repo_root: Path, base: str, head: str) -> list[str]:
    return _run_git(
        repo_root,
        ["diff", "--name-only", "--diff-filter=ACMRT", f"{base}...{head}", "--"],
    )


def changed_paths_in_commit(repo_root: Path, commit: str) -> list[str]:
    return _run_git(
        repo_root,
        ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", commit, "--"],
    )


def staged_paths(repo_root: Path) -> list[str]:
    return _run_git(repo_root, ["diff", "--cached", "--name-only", "--diff-filter=ACMRT", "--"])


def working_tree_paths(repo_root: Path) -> tuple[list[str], list[str]]:
    tracked = _run_git(
        repo_root,
        ["diff", "HEAD", "--name-only", "--diff-filter=ACMRT", "--"],
    )
    untracked = _run_git(repo_root, ["ls-files", "--others", "--exclude-standard", "--"])
    return tracked, untracked


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="새로 변경된 파일의 개인정보·비밀정보 유입을 검사합니다."
    )
    parser.add_argument("--repo-root", default=".", help="Git 저장소 루트")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--base", help="비교 기준 Git SHA (사용 시 --head 필요)")
    mode.add_argument("--commit", help="단일 커밋에서 변경된 파일 검사")
    mode.add_argument("--staged", action="store_true", help="스테이징된 파일 검사")
    mode.add_argument(
        "--working-tree",
        action="store_true",
        help="HEAD 대비 작업 트리의 새 줄과 신규 파일 검사",
    )
    mode.add_argument("--paths", nargs="+", help="명시한 경로만 검사")
    parser.add_argument("--head", help="비교 대상 Git SHA")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    try:
        text_overrides: dict[str, str] | None = None
        untracked_paths: list[str] = []
        if args.base:
            if not args.head:
                raise ValueError("--base 사용 시 --head가 필요합니다.")
            paths = changed_paths_between(repo_root, args.base, args.head)
            text_overrides = _added_text_map(
                repo_root,
                paths,
                ["diff", f"{args.base}...{args.head}"],
            )
        elif args.commit:
            paths = changed_paths_in_commit(repo_root, args.commit)
            text_overrides = _added_text_map(
                repo_root,
                paths,
                ["show", "--format=", args.commit],
            )
        elif args.staged:
            paths = staged_paths(repo_root)
            text_overrides = _added_text_map(
                repo_root,
                paths,
                ["diff", "--cached"],
            )
        elif args.working_tree:
            paths, untracked_paths = working_tree_paths(repo_root)
            text_overrides = _added_text_map(
                repo_root,
                paths,
                ["diff", "HEAD"],
            )
        else:
            paths = list(args.paths or [])
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"privacy-guard 설정 오류: {type(exc).__name__}", file=sys.stderr)
        return 2

    findings = scan_paths(repo_root, paths, text_overrides=text_overrides)
    if untracked_paths:
        findings.extend(scan_paths(repo_root, untracked_paths))
        paths = [*paths, *untracked_paths]
    if findings:
        print("개인정보·비밀정보 유입 가능성이 있는 변경 파일을 차단했습니다.", file=sys.stderr)
        for finding in findings:
            print(f"- {finding.path} [{finding.rule}] {finding.message}", file=sys.stderr)
        print("고객 원본은 비공개 Supabase Storage에 저장하고 Git에는 추가하지 마세요.", file=sys.stderr)
        return 1

    print(f"privacy-guard 통과: 변경 파일 {len(set(map(_posix_path, paths)))}개 검사")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
