from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


BIZINFO_URL = "https://www.bizinfo.go.kr/uss/rss/bizinfoApi.do"
DEFAULT_PAGE_COUNT = 10
DEFAULT_PAGE_SIZE = 100
KST = ZoneInfo("Asia/Seoul")


def now_kst_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": "OASIS-Internal-Funding-Collector/3.5.0",
        "Accept": "application/json,text/plain,*/*",
    })
    return session


def _dedupe_key(row: dict[str, Any]) -> tuple[str, ...]:
    identifier = str(
        row.get("pblancId")
        or row.get("pblancSn")
        or row.get("annnRegistNo")
        or ""
    ).strip()
    if identifier:
        return ("id", identifier)

    return (
        "fields",
        str(row.get("pblancNm", "")).strip(),
        str(row.get("pblancUrl", "")).strip(),
        str(row.get("reqstBeginEndDe", "")).strip(),
        str(row.get("jrsdInsttNm", "")).strip(),
    )


def collect_bizinfo(
    api_key: str,
    page_count: int = DEFAULT_PAGE_COUNT,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout_connect: int = 10,
    timeout_read: int = 35,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not api_key:
        raise RuntimeError(
            "BIZINFO_API_KEY가 없습니다. GitHub Actions 또는 Streamlit Secrets에 등록해주세요."
        )

    session = build_session()
    collected: list[dict[str, Any]] = []
    page_logs: list[dict[str, Any]] = []

    for page in range(1, page_count + 1):
        params = {
            "crtfcKey": api_key,
            "serviceKey": api_key,
            "dataType": "json",
            "searchCnt": str(page_size),
            "pageUnit": str(page_size),
            "pageIndex": str(page),
        }
        started = time.monotonic()
        try:
            response = session.get(
                BIZINFO_URL,
                params=params,
                timeout=(timeout_connect, timeout_read),
            )
            elapsed = round(time.monotonic() - started, 2)

            if response.status_code != 200:
                page_logs.append({
                    "page": page,
                    "success": False,
                    "status_code": response.status_code,
                    "elapsed_seconds": elapsed,
                    "message": f"HTTP {response.status_code}",
                })
                continue

            try:
                payload = response.json()
            except ValueError as exc:
                page_logs.append({
                    "page": page,
                    "success": False,
                    "status_code": response.status_code,
                    "elapsed_seconds": elapsed,
                    "message": f"JSON 변환 실패: {exc}",
                })
                continue

            rows = payload.get("jsonArray")
            if not isinstance(rows, list):
                page_logs.append({
                    "page": page,
                    "success": False,
                    "status_code": response.status_code,
                    "elapsed_seconds": elapsed,
                    "message": "응답에 jsonArray 목록이 없습니다.",
                })
                continue

            valid_rows = [row for row in rows if isinstance(row, dict)]
            collected.extend(valid_rows)
            page_logs.append({
                "page": page,
                "success": True,
                "status_code": response.status_code,
                "elapsed_seconds": elapsed,
                "row_count": len(valid_rows),
                "message": "정상",
            })

            if len(valid_rows) < page_size:
                break

        except requests.RequestException as exc:
            page_logs.append({
                "page": page,
                "success": False,
                "status_code": None,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "message": f"{type(exc).__name__}: {exc}",
            })

    deduped: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in collected:
        deduped[_dedupe_key(row)] = row

    return list(deduped.values()), page_logs


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp"
    ) as temp:
        temp.write(text)
        temp_path = Path(temp.name)
    temp_path.replace(path)


def _atomic_write_excel(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp.xlsx")
    try:
        pd.DataFrame(rows).to_excel(temp_path, index=False)
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def sync_bizinfo_cache(
    output_dir: str | Path = "data",
    api_key: str | None = None,
    page_count: int = DEFAULT_PAGE_COUNT,
    page_size: int = DEFAULT_PAGE_SIZE,
    source: str = "manual",
    strict: bool = False,
) -> dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    resolved_key = (
        api_key
        or os.getenv("BIZINFO_API_KEY", "").strip()
        or os.getenv("BIZINFO_LEGACY_API_KEY", "").strip()
    )

    json_path = output_path / "bizinfo_programs.json"
    xlsx_path = output_path / "기업마당_공고DB.xlsx"
    metadata_path = output_path / "bizinfo_metadata.json"

    page_logs: list[dict[str, Any]] = []
    try:
        rows, page_logs = collect_bizinfo(
            api_key=resolved_key,
            page_count=page_count,
            page_size=page_size,
        )
        if not rows:
            raise RuntimeError("유효한 기업마당 공고를 한 건도 가져오지 못했습니다.")

        metadata = {
            "status": "success",
            "generated_at": now_kst_iso(),
            "source": source,
            "record_count": len(rows),
            "successful_pages": sum(1 for item in page_logs if item.get("success")),
            "failed_pages": sum(1 for item in page_logs if not item.get("success")),
            "page_logs": page_logs,
            "json_file": json_path.name,
            "xlsx_file": xlsx_path.name,
        }

        _atomic_write_text(
            json_path,
            json.dumps(rows, ensure_ascii=False, indent=2, default=str),
        )
        _atomic_write_excel(xlsx_path, rows)
        _atomic_write_text(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2, default=str),
        )
        return metadata

    except Exception as exc:
        metadata = {
            "status": "failed",
            "generated_at": now_kst_iso(),
            "source": source,
            "message": f"{type(exc).__name__}: {exc}",
            "existing_cache_preserved": json_path.exists(),
            "page_logs": page_logs,
        }
        _atomic_write_text(
            metadata_path,
            json.dumps(metadata, ensure_ascii=False, indent=2, default=str),
        )
        if strict or not json_path.exists():
            raise
        return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="기업마당 공고DB 동기화")
    parser.add_argument("--output-dir", default="data")
    parser.add_argument("--pages", type=int, default=DEFAULT_PAGE_COUNT)
    parser.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    parser.add_argument("--source", default="manual")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    try:
        result = sync_bizinfo_cache(
            output_dir=args.output_dir,
            page_count=args.pages,
            page_size=args.page_size,
            source=args.source,
            strict=args.strict,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "success" else 1
    except Exception as exc:
        print(f"기업마당 DB 동기화 실패: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
