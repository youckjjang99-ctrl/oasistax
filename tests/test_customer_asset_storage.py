from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

import customer_asset_storage as storage
import data_safety_storage
from sync_outbox import load_local_outbox, retry_local_outbox


SYNTHETIC_PHONE = "-".join(("010", "0000", "0000"))
SYNTHETIC_NAME = "".join(("Private", "Person"))
SYNTHETIC_SECRET = "".join(("private", "-secret"))


@pytest.mark.parametrize("filename", ["한글_세무조정계산서.pdf", "원본_정관_최종.docx", "企業資料_日本語.xlsx"])
def test_private_storage_key_is_ascii_without_changing_display_filename(filename):
    first = storage._storage_path("synthetic-owner", filename)
    second = storage._storage_path("synthetic-owner", filename)
    assert first.isascii() and second.isascii()
    assert first != second
    assert Path(first).suffix == Path(filename).suffix
    assert "synthetic-owner" not in first
    assert storage._safe_filename("한글_세무조정계산서.pdf") == "한글_세무조정계산서.pdf"


def test_korean_original_keeps_metadata_name_and_bytes_while_using_valid_private_key(tmp_path, monkeypatch):
    source = tmp_path / "한글_세무조정계산서.pdf"
    source.write_bytes(b"synthetic original")
    monkeypatch.setenv(storage.PRIVATE_ASSET_FEATURE_FLAG, "true")
    monkeypatch.setattr(storage, "cloud_is_configured", lambda: True)
    captured = {}
    class FakeDatabase:
        def select(self, *args, **kwargs):
            return []
        def upload_private_object(self, bucket, path, content, content_type):
            assert path.isascii()
            captured.update(path=path, content=content)
        def upsert(self, table, rows, on_conflict):
            if table == storage.TABLE_CUSTOMER_ASSETS:
                captured["metadata"] = rows[0]
            return rows
    monkeypatch.setattr(storage, "CloudDatabase", FakeDatabase)
    result = storage.store_private_customer_asset(source, owner_user_id="synthetic-owner", asset_type="enterprise_document")
    assert result.cloud_saved and result.metadata_saved
    assert captured["metadata"]["original_filename"] == source.name
    assert captured["metadata"]["storage_path"] == captured["path"]
    assert captured["content"] == source.read_bytes() == b"synthetic original"


