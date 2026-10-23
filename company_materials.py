"""Lazy, read-only, owner/company-scoped views of existing material stores."""
from __future__ import annotations

import hashlib
import json
import re
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


def render_company_materials(owner: str, business_no: str, company_name: str = "") -> None:
    business = _business(business_no)
    if not business:
        st.info("자료를 확인할 기업을 먼저 선택해주세요.")
        return
    st.markdown("### 이 기업 자료함")
    st.caption("원본과 분석·계산 이력을 분리해 확인합니다. 다른 기업의 자료는 표시하지 않습니다.")
    if st.button("자료 목록 새로고침", key=f"materials_refresh:{owner}:{business}"):
        _cached_company_materials.clear()
        from snapshot_read_cache import invalidate_snapshot_reads
        from stock_valuation import _load_cloud_records_cached
        invalidate_snapshot_reads(owner)
        _load_cloud_records_cached.clear(owner, business)
    with st.spinner("등록 자료와 분석 이력을 확인하고 있습니다..."):
        items, failures = _cached_company_materials(owner, business, company_name, _material_revision(owner))
    if failures:
        st.warning("일부 자료를 확인하지 못했습니다: " + ", ".join(failures) + ". 자료가 없다는 뜻은 아니며 기존 원본은 유지됩니다.")
    if not items:
        st.info("현재 확인 가능한 자료가 없습니다. 업로드 화면의 저장 상태를 확인해주세요.")
        return
    labels = sorted({item["label"] for item in items})
    kind = st.selectbox("자료 유형", ["전체", *labels], key=f"materials_type:{owner}:{business}")
    visible = [item for item in items if kind == "전체" or item["label"] == kind]
    st.caption(f"현재 조회한 자료·분석 버전 {len(visible):,}건 · 이전 버전은 삭제하지 않습니다.")
    st.dataframe(pd.DataFrame([{"종류": item["label"], "파일": item["filename"], "등록·분석일": item["saved_at"]} for item in visible]), hide_index=True, use_container_width=True)
    selected = st.selectbox("바로 확인할 자료", range(len(visible)),
                            format_func=lambda index: f"{visible[index]['label']} · {visible[index]['saved_at']} · {visible[index]['filename'] or '분석 결과'}",
                            index=None, placeholder="원본 또는 분석 결과를 선택하세요", key=f"materials_record:{owner}:{business}:{kind}")
    if selected is None:
        return
    record = visible[selected]["record"]
    st.markdown("#### 저장된 분석·계산 내용")
    st.json(_safe_fields(record), expanded=False)
    if record.get("filename") or record.get("local_path") or record.get("audio_id") or record.get("journal_id"):
        if st.button("원본 파일 준비", key=f"materials_original:{owner}:{business}:{visible[selected]['key']}"):
            try:
                content = read_owned_material_file(owner, business, record)
                st.download_button("원본 파일 다운로드", content, file_name=Path(str(record.get("filename") or "original.pdf")).name,
                                   key=f"materials_download:{owner}:{business}:{visible[selected]['key']}")
            except (FileNotFoundError, PermissionError):
                try:
                    url = material_cloud_url(owner, business, record)
                    st.link_button("비공개 원본 열기 · 60초 유효", url)
                except Exception:
                    st.warning("현재 기기에서 원본 연결을 확인하지 못했습니다. 원본은 삭제하지 않았으며 저장 상태 확인이 필요합니다.")
            except ValueError:
                st.error("원본의 무결성을 확인하지 못해 다운로드를 중단했습니다. 기존 자료는 보존했습니다.")
