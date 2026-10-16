"""Build the reviewed source-only v9.14.3 overlay; never migrate or deploy."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "b9eb3eaa3754ace9ff53762c4939c46fab599d98"
VERSION = "v9.14.3-ai-visit-calling-admin-catalog"
MANIFEST = "docs/ai-visit-calling-admin-files-v9.14.3.md"
BLOCKED_PARTS = {".git", ".codex", ".streamlit", "tmp", "data", "uploads",
                 "user_data", "downloads", "logs", "__pycache__", "node_modules"}
ALLOWED_EXTENSIONS = {".py", ".md", ".sql", ".bat", ".txt", ".mjs"}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT).decode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if git("rev-parse", "HEAD").strip() != BASE:
        raise RuntimeError("The reviewed uncommitted baseline is required.")
    if (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip() != VERSION:
        raise RuntimeError("Release version mismatch.")
    changed = set(git("diff", "--name-only", "-z", "HEAD", "--").split("\0"))
    changed.update(git("ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    changed.discard("")
    expected = {line[3:-1] for line in (ROOT / MANIFEST).read_text(encoding="utf-8").splitlines()
                if line.startswith("- `") and line.endswith("`")}
    if changed != expected:
        raise RuntimeError("Changed files differ from the reviewed source manifest.")
    contents = {}
    for relative in sorted(changed):
        name = PurePosixPath(relative)
        source = ROOT.joinpath(*name.parts)
        if (name.is_absolute() or ".." in name.parts or set(name.parts) & BLOCKED_PARTS
                or name.suffix not in ALLOWED_EXTENSIONS or source.is_symlink()
                or not source.is_file() or not source.resolve().is_relative_to(ROOT)):
            raise RuntimeError("Unexpected non-source patch entry.")
        contents[relative] = source.read_bytes()
    subprocess.run([sys.executable, "tools/privacy_guard.py", "--working-tree"], cwd=ROOT, check=True)
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    archive = args.output.resolve()
    if archive.suffix.lower() != ".zip" or archive.exists():
        raise RuntimeError("A new ZIP output path is required; existing files are preserved.")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for relative, body in contents.items():
            bundle.writestr(relative, body)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or set(bundle.namelist()) != set(contents):
            raise RuntimeError("Archive integrity check failed.")
        if any(bundle.read(name) != body for name, body in contents.items()):
            raise RuntimeError("Archive content check failed.")
    print(f"Verified {len(contents)} source files, no wrapper folder.")
    print("SHA256 " + hashlib.sha256(archive.read_bytes()).hexdigest())
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
