"""Tests for :mod:`app.services.trace_v2_service`.

Covers:

* ``humanize_instruction_type`` for known + unknown instructions,
  with and without a target name.
* ``extract_simple_ladder_conditions`` for plain rung strings,
  output-only strings, mixed-case, and empty input.
* ``trace_object_v2`` end-to-end: writer WHAT + CONDITIONS
  conclusions, multiple writers, latch/unlatch phrasing, JSR,
  Structured Text "too complex" fallback, summary augmentation,
  and identical pass-through of v1's writer/reader/upstream/
  downstream lists.
"""

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
    ExecutionContext,
    ExecutionContextType,
    Relationship,
    RelationshipType,
    TraceResult,
    WriteBehaviorType,
)
from app.services.trace_v2_service import (  # noqa: E402
    LadderConditionExtraction,
    extract_simple_ladder_conditions,
    humanize_instruction_type,
    trace_object_v2,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _tag(tag_id: str, name: str) -> ControlObject:
    return ControlObject(
        id=tag_id,
        name=name,
        object_type=ControlObjectType.TAG,
        source_platform="rockwell",
        confidence=ConfidenceLevel.HIGH,
    )


def _rung(rung_id: str, routine: str, rung_number: int) -> ControlObject:
    return ControlObject(
        id=rung_id,
        name=f"Rung[{rung_number}]",
        object_type=ControlObjectType.RUNG,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}"
            f"/Rung[{rung_number}]"
        ),
        confidence=ConfidenceLevel.HIGH,
    )


def _routine(routine_id: str, name: str, language: str = "ladder") -> ControlObject:
    return ControlObject(
        id=routine_id,
        name=name,
        object_type=ControlObjectType.ROUTINE,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{name}"
        ),
        attributes={"language": language},
        confidence=ConfidenceLevel.HIGH,
    )


def _writes(
    rung_id: str,
    tag_id: str,
    routine: str,
    rung_number: int,
    instruction_type: str,
    write_behavior: WriteBehaviorType | None = None,
    rel_type: RelationshipType = RelationshipType.WRITES,
    logic_condition: str | None = None,
) -> Relationship:
    return Relationship(
        source_id=rung_id,
        target_id=tag_id,
        relationship_type=rel_type,
        write_behavior=write_behavior,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}"
            f"/Rung[{rung_number}]"
        ),
        logic_condition=logic_condition,
        platform_specific={"instruction_type": instruction_type},
        confidence=ConfidenceLevel.HIGH,
    )


def _reads(
    rung_id: str,
    tag_id: str,
    routine: str,
    rung_number: int,
    examined: bool,
) -> Relationship:
    itype = "XIC" if examined else "XIO"
    return Relationship(
        source_id=rung_id,
        target_id=tag_id,
        relationship_type=RelationshipType.READS,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}"
            f"/Rung[{rung_number}]"
        ),
        platform_specific={
            "instruction_type": itype,
            "examined_value": examined,
        },
        confidence=ConfidenceLevel.HIGH,
    )


# ---------------------------------------------------------------------------
# Part 1 -- humanize_instruction_type
# ---------------------------------------------------------------------------


class HumanizeInstructionTypeTests(unittest.TestCase):
    def test_generic_phrases_match_spec(self) -> None:
        cases = {
            "XIC": "requires the tag to be TRUE",
            "XIO": "requires the tag to be FALSE",
            "OTE": "turns the output ON while the rung is true",
            "OTL": "latches the output ON",
            "OTU": "unlatches or resets the output OFF",
            "TON": "runs an on-delay timer",
            "TOF": "runs an off-delay timer",
            "RTO": "runs a retentive timer",
            "CTU": "counts up",
            "CTD": "counts down",
            "RES": "resets the target",
            "JSR": "calls another routine",
        }
        for itype, expected in cases.items():
            with self.subTest(itype=itype):
                self.assertEqual(humanize_instruction_type(itype), expected)

    def test_with_target_personalizes_the_phrase(self) -> None:
        self.assertEqual(
            humanize_instruction_type("OTL", "State_Fill"),
            "latches State_Fill ON",
        )
        self.assertEqual(
            humanize_instruction_type("XIO", "Faults_Any"),
            "requires Faults_Any to be FALSE",
        )
        self.assertEqual(
            humanize_instruction_type("RES", "Tank_Timer"),
            "resets Tank_Timer",
        )

    def test_unknown_instruction_falls_back(self) -> None:
        self.assertEqual(
            humanize_instruction_type("FOO"), "executes FOO"
        )
        self.assertEqual(
            humanize_instruction_type("FOO", "X"), "executes FOO on X"
        )

    def test_case_insensitive(self) -> None:
        self.assertEqual(
            humanize_instruction_type("otl", "X"), "latches X ON"
        )

    def test_empty_or_none_returns_empty(self) -> None:
        self.assertEqual(humanize_instruction_type(None), "")
        self.assertEqual(humanize_instruction_type(""), "")


