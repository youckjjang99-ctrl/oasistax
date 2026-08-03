from __future__ import annotations

import re
import traceback
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


REDACTED = "[REDACTED]"
MAX_FIELD_CHARS = 12_000
MAX_TRACEBACK_CHARS = 24_000
MAX_COLLECTION_ITEMS = 100
MAX_SANITIZE_DEPTH = 6

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|api[_-]?secret|secret|password|passwd|token|"
    r"authorization|cookie|credential|session(?:[_-]?(?:id|token|secret))?|"
    r"resident(?:[_-]?(?:no|number))?|identity(?:[_-]?(?:no|number))?|"
    r"business(?:[_-]?(?:no|number))|phone|cellphone|mobile|"
    r"customer(?:[_-]?name)?|company(?:[_-]?name)?|name|address|email|"
    r"memo|notes?|content|consultation|"
    r"주민(?:등록)?번호|사업자(?:등록)?번호|전화번호|휴대전화|인증정보|"
    r"고객명|업체명|상호|성명|이름|주소|이메일|메모|상담(?:내용)?)",
    re.IGNORECASE,
)
_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{10,}")
_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\b"
)
_KNOWN_TOKEN = re.compile(
    r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{20,}|"
    r"github_pat_[A-Za-z0-9_]{25,}|AKIA[0-9A-Z]{16})\b"
)
_SENSITIVE_KEY_VALUE = re.compile(
    r"(?i)(?P<prefix>['\"]?(?:api[_-]?key|api[_-]?secret|secret(?:[_-]?key)?|"
    r"password|passwd|token|authorization|cookie|credential|"
    r"session(?:[_-]?(?:id|token|secret))?|resident(?:[_-]?(?:no|number))?|"
    r"identity(?:[_-]?(?:no|number))?|business(?:[_-]?(?:no|number))|"
    r"phone|cellphone|mobile|customer(?:[_-]?name)?|company(?:[_-]?name)?|"
    r"name|address|email|memo|notes?|content|consultation|"
    r"주민(?:등록)?번호|사업자(?:등록)?번호|전화번호|휴대전화|인증정보|"
    r"고객명|업체명|상호|성명|이름|주소|이메일|메모|상담(?:내용)?)"
    r"['\"]?\s*[:=]\s*)"
    r"(?P<quote>['\"]?)(?P<value>\[[A-Z_]+\]|.*?)(?P=quote)"
    r"(?=(?:[,;}\]\r\n]|$))"
)
_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:api[_-]?key|secret|password|token|authorization|"
    r"session[_-]?(?:id|token))=)[^&#\s]+"
)
_RRN = re.compile(r"(?<!\d)\d{6}[-\s]?[1-8]\d{6}(?!\d)")
_BUSINESS_NUMBER = re.compile(r"(?<!\d)\d{3}[-\s]\d{2}[-\s]\d{5}(?!\d)")
_TEN_DIGIT_IDENTIFIER = re.compile(r"(?<!\d)\d{10}(?!\d)")
_EMAIL = re.compile(
    r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])",
    re.IGNORECASE,
)
_PHONE = re.compile(
    r"(?<!\d)(?:\+?82[-.\s]?)?0?(?:1[016789]|2|[3-6][1-5]|70|80|50[2-8])"
    r"[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"
)
_WINDOWS_PATH = re.compile(r"(?i)\b[A-Z]:[\\/](?:[^\\/\r\n]+[\\/])*[^\\/\r\n]*")
_POSIX_PATH = re.compile(r"(?<![:\w])/(?:[^/\s]+/)+[^/\s]*")


