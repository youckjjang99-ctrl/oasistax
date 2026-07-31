"""Small, process-local cache generations for user-scoped read caches.

The generation value is intentionally kept separate from the cached payload.
Writers increment only the affected namespace/user pair, so unrelated users do
not lose warm caches after a save.
"""

from __future__ import annotations

import threading


_GENERATIONS: dict[tuple[str, str], int] = {}
_LOCK = threading.Lock()


def _key(namespace: str, scope: str) -> tuple[str, str]:
    return (str(namespace or "default"), str(scope or "default"))


def cache_generation(namespace: str, scope: str) -> int:
    """Return the current generation for one cache namespace and scope."""
    with _LOCK:
        return int(_GENERATIONS.get(_key(namespace, scope), 0))


def invalidate_cache(namespace: str, scope: str) -> int:
    """Invalidate only the requested namespace/scope and return its version."""
    key = _key(namespace, scope)
    with _LOCK:
        next_generation = int(_GENERATIONS.get(key, 0)) + 1
        _GENERATIONS[key] = next_generation
        return next_generation
