from __future__ import annotations

import json
import sys
import types

employee_status_stub = types.ModuleType("employee_status")
employee_status_stub.get_latest_employee_status = lambda *args, **kwargs: {}
sys.modules.setdefault("employee_status", employee_status_stub)

import consulting_report as report


def test_consulting_report_uses_cloud_financial_over_stale_local(
    monkeypatch,
    tmp_path,
):
    (tmp_path / "stock_financial_cache.json").write_text(
        json.dumps(
            {
                "123-45-67890": {
                    "당기순이익": "-",
                    "자산총계": 100,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        report,
        "get_user_dirs",
        lambda user_id: {"base": tmp_path},
    )
    monkeypatch.setattr(
        report,
        "load_financial_snapshot",
        lambda user_id, business_no: {
            "당기순이익": 76_000_000,
            "자산총계": 830_000_000,
        },
    )

    snapshot = report._financial_snapshot("member", "1234567890")

    assert snapshot["당기순이익"] == 76_000_000
    assert snapshot["자산총계"] == 830_000_000

