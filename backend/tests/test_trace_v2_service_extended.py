"""Trace v2 natural-language rendering tests for the expanded layer.

Covers the spec items added on top of the v1 NL envelope:

* Ladder comparisons -- EQU / NEQ / LES / LEQ / GRT / GEQ / LIM.
* MOV / COP and math (ADD / SUB / MUL / DIV / CPT) WHAT phrasing.
* One-shots (ONS / OSR / OSF) condition phrasing.
* Timer / counter member access (``.DN`` / ``.TT`` / ``.EN``).
* ST OR / AND-OR rendering ("either ... or ...").
* ST comparison rendering.
* CASE branch prefix on ST conclusions.
* Branch warning on rungs that contain BST / NXB / BND.

The v1 envelope tests live in :mod:`test_trace_v2_service`; this file
is purely additive and never relaxes those guarantees.
"""

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
from app.services.trace_v2_service import trace_object_v2  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny fixture helpers
# ---------------------------------------------------------------------------


def _tag(tag_id: str, name: str) -> ControlObject:
    return ControlObject(
        id=tag_id,
        name=name,
        object_type=ControlObjectType.TAG,
        source_platform="rockwell",
        confidence=ConfidenceLevel.HIGH,
    )


def _rung(
    rung_id: str,
    routine: str,
    rung_number: int,
    *,
    has_branches: bool = False,
    branch_count: int = 1,
) -> ControlObject:
    return ControlObject(
        id=rung_id,
        name=f"Rung[{rung_number}]",
        object_type=ControlObjectType.RUNG,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}"
            f"/Rung[{rung_number}]"
        ),
        attributes={
            "has_branches": has_branches,
            "branch_count": branch_count,
        },
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


def _statement(stmt_id: str, routine: str, idx: int) -> ControlObject:
    return ControlObject(
        id=stmt_id,
        name=f"Statement[{idx}]",
        object_type=ControlObjectType.INSTRUCTION,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}"
            f"/Statement[{idx}]"
        ),
        attributes={"language": "structured_text"},
        confidence=ConfidenceLevel.HIGH,
    )


def _writes(
    source_id: str,
    target_id: str,
    routine: str,
    rung_number: int,
    *,
    instruction_type: str,
    write_behavior: WriteBehaviorType | None = None,
    extras: dict | None = None,
    logic_condition: str | None = None,
) -> Relationship:
    meta: dict = {"instruction_type": instruction_type}
    if extras:
        meta.update(extras)
    return Relationship(
        source_id=source_id,
        target_id=target_id,
        relationship_type=RelationshipType.WRITES,
        write_behavior=write_behavior,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}"
            f"/Rung[{rung_number}]"
        ),
        logic_condition=logic_condition,
        platform_specific=meta,
        confidence=ConfidenceLevel.HIGH,
    )


def _reads(
    source_id: str,
    target_id: str,
    routine: str,
    rung_number: int,
    *,
    instruction_type: str,
    examined: bool | None = None,
    extras: dict | None = None,
) -> Relationship:
    meta: dict = {"instruction_type": instruction_type}
    if examined is not None:
        meta["examined_value"] = examined
    if extras:
        meta.update(extras)
    return Relationship(
        source_id=source_id,
        target_id=target_id,
        relationship_type=RelationshipType.READS,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}"
            f"/Rung[{rung_number}]"
        ),
        platform_specific=meta,
        confidence=ConfidenceLevel.HIGH,
    )


def _statements_of(trace_result) -> list[str]:
    return [c.statement for c in trace_result.conclusions]


# ===========================================================================
# Ladder: comparison conditions
# ===========================================================================


