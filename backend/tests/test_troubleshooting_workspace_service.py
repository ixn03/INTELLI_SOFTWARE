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
from app.models.knowledge import KnowledgeItem, KnowledgeStatus, KnowledgeType  # noqa: E402
from app.services.knowledge_service import knowledge_service  # noqa: E402
from app.services.troubleshooting_workspace_service import (  # noqa: E402
    build_signal_workspace,
    interpret_question,
)


def _tag(name: str, program: str = "PRG") -> ControlObject:
    return ControlObject(
        id=f"tag::PLC/{program}/{name}",
        name=name,
        object_type=ControlObjectType.TAG,
        source_location=f"Controller:PLC/Program:{program}/Tag:{name}",
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


def _fbd_block() -> ControlObject:
    return ControlObject(
        id="block::PLC/PRG/FBD_A",
        name="FBD_A",
        object_type=ControlObjectType.FUNCTION_BLOCK,
        source_location="Controller:PLC/Program:PRG/Routine:FBD_Main/Block:FBD_A",
    )


def _st_statement() -> ControlObject:
    return ControlObject(
        id="st::PLC/PRG/ST_Main/1",
        name="ST statement 1",
        object_type=ControlObjectType.INSTRUCTION,
        source_location="Controller:PLC/Program:PRG/Routine:ST_Main/Statement:1",
    )


def _calc_rung() -> ControlObject:
    return ControlObject(
        id="rung::PLC/PRG/Recipe_Calc/10",
        name="Rung 10",
        object_type=ControlObjectType.RUNG,
        source_location="Controller:PLC/Program:PRG/Routine:Recipe_Calc/Rung[10]",
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
        knowledge_service.reset()
        self.objects = [
            _tag("Permissive_A"),
            _tag("Fault_1"),
            _tag("Motor_Run"),
            _tag("Valve_Open"),
            _tag("No_Writer"),
            _tag("Duplicated_Tag"),
            _tag("Duplicated_Tag", "PRG_B"),
            _tag("FBD_Result"),
            _tag("ST_Result"),
            _tag("Internal_Recipes[0]"),
            _tag("MaxRecipeNum"),
            _rung(1),
            _rung(2),
            _rung(3),
            _calc_rung(),
            _block(),
            _fbd_block(),
            _st_statement(),
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
            Relationship(
                id="rel::write::fbd",
                source_id="block::PLC/PRG/FBD_A",
                target_id="tag::PLC/PRG/FBD_Result",
                relationship_type=RelationshipType.WRITES,
                source_location="Controller:PLC/Program:PRG/Routine:FBD_Main/Block:FBD_A/Pin:Out",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "FBD_OUTPUT"},
            ),
            Relationship(
                id="rel::write::st",
                source_id="st::PLC/PRG/ST_Main/1",
                target_id="tag::PLC/PRG/ST_Result",
                relationship_type=RelationshipType.WRITES,
                source_location="Controller:PLC/Program:PRG/Routine:ST_Main/Statement:1",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "ST"},
            ),
            Relationship(
                id="rel::read::size::source",
                source_id="rung::PLC/PRG/Recipe_Calc/10",
                target_id="tag::PLC/PRG/Internal_Recipes[0]",
                relationship_type=RelationshipType.READS,
                source_location="Controller:PLC/Program:PRG/Routine:Recipe_Calc/Rung[10]/Instruction:SIZE",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "SIZE", "operand_index": 0},
            ),
            Relationship(
                id="rel::write::size::dest",
                source_id="rung::PLC/PRG/Recipe_Calc/10",
                target_id="tag::PLC/PRG/MaxRecipeNum",
                relationship_type=RelationshipType.WRITES,
                write_behavior=WriteBehaviorType.MOVES_VALUE,
                source_location="Controller:PLC/Program:PRG/Routine:Recipe_Calc/Rung[10]/Instruction:SIZE",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "SIZE", "operand_index": 2},
            ),
            Relationship(
                id="rel::read::sub::accumulator",
                source_id="rung::PLC/PRG/Recipe_Calc/10",
                target_id="tag::PLC/PRG/MaxRecipeNum",
                relationship_type=RelationshipType.READS,
                source_location="Controller:PLC/Program:PRG/Routine:Recipe_Calc/Rung[10]/Instruction:SUB",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "SUB", "operand_index": 0},
            ),
            Relationship(
                id="rel::write::sub::dest",
                source_id="rung::PLC/PRG/Recipe_Calc/10",
                target_id="tag::PLC/PRG/MaxRecipeNum",
                relationship_type=RelationshipType.WRITES,
                write_behavior=WriteBehaviorType.CALCULATES,
                source_location="Controller:PLC/Program:PRG/Routine:Recipe_Calc/Rung[10]/Instruction:SUB",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "SUB", "operand_index": 2},
            ),
        ]

    def tearDown(self) -> None:
        knowledge_service.reset()

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
        controls_names = {
            item.target_name
            for item in workspace.what_controls_this_signal.upstream_required_conditions
        }
        self.assertIn("Permissive_A", controls_names)
        self.assertEqual(
            workspace.current_state_explanation.status,
            "live_data_missing",
        )

    def test_logic_line_groups_group_tags_by_writer_line(self) -> None:
        workspace = build_signal_workspace(
            question="Why is Motor_Run not energizing?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        groups = workspace.what_controls_this_signal.logic_line_groups
        self.assertGreaterEqual(len(groups), 2)
        gated = next(
            g
            for g in groups
            if {c.signal_name for c in g.conditions} >= {"Permissive_A", "Fault_1"}
        )
        self.assertEqual(gated.language, "ladder")
        self.assertEqual(len(gated.conditions), 2)
        self.assertTrue(gated.logic_text)
        self.assertIn("Permissive_A", gated.logic_text)
        self.assertIn("Fault_1", gated.logic_text)

    def test_logic_paths_group_ladder_xio_conditions_into_one_path(self) -> None:
        objects = [
            _tag("MTR_T1_OL"),
            _tag("MTR_T2_OL"),
            _tag("EMERG_STOP"),
            _tag("FAULT_OK"),
            ControlObject(
                id="rung::PLC/PRG/Fault_Logic/9",
                name="Rung 9",
                object_type=ControlObjectType.RUNG,
                source_location="Controller:PLC/Program:PRG/Routine:Fault_Logic/Rung[9]",
            ),
        ]
        rung_id = "rung::PLC/PRG/Fault_Logic/9"
        relationships = [
            Relationship(
                id="rel::read::t1",
                source_id=rung_id,
                target_id="tag::PLC/PRG/MTR_T1_OL",
                relationship_type=RelationshipType.READS,
                source_location="Controller:PLC/Program:PRG/Routine:Fault_Logic/Rung[9]",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "XIO", "operand_role": "condition"},
            ),
            Relationship(
                id="rel::read::t2",
                source_id=rung_id,
                target_id="tag::PLC/PRG/MTR_T2_OL",
                relationship_type=RelationshipType.READS,
                source_location="Controller:PLC/Program:PRG/Routine:Fault_Logic/Rung[9]",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "XIO", "operand_role": "condition"},
            ),
            Relationship(
                id="rel::read::estop",
                source_id=rung_id,
                target_id="tag::PLC/PRG/EMERG_STOP",
                relationship_type=RelationshipType.READS,
                source_location="Controller:PLC/Program:PRG/Routine:Fault_Logic/Rung[9]",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "XIO", "operand_role": "condition"},
            ),
            Relationship(
                id="rel::write::fault_ok",
                source_id=rung_id,
                target_id="tag::PLC/PRG/FAULT_OK",
                relationship_type=RelationshipType.WRITES,
                write_behavior=WriteBehaviorType.SETS_TRUE,
                source_location="Controller:PLC/Program:PRG/Routine:Fault_Logic/Rung[9]",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={"instruction_type": "OTE"},
            ),
        ]
        workspace = build_signal_workspace(
            question="Why is FAULT_OK not on?",
            control_objects=objects,
            relationships=relationships,
        )
        paths = workspace.what_controls_this_signal.logic_paths
        self.assertEqual(len(paths), 1)
        path = paths[0]
        self.assertEqual(path.rung_number, 9)
        self.assertEqual(path.language, "ladder")
        expression = path.readable_expression or ""
        self.assertIn("NOT MTR_T1_OL", expression)
        self.assertIn("NOT MTR_T2_OL", expression)
        self.assertIn("NOT EMERG_STOP", expression)
        self.assertIn("FAULT_OK", expression)
        input_names = {ref.signal_name for ref in path.input_signals}
        self.assertEqual(input_names, {"MTR_T1_OL", "MTR_T2_OL", "EMERG_STOP"})
        write_names = {ref.signal_name for ref in path.output_signals}
        self.assertEqual(write_names, {"FAULT_OK"})
        self.assertNotIn("controlled by", workspace.deterministic_explanation.lower())

    def test_logic_paths_surface_size_sub_calculation(self) -> None:
        workspace = build_signal_workspace(
            question="How is MaxRecipeNum calculated?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        paths = workspace.what_controls_this_signal.logic_paths
        self.assertEqual(len(paths), 1)
        path = paths[0]
        self.assertEqual(path.metadata.get("path_kind"), "calculation")
        self.assertIn(
            "MaxRecipeNum = SIZE(Internal_Recipes[0]) - 1",
            path.readable_expression or "",
        )
        input_names = {ref.signal_name for ref in path.input_signals}
        self.assertIn("Internal_Recipes[0]", input_names)
        write_names = {write.signal_name for write in path.write_operations}
        self.assertIn("MaxRecipeNum", write_names)
        self.assertEqual(
            workspace.what_controls_this_signal.upstream_required_conditions,
            [],
        )
        self.assertIn("calculation logic path", workspace.deterministic_explanation.lower())

    def test_logic_paths_use_st_statement_text_when_available(self) -> None:
        objects = [
            _tag("ST_Result"),
            ControlObject(
                id="st::PLC/PRG/ST_Main/3",
                name="ST statement 3",
                object_type=ControlObjectType.INSTRUCTION,
                source_location="Controller:PLC/Program:PRG/Routine:ST_Main/Statement:3",
            ),
        ]
        relationships = [
            Relationship(
                id="rel::write::st::3",
                source_id="st::PLC/PRG/ST_Main/3",
                target_id="tag::PLC/PRG/ST_Result",
                relationship_type=RelationshipType.WRITES,
                source_location="Controller:PLC/Program:PRG/Routine:ST_Main/Statement:3",
                confidence=ConfidenceLevel.HIGH,
                platform_specific={
                    "instruction_type": "ST",
                    "statement_text": "ST_Result := Permissive_A AND NOT Fault_1;",
                },
            ),
        ]
        workspace = build_signal_workspace(
            question="Where is ST_Result written?",
            control_objects=objects,
            relationships=relationships,
        )
        paths = workspace.what_controls_this_signal.logic_paths
        self.assertEqual(len(paths), 1)
        self.assertEqual(
            paths[0].readable_expression,
            "ST_Result := Permissive_A AND NOT Fault_1;",
        )
        self.assertEqual(paths[0].statement_index, 3)
        self.assertEqual(paths[0].language, "structured_text")

    def test_downstream_readers_shown(self) -> None:
        workspace = build_signal_workspace(
            question="Where is Motor_Run read?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(len(workspace.downstream_readers), 1)
        self.assertEqual(workspace.downstream_readers[0].source_name, "Rung 3")
        influenced = workspace.what_this_signal_controls.downstream_writes_influenced
        self.assertEqual(len(influenced), 1)
        self.assertEqual(influenced[0].target_name, "Valve_Open")

    def test_unknown_direction_block_is_not_cause(self) -> None:
        workspace = build_signal_workspace(
            question="Why is Motor_Run not energizing?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(len(workspace.unknown_direction_blocks), 1)
        upstream_source_ids = {i.source_id for i in workspace.upstream_required_conditions}
        self.assertNotIn("block::PLC/PRG/Generic_A", upstream_source_ids)
        refs = workspace.what_controls_this_signal.unknown_direction_references
        self.assertEqual(refs[0].source_name, "Generic_A")

    def test_duplicate_scoped_tag_candidates_are_reported(self) -> None:
        workspace = build_signal_workspace(
            question="Where is Duplicated_Tag written?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(
            workspace.resolved_scope.duplicate_name_status,
            "duplicates_found",
        )
        programs = {candidate.program for candidate in workspace.resolved_scope.candidate_scoped_tags}
        self.assertEqual(programs, {"PRG", "PRG_B"})

    def test_evidence_grouped_by_language(self) -> None:
        ladder = build_signal_workspace(
            question="Where is Motor_Run written?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        fbd = build_signal_workspace(
            question="Where is FBD_Result written?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        st = build_signal_workspace(
            question="Where is ST_Result written?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(len(ladder.who_writes_this_signal.ladder), 2)
        self.assertEqual(len(fbd.who_writes_this_signal.fbd), 1)
        self.assertEqual(len(st.who_writes_this_signal.structured_text), 1)
        languages = {item.language for item in fbd.where_evidence_comes_from}
        self.assertIn("fbd", languages)

    def test_knowledge_context_included_when_facts_exist(self) -> None:
        knowledge_service.create(
            KnowledgeItem(
                target_object_id="tag::PLC/PRG/Motor_Run",
                target_name="Motor_Run",
                knowledge_type=KnowledgeType.TROUBLESHOOTING_NOTE,
                statement="Synthetic note: verify permissive before forcing output.",
                status=KnowledgeStatus.VERIFIED,
            )
        )
        workspace = build_signal_workspace(
            question="Why is Motor_Run not energizing?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(workspace.knowledge_context.status, "available")
        self.assertEqual(len(workspace.knowledge_context.engineer_notes), 1)
        self.assertFalse(workspace.confidence_summary.missing_engineer_knowledge)

    def test_live_state_available_identifies_blocking_condition(self) -> None:
        workspace = build_signal_workspace(
            question="Why is Motor_Run not energizing?",
            control_objects=self.objects,
            relationships=self.relationships,
            runtime_snapshot={
                "Motor_Run": {"value": False, "timestamp": "2026-06-06T12:00:00Z"},
                "Permissive_A": {"value": False, "timestamp": "2026-06-06T12:00:00Z"},
                "Fault_1": {"value": True, "timestamp": "2026-06-06T12:00:00Z"},
            },
        )
        self.assertEqual(
            workspace.current_state_explanation.status,
            "live_data_available",
        )
        blocking = {
            item.signal_name
            for item in workspace.current_state_explanation.blocking_conditions
        }
        self.assertIn("Permissive_A", blocking)
        self.assertFalse(workspace.confidence_summary.missing_live_state)

    def test_historical_context_stub_is_explicit(self) -> None:
        workspace = build_signal_workspace(
            question="Why is Motor_Run not energizing?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        self.assertEqual(workspace.historical_context.status, "not_available")

    def test_size_sub_operands_are_data_sources_not_conditions(self) -> None:
        workspace = build_signal_workspace(
            question="How is MaxRecipeNum calculated?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        condition_names = {
            item.target_name
            for item in workspace.what_controls_this_signal.upstream_required_conditions
        }
        data_source_names = {
            item.target_name
            for item in workspace.what_controls_this_signal.data_source_reads
        }
        self.assertNotIn("Internal_Recipes[0]", condition_names)
        self.assertIsNotNone(workspace.unified_evidence)
        assert workspace.unified_evidence is not None
        unified_condition_names = {
            item.signal_name
            for item in workspace.unified_evidence.what_controls_this_signal
        }
        self.assertNotIn("Internal_Recipes[0]", unified_condition_names)
        self.assertIn("Internal_Recipes[0]", data_source_names)
        self.assertIn("MaxRecipeNum", data_source_names)
        self.assertEqual(
            workspace.what_controls_this_signal.data_source_reads[0].metadata[
                "operand_semantic_role"
            ],
            "data_source_read",
        )

    def test_size_sub_target_is_derived_calculation_write_target(self) -> None:
        workspace = build_signal_workspace(
            question="How is MaxRecipeNum calculated?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        instructions = {
            item.instruction_type
            for item in workspace.what_controls_this_signal.derived_calculations
        }
        write_targets = {
            item.target_name
            for item in workspace.what_controls_this_signal.write_operations
        }
        self.assertEqual(instructions, {"SIZE", "SUB"})
        self.assertIn("MaxRecipeNum", write_targets)
        self.assertIn(
            "highest valid zero-based index",
            workspace.what_controls_this_signal.derived_explanation or "",
        )
        self.assertNotIn("controlled by", workspace.deterministic_explanation.lower())
        for item in workspace.what_controls_this_signal.write_operations:
            self.assertEqual(item.condition_signal_names, [])

    def test_boolean_conditions_still_render_as_required_conditions(self) -> None:
        workspace = build_signal_workspace(
            question="Why is Motor_Run not energizing?",
            control_objects=self.objects,
            relationships=self.relationships,
        )
        roles = {
            item.metadata.get("operand_semantic_role")
            for item in workspace.what_controls_this_signal.upstream_required_conditions
        }
        self.assertEqual(roles, {"boolean_condition_read"})


if __name__ == "__main__":
    unittest.main()
