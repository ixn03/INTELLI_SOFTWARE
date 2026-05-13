"""Unit tests for the expanded ladder normalization layer.

Covers the new ladder instruction families plumbed into
``normalize_l5x_project``:

* Comparisons (EQU, NEQ, LES, LEQ, GRT, GEQ, LIM) -> READS with
  ``comparison_operator``.
* Math (ADD, SUB, MUL, DIV, CPT) -> READS for sources +
  WRITES(``calculates``) for destination.
* Move / copy (MOV, COP) -> READS source + WRITES(``moves_value``)
  destination.
* One-shots (ONS, OSR, OSF) -> READS storage + WRITES(``pulses``)
  output.
* Timer / counter member access (``.DN`` / ``.TT`` / ``.EN`` /
  ``.ACC`` / ``.PRE``) annotates relationships with
  ``platform_specific["member"]`` + ``["member_semantic"]``.
* Conservative branch detection: ``BST`` / ``NXB`` / ``BND`` markers
  set ``rung_has_branches`` + ``rung_branch_count`` on both the rung
  ``ControlObject`` and every relationship emitted from that rung,
  but instructions are NOT attributed to specific branches.

Run with::

    python -m unittest tests.test_ladder_normalization_extended
"""

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.control_model import (  # noqa: E402
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.models.reasoning import (  # noqa: E402
    ControlObjectType,
    RelationshipType,
    WriteBehaviorType,
)
from app.services.normalization_service import (  # noqa: E402
    normalize_l5x_project,
)


def _ladder_instr(
    iid: str,
    itype: str,
    operands: list[str],
    rung: int,
    output: str | None = None,
    rung_text: str | None = None,
) -> ControlInstruction:
    """Build a ladder ``ControlInstruction`` with sensible defaults.

    The optional ``rung_text`` mirrors what the real ladder parser
    attaches in ``metadata["rung_text"]`` so branch markers
    (``BST`` / ``NXB`` / ``BND``) are visible to the normalizer.
    """

    metadata: dict = {}
    if rung_text:
        metadata["rung_text"] = rung_text
    return ControlInstruction(
        id=iid,
        instruction_type=itype,
        operands=list(operands),
        output=output,
        raw_text=f"{itype}({','.join(operands)})",
        language="ladder",
        rung_number=rung,
        metadata=metadata,
    )


def _make_project(
    *,
    rungs: list[tuple[list[ControlInstruction], str | None]],
    program_tags: list[str],
    controller_tags: list[str] | None = None,
) -> ControlProject:
    """Build a tiny one-program / one-routine project.

    ``rungs`` is a list of ``(instructions, raw_rung_text)`` pairs. Each
    list of instructions becomes its own rung.
    ``raw_rung_text`` is used directly as ``ControlRoutine.raw_logic``
    when there is only one rung; for multi-rung tests we concatenate
    them with newlines so the legacy ladder parser sees the right
    BST/NXB/BND markers if any.
    """

    flat_instructions: list[ControlInstruction] = []
    for instrs, _ in rungs:
        flat_instructions.extend(instrs)
    raw_logic = "\n".join(text or "" for _, text in rungs)

    routine = ControlRoutine(
        name="TestRoutine",
        language="ladder",
        instructions=flat_instructions,
        raw_logic=raw_logic,
        metadata={"rockwell_type": "RLL"},
    )
    program = ControlProgram(
        name="TestProgram",
        tags=[
            ControlTag(
                name=name,
                data_type="DINT",
                scope="TestProgram",
                platform_source="rockwell_l5x",
            )
            for name in program_tags
        ],
        routines=[routine],
    )
    controller = ControlController(
        name="PLC",
        platform="rockwell",
        controller_tags=[
            ControlTag(
                name=name,
                data_type="DINT",
                scope="controller",
                platform_source="rockwell_l5x",
            )
            for name in (controller_tags or [])
        ],
        programs=[program],
    )
    return ControlProject(
        project_name="PLC",
        source_file="test.L5X",
        controllers=[controller],
    )


def _rels(output, rel_type):
    return [
        r for r in output["relationships"]
        if r.relationship_type == rel_type
    ]


def _rels_for_instruction(output, instr_id):
    return [
        r for r in output["relationships"]
        if (r.platform_specific or {}).get("instruction_id") == instr_id
    ]


# ---------------------------------------------------------------------------
# Comparisons
# ---------------------------------------------------------------------------


class LadderComparisonTests(unittest.TestCase):
    """EQU / NEQ / LES / LEQ / GRT / GEQ / LIM emit READS for tag operands."""

    def test_equ_emits_reads_for_both_tag_operands(self) -> None:
        instr = _ladder_instr("r0_i0", "EQU", ["State", "5"], rung=0)
        project = _make_project(
            rungs=[([instr], "EQU(State,5)")],
            program_tags=["State"],
        )
        output = normalize_l5x_project(project)
        reads = _rels_for_instruction(output, "r0_i0")
        # State -> READS once (5 is a numeric literal -> skipped)
        self.assertEqual(
            [r.relationship_type for r in reads],
            [RelationshipType.READS],
        )
        meta = reads[0].platform_specific or {}
        self.assertEqual(meta.get("instruction_type"), "EQU")
        self.assertEqual(meta.get("comparison_operator"), "=")
        # Numeric literal is still surfaced in compared_operands so
        # consumers can render "State = 5" without re-parsing.
        self.assertIn("5", meta.get("compared_operands") or [])
        # gating_kind protects Trace v2 from folding the comparison
        # READS into its XIC/XIO condition aggregator.
        self.assertEqual(meta.get("gating_kind"), "comparison")

    def test_neq_grt_geq_les_leq_emit_expected_operators(self) -> None:
        cases = [
            ("NEQ", "<>"),
            ("GRT", ">"),
            ("GEQ", ">="),
            ("LES", "<"),
            ("LEQ", "<="),
        ]
        for itype, expected_op in cases:
            with self.subTest(instruction=itype):
                instr = _ladder_instr(
                    "r0_i0", itype, ["A", "B"], rung=0,
                )
                project = _make_project(
                    rungs=[([instr], f"{itype}(A,B)")],
                    program_tags=["A", "B"],
                )
                output = normalize_l5x_project(project)
                reads = _rels_for_instruction(output, "r0_i0")
                self.assertEqual(len(reads), 2)
                for r in reads:
                    self.assertEqual(
                        r.relationship_type, RelationshipType.READS
                    )
                    self.assertEqual(
                        (r.platform_specific or {}).get(
                            "comparison_operator"
                        ),
                        expected_op,
                    )

    def test_lim_reads_three_tag_operands(self) -> None:
        # LIM(Low, Test, High)
        instr = _ladder_instr(
            "r0_i0", "LIM", ["Low", "Test", "High"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "LIM(Low,Test,High)")],
            program_tags=["Low", "Test", "High"],
        )
        output = normalize_l5x_project(project)
        reads = _rels_for_instruction(output, "r0_i0")
        self.assertEqual(len(reads), 3)
        operand_indices = sorted(
            (r.platform_specific or {}).get("operand_index")
            for r in reads
        )
        self.assertEqual(operand_indices, [0, 1, 2])

    def test_comparison_skips_numeric_literals(self) -> None:
        instr = _ladder_instr(
            "r0_i0", "GRT", ["Temperature", "200"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "GRT(Temperature,200)")],
            program_tags=["Temperature"],
        )
        output = normalize_l5x_project(project)
        reads = _rels_for_instruction(output, "r0_i0")
        self.assertEqual(len(reads), 1)
        # Numeric literal '200' is not in tag inventory; ensure no
        # spurious READS emitted (the resolver would otherwise create
        # a tag stub).
        tag_stubs = [
            o for o in output["control_objects"]
            if o.object_type == ControlObjectType.TAG and o.name == "200"
        ]
        self.assertEqual(tag_stubs, [])

    def test_comparison_emits_no_writes(self) -> None:
        instr = _ladder_instr("r0_i0", "EQU", ["A", "B"], rung=0)
        project = _make_project(
            rungs=[([instr], "EQU(A,B)")],
            program_tags=["A", "B"],
        )
        output = normalize_l5x_project(project)
        writes = [
            r for r in _rels(output, RelationshipType.WRITES)
            if (r.platform_specific or {}).get("instruction_id")
            == "r0_i0"
        ]
        self.assertEqual(writes, [])


