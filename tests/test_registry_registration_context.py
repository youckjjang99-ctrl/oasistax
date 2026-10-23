from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest

import stock_valuation as stock


BUSINESS = "-".join(("123", "45", "67890"))
OTHER_BUSINESS = "-".join(("987", "65", "43210"))
CORPORATE = "-".join(("110111", "1234567"))
OTHER_CORPORATE = "-".join(("110111", "7654321"))
OWNER = "synthetic-owner"


def customer_row(business_no=BUSINESS, name="검증법인"):
    return pd.Series({
        "업체명": name,
        "사업자등록번호": business_no,
        "법인등록번호": CORPORATE,
        "_customer_id": "synthetic-customer",
        "_cloud_updated_at": "synthetic-revision",
    })


def uploader_ui(monkeypatch, state=None):
    """Exercise the real uploader without file, worker, or cloud operations."""
    ui = SimpleNamespace(session_state=state if state is not None else {}, calls=[])
    ui.markdown = ui.caption = lambda *args, **kwargs: None
    ui.info = lambda message, **kwargs: ui.calls.append(("info", message))

    def file_uploader(label, **kwargs):
        ui.calls.append(("uploader", label, kwargs))
        return None

    ui.file_uploader = file_uploader
    monkeypatch.setattr(stock, "st", ui)
    monkeypatch.setattr(stock, "render_upload_feedback", lambda *args, **kwargs: None)
    restore = Mock(return_value={})
    monkeypatch.setattr(stock, "_restore_registry_for_business", restore)
    monkeypatch.setattr(stock, "_apply_customer_financial_data", Mock(
        side_effect=AssertionError("registration must not hydrate valuation inputs"),
    ))
    return ui, restore


@pytest.mark.parametrize("state", [
    {},
    {
        "stock_customer_context_key": f"{BUSINESS}:synthetic-customer:synthetic-revision",
        "stock_auto_loaded_customer_key": f"{BUSINESS}:synthetic-customer:synthetic-revision",
        "stock_registry_restored_key": BUSINESS,
    },
])
def test_registration_upload_enabled_without_valuation_widgets(monkeypatch, state):
    ui, restore = uploader_ui(monkeypatch, deepcopy(state))

    stock._render_registry_upload(OWNER, customer_row())

    upload = next(call for call in ui.calls if call[0] == "uploader")
    assert upload[2]["disabled"] is False
    assert upload[2]["key"] == f"stock_registry_pdf:{OWNER}:{BUSINESS}"
    assert not [call for call in ui.calls if call[0] == "info"]
    restore.assert_not_called()
    assert "stock_business_no" not in ui.session_state


def test_real_registration_route_survives_rerun_and_company_switch(monkeypatch):
    from streamlit.testing.v1 import AppTest
    import enterprise_documents

    customers = pd.DataFrame([
        customer_row().to_dict(),
        customer_row(OTHER_BUSINESS, "두번째 검증법인").to_dict(),
    ])
    labels, _ = enterprise_documents.build_customer_labels(customers)
    monkeypatch.setattr(enterprise_documents, "load_registered_customers", Mock(return_value=customers))
    monkeypatch.setattr(enterprise_documents, "get_user_cumulative_db_path", lambda *args: Path("unused"))
    monkeypatch.setattr(enterprise_documents, "_render_note_input", lambda *args, **kwargs: None)
    monkeypatch.setattr(stock, "_read_customers", Mock(
        side_effect=AssertionError("the registration picker already resolved the owner-scoped row"),
    ))
    monkeypatch.setattr(stock, "_load_stock_customer_context", Mock(
        side_effect=AssertionError("registration must not use consulting or valuation context"),
    ))
    monkeypatch.setattr(stock, "_restore_registry_for_business", Mock(
        side_effect=AssertionError("no active valuation exists on this registration screen"),
    ))
    app = AppTest.from_string(
        "import streamlit as st\n"
        "from enterprise_documents import render_enterprise_information_assets\n"
        f"st.session_state.setdefault('stock_customer_context_key', '{BUSINESS}:synthetic-customer:synthetic-revision')\n"
        f"st.session_state.setdefault('stock_auto_loaded_customer_key', '{BUSINESS}:synthetic-customer:synthetic-revision')\n"
        "st.session_state.setdefault('enterprise_information_document_type', '법인등기사항증명서')\n"
        f"render_enterprise_information_assets({OWNER!r}, '검증 담당자')\n",
        default_timeout=15,
    ).run()
    assert not app.exception
    app.selectbox(key="enterprise_information_asset_customer").select(labels[0]).run()
    assert not app.exception
    app.selectbox(key="enterprise_information_document_type").select("법인등기사항증명서").run()
    assert not app.exception
    first = app.get("file_uploader")[0]
    assert first.proto.disabled is False
    assert f"stock_registry_pdf:{OWNER}:{BUSINESS}" in first.proto.id
    assert "stock_business_no" not in app.session_state
    assert not any("기업컨설팅에서 업체를 먼저" in item.value for item in app.info)

    app.run()
    assert not app.exception
    assert app.get("file_uploader")[0].proto.disabled is False
    first_id = app.get("file_uploader")[0].proto.id
    app.selectbox(key="enterprise_information_asset_customer").select(labels[1]).run()
    assert not app.exception
    second = app.get("file_uploader")[0]
    assert second.proto.disabled is False
    assert second.proto.id != first_id
    assert f"stock_registry_pdf:{OWNER}:{OTHER_BUSINESS}" in second.proto.id
    assert "stock_business_no" not in app.session_state


