from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

import registry_runner
import stock_valuation as stock

BUSINESS = "-".join(("123", "45", "67890"))
CORPORATE = "-".join(("110111", "1234567"))
OTHER_CORPORATE = "-".join(("110111", "7654321"))


def test_restore_never_overwrites_corrupt_existing_cache(monkeypatch, tmp_path):
    path = tmp_path / "registry_cache.json"
    original = b"{corrupt registry cache"
    path.write_bytes(original)
    monkeypatch.setattr(stock, "get_user_dirs", lambda *a: {"base": tmp_path})
    cloud = Mock(return_value={"법인명": "검증용"})
    monkeypatch.setattr(stock, "load_registry_snapshot", cloud)
    monkeypatch.setattr(stock.st, "warning", lambda *a, **kw: None)
    assert stock._restore_registry_for_business("owner", BUSINESS) == {}
    assert path.read_bytes() == original
    cloud.assert_not_called()


def test_registry_actual_streamlit_rerun_keeps_saved_confirmation(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    import enterprise_documents

    monkeypatch.setattr(stock.st, "file_uploader", lambda *a, **kw: SimpleNamespace(
        size=20, getvalue=lambda: b"%PDF-1.4 synthetic",
    ))
    worker = Mock(return_value=({"법인명": "검증법인", "자본금": 5000, "발행주식총수": 1000}, ""))
    monkeypatch.setattr(registry_runner, "run_registry_worker", worker)
    monkeypatch.setattr(stock, "get_user_dirs", lambda *a: {"base": tmp_path, "uploads": tmp_path / "uploads"})
    monkeypatch.setattr(stock, "sync_registry_snapshot", lambda *a: (True, ""))
    monkeypatch.setattr(enterprise_documents, "register_existing_enterprise_document", Mock(return_value={
        "asset_storage_status": {"cloud_saved": True, "metadata_saved": True},
        "history_storage_status": {"cloud_saved": True},
    }))
    app = AppTest.from_string(
        "import pandas as pd\nimport streamlit as st\nfrom stock_valuation import _render_registry_upload\n"
        f"st.session_state.setdefault('stock_business_no', {BUSINESS!r})\n"
        "st.session_state.setdefault('stock_company_name', '검증법인')\n"
        f"st.session_state.setdefault('stock_registry_restored_key', {BUSINESS!r})\n"
        f"_render_registry_upload('owner', pd.Series({{'업체명':'검증법인', '사업자등록번호':{BUSINESS!r}}}))\n"
    ).run()
    app.button(key="stock_analyze_registry").click().run()
    assert not app.exception
    worker.assert_called_once()
    assert app.session_state["stock_capital"] == "5,000"
    assert any("등기자료 분석·등록이 완료되었습니다" in item.value for item in app.markdown)
    app.run()
    worker.assert_called_once()


def test_worker_timeout_has_safe_retry_message(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("private-path", 90)

    monkeypatch.setattr(registry_runner.subprocess, "run", fail)
    data, error = registry_runner.run_registry_worker(tmp_path / "source.pdf")
    assert data == {}
    assert "시간이 초과" in error
    assert "private-path" not in error


def test_worker_does_not_expose_subprocess_output(monkeypatch, tmp_path):
    monkeypatch.setattr(registry_runner.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stderr="customer-address secret-key", stdout="private-content",
    ))
    data, error = registry_runner.run_registry_worker(tmp_path / "source.pdf")
    assert data == {}
    assert "읽지 못" in error
    assert "secret" not in error and "private" not in error


@pytest.mark.parametrize("payload", [[], {}, {"법인명": "", "자본금": None}, {"자본금": 1000}])
def test_worker_rejects_empty_or_unidentified_result(monkeypatch, tmp_path, payload):
    def run(command, **kwargs):
        Path(command[-1]).write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(registry_runner.subprocess, "run", run)
    data, error = registry_runner.run_registry_worker(tmp_path / "source.pdf")
    assert data == {}
    assert error


def test_worker_returns_identified_result(monkeypatch, tmp_path):
    def run(command, **kwargs):
        Path(command[-1]).write_text(json.dumps({"법인명": "테스트법인", "자본금": 1000}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(registry_runner.subprocess, "run", run)
    data, error = registry_runner.run_registry_worker(tmp_path / "source.pdf")
    assert error == ""
    assert data["자본금"] == 1000


def test_registry_cache_preserves_confirmed_values_and_reports_pending(monkeypatch, tmp_path):
    previous = {BUSINESS: {"법인명": "테스트법인", "자본금": 5000, "발행할주식총수": 2000}}
    path = tmp_path / "registry_cache.json"
    path.write_text(json.dumps(previous), encoding="utf-8")
    monkeypatch.setattr(stock, "get_user_dirs", lambda *a: {"base": tmp_path})
    synced = []
    monkeypatch.setattr(stock, "sync_registry_snapshot", lambda *a: synced.append(a[2]) or (False, "pending"))
    result = {}

    key = stock._save_registry_cache("member", BUSINESS.replace("-", ""), {
        "법인명": "테스트법인", "자본금": None, "발행할주식총수": "", "발행주식총수": 1000,
    }, storage_result=result)

    assert key == BUSINESS
    saved = json.loads(path.read_text(encoding="utf-8"))[key]
    assert saved["자본금"] == 5000
    assert saved["발행할주식총수"] == 2000
    assert saved["발행주식총수"] == 1000
    assert result["cloud_saved"] is False
    assert result["registry_data"] == saved == synced[0]
    assert not list(tmp_path.glob("*.tmp"))


def test_registry_partial_apply_preserves_existing_capital_and_financial(monkeypatch):
    state = {"stock_capital": "5,000", "stock_authorized_shares": "2,000", "stock_net_income_1": "900"}
    monkeypatch.setattr(stock.st, "session_state", state)
    stock._apply_registry_data({"법인명": "테스트법인", "자본금": None, "발행할주식총수": ""})
    assert state["stock_capital"] == "5,000"
    assert state["stock_authorized_shares"] == "2,000"
    assert state["stock_net_income_1"] == "900"


@pytest.mark.parametrize("original", [b"{broken-json", b"[]", b'{"existing": null}'])
def test_registry_write_refuses_unreadable_existing_cache(monkeypatch, tmp_path, original):
    path = tmp_path / "registry_cache.json"
    path.write_bytes(original)
    monkeypatch.setattr(stock, "get_user_dirs", lambda *a: {"base": tmp_path})
    monkeypatch.setattr(stock, "sync_registry_snapshot", lambda *a: pytest.fail("must not sync"))
    with pytest.raises(stock.RegistryCacheReadError):
        stock._save_registry_cache("member", BUSINESS, {"법인명": "테스트법인"})
    assert path.read_bytes() == original


def test_registry_rejects_other_corporation_but_accepts_name_change():
    selected = {"업체명": "테스트법인", "법인등록번호": CORPORATE}
    assert stock._registry_identity_error({"법인명": "다른법인", "법인등록번호": OTHER_CORPORATE}, selected)
    assert stock._registry_identity_error({"법인명": "구상호", "법인등록번호": CORPORATE}, selected) == ""
    assert stock._registry_identity_error({"법인명": "주식회사 테스트법인"}, {"업체명": "(주)테스트법인"}) == ""


def test_registry_requires_positive_comparable_identity():
    selected = {"업체명": "테스트법인", "사업자등록번호": BUSINESS}
    assert "식별정보가 부족" in stock._registry_identity_error(
        {"법인등록번호": OTHER_CORPORATE, "자본금": 1000}, selected,
    )
    assert stock._registry_identity_error({
        "법인등록번호": OTHER_CORPORATE, "사업자등록번호": BUSINESS.replace("-", ""),
    }, selected) == ""


class Progress:
    def __init__(self, key, title, *, steps, ui):
        self.key = key
        self.ui = ui
        self.ui.events.append(("started", key))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def step(self, label, detail=""):
        self.ui.events.append(("step", label))

    def _terminal(self, state, message, detail, persist):
        self.ui.events.append((state, message, detail, persist))
        if persist:
            self.ui.session_state["feedback"] = (self.key, state, message, detail)

    def complete(self, message, detail="", *, persist=False):
        self._terminal("complete", message, detail, persist)

    def warning(self, message, detail="", *, persist=False):
        self._terminal("warning", message, detail, persist)

    def error(self, message, detail="", *, persist=False):
        self._terminal("error", message, detail, persist)


def setup_upload(monkeypatch, tmp_path, result, *, cloud=True, attachment=True):
    import enterprise_documents

    selected = pd.Series({"업체명": "테스트법인", "사업자등록번호": BUSINESS, "_customer_id": "synthetic"})
    state = {"stock_business_no": BUSINESS, "stock_company_name": "테스트법인",
             "stock_registry_restored_key": BUSINESS, "stock_net_income_1": "900"}
    ui = SimpleNamespace(session_state=state, events=[])
    ui.markdown = ui.caption = ui.info = lambda *a, **k: None
    ui.file_uploader = lambda *a, **k: SimpleNamespace(getvalue=lambda: b"%PDF-1.4 synthetic")
    ui.button = lambda *a, **k: True
    ui.rerun = lambda: ui.events.append(("rerun",))
    monkeypatch.setattr(stock, "st", ui)
    monkeypatch.setattr(stock, "UploadProgress", Progress)
    monkeypatch.setattr(stock, "render_upload_feedback", lambda *a, **k: None)
    monkeypatch.setattr(stock, "render_upload_ready", lambda *a, **k: None)
    monkeypatch.setattr(stock, "get_user_dirs", lambda *a: {"base": tmp_path, "uploads": tmp_path / "uploads"})
    monkeypatch.setattr(stock, "sync_registry_snapshot", lambda *a: (cloud, ""))
    monkeypatch.setattr(registry_runner, "run_registry_worker", lambda *a, **k: (result, ""))

    def register(*args, **kwargs):
        ui.events.append(("attachment",))
        if not attachment:
            raise OSError("sensitive filename")
        return {"asset_storage_status": {"cloud_saved": True, "metadata_saved": True},
                "history_storage_status": {"cloud_saved": True}}

    monkeypatch.setattr(enterprise_documents, "register_existing_enterprise_document", register)
    monkeypatch.setattr(enterprise_documents._load_enterprise_source_overview_cached, "clear", lambda: None)
    monkeypatch.setattr(stock, "write_runtime_error", lambda *a, **k: "")
    return ui, selected


def test_registry_upload_preserves_terminal_feedback_before_rerun(monkeypatch, tmp_path):
    ui, selected = setup_upload(monkeypatch, tmp_path, {"법인명": "테스트법인", "자본금": 1000})
    stock._render_registry_upload("member", selected)
    assert ui.events[0][0] == "started"
    assert ui.events[-2][0] == "complete"
    assert ui.events[-2][3] is True
    assert ui.events[-1] == ("rerun",)
    assert ui.session_state["feedback"][0] == f"registry_upload:member:{BUSINESS}"
    assert ui.session_state["stock_net_income_1"] == "900"


@pytest.mark.parametrize("cloud,attachment", [(False, True), (True, False), (False, False)])
def test_registry_partial_storage_is_warning_not_success(monkeypatch, tmp_path, cloud, attachment):
    ui, selected = setup_upload(monkeypatch, tmp_path, {"법인명": "테스트법인", "자본금": 1000}, cloud=cloud, attachment=attachment)
    stock._render_registry_upload("member", selected)
    assert not any(event[0] == "complete" for event in ui.events)
    terminal = next(event for event in ui.events if event[0] == "warning")
    assert terminal[3] is True
    if not cloud:
        assert "로컬" in terminal[2]
    if not attachment:
        assert "첨부자료" in terminal[2]
    assert ui.session_state["stock_capital"] == "1,000"


@pytest.mark.parametrize("data", [{}, {"법인명": "다른법인", "자본금": 999}])
def test_registry_invalid_result_does_not_mutate_existing_data(monkeypatch, tmp_path, data):
    ui, selected = setup_upload(monkeypatch, tmp_path, data)
    ui.session_state["stock_capital"] = "5,000"
    stock._render_registry_upload("member", selected)
    assert ui.session_state["stock_capital"] == "5,000"
    assert not (tmp_path / "registry_cache.json").exists()
    assert any(event[0] == "error" for event in ui.events)
    assert not any(event[0] in {"attachment", "rerun", "complete"} for event in ui.events)
