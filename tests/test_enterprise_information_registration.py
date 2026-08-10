from __future__ import annotations

import json
from pathlib import Path

import pytest

import enterprise_documents as documents


def _user_dirs(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "user"
    uploads = base / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    return {
        "base": base,
        "uploads": uploads,
        "results": base / "results",
        "history": base / "history",
    }


def test_tax_adjustment_analysis_extracts_financial_fallbacks():
    result = documents.analyze_tax_adjustment_text(
        "2025 사업연도 법인세 세무조정계산서\n"
        "당기순이익 82,000,000원\n"
        "자산총계 830백만원\n"
        "부채총계 649,000,000원"
    )

    assert result["recognized"] is True
    assert result["financial_fields"]["당기순이익"] == 82_000_000
    assert result["financial_fields"]["자산총계"] == 830_000_000
    assert result["financial_fields"]["부채총계"] == 649_000_000


def test_rnd_certificate_analysis_is_conservative():
    result = documents.analyze_rnd_certificate_text(
        "기업부설연구소 인정서 인정번호 2026-ABC-12 인정일 2026-08-10"
    )
    unrelated = documents.analyze_rnd_certificate_text("일반 회사 소개 자료")

    assert result["recognized"] is True
    assert result["rnd_fields"]["기업부설연구소"] == "Y"
    assert unrelated["recognized"] is False
    assert unrelated["rnd_fields"] == {}


def test_document_registration_preserves_original_and_deduplicates(
    tmp_path: Path,
    monkeypatch,
):
    dirs = _user_dirs(tmp_path)
    events = []
    monkeypatch.setattr(documents, "get_user_dirs", lambda _user_id: dirs)
    monkeypatch.setattr(
        documents,
        "save_customer_event",
        lambda *args, **kwargs: events.append((args, kwargs)) or {},
    )

    first = documents.register_enterprise_document_bytes(
        "member",
        "123-45-67890",
        "테스트법인",
        "tax_adjustment",
        "세무조정.pdf",
        b"synthetic-document",
        analysis_summary="분석 완료",
        extracted_fields={"financial_fields": {"당기순이익": 10}},
    )
    second = documents.register_enterprise_document_bytes(
        "member",
        "1234567890",
        "테스트법인",
        "tax_adjustment",
        "세무조정.pdf",
        b"synthetic-document",
        analysis_summary="분석 완료",
        extracted_fields={"financial_fields": {"당기순이익": 10}},
    )

    records = json.loads(
        (dirs["base"] / "enterprise_documents.json").read_text(encoding="utf-8")
    )
    assert first["record_id"] == second["record_id"]
    assert len(records) == 1
    assert Path(records[0]["local_path"]).read_bytes() == b"synthetic-document"
    assert events


def test_corrupt_metadata_is_never_overwritten(tmp_path: Path, monkeypatch):
    dirs = _user_dirs(tmp_path)
    path = dirs["base"] / "enterprise_documents.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(documents, "get_user_dirs", lambda _user_id: dirs)

    with pytest.raises(documents.EnterpriseDocumentCorruptionError):
        documents.register_enterprise_document_bytes(
            "member",
            "123-45-67890",
            "테스트법인",
            "articles",
            "정관.pdf",
            b"synthetic-document",
        )

    assert path.read_text(encoding="utf-8") == "{broken"


def test_document_context_exposes_only_matching_company_records(
    tmp_path: Path,
    monkeypatch,
):
    dirs = _user_dirs(tmp_path)
    monkeypatch.setattr(documents, "get_user_dirs", lambda _user_id: dirs)
    (dirs["base"] / "enterprise_documents.json").write_text(
        json.dumps([
            {
                "record_id": "one",
                "business_no": "123-45-67890",
                "document_type": "tax_adjustment",
                "document_label": "세무조정계산서",
                "uploaded_at": "2026-08-10T10:00:00",
                "extracted_fields": {
                    "financial_fields": {"당기순이익": 82_000_000}
                },
            },
            {
                "record_id": "other",
                "business_no": "999-99-99999",
                "document_type": "tax_adjustment",
                "uploaded_at": "2026-08-10T11:00:00",
                "extracted_fields": {
                    "financial_fields": {"당기순이익": 1}
                },
            },
        ], ensure_ascii=False),
        encoding="utf-8",
    )

    context = documents.load_enterprise_document_context(
        "member", "1234567890", "테스트법인"
    )

    assert len(context["records"]) == 1
    assert context["financial_fields"]["당기순이익"] == 82_000_000


def test_menu_and_requested_document_labels_are_present():
    app_source = Path("app.py").read_text(encoding="utf-8")
    module_source = Path("enterprise_documents.py").read_text(encoding="utf-8")

    assert '"기업정보등록": "기업등록"' in app_source
    for label in [
        "녹음파일",
        "법인등기사항증명서",
        "4대보험 가입자명부",
        "정관",
        "연구개발부서·전담부서 인정서",
        "세무조정계산서",
    ]:
        assert label in module_source
