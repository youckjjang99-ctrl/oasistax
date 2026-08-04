from __future__ import annotations

import json
import os

from scheduled_employment_contact_enrichment import (
    EXIT_DAILY_QUOTA,
    KAKAO_DAILY_SAFE_REQUESTS,
    NAVER_DAILY_SAFE_RECORDS,
    run_enrichment,
)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def run_phone_pipeline() -> int:
    """Run Kakao first, then run Naver only against Kakao no-match rows.

    The queue transition itself is handled atomically by
    scheduled_employment_contact_enrichment: Kakao no-match rows move to the
    Naver queue, while a Naver match or no-match moves to the terminal
    ``complete`` stage. Terminal rows are therefore never selected again.
    """
    workers = _env_int("EMPLOYMENT_CONTACT_WORKERS", 12)
    batch_size = _env_int("EMPLOYMENT_CONTACT_BATCH_SIZE", 200)
    kakao_request_limit = _env_int(
        "EMPLOYMENT_KAKAO_MAX_REQUESTS",
        KAKAO_DAILY_SAFE_REQUESTS,
    )
    naver_limit = _env_int(
        "EMPLOYMENT_NAVER_MAX_RECORDS",
        NAVER_DAILY_SAFE_RECORDS,
    )

    print(
        json.dumps(
            {
                "job": "employment-phone-pipeline",
                "status": "started",
                "order": ["kakao", "naver"],
                "kakao_max_requests": kakao_request_limit,
                "naver_max_records": naver_limit,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    kakao_result = run_enrichment(
        stage="phone",
        phone_provider="kakao",
        workers=workers,
        batch_size=batch_size,
        max_records=0,
        max_requests=kakao_request_limit,
    )
    if kakao_result not in {0, EXIT_DAILY_QUOTA}:
        return kakao_result

    naver_result = run_enrichment(
        stage="phone",
        phone_provider="naver",
        workers=workers,
        batch_size=batch_size,
        max_records=naver_limit,
    )
    print(
        json.dumps(
            {
                "job": "employment-phone-pipeline",
                "status": "finished",
                "kakao_exit_code": kakao_result,
                "naver_exit_code": naver_result,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return naver_result


if __name__ == "__main__":
    raise SystemExit(run_phone_pipeline())
