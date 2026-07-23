from __future__ import annotations

import os
import py_compile
import shutil
import sys
import tempfile
import types
from pathlib import Path


VERSION = "v9.7.3"
SUPPORTED_PREVIOUS_VERSIONS = ("v9.7.1", "v9.7.2")
FILES = (
    "prospect_db_center.py",
    "sales_intelligence.py",
    "contact_enrichment.py",
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
        import sales_intelligence

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
        sales_intelligence.enrich_company = lambda _prospect: {
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
                "confidence": 90,
                "contacts": (
                    []
                    if "first" in url
                    else [
                        {
                            "contact_type": "phone",
                            "contact_value": "031-555-1212",
                            "source_type": "official_website",
                            "confidence": 90,
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
        contact_enrichment.kakao_local_client.search_company = original_kakao
        contact_enrichment.localdata_contact_client.search_company = original_localdata
        contact_enrichment.naver_web_search_client.search_official_websites = original_naver
        contact_enrichment.inspect_website = original_inspect

        source = (project_dir / "prospect_db_center.py").read_text(encoding="utf-8")
        assert "kipris_patent_client" not in source
        assert "KIPRIS_API_KEY" not in source
        assert "농업회사법인" in source and "유한회사" in source
        assert "_is_stock_company" in source
        assert "주식회사 외 제외" in source
        assert "전화출처" in source and "연락처상태" in source
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

    backup_dir = project_dir / "_update_backups" / "before_v9.7.3"
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
    print("PHONE_CHAIN=KAKAO_LOCALDATA_NAVER_WEBSITE")
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
