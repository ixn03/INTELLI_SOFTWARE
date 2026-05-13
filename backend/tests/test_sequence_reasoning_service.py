"""Tests for :mod:`app.services.sequence_reasoning_service`."""

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
from app.services.sequence_reasoning_service import (  # noqa: E402
    analyze_sequences,
    filter_sequence_result_for_tag,
)


def _tag(tag_id: str, name: str, *, data_type: str | None = None) -> ControlObject:
    attrs: dict = {}
    if data_type is not None:
        attrs["data_type"] = data_type
    return ControlObject(
        id=tag_id,
        name=name,
        object_type=ControlObjectType.TAG,
        source_platform="rockwell",
        source_location=f"Tag:{name}",
        attributes=attrs,
        confidence=ConfidenceLevel.HIGH,
    )


def _rung(rung_id: str, n: int = 1) -> ControlObject:
    return ControlObject(
        id=rung_id,
        name=f"Rung[{n}]",
        object_type=ControlObjectType.RUNG,
        source_platform="rockwell",
        source_location=f"Routine/Main/Rung[{n}]",
        confidence=ConfidenceLevel.HIGH,
    )


def _stmt(stmt_id: str) -> ControlObject:
    return ControlObject(
        id=stmt_id,
        name="Statement[0]",
        object_type=ControlObjectType.INSTRUCTION,
        source_platform="rockwell",
        source_location="Routine/Main/Statement[0]",
        attributes={"language": "structured_text"},
        platform_specific={"language": "structured_text", "statement_type": "case"},
        confidence=ConfidenceLevel.HIGH,
    )


def _instr_mov(instr_id: str, rung_id: str, mov_local: str) -> ControlObject:
    return ControlObject(
        id=instr_id,
        name="MOV",
        object_type=ControlObjectType.INSTRUCTION,
        source_platform="rockwell",
        source_location=f"{rung_id}/MOV",
        parent_ids=[rung_id],
        attributes={
            "instruction_type": "MOV",
            "operands": ["10", "State"],
        },
        platform_specific={"instruction_local_id": mov_local},
        confidence=ConfidenceLevel.HIGH,
    )


