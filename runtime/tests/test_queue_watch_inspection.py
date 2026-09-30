from __future__ import annotations

import unittest

from ocpf_post.portfolio import PortfolioError
from ocpf_post.portfolio_cli import _watch_inspection, build_parser


class QueueWatchInspectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = {
            "status": "attention",
            "observed_at": "2026-09-11T17:00:00Z",
            "last_evaluated_at": "2026-09-11T17:00:00Z",
            "selection": "fair",
            "capacity": {"linkedin": {"daily_target": 6, "daily_budget_remaining": 0}},
            "account_capacity": {},
            "survival": {
                "waiting": 2, "rescue_window": 1, "expired_unpublished": 0,
                "expired_after_rescue_window": 0,
            },
            "issues": [
                {
                    "code": "waiting_too_long", "campaign": "A", "provider": "linkedin",
                    "account_id": "111", "project": "alpha", "state": "waiting",
                    "reason": "eligible_unreserved", "age_hours": 30.0,
                    "waiting_evaluations": 8, "unselected_evaluations": 7,
                },
                {
                    "code": "waiting_too_long", "campaign": "B", "provider": "linkedin",
                    "account_id": "111", "project": "alpha", "state": "waiting",
                    "reason": "eligible_unreserved", "age_hours": 55.0,
                    "waiting_evaluations": 13, "unselected_evaluations": 12,
                },
                {
                    "code": "schedule_requires_review", "campaign": "C", "provider": "x",
                    "account_id": "222", "project": "beta", "state": "schedule_requires_review",
                    "reason": "failed", "age_hours": 2.0,
                    "waiting_evaluations": 0, "unselected_evaluations": 0,
                },
            ],
        }

    def test_filters_oldest_first_groups_and_bounds_output(self) -> None:
        result = _watch_inspection(
            self.report, issue="waiting_too_long", provider="linkedin", limit=1,
        )
        self.assertTrue(result["read_only"])
        self.assertEqual(result["queue_status"], "attention")
        self.assertEqual(result["matching_issue_rows"], 2)
        self.assertEqual(result["returned_issue_rows"], 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["issues"][0]["campaign"], "B")
        self.assertEqual(result["issue_counts"], {"waiting_too_long": 2})
        self.assertEqual(result["groups"], [{
            "provider": "linkedin", "account_id": "111", "project": "alpha",
            "issue_rows": 2, "oldest_age_hours": 55.0,
            "max_waiting_evaluations": 13, "max_unselected_evaluations": 12,
        }])
        self.assertNotIn("records", result)
        self.assertEqual(result["capacity"], self.report["capacity"])
        self.assertEqual(result["survival"], self.report["survival"])
        self.assertIn("No observation, reservation, provider call, retry", result["boundary"])

    def test_account_and_project_filters_are_exact(self) -> None:
        result = _watch_inspection(self.report, project="beta", account_id="222", limit=25)
        self.assertEqual(result["matching_issue_rows"], 1)
        self.assertEqual(result["issues"][0]["code"], "schedule_requires_review")
        self.assertEqual(result["issues"][0]["campaign"], "C")

    def test_limit_is_bounded(self) -> None:
        for value in (0, 501):
            with self.subTest(value=value), self.assertRaises(PortfolioError):
                _watch_inspection(self.report, limit=value)

    def test_parser_exposes_read_only_filters(self) -> None:
        args = build_parser().parse_args([
            "watch", "--issue", "waiting_too_long", "--provider", "linkedin",
            "--project", "alpha", "--account-id", "111", "--limit", "10", "--json",
        ])
        self.assertEqual(args.issue, "waiting_too_long")
        self.assertEqual(args.provider, "linkedin")
        self.assertEqual(args.project, "alpha")
        self.assertEqual(args.account_id, "111")
        self.assertEqual(args.limit, 10)
        self.assertTrue(args.json)


if __name__ == "__main__":
    unittest.main()
