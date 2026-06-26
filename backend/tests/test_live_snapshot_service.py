"""Tests for live snapshot resolution (tag registry + InfluxDB)."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.db.models.tag_registry import SourceSystem, Tag
from app.db.seed import DEMO_TAG_P101_RUNCMD_ID, seed_demo_registry
from app.db.session import get_session_factory, init_db, reset_engine_cache
from app.schemas.tag_sample import SampleQuality, TagSampleRead
from app.services.live_snapshot_service import RegistryIndex, build_live_runtime_snapshot
from app.services.troubleshooting_workspace_service import SignalRef


class _MockInfluxReader:
    def __init__(self, samples: dict[str, TagSampleRead]) -> None:
        self.samples = samples

    def get_latest_tag_value(self, tag_id: str) -> TagSampleRead | None:
        return self.samples.get(tag_id)


class LiveSnapshotServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_engine_cache()
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        init_db()
        db = get_session_factory()()
        try:
            seed_demo_registry(db)
            db.commit()
        finally:
            db.close()
        self.db = get_session_factory()()

    def tearDown(self) -> None:
        self.db.close()
        reset_engine_cache()

    def test_resolves_by_canonical_name_and_builds_snapshot(self) -> None:
        reader = _MockInfluxReader(
            {
                str(DEMO_TAG_P101_RUNCMD_ID): TagSampleRead(
                    tag_id=DEMO_TAG_P101_RUNCMD_ID,
                    raw_tag_name="P101_RunCmd",
                    value=True,
                    timestamp=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
                    quality=SampleQuality.GOOD,
                    source=SourceSystem.OPC_UA,
                )
            }
        )
        signals = [
            SignalRef(
                id="tag::PLC/MainProgram/P101_RunCmd",
                name="P101_RunCmd",
            )
        ]
        snapshot, meta = build_live_runtime_snapshot(
            self.db, signals, reader=reader  # type: ignore[arg-type]
        )
        self.assertEqual(meta["resolved"], 1)
        self.assertEqual(snapshot["P101_RunCmd"]["value"], True)
        self.assertEqual(
            snapshot["tag::PLC/MainProgram/P101_RunCmd"]["canonical_name"],
            "P101_RunCmd",
        )

    def test_registry_index_resolves_logic_ref_key(self) -> None:
        tag = self.db.get(Tag, DEMO_TAG_P101_RUNCMD_ID)
        self.assertIsNotNone(tag)
        index = RegistryIndex([tag])  # type: ignore[list-item]
        resolved = index.resolve(
            SignalRef(id="tag::PLC/PRG/Other", name="P101_RunCmd")
        )
        self.assertIsNotNone(resolved)
        self.assertEqual(resolved.canonical_name, "P101_RunCmd")

    def test_missing_influx_sample_counts_as_missing(self) -> None:
        reader = _MockInfluxReader({})
        signals = [SignalRef(id="tag::PLC/PRG/P101_RunCmd", name="P101_RunCmd")]
        _, meta = build_live_runtime_snapshot(
            self.db, signals, reader=reader  # type: ignore[arg-type]
        )
        self.assertEqual(meta["resolved"], 0)
        self.assertEqual(meta["missing_influx"], 1)


if __name__ == "__main__":
    unittest.main()
