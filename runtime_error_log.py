from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path
from typing import Any


def write_runtime_error(context: str, exc: BaseException, details: Any = None) -> str:
    """Write a best-effort runtime error log without raising another error."""
    try:
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = log_dir / f"error_{stamp}.log"
        body = [
            f"time={datetime.now().isoformat(timespec='seconds')}",
            f"context={context}",
            f"exception_type={type(exc).__name__}",
            f"exception={exc}",
        ]
        if details is not None:
            body.append(f"details={details!r}")
        body.extend(["", traceback.format_exc()])
        path.write_text("\n".join(body), encoding="utf-8")
        return str(path)
    except Exception:
        return ""
