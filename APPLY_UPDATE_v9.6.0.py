from __future__ import annotations

import importlib.util
import os
import py_compile
import shutil
import sys
import types
from datetime import datetime
from pathlib import Path


EXPECTED = "v9.5.0"
TARGET = "v9.6.0"
FILES = [
    "app.py",
    "public_data_api.py",
    "prospect_db_center.py",
    "prospect_db_repository.py",
    "supabase_v960_prospect_db.sql",
    "VERSION.txt",
]
COMPILE_FILES = [
    "app.py",
    "public_data_api.py",
    "prospect_db_center.py",
    "prospect_db_repository.py",
]


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Import spec 생성 실패: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    try:
        api = _load_module(
            root / "public_data_api.py",
            "_oasis_public_data_v960_test",
        )
    finally:
        if requests_stubbed:
            sys.modules.pop("requests", None)

    class _FakeResponse:
        status_code = 200
        ok = True
        text = (
            '{"response":{"header":{"resultCode":"00",'
            '"resultMsg":"NORMAL SERVICE."},"body":{"items":{"item":['
            '{"seq":"1","wkplNm":"서울테스트","bzowrRgstNo":"1234567890",'
            '"wkplRoadNmDtlAddr":"서울특별시 강남구 테헤란로 1",'
            '"ldongAddrMgplDgCd":"11","jnngpCnt":"12",'
            '"nwAcqzrCnt":"3","lssJnngpCnt":"1","dataCrtYm":"202607"},'
            '{"seq":"2","wkplNm":"부산테스트",'
            '"wkplRoadNmDtlAddr":"부산광역시 중구 중앙대로 1",'
            '"jnngpCnt":"5"}'
            ']},"numOfRows":2,"pageNo":3,"totalCount":200}}}'
        )
        headers = {"content-type": "application/json"}

        @staticmethod
        def json():
            import json

            return json.loads(_FakeResponse.text)

    original_get = api.requests.get
    original_key = os.environ.get(api.SERVICE_KEY_ENV)
    captured = {}

    def _fake_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params", {})
        return _FakeResponse()

    try:
        os.environ[api.SERVICE_KEY_ENV] = "abc%2B123%3D"
        api.requests.get = _fake_get
        result = api.fetch_nps_workplaces(
            "11",
            page_no=3,
            rows=2,
            sigungu_code="680",
        )
    finally:
        api.requests.get = original_get
        if original_key is None:
            os.environ.pop(api.SERVICE_KEY_ENV, None)
        else:
            os.environ[api.SERVICE_KEY_ENV] = original_key

    if not result.get("ok"):
        raise RuntimeError("국민연금 사업장 수집 응답 파싱 실패")
    if result.get("received_count") != 2:
        raise RuntimeError("국민연금 사업장 수신 건수 점검 실패")
    if len(result.get("items", [])) != 1:
        raise RuntimeError("서울·경기 외 지역 안전 제외 점검 실패")
    sample = result["items"][0]
    if sample.get("사업자등록번호") != "123-45-67890":
        raise RuntimeError("사업자등록번호 정규화 점검 실패")
    if sample.get("우선순위점수", 0) <= 10:
        raise RuntimeError("영업후보 우선순위 계산 점검 실패")
    if captured.get("params", {}).get("serviceKey") != "abc+123=":
        raise RuntimeError("공공데이터 인증키 Encoding 처리 점검 실패")
    if captured.get("params", {}).get("ldongAddrMgplSgguCd") != "680":
        raise RuntimeError("시군구 조회조건 전달 점검 실패")

    app_source = (root / "app.py").read_text(encoding="utf-8")
    ui_source = (root / "prospect_db_center.py").read_text(encoding="utf-8")
    repo_source = (
        root / "prospect_db_repository.py"
    ).read_text(encoding="utf-8")
    sql_source = (
        root / "supabase_v960_prospect_db.sql"
    ).read_text(encoding="utf-8")
    if "render_prospect_db_center(CURRENT_USER_ID)" not in app_source:
        raise RuntimeError("영업후보DB 사용자 연결 누락")
    for marker in (
        "사업장 미리보기 수집",
        "선택한 업체를 영업후보DB에 저장",
        "Supabase 영업후보DB 생성 SQL 다운로드",
    ):
        if marker not in ui_source:
            raise RuntimeError(f"영업후보DB 화면 기능 누락: {marker}")
    for marker in (
        "remove_existing_customers",
        "save_prospects",
        "oasis_prospect_companies",
    ):
        if marker not in repo_source:
            raise RuntimeError(f"영업후보DB 저장기능 누락: {marker}")
    for marker in (
        "create table if not exists public.oasis_prospect_companies",
        "unique (source, source_key)",
        "enable row level security",
    ):
        if marker not in sql_source:
            raise RuntimeError(f"영업후보DB SQL 누락: {marker}")


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
    restored_files: list[str] = []
    created_files: list[str] = []

    try:
        for name in FILES:
            source = payload / name
            target = root / name
            if target.exists():
                backup_target = backup / name
                backup_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_target)
                restored_files.append(name)
            else:
                created_files.append(name)
            shutil.copy2(source, target)

        for name in COMPILE_FILES:
            py_compile.compile(str(root / name), doraise=True)
        _runtime_smoke_test(root)

        if os.environ.get("OASIS_UPDATE_FORCE_FAIL") == "1":
            raise RuntimeError("강제 롤백 테스트")

        print("UPDATE_OK")
        print(f"VERSION={TARGET}")
        print("NPS_PREVIEW=MAX_100")
        print("REGION_FILTER=SEOUL_GYEONGGI")
        print("CUSTOMER_DEDUP=READY")
        print("PROSPECT_DB=SEPARATE_TABLE")
        print("PY_COMPILE=OK")
        print("RUNTIME_SMOKE_TEST=OK")
        print("DB_SCHEMA=ADD_ONLY")
        print(f"BACKUP={backup}")
        return 0
    except Exception as exc:
        print(f"UPDATE_FAILED: {exc}")
        for name in restored_files:
            backup_file = backup / name
            if backup_file.exists():
                shutil.copy2(backup_file, root / name)
        for name in created_files:
            target = root / name
            if target.exists():
                target.unlink()
        print(f"ROLLBACK_OK={backup}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
