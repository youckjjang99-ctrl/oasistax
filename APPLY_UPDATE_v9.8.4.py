from __future__ import annotations

import inspect
import py_compile
import shutil
import sys
from pathlib import Path
from unittest.mock import patch


VERSION = "v9.8.4"
SUPPORTED_PREVIOUS_VERSIONS = (
    "v9.8.0",
    "v9.8.1",
    "v9.8.2",
    "v9.8.3",
)
FILES = (
    "app.py",
    "prospect_db_center.py",
    "prospect_collection_service.py",
    "prospect_db_repository.py",
    "public_data_api.py",
    "sales_intelligence.py",
    "contact_enrichment.py",
    "localdata_contact_client.py",
    "kakao_local_client.py",
    "naver_web_search_client.py",
    "supabase_v984_db_discovery.sql",
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
        import prospect_db_repository
        import public_data_api
        import sales_intelligence

        imported.extend(
            [
                "localdata_contact_client",
                "naver_web_search_client",
                "prospect_collection_service",
                "prospect_db_repository",
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
            "오아시스한의원"
        )
        assert not public_data_api.is_individual_business_candidate(
            "유한회사 오아시스"
        )
        assert public_data_api.industry_category(
            "한식 일반 음식점업"
        ) == "음식점"
        assert public_data_api.industry_category(
            "치과 의원"
        ) == "병원·의원"
        normalized_missing = public_data_api.normalize_nps_workplace(
            {
                "seq": "missing-growth",
                "wkplNm": "주식회사 오아시스",
                "jnngpCnt": 8,
                "dataCrtYm": "202607",
            },
            "11",
        )
        assert normalized_missing["신규취득자수"] is None
        assert normalized_missing["상실가입자수"] is None
        assert normalized_missing["순고용증가"] is None

        class FakeResponse:
            ok = True
            status_code = 200
            headers = {"content-type": "application/json"}

            def __init__(self, items):
                self._items = items
                self.text = "{}"

            def json(self):
                return {
                    "response": {
                        "header": {
                            "resultCode": "00",
                            "resultMsg": "NORMAL SERVICE",
                        },
                        "body": {
                            "totalCount": len(self._items),
                            "items": {"item": self._items},
                        },
                    }
                }

        with (
            patch.dict(
                public_data_api.os.environ,
                {public_data_api.SERVICE_KEY_ENV: "test-key"},
            ),
            patch.object(
                public_data_api,
                "_get_with_retry",
                return_value=(
                    FakeResponse(
                        [
                            {
                                "dataCrtYm": "202607",
                                "nwAcqzrCnt": 4,
                                "lssJnngpCnt": 1,
                            }
                        ]
                    ),
                    "",
                    "",
                    1,
                ),
            ),
        ):
            period = public_data_api.fetch_nps_period_status(
                "period-seq",
                data_created_ym="202607",
            )
        assert period["ok"]
        assert period["net_growth"] == 3

        current_yoy = {
            "사업장명": "주식회사 오아시스",
            "사업자등록번호": "123-45-67890",
            "주소": "서울특별시 중구 세종대로 1",
            "지역코드": "11",
            "가입자수": 12,
            "자료생성년월": "202607",
        }
        prior_basic = {
            "seq": "prior-seq",
            "wkplNm": "주식회사 오아시스",
            "bzowrRgstNo": "123456",
            "wkplRoadNmDtlAddr": "서울특별시 중구 세종대로 1",
            "dataCrtYm": "202507",
        }
        with (
            patch.dict(
                public_data_api.os.environ,
                {public_data_api.SERVICE_KEY_ENV: "test-key"},
            ),
            patch.object(
                public_data_api,
                "_get_with_retry",
                return_value=(FakeResponse([prior_basic]), "", "", 1),
            ),
            patch.object(
                public_data_api,
                "_fetch_nps_detail",
                return_value=(
                    {**prior_basic, "jnngpCnt": 8},
                    True,
                    "",
                    1,
                ),
            ),
        ):
            yoy = public_data_api.fetch_nps_year_over_year(current_yoy)
        assert yoy["ok"]
        assert yoy["previous_data_created_ym"] == "202507"
        assert yoy["year_over_year_growth"] == 4

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
                    "phone": "010-9876-5432",
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
                "오아시스한의원",
                "서울특별시 중구",
                "보건업",
                allow_extended=False,
            )
        assert phone["phone"] == "010-9876-5432"

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
                        "source_key": "growth-food-1",
                        "사업장명": "오아시스식당",
                        "사업자등록번호": "123-45-67890",
                        "업종명": "한식 일반 음식점업",
                        "업종분류": "음식점",
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
                [{**rows[0], "대표전화": "010-5555-1212"}],
                [],
                len(rows),
            )

        def fake_employment(rows, **kwargs):
            assert kwargs["basis"] == "year_over_year"
            enriched = [
                {
                    **row,
                    "전년가입자수": 5,
                    "전년대비고용증가": 3,
                    "선택고용증가": 3,
                    "고용증가기준": "year_over_year",
                    "고용자료상태": "CONFIRMED",
                    "고용증가판정": "전년 동월 대비 가입자 증가",
                }
                for row in rows
            ]
            return (
                enriched,
                {
                    "employment_checked": len(enriched),
                    "employment_unavailable": 0,
                    "employment_failed": 0,
                    "employment_api_attempts": len(enriched) * 2,
                },
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
                "enrich_employment_growth",
                side_effect=fake_employment,
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
                    growth_basis="year_over_year",
                    industry_categories=["음식점"],
                    progress=events.append,
                )
            )
        assert result["found_count"] == 1
        assert result["items"][0]["업종분류"] == "음식점"
        assert result["industry_categories"] == ["음식점"]
        assert fetched_options[0]["page_no"] == 11
        assert fetched_options[0]["timeout"] == 30
        assert fetched_options[0]["retries"] == 2
        assert fetched_options[0]["business_type"] == "individual"
        assert result["searched_start_page"] == 11
        assert result["searched_end_page"] == 11
        assert result["growth_basis"] == "year_over_year"
        assert result["items"][0]["전년대비고용증가"] == 3
        assert any(row.get("stage") == "nps_complete" for row in events)
        assert any(
            row.get("stage") == "employment_complete" for row in events
        )

        center_source = (project_dir / "prospect_db_center.py").read_text(
            encoding="utf-8"
        )
        for phrase in (
            "내 검색 페이지 이력",
            "업종 필터",
            "이번 발굴결과 엑셀 다운로드",
            "저장된 영업후보 엑셀 다운로드",
            "업체 메모",
            "save_search_history",
            "save_prospect_memo",
            "고용 증가 판단 기준",
            "전년 동월 대비 가입자수 증가",
            "고용자료 확인 불가",
        ):
            assert phrase in center_source
        assert "time_limit_seconds=120" not in center_source

        app_source = (project_dir / "app.py").read_text(encoding="utf-8")
        menu_block = app_source.split("menu_label_map = {", 1)[1].split(
            "}", 1
        )[0]
        assert menu_block.index('"홈"') < menu_block.index('"DB발굴"')
        assert menu_block.index('"DB발굴"') < menu_block.index('"기업등록"')
        assert 'elif active_tab == "DB발굴"' in app_source

        repository_source = (
            project_dir / "prospect_db_repository.py"
        ).read_text(encoding="utf-8")
        for function_name in (
            "def save_search_history",
            "def list_search_history",
            "def save_prospect_memo",
            "def search_history_table_status",
        ):
            assert function_name in repository_source
        identity_block = repository_source.split(
            "def existing_prospect_identities", 1
        )[1].split("def remove_existing_prospects", 1)[0]
        assert "owner_user_id" not in identity_block

        sql_source = (
            project_dir / "supabase_v984_db_discovery.sql"
        ).read_text(encoding="utf-8").lower()
        assert "add column if not exists memo" in sql_source
        assert "growth_basis" in sql_source
        assert "create table if not exists" in sql_source
        for forbidden in ("drop table", "truncate ", "delete from"):
            assert forbidden not in sql_source
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
            f"v9.8.0 이상에서 실행해 주세요. 현재={current_version}, "
            f"지원={allowed}"
        )

    backup_dir = project_dir / "_update_backups" / "before_v9.8.4"
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
    print("MENU=DB_DISCOVERY_AFTER_HOME")
    print("SEARCH_HISTORY=PER_USER")
    print("INDUSTRY_FILTER=ENABLED")
    print("EXCEL_DOWNLOAD=SEARCH_AND_SAVED")
    print("PROSPECT_MEMO=ENABLED")
    print("GLOBAL_SAVED_PROSPECT_EXCLUSION=ENABLED")
    print("MOBILE_PHONE_AUTO_EXCLUSION=DISABLED")
    print("EMPLOYMENT_BASIS=YEAR_OVER_YEAR_OR_RECENT_NET_OR_NONE")
    print("MISSING_EMPLOYMENT_DATA=NOT_ZERO")
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
