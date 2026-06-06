"""Fixture-backed unified evidence tests for structured text programs."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.rockwell_l5x import RockwellL5XConnector  # noqa: E402
from app.services.normalization_service import normalize_l5x_project  # noqa: E402
from app.services.unified_evidence_service import get_signal_evidence  # noqa: E402

_FIXTURES = _BACKEND_ROOT / "tests" / "fixtures" / "l5x"


class UnifiedEvidenceSTTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        path = _FIXTURES / "INTELLI_StructuredText_Test.L5X"
        project = RockwellL5XConnector().parse(path.name, path.read_bytes())
        cls.normalized = normalize_l5x_project(project)
        cls.objects = cls.normalized["control_objects"]
        cls.relationships = cls.normalized["relationships"]
        cls.tags = {o.name: o.id for o in cls.objects if o.name}

    def test_motor_run_has_st_writer(self) -> None:
        evidence = get_signal_evidence(
            self.tags["Motor_Run"],
            self.objects,
            self.relationships,
        )
        writer_types = {w.writer_type for w in evidence.who_writes_this_signal}
        self.assertIn("st_assignment", writer_types)
        self.assertGreater(evidence.evidence_sources.structured_text, 0)

    def test_st_writer_has_statement_provenance(self) -> None:
        evidence = get_signal_evidence(
            self.tags["Motor_Run"],
            self.objects,
            self.relationships,
        )
        st_writers = [
            w
            for w in evidence.who_writes_this_signal
            if w.source_provenance.originating_language == "structured_text"
        ]
        self.assertTrue(st_writers)
        prov = st_writers[0].source_provenance
        self.assertEqual(prov.originating_language, "structured_text")
        self.assertIsNotNone(prov.statement_index)

    def test_st_conditions_in_verification(self) -> None:
        evidence = get_signal_evidence(
            self.tags["Motor_Run"],
            self.objects,
            self.relationships,
        )
        control_names = {c.signal_name for c in evidence.what_controls_this_signal}
        self.assertTrue(
            {"Mode_OK", "StartPB", "StopPB"} & control_names,
            f"expected ST gating tags, got {control_names}",
        )
        st_groups = evidence.verification.structured_text
        self.assertGreater(len(st_groups), 0)
        roles = {g.role for g in st_groups}
        self.assertTrue(roles & {"assignment", "condition", "read"})


if __name__ == "__main__":
    unittest.main()
