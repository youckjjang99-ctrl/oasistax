from __future__ import annotations

import importlib
import json
import os
import py_compile
import shutil
import sys
import types
from datetime import datetime
from pathlib import Path


EXPECTED = "v9.6.1"
TARGET = "v9.7.0"
FILES = [
    "prospect_db_center.py",
    "prospect_db_repository.py",
    "contact_matching.py",
    "kakao_local_client.py",
    "localdata_contact_client.py",
    "naver_web_search_client.py",
    "website_contact_parser.py",
    "contact_enrichment.py",
    "supabase_v970_contact_enrichment.sql",
    "VERSION.txt",
]
COMPILE_FILES = [name for name in FILES if name.endswith(".py")]


class _FakeResponse:
    def __init__(self, payload, status_code=200, url="https://example.com"):
        self.payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = json.dumps(payload, ensure_ascii=False)
        self.content = self.text.encode("utf-8")
        self.headers = {"content-type": "application/json"}
        self.url = url
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def json(self):
        return self.payload


def _runtime_smoke_test(root: Path) -> None:
    requests_stubbed = False
    try:
        import requests  # noqa: F401
    except ImportError:
        requests_stub = types.ModuleType("requests")
        requests_stub.get = lambda *args, **kwargs: None
        requests_stub.post = lambda *args, **kwargs: None
        requests_stub.Timeout = type("Timeout", (Exception,), {})
        requests_stub.RequestException = type(
            "RequestException",
            (Exception,),
            {},
        )
        sys.modules["requests"] = requests_stub
        requests_stubbed = True

    sys.path.insert(0, str(root))
    loaded_names = [
        "contact_matching",
        "kakao_local_client",
        "localdata_contact_client",
        "naver_web_search_client",
        "website_contact_parser",
        "contact_enrichment",
    ]
    try:
        for name in loaded_names:
            sys.modules.pop(name, None)
        matching = importlib.import_module("contact_matching")
        kakao = importlib.import_module("kakao_local_client")
        localdata = importlib.import_module("localdata_contact_client")
        naver = importlib.import_module("naver_web_search_client")
        website = importlib.import_module("website_contact_parser")
        enrichment = importlib.import_module("contact_enrichment")

        if matching.normalize_company_name("(주) 오아시스") != "오아시스":
            raise RuntimeError("회사명 정규화 점검 실패")
        if matching.normalize_phone("02-1234-5678") != "02-1234-5678":
            raise RuntimeError("전화번호 정규화 점검 실패")
        phones, emails = website.extract_public_contacts(
            "대표전화 02-1234-5678 문의 sales@example.com"
        )
        if "02-1234-5678" not in phones or "sales@example.com" not in emails:
            raise RuntimeError("홈페이지 연락처 추출 점검 실패")
        if (
            enrichment._verification_status(
                95,
                is_email=True,
                email_domain_match=False,
            )
            != "review_required"
        ):
            raise RuntimeError("이메일 도메인 검증 점검 실패")

        old_values = {
            "KAKAO_REST_API_KEY": os.environ.get("KAKAO_REST_API_KEY"),
            "NAVER_CLIENT_ID": os.environ.get("NAVER_CLIENT_ID"),
            "NAVER_CLIENT_SECRET": os.environ.get("NAVER_CLIENT_SECRET"),
            "DATA_GO_KR_SERVICE_KEY": os.environ.get("DATA_GO_KR_SERVICE_KEY"),
        }
        os.environ.update(
            {
                "KAKAO_REST_API_KEY": "test-kakao",
                "NAVER_CLIENT_ID": "test-naver-id",
                "NAVER_CLIENT_SECRET": "test-naver-secret",
                "DATA_GO_KR_SERVICE_KEY": "test-data-go",
            }
        )
        originals = {
            "kakao": kakao.requests.get,
            "naver": naver.requests.get,
            "localdata": localdata.requests.get,
        }
        try:
            kakao.requests.get = lambda *args, **kwargs: _FakeResponse(
                {
                    "documents": [
                        {
                            "place_name": "(주)오아시스",
                            "road_address_name": "서울특별시 강남구 테헤란로 1",
                            "phone": "02-1234-5678",
                            "place_url": "https://place.map.kakao.com/1",
                        }
                    ]
                }
            )
            kakao_result = kakao.search_company(
                "(주)오아시스",
                "서울특별시 강남구 테헤란로 1",
            )
            if not kakao_result.get("candidates"):
                raise RuntimeError("카카오 후보 변환 점검 실패")

            naver.requests.get = lambda *args, **kwargs: _FakeResponse(
                {
                    "items": [
                        {
                            "title": "오아시스 공식 홈페이지",
                            "description": "오아시스 회사소개",
                            "link": "https://oasis-example.co.kr",
                        }
                    ]
                }
            )
            naver_result = naver.search_official_websites(
                "(주)오아시스",
                "서울특별시 강남구",
            )
            if not naver_result.get("candidates"):
                raise RuntimeError("네이버 홈페이지 후보 변환 점검 실패")

            localdata.requests.get = lambda *args, **kwargs: _FakeResponse(
                {
                    "response": {
                        "header": {"resultCode": "00", "resultMsg": "OK"},
                        "body": {
                            "totalCount": 1,
                            "items": {
                                "item": [
                                    {
                                        "BPLC_NM": "(주)오아시스",
                                        "ROAD_NM_ADDR": (
                                            "서울특별시 강남구 테헤란로 1"
                                        ),
                                        "SITE_TEL": "02-1234-5678",
                                        "SALS_STTS_NM": "영업",
                                    }
                                ]
                            },
                        },
                    }
                }
            )
            first_service = next(iter(localdata.SERVICES))
            local_result = localdata._search_service(
                first_service,
                "(주)오아시스",
                "서울특별시 강남구 테헤란로 1",
                timeout=1,
            )
            if not local_result.get("candidates"):
                raise RuntimeError("인허가 API 후보 변환 점검 실패")

            original_kakao_search = enrichment.kakao_local_client.search_company
            original_local_search = (
                enrichment.localdata_contact_client.search_company
            )
            original_naver_search = (
                enrichment.naver_web_search_client.search_official_websites
            )
            try:
                enrichment.kakao_local_client.search_company = (
                    lambda *args, **kwargs: {
                        "status": "SUCCESS",
                        "message": "mock",
                        "candidates": kakao_result["candidates"],
                    }
                )
                enrichment.localdata_contact_client.search_company = (
                    lambda *args, **kwargs: {
                        "status": "SUCCESS",
                        "message": "mock",
                        "candidates": [],
                        "services": [],
                    }
                )
                enrichment.naver_web_search_client.search_official_websites = (
                    lambda *args, **kwargs: {
                        "status": "SUCCESS",
                        "message": "mock",
                        "candidates": [],
                    }
                )
                enriched = enrichment.enrich_company(
                    {
                        "company_name": "(주)오아시스",
                        "address": "서울특별시 강남구 테헤란로 1",
                    }
                )
                if not any(
                    row.get("contact_value") == "02-1234-5678"
                    for row in enriched.get("contacts", [])
                ):
                    raise RuntimeError("연락처 순차 보강 실행 점검 실패")
            finally:
                enrichment.kakao_local_client.search_company = (
                    original_kakao_search
                )
                enrichment.localdata_contact_client.search_company = (
                    original_local_search
                )
                enrichment.naver_web_search_client.search_official_websites = (
                    original_naver_search
                )
        finally:
            kakao.requests.get = originals["kakao"]
            naver.requests.get = originals["naver"]
            localdata.requests.get = originals["localdata"]
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        ui_source = (root / "prospect_db_center.py").read_text(encoding="utf-8")
        repository_source = (root / "prospect_db_repository.py").read_text(
            encoding="utf-8"
        )
        sql_source = (
            root / "supabase_v970_contact_enrichment.sql"
        ).read_text(encoding="utf-8")
        for marker in (
            "카카오 로컬 → 승인 인허가 API → 네이버 웹검색 → 공식 홈페이지",
            "contact_enrichment_form_v970",
            "save_prospect_contacts",
        ):
            if marker not in ui_source:
                raise RuntimeError(f"연락처 보강 화면 기능 누락: {marker}")
        for marker in (
            "TABLE_CONTACTS",
            "manual_verified",
            "do_not_contact",
        ):
            if marker not in repository_source + sql_source:
                raise RuntimeError(f"연락처 보호 기능 누락: {marker}")
    finally:
        if sys.path and sys.path[0] == str(root):
            sys.path.pop(0)
        for name in loaded_names:
            sys.modules.pop(name, None)
        if requests_stubbed:
            sys.modules.pop("requests", None)


