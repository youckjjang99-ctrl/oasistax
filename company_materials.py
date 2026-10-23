"""Lazy, read-only, owner/company-scoped views of existing material stores."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import time
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from utils import get_user_dirs


def _business(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}" if len(digits) == 10 else ""


def _belongs(record: dict[str, Any], owner: str, business: str) -> bool:
    if not owner or not business:
        return False
    for key in ("owner_user_id", "user_id"):
        if record.get(key) and str(record[key]) != owner:
            return False
    identities = [_business(record[key]) for key in ("business_no", "사업자등록번호", "business_no_normalized") if record.get(key)]
    return bool(identities and all(value == business for value in identities))


def _safe_fields(value: Any) -> Any:
    """Keep sensitive identifiers, secrets and server paths out of the summary."""
    blocked = r"주민|생년|전화|휴대|인증|토큰|비밀|password|secret|token|session|authorization|credential|api.?key|_path$|owner_user|user_id|sha256|source_text|raw_text|transcript"
    if isinstance(value, dict):
        return {str(key): _safe_fields(item) for key, item in value.items()
                if not re.search(blocked, str(key), re.IGNORECASE)}
    if isinstance(value, list):
        return [_safe_fields(item) for item in value]
    return value


def collect_company_materials(owner: str, business_no: str, company_name: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """Run only when the user explicitly opens the material library."""
    business = _business(business_no)
    if not owner or not business:
        return [], ["기업을 먼저 선택해주세요."]
    items: list[dict[str, Any]] = []
    failures: list[str] = []

    def add(kind: str, label: str, record: Any, *, scoped: bool = False) -> None:
        if not isinstance(record, dict) or not record:
            return
        row = dict(record)
        if scoped and not row.get("business_no") and not row.get("사업자등록번호"):
            row["business_no"] = business
        if not _belongs(row, owner, business):
            return
        stamp = str(row.get("uploaded_at") or row.get("saved_at") or row.get("reviewed_at") or row.get("captured_at") or "")
        identity = str(row.get("record_id") or row.get("journal_id") or row.get("version_id") or "")
        digest = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        items.append({"key": f"{kind}:{identity}:{digest}", "type": kind, "label": label,
                      "saved_at": stamp, "filename": str(row.get("filename") or row.get("source_filename") or ""),
                      "record": row})

    from enterprise_documents import DOCUMENT_TYPES, load_enterprise_document_context
    try:
        context = load_enterprise_document_context(owner, business, company_name,
                                                  include_remote_types=tuple(DOCUMENT_TYPES))
        for record in context.get("records", []):
            kind = str(record.get("document_type", ""))
            add(kind, DOCUMENT_TYPES.get(kind, "기업 자료"), record)
            for version in record.get("analysis_versions", []):
                if isinstance(version, dict):
                    add(kind, DOCUMENT_TYPES.get(kind, "기업 자료") + " · 이전 분석", {**record, **version, "analysis_versions": []})
        for record in context.get("source_history", []):
            add("customer_history", "기업 자료·변경 이력", record)
    except Exception:
        failures.append("첨부자료 목록")
    try:
        from cloud_sync import load_financial_snapshot, load_registry_snapshot
        from stock_valuation import _load_financial_cache, _load_registry_cache, _registry_snapshot_from_cache, _merge_registry_snapshot_versions
        financial = _load_financial_cache(owner).get(business, {})
        remote_financial = load_financial_snapshot(owner, business)
        for snapshot in (remote_financial, financial):
            add("financial", "크레탑 재무 분석", snapshot, scoped=True)
            for version in snapshot.get("_versions", []) if isinstance(snapshot, dict) else []:
                add("financial", "크레탑 재무 분석 · 이전 버전", version, scoped=True)
        local_registry = _registry_snapshot_from_cache(_load_registry_cache(owner, strict=True), business, company_name)
        remote_registry = load_registry_snapshot(owner, business, company_name)
        add("corporate_registry", "등기 분석 스냅샷", _merge_registry_snapshot_versions(local_registry, remote_registry), scoped=True)
    except Exception:
        failures.append("등기·재무 분석")
    try:
        from employee_status import load_employee_versions
        for record in load_employee_versions(owner, business, company_name):
            add("employee_roster", "직원명부 분석", record, scoped=True)
    except Exception:
        failures.append("직원명부 분석")
    try:
        from articles_review import get_latest_articles_review
        articles = get_latest_articles_review(owner, business, company_name)
        add("articles", "정관 분석", articles, scoped=True)
        for record in articles.get("_versions", []) if isinstance(articles, dict) else []:
            add("articles", "정관 분석 · 이전 버전", record, scoped=True)
    except Exception:
        failures.append("정관 분석")
    try:
        from consultation_journal import load_company_consultation_context
        for record in load_company_consultation_context(owner, business, company_name, limit=1000):
            add("consultation_audio", "상담일지·녹음 분석", record)
    except Exception:
        failures.append("상담일지")
    try:
        from consultation_audio_storage import list_company_audio
        for record in list_company_audio(owner, business, company_name):
            add("consultation_audio", "상담 녹음 원본", record)
    except Exception:
        failures.append("상담 녹음 원본")
    try:
        from stock_valuation import _records_for_stock_company
        for record in _records_for_stock_company(owner, business):
            add("stock_valuation", "주가평가 계산", record)
    except Exception:
        failures.append("주가평가")
    unique = {item["key"]: item for item in items}
    return sorted(unique.values(), key=lambda item: item["saved_at"].replace("T", " "), reverse=True), failures


def read_owned_material_file(owner: str, business_no: str, record: dict[str, Any]) -> bytes:
    if not _belongs(record, owner, _business(business_no)):
        raise PermissionError("현재 기업의 자료만 확인할 수 있습니다.")
    text = record.get("local_path") or record.get("file_path") or record.get("audio_path")
    if not text:
        raise FileNotFoundError("원본 보관 경로를 확인해야 합니다.")
    path = Path(str(text)).resolve()
    root = Path(get_user_dirs(owner)["base"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise PermissionError("사용자 자료 보관 영역 밖의 파일은 열 수 없습니다.")
    content = path.read_bytes()
    if record.get("sha256") and hashlib.sha256(content).hexdigest() != record["sha256"]:
        raise ValueError("원본 무결성을 확인하지 못했습니다. 파일은 변경하지 않았습니다.")
    return content


def _original_filename(record: dict[str, Any]) -> str:
    value = next((record.get(name) for name in ("filename", "source_filename", "original_filename")
                  if record.get(name)), "")
    if not value:
        value = next((record.get(name) for name in ("local_path", "file_path", "audio_path")
                      if record.get(name)), "")
    # Both legacy Windows and server-native paths may appear in stored metadata.
    return str(value).replace("\\", "/").rsplit("/", 1)[-1].strip()


def project_original_files(owner: str, business_no: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project original references only; never mutate/delete analysis or history."""
    from enterprise_documents import DOCUMENT_TYPES

    business = _business(business_no)
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        record = item.get("record", {})
        if not isinstance(record, dict) or not _belongs(record, owner, business):
            continue
        filename = _original_filename(record)
        if not (filename or record.get("asset_id") or record.get("audio_id")):
            continue
        kind = str(record.get("document_type") or item.get("type") or "document")
        checksum = str(record.get("sha256") or "").strip().lower()
        identity = (f"sha:{checksum}" if checksum else
                    f"asset:{record['asset_id']}" if record.get("asset_id") else
                    f"audio:{record['audio_id']}" if record.get("audio_id") else
                    f"record:{record['record_id']}" if record.get("record_id") else
                    f"path:{record.get('local_path') or record.get('file_path') or record.get('audio_path')}"
                    if any(record.get(name) for name in ("local_path", "file_path", "audio_path")) else
                    f"legacy:{item.get('key', '')}:{filename}:{item.get('saved_at', '')}")
        key = hashlib.sha256(f"{kind}:{identity}".encode()).hexdigest()
        stamp = str(record.get("first_uploaded_at") or record.get("uploaded_at")
                    or record.get("saved_at") or item.get("saved_at") or "")
        value = {"key": key, "type": kind,
                 "label": DOCUMENT_TYPES.get(kind, "업로드 파일"), "filename": filename or "원본 파일",
                 "saved_at": stamp, "format": Path(filename).suffix.lstrip(".").upper() or "파일",
                 "record": dict(record)}
        previous = unique.get(key)
        if previous:
            # An analysis revision may omit the original path or private asset ID.
            # Combine only references with exactly the same original identity.
            for name in ("local_path", "file_path", "audio_path", "asset_id", "audio_id", "journal_id"):
                if not value["record"].get(name) and previous["record"].get(name):
                    value["record"][name] = previous["record"][name]
            value["saved_at"] = min(filter(None, (stamp, previous["saved_at"])), default="")
        unique[key] = value
    return sorted(unique.values(), key=lambda item: item["saved_at"].replace("T", " "), reverse=True)


