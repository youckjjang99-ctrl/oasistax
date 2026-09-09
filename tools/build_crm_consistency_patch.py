"""Build a source-only overlay ZIP for this release; never upload or deploy."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import subprocess
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
BASE = "4db662cbc5f7ac04a22b118dd1246b54bc2f6e1b"
VERSION = "v9.13.0-crm-consistency"
MANIFEST = "docs/crm-consistency-files-v9.13.0.md"
ALLOWED_EXTENSIONS = {".py", ".md", ".sql", ".bat", ".txt"}
BLOCKED_PARTS = {".git", ".codex", ".streamlit", "tmp", "data", "uploads",
                 "user_data", "downloads", "logs", "__pycache__", "node_modules"}


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT).decode("utf-8")


def main() -> int:
    if git("rev-parse", "HEAD").strip() != BASE:
        raise RuntimeError("Patch builder requires the reviewed, uncommitted release baseline.")
    if (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip() != VERSION:
        raise RuntimeError("Release version mismatch.")
    changed = set(git("diff", "--name-only", "-z", "HEAD", "--").split("\0"))
    changed.update(git("ls-files", "--others", "--exclude-standard", "-z").split("\0"))
    changed.discard("")
    paths = sorted(changed)
    manifest = (ROOT / MANIFEST).read_text(encoding="utf-8")
    expected = {line[3:-1] for line in manifest.splitlines()
                if line.startswith("- `") and line.endswith("`")}
    if set(paths) != expected:
        raise RuntimeError("Manifest differs from changed files; review it before packaging.")
    contents = {}
    for relative in paths:
        name = PurePosixPath(relative)
        source = ROOT.joinpath(*name.parts)
        if (name.is_absolute() or ".." in name.parts or set(name.parts) & BLOCKED_PARTS
                or source.is_symlink() or not source.is_file()
                or not source.resolve().is_relative_to(ROOT)
                or (relative != ".gitignore" and name.suffix not in ALLOWED_EXTENSIONS)):
            raise RuntimeError("Unexpected non-source patch entry.")
        contents[relative] = source.read_bytes()
    subprocess.run([sys.executable, "tools/privacy_guard.py", "--working-tree"], cwd=ROOT, check=True)
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    output = ROOT / "tmp" / "deliverables"
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"OASIS_{VERSION}_patch.zip"
    if archive.exists():
        raise RuntimeError("Existing patch preserved. Choose a new output after reviewing changes.")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for relative, body in contents.items():
            bundle.writestr(relative, body)
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or set(bundle.namelist()) != set(contents):
            raise RuntimeError("Archive integrity verification failed.")
        if any(bundle.read(name) != body for name, body in contents.items()):
            raise RuntimeError("Archive content verification failed.")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(f"Verified {len(contents)} source files; no top-level wrapper folder.")
    print(f"SHA256 {digest}")
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
