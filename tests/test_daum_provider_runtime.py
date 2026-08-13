from __future__ import annotations

from unittest.mock import Mock

import daum_provider_runtime as runtime


def test_reserve_quota_uses_separate_daum_provider() -> None:
    database = Mock()
    database.rpc.return_value = [{
        "request_count": 2,
        "reserved": True,
        "blocked_until": None,
        "last_safe_error_code": "",
        "quota_date": "2026-08-13",
    }]

    result = runtime.reserve_quota(2, database=database)

    assert result["reserved"] is True
    assert database.rpc.call_args.args[1]["p_provider"] == "daum_web"
    assert database.rpc.call_args.args[1]["p_safe_limit"] == 28_500


def test_daum_limit_cannot_exceed_endpoint_quota() -> None:
    database = Mock()

    try:
        runtime.reserve_quota(1, 30_001, database=database)
    except ValueError as exc:
        assert "30000" in str(exc)
    else:
        raise AssertionError("Daum endpoint quota must be enforced")


def test_migration_allows_daum_without_bulk_contact_rewrite() -> None:
    path = (
        runtime.__file__.replace("daum_provider_runtime.py", "")
        + "supabase/migrations/20260813223000_add_daum_web_quota_tracking.sql"
    )
    sql = open(path, encoding="utf-8").read().lower()

    assert "'kakao_local', 'daum_web'" in sql
    assert "when v_provider = 'daum_web' then 30000" in sql
    assert "to service_role" in sql
    assert "update public.oasis_employment_contacts" not in sql


def test_requeue_is_scoped_and_preserves_existing_phone_values() -> None:
    path = (
        runtime.__file__.replace("daum_provider_runtime.py", "")
        + "supabase/migrations/"
        + "20260813224000_requeue_mobile_gap_after_daum_query_fix.sql"
    )
    sql = open(path, encoding="utf-8").read().lower()

    assert "phone_provider_stage = 'complete'" in sql
    assert "not coalesce(has_mobile_phone, false)" in sql
    assert "2026-08-13 00:00:00+09" in sql
    assert "2026-08-14 00:00:00+09" in sql
    assert "phone_provider_stage = 'daum'" in sql
    assert "mobile_phone =" not in sql
    assert "landline_phone =" not in sql
    assert "contact_sources =" not in sql
