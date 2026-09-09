"""Short-lived, count-only caches; never a source of authorization.

Generations are process-local. Other instances refresh count snapshots within
20 seconds; assignment detail and contact authorization are never cached here.
"""
from __future__ import annotations

from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
import inspect
import threading
import time
from typing import Mapping

from performance_cache import cache_generation, invalidate_cache

TTL_SECONDS = 20.0
_SCOPE = ContextVar("sales_current_read_scope", default=None)
_CACHE = {}
_LOCK = threading.Lock()


def _owner(value):
    return str(value or "").strip().lower()


def validated_count_row(raw, fields):
    """Accept exactly one complete count row, never coerce unknowns to zero."""
    if isinstance(raw, list):
        if len(raw) != 1:
            return None
        raw = raw[0]
    if not isinstance(raw, Mapping):
        return None
    if any(type(raw.get(field)) is not int or raw[field] < 0 for field in fields):
        return None
    return {field: raw[field] for field in fields}


def _load_access(owner):
    # This existing loader performs a fresh DB read; do not replace with is_admin
    # or a session-state role, both of which can be temporarily stale.
    from auth import _load_current_user_access_status
    return _load_current_user_access_status(owner)


def read_scope(owner):
    owner = _owner(owner)
    scope = _SCOPE.get()
    if scope is not None and scope["owner"] == owner and scope.get("checked"):
        return scope
    try:
        user, status = _load_access(owner)
        approved = (bool(owner) and status == "ok" and isinstance(user, Mapping)
                    and _owner(user.get("user_id")) == owner
                    and user.get("status") == "approved")
        role = str(user.get("role") or "member") if approved else ""
    except Exception:
        approved, role = False, ""
    result = {"owner": owner, "role": role, "ok": bool(approved), "checked": True}
    if scope is not None and scope["owner"] == owner:
        scope.update(result)
    return result


def scoped_render(owner_argument):
    """Share one fresh actor check only within the current synchronous render."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def render(*args, **kwargs):
            owner = signature.bind_partial(*args, **kwargs).arguments.get(owner_argument, "")
            parent = _SCOPE.get()
            context = parent if parent is not None and parent["owner"] == _owner(owner) else {
                "owner": _owner(owner), "checked": False}
            token = _SCOPE.set(context)
            try:
                return function(*args, **kwargs)
            finally:
                _SCOPE.reset(token)
        return render
    return decorate


def generation_key(owner, *, inventory=False):
    owner = _owner(owner)
    return (cache_generation("sales_reads", owner),
            cache_generation("sales_admin_safety", "all"),
            cache_generation("sales_inventory", "all") if inventory else 0)


def invalidate_sales_reads(owner="", *, inventory=False, admin_safety=False):
    owner = _owner(owner)
    if owner:
        invalidate_cache("sales_reads", owner)
        invalidate_cache("work_inbox", owner)
    if inventory:
        invalidate_cache("sales_inventory", "all")
    if admin_safety:
        invalidate_cache("sales_admin_safety", "all")


def sales_mutation(*, owner_argument="current_user_id", inventory=False,
                   admin_safety=False, requested_owner=False, positive_key=None,
                   may_affect_other_owner=False):
    """Invalidate at repository success boundaries, including non-UI callers."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def mutate(*args, **kwargs):
            arguments = signature.bind_partial(*args, **kwargs).arguments
            result = function(*args, **kwargs)
            if not isinstance(result, Mapping) or result.get("ok") is not True:
                return result
            if positive_key and not (isinstance(result.get(positive_key), int)
                                     and result[positive_key] > 0):
                return result
            owner = arguments.get(owner_argument, "")
            safety = admin_safety
            if may_affect_other_owner:
                actor_scope = read_scope(arguments.get("current_user_id", ""))
                # These RPCs permit an admin to act on another owner's company,
                # but their result does not reveal the actual owner.
                safety = safety or not actor_scope["ok"] or actor_scope["role"] == "admin"
            if requested_owner:
                request = result.get("request")
                owner = request.get("requested_user_id", "") if isinstance(request, Mapping) else ""
                safety = safety or not bool(_owner(owner))
            invalidate_sales_reads(owner, inventory=inventory, admin_safety=safety)
            return result
        return mutate
    return decorate


def count_summary(namespace, owner, loader, *, inventory=False):
    """Cache only complete, numeric count summaries after fresh access checks."""
    scope = read_scope(owner)
    if not scope["ok"]:
        return {"ok": False, "code": "PERMISSION_DENIED", "metrics": {},
                "message": "현재 계정의 조회 권한을 확인하지 못했습니다."}
    owner = scope["owner"]
    generations = generation_key(owner, inventory=inventory)
    key = (namespace, owner, scope["role"], generations)
    now = time.monotonic()
    with _LOCK:
        entry = _CACHE.get(key)
        if entry and 0 <= now - entry[0] < TTL_SECONDS:
            return deepcopy(entry[1])
    value = loader()
    if not isinstance(value, Mapping) or value.get("ok") is not True:
        return value
    # Deliberately do not persist names, notes, source snapshots, or error text.
    metrics = value.get("metrics")
    if isinstance(metrics, Mapping) and metrics:
        clean = {"ok": True, "metrics": dict(metrics)}
        counts = metrics.values()
    elif all(field in value for field in ("total", "registered", "contracted")):
        clean = {"ok": True, **{field: value[field] for field in ("total", "registered", "contracted")}}
        counts = [clean[field] for field in ("total", "registered", "contracted")]
    else:
        return value
    if (value.get("warning") or value.get("warnings") or value.get("legacy_fallback")
            or not all(type(count) is int and count >= 0 for count in counts)):
        return value
    if generations == generation_key(owner, inventory=inventory):
        with _LOCK:
            if len(_CACHE) >= 256:
                _CACHE.pop(next(iter(_CACHE)), None)
            _CACHE[key] = (now, deepcopy(clean))
    return deepcopy(clean)