# ---------------------------------------------------------------------------
# Math
# ---------------------------------------------------------------------------


class LadderMathTests(unittest.TestCase):
    """ADD / SUB / MUL / DIV / CPT emit reads + a destination write."""

    def test_add_emits_reads_and_calculates_write(self) -> None:
        instr = _ladder_instr(
            "r0_i0", "ADD", ["A", "B", "Sum"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "ADD(A,B,Sum)")],
            program_tags=["A", "B", "Sum"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")

        reads = [r for r in rels if r.relationship_type
                 == RelationshipType.READS]
        writes = [r for r in rels if r.relationship_type
                  == RelationshipType.WRITES]

        self.assertEqual(len(reads), 2)
        for r in reads:
            self.assertEqual(
                (r.platform_specific or {}).get("operand_role"),
                "math_source",
            )
            self.assertEqual(
                (r.platform_specific or {}).get("math_operator"),
                "+",
            )

        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0].write_behavior, WriteBehaviorType.CALCULATES
        )
        meta = writes[0].platform_specific or {}
        self.assertEqual(meta.get("operand_role"), "math_destination")
        self.assertEqual(meta.get("math_operator"), "+")
        self.assertEqual(meta.get("source_operands"), ["A", "B"])

    def test_sub_mul_div_emit_distinct_operators(self) -> None:
        cases = [("SUB", "-"), ("MUL", "*"), ("DIV", "/")]
        for itype, expected_op in cases:
            with self.subTest(instruction=itype):
                instr = _ladder_instr(
                    "r0_i0", itype, ["A", "B", "Result"], rung=0,
                )
                project = _make_project(
                    rungs=[([instr], f"{itype}(A,B,Result)")],
                    program_tags=["A", "B", "Result"],
                )
                output = normalize_l5x_project(project)
                rels = _rels_for_instruction(output, "r0_i0")
                ops_on_reads = {
                    (r.platform_specific or {}).get("math_operator")
                    for r in rels
                    if r.relationship_type == RelationshipType.READS
                }
                self.assertEqual(ops_on_reads, {expected_op})
                writes = [
                    r for r in rels
                    if r.relationship_type == RelationshipType.WRITES
                ]
                self.assertEqual(len(writes), 1)
                self.assertEqual(
                    writes[0].write_behavior,
                    WriteBehaviorType.CALCULATES,
                )

    def test_math_skips_literal_sources(self) -> None:
        # ADD(MyCounter, 1, MyCounter): the '1' literal is not a tag.
        instr = _ladder_instr(
            "r0_i0", "ADD", ["MyCounter", "1", "MyCounter"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "ADD(MyCounter,1,MyCounter)")],
            program_tags=["MyCounter"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")
        reads = [r for r in rels if r.relationship_type
                 == RelationshipType.READS]
        # Only MyCounter reads; the literal "1" is skipped.
        self.assertEqual(len(reads), 1)
        # And the destination WRITES still fires.
        writes = [r for r in rels if r.relationship_type
                  == RelationshipType.WRITES]
        self.assertEqual(len(writes), 1)

    def test_cpt_emits_only_destination_write(self) -> None:
        # CPT(Dest, "Expr") - the expression operand is a quoted string
        # the ladder parser cannot crack today.
        instr = _ladder_instr(
            "r0_i0", "CPT",
            ["Dest", '"A + B * 2"'],
            rung=0,
        )
        project = _make_project(
            rungs=[([instr], 'CPT(Dest,"A + B * 2")')],
            program_tags=["Dest"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")
        reads = [r for r in rels if r.relationship_type
                 == RelationshipType.READS]
        writes = [r for r in rels if r.relationship_type
                  == RelationshipType.WRITES]
        self.assertEqual(reads, [])
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0].write_behavior, WriteBehaviorType.CALCULATES
        )
        meta = writes[0].platform_specific or {}
        self.assertEqual(meta.get("math_operator"), "expression")


# ---------------------------------------------------------------------------
# Move / Copy
# ---------------------------------------------------------------------------


class LadderMoveCopyTests(unittest.TestCase):
    def test_mov_emits_reads_source_and_moves_value_write(self) -> None:
        instr = _ladder_instr(
            "r0_i0", "MOV", ["Source", "Destination"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "MOV(Source,Destination)")],
            program_tags=["Source", "Destination"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")
        reads = [r for r in rels if r.relationship_type
                 == RelationshipType.READS]
        writes = [r for r in rels if r.relationship_type
                  == RelationshipType.WRITES]
        self.assertEqual(len(reads), 1)
        self.assertEqual(
            (reads[0].platform_specific or {}).get("operand_role"),
            "move_source",
        )
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0].write_behavior, WriteBehaviorType.MOVES_VALUE
        )
        self.assertEqual(
            (writes[0].platform_specific or {}).get("operand_role"),
            "move_destination",
        )

    def test_cop_emits_reads_source_and_moves_value_write(self) -> None:
        instr = _ladder_instr(
            "r0_i0", "COP", ["SrcArray", "DstArray", "10"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "COP(SrcArray,DstArray,10)")],
            program_tags=["SrcArray", "DstArray"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")
        reads = [r for r in rels if r.relationship_type
                 == RelationshipType.READS]
        writes = [r for r in rels if r.relationship_type
                  == RelationshipType.WRITES]
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0].write_behavior, WriteBehaviorType.MOVES_VALUE
        )


