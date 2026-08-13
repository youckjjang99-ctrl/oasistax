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


def test_migration_deduplicates_same_source_key_within_batch() -> None:
    migration = Path(
        "supabase/migrations/20260813193000_deduplicate_compact_license_signal_batches.sql"
    ).read_text(encoding="utf-8")

    assert "select distinct on (signal_key)" in migration
    assert "from deduplicated" in migration


def test_improved_matching_keeps_source_pii_transient() -> None:
    migration = Path(
        "supabase/migrations/20260813200000_improve_license_contact_matching.sql"
    ).read_text(encoding="utf-8").lower()

    added_columns = migration.split(
        "alter table public.oasis_recent_license_signals",
        1,
    )[1].split(";", 1)[0]
    assert "contact_ref_key text" in added_columns
    assert "contact_match_method text" in added_columns
    assert "has_mobile_phone boolean" in added_columns
    assert "has_landline_phone boolean" in added_columns
    assert "company_name text" not in added_columns
    assert "address text" not in added_columns
    assert "mobile_phone text" not in added_columns
    assert "landline_phone text" not in added_columns
    assert "exact_address" in migration
    assert "address_core" in migration
    assert "unique_region_name" in migration
    assert "extensions.digest" in migration


def test_improved_matching_is_service_only_and_replays_collection() -> None:
    migration = Path(
        "supabase/migrations/20260813200000_improve_license_contact_matching.sql"
    ).read_text(encoding="utf-8").lower()
    collector = Path("scheduled_license_collection.py").read_text(
        encoding="utf-8"
    )

    assert "revoke all on function public.oasis_compact_address_core(text)" in migration
    assert "revoke all on function public.oasis_address_province_code(text)" in migration
    assert "to service_role" in migration
    assert "compact-v2" in collector
