from __future__ import annotations

import json

import pandas as pd

import stock_valuation as stock


def test_registry_business_number_resolves_from_registered_company(monkeypatch):
    monkeypatch.setattr(
        stock,
        "_read_customers",
        lambda user_id: pd.DataFrame(
            [
                {
                    "업체명": "테스트 주식회사",
                    "사업자등록번호": "123-45-67890",
                    "법인등록번호": "110111-1234567",
                }
            ]
        ),
    )

    resolved = stock._resolve_registry_business_no(
        "member",
        "",
        {
            "법인명": "테스트 주식회사",
            "법인등록번호": "110111-1234567",
        },
    )

    assert resolved == "123-45-67890"


def test_restore_legacy_registry_rekeys_and_syncs(monkeypatch, tmp_path):
    cache_path = tmp_path / "registry_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "110111-1234567": {
                    "법인명": "테스트 주식회사",
                    "법인등록번호": "110111-1234567",
                    "발행주식총수": 10_000,
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    synced = []
    monkeypatch.setattr(
        stock,
        "get_user_dirs",
        lambda user_id: {"base": tmp_path},
    )
    monkeypatch.setattr(stock, "load_registry_snapshot", lambda *args: {})
    monkeypatch.setattr(
        stock,
        "sync_registry_snapshot",
        lambda user_id, business_no, data: synced.append(business_no),
    )
    monkeypatch.setattr(stock, "_apply_registry_data", lambda data: None)
    stock.st.session_state.clear()

    restored = stock._restore_registry_for_business(
        "member",
        "1234567890",
        company_name="테스트 주식회사",
        corporate_no="110111-1234567",
    )

    assert restored["발행주식총수"] == 10_000
    assert restored["사업자등록번호"] == "123-45-67890"
    assert synced == ["123-45-67890"]
    saved = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "123-45-67890" in saved

