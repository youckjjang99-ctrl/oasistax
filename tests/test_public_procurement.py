from __future__ import annotations

from datetime import date

import pytest

from cloud_db import TABLE_CUSTOMER_PROCUREMENT
from public_procurement import (
    ProcurementConfigError,
    PublicProcurementClient,
    default_query_period,
    load_procurement_summary,
    save_procurement_summary,
)


class FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, items, *, total=None):
        self.items = items
        self.total = len(items) if total is None else total

    def json(self):
        return {
            "response": {
                "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
                "body": {
                    "totalCount": self.total,
                    "items": {"item": self.items},
                },
            }
        }


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def supplier_item(name="(주)테스트", unity_no=""):
    return {
        "bizno": "1234567890",
        "corpNm": name,
        "corpUntyNo": unity_no,
        "chgDt": "20260801",
    }


def stats_item(*, unity_no="U-1", system="나라장터", amount=1000, name="(주)테스트"):
    return {
        "corpNm": name,
        "corpUntyNo": unity_no,
        "linkSystmNm": system,
        "arsltSumNum": "1",
        "arsltSumAmt": str(amount),
        "prdctArsltNum": "1",
        "prdctArsltAmt": str(amount),
        "cnstwkArsltNum": "0",
        "cnstwkArsltAmt": "0",
        "gnrlSrvceArsltNum": "0",
        "gnrlSrvceArsltAmt": "0",
        "techSrvceArsltNum": "0",
        "techSrvceArsltAmt": "0",
        "unClsfcArsltNum": "0",
        "unClsfcArsltAmt": "0",
    }


def test_service_key_is_required():
    with pytest.raises(ProcurementConfigError):
        PublicProcurementClient(service_key="")


def test_default_period_is_recent_36_months():
    assert default_query_period(date(2026, 8, 13)) == ("202309", "202608")


def test_collect_summary_uses_business_number_identity_and_aggregates():
    session = FakeSession(
        [
            FakeResponse([supplier_item(unity_no="U-1")]),
            FakeResponse(
                [
                    stats_item(system="나라장터", amount=1000, name="주식회사 테스트"),
                    stats_item(system="국방전자조달", amount=2500),
                ]
            ),
        ]
    )
    client = PublicProcurementClient(service_key="test", session=session)

    summary = client.collect_summary(
        "123-45-67890",
        start_ym="202601",
        end_ym="202608",
    )

    assert summary["match_status"] == "matched"
    assert summary["total_count"] == 2
    assert summary["total_amount"] == 3500
    assert summary["product_count"] == 2
    assert summary["source_systems"] == ["국방전자조달", "나라장터"]
    supplier_params = session.calls[0][1]["params"]
    assert supplier_params["bizno"] == "1234567890"
    assert "serviceKey" in supplier_params


def test_collect_summary_stops_when_same_name_has_multiple_unity_numbers():
    session = FakeSession(
        [
            FakeResponse([supplier_item(unity_no="")]),
            FakeResponse(
                [
                    stats_item(unity_no="U-1", amount=1000),
                    stats_item(unity_no="U-2", amount=9000),
                ]
            ),
        ]
    )
    client = PublicProcurementClient(service_key="test", session=session)

    summary = client.collect_summary("1234567890")

    assert summary["match_status"] == "ambiguous"
    assert summary["total_count"] == 0
    assert summary["total_amount"] == 0


def test_collect_summary_paginates_statistics():
    first_page = [stats_item(unity_no="U-1") for _ in range(100)]
    session = FakeSession(
        [
            FakeResponse([supplier_item(unity_no="U-1")]),
            FakeResponse(first_page, total=101),
            FakeResponse([stats_item(unity_no="U-1")], total=101),
        ]
    )
    client = PublicProcurementClient(service_key="test", session=session)

    summary = client.collect_summary("1234567890")

    assert summary["total_count"] == 101
    assert session.calls[1][1]["params"]["pageNo"] == 1
    assert session.calls[2][1]["params"]["pageNo"] == 2


class FakeDatabase:
    def __init__(self):
        self.select_calls = []
        self.upsert_calls = []

    def select(self, table, **kwargs):
        self.select_calls.append((table, kwargs))
        return [{"business_no": "123-45-67890", "match_status": "matched"}]

    def upsert(self, table, rows, on_conflict):
        self.upsert_calls.append((table, rows, on_conflict))
        return rows


def test_repository_reads_and_writes_with_owner_scope():
    database = FakeDatabase()
    loaded = load_procurement_summary(
        "Sales.User",
        "1234567890",
        database=database,
    )
    saved = save_procurement_summary(
        "Sales.User",
        {
            "business_no": "1234567890",
            "query_start_ym": "202601",
            "query_end_ym": "202608",
            "match_status": "not_found",
            "source_systems": [],
        },
        database=database,
    )

    table, select_options = database.select_calls[0]
    assert table == TABLE_CUSTOMER_PROCUREMENT
    assert select_options["filters"] == {
        "owner_user_id": "sales.user",
        "business_no": "123-45-67890",
    }
    upsert_table, rows, conflict = database.upsert_calls[0]
    assert upsert_table == TABLE_CUSTOMER_PROCUREMENT
    assert conflict == "owner_user_id,business_no"
    assert rows[0]["owner_user_id"] == "sales.user"
    assert saved["business_no"] == "123-45-67890"
