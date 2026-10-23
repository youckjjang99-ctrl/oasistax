"""Offline synthetic tests for immutable valuation versions and storage feedback."""
from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import stock_valuation as stock


BUSINESS = "-".join(("111", "11", "11111"))
OTHER_BUSINESS = "-".join(("222", "22", "22222"))


def inputs(**overrides):
    data = {
        "company_name": "합성 검증법인", "business_no": BUSINESS,
        "corporate_no": "synthetic-corporation", "address": "합성 주소",
        "establishment": "2020-01-01", "valuation_date": "2026-10-23",
        "current_shares": 100, "par_value": 5000, "capital": 500000,
        "authorized_shares": 1000, "share_classes": [], "valuation_type": "일반법인(3:2)",
        "annual_rows": [{"year": str(year), "adjusted_net_income": amount, "shares": 100}
                        for year, amount in ((2025, 300000), (2024, 200000), (2023, 100000))],
        "total_assets": 1000000, "total_liabilities": 200000,
        "asset_additions": 0, "asset_deductions": 0, "liability_additions": 0,
        "liability_deductions": 0, "other_adjustments": 0, "goodwill": 0,
        "goodwill_mode": "manual", "goodwill_acquired_intangible_remaining": 0,
        "goodwill_exclusion_reason": "", "capitalization_rate": 0.10,
        "nav_floor_rate": 0.80, "premium_exempt": True, "premium_rate": 0,
    }
    data.update(overrides)
    return data


def record(record_id="original-version", **overrides):
    values = inputs(**overrides)
    return stock._build_stock_record(values, stock.calculate_stock_valuation(values),
                                    record_id=record_id, saved_by="synthetic-user",
                                    saved_at="2026-10-23 06:00:00")


@pytest.fixture
def history(monkeypatch, tmp_path):
    state = {}
    messages = []
    cloud_records = []
    monkeypatch.setattr(stock, "get_user_dirs", lambda owner: {"base": tmp_path})
    monkeypatch.setattr(stock, "st", SimpleNamespace(
        session_state=state, success=lambda value: messages.append(("success", value)),
        warning=lambda value: messages.append(("warning", value)),
        error=lambda value: messages.append(("error", value)),
    ))
    monkeypatch.setattr(stock, "write_runtime_error", lambda *args, **kwargs: "")
    monkeypatch.setattr(stock, "sync_stock_valuation", lambda owner, value:
                        cloud_records.append(deepcopy(value)) or (True, "ok"))
    monkeypatch.setattr(stock, "load_stock_valuations", lambda *args, **kwargs: [])
    stock._load_cloud_records_cached.clear()
    path = tmp_path / "stock_valuations.json"
    return SimpleNamespace(state=state, messages=messages, cloud_records=cloud_records,
                           path=path, read=lambda: json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize("raw", [b"{malformed-json", b"{}", b'[null]'])
def test_corrupt_existing_history_refuses_overwrite(history, raw):
    history.path.write_bytes(raw)
    with pytest.raises(ValueError, match="기존 평가이력"):
        stock._save_records("owner", [record("new-version")])
    assert history.path.read_bytes() == raw


def test_atomic_append_preserves_existing_versions_and_other_company(history):
    old = record()
    other = record("other-company-version", business_no=OTHER_BUSINESS, company_name="다른 합성법인")
    history.path.write_text(json.dumps([old, other], ensure_ascii=False), encoding="utf-8")
    new = record("new-version", total_assets=1200000)
    stock._save_records("owner", [new])
    saved = {value["record_id"]: value for value in history.read()}
    assert saved == {old["record_id"]: old, other["record_id"]: other, new["record_id"]: new}
    assert not list(history.path.parent.glob("*.tmp"))


def test_changed_calculation_appends_new_id_and_never_overwrites_old(history):
    old = record()
    history.path.write_text(json.dumps([old]), encoding="utf-8")
    history.state["stock_loaded_record_id"] = old["record_id"]
    first_inputs = inputs()
    first = stock._persist_stock_calculation("owner", "synthetic-user", first_inputs,
                                             stock.calculate_stock_valuation(first_inputs))
    changed = inputs(total_assets=1200000)
    second = stock._persist_stock_calculation("owner", "synthetic-user", changed,
                                              stock.calculate_stock_valuation(changed))
    saved = {value["record_id"]: value for value in history.read()}
    assert len({old["record_id"], first["record_id"], second["record_id"]}) == 3
    assert saved[old["record_id"]] == old
    assert saved[first["record_id"]]["total_assets"] == first_inputs["total_assets"]
    assert saved[second["record_id"]]["supersedes_record_id"] == first["record_id"]


def test_same_calculation_retry_reuses_version_without_self_supersedes(history):
    data = inputs()
    result = stock.calculate_stock_valuation(data)
    first = stock._persist_stock_calculation("owner", "synthetic-user", data, result)
    retry = stock._persist_stock_calculation("owner", "synthetic-user", deepcopy(data), deepcopy(result))
    assert first["record_id"] == retry["record_id"]
    assert len(history.read()) == 1
    assert history.read()[0]["supersedes_record_id"] != first["record_id"]


def test_cloud_failure_is_local_preserved_not_cloud_success(monkeypatch, history):
    monkeypatch.setattr(stock, "sync_stock_valuation", lambda *args: (False, "pending"))
    data = inputs()
    saved = stock._persist_stock_calculation("owner", "synthetic-user", data,
                                            stock.calculate_stock_valuation(data))
    stock._render_stock_save_feedback()
    assert saved["local_saved"] and not saved["cloud_saved"]
    assert history.messages and all(kind != "success" for kind, _ in history.messages)
    assert any("로컬" in message for _, message in history.messages)


def test_local_failure_cloud_success_reports_storage_distinctly(monkeypatch, history):
    def fail(*args):
        raise OSError("synthetic local failure")

    monkeypatch.setattr(stock, "_save_records", fail)
    data = inputs()
    saved = stock._persist_stock_calculation("owner", "synthetic-user", data,
                                            stock.calculate_stock_valuation(data))
    stock._render_stock_save_feedback()
    assert not saved["local_saved"] and saved["cloud_saved"]
    assert [kind for kind, _ in history.messages] == ["success", "warning"]
    assert history.state["stock_loaded_record_id"] == saved["record_id"]


def test_both_save_failures_keep_calculated_result_and_inputs_for_retry(monkeypatch, history):
    def fail(*args):
        raise OSError("synthetic local failure")

    monkeypatch.setattr(stock, "_save_records", fail)
    monkeypatch.setattr(stock, "sync_stock_valuation", lambda *args: (False, "failed"))
    data = inputs()
    result = stock.calculate_stock_valuation(data)
    history.state.update(stock_last_result=deepcopy(result), stock_last_inputs=deepcopy(data))
    saved = stock._persist_stock_calculation("owner", "synthetic-user", data, result)
    stock._render_stock_save_feedback()
    assert not saved["local_saved"] and not saved["cloud_saved"]
    assert history.state["stock_last_result"] == result
    assert history.state["stock_last_inputs"] == data
    assert "stock_persisted_record_id" not in history.state
    assert [kind for kind, _ in history.messages] == ["error"]
