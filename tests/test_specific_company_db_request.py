from __future__ import annotations

import inspect
from pathlib import Path

import company_sales_assignment as assignments
import prospect_db_center as prospect


SYNTHETIC_BUSINESS_NO = "123-45-67890"


class _FakeDatabase:
    def __init__(self, responses: dict[str, object]):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, parameters: dict):
        self.calls.append((name, parameters))
        return self.responses.get(name)


def test_exact_search_normalizes_business_no_and_drops_raw_mobile():
    database = _FakeDatabase(
        {
            assignments.RPC_SEARCH_COMPANY_DB_BY_BUSINESS_NO: [
                {
                    "found": True,
                    "code": "found",
                    "company_uid": "business:1234567890",
                    "company_name": "테스트 업체",
                    "business_no": "1234567890",
                    "masked_mobile_phone": "010-****-5678",
                    "mobile_phone": "must-not-leak",
                    "requestable": True,
                    "request_status": "available",
                }
            ]
        }
    )

    result = assignments.search_company_db_by_business_no(
        " Sales-User ",
        SYNTHETIC_BUSINESS_NO,
        db=database,
    )

    assert result["ok"] is True
    assert result["found"] is True
    assert result["result"]["masked_mobile_phone"] == "010-****-5678"
    assert "mobile_phone" not in result["result"]
    assert database.calls == [
        (
            assignments.RPC_SEARCH_COMPANY_DB_BY_BUSINESS_NO,
            {
                "p_current_user_id": "sales-user",
                "p_business_no": "1234567890",
            },
        )
    ]


def test_invalid_business_no_stops_before_rpc():
    database = _FakeDatabase({})

    result = assignments.search_company_db_by_business_no(
        "sales-user",
        "123",
        db=database,
    )

    assert result["ok"] is False
    assert result["code"] == "INVALID_INPUT"
    assert database.calls == []


def test_submit_and_admin_review_use_dedicated_service_rpcs():
    database = _FakeDatabase(
        {
            assignments.RPC_SUBMIT_SPECIFIC_COMPANY_DB_REQUEST: [
                {
                    "success": True,
                    "code": "requested",
                    "message": "접수 완료",
                    "request_id": "request-1",
                    "status": "pending",
                }
            ],
            assignments.RPC_ADMIN_REVIEW_SPECIFIC_COMPANY_DB_REQUEST: [
                {
                    "success": True,
                    "code": "approved",
                    "message": "승인 완료",
                    "request_id": "request-1",
                    "status": "approved",
                    "assignment_id": "assignment-1",
                }
            ],
        }
    )

    submitted = assignments.submit_specific_company_db_request(
        "sales-user",
        SYNTHETIC_BUSINESS_NO,
        session_id="session-1",
        db=database,
    )
    reviewed = assignments.admin_review_specific_company_db_request(
        "admin-user",
        "request-1",
        "approve",
        reason="승인",
        session_id="session-2",
        db=database,
    )

    assert submitted["ok"] is True
    assert submitted["request"]["request_id"] == "request-1"
    assert reviewed["ok"] is True
    assert reviewed["assignment"]["assignment_id"] == "assignment-1"
    assert database.calls[0][1]["p_business_no"] == "1234567890"
    assert database.calls[1][1]["p_action"] == "approve"


def test_list_helpers_whitelist_fields_and_never_return_raw_mobile():
    database = _FakeDatabase(
        {
            assignments.RPC_LIST_USER_SPECIFIC_COMPANY_DB_REQUESTS: [
                {
                    "request_id": "request-1",
                    "company_name": "테스트 업체",
                    "masked_mobile_phone": "010-****-5678",
                    "mobile_phone": "must-not-leak",
                    "private_column": "must-not-leak",
                    "status": "pending",
                }
            ],
            assignments.RPC_LIST_ADMIN_SPECIFIC_COMPANY_DB_REQUESTS: [
                {
                    "request_id": "request-1",
                    "requested_user_id": "sales-user",
                    "company_name": "테스트 업체",
                    "masked_mobile_phone": "010-****-5678",
                    "mobile_phone": "must-not-leak",
                    "status": "pending",
                }
            ],
        }
    )

    user_result = assignments.list_user_specific_company_db_requests(
        "sales-user", db=database
    )
    admin_result = assignments.list_admin_specific_company_db_requests(
        "admin-user", statuses=["pending"], db=database
    )

    assert "mobile_phone" not in user_result["requests"][0]
    assert "private_column" not in user_result["requests"][0]
    assert "mobile_phone" not in admin_result["requests"][0]
    assert database.calls[1][1]["p_statuses"] == ["pending"]


def test_migration_is_service_only_and_approval_is_atomic():
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "20260813080857_add_specific_company_db_requests.sql"
    ).read_text(encoding="utf-8").lower()

    table_section = migration.split(
        "create table if not exists public.oasis_specific_company_db_requests",
        1,
    )[1].split(");", 1)[0]
    assert "mobile_phone" not in table_section
    assert "landline_phone" not in table_section
    assert "enable row level security" in migration
    assert "from public, anon, authenticated" in migration
    assert "to service_role" in migration
    assert "security invoker" in migration
    assert "idx_oasis_specific_company_db_requests_one_pending_company" in migration
    assert "masked_mobile_phone" in migration
    assert "oasis_claim_and_save_company_sales_assignment(" in migration
    assert "for update" in migration


def test_ui_places_exact_search_on_request_screen_and_admin_queue():
    request_source = inspect.getsource(prospect._render_db_request_home)
    search_source = inspect.getsource(prospect._render_specific_company_db_search)
    admin_source = inspect.getsource(prospect._render_mobile_db_admin)
    admin_queue_source = inspect.getsource(
        prospect._render_specific_company_db_admin
    )

    assert "_render_specific_company_db_search(owner_user_id)" in request_source
    assert "사업자등록번호로 DB 찾기" in search_source
    assert "masked_mobile_phone" in search_source
    assert 'row.get("mobile_phone")' not in search_source
    assert "이 업체 DB 신청" in search_source
    assert "_render_specific_company_db_admin(current_user_id)" in admin_source
    assert "승인 및 DB 배정" in admin_queue_source
    assert 'statuses=["pending"]' in admin_queue_source
