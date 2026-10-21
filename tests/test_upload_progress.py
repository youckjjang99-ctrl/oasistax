from __future__ import annotations

import ast
import hashlib
import shutil
import sys
import types
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

from upload_progress import (
    UploadProgress, remember_upload_feedback, render_upload_feedback,
    render_upload_ready,
)


class FakeUI:
    def __init__(self):
        self.session_state = {}
        self.cards = []

    def empty(self):
        return self

    def markdown(self, text, **kwargs):
        self.cards.append(text)


def test_running_card_is_immediate_and_only_explicit_completion_is_success():
    ui = FakeUI()
    with UploadProgress("owner:document", "자료 분석", steps=("확인", "분석", "저장"), ui=ui) as task:
        assert 'data-state="running"' in ui.cards[-1]
        assert 'role="status"' in ui.cards[-1]
        task.step("분석", "실제 단계를 표시합니다.")
    assert 'data-state="warning"' in ui.cards[-1]
    assert 'data-state="complete"' not in ui.cards[-1]


def test_failure_does_not_render_raw_exception_or_false_success():
    ui = FakeUI()
    with pytest.raises(RuntimeError):
        with UploadProgress("a", "분석", ui=ui):
            raise RuntimeError("private customer content and secret")
    assert 'data-state="error"' in ui.cards[-1]
    assert "private customer" not in ui.cards[-1]


def test_feedback_survives_rerun_is_scoped_and_consumed_once():
    ui = FakeUI()
    with UploadProgress("owner:a", "저장", ui=ui) as task:
        task.warning("일부 연결 확인 필요", "로컬 보존", persist=True)
    render_upload_feedback("other:a", ui=ui)
    assert len(ui.cards) == 2
    render_upload_feedback("owner:a", ui=ui)
    assert "일부 연결 확인 필요" in ui.cards[-1]
    previous_count = len(ui.cards)
    render_upload_feedback("owner:a", ui=ui)
    assert len(ui.cards) == previous_count


def test_feedback_and_stage_content_is_html_escaped():
    ui = FakeUI()
    remember_upload_feedback("a", "<script>bad()</script>", '<img src=x onerror="bad()">', ui=ui)
    render_upload_feedback("a", ui=ui)
    assert "<script>" not in ui.cards[-1]
    assert "&lt;script&gt;" in ui.cards[-1]
    assert "<img " not in ui.cards[-1]


def test_ready_hint_uses_metadata_without_reading_upload_bytes():
    ui = FakeUI()
    item = SimpleNamespace(size=1024 * 1024, getvalue=Mock(side_effect=AssertionError("no reread")))
    render_upload_ready([item, item], ui=ui)
    assert "파일 2개 준비됨 · 2.0MB" in ui.cards[-1]


def load_app_function(name, namespace):
    source = ast.parse((Path(__file__).parents[1] / "app.py").read_text(encoding="utf-8"))
    node = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
    return namespace[name]


def excel_context(tmp_path, monkeypatch):
    import customer_asset_storage
    ui = FakeUI()
    append = Mock(return_value=(tmp_path / "customers.xlsx", 1, pd.DataFrame([{"id": "synthetic"}])))
    cloud = Mock(return_value={"saved": 1, "queued": 0, "skipped": 0, "failed": 0})
    asset = Mock(return_value=SimpleNamespace(degraded=False))
    monkeypatch.setattr(customer_asset_storage, "store_private_customer_asset", asset)
    namespace = {"st": ui, "Path": Path, "hashlib": hashlib,
                 "UploadProgress": lambda *a, **kw: UploadProgress(*a, **kw, ui=ui),
                 "make_upload_filename": lambda _: "synthetic.xlsx",
                 "validate_customer_workbook": Mock(return_value=(True, [], [])),
                 "append_user_customer_db": append, "sync_customer_snapshots": cloud,
                 "write_runtime_error": Mock()}
    process = load_app_function("_process_customer_excel_upload", namespace)
    uploaded = BytesIO(b"synthetic spreadsheet bytes")
    uploaded.name = "synthetic.xlsx"
    return ui, process, uploaded, namespace, append, cloud, asset


def test_excel_rerun_does_not_append_or_sync_same_file_again(tmp_path, monkeypatch):
    ui, process, file, _, append, cloud, asset = excel_context(tmp_path, monkeypatch)
    first = process(file, "owner", "manager", tmp_path)
    second = process(file, "owner", "manager", tmp_path)
    assert first == second
    append.assert_called_once()
    assert append.call_args.kwargs["raise_on_error"] is True
    cloud.assert_called_once()
    asset.assert_called_once()
    assert "중복 저장하지 않았습니다" in ui.cards[-1]


def test_excel_failure_has_no_success_receipt_and_can_retry(tmp_path, monkeypatch):
    ui, process, file, _, append, _, _ = excel_context(tmp_path, monkeypatch)
    append.side_effect = OSError("synthetic disk error")
    with pytest.raises(OSError):
        process(file, "owner", "manager", tmp_path)
    assert not ui.session_state
    assert 'data-state="error"' in ui.cards[-1]


