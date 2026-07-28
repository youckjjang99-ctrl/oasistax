from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import requests


INTEGER_COLUMNS = {
    "workers_2023",
    "workers_2024",
    "workers_2025",
    "growth_2023_2024",
    "growth_2024_2025",
    "growth_2023_2025",
    "workplace_count_2025",
    "source_year_mask",
}


def _row(raw: dict[str, str]) -> dict[str, Any]:
    row: dict[str, Any] = dict(raw)
    for column in INTEGER_COLUMNS:
        row[column] = int(raw[column])
    row["is_new_2025"] = raw["is_new_2025"].lower() in {"1", "true", "yes"}
    return row


def _send(
    function_url: str,
    api_key: str,
    rows: list[dict[str, Any]],
) -> int:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "apikey": api_key,
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = json.dumps({"rows": rows}, ensure_ascii=False).encode("utf-8")
    last_error = ""
    for attempt in range(1, 6):
        try:
            response = requests.post(
                function_url,
                headers=headers,
                data=payload,
                timeout=180,
            )
            if response.ok:
                result = response.json()
                upserted = int(result.get("upserted") or 0)
                if upserted != len(rows):
                    raise RuntimeError(f"저장 건수 불일치: {result}")
                return upserted
            last_error = f"HTTP {response.status_code}: {response.text[:500]}"
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = str(exc)
        if attempt < 5:
            time.sleep(min(30, 2 ** attempt))
    raise RuntimeError(last_error or "알 수 없는 Supabase 저장 오류")


def _batches(
    path: Path,
    batch_size: int,
    skip_rows: int,
) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        batch: list[dict[str, Any]] = []
        row_number = 0
        for raw in reader:
            row_number += 1
            if row_number <= skip_rows:
                continue
            batch.append(_row(raw))
            if len(batch) >= batch_size:
                yield row_number - len(batch) + 1, batch
                batch = []
        if batch:
            yield row_number - len(batch) + 1, batch


def upload(
    path: Path,
    *,
    function_url: str,
    api_key: str,
    batch_size: int,
    workers: int,
    skip_rows: int,
) -> int:
    uploaded = 0
    started = time.monotonic()
    pending: dict[Future[int], tuple[int, int]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for start_row, batch in _batches(path, batch_size, skip_rows):
            future = executor.submit(_send, function_url, api_key, batch)
            pending[future] = (start_row, len(batch))
            if len(pending) < workers * 2:
                continue
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for completed in done:
                start, size = pending.pop(completed)
                try:
                    uploaded += completed.result()
                except Exception as exc:
                    raise RuntimeError(
                        f"{start:,}번째 행부터 {size:,}건 저장 실패: {exc}"
                    ) from exc
                if uploaded % 50_000 == 0:
                    print(
                        json.dumps(
                            {
                                "uploaded": uploaded,
                                "elapsed_seconds": round(
                                    time.monotonic() - started,
                                    1,
                                ),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
        for completed in wait(pending).done:
            start, size = pending[completed]
            try:
                uploaded += completed.result()
            except Exception as exc:
                raise RuntimeError(
                    f"{start:,}번째 행부터 {size:,}건 저장 실패: {exc}"
                ) from exc
    print(
        json.dumps(
            {
                "status": "complete",
                "uploaded": uploaded,
                "skip_rows": skip_rows,
                "elapsed_seconds": round(time.monotonic() - started, 1),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return uploaded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "file",
        type=Path,
        nargs="?",
        default=Path("data/comwel/comwel_annual_compact_2023_2025.csv"),
    )
    parser.add_argument("--batch-size", type=int, default=5_000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--skip-rows", type=int, default=0)
    args = parser.parse_args()

    function_url = os.environ.get("COMWEL_IMPORT_FUNCTION_URL", "").strip()
    api_key = (
        os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
        or os.environ.get("SUPABASE_ANON_KEY", "").strip()
    )
    if not function_url or not api_key:
        raise SystemExit(
            "COMWEL_IMPORT_FUNCTION_URL과 SUPABASE_PUBLISHABLE_KEY가 필요합니다."
        )
    upload(
        args.file,
        function_url=function_url,
        api_key=api_key,
        batch_size=max(100, min(5_000, args.batch_size)),
        workers=max(1, min(8, args.workers)),
        skip_rows=max(0, args.skip_rows),
    )


if __name__ == "__main__":
    main()
