from __future__ import annotations

import json
import os
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

KST = ZoneInfo("Asia/Seoul")
OUTPUT_PATH = Path("data/external_policy_programs.json")
METADATA_PATH = Path("data/external_policy_metadata.json")


def now_kst() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=path.parent,
        suffix=".tmp",
    ) as temp:
        json.dump(value, temp, ensure_ascii=False, indent=2, default=str)
        temp_path = Path(temp.name)
    temp_path.replace(path)


def _json_env(name: str) -> dict[str, Any]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": "OASIS-Scheduled-Policy-Sync/7.3.0",
            "Accept": "application/json,application/xml,text/xml,text/plain,*/*",
        }
    )
    return session


def flatten_json(value: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            scalar_count = sum(
                not isinstance(child, (dict, list))
                for child in node.values()
            )
            if scalar_count >= 2:
                results.append(node)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    visit(child)

    visit(value)

    deduped = []
    seen = set()
    for row in results:
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def parse_xml(content: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(content)
    candidates = list(root.findall(".//item"))
    if not candidates:
        candidates = [
            node
            for node in root.iter()
            if list(node)
            and sum(bool((child.text or "").strip()) for child in list(node)) >= 2
        ]

    rows = []
    for node in candidates:
        record = {}
        for child in list(node):
            tag = str(child.tag).split("}")[-1]
            value = "".join(child.itertext()).strip()
            if tag and value:
                record[tag] = value
        if record:
            rows.append(record)
    return rows


def fetch_source(
    source_name: str,
    url_env: str,
    key_env: str,
    params_env: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = os.getenv(url_env, "").strip()
    key = os.getenv(key_env, "").strip()

    if not url:
        return [], {
            "source": source_name,
            "status": "미설정",
            "message": f"{url_env} 미설정",
            "count": 0,
        }

    params = _json_env(params_env)
    key_parameter = str(params.pop("_key_parameter", "serviceKey"))
    if key:
        params.setdefault(key_parameter, key)
    params.setdefault("pageNo", 1)
    params.setdefault("numOfRows", 1000)

    session = build_session()
    started = time.monotonic()
    try:
        response = session.get(
            url,
            params=params,
            timeout=(12, 40),
        )
        elapsed = round(time.monotonic() - started, 2)
        if response.status_code != 200:
            return [], {
                "source": source_name,
                "status": "오류",
                "message": f"HTTP {response.status_code}",
                "count": 0,
                "elapsed_seconds": elapsed,
            }

        try:
            rows = flatten_json(response.json())
        except Exception:
            rows = parse_xml(response.content)

        tagged = [
            {
                "source_name": source_name,
                "raw_data": row,
            }
            for row in rows
            if isinstance(row, dict)
        ]
        return tagged, {
            "source": source_name,
            "status": "정상",
            "message": "새벽 동기화 완료",
            "count": len(tagged),
            "elapsed_seconds": elapsed,
        }
    except Exception as exc:
        return [], {
            "source": source_name,
            "status": "오류",
            "message": f"{type(exc).__name__}: {exc}",
            "count": 0,
        }


def load_existing() -> list[dict[str, Any]]:
    if not OUTPUT_PATH.exists():
        return []
    try:
        value = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except Exception:
        return []


def main() -> int:
    existing = load_existing()
    existing_by_source: dict[str, list[dict[str, Any]]] = {}
    for row in existing:
        source = str(row.get("source_name", "") or "")
        existing_by_source.setdefault(source, []).append(row)

    statuses = []
    final_rows: list[dict[str, Any]] = []

    for source_args in [
        (
            "K-Startup",
            "KSTARTUP_API_URL",
            "KSTARTUP_API_KEY",
            "KSTARTUP_API_PARAMS_JSON",
        ),
        (
            "중진공 OpenAPI",
            "KOSMES_API_URL",
            "KOSMES_API_KEY",
            "KOSMES_API_PARAMS_JSON",
        ),
    ]:
        source_name = source_args[0]
        rows, status = fetch_source(*source_args)
        statuses.append(status)
        if rows:
            final_rows.extend(rows)
        else:
            # 장애·미설정 시 마지막 정상 자료 유지
            preserved = existing_by_source.get(source_name, [])
            final_rows.extend(preserved)
            if preserved:
                status["preserved_count"] = len(preserved)
                status["message"] += " · 마지막 정상자료 유지"

    _atomic_json(OUTPUT_PATH, final_rows)
    _atomic_json(
        METADATA_PATH,
        {
            "generated_at": now_kst(),
            "status": (
                "success"
                if any(item.get("status") == "정상" for item in statuses)
                else "preserved"
            ),
            "record_count": len(final_rows),
            "sources": statuses,
        },
    )
    print(json.dumps(statuses, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
