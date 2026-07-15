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


def load_repository_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    seed_result = ensure_seed_loaded()
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
    if not rows:
        cached = _load_json(LOCAL_CACHE_PATH, [])
        rows = [_normalize_record(row) for row in cached if isinstance(row, dict)]
    rows = [row for row in rows if row.get("active") and row.get("title")]
    return rows, {
        "source": "내부 통합 정책DB",
        "status": "정상" if rows else "자료없음",
        "message": f"{source} · {seed_result.get('message','')}",
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
    state = _load_json(REFRESH_STATE_PATH, {})
    try:
        last_attempt = datetime.fromisoformat(state.get("last_attempt_at",""))
    except Exception:
        last_attempt = None
    if not force and last_attempt and datetime.now() - last_attempt < timedelta(hours=DEFAULT_REFRESH_HOURS):
        rows, _ = load_repository_records()
        return {"skipped":True,"message":"최근 24시간 이내 최신화 확인 완료","count":len(rows)}
    existing, _ = load_repository_records()
    bizinfo, biz_status = fetch_bizinfo_records()
    merged = {row["record_id"]: row for row in existing}
    for row in bizinfo:
        merged[row["record_id"]] = _normalize_record(row)
    rows = list(merged.values())
    _save_json(LOCAL_CACHE_PATH, rows)
    if bizinfo and cloud_is_configured():
        try:
            CloudDatabase().upsert(TABLE_POLICY_REPOSITORY, bizinfo, "record_id")
        except Exception:
            pass
    _save_json(REFRESH_STATE_PATH, {
        "last_attempt_at": datetime.now().isoformat(timespec="seconds"),
        "last_success_at": datetime.now().isoformat(timespec="seconds") if biz_status.get("status") == "정상" else state.get("last_success_at",""),
        "bizinfo_status": biz_status,
        "record_count": len(rows),
    })
    return {"skipped":False,"message":biz_status.get("message",""),"count":len(rows),"bizinfo_status":biz_status}


def repository_status() -> dict[str, Any]:
    rows, status = load_repository_records()
    state = _load_json(REFRESH_STATE_PATH, {})
    counts = {}
    for row in rows:
        key = str(row.get("source_type","unknown"))
        counts[key] = counts.get(key,0)+1
    return {"count":len(rows),"counts":counts,"last_attempt_at":state.get("last_attempt_at",""),"last_success_at":state.get("last_success_at",""),"source_status":status}
