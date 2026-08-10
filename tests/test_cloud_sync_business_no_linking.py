from __future__ import annotations

import cloud_sync


class _FakeDatabase:
    def __init__(self, rows_by_business_no, identity_rows=None):
        self.rows_by_business_no = rows_by_business_no
        self.identity_rows = list(identity_rows or [])
        self.seen = []
        self.identity_filters = []

    def select(self, table, filters, columns="*", limit=None):
        del table, columns, limit
        business_no = filters["business_no"]
        self.seen.append(business_no)
        row = self.rows_by_business_no.get(business_no)
        return [row] if row else []

    def select_all(
        self,
        table,
        filters,
        columns="*",
        order=None,
        page_size=1000,
        max_rows=100000,
    ):
        del table, columns, order, page_size, max_rows
        self.identity_filters.append(dict(filters))
        return list(self.identity_rows)


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


def test_load_registry_snapshot_recovers_legacy_row_by_corporate_number(
    monkeypatch,
):
    database = _FakeDatabase(
        {},
        identity_rows=[
            {
                "business_no": "legacy-key",
                "registry_data": {
                    "법인명": "테스트 주식회사",
                    "법인등록번호": "110111-1234567",
                    "발행주식총수": 10_000,
                },
            }
        ],
    )
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(cloud_sync, "CloudDatabase", lambda: database)

    snapshot = cloud_sync.load_registry_snapshot(
        "member",
        "123-45-67890",
        "테스트 주식회사",
        "110111-1234567",
    )

    assert snapshot["발행주식총수"] == 10_000
    assert database.identity_filters == [{"owner_user_id": "member"}]


def test_load_registry_snapshot_rejects_ambiguous_company_name(monkeypatch):
    database = _FakeDatabase(
        {},
        identity_rows=[
            {
                "business_no": "legacy-a",
                "registry_data": {"법인명": "동일 상호", "자본금": 1},
            },
            {
                "business_no": "legacy-b",
                "registry_data": {"법인명": "동일상호", "자본금": 2},
            },
        ],
    )
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(cloud_sync, "CloudDatabase", lambda: database)

    snapshot = cloud_sync.load_registry_snapshot(
        "member",
        "123-45-67890",
        "동일 상호",
    )

    assert snapshot == {}


def test_load_registry_snapshot_does_not_override_corporate_mismatch_by_name(
    monkeypatch,
):
    database = _FakeDatabase(
        {},
        identity_rows=[
            {
                "business_no": "legacy-key",
                "registry_data": {
                    "법인명": "테스트 주식회사",
                    "법인등록번호": "220222-7654321",
                },
            }
        ],
    )
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(cloud_sync, "CloudDatabase", lambda: database)

    snapshot = cloud_sync.load_registry_snapshot(
        "member",
        "123-45-67890",
        "테스트 주식회사",
        "110111-1234567",
    )

    assert snapshot == {}

