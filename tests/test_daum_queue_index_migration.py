from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SQL = (
    ROOT
    / "supabase"
    / "migrations"
    / "20260813195500_optimize_daum_legacy_phone_queue.sql"
).read_text(encoding="utf-8").lower()


def test_daum_queue_index_matches_runtime_query() -> None:
    assert "phone_provider_stage in ('daum', 'naver')" in SQL
    assert "phone_status = 'pending'" in SQL
    assert "source_type =" not in SQL
    assert "(created_at, contact_key)" in SQL
