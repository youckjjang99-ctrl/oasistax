from __future__ import annotations

import json
import os

from scheduled_employment_contact_enrichment import (
    DAUM_DAILY_SAFE_RECORDS,
    DAUM_DAILY_SAFE_REQUESTS,
    EXIT_DAILY_QUOTA,
    KAKAO_DAILY_SAFE_REQUESTS,
    run_enrichment,
)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def run_phone_pipeline() -> int:
    """Run Kakao first, then Daum Web against Kakao no-match rows.

    The queue transition itself is handled atomically by
    scheduled_employment_contact_enrichment: Kakao no-match and landline-only
    rows move to the mobile-first Daum queue, while a Daum match or no-match
    moves to the terminal ``complete`` stage. Terminal rows are therefore
    never selected again.
    """
    workers = _env_int("EMPLOYMENT_CONTACT_WORKERS", 12)
    batch_size = _env_int("EMPLOYMENT_CONTACT_BATCH_SIZE", 200)
    kakao_request_limit = _env_int(
        "EMPLOYMENT_KAKAO_MAX_REQUESTS",
        KAKAO_DAILY_SAFE_REQUESTS,
    )
    daum_limit = _env_int(
        "EMPLOYMENT_DAUM_MAX_RECORDS",
        DAUM_DAILY_SAFE_RECORDS,
    )
    daum_request_limit = _env_int(
        "EMPLOYMENT_DAUM_MAX_REQUESTS",
        DAUM_DAILY_SAFE_REQUESTS,
    )

    print(
        json.dumps(
            {
                "job": "employment-phone-pipeline",
                "status": "started",
                "order": ["kakao", "daum"],
                "kakao_max_requests": kakao_request_limit,
                "daum_max_records": daum_limit,
                "daum_max_requests": daum_request_limit,
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

    daum_result = run_enrichment(
        stage="phone",
        phone_provider="daum",
        workers=workers,
        batch_size=batch_size,
        max_records=daum_limit,
        max_requests=daum_request_limit,
    )
    print(
        json.dumps(
            {
                "job": "employment-phone-pipeline",
                "status": "finished",
                "kakao_exit_code": kakao_result,
                "daum_exit_code": daum_result,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return daum_result


if __name__ == "__main__":
    raise SystemExit(run_phone_pipeline())