class SequenceReasoningTests(unittest.TestCase):
    def test_st_case_creates_candidate_and_branches(self) -> None:
        state_id = "tag::PLC01/State"
        step_id = "tag::PLC01/CurrentStep"
        stmt_id = "stmt::PLC01/Main/MainRoutine/Statement[0]"
        objs = [
            _tag(state_id, "State", data_type="DINT"),
            _tag(step_id, "CurrentStep", data_type="DINT"),
            _stmt(stmt_id),
        ]
        rels = [
            Relationship(
                source_id=stmt_id,
                target_id=state_id,
                relationship_type=RelationshipType.READS,
                source_platform="rockwell",
                source_location="Routine/Main/Statement[0]",
                platform_specific={
                    "language": "structured_text",
                    "statement_type": "case",
                    "instruction_type": "CASE_SELECTOR",
                    "condition_source": "case_selector",
                    "case_condition_summary": "State = 1",
                    "branch_label": "1",
                },
                confidence=ConfidenceLevel.HIGH,
            ),
            Relationship(
                source_id=stmt_id,
                target_id=step_id,
                relationship_type=RelationshipType.WRITES,
                source_platform="rockwell",
                source_location="Routine/Main/Statement[0]",
                platform_specific={
                    "language": "structured_text",
                    "statement_type": "case",
                    "instruction_type": "ST_ASSIGN",
                    "assigned_value": "10",
                    "st_parse_status": "ok",
                    "gating_logic_type": "and",
                    "case_condition_summary": "State = 1",
                    "branch_label": "1",
                    "extracted_conditions": [],
                },
                confidence=ConfidenceLevel.HIGH,
            ),
        ]
        out = analyze_sequences(objs, rels, [])
        ids = {c["tag_id"] for c in out["state_candidates"]}
        self.assertIn(state_id, ids)
        self.assertIn(step_id, ids)
        self.assertTrue(any(b["case_condition_summary"] == "State = 1" for b in out["case_branches"]))
        self.assertTrue(any(t["target_state"] == "10" for t in out["state_transitions"]))

    def test_st_state_assignment_transition(self) -> None:
        state_id = "tag::PLC01/State"
        stmt_id = "stmt::PLC01/Main/MainRoutine/Statement[1]"
        objs = [
            _tag(state_id, "State", data_type="DINT"),
            _stmt(stmt_id),
        ]
        rels = [
            Relationship(
                source_id=stmt_id,
                target_id=state_id,
                relationship_type=RelationshipType.WRITES,
                source_platform="rockwell",
                source_location="Routine/Main/Statement[1]",
                platform_specific={
                    "language": "structured_text",
                    "statement_type": "assignment",
                    "instruction_type": "ST_ASSIGN",
                    "assigned_value": "10",
                    "st_parse_status": "ok",
                    "gating_logic_type": "and",
                    "extracted_conditions": [],
                },
                confidence=ConfidenceLevel.HIGH,
            ),
        ]
        out = analyze_sequences(objs, rels, [])
        self.assertEqual(len(out["state_transitions"]), 1)
        self.assertEqual(out["state_transitions"][0]["target_state"], "10")

    def test_ladder_mov_creates_transition(self) -> None:
        rung_id = "rung::PLC01/Main/MainRoutine/Rung[1]"
        state_id = "tag::PLC01/State"
        mov_local = "mov_inst_1"
        instr_id = "instr::PLC01/Main/MainRoutine/Rung[1]/mov_inst_1"
        objs = [
            _tag(state_id, "State", data_type="DINT"),
            _rung(rung_id),
            _instr_mov(instr_id, rung_id, mov_local),
        ]
        rels = [
            Relationship(
                source_id=rung_id,
                target_id=state_id,
                relationship_type=RelationshipType.WRITES,
                write_behavior=WriteBehaviorType.MOVES_VALUE,
                source_platform="rockwell",
                source_location="Routine/Main/Rung[1]",
                logic_condition="MOV(10,State);",
                platform_specific={
                    "instruction_type": "MOV",
                    "instruction_id": mov_local,
                    "operand_role": "move_destination",
                },
                confidence=ConfidenceLevel.HIGH,
            ),
        ]
        out = analyze_sequences(objs, rels, [])
        self.assertEqual(len(out["state_transitions"]), 1)
        self.assertEqual(out["state_transitions"][0]["target_state"], "10")
        self.assertEqual(out["state_transitions"][0]["writer_instruction_type"], "MOV")

    def test_ladder_equ_state_candidate(self) -> None:
        rung_id = "rung::PLC01/Main/MainRoutine/Rung[2]"
        state_id = "tag::PLC01/State"
        objs = [_tag(state_id, "State", data_type="DINT"), _rung(rung_id, 2)]
        rels = [
            Relationship(
                source_id=rung_id,
                target_id=state_id,
                relationship_type=RelationshipType.READS,
                source_platform="rockwell",
                source_location="Routine/Main/Rung[2]",
                logic_condition="EQU(State,1);",
                platform_specific={
                    "instruction_type": "EQU",
                    "instruction_id": "equ1",
                    "gating_kind": "comparison",
                    "comparison_operator": "=",
                    "compared_operands": ["State", "1"],
                },
                confidence=ConfidenceLevel.HIGH,
            ),
        ]
        out = analyze_sequences(objs, rels, [])
        self.assertTrue(any(c["tag_id"] == state_id for c in out["state_candidates"]))
        self.assertEqual(len(out["state_transitions"]), 0)

    def test_non_state_mov_no_transition(self) -> None:
        rung_id = "rung::PLC01/Main/MainRoutine/Rung[3]"
        speed_id = "tag::PLC01/Speed"
        mov_local = "m2"
        instr_id = "instr::PLC01/Main/MainRoutine/Rung[3]/m2"
        objs = [
            _tag(speed_id, "Speed", data_type="DINT"),
            _rung(rung_id, 3),
            _instr_mov(instr_id, rung_id, mov_local),
        ]
        objs[-1].attributes["operands"] = ["100", "Speed"]  # type: ignore[index]
        rels = [
            Relationship(
                source_id=rung_id,
                target_id=speed_id,
                relationship_type=RelationshipType.WRITES,
                write_behavior=WriteBehaviorType.MOVES_VALUE,
                source_platform="rockwell",
                source_location="Routine/Main/Rung[3]",
                platform_specific={
                    "instruction_type": "MOV",
                    "instruction_id": mov_local,
                    "operand_role": "move_destination",
                },
                confidence=ConfidenceLevel.HIGH,
            ),
        ]
        out = analyze_sequences(objs, rels, [])
        self.assertEqual(out["state_transitions"], [])
        self.assertEqual(out["unsupported_sequence_patterns"], [])

    def test_unsupported_calculated_write_preserved(self) -> None:
        rung_id = "rung::PLC01/Main/MainRoutine/Rung[4]"
        state_id = "tag::PLC01/State"
        objs = [_tag(state_id, "State", data_type="DINT"), _rung(rung_id, 4)]
        rels = [
            Relationship(
                source_id=rung_id,
                target_id=state_id,
                relationship_type=RelationshipType.WRITES,
                write_behavior=WriteBehaviorType.CALCULATES,
                source_platform="rockwell",
                source_location="Routine/Main/Rung[4]",
                platform_specific={
                    "instruction_type": "CPT",
                    "instruction_id": "c1",
                },
                confidence=ConfidenceLevel.HIGH,
            ),
        ]
        out = analyze_sequences(objs, rels, [])
        kinds = {u["kind"] for u in out["unsupported_sequence_patterns"]}
        self.assertIn("calculated_state_write", kinds)
        self.assertTrue(any("calculated" in t["target_state"] for t in out["state_transitions"]))

    def test_filter_sequence_for_tag(self) -> None:
        full = {
            "state_candidates": [
                {"tag_id": "tag::A/State", "tag_name": "State", "confidence": "high"}
            ],
            "state_transitions": [
                {"state_tag": "tag::A/State", "target_state": "1"},
                {"state_tag": "tag::A/Other", "target_state": "2"},
            ],
            "case_branches": [
                {"selector_tag_id": "tag::A/State", "case_condition_summary": "State = 1"}
            ],
            "sequence_summary": ["ignored"],
            "unsupported_sequence_patterns": [
                {"state_tag_id": "tag::A/State", "kind": "x"}
            ],
        }
        sub = filter_sequence_result_for_tag(full, "tag::A/State")
        self.assertEqual(len(sub["state_transitions"]), 1)
        self.assertEqual(len(sub["case_branches"]), 1)


if __name__ == "__main__":
    unittest.main()
