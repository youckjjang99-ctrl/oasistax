from __future__ import annotations

import hashlib
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from company_sales_assignment import (
    CompanyIdentityError,
    admin_change_assignee,
    admin_permanent_exclude,
    admin_reactivate,
    admin_release_assignment,
    admin_set_user_limit,
    assignment_feature_ready,
    assignment_status_label,
    build_company_uid,
    claim_company,
    filter_company_availability,
    list_admin_assignment_audit,
    list_admin_assignment_metrics,
    list_admin_assignments,
    list_blocked_company_uids,
    list_company_contacts,
    list_user_assignments,
    record_company_views,
    record_contact,
    resolve_candidate_company_uids,
    release_assignment,
    release_expired_assignments,
    save_user_note,
)


class _FakeDatabase:
    def __init__(self, responses=None, failures=None):
        self.responses = dict(responses or {})
        self.failures = dict(failures or {})
        self.calls: list[tuple[str, dict]] = []

    def rpc(self, name: str, parameters: dict):
        self.calls.append((name, parameters))
        if name in self.failures:
            raise self.failures[name]
        response = self.responses.get(name)
        return response(parameters) if callable(response) else response


class CompanyUidTests(unittest.TestCase):
    def test_identifier_priority_is_business_corporate_nps(self):
        company = {
            "business_no": "123-45-67890",
            "corporate_registration_no": "110111-1234567",
            "nps_workplace_management_no": "NPS-900",
        }
        self.assertEqual(build_company_uid(company), "business:1234567890")

        company["business_no"] = "invalid"
        self.assertEqual(
            build_company_uid(company),
            "corporate:1101111234567",
        )

        company["corporate_registration_no"] = "invalid"
        self.assertEqual(build_company_uid(company), "nps:NPS900")

    def test_korean_aliases_are_supported(self):
        self.assertEqual(
            build_company_uid({"사업자등록번호": "220 81 62517"}),
            "business:2208162517",
        )

    def test_fallback_normalizes_legal_marker_address_and_country_phone(self):
        first = build_company_uid(
            {
                "company_name": "주식회사  오아시스",
                "address": "서울특별시 강남구 테헤란로 1",
                "phone": "+82 (0)10-1234-5678",
            }
        )
        second = build_company_uid(
            {
                "업체명": "(주)오아시스",
                "주소": "서울특별시강남구 테헤란로1",
                "전화번호": "010 1234 5678",
            }
        )
        self.assertEqual(first, second)
        self.assertRegex(first, r"^fallback:[0-9a-f]{64}$")

    def test_incomplete_place_identity_uses_source_key_instead(self):
        uid = build_company_uid(
            {
                "company_name": "동명이름 업체",
                "address": "서울시 중구",
                "source": "nps_monthly",
                "source_record_key": "workplace-1",
            }
        )
        expected = hashlib.sha256(
            "nps_monthly|workplace-1".encode("utf-8")
        ).hexdigest()
        self.assertEqual(uid, f"source:{expected}")

    def test_name_or_name_address_alone_never_merges(self):
        with self.assertRaises(CompanyIdentityError):
            build_company_uid(
                {"company_name": "동명이름 업체", "address": "서울시 중구"}
            )
        with self.assertRaises(CompanyIdentityError):
            build_company_uid({"company_name": "동명이름 업체"})

    def test_existing_valid_uid_can_be_reused(self):
        existing = "source:" + "a" * 64
        self.assertEqual(build_company_uid({"company_uid": existing}), existing)

    def test_existing_uid_stays_stable_after_contact_enrichment(self):
        existing = "source:" + "b" * 64
        self.assertEqual(
            build_company_uid(
                {
                    "company_uid": existing,
                    "company_name": "오아시스",
                    "address": "서울시 강남구",
                    "phone": "02-123-4567",
                }
            ),
            existing,
        )

    def test_status_labels_support_code_and_legacy_korean(self):
        self.assertEqual(assignment_status_label("consulting"), "상담진행")
        self.assertEqual(assignment_status_label("번호오류"), "번호오류")
        self.assertEqual(assignment_status_label(""), "미배정")


