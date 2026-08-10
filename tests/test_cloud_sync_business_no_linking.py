from __future__ import annotations

import cloud_sync


class _FakeDatabase:
    def __init__(self, rows_by_business_no):
        self.rows_by_business_no = rows_by_business_no
        self.seen = []

    def select(self, table, filters, columns="*", limit=None):
        del table, columns, limit
        business_no = filters["business_no"]
        self.seen.append(business_no)
        row = self.rows_by_business_no.get(business_no)
        return [row] if row else []


def test_load_financial_snapshot_accepts_legacy_digits_only_key(monkeypatch):
    database = _FakeDatabase(
        {
            "1234567890": {
                "financial_data": {"당기순이익": 76_000_000}
            }
        }
    )
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(cloud_sync, "CloudDatabase", lambda: database)

    snapshot = cloud_sync.load_financial_snapshot("member", "123-45-67890")

    assert snapshot["당기순이익"] == 76_000_000
    assert database.seen == ["123-45-67890", "1234567890"]


def test_financial_snapshot_preserves_existing_fields_and_storage_key(
    monkeypatch,
):
    database = _FakeDatabase(
        {
            "1234567890": {
                "business_no": "1234567890",
                "financial_data": {
                    "재무메모": "기존 메모",
                    "당기순이익": "",
                },
            }
        }
    )
    captured = {}
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(cloud_sync, "CloudDatabase", lambda: database)
    monkeypatch.setattr(
        cloud_sync,
        "_safe_upsert",
        lambda user_id, operation, table, rows, on_conflict: (
            captured.update({"rows": rows}) or True,
            "ok",
        ),
    )

    result = cloud_sync.sync_financial_snapshot(
        "member",
        "123-45-67890",
        {"당기순이익": 76_000_000},
    )

    assert result == (True, "ok")
    assert captured["rows"][0]["business_no"] == "1234567890"
    assert captured["rows"][0]["financial_data"]["재무메모"] == "기존 메모"
    assert captured["rows"][0]["financial_data"]["당기순이익"] == 76_000_000