def test_registration_for_other_company_preserves_current_valuation(monkeypatch):
    state = {
        "stock_business_no": OTHER_BUSINESS,
        "stock_company_name": "다른 검증법인",
        "stock_corporate_no": OTHER_CORPORATE,
        "stock_net_income_1": "900",
        "stock_total_assets": "1,000",
        "stock_current_shares": "200",
        "stock_shares_1": "150",
        "stock_loaded_record_id": "synthetic-history",
        "stock_tax_adjustment_review": {"business_no": OTHER_BUSINESS},
        "stock_last_result": {"synthetic": True},
    }
    original = deepcopy(state)
    ui, restore = uploader_ui(monkeypatch, state)

    stock._render_registry_upload(OWNER, customer_row())

    upload = next(call for call in ui.calls if call[0] == "uploader")
    assert upload[2]["disabled"] is False
    assert upload[2]["key"] == f"stock_registry_pdf:{OWNER}:{BUSINESS}"
    restore.assert_not_called()
    for key, value in original.items():
        assert ui.session_state[key] == value


def test_registration_upload_saves_target_without_applying_to_other_valuation(monkeypatch, tmp_path):
    import enterprise_documents
    import registry_runner

    state = {
        "stock_business_no": OTHER_BUSINESS,
        "stock_company_name": "다른 검증법인",
        "stock_capital": "8,000",
        "stock_current_shares": "800",
        "stock_net_income_1": "900",
        "stock_shares_1": "700",
        "stock_last_registry_data": {"사업자등록번호": OTHER_BUSINESS},
        "stock_loaded_record_id": "synthetic-history",
        "stock_tax_adjustment_review": {"business_no": OTHER_BUSINESS},
    }
    original = deepcopy(state)
    ui, restore = uploader_ui(monkeypatch, state)
    ui.file_uploader = lambda *args, **kwargs: SimpleNamespace(
        getvalue=lambda: b"%PDF-1.4 synthetic registry",
    )
    ui.button = lambda *args, **kwargs: True
    ui.rerun = Mock()

    class Progress:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def step(self, *args, **kwargs):
            pass

        def complete(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

        def error(self, *args, **kwargs):
            pytest.fail("synthetic valid registry must save successfully")

    monkeypatch.setattr(stock, "UploadProgress", Progress)
    monkeypatch.setattr(stock, "render_upload_ready", lambda *args, **kwargs: None)
    monkeypatch.setattr(stock, "get_user_dirs", lambda *args: {
        "base": tmp_path, "uploads": tmp_path / "uploads",
    })
    registry = {"법인명": "검증법인", "법인등록번호": CORPORATE,
                "자본금": 1000, "발행주식총수": 100}
    monkeypatch.setattr(registry_runner, "run_registry_worker", Mock(return_value=(registry, "")))
    saved = []

    def save_registry(user_id, business_no, data, *, storage_result):
        saved.append((user_id, business_no, dict(data)))
        storage_result.update({"cloud_saved": True, "registry_data": dict(data)})
        return business_no

    monkeypatch.setattr(stock, "_save_registry_cache", save_registry)
    applied = Mock(side_effect=AssertionError("foreign company valuation must not change"))
    monkeypatch.setattr(stock, "_apply_registry_data", applied)
    linked = Mock()
    monkeypatch.setattr(enterprise_documents, "register_existing_enterprise_document", linked)
    monkeypatch.setattr(enterprise_documents._load_enterprise_source_overview_cached, "clear", lambda: None)

    stock._render_registry_upload(OWNER, customer_row())

    assert len(saved) == 1
    assert saved[0][0:2] == (OWNER, BUSINESS)
    linked.assert_called_once()
    assert linked.call_args.kwargs["business_no"] == BUSINESS
    assert linked.call_args.kwargs["company_name"] == "검증법인"
    assert linked.call_args.kwargs["customer_id"] == "synthetic-customer"
    applied.assert_not_called()
    restore.assert_not_called()
    ui.rerun.assert_called_once()
    for key, value in original.items():
        assert ui.session_state[key] == value


def test_same_company_restore_uses_selected_row_identity(monkeypatch):
    state = {
        "stock_business_no": BUSINESS.replace("-", ""),
        "stock_company_name": "오래된 입력명",
        "stock_corporate_no": "",
        "stock_net_income_1": "900",
    }
    ui, restore = uploader_ui(monkeypatch, state)

    stock._render_registry_upload(OWNER, customer_row())

    restore.assert_called_once_with(
        OWNER, BUSINESS, company_name="검증법인", corporate_no=CORPORATE,
    )
    assert ui.session_state["stock_net_income_1"] == "900"


@pytest.mark.parametrize("row", [None, customer_row(""), customer_row("invalid")])
def test_missing_or_invalid_selected_row_never_uses_stale_valuation_context(monkeypatch, row):
    ui, restore = uploader_ui(monkeypatch, {
        "stock_business_no": OTHER_BUSINESS, "stock_company_name": "다른 검증법인",
    })

    stock._render_registry_upload(OWNER, row)

    upload = next(call for call in ui.calls if call[0] == "uploader")
    assert upload[2]["disabled"] is True
    assert upload[2]["key"] == f"stock_registry_pdf:{OWNER}:unselected"
    assert any(call[0] == "info" for call in ui.calls)
    assert not any("기업컨설팅에서" in str(call) for call in ui.calls)
    restore.assert_not_called()


def test_selected_customer_is_passed_directly_without_reread_or_financial_context(monkeypatch):
    selected = customer_row()
    render = Mock()
    monkeypatch.setattr(stock, "_render_registry_upload", render)
    monkeypatch.setattr(stock, "_read_customers", Mock(
        side_effect=AssertionError("already authorized row must not be reread"),
    ))
    monkeypatch.setattr(stock, "_load_stock_customer_context", Mock(
        side_effect=AssertionError("document registration is not stock valuation"),
    ))

    stock.render_registry_upload_for_customer(
        OWNER, BUSINESS.replace("-", ""), "검증법인", selected_customer=selected,
    )

    render.assert_called_once()
    assert render.call_args.args[0] == OWNER
    assert render.call_args.args[1]["사업자등록번호"] == BUSINESS
    assert render.call_args.args[1]["_customer_id"] == "synthetic-customer"


def test_inconsistent_selected_customer_is_not_replaced_by_another_customer(monkeypatch):
    render = Mock()
    monkeypatch.setattr(stock, "_render_registry_upload", render)
    reread = Mock(side_effect=AssertionError("mismatched selected row must fail closed"))
    monkeypatch.setattr(stock, "_read_customers", reread)

    stock.render_registry_upload_for_customer(
        OWNER, BUSINESS, "검증법인", selected_customer=customer_row(OTHER_BUSINESS),
    )

    reread.assert_not_called()
    assert all(call.args[1] is None for call in render.call_args_list)


def test_legacy_call_resolves_exact_business_with_owner_scope(monkeypatch):
    customers = pd.DataFrame([
        customer_row(OTHER_BUSINESS).to_dict(), customer_row().to_dict(),
    ])
    read = Mock(return_value=customers)
    render = Mock()
    monkeypatch.setattr(stock, "_read_customers", read)
    monkeypatch.setattr(stock, "_render_registry_upload", render)
    monkeypatch.setattr(stock, "_load_stock_customer_context", Mock(
        side_effect=AssertionError("legacy registration must not hydrate valuation"),
    ))

    stock.render_registry_upload_for_customer(OWNER, BUSINESS.replace("-", ""), "검증법인")

    read.assert_called_once_with(OWNER)
    render.assert_called_once()
    assert render.call_args.args[1]["사업자등록번호"] == BUSINESS


@pytest.mark.parametrize("rows", [
    [customer_row(OTHER_BUSINESS).to_dict()],
    [customer_row().to_dict(), customer_row().to_dict()],
])
def test_legacy_call_rejects_missing_or_duplicate_business_without_name_fallback(monkeypatch, rows):
    render = Mock()
    monkeypatch.setattr(stock, "_read_customers", Mock(return_value=pd.DataFrame(rows)))
    monkeypatch.setattr(stock, "_render_registry_upload", render)

    stock.render_registry_upload_for_customer(OWNER, BUSINESS, "검증법인")

    assert all(call.args[1] is None for call in render.call_args_list)
