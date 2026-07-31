from __future__ import annotations

import hashlib
import pickle
from pathlib import Path
from typing import Any


def content_digest(*values: Any) -> str:
    """Return a non-reversible cache key for report inputs.

    Report inputs can contain nested dictionaries, pandas objects and dates, so
    using a compact pickle payload is more reliable than coercing everything to
    text.  Only the digest is used as the Streamlit cache key; customer data is
    never written to logs or exposed in the UI.
    """

    try:
        payload = pickle.dumps(values, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        payload = repr(values).encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def file_revision(path: str | Path | None) -> tuple[str, int, int]:
    """Identify a file revision without reading its potentially large bytes."""

    if not path:
        return ("", 0, 0)
    target = Path(path)
    try:
        stat = target.stat()
    except OSError:
        return (str(target), 0, 0)
    return (str(target), int(stat.st_mtime_ns), int(stat.st_size))
