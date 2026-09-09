"""Exercise CRM CAS SQL against a synthetic, loopback-only PostgreSQL cluster.

Never reads environment credentials or connects to Supabase. Each run creates a
new synthetic database and retains it for inspection. Requires pg8000; the task's
isolated installation under tmp/crm-pg-test/python-client is supported directly.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import sys
from threading import Barrier
from typing import Any
import uuid

ROOT = Path(__file__).resolve().parents[1]
LOCAL_RUNTIME = ROOT / "tmp" / "crm-pg-test"
sys.path.insert(0, str(LOCAL_RUNTIME / "python-client"))

from pg8000.native import Connection

MIGRATION = ROOT / "supabase/migrations/20260909073550_crm_consistency.sql"
SAFE_ROLES = {"anon", "authenticated", "service_role"}
METADATA_KEYS = (
    "_local_revision", "_cloud_base", "_cloud_version", "_sync_state",
    "_cloud_latest", "_cloud_latest_version", "_sync_error", "_sync_request_id",
    "timeline", "timelines", "created_at", "updated_at",
)
FIXTURE_SCHEMA = """
CREATE TABLE public.oasis_crm (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id text NOT NULL,
    business_no text NOT NULL,
    crm_data jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    customer_id uuid,
    UNIQUE (owner_user_id, business_no)
);
CREATE FUNCTION public.set_oasis_updated_at() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $$
BEGIN NEW.updated_at=now(); RETURN NEW; END;
$$;
CREATE TRIGGER trg_oasis_crm_updated_at BEFORE UPDATE ON public.oasis_crm
FOR EACH ROW EXECUTE FUNCTION public.set_oasis_updated_at();
ALTER TABLE public.oasis_crm ENABLE ROW LEVEL SECURITY;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.oasis_crm
TO anon, authenticated, service_role;
"""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise AssertionError(reason)


def sqlstate(exc: Exception) -> str:
    for item in exc.args:
        if isinstance(item, dict):
            return str(item.get("C", ""))
    return ""


def event_counter(events: list[Any]) -> Counter:
    return Counter(json.dumps(event, sort_keys=True) for event in events)


def legacy_events() -> list[dict[str, Any]]:
    result = []
    for group in range(40):
        base = {"at": "2026-01-01T00:00:00Z", "detail": "synthetic-history", "group": group}
        result.extend([dict(base), dict(base), dict(base, extra="preserve")])
    return result


class Verification:
    def __init__(self, host: str, port: int):
        require(host in {"localhost", "127.0.0.1"}, "loopback_host_required")
        require(port == 55439, "isolated_cluster_port_required")
        self.host = "127.0.0.1"  # Do not trust DNS/hosts-file resolution.
        self.port = port
        self.database = "crm_consistency_" + uuid.uuid4().hex[:16]
        self.results: list[dict[str, Any]] = []
        self.seed_id = str(uuid.uuid4())
        self.seed_customer_id = str(uuid.uuid4())
        self.legacy = legacy_events()
        self.migration_text = MIGRATION.read_text(encoding="utf-8")
        self.migration_sha256 = hashlib.sha256(self.migration_text.encode()).hexdigest()

    def connect(self, *, database: str | None = None, role: str | None = None) -> Connection:
        connection = Connection(
            user="crm_test", host=self.host, port=self.port,
            database=database or self.database, timeout=20,
        )
        connection.run("SET statement_timeout='15s'")
        if role:
            require(role in SAFE_ROLES, "unexpected_test_role")
            connection.run("SET ROLE " + role)
        return connection

    def run_case(self, name: str, callback) -> bool:
        try:
            callback()
        except Exception as exc:
            # Do not print SQL results, raw exceptions, payloads, or identifiers.
            self.results.append({"test": name, "passed": False,
                                 "error_type": type(exc).__name__, "sqlstate": sqlstate(exc)})
            print("FAIL " + name, flush=True)
            return False
        self.results.append({"test": name, "passed": True})
        print("PASS " + name, flush=True)
        return True

    def setup(self) -> None:
        connection = self.connect(database="postgres")
        try:
            row = connection.run(
                "SELECT current_setting('data_directory'), current_setting('listen_addresses'), "
                "current_user, current_setting('server_version_num')"
            )[0]
            require(Path(row[0]).resolve() == (LOCAL_RUNTIME / "data").resolve(),
                    "unexpected_cluster_directory")
            require(row[1] == "127.0.0.1" and row[2] == "crm_test", "unexpected_cluster_identity")
            require(int(row[3]) // 10000 == 17, "postgres_major_version_mismatch")
            for role in sorted(SAFE_ROLES):
                existing = connection.run(
                    "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname=:role",
                    role=role,
                )
                if not existing:
                    connection.run("CREATE ROLE " + role + " NOLOGIN NOSUPERUSER " +
                                   ("BYPASSRLS" if role == "service_role" else "NOBYPASSRLS"))
                else:
                    require(existing[0] == [False, role == "service_role", False],
                            "unexpected_existing_role_permissions")
            require(bool(re.fullmatch(r"crm_consistency_[a-f0-9]{16}", self.database)),
                    "invalid_fixture_database_name")
            connection.run('CREATE DATABASE "' + self.database + '"')
        finally:
            connection.close()
        connection = self.connect()
        try:
            connection.run(FIXTURE_SCHEMA)
            connection.run(
                "INSERT INTO public.oasis_crm(id,owner_user_id,business_no,crm_data,customer_id) "
                "VALUES(CAST(:id AS uuid),'fixture-owner-a','fixture-legacy',CAST(:data AS jsonb),"
                "CAST(:customer AS uuid))",
                id=self.seed_id, customer=self.seed_customer_id,
                data=json.dumps({"memo": "synthetic-seed", "unknown": {"keep": True},
                                 "timeline": self.legacy}),
            )
            for business, data in (("fixture-invalid-object", []),
                                   ("fixture-invalid-timeline", {"timeline": {"keep": True}})):
                connection.run(
                    "INSERT INTO public.oasis_crm(owner_user_id,business_no,crm_data) "
                    "VALUES('fixture-owner-a',:business,CAST(:data AS jsonb))",
                    business=business, data=json.dumps(data),
                )
            self.seed_before = connection.run(
                "SELECT id,owner_user_id,business_no,crm_data,updated_at,customer_id "
                "FROM public.oasis_crm ORDER BY business_no"
            )
        finally:
            connection.close()

    def migrate_twice(self) -> None:
        connection = self.connect()
        try:
            connection.run(self.migration_text)
            connection.run(self.migration_text)
            after = connection.run(
                "SELECT id,owner_user_id,business_no,crm_data,updated_at,customer_id "
                "FROM public.oasis_crm ORDER BY business_no"
            )
            require(after == self.seed_before, "migration_changed_existing_data")
            require(connection.run("SELECT DISTINCT crm_version FROM public.oasis_crm") == [[1]],
                    "initial_version_incorrect")
        finally:
            connection.close()

    def save(self, business: str, expected: int, patch: Any, events: Any = None,
             *, owner: str = "fixture-owner-a", request_id: str | None = None,
             role: str = "service_role", connection: Connection | None = None) -> dict[str, Any]:
        owned = connection is None
        connection = connection or self.connect(role=role)
        try:
            return connection.run(
                "SELECT public.oasis_save_crm_versioned(:owner,:business,CAST(:request AS uuid),"
                ":expected,CAST(:patch AS jsonb),CAST(:events AS jsonb))",
                owner=owner, business=business, request=request_id or str(uuid.uuid4()),
                expected=expected, patch=json.dumps(patch),
                events=json.dumps([] if events is None else events),
            )[0][0]
        finally:
            if owned:
                connection.close()

    def rows(self, query: str, **params) -> list:
        connection = self.connect()
        try:
            return connection.run(query, **params)
        finally:
            connection.close()

    def race(self, business: str, expected: int) -> list[dict[str, Any]]:
        barrier = Barrier(2, timeout=10)

        def worker(index: int):
            connection = self.connect(role="service_role")
            try:
                connection.run("BEGIN")
                connection.run("SET LOCAL lock_timeout='10s'")
                barrier.wait()
                result = self.save(business, expected, {"memo": "race-" + str(index)},
                                   connection=connection)
                connection.run("COMMIT")
                return result
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker, index) for index in range(2)]
            return [future.result(timeout=20) for future in futures]

    def concurrent_update(self) -> None:
        self.save("fixture-race-update", 0, {"memo": "original"})
        results = self.race("fixture-race-update", 1)
        require(sorted(item["status"] for item in results) == ["applied", "conflict"],
                "concurrent_update_not_exclusive")
        require(self.rows("SELECT count(*),max(crm_version) FROM public.oasis_crm "
                          "WHERE business_no='fixture-race-update'") == [[1, 2]],
                "concurrent_update_created_duplicate")

    def concurrent_creation(self) -> None:
        results = self.race("fixture-race-create", 0)
        require(sorted(item["status"] for item in results) == ["applied", "conflict"],
                "concurrent_create_not_exclusive")
        require(self.rows("SELECT count(*),max(crm_version) FROM public.oasis_crm "
                          "WHERE business_no='fixture-race-create'") == [[1, 1]],
                "concurrent_create_created_duplicate")

    def replay(self) -> None:
        request_id = str(uuid.uuid4())
        event = {"detail": "synthetic-replay", "at": "2026-01-02T00:00:00Z"}
        first = self.save("fixture-replay", 0, {"memo": "replay"}, [event], request_id=request_id)
        second = self.save("fixture-replay", 0, {"memo": "replay"}, [event], request_id=request_id)
        require(first["status"] == second["status"] == "applied", "replay_status_wrong")
        require(second["replayed"] is True and second["version"] == first["version"],
                "replay_wrote_again")
        require(second["crm_data"]["timeline"] == [event], "replay_duplicated_event")
        require(self.rows("SELECT count(*) FROM public.oasis_crm_save_requests "
                          "WHERE id=CAST(:id AS uuid)", id=request_id) == [[1]],
                "replay_duplicated_ledger")

    def append_stale(self) -> None:
        first_event = {"detail": "first", "at": "2026-01-02T00:00:00Z"}
        second_event = {"detail": "second", "at": "2026-01-03T00:00:00Z"}
        self.save("fixture-append", 0, {"memo": "keep"}, [first_event])
        self.save("fixture-append", 1, {"memo": "newer"})
        result = self.save("fixture-append", 1, {}, [second_event])
        require(result["status"] == "applied" and result["version"] == 3,
                "stale_append_rejected")
        require(result["crm_data"]["memo"] == "newer", "stale_append_overwrote_fields")
        require(event_counter(result["crm_data"]["timeline"]) ==
                event_counter([first_event, second_event]), "stale_append_lost_events")

    def preserve_legacy(self) -> None:
        added = {"detail": "new-event", "at": "2026-01-04T00:00:00Z"}
        result = self.save("fixture-legacy", 1, {"status": "updated"}, [added])
        require(result["status"] == "applied", "legacy_save_failed")
        require(event_counter(result["crm_data"]["timeline"]) == event_counter(self.legacy + [added]),
                "legacy_event_multiplicity_or_fields_lost")
        require(len(result["crm_data"]["timeline"]) == 121, "legacy_history_truncated")
        require(result["crm_data"]["unknown"] == {"keep": True}, "unknown_data_lost")
        row = self.rows("SELECT id,customer_id FROM public.oasis_crm WHERE business_no='fixture-legacy'")[0]
        require(str(row[0]) == self.seed_id and str(row[1]) == self.seed_customer_id,
                "existing_customer_identity_changed")

    def repeated_delta_event(self) -> None:
        event = {"detail": "repeated-real-attempt", "at": "2026-01-01T00:00:00Z"}
        self.save("fixture-equal-event", 0, {}, [event])
        result = self.save("fixture-equal-event", 1, {}, [event])
        require(result["status"] == "applied" and result["crm_data"]["timeline"] == [event, event],
                "identical_distinct_append_attempt_lost")

    def owner_isolation(self) -> None:
        first = self.save("fixture-shared", 0, {"memo": "a"}, owner="fixture-owner-a")
        second = self.save("fixture-shared", 0, {"memo": "b"}, owner="fixture-owner-b")
        require(first["status"] == second["status"] == "applied", "owner_keys_collided")
        require(first["crm_data"]["memo"] == "a" and second["crm_data"]["memo"] == "b",
                "owner_payload_mixed")
        require(self.rows("SELECT count(DISTINCT owner_user_id) FROM public.oasis_crm "
                          "WHERE business_no='fixture-shared'") == [[2]], "owner_rows_not_separate")

    def expect_sqlstate(self, callback, expected: str) -> None:
        try:
            callback()
        except Exception as exc:
            require(sqlstate(exc) == expected, "unexpected_rejection_sqlstate")
        else:
            raise AssertionError("expected_sql_rejection_missing")

    def unprivileged_access(self, role: str) -> None:
        self.expect_sqlstate(lambda: self.save("fixture-private", 0, {}, role=role), "42501")
        connection = self.connect(role=role)
        try:
            require(connection.run("SELECT * FROM public.oasis_crm") == [], "rls_leaked_rows")
            self.expect_sqlstate(lambda: connection.run(
                "INSERT INTO public.oasis_crm(owner_user_id,business_no) VALUES('attacker','fixture')"
            ), "40001")  # The write guard can reject before RLS policy evaluation.
            self.expect_sqlstate(lambda: connection.run("SELECT * FROM public.oasis_crm_save_requests"),
                                 "42501")
        finally:
            connection.close()

    def direct_service_write(self) -> None:
        connection = self.connect(role="service_role")
        try:
            self.expect_sqlstate(lambda: connection.run(
                "INSERT INTO public.oasis_crm(owner_user_id,business_no,crm_data) "
                "VALUES('fixture-owner-a','fixture-legacy','{}'::jsonb) "
                "ON CONFLICT(owner_user_id,business_no) DO UPDATE SET crm_data=excluded.crm_data"
            ), "40001")
            self.expect_sqlstate(lambda: connection.run(
                "UPDATE public.oasis_crm SET crm_data='{}'::jsonb WHERE business_no='fixture-legacy'"
            ), "40001")
            require(self.rows("SELECT count(*) FROM public.oasis_crm WHERE business_no='fixture-legacy'")
                    == [[1]], "guard_damaged_rows")
        finally:
            connection.close()

    def metadata_rejection(self) -> None:
        before = self.rows("SELECT count(*) FROM public.oasis_crm_save_requests")[0][0]
        for key in METADATA_KEYS:
            self.expect_sqlstate(lambda key=key: self.save("fixture-metadata", 0, {key: "unsafe"}), "22023")
        self.expect_sqlstate(lambda: self.save("fixture-metadata", 0, {}, [None]), "22023")
        require(self.rows("SELECT count(*) FROM public.oasis_crm_save_requests")[0][0] == before,
                "invalid_payload_written_to_ledger")

    def request_id_reuse(self) -> None:
        request_id = str(uuid.uuid4())
        self.save("fixture-request-id", 0, {"memo": "first"}, request_id=request_id)
        changes = (
            {"patch": {"memo": "changed"}}, {"events": [{"detail": "changed"}]},
            {"expected": 1}, {"owner": "fixture-owner-b"}, {"business": "fixture-other"},
        )
        for change in changes:
            args = dict(business="fixture-request-id", expected=0, patch={"memo": "first"},
                        events=[], owner="fixture-owner-a", request_id=request_id)
            args.update(change)
            self.expect_sqlstate(lambda args=args: self.save(**args), "22023")
        require(self.rows("SELECT count(*) FROM public.oasis_crm_save_requests WHERE id=CAST(:id AS uuid)",
                          id=request_id) == [[1]], "reused_request_changed_ledger")

    def malformed_legacy(self) -> None:
        for business in ("fixture-invalid-object", "fixture-invalid-timeline"):
            before = self.rows("SELECT crm_data,crm_version FROM public.oasis_crm WHERE business_no=:biz", biz=business)
            result = self.save(business, 1, {}, [{"detail": "do-not-overwrite"}])
            require(result["status"] == "conflict", "malformed_legacy_not_protected")
            require(self.rows("SELECT crm_data,crm_version FROM public.oasis_crm WHERE business_no=:biz", biz=business)
                    == before, "malformed_legacy_changed")

    def execute(self) -> bool:
        if not self.run_case("isolated_fixture_setup", self.setup):
            return False
        if not self.run_case("migration_twice_preserves_seeded_rows", self.migrate_twice):
            return False
        cases = (
            ("same_version_concurrent_update_one_winner", self.concurrent_update),
            ("concurrent_creation_one_row", self.concurrent_creation),
            ("request_replay_is_idempotent", self.replay),
            ("stale_append_preserves_newer_fields_and_events", self.append_stale),
            ("legacy_120_events_and_customer_ids_preserved", self.preserve_legacy),
            ("equal_event_in_new_request_preserved", self.repeated_delta_event),
            ("owner_a_b_records_are_separate", self.owner_isolation),
            ("anon_rpc_and_table_access_denied", lambda: self.unprivileged_access("anon")),
            ("authenticated_rpc_and_table_access_denied", lambda: self.unprivileged_access("authenticated")),
            ("direct_service_role_upsert_blocked", self.direct_service_write),
            ("metadata_and_nonobject_events_rejected", self.metadata_rejection),
            ("request_id_reuse_with_changed_content_rejected", self.request_id_reuse),
            ("malformed_legacy_json_preserved_as_conflict", self.malformed_legacy),
        )
        for name, callback in cases:
            self.run_case(name, callback)
        return all(item["passed"] for item in self.results)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("127.0.0.1", "localhost"), default="127.0.0.1")
    parser.add_argument("--port", type=int, choices=(55439,), default=55439)
    args = parser.parse_args()
    verification = Verification(args.host, args.port)
    passed = verification.execute()
    result_path = LOCAL_RUNTIME / "verification-result.json"
    result_path.write_text(json.dumps({
        "database": verification.database,
        "migration_sha256": verification.migration_sha256,
        "passed": passed, "tests": verification.results,
    }, indent=2), encoding="utf-8")
    print(("PASS" if passed else "FAIL") + " overall", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
