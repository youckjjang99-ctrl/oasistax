from __future__ import annotations

import importlib.util
import os
import py_compile
import shutil
import sys
import types
from datetime import datetime
from pathlib import Path


EXPECTED = "v9.4.4"
TARGET = "v9.5.0"
FILES = [
    "app.py",
    "public_data_api.py",
    "prospect_db_center.py",
    "VERSION.txt",
]
COMPILE_FILES = [
    "app.py",
    "public_data_api.py",
    "prospect_db_center.py",
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
            "_oasis_public_data_v950_test",
        )
    finally:
        if requests_stubbed:
            sys.modules.pop("requests", None)

    class _FakeResponse:
        status_code = 200
        ok = True
        text = '{"response":{"header":{"resultCode":"00","resultMsg":"NORMAL SERVICE."},"body":{"items":{"item":[{"wkplNm":"테스트사업장"}]},"numOfRows":1,"pageNo":1,"totalCount":1}}}'
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
        result = api.test_nps_connection("11")
    finally:
        api.requests.get = original_get
        if original_key is None:
            os.environ.pop(api.SERVICE_KEY_ENV, None)
        else:
            os.environ[api.SERVICE_KEY_ENV] = original_key

    if not result.get("ok"):
        raise RuntimeError("국민연금 API 응답 파싱 점검 실패")
    if captured.get("params", {}).get("serviceKey") != "abc+123=":
        raise RuntimeError("공공데이터 인증키 Encoding 처리 점검 실패")
    if result.get("total_count") != 1:
        raise RuntimeError("국민연금 API 전체 건수 파싱 점검 실패")
    if result.get("sample", [{}])[0].get("wkplNm") != "테스트사업장":
        raise RuntimeError("국민연금 API 사업장 샘플 파싱 점검 실패")

    app_source = (root / "app.py").read_text(encoding="utf-8")
    ui_source = (root / "prospect_db_center.py").read_text(encoding="utf-8")
    for marker in (
        "from prospect_db_center import render_prospect_db_center",
        'menu_label_map["영업후보DB"]',
        'elif active_tab == "영업후보DB":',
    ):
        if marker not in app_source:
            raise RuntimeError(f"영업후보DB 메뉴 연결 누락: {marker}")
    for marker in (
        "DATA_GO_KR_SERVICE_KEY",
        "국민연금 API 연결 테스트",
        "조회 결과는 고객DB나 Supabase에 저장하지 않습니다.",
    ):
        if marker not in ui_source:
            raise RuntimeError(f"API 점검 화면 항목 누락: {marker}")


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
        print("PUBLIC_DATA_KEY=ENV_ONLY")
        print("NPS_API=CONNECTION_TEST_READY")
        print("PROSPECT_DB=NO_WRITE_TEST_MODE")
        print("PY_COMPILE=OK")
        print("RUNTIME_SMOKE_TEST=OK")
        print("DB_SCHEMA=PRESERVED")
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
