from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.modules.setdefault("cv2", Mock())
sys.modules.setdefault("pytesseract", Mock())
sys.modules.setdefault("fitz", Mock())

import consultation_audio_storage
import consulting_copilot
import customer_history
import data_safety_storage

SYNTHETIC_BUSINESS_NO = "-".join(("123", "45", "67890"))
SYNTHETIC_BUSINESS_NO_DIGITS = "".join(("123", "45", "67890"))


class DataSafetyStorageTests(unittest.TestCase):
    def test_copilot_feature_flag_defaults_off(self) -> None:
        with patch.dict(os.environ, {}, clear=False), patch.object(
            data_safety_storage,
            "cloud_is_configured",
        ) as configured:
            os.environ.pop("OASIS_CLOUD_COPILOT_V1", None)
            status = data_safety_storage.write_copilot_asset(
                owner_user_id="owner",
                asset_type="memory",
                asset_key="company",
                payload={"note": "preserved"},
                source_updated_at="2026-08-03T00:00:00",
            )
        self.assertTrue(status.local_saved)
        self.assertFalse(status.cloud_enabled)
        configured.assert_not_called()

    def test_cloud_failure_is_structured_and_does_not_echo_payload(self) -> None:
        database = Mock()
        database.upsert.side_effect = RuntimeError("private-value-must-not-leak")
        with patch.dict(
            os.environ,
            {"OASIS_CLOUD_COPILOT_V1": "true"},
        ), patch.object(
            data_safety_storage,
            "cloud_is_configured",
            return_value=True,
        ), patch.object(
            data_safety_storage,
            "CloudDatabase",
            return_value=database,
        ):
            result = data_safety_storage.write_copilot_asset(
                owner_user_id="owner",
                asset_type="memory",
                asset_key="company",
                payload={"note": "private-value-must-not-leak"},
                source_updated_at="2026-08-03T00:00:00",
            ).as_dict()
        self.assertTrue(result["degraded"])
        self.assertFalse(result["ok"])
        self.assertNotIn("private-value", json.dumps(result))

    def test_owner_context_rejects_cross_user_service_role_access(self) -> None:
        with patch.object(
            data_safety_storage,
            "_current_session_identity",
            return_value=("signed-in-owner", "user"),
        ):
            with self.assertRaises(PermissionError):
                data_safety_storage.require_owner_context("different-owner")

    def test_owner_context_rejects_unscoped_background_access(self) -> None:
        with patch.object(
            data_safety_storage,
            "_current_session_identity",
            return_value=("", ""),
        ):
            with self.assertRaises(PermissionError):
                data_safety_storage.require_owner_context("")

    def test_cloud_asset_load_uses_paginated_reader(self) -> None:
        database = Mock()
        database.select_all.return_value = [{"asset_key": "case-1"}]
        with patch.dict(
            os.environ,
            {"OASIS_CLOUD_COPILOT_V1": "true"},
        ), patch.object(
            data_safety_storage,
            "cloud_is_configured",
            return_value=True,
        ), patch.object(
            data_safety_storage,
            "CloudDatabase",
            return_value=database,
        ):
            rows = data_safety_storage.load_copilot_assets(
                owner_user_id="owner",
                asset_type="success_case",
            )
        self.assertEqual(rows, [{"asset_key": "case-1"}])
        database.select_all.assert_called_once()
        self.assertEqual(
            database.select_all.call_args.kwargs["page_size"],
            1000,
        )

    def test_local_copilot_migration_is_idempotent_and_newer_wins(self) -> None:
        database = Mock()
        newer_existing = {
            "asset_type": "memory",
            "asset_key": "existing-newer",
            "source_updated_at": "2026-08-03T10:00:00+00:00",
        }
        migrated_existing = {
            "asset_type": "memory",
            "asset_key": "missing",
            "source_updated_at": "2026-08-03T09:00:00+00:00",
        }
        database.select_all.side_effect = [
            [newer_existing],
            [newer_existing, migrated_existing],
        ]
        assets = [
            {
                "asset_type": "memory",
                "asset_key": "existing-newer",
                "payload": {"note": "older-local"},
                "source_updated_at": "2026-08-03T09:00:00+00:00",
            },
            {
                "asset_type": "memory",
                "asset_key": "missing",
                "payload": {"note": "preserve"},
                "source_updated_at": "2026-08-03T09:00:00+00:00",
            },
        ]
        with patch.dict(
            os.environ,
            {"OASIS_CLOUD_COPILOT_V1": "true"},
        ), patch.object(
            data_safety_storage,
            "cloud_is_configured",
            return_value=True,
        ), patch.object(
            data_safety_storage,
            "CloudDatabase",
            return_value=database,
        ):
            first = data_safety_storage.migrate_local_copilot_assets(
                owner_user_id="owner",
                assets=assets,
                batch_size=1,
            )
            second = data_safety_storage.migrate_local_copilot_assets(
                owner_user_id="owner",
                assets=assets,
                batch_size=1,
            )
        self.assertEqual(first["migrated"], 1)
        self.assertEqual(first["skipped"], 1)
        self.assertEqual(second["migrated"], 0)
        self.assertEqual(second["skipped"], 2)
        migrated_rows = database.upsert.call_args.args[1]
        self.assertEqual([row["asset_key"] for row in migrated_rows], ["missing"])
        self.assertEqual(database.upsert.call_count, 1)


