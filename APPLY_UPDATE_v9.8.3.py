from __future__ import annotations

import inspect
import py_compile
import shutil
import sys
from pathlib import Path
from unittest.mock import patch


VERSION = "v9.8.3"
SUPPORTED_PREVIOUS_VERSIONS = ("v9.8.0", "v9.8.1", "v9.8.2")
FILES = (
    "prospect_db_center.py",
    "prospect_collection_service.py",
    "prospect_db_repository.py",
    "public_data_api.py",
    "sales_intelligence.py",
    "contact_enrichment.py",
    "localdata_contact_client.py",
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
            py_compile.compile(str(project_dir / name), doraise=True)


def _smoke(project_dir: Path) -> None:
    sys.path.insert(0, str(project_dir))
    imported: list[str] = []
    try:
        import localdata_contact_client
        import naver_web_search_client
        import prospect_collection_service
        import public_data_api
        import sales_intelligence

        imported.extend(
            [
                "localdata_contact_client",
                "naver_web_search_client",
                "prospect_collection_service",
                "public_data_api",
                "sales_intelligence",
            ]
        )

        phones = naver_web_search_client._phones_from_text(
            "대표전화 02-1234-5678 / 담당자 010-9876-5432 / 기준일 20260723"
        )
        assert phones == ["02-1234-5678", "010-9876-5432"]
        assert "max_services" in inspect.signature(
            localdata_contact_client.search_company
        ).parameters
        assert public_data_api.is_stock_company_name(
            "주식회사 오아시스"
        )
        assert public_data_api.is_individual_business_candidate(
            "오아시스컨설팅"
        )
        assert not public_data_api.is_individual_business_candidate(
            "유한회사 오아시스"
        )

        empty = {
            "status": "SUCCESS",
            "message": "없음",
            "candidates": [],
        }
        naver_phone = {
            "status": "SUCCESS",
            "message": "1건",
            "candidates": [
                {
                    "phone": "02-1234-5678",
                    "confidence": 75,
                    "source_type": "naver_web_snippet",
                }
            ],
        }
        with (
            patch.object(
                sales_intelligence.kakao_local_client,
                "search_company",
                return_value=empty,
            ),
            patch.object(
                sales_intelligence.localdata_contact_client,
                "search_company",
                return_value=empty,
            ),
            patch.object(
                sales_intelligence.naver_web_search_client,
                "search_public_phones",
                return_value=naver_phone,
            ),
        ):
            phone = sales_intelligence._best_phone(
                "주식회사 오아시스",
                "서울특별시 중구",
                "정보서비스업",
                allow_extended=False,
            )
        assert phone["phone"] == "02-1234-5678"
        assert phone["phone_source"] == "네이버 공개검색"

        events: list[dict] = []
        fetched_options: list[dict] = []

        def fake_fetch(*_args, **kwargs):
            fetched_options.append(kwargs)
            return {
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

        def fake_find(rows, **_kwargs):
            if not rows:
                return [], [], 0
            return (
                [{**rows[0], "대표전화": "031-555-1212"}],
                [],
                len(rows),
            )

        with (
            patch.object(
                prospect_collection_service,
                "existing_prospect_identities",
                return_value=(set(), set()),
            ),
            patch.object(
                prospect_collection_service,
                "fetch_nps_workplaces",
                side_effect=fake_fetch,
            ),
            patch.object(
                prospect_collection_service,
                "remove_existing_customers",
                side_effect=lambda rows: (rows, 0),
            ),
            patch.object(
                prospect_collection_service,
                "remove_existing_prospects",
                side_effect=lambda rows, **_kwargs: (rows, 0),
            ),
            patch.object(
                prospect_collection_service,
                "_find_contactable",
                side_effect=fake_find,
            ),
        ):
            result = (
                prospect_collection_service.collect_contactable_growth_companies(
                    "41",
                    target_count=1,
                    start_page=11,
                    max_pages=1,
                    business_type="individual",
                    growth_only=True,
                    progress=events.append,
                )
            )
        assert result["found_count"] == 1
        assert fetched_options[0]["page_no"] == 11
        assert fetched_options[0]["timeout"] == 30
        assert fetched_options[0]["retries"] == 2
        assert fetched_options[0]["business_type"] == "individual"
        assert result["searched_start_page"] == 11
        assert result["searched_end_page"] == 11
        assert any(row.get("stage") == "nps_complete" for row in events)

        center_source = (project_dir / "prospect_db_center.py").read_text(
            encoding="utf-8"
        )
        assert "시작 페이지" in center_source
        assert "종료 페이지" in center_source
        assert "사업자 유형" in center_source
        assert "순고용 증가 사업장만 표시" in center_source
        assert "약 2분 안에 검색을 종료" not in center_source
        assert "네이버 공개검색" in center_source
        assert "time_limit_seconds=120" not in center_source

        repository_source = (
            project_dir / "prospect_db_repository.py"
        ).read_text(encoding="utf-8")
        identity_block = repository_source.split(
            "def existing_prospect_identities", 1
        )[1].split("def remove_existing_prospects", 1)[0]
        assert "owner_user_id" not in identity_block
    finally:
        for name in imported:
            sys.modules.pop(name, None)
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

    current_version = (
        (project_dir / "VERSION.txt").read_text(encoding="utf-8").strip()
        if (project_dir / "VERSION.txt").exists()
        else ""
    )
    if current_version and current_version not in SUPPORTED_PREVIOUS_VERSIONS:
        allowed = ", ".join(SUPPORTED_PREVIOUS_VERSIONS)
        raise RuntimeError(
            f"v9.8.0 이상에서 실행해 주세요. 현재={current_version}, 지원={allowed}"
        )

    backup_dir = project_dir / "_update_backups" / "before_v9.8.3"
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
    print("CONTACT_BUG_MAX_SERVICES=FIXED")
    print("QUICK_CONTACT_SOURCES=PARALLEL")
    print("NAVER_SEARCH_SNIPPET_PHONE=ENABLED")
    print("MOBILE_PHONE_AUTO_EXCLUSION=DISABLED")
    print("KAKAO_NAME_ONLY_RETRY_WHEN_PHONE_EMPTY=ENABLED")
    print("SEARCH_LIMIT=USER_PAGE_RANGE")
    print("SEARCH_TIMEOUT_FORCE_STOP=DISABLED")
    print("NPS_TIMEOUT=30_SECONDS_WITH_RETRY")
    print("OFFICIAL_WEBSITE_SCAN=RESTORED")
    print("BUSINESS_TYPES=STOCK_INDIVIDUAL_ALL")
    print("GROWTH_ONLY_DEFAULT=YES")
    print("GLOBAL_SAVED_PROSPECT_EXCLUSION=ENABLED")
    print("DB_SCHEMA=UNCHANGED")
    print("EXISTING_FEATURES=RETAINED")
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
