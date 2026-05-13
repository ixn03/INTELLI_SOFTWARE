"""Tests for the expanded Structured Text parser + normalization layer.

Covers the additions made on top of the v1 ST envelope:

* ``OR`` / ``AND`` / ``NOT`` composition with optional parentheses.
* Comparison terms (``>``, ``>=``, ``<``, ``<=``, ``=``, ``<>``).
* Array / dotted member identifiers (``Bits[3]``, ``Tank.Level``).
* Multiple assignments inside one IF / ELSE branch.
* CASE branches surface a ``condition_summary`` like ``"State = 1"``.
* Genuinely too-complex constructs (``WHILE`` loops, function calls,
  unbalanced parens) survive without crashing and are marked
  ``st_parse_status="too_complex"`` on the WRITES they emit.

The v1 envelope tests live in :mod:`test_st_normalization`; this
file is purely additive and never relaxes those guarantees.
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
    RelationshipType,
    WriteBehaviorType,
)
from app.parsers.st_expression import (  # noqa: E402
    STComparisonTerm,
    parse_st_expression,
)
from app.parsers.structured_text import (  # noqa: E402
    parse_structured_text,
)
from app.parsers.structured_text_blocks import (  # noqa: E402
    STAssignment,
    STIfBlock,
    parse_structured_text_blocks,
)
from app.services.normalization_service import (  # noqa: E402
    normalize_l5x_project,
)
from app.services.structured_text_extraction import (  # noqa: E402
    STCondition,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_project(raw_logic: str, tag_names: list[str]) -> ControlProject:
    routine = ControlRoutine(
        name="STExt",
        language="structured_text",
        instructions=parse_structured_text(raw_logic, "STExt"),
        raw_logic=raw_logic,
        metadata={"rockwell_type": "ST"},
    )
    program = ControlProgram(
        name="MainProgram",
        tags=[
            ControlTag(
                name=n,
                data_type="DINT",
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
        source_file="st_ext.L5X",
        file_hash="st-ext-hash",
        controllers=[controller],
    )


def _tag_id(name: str) -> str:
    return f"tag::PLC01/MainProgram/{name}"


def _writes_to(rels, target_id):
    return [
        r for r in rels
        if r.relationship_type == RelationshipType.WRITES
        and r.target_id == target_id
    ]


def _reads_to(rels, target_id):
    return [
        r for r in rels
        if r.relationship_type == RelationshipType.READS
        and r.target_id == target_id
    ]


# ---------------------------------------------------------------------------
# parse_st_expression: pure parser tests
# ---------------------------------------------------------------------------


class STExpressionParserTests(unittest.TestCase):
    """Direct tests for the new ``parse_st_expression`` parser."""

    # ---- AND-only conjunctions (regression with the existing envelope) -

    def test_flat_and_conjunction(self) -> None:
        expr = parse_st_expression("A AND B AND NOT C")
        self.assertFalse(expr.too_complex)
        self.assertEqual(len(expr.branches), 1)
        self.assertTrue(expr.is_simple_conjunction)
        self.assertEqual(expr.gating_logic_type, "and")
        tags = [
            (t.tag, t.required_value)
            for t in expr.branches[0].terms
            if isinstance(t, STCondition)
        ]
        self.assertEqual(tags, [("A", True), ("B", True), ("C", False)])

    # ---- OR -----------------------------------------------------------

    def test_simple_or_two_branches(self) -> None:
        expr = parse_st_expression("A OR B")
        self.assertFalse(expr.too_complex)
        self.assertEqual(len(expr.branches), 2)
        self.assertEqual(expr.gating_logic_type, "or")
        self.assertFalse(expr.is_simple_conjunction)
        identifiers = [
            [t.tag for t in b.terms if isinstance(t, STCondition)]
            for b in expr.branches
        ]
        self.assertEqual(identifiers, [["A"], ["B"]])

    def test_dnf_and_or(self) -> None:
        # (A AND B) OR (C AND D) -> 2 branches of 2 terms each.
        expr = parse_st_expression("A AND B OR C AND D")
        self.assertFalse(expr.too_complex)
        self.assertEqual(len(expr.branches), 2)
        self.assertEqual(expr.gating_logic_type, "and_or")
        terms_per_branch = [len(b.terms) for b in expr.branches]
        self.assertEqual(terms_per_branch, [2, 2])

    # ---- Parentheses --------------------------------------------------

    def test_balanced_outer_parens_stripped(self) -> None:
        expr = parse_st_expression("(A AND B)")
        self.assertFalse(expr.too_complex)
        self.assertEqual(len(expr.branches), 1)
        self.assertEqual(len(expr.branches[0].terms), 2)

    def test_parens_around_or_branch(self) -> None:
        # (A AND B) OR C -- the inner parens just group, no semantics.
        expr = parse_st_expression("(A AND B) OR C")
        self.assertFalse(expr.too_complex)
        self.assertEqual(len(expr.branches), 2)

    def test_unbalanced_parens_too_complex(self) -> None:
        expr = parse_st_expression("(A AND B")
        self.assertTrue(expr.too_complex)

    # ---- Comparisons --------------------------------------------------

    def test_simple_greater_than_comparison(self) -> None:
        expr = parse_st_expression("Tank_Level > 80")
        self.assertFalse(expr.too_complex)
        self.assertEqual(expr.gating_logic_type, "comparison")
        term = expr.branches[0].terms[0]
        self.assertIsInstance(term, STComparisonTerm)
        self.assertEqual(term.lhs, "Tank_Level")
        self.assertEqual(term.operator, ">")
        self.assertEqual(term.rhs, "80")
        self.assertTrue(term.lhs_is_tag)
        self.assertFalse(term.rhs_is_tag)

    def test_each_comparison_operator(self) -> None:
        for op in ["=", "<>", "<", "<=", ">", ">="]:
            with self.subTest(operator=op):
                expr = parse_st_expression(f"A {op} 5")
                self.assertFalse(expr.too_complex, op)
                term = expr.branches[0].terms[0]
                self.assertEqual(term.operator, op)

    def test_comparison_with_tag_rhs(self) -> None:
        expr = parse_st_expression("Setpoint <> ActualValue")
        self.assertFalse(expr.too_complex)
        term = expr.branches[0].terms[0]
        self.assertTrue(term.rhs_is_tag)
        self.assertEqual(term.rhs, "ActualValue")

    def test_comparison_mixed_with_boolean(self) -> None:
        # IF A AND B > 5 THEN ...  -- treated as A AND (B > 5)
        expr = parse_st_expression("A AND B > 5")
        self.assertFalse(expr.too_complex)
        self.assertEqual(expr.gating_logic_type, "comparison")
        terms = expr.branches[0].terms
        self.assertIsInstance(terms[0], STCondition)
        self.assertIsInstance(terms[1], STComparisonTerm)

    # ---- Array / member identifiers -----------------------------------

    def test_array_index_identifier(self) -> None:
        expr = parse_st_expression("Bits[3] AND Tank.Level > 50")
        self.assertFalse(expr.too_complex)
        terms = expr.branches[0].terms
        self.assertEqual(terms[0].tag, "Bits[3]")
        self.assertIsInstance(terms[1], STComparisonTerm)
        self.assertEqual(terms[1].lhs, "Tank.Level")

    # ---- Too-complex --------------------------------------------------

    def test_function_call_too_complex(self) -> None:
        expr = parse_st_expression("SQRT(A) > 5")
        self.assertTrue(expr.too_complex)

    def test_arithmetic_too_complex(self) -> None:
        expr = parse_st_expression("A + B AND C")
        self.assertTrue(expr.too_complex)

    def test_double_negation_too_complex(self) -> None:
        expr = parse_st_expression("NOT NOT A")
        self.assertTrue(expr.too_complex)

    def test_empty_input_returns_empty_parse(self) -> None:
        expr = parse_st_expression("")
        self.assertFalse(expr.too_complex)
        self.assertEqual(expr.branches, [])


# ---------------------------------------------------------------------------
# Block parser: new patterns
# ---------------------------------------------------------------------------


class STBlockParserExtendedTests(unittest.TestCase):
    def test_or_rhs_populates_expression_not_legacy_conditions(
        self,
    ) -> None:
        blocks = parse_structured_text_blocks(
            "Motor_Run := StartPB OR ManualOverride;"
        )
        assert isinstance(blocks[0], STAssignment)
        b = blocks[0]
        # Legacy ``conditions`` field stays empty for OR shapes.
        self.assertEqual(b.conditions, [])
        # New ``expression`` field carries the full DNF.
        self.assertIsNotNone(b.expression)
        self.assertEqual(len(b.expression.branches), 2)
        self.assertFalse(b.too_complex)

    def test_comparison_rhs_marks_expression_only(self) -> None:
        blocks = parse_structured_text_blocks(
            "AlarmActive := Tank_Level > 80;"
        )
        b = blocks[0]
        assert isinstance(b, STAssignment)
        self.assertEqual(b.conditions, [])
        self.assertFalse(b.too_complex)
        self.assertEqual(b.expression.gating_logic_type, "comparison")

    def test_if_with_or_condition_preserves_two_branches(self) -> None:
        blocks = parse_structured_text_blocks(
            "IF A OR B THEN Motor := TRUE; END_IF;"
        )
        b = blocks[0]
        assert isinstance(b, STIfBlock)
        self.assertIsNotNone(b.condition_expression)
        self.assertEqual(len(b.condition_expression.branches), 2)
        self.assertFalse(b.too_complex_condition)
        # ELSE inversion of an OR is impossible to do mechanically.
        self.assertTrue(b.else_too_complex)

    def test_if_with_comparison_condition_invertible_for_else(
        self,
    ) -> None:
        blocks = parse_structured_text_blocks(
            "IF A > 5 THEN X := TRUE; ELSE X := FALSE; END_IF;"
        )
        b = blocks[0]
        assert isinstance(b, STIfBlock)
        self.assertEqual(
            b.condition_expression.gating_logic_type, "comparison"
        )
        # A single comparison term is invertible -> ELSE is NOT
        # marked too complex.
        self.assertFalse(b.else_too_complex)

    def test_multiple_assignments_in_then_branch(self) -> None:
        blocks = parse_structured_text_blocks(
            "IF Start THEN MotorA := TRUE; MotorB := TRUE; END_IF;"
        )
        b = blocks[0]
        assert isinstance(b, STIfBlock)
        self.assertEqual(len(b.then_assignments), 2)
        self.assertEqual(
            [a.target for a in b.then_assignments],
            ["MotorA", "MotorB"],
        )

    def test_multiple_assignments_in_else_branch(self) -> None:
        blocks = parse_structured_text_blocks(
            """
            IF Faulted THEN
                MotorA := FALSE;
                MotorB := FALSE;
            ELSE
                MotorA := TRUE;
                MotorB := TRUE;
            END_IF;
            """
        )
        b = blocks[0]
        assert isinstance(b, STIfBlock)
        self.assertEqual(len(b.then_assignments), 2)
        self.assertEqual(len(b.else_assignments), 2)

    def test_case_branch_carries_condition_summary(self) -> None:
        blocks = parse_structured_text_blocks(
            """
            CASE State OF
                1: MotorA := TRUE;
                2: MotorB := TRUE;
                ELSE: MotorA := FALSE;
            END_CASE;
            """
        )
        block = blocks[0]
        summaries = [
            (b.label, b.condition_summary, b.is_default)
            for b in block.branches
        ]
        self.assertEqual(
            summaries,
            [
                ("1", "State = 1", False),
                ("2", "State = 2", False),
                ("ELSE", None, True),
            ],
        )

    def test_array_tag_in_assignment_target(self) -> None:
        blocks = parse_structured_text_blocks("Bits[5] := TRUE;")
        b = blocks[0]
        assert isinstance(b, STAssignment)
        self.assertEqual(b.target, "Bits[5]")
        self.assertEqual(b.assigned_value, True)


# ---------------------------------------------------------------------------
# Normalization: edge emission for the new patterns
# ---------------------------------------------------------------------------


class STNormalizationOrTests(unittest.TestCase):
    """An OR-RHS emits one READS per branch + marks gating_logic_type."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = _make_project(
            "Motor_Run := StartPB OR ManualOverride;",
            ["Motor_Run", "StartPB", "ManualOverride"],
        )
        cls.output = normalize_l5x_project(cls.project)
        cls.rels = cls.output["relationships"]

    def test_writes_emitted_with_or_gating_logic_type(self) -> None:
        writes = _writes_to(self.rels, _tag_id("Motor_Run"))
        self.assertEqual(len(writes), 1)
        meta = writes[0].platform_specific or {}
        self.assertEqual(meta.get("gating_logic_type"), "or")
        self.assertEqual(meta.get("st_parse_status"), "ok")
        # Each OR branch contributes its own ``extracted_conditions``
        # entry, annotated with ``or_branch_index``.
        extracted = meta.get("extracted_conditions") or []
        self.assertEqual(len(extracted), 2)
        indices = {e.get("or_branch_index") for e in extracted}
        self.assertEqual(indices, {0, 1})

    def test_reads_emitted_for_each_or_branch_tag(self) -> None:
        reads_start = _reads_to(self.rels, _tag_id("StartPB"))
        reads_manual = _reads_to(self.rels, _tag_id("ManualOverride"))
        self.assertEqual(len(reads_start), 1)
        self.assertEqual(len(reads_manual), 1)
        # Each READS carries the OR-branch annotation since branches > 1.
        self.assertEqual(
            (reads_start[0].platform_specific or {}).get("or_branch_index"),
            0,
        )
        self.assertEqual(
            (reads_manual[0].platform_specific or {}).get("or_branch_index"),
            1,
        )


