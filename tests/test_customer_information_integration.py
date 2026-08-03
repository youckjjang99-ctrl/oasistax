from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import cloud_db
import cloud_sync
import registered_policy_match
import sync_outbox
from registered_policy_match import (
    _load_registered_customers_from_cloud,
    _merge_registered_customer_frames,
    build_customer_labels,
    customer_preview,
)
from sync_outbox import (
    enqueue_local_outbox,
    enqueue_rpc_outbox,
    load_local_outbox,
    make_outbox_job,
    make_rpc_outbox_job,
    retry_cloud_outbox,
    retry_local_outbox,
)


BUSINESS_ONE = "-".join(("123", "45", "67890"))
BUSINESS_TWO = "-".join(("234", "56", "78901"))
BUSINESS_THREE = "-".join(("345", "67", "89012"))
CUSTOMER_ID = "11111111-1111-4111-8111-111111111111"
COMPANY_UID = "business:" + BUSINESS_ONE.replace("-", "")
SYNTHETIC_PHONE = "-".join(("010", "1234", "5678"))


def test_customer_frames_are_losslessly_unioned_with_local_precedence() -> None:
    local = pd.DataFrame(
        [
            {
                "업체명": "로컬 우선",
                "대표자명": "",
                "사업자등록번호": BUSINESS_ONE,
                "사업장 소재지": "로컬 주소",
            },
            {
                "업체명": "로컬 전용",
                "사업자등록번호": BUSINESS_TWO,
            },
            {
                "업체명": "식별번호 없음",
                "사업자등록번호": "",
                "사업장 소재지": "같은 주소",
            },
        ]
    )
    cloud = pd.DataFrame(
        [
            {
                "업체명": "클라우드 값",
                "대표자명": "클라우드 보충",
                "사업자등록번호": BUSINESS_ONE.replace("-", ""),
                "사업장 소재지": "클라우드 주소",
                "_customer_id": CUSTOMER_ID,
                "_company_uid": COMPANY_UID,
                "_lifecycle_status": "active",
                "_cloud_updated_at": "2026-08-04T01:00:00Z",
            },
            {
                "업체명": "클라우드 전용",
                "사업자등록번호": BUSINESS_THREE,
                "_customer_id": "cloud-only",
            },
            {
                "업체명": "식별번호 없음",
                "사업자등록번호": "",
                "사업장 소재지": "같은 주소",
                "_customer_id": "unidentified-cloud",
            },
        ]
    )

    merged = _merge_registered_customer_frames(local, cloud)

    assert len(merged) == 5
    identified = merged.loc[
        merged["사업자등록번호"] == BUSINESS_ONE
    ].iloc[0]
    assert identified["업체명"] == "로컬 우선"
    assert identified["대표자명"] == "클라우드 보충"
    assert identified["사업장 소재지"] == "로컬 주소"
    assert identified["_customer_id"] == CUSTOMER_ID
    assert identified["_company_uid"] == COMPANY_UID
    assert set(merged["업체명"]) >= {
        "로컬 전용",
        "클라우드 전용",
    }
    assert list(merged["업체명"]).count("식별번호 없음") == 2


def test_name_and_address_never_merge_without_exact_ten_digit_identity() -> None:
    local = pd.DataFrame(
        [{
            "업체명": "동일 표시명",
            "사업장 소재지": "동일 표시주소",
            "사업자등록번호": "123456789",
        }]
    )
    cloud = pd.DataFrame(
        [{
            "업체명": "동일 표시명",
            "사업장 소재지": "동일 표시주소",
            "사업자등록번호": "123456789",
            "대표자명": "합쳐지면 안 됨",
            "_customer_id": CUSTOMER_ID,
        }]
    )

    merged = _merge_registered_customer_frames(local, cloud)

    assert len(merged) == 2
    assert merged.iloc[0]["업체명"] == "동일 표시명"
    assert pd.isna(merged.iloc[0]["대표자명"])
    assert merged.iloc[1]["_customer_id"] == CUSTOMER_ID