# ---------------------------------------------------------------------------
# Part 2 -- extract_simple_ladder_conditions
# ---------------------------------------------------------------------------


class LadderExtractionTests(unittest.TestCase):
    def test_spec_example_extracts_three_conditions(self) -> None:
        result = extract_simple_ladder_conditions(
            "XIC(StartPB_OS) XIC(AutoMode) XIO(Faults_Any) OTL(State_Fill)"
        )
        self.assertEqual(len(result), 3)
        self.assertEqual(
            [(c.tag, c.required_value, c.instruction_type) for c in result],
            [
                ("StartPB_OS", True, "XIC"),
                ("AutoMode", True, "XIC"),
                ("Faults_Any", False, "XIO"),
            ],
        )
        self.assertEqual(
            [c.natural_language for c in result],
            [
                "StartPB_OS must be TRUE",
                "AutoMode must be TRUE",
                "Faults_Any must be FALSE",
            ],
        )

    def test_output_only_instructions_are_ignored(self) -> None:
        # No XIC/XIO -> no conditions extracted.
        result = extract_simple_ladder_conditions("OTE(Motor_Run)")
        self.assertEqual(result, [])

    def test_dotted_and_bracketed_tags_preserved(self) -> None:
        result = extract_simple_ladder_conditions(
            "XIC(Pump_01.Run) XIO(Bits[3])"
        )
        self.assertEqual([c.tag for c in result], ["Pump_01.Run", "Bits[3]"])

    def test_case_insensitive_instruction_match(self) -> None:
        result = extract_simple_ladder_conditions("xic(A) Xio(B)")
        self.assertEqual([c.instruction_type for c in result], ["XIC", "XIO"])

    def test_empty_or_none_returns_empty_list(self) -> None:
        self.assertEqual(extract_simple_ladder_conditions(""), [])
        self.assertEqual(extract_simple_ladder_conditions(None), [])

    def test_returns_dataclass_instances(self) -> None:
        # Helps API consumers rely on attribute access.
        result = extract_simple_ladder_conditions("XIC(Foo)")
        self.assertIsInstance(result[0], LadderConditionExtraction)


# ---------------------------------------------------------------------------
# Part 3 -- trace_object_v2 end-to-end
# ---------------------------------------------------------------------------


