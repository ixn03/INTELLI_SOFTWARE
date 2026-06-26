"""API tests for live data merge into troubleshooting workspace."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.db.models.tag_registry import SourceSystem
from app.db.seed import DEMO_TAG_P101_RUNCMD_ID, seed_demo_registry
from app.db.session import get_session_factory, init_db, reset_engine_cache
from app.models.control_model import ControlProject
from app.models.reasoning import ControlObject, ControlObjectType, Relationship, RelationshipType
from app.schemas.tag_sample import SampleQuality, TagSampleRead
from app.services.influx_reader import set_influx_reader
from app.services.project_store import project_store
from main import app


class _MemoryInfluxReader:
    def __init__(self, samples: dict[str, TagSampleRead]) -> None:
        self.samples = samples

    def get_latest_tag_value(self, tag_id: str) -> TagSampleRead | None:
        return self.samples.get(tag_id)

    def get_tag_history(self, tag_id: str, **kwargs):  # noqa: ANN003
        sample = self.get_latest_tag_value(tag_id)
        return [sample] if sample else []


class TroubleshootLiveDataApiTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_engine_cache()
        project_store.reset()
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
        init_db()
        db = get_session_factory()()
        try:
            seed_demo_registry(db)
            db.commit()
        finally:
            db.close()

        project_id = "live-merge-test"
        motor = ControlObject(
            id="tag::PLC/PRG/P101_RunCmd",
            name="P101_RunCmd",
            object_type=ControlObjectType.TAG,
            source_location="Controller:PLC/Program:PRG/Tag:P101_RunCmd",
        )
        permissive = ControlObject(
            id="tag::PLC/PRG/Permissive_A",
            name="Permissive_A",
            object_type=ControlObjectType.TAG,
            source_location="Controller:PLC/Program:PRG/Tag:Permissive_A",
        )
        rung = ControlObject(
            id="rung::PLC/PRG/Routine_A/1",
            name="Rung 1",
            object_type=ControlObjectType.RUNG,
            source_location="Controller:PLC/Program:PRG/Routine:Routine_A/Rung[1]",
        )
        relationships = [
            Relationship(
                source_id=rung.id,
                target_id=motor.id,
                relationship_type=RelationshipType.WRITES,
                source_location=rung.source_location,
            ),
            Relationship(
                source_id=permissive.id,
                target_id=rung.id,
                relationship_type=RelationshipType.READS,
                source_location=rung.source_location,
            ),
        ]
        project_store.save(
            ControlProject(
                project_name="Live merge test",
                file_hash=project_id,
            )
        )
        project_store.seed_normalized(
            project_id,
            {
                "control_objects": [motor, permissive, rung],
                "relationships": relationships,
                "execution_contexts": [],
            },
        )

        reader = _MemoryInfluxReader(
            {
                str(DEMO_TAG_P101_RUNCMD_ID): TagSampleRead(
                    tag_id=DEMO_TAG_P101_RUNCMD_ID,
                    raw_tag_name="P101_RunCmd",
                    value=False,
                    timestamp=datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc),
                    quality=SampleQuality.GOOD,
                    source=SourceSystem.OPC_UA,
                )
            }
        )
        set_influx_reader(reader)  # type: ignore[arg-type]
        self.client = TestClient(app)

    def tearDown(self) -> None:
        set_influx_reader(None)
        project_store.reset()
        reset_engine_cache()

    def test_troubleshoot_auto_fetches_live_target_value(self) -> None:
        response = self.client.post(
            "/api/troubleshoot/question",
            json={
                "project_id": "live-merge-test",
                "question": "Why is P101_RunCmd not energizing?",
                "use_live_data": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        live_meta = body["advanced_details"]["live_data"]
        self.assertTrue(live_meta["enabled"])
        self.assertGreaterEqual(live_meta["resolved"], 1)
        target = body["current_state_explanation"]["target_current_value"]
        self.assertIsNotNone(target)
        self.assertEqual(target["value"], False)
        self.assertEqual(target["canonical_name"], "P101_RunCmd")


if __name__ == "__main__":
    unittest.main()
