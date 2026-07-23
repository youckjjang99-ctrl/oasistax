from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import shutil
import sys
import threading
import types
from datetime import datetime
from pathlib import Path


EXPECTED = "v9.6.0"
TARGET = "v9.6.1"
FILES = [
    "public_data_api.py",
    "prospect_db_center.py",
    "VERSION.txt",
]
COMPILE_FILES = [
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
            "_oasis_public_data_v961_test",
        )
    finally:
        if requests_stubbed:
            sys.modules.pop("requests", None)

    class _FakeResponse:
        def __init__(self, payload, status_code=200):
            self.payload = payload
            self.status_code = status_code
            self.ok = 200 <= status_code < 300
            self.text = json.dumps(payload, ensure_ascii=False)
            self.headers = {"content-type": "application/json"}

        def json(self):
            return self.payload

    basic_payload = {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "items": {
                    "item": [
                        {
                            "seq": "101",
                            "wkplNm": "서울상세성공1",
                            "ldongAddrMgplDgCd": "11",
                            "ldongAddrMgplSgguCd": "680",
                        },
                        {
                            "seq": "102",
                            "wkplNm": "서울상세성공2",
                            "ldongAddrMgplDgCd": "11",
                            "ldongAddrMgplSgguCd": "680",
                        },
                        {
                            "seq": "103",
                            "wkplNm": "서울상세실패",
                            "ldongAddrMgplDgCd": "11",
                            "ldongAddrMgplSgguCd": "680",
                        },
                    ]
                },
                "totalCount": 300,
            },
        }
    }
    details = {
        "101": {
            "seq": "101",
            "wkplRoadNmDtlAddr": "서울특별시 강남구 테헤란로 1",
            "bzowrRgstNo": "1234567890",
            "jnngpCnt": "12",
            "nwAcqzrCnt": "3",
            "lssJnngpCnt": "1",
            "dataCrtYm": "202607",
        },
        "102": {
            "seq": "102",
            "wkplRoadNmDtlAddr": "서울특별시 강남구 선릉로 2",
            "bzowrRgstNo": "1112233333",
            "jnngpCnt": "7",
            "nwAcqzrCnt": "1",
            "lssJnngpCnt": "0",
            "dataCrtYm": "202607",
        },
    }
    lock = threading.Lock()
    basic_calls = 0

    def _fake_get(url, **kwargs):
        nonlocal basic_calls
        params = kwargs.get("params", {})
        if "getBassInfoSearchV2" in url:
            with lock:
                basic_calls += 1
                current = basic_calls
            if current == 1:
                raise api.requests.Timeout("first request timeout")
            return _FakeResponse(basic_payload)

        sequence = str(params.get("seq", ""))
        if sequence in details:
            payload = {
                "response": {
                    "header": {
                        "resultCode": "00",
                        "resultMsg": "NORMAL SERVICE.",
                    },
                    "body": {
                        "items": {"item": [details[sequence]]},
                        "totalCount": 1,
                    },
                }
            }
            return _FakeResponse(payload)
        error_payload = {
            "response": {
                "header": {
                    "resultCode": "99",
                    "resultMsg": "DETAIL TEMPORARY ERROR",
                },
                "body": {"items": {"item": []}, "totalCount": 0},
            }
        }
        return _FakeResponse(error_payload)

    original_get = api.requests.get
    original_sleep = api.time.sleep
    original_key = os.environ.get(api.SERVICE_KEY_ENV)
    try:
        os.environ[api.SERVICE_KEY_ENV] = "abc%2B123%3D"
        api.requests.get = _fake_get
        api.time.sleep = lambda _seconds: None
        result = api.fetch_nps_workplaces(
            "11",
            page_no=1,
            rows=3,
            timeout=30,
            retries=2,
            detail_workers=5,
        )
    finally:
        api.requests.get = original_get
        api.time.sleep = original_sleep
        if original_key is None:
            os.environ.pop(api.SERVICE_KEY_ENV, None)
        else:
            os.environ[api.SERVICE_KEY_ENV] = original_key

    if not result.get("ok"):
        raise RuntimeError("기본조회·상세조회 연동 점검 실패")
    if basic_calls != 2:
        raise RuntimeError("기본조회 타임아웃 자동 재시도 점검 실패")
    if result.get("basic_received_count") != 3:
        raise RuntimeError("기본조회 수신 건수 점검 실패")
    if result.get("detail_success_count") != 2:
        raise RuntimeError("상세조회 성공 건수 점검 실패")
    if result.get("detail_failed_count") != 1:
        raise RuntimeError("상세조회 실패 분리 점검 실패")
    if len(result.get("items", [])) != 2:
        raise RuntimeError("상세조회 성공 사업장 후보 변환 실패")
    if len(result.get("detail_failures", [])) != 1:
        raise RuntimeError("상세조회 실패 사업장 표시자료 누락")
    first = result["items"][0]
    if first.get("가입자수") != 12:
        raise RuntimeError("상세조회 가입자 수 반영 실패")
    if first.get("사업자등록번호") != "123-45-67890":
        raise RuntimeError("상세조회 사업자등록번호 반영 실패")

    api_source = (root / "public_data_api.py").read_text(encoding="utf-8")
    ui_source = (root / "prospect_db_center.py").read_text(encoding="utf-8")
    for marker in (
        "NPS_DETAIL_URL",
        "getDetailInfoSearchV2",
        "_enrich_nps_details",
        "ThreadPoolExecutor",
        "_get_with_retry",
    ):
        if marker not in api_source:
            raise RuntimeError(f"상세조회 연동 기능 누락: {marker}")
    for marker in (
        "상세조회 성공",
        "상세조회 실패",
        "실제 API 호출 시도",
        "같은 페이지를 다시 조회하면 자동으로 재시도합니다.",
    ):
        if marker not in ui_source:
            raise RuntimeError(f"상세조회 화면 표시 누락: {marker}")


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

    try:
        for name in FILES:
            source = payload / name
            target = root / name
            if not target.exists():
                raise RuntimeError(f"기존 필수 파일 누락: {name}")
            shutil.copy2(target, backup / name)
            shutil.copy2(source, target)
            copied.append(name)

        for name in COMPILE_FILES:
            py_compile.compile(str(root / name), doraise=True)
        _runtime_smoke_test(root)

        if os.environ.get("OASIS_UPDATE_FORCE_FAIL") == "1":
            raise RuntimeError("강제 롤백 테스트")

        print("UPDATE_OK")
        print(f"VERSION={TARGET}")
        print("NPS_BASIC_DETAIL=LINKED")
        print("DETAIL_WORKERS=MAX_5")
        print("TIMEOUT_SECONDS=30")
        print("AUTO_RETRY=2")
        print("DETAIL_FAILURES=VISIBLE")
        print("PY_COMPILE=OK")
        print("RUNTIME_SMOKE_TEST=OK")
        print("DB_SCHEMA=PRESERVED")
        print(f"BACKUP={backup}")
        return 0
    except Exception as exc:
        print(f"UPDATE_FAILED: {exc}")
        for name in copied:
            backup_file = backup / name
            if backup_file.exists():
                shutil.copy2(backup_file, root / name)
        print(f"ROLLBACK_OK={backup}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