class AssignmentRpcTests(unittest.TestCase):
    def test_claim_sends_canonical_user_and_exact_identity_payload(self):
        database = _FakeDatabase(
            {
                "oasis_claim_company_sales_assignment": [
                    {
                        "success": True,
                        "code": "ASSIGNED",
                        "assignment_id": "assignment-1",
                        "company_id": "company-1",
                        "company_uid": "business:1234567890",
                        "status": "assigned",
                        "assigned_user_id": "other-user-must-not-leak",
                    }
                ]
            }
        )

        result = claim_company(
            "  SALES@EXAMPLE.COM ",
            "company-1",
            "business:1234567890",
            session_id="session-1",
            db=database,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["assignment"]["assignment_id"], "assignment-1")
        self.assertNotIn("assigned_user_id", result["assignment"])
        self.assertEqual(
            database.calls,
            [
                (
                    "oasis_claim_company_sales_assignment",
                    {
                        "p_current_user_id": "sales@example.com",
                        "p_company_id": "company-1",
                        "p_company_uid": "business:1234567890",
                        "p_session_id": "session-1",
                    },
                )
            ],
        )

    def test_claim_conflict_uses_required_safe_message(self):
        database = _FakeDatabase(
            {
                "oasis_claim_company_sales_assignment": [
                    {
                        "success": False,
                        "code": "ASSIGNMENT_CONFLICT",
                        "message": "raw database message with private user",
                        "assigned_user_id": "private-user",
                    }
                ]
            }
        )
        result = claim_company(
            "sales-a",
            "company-1",
            "business:1234567890",
            db=database,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["message"],
            "다른 담당자가 먼저 배정받은 업체입니다. 검색 결과를 새로고침합니다.",
        )
        self.assertNotIn("private-user", str(result))

    def test_empty_company_id_is_sent_as_null_uuid(self):
        database = _FakeDatabase(
            {
                "oasis_claim_company_sales_assignment": [
                    {
                        "success": True,
                        "code": "ASSIGNED",
                        "company_uid": "source:" + "a" * 64,
                    }
                ]
            }
        )
        result = claim_company(
            "sales-a",
            "",
            "source:" + "a" * 64,
            db=database,
        )
        self.assertTrue(result["ok"])
        self.assertIsNone(database.calls[0][1]["p_company_id"])

    def test_unknown_backend_error_never_leaks_raw_detail(self):
        database = _FakeDatabase(
            failures={
                "oasis_claim_company_sales_assignment": RuntimeError(
                    "HTTP 500 secret=service-role phone=010-1234-5678"
                )
            }
        )
        result = claim_company(
            "sales-a",
            "company-1",
            "business:1234567890",
            db=database,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "ASSIGNMENT_SERVICE_UNAVAILABLE")
        self.assertNotIn("secret", str(result).lower())
        self.assertNotIn("010-", str(result))

    def test_missing_rpc_is_explicit_feature_fallback(self):
        database = _FakeDatabase(
            failures={
                "oasis_company_sales_assignment_feature_ready": RuntimeError(
                    "PGRST202 Could not find the function in schema cache"
                )
            }
        )
        ready, message = assignment_feature_ready(db=database)
        self.assertFalse(ready)
        self.assertIn("아직 데이터베이스에 적용되지", message)

    def test_release_expired_maps_scalar_count_and_parameters(self):
        database = _FakeDatabase(
            {"oasis_release_expired_company_assignments": 3}
        )
        result = release_expired_assignments(
            "Sales-A", session_id="web-1", db=database
        )
        self.assertEqual(result["released_count"], 3)
        self.assertEqual(
            database.calls[0],
            (
                "oasis_release_expired_company_assignments",
                {
                    "p_current_user_id": "sales-a",
                    "p_session_id": "web-1",
                },
            ),
        )

    def test_block_filter_maps_only_explicit_relations(self):
        database = _FakeDatabase(
            {
                "oasis_filter_blocked_company_uids": [
                    {
                        "company_uid": "business:1111111111",
                        "relation": "blocked",
                    },
                    {
                        "company_uid": "business:2222222222",
                        "relation": "own",
                    },
                    {
                        "company_uid": "business:3333333333",
                        "relation": "available",
                    },
                ]
            }
        )
        result = list_blocked_company_uids(
            "sales-a",
            [
                "business:1111111111",
                "business:2222222222",
                "business:3333333333",
                "business:3333333333",
            ],
            db=database,
        )
        self.assertEqual(
            result["blocked_company_uids"], ["business:1111111111"]
        )
        self.assertEqual(
            result["own_company_uids"], ["business:2222222222"]
        )
        self.assertEqual(
            database.calls[0][1]["p_company_uids"],
            [
                "business:1111111111",
                "business:2222222222",
                "business:3333333333",
            ],
        )

    def test_empty_block_filter_does_not_call_database(self):
        database = _FakeDatabase()
        result = list_blocked_company_uids("sales-a", [], db=database)
        self.assertTrue(result["ok"])
        self.assertEqual(database.calls, [])

    def test_availability_excludes_blocked_and_separates_own(self):
        def response(parameters):
            relations = {
                "business:1111111111": "blocked",
                "business:2222222222": "own",
                "business:3333333333": "available",
            }
            return [
                {"company_uid": uid, "relation": relations[uid]}
                for uid in parameters["p_company_uids"]
            ]

        database = _FakeDatabase(
            {"oasis_filter_blocked_company_uids": response}
        )
        result = filter_company_availability(
            [
                {"id": "1", "business_no": "1111111111"},
                {"id": "2", "business_no": "2222222222"},
                {"id": "3", "business_no": "3333333333"},
            ],
            "sales-a",
            db=database,
        )
        self.assertEqual([row["id"] for row in result["items"]], ["3"])
        self.assertEqual([row["id"] for row in result["own_items"]], ["2"])
        self.assertEqual(result["excluded_count"], 1)
        self.assertEqual(result["own_count"], 1)

    def test_availability_failure_requests_existing_fallback(self):
        database = _FakeDatabase(
            failures={
                "oasis_filter_blocked_company_uids": RuntimeError(
                    "PGRST202 schema cache"
                )
            }
        )
        original = [{"id": "1", "business_no": "1111111111"}]
        result = filter_company_availability(original, "sales-a", db=database)
        self.assertFalse(result["ready"])
        self.assertTrue(result["fallback_required"])
        self.assertEqual(result["items"][0]["id"], "1")

    def test_source_identity_is_resolved_before_block_filter(self):
        original_uid = "fallback:" + "a" * 64
        canonical_uid = "source:" + "b" * 64

        def resolve_response(parameters):
            self.assertEqual(len(parameters["p_candidates"]), 1)
            return [
                {
                    "candidate_index": 0,
                    "input_company_uid": original_uid,
                    "canonical_company_uid": canonical_uid,
                    "resolution_code": "source_identity",
                }
            ]

        database = _FakeDatabase(
            {
                "oasis_resolve_candidate_company_uids": resolve_response,
                "oasis_filter_blocked_company_uids": [
                    {"company_uid": canonical_uid, "relation": "blocked"}
                ],
            }
        )
        result = filter_company_availability(
            [
                {
                    "id": "1",
                    "company_uid": original_uid,
                    "source": "nps_monthly",
                    "source_key": "nps_monthly:workplace-1",
                    "company_name": "테스트 업체",
                    "address": "서울특별시 중구",
                    "phone": "02-123-4567",
                }
            ],
            "sales-a",
            db=database,
        )

        self.assertEqual(result["items"], [])
        self.assertEqual(result["excluded_count"], 1)
        self.assertEqual(
            result["blocked_items"][0]["company_uid"], canonical_uid
        )
        self.assertEqual(
            [call[0] for call in database.calls],
            [
                "oasis_resolve_candidate_company_uids",
                "oasis_filter_blocked_company_uids",
            ],
        )

    def test_uid_resolver_fails_closed_on_incomplete_response(self):
        uid = "source:" + "c" * 64
        database = _FakeDatabase(
            {"oasis_resolve_candidate_company_uids": []}
        )
        result = resolve_candidate_company_uids(
            "sales-a",
            [
                {
                    "company_uid": uid,
                    "source": "nps_monthly",
                    "source_key": "nps_monthly:workplace-2",
                }
            ],
            db=database,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "MALFORMED_RESPONSE")
        self.assertTrue(result["fallback_required"])

    def test_list_user_preserves_own_memo_and_saved_prospect_fields(self):
        database = _FakeDatabase(
            {
                "oasis_list_user_company_assignments": [
                    {
                        "id": "assignment-1",
                        "company_id": "company-1",
                        "company_uid": "business:1234567890",
                        "source": "nps",
                        "source_key": "nps-1",
                        "employee_count": 12,
                        "own_memo": "내 메모",
                        "other_user_memo": "노출 금지",
                    }
                ]
            }
        )
        result = list_user_assignments(
            "sales-a",
            statuses=["assigned"],
            limit=2000,
            offset=-1,
            db=database,
        )
        assignment = result["assignments"][0]
        self.assertEqual(assignment["memo"], "내 메모")
        self.assertEqual(assignment["employee_count"], 12)
        self.assertNotIn("other_user_memo", assignment)
        self.assertEqual(database.calls[0][1]["p_limit"], 1000)
        self.assertEqual(database.calls[0][1]["p_offset"], 0)

    def test_contact_requires_next_date_for_follow_up_without_rpc(self):
        database = _FakeDatabase()
        result = record_contact(
            "sales-a",
            "company-1",
            "business:1234567890",
            "전화",
            "재연락 요청",
            db=database,
        )
        self.assertEqual(result["code"], "NEXT_CONTACT_REQUIRED")
        self.assertEqual(database.calls, [])

    def test_contact_sends_exact_values_and_utc_datetimes(self):
        database = _FakeDatabase(
            {
                "oasis_record_company_sales_contact": [
                    {
                        "success": True,
                        "code": "CONTACT_RECORDED",
                        "assignment_id": "assignment-1",
                        "company_uid": "business:1234567890",
                        "status": "follow_up",
                    }
                ]
            }
        )
        contacted_at = datetime(
            2026, 8, 2, 9, 30, tzinfo=timezone(timedelta(hours=9))
        )
        result = record_contact(
            "sales-a",
            "company-1",
            "business:1234567890",
            "전화",
            "재연락 요청",
            notes="상담 메모",
            next_contact_at="2026-08-03T10:00:00+09:00",
            contacted_at=contacted_at,
            session_id="session-1",
            db=database,
        )
        self.assertTrue(result["ok"])
        params = database.calls[0][1]
        self.assertEqual(params["p_current_user_id"], "sales-a")
        self.assertEqual(params["p_contact_method"], "전화")
        self.assertEqual(params["p_contact_result"], "재연락 요청")
        self.assertEqual(params["p_notes"], "상담 메모")
        self.assertEqual(params["p_next_contact_at"], "2026-08-03T01:00:00Z")
        self.assertEqual(params["p_contacted_at"], "2026-08-02T00:30:00Z")

    def test_contact_history_uses_server_scoped_rpc_and_allowlist(self):
        database = _FakeDatabase(
            {
                "oasis_list_company_sales_contacts": [
                    {
                        "company_uid": "business:1234567890",
                        "contact_method": "전화",
                        "contact_result": "연결됨",
                        "notes": "내 상담내용",
                        "created_by_user_id": "sales-a",
                        "other_user_secret": "must-not-leak",
                    }
                ]
            }
        )
        result = list_company_contacts(
            "sales-a",
            "business:1234567890",
            limit=5000,
            offset=-1,
            db=database,
        )
        self.assertEqual(result["contacts"][0]["notes"], "내 상담내용")
        self.assertNotIn("other_user_secret", result["contacts"][0])
        self.assertEqual(database.calls[0][1]["p_limit"], 1000)
        self.assertEqual(database.calls[0][1]["p_offset"], 0)

    def test_user_note_is_scoped_by_user_and_company_uid(self):
        database = _FakeDatabase(
            {"oasis_save_user_prospect_note": True}
        )
        result = save_user_note(
            "SALES-A",
            "business:1234567890",
            "개인 메모",
            "company-1",
            db=database,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            database.calls[0][1],
            {
                "p_current_user_id": "sales-a",
                "p_company_uid": "business:1234567890",
                "p_company_id": "company-1",
                "p_memo": "개인 메모",
            },
        )

    def test_release_assignment_uses_uid_and_not_legacy_company_id(self):
        database = _FakeDatabase(
            {
                "oasis_release_company_sales_assignment": [
                    {
                        "success": True,
                        "code": "RELEASED",
                        "company_uid": "business:1234567890",
                        "status": "unassigned",
                    }
                ]
            }
        )
        result = release_assignment(
            "sales-a",
            "legacy-company-id",
            "business:1234567890",
            reason="직접 해제",
            session_id="session-1",
            db=database,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            database.calls[0][1],
            {
                "p_current_user_id": "sales-a",
                "p_company_uid": "business:1234567890",
                "p_reason": "직접 해제",
                "p_session_id": "session-1",
            },
        )

    def test_view_history_deduplicates_company_uid_and_never_assigns(self):
        database = _FakeDatabase(
            {"oasis_record_company_views": {"recorded_count": 1}}
        )
        result = record_company_views(
            "sales-a",
            [
                {"id": "company-1", "business_no": "1234567890"},
                {"id": "company-1", "business_no": "1234567890"},
            ],
            session_id="session-1",
            db=database,
        )
        self.assertEqual(result["recorded_count"], 1)
        self.assertEqual(
            database.calls,
            [
                (
                    "oasis_record_company_views",
                    {
                        "p_current_user_id": "sales-a",
                        "p_companies": [
                            {
                                "company_id": "company-1",
                                "company_uid": "business:1234567890",
                            }
                        ],
                        "p_session_id": "session-1",
                    },
                )
            ],
        )

    def test_admin_change_assignee_keeps_actor_and_target_separate(self):
        database = _FakeDatabase(
            {
                "oasis_admin_change_company_assignee": [
                    {
                        "success": True,
                        "code": "UPDATED",
                        "company_uid": "business:1234567890",
                        "assigned_user_id": "sales-b",
                    }
                ]
            }
        )
        result = admin_change_assignee(
            "ADMIN",
            "company-1",
            "business:1234567890",
            "SALES-B",
            reason="담당 변경",
            session_id="admin-session",
            db=database,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["assignment"]["assigned_user_id"], "sales-b")
        self.assertEqual(
            database.calls[0][1],
            {
                "p_current_user_id": "admin",
                "p_company_uid": "business:1234567890",
                "p_reason": "담당 변경",
                "p_session_id": "admin-session",
                "p_new_assigned_user_id": "sales-b",
            },
        )

    def test_admin_release_uses_admin_only_rpc(self):
        database = _FakeDatabase(
            {
                "oasis_admin_release_company_assignment": [
                    {
                        "success": True,
                        "code": "ADMIN_RELEASED",
                        "company_uid": "business:1234567890",
                        "status": "unassigned",
                    }
                ]
            }
        )
        result = admin_release_assignment(
            "admin",
            "ignored-company-id",
            "business:1234567890",
            reason="강제 회수",
            session_id="admin-session",
            db=database,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            database.calls[0],
            (
                "oasis_admin_release_company_assignment",
                {
                    "p_current_user_id": "admin",
                    "p_company_uid": "business:1234567890",
                    "p_reason": "강제 회수",
                    "p_session_id": "admin-session",
                },
            ),
        )

    def test_admin_list_and_state_actions_use_admin_rpc_contract(self):
        database = _FakeDatabase(
            {
                "oasis_list_admin_company_assignments": [
                    {
                        "company_uid": "business:1234567890",
                        "assigned_user_id": "sales-a",
                        "status": "consulting",
                        "private_contact_notes": "must-not-leak",
                    }
                ],
                "oasis_admin_reactivate_company_assignment": [
                    {
                        "success": True,
                        "code": "REACTIVATED",
                        "company_uid": "business:1234567890",
                        "status": "unassigned",
                    }
                ],
                "oasis_admin_permanent_exclude_company": [
                    {
                        "success": True,
                        "code": "PERMANENTLY_EXCLUDED",
                        "company_uid": "business:1234567890",
                        "status": "permanently_excluded",
                    }
                ],
            }
        )
        listed = list_admin_assignments(
            "admin",
            statuses=["consulting"],
            assigned_user_id="sales-a",
            limit=50,
            offset=10,
            db=database,
        )
        self.assertEqual(listed["assignments"][0]["assigned_user_id"], "sales-a")
        self.assertNotIn("private_contact_notes", listed["assignments"][0])

        reactivated = admin_reactivate(
            "admin",
            "ignored-company-id",
            "business:1234567890",
            reason="관리자 재활성화",
            db=database,
        )
        excluded = admin_permanent_exclude(
            "admin",
            "ignored-company-id",
            "business:1234567890",
            reason="폐업 확인",
            session_id="session-1",
            db=database,
        )
        self.assertTrue(reactivated["ok"])
        self.assertTrue(excluded["ok"])
        self.assertNotIn("p_company_id", database.calls[1][1])
        self.assertEqual(
            database.calls[2],
            (
                "oasis_admin_permanent_exclude_company",
                {
                    "p_current_user_id": "admin",
                    "p_company_uid": "business:1234567890",
                    "p_reason": "폐업 확인",
                    "p_session_id": "session-1",
                },
            ),
        )

    def test_admin_limit_validates_and_sends_exact_payload(self):
        database = _FakeDatabase(
            {"oasis_admin_set_sales_user_limit": True}
        )
        result = admin_set_user_limit(
            "admin",
            "sales-a",
            40,
            "분기 한도 조정",
            session_id="session-1",
            db=database,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["assignment"]["max_uncontacted"], 40)
        self.assertEqual(
            database.calls[0][1],
            {
                "p_admin_user_id": "admin",
                "p_target_user_id": "sales-a",
                "p_max_uncontacted": 40,
                "p_reason": "분기 한도 조정",
                "p_session_id": "session-1",
            },
        )

        invalid_database = _FakeDatabase()
        invalid = admin_set_user_limit(
            "admin", "sales-a", 0, "invalid", db=invalid_database
        )
        self.assertEqual(invalid["code"], "INVALID_INPUT")
        self.assertEqual(invalid_database.calls, [])

    def test_admin_audit_payload_and_field_allowlist(self):
        database = _FakeDatabase(
            {
                "oasis_list_company_assignment_audit": [
                    {
                        "id": 1,
                        "user_id": "sales-a",
                        "user_name": "홍길동",
                        "company_uid": "business:1234567890",
                        "action": "assigned",
                        "previous_value": {},
                        "new_value": {"status": "assigned"},
                        "session_fingerprint": "sha256:session-fingerprint",
                        "mobile_phone": "010-1234-5678",
                        "landline_phone": "02-1234-5678",
                        "phone": "010-9999-9999",
                        "created_at": "2026-08-02T00:00:00Z",
                        "secret_key": "must-not-leak",
                    }
                ]
            }
        )
        result = list_admin_assignment_audit(
            "admin",
            "business:1234567890",
            limit=5000,
            offset=-1,
            db=database,
        )
        self.assertEqual(len(result["audit"]), 1)
        audit = result["audit"][0]
        self.assertEqual(audit["user_name"], "홍길동")
        self.assertEqual(
            audit["session_fingerprint"],
            "sha256:session-fingerprint",
        )
        for private_field in (
            "mobile_phone",
            "landline_phone",
            "phone",
            "secret_key",
        ):
            self.assertNotIn(private_field, audit)
        self.assertNotIn("010-1234-5678", repr(audit))
        self.assertNotIn("02-1234-5678", repr(audit))
        self.assertEqual(database.calls[0][1]["p_limit"], 1000)
        self.assertEqual(database.calls[0][1]["p_offset"], 0)

    def test_admin_metrics_sql_fields_map_to_stable_ui_contract(self):
        database = _FakeDatabase(
            {
                "oasis_list_company_assignment_admin_metrics": [
                    {
                        "user_id": "sales-a",
                        "user_name": "홍길동",
                        "uncontacted_assignment_count": 4,
                        "contacted_assignment_count": 12,
                        "long_unprocessed_assignment_count": 2,
                        "duplicate_assignment_attempt_count": 3,
                        "global_assignment_count": 88,
                        "global_duplicate_assignment_attempt_count": 7,
                        "global_migration_conflict_count": 1,
                        "private_phone": "010-1234-5678",
                    }
                ]
            }
        )

        result = list_admin_assignment_metrics("ADMIN", db=database)

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["metrics"]), 1)
        metric = result["metrics"][0]
        self.assertEqual(metric["assigned_user_id"], "sales-a")
        self.assertEqual(metric["assigned_user_name"], "홍길동")
        self.assertEqual(metric["uncontacted_count"], 4)
        self.assertEqual(metric["contacted_count"], 12)
        self.assertEqual(metric["long_unprocessed_count"], 2)
        self.assertEqual(metric["duplicate_attempt_count"], 3)
        self.assertEqual(metric["total_assignment_count"], 88)
        self.assertEqual(
            metric["global_duplicate_assignment_attempt_count"],
            7,
        )
        self.assertEqual(metric["global_migration_conflict_count"], 1)
        self.assertNotIn("private_phone", metric)
        self.assertEqual(
            database.calls,
            [
                (
                    "oasis_list_company_assignment_admin_metrics",
                    {"p_current_user_id": "admin"},
                )
            ],
        )


