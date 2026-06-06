"""Synthetic tests for deterministic tag dependency/provenance graphs."""

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
    LogicExpression,
    LogicExpressionKind,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)
from app.services.dependency_graph_service import (  # noqa: E402
    build_control_dependency_graph,
    trace_dependency_chains,
)


def _tag(name: str, *, value: bool | None = None) -> ControlObject:
    return ControlObject(
        id=f"tag::PRG.{name}",
        name=name,
        object_type=ControlObjectType.TAG,
        current_state={} if value is None else {"value": value, "quality": "good"},
    )


def _rung(idx: int) -> ControlObject:
    return ControlObject(
        id=f"rung::PRG.Routine_A.{idx}",
        name=f"Rung {idx}",
        object_type=ControlObjectType.RUNG,
        source_location=f"Controller/Program:PRG/Routine:Routine_A/Rung[{idx}]",
    )


def _read(source_id: str, tag: str, idx: int) -> Relationship:
    return Relationship(
        id=f"rel::read::{idx}",
        source_id=source_id,
        target_id=f"tag::PRG.{tag}",
        relationship_type=RelationshipType.READS,
        source_location=f"Controller/Program:PRG/Routine:Routine_A/Rung[{idx}]",
        confidence=ConfidenceLevel.HIGH,
    )


def _write(
    source_id: str,
    tag: str,
    idx: int,
    expr: LogicExpression,
) -> Relationship:
    return Relationship(
        id=f"rel::write::{idx}",
        source_id=source_id,
        target_id=f"tag::PRG.{tag}",
        relationship_type=RelationshipType.WRITES,
        write_behavior=WriteBehaviorType.SETS_TRUE,
        logic_expression=expr,
        source_location=f"Controller/Program:PRG/Routine:Routine_A/Rung[{idx}]",
        confidence=ConfidenceLevel.HIGH,
        platform_specific={"instruction_type": "OTE"},
    )


def _contact(tag: str, *, examined: bool = True) -> LogicExpression:
    return LogicExpression(
        kind=LogicExpressionKind.CONTACT,
        tag=tag,
        examined_value=examined,
        instruction_type="XIC" if examined else "XIO",
    )


class DependencyGraphServiceTests(unittest.TestCase):
    def test_builds_dependency_chains_from_reads_that_gate_writes(self) -> None:
        objects = [
            _tag("Permissive_A"),
            _tag("Fault_1"),
            _tag("Pump_Ready"),
            _tag("Motor_Run"),
            _tag("Valve_Open"),
            _rung(1),
            _rung(2),
            _rung(3),
        ]
        expr_motor = LogicExpression(
            kind=LogicExpressionKind.AND,
            children=[_contact("Pump_Ready"), _contact("Fault_1", examined=False)],
        )
        relationships = [
            _read("rung::PRG.Routine_A.1", "Permissive_A", 1),
            _write("rung::PRG.Routine_A.1", "Pump_Ready", 1, _contact("Permissive_A")),
            _read("rung::PRG.Routine_A.2", "Pump_Ready", 2),
            _read("rung::PRG.Routine_A.2", "Fault_1", 2),
            _write("rung::PRG.Routine_A.2", "Motor_Run", 2, expr_motor),
            _read("rung::PRG.Routine_A.3", "Motor_Run", 3),
            _write("rung::PRG.Routine_A.3", "Valve_Open", 3, _contact("Motor_Run")),
        ]

        graph = build_control_dependency_graph(objects, relationships)

        self.assertEqual(graph.metadata["dependency_edge_count"], 4)
        self.assertIn(
            ("Permissive_A", "Pump_Ready"),
            {(e.upstream_tag_name, e.downstream_tag_name) for e in graph.edges},
        )
        self.assertIn(
            ("Pump_Ready", "Motor_Run"),
            {(e.upstream_tag_name, e.downstream_tag_name) for e in graph.edges},
        )
        trace = trace_dependency_chains(graph, "Motor_Run")
        self.assertIn(
            ["Permissive_A", "Pump_Ready", "Motor_Run"],
            trace.dependency_chain_names,
        )
        self.assertIn("Pump_Ready", trace.required_condition_tag_names)
        self.assertIn("Fault_1", trace.required_condition_tag_names)

        motor = graph.provenance["tag::PRG.Motor_Run"]
        self.assertEqual(motor.writer_count, 1)
        self.assertEqual(motor.reader_count, 1)
        self.assertGreaterEqual(motor.confidence.confidence_score, 0.9)
        self.assertIn("live_tag_state", motor.confidence.evidence.missing)

    def test_unconditional_writer_is_preserved_without_fake_edges(self) -> None:
        objects = [_tag("Always_On"), _rung(1)]
        expr = LogicExpression(
            kind=LogicExpressionKind.CONSTANT,
            constant_value=True,
            instruction_type="UNCONDITIONAL",
        )
        relationships = [_write("rung::PRG.Routine_A.1", "Always_On", 1, expr)]

        graph = build_control_dependency_graph(objects, relationships)

        self.assertEqual(graph.edges, [])
        writer = graph.provenance["tag::PRG.Always_On"].writers[0]
        self.assertEqual(writer.condition_summary, "unconditional")

    def test_direction_unknown_generic_blocks_do_not_create_dependencies(self) -> None:
        objects = [
            _tag("Pump_A"),
            ControlObject(
                id="block::PRG.Unknown_Block.1",
                name="Unknown_Block",
                object_type=ControlObjectType.FUNCTION_BLOCK,
                platform_specific={
                    "generic_logic_block": True,
                    "binding_status": "direction_unknown",
                },
            ),
        ]
        relationships = [
            Relationship(
                id="rel::ref::1",
                source_id="block::PRG.Unknown_Block.1",
                target_id="tag::PRG.Pump_A",
                relationship_type=RelationshipType.REFERENCES,
                confidence=ConfidenceLevel.LOW,
                platform_specific={"binding_status": "direction_unknown"},
            )
        ]

        graph = build_control_dependency_graph(objects, relationships)

        self.assertEqual(graph.edges, [])
        provenance = graph.provenance["tag::PRG.Pump_A"]
        self.assertEqual(len(provenance.referenced_by_unknown_blocks), 1)
        self.assertEqual(provenance.writer_count, 0)
        self.assertLess(provenance.confidence.confidence_score, 0.5)


if __name__ == "__main__":
    unittest.main()
