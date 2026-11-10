"""Explicitly reanalyse an owned registered original; never hydrate valuation inputs."""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlsplit

import requests

from data_safety_storage import require_owner_context


MAX_REGISTRY_BYTES = 100 * 1024 * 1024


def _business(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return f"{digits[:3]}-{digits[3:5]}-{digits[5:]}" if len(digits) == 10 else ""


def _text(value: Any) -> str:
    text = str(value if value is not None else "").strip()
    return "" if text.lower() in {"", "none", "nan", "nat", "<na>"} else text


def _validate_original(content: bytes, record: dict[str, Any], business: str) -> bytes:
    if not content or len(content) > MAX_REGISTRY_BYTES or b"%PDF-" not in content[:1024]:
        raise ValueError("100MB 이하의 읽을 수 있는 등기 원본 PDF가 필요합니다.")
    checksum = hashlib.sha256(content).hexdigest()
    declared = _text(record.get("sha256")).lower()
    if declared and (not re.fullmatch(r"[0-9a-f]{64}", declared) or checksum != declared):
        raise ValueError("등기 원본의 무결성을 확인하지 못했습니다. 기존 자료는 변경하지 않았습니다.")
    size = record.get("size_bytes")
    if size not in (None, "", 0):
        try:
            if int(size) != len(content):
                raise ValueError("size mismatch")
        except (TypeError, ValueError, OverflowError):
            raise ValueError("등기 원본의 크기가 등록 정보와 다릅니다. 기존 자료는 변경하지 않았습니다.") from None
    expected_id = hashlib.sha256(f"{business}|corporate_registry|{checksum}".encode("utf-8")).hexdigest()[:32]
    if str(record.get("record_id", "")) != expected_id:
        raise ValueError("현재 기업에 등록된 동일 원본인지 확인하지 못했습니다. 원본을 다시 등록해주세요.")
    return content


def _read_registered_original(owner: str, business: str, record: dict[str, Any]) -> bytes:
    from company_materials import material_cloud_url, read_owned_material_file
    from utils import get_user_dirs

    local = record.get("local_path") or record.get("file_path") or record.get("audio_path")
    if local:
        path = Path(str(local)).resolve()
        root = Path(get_user_dirs(owner)["base"]).resolve()
        if path.is_relative_to(root) and path.is_file() and path.stat().st_size > MAX_REGISTRY_BYTES:
            raise ValueError("재분석할 등기 원본은 100MB 이하여야 합니다.")
    try:
        content = read_owned_material_file(owner, business, record)
    except (FileNotFoundError, PermissionError):
        content = None
    if content is not None:
        # An existing local original with a bad checksum must not silently fall
        # back to another copy: the integrity failure needs explicit review.
        return _validate_original(content, record, business)

    from cloud_db import CloudDatabase

    try:
        url = material_cloud_url(owner, business, record)
        actual = urlsplit(str(url))
        expected = urlsplit(CloudDatabase().config.url)
        if (actual.scheme != "https" or expected.scheme != "https"
                or not actual.hostname or actual.hostname != expected.hostname
                or actual.port != expected.port or actual.username or actual.password
                or not actual.path.startswith("/storage/v1/")):
            raise PermissionError("등기 원본의 비공개 보관 출처를 확인하지 못했습니다.")
        response = requests.get(url, timeout=(10, 45), stream=True, allow_redirects=False)
        try:
            if response.status_code != 200:
                raise RuntimeError("등기 원본을 비공개 보관소에서 확인하지 못했습니다.")
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_REGISTRY_BYTES:
                raise ValueError("재분석할 등기 원본은 100MB 이하여야 합니다.")
            chunks = []
            received = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                received += len(chunk)
                if received > MAX_REGISTRY_BYTES:
                    raise ValueError("재분석할 등기 원본은 100MB 이하여야 합니다.")
                chunks.append(chunk)
            content = b"".join(chunks)
        finally:
            response.close()
    except (PermissionError, ValueError):
        raise
    except Exception:
        raise RuntimeError("등기 원본을 비공개 보관소에서 확인하지 못했습니다. 원본을 다시 등록해주세요.") from None
    return _validate_original(content, record, business)


def reanalyze_registered_registry(
    user_id: str, business_no: str, record_id: str, selected_row: Any,
) -> dict[str, Any]:
    """Save a new source-analysis version, not a new valuation or its inputs.

    All customer/document identifiers are re-resolved from owner-scoped server
    loaders. No caller-supplied path, URL, name-only match, or session state is
    used to choose an original. Cloud writes occur only after explicit invocation
    and successful original/parsed-corporation validation.
    """
    owner = require_owner_context(user_id)
    business = _business(business_no)
    if (not business or selected_row is None
            or _business(selected_row.get("사업자등록번호", "")) != business
            or not _text(record_id)):
        raise PermissionError("재분석할 등록 기업과 등기 원본을 먼저 선택해주세요.")

    from enterprise_documents import load_enterprise_document_context, register_enterprise_document_bytes
    from registry_runner import run_registry_worker
    from stock_valuation import (
        _find_stock_customer_row, _read_customers, _registry_identity_error,
        _registry_snapshot_identity_conflicts, _save_registry_cache,
    )

    customer = _find_stock_customer_row(_read_customers(owner), business_no=business)
    if customer is None:
        raise PermissionError("현재 사용자가 조회할 수 있는 등록 기업을 확인하지 못했습니다.")
    if _registry_snapshot_identity_conflicts(
        {"사업자등록번호": selected_row.get("사업자등록번호", ""),
         "법인등록번호": selected_row.get("법인등록번호", "")},
        business, customer.get("법인등록번호", ""),
    ):
        raise PermissionError("선택 기업의 식별정보가 변경되었습니다. 기업을 다시 선택해주세요.")
    company_name = _text(customer.get("업체명", ""))
    context = load_enterprise_document_context(owner, business, company_name,
                                               include_remote_types=("corporate_registry",))
    matches = [record for record in context.get("records", [])
               if isinstance(record, dict) and str(record.get("record_id", "")) == str(record_id)]
    if len(matches) != 1:
        raise PermissionError("현재 기업에 연결된 등기 원본을 하나로 확인하지 못했습니다.")
    original = deepcopy(matches[0])
    if (original.get("document_type") != "corporate_registry"
            or _business(original.get("business_no", "")) != business
            or any(_text(original.get(key)) not in {"", owner} for key in ("owner_user_id", "user_id"))
            or any(_text(original.get(key)) not in {"", "enterprise_document:corporate_registry"}
                   for key in ("source", "source_type"))):
        raise PermissionError("현재 사용자의 해당 기업에 등록된 등기 원본만 재분석할 수 있습니다.")
    content = _read_registered_original(owner, business, original)
    checksum = hashlib.sha256(content).hexdigest()
    with tempfile.TemporaryDirectory(prefix="oasis_registry_reanalysis_") as temporary:
        source = Path(temporary) / "registered-original.pdf"
        source.write_bytes(content)
        registry_data, error = run_registry_worker(source, timeout=90)
    if error:
        # The worker already returns bounded user-safe messages, never process
        # output, local paths, the document text, or the signed URL.
        raise ValueError("등록 등기 원본을 재분석하지 못했습니다. 원본 상태를 확인해주세요.")
    error = _registry_identity_error(registry_data, customer)
    if error:
        raise ValueError(error)
    registry_data = deepcopy(registry_data)
    registry_data.update({"사업자등록번호": business, "source_record_id": str(record_id),
                          "source_sha256": checksum})
    storage_result: dict[str, Any] = {}
    saved_business = _save_registry_cache(owner, business, registry_data, storage_result=storage_result)
    if saved_business != business:
        raise ValueError("재분석 결과를 선택 기업에 안전하게 연결하지 못했습니다. 기존 평가 입력값은 유지됩니다.")
    saved_registry = deepcopy(storage_result.get("registry_data") or registry_data)
    result = {
        "registry_data": saved_registry, "cloud_saved": bool(storage_result.get("cloud_saved")),
        "original_cloud_saved": False, "history_cloud_saved": False,
        "document_registered": False, "record_id": str(record_id),
        "source_sha256": checksum, "warnings": [],
    }
    try:
        registered = register_enterprise_document_bytes(
            owner, business, company_name, "corporate_registry",
            _text(original.get("filename")) or "registered-original.pdf", content,
            customer_id=_text(customer.get("_customer_id", "")), note=_text(original.get("note", "")),
            analysis_summary="등록된 법인 등기 원본을 현재 분석기로 다시 확인했습니다.",
            extracted_fields=saved_registry,
        )
        if not isinstance(registered, dict) or str(registered.get("record_id", "")) != str(record_id):
            raise ValueError("registered source not confirmed")
        asset = registered.get("asset_storage_status") or {}
        history = registered.get("history_storage_status") or {}
        result.update(
            document_registered=True,
            original_cloud_saved=bool(asset.get("cloud_saved") and asset.get("metadata_saved")),
            history_cloud_saved=bool(history.get("cloud_saved")),
        )
    except Exception:
        result["document_error_code"] = "registry_document_reanalysis_unconfirmed"
        result["warnings"].append("등기 스냅샷은 보관했지만 원본·분석이력 연결은 확인이 필요합니다. 기존 원본과 평가 입력값은 유지됩니다.")
    if not result["cloud_saved"] or not result["original_cloud_saved"] or not result["history_cloud_saved"]:
        result["warnings"].append("일부 자료의 클라우드 저장 완료를 확인하지 못했습니다. 로컬 원본과 기존 이력은 유지됩니다.")
    return result
