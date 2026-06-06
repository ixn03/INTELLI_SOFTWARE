"""API tests for the INTELLI tag registry."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi.testclient import TestClient

from app.db.seed import (
    DEMO_AREA_ID,
    DEMO_DATA_SOURCE_ID,
    DEMO_EQUIP_P101_ID,
    DEMO_PLANT_ID,
    seed_demo_registry,
)
from app.db.session import get_session_factory, init_db, reset_engine_cache
from main import app


class TagRegistryApiTests(unittest.TestCase):
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
        self.client = TestClient(app)

    def test_list_tags_returns_demo_seed(self) -> None:
        response = self.client.get("/api/tags")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 6)
        names = {item["canonical_name"] for item in payload["items"]}
        self.assertIn("P101_RunCmd", names)
        self.assertIn("Tank101_Level", names)

    def test_create_tag(self) -> None:
        response = self.client.post(
            "/api/tags",
            json={
                "plant_id": str(DEMO_PLANT_ID),
                "area_id": str(DEMO_AREA_ID),
                "equipment_id": str(DEMO_EQUIP_P101_ID),
                "data_source_id": str(DEMO_DATA_SOURCE_ID),
                "raw_name": "Program:MainProgram.P101_Permissive",
                "canonical_name": "P101_Permissive",
                "description": "Pump start permissive",
                "data_type": "BOOL",
                "tag_role": "PERMISSIVE",
                "source_system": "SIMULATOR",
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["canonical_name"], "P101_Permissive")
        self.assertEqual(body["raw_name"], "Program:MainProgram.P101_Permissive")
        self.assertEqual(body["tag_role"], "PERMISSIVE")

    def test_filter_tags_by_equipment(self) -> None:
        response = self.client.get(f"/api/equipment/{DEMO_EQUIP_P101_ID}/tags")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 3)
        for item in payload["items"]:
            self.assertEqual(item["equipment_id"], str(DEMO_EQUIP_P101_ID))

    def test_create_data_source(self) -> None:
        response = self.client.post(
            "/api/data-sources",
            json={
                "plant_id": str(DEMO_PLANT_ID),
                "name": "Plant PI Historian",
                "source_system": "PI",
                "endpoint_url": "https://pi-server.example/piwebapi",
                "auth_type": "BASIC",
                "is_enabled": True,
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["name"], "Plant PI Historian")
        self.assertEqual(body["source_system"], "PI")

        listing = self.client.get("/api/data-sources")
        self.assertEqual(listing.status_code, 200)
        self.assertGreaterEqual(listing.json()["total"], 2)

    def test_get_tag_by_id(self) -> None:
        listing = self.client.get("/api/tags?canonical_name=P101_RunCmd")
        # filter not exposed; grab first seeded tag from list
        tags = self.client.get("/api/tags").json()["items"]
        tag_id = tags[0]["id"]
        response = self.client.get(f"/api/tags/{tag_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], tag_id)

    def test_patch_tag_updates_role(self) -> None:
        tags = self.client.get("/api/tags").json()["items"]
        tag = next(t for t in tags if t["canonical_name"] == "P101_Fault")
        response = self.client.patch(
            f"/api/tags/{tag['id']}",
            json={"tag_role": "STATUS", "description": "Updated fault status"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tag_role"], "STATUS")
        self.assertEqual(response.json()["description"], "Updated fault status")


if __name__ == "__main__":
    unittest.main()