class LadderComparisonConditionsTests(unittest.TestCase):
    """``EQU`` / ``NEQ`` / ``GRT`` / ... reads render as gating phrases.

    A typical comparison-gated rung looks like
    ``EQU(State, 1) OTE(MotorRun)`` -- the EQU contributes a READS for
    ``State`` and the OTE contributes the WRITES for ``MotorRun``.
    The trace conclusion for ``MotorRun`` should say
    ``"MotorRun is energized in <loc> when State must equal 1."``.
    """

    def _build(
        self,
        cmp_itype: str,
        operator: str,
        operands: list[str],
    ):
        routine = _routine(
            "routine::PLC01/MainProgram/R", "R",
        )
        rung = _rung(
            "rung::PLC01/MainProgram/R/Rung[0]", "R", 0,
        )
        motor = _tag("tag::PLC01/MainProgram/MotorRun", "MotorRun")
        # The comparison reads its operand[0] (the only tag in most
        # of our test cases).
        operand_objs = [
            _tag(f"tag::PLC01/MainProgram/{op}", op)
            for op in operands
            if op.replace("_", "").isalpha()
        ]
        # READS: one per tag-shaped operand.
        rels: list[Relationship] = []
        for idx, op in enumerate(operands):
            if not op.replace("_", "").isalpha():
                continue
            rels.append(
                _reads(
                    rung.id,
                    f"tag::PLC01/MainProgram/{op}",
                    "R", 0,
                    instruction_type=cmp_itype,
                    extras={
                        "instruction_id": "r0_i0",
                        "operand_index": idx,
                        "comparison_operator": operator,
                        "compared_operands": list(operands),
                        "gating_kind": "comparison",
                    },
                )
            )
        rels.append(
            _writes(
                rung.id,
                motor.id,
                "R", 0,
                instruction_type="OTE",
                write_behavior=WriteBehaviorType.SETS_TRUE,
                extras={"instruction_id": "r0_i1"},
            )
        )
        objs = [routine, rung, motor, *operand_objs]
        return objs, rels, motor.id

    def test_equ_renders_must_equal(self) -> None:
        objs, rels, target = self._build("EQU", "=", ["State", "1"])
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "State must equal 1" in s for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_neq_renders_must_not_equal(self) -> None:
        objs, rels, target = self._build("NEQ", "<>", ["Setpoint", "Actual"])
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "Setpoint must not equal Actual" in s
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_grt_renders_greater_than(self) -> None:
        objs, rels, target = self._build("GRT", ">", ["Tank_Level", "80"])
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "Tank_Level must be greater than 80" in s
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_geq_renders_greater_or_equal(self) -> None:
        objs, rels, target = self._build("GEQ", ">=", ["A", "B"])
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "A must be greater than or equal to B" in s
                for s in _statements_of(result)
            )
        )

    def test_les_renders_less_than(self) -> None:
        objs, rels, target = self._build("LES", "<", ["A", "B"])
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "A must be less than B" in s
                for s in _statements_of(result)
            )
        )

    def test_leq_renders_less_or_equal(self) -> None:
        objs, rels, target = self._build("LEQ", "<=", ["A", "B"])
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "A must be less than or equal to B" in s
                for s in _statements_of(result)
            )
        )

    def test_lim_renders_between(self) -> None:
        objs, rels, target = self._build(
            "LIM", "<=lim<=", ["Low", "Test", "High"],
        )
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "Test must be between Low and High" in s
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_lim_emits_a_single_phrase_despite_three_reads(self) -> None:
        # The LIM instruction emits three READS sharing one instruction_id.
        # The conditions clause should mention "Test must be between Low and High"
        # exactly once -- not three times.
        objs, rels, target = self._build(
            "LIM", "<=lim<=", ["Low", "Test", "High"],
        )
        result = trace_object_v2(target, objs, rels)
        cond_statements = [
            s for s in _statements_of(result)
            if "must be between" in s
        ]
        self.assertEqual(len(cond_statements), 1)


# ===========================================================================
# Ladder: math + move WHAT phrasing
# ===========================================================================


