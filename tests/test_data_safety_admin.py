import data_safety_admin
from data_safety_admin import (
    _admin_sync_summary,
    _safe_backup_rows,
    _safe_customer_archive_rows,
    _safe_restore_rows,
)


def test_backup_status_view_omits_storage_paths_and_errors() -> None:
    rows = _safe_backup_rows(
        [
            {
                "backup_type": "database",
                "status": "completed",
                "storage_path": "private/customer/path",
                "checksum_sha256": "secret-checksum",
                "size_bytes": 2048,
                "record_counts": {
                    "customers": 100,
                    "documents": 12,
                    "missing_targets": ["storage"],
                    "홍길동": 1,
                },
                "error_summary": "private error",
                "created_at": "2026-08-03T00:00:00Z",
            }
        ]
    )
    assert rows == [
        {
            "backup_type": "database",
            "status": "completed",
            "started_at": None,
            "completed_at": None,
            "retention_until": None,
            "created_at": "2026-08-03T00:00:00Z",
            "integrity_status": "체크섬 기록됨",
            "size_bytes": 2048,
            "record_counts": {"customers": 100, "documents": 12},
            "missing_targets": "storage",
        }
    ]
    assert "secret-checksum" not in repr(rows)
    assert "홍길동" not in repr(rows)


def test_restore_status_view_omits_result_payload() -> None:
    rows = _safe_restore_rows(
        [
            {
                "environment_label": "isolated-test",
                "status": "completed",
                "integrity_verified": True,
                "result_summary": {
                    "customer_count": 100,
                    "documents_restored": 20,
                    "missing_targets": [],
                    "storage_path": "private/customer/path",
                },
                "created_at": "2026-08-03T00:00:00Z",
            }
        ]
    )
    assert rows[0]["result_summary"] == {
        "customer_count": 100,
        "documents_restored": 20,
    }
    assert rows[0]["missing_targets"] == "없음"
    assert rows[0]["integrity_verified"] is True
    assert "private/customer/path" not in repr(rows)


def test_failed_or_unverified_backup_does_not_claim_integrity() -> None:
    rows = _safe_backup_rows(
        [
            {"status": "completed", "record_counts": {}},
            {"status": "failed", "checksum_sha256": "stale-value"},
        ]
    )

    assert rows[0]["integrity_status"] == "검증 증거 없음"
    assert rows[1]["integrity_status"] == "실패"


def test_admin_sync_summary_uses_global_durable_queue_without_sensitive_data(
    monkeypatch,
) -> None:
    captured: list[object] = []
    monkeypatch.setattr(
        data_safety_admin,
        "get_cloud_sync_status",
        lambda _user_id: {
            "local": {
                "queued": 2,
                "dead_letter": 1,
                "corrupted": False,
                "queue_path": "private/local/path",
            },
            "payload": {"resident_number": "sensitive"},
        },
    )

    def fake_cloud_status(owner_user_id):
        captured.append(owner_user_id)
        return {
            "enabled": True,
            "queued": 11,
            "dead_letter": 3,
            "total": 20,
            "payload": {"phone": "sensitive"},
        }

    monkeypatch.setattr(
        data_safety_admin,
        "cloud_outbox_status",
        fake_cloud_status,
    )

    result = _admin_sync_summary("admin-1")

    assert captured == [None]
    assert result == {
        "global_queued": 11,
        "global_dead_letter": 3,
        "global_total": 20,
        "global_unavailable": False,
        "durable_enabled": True,
        "local_queued": 2,
        "local_dead_letter": 1,
        "local_corrupted": False,
    }
    assert "private/local/path" not in repr(result)
    assert "sensitive" not in repr(result)


def test_admin_sync_summary_marks_global_queue_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        data_safety_admin,
        "get_cloud_sync_status",
        lambda _user_id: {"local": {"queued": 4, "corrupted": True}},
    )
    monkeypatch.setattr(
        data_safety_admin,
        "cloud_outbox_status",
        lambda _owner_user_id: (_ for _ in ()).throw(RuntimeError("offline")),
    )

    result = _admin_sync_summary("admin-1")

    assert result["global_unavailable"] is True
    assert result["global_queued"] == 0
    assert result["local_queued"] == 4
    assert result["local_corrupted"] is True


def test_archive_table_omits_business_and_contact_fields() -> None:
    rows = _safe_customer_archive_rows(
        [
            {
                "id": "12345678-1234-1234-1234-123456789012",
                "owner_user_id": "owner-user-123",
                "company_name": "안전한 업체명",
                "lifecycle_status": "archived",
                "archived_at": "2026-08-03T00:00:00Z",
                "business_no": "sensitive-business-number",
                "phone": "sensitive-phone",
                "archive_reason": "internal reason",
            }
        ]
    )

    assert rows == [
        {
            "고객 ID": "12345678",
            "업체명": "안전한 업체명",
            "소유자": "own***123",
            "상태": "archived",
            "보관일": "2026-08-03T00:00:00Z",
        }
    ]
    assert "sensitive" not in repr(rows)
    assert "internal reason" not in repr(rows)
