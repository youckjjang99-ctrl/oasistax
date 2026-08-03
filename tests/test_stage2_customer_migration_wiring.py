from __future__ import annotations

from pathlib import Path

import pandas as pd

import cloud_migration


class _MigrationDB:
    def __init__(self) -> None:
        self.inserted: list[tuple[str, list[dict]]] = []

    def upsert(self, _table, rows, _on_conflict):
        return list(rows)

    def insert(self, table, rows):
        self.inserted.append((table, list(rows)))
        return list(rows)


def _configure_empty_sources(monkeypatch, tmp_path: Path, customer_path: Path) -> None:
    monkeypatch.setattr(
        cloud_migration,
        "get_user_cumulative_db_path",
        lambda _user_id: customer_path,
    )
    monkeypatch.setattr(
        cloud_migration,
        "get_user_dirs",
        lambda _user_id: {"base": tmp_path},
    )


def test_stage2_migration_version_is_recorded() -> None:
    assert cloud_migration.MIGRATION_VERSION == "v9.11.0"


def test_partial_customer_sync_is_not_reported_as_complete(
    monkeypatch,
    tmp_path: Path,
) -> None:
    customer_path = tmp_path / "customers.xlsx"
    customer_path.touch()
    _configure_empty_sources(monkeypatch, tmp_path, customer_path)
    monkeypatch.setattr(
        cloud_migration.pd,
        "read_excel",
        lambda *_args, **_kwargs: pd.DataFrame([{"사업자등록번호": ""}]),
    )
    monkeypatch.setattr(
        cloud_migration,
        "sync_customer_snapshots",
        lambda *_args, **_kwargs: {
            "attempted": 1,
            "synced": 0,
            "queued": 1,
            "skipped": 1,
            "failed": 1,
        },
    )

    db = _MigrationDB()
    result = cloud_migration.migrate_user_data("owner-a", db=db)

    assert result["customer_unresolved"] == 1
    assert result["customer_queued"] == 1
    assert len(result["errors"]) == 3
    stored_result = db.inserted[-1][1][0]["result_data"]
    assert len(stored_result["errors"]) == 3


def test_customer_migration_exception_is_sanitized_before_history_storage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    customer_path = tmp_path / "customers.xlsx"
    customer_path.touch()
    _configure_empty_sources(monkeypatch, tmp_path, customer_path)

    def _raise_read_error(*_args, **_kwargs):
        raise RuntimeError("SENSITIVE_MARKER")

    monkeypatch.setattr(cloud_migration.pd, "read_excel", _raise_read_error)
    db = _MigrationDB()

    result = cloud_migration.migrate_user_data("owner-a", db=db)

    assert result["errors"]
    assert all("SENSITIVE_MARKER" not in item for item in result["errors"])
    assert "RuntimeError" in result["errors"][0]
    stored_result = db.inserted[-1][1][0]["result_data"]
    assert all(
        "SENSITIVE_MARKER" not in item
        for item in stored_result["errors"]
    )
