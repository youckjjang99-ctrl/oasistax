from __future__ import annotations

import os
import py_compile
import shutil
import sys
import tempfile
import types
from pathlib import Path


VERSION = "v9.8.0"
SUPPORTED_PREVIOUS_VERSIONS = (
    "v9.7.1",
    "v9.7.2",
    "v9.7.3",
    "v9.7.4",
    "v9.7.5",
)
FILES = (
    "app.py",
    "prospect_db_center.py",
    "prospect_collection_service.py",
    "prospect_db_repository.py",
    "public_data_api.py",
    "sales_intelligence.py",
    "contact_enrichment.py",
    "contact_matching.py",
    "kakao_local_client.py",
    "naver_web_search_client.py",
    "VERSION.txt",
)


def _copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _compile(project_dir: Path) -> None:
    for name in FILES:
        if name.endswith(".py"):
            py_compile.compile(
                str(project_dir / name),
                doraise=True,
            )


def _smoke(project_dir: Path) -> None:
    sys.path.insert(0, str(project_dir))
    try:
        if "requests" not in sys.modules:
            requests_stub = types.ModuleType("requests")
            requests_stub.get = lambda *_args, **_kwargs: None
            sys.modules["requests"] = requests_stub
        import contact_enrichment
        import contact_matching
        import prospect_collection_service
        import sales_intelligence

        assert contact_matching.normalize_phone("20260723") == ""
        assert contact_matching.normalize_phone("20162015") == ""
        assert contact_matching.normalize_phone("0315551212") == "031-555-1212"
        assert contact_matching.normalize_phone("15881234") == "1588-1234"

        original_kakao = sales_intelligence.kakao_local_client.search_company
        original_localdata = (
            sales_intelligence.localdata_contact_client.search_company
        )
        original_enrich = sales_intelligence.enrich_company
        sales_intelligence.kakao_local_client.search_company = (
            lambda *_args, **_kwargs: {"candidates": []}
        )
        sales_intelligence.localdata_contact_client.search_company = (
            lambda *_args, **_kwargs: {"candidates": []}
        )
        sales_intelligence.enrich_company = lambda _prospect, **_kwargs: {
            "contacts": [
                {
                    "contact_type": "phone",
                    "contact_value": "02-1234-5678",
                    "source_type": "official_website",
                    "confidence": 90,
                    "verification_status": "auto_verified",
                }
            ],
            "trace": [{"stage": "website", "status": "FOUND"}],
        }
        analyzed = sales_intelligence.analyze_sales_candidate(
            {
                "company_name": "주식회사 오아시스",
                "address": "서울특별시 중구",
                "industry_name": "정보서비스업",
                "가입자수": 8,
                "신규취득자수": 3,
                "상실가입자수": 1,
            }
        )
        assert analyzed["phone"] == "02-1234-5678"
        assert analyzed["phone_source"] == "공식 홈페이지"
        assert analyzed["net_hiring"] == 2
        assert analyzed["primary_topic"] == "고용지원금"
        assert "특허" not in " ".join(analyzed["sales_topics"])
        sales_intelligence.kakao_local_client.search_company = original_kakao
        sales_intelligence.localdata_contact_client.search_company = original_localdata
        sales_intelligence.enrich_company = original_enrich

        original_kakao = contact_enrichment.kakao_local_client.search_company
        original_localdata = (
            contact_enrichment.localdata_contact_client.search_company
        )
        original_naver = contact_enrichment.naver_web_search_client.search_official_websites
        original_inspect = contact_enrichment.inspect_website
        contact_enrichment.kakao_local_client.search_company = (
            lambda *_args, **_kwargs: {"candidates": []}
        )
        contact_enrichment.localdata_contact_client.search_company = (
            lambda *_args, **_kwargs: {"candidates": []}
        )
        contact_enrichment.naver_web_search_client.search_official_websites = (
            lambda *_args, **_kwargs: {
                "candidates": [
                    {"url": "https://first.example", "confidence": 80},
                    {"url": "https://second.example", "confidence": 75},
                ]
            }
        )
        inspected: list[str] = []

        def _inspect(url: str, *_args, **_kwargs) -> dict:
            inspected.append(url)
            return {
                "ok": True,
                "status": "FOUND",
                "message": "smoke",
                "website_url": url,
                "confidence": 45,
                "contacts": (
                    []
                    if "first" in url
                    else [
                        {
                            "contact_type": "phone",
                            "contact_value": "031-555-1212",
                            "source_type": "official_website",
                            "confidence": 45,
                            "verification_status": "auto_verified",
                        }
                    ]
                ),
            }

        contact_enrichment.inspect_website = _inspect
        enriched = contact_enrichment.enrich_company(
            {"company_name": "주식회사 오아시스", "address": "경기도"}
        )
        assert len(inspected) == 2
        assert any(
            row.get("contact_value") == "031-555-1212"
            for row in enriched.get("contacts", [])
        )
        assert any(
            row.get("verification_status") == "review_required"
            for row in enriched.get("contacts", [])
            if row.get("contact_type") == "phone"
        )
        contact_enrichment.kakao_local_client.search_company = original_kakao
        contact_enrichment.localdata_contact_client.search_company = original_localdata
        contact_enrichment.naver_web_search_client.search_official_websites = original_naver
        contact_enrichment.inspect_website = original_inspect

        original_identities = (
            prospect_collection_service.existing_prospect_identities
        )
        original_fetch = prospect_collection_service.fetch_nps_workplaces
        original_customers = (
            prospect_collection_service.remove_existing_customers
        )
        original_prospects = (
            prospect_collection_service.remove_existing_prospects
        )
        original_find = prospect_collection_service._find_contactable
        prospect_collection_service.existing_prospect_identities = (
            lambda: (set(), set())
        )
        prospect_collection_service.fetch_nps_workplaces = (
            lambda *_args, **_kwargs: {
                "ok": True,
                "basic_received_count": 100,
                "basic_detail_target_count": 1,
                "detail_success_count": 1,
                "detail_failed_count": 0,
                "items": [
                    {
                        "source_key": "growth-1",
                        "사업장명": "주식회사 오아시스",
                        "사업자등록번호": "123-45-67890",
                        "가입자수": 8,
                        "신규취득자수": 3,
                        "상실가입자수": 1,
                    }
                ],
            }
        )
        prospect_collection_service.remove_existing_customers = (
            lambda rows: (rows, 0)
        )
        prospect_collection_service.remove_existing_prospects = (
            lambda rows, **_kwargs: (rows, 0)
        )
        prospect_collection_service._find_contactable = (
            lambda rows, **_kwargs: (
                [{**rows[0], "대표전화": "031-555-1212"}],
                [],
                len(rows),
            )
        )
        collection = (
            prospect_collection_service.collect_contactable_growth_companies(
                "41",
                target_count=1,
                max_pages=1,
            )
        )
        assert collection["found_count"] == 1
        assert collection["items"][0]["순고용증가"] == 2
        prospect_collection_service.existing_prospect_identities = (
            original_identities
        )
        prospect_collection_service.fetch_nps_workplaces = original_fetch
        prospect_collection_service.remove_existing_customers = (
            original_customers
        )
        prospect_collection_service.remove_existing_prospects = (
            original_prospects
        )
        prospect_collection_service._find_contactable = original_find

        source = (project_dir / "prospect_db_center.py").read_text(encoding="utf-8")
        assert "kipris_patent_client" not in source
        assert "KIPRIS_API_KEY" not in source
        assert "농업회사법인" in source and "유한회사" in source
        assert "_is_stock_company" in source
        assert "연락 가능한 성장기업 30개 찾기" not in source
        assert "연락 가능한 성장기업" in source
        assert "전화출처" in source and "연락처상태" in source
        assert "render_prospect_admin_settings" in source
        assert "_render_prospect_db_center_legacy" in source
        assert "fallback_query" in (
            project_dir / "kakao_local_client.py"
        ).read_text(encoding="utf-8")
        assert "fallback_query" in (
            project_dir / "naver_web_search_client.py"
        ).read_text(encoding="utf-8")
        assert "kipris" not in (project_dir / "sales_intelligence.py").read_text(
            encoding="utf-8"
        ).lower()
    finally:
        if str(project_dir) in sys.path:
            sys.path.remove(str(project_dir))