class LadderMathMoveWhatTests(unittest.TestCase):
    def test_add_what_phrase_names_both_sources(self) -> None:
        routine = _routine("routine::R", "R")
        rung = _rung("rung::R/0", "R", 0)
        sum_tag = _tag("tag::Sum", "Sum")
        a = _tag("tag::A", "A")
        b = _tag("tag::B", "B")
        rels = [
            _reads(
                rung.id, a.id, "R", 0,
                instruction_type="ADD",
                extras={
                    "instruction_id": "r0_i0",
                    "operand_role": "math_source",
                    "math_operator": "+",
                },
            ),
            _reads(
                rung.id, b.id, "R", 0,
                instruction_type="ADD",
                extras={
                    "instruction_id": "r0_i0",
                    "operand_role": "math_source",
                    "math_operator": "+",
                },
            ),
            _writes(
                rung.id, sum_tag.id, "R", 0,
                instruction_type="ADD",
                write_behavior=WriteBehaviorType.CALCULATES,
                extras={
                    "instruction_id": "r0_i0",
                    "math_operator": "+",
                    "source_operands": ["A", "B"],
                    "operand_role": "math_destination",
                },
            ),
        ]
        objs = [routine, rung, sum_tag, a, b]
        result = trace_object_v2(sum_tag.id, objs, rels)
        self.assertTrue(
            any(
                "Sum is calculated from A + B" in s
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_sub_mul_div_each_render_the_operator(self) -> None:
        for itype, operator in [("SUB", "-"), ("MUL", "*"), ("DIV", "/")]:
            with self.subTest(itype=itype):
                routine = _routine("routine::R", "R")
                rung = _rung("rung::R/0", "R", 0)
                dest = _tag("tag::Dest", "Dest")
                rels = [
                    _writes(
                        rung.id, dest.id, "R", 0,
                        instruction_type=itype,
                        write_behavior=WriteBehaviorType.CALCULATES,
                        extras={
                            "instruction_id": "r0_i0",
                            "math_operator": operator,
                            "source_operands": ["X", "Y"],
                        },
                    )
                ]
                result = trace_object_v2(
                    dest.id, [routine, rung, dest], rels
                )
                self.assertTrue(
                    any(
                        f"Dest is calculated from X {operator} Y" in s
                        for s in _statements_of(result)
                    ),
                    _statements_of(result),
                )

    def test_cpt_renders_compute_expression_phrase(self) -> None:
        routine = _routine("routine::R", "R")
        rung = _rung("rung::R/0", "R", 0)
        dest = _tag("tag::Dest", "Dest")
        rels = [
            _writes(
                rung.id, dest.id, "R", 0,
                instruction_type="CPT",
                write_behavior=WriteBehaviorType.CALCULATES,
                extras={
                    "instruction_id": "r0_i0",
                    "math_operator": "expression",
                },
            )
        ]
        result = trace_object_v2(dest.id, [routine, rung, dest], rels)
        self.assertTrue(
            any(
                "Dest is calculated from a compute expression" in s
                for s in _statements_of(result)
            )
        )

    def test_mov_names_source_via_sibling_reads(self) -> None:
        routine = _routine("routine::R", "R")
        rung = _rung("rung::R/0", "R", 0)
        dest = _tag("tag::Dest", "Dest")
        source = _tag("tag::Source", "Source")
        rels = [
            _reads(
                rung.id, source.id, "R", 0,
                instruction_type="MOV",
                extras={
                    "instruction_id": "r0_i0",
                    "operand_role": "move_source",
                },
            ),
            _writes(
                rung.id, dest.id, "R", 0,
                instruction_type="MOV",
                write_behavior=WriteBehaviorType.MOVES_VALUE,
                extras={
                    "instruction_id": "r0_i0",
                    "operand_role": "move_destination",
                },
            ),
        ]
        result = trace_object_v2(
            dest.id, [routine, rung, dest, source], rels,
        )
        self.assertTrue(
            any(
                "Dest is loaded from Source" in s
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_cop_renders_copy_phrase(self) -> None:
        routine = _routine("routine::R", "R")
        rung = _rung("rung::R/0", "R", 0)
        dst = _tag("tag::DstArr", "DstArr")
        src = _tag("tag::SrcArr", "SrcArr")
        rels = [
            _reads(
                rung.id, src.id, "R", 0,
                instruction_type="COP",
                extras={
                    "instruction_id": "r0_i0",
                    "operand_role": "move_source",
                },
            ),
            _writes(
                rung.id, dst.id, "R", 0,
                instruction_type="COP",
                write_behavior=WriteBehaviorType.MOVES_VALUE,
                extras={
                    "instruction_id": "r0_i0",
                    "operand_role": "move_destination",
                },
            ),
        ]
        result = trace_object_v2(
            dst.id, [routine, rung, dst, src], rels,
        )
        self.assertTrue(
            any(
                "DstArr is copied from SrcArr" in s
                for s in _statements_of(result)
            )
        )


# ===========================================================================
# Ladder: one-shot conditions
# ===========================================================================


class LadderOneShotConditionTests(unittest.TestCase):
    def _build(self, itype: str, condition_extras: dict):
        routine = _routine("routine::R", "R")
        rung = _rung("rung::R/0", "R", 0)
        motor = _tag("tag::Motor", "Motor")
        storage = _tag("tag::Storage", "Storage")
        rels = [
            _reads(
                rung.id, storage.id, "R", 0,
                instruction_type=itype,
                extras={
                    "instruction_id": "r0_i0",
                    **condition_extras,
                },
            ),
            _writes(
                rung.id, motor.id, "R", 0,
                instruction_type="OTE",
                write_behavior=WriteBehaviorType.SETS_TRUE,
                extras={"instruction_id": "r0_i1"},
            ),
        ]
        return [routine, rung, motor, storage], rels, motor.id

    def test_ons_says_one_scan_pulse_condition(self) -> None:
        objs, rels, target = self._build(
            "ONS",
            {"operand_role": "one_shot_storage", "gating_kind": "one_shot"},
        )
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "Storage drives a one-scan pulse condition" in s
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_osr_says_rising_edge(self) -> None:
        objs, rels, target = self._build(
            "OSR",
            {"operand_role": "one_shot_storage"},
        )
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "Storage drives a rising-edge one-shot" in s
                for s in _statements_of(result)
            )
        )

    def test_osf_says_falling_edge(self) -> None:
        objs, rels, target = self._build(
            "OSF",
            {"operand_role": "one_shot_storage"},
        )
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "Storage drives a falling-edge one-shot" in s
                for s in _statements_of(result)
            )
        )