def test_feature_off_preserves_local_file_without_cloud_call(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "customer.pdf"
    source.write_bytes(b"private")
    monkeypatch.delenv(storage.PRIVATE_ASSET_FEATURE_FLAG, raising=False)

    result = storage.store_private_customer_asset(
        source,
        owner_user_id="user-1",
        asset_type="upload",
    )

    assert result.local_preserved is True
    assert result.cloud_enabled is False
    assert source.read_bytes() == b"private"


def test_repeated_asset_reuses_checksum_without_reupload(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "customer.pdf"
    source.write_bytes(b"private")
    monkeypatch.setenv(storage.PRIVATE_ASSET_FEATURE_FLAG, "true")
    monkeypatch.setattr(storage, "cloud_is_configured", lambda: True)

    class FakeDatabase:
        upserts = []

        def select(self, *_args, **_kwargs):
            return [{"id": "existing-id"}]

        def upsert(self, table, rows, on_conflict):
            self.upserts.append((table, rows, on_conflict))
            return rows

        def upload_private_object(self, *_args, **_kwargs):
            raise AssertionError("matching checksum must not upload again")

    monkeypatch.setattr(storage, "CloudDatabase", FakeDatabase)

    result = storage.store_private_customer_asset(
        source,
        owner_user_id="user-1",
        asset_type="upload",
    )

    assert result.asset_id == "existing-id"
    assert result.cloud_saved is True
    assert result.metadata_saved is True
    assert source.exists()
    assert FakeDatabase.upserts[0][0] == storage.CUSTOMER_ASSET_LINKS_TABLE


def test_signed_download_is_scoped_to_owner(monkeypatch) -> None:
    monkeypatch.setenv(storage.PRIVATE_ASSET_FEATURE_FLAG, "1")
    captured = {}

    class FakeDatabase:
        def select(self, table, *, filters, columns, limit):
            captured["filters"] = filters
            return [
                {
                    "storage_bucket": "oasis-customer-assets",
                    "storage_path": "owner/private.pdf",
                    "original_filename": "customer.pdf",
                }
            ]

        def create_private_signed_url(self, bucket, path, **kwargs):
            captured["signed"] = (bucket, path, kwargs)
            return "https://signed.invalid/temporary"

    monkeypatch.setattr(storage, "CloudDatabase", FakeDatabase)

    url = storage.create_customer_asset_download_url(
        "asset-1",
        owner_user_id="user-1",
    )

    assert url == "https://signed.invalid/temporary?download=customer.pdf"
    assert captured["filters"]["owner_user_id"] == "user-1"
    assert captured["filters"]["status"] == "active"


def test_signed_download_preserves_signature_and_uses_korean_original_filename(monkeypatch):
    monkeypatch.setenv(storage.PRIVATE_ASSET_FEATURE_FLAG, "1")
    captured = {}

    class FakeDatabase:
        def select(self, *args, **kwargs):
            return [{"storage_bucket": "private", "storage_path": "opaque.pdf",
                     "original_filename": "한글 자료.pdf"}]

        def create_private_signed_url(self, *args, **kwargs):
            captured.update(kwargs)
            return "https://signed.invalid/temporary?token=synthetic%2Bsignature&download=old&option=&download=duplicate#section"

    monkeypatch.setattr(storage, "CloudDatabase", FakeDatabase)
    url = storage.create_customer_asset_download_url("asset-1", owner_user_id="user-1", expires_in=999)
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "signed.invalid"
    assert parts.path == "/temporary" and parts.fragment == "section"
    assert parse_qsl(parts.query, keep_blank_values=True) == [
        ("token", "synthetic+signature"), ("option", ""), ("download", "한글 자료.pdf")]
    assert captured["expires_in"] == 300
    assert captured["download_name"] == "한글 자료.pdf"


def test_same_blob_can_be_linked_to_a_second_customer_without_reupload(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "customer.pdf"
    source.write_bytes(b"private")
    monkeypatch.setenv(storage.PRIVATE_ASSET_FEATURE_FLAG, "true")
    monkeypatch.setattr(storage, "cloud_is_configured", lambda: True)

    class FakeDatabase:
        upserts = []

        def select(self, *_args, **_kwargs):
            return [
                {
                    "id": "existing-id",
                    "customer_id": "customer-a",
                    "source_id": "source-a",
                }
            ]

        def upsert(self, table, rows, on_conflict):
            self.upserts.append((table, rows, on_conflict))
            return rows

        def upload_private_object(self, *_args, **_kwargs):
            raise AssertionError("existing checksum must not upload again")

    monkeypatch.setattr(storage, "CloudDatabase", FakeDatabase)

    result = storage.store_private_customer_asset(
        source,
        owner_user_id="user-1",
        asset_type="upload",
        customer_id="customer-b",
        source_id="source-b",
    )

    assert result.cloud_saved is True
    assert result.metadata_saved is True
    assert result.error_code == ""
    table, rows, on_conflict = FakeDatabase.upserts[0]
    assert table == storage.CUSTOMER_ASSET_LINKS_TABLE
    assert on_conflict == "owner_user_id,asset_id,association_key"
    assert rows[0]["asset_id"] == "existing-id"
    assert rows[0]["customer_id"] == "customer-b"
    assert rows[0]["source_id"] == "source-b"


def test_new_blob_saves_asset_metadata_and_logical_link(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "customer.pdf"
    source.write_bytes(b"private")
    monkeypatch.setenv(storage.PRIVATE_ASSET_FEATURE_FLAG, "true")
    monkeypatch.setattr(storage, "cloud_is_configured", lambda: True)

    class FakeDatabase:
        upserts = []
        uploads = []

        def select(self, *_args, **_kwargs):
            return []

        def upload_private_object(self, bucket, path, content, content_type):
            self.uploads.append((bucket, path, content, content_type))

        def upsert(self, table, rows, on_conflict):
            self.upserts.append((table, rows, on_conflict))
            return rows

    monkeypatch.setattr(storage, "CloudDatabase", FakeDatabase)

    result = storage.store_private_customer_asset(
        source,
        owner_user_id="user-1",
        asset_type="upload",
        customer_id="customer-a",
        source_type="crm_upload",
        source_id="source-a",
    )

    assert result.cloud_saved is True
    assert result.metadata_saved is True
    assert len(FakeDatabase.uploads) == 1
    assert [item[0] for item in FakeDatabase.upserts] == [
        storage.TABLE_CUSTOMER_ASSETS,
        storage.CUSTOMER_ASSET_LINKS_TABLE,
    ]
    assert FakeDatabase.upserts[1][1][0]["asset_id"] == result.asset_id


def test_uploaded_blob_metadata_failure_queues_idempotent_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "customer.pdf"
    source.write_bytes(b"private")
    queue_path = tmp_path / "cloud_sync_queue.json"
    monkeypatch.setenv(storage.PRIVATE_ASSET_FEATURE_FLAG, "true")
    monkeypatch.delenv("OASIS_DURABLE_OUTBOX_V1", raising=False)
    monkeypatch.setattr(storage, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(
        storage,
        "_reconciliation_queue_path",
        lambda _owner: queue_path,
    )

    class FakeDatabase:
        uploads = []

        def select(self, *_args, **_kwargs):
            return []

        def upload_private_object(self, bucket, path, content, content_type):
            self.uploads.append((bucket, path, content, content_type))

        def upsert(self, *_args, **_kwargs):
            raise RuntimeError(f"{SYNTHETIC_NAME} {SYNTHETIC_PHONE}")

    monkeypatch.setattr(storage, "CloudDatabase", FakeDatabase)

    result = storage.store_private_customer_asset(
        source,
        owner_user_id="user-1",
        asset_type="upload",
        customer_id="customer-a",
        source_type="crm_upload",
        source_id="source-a",
    )
    # Calling the repair helper again with the same stable rows must not add
    # another active job for either step.
    queued = load_local_outbox(queue_path)
    metadata_row = queued[0]["payload"]["rows"][0]
    link_row = queued[1]["payload"]["rows"][0]
    storage._queue_asset_reconciliation(
        owner_user_id="user-1",
        asset_row=metadata_row,
        link_row=link_row,
        error_code="RuntimeError",
    )

    stored = load_local_outbox(queue_path)
    assert source.read_bytes() == b"private"
    assert len(FakeDatabase.uploads) == 1
    assert result.cloud_saved is True
    assert result.metadata_saved is False
    assert result.recovery_queued is True
    assert result.recovery_queue == "local"
    assert result.error_code == "RuntimeError"
    assert len(stored) == 2
    assert [item["job_type"] for item in stored] == [
        storage.ASSET_METADATA_RECOVERY_JOB,
        storage.ASSET_LINK_RECOVERY_JOB,
    ]
    assert all(item["status"] == "pending" for item in stored)
    summaries = " ".join(item["last_error_summary"] for item in stored)
    assert SYNTHETIC_NAME not in summaries
    assert SYNTHETIC_PHONE not in summaries
    assert all(
        "local_path" not in item["payload"]["rows"][0]
        for item in stored
    )
    replayed = []

    def replay(table, rows, on_conflict):
        replayed.append((table, rows, on_conflict))

    retry_result = retry_local_outbox(queue_path, replay)
    assert retry_result == {"success": 2, "failed": 0, "dead_letter": 0}
    assert [item[0] for item in replayed] == [
        storage.TABLE_CUSTOMER_ASSETS,
        storage.CUSTOMER_ASSET_LINKS_TABLE,
    ]


def test_existing_asset_link_failure_queues_link_only_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "customer.pdf"
    source.write_bytes(b"private")
    queue_path = tmp_path / "cloud_sync_queue.json"
    monkeypatch.setenv(storage.PRIVATE_ASSET_FEATURE_FLAG, "true")
    monkeypatch.delenv("OASIS_DURABLE_OUTBOX_V1", raising=False)
    monkeypatch.setattr(storage, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(
        storage,
        "_reconciliation_queue_path",
        lambda _owner: queue_path,
    )

    class FakeDatabase:
        def select(self, *_args, **_kwargs):
            return [{"id": "existing-id"}]

        def upsert(self, *_args, **_kwargs):
            raise RuntimeError(SYNTHETIC_SECRET)

        def upload_private_object(self, *_args, **_kwargs):
            raise AssertionError("existing object must not upload again")

    monkeypatch.setattr(storage, "CloudDatabase", FakeDatabase)

    result = storage.store_private_customer_asset(
        source,
        owner_user_id="user-1",
        asset_type="upload",
        customer_id="customer-b",
        source_id="source-b",
    )

    stored = load_local_outbox(queue_path)
    assert result.cloud_saved is True
    assert result.metadata_saved is False
    assert result.recovery_queued is True
    assert result.recovery_queue == "local"
    assert len(stored) == 1
    assert stored[0]["job_type"] == storage.ASSET_LINK_RECOVERY_JOB
    assert stored[0]["payload"]["rows"][0]["asset_id"] == "existing-id"
    assert SYNTHETIC_SECRET not in stored[0]["last_error_summary"]


def test_signed_download_rejects_cross_user_session(monkeypatch) -> None:
    monkeypatch.setattr(
        data_safety_storage,
        "_current_session_identity",
        lambda: ("signed-in-owner", "user"),
    )
    # customer_asset_storage imported the guard directly, so patch its trusted
    # session dependency through the defining module.
    with pytest.raises(PermissionError):
        storage.create_customer_asset_download_url(
            "asset-1",
            owner_user_id="different-owner",
        )
