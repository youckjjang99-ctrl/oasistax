from __future__ import annotations

import ast
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

import integrated_policy_repository as repository


def _load_function(module_name: str, name: str, namespace: dict):
    """Execute the real UI function without unrelated OCR import dependencies."""
    path = Path(__file__).resolve().parents[1] / module_name
    source = ast.parse(path.read_text(encoding="utf-8"))
    function = next(
        node for node in source.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
        function,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace[name]


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _row(record_id: str, title: str = "Synthetic public policy") -> dict:
    return {
        "record_id": record_id,
        "title": title,
        "agency": "Synthetic public agency",
        "source_type": "internal",
        "active": True,
        "raw_data": {"사업명": title},
    }


@pytest.fixture
def public_sources(tmp_path: Path, monkeypatch):
    repository.clear_repository_display_cache()
    names = (
        "SEED_PATH", "LOCAL_CACHE_PATH", "BIZINFO_CACHE_PATH",
        "BIZINFO_METADATA_PATH", "EXTERNAL_CACHE_PATH",
        "EXTERNAL_METADATA_PATH", "REFRESH_STATE_PATH",
    )
    for name in names:
        monkeypatch.setattr(repository, name, tmp_path / f"{name}.json")
    monkeypatch.setattr(repository, "cloud_is_configured", lambda: False)
    yield repository
    repository.clear_repository_display_cache()


def test_public_display_unions_all_sources_without_upserts(public_sources, monkeypatch):
    r = public_sources
    _write_json(r.SEED_PATH, {"records": [_row("seed")]})
    _write_json(r.LOCAL_CACHE_PATH, [_row("local")])
    original_cache = r.LOCAL_CACHE_PATH.read_bytes()
    _write_json(r.BIZINFO_CACHE_PATH, [{"pblancNm": "Synthetic overnight notice"}])
    _write_json(r.EXTERNAL_CACHE_PATH, [{
        "source_name": "Synthetic startup source",
        "raw_data": {"title": "Synthetic external notice"},
    }])
    calls = {"select": 0, "upsert": 0}

    class Database:
        def select(self, table, **kwargs):
            calls["select"] += 1
            assert table == r.TABLE_POLICY_REPOSITORY
            assert kwargs["filters"] == {"active": "true"}
            return [_row("cloud")]

        def upsert(self, *args, **kwargs):
            calls["upsert"] += 1
            raise AssertionError("Navigation must not synchronize source rows")

    monkeypatch.setattr(r, "CloudDatabase", Database)
    monkeypatch.setattr(r, "cloud_is_configured", lambda: True)
    rows, status = r.load_repository_records_for_display()
    assert {row["record_id"] for row in rows}.issuperset({"seed", "local", "cloud"})
    assert len(rows) == 5
    assert status["count"] == 5
    rows[0]["raw_data"]["사업명"] = "Modified by caller"
    status["count"] = -1
    again, again_status = r.load_repository_records_for_display()
    assert again[0]["raw_data"]["사업명"] != "Modified by caller"
    assert again_status["count"] == 5
    assert calls == {"select": 1, "upsert": 0}
    assert r.LOCAL_CACHE_PATH.read_bytes() == original_cache
    assert r.repository_status()["count"] == 5
    assert calls["select"] == 1


@pytest.mark.parametrize("source_name", [
    "SEED_PATH", "LOCAL_CACHE_PATH", "BIZINFO_CACHE_PATH",
    "BIZINFO_METADATA_PATH", "EXTERNAL_CACHE_PATH",
    "EXTERNAL_METADATA_PATH", "REFRESH_STATE_PATH",
])
def test_changed_public_source_invalidates_display_cache(
    public_sources, monkeypatch, source_name,
):
    r = public_sources
    calls = []
    monkeypatch.setattr(r, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(r, "CloudDatabase", lambda: SimpleNamespace(
        select=lambda *args, **kwargs: calls.append(1) or [_row("cloud")],
    ))
    r.load_repository_records_for_display()
    path = getattr(r, source_name)
    value = {"records": [_row("new-seed")]} if source_name == "SEED_PATH" else {}
    _write_json(path, value)
    r.load_repository_records_for_display()
    assert len(calls) == 2


def test_display_cache_retries_failure_and_keeps_last_good_rows(public_sources, monkeypatch):
    r = public_sources
    now = [0.0]
    calls = []
    fail = [False]

    def select(*args, **kwargs):
        calls.append(1)
        if fail[0]:
            raise RuntimeError("Synthetic unavailable database")
        return [_row("cloud")]

    monkeypatch.setattr(r, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(r.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(r, "CloudDatabase", lambda: SimpleNamespace(select=select))
    r.load_repository_records_for_display()
    now[0] = r.DISPLAY_CACHE_SECONDS + 1
    fail[0] = True
    for _ in range(2):
        rows, status = r.load_repository_records_for_display()
        assert [row["record_id"] for row in rows] == ["cloud"]
        assert "마지막 정상" in status["message"]
    assert len(calls) == 3
    fail[0] = False
    r.load_repository_records_for_display()
    r.load_repository_records_for_display()
    assert len(calls) == 4


def test_forced_read_does_not_reuse_warm_snapshot(public_sources, monkeypatch):
    r = public_sources
    calls = []
    monkeypatch.setattr(r, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(r, "CloudDatabase", lambda: SimpleNamespace(
        select=lambda *args, **kwargs: calls.append(1) or [],
    ))
    r.load_repository_records_for_display()
    r.load_repository_records_for_display(force=True)
    assert len(calls) == 2


def test_changed_seed_overrides_old_cloud_copy_without_a_write(public_sources, monkeypatch):
    r = public_sources
    _write_json(r.SEED_PATH, {"records": [_row("seed", "Current bundled policy")]})
    monkeypatch.setattr(r, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(r, "CloudDatabase", lambda: SimpleNamespace(
        select=lambda *args, **kwargs: [_row("seed", "Old cloud policy")],
    ))
    rows, _ = r.load_repository_records_for_display()
    assert rows[0]["title"] == "Current bundled policy"


def test_failed_forced_read_cannot_leave_previous_warm_cache(public_sources, monkeypatch):
    r = public_sources
    calls = []

    def select(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("Synthetic temporary outage")
        return [_row("cloud")]

    monkeypatch.setattr(r, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(r, "CloudDatabase", lambda: SimpleNamespace(select=select))
    r.load_repository_records_for_display()
    r.load_repository_records_for_display(force=True)
    r.load_repository_records_for_display()
    assert len(calls) == 3


def test_failure_keeps_last_cloud_rows_and_newly_downloaded_sources(public_sources, monkeypatch):
    r = public_sources
    monkeypatch.setattr(r, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(r, "CloudDatabase", lambda: SimpleNamespace(
        select=lambda *args, **kwargs: [_row("cloud")],
    ))
    r.load_repository_records_for_display()
    _write_json(r.SEED_PATH, {"records": [_row("new-seed")]})

    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic temporary outage")

    monkeypatch.setattr(r, "CloudDatabase", lambda: SimpleNamespace(select=fail))
    rows, _ = r.load_repository_records_for_display()
    assert {row["record_id"] for row in rows} == {"cloud", "new-seed"}


def test_explicit_repository_sync_keeps_existing_writes(public_sources, monkeypatch):
    r = public_sources
    _write_json(r.SEED_PATH, {"records": [_row("seed")]})
    _write_json(r.BIZINFO_CACHE_PATH, [{"pblancNm": "Synthetic overnight notice"}])
    writes = []
    monkeypatch.setattr(r, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(r, "CloudDatabase", lambda: SimpleNamespace(
        select=lambda *args, **kwargs: [],
        upsert=lambda table, rows, conflict: writes.append((table, rows, conflict)),
    ))
    result = r.refresh_repository(force=True)
    assert result["count"] == 2
    assert len(writes) == 2
    assert all(write[0] == r.TABLE_POLICY_REPOSITORY for write in writes)


def test_policy_matcher_uses_public_display_reader(monkeypatch):
    namespace = {
        "load_repository_records_for_display": lambda: (
            [_row("policy")], {"message": "Synthetic cached source"},
        ),
        "normalize_record": lambda raw, source: {
            "id": "policy", "title": raw["사업명"], "source": source,
        },
        "_deduplicate": lambda rows: rows,
    }
    _load_function("multi_source_policy.py", "load_local_sources", namespace)
    collect = _load_function("multi_source_policy.py", "collect_all_sources", namespace)
    rows, statuses = collect()
    assert len(rows) == 1
    assert statuses[0]["count"] == 1


@pytest.mark.parametrize("derived", [[], ["Synthetic keyword"]])
def test_reconcile_reads_preferences_once_and_keeps_auto_keywords(monkeypatch, derived):
    import enterprise_consulting_engine as engine

    reads, writes = [], []
    monkeypatch.setattr(engine, "get_company_consultation_context", lambda **kwargs: {
        "matching_keywords": derived,
        "interest_fields": [],
        "journals": [],
    })
    monkeypatch.setattr(engine, "get_matching_preferences", lambda *args: reads.append(1) or {})
    monkeypatch.setattr(engine, "save_matching_preferences", lambda **kwargs: (
        writes.append(kwargs) or {"매칭키워드": kwargs["matching_keywords"]}
    ))
    result = engine.reconcile_enterprise_consulting_context("synthetic-owner", "", "")
    assert len(reads) == 1
    assert len(writes) == bool(derived)
    assert result["added_keywords"] == derived


@pytest.mark.parametrize("version_count", [0, 1, 2])
def test_employee_render_reuses_one_versions_read(monkeypatch, version_count):
    reads, analyses = [], []
    versions = [
        {"summary": {}, "employees": [], "uploaded_at": f"2026-01-0{index + 1}"}
        for index in range(version_count)
    ]
    noop = lambda *args, **kwargs: None
    fake_ui = SimpleNamespace(
        session_state={}, markdown=noop, caption=noop, info=noop,
        dataframe=noop, expander=lambda *args, **kwargs: nullcontext(),
        columns=lambda count: [SimpleNamespace(metric=noop) for _ in range(count)],
    )
    import pandas as pd

    render = _load_function("employee_status.py", "render_employee_status", {
        "st": fake_ui,
        "pd": pd,
        "_company_key": lambda *args: "synthetic-company",
        "_display_employee_rows": lambda *args: pd.DataFrame(),
        "load_employee_versions": lambda *args: reads.append(1) or versions,
        "render_employment_support_analysis": lambda **kwargs: analyses.append(kwargs),
    })
    render("synthetic-owner", "", "", allow_upload=False)
    assert len(reads) == 1
    assert len(analyses) == bool(version_count)
    if versions:
        assert analyses[0]["latest"] is versions[0]
