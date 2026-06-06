"""API tests for live tag sample ingestion and query endpoints."""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.db.models.tag_registry import SourceSystem
from app.db.seed import (
    DEMO_TAG_P101_RUNCMD_ID,
    DEMO_TAG_P101_RUNNINGFB_ID,
    DEMO_TAG_TANK101_LEVEL_ID,
    seed_demo_registry,
)
from app.db.session import get_session_factory, init_db, reset_engine_cache
from app.schemas.tag_sample import SampleQuality, TagSampleRead
from app.services.influx_writer import InfluxSampleRecord
from app.services.influx_writer import set_influx_writer
from app.services.influx_reader import set_influx_reader
from main import app


class _MemoryInfluxStore:
    """In-memory stand-in for InfluxDB writer + reader in unit tests."""

    def __init__(self) -> None:
        self.records: list[InfluxSampleRecord] = []

    def write_samples(self, records: list[InfluxSampleRecord]) -> None:
        self.records.extend(records)

    def get_latest_tag_value(self, tag_id: str) -> TagSampleRead | None:
        matching = [r for r in self.records if r.tag_id == tag_id]
        if not matching:
            return None
        rec = max(matching, key=lambda r: r.timestamp)
        return self._to_read(rec)

    def get_tag_history(
        self,
        tag_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[TagSampleRead]:
        matching = [r for r in self.records if r.tag_id == tag_id]
        if start is not None:
            matching = [r for r in matching if r.timestamp >= start]
        if end is not None:
            matching = [r for r in matching if r.timestamp <= end]
        matching.sort(key=lambda r: r.timestamp, reverse=True)
        return [self._to_read(r) for r in matching[:limit]]

    @staticmethod
    def _to_read(rec: InfluxSampleRecord) -> TagSampleRead:
        return TagSampleRead(
            tag_id=uuid.UUID(rec.tag_id),
            raw_tag_name=rec.raw_tag_name,
            value=rec.value,
            timestamp=rec.timestamp,
            quality=SampleQuality(rec.quality),
            source=SourceSystem(rec.source),
            plant_id=uuid.UUID(rec.plant_id),
            equipment_id=uuid.UUID(rec.equipment_id) if rec.equipment_id else None,
        )


class TagIngestionApiTests(unittest.TestCase):
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
        self.store = _MemoryInfluxStore()
        set_influx_writer(self.store)
        set_influx_reader(self.store)
        self.client = TestClient(app)
        self.ts = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        set_influx_writer(None)
        set_influx_reader(None)

    def _sample_payload(
        self,
        tag_id: uuid.UUID,
        *,
        raw_name: str = "P101_RunCmd",
        value: bool | float = True,
    ) -> dict:
        return {
            "tag_id": str(tag_id),
            "raw_tag_name": raw_name,
            "value": value,
            "timestamp": self.ts.isoformat(),
            "quality": "good",
            "source": "OPC_UA",
        }

    def test_valid_sample_ingestion(self) -> None:
        response = self.client.post(
            "/api/ingest/tag-samples",
            json=self._sample_payload(DEMO_TAG_P101_RUNCMD_ID),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["accepted_count"], 1)
        self.assertEqual(body["rejected_count"], 0)
        self.assertEqual(body["accepted_ids"], [str(DEMO_TAG_P101_RUNCMD_ID)])
        self.assertEqual(len(self.store.records), 1)
        self.assertEqual(self.store.records[0].raw_tag_name, "P101_RunCmd")

    def test_unknown_tag_rejection(self) -> None:
        unknown = uuid.UUID("99999999-9999-9999-9999-999999999999")
        response = self.client.post(
            "/api/ingest/tag-samples",
            json=self._sample_payload(unknown),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["accepted_count"], 0)
        self.assertEqual(body["rejected_count"], 1)
        self.assertIn("Unknown tag_id", body["rejections"][0]["reason"])

    def test_disabled_tag_rejection(self) -> None:
        patch = self.client.patch(
            f"/api/tags/{DEMO_TAG_P101_RUNCMD_ID}",
            json={"is_enabled": False},
        )
        self.assertEqual(patch.status_code, 200)
        response = self.client.post(
            "/api/ingest/tag-samples",
            json=self._sample_payload(DEMO_TAG_P101_RUNCMD_ID),
        )
        body = response.json()
        self.assertEqual(body["accepted_count"], 0)
        self.assertEqual(body["rejected_count"], 1)
        self.assertIn("disabled", body["rejections"][0]["reason"].lower())

    def test_partial_batch_success(self) -> None:
        unknown = uuid.UUID("99999999-9999-9999-9999-999999999999")
        response = self.client.post(
            "/api/ingest/tag-samples/batch",
            json={
                "samples": [
                    self._sample_payload(DEMO_TAG_P101_RUNCMD_ID),
                    self._sample_payload(
                        unknown,
                        raw_name="Missing",
                    ),
                    {
                        **self._sample_payload(DEMO_TAG_TANK101_LEVEL_ID),
                        "raw_tag_name": "Tank101_Level",
                        "value": 42.5,
                    },
                ]
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["accepted_count"], 2)
        self.assertEqual(body["rejected_count"], 1)
        self.assertEqual(len(body["accepted_ids"]), 2)

    def test_latest_value_query(self) -> None:
        ingest = self.client.post(
            "/api/ingest/tag-samples",
            json=self._sample_payload(DEMO_TAG_P101_RUNNINGFB_ID, raw_name="P101_RunningFB"),
        )
        self.assertEqual(ingest.status_code, 200)
        response = self.client.get(f"/api/tags/{DEMO_TAG_P101_RUNNINGFB_ID}/latest")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tag"]["canonical_name"], "P101_RunningFB")
        self.assertIsNotNone(body["sample"])
        self.assertEqual(body["sample"]["value"], True)
        self.assertEqual(body["sample"]["quality"], "good")
        self.assertEqual(body["sample"]["source"], "OPC_UA")

    def test_history_query(self) -> None:
        t0 = self.ts
        t1 = t0 + timedelta(seconds=5)
        for ts, val in ((t0, True), (t1, False)):
            response = self.client.post(
                "/api/ingest/tag-samples",
                json={
                    **self._sample_payload(DEMO_TAG_P101_RUNCMD_ID),
                    "timestamp": ts.isoformat(),
                    "value": val,
                },
            )
            self.assertEqual(response.status_code, 200)
        response = self.client.get(
            f"/api/tags/{DEMO_TAG_P101_RUNCMD_ID}/history",
            params={"limit": 10},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 2)
        values = {item["value"] for item in body["samples"]}
        self.assertEqual(values, {True, False})


if __name__ == "__main__":
    unittest.main()
