from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from customer_history import save_customer_event
from registered_policy_match import build_customer_labels, load_registered_customers
from runtime_error_log import safe_public_error
from utils import get_user_cumulative_db_path, get_user_dirs


DOCUMENT_TYPES = {
    "cretop_report": "크레탑 자료",
    "consultation_audio": "녹음파일",
    "corporate_registry": "법인등기사항증명서",
    "employee_roster": "4대보험 가입자명부",
    "articles": "정관",
    "rnd_certificate": "연구개발부서·전담부서 인정서",
    "tax_adjustment": "세무조정계산서",
}
UPLOAD_DOCUMENT_TYPES = list(DOCUMENT_TYPES)[1:]


class EnterpriseDocumentCorruptionError(RuntimeError):
    """Raised without replacing malformed enterprise-document metadata."""


def _normalize_business_no(value: Any) -> str:
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}" if len(digits) == 10 else ""


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"", "-", "nan", "none", "nat", "<na>"} else text


def _safe_filename(value: Any) -> str:
    source_path = Path(str(value or "file"))
    suffix = source_path.suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,10}", suffix):
        suffix = ""
    stem = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", source_path.stem)
    return f"{stem.strip('._')[:80] or 'file'}{suffix}"


def _metadata_path(user_id: str) -> Path:
    return get_user_dirs(user_id)["base"] / "enterprise_documents.json"


def _load_records(user_id: str) -> list[dict[str, Any]]:
    path = _metadata_path(user_id)
    if not path.exists():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise EnterpriseDocumentCorruptionError(
            "기업 첨부자료 목록을 읽지 못했습니다. 기존 파일은 덮어쓰지 않았습니다."
        ) from exc
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise EnterpriseDocumentCorruptionError(
            "기업 첨부자료 목록 형식이 올바르지 않습니다. 기존 파일은 덮어쓰지 않았습니다."
        )
    return value


