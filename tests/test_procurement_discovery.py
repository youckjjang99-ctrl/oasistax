from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import procurement_bid_import
import procurement_discovery


def test_activity_label_distinguishes_bidder_and_winner() -> None:
    assert procurement_discovery.activity_label(
        {"activity_status": "bidder"}
    ) == "나라장터 투찰 이력"
    assert procurement_discovery.activity_label(
        {"activity_status": "winner"}
    ) == "나라장터 낙찰 이력"
    assert procurement_discovery.activity_label(None) == ""


def test_lookup_normalizes_and_deduplicates_business_numbers() -> None:
    database = Mock()
    database.rpc.return_value = [
        {"business_no": "1234567890", "activity_status": "bidder"}
    ]
    result = procurement_discovery.load_activity_map(
        ["123-45-67890", "1234567890", "invalid"],
        database=database,
    )
    database.rpc.assert_called_once_with(
        "oasis_lookup_procurement_activity",
        {"p_business_nos": ["1234567890"]},
    )
    assert result["1234567890"]["activity_status"] == "bidder"


def test_csv_parser_keeps_only_minimal_bidder_fields(tmp_path: Path) -> None:
    source = tmp_path / "bids.csv"
    source.write_text(
        "업체명,업체사업자등록번호,투찰일자,낙찰자선정여부,업무구분,투찰금액\n"
        "민감업체,123-45-67890,20260812,Y,용역,999999\n",
        encoding="utf-8-sig",
    )
    rows = list(procurement_bid_import.iter_bidder_rows(source))
    assert rows == [
        {
            "business_no": "1234567890",
            "bid_date": "20260812",
            "has_won": True,
            "business_category": "용역",
        }
    ]
    assert "업체명" not in rows[0]
    assert "투찰금액" not in rows[0]


def test_csv_import_batches_and_summarizes(tmp_path: Path) -> None:
    source = tmp_path / "bids.csv"
    source.write_text(
        "업체사업자등록번호,투찰일자,낙찰자선정여부,업무구분\n"
        "123-45-67890,20260811,N,물품\n"
        "123-45-67890,20260812,Y,용역\n"
        "111-22-33333,20260812,N,공사\n",
        encoding="utf-8-sig",
    )
    database = Mock()
    database.rpc.return_value = [
        {"signal_count": 2, "matched_contact_count": 1}
    ]
    result = procurement_bid_import.import_bidder_csv(
        source,
        database=database,
        batch_size=100,
    )
    assert result == {
        "source_rows": 3,
        "signal_rows": 2,
        "matched_contacts": 1,
    }
    rows = database.rpc.call_args.args[1]["p_rows"]
    assert len(rows) == 2
    merged = next(row for row in rows if row["business_no"] == "1234567890")
    assert merged["bid_date"] == "20260812"
    assert merged["has_won"] is True
    assert merged["business_category"] == "용역"


def test_migration_is_contact_only_private_and_runs_at_10am_kst() -> None:
    sql = Path(
        "supabase/migrations/20260813210000_add_procurement_bidder_classification.sql"
    ).read_text(encoding="utf-8")
    assert "c.has_mobile_phone or c.has_landline_phone" in sql
    assert "phone_checked_at >= p_since" in sql
    assert "'0 1 * * *'" in sql
    assert "Asia/Seoul" in sql
    assert "enable row level security" in sql
    assert "revoke all on table public.oasis_procurement_bidder_signals" in sql
    assert "extensions.digest" in sql
    assert "company_name" not in sql
    assert "mobile_phone text" not in sql
    assert "landline_phone text" not in sql


def test_saved_and_search_tables_show_procurement_activity() -> None:
    source = Path("prospect_db_center.py").read_text(encoding="utf-8")
    assert source.count('"나라장터활동"') >= 8
    assert "_procurement_activity_map" in source
    repository = Path("prospect_db_repository.py").read_text(encoding="utf-8")
    assert 'source_data["procurement_activity"]' in repository
