from __future__ import annotations

import importlib
import os
import py_compile
import shutil
import sys
import types
from datetime import datetime
from pathlib import Path


EXPECTED = "v9.7.0"
TARGET = "v9.7.1"
FILES = [
    "prospect_db_center.py",
    "prospect_db_repository.py",
    "localdata_contact_client.py",
    "kipris_patent_client.py",
    "sales_intelligence.py",
    "VERSION.txt",
]
COMPILE_FILES = [name for name in FILES if name.endswith(".py")]
REQUIRED_BASE_FILES = [
    "cloud_db.py",
    "public_data_api.py",
    "contact_matching.py",
    "kakao_local_client.py",
    "naver_web_search_client.py",
    "website_contact_parser.py",
    "contact_enrichment.py",
    "supabase_v960_prospect_db.sql",
    "supabase_v970_contact_enrichment.sql",
]


def _runtime_smoke_test(root: Path) -> None:
    requests_stubbed = False
    try:
        import requests  # noqa: F401
    except ImportError:
        requests_stub = types.ModuleType("requests")
        requests_stub.get = lambda *args, **kwargs: None
        requests_stub.post = lambda *args, **kwargs: None
        requests_stub.patch = lambda *args, **kwargs: None
        requests_stub.Timeout = type("Timeout", (Exception,), {})
        requests_stub.RequestException = type(
            "RequestException",
            (Exception,),
            {},
        )
        sys.modules["requests"] = requests_stub
        requests_stubbed = True

    loaded_names = [
        "contact_matching",
        "kakao_local_client",
        "localdata_contact_client",
        "kipris_patent_client",
        "sales_intelligence",
    ]
    sys.path.insert(0, str(root))
    try:
        for name in loaded_names:
            sys.modules.pop(name, None)
        kipris = importlib.import_module("kipris_patent_client")
        sales = importlib.import_module("sales_intelligence")

        sample_xml = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>OK</resultMsg></header>
  <body>
    <totalCount>1</totalCount>
    <items>
      <item>
        <applicantName>(주)오아시스</applicantName>
        <applicationNumber>10-2025-0000001</applicationNumber>
        <inventionTitle>기업지원 자동분석 시스템</inventionTitle>
        <registerNumber>10-3000001</registerNumber>
        <registerStatus>등록</registerStatus>
      </item>
    </items>
  </body>