def test_duplicate_business_numbers_keep_excess_source_rows() -> None:
    local = pd.DataFrame(
        [
            {"업체명": "로컬 1", "사업자등록번호": BUSINESS_ONE},
            {"업체명": "로컬 2", "사업자등록번호": BUSINESS_ONE},
        ]
    )
    cloud = pd.DataFrame(
        [{
            "업체명": "클라우드 1",
            "사업자등록번호": BUSINESS_ONE,
            "_customer_id": CUSTOMER_ID,
        }]
    )

    merged = _merge_registered_customer_frames(local, cloud)

    assert len(merged) == 3
    assert list(merged["업체명"]) == ["로컬 1", "로컬 2", "클라우드 1"]
    assert pd.isna(merged.iloc[0]["_customer_id"])
    assert pd.isna(merged.iloc[1]["_customer_id"])
    assert merged.iloc[2]["_customer_id"] == CUSTOMER_ID


def test_cloud_metadata_is_not_exposed_in_labels_or_preview() -> None:
    frame = pd.DataFrame(
        [{
            "업체명": "표시 회사",
            "대표자명": "표시 담당",
            "사업자등록번호": BUSINESS_ONE,
            "_customer_id": CUSTOMER_ID,
            "_company_uid": COMPANY_UID,
            "_lifecycle_status": "active",
            "_cloud_updated_at": "2026-08-04T01:00:00Z",
        }]
    )

    labels, row_map = build_customer_labels(frame)
    preview = customer_preview(frame.iloc[0])

    rendered = " ".join(labels) + " " + preview.to_string()
    assert row_map[labels[0]] == 0
    assert CUSTOMER_ID not in rendered
    assert COMPANY_UID not in rendered
    assert "_lifecycle_status" not in rendered
    assert "_cloud_updated_at" not in rendered


def test_archived_cloud_rows_remain_in_frame_but_not_active_labels() -> None:
    frame = pd.DataFrame(
        [
            {
                "업체명": "활성 고객",
                "사업자등록번호": BUSINESS_ONE,
                "_lifecycle_status": "active",
            },
            {
                "업체명": "보관 고객",
                "사업자등록번호": BUSINESS_TWO,
                "_lifecycle_status": "archived",
            },
        ]
    )

    labels, _row_map = build_customer_labels(frame)

    assert len(frame) == 2
    assert any("활성 고객" in label for label in labels)
    assert all("보관 고객" not in label for label in labels)


class _CustomerLoaderDatabase:
    def __init__(self, *, rpc_result=None, rpc_error: Exception | None = None):
        self.rpc_result = rpc_result
        self.rpc_error = rpc_error
        self.rpc_calls = []
        self.select_calls = []

    def rpc(self, name, parameters):
        self.rpc_calls.append((name, parameters))
        if self.rpc_error is not None:
            raise self.rpc_error
        return self.rpc_result

    def select_all(self, table, **kwargs):
        self.select_calls.append((table, kwargs))
        return [{
            "id": "legacy-id",
            "business_no": BUSINESS_TWO,
            "company_name": "레거시 조회",
            "customer_data": {},
            "company_uid": "business:" + BUSINESS_TWO.replace("-", ""),
            "lifecycle_status": "active",
            "updated_at": "2026-08-04T02:00:00Z",
        }]


def _install_customer_loader_database(monkeypatch, database) -> None:
    monkeypatch.setattr(cloud_db, "cloud_is_configured", lambda: True)
    monkeypatch.setattr(cloud_db, "CloudDatabase", lambda: database)


def test_cloud_loader_prefers_unified_rpc_and_preserves_metadata(
    monkeypatch,
) -> None:
    database = _CustomerLoaderDatabase(
        rpc_result=[{
            "id": CUSTOMER_ID,
            "business_no": BUSINESS_ONE.replace("-", ""),
            "company_name": "통합 조회",
            "representative_name": "대표",
            "industry_name": "제조",
            "address": "주소",
            "manager_name": "담당",
            "customer_data": {"추가필드": "보존"},
            "company_uid": COMPANY_UID,
            "lifecycle_status": "active",
            "updated_at": "2026-08-04T01:00:00Z",
        }]
    )
    _install_customer_loader_database(monkeypatch, database)

    result = _load_registered_customers_from_cloud("Owner-A")

    assert database.rpc_calls == [
        (
            "oasis_list_unified_customers",
            {"p_owner_user_id": "owner-a"},
        )
    ]
    assert database.select_calls == []
    assert result is not None
    assert result.iloc[0]["사업자등록번호"] == BUSINESS_ONE
    assert result.iloc[0]["추가필드"] == "보존"
    assert result.iloc[0]["_customer_id"] == CUSTOMER_ID
    assert result.iloc[0]["_company_uid"] == COMPANY_UID