def _save_records(user_id: str, records: list[dict[str, Any]]) -> None:
    path = _metadata_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(
        json.dumps(records, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temp, path)


def _save_record(user_id: str, record: dict[str, Any]) -> dict[str, Any]:
    records = _load_records(user_id)
    record_id = str(record.get("record_id", ""))
    for index, existing in enumerate(records):
        if str(existing.get("record_id", "")) == record_id:
            records[index] = {**existing, **record}
            _save_records(user_id, records)
            return records[index]
    records.insert(0, record)
    _save_records(user_id, records)
    return record


def register_enterprise_document_bytes(
    user_id: str,
    business_no: str,
    company_name: str,
    document_type: str,
    filename: str,
    content: bytes,
    *,
    customer_id: str = "",
    note: str = "",
    analysis_summary: str = "",
    extracted_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = _normalize_business_no(business_no)
    if not normalized:
        raise ValueError("첨부자료를 연결할 올바른 사업자등록번호가 필요합니다.")
    if document_type not in DOCUMENT_TYPES:
        raise ValueError("지원하지 않는 기업 첨부자료 유형입니다.")
    if not isinstance(content, bytes) or not content:
        raise ValueError("빈 파일은 저장할 수 없습니다.")

    checksum = hashlib.sha256(content).hexdigest()
    record_id = hashlib.sha256(
        f"{normalized}|{document_type}|{checksum}".encode("utf-8")
    ).hexdigest()[:32]
    safe_name = _safe_filename(filename)
    business_key = re.sub(r"[^0-9]", "", normalized)
    directory = (
        get_user_dirs(user_id)["uploads"]
        / "enterprise_documents" / business_key / document_type
    )
    directory.mkdir(parents=True, exist_ok=True)
    existing = next(
        (item for item in _load_records(user_id)
         if str(item.get("record_id", "")) == record_id
         and Path(str(item.get("local_path", ""))).is_file()),
        None,
    )
    if existing:
        target = Path(str(existing["local_path"]))
    else:
        target = directory / (
            f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{record_id[:8]}_{safe_name}"
        )
        target.write_bytes(content)

    saved_at = datetime.now().isoformat(timespec="seconds")
    saved = _save_record(user_id, {
        "record_id": record_id,
        "business_no": normalized,
        "company_name": _clean(company_name),
        "customer_id": _clean(customer_id),
        "document_type": document_type,
        "document_label": DOCUMENT_TYPES[document_type],
        "filename": safe_name,
        "local_path": str(target),
        "sha256": checksum,
        "size_bytes": len(content),
        "note": _clean(note),
        "analysis_summary": _clean(analysis_summary),
        "extracted_fields": dict(extracted_fields or {}),
        "uploaded_at": saved_at,
    })
    save_customer_event(
        user_id, normalized, company_name,
        event_id=f"enterprise-document-{record_id}",
        event_title=f"{DOCUMENT_TYPES[document_type]} 등록",
        event_detail=analysis_summary or note or "기업 첨부자료를 등록했습니다.",
        occurred_at=saved_at,
        source=f"enterprise_document:{document_type}",
        history_type="기업자료",
        extra_data={
            "문서유형": document_type,
            "문서명": DOCUMENT_TYPES[document_type],
            "분석요약": _clean(analysis_summary),
            "추출정보": dict(extracted_fields or {}),
            "파일명": safe_name,
            "자료ID": record_id,
        },
    )
    return saved


def register_existing_enterprise_document(
    local_path: str | Path,
    *, user_id: str, business_no: str, company_name: str,
    document_type: str, customer_id: str = "", note: str = "",
    analysis_summary: str = "", extracted_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    source = Path(local_path)
    if not source.is_file():
        raise FileNotFoundError(str(source))
    return register_enterprise_document_bytes(
        user_id, business_no, company_name, document_type,
        source.name, source.read_bytes(), customer_id=customer_id, note=note,
        analysis_summary=analysis_summary, extracted_fields=extracted_fields,
    )


def save_enterprise_document_note(
    user_id: str, business_no: str, company_name: str,
    document_type: str, note: str, *, customer_id: str = "",
) -> dict[str, Any]:
    normalized = _normalize_business_no(business_no)
    clean_note = _clean(note)
    if not normalized or not clean_note:
        raise ValueError("자료 메모를 입력해주세요.")
    checksum = hashlib.sha256(clean_note.encode("utf-8")).hexdigest()
    record_id = hashlib.sha256(
        f"{normalized}|{document_type}:note|{checksum}".encode("utf-8")
    ).hexdigest()[:32]
    saved_at = datetime.now().isoformat(timespec="seconds")
    record = _save_record(user_id, {
        "record_id": record_id, "business_no": normalized,
        "company_name": _clean(company_name), "customer_id": _clean(customer_id),
        "document_type": document_type, "document_label": DOCUMENT_TYPES[document_type],
        "filename": "", "local_path": "", "sha256": checksum, "size_bytes": 0,
        "note": clean_note, "analysis_summary": "", "extracted_fields": {},
        "uploaded_at": saved_at,
    })
    save_customer_event(
        user_id, normalized, company_name,
        event_id=f"enterprise-document-note-{record_id}",
        event_title=f"{DOCUMENT_TYPES[document_type]} 메모",
        event_detail=clean_note, occurred_at=saved_at,
        source=f"enterprise_document:{document_type}", history_type="기업자료",
        extra_data={"문서유형": document_type, "문서명": DOCUMENT_TYPES[document_type], "자료ID": record_id},
    )
    return record


def _money_value(raw: str, unit: str) -> int | None:
    negative = raw.strip().startswith("-") or (
        raw.strip().startswith("(") and raw.strip().endswith(")")
    )
    digits = re.sub(r"[^0-9.]", "", raw)
    if not digits:
        return None
    multiplier = {"억원": 100_000_000, "백만원": 1_000_000, "천원": 1_000, "원": 1}.get(unit, 1)
    return int(round(float(digits) * multiplier)) * (-1 if negative else 1)


def analyze_tax_adjustment_text(text: str) -> dict[str, Any]:
    source = re.sub(r"[\u00a0\t]+", " ", str(text or ""))
    fields: dict[str, Any] = {}
    for label in [
        "당기순이익", "자산총계", "부채총계", "자본총계", "과세표준",
        "산출세액", "결정세액", "세액공제", "연구개발비",
    ]:
        match = re.search(
            rf"{re.escape(label)}[^\d\-\(]{{0,30}}([-+]?\(?\d[\d,]*(?:\.\d+)?\)?)\s*(억원|백만원|천원|원)?",
            source,
        )
        if match:
            value = _money_value(match.group(1), match.group(2) or "")
            if value is not None:
                fields[label] = value
    years = sorted(set(re.findall(r"(?:19|20)\d{2}", source)), reverse=True)[:3]
    return {
        "recognized": bool(re.search(r"세무조정|법인세.*신고|조정계산서", source)),
        "years": years, "financial_fields": fields,
        "summary": (f"세무조정계산서에서 {len(fields)}개 재무·세무 항목을 확인했습니다."
                    if fields else "세무조정계산서를 등록했으나 자동 추출할 금액 항목은 확인이 필요합니다."),
    }


def analyze_rnd_certificate_text(text: str) -> dict[str, Any]:
    source = re.sub(r"\s+", " ", str(text or ""))
    has_lab = bool(re.search(r"기업부설\s*연구소", source))
    has_department = bool(re.search(r"연구개발\s*(?:전담|담당)?\s*부서", source))
    fields: dict[str, Any] = {}
    if has_lab:
        fields["기업부설연구소"] = "Y"
    if has_department:
        fields["연구개발전담부서"] = "Y"
    number_match = re.search(r"(?:인정|신고)\s*번호\s*[:：]?\s*([0-9A-Za-z가-힣-]{4,30})", source)
    date_match = re.search(r"(?:인정|발급)\s*(?:일자|일)?\s*[:：]?\s*((?:19|20)\d{2}[./-]\d{1,2}[./-]\d{1,2})", source)
    if number_match:
        fields["인정번호"] = number_match.group(1)
    if date_match:
        fields["인정일"] = date_match.group(1)
    return {
        "recognized": bool(has_lab or has_department), "rnd_fields": fields,
        "summary": ("연구개발 조직 인정정보를 확인했습니다."
                    if fields else "인정서를 등록했으나 연구개발 조직 구분은 직접 확인이 필요합니다."),
    }


def load_enterprise_document_context(
    user_id: str, business_no: str, company_name: str = "",
) -> dict[str, Any]:
    normalized = _normalize_business_no(business_no)
    records = [dict(item) for item in _load_records(user_id)
               if _normalize_business_no(item.get("business_no", "")) == normalized]
    records.sort(key=lambda item: str(item.get("uploaded_at", "")), reverse=True)
    latest_by_type: dict[str, dict[str, Any]] = {}
    for item in records:
        latest_by_type.setdefault(str(item.get("document_type", "")), item)
    tax = latest_by_type.get("tax_adjustment", {}).get("extracted_fields", {}) or {}
    rnd = latest_by_type.get("rnd_certificate", {}).get("extracted_fields", {}) or {}
    parts: list[str] = []
    for item in records:
        parts.extend([
            _clean(item.get("document_label", "")), _clean(item.get("note", "")),
            _clean(item.get("analysis_summary", "")),
            " ".join(f"{key} {value}" for key, value in dict(item.get("extracted_fields", {}) or {}).items()),
        ])
    return {
        "business_no": normalized, "company_name": _clean(company_name),
        "records": records, "latest_by_type": latest_by_type,
        "financial_fields": dict(tax.get("financial_fields", tax) or {}),
        "rnd_fields": dict(rnd.get("rnd_fields", rnd) or {}),
        "combined_text": " ".join(part for part in parts if part),
    }


def load_enterprise_source_overview(
    user_id: str, business_no: str, company_name: str = "",
) -> dict[str, dict[str, Any]]:
    context = load_enterprise_document_context(user_id, business_no, company_name)
    try:
        from consultation_journal import load_company_consultation_context
        journals = load_company_consultation_context(user_id, business_no, company_name=company_name, limit=20)
    except Exception:
        journals = []
    try:
        from cloud_sync import load_registry_snapshot
        registry = load_registry_snapshot(user_id, business_no, company_name=company_name)
    except Exception:
        registry = {}
    try:
        from employee_status import get_latest_employee_status
        employees = get_latest_employee_status(user_id, business_no, company_name)
    except Exception:
        employees = {}
    try:
        from articles_review import get_latest_articles_review
        articles = get_latest_articles_review(user_id, business_no, company_name)
    except Exception:
        articles = {}
    latest = context.get("latest_by_type", {}) or {}
    return {
        "consultation_audio": {"count": len(journals), "available": bool(journals)},
        "corporate_registry": {"count": int(bool(registry or latest.get("corporate_registry"))), "available": bool(registry or latest.get("corporate_registry"))},
        "employee_roster": {"count": int(bool(employees or latest.get("employee_roster"))), "available": bool(employees or latest.get("employee_roster"))},
        "articles": {"count": int(bool(articles or latest.get("articles"))), "available": bool(articles or latest.get("articles"))},
        "rnd_certificate": {"count": sum(item.get("document_type") == "rnd_certificate" for item in context["records"]), "available": bool(latest.get("rnd_certificate"))},
        "tax_adjustment": {"count": sum(item.get("document_type") == "tax_adjustment" for item in context["records"]), "available": bool(latest.get("tax_adjustment"))},
    }


def _render_note_input(user_id: str, customer: pd.Series, document_type: str) -> None:
    business_no = _normalize_business_no(customer.get("사업자등록번호", ""))
    company_name = _clean(customer.get("업체명", ""))
    customer_id = _clean(customer.get("_customer_id", ""))
    key = f"enterprise_doc_note_{document_type}_{business_no}"
    note = st.text_area(
        f"{DOCUMENT_TYPES[document_type]} 자료 메모", key=key, height=80,
        placeholder="자료의 기준일, 확인할 내용, 상담 참고사항 등을 입력하세요.",
    )
    if st.button(
        "자료 메모 저장", key=f"enterprise_doc_note_save_{document_type}_{business_no}",
        disabled=not _clean(note), use_container_width=True,
    ):
        save_enterprise_document_note(
            user_id, business_no, company_name, document_type, note, customer_id=customer_id,
        )
        st.success("자료 메모를 해당 기업에 연결해 저장했습니다.")
        st.rerun()


def _render_generic_upload(user_id: str, customer: pd.Series, document_type: str) -> None:
    from articles_review import extract_articles_text

    business_no = _normalize_business_no(customer.get("사업자등록번호", ""))
    company_name = _clean(customer.get("업체명", ""))
    uploaded = st.file_uploader(
        f"{DOCUMENT_TYPES[document_type]} 파일 업로드",
        type=["pdf", "hwp", "docx", "txt", "png", "jpg", "jpeg", "webp", "tif", "tiff"],
        key=f"enterprise_doc_upload_{document_type}_{business_no}",
    )
    if uploaded is None or not st.button(
        "파일 분석·등록", type="primary", use_container_width=True,
        key=f"enterprise_doc_analyze_{document_type}_{business_no}",
    ):
        return
    try:
        with st.spinner("문서를 분석하고 해당 기업에 연결하고 있습니다..."):
            content = uploaded.getvalue()
            text, extraction = extract_articles_text(uploaded.name, content)
            if len(re.sub(r"\s+", "", text)) < 20:
                raise ValueError("문서에서 분석 가능한 내용을 충분히 읽지 못했습니다.")
            analysis = (analyze_rnd_certificate_text(text)
                        if document_type == "rnd_certificate"
                        else analyze_tax_adjustment_text(text))
            register_enterprise_document_bytes(
                user_id, business_no, company_name, document_type, uploaded.name, content,
                customer_id=_clean(customer.get("_customer_id", "")),
                note=st.session_state.get(f"enterprise_doc_note_{document_type}_{business_no}", ""),
                analysis_summary=analysis.get("summary", ""),
                extracted_fields={**analysis, "extraction_method": extraction.get("method", "")},
            )
        st.success("파일 원본과 분석 결과를 해당 기업에 연결해 저장했습니다.")
        st.rerun()
    except Exception as exc:
        st.error(safe_public_error(exc, "기업자료 분석·등록에 실패했습니다."))


def render_enterprise_information_assets(user_id: str, user_name: str = "") -> None:
    st.divider()
    st.markdown("### 기업 첨부자료 통합 등록")
    st.caption(
        "등록·분석한 자료는 선택한 기업의 사업자등록번호에 연결되며 "
        "기업컨설팅과 AI 코파일럿에서 자동으로 활용됩니다."
    )
    customers = load_registered_customers(
        get_user_cumulative_db_path(user_id), owner_user_id=user_id,
    )
    labels, row_map = build_customer_labels(customers)
    if not labels:
        st.info("먼저 위 크레탑 또는 개인사업자 등록에서 기업 기본정보를 저장해주세요.")
        return
    selected = st.selectbox("자료를 연결할 기업", labels, key="enterprise_information_asset_customer")
    customer = customers.loc[row_map[selected]]
    business_no = _normalize_business_no(customer.get("사업자등록번호", ""))
    company_name = _clean(customer.get("업체명", ""))
    customer_id = _clean(customer.get("_customer_id", ""))
    if not business_no:
        st.warning("사업자등록번호가 확인되는 기업만 첨부자료를 연결할 수 있습니다.")
        return
    st.session_state["_oasis_active_company_business_no"] = business_no
    st.session_state["_oasis_active_company_name"] = company_name

    overview = load_enterprise_source_overview(user_id, business_no, company_name)
    st.dataframe(pd.DataFrame([
        {"자료": DOCUMENT_TYPES[item], "상태": "등록됨" if overview[item]["available"] else "미등록", "연결 건수": overview[item]["count"]}
        for item in UPLOAD_DOCUMENT_TYPES
    ]), hide_index=True, use_container_width=True)
    chosen_label = st.selectbox(
        "등록할 자료", [DOCUMENT_TYPES[item] for item in UPLOAD_DOCUMENT_TYPES],
        key="enterprise_information_document_type",
    )
    document_type = next(key for key, label in DOCUMENT_TYPES.items() if label == chosen_label)
    _render_note_input(user_id, customer, document_type)
    st.divider()

    if document_type == "consultation_audio":
        from consultation_journal import render_audio_consultation_journal, render_saved_consultation_journals
        from crm import get_customer_record, make_customer_key
        customer_key = make_customer_key(company_name, business_no)
        render_audio_consultation_journal(
            user_id=user_id, customer_key=customer_key, company_name=company_name,
            business_no=business_no, consultant_name=user_name,
            current_crm=get_customer_record(user_id, customer_key),
        )
        render_saved_consultation_journals(user_id, business_no, company_name=company_name)
    elif document_type == "corporate_registry":
        from stock_valuation import render_registry_upload_for_customer
        render_registry_upload_for_customer(user_id, business_no, company_name)
    elif document_type == "employee_roster":
        from employee_status import render_employee_status
        render_employee_status(
            user_id, business_no, company_name,
            company_address=_clean(customer.get("사업장 소재지", "")), customer_id=customer_id,
        )
    elif document_type == "articles":
        from articles_review import render_articles_review
        render_articles_review(user_id, business_no, company_name, customer_id=customer_id)
    else:
        _render_generic_upload(user_id, customer, document_type)
