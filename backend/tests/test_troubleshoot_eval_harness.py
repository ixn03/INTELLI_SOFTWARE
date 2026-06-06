"""Tests for the private-safe troubleshooting eval harness."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.project_store import project_store  # noqa: E402
from app.services.troubleshoot_eval_service import (  # noqa: E402
    load_suite,
    redact_question,
    report_to_dict,
    run_suite,
)

_SUITE = _BACKEND_ROOT / "tests" / "fixtures" / "troubleshoot_eval" / "suite.json"


class TroubleshootEvalHarnessTests(unittest.TestCase):
    def test_suite_loads(self) -> None:
        suite = load_suite(_SUITE)
        self.assertGreaterEqual(len(suite["cases"]), 8)
        self.assertIn("synthetic_motor", suite["fixtures"])

    def test_redact_question_replaces_signal_names(self) -> None:
        signals = {"SIG_TARGET": "Motor_Run", "SIG_PERM": "Permissive_A"}
        redacted = redact_question(
            "Why is Motor_Run blocked by Permissive_A?",
            signals,
        )
        self.assertIn("SIG_TARGET", redacted)
        self.assertIn("SIG_PERM", redacted)
        self.assertNotIn("Motor_Run", redacted)
        self.assertNotIn("Permissive_A", redacted)

    def test_direct_mode_suite_passes(self) -> None:
        suite = load_suite(_SUITE)
        report = run_suite(suite, mode="direct")
        self.assertEqual(report.total_cases, len(suite["cases"]))
        self.assertEqual(report.failed_cases, 0, report.failures)
        self.assertGreaterEqual(report.pass_rate, 1.0)

    def test_route_mode_matches_direct_pass_rate(self) -> None:
        project_store.reset()
        try:
            suite = load_suite(_SUITE)
            direct = run_suite(suite, mode="direct")
            route = run_suite(suite, mode="route")
            self.assertEqual(direct.pass_rate, route.pass_rate)
            self.assertEqual(direct.failed_cases, route.failed_cases)
        finally:
            project_store.reset()

    def test_report_contains_no_raw_tag_names(self) -> None:
        suite = load_suite(_SUITE)
        report = run_suite(suite, mode="direct")
        blob = json.dumps(report_to_dict(report))
        forbidden = set()
        for fixture in suite["fixtures"].values():
            forbidden.update(fixture.get("signals", {}).values())
        for name in forbidden:
            if name and len(name) >= 3:
                self.assertNotIn(name, blob, f"leaked tag name {name!r} in report")

    def test_case_summaries_use_redacted_questions(self) -> None:
        suite = load_suite(_SUITE)
        report = run_suite(suite, mode="direct")
        payload = report_to_dict(report)
        for summary in payload["case_summaries"]:
            question = summary["redacted_question"]
            self.assertNotIn("Motor_Run", question)
            self.assertNotIn("Pump_Transfer.Run_Cmd", question)


if __name__ == "__main__":
    unittest.main()
