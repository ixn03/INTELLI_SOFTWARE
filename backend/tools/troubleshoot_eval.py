#!/usr/bin/env python3
"""Troubleshooting workspace evaluation harness (CLI).

Runs the declarative case suite in ``tests/fixtures/troubleshoot_eval/``
against the signal troubleshooting workspace and prints aggregate accuracy
metrics. Output redacts tag/rung names to opaque signal keys.

Usage::

    cd backend
    PYTHONPATH=. python tools/troubleshoot_eval.py
    PYTHONPATH=. python tools/troubleshoot_eval.py --mode route
    PYTHONPATH=. python tools/troubleshoot_eval.py --suite path/to/suite.json --fail-under 0.9

Modes:

* ``direct`` (default) — calls ``build_signal_workspace`` (same logic as the API).
* ``route`` — exercises ``POST /api/troubleshoot/question`` handler via project_store.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.troubleshoot_eval_service import (  # noqa: E402
    load_suite,
    report_to_dict,
    run_suite,
)

_DEFAULT_SUITE = (
    _BACKEND_ROOT / "tests" / "fixtures" / "troubleshoot_eval" / "suite.json"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        type=Path,
        default=_DEFAULT_SUITE,
        help="Path to eval suite JSON (default: committed synthetic suite).",
    )
    parser.add_argument(
        "--mode",
        choices=("direct", "route"),
        default="direct",
        help="Evaluation path: service direct call or API route handler.",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=None,
        help="Exit non-zero if pass_rate is below this threshold (0-1).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON report.",
    )
    args = parser.parse_args(argv)

    suite = load_suite(args.suite)
    report = run_suite(suite, mode=args.mode)
    payload = report_to_dict(report)

    if args.pretty:
        print(json.dumps(payload, indent=2))
    else:
        print(json.dumps(payload))

    if args.fail_under is not None and report.pass_rate < args.fail_under:
        return 1
    if report.failed_cases > 0 and args.fail_under is None:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
