from __future__ import annotations

import pandas as pd

import stock_valuation as module


def test_customer_defaults_accepts_business_number_and_balance_aliases():
    row = pd.Series(
        {
            "업체명": "테스트기업",
            "사업자번호": "1234567890",
            "장부상총자산": "830000000",
            "장부상총부채": "649000000",
            "순이익": "76000000",
        }
    )

    defaults = module._customer_defaults(row)

    assert defaults["사업자등록번호"] == "1234567890"
    assert defaults["총자산"] == "830000000"
    assert defaults["총부채"] == "649000000"
    assert defaults["최근당기순이익"] == "76000000"


def test_normalize_financial_snapshot_preserves_three_year_net_income_and_balances():
    snapshot = module._normalize_financial_snapshot(
        {
            "총자산": 830000000,
            "총부채": 649000000,
            "순이익": 76000000,
            "재무연도별": [
                {"연도": 2025, "순이익": 76000000, "총자산": 830000000, "총부채": 649000000},
                {"연도": 2024, "순이익": 111000000, "총자산": 677000000, "총부채": 572000000},
                {"연도": 2023, "순이익": -16000000, "총자산": 198000000, "총부채": 204000000},
            ],
        }
    )

    assert snapshot["자산총계"] == 830000000
    assert snapshot["부채총계"] == 649000000
    assert snapshot["당기순이익"] == 76000000
    assert [row["당기순이익"] for row in snapshot["재무연도별"]] == [
        76000000,
        111000000,
        -16000000,
    ]


def test_apply_customer_financial_data_loads_cloud_snapshot(monkeypatch):
    monkeypatch.setattr(
        module,
        "_load_financial_cache",
        lambda user_id: {
            "123-45-67890": {
                "자산총계": None,
                "부채총계": None,
                "재무연도별": [],
            }
        },
    )
    monkeypatch.setattr(
        module,
        "load_financial_snapshot",
        lambda user_id, business_no: {
            "업체명": "테스트기업",
            "사업자등록번호": "123-45-67890",
            "자산총계": 830000000,
            "부채총계": 649000000,
            "재무연도별": [
                {"연도": 2026, "당기순이익": None},
                {"연도": 2025, "당기순이익": 76000000},
                {"연도": 2024, "당기순이익": 111000000},
                {"연도": 2023, "당기순이익": -16000000},
            ],
        },
    )
    monkeypatch.setattr(module, "_save_financial_cache", lambda user_id, data: None)
    module.st.session_state.clear()

    loaded, _ = module._apply_customer_financial_data(
        "test-user",
        pd.Series({"업체명": "테스트기업", "사업자번호": "1234567890"}),
    )

    assert loaded is True
    assert module.st.session_state["stock_business_no"] == "123-45-67890"
    assert module.st.session_state["stock_total_assets"] == "830,000,000"
    assert module.st.session_state["stock_total_liabilities"] == "649,000,000"
    assert module.st.session_state["stock_net_income_1"] == "76,000,000"
    assert module.st.session_state["stock_net_income_2"] == "111,000,000"
    assert module.st.session_state["stock_net_income_3"] == "-16,000,000"
