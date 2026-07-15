from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from cloud_db import CloudDatabase, cloud_is_configured
from utils import ROOT_DIR

TABLE_POLICY_REPOSITORY = "oasis_policy_repository"
SEED_PATH = ROOT_DIR / "data" / "internal_policy_seed.json"
LOCAL_CACHE_PATH = ROOT_DIR / "data" / "internal_policy_cache.json"
REFRESH_STATE_PATH = ROOT_DIR / "data" / "internal_policy_refresh_state.json"
BIZINFO_CACHE_PATH = ROOT_DIR / "data" / "bizinfo_programs.json"
BIZINFO_METADATA_PATH = ROOT_DIR / "data" / "bizinfo_metadata.json"
EXTERNAL_CACHE_PATH = ROOT_DIR / "data" / "external_policy_programs.json"
EXTERNAL_METADATA_PATH = ROOT_DIR / "data" / "external_policy_metadata.json"
DEFAULT_REFRESH_HOURS = 24


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat"}:
        return ""
    return text


def _secret(name: str, default: str = "") -> str:
    value = os.environ.get(name, "")
    if value:
        return value.strip()
    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return default


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _normalize_record(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw_data", {})
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    title = _clean(row.get("title"))
    agency = _clean(row.get("agency"))
    source_type = _clean(row.get("source_type")) or "bizinfo"
    record_id = _clean(row.get("record_id")) or hashlib.sha256(
        f"{source_type}|{agency}|{title}".encode("utf-8")
    ).hexdigest()[:24]
    return {
        "record_id": record_id,
        "source_type": source_type,
        "source_name": _clean(row.get("source_name")) or source_type,
        "title": title,
        "agency": agency,
        "active": bool(row.get("active", True)),
        "raw_data": raw if isinstance(raw, dict) else {},
        "updated_at": _clean(row.get("updated_at")),
    }


def _seed_records() -> list[dict[str, Any]]:
    data = _load_json(SEED_PATH, {})
    rows = data.get("records", []) if isinstance(data, dict) else []
    return [_normalize_record(row) for row in rows if isinstance(row, dict)]


def ensure_seed_loaded() -> dict[str, Any]:
    seed = _seed_records()
    if not seed:
        return {"loaded": 0, "cloud": False, "message": "내부 정책DB 초기자료가 없습니다."}
    cached = _load_json(LOCAL_CACHE_PATH, [])
    if not isinstance(cached, list) or not cached:
        _save_json(LOCAL_CACHE_PATH, seed)
    if not cloud_is_configured():
        return {"loaded": len(seed), "cloud": False, "message": f"로컬 내부 정책DB {len(seed)}건 사용"}
    try:
        now = datetime.now().isoformat(timespec="seconds")
        CloudDatabase().upsert(
            TABLE_POLICY_REPOSITORY,
            [{**row, "updated_at": now} for row in seed],
            "record_id",
        )
        return {"loaded": len(seed), "cloud": True, "message": f"Supabase 내부 정책DB {len(seed)}건 확인"}
    except Exception as exc:
        return {"loaded": len(seed), "cloud": False, "message": f"클라우드 동기화 실패, 로컬 DB 사용: {exc}"}



def _first_value(row: dict[str, Any], keys: list[str]) -> str:
    for key in keys:
        value = _clean(row.get(key))
        if value:
            return value
    return ""


def _scheduled_cache_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    now = datetime.now().isoformat(timespec="seconds")

    bizinfo_rows = _load_json(BIZINFO_CACHE_PATH, [])
    if isinstance(bizinfo_rows, list):
        for raw in bizinfo_rows:
            if not isinstance(raw, dict):
                continue
            title = _first_value(
                raw,
                [
                    "pblancNm", "pblanc_nm", "biz_pbanc_nm",
                    "공고명", "사업명", "지원사업명",
                ],
            )
            if not title:
                continue
            agency = _first_value(
                raw,
                [
                    "jrsdInsttNm", "excInsttNm", "기관명",
                    "주관기관", "수행기관명",
                ],
            )
            url = _first_value(
                raw,
                ["detailUrl", "pblancUrl", "공고URL", "url"],
            )
            rid = hashlib.sha256(
                f"bizinfo|{agency}|{title}|{url}".encode("utf-8")
            ).hexdigest()[:24]
            records.append(
                {
                    "record_id": rid,
                    "source_type": "bizinfo",
                    "source_name": "기업마당 새벽동기화",
                    "title": title,
                    "agency": agency,
                    "active": True,
                    "raw_data": raw,
                    "updated_at": now,
                }
            )

    external_rows = _load_json(EXTERNAL_CACHE_PATH, [])
    if isinstance(external_rows, list):
        for wrapper in external_rows:
            if not isinstance(wrapper, dict):
                continue
            raw = wrapper.get("raw_data", {})
            if not isinstance(raw, dict):
                continue
            source_name = _clean(wrapper.get("source_name")) or "외부 새벽동기화"
            title = _first_value(
                raw,
                [
                    "biz_pbanc_nm", "pblancNm", "pblanc_nm",
                    "공고명", "사업명", "지원사업명",
                    "제도명", "상품명", "title",
                ],
            )
            if not title:
                continue
            agency = _first_value(
                raw,
                [
                    "기관명", "주관기관", "수행기관명",
                    "jrsdInsttNm", "excInsttNm",
                    "agency", "organization",
                ],
            )
            url = _first_value(
                raw,
                ["공고URL", "detailUrl", "pblancUrl", "url"],
            )
            source_type = (
                "kstartup"
                if "startup" in source_name.lower()
                else "kosmes"
                if "중진공" in source_name
                else "external"
            )
            rid = hashlib.sha256(
                f"{source_type}|{agency}|{title}|{url}".encode("utf-8")
            ).hexdigest()[:24]
            records.append(
                {
                    "record_id": rid,
                    "source_type": source_type,
                    "source_name": f"{source_name} 새벽동기화",
                    "title": title,
                    "agency": agency,
                    "active": True,
                    "raw_data": raw,
                    "updated_at": now,
                }
            )

    return records


def scheduled_sync_status() -> dict[str, Any]:
    biz_meta = _load_json(BIZINFO_METADATA_PATH, {})
    ext_meta = _load_json(EXTERNAL_METADATA_PATH, {})
    return {
        "bizinfo": biz_meta if isinstance(biz_meta, dict) else {},
        "external": ext_meta if isinstance(ext_meta, dict) else {},
    }


def load_repository_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seed_result = ensure_seed_loaded()
    scheduled_rows = _scheduled_cache_records()
    rows = []
    source = "앱 내부 정책DB"
    if cloud_is_configured():
        try:
            rows = CloudDatabase().select(
                TABLE_POLICY_REPOSITORY,
                filters={"active": "true"},
                order="updated_at.desc",
                limit=5000,
            )
            rows = [_normalize_record(row) for row in rows if isinstance(row, dict)]
            source = "Supabase 내부 정책DB"
        except Exception:
            rows = []
    cached = _load_json(LOCAL_CACHE_PATH, [])
    cached_rows = [
        _normalize_record(row)
        for row in cached
        if isinstance(row, dict)
    ]
    combined = {
        row["record_id"]: row
        for row in cached_rows + rows + scheduled_rows
        if row.get("record_id")
    }
    rows = [
        row
        for row in combined.values()
        if row.get("active") and row.get("title")
    ]
    _save_json(LOCAL_CACHE_PATH, rows)

    if scheduled_rows and cloud_is_configured():
        try:
            CloudDatabase().upsert(
                TABLE_POLICY_REPOSITORY,
                scheduled_rows,
                "record_id",
            )
        except Exception:
            pass
    return rows, {
        "source": "내부 통합 정책DB",
        "status": "정상" if rows else "자료없음",
        "message": (
            f"{source} · 새벽동기화 {len(scheduled_rows)}건 · "
            f"{seed_result.get('message','')}"
        ),
        "count": len(rows),
    }


def _flatten_json(value: Any) -> list[dict[str, Any]]:
    result = []
    def visit(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                visit(item)
        elif isinstance(node, dict):
            keys = {str(k).lower() for k in node}
            if keys & {"pblancnm","pblanc_nm","biz_pbanc_nm","공고명","사업명","지원사업명"}:
                result.append(node)
            for child in node.values():
                if isinstance(child, (dict, list)):
                    visit(child)
    visit(value)
    return result


def fetch_bizinfo_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = _secret("BIZINFO_API_URL")
    if not url:
        return [], {"source":"기업마당 API","status":"미설정","message":"BIZINFO_API_URL 미설정","count":0}
    try:
        params = json.loads(_secret("BIZINFO_API_PARAMS_JSON", "{}"))
        if not isinstance(params, dict):
            params = {}
    except Exception:
        params = {}
    key = _secret("BIZINFO_API_KEY")
    if key:
        params.setdefault(str(params.pop("_key_parameter", "serviceKey")), key)
    params.setdefault("pageNo", 1)
    params.setdefault("numOfRows", 1000)
    params.setdefault("type", "json")
    try:
        response = requests.get(url, params=params, timeout=45)
        if not response.ok:
            return [], {"source":"기업마당 API","status":"오류","message":f"HTTP {response.status_code}: {response.text[:300]}","count":0}
        raw_items = _flatten_json(response.json())
        now = datetime.now().isoformat(timespec="seconds")
        records = []
        for raw in raw_items:
            title = _clean(raw.get("pblancNm") or raw.get("pblanc_nm") or raw.get("biz_pbanc_nm") or raw.get("공고명") or raw.get("사업명") or raw.get("지원사업명"))
            if not title:
                continue
            agency = _clean(raw.get("jrsdInsttNm") or raw.get("excInsttNm") or raw.get("기관명") or raw.get("주관기관"))
            url_value = _clean(raw.get("detailUrl") or raw.get("pblancUrl") or raw.get("공고URL"))
            rid = hashlib.sha256(f"bizinfo|{agency}|{title}|{url_value}".encode()).hexdigest()[:24]
            records.append({"record_id":rid,"source_type":"bizinfo","source_name":"기업마당 API","title":title,"agency":agency,"active":True,"raw_data":raw,"updated_at":now})
        return records, {"source":"기업마당 API","status":"정상","message":"기업마당 공고 조회 완료","count":len(records)}
    except Exception as exc:
        return [], {"source":"기업마당 API","status":"오류","message":str(exc),"count":0}


def refresh_repository(force: bool = False) -> dict[str, Any]:
    rows, _ = load_repository_records()
    sync = scheduled_sync_status()
    bizinfo = sync.get("bizinfo", {})
    external = sync.get("external", {})
    return {
        "skipped": True,
        "message": (
            "외부 API 직접 호출 없이 새벽 3시 동기화 자료를 다시 불러왔습니다."
        ),
        "count": len(rows),
        "bizinfo_status": bizinfo,
        "external_status": external,
    }

def repository_status() -> dict[str, Any]:
    rows, status = load_repository_records()
    state = _load_json(REFRESH_STATE_PATH, {})
    counts = {}
    for row in rows:
        key = str(row.get("source_type","unknown"))
        counts[key] = counts.get(key,0)+1
    sync = scheduled_sync_status()
    bizinfo_meta = sync.get("bizinfo", {})
    external_meta = sync.get("external", {})
    last_success = (
        bizinfo_meta.get("generated_at")
        or external_meta.get("generated_at")
        or state.get("last_success_at", "")
    )
    return {
        "count": len(rows),
        "counts": counts,
        "last_attempt_at": last_success,
        "last_success_at": last_success,
        "source_status": status,
        "scheduled_sync": sync,
    }