def test_cloud_loader_falls_back_only_when_rpc_is_unavailable(monkeypatch) -> None:
    unavailable = _CustomerLoaderDatabase(
        rpc_error=RuntimeError(
            "PGRST202 Could not find the function in the schema cache"
        )
    )
    _install_customer_loader_database(monkeypatch, unavailable)

    result = _load_registered_customers_from_cloud("owner-a")

    assert result is not None
    assert result.iloc[0]["업체명"] == "레거시 조회"
    assert len(unavailable.select_calls) == 1

    rejected = _CustomerLoaderDatabase(
        rpc_error=RuntimeError("HTTP 409 customer profile conflict")
    )
    _install_customer_loader_database(monkeypatch, rejected)

    assert _load_registered_customers_from_cloud("owner-a") is None
    assert rejected.select_calls == []


class _ProfileDatabase:
    def __init__(self, handler=None) -> None:
        self.handler = handler
        self.rpc_calls = []
        self.upsert_calls = []

    def rpc(self, name, parameters):
        self.rpc_calls.append((name, parameters))
        if self.handler is not None:
            return self.handler(name, parameters)
        return {
            "customer_id": CUSTOMER_ID,
            "company_uid": COMPANY_UID,
            "created": False,
            "link_status": "linked",
        }

    def upsert(self, table, rows, on_conflict):
        self.upsert_calls.append((table, rows, on_conflict))
        return rows


def _disable_prewrite_retry(monkeypatch) -> None:
    monkeypatch.setattr(
        cloud_sync,
        "retry_cloud_sync_queue",
        lambda *_args, **_kwargs: {
            "success": 0,
            "failed": 0,
            "dead_letter": 0,
        },
    )


def test_customer_snapshot_calls_profile_rpc_with_stable_identity(
    monkeypatch,
) -> None:
    _disable_prewrite_retry(monkeypatch)
    database = _ProfileDatabase()

    success, _message = cloud_sync.sync_customer_snapshot(
        "owner-a",
        {
            "업체명": "로컬 회사",
            "사업자등록번호": " ".join(("123", "45", "67890")),
            "_customer_id": "metadata-id-is-overridden",
            "_company_uid": COMPANY_UID,
            "_lifecycle_status": "active",
            "_cloud_updated_at": "2026-08-04T01:00:00Z",
        },
        source="excel",
        manager_name="담당",
        customer_id=CUSTOMER_ID,
        previous_business_no=BUSINESS_TWO,
        db=database,
    )

    assert success is True
    assert database.upsert_calls == []
    name, parameters = database.rpc_calls[0]
    assert name == "oasis_upsert_customer_profile"
    assert parameters["p_business_no"] == BUSINESS_ONE
    assert parameters["p_customer_id"] == CUSTOMER_ID
    assert parameters["p_previous_business_no"] == BUSINESS_TWO
    assert parameters["p_source"] == "excel"
    assert parameters["p_manager_name"] == "담당"
    assert not any(
        key.startswith("_")
        for key in parameters["p_customer_data"]
    )