def main() -> int:
    root = Path(__file__).resolve().parent
    payload = root / "payload"
    version_file = root / "VERSION.txt"
    current = (
        version_file.read_text(encoding="utf-8-sig").strip()
        if version_file.exists()
        else ""
    )
    if current != EXPECTED:
        print(
            f"UPDATE_FAILED: Expected {EXPECTED} "
            f"but found {current or 'UNKNOWN'}"
        )
        return 1

    missing = [name for name in FILES if not (payload / name).exists()]
    if missing:
        print(f"UPDATE_FAILED: Missing payload: {', '.join(missing)}")
        return 1

    backup = (
        root
        / "_update_backups"
        / f"{EXPECTED}_before_{TARGET}_{datetime.now():%Y%m%d_%H%M%S}"
    )
    backup.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    created: list[str] = []

    try:
        for name in FILES:
            source = payload / name
            target = root / name
            if target.exists():
                backup_file = backup / name
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_file)
            else:
                created.append(name)
            shutil.copy2(source, target)
            copied.append(name)

        for name in COMPILE_FILES:
            py_compile.compile(str(root / name), doraise=True)
        _runtime_smoke_test(root)

        if os.environ.get("OASIS_UPDATE_FORCE_FAIL") == "1":
            raise RuntimeError("강제 롤백 테스트")

        print("UPDATE_OK")
        print(f"VERSION={TARGET}")
        print("CONTACT_CHAIN=KAKAO_LOCALDATA_NAVER_WEBSITE")
        print("APPROVED_LOCALDATA_APIS=6")
        print("CONTACT_VERIFICATION=CONFIDENCE_AND_REVIEW")
        print("PY_COMPILE=OK")
        print("RUNTIME_SMOKE_TEST=OK")
        print("DB_SCHEMA=ADD_ONLY_SQL_INCLUDED")
        print(f"BACKUP={backup}")
        return 0
    except Exception as exc:
        print(f"UPDATE_FAILED: {exc}")
        for name in reversed(copied):
            target = root / name
            backup_file = backup / name
            if backup_file.exists():
                shutil.copy2(backup_file, target)
            elif name in created and target.exists():
                target.unlink()
        print(f"ROLLBACK_OK={backup}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