class STNormalizationComparisonTests(unittest.TestCase):
    """A comparison RHS emits READS for tag operands + records the op."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = _make_project(
            "AlarmActive := Tank_Level > Setpoint;",
            ["AlarmActive", "Tank_Level", "Setpoint"],
        )
        cls.output = normalize_l5x_project(cls.project)
        cls.rels = cls.output["relationships"]

    def test_writes_marks_comparison_in_gating_logic_type(self) -> None:
        writes = _writes_to(self.rels, _tag_id("AlarmActive"))
        self.assertEqual(len(writes), 1)
        meta = writes[0].platform_specific or {}
        self.assertEqual(meta.get("gating_logic_type"), "comparison")

    def test_reads_for_both_tag_operands(self) -> None:
        # LHS and RHS are both tags -> both get READS edges.
        reads_lhs = _reads_to(self.rels, _tag_id("Tank_Level"))
        reads_rhs = _reads_to(self.rels, _tag_id("Setpoint"))
        self.assertEqual(len(reads_lhs), 1)
        self.assertEqual(len(reads_rhs), 1)
        for r in (reads_lhs[0], reads_rhs[0]):
            meta = r.platform_specific or {}
            self.assertEqual(meta.get("comparison_operator"), ">")
            self.assertEqual(meta.get("gating_kind"), "comparison")
            # Comparison reads MUST NOT use XIC/XIO -- Trace v2 would
            # otherwise fold them into the ladder gating conjunction.
            self.assertEqual(meta.get("instruction_type"), "ST_COMPARE")

    def test_reads_for_literal_rhs_skipped(self) -> None:
        # The literal '80' should never produce a stub tag.
        project = _make_project(
            "AlarmActive := Tank_Level > 80;",
            ["AlarmActive", "Tank_Level"],
        )
        output = normalize_l5x_project(project)
        reads = _reads_to(output["relationships"], _tag_id("Tank_Level"))
        self.assertEqual(len(reads), 1)
        # The numeric literal is captured as ``compared_with`` but
        # does not get its own READS edge.
        meta = reads[0].platform_specific or {}
        self.assertEqual(meta.get("compared_with"), "80")


class STNormalizationParensTests(unittest.TestCase):
    def test_parenthesized_or_emits_two_reads(self) -> None:
        project = _make_project(
            "Out := (A AND B) OR C;",
            ["Out", "A", "B", "C"],
        )
        output = normalize_l5x_project(project)
        # Each unique tag appears at least once.
        for name in ["A", "B", "C"]:
            with self.subTest(tag=name):
                reads = _reads_to(
                    output["relationships"], _tag_id(name)
                )
                self.assertGreaterEqual(len(reads), 1)
        writes = _writes_to(output["relationships"], _tag_id("Out"))
        self.assertEqual(
            (writes[0].platform_specific or {}).get("gating_logic_type"),
            "and_or",
        )


class STNormalizationArrayMemberTests(unittest.TestCase):
    def test_array_target_and_member_tag_in_rhs(self) -> None:
        project = _make_project(
            "Bits[5] := Pump_01.Run AND NOT Pump_01.Faulted;",
            ["Bits", "Pump_01"],
        )
        output = normalize_l5x_project(project)
        # The WRITES targets the array element (resolved against the
        # root tag stub when not present in the tag inventory).
        rels = output["relationships"]
        writes = [
            r for r in rels
            if r.relationship_type == RelationshipType.WRITES
            and (r.platform_specific or {}).get("language")
            == "structured_text"
        ]
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            (writes[0].platform_specific or {}).get("st_parse_status"),
            "ok",
        )


class STNormalizationIfMultiAssignTests(unittest.TestCase):
    """Multiple assignments inside one IF body all get WRITES+READS."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = _make_project(
            "IF Start AND NOT Faulted THEN "
            "MotorA := TRUE; MotorB := TRUE; END_IF;",
            ["MotorA", "MotorB", "Start", "Faulted"],
        )
        cls.output = normalize_l5x_project(cls.project)
        cls.rels = cls.output["relationships"]

    def test_each_assignment_gets_its_own_writes(self) -> None:
        self.assertEqual(
            len(_writes_to(self.rels, _tag_id("MotorA"))), 1
        )
        self.assertEqual(
            len(_writes_to(self.rels, _tag_id("MotorB"))), 1
        )

    def test_both_writes_carry_same_gating_reads(self) -> None:
        # Reads of Start / Faulted should appear at least once each;
        # the assignment-level emitter dedupes per-statement, so we
        # only verify they exist (the IF block carries one
        # synthetic Statement[N] per top-level block).
        for name, examined in [("Start", True), ("Faulted", False)]:
            with self.subTest(tag=name):
                reads = _reads_to(self.rels, _tag_id(name))
                self.assertGreaterEqual(len(reads), 1)
                self.assertTrue(
                    any(
                        (r.platform_specific or {}).get("examined_value")
                        is examined
                        for r in reads
                    )
                )


