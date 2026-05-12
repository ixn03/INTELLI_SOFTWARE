"""Tests for Structured Text parsing + normalization.

Covers two layers:

* :mod:`app.parsers.structured_text_blocks` -- block decomposition
  (assignment / IF / IF-ELSE / simple CASE / too-complex).
* :mod:`app.services.normalization_service` -- ST routines produce
  WRITES + READS relationships in the reasoning graph, with
  ``platform_specific`` populated per spec.

A small Trace v2 integration test also verifies that the new ST
WRITES are picked up by ``trace_object_v2``'s ST writer path, so an
``IF cond THEN target := TRUE; END_IF`` block renders a natural-
language conclusion downstream.

Plus a regression test that confirms ladder normalization still
emits the same edges after the ST plumbing landed.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.control_model import (  # noqa: E402
    ControlController,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.models.reasoning import (  # noqa: E402
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)
from app.parsers.structured_text import (  # noqa: E402
    parse_structured_text,
)
from app.parsers.structured_text_blocks import (  # noqa: E402
    STAssignment,
    STCaseBlock,
    STComplexBlock,
    STIfBlock,
    parse_structured_text_blocks,
)
from app.services.normalization_service import (  # noqa: E402
    normalize_l5x_project,
)
from app.services.trace_v2_service import trace_object_v2  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_project_with_st_routine(
    routine_name: str,
    raw_logic: str,
    tag_names: list[str],
) -> ControlProject:
    """Build a minimal one-controller / one-program project carrying
    a single Structured Text routine.

    Tags are pre-declared in the program scope so the normalizer
    resolves them to concrete ``ControlObject``s rather than stubs.
    The routine's ``instructions`` list is populated via the legacy
    ST parser so the normalizer's per-instruction CONTAINS pass also
    has something to walk -- this proves both surfaces coexist.
    """

    routine = ControlRoutine(
        name=routine_name,
        language="structured_text",
        instructions=parse_structured_text(raw_logic, routine_name),
        raw_logic=raw_logic,
        metadata={"rockwell_type": "ST"},
    )
    program = ControlProgram(
        name="MainProgram",
        tags=[
            ControlTag(
                name=n,
                data_type="BOOL",
                scope="MainProgram",
                platform_source="rockwell_l5x",
            )
            for n in tag_names
        ],
        routines=[routine],
    )
    controller = ControlController(
        name="PLC01",
        platform="rockwell",
        controller_tags=[],
        programs=[program],
    )
    return ControlProject(
        project_name="PLC01",
        source_file="st_test.L5X",
        file_hash="st-test-hash",
        controllers=[controller],
    )


def _tag_id(name: str) -> str:
    return f"tag::PLC01/MainProgram/{name}"


def _statement_id(routine: str, idx: int) -> str:
    return f"stmt::PLC01/MainProgram/{routine}/Statement[{idx}]"


def _find_writes(
    relationships: list[Relationship],
    *,
    target_id: str,
) -> list[Relationship]:
    return [
        r
        for r in relationships
        if r.target_id == target_id
        and r.relationship_type == RelationshipType.WRITES
    ]


def _find_reads(
    relationships: list[Relationship],
    *,
    target_id: str,
) -> list[Relationship]:
    return [
        r
        for r in relationships
        if r.target_id == target_id
        and r.relationship_type == RelationshipType.READS
    ]


def _find_by_id(
    objects: list[ControlObject], obj_id: str
) -> ControlObject | None:
    for o in objects:
        if o.id == obj_id:
            return o
    return None


# ===========================================================================
# Part 1 -- Block parser
# ===========================================================================


class STBlockParserTests(unittest.TestCase):
    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(parse_structured_text_blocks(""), [])
        self.assertEqual(parse_structured_text_blocks(None), [])
        self.assertEqual(parse_structured_text_blocks("   \n\t  "), [])

    def test_literal_true_assignment(self) -> None:
        blocks = parse_structured_text_blocks("Motor_Run := TRUE;")
        self.assertEqual(len(blocks), 1)
        b = blocks[0]
        self.assertIsInstance(b, STAssignment)
        assert isinstance(b, STAssignment)
        self.assertEqual(b.target, "Motor_Run")
        self.assertIs(b.assigned_value, True)
        self.assertFalse(b.too_complex)
        self.assertEqual(b.conditions, [])

    def test_literal_false_assignment(self) -> None:
        blocks = parse_structured_text_blocks("Motor_Run := FALSE;")
        b = blocks[0]
        assert isinstance(b, STAssignment)
        self.assertIs(b.assigned_value, False)

    def test_boolean_conjunction_assignment(self) -> None:
        blocks = parse_structured_text_blocks(
            "Motor_Run := StartPB AND AutoMode AND NOT Faulted;"
        )
        b = blocks[0]
        assert isinstance(b, STAssignment)
        self.assertEqual(b.target, "Motor_Run")
        self.assertIsNone(b.assigned_value)
        self.assertFalse(b.too_complex)
        self.assertEqual(
            [(c.tag, c.required_value) for c in b.conditions],
            [
                ("StartPB", True),
                ("AutoMode", True),
                ("Faulted", False),
            ],
        )

    def test_simple_if_block_extracts_condition_and_body(self) -> None:
        blocks = parse_structured_text_blocks(
            "IF StartPB AND AutoMode AND NOT Faulted THEN\n"
            "    Motor_Run := TRUE;\n"
            "END_IF;"
        )
        self.assertEqual(len(blocks), 1)
        b = blocks[0]
        assert isinstance(b, STIfBlock)
        self.assertFalse(b.too_complex_condition)
        self.assertEqual(
            [(c.tag, c.required_value) for c in b.condition_terms],
            [("StartPB", True), ("AutoMode", True), ("Faulted", False)],
        )
        self.assertEqual(len(b.then_assignments), 1)
        self.assertIs(b.then_assignments[0].assigned_value, True)
        self.assertEqual(b.then_assignments[0].target, "Motor_Run")
        self.assertEqual(b.else_assignments, [])

    def test_if_else_block_single_tag_condition_inverts_for_else(
        self,
    ) -> None:
        blocks = parse_structured_text_blocks(
            "IF Faulted THEN\n"
            "    Motor_Run := FALSE;\n"
            "ELSE\n"
            "    Motor_Run := TRUE;\n"
            "END_IF;"
        )
        b = blocks[0]
        assert isinstance(b, STIfBlock)
        # Single-tag THEN condition => ELSE invertible, not too complex.
        self.assertFalse(b.else_too_complex)
        self.assertEqual(len(b.then_assignments), 1)
        self.assertIs(b.then_assignments[0].assigned_value, False)
        self.assertEqual(len(b.else_assignments), 1)
        self.assertIs(b.else_assignments[0].assigned_value, True)

    def test_if_else_block_multi_tag_condition_marks_else_too_complex(
        self,
    ) -> None:
        # NOT(A AND B) = NOT A OR NOT B, which is a disjunction and
        # therefore out of scope: ELSE branch's gating reads must be
        # flagged as too complex even though THEN is fine.
        blocks = parse_structured_text_blocks(
            "IF A AND B THEN\n"
            "    X := TRUE;\n"
            "ELSE\n"
            "    X := FALSE;\n"
            "END_IF;"
        )
        b = blocks[0]
        assert isinstance(b, STIfBlock)
        self.assertFalse(b.too_complex_condition)
        self.assertTrue(b.else_too_complex)

    def test_simple_case_block_extracts_branches(self) -> None:
        blocks = parse_structured_text_blocks(
            "CASE State OF\n"
            "    1: Motor_Run := TRUE;\n"
            "    2: Motor_Run := FALSE;\n"
            "END_CASE;"
        )
        b = blocks[0]
        assert isinstance(b, STCaseBlock)
        self.assertEqual(b.selector_tag, "State")
        self.assertFalse(b.too_complex_selector)
        self.assertEqual(len(b.branches), 2)
        self.assertEqual(b.branches[0].label, "1")
        self.assertEqual(b.branches[1].label, "2")
        self.assertEqual(
            [a.target for a in b.branches[0].assignments], ["Motor_Run"]
        )
        self.assertIs(b.branches[0].assignments[0].assigned_value, True)
        self.assertIs(b.branches[1].assignments[0].assigned_value, False)

    def test_case_block_with_else_default_branch(self) -> None:
        blocks = parse_structured_text_blocks(
            "CASE State OF\n"
            "    1: X := TRUE;\n"
            "    ELSE: X := FALSE;\n"
            "END_CASE;"
        )
        b = blocks[0]
        assert isinstance(b, STCaseBlock)
        labels = [(br.label, br.is_default) for br in b.branches]
        self.assertEqual(labels, [("1", False), ("ELSE", True)])

    def test_too_complex_statement_does_not_crash(self) -> None:
        # FOR loop is outside the supported envelope. We must
        # produce *some* block (so the normalizer can flag it) and
        # not raise, even on weird input.
        blocks = parse_structured_text_blocks(
            "FOR i := 0 TO 10 DO\n"
            "    Counter := Counter + 1;\n"
            "END_FOR;"
        )
        # At least one of the emitted blocks should be too-complex.
        too_complex_seen = any(
            isinstance(b, STComplexBlock)
            or (isinstance(b, STAssignment) and b.too_complex)
            for b in blocks
        )
        self.assertTrue(
            too_complex_seen,
            f"Expected a too-complex marker; got {blocks!r}",
        )

    def test_mixed_sequence_of_blocks(self) -> None:
        blocks = parse_structured_text_blocks(
            "Pump := TRUE;\n"
            "IF Faulted THEN Pump := FALSE; END_IF;\n"
            "CASE State OF 1: X := TRUE; END_CASE;"
        )
        kinds = [type(b).__name__ for b in blocks]
        self.assertEqual(
            kinds, ["STAssignment", "STIfBlock", "STCaseBlock"]
        )

    def test_statement_index_is_assigned_in_order(self) -> None:
        blocks = parse_structured_text_blocks(
            "A := TRUE;\n"
            "B := FALSE;\n"
            "C := TRUE;"
        )
        self.assertEqual([b.statement_index for b in blocks], [0, 1, 2])


# ===========================================================================
# Part 2 -- Normalization service integration
# ===========================================================================


class STNormalizationAssignmentTests(unittest.TestCase):
    """ST assignment creates WRITES to target and READS for cond tags."""

    def setUp(self) -> None:
        self.project = _make_project_with_st_routine(
            routine_name="MainRoutine",
            raw_logic=(
                "Motor_Run := StartPB AND AutoMode AND NOT Faulted;"
            ),
            tag_names=["Motor_Run", "StartPB", "AutoMode", "Faulted"],
        )
        out = normalize_l5x_project(self.project)
        self.objects = out["control_objects"]
        self.relationships = out["relationships"]

    def test_st_statement_control_object_is_emitted(self) -> None:
        stmt = _find_by_id(self.objects, _statement_id("MainRoutine", 0))
        self.assertIsNotNone(stmt)
        assert stmt is not None
        self.assertEqual(stmt.name, "Statement[0]")
        self.assertEqual(stmt.object_type, ControlObjectType.INSTRUCTION)
        self.assertEqual(stmt.attributes.get("language"), "structured_text")
        self.assertEqual(stmt.attributes.get("statement_type"), "assignment")
        self.assertEqual(
            stmt.platform_specific.get("st_parse_status"), "ok"
        )
        self.assertIn(
            "Motor_Run := StartPB", stmt.platform_specific.get("raw_text", "")
        )

    def test_writes_edge_targets_motor_run(self) -> None:
        writes = _find_writes(
            self.relationships, target_id=_tag_id("Motor_Run")
        )
        self.assertEqual(len(writes), 1)
        w = writes[0]
        self.assertEqual(w.source_id, _statement_id("MainRoutine", 0))
        # Boolean expression -> no literal write_behavior, raw expr
        # surfaces via platform_specific.assigned_value.
        self.assertIsNone(w.write_behavior)
        self.assertEqual(
            w.platform_specific.get("assigned_value"),
            "(boolean expression)",
        )
        self.assertEqual(
            w.platform_specific.get("language"), "structured_text"
        )
        self.assertEqual(
            w.platform_specific.get("statement_type"), "assignment"
        )
        extracted = w.platform_specific.get("extracted_conditions") or []
        self.assertEqual(len(extracted), 3)

    def test_reads_edges_for_each_condition_tag(self) -> None:
        for tag, examined in (
            ("StartPB", True),
            ("AutoMode", True),
            ("Faulted", False),
        ):
            with self.subTest(tag=tag):
                reads = _find_reads(
                    self.relationships, target_id=_tag_id(tag)
                )
                # Exactly one READS edge per condition tag.
                self.assertEqual(len(reads), 1)
                r = reads[0]
                self.assertEqual(
                    r.source_id, _statement_id("MainRoutine", 0)
                )
                self.assertEqual(
                    r.platform_specific.get("examined_value"), examined
                )
                # Mirror ladder XIC/XIO for downstream consumers.
                self.assertEqual(
                    r.platform_specific.get("instruction_type"),
                    "XIC" if examined else "XIO",
                )

    def test_literal_true_assignment_sets_write_behavior_sets_true(
        self,
    ) -> None:
        # Re-normalize a simpler routine to isolate write_behavior.
        project = _make_project_with_st_routine(
            routine_name="MainRoutine",
            raw_logic="Motor_Run := TRUE;",
            tag_names=["Motor_Run"],
        )
        rels = normalize_l5x_project(project)["relationships"]
        writes = _find_writes(rels, target_id=_tag_id("Motor_Run"))
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].write_behavior, WriteBehaviorType.SETS_TRUE)
        self.assertEqual(
            writes[0].platform_specific.get("assigned_value"), "TRUE"
        )

    def test_literal_false_assignment_sets_write_behavior_sets_false(
        self,
    ) -> None:
        project = _make_project_with_st_routine(
            routine_name="MainRoutine",
            raw_logic="Motor_Run := FALSE;",
            tag_names=["Motor_Run"],
        )
        rels = normalize_l5x_project(project)["relationships"]
        writes = _find_writes(rels, target_id=_tag_id("Motor_Run"))
        self.assertEqual(writes[0].write_behavior, WriteBehaviorType.SETS_FALSE)
        self.assertEqual(
            writes[0].platform_specific.get("assigned_value"), "FALSE"
        )


class STNormalizationIfBlockTests(unittest.TestCase):
    """IF block creates WRITES + READS rooted at the same statement."""

    def setUp(self) -> None:
        self.project = _make_project_with_st_routine(
            routine_name="MainRoutine",
            raw_logic=(
                "IF StartPB AND AutoMode AND NOT Faulted THEN\n"
                "    Motor_Run := TRUE;\n"
                "END_IF;"
            ),
            tag_names=[
                "Motor_Run", "StartPB", "AutoMode", "Faulted",
            ],
        )
        out = normalize_l5x_project(self.project)
        self.objects = out["control_objects"]
        self.relationships = out["relationships"]

    def test_statement_type_is_if(self) -> None:
        stmt = _find_by_id(self.objects, _statement_id("MainRoutine", 0))
        assert stmt is not None
        self.assertEqual(stmt.attributes.get("statement_type"), "if")

    def test_writes_carries_set_true_and_branch_label(self) -> None:
        writes = _find_writes(
            self.relationships, target_id=_tag_id("Motor_Run")
        )
        self.assertEqual(len(writes), 1)
        w = writes[0]
        self.assertEqual(w.write_behavior, WriteBehaviorType.SETS_TRUE)
        self.assertEqual(w.platform_specific.get("branch_label"), "THEN")
        # Extracted conditions include the 3 IF-gating tags.
        extracted = w.platform_specific.get("extracted_conditions") or []
        self.assertEqual(
            {(c["tag"], c["required_value"]) for c in extracted},
            {
                ("StartPB", True),
                ("AutoMode", True),
                ("Faulted", False),
            },
        )

    def test_reads_edges_for_each_if_condition_tag(self) -> None:
        for tag, examined in (
            ("StartPB", True),
            ("AutoMode", True),
            ("Faulted", False),
        ):
            with self.subTest(tag=tag):
                reads = _find_reads(
                    self.relationships, target_id=_tag_id(tag)
                )
                self.assertEqual(len(reads), 1)
                self.assertEqual(
                    reads[0].platform_specific.get("examined_value"),
                    examined,
                )
                self.assertEqual(
                    reads[0].platform_specific.get("condition_source"),
                    "if_condition",
                )

    def test_not_condition_becomes_required_false_for_trace_v2(
        self,
    ) -> None:
        # End-to-end with Trace v2: the NOT Faulted condition must
        # surface as "Faulted is FALSE" in the natural-language
        # conditions conclusion.
        out = normalize_l5x_project(self.project)
        result = trace_object_v2(
            target_object_id=_tag_id("Motor_Run"),
            control_objects=out["control_objects"],
            relationships=out["relationships"],
            execution_contexts=out["execution_contexts"],
        )
        # Confidence on the writer is HIGH (parse_status="ok").
        self.assertEqual(len(result.writer_relationships), 1)
        statements = " | ".join(c.statement for c in result.conclusions)
        self.assertIn("Faulted is FALSE", statements)
        self.assertIn("StartPB is TRUE", statements)
        # Location label uses the new Statement[N] shorthand.
        locations = {
            c.platform_specific.get("location")
            for c in result.conclusions
            if c.platform_specific
        }
        self.assertIn("MainRoutine/Statement[0]", locations)


class STNormalizationIfElseTests(unittest.TestCase):
    """IF/ELSE: ELSE branch inverts a single-tag THEN condition."""

    def test_else_branch_inverts_single_tag_condition(self) -> None:
        project = _make_project_with_st_routine(
            routine_name="MainRoutine",
            raw_logic=(
                "IF Faulted THEN\n"
                "    Motor_Run := FALSE;\n"
                "ELSE\n"
                "    Motor_Run := TRUE;\n"
                "END_IF;"
            ),
            tag_names=["Motor_Run", "Faulted"],
        )
        out = normalize_l5x_project(project)
        rels = out["relationships"]

        writes = _find_writes(rels, target_id=_tag_id("Motor_Run"))
        self.assertEqual(len(writes), 2)
        by_branch = {
            w.platform_specific["branch_label"]: w for w in writes
        }
        self.assertEqual(set(by_branch), {"THEN", "ELSE"})
        self.assertEqual(
            by_branch["THEN"].write_behavior, WriteBehaviorType.SETS_FALSE
        )
        self.assertEqual(
            by_branch["ELSE"].write_behavior, WriteBehaviorType.SETS_TRUE
        )

        # Faulted is read twice -- once with examined_value=True (THEN
        # branch gating) and once with examined_value=False (ELSE
        # branch gating, the inverted condition).
        faulted_reads = _find_reads(rels, target_id=_tag_id("Faulted"))
        examined_values = sorted(
            r.platform_specific.get("examined_value") for r in faulted_reads
        )
        self.assertEqual(examined_values, [False, True])


class STNormalizationCaseTests(unittest.TestCase):
    """Simple CASE block emits per-branch WRITES + a selector READS."""

    def test_case_emits_writes_per_branch_and_selector_reads(self) -> None:
        project = _make_project_with_st_routine(
            routine_name="MainRoutine",
            raw_logic=(
                "CASE State OF\n"
                "    1: Motor_Run := TRUE;\n"
                "    2: Motor_Run := FALSE;\n"
                "END_CASE;"
            ),
            tag_names=["Motor_Run", "State"],
        )
        out = normalize_l5x_project(project)
        rels = out["relationships"]
        objs = out["control_objects"]

        stmt = _find_by_id(objs, _statement_id("MainRoutine", 0))
        assert stmt is not None
        self.assertEqual(stmt.attributes.get("statement_type"), "case")

        writes = _find_writes(rels, target_id=_tag_id("Motor_Run"))
        self.assertEqual(len(writes), 2)
        labels = {w.platform_specific["branch_label"] for w in writes}
        self.assertEqual(labels, {"1", "2"})
        behaviors = {
            w.platform_specific["branch_label"]: w.write_behavior
            for w in writes
        }
        self.assertEqual(behaviors["1"], WriteBehaviorType.SETS_TRUE)
        self.assertEqual(behaviors["2"], WriteBehaviorType.SETS_FALSE)

        # The selector tag is read once per branch (so each WRITES has
        # a sibling READS rooted at the same statement).
        state_reads = _find_reads(rels, target_id=_tag_id("State"))
        self.assertEqual(len(state_reads), 2)
        for r in state_reads:
            self.assertEqual(
                r.platform_specific.get("condition_source"),
                "case_selector",
            )


class STNormalizationTooComplexTests(unittest.TestCase):
    """Complex ST does not crash and is marked too_complex."""

    def test_for_loop_produces_too_complex_statement_object(self) -> None:
        project = _make_project_with_st_routine(
            routine_name="MainRoutine",
            raw_logic=(
                "FOR i := 0 TO 10 DO\n"
                "    Counter := Counter + 1;\n"
                "END_FOR;"
            ),
            tag_names=["Counter"],
        )
        out = normalize_l5x_project(project)
        objs = out["control_objects"]

        # At least one ST statement object must carry the too_complex
        # marker. We don't pin a specific index because the parser may
        # emit several adjacent complex chunks while consuming the
        # FOR / END_FOR text.
        st_objs = [
            o
            for o in objs
            if o.object_type == ControlObjectType.INSTRUCTION
            and (o.attributes or {}).get("language") == "structured_text"
        ]
        self.assertTrue(st_objs)
        too_complex = [
            o
            for o in st_objs
            if (o.platform_specific or {}).get("st_parse_status")
            == "too_complex"
        ]
        self.assertTrue(
            too_complex,
            "Expected at least one ST statement with "
            "st_parse_status='too_complex'.",
        )

    def test_too_complex_assignment_still_emits_write(self) -> None:
        # `Motor := SQRT(value);` is recognized as an assignment to
        # Motor, but the RHS is outside the supported envelope --
        # the WRITE must still appear so the graph isn't blind to
        # the writer, just flagged.
        project = _make_project_with_st_routine(
            routine_name="MainRoutine",
            raw_logic="Motor := SQRT(value);",
            tag_names=["Motor", "value"],
        )
        out = normalize_l5x_project(project)
        rels = out["relationships"]
        writes = _find_writes(rels, target_id=_tag_id("Motor"))
        self.assertEqual(len(writes), 1)
        w = writes[0]
        self.assertEqual(
            w.platform_specific.get("st_parse_status"), "too_complex"
        )
        # No literal write_behavior because the RHS isn't a literal.
        self.assertIsNone(w.write_behavior)
        self.assertEqual(w.confidence, ConfidenceLevel.LOW)


class STAndLadderCoexistenceTests(unittest.TestCase):
    """Ladder normalization must still emit its edges after ST landed."""

    def test_ladder_routine_still_produces_writes_and_reads(self) -> None:
        # Hand-roll the smallest ladder fixture: XIC(Start_PB) /
        # OTE(Motor_Run) on Rung 0. We import from the existing
        # pipeline test to reuse a proven fixture.
        from tests.test_trace_v1_pipeline import (  # noqa: WPS433
            MOTOR_RUN_ID,
            _make_pipeline_project,
        )

        project = _make_pipeline_project()
        out = normalize_l5x_project(project)
        rels = out["relationships"]

        writes = _find_writes(rels, target_id=MOTOR_RUN_ID)
        reads = _find_reads(rels, target_id=MOTOR_RUN_ID)
        self.assertEqual(len(writes), 1)
        # Motor_Run is also read on rung 1 to drive AlarmFlag.
        self.assertEqual(len(reads), 1)
        # Ladder source_ids still look like rung:: -- ST changes
        # haven't accidentally remapped them.
        self.assertTrue(writes[0].source_id.startswith("rung::"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