class TraceObjectV2LadderTests(unittest.TestCase):
    """End-to-end ladder trace + natural-language conclusion shape."""

    def setUp(self) -> None:
        # Mini graph: R02_Sequencer/Rung[1] latches State_Fill when
        # StartPB_OS, AutoMode, NOT Faults_Any.
        self.routine = _routine(
            "routine::PLC01/MainProgram/R02_Sequencer", "R02_Sequencer"
        )
        self.rung1 = _rung(
            "rung::PLC01/MainProgram/R02_Sequencer/Rung[1]",
            "R02_Sequencer",
            1,
        )
        self.state_fill = _tag(
            "tag::PLC01/MainProgram/State_Fill", "State_Fill"
        )
        self.start_pb = _tag(
            "tag::PLC01/MainProgram/StartPB_OS", "StartPB_OS"
        )
        self.auto_mode = _tag(
            "tag::PLC01/MainProgram/AutoMode", "AutoMode"
        )
        self.faults_any = _tag(
            "tag::PLC01/MainProgram/Faults_Any", "Faults_Any"
        )

        self.objects = [
            self.routine,
            self.rung1,
            self.state_fill,
            self.start_pb,
            self.auto_mode,
            self.faults_any,
        ]
        self.relationships = [
            _reads(self.rung1.id, self.start_pb.id, "R02_Sequencer", 1, True),
            _reads(self.rung1.id, self.auto_mode.id, "R02_Sequencer", 1, True),
            _reads(self.rung1.id, self.faults_any.id, "R02_Sequencer", 1, False),
            _writes(
                self.rung1.id,
                self.state_fill.id,
                "R02_Sequencer",
                1,
                "OTL",
                write_behavior=WriteBehaviorType.LATCHES,
                logic_condition=(
                    "XIC(StartPB_OS) XIC(AutoMode) XIO(Faults_Any) "
                    "OTL(State_Fill)"
                ),
            ),
        ]

    def _trace(self) -> TraceResult:
        return trace_object_v2(
            target_object_id=self.state_fill.id,
            control_objects=self.objects,
            relationships=self.relationships,
            execution_contexts=[],
        )

    def test_returns_trace_result_with_v2_marker(self) -> None:
        result = self._trace()
        self.assertIsInstance(result, TraceResult)
        self.assertEqual(result.target_object_id, self.state_fill.id)
        self.assertEqual(result.platform_specific.get("trace_version"), "v2")
        self.assertGreaterEqual(
            result.platform_specific.get("natural_conclusion_count", 0), 2
        )

    def test_writer_what_conclusion_says_latched_on(self) -> None:
        result = self._trace()
        statements = [c.statement for c in result.conclusions]
        self.assertTrue(
            any(
                s == "State_Fill is latched ON in R02_Sequencer/Rung[1]."
                for s in statements
            ),
            f"Expected latched-on conclusion. Got: {statements}",
        )

    def test_writer_conditions_conclusion_lists_gating_tags(self) -> None:
        result = self._trace()
        statements = [c.statement for c in result.conclusions]
        expected = (
            "State_Fill is latched ON in R02_Sequencer/Rung[1] when "
            "StartPB_OS is TRUE, AutoMode is TRUE, and "
            "Faults_Any is FALSE."
        )
        self.assertIn(expected, statements)

    def test_conditions_metadata_attached(self) -> None:
        result = self._trace()
        conditions_concl = next(
            c
            for c in result.conclusions
            if c.platform_specific.get("trace_v2_kind") == "writer_conditions"
        )
        self.assertEqual(
            conditions_concl.platform_specific["instruction_type"], "OTL"
        )
        self.assertEqual(
            conditions_concl.platform_specific["location"],
            "R02_Sequencer/Rung[1]",
        )
        cond_rows = conditions_concl.platform_specific["conditions"]
        self.assertEqual(len(cond_rows), 3)
        self.assertEqual(
            {c["tag"]: c["required_value"] for c in cond_rows},
            {
                "StartPB_OS": True,
                "AutoMode": True,
                "Faults_Any": False,
            },
        )

    def test_v1_conclusions_are_preserved_after_v2_natural_ones(self) -> None:
        # We promise v1 conclusions still appear; just after v2.
        result = self._trace()
        kinds = [
            c.platform_specific.get("trace_v2_kind") for c in result.conclusions
        ]
        # First two entries are v2; later entries lack the v2 kind marker.
        self.assertEqual(kinds[0], "writer_what")
        self.assertEqual(kinds[1], "writer_conditions")
        self.assertTrue(
            any(k is None for k in kinds[2:]),
            "Expected at least one passthrough v1 conclusion.",
        )

    def test_summary_includes_natural_language(self) -> None:
        result = self._trace()
        self.assertIsNotNone(result.summary)
        assert result.summary is not None
        self.assertIn(
            "State_Fill is latched ON in R02_Sequencer/Rung[1].",
            result.summary,
        )

    def test_writer_and_reader_lists_pass_through_from_v1(self) -> None:
        result = self._trace()
        self.assertEqual(len(result.writer_relationships), 1)
        # The 3 XIC/XIO reads target three different tags; State_Fill
        # itself isn't read here, so reader_relationships for it is 0.
        self.assertEqual(len(result.reader_relationships), 0)


