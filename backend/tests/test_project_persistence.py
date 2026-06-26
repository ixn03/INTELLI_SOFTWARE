"""Tests for durable project/graph persistence."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.db.session import init_db, reset_engine_cache  # noqa: E402
from app.services.normalization_service import normalize_l5x_project  # noqa: E402
from app.services.project_persistence_service import (  # noqa: E402
    load_normalized,
    load_project,
    persistence_enabled,
    save_project,
)
from app.services.project_store import InMemoryProjectStore  # noqa: E402
from tests.test_trace_v1_pipeline import _make_pipeline_project  # noqa: E402


class ProjectPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "persist_test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{self._db_path}"
        reset_engine_cache()
        init_db()
        self.store = InMemoryProjectStore()

    def tearDown(self) -> None:
        self.store.reset()
        reset_engine_cache()
        os.environ.pop("DATABASE_URL", None)
        self._tmpdir.cleanup()

    def test_persistence_enabled_for_file_sqlite(self) -> None:
        self.assertTrue(persistence_enabled())

    def test_save_and_load_project_round_trip(self) -> None:
        project = _make_pipeline_project()
        self.store.save(project)

        reloaded = InMemoryProjectStore()
        loaded = reloaded.get(project.file_hash)
        self.assertEqual(loaded.project_name, project.project_name)
        self.assertEqual(loaded.file_hash, project.file_hash)
        self.assertEqual(len(loaded.controllers), 1)

    def test_normalized_graph_persisted_and_cached(self) -> None:
        project = _make_pipeline_project()
        self.store.save(project)

        first = self.store.get_normalized(project.file_hash)
        second_store = InMemoryProjectStore()
        second = second_store.get_normalized(project.file_hash)

        self.assertEqual(
            len(first["control_objects"]),
            len(second["control_objects"]),
        )
        self.assertEqual(
            len(first["relationships"]),
            len(second["relationships"]),
        )

    def test_persistence_service_direct_round_trip(self) -> None:
        from app.db.session import get_session_factory

        project = _make_pipeline_project()
        normalized = normalize_l5x_project(project)
        db = get_session_factory()()
        try:
            save_project(db, project, normalized=normalized)
            db.commit()
            loaded_project = load_project(db, project.file_hash)
            loaded_normalized = load_normalized(db, project.file_hash)
        finally:
            db.close()

        self.assertIsNotNone(loaded_project)
        self.assertEqual(loaded_project.file_hash, project.file_hash)
        self.assertIsNotNone(loaded_normalized)
        self.assertGreater(len(loaded_normalized["control_objects"]), 0)


if __name__ == "__main__":
    unittest.main()