def test_missing_profile_rpc_queues_without_overwriting_cloud_fields(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _disable_prewrite_retry(monkeypatch)
    monkeypatch.setattr(sync_outbox, "durable_outbox_enabled", lambda: False)
    queue_path = tmp_path / "rpc-unavailable.json"
    monkeypatch.setattr(cloud_sync, "_queue_path", lambda _user_id: queue_path)

    def missing(name, _parameters):
        assert name == "oasis_upsert_customer_profile"
        raise RuntimeError("PGRST202 Could not find the function")

    database = _ProfileDatabase(missing)

    success, _message = cloud_sync.sync_customer_snapshot(
        "owner-a",
        {"업체명": "호환 회사", "사업자등록번호": BUSINESS_ONE},
        db=database,
    )

    assert success is False
    assert database.upsert_calls == []
    payload = load_local_outbox(queue_path)[0]["payload"]
    assert payload["operation"] == "rpc"
    assert payload["function_name"] == "oasis_upsert_customer_profile"
    assert payload["parameters"]["p_business_no"] == BUSINESS_ONE


def test_missing_profile_rpc_queues_when_stable_reference_is_present(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _disable_prewrite_retry(monkeypatch)
    monkeypatch.setattr(sync_outbox, "durable_outbox_enabled", lambda: False)
    queue_path = tmp_path / "identity-safe-queue.json"
    monkeypatch.setattr(cloud_sync, "_queue_path", lambda _user_id: queue_path)

    def missing(_name, _parameters):
        raise RuntimeError("PGRST202 Could not find the function")

    database = _ProfileDatabase(missing)
    success, _message = cloud_sync.sync_customer_snapshot(
        "owner-a",
        {"업체명": "식별자 보존", "사업자등록번호": BUSINESS_ONE},
        customer_id=CUSTOMER_ID,
        previous_business_no=BUSINESS_TWO,
        db=database,
    )

    assert success is False
    assert database.upsert_calls == []
    payload = load_local_outbox(queue_path)[0]["payload"]
    assert payload["operation"] == "rpc"
    assert payload["parameters"]["p_customer_id"] == CUSTOMER_ID
    assert payload["parameters"]["p_previous_business_no"] == BUSINESS_TWO


def test_normal_profile_rejection_is_not_counted_as_synced(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _disable_prewrite_retry(monkeypatch)
    monkeypatch.setattr(sync_outbox, "durable_outbox_enabled", lambda: False)
    queue_path = tmp_path / "rejected-profile.json"
    monkeypatch.setattr(cloud_sync, "_queue_path", lambda _user_id: queue_path)

    database = _ProfileDatabase(
        lambda _name, _parameters: {
            "customer_id": None,
            "company_uid": COMPANY_UID,
            "created": False,
            "link_status": "business_number_conflict",
        }
    )
    success, message = cloud_sync.sync_customer_snapshot(
        "owner-a",
        {"업체명": "검증 거절", "사업자등록번호": BUSINESS_ONE},
        db=database,
    )

    assert success is False
    assert "business_number_conflict" not in message
    assert database.upsert_calls == []
    assert load_local_outbox(queue_path)[0]["payload"]["operation"] == "rpc"


def test_real_profile_conflict_never_falls_back_and_queues_rpc(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _disable_prewrite_retry(monkeypatch)
    monkeypatch.setattr(sync_outbox, "durable_outbox_enabled", lambda: False)
    queue_path = tmp_path / "customer-queue.json"
    monkeypatch.setattr(cloud_sync, "_queue_path", lambda _user_id: queue_path)

    def conflict(_name, _parameters):
        raise RuntimeError(
            f"HTTP 409 profile conflict phone={SYNTHETIC_PHONE}"
        )

    database = _ProfileDatabase(conflict)

    success, message = cloud_sync.sync_customer_snapshot(
        "owner-a",
        {"업체명": "충돌 회사", "사업자등록번호": BUSINESS_ONE},
        customer_id=CUSTOMER_ID,
        previous_business_no=BUSINESS_TWO,
        db=database,
    )

    assert success is False
    assert "409" not in message
    assert database.upsert_calls == []
    queued = load_local_outbox(queue_path)
    assert len(queued) == 1
    payload = queued[0]["payload"]
    assert payload["operation"] == "rpc"
    assert payload["function_name"] == "oasis_upsert_customer_profile"
    assert payload["parameters"]["p_customer_id"] == CUSTOMER_ID
    assert payload["parameters"]["p_previous_business_no"] == BUSINESS_TWO
    assert SYNTHETIC_PHONE not in queued[0]["last_error_summary"]


def test_batch_reuses_explicit_db_and_reports_only_safe_counts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _disable_prewrite_retry(monkeypatch)
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: False)
    monkeypatch.setattr(sync_outbox, "durable_outbox_enabled", lambda: False)
    queue_path = tmp_path / "batch-queue.json"
    monkeypatch.setattr(cloud_sync, "_queue_path", lambda _user_id: queue_path)

    def handler(name, parameters):
        assert name == "oasis_upsert_customer_profile"
        if parameters["p_business_no"] == BUSINESS_TWO:
            raise RuntimeError("profile validation conflict")
        return {"customer_id": CUSTOMER_ID, "link_status": "linked"}

    database = _ProfileDatabase(handler)
    result = cloud_sync.sync_customer_snapshots(
        "owner-a",
        [
            {"업체명": "성공", "사업자등록번호": BUSINESS_ONE},
            {
                "업체명": "대기",
                "사업자등록번호": BUSINESS_TWO,
                "_customer_id": CUSTOMER_ID,
            },
            {"업체명": "번호 없음", "사업자등록번호": ""},
        ],
        db=database,
    )

    assert result == {
        "attempted": 3,
        "synced": 1,
        "queued": 1,
        "skipped": 1,
        "failed": 0,
    }
    profile_calls = [
        call for call in database.rpc_calls
        if call[0] == "oasis_upsert_customer_profile"
    ]
    assert len(profile_calls) == 2
    assert database.upsert_calls == []
    assert load_local_outbox(queue_path)[0]["payload"]["operation"] == "rpc"


def test_batch_without_cloud_queues_valid_rows_and_never_synthesizes_ids(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cloud_sync, "cloud_is_configured", lambda: False)
    monkeypatch.setattr(sync_outbox, "durable_outbox_enabled", lambda: False)
    queue_path = tmp_path / "offline-queue.json"
    monkeypatch.setattr(cloud_sync, "_queue_path", lambda _user_id: queue_path)

    result = cloud_sync.sync_customer_snapshots(
        "owner-a",
        [
            {"업체명": "유효", "사업자등록번호": BUSINESS_ONE},
            {"업체명": "번호 없음"},
        ],
    )

    assert result == {
        "attempted": 2,
        "synced": 0,
        "queued": 1,
        "skipped": 1,
        "failed": 0,
    }
    queued = load_local_outbox(queue_path)
    assert len(queued) == 1
    assert queued[0]["payload"]["parameters"]["p_business_no"] == BUSINESS_ONE
    assert queued[0]["payload"]["parameters"]["p_customer_id"] is None


def test_direct_snapshot_returns_safe_failure_if_queue_helper_raises(
    monkeypatch,
) -> None:
    _disable_prewrite_retry(monkeypatch)
    database = _ProfileDatabase(
        lambda _name, _parameters: {
            "customer_id": None,
            "link_status": "customer_reference_conflict",
        }
    )
    monkeypatch.setattr(
        cloud_sync,
        "_enqueue_customer_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("queue unavailable")
        ),
    )

    success, message = cloud_sync.sync_customer_snapshot(
        "owner-a",
        {"업체명": "안전 실패", "사업자등록번호": BUSINESS_ONE},
        db=database,
    )

    assert success is False
    assert message == "고객 동기화를 안전하게 완료하지 못했습니다."


def test_rpc_outbox_preserves_parameters_and_dispatches_locally(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(sync_outbox, "durable_outbox_enabled", lambda: False)
    path = tmp_path / "rpc-queue.json"
    parameters = {
        "p_owner_user_id": "owner-a",
        "p_customer_id": CUSTOMER_ID,
        "p_previous_business_no": BUSINESS_TWO,
    }

    location, job = enqueue_rpc_outbox(
        path,
        "owner-a",
        "customer",
        "oasis_upsert_customer_profile",
        parameters,
    )
    upsert_calls = []
    rpc_calls = []
    result = retry_local_outbox(
        path,
        lambda *args: upsert_calls.append(args),
        rpc=lambda name, params: (
            rpc_calls.append((name, params))
            or {"customer_id": CUSTOMER_ID, "link_status": "linked"}
        ),
    )

    assert location == "local"
    assert job["payload"]["parameters"] == parameters
    assert result == {"success": 1, "failed": 0, "dead_letter": 0}
    assert upsert_calls == []
    assert rpc_calls == [("oasis_upsert_customer_profile", parameters)]


def test_rpc_outbox_failure_is_sanitized_and_legacy_jobs_still_dispatch(
    tmp_path: Path,
) -> None:
    rpc_path = tmp_path / "rpc-failure.json"
    rpc_job = make_rpc_outbox_job(
        "owner-a",
        "customer",
        "oasis_upsert_customer_profile",
        {
            "p_owner_user_id": "owner-a",
            "p_business_no": BUSINESS_ONE,
        },
    )
    enqueue_local_outbox(rpc_path, rpc_job)

    def fail_rpc(_name, _parameters):
        raise RuntimeError(
            f"token=plain-secret phone={SYNTHETIC_PHONE}"
        )

    result = retry_local_outbox(
        rpc_path,
        lambda *_args: None,
        rpc=fail_rpc,
    )
    stored = load_local_outbox(rpc_path)[0]
    assert result["failed"] == 1
    assert "plain-secret" not in stored["last_error_summary"]
    assert SYNTHETIC_PHONE not in stored["last_error_summary"]

    legacy_path = tmp_path / "legacy.json"
    legacy_job = make_outbox_job(
        "owner-a",
        "customer",
        cloud_db.TABLE_CUSTOMERS,
        [{"owner_user_id": "owner-a", "business_no": BUSINESS_ONE}],
        "owner_user_id,business_no",
    )
    enqueue_local_outbox(legacy_path, legacy_job)
    legacy_calls = []
    legacy_result = retry_local_outbox(
        legacy_path,
        lambda *args: legacy_calls.append(args),
        rpc=lambda *_args: pytest.fail("legacy job must not call RPC"),
    )
    assert legacy_result["success"] == 1
    assert len(legacy_calls) == 1


def test_rpc_outbox_rejects_unvalidated_function_names() -> None:
    with pytest.raises(ValueError, match="RPC 함수명"):
        make_rpc_outbox_job(
            "owner-a",
            "customer",
            "unsafe/function",
            {},
        )

    with pytest.raises(ValueError, match="허용되지 않은"):
        make_rpc_outbox_job(
            "owner-a",
            "customer",
            "oasis_archive_customer",
            {"p_owner_user_id": "owner-a"},
        )


def test_rpc_outbox_refuses_cross_owner_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "cross-owner.json"
    job = make_rpc_outbox_job(
        "owner-a",
        "customer",
        "oasis_upsert_customer_profile",
        {"p_owner_user_id": "owner-a"},
    )
    job["payload"]["parameters"]["p_owner_user_id"] = "owner-b"
    enqueue_local_outbox(path, job)
    rpc_calls = []

    result = retry_local_outbox(
        path,
        lambda *_args: None,
        rpc=lambda *args: rpc_calls.append(args),
    )

    assert result["failed"] == 1
    assert rpc_calls == []


class _DurableRpcDatabase:
    def __init__(self) -> None:
        self.calls = []

    def rpc(self, name, parameters):
        self.calls.append((name, parameters))
        if name == "oasis_claim_sync_outbox":
            return [{
                "id": "job-1",
                "owner_user_id": "owner-a",
                "lease_token": "lease-1",
                "payload": {
                    "operation": "rpc",
                    "function_name": "oasis_upsert_customer_profile",
                    "parameters": {
                        "p_owner_user_id": "owner-a",
                        "p_customer_id": CUSTOMER_ID,
                    },
                },
            }]
        if name in {
            "oasis_complete_sync_outbox",
        }:
            return True
        if name == "oasis_upsert_customer_profile":
            return {"customer_id": CUSTOMER_ID, "link_status": "linked"}
        raise AssertionError(f"unexpected RPC: {name}")

    def upsert(self, *_args):
        pytest.fail("durable RPC job must not dispatch a table upsert")


def test_durable_outbox_dispatches_rpc_payload_before_completion() -> None:
    database = _DurableRpcDatabase()

    result = retry_cloud_outbox(
        database,
        owner_user_id="owner-a",
        worker_id="worker-a",
    )

    assert result == {"success": 1, "failed": 0}
    assert [name for name, _params in database.calls] == [
        "oasis_claim_sync_outbox",
        "oasis_upsert_customer_profile",
        "oasis_complete_sync_outbox",
    ]