class ProspectSaveNoticeTests(unittest.TestCase):
    def test_save_confirmation_survives_result_rerun_once(self):
        source = (
            Path(__file__).resolve().parents[1] / "prospect_db_center.py"
        ).read_text(encoding="utf-8")
        helper_start = source.index(
            "def _show_pending_prospect_save_notices()"
        )
        helper_end = source.index("\ndef ", helper_start + 5)
        helper_source = source[helper_start:helper_end]
        self.assertIn(
            "st.session_state.pop(_PROSPECT_SAVE_FLASH_KEY, [])",
            helper_source,
        )
        self.assertIn("renderer(message)", helper_source)

        queue_marker = (
            "st.session_state[_PROSPECT_SAVE_FLASH_KEY] = ("
        )
        queue_index = source.index(queue_marker)
        rerun_index = source.index("st.rerun()", queue_index)
        self.assertLess(queue_index, rerun_index)
        self.assertIn("저장 완료: ", source)

    def test_save_rerun_does_not_repeat_stale_assignment_warning(self):
        source = (
            Path(__file__).resolve().parents[1] / "prospect_db_center.py"
        ).read_text(encoding="utf-8")
        clear_index = source.index(
            'result.pop("assignment_warning", None)'
        )
        save_index = source.index(
            "st.session_state[result_state_key] = result",
            clear_index,
        )
        self.assertLess(clear_index, save_index)

    def test_obsolete_prospect_source_caption_is_removed(self):
        source = (
            Path(__file__).resolve().parents[1] / "prospect_db_center.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("행안부 자료는 사용하지 않습니다.", source)


if __name__ == "__main__":
    unittest.main()
