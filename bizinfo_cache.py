from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from collector import sync_bizinfo_cache


KST = ZoneInfo("Asia/Seoul")
ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT_DIR / "data"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def get_bizinfo_cache_status(data_dir: str | Path | None = None) -> dict[str, Any]:
    base = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    json_path = base / "bizinfo_programs.json"
    metadata_path = base / "bizinfo_metadata.json"
    metadata = _read_json(metadata_path, {})

    status = {
        "exists": json_path.exists(),
        "status": metadata.get("status", "unknown"),
        "generated_at": metadata.get("generated_at", ""),
        "record_count": int(metadata.get("record_count", 0) or 0),
        "successful_pages": int(metadata.get("successful_pages", 0) or 0),
        "failed_pages": int(metadata.get("failed_pages", 0) or 0),
        "message": metadata.get("message", ""),
        "source": metadata.get("source", ""),
    }

    if json_path.exists() and not status["record_count"]:
        records = _read_json(json_path, [])
        status["record_count"] = len(records) if isinstance(records, list) else 0

    if json_path.exists():
        modified = datetime.fromtimestamp(json_path.stat().st_mtime, tz=KST)
        status["file_modified_at"] = modified.isoformat(timespec="seconds")
    else:
        status["file_modified_at"] = ""

    return status


def load_cached_bizinfo_programs(
    data_dir: str | Path | None = None,
) -> pd.DataFrame:
    base = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    json_path = base / "bizinfo_programs.json"
    xlsx_path = base / "기업마당_공고DB.xlsx"

    records = _read_json(json_path, [])
    if isinstance(records, list) and records:
        return pd.DataFrame(records)

    if xlsx_path.exists():
        try:
            return pd.read_excel(xlsx_path)
        except Exception:
            pass

    return pd.DataFrame()


def load_bizinfo_programs_cached(
    api_key: str = "",
    page_count: int = 10,
    allow_live_fallback: bool = True,
    data_dir: str | Path | None = None,
) -> pd.DataFrame:
    base = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
    cached = load_cached_bizinfo_programs(base)

    if not cached.empty:
        status = get_bizinfo_cache_status(base)
        print(
            f"기업마당 내부 DB 사용: {len(cached)}건 "
            f"({status.get('generated_at') or status.get('file_modified_at') or '갱신일 미확인'})"
        )
        return cached

    if not allow_live_fallback:
        print("기업마당 내부 DB가 없어 공고형 매칭을 건너뜁니다.")
        return pd.DataFrame()

    print("기업마당 내부 DB가 없어 최초 동기화를 시도합니다...")
    try:
        sync_bizinfo_cache(
            output_dir=base,
            api_key=api_key or os.getenv("BIZINFO_API_KEY", ""),
            page_count=page_count,
            source="matching-first-run",
            strict=True,
        )
    except Exception as exc:
        print(f"기업마당 최초 동기화 실패: {type(exc).__name__}: {exc}")
        return pd.DataFrame()

    return load_cached_bizinfo_programs(base)
