"""Verify contact-status SQL using only the guarded synthetic loopback cluster.

No environment credentials, Supabase connections, or production data are read.
Each execution creates and retains one uniquely named fixture database. Only
case names and pass/fail flags are printed; detailed SQL rows stay in memory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.verify_crm_consistency_postgres import LOCAL_RUNTIME, SAFE_ROLES, Verification, require

MIGRATION = ROOT / "supabase/migrations/20260909080959_contact_status_accuracy.sql"
LIST_RPC = "oasis_list_user_db_assignments_v2"
COUNT_RPC = "oasis_get_user_db_dashboard"
OWNER_A, OWNER_B, ADMIN = "fixture-owner-a", "fixture-owner-b", "fixture-admin"
TABLES = (
    "oasis_prospect_companies", "oasis_company_sales_assignments",
    "oasis_user_prospect_notes", "oasis_prospect_contacts", "oasis_company_sales_contact_logs",
)
FIXTURE_SCHEMA = """
CREATE TABLE public.oasis_prospect_companies (
 id uuid PRIMARY KEY, company_uid text NOT NULL, source text DEFAULT 'synthetic',
 source_key text DEFAULT '', business_no text DEFAULT '', company_name text DEFAULT 'synthetic-fixture',
 address text DEFAULT '', region text DEFAULT '', industry_code text DEFAULT '', industry_name text DEFAULT '',
 employee_count integer DEFAULT 0, new_employee_count integer DEFAULT 0, lost_employee_count integer DEFAULT 0,
 monthly_notice_amount bigint DEFAULT 0, data_created_ym text DEFAULT '', priority_score integer DEFAULT 0,
 priority_reasons jsonb DEFAULT '[]', source_data jsonb DEFAULT '{}', updated_at timestamptz NOT NULL
);
CREATE TABLE public.oasis_company_sales_assignments (
 id uuid PRIMARY KEY, company_id uuid, company_uid text UNIQUE NOT NULL,
 assigned_user_id text, status text NOT NULL DEFAULT 'assigned', assigned_at timestamptz,
 assignment_expires_at timestamptz, first_contacted_at timestamptz, last_contacted_at timestamptz,
 next_contact_at timestamptz, contact_count integer NOT NULL DEFAULT 0,
 current_assignment_contact_count integer NOT NULL DEFAULT 0, legacy_hold boolean NOT NULL DEFAULT false,
 permanently_excluded boolean NOT NULL DEFAULT false, released_at timestamptz,
 created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL
);
CREATE TABLE public.oasis_user_prospect_notes (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), company_uid text NOT NULL,
 user_id text NOT NULL, memo text NOT NULL, UNIQUE(company_uid,user_id)
);
CREATE TABLE public.oasis_prospect_contacts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), prospect_id uuid NOT NULL,
 contact_type text NOT NULL, contact_value text NOT NULL, verification_status text DEFAULT '',
 do_not_contact boolean DEFAULT false, opt_out_at timestamptz
);
CREATE TABLE public.oasis_company_sales_contact_logs (
 id uuid PRIMARY KEY, assignment_id uuid, company_id uuid, company_uid text NOT NULL,
 assigned_user_id text NOT NULL, created_by_user_id text NOT NULL,
 contact_method text NOT NULL DEFAULT 'phone', contact_result text NOT NULL,
 notes text NOT NULL DEFAULT 'synthetic-log-private', contacted_at timestamptz NOT NULL,
 next_contact_at timestamptz, created_at timestamptz NOT NULL,
 legacy_source_data jsonb NOT NULL DEFAULT '{}'
);
CREATE FUNCTION public.oasis_sales_actor_is_active(p_user_id text) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
 SELECT p_user_id IN ('fixture-owner-a','fixture-owner-b','fixture-admin');
$$;
CREATE FUNCTION public.oasis_sales_actor_is_admin(p_user_id text) RETURNS boolean
LANGUAGE sql STABLE SECURITY INVOKER SET search_path='' AS $$
 SELECT p_user_id='fixture-admin';
