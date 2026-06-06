#!/usr/bin/env python3
"""Seed demo tag registry data into the configured DATABASE_URL."""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.db.seed import seed_demo_registry  # noqa: E402
from app.db.session import get_session_factory, init_db  # noqa: E402


def main() -> int:
    init_db()
    session = get_session_factory()()
    try:
        seed_demo_registry(session)
        session.commit()
        print("Demo tag registry seeded.")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
