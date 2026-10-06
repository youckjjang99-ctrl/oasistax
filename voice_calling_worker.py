"""Explicitly enabled, bounded dispatcher. Never automatically retries a call.

Railway worker: python voice_calling_worker.py
Only approved jobs are considered; unknown calls require human reconciliation.
"""
from __future__ import annotations

import argparse
import json
import time
from typing import Any

from voice_calling import VoiceSettings, normalize_phone
from voice_calling_repository import VoiceRepository


_SAFE_CODES = frozenset({
    "CALLING_DISABLED_OR_NOT_READY", "QUEUE_UNAVAILABLE", "PROVIDER_NOT_CONFIGURED",
    "CARRIER_MIGRATION_REQUIRED", "TARGET_CHANGED", "PROVIDER_REJECTED",
    "DISPATCH_UNKNOWN", "PROVIDER_ACCEPTED", "EMPTY", "BUSY", "DAILY_LIMIT",
    "OUTSIDE_HOURS", "WORKER_UNAVAILABLE", "QUEUE_RESPONSE_UNEXPECTED",
})


def _queue(repo: Any, action: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        result = repo.worker(action, payload)
        return result if isinstance(result, dict) else {"ok": False}
    except Exception:
        return {"ok": False}


def _safe_code(value: Any) -> str:
    return value if isinstance(value, str) and value in _SAFE_CODES else "QUEUE_RESPONSE_UNEXPECTED"


def dispatch_once(config: VoiceSettings | None = None, repository: Any = None, provider: Any = None) -> dict[str, Any]:
    settings = config or VoiceSettings.from_environment()
    if not settings.readiness()["ready"]:
        return {"ok": False, "code": "CALLING_DISABLED_OR_NOT_READY"}
    # Import optional telecom dependency only after the kill switch/config check.
    from voice_calling_provider import create_voice_provider, CallRejected
    try:
        repo = repository if repository is not None else VoiceRepository()
    except Exception:
        return {"ok": False, "code": "QUEUE_UNAVAILABLE"}
    try:
        carrier = provider if provider is not None else create_voice_provider(settings)
    except Exception:
        return {"ok": False, "code": "PROVIDER_NOT_CONFIGURED"}
    provider_scope = {"provider": settings.provider} if settings.provider == "clawops" else {}
    claim_options = {"daily_limit": settings.daily_limit, **provider_scope}
    if settings.provider == "clawops" and not settings.clawops_live_verified:
        # Leave real customer approvals untouched while running internal trials.
        claim_options["test_phone_allowlist"] = [normalize_phone(x) for x in settings.clawops_test_numbers]
    reserved = _queue(repo, "claim", claim_options)
    if not reserved.get("ok"):
        return {"ok": False, "code": "QUEUE_UNAVAILABLE"}
    job = reserved.get("job")
    if not isinstance(job, dict) or not job.get("id"):
        return {"ok": True, "code": _safe_code(reserved.get("code", "EMPTY"))}
    if settings.provider == "clawops" and job.get("provider") != "clawops":
        # Old SQL can ignore unknown payload fields. Never dial if the carrier
        # migration was missed, even when all environment flags were enabled.
        _queue(repo, "mark_failed", {"job_id": job["id"], **provider_scope})
        return {"ok": False, "code": "CARRIER_MIGRATION_REQUIRED"}
    # Recheck mutable permission and ownership immediately before provider I/O.
    job_scope = {"job_id": job["id"], **provider_scope}
    checked = _queue(repo, "get_job", job_scope)
    if not checked.get("ok"):
        _queue(repo, "mark_failed", job_scope)
        return {"ok": False, "code": "TARGET_CHANGED"}
    try:
        sent = carrier.start_call(job)
    except CallRejected:
        _queue(repo, "mark_failed", job_scope)
        return {"ok": False, "code": "PROVIDER_REJECTED"}
    except Exception:
        # The number might already be ringing. Never release/requeue the claim.
        _queue(repo, "mark_unknown", job_scope)
        return {"ok": False, "code": "DISPATCH_UNKNOWN"}
    sid = sent.get("provider_call_id") if isinstance(sent, dict) else None
    if not sid:
        _queue(repo, "mark_unknown", job_scope)
        return {"ok": False, "code": "DISPATCH_UNKNOWN"}
    recorded = _queue(repo, "mark_dispatched", {**job_scope, "provider_call_id": sid})
    if not recorded.get("ok"):
        # Stop a known call if we cannot establish a durable accepted state.
        try:
            carrier.hangup(sid)
        except Exception:
            pass
        _queue(repo, "mark_unknown", {**job_scope, "provider_call_id": sid})
        return {"ok": False, "code": "DISPATCH_UNKNOWN"}
    return {"ok": True, "code": "PROVIDER_ACCEPTED"}


def run_dispatch_loop(*, once: bool = False, interval: int = 10, dispatch: Any = None,
                      sleep: Any = None, clock: Any = None, emit: Any = None) -> None:
    """Bound retries of polling, not telephone calls; suppress repeated logs."""
    dispatch = dispatch or dispatch_once
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    emit = emit or (lambda code: print("voice_dispatch", code, flush=True))
    interval = max(5, min(interval, 60))
    failures, last_code, last_report = 0, None, 0.0
    while True:
        try:
            result = dispatch()
            ok = isinstance(result, dict) and result.get("ok") is True
            code = _safe_code(result.get("code") if isinstance(result, dict) else None)
        except Exception:
            ok, code = False, "WORKER_UNAVAILABLE"
        now = clock()
        if code != last_code or now - last_report >= 300 or code == "PROVIDER_ACCEPTED":
            emit(code)
            last_code, last_report = code, now
        if once:
            return
        failures = 0 if ok else min(failures + 1, 5)
        sleep(min(60, interval * (2 ** max(0, failures - 1))))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="OASIS consented AI visit-call dispatcher")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--check-only", action="store_true", help="Show diagnostics without claiming jobs or calling")
    parser.add_argument("--check-network", action="store_true", help="With --check-only: read OpenAI model metadata")
    args = parser.parse_args(argv)
    if args.check_network and not args.check_only:
        parser.error("--check-network requires --check-only")
    if args.check_only:
        from voice_calling_preflight import build_preflight
        print(json.dumps(build_preflight(check_network=args.check_network), ensure_ascii=False, indent=2))
        return
    run_dispatch_loop(once=args.once, interval=args.interval)


if __name__ == "__main__":
    main()