# ===========================================================================
# Ladder: timer/counter member phrasing
# ===========================================================================


class LadderMemberPhrasingTests(unittest.TestCase):
    def _build(self, member: str, semantic: str, examined: bool):
        routine = _routine("routine::R", "R")
        rung = _rung("rung::R/0", "R", 0)
        motor = _tag("tag::Motor", "Motor")
        timer = _tag("tag::DelayTimer", "DelayTimer")
        rels = [
            _reads(
                rung.id, timer.id, "R", 0,
                instruction_type="XIC" if examined else "XIO",
                examined=examined,
                extras={
                    "instruction_id": "r0_i0",
                    "member": member,
                    "member_semantic": semantic,
                },
            ),
            _writes(
                rung.id, motor.id, "R", 0,
                instruction_type="OTE",
                write_behavior=WriteBehaviorType.SETS_TRUE,
                extras={"instruction_id": "r0_i1"},
            ),
        ]
        return [routine, rung, motor, timer], rels, motor.id

    def test_dn_examined_true_renders_done_bit_is_set(self) -> None:
        objs, rels, target = self._build("DN", "done", True)
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "DelayTimer's timer done bit is set" in s
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_dn_examined_false_renders_done_bit_is_clear(self) -> None:
        objs, rels, target = self._build("DN", "done", False)
        result = trace_object_v2(target, objs, rels)
        self.assertTrue(
            any(
                "DelayTimer's timer done bit is clear" in s
                for s in _statements_of(result)
            )
        )

    def test_tt_and_en_render_timing_and_enabled(self) -> None:
        for member, semantic, expected in [
            ("TT", "timing", "timer timing bit"),
            ("EN", "enabled", "timer enabled bit"),
        ]:
            with self.subTest(member=member):
                objs, rels, target = self._build(member, semantic, True)
                result = trace_object_v2(target, objs, rels)
                self.assertTrue(
                    any(
                        expected in s for s in _statements_of(result)
                    ),
                    _statements_of(result),
                )


# ===========================================================================
# Ladder: branch warning
# ===========================================================================


class LadderBranchWarningTests(unittest.TestCase):
    def _build(self, has_branches: bool, branch_count: int = 1):
        routine = _routine("routine::R", "R")
        rung = _rung(
            "rung::R/0", "R", 0,
            has_branches=has_branches, branch_count=branch_count,
        )
        motor = _tag("tag::Motor", "Motor")
        a = _tag("tag::A", "A")
        b = _tag("tag::B", "B")
        writes_extras: dict = {"instruction_id": "r0_i2"}
        if has_branches:
            writes_extras.update(
                {
                    "rung_has_branches": True,
                    "rung_branch_count": branch_count,
                }
            )
        rels = [
            _reads(rung.id, a.id, "R", 0, instruction_type="XIC",
                   examined=True),
            _reads(rung.id, b.id, "R", 0, instruction_type="XIC",
                   examined=True),
            _writes(
                rung.id, motor.id, "R", 0,
                instruction_type="OTE",
                write_behavior=WriteBehaviorType.SETS_TRUE,
                extras=writes_extras,
            ),
        ]
        return [routine, rung, motor, a, b], rels, motor.id

    def test_branched_rung_emits_warning_conclusion(self) -> None:
        objs, rels, target = self._build(has_branches=True, branch_count=2)
        result = trace_object_v2(target, objs, rels)
        warnings = [
            c for c in result.conclusions
            if (c.platform_specific or {}).get("trace_v2_kind")
            == "branch_warning"
        ]
        self.assertEqual(len(warnings), 1)
        self.assertIn("parallel branches", warnings[0].statement)
        # The branch count is recorded so a UI can render "this rung
        # has 2 parallel branches" if desired.
        self.assertEqual(
            warnings[0].platform_specific.get("rung_branch_count"), 2
        )

    def test_unbranched_rung_emits_no_warning(self) -> None:
        objs, rels, target = self._build(has_branches=False)
        result = trace_object_v2(target, objs, rels)
        warnings = [
            c for c in result.conclusions
            if (c.platform_specific or {}).get("trace_v2_kind")
            == "branch_warning"
        ]
        self.assertEqual(warnings, [])