class CustomerHistoryRetentionTests(unittest.TestCase):
    def test_local_history_is_never_truncated_or_shrunk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            existing = [
                {
                    "captured_at": f"2026-01-{(index % 28) + 1:02d} 00:00:00",
                    "source": "existing",
                    "business_no": SYNTHETIC_BUSINESS_NO,
                    "data": {"index": index},
                }
                for index in range(220)
            ]
            path.write_text(
                json.dumps({SYNTHETIC_BUSINESS_NO: existing}, ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(
                customer_history,
                "_path",
                return_value=path,
            ), patch.object(
                customer_history,
                "cloud_is_configured",
                return_value=False,
            ):
                customer_history.save_customer_event(
                    "owner",
                    SYNTHETIC_BUSINESS_NO,
                    "회사",
                    "event-new",
                    "상담",
                    "내용",
                )
                customer_history._save_all(
                    "owner",
                    {SYNTHETIC_BUSINESS_NO: existing[:2]},
                )
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(stored[SYNTHETIC_BUSINESS_NO]), 221)

    def test_history_cloud_failure_returns_degraded_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            database = Mock()
            database.insert.side_effect = RuntimeError("hidden-private-value")
            with patch.object(
                customer_history,
                "_path",
                return_value=path,
            ), patch.object(
                customer_history,
                "cloud_is_configured",
                return_value=True,
            ), patch.object(
                customer_history,
                "CloudDatabase",
                return_value=database,
            ):
                result = customer_history.save_customer_event(
                    "owner",
                    SYNTHETIC_BUSINESS_NO,
                    "회사",
                    "event-one",
                    "상담",
                    "내용",
                    return_status=True,
                )
            status = result["storage_status"]
            self.assertTrue(status["local_saved"])
            self.assertTrue(status["degraded"])
            self.assertNotIn("hidden-private", json.dumps(status))

    def test_malformed_history_is_preserved_instead_of_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            original = b'{"incomplete":'
            path.write_bytes(original)
            with patch.object(customer_history, "_path", return_value=path):
                with self.assertRaises(
                    customer_history.CustomerHistoryCorruptionError
                ):
                    customer_history._save_all(
                        "owner",
                        {SYNTHETIC_BUSINESS_NO: []},
                    )
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


class CopilotDualWriteTests(unittest.TestCase):
    def test_success_case_local_fallback_does_not_truncate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "success.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "case_id": f"case-{index}",
                            "saved_at": f"2026-01-01T00:{index % 60:02d}:00",
                        }
                        for index in range(1001)
                    ]
                ),
                encoding="utf-8",
            )
            with patch.object(
                consulting_copilot,
                "_success_path",
                return_value=path,
            ), patch.object(
                consulting_copilot,
                "_cloud_asset_payloads",
                return_value={},
            ), patch.object(
                consulting_copilot,
                "write_copilot_asset",
                return_value=data_safety_storage.local_only_status(),
            ):
                result = consulting_copilot.save_success_case(
                    "owner",
                    {"consulting_topic": "test"},
                    return_status=True,
                )
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(stored), 1002)
            self.assertTrue(result["storage_status"]["local_saved"])

    def test_cloud_memory_merges_without_dropping_local_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(
                json.dumps({SYNTHETIC_BUSINESS_NO_DIGITS: {"local": "keep"}}),
                encoding="utf-8",
            )
            with patch.object(
                consulting_copilot,
                "_memory_path",
                return_value=path,
            ), patch.object(
                consulting_copilot,
                "_cloud_asset_payloads",
                return_value={SYNTHETIC_BUSINESS_NO_DIGITS: {"cloud": "new"}},
            ):
                value = consulting_copilot.get_company_memory(
                    "owner",
                    "회사",
                    SYNTHETIC_BUSINESS_NO,
                )
            self.assertEqual(value["local"], "keep")
            self.assertEqual(value["cloud"], "new")

    def test_newer_local_memory_cannot_be_rolled_back_by_stale_cloud(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory_path = root / "memory.json"
            sync_path = root / "sync.json"
            conflict_path = root / "conflicts.json"
            memory_path.write_text(
                json.dumps(
                    {
                        SYNTHETIC_BUSINESS_NO_DIGITS: {
                            "note": "new-local",
                            "local_only": "preserved",
                            "updated_at": "2026-08-03T10:00:00+00:00",
                        }
                    }
                ),
                encoding="utf-8",
            )
            consulting_copilot._CLOUD_SOURCE_TIMES.clear()
            with patch.object(
                consulting_copilot,
                "_memory_path",
                return_value=memory_path,
            ), patch.object(
                consulting_copilot,
                "_sync_meta_path",
                return_value=sync_path,
            ), patch.object(
                consulting_copilot,
                "_conflict_path",
                return_value=conflict_path,
            ), patch.object(
                consulting_copilot,
                "_cloud_asset_payloads",
                return_value={
                    SYNTHETIC_BUSINESS_NO_DIGITS: {
                        "note": "old-cloud",
                        "cloud_only": "preserved",
                        "updated_at": "2026-08-03T09:00:00+00:00",
                    }
                },
            ):
                value = consulting_copilot.get_company_memory(
                    "owner",
                    "synthetic-company",
                    SYNTHETIC_BUSINESS_NO,
                )
            self.assertEqual(value["note"], "new-local")
            self.assertEqual(value["cloud_only"], "preserved")
            conflicts = json.loads(conflict_path.read_text(encoding="utf-8"))
            self.assertEqual(conflicts[0]["chosen_source"], "local")
            self.assertEqual(conflicts[0]["local_payload"]["note"], "new-local")
            self.assertEqual(conflicts[0]["cloud_payload"]["note"], "old-cloud")

    def test_newer_cloud_memory_updates_local_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory_path = root / "memory.json"
            memory_path.write_text(
                json.dumps(
                    {
                        SYNTHETIC_BUSINESS_NO_DIGITS: {
                            "note": "old-local",
                            "updated_at": "2026-08-03T08:00:00+00:00",
                        }
                    }
                ),
                encoding="utf-8",
            )
            consulting_copilot._CLOUD_SOURCE_TIMES.clear()
            consulting_copilot._CLOUD_SOURCE_TIMES[
                ("owner", "memory", SYNTHETIC_BUSINESS_NO_DIGITS)
            ] = "2026-08-03T10:00:00+00:00"
            with patch.object(
                consulting_copilot,
                "_memory_path",
                return_value=memory_path,
            ), patch.object(
                consulting_copilot,
                "_sync_meta_path",
                return_value=root / "sync.json",
            ), patch.object(
                consulting_copilot,
                "_conflict_path",
                return_value=root / "conflicts.json",
            ), patch.object(
                consulting_copilot,
                "_cloud_asset_payloads",
                return_value={
                    SYNTHETIC_BUSINESS_NO_DIGITS: {
                        "note": "new-cloud",
                    }
                },
            ):
                value = consulting_copilot.get_company_memory(
                    "owner",
                    "synthetic-company",
                    SYNTHETIC_BUSINESS_NO,
                )
            self.assertEqual(value["note"], "new-cloud")
            stored = json.loads(memory_path.read_text(encoding="utf-8"))
            self.assertEqual(
                stored[SYNTHETIC_BUSINESS_NO_DIGITS]["note"],
                "new-cloud",
            )

    def test_malformed_local_copilot_memory_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            original = b'{"broken":'
            path.write_bytes(original)
            with patch.object(
                consulting_copilot,
                "_memory_path",
                return_value=path,
            ):
                with self.assertRaises(
                    consulting_copilot.CopilotLocalDataCorruptionError
                ):
                    consulting_copilot.get_company_memory(
                        "owner",
                        "synthetic-company",
                        SYNTHETIC_BUSINESS_NO,
                    )
            self.assertEqual(path.read_bytes(), original)


class ConsultationAudioArchiveTests(unittest.TestCase):
    def test_regular_delete_archives_without_physical_delete(self) -> None:
        response = Mock()
        response.ok = True
        response.text = '[{"audio_id":"audio-1","status":"archived"}]'
        response.json.return_value = [
            {"audio_id": "audio-1", "status": "archived"}
        ]
        with patch.object(
            consultation_audio_storage,
            "storage_is_configured",
            return_value=True,
        ), patch.object(
            consultation_audio_storage.requests,
            "patch",
            return_value=response,
        ) as request_patch, patch.object(
            consultation_audio_storage.requests,
            "delete",
        ) as request_delete:
            ok, _ = consultation_audio_storage.delete_audio(
                "audio-1",
                "owner/audio.m4a",
                owner_user_id="owner",
            )
        self.assertTrue(ok)
        request_patch.assert_called_once()
        request_delete.assert_not_called()

    def test_archived_audio_is_hidden_from_regular_list(self) -> None:
        rows = [
            {
                "audio_id": "active",
                "company_name": "회사",
                "business_no": SYNTHETIC_BUSINESS_NO,
                "status": "active",
            },
            {
                "audio_id": "archived",
                "company_name": "회사",
                "business_no": SYNTHETIC_BUSINESS_NO,
                "status": "archived",
            },
        ]
        with patch.object(
            consultation_audio_storage,
            "_select_audio_rows",
            return_value=rows,
        ):
            result = consultation_audio_storage.list_company_audio(
                "owner",
                SYNTHETIC_BUSINESS_NO,
                "회사",
            )
        self.assertEqual([row["audio_id"] for row in result], ["active"])

    def test_physical_purge_is_disabled_by_default(self) -> None:
        with patch.object(
            consultation_audio_storage,
            "_purge_audio_unchecked",
        ) as purge:
            ok, _ = consultation_audio_storage.purge_audio_with_admin_approval(
                "audio-1",
                "owner/audio.m4a",
                admin_approved=True,
            )
        self.assertFalse(ok)
        purge.assert_not_called()

    def test_physical_purge_stays_disabled_even_with_flag_and_approval(self) -> None:
        with patch.object(
            consultation_audio_storage.requests,
            "delete",
        ) as request_delete:
            ok, _ = consultation_audio_storage.purge_audio_with_admin_approval(
                "audio-1",
                "owner/audio.m4a",
                admin_approved=True,
                owner_user_id="owner",
            )
        self.assertFalse(ok)
        request_delete.assert_not_called()

    def test_metadata_failure_keeps_uploaded_object_and_queues_recovery(self) -> None:
        upload_response = Mock(ok=True, status_code=200, text="")
        metadata_response = Mock(
            ok=False,
            status_code=503,
            text="private-response-must-not-leak",
        )
        with patch.object(
            consultation_audio_storage,
            "storage_is_configured",
            return_value=True,
        ), patch.object(
            consultation_audio_storage,
            "find_existing_audio",
            return_value=None,
        ), patch.object(
            consultation_audio_storage.requests,
            "post",
            side_effect=[upload_response, metadata_response],
        ), patch.object(
            consultation_audio_storage,
            "_queue_audio_metadata_recovery",
            return_value=True,
        ) as queue_recovery, patch.object(
            consultation_audio_storage.requests,
            "delete",
        ) as request_delete:
            with self.assertRaises(RuntimeError) as raised:
                consultation_audio_storage.upload_audio(
                    "owner",
                    "user",
                    "synthetic-company",
                    SYNTHETIC_BUSINESS_NO,
                    "audio.m4a",
                    b"synthetic-audio",
                )
        queue_recovery.assert_called_once()
        request_delete.assert_not_called()
        self.assertNotIn("private-response", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