class STNormalizationCaseSummaryTests(unittest.TestCase):
    """CASE branches surface ``case_condition_summary`` metadata."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.project = _make_project(
            """
            CASE State OF
                1: MotorA := TRUE;
                2: MotorB := TRUE;
                ELSE: MotorA := FALSE;
            END_CASE;
            """,
            ["State", "MotorA", "MotorB"],
        )
        cls.output = normalize_l5x_project(cls.project)
        cls.rels = cls.output["relationships"]

    def test_branch_writes_carry_condition_summary(self) -> None:
        writes_a = _writes_to(self.rels, _tag_id("MotorA"))
        # MotorA gets one WRITES from branch 1 and one from ELSE.
        self.assertEqual(len(writes_a), 2)
        summaries = sorted(
            (w.platform_specific or {}).get("case_condition_summary")
            or ""
            for w in writes_a
        )
        # One non-ELSE branch carries "State = 1"; ELSE carries no
        # summary (None becomes "" after the sort default).
        self.assertEqual(summaries, ["", "State = 1"])

    def test_selector_read_carries_branch_summary(self) -> None:
        # The CASE selector tag (State) gets one READS per branch,
        # each annotated with its branch_label.
        reads_state = _reads_to(self.rels, _tag_id("State"))
        labels = sorted(
            (r.platform_specific or {}).get("branch_label") or ""
            for r in reads_state
        )
        self.assertEqual(labels, ["1", "2", "ELSE"])
        non_default = [
            (r.platform_specific or {}).get("case_condition_summary")
            for r in reads_state
            if (r.platform_specific or {}).get("branch_label") in ("1", "2")
        ]
        self.assertEqual(
            sorted(non_default),
            ["State = 1", "State = 2"],
        )


class STNormalizationElseInversionTests(unittest.TestCase):
    """ELSE inversion works for single identifier and single comparison
    THEN conditions."""

    def test_else_inverts_single_identifier(self) -> None:
        project = _make_project(
            "IF Faulted THEN Motor := FALSE; ELSE Motor := TRUE; END_IF;",
            ["Faulted", "Motor"],
        )
        output = normalize_l5x_project(project)
        rels = output["relationships"]
        # Two reads of Faulted: one from THEN (examined=True), one
        # from ELSE (examined=False).
        reads = _reads_to(rels, _tag_id("Faulted"))
        examined = sorted(
            (r.platform_specific or {}).get("examined_value")
            for r in reads
        )
        self.assertEqual(examined, [False, True])

    def test_else_inverts_single_comparison(self) -> None:
        project = _make_project(
            "IF Level > 80 THEN Alarm := TRUE; "
            "ELSE Alarm := FALSE; END_IF;",
            ["Level", "Alarm"],
        )
        output = normalize_l5x_project(project)
        rels = output["relationships"]
        reads = _reads_to(rels, _tag_id("Level"))
        # Two reads of Level: one with ">" (THEN), one with "<="
        # (the inversion of >).
        operators = sorted(
            (r.platform_specific or {}).get("comparison_operator") or ""
            for r in reads
        )
        self.assertEqual(operators, ["<=", ">"])


class STNormalizationTooComplexExtendedTests(unittest.TestCase):
    """Genuinely unsupported ST does not crash and is flagged."""

    def test_function_call_in_rhs_marks_too_complex(self) -> None:
        project = _make_project(
            "Out := SQRT(A) + B;",
            ["Out", "A", "B"],
        )
        output = normalize_l5x_project(project)
        writes = _writes_to(output["relationships"], _tag_id("Out"))
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            (writes[0].platform_specific or {}).get("st_parse_status"),
            "too_complex",
        )

    def test_while_loop_does_not_crash(self) -> None:
        project = _make_project(
            "WHILE A < 10 DO A := A + 1; END_WHILE;",
            ["A"],
        )
        # The point is just that the normalizer survives -- no
        # specific edge contract is asserted here.
        output = normalize_l5x_project(project)
        self.assertIsInstance(output["relationships"], list)


if __name__ == "__main__":
    unittest.main()
