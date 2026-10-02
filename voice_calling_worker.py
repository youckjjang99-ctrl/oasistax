"""Explicitly enabled, bounded dispatcher. Never automatically retries a call.

Railway worker: python voice_calling_worker.py
Only approved jobs are considered; unknown calls require human reconciliation.
"""
from __future__ import annotations

import argparse
import time
from typing import Any

from voice_calling import VoiceSettings
from voice_calling_repository import VoiceRepository


def dispatch_once(config: VoiceSettings | None = None, repository: Any = None, provider: Any = None) -> dict[str, Any]:
    settings = config or VoiceSettings.from_environment()
    if not settings.readiness()["ready"]:
        return {"ok": False, "code": "CALLING_DISABLED_OR_NOT_READY"}
    # Import optional telecom dependency only after the kill switch/config check.
    from voice_calling_provider import TwilioVoiceProvider, CallRejected
    repo = repository if repository is not None else VoiceRepository()
    carrier = provider if provider is not None else TwilioVoiceProvider(settings)
    reserved = repo.worker("claim", {"daily_limit": settings.daily_limit})
    if not reserved.get("ok"):
        return {"ok": False, "code": "QUEUE_UNAVAILABLE"}
    job = reserved.get("job")
    if not isinstance(job, dict) or not job.get("id"):
        return {"ok": True, "code": reserved.get("code", "EMPTY")}
    # Recheck mutable permission and ownership immediately before provider I/O.
    checked = repo.worker("get_job", {"job_id": job["id"]})
    if not checked.get("ok"):
        repo.worker("mark_failed", {"job_id": job["id"]})
        return {"ok": False, "code": "TARGET_CHANGED"}
    try:
        sent = carrier.start_call(job)
    except CallRejected:
        repo.worker("mark_failed", {"job_id": job["id"]})
        return {"ok": False, "code": "PROVIDER_REJECTED"}
    except Exception:
        # The number might already be ringing. Never release/requeue the claim.
        repo.worker("mark_unknown", {"job_id": job["id"]})
        return {"ok": False, "code": "DISPATCH_UNKNOWN"}
    sid = sent.get("provider_call_id") if isinstance(sent, dict) else None
    if not sid:
        repo.worker("mark_unknown", {"job_id": job["id"]})
        return {"ok": False, "code": "DISPATCH_UNKNOWN"}
    recorded = repo.worker("mark_dispatched", {"job_id": job["id"], "provider_call_id": sid})
    if not recorded.get("ok"):
        # Stop a known call if we cannot establish a durable accepted state.
        try:
            carrier.hangup(sid)
        except Exception:
            pass
        repo.worker("mark_unknown", {"job_id": job["id"], "provider_call_id": sid})
        return {"ok": False, "code": "DISPATCH_UNKNOWN"}
    return {"ok": True, "code": "PROVIDER_ACCEPTED"}


def main() -> None:
    parser = argparse.ArgumentParser(description="OASIS consented AI visit-call dispatcher")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args()
    while True:
        result = dispatch_once()
        # Deliberately omit job IDs, company names, phone numbers and errors.
        print("voice_dispatch", result["code"], flush=True)
        if args.once:
            return
        time.sleep(max(5, min(args.interval, 60)))


if __name__ == "__main__":
    main()