class TraceObjectV2OTEAndOTUTests(unittest.TestCase):
    """Verify per-instruction phrasing for OTE / OTU writers."""

    def _build(self, instruction: str, behavior: WriteBehaviorType):
        routine = _routine(
            "routine::PLC01/MainProgram/MotorRoutine", "MotorRoutine"
        )
        rung = _rung(
            "rung::PLC01/MainProgram/MotorRoutine/Rung[3]",
            "MotorRoutine",
            3,
        )
        motor = _tag("tag::PLC01/MainProgram/Motor_Run", "Motor_Run")
        objs = [routine, rung, motor]
        rels = [
            _writes(
                rung.id,
                motor.id,
                "MotorRoutine",
                3,
                instruction,
                write_behavior=behavior,
            )
        ]
        return objs, rels, motor.id

    def test_ote_says_energized(self) -> None:
        objs, rels, target = self._build("OTE", WriteBehaviorType.SETS_TRUE)
        result = trace_object_v2(target, objs, rels, [])
        statements = [c.statement for c in result.conclusions]
        self.assertIn(
            "Motor_Run is energized while the rung is TRUE in "
            "MotorRoutine/Rung[3].",
            statements,
        )

    def test_otu_says_unlatched_reset(self) -> None:
        objs, rels, target = self._build("OTU", WriteBehaviorType.UNLATCHES)
        result = trace_object_v2(target, objs, rels, [])
        statements = [c.statement for c in result.conclusions]
        self.assertIn(
            "Motor_Run is unlatched / reset OFF in MotorRoutine/Rung[3].",
            statements,
        )


class TraceObjectV2MultipleWritersTests(unittest.TestCase):
    """Multiple writers (latch + unlatch) on the same target."""

    def test_each_writer_gets_its_own_what_conclusion(self) -> None:
        routine_seq = _routine(
            "routine::PLC01/MainProgram/R02_Sequencer", "R02_Sequencer"
        )
        routine_flt = _routine(
            "routine::PLC01/MainProgram/FaultRoutine", "FaultRoutine"
        )
        rung_seq = _rung(
            "rung::PLC01/MainProgram/R02_Sequencer/Rung[1]",
            "R02_Sequencer", 1,
        )
        rung_flt = _rung(
            "rung::PLC01/MainProgram/FaultRoutine/Rung[4]",
            "FaultRoutine", 4,
        )
        state = _tag("tag::PLC01/MainProgram/State_Fill", "State_Fill")
        objs = [routine_seq, routine_flt, rung_seq, rung_flt, state]
        rels = [
            _writes(
                rung_seq.id, state.id, "R02_Sequencer", 1, "OTL",
                write_behavior=WriteBehaviorType.LATCHES,
            ),
            _writes(
                rung_flt.id, state.id, "FaultRoutine", 4, "OTU",
                write_behavior=WriteBehaviorType.UNLATCHES,
            ),
        ]
        result = trace_object_v2(state.id, objs, rels, [])
        statements = [c.statement for c in result.conclusions]
        self.assertIn(
            "State_Fill is latched ON in R02_Sequencer/Rung[1].",
            statements,
        )
        self.assertIn(
            "State_Fill is unlatched / reset OFF in FaultRoutine/Rung[4].",
            statements,
        )


