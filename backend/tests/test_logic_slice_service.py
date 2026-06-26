"""Tests for canonical LogicSlice layer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.reasoning import (  # noqa: E402
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)
from app.services.logic_slice_service import (  # noqa: E402
    build_program_slices,
    logic_paths_from_slices,
    slices_for_target,
)


def _tag(name: str) -> ControlObject:
    return ControlObject(
        id=f"tag::PLC/PRG/{name}",
        name=name,
        object_type=ControlObjectType.TAG,
        source_location=f"Controller:PLC/Program:PRG/Tag:{name}",
    )


class LogicSliceServiceTests(unittest.TestCase):
    def test_ladder_xio_slice_groups_conditions_and_write(self) -> None:
        rung_id = "rung::PLC/PRG/Fault_Logic/9"
        objects = [
            _tag("MTR_T1_OL"),
            _tag("MTR_T2_OL"),
            _tag("EMERG_STOP"),
            _tag("FAULT_OK"),
            ControlObject(
                id=rung_id,
                name="Rung 9",
                object_type=ControlObjectType.RUNG,
                source_location="Controller:PLC/Program:PRG/Routine:Fault_Logic/Rung[9]",
            ),
        ]
        relationships = [
            Relationship(
                id="rel::read::t1",
                source_id=rung_id,
                target_id="tag::PLC/PRG/MTR_T1_OL",
                relationship_type=RelationshipType.READS,
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "XIO", "operand_role": "condition"},
            ),
            Relationship(
                id="rel::read::t2",
                source_id=rung_id,
                target_id="tag::PLC/PRG/MTR_T2_OL",
                relationship_type=RelationshipType.READS,
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "XIO", "operand_role": "condition"},
            ),
            Relationship(
                id="rel::read::estop",
                source_id=rung_id,
                target_id="tag::PLC/PRG/EMERG_STOP",
                relationship_type=RelationshipType.READS,
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "XIO", "operand_role": "condition"},
            ),
            Relationship(
                id="rel::write::fault_ok",
                source_id=rung_id,
                target_id="tag::PLC/PRG/FAULT_OK",
                relationship_type=RelationshipType.WRITES,
                write_behavior=WriteBehaviorType.SETS_TRUE,
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "OTE"},
            ),
        ]
        slices = build_program_slices(objects, relationships)
        self.assertEqual(len(slices), 1)
        slice_obj = slices[0]
        self.assertEqual(slice_obj.slice_kind, "boolean_control")
        self.assertEqual(len(slice_obj.gate_operands), 3)
        expression = slice_obj.readable_expression or ""
        self.assertIn("NOT MTR_T1_OL", expression)
        self.assertIn("FAULT_OK", expression)

        target_slices = slices_for_target(slices, "tag::PLC/PRG/FAULT_OK")
        paths = logic_paths_from_slices(slices, "tag::PLC/PRG/FAULT_OK")
        self.assertEqual(len(paths), 1)
        self.assertEqual(
            {ref.signal_name for ref in paths[0].input_signals},
            {"MTR_T1_OL", "MTR_T2_OL", "EMERG_STOP"},
        )

    def test_add_accumulator_not_listed_as_external_input(self) -> None:
        rung_id = "rung::PLC/PRG/Calc/2"
        objects = [
            _tag("PE2"),
            _tag("Gap"),
            ControlObject(
                id=rung_id,
                name="Rung 2",
                object_type=ControlObjectType.RUNG,
                source_location="Controller:PLC/Program:PRG/Routine:Calc/Rung[2]",
            ),
        ]
        relationships = [
            Relationship(
                id="rel::read::pe2",
                source_id=rung_id,
                target_id="tag::PLC/PRG/PE2",
                relationship_type=RelationshipType.READS,
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "ADD", "operand_index": 0},
            ),
            Relationship(
                id="rel::read::gap",
                source_id=rung_id,
                target_id="tag::PLC/PRG/Gap",
                relationship_type=RelationshipType.READS,
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "ADD", "operand_index": 1},
            ),
            Relationship(
                id="rel::write::pe2",
                source_id=rung_id,
                target_id="tag::PLC/PRG/PE2",
                relationship_type=RelationshipType.WRITES,
                write_behavior=WriteBehaviorType.CALCULATES,
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "ADD", "operand_index": 2},
            ),
        ]
        slices = build_program_slices(objects, relationships)
        self.assertEqual(slices[0].slice_kind, "calculation")
        accumulators = [o for o in slices[0].data_operands if o.role == "accumulator"]
        self.assertEqual(len(accumulators), 1)
        expression = slices[0].readable_expression or ""
        self.assertIn("+=", expression)
        paths = logic_paths_from_slices(slices, "tag::PLC/PRG/PE2")
        input_names = {ref.signal_name for ref in paths[0].input_signals}
        self.assertIn("Gap", input_names)
        self.assertNotIn("PE2", input_names)


if __name__ == "__main__":
    unittest.main()
