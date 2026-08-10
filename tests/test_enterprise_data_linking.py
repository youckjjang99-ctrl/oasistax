from __future__ import annotations

import json

import pandas as pd

import enterprise_center as enterprise
from registered_policy_match import _merge_registered_customer_frames


def test_enterprise_financial_snapshot_prefers_cloud_values(
    monkeypatch,
    tmp_path,
):
    (tmp_path / "stock_financial_cache.json").write_text(
        json.dumps(
            {
                "123-45-67890": {
                    "당기순이익": "-",
                    "자산총계": 100,
                    "로컬전용": "유지",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        enterprise,
        "get_user_dirs",
        lambda user_id: {"base": tmp_path},
    )
    monkeypatch.setattr(
        enterprise,
        "load_financial_snapshot",
        lambda user_id, business_no: {
            "당기순이익": 76_000_000,
            "자산총계": 830_000_000,
        },
    )

    snapshot = enterprise._financial_snapshot("member", "1234567890")

    assert snapshot["당기순이익"] == 76_000_000
    assert snapshot["자산총계"] == 830_000_000
    assert snapshot["로컬전용"] == "유지"


def test_enterprise_registry_snapshot_loads_cloud_when_local_is_missing(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        enterprise,
        "get_user_dirs",
        lambda user_id: {"base": tmp_path},
    )
    monkeypatch.setattr(
        enterprise,
        "load_registry_snapshot",
        lambda user_id, business_no: {
            "법인명": "테스트법인",
            "발행주식총수": 10_000,
        },
    )

    snapshot = enterprise._registry_snapshot("member", "1234567890")

    assert snapshot["법인명"] == "테스트법인"
    assert snapshot["발행주식총수"] == 10_000


def test_cloud_customer_value_replaces_dash_placeholder():
    local = pd.DataFrame(
        [{"사업자등록번호": "123-45-67890", "당기순이익": "-"}]
    )
    cloud = pd.DataFrame(
        [{"사업자등록번호": "1234567890", "당기순이익": 76_000_000}]
    )

    merged = _merge_registered_customer_frames(local, cloud)

    assert merged.iloc[0]["당기순이익"] == 76_000_000


def test_enterprise_dash_placeholder_does_not_hide_financial_snapshot():
    customer = pd.Series({"당기순이익": "-"})
    financial = {"당기순이익": 76_000_000}

    value = enterprise._first_value(
        customer,
        financial,
        "당기순이익",
    )

    assert value == 76_000_000