class TraceObjectV2FallbackToLogicConditionTests(unittest.TestCase):
    """When there are no READS edges from the same rung, v2 should
    still extract conditions from ``logic_condition`` via the regex."""

    def test_conditions_from_logic_condition_when_no_reads_edges(self) -> None:
        routine = _routine(
            "routine::PLC01/MainProgram/R02_Sequencer", "R02_Sequencer"
        )
        rung = _rung(
            "rung::PLC01/MainProgram/R02_Sequencer/Rung[1]",
            "R02_Sequencer", 1,
        )
        state = _tag("tag::PLC01/MainProgram/State_Fill", "State_Fill")
        objs = [routine, rung, state]
        rels = [
            _writes(
                rung.id, state.id, "R02_Sequencer", 1, "OTL",
                write_behavior=WriteBehaviorType.LATCHES,
                logic_condition=(
                    "XIC(StartPB_OS) XIO(Faults_Any) OTL(State_Fill)"
                ),
            ),
        ]
        result = trace_object_v2(state.id, objs, rels, [])
        statements = [c.statement for c in result.conclusions]
        self.assertIn(
            "State_Fill is latched ON in R02_Sequencer/Rung[1] when "
            "StartPB_OS is TRUE and Faults_Any is FALSE.",
            statements,
        )


# NOTE: We don't end-to-end test JSR/CALLS through ``trace_object_v2``
# here. Trace v1's ``WRITER_RELATIONSHIP_TYPES`` does NOT include
# ``CALLS`` (a JSR isn't a "write" in v1's vocabulary), so a routine
# that is the target of a CALLS edge has zero writer_relationships
# and v2's writer-WHAT conclusion path never fires for it. The JSR
# phrase tables still apply if v2 grows a dedicated CALLS path later;
# ``humanize_instruction_type("JSR", ...)`` is already covered by the
# HumanizeInstructionTypeTests above.


class TraceObjectV2StructuredTextTests(unittest.TestCase):
    """The ST normalization isn't wired yet, but v2 must still produce
    a sane natural-language conclusion when an ST writer appears."""

    def _build_st_writer(
        self, raw_text: str, logic_condition: str | None
    ) -> tuple[list[ControlObject], list[Relationship], str]:
        routine = _routine(
            "routine::PLC01/MainProgram/STRoutine", "STRoutine",
            language="structured_text",
        )
        # The "source" of the WRITES is an INSTRUCTION whose language
        # attribute marks it as ST, simulating the upcoming normalizer.
        instr = ControlObject(
            id="instr::PLC01/MainProgram/STRoutine/Assign1",
            name="ASSIGN",
            object_type=ControlObjectType.INSTRUCTION,
            source_platform="rockwell",
            source_location=(
                "Controller:PLC01/Program:MainProgram/Routine:STRoutine"
                "/Instr:Assign1"
            ),
            attributes={"language": "structured_text"},
            platform_specific={"raw_text": raw_text},
            confidence=ConfidenceLevel.HIGH,
        )
        motor = _tag("tag::PLC01/MainProgram/Motor_Run", "Motor_Run")
        objs = [routine, instr, motor]
        rels = [
            Relationship(
                source_id=instr.id,
                target_id=motor.id,
                relationship_type=RelationshipType.WRITES,
                source_platform="rockwell",
                source_location=instr.source_location,
                logic_condition=logic_condition,
                platform_specific={"instruction_type": "ASSIGN"},
                confidence=ConfidenceLevel.HIGH,
            )
        ]
        return objs, rels, motor.id

    def test_supported_assignment_renders_natural_language(self) -> None:
        objs, rels, target = self._build_st_writer(
            raw_text="Motor_Run := StartPB AND AutoMode AND NOT Faulted;",
            logic_condition="Motor_Run := StartPB AND AutoMode AND NOT Faulted;",
        )
        result = trace_object_v2(target, objs, rels, [])
        statements = [c.statement for c in result.conclusions]
        self.assertIn(
            "Motor_Run is assigned TRUE when StartPB is TRUE, "
            "AutoMode is TRUE, and Faulted is FALSE.",
            statements,
        )

    def test_unsupported_expression_emits_canonical_too_complex(self) -> None:
        objs, rels, target = self._build_st_writer(
            raw_text="Motor_Run := A OR (B AND NOT C);",
            logic_condition="Motor_Run := A OR (B AND NOT C);",
        )
        result = trace_object_v2(target, objs, rels, [])
        statements = [c.statement for c in result.conclusions]
        self.assertIn(
            "Structured Text logic was detected, but this expression is "
            "too complex for deterministic Trace v2 extraction.",
            statements,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