def _restore(project_dir: Path, backup_dir: Path) -> None:
    for name in FILES:
        backup = backup_dir / name
        target = project_dir / name
        if backup.exists():
            _copy(backup, target)
        elif target.exists():
            target.unlink()


def main() -> int:
    package_dir = Path(__file__).resolve().parent
    project_dir = Path.cwd()
    payload_dir = package_dir / "payload"
    if not payload_dir.is_dir():
        raise RuntimeError("payload 폴더를 찾을 수 없습니다.")
    missing = [name for name in FILES if not (payload_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"payload 누락: {', '.join(missing)}")

    current_version = (project_dir / "VERSION.txt").read_text(
        encoding="utf-8"
    ).strip() if (project_dir / "VERSION.txt").exists() else ""
    if current_version and current_version not in SUPPORTED_PREVIOUS_VERSIONS:
        allowed = ", ".join(SUPPORTED_PREVIOUS_VERSIONS)
        print(f"[INFO] Current version: {current_version} (base: {allowed})")

    backup_dir = project_dir / "_update_backups" / "before_v9.8.0"
    if backup_dir.exists():
        raise RuntimeError(f"기존 백업 폴더가 있습니다: {backup_dir}")
    backup_dir.mkdir(parents=True)

    try:
        for name in FILES:
            target = project_dir / name
            if target.exists():
                _copy(target, backup_dir / name)
        for name in FILES:
            _copy(payload_dir / name, project_dir / name)
        _compile(project_dir)
        _smoke(project_dir)
    except Exception:
        _restore(project_dir, backup_dir)
        raise

    print("UPDATE_OK")
    print(f"VERSION={VERSION}")
    print("KIPRIS_CALL=DISABLED")
    print("CORPORATE_TARGET=STOCK_COMPANY_ONLY")
    print("EXCLUDED_LEGAL_FORMS=AGRICULTURAL_LIMITED_AND_OTHERS")
    print("PHONE_CHAIN=KAKAO_NAME_ADDRESS_NAME_ONLY_LOCALDATA_NAVER_WEBSITE")
    print("PHONE_REQUIRED_FOR_PROSPECT=YES")
    print("WEBSITE_PHONE_REVIEW_FALLBACK=ENABLED")
    print("DATE_LIKE_PHONE_REJECTED=YES")
    print("TARGET_CONTACTABLE_COMPANIES=30")
    print("GROWTH_PRIORITY=RECENT_NET_EMPLOYMENT")
    print("SAVED_PROSPECT_EXCLUSION=ENABLED")
    print("USER_UI=SIMPLIFIED")
    print("API_TESTS=SYSTEM_MANAGEMENT")
    print("DB_SCHEMA=UNCHANGED")
    print("PY_COMPILE=OK")
    print("RUNTIME_SMOKE_TEST=OK")
    print(f"BACKUP={backup_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"UPDATE_FAILED={type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
