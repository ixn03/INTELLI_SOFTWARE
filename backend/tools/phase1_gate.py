#!/usr/bin/env python3
"""Phase 1 acceptance gate — MVP spine + Rockwell depth.

Runs the minimum checks required to call Product Phase 1 complete on the
fixture corpus:

  1. Backend pytest suite
  2. Troubleshooting eval harness (direct + route modes)
  3. Parser grade on committed L5X fixtures

Usage:
  cd backend
  python tools/phase1_gate.py
  python tools/phase1_gate.py --skip-pytest   # faster local check
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_FIXTURES = _BACKEND / "tests" / "fixtures" / "l5x"


def _run(cmd: list[str], *, cwd: Path) -> int:
    print(f"\n>> {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    return int(result.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="INTELLI Phase 1 acceptance gate")
    parser.add_argument(
        "--skip-pytest",
        action="store_true",
        help="Skip full pytest (run eval + parser grade only)",
    )
    args = parser.parse_args()

    failures = 0

    if not args.skip_pytest:
        failures += _run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=_BACKEND,
        )

    failures += _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_troubleshoot_eval_harness.py",
            "tests/test_intelli_ladder_answer_keys.py",
            "tests/test_live_snapshot_service.py",
            "tests/test_troubleshoot_live_data_api.py",
            "-q",
        ],
        cwd=_BACKEND,
    )

    failures += _run(
        [sys.executable, "tools/troubleshoot_eval.py", "--mode", "direct"],
        cwd=_BACKEND,
    )
    failures += _run(
        [sys.executable, "tools/troubleshoot_eval.py", "--mode", "route"],
        cwd=_BACKEND,
    )

    if _FIXTURES.is_dir():
        failures += _run(
            [sys.executable, "tools/parser_grade.py", str(_FIXTURES)],
            cwd=_BACKEND,
        )
    else:
        print(f"WARNING: fixture folder missing: {_FIXTURES}")
        failures += 1

    if failures:
        print(f"\nPhase 1 gate FAILED ({failures} step(s) failed).")
        return 1

    print(
        "\nPhase 1 gate PASSED — fixture corpus, eval harness, and parser grade OK."
    )
    print(
        "Note: plant-scale L5X programs may still score lower; use traceability_score "
        "for production readiness beyond this gate."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
