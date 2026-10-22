"""Shared server-only Supabase configuration resolution and request headers.

Keep legacy variable names available during staged key rotation. Never return
these credentials to a browser, and do not retry a rejected key with an old key.
"""
from __future__ import annotations

from collections.abc import Callable


SERVER_KEY_NAMES = (
    "SUPABASE_SECRET_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_SERVICE_KEY",
    "SUPABASE_KEY",
)


def resolve_server_key(read_secret: Callable[[str], str]) -> str:
    """Use one deterministic alias order for database and private-file access."""
    for name in SERVER_KEY_NAMES:
        value = str(read_secret(name) or "").strip()
        if value:
            return value
    return ""


def server_api_headers(key: str) -> dict[str, str]:
    """Opaque API keys go in apikey only; legacy JWTs keep their Bearer header."""
    value = str(key or "").strip()
    headers = {"apikey": value}
    if value and not value.startswith(("sb_secret_", "sb_publishable_")):
        headers["Authorization"] = f"Bearer {value}"
    return headers
