"""Bounded snapshot payload cache with a fresh owner/version check on every hit.

Only the large JSON payload is cached. A cached row is never an authorization
decision: its ID, owner and modification timestamp are read from the server on
every reuse. Missing rows, malformed responses and errors are not cached.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
import threading
import time
from typing import Callable
import uuid

from performance_cache import cache_generation, invalidate_cache

SNAPSHOT_CACHE_TTL = 20
SNAPSHOT_CACHE_LIMIT = 128
_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_LOCK = threading.Lock()


def invalidate_snapshot_reads(owner: str) -> None:
    invalidate_cache("snapshot_reads", str(owner).strip())


def clear_snapshot_cache() -> None:
    """Drop transient payloads only; used by isolated tests and diagnostics."""
    with _LOCK:
        _CACHE.clear()


def _identity(row: dict, owner: str) -> tuple[str, str]:
    if not isinstance(row, dict) or row.get("owner_user_id") != owner:
        raise RuntimeError("snapshot_owner_unconfirmed")
    try:
        row_id = str(uuid.UUID(str(row["id"])))
        stamp = str(row["updated_at"])
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp_timezone_required")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise RuntimeError("snapshot_identity_unconfirmed") from exc
    return row_id, stamp


def _forget(key: tuple) -> None:
    with _LOCK:
        _CACHE.pop(key, None)


def read_snapshot(database, table: str, owner: str, logical_key: tuple,
                  field: str, fetch_row: Callable[[], dict], *,
                  cache_row: Callable[[dict], bool] | None = None) -> dict:
    """Return a detached JSON snapshot; never cache an empty/error outcome."""
    owner = str(owner or "").strip()
    if not owner:
        raise ValueError("snapshot_owner_required")
    project = str(getattr(getattr(database, "config", None), "url", "") or "")
    key = (project, owner, table, *logical_key)
    generation = cache_generation("snapshot_reads", owner)
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and (cached["generation"] != generation
                       or now - cached["at"] >= SNAPSHOT_CACHE_TTL):
            _CACHE.pop(key, None)
            cached = None
        cached = deepcopy(cached)
    if cached:
        try:
            metadata = database.select(
                table, filters={"owner_user_id": owner, "id": cached["identity"][0]},
                columns="id,owner_user_id,updated_at", limit=1,
            )
            if not isinstance(metadata, list) or len(metadata) > 1:
                raise RuntimeError("snapshot_metadata_unconfirmed")
            if metadata and _identity(metadata[0], owner) == cached["identity"]:
                # An in-process write may have completed during the HTTP call.
                if cache_generation("snapshot_reads", owner) == generation:
                    return deepcopy(cached["data"])
            _forget(key)
        except Exception:
            _forget(key)
            raise

    row = fetch_row()
    if not row:
        return {}
    identity = _identity(row, owner)
    data = row.get(field)
    if not isinstance(data, dict):
        raise RuntimeError("snapshot_payload_unconfirmed")
    # Legacy/name-based fallbacks can become ambiguous when another row is
    # inserted without updating the old row. Only exact unique-key lookups may
    # rely on that row's metadata to revalidate the logical identity.
    eligible = cache_row is None or cache_row(row)
    if project and eligible and cache_generation("snapshot_reads", owner) == generation:
        with _LOCK:
            _CACHE[key] = {"identity": identity, "data": deepcopy(data),
                           "generation": generation, "at": time.monotonic()}
            _CACHE.move_to_end(key)
            while len(_CACHE) > SNAPSHOT_CACHE_LIMIT:
                _CACHE.popitem(last=False)
    return deepcopy(data)
