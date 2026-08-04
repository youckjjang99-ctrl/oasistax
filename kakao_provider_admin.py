from __future__ import annotations

import argparse
import json
from typing import Any, Sequence

import kakao_provider_runtime


def _safe_timestamp(value: Any) -> str:
    parsed = kakao_provider_runtime.parse_timestamp(value)
    return parsed.isoformat() if parsed else ""


def _safe_status_payload(state: dict[str, Any]) -> dict[str, Any]:
    guard_state = str(state.get("state") or "").strip().lower()
    if guard_state not in {
        kakao_provider_runtime.GUARD_STATE_READY,
        kakao_provider_runtime.GUARD_STATE_BLOCKED,
        kakao_provider_runtime.GUARD_STATE_RESUME_APPROVED,
    }:
        guard_state = "unavailable"
    reason = str(state.get("guard_reason") or "").strip().upper()
    if reason and reason not in kakao_provider_runtime.GUARD_REASONS:
        reason = "PROVIDER_GUARD"
    source_job = str(state.get("source_job") or "").strip().lower()
    if source_job not in kakao_provider_runtime.GUARD_SOURCE_JOBS:
        source_job = ""
    return {
        "provider": "kakao",
        "state": guard_state,
        "generation": max(0, int(state.get("guard_generation") or 0)),
        "reason": reason,
        "source_job": source_job,
        "observed_count": max(0, int(state.get("observed_count") or 0)),
        "matched_count": max(0, int(state.get("matched_count") or 0)),
        "tripped_at": _safe_timestamp(state.get("tripped_at")),
        "approved_at": _safe_timestamp(state.get("approved_at")),
        "resumed_at": _safe_timestamp(state.get("resumed_at")),
    }


def _print_status(state: dict[str, Any], *, action: str) -> None:
    print(
        json.dumps(
            {
                "job": "kakao-provider-admin",
                "action": action,
                **_safe_status_payload(state),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def _run_preflight() -> int:
    lease_token = kakao_provider_runtime.new_lease_token()
    acquired = kakao_provider_runtime.acquire_lease(lease_token)
    if not acquired:
        print(
            "kakao-provider-admin preflight=lease-unavailable",
            flush=True,
        )
        return 2
    try:
        result = kakao_provider_runtime.test_connection_and_record()
    finally:
        if not kakao_provider_runtime.release_lease(lease_token):
            raise RuntimeError("provider lease release failed")
    ok = bool(result.get("ok"))
    category = kakao_provider_runtime.safe_connection_category(
        result.get("category"),
        ok=ok,
    )
    safe_code = (
        ""
        if ok
        else kakao_provider_runtime.safe_error_code(
            result.get("safe_error_code")
        )
    )
    print(
        json.dumps(
            {
                "job": "kakao-provider-admin",
                "action": "preflight",
                "provider": "kakao",
                "ok": ok,
                "category": category,
                "safe_error_code": safe_code,
                "request_count": max(
                    0,
                    int(result.get("request_count") or 0),
                ),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0 if ok else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly approve the Kakao provider guard.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="Show the safe provider guard state.")
    commands.add_parser(
        "preflight",
        help="Run one safe Kakao HTTP connection check.",
    )

    approve = commands.add_parser(
        "approve",
        help="Approve one exact blocked guard generation.",
    )
    approve.add_argument("--generation", type=int, required=True)
    approve.add_argument("--confirm", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            state = kakao_provider_runtime.get_guard_state()
            _print_status(state, action="status")
            return 0
        if args.command == "preflight":
            return _run_preflight()

        approved = kakao_provider_runtime.approve_guard(
            args.generation,
            args.confirm,
        )
        state = kakao_provider_runtime.get_guard_state()
        _print_status(
            state,
            action="approve" if approved else "approve_rejected",
        )
        return 0 if approved else 2
    except (TypeError, ValueError):
        print(
            "kakao-provider-admin status=invalid-request",
            flush=True,
        )
        return 2
    except Exception:
        print(
            "kakao-provider-admin status=unavailable",
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