# ===========================================================================
# Structured Text: OR / AND-OR / comparison / CASE
# ===========================================================================


def _st_writes(
    stmt_id: str,
    target_id: str,
    routine: str,
    stmt_idx: int,
    *,
    extracted: list[dict],
    gating: str,
    assigned_value: str = "TRUE",
    case_condition_summary: str | None = None,
) -> Relationship:
    meta: dict = {
        "instruction_type": "ST_ASSIGN",
        "language": "structured_text",
        "statement_type": "assignment",
        "assigned_value": assigned_value,
        "extracted_conditions": list(extracted),
        "st_parse_status": "ok",
        "gating_logic_type": gating,
    }
    if case_condition_summary is not None:
        meta["case_condition_summary"] = case_condition_summary
    return Relationship(
        source_id=stmt_id,
        target_id=target_id,
        relationship_type=RelationshipType.WRITES,
        write_behavior=WriteBehaviorType.SETS_TRUE,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}"
            f"/Statement[{stmt_idx}]"
        ),
        logic_condition=None,
        platform_specific=meta,
        confidence=ConfidenceLevel.HIGH,
    )


class STOrConclusionTests(unittest.TestCase):
    def test_two_branch_or_renders_either_or(self) -> None:
        routine = _routine(
            "routine::ST", "ST", language="structured_text",
        )
        stmt = _statement("stmt::ST/0", "ST", 0)
        motor = _tag("tag::Motor", "Motor")
        start = _tag("tag::StartPB", "StartPB")
        manual = _tag("tag::ManualOverride", "ManualOverride")
        rel = _st_writes(
            stmt.id, motor.id, "ST", 0,
            extracted=[
                {"tag": "StartPB", "required_value": True,
                 "source": "rhs", "or_branch_index": 0},
                {"tag": "ManualOverride", "required_value": True,
                 "source": "rhs", "or_branch_index": 1},
            ],
            gating="or",
            assigned_value="(boolean expression)",
        )
        result = trace_object_v2(
            motor.id, [routine, stmt, motor, start, manual], [rel],
        )
        # The OR branches should be joined with "either ... or ...".
        self.assertTrue(
            any(
                "either StartPB is TRUE, or ManualOverride is TRUE"
                in s
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_and_or_dnf_renders_grouped_conjunctions(self) -> None:
        # (A AND B) OR C -- two branches, first has two terms.
        routine = _routine(
            "routine::ST", "ST", language="structured_text",
        )
        stmt = _statement("stmt::ST/0", "ST", 0)
        out = _tag("tag::Out", "Out")
        a = _tag("tag::A", "A")
        b = _tag("tag::B", "B")
        c = _tag("tag::C", "C")
        rel = _st_writes(
            stmt.id, out.id, "ST", 0,
            extracted=[
                {"tag": "A", "required_value": True,
                 "source": "rhs", "or_branch_index": 0},
                {"tag": "B", "required_value": True,
                 "source": "rhs", "or_branch_index": 0},
                {"tag": "C", "required_value": True,
                 "source": "rhs", "or_branch_index": 1},
            ],
            gating="and_or",
            assigned_value="(boolean expression)",
        )
        result = trace_object_v2(
            out.id, [routine, stmt, out, a, b, c], [rel],
        )
        # The first branch should render with "A is TRUE and B is TRUE";
        # the second with "C is TRUE"; they should be joined with
        # "either ... or ...".
        joined = " ".join(_statements_of(result))
        self.assertIn("A is TRUE and B is TRUE", joined)
        self.assertIn("C is TRUE", joined)
        self.assertIn("either", joined)


class STComparisonConclusionTests(unittest.TestCase):
    def test_greater_than_renders_must_be_greater_than(self) -> None:
        routine = _routine(
            "routine::ST", "ST", language="structured_text",
        )
        stmt = _statement("stmt::ST/0", "ST", 0)
        alarm = _tag("tag::Alarm", "Alarm")
        level = _tag("tag::Tank_Level", "Tank_Level")
        rel = _st_writes(
            stmt.id, alarm.id, "ST", 0,
            extracted=[
                {
                    "tag": "Tank_Level",
                    "required_value": True,
                    "source": "rhs",
                    "comparison_operator": ">",
                    "compared_with": "80",
                },
            ],
            gating="comparison",
            assigned_value="(boolean expression)",
        )
        result = trace_object_v2(
            alarm.id, [routine, stmt, alarm, level], [rel],
        )
        self.assertTrue(
            any(
                "Tank_Level must be greater than 80" in s
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_not_equal_renders_must_not_equal(self) -> None:
        routine = _routine(
            "routine::ST", "ST", language="structured_text",
        )
        stmt = _statement("stmt::ST/0", "ST", 0)
        alarm = _tag("tag::Alarm", "Alarm")
        setp = _tag("tag::Setpoint", "Setpoint")
        rel = _st_writes(
            stmt.id, alarm.id, "ST", 0,
            extracted=[
                {
                    "tag": "Setpoint",
                    "required_value": True,
                    "source": "rhs",
                    "comparison_operator": "<>",
                    "compared_with": "ActualValue",
                },
            ],
            gating="comparison",
            assigned_value="(boolean expression)",
        )
        result = trace_object_v2(
            alarm.id, [routine, stmt, alarm, setp], [rel],
        )
        self.assertTrue(
            any(
                "Setpoint must not equal ActualValue" in s
                for s in _statements_of(result)
            )
        )


class STCaseBranchConclusionTests(unittest.TestCase):
    def test_branch_writes_carry_case_condition_summary(self) -> None:
        # CASE State OF 1: MotorA := TRUE; END_CASE; ->
        # the MotorA WRITES carries case_condition_summary="State = 1"
        # and Trace v2 should prepend a "This branch applies when ..."
        # sentence.
        routine = _routine(
            "routine::ST", "ST", language="structured_text",
        )
        stmt = _statement("stmt::ST/0", "ST", 0)
        motor = _tag("tag::MotorA", "MotorA")
        state = _tag("tag::State", "State")
        rel = _st_writes(
            stmt.id, motor.id, "ST", 0,
            extracted=[],  # CASE bodies carry no per-branch reads here
            gating="and",
            assigned_value="TRUE",
            case_condition_summary="State = 1",
        )
        result = trace_object_v2(
            motor.id, [routine, stmt, motor, state], [rel],
        )
        self.assertTrue(
            any(
                s.startswith("This branch applies when State = 1.")
                for s in _statements_of(result)
            ),
            _statements_of(result),
        )

    def test_case_branch_with_or_gating_carries_both_prefix_and_clause(
        self,
    ) -> None:
        # An assignment inside a CASE branch can still have an OR RHS
        # (e.g. CASE State OF 1: M := A OR B; END_CASE;). The
        # conclusion should carry both the CASE prefix and the OR
        # clause.
        routine = _routine(
            "routine::ST", "ST", language="structured_text",
        )
        stmt = _statement("stmt::ST/0", "ST", 0)
        motor = _tag("tag::Motor", "Motor")
        a = _tag("tag::A", "A")
        b = _tag("tag::B", "B")
        state = _tag("tag::State", "State")
        rel = _st_writes(
            stmt.id, motor.id, "ST", 0,
            extracted=[
                {"tag": "A", "required_value": True,
                 "source": "rhs", "or_branch_index": 0},
                {"tag": "B", "required_value": True,
                 "source": "rhs", "or_branch_index": 1},
            ],
            gating="or",
            assigned_value="(boolean expression)",
            case_condition_summary="State = 1",
        )
        result = trace_object_v2(
            motor.id, [routine, stmt, motor, a, b, state], [rel],
        )
        statements = _statements_of(result)
        # Find the rich-ST statement.
        rich = next(
            (s for s in statements if "This branch applies" in s), None
        )
        self.assertIsNotNone(rich, statements)
        self.assertIn("State = 1", rich)
        self.assertIn("either A is TRUE, or B is TRUE", rich)


if __name__ == "__main__":
    unittest.main()