$$;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
"""


class ContactVerification(Verification):
    def __init__(self, host: str, port: int):
        super().__init__(host, port)
        self.database = "crm_contact_status_" + uuid.uuid4().hex[:16]
        self.migration_text = MIGRATION.read_text(encoding="utf-8")
        require(bool(self.migration_text.strip()), "contact_migration_empty")
        self.migration_sha256 = hashlib.sha256(self.migration_text.encode()).hexdigest()
        self.assignments: dict[str, dict[str, Any]] = {}
        self.clock = datetime.now(timezone.utc).replace(microsecond=0)

    def setup(self) -> None:
        # Reuse the original verifier's fixed loopback connection implementation,
        # then verify directory, listening interface, account and PG major before writes.
        connection = self.connect(database="postgres")
        try:
            identity = connection.run(
                "SELECT current_setting('data_directory'),current_setting('listen_addresses'),"
                "current_user,current_setting('server_version_num')"
            )[0]
            require(Path(identity[0]).resolve() == (LOCAL_RUNTIME / "data").resolve(), "unexpected_cluster_directory")
            require(identity[1:3] == ["127.0.0.1", "crm_test"], "unexpected_cluster_identity")
            require(int(identity[3]) // 10000 == 17, "postgres_major_version_mismatch")
            for role in sorted(SAFE_ROLES):
                values = connection.run("SELECT rolsuper,rolbypassrls,rolcanlogin FROM pg_roles WHERE rolname=:role", role=role)
                require(values == [[False, role == "service_role", False]], "unexpected_test_role_permissions")
            require(bool(re.fullmatch(r"crm_contact_status_[a-f0-9]{16}", self.database)), "invalid_fixture_database_name")
            connection.run('CREATE DATABASE "' + self.database + '"')
        finally:
            connection.close()
        connection = self.connect()
        try:
            connection.run(FIXTURE_SCHEMA)
            for table in TABLES:
                connection.run("ALTER TABLE public." + table + " ENABLE ROW LEVEL SECURITY")
                connection.run("REVOKE ALL ON public." + table + " FROM public,anon,authenticated")
                connection.run("GRANT SELECT ON public." + table + " TO service_role")
            self.seed_fixtures(connection)
            self.before = self.fingerprints(connection)
        finally:
            connection.close()

    def assignment(self, connection, label: str, *, owner=OWNER_A, count=0,
                   legacy=False, status="assigned", assigned_days=1, **overrides) -> dict:
        value = {
            "id": str(uuid.uuid4()), "company": str(uuid.uuid4()), "uid": "fixture-company:" + label,
            "owner": owner, "count": count, "legacy": legacy, "status": status,
            "assigned": self.clock - timedelta(days=assigned_days),
            "expires": self.clock + timedelta(days=100), "updated": self.clock,
            "next": None, "released": None, "excluded": False,
        }
        value.update(overrides)
        connection.run(
            "INSERT INTO public.oasis_prospect_companies(id,company_uid,updated_at,source_data) "
            "VALUES(CAST(:company AS uuid),:uid,:updated,'{\"synthetic_fixture\":true}'::jsonb)",
            company=value["company"], uid=value["uid"], updated=value["updated"],
        )
        connection.run(
            "INSERT INTO public.oasis_company_sales_assignments(id,company_id,company_uid,assigned_user_id,"
            "status,assigned_at,assignment_expires_at,current_assignment_contact_count,contact_count,legacy_hold,"
            "next_contact_at,released_at,permanently_excluded,created_at,updated_at) "
            "VALUES(CAST(:id AS uuid),CAST(:company AS uuid),:uid,:owner,:status,:assigned,:expires,:count,:count,"
            ":legacy,:next,:released,:excluded,:updated,:updated)", **value,
        )
        self.assignments[label] = value
        return value

    def contact(self, connection, assignment: dict, result: str, *, owner=None, creator=None,
                contacted=None, created=None, next_at=None, identity=None) -> None:
        connection.run(
            "INSERT INTO public.oasis_company_sales_contact_logs(id,assignment_id,company_id,company_uid,"
            "assigned_user_id,created_by_user_id,contact_result,contacted_at,created_at,next_contact_at,legacy_source_data) "
            "VALUES(CAST(:id AS uuid),CAST(:assignment AS uuid),CAST(:company AS uuid),:uid,:owner,:creator,"
            ":result,:contacted,:created,:next,'{\"unknown_history_key\":\"preserve\"}'::jsonb)",
            id=identity or str(uuid.uuid4()), assignment=assignment["id"], company=assignment["company"], uid=assignment["uid"],
            owner=owner or assignment["owner"], creator=creator or assignment["owner"], result=result,
            contacted=contacted or self.clock, created=created or self.clock, next=next_at,
        )

    def seed_fixtures(self, connection) -> None:
        self.assignment(connection, "fresh")
        reassigned = self.assignment(connection, "reassigned", count=0)
        self.contact(connection, reassigned, "contracted", created=self.clock - timedelta(days=5))
        previous_owner = self.assignment(connection, "previous-owner", count=1)
        self.contact(connection, previous_owner, "contracted", owner=OWNER_B)
        backdated = self.assignment(connection, "backdated", count=1, status="follow_up")
        self.contact(connection, backdated, "no_answer", contacted=self.clock - timedelta(days=90))
        legacy = self.assignment(connection, "legacy-zero", legacy=True, count=0)
        self.contact(connection, legacy, "contracted", contacted=self.clock - timedelta(days=60), created=self.clock - timedelta(days=60))
        private = self.assignment(connection, "creator-private", count=1)
        self.contact(connection, private, "not_interested", creator=ADMIN)
        legacy_private = self.assignment(connection, "legacy-creator-private", legacy=True, count=0)
        self.contact(connection, legacy_private, "not_interested", creator=ADMIN, created=self.clock - timedelta(days=60))
        admin_owned = self.assignment(connection, "admin-visible", owner=ADMIN, count=1)
        self.contact(connection, admin_owned, "connected", creator=OWNER_A)
        tie = self.assignment(connection, "id-tie", count=2)
        self.contact(connection, tie, "no_answer", identity=str(uuid.UUID(int=101)))
        self.contact(connection, tie, "contracted", identity=str(uuid.UUID(int=102)))
        created_tie = self.assignment(connection, "created-tie", count=2)
        self.contact(connection, created_tie, "no_answer", created=self.clock - timedelta(hours=1), identity=str(uuid.UUID(int=104)))
        self.contact(connection, created_tie, "connected", identity=str(uuid.UUID(int=103)))
        next_contact = self.assignment(connection, "follow-up", count=1)
        self.contact(connection, next_contact, "connected", next_at=self.clock + timedelta(days=1))
        quiet = self.assignment(connection, "quiet-after-noise", count=1, assigned_days=50)
        self.contact(connection, quiet, "contracted", contacted=self.clock - timedelta(days=30))
        noise = self.assignment(connection, "global-noise", owner=OWNER_B, count=1050)
        connection.run(
            "INSERT INTO public.oasis_company_sales_contact_logs(id,assignment_id,company_id,company_uid,"
            "assigned_user_id,created_by_user_id,contact_result,contacted_at,created_at) "
            "SELECT gen_random_uuid(),CAST(:assignment AS uuid),CAST(:company AS uuid),:uid,:owner,:owner,"
            "'no_answer',CAST(:stamp AS timestamptz) + n*interval '1 second',CAST(:stamp AS timestamptz) "
            "FROM generate_series(1,1050) AS n",
            assignment=noise["id"], company=noise["company"], uid=noise["uid"], owner=OWNER_B, stamp=self.clock,
        )
        self.assignment(connection, "released", released=self.clock)
        self.assignment(connection, "expired", expires=self.clock - timedelta(days=1))
        self.assignment(connection, "excluded", excluded=True)
        for owner, memo in ((OWNER_A, "synthetic-own-note"), (OWNER_B, "synthetic-other-note"), (ADMIN, "synthetic-admin-note")):
            connection.run("INSERT INTO public.oasis_user_prospect_notes(company_uid,user_id,memo) VALUES(:uid,:owner,:memo)", uid=private["uid"], owner=owner, memo=memo)
        # Construct known synthetic phone formats; no production identifiers are embedded.
        phone_rows = (
            ("fresh", "-".join(("02", "111", "2222")), False),
            ("follow-up", "-".join(("010", "1111", "2222")), False),
            ("backdated", "-".join(("010", "2222", "3333")), True),
        )
        for label, phone, blocked in phone_rows:
            connection.run("INSERT INTO public.oasis_prospect_contacts(prospect_id,contact_type,contact_value,do_not_contact) "
                           "VALUES(CAST(:id AS uuid),'phone',:value,:blocked)", id=self.assignments[label]["company"], value=phone, blocked=blocked)

    def fingerprints(self, connection) -> dict:
        return {table: connection.run("SELECT count(*),md5(coalesce(string_agg(to_jsonb(t)::text,'' ORDER BY id),'')) "
                                      "FROM public." + table + " t")[0] for table in TABLES}

    def migrate_twice(self) -> None:
        connection = self.connect()
        try:
            connection.run(self.migration_text)
            require(self.fingerprints(connection) == self.before, "first_migration_changed_seed_rows")
            connection.run(self.migration_text)
            require(self.fingerprints(connection) == self.before, "second_migration_changed_seed_rows")
        finally:
            connection.close()

    def list_rows(self, *, owner=OWNER_A, filter_name="all", limit=1000, offset=0, role="service_role") -> list[dict]:
        connection = self.connect(role=role)
        try:
            return [value[0] for value in connection.run(
                "SELECT to_jsonb(r) FROM public." + LIST_RPC + "(:owner,:filter,:limit,:offset) r",
                owner=owner, filter=filter_name, limit=limit, offset=offset,
            )]
        finally:
            connection.close()

    def summary(self, *, owner=OWNER_A, role="service_role") -> dict:
        connection = self.connect(role=role)
        try:
            return connection.run("SELECT to_jsonb(r) FROM public." + COUNT_RPC + "(:owner) r", owner=owner)[0][0]
        finally:
            connection.close()

    def find(self, label: str, *, owner=OWNER_A, filter_name="all") -> dict:
        matches = [value for value in self.list_rows(owner=owner, filter_name=filter_name) if value["assignment_id"] == self.assignments[label]["id"]]
        require(len(matches) == 1, "expected_assignment_not_uniquely_present")
        return matches[0]

    def owner_isolation(self) -> None:
        for owner in (OWNER_A, OWNER_B, ADMIN):
            expected = {value["id"] for value in self.assignments.values()
                        if value["owner"] == owner and value["released"] is None and not value["excluded"] and value["expires"] > self.clock}
            listed = self.list_rows(owner=owner)
            require({row["assignment_id"] for row in listed} == expected, "owner_assignment_scope_mismatch")
            require(all(row["contact_summary_loaded"] is True for row in listed), "summary_not_explicitly_loaded")

    def creator_privacy(self) -> None:
        private = self.find("creator-private")
        require(private["latest_contact_result"] is None and private["latest_contacted_at"] is None, "other_creator_summary_leaked")
        require(private["own_memo"] == "synthetic-own-note", "note_owner_scope_mismatch")
        require(not any(key in private for key in ("notes", "created_by_user_id", "assigned_user_id", "latest_notes")), "private_log_fields_returned")
        require(self.find("admin-visible", owner=ADMIN)["latest_contact_result"] == "connected", "admin_own_assignment_log_invisible")
        require(self.find("legacy-creator-private")["latest_contact_result"] is None, "legacy_flag_bypassed_creator_privacy")

    def reassignment(self) -> None:
        cleared = self.find("reassigned", filter_name="new")
        require(cleared["current_assignment_contact_count"] == 0 and cleared["latest_contact_result"] is None,
                "old_assignment_contact_classified_as_current")
        require(self.find("previous-owner")["latest_contact_result"] is None, "previous_owner_log_became_current")

    def backdated(self) -> None:
        value = self.find("backdated", filter_name="in_progress")
        require(value["latest_contact_result"] == "no_answer", "backdated_current_contact_ignored")
        require(datetime.fromisoformat(value["latest_contacted_at"]) < datetime.fromisoformat(value["assigned_at"]),
                "backdated_fixture_not_earlier_than_assignment")

    def legacy_hold(self) -> None:
        value = self.find("legacy-zero", filter_name="completed")
        require(value["legacy_hold"] is True and value["current_assignment_contact_count"] == 0, "legacy_fixture_not_zero_count")
        require(value["latest_contact_result"] == "contracted", "legacy_hold_contact_lost")

    def deterministic_ties(self) -> None:
        for _ in range(3):
            require(self.find("id-tie")["latest_contact_result"] == "contracted", "uuid_tie_order_unstable")
            require(self.find("created-tie")["latest_contact_result"] == "connected", "created_time_tie_order_wrong")

    def high_volume(self) -> None:
        quiet = self.find("quiet-after-noise")
        require(self.rows("SELECT count(*) FROM public.oasis_company_sales_contact_logs WHERE contacted_at>:stamp", stamp=self.clock) == [[1050]], "noise_fixture_too_small")
        require(quiet["latest_contact_result"] == "contracted", "company_latest_lost_behind_global_limit")
        first = self.list_rows(limit=2, offset=0)
        second = self.list_rows(limit=2, offset=2)
        all_rows = self.list_rows()
        require(first + second == all_rows[:4], "assignment_offset_pages_not_deterministic")
        require(all(row["total_count"] == len(all_rows) for row in first + second), "page_total_count_wrong")

    def classification_consistency(self) -> None:
        for owner in (OWNER_A, OWNER_B, ADMIN):
            counts = self.summary(owner=owner)
            names = {"all": "total_db_count", "landline": "landline_db_count", "mobile": "mobile_db_count",
                     "new": "new_db_count", "in_progress": "in_progress_db_count", "completed": "completed_db_count"}
            for filter_name, field in names.items():
                rows = self.list_rows(owner=owner, filter_name=filter_name)
                require(counts[field] == len(rows), "dashboard_list_classification_disagrees")
                require(all(row["total_count"] == len(rows) for row in rows), "filtered_total_count_disagrees")
            require(counts["new_db_count"] + counts["in_progress_db_count"] + counts["completed_db_count"] == counts["total_db_count"], "classification_not_partition")
        require(self.summary()["landline_db_count"] == 1 and self.summary()["mobile_db_count"] == 1, "synthetic_phone_or_opt_out_filter_wrong")

    def permissions(self, role: str) -> None:
        self.expect_sqlstate(lambda: self.list_rows(role=role), "42501")
        self.expect_sqlstate(lambda: self.summary(role=role), "42501")

    def inactive_and_invalid_filter(self) -> None:
        self.expect_sqlstate(lambda: self.list_rows(owner="fixture-inactive"), "42501")
        self.expect_sqlstate(lambda: self.summary(owner="fixture-inactive"), "42501")
        self.expect_sqlstate(lambda: self.list_rows(filter_name="unsupported"), "22023")

    def final_preservation(self) -> None:
        connection = self.connect()
        try:
            require(self.fingerprints(connection) == self.before, "read_verification_changed_seed_rows")
        finally:
            connection.close()

    def execute(self) -> bool:
        if not self.run_case("isolated_contact_fixture_setup", self.setup):
            return False
        if not self.run_case("contact_migration_twice_preserves_all_seeded_rows", self.migrate_twice):
            return False
        for name, callback in (
            ("owner_a_b_admin_assignment_scope", self.owner_isolation),
            ("creator_and_private_note_scope", self.creator_privacy),
            ("reassignment_old_contacts_and_zero_count", self.reassignment),
            ("backdated_contact_current_creation_is_visible", self.backdated),
            ("legacy_hold_zero_count_history_preserved", self.legacy_hold),
            ("created_timestamp_and_uuid_ties_are_deterministic", self.deterministic_ties),
            ("company_latest_survives_over_thousand_global_logs", self.high_volume),
            ("dashboard_and_list_classifications_match", self.classification_consistency),
            ("anon_contact_rpcs_denied", lambda: self.permissions("anon")),
            ("authenticated_contact_rpcs_denied", lambda: self.permissions("authenticated")),
            ("inactive_actor_and_invalid_filter_denied", self.inactive_and_invalid_filter),
            ("all_queries_preserve_fixture_identifiers_and_content", self.final_preservation),
        ):
            self.run_case(name, callback)
        return all(case["passed"] for case in self.results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, choices=(55439,), default=55439)
    args = parser.parse_args()
    verification = ContactVerification(args.host, args.port)
    passed = verification.execute()
    result_path = LOCAL_RUNTIME / "contact-status-verification-result.json"
    result_path.write_text(json.dumps({"database": verification.database,
        "migration_sha256": verification.migration_sha256, "passed": passed,
        "tests": verification.results}, indent=2), encoding="utf-8")
    print(("PASS" if passed else "FAIL") + " contact-status overall", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
