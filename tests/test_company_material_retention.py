"""Synthetic company-material isolation, concurrent retention and recovery tests."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import Mock

import pytest

import articles_review
import employee_status
from crm_file_store import CrmStorageError, atomic_write_json

OWNER = "synthetic-owner"
OTHER_OWNER = "different-owner"
BUSINESS = "".join(("123", "45", "67890"))
OTHER_BUSINESS = "".join(("987", "65", "43210"))
DISPLAY_BUSINESS = "-".join(("123", "45", "67890"))


@pytest.fixture(autouse=True)
def isolated_materials(monkeypatch, tmp_path):
    monkeypatch.setattr(articles_review, "_review_path", lambda owner: tmp_path / f"articles-{owner}.json")
    monkeypatch.setattr(employee_status, "_storage_path", lambda owner: tmp_path / f"employee-{owner}.json")
    monkeypatch.setattr(articles_review, "save_customer_event", Mock())
    monkeypatch.setattr(employee_status, "save_customer_event", Mock())
    monkeypatch.setattr(employee_status, "cloud_is_configured", lambda: False)
    import cloud_db
    monkeypatch.setattr(cloud_db, "cloud_is_configured", lambda: False)
    uncached = articles_review._load_remote_articles_reviews.__wrapped__
    monkeypatch.setattr(uncached, "clear", Mock(), raising=False)
    monkeypatch.setattr(articles_review, "_load_remote_articles_reviews", uncached)


def employee_version(version_id="v1", business=BUSINESS, owner=OWNER, stamp="2026-01-01"):
    return {"version_id": version_id, "owner_user_id": owner, "business_no": business,
            "company_name": "동일한 합성명", "uploaded_at": stamp,
            "summary": {}, "employees": [], "parse_info": {}}


def test_employee_save_keeps_more_than_twenty_versions_and_has_unique_ids():
    versions = [employee_status.save_employee_roster(
        OWNER, DISPLAY_BUSINESS, "합성 기업", "same.csv", [], {},
    )[0] for _ in range(25)]
    loaded = employee_status.load_employee_versions(OWNER, BUSINESS, "합성 기업")
    assert len(loaded) == 25
    assert len({item["version_id"] for item in versions}) == 25
    assert all(item["business_no"] == BUSINESS for item in loaded)


def test_employee_simultaneous_saves_do_not_drop_versions():
    def save(index):
        return employee_status.save_employee_roster(OWNER, BUSINESS, "합성 기업", f"{index}.csv", [], {})
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(save, range(16)))
    assert len(employee_status.load_employee_versions(OWNER, BUSINESS)) == 16


@pytest.mark.parametrize("content", ['{"broken":', '{"a": 1, "a": 2}', '[]'])
def test_employee_corrupt_store_never_overwritten(content):
    path = employee_status._storage_path(OWNER)
    path.write_text(content, encoding="utf-8")
    with pytest.raises(CrmStorageError):
        employee_status.save_employee_roster(OWNER, BUSINESS, "합성 기업", "test.csv", [], {})
    assert path.read_text(encoding="utf-8") == content


def test_employee_payload_is_detached_from_callers():
    employees = [{"name_masked": "합*", "status": "가입중"}]
    info = {"files": ["before"]}
    record, _ = employee_status.save_employee_roster(OWNER, BUSINESS, "합성 기업", "test.csv", employees, info)
    employees[0]["name_masked"] = "changed"
    info["files"].append("after")
    assert record["employees"][0]["name_masked"] == "합*"
    assert record["parse_info"]["files"] == ["before"]


def test_employee_cloud_query_filters_company_before_paginating(monkeypatch):
    monkeypatch.setattr(employee_status, "cloud_is_configured", lambda: True)
    database = Mock()
    rows = [{**employee_version(f"v{index}"), "employee_data": [], "summary_data": {}} for index in range(25)]
    rows.extend([employee_version("wrong-business", OTHER_BUSINESS), employee_version("wrong-owner", owner=OTHER_OWNER)])
    database.select_all.side_effect = [rows, []]
    monkeypatch.setattr(employee_status, "CloudDatabase", lambda: database)
    loaded = employee_status._load_cloud_employee_versions(OWNER, BUSINESS, "동일한 합성명")
    assert len(loaded) == 25
    assert [call.kwargs["filters"] for call in database.select_all.call_args_list] == [
        {"owner_user_id": OWNER, "business_no": BUSINESS},
        {"owner_user_id": OWNER, "business_no": DISPLAY_BUSINESS},
    ]
    assert all(call.kwargs["order"] == "uploaded_at.desc,version_id.desc"
               for call in database.select_all.call_args_list)


def test_employee_merge_retains_newer_local_unsynced_and_cloud_history(monkeypatch):
    local = [employee_version("local-new", stamp="2026-02-02"), employee_version("same")]
    cloud = [employee_version("same"), employee_version("cloud-only", stamp="2026-01-02")]
    monkeypatch.setattr(employee_status, "_load_local_employee_versions", lambda *a: deepcopy(local))
    monkeypatch.setattr(employee_status, "_load_cloud_employee_versions", lambda *a: deepcopy(cloud))
    loaded = employee_status.load_employee_versions(OWNER, BUSINESS)
    assert [item["version_id"] for item in loaded] == ["local-new", "cloud-only", "same"]


def test_employee_same_company_name_cannot_cross_owner_or_business():
    records = [employee_version(), employee_version("wrong-business", OTHER_BUSINESS),
               employee_version("wrong-owner", owner=OTHER_OWNER)]
    atomic_write_json(employee_status._storage_path(OWNER), {BUSINESS: records})
    assert [item["version_id"] for item in employee_status.load_employee_versions(OWNER, BUSINESS)] == ["v1"]
    assert employee_status.load_employee_versions(OWNER, "", "동일한 합성명") == []


def test_articles_preserve_legacy_latest_shape_and_all_versions():
    path = articles_review._review_path(OWNER)
    atomic_write_json(path, {BUSINESS: {"business_no": BUSINESS, "filename": "legacy.txt", "reviewed_at": "2025"}})
    for index in range(25):
        articles_review.save_articles_review(OWNER, BUSINESS, "합성 기업", f"{index}.txt", {"reviewed_at": f"2026-{index:02d}"})
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw[BUSINESS]["filename"] == "24.txt"
    assert len(raw[BUSINESS]["_versions"]) == 26
    assert len(articles_review.load_articles_review_versions(OWNER, BUSINESS)) == 26
    assert articles_review.get_latest_articles_review(OWNER, BUSINESS)["filename"] == "24.txt"


def test_articles_concurrent_append_preserves_all():
    def save(index):
        return articles_review.save_articles_review(OWNER, BUSINESS, "합성 기업", f"{index}.txt", {"reviewed_at": "2026"})
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(save, range(16)))
    assert len(articles_review.load_articles_review_versions(OWNER, BUSINESS)) == 16


@pytest.mark.parametrize("content", ['{"broken":', '{"a": 1, "a": 2}', '[]',
                                        json.dumps({BUSINESS: {"_versions": "invalid"}})])
def test_articles_corrupt_store_never_overwritten(content):
    path = articles_review._review_path(OWNER)
    path.write_text(content, encoding="utf-8")
    with pytest.raises((CrmStorageError, ValueError)):
        articles_review.save_articles_review(OWNER, BUSINESS, "합성 기업", "test.txt", {})
    assert path.read_text(encoding="utf-8") == content


def test_articles_remote_fallback_after_local_disappears(monkeypatch):
    import cloud_db
    import customer_history
    monkeypatch.setattr(cloud_db, "cloud_is_configured", lambda: True)
    review = {"version_id": "recovered", "business_no": BUSINESS, "owner_user_id": OWNER, "score": 70}
    monkeypatch.setattr(customer_history, "get_customer_history", lambda *a, **kw: [
        {"business_no": BUSINESS, "data": {"문서유형": "articles", "정관분석": review}},
        {"business_no": OTHER_BUSINESS, "data": {"문서유형": "articles", "정관분석": review}},
    ])
    versions = articles_review.load_articles_review_versions(OWNER, BUSINESS)
    assert [item["version_id"] for item in versions] == ["recovered"]


def test_articles_name_or_wrong_context_does_not_recover():
    assert articles_review.load_articles_review_versions(OWNER, "", "합성 기업") == []
    assert articles_review._articles_versions_from_value(
        [{"owner_user_id": OTHER_OWNER}, {"business_no": OTHER_BUSINESS}], OWNER, BUSINESS,
    ) == []
