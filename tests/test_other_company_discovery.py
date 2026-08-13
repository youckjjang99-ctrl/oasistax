from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from prospect_collection_service import collect_other_companies
from prospect_db_repository import load_other_company_candidates


class _Response:
    ok = True
    status_code = 200
    text = "rows"

    def __init__(self, rows):
        self._rows = rows

    def json(self):
        return self._rows


@patch(
    "prospect_db_repository.get_cloud_config",
    return_value=SimpleNamespace(
        configured=True,
        url="https://example.supabase.co",
        timeout=20,
    ),
)
@patch(
    "prospect_db_repository._rest_headers",
    return_value={"Authorization": "Bearer test"},
)
@patch("prospect_db_repository.requests.post")
def test_other_company_repository_uses_dedicated_rpc(
    request_post,
    _headers,
    _config,
) -> None:
    request_post.return_value = _Response(
        [
            {
                "source_record_key": "contact-key",
                "business_no": "",
                "company_name": "기타 분류 업체",
                "address": "테스트 주소",
                "province_name": "서울특별시",
                "district_name": "마포구",
                "industry_code": "service-code",
                "industry_name": "서비스업",
                "industry_category": "서비스업",
                "current_employee_count": 7,
                "previous_employee_count": 9,
                "employee_growth": -2,
                "current_period": "current-period",
                "previous_period": "previous-period",
                "mobile_phone": "mobile-value",
                "landline_phone": "",
                "email": "",
                "instagram": "",
                "instagram_url": "",
                "contact_status": "matched",
            }
        ]
    )

    rows = load_other_company_candidates(
        "11",
        minimum_employees=2,
        maximum_employees=20,
        business_type="individual",
        district_name="마포구",
        industry_categories=["서비스업"],
        contact_channels=["mobile_phone"],
        limit=30,
    )

    assert len(rows) == 1
    assert rows[0]["source_key"] == "other_company:contact-key"
    assert rows[0]["고용증가신호"] is False
    assert rows[0]["신규업체"] is False
    assert rows[0]["고용증가구분"] == "고용증가·신규개업 해당 없음"
    assert request_post.call_args.args[0].endswith(
        "/rpc/oasis_search_other_companies_v2"
    )
    payload = json.loads(request_post.call_args.kwargs["data"])
    assert payload["p_province_code"] == "11"
    assert payload["p_district"] == "마포구"
    assert payload["p_industries"] == ["서비스업"]
    assert payload["p_contact_channels"] == ["mobile_phone"]
    assert payload["p_business_type"] == "individual"


@patch("prospect_collection_service.remove_existing_prospects")
@patch(
    "prospect_collection_service.remove_existing_customers",
    side_effect=lambda rows: (rows, 0),
)
@patch(
    "prospect_collection_service.load_other_company_candidates",
    return_value=[
        {
            "source_key": f"other_company:key-{index}",
            "사업자등록번호": "",
            "사업장명": f"그 외 업체 {index}",
        }
        for index in range(80)
    ],
)
@patch(
    "prospect_collection_service.existing_prospect_identities",
    return_value=(set(), set(), set()),
)
def test_other_company_collection_is_precomputed_and_limited(
    _identities,
    loader,
    _customers,
    remove_prospects,
) -> None:
    remove_prospects.side_effect = lambda rows, **_kwargs: (rows, 0)
    events: list[dict] = []

    result = collect_other_companies(
        "11",
        target_count=30,
        business_type="all",
        progress=events.append,
    )

    assert result["ok"] is True
    assert result["found_count"] == 30
    assert result["stats"]["discovery_type"] == "other"
    assert result["stats"]["other_candidates"] == 80
    assert result["growth_basis"] == "other"
    assert events[0]["stage"] == "other"
    assert events[1]["stage"] == "other_complete"
    assert loader.call_args.kwargs["limit"] == 90


def test_other_company_migration_is_disjoint_and_service_role_only() -> None:
    migration = Path(
        "supabase/migrations/20260805175024_other_company_discovery.sql"
    ).read_text(encoding="utf-8")
    normalized = " ".join(migration.lower().split())

    assert "discovery_type in ('growth', 'recent_opening', 'other')" in normalized
    assert "c.employee_growth <= 0" in normalized
    assert "c.is_new_company is false" in normalized
    assert "c.opening_signal_basis = ''" in normalized
    assert "from public, anon, authenticated" in normalized
    assert "to service_role" in normalized
    assert "security invoker" in normalized


def test_other_company_correction_uses_comwel_source_facts() -> None:
    migration = Path(
        "supabase/migrations/"
        "20260805182421_correct_other_company_source_fields.sql"
    ).read_text(encoding="utf-8")
    normalized = " ".join(migration.lower().split())

    assert "join public.oasis_comwel_annual_growth w" in normalized
    assert "w.workers_2025 as current_employee_count" in normalized
    assert "w.growth_2024_2025 <= 0" in normalized
    assert "w.is_new_2025 is false" in normalized
    assert "w.province = trim(p_province_name)" in normalized
    assert "w.district = trim(p_district)" in normalized


def test_db_request_migration_excludes_new_signals_from_other_pool() -> None:
    migration = Path(
        "supabase/migrations/20260813203000_add_db_request_discovery_type.sql"
    ).read_text(encoding="utf-8")
    normalized = " ".join(migration.lower().split())

    assert "oasis_search_other_companies_v2" in normalized
    assert "c.is_new_company is false" in normalized
    assert "c.opening_signal_basis" in normalized
    assert "s.contact_ref_key" in normalized
    assert "not exists" in normalized