def collect_company_files(owner: str, business_no: str, company_name: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """Read original archives without querying unrelated calculation stores."""
    business = _business(business_no)
    if not owner or not business:
        return [], ["기업을 먼저 선택해주세요."]
    from enterprise_documents import DOCUMENT_TYPES, load_enterprise_document_context

    items: list[dict[str, Any]] = []
    failures: list[str] = []
    try:
        context = load_enterprise_document_context(owner, business, company_name,
                                                  include_remote_types=tuple(DOCUMENT_TYPES))
        for record in context.get("records", []):
            if isinstance(record, dict):
                items.append({"type": record.get("document_type", "document"), "record": record})
    except Exception:
        failures.append("업로드 원본 목록")
    try:
        from consultation_audio_storage import list_company_audio
        for record in list_company_audio(owner, business, company_name):
            if isinstance(record, dict):
                items.append({"type": "consultation_audio", "record": record})
    except Exception:
        failures.append("상담 녹음 원본")
    return project_original_files(owner, business, items), failures


def _original_revision(owner: str, business: str, item: dict[str, Any]) -> str:
    """Metadata + selected local file stat invalidate one prepared download."""
    record = item["record"]
    if not _belongs(record, owner, business):
        raise PermissionError("현재 기업의 자료만 확인할 수 있습니다.")
    original = {name: record.get(name) for name in (
        "record_id", "document_type", "filename", "source_filename", "original_filename",
        "sha256", "asset_id", "audio_id", "journal_id", "local_path", "file_path", "audio_path",
        "uploaded_at", "asset_storage_status",
    )}
    text = record.get("local_path") or record.get("file_path") or record.get("audio_path")
    if text:
        try:
            path = Path(str(text)).resolve()
            root = Path(get_user_dirs(owner)["base"]).resolve()
            if path.is_relative_to(root):
                stat = path.stat()
                original["file_revision"] = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
            else:
                original["file_revision"] = "outside_owner_area"
        except (OSError, ValueError, RuntimeError):
            original["file_revision"] = "missing"
    return hashlib.sha256(json.dumps(original, sort_keys=True, default=str).encode()).hexdigest()


def _original_status(owner: str, business: str, item: dict[str, Any]) -> str:
    record = item["record"]
    if not _belongs(record, owner, business):
        return "원본 확인 필요"
    text = record.get("local_path") or record.get("file_path") or record.get("audio_path")
    if text:
        try:
            path = Path(str(text)).resolve()
            if path.is_relative_to(Path(get_user_dirs(owner)["base"]).resolve()) and path.is_file():
                return "다운로드 준비 필요"
        except (OSError, ValueError, RuntimeError):
            pass
    status = record.get("asset_storage_status") or {}
    if record.get("audio_id") or record.get("asset_id") or (
        isinstance(status, dict) and status.get("cloud_saved") and status.get("metadata_saved")
    ):
        return "다운로드 준비 필요"
    return "원본 확인 필요"


def material_cloud_url(owner: str, business_no: str, record: dict[str, Any]) -> str:
    if not _belongs(record, owner, _business(business_no)):
        raise PermissionError("현재 기업의 자료만 확인할 수 있습니다.")
    if record.get("audio_id") or record.get("journal_id"):
        from consultation_audio_storage import list_company_audio, create_signed_audio_url
        for audio in list_company_audio(owner, business_no):
            if not _belongs(audio, owner, _business(business_no)):
                continue
            if (record.get("audio_id") and record["audio_id"] == audio.get("audio_id")) or (
                record.get("journal_id") and record["journal_id"] == audio.get("journal_id")
            ):
                return create_signed_audio_url(str(audio.get("storage_path", "")), 60,
                                               owner_user_id=owner, audio_id=str(audio.get("audio_id", "")))
        raise FileNotFoundError("연결된 상담 녹음 원본을 확인하지 못했습니다.")
    from cloud_db import CloudDatabase, cloud_is_configured
    from customer_asset_storage import CUSTOMER_ASSET_LINKS_TABLE, create_customer_asset_download_url
    if not cloud_is_configured() or not record.get("record_id") or not record.get("document_type"):
        raise FileNotFoundError("클라우드 원본 연결을 확인해야 합니다.")
    rows = CloudDatabase().select(
        CUSTOMER_ASSET_LINKS_TABLE,
        filters={"owner_user_id": owner, "source_type": f"enterprise_document:{record['document_type']}",
                 "source_id": str(record["record_id"])}, columns="asset_id,owner_user_id,source_id", limit=1,
    )
    if not rows or rows[0].get("owner_user_id") != owner or str(rows[0].get("source_id")) != str(record["record_id"]):
        raise FileNotFoundError("연결된 클라우드 원본을 확인하지 못했습니다.")
    return create_customer_asset_download_url(str(rows[0]["asset_id"]), owner_user_id=owner, expires_in=60)


def _material_revision(owner: str) -> tuple[tuple[str, int, int], ...]:
    base = Path(get_user_dirs(owner)["base"])
    result = []
    for name in ("enterprise_documents.json", "stock_financial_cache.json", "registry_cache.json", "stock_valuations.json",
                 "articles_reviews.json", "employee_rosters.json", "consultation_journals.json"):
        try:
            stat = (base / name).stat()
            result.append((name, stat.st_mtime_ns, stat.st_size))
        except OSError:
            result.append((name, 0, 0))
    return tuple(result)


@st.cache_data(ttl=15, max_entries=64, show_spinner=False, scope="session")
def _cached_company_materials(owner: str, business: str, name: str, revision: tuple) -> tuple[list[dict[str, Any]], list[str]]:
    del revision
    return collect_company_materials(owner, business, name)


@st.cache_data(ttl=15, max_entries=64, show_spinner=False, scope="session")
def _cached_company_files(owner: str, business: str, name: str, revision: tuple) -> tuple[list[dict[str, Any]], list[str]]:
    del revision
    return collect_company_files(owner, business, name)


def render_company_materials(owner: str, business_no: str, company_name: str = "") -> None:
    business = _business(business_no)
    prepared_key = "_materials_prepared_download"
    prepared = st.session_state.get(prepared_key, {})
    if prepared.get("owner") != owner or prepared.get("business") != business:
        st.session_state.pop(prepared_key, None)
    if not owner or not business:
        st.info("자료를 확인할 기업을 먼저 선택해주세요.")
        return
    st.markdown("### 이 기업 자료함")
    st.caption("업로드한 원본 파일만 모았습니다. 분석·계산 이력은 화면에서만 숨기며 기존 자료는 그대로 보존합니다.")
    if st.button("자료 목록 새로고침", key=f"materials_refresh:{owner}:{business}"):
        _cached_company_files.clear()
        st.session_state.pop(prepared_key, None)
        from snapshot_read_cache import invalidate_snapshot_reads
        invalidate_snapshot_reads(owner)
    with st.spinner("업로드한 파일 목록을 확인하고 있습니다..."):
        items, failures = _cached_company_files(owner, business, company_name, _material_revision(owner))
    if failures:
        st.warning("일부 자료를 확인하지 못했습니다: " + ", ".join(failures) + ". 자료가 없다는 뜻은 아니며 기존 원본은 유지됩니다.")
    if not items:
        st.session_state.pop(prepared_key, None)
        st.info("현재 확인 가능한 업로드 파일이 없습니다. 분석 이력은 보존되어 있으며 원본 파일의 저장 상태를 확인해주세요.")
        return
    labels = sorted({item["label"] for item in items})
    kind = st.selectbox("자료 유형", ["전체", *labels], key=f"materials_type:{owner}:{business}")
    visible = [item for item in items if kind == "전체" or item["label"] == kind]
    st.caption(f"업로드 파일 {len(visible):,}개 · 같은 원본의 반복 분석은 한 파일로 표시합니다.")
    st.dataframe(pd.DataFrame([
        {"종류": item["label"], "파일명": item["filename"], "형식": item["format"],
         "등록일": item["saved_at"], "원본 상태": _original_status(owner, business, item)} for item in visible
    ]), hide_index=True, use_container_width=True)
    by_key = {item["key"]: item for item in visible}
    selected = st.selectbox("다운로드할 파일", list(by_key),
                            format_func=lambda key: f"{by_key[key]['filename']} · {by_key[key]['label']} · {by_key[key]['saved_at']}",
                            index=None, placeholder="다운로드할 원본 파일을 선택하세요", key=f"materials_record:{owner}:{business}:{kind}")
    if selected is None:
        return
    item = by_key[selected]
    record = item["record"]
    revision = _original_revision(owner, business, item)
    prepared = st.session_state.get(prepared_key, {})
    if (prepared.get("owner"), prepared.get("business"), prepared.get("key"), prepared.get("revision")) != (
        owner, business, selected, revision,
    ) or (prepared.get("url") and prepared.get("expires_at", 0) <= time.monotonic()):
        st.session_state.pop(prepared_key, None)
        prepared = {}
    st.caption("선택한 파일만 확인합니다. ‘원본 다운로드 준비’를 누른 뒤 다운로드 버튼을 이용해주세요.")
    if st.button("원본 다운로드 다시 준비" if prepared else "원본 다운로드 준비",
                 key=f"materials_original:{owner}:{business}:{selected}"):
        st.session_state.pop(prepared_key, None)
        prepared = {}
        try:
            try:
                content = read_owned_material_file(owner, business, record)
                if _original_revision(owner, business, item) != revision:
                    raise ValueError("original changed during preparation")
                prepared = {"content": content}
            except (FileNotFoundError, PermissionError):
                url = material_cloud_url(owner, business, record)
                if not url or not str(url).startswith("https://"):
                    raise FileNotFoundError("private original link unavailable")
                prepared = {"url": url, "expires_at": time.monotonic() + 50}
            prepared.update(owner=owner, business=business, key=selected, revision=revision)
            st.session_state[prepared_key] = prepared
        except ValueError:
            st.error("원본의 무결성을 확인하지 못해 다운로드를 중단했습니다. 기존 자료는 보존했습니다.")
        except Exception:
            st.warning("이 파일의 원본을 현재 보관 위치에서 확인하지 못했습니다. 원본 파일을 다시 업로드해주세요. 기존 분석자료는 유지됩니다.")
    if prepared.get("content") is not None:
        st.download_button("원본 파일 다운로드", prepared["content"], file_name=item["filename"],
                           mime=mimetypes.guess_type(item["filename"])[0] or "application/octet-stream",
                           key=f"materials_download:{owner}:{business}:{selected}", on_click="ignore", type="primary")
    elif prepared.get("url"):
        st.link_button("비공개 원본 다운로드 · 60초 유효", prepared["url"], type="primary")
