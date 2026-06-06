"""Unified evidence tests for AOI instances and vendor AMP blocks."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.rockwell_l5x import RockwellL5XConnector  # noqa: E402
from app.models.control_model import (  # noqa: E402
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.models.reasoning import ControlObjectType, RelationshipType  # noqa: E402
from app.services.normalization_service import (  # noqa: E402
    normalize_l5x_project,
)
from app.services.unified_evidence_service import get_signal_evidence  # noqa: E402

_FIXTURES = _BACKEND_ROOT / "tests" / "fixtures" / "l5x"


def _vendor_amp_project() -> ControlProject:
    rung_instr = ControlInstruction(
        id="instr::1",
        instruction_type="AMP_ABSOLUTE_MOVE_INTRALOX",
        operands=["AmpInst_1", "Permissive_A", "Position_A", "Output_A"],
        raw_text="AMP_ABSOLUTE_MOVE_INTRALOX(AmpInst_1,Permissive_A,Position_A,Output_A)",
        language="ladder",
        rung_number=0,
    )
    routine = ControlRoutine(
        name="Routine_A",
        language="ladder",
        instructions=[rung_instr],
        ladder_rungs=[],
    )
    tags = [
        ControlTag(name="Permissive_A", data_type="BOOL"),
        ControlTag(name="Position_A", data_type="REAL"),
        ControlTag(name="Output_A", data_type="BOOL"),
        ControlTag(name="AmpInst_1", data_type="BOOL"),
    ]
    program = ControlProgram(name="PRG_A", tags=tags, routines=[routine])
    controller = ControlController(name="Synth", programs=[program])
    return ControlProject(
        project_name="synthetic_amp",
        file_name="synthetic_amp.L5X",
        connector="rockwell_l5x",
        controllers=[controller],
    )


class UnifiedEvidenceAOIVendorTests(unittest.TestCase):
    def test_aoi_fixture_shows_parameter_out(self) -> None:
        path = _FIXTURES / "synthetic_aoi_udt_system.L5X"
        project = RockwellL5XConnector().parse(path.name, path.read_bytes())
        normalized = normalize_l5x_project(project)
        pump_run = next(
            obj
            for obj in normalized["control_objects"]
            if obj.name and obj.name.endswith("Run_Cmd") and "Pump_Transfer" in obj.name
        )
        evidence = get_signal_evidence(
            pump_run.id,
            normalized["control_objects"],
            normalized["relationships"],
        )
        aoi_groups = evidence.verification.aoi
        self.assertGreater(len(aoi_groups), 0)
        param_names = {g.parameter_name for g in aoi_groups if g.parameter_name}
        self.assertIn("Run", param_names, f"expected AOI Run output parameter, got {param_names}")
        writer_types = {w.writer_type for w in evidence.who_writes_this_signal}
        self.assertIn("aoi_parameter", writer_types)
        out_writer = next(
            w for w in evidence.who_writes_this_signal if w.writer_type == "aoi_parameter"
        )
        self.assertEqual(out_writer.source_provenance.pin_name, "Run")

    def test_vendor_amp_block_not_unknown(self) -> None:
        normalized = normalize_l5x_project(_vendor_amp_project())
        output_a = next(
            obj for obj in normalized["control_objects"] if obj.name == "Output_A"
        )
        evidence = get_signal_evidence(
            output_a.id,
            normalized["control_objects"],
            normalized["relationships"],
        )
        self.assertGreater(len(evidence.who_writes_this_signal), 0)
        writer = evidence.who_writes_this_signal[0]
        self.assertEqual(writer.writer_type, "aoi_parameter")
        self.assertEqual(writer.source_provenance.originating_language, "aoi")
        prov = writer.source_provenance
        self.assertEqual(prov.pin_name, "Out")
        self.assertEqual(len(evidence.unknowns), 0)

        write_rels = [
            r
            for r in normalized["relationships"]
            if r.target_id == output_a.id
            and r.relationship_type == RelationshipType.WRITES
        ]
        self.assertTrue(write_rels)
        meta = write_rels[0].platform_specific or {}
        self.assertEqual(meta.get("binding_kind"), "vendor_amp_block")
        self.assertEqual(meta.get("parameter_name"), "Out")

    def test_vendor_amp_reads_are_deterministic_conditions(self) -> None:
        normalized = normalize_l5x_project(_vendor_amp_project())
        permissive = next(
            obj for obj in normalized["control_objects"] if obj.name == "Permissive_A"
        )
        evidence = get_signal_evidence(
            permissive.id,
            normalized["control_objects"],
            normalized["relationships"],
        )
        aoi_reads = [
            g for g in evidence.verification.aoi if g.parameter_name and "In_" in g.parameter_name
        ]
        self.assertGreater(len(aoi_reads), 0)

        amp_blocks = [
            o
            for o in normalized["control_objects"]
            if o.object_type == ControlObjectType.FUNCTION_BLOCK
            and (o.attributes or {}).get("is_vendor_amp_block")
        ]
        self.assertEqual(len(amp_blocks), 1)


if __name__ == "__main__":
    unittest.main()