# ---------------------------------------------------------------------------
# One-shots
# ---------------------------------------------------------------------------


class LadderOneShotTests(unittest.TestCase):
    def test_ons_emits_read_and_write_on_storage_bit(self) -> None:
        instr = _ladder_instr(
            "r0_i0", "ONS", ["StorageBit"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "ONS(StorageBit)")],
            program_tags=["StorageBit"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")
        reads = [r for r in rels if r.relationship_type
                 == RelationshipType.READS]
        writes = [r for r in rels if r.relationship_type
                  == RelationshipType.WRITES]
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0].write_behavior, WriteBehaviorType.PULSES
        )
        # ONS storage bit: both edges target the same tag id.
        self.assertEqual(reads[0].target_id, writes[0].target_id)

    def test_osr_emits_read_storage_and_write_output(self) -> None:
        instr = _ladder_instr(
            "r0_i0", "OSR", ["StorageBit", "OutputBit"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "OSR(StorageBit,OutputBit)")],
            program_tags=["StorageBit", "OutputBit"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")
        reads = [r for r in rels if r.relationship_type
                 == RelationshipType.READS]
        writes = [r for r in rels if r.relationship_type
                  == RelationshipType.WRITES]
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(writes), 1)
        # Storage read targets StorageBit, output write targets OutputBit
        # -- they must differ.
        self.assertNotEqual(reads[0].target_id, writes[0].target_id)
        self.assertEqual(
            writes[0].write_behavior, WriteBehaviorType.PULSES
        )

    def test_osf_emits_pulses_write(self) -> None:
        instr = _ladder_instr(
            "r0_i0", "OSF", ["StorageBit", "OutputBit"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "OSF(StorageBit,OutputBit)")],
            program_tags=["StorageBit", "OutputBit"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")
        writes = [r for r in rels if r.relationship_type
                  == RelationshipType.WRITES]
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0].write_behavior, WriteBehaviorType.PULSES
        )


