from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import licensed_business_repository


def test_repository_sends_only_minimum_transient_fields() -> None:
    database = MagicMock()
    database.rpc.return_value = 1
    today = datetime.now(timezone.utc).date().isoformat()

    with patch(
        "licensed_business_repository.CloudDatabase",
        return_value=database,
    ):
        saved = licensed_business_repository.save_recent_license_signals(
            [
                {
                    "source_key": "service:management-number",
                    "company_name": "Example Company",
                    "address": "Seoul 1",
                    "license_date": today,
                    "is_active": True,
                    "phone": "010-0000-0000",
                    "raw": {"sensitive": "payload"},
                }
            ]
        )

    assert saved == 1
    name, parameters = database.rpc.call_args.args
    assert name == "oasis_upsert_recent_license_signals"
    assert set(parameters["p_rows"][0]) == {
        "source_key",
        "company_name",
        "address",
        "license_date",
        "is_active",
    }


def test_migration_table_has_no_original_identifiers() -> None:
    migration = Path(
        "supabase/migrations/20260813190000_add_compact_recent_license_signals.sql"
    ).read_text(encoding="utf-8")
    table_block = migration.split(
        "create table if not exists public.oasis_recent_license_signals (",
        1,
    )[1].split(");", 1)[0]

    assert "company_name" not in table_block
    assert "address" not in table_block
    assert "phone" not in table_block
    assert "source_data" not in table_block
    assert "signal_key text primary key" in table_block
    assert "match_key text not null" in table_block
    assert "license_date date not null" in table_block
    assert "enable row level security" in migration
    assert "to service_role" in migration