def _bounded(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[TRUNCATED {len(value) - limit} chars]"


def _redact_text(value: Any) -> str:
    text = str(value or "")
    text = _PRIVATE_KEY.sub("[PRIVATE_KEY_REDACTED]", text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _JWT.sub("[JWT_REDACTED]", text)
    text = _KNOWN_TOKEN.sub("[TOKEN_REDACTED]", text)
    text = _SENSITIVE_QUERY.sub(lambda match: match.group(1) + REDACTED, text)
    text = _SENSITIVE_KEY_VALUE.sub(
        lambda match: match.group("prefix") + match.group("quote") + REDACTED + match.group("quote"),
        text,
    )
    text = _RRN.sub("[RRN_REDACTED]", text)
    text = _BUSINESS_NUMBER.sub("[BUSINESS_NUMBER_REDACTED]", text)
    text = _EMAIL.sub("[EMAIL_REDACTED]", text)
    text = _PHONE.sub("[PHONE_REDACTED]", text)
    text = _TEN_DIGIT_IDENTIFIER.sub("[NUMERIC_IDENTIFIER_REDACTED]", text)
    return text


def sanitize_public_text(value: Any) -> str:
    """Return bounded display text with credentials, PII and local paths removed."""
    text = _redact_text(value)
    text = _WINDOWS_PATH.sub("[PATH_REDACTED]", text)
    text = _POSIX_PATH.sub("[PATH_REDACTED]", text)
    return _bounded(text, 1_000)


def safe_public_error(exc: BaseException, fallback: str) -> str:
    """Build a public error without leaking the exception message or file paths."""
    safe_fallback = sanitize_public_text(fallback or "처리 중 오류가 발생했습니다.").strip()
    if not safe_fallback:
        safe_fallback = "처리 중 오류가 발생했습니다."
    return f"{safe_fallback} (오류 유형: {type(exc).__name__})"


def _safe_traceback(exc: BaseException) -> str:
    frames = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
    lines = ["traceback_frames="]
    for frame in frames[-50:]:
        filename = Path(frame.filename).name or "[UNKNOWN_FILE]"
        function_name = sanitize_public_text(frame.name)
        lines.append(f"- file={filename} line={frame.lineno} function={function_name}")
    if len(lines) == 1:
        lines.append("- unavailable")
    return _bounded("\n".join(lines), MAX_TRACEBACK_CHARS)


def _sanitize_details(value: Any, *, key_hint: str = "", depth: int = 0) -> Any:
    if key_hint and _SENSITIVE_KEY.search(key_hint):
        return REDACTED
    if depth >= MAX_SANITIZE_DEPTH:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _bounded(_redact_text(value), MAX_FIELD_CHARS)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"[BINARY {len(value)} bytes]"
    if isinstance(value, Mapping):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_COLLECTION_ITEMS:
                result["[TRUNCATED]"] = len(value) - MAX_COLLECTION_ITEMS
                break
            safe_key = _bounded(_redact_text(key), 200)
            result[safe_key] = _sanitize_details(
                item,
                key_hint=str(key),
                depth=depth + 1,
            )
        return result
    if isinstance(value, Sequence):
        items = list(value[:MAX_COLLECTION_ITEMS])
        sanitized = [
            _sanitize_details(item, depth=depth + 1)
            for item in items
        ]
        if len(value) > MAX_COLLECTION_ITEMS:
            sanitized.append(f"[TRUNCATED {len(value) - MAX_COLLECTION_ITEMS} items]")
        return sanitized
    return _bounded(_redact_text(repr(value)), MAX_FIELD_CHARS)


def write_runtime_error(context: str, exc: BaseException, details: Any = None) -> str:
    """Write a bounded, redacted runtime error log without raising again."""
    try:
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = log_dir / f"error_{stamp}.log"
        body = [
            f"time={datetime.now().isoformat(timespec='seconds')}",
            f"context={_bounded(_redact_text(context), 1_000)}",
            f"exception_type={type(exc).__name__}",
            "exception_message=[REDACTED]",
        ]
        if details is not None:
            safe_details = _sanitize_details(details)
            body.append(
                "details="
                + _bounded(_redact_text(repr(safe_details)), MAX_FIELD_CHARS)
            )
        safe_traceback = _safe_traceback(exc)
        body.extend(["", safe_traceback])
        path.write_text("\n".join(body), encoding="utf-8")
        return str(path)
    except Exception:
        return ""