</response>"""
        sample_xml = sample_xml.encode("utf-8")
        code, _, total_count, patent_rows = kipris._parse_xml(sample_xml)
        if code != "00" or total_count != 1:
            raise RuntimeError("KIPRIS XML 응답 해석 점검 실패")
        if not patent_rows or not patent_rows[0].get("registered"):
            raise RuntimeError("등록특허 판정 점검 실패")
        if not kipris._applicant_matches(
            "(주)오아시스",
            "오아시스 주식회사",
        ):
            raise RuntimeError("특허 출원인 회사명 일치 점검 실패")

        original_kakao_search = sales.kakao_local_client.search_company
        original_local_search = sales.localdata_contact_client.search_company
        original_patent_search = (
            sales.kipris_patent_client.search_registered_patents
        )
        try:
            sales.kakao_local_client.search_company = (
                lambda *args, **kwargs: {
                    "status": "SUCCESS",
                    "candidates": [
                        {
                            "phone": "02-1234-5678",
                            "confidence": 95,
                        }
                    ],
                }
            )
            sales.localdata_contact_client.search_company = (
                lambda *args, **kwargs: {
                    "status": "SUCCESS",
                    "candidates": [],
                    "services": [],
                }
            )
            sales.kipris_patent_client.search_registered_patents = (
                lambda *args, **kwargs: {
                    "ok": True,
                    "status": "CONNECTED",
                    "message": "등록특허 2건 확인",
                    "registered_count": 2,
                    "active_count": 2,
                    "patents": [
                        {"invention_title": "특허 A"},
                        {"invention_title": "특허 B"},
                    ],
                }
            )
            prospect = {
                "source_key": "mock-1",
                "사업장명": "(주)오아시스",
                "주소": "서울특별시 강남구 테헤란로 1",
                "업종명": "소프트웨어 개발업",
                "가입자수": 12,
                "신규취득자수": 3,
                "상실가입자수": 1,
                "우선순위점수": 40,
                "추천사유": ["국민연금 가입자 3인 이상"],
            }
            analysis = sales.analyze_sales_candidate(prospect)
            if analysis.get("phone") != "02-1234-5678":
                raise RuntimeError("대표전화 자동표시 점검 실패")
            if analysis.get("registered_patent_count") != 2:
                raise RuntimeError("등록특허 집계 점검 실패")
            if analysis.get("net_hiring") != 2:
                raise RuntimeError("순고용 증가 계산 점검 실패")
            topics = analysis.get("sales_topics") or []
            if "특허·연구개발 혜택" not in topics:
                raise RuntimeError("특허 영업주제 점검 실패")
            if "고용지원금" not in topics:
                raise RuntimeError("고용증가 영업주제 점검 실패")
            script = str(analysis.get("first_call_script") or "")
            if "20초" not in script or "특허" not in script:
                raise RuntimeError("초회 영업전화 스크립트 점검 실패")
            merged = sales.merge_analysis(prospect, analysis)
            if merged.get("우선순위점수") != analysis.get(
                "recommendation_score"
            ):
                raise RuntimeError("영업 추천점수 반영 점검 실패")
            if len(merged.get("추천사유") or []) < 2:
                raise RuntimeError("기존 추천사유 보존 점검 실패")
        finally:
            sales.kakao_local_client.search_company = original_kakao_search
            sales.localdata_contact_client.search_company = original_local_search
            sales.kipris_patent_client.search_registered_patents = (
                original_patent_search
            )

        ui_source = (root / "prospect_db_center.py").read_text(
            encoding="utf-8"
        )
        repository_source = (root / "prospect_db_repository.py").read_text(
            encoding="utf-8"
        )
        localdata_source = (root / "localdata_contact_client.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            "대표전화",
            "등록특허 보유업체만",
            "순고용 증가업체만",
            "초회 영업전화 스크립트",
            "관리자 설정 · DB 연결 상태",
            "기존 영업후보 원본데이터 보기",
            "저장된 연락처 새로고침",
            "enrich_company",
            "save_prospect_contacts",
        ):
            if marker not in ui_source:
                raise RuntimeError(f"영업후보 화면 기능 누락: {marker}")
        for marker in (
            "source_data",
            "sales_intelligence_v971",
            "save_sales_analysis",
        ):
            if marker not in repository_source:
                raise RuntimeError(f"영업분석 저장 기능 누락: {marker}")
        if "max_services" not in localdata_source:
            raise RuntimeError("빠른 연락처 탐색 제한 기능 누락")
    finally:
        if sys.path and sys.path[0] == str(root):
            sys.path.pop(0)
        for name in loaded_names:
            sys.modules.pop(name, None)
        if requests_stubbed:
            sys.modules.pop("requests", None)


def _restore(
    root: Path,
    backup: Path,
    copied: list[str],
    created: list[str],
) -> None:
    for name in reversed(copied):
        target = root / name
        backup_file = backup / name
        if backup_file.exists():
            shutil.copy2(backup_file, target)
        elif name in created and target.exists():
            target.unlink()


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

    missing_payload = [
        name for name in FILES if not (payload / name).exists()
    ]
    if missing_payload:
        print(
            "UPDATE_FAILED: Missing payload: "
            + ", ".join(missing_payload)
        )
        return 1

    missing_base = [
        name for name in REQUIRED_BASE_FILES if not (root / name).exists()
    ]
    if missing_base:
        print(
            "UPDATE_FAILED: v9.7.0 base file missing: "
            + ", ".join(missing_base)
        )
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
        print("CANDIDATE_CONTACT=VISIBLE")
        print("PATENT_FILTER=ENABLED")
        print("EMPLOYMENT_SIGNAL=ENABLED")
        print("FIRST_CALL_SCRIPT=ENABLED")
        print("DB_SCHEMA=UNCHANGED")
        print("PY_COMPILE=OK")
        print("RUNTIME_SMOKE_TEST=OK")
        print(f"BACKUP={backup}")
        return 0
    except Exception as exc:
        print(f"UPDATE_FAILED: {exc}")
        _restore(root, backup, copied, created)
        print(f"ROLLBACK_OK={backup}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
