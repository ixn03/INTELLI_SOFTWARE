"""Synthetic tests for the signal troubleshooting workspace service."""

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
from app.services.troubleshooting_workspace_service import (  # noqa: E402
    build_signal_workspace,
    interpret_question,
)


def _tag(name: str) -> ControlObject:
    return ControlObject(
        id=f"tag::PLC/PRG/{name}",
        name=name,
        object_type=ControlObjectType.TAG,
        source_location=f"Controller:PLC/Program:PRG/Tag:{name}",
    )


def _rung(idx: int) -> ControlObject:
    return ControlObject(
        id=f"rung::PLC/PRG/Routine_A/{idx}",
        name=f"Rung {idx}",
        object_type=ControlObjectType.RUNG,
        source_location=f"Controller:PLC/Program:PRG/Routine:Routine_A/Rung[{idx}]",
    )


def _block() -> ControlObject:
    return ControlObject(
        id="block::PLC/PRG/Generic_A",
        name="Generic_A",
        object_type=ControlObjectType.FUNCTION_BLOCK,
        source_location="Controller:PLC/Program:PRG/Routine:Routine_A/Block:1",
    )


def _read(source: str, target: str, idx: int) -> Relationship:
    return Relationship(
        id=f"rel::read::{source}::{target}::{idx}",
        source_id=source,
        target_id=f"tag::PLC/PRG/{target}",
        relationship_type=RelationshipType.READS,
        source_location=f"Controller:PLC/Program:PRG/Routine:Routine_A/Rung[{idx}]",
        confidence=ConfidenceLevel.HIGH,
        platform_specific={"instruction_type": "XIC"},
    )


def _write(source: str, target: str, idx: int) -> Relationship:
    return Relationship(
        id=f"rel::write::{source}::{target}::{idx}",
        source_id=source,
        target_id=f"tag::PLC/PRG/{target}",
        relationship_type=RelationshipType.WRITES,
        write_behavior=WriteBehaviorType.SETS_TRUE,
        source_location=f"Controller:PLC/Program:PRG/Routine:Routine_A/Rung[{idx}]",
        confidence=ConfidenceLevel.HIGH,
        platform_specific={"instruction_type": "OTE"},
    )


class TroubleshootingWorkspaceServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.objects = [
            _tag("Permissive_A"),
            _tag("Fault_1"),
            _tag("Motor_Run"),
            _tag("Valve_Open"),
            _tag("No_Writer"),
            _rung(1),
            _rung(2),
            _rung(3),
            _block(),
        ]
        self.relationships = [
            _read("rung::PLC/PRG/Routine_A/1", "Permissive_A", 1),
            _read("rung::PLC/PRG/Routine_A/1", "Fault_1", 1),
            _write("rung::PLC/PRG/Routine_A/1", "Motor_Run", 1),
            _write("rung::PLC/PRG/Routine_A/2", "Motor_Run", 2),
            _read("rung::PLC/PRG/Routine_A/3", "Motor_Run", 3),
            _write("rung::PLC/PRG/Routine_A/3", "Valve_Open", 3),
            Relationship(
                id="rel::unknown::1",
                source_id="block::PLC/PRG/Generic_A",
                target_id="tag::PLC/PRG/Motor_Run",
                relationship_type=RelationshipType.REFERENCES,
                source_location="Controller:PLC/Program:PRG/Routine:Routine_A/Block:1",
                confidence=ConfidenceLevel.LOW,
                platform_specific={"binding_status": "direction_unknown"},
            ),
        ]

    def test_exact_question_resolves_target(self) -> None:
        interp = interpret_question("Why is Motor_Run not energizing?", self.objects)
        self.assertEqual(interp.intent, "why_not_energized")
        self.assertEqual(interp.selected_target_signal.name, "Motor_Run")
        self.assertEqual(interp.selected_target_signal.match_type, "exact_tag_match")

    def test_why_on_question_resolves_on_intent(self) -> None:
        interp = interpret_question("Why is Motor_Run energized?", self.objects)
        self.assertEqual(interp.intent, "why_on")
        self.assertEqual(interp.selected_target_signal.name, "Motor_Run")

    def test_fuzzy_question_resolves_intended_tag(self) -> None:
        interp = interpret_question("why is moter run not changing", self.objects)
        self.assertEqual(interp.selected_target_signal.name, "Motor_Run")
        self.assertEqual(interp.selected_target_signal.match_type, "fuzzy_match")

    def test_no_writer_found(self) -> None:
        workspace = build_signal_workspace(
            question="Why is No_Writer not on?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(workspace.target_signal.name, "No_Writer")
        self.assertEqual(workspace.writer_rungs, [])
        self.assertTrue(workspace.confidence_summary.warnings)

    def test_multiple_writers_found(self) -> None:
        workspace = build_signal_workspace(
            question="Where is Motor_Run written?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(len(workspace.writer_rungs), 2)
        self.assertEqual(workspace.interpretation.intent, "where_written")

    def test_upstream_dependencies_shown(self) -> None:
        workspace = build_signal_workspace(
            question="Why is Motor_Run not energizing?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        names = {item.target_name for item in workspace.upstream_required_conditions}
        self.assertIn("Permissive_A", names)
        self.assertIn("Fault_1", names)

    def test_downstream_readers_shown(self) -> None:
        workspace = build_signal_workspace(
            question="Where is Motor_Run read?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(len(workspace.downstream_readers), 1)
        self.assertEqual(workspace.downstream_readers[0].source_name, "Rung 3")

    def test_unknown_direction_block_is_not_cause(self) -> None:
        workspace = build_signal_workspace(
            question="Why is Motor_Run not energizing?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(len(workspace.unknown_direction_blocks), 1)
        upstream_source_ids = {i.source_id for i in workspace.upstream_required_conditions}
        self.assertNotIn("block::PLC/PRG/Generic_A", upstream_source_ids)


if __name__ == "__main__":
    unittest.main()
