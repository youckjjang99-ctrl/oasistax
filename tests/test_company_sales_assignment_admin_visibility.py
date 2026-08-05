from __future__ import annotations

import unittest

from company_sales_assignment import filter_company_availability


class _FakeDatabase:
    def __init__(self, response):
        self.response = response

    def rpc(self, name: str, parameters: dict):
        if name != "oasis_filter_blocked_company_uids":
            return None
        return self.response(parameters)


class AdminAssignmentVisibilityTests(unittest.TestCase):
    def test_admin_search_excludes_owned_and_review_hold_rows(self):
        review_uid = "source:" + "a" * 64
        owned_uid = "source:" + "b" * 64
        available_uid = "source:" + "c" * 64

        def response(parameters):
            relations = {
                review_uid: "blocked",
                owned_uid: "own",
                available_uid: "available",
            }
            return [
                {"company_uid": uid, "relation": relations[uid]}
                for uid in parameters["p_company_uids"]
            ]

        database = _FakeDatabase(response)
        result = filter_company_availability(
            [
                {"id": "review-hold", "company_uid": review_uid},
                {"id": "already-owned", "company_uid": owned_uid},
                {"id": "available", "company_uid": available_uid},
            ],
            "admin-a",
            is_admin_user=True,
            db=database,
        )

        self.assertEqual(
            [row["id"] for row in result["items"]],
            ["available"],
        )
        self.assertEqual(
            [row["id"] for row in result["blocked_items"]],
            ["review-hold"],
        )
        self.assertEqual(
            [row["id"] for row in result["own_items"]],
            ["already-owned"],
        )
        self.assertEqual(result["excluded_count"], 1)
        self.assertEqual(result["own_count"], 1)


if __name__ == "__main__":
    unittest.main()