# ---------------------------------------------------------------------------
# Timer / counter member access
# ---------------------------------------------------------------------------


class LadderMemberAccessTests(unittest.TestCase):
    """``.DN`` / ``.TT`` / ``.EN`` / ``.ACC`` / ``.PRE`` get surfaced."""

    def test_xic_on_timer_dn_records_member_metadata(self) -> None:
        # XIC(DelayTimer.DN) -> READS DelayTimer with member='DN'
        instr = _ladder_instr(
            "r0_i0", "XIC", ["DelayTimer.DN"], rung=0,
        )
        project = _make_project(
            rungs=[([instr], "XIC(DelayTimer.DN)")],
            program_tags=["DelayTimer"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")
        self.assertEqual(len(rels), 1)
        meta = rels[0].platform_specific or {}
        self.assertEqual(meta.get("member"), "DN")
        self.assertEqual(meta.get("member_semantic"), "done")

    def test_each_known_member_suffix(self) -> None:
        cases = [
            (".TT", "TT", "timing"),
            (".EN", "EN", "enabled"),
            (".ACC", "ACC", "accumulated_value"),
            (".PRE", "PRE", "preset_value"),
        ]
        for suffix, expected_member, expected_label in cases:
            with self.subTest(suffix=suffix):
                instr = _ladder_instr(
                    "r0_i0", "XIC",
                    [f"DelayTimer{suffix}"], rung=0,
                )
                project = _make_project(
                    rungs=[([instr],
                            f"XIC(DelayTimer{suffix})")],
                    program_tags=["DelayTimer"],
                )
                output = normalize_l5x_project(project)
                rels = _rels_for_instruction(output, "r0_i0")
                meta = rels[0].platform_specific or {}
                self.assertEqual(meta.get("member"), expected_member)
                self.assertEqual(
                    meta.get("member_semantic"), expected_label
                )

    def test_plain_operand_has_no_member_metadata(self) -> None:
        instr = _ladder_instr("r0_i0", "XIC", ["PlainTag"], rung=0)
        project = _make_project(
            rungs=[([instr], "XIC(PlainTag)")],
            program_tags=["PlainTag"],
        )
        output = normalize_l5x_project(project)
        rels = _rels_for_instruction(output, "r0_i0")
        meta = rels[0].platform_specific or {}
        self.assertNotIn("member", meta)
        self.assertNotIn("member_semantic", meta)


# ---------------------------------------------------------------------------
# Branch detection
# ---------------------------------------------------------------------------


class LadderBranchDetectionTests(unittest.TestCase):
    """Conservative BST/NXB/BND detection.

    We assert presence of the rung-level flags + branch counts; we do
    NOT assert per-instruction branch attribution (that's deliberately
    out of scope).
    """

    def test_no_branch_tokens_marks_rung_unbranched(self) -> None:
        instrs = [
            _ladder_instr("r0_i0", "XIC", ["A"], rung=0),
            _ladder_instr("r0_i1", "OTE", ["B"], rung=0, output="B"),
        ]
        project = _make_project(
            rungs=[(instrs, "XIC(A)OTE(B)")],
            program_tags=["A", "B"],
        )
        output = normalize_l5x_project(project)
        rung_objs = [
            o for o in output["control_objects"]
            if o.object_type == ControlObjectType.RUNG
        ]
        self.assertEqual(len(rung_objs), 1)
        rung_obj = rung_objs[0]
        self.assertFalse(rung_obj.attributes.get("has_branches", True))
        self.assertEqual(rung_obj.attributes.get("branch_count"), 1)
        # Per-relationship: no rung_has_branches key when False.
        for r in output["relationships"]:
            if (r.platform_specific or {}).get("instruction_id") in (
                "r0_i0", "r0_i1"
            ):
                self.assertNotIn(
                    "rung_has_branches", r.platform_specific or {}
                )

    def test_bst_nxb_bnd_sets_branch_metadata(self) -> None:
        # Two parallel branches: BST XIC(A) NXB XIC(B) BND OTE(C)
        rung_text = "BST XIC(A) NXB XIC(B) BND OTE(C)"
        instrs = [
            _ladder_instr("r0_i0", "XIC", ["A"], rung=0,
                          rung_text=rung_text),
            _ladder_instr("r0_i1", "XIC", ["B"], rung=0,
                          rung_text=rung_text),
            _ladder_instr(
                "r0_i2", "OTE", ["C"], rung=0, output="C",
                rung_text=rung_text,
            ),
        ]
        project = _make_project(
            rungs=[
                (
                    instrs,
                    rung_text,
                )
            ],
            program_tags=["A", "B", "C"],
        )
        output = normalize_l5x_project(project)
        rung_obj = next(
            o for o in output["control_objects"]
            if o.object_type == ControlObjectType.RUNG
        )
        self.assertTrue(rung_obj.attributes.get("has_branches"))
        self.assertEqual(rung_obj.attributes.get("branch_count"), 2)
        # Every relationship emitted from this rung carries the flag.
        for r in output["relationships"]:
            meta = r.platform_specific or {}
            if meta.get("instruction_id") in (
                "r0_i0", "r0_i1", "r0_i2"
            ):
                self.assertIs(meta.get("rung_has_branches"), True)
                self.assertEqual(meta.get("rung_branch_count"), 2)

    def test_three_parallel_branches(self) -> None:
        rung_text = "BST XIC(A) NXB XIC(B) NXB XIC(C) BND OTE(D)"
        instrs = [
            _ladder_instr("r0_i0", "XIC", ["A"], rung=0,
                          rung_text=rung_text),
            _ladder_instr("r0_i1", "XIC", ["B"], rung=0,
                          rung_text=rung_text),
            _ladder_instr("r0_i2", "XIC", ["C"], rung=0,
                          rung_text=rung_text),
            _ladder_instr(
                "r0_i3", "OTE", ["D"], rung=0, output="D",
                rung_text=rung_text,
            ),
        ]
        project = _make_project(
            rungs=[(instrs, rung_text)],
            program_tags=["A", "B", "C", "D"],
        )
        output = normalize_l5x_project(project)
        rung_obj = next(
            o for o in output["control_objects"]
            if o.object_type == ControlObjectType.RUNG
        )
        self.assertEqual(rung_obj.attributes.get("branch_count"), 3)

    def test_branch_detection_is_conservative_not_per_instruction(
        self,
    ) -> None:
        """We do not claim per-instruction branch attribution.

        Verify that no emitted relationship has a ``branch_index``
        field (a name that would imply per-branch attribution).
        """
        rung_text = "BST XIC(A) NXB XIC(B) BND OTE(C)"
        instrs = [
            _ladder_instr("r0_i0", "XIC", ["A"], rung=0,
                          rung_text=rung_text),
            _ladder_instr("r0_i1", "XIC", ["B"], rung=0,
                          rung_text=rung_text),
            _ladder_instr("r0_i2", "OTE", ["C"], rung=0, output="C",
                          rung_text=rung_text),
        ]
        project = _make_project(
            rungs=[(instrs, rung_text)],
            program_tags=["A", "B", "C"],
        )
        output = normalize_l5x_project(project)
        for r in output["relationships"]:
            self.assertNotIn(
                "branch_index", r.platform_specific or {},
            )


# ---------------------------------------------------------------------------
# Regression: existing XIC/XIO/OTE/OTL/OTU/TON/RES/JSR still work
# ---------------------------------------------------------------------------


class LadderRegressionTests(unittest.TestCase):
    """Sanity check: the small core instruction set still functions.

    A separate, dedicated regression test for the new infrastructure is
    intentional -- the existing ``test_normalization_service.py`` is a
    detailed contract test; this is a lightweight smoke against the
    same surface in case the registry / dispatcher refactor breaks.
    """

    def test_xic_otE_still_emit_reads_and_writes(self) -> None:
        instrs = [
            _ladder_instr("r0_i0", "XIC", ["Start"], rung=0),
            _ladder_instr(
                "r0_i1", "OTE", ["Motor"], rung=0, output="Motor",
            ),
        ]
        project = _make_project(
            rungs=[(instrs, "XIC(Start)OTE(Motor)")],
            program_tags=["Start", "Motor"],
        )
        output = normalize_l5x_project(project)
        reads = _rels(output, RelationshipType.READS)
        writes = _rels(output, RelationshipType.WRITES)
        self.assertEqual(len(reads), 1)
        self.assertEqual(len(writes), 1)
        self.assertEqual(
            writes[0].write_behavior, WriteBehaviorType.SETS_TRUE
        )


if __name__ == "__main__":
    unittest.main()