def test_excel_partial_cloud_failure_keeps_local_receipt_without_repeat_append(tmp_path, monkeypatch):
    ui, process, file, _, append, cloud, _ = excel_context(tmp_path, monkeypatch)
    cloud.side_effect = RuntimeError("synthetic cloud failure")
    result = process(file, "owner", "manager", tmp_path)
    assert result["valid"] and result["warnings"]
    process(file, "owner", "manager", tmp_path)
    append.assert_called_once()
    assert 'data-state="warning"' in ui.cards[-1]


def test_excel_invalid_workbook_is_not_registered(tmp_path, monkeypatch):
    _, process, file, namespace, append, _, _ = excel_context(tmp_path, monkeypatch)
    namespace["validate_customer_workbook"].return_value = (False, ["missing sheet"], [])
    assert not process(file, "owner", "manager", tmp_path)["valid"]
    append.assert_not_called()


def test_excel_utils_opt_in_propagates_save_error_legacy_tuple_is_compatible(tmp_path, monkeypatch):
    import utils
    monkeypatch.setattr(utils, "get_user_cumulative_db_path", lambda _: tmp_path / "customers.xlsx")
    monkeypatch.setattr(utils.pd, "read_excel", Mock(side_effect=OSError("synthetic disk failure")))
    assert utils.append_user_customer_db("fixture.xlsx", "owner")[1] == 0
    with pytest.raises(OSError):
        utils.append_user_customer_db("fixture.xlsx", "owner", return_saved_rows=True, raise_on_error=True)


def test_income_file_change_clears_previous_analysis_only():
    ui = FakeUI()
    ui.session_state.update({"income_tax_parsed": {"old": True}, "income_tax_error": "old",
                             "income_tax_selected_businesses": ["old"], "income_tax_pdf_path": "old",
                             "income_tax_pdf_uploader": "new", "income_tax_manager_name": "manager",
                             "other": "keep"})
    reset = load_app_function("_reset_income_tax_upload_state", {"st": ui})
    reset()
    assert ui.session_state == {"income_tax_pdf_uploader": "new", "income_tax_manager_name": "manager", "other": "keep"}


def test_global_styles_do_not_hide_actual_streamlit_running_indicator():
    source = (Path(__file__).parents[1] / "ui.py").read_text(encoding="utf-8")
    hidden = source.split("#MainMenu,", 1)[1].split("}", 1)[0]
    assert 'data-testid="stStatusWidget"' not in hidden


def income_app(tmp_path, monkeypatch, *, parse_error="", failed_save=False):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import income_tax_return_parser
    import corporate_conversion_analyzer
    from runtime_error_log import safe_public_error

    module = types.ModuleType("_oasis_income_upload_test")
    module.__dict__.update({"st": st, "Path": Path, "shutil": shutil, "pd": pd,
                           "UploadProgress": UploadProgress, "render_upload_ready": render_upload_ready,
                           "make_upload_filename": lambda _: "synthetic-tax.pdf",
                           "safe_public_error": safe_public_error, "write_runtime_error": Mock(),
                           "save_customer_snapshot": Mock(), "sync_customer_snapshot": Mock(return_value=(True, "")),
                           "append_income_tax_business_to_user_customer_db": Mock(
                               side_effect=OSError("synthetic failure") if failed_save else None)})
    load_app_function("_reset_income_tax_upload_state", module.__dict__)
    load_app_function("render_personal_business_registration", module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    file = BytesIO(b"synthetic PDF fixture")
    file.name, file.size = "synthetic-tax.pdf", 21
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: file)
    monkeypatch.setattr(income_tax_return_parser, "parse_income_tax_return", Mock(return_value=({
        "businesses": [] if parse_error else [{"업체명": "검증용 사업장", "사업자등록번호": "".join(("123", "45", "67890")),
                                               "매출액": 100, "필요경비": 80, "사업소득금액": 20}],
    }, parse_error)))
    monkeypatch.setattr(corporate_conversion_analyzer, "analyze_corporate_conversion", lambda *a: {
        "score": 50, "grade": "검토", "metrics": {"소득률": 20}, "summary": "합성 검증용",
        "reasons": [], "cautions": [], "action_plan": [],
    })
    app = AppTest.from_string(
        "from _oasis_income_upload_test import render_personal_business_registration\n"
        f"render_personal_business_registration('owner', '검증담당자', {str(tmp_path)!r})\n"
    ).run()
    return app


def test_income_analysis_error_is_visible_and_does_not_reuse_old_result(tmp_path, monkeypatch):
    app = income_app(tmp_path, monkeypatch, parse_error="검증용 분석 실패")
    app.button(key="income_tax_analyze_button").click().run()
    assert not app.exception
    assert app.session_state["income_tax_parsed"] == {"businesses": []}
    assert any("검증용 분석 실패" in item.value for item in app.error)
    assert any('data-state="error"' in item.value for item in app.markdown)


def test_income_registration_failure_is_not_counted_as_successful_update(tmp_path, monkeypatch):
    app = income_app(tmp_path, monkeypatch, failed_save=True)
    app.button(key="income_tax_analyze_button").click().run()
    assert not app.exception
    app.button(key="income_tax_register_selected").click().run()
    assert not app.exception
    text = "\n".join(item.value for item in app.markdown)
    assert "기존자료 처리 0건 · 실패 1건" in text
    assert 'data-state="warning"' in text
