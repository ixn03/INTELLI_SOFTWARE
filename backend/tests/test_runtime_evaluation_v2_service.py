"""Tests for :mod:`app.services.runtime_evaluation_v2_service`.

Covers every scenario in the v2 spec:

* simple boolean satisfied / blocked / missing
* comparison numeric satisfied / blocked
* comparison tag-to-tag satisfied / blocked
* OR branch satisfied
* OR all branches blocked
* timer member DN satisfied / blocked
* multiple writers TRUE/FALSE conflict
* latch / unlatch conflict
* incomplete due to missing values
* unsupported conditions are preserved without crashing

The tests construct :class:`TraceResult` instances directly with the
conclusions that ``trace_object_v2`` would produce. This isolates the
evaluator's contract from the rest of the trace pipeline.
"""

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.reasoning import (  # noqa: E402
    ConfidenceLevel,
    TraceResult,
    TruthConclusion,
    TruthContextType,
)
from app.services.runtime_evaluation_v2_service import (  # noqa: E402
    ConditionStatus,
    OverallVerdict,
    PathStatus,
    WriteEffect,
    evaluate_trace_runtime_v2,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _writer_conditions(
    *,
    location: str,
    instruction_type: str,
    conditions: list[dict],
    target_id: str,
    source_id: str,
    statement: str = "Target is written when ...",
) -> TruthConclusion:
    """Build a ``writer_conditions`` conclusion shaped like Trace v2."""

    return TruthConclusion(
        statement=statement,
        subject_ids=[target_id, source_id],
        truth_context=TruthContextType.DESIGN_TRUTH,
        confidence=ConfidenceLevel.HIGH,
        platform_specific={
            "trace_v2_kind": "writer_conditions",
            "instruction_type": instruction_type,
            "location": location,
            "conditions": list(conditions),
        },
    )


def _st_assignment(
    *,
    location: str,
    assigned_value: str,
    conditions: list[dict],
    target_id: str,
    source_id: str,
    statement: str = "Target is assigned when ...",
    gating_logic_type: str = "and",
) -> TruthConclusion:
    """Build an ``st_assignment`` conclusion shaped like Trace v2 ST."""

    return TruthConclusion(
        statement=statement,
        subject_ids=[target_id, source_id],
        truth_context=TruthContextType.DESIGN_TRUTH,
        confidence=ConfidenceLevel.HIGH,
        platform_specific={
            "trace_v2_kind": "st_assignment",
            "location": location,
            "assigned_target": "Target",
            "assigned_value": assigned_value,
            "gating_logic_type": gating_logic_type,
            "case_condition_summary": None,
            "conditions": list(conditions),
        },
    )


def _trace(
    *conclusions: TruthConclusion,
    target_id: str,
    summary: str | None = None,
) -> TraceResult:
    return TraceResult(
        target_object_id=target_id,
        conclusions=list(conclusions),
        summary=summary,
        platform_specific={"trace_version": "v2"},
    )


def _verdict(result: TraceResult) -> str:
    return result.platform_specific["overall_verdict"]


def _primary_statement(result: TraceResult) -> str:
    """First conclusion is always the runtime v2 verdict."""

    return result.conclusions[0].statement


# ---------------------------------------------------------------------------
# Simple boolean
# ---------------------------------------------------------------------------


TARGET_ID = "tag::PLC01/MainProgram/Pump_Run"
RUNG_ID = "rung::PLC01/MainProgram/MotorRoutine/Rung[3]"


class SimpleBooleanTests(unittest.TestCase):
    """Plain XIC / XIO conditions with no member or comparison."""

    def _build(self, snapshot: dict) -> TraceResult:
        return evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="MotorRoutine/Rung[3]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "tag": "StartPB",
                            "required_value": True,
                            "instruction_type": "XIC",
                            "natural_language": "StartPB is TRUE",
                        },
                        {
                            "tag": "Faulted",
                            "required_value": False,
                            "instruction_type": "XIO",
                            "natural_language": "Faulted is FALSE",
                        },
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            snapshot,
        )

    def test_satisfied_yields_target_can_be_on(self) -> None:
        result = self._build({"StartPB": True, "Faulted": False})
        self.assertEqual(_verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value)
        self.assertEqual(
            _primary_statement(result),
            "Pump_Run can be energized: all known required runtime "
            "conditions are satisfied.",
        )

    def test_blocked_yields_blocked_verdict(self) -> None:
        result = self._build({"StartPB": False, "Faulted": False})
        self.assertEqual(_verdict(result), OverallVerdict.BLOCKED.value)
        # Headline names the first blocker and its actual value.
        self.assertIn(
            "Pump_Run is blocked because StartPB is FALSE.",
            _primary_statement(result),
        )

    def test_missing_value_yields_incomplete(self) -> None:
        result = self._build({"Faulted": False})  # StartPB omitted
        self.assertEqual(_verdict(result), OverallVerdict.INCOMPLETE.value)
        self.assertIn(
            "Pump_Run cannot be fully evaluated because StartPB is missing",
            _primary_statement(result),
        )

    def test_xio_required_false_blocked_by_true(self) -> None:
        # Mirrors the spec example: required FALSE, actual TRUE -> blocked.
        result = self._build({"StartPB": True, "Faulted": True})
        self.assertEqual(_verdict(result), OverallVerdict.BLOCKED.value)
        blockers = result.platform_specific["blocking_conditions"]
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["tag"], "Faulted")
        self.assertEqual(blockers[0]["required_value"], False)
        self.assertEqual(blockers[0]["actual_value"], True)


# ---------------------------------------------------------------------------
# Comparison conditions
# ---------------------------------------------------------------------------


class ComparisonNumericTests(unittest.TestCase):
    """``Tank_Level > 80`` style conditions against numeric literals."""

    def _build(self, op: str, rhs, snapshot: dict) -> TraceResult:
        return evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="LevelRoutine/Rung[0]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "tag": "Tank_Level",
                            "comparison_operator": op,
                            "compared_with": rhs,
                            "instruction_type": (
                                "GRT" if op == ">" else "EQU"
                            ),
                            "natural_language": (
                                f"Tank_Level must be {op} {rhs}"
                            ),
                        }
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            snapshot,
        )

    def test_greater_than_satisfied(self) -> None:
        result = self._build(">", "80", {"Tank_Level": 85})
        self.assertEqual(_verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value)
        satisfied = result.platform_specific["satisfied_conditions"]
        self.assertEqual(len(satisfied), 1)
        self.assertEqual(satisfied[0]["actual_value"], 85)
        self.assertEqual(satisfied[0]["compared_with"], "80")

    def test_greater_than_blocked(self) -> None:
        result = self._build(">", "80", {"Tank_Level": 50})
        self.assertEqual(_verdict(result), OverallVerdict.BLOCKED.value)
        self.assertIn(
            "Pump_Run is blocked because Tank_Level is 50",
            _primary_statement(result),
        )
        self.assertIn("must be greater than", _primary_statement(result))

    def test_equality_against_integer_literal(self) -> None:
        result = self._build("=", "1", {"Tank_Level": 1})
        self.assertEqual(_verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value)


class ComparisonTagToTagTests(unittest.TestCase):
    """Comparison with another tag as the right-hand side."""

    def _build(self, op: str, snapshot: dict) -> TraceResult:
        return evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="CompareRoutine/Rung[0]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "tag": "Setpoint",
                            "comparison_operator": op,
                            "compared_with": "ActualValue",
                            "instruction_type": "NEQ" if op == "<>" else "EQU",
                            "natural_language": (
                                f"Setpoint must {op} ActualValue"
                            ),
                        }
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            snapshot,
        )

    def test_not_equal_satisfied(self) -> None:
        result = self._build(
            "<>", {"Setpoint": 10, "ActualValue": 20},
        )
        self.assertEqual(_verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value)

    def test_equal_blocked(self) -> None:
        result = self._build(
            "=", {"Setpoint": 10, "ActualValue": 20},
        )
        self.assertEqual(_verdict(result), OverallVerdict.BLOCKED.value)

    def test_rhs_tag_missing_marks_path_incomplete(self) -> None:
        result = self._build("=", {"Setpoint": 10})  # ActualValue absent
        self.assertEqual(_verdict(result), OverallVerdict.INCOMPLETE.value)
        missing = result.platform_specific["missing_conditions"]
        self.assertEqual(len(missing), 1)


# ---------------------------------------------------------------------------
# OR / AND-OR branches
# ---------------------------------------------------------------------------


class OrBranchTests(unittest.TestCase):
    """``or_branch_index``-grouped condition lists."""

    def _build(self, snapshot: dict) -> TraceResult:
        # ST assignment with two OR branches:
        #   branch 0: Mode = 1
        #   branch 1: Override AND NOT Faulted
        return evaluate_trace_runtime_v2(
            _trace(
                _st_assignment(
                    location="StRoutine/Block[0]",
                    assigned_value="TRUE",
                    conditions=[
                        {
                            "tag": "Mode",
                            "comparison_operator": "=",
                            "compared_with": "1",
                            "or_branch_index": 0,
                            "natural_language": "Mode must equal 1",
                        },
                        {
                            "tag": "Override",
                            "required_value": True,
                            "or_branch_index": 1,
                            "natural_language": "Override is TRUE",
                        },
                        {
                            "tag": "Faulted",
                            "required_value": False,
                            "or_branch_index": 1,
                            "natural_language": "Faulted is FALSE",
                        },
                    ],
                    target_id=TARGET_ID,
                    source_id="block::PLC01/StRoutine/0",
                    gating_logic_type="or",
                ),
                target_id=TARGET_ID,
            ),
            snapshot,
        )

    def test_one_branch_satisfied_marks_path_satisfied(self) -> None:
        # Branch 0 fails (Mode != 1) but branch 1 succeeds.
        result = self._build(
            {"Mode": 0, "Override": True, "Faulted": False},
        )
        self.assertEqual(_verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value)
        # Path-level status is also satisfied.
        path = result.platform_specific["writer_path_results"][0]
        self.assertEqual(path["status"], PathStatus.PATH_SATISFIED.value)

    def test_all_branches_blocked_marks_path_blocked(self) -> None:
        # Branch 0 fails (Mode != 1); branch 1 fails (Faulted = True).
        result = self._build(
            {"Mode": 0, "Override": True, "Faulted": True},
        )
        self.assertEqual(_verdict(result), OverallVerdict.BLOCKED.value)
        path = result.platform_specific["writer_path_results"][0]
        self.assertEqual(path["status"], PathStatus.PATH_BLOCKED.value)

    def test_branch_with_missing_value_marks_path_incomplete(self) -> None:
        # Branch 0 fails (Mode != 1); branch 1 is incomplete (Faulted
        # missing). No branch is satisfied, so the path is incomplete.
        result = self._build({"Mode": 0, "Override": True})
        self.assertEqual(_verdict(result), OverallVerdict.INCOMPLETE.value)


# ---------------------------------------------------------------------------
# Timer / counter member access
# ---------------------------------------------------------------------------


class TimerMemberTests(unittest.TestCase):
    """``Timer1's timer done bit is set`` style conditions."""

    def _build_with_member_metadata(self, snapshot: dict) -> TraceResult:
        # Modern trace_v2 propagates ``member`` directly.
        return evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="TimerRoutine/Rung[2]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "tag": "Timer1",
                            "required_value": True,
                            "member": "DN",
                            "instruction_type": "XIC",
                            "natural_language": (
                                "Timer1's timer done bit is set"
                            ),
                        }
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            snapshot,
        )

    def test_done_bit_satisfied(self) -> None:
        result = self._build_with_member_metadata({"Timer1.DN": True})
        self.assertEqual(_verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value)
        satisfied = result.platform_specific["satisfied_conditions"][0]
        self.assertEqual(satisfied["snapshot_key"], "Timer1.DN")
        self.assertEqual(satisfied["member"], "DN")

    def test_done_bit_blocked(self) -> None:
        result = self._build_with_member_metadata({"Timer1.DN": False})
        self.assertEqual(_verdict(result), OverallVerdict.BLOCKED.value)
        self.assertIn(
            "Timer1.DN", _primary_statement(result),
        )

    def test_member_inferred_from_natural_language(self) -> None:
        # Legacy traces don't carry the ``member`` field. The
        # evaluator should still find the right snapshot key by
        # parsing the natural-language phrase.
        result = evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="TimerRoutine/Rung[2]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "tag": "Timer1",
                            "required_value": True,
                            "instruction_type": "XIC",
                            # No "member" key on purpose.
                            "natural_language": (
                                "Timer1's timer enabled bit is set"
                            ),
                        }
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            {"Timer1.EN": True},
        )
        self.assertEqual(_verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value)
        satisfied = result.platform_specific["satisfied_conditions"][0]
        self.assertEqual(satisfied["snapshot_key"], "Timer1.EN")
        self.assertEqual(satisfied["member"], "EN")

    def test_acc_non_zero_treated_as_true(self) -> None:
        # XIC on .ACC: required true means non-zero.
        result = evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="TimerRoutine/Rung[3]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "tag": "Timer1",
                            "required_value": True,
                            "member": "ACC",
                            "instruction_type": "XIC",
                            "natural_language": (
                                "Timer1's accumulated value is non-zero"
                            ),
                        }
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            {"Timer1.ACC": 1500},
        )
        self.assertEqual(_verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value)


# ---------------------------------------------------------------------------
# Multiple writers
# ---------------------------------------------------------------------------


STATE_FILL_ID = "tag::PLC01/MainProgram/State_Fill"
T2_FWD_ID = "tag::PLC01/MainProgram/T2_FWD"


class LatchUnlatchConflictTests(unittest.TestCase):
    """OTL latch and OTU unlatch both satisfied at the same scan."""

    def test_latch_and_unlatch_both_satisfied_flags_conflict(self) -> None:
        latch = _writer_conditions(
            location="R02_Sequencer/Rung[1]",
            instruction_type="OTL",
            conditions=[
                {
                    "tag": "StartPB", "required_value": True,
                    "instruction_type": "XIC",
                    "natural_language": "StartPB is TRUE",
                }
            ],
            target_id=STATE_FILL_ID,
            source_id="rung::PLC01/MainProgram/R02_Sequencer/Rung[1]",
        )
        unlatch = _writer_conditions(
            location="FaultRoutine/Rung[4]",
            instruction_type="OTU",
            conditions=[
                {
                    "tag": "Faulted", "required_value": True,
                    "instruction_type": "XIC",
                    "natural_language": "Faulted is TRUE",
                }
            ],
            target_id=STATE_FILL_ID,
            source_id="rung::PLC01/MainProgram/FaultRoutine/Rung[4]",
        )
        result = evaluate_trace_runtime_v2(
            _trace(latch, unlatch, target_id=STATE_FILL_ID),
            {"StartPB": True, "Faulted": True},
        )
        self.assertEqual(
            _verdict(result),
            OverallVerdict.CONFLICT_OR_SCAN_ORDER_DEPENDENT.value,
        )
        self.assertIn(
            "has conflicting runtime paths", _primary_statement(result)
        )
        conflicts = result.platform_specific["conflicts"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            conflicts[0]["true_writer"]["instruction_type"], "OTL"
        )
        self.assertEqual(
            conflicts[0]["false_writer"]["instruction_type"], "OTU"
        )

    def test_latch_satisfied_unlatch_blocked_yields_can_be_on(self) -> None:
        latch = _writer_conditions(
            location="R02_Sequencer/Rung[1]",
            instruction_type="OTL",
            conditions=[
                {
                    "tag": "StartPB", "required_value": True,
                    "instruction_type": "XIC",
                    "natural_language": "StartPB is TRUE",
                }
            ],
            target_id=STATE_FILL_ID,
            source_id="rung::PLC01/MainProgram/R02_Sequencer/Rung[1]",
        )
        unlatch = _writer_conditions(
            location="FaultRoutine/Rung[4]",
            instruction_type="OTU",
            conditions=[
                {
                    "tag": "Faulted", "required_value": True,
                    "instruction_type": "XIC",
                    "natural_language": "Faulted is TRUE",
                }
            ],
            target_id=STATE_FILL_ID,
            source_id="rung::PLC01/MainProgram/FaultRoutine/Rung[4]",
        )
        result = evaluate_trace_runtime_v2(
            _trace(latch, unlatch, target_id=STATE_FILL_ID),
            {"StartPB": True, "Faulted": False},
        )
        self.assertEqual(
            _verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value
        )
        self.assertEqual(result.platform_specific["conflicts"], [])


class MultipleWritersTrueFalseConflictTests(unittest.TestCase):
    """ST writer assigns TRUE and another ST writer assigns FALSE."""

    def test_st_true_and_st_false_both_satisfied(self) -> None:
        st_true = _st_assignment(
            location="StRoutine/Block[0]",
            assigned_value="TRUE",
            conditions=[
                {
                    "tag": "JogFwd", "required_value": True,
                    "or_branch_index": 0,
                    "natural_language": "JogFwd is TRUE",
                }
            ],
            target_id=T2_FWD_ID,
            source_id="block::PLC01/StRoutine/0",
        )
        st_false = _st_assignment(
            location="StRoutine/Block[1]",
            assigned_value="FALSE",
            conditions=[
                {
                    "tag": "JogRev", "required_value": True,
                    "or_branch_index": 0,
                    "natural_language": "JogRev is TRUE",
                }
            ],
            target_id=T2_FWD_ID,
            source_id="block::PLC01/StRoutine/1",
        )
        result = evaluate_trace_runtime_v2(
            _trace(st_true, st_false, target_id=T2_FWD_ID),
            {"JogFwd": True, "JogRev": True},
        )
        self.assertEqual(
            _verdict(result),
            OverallVerdict.CONFLICT_OR_SCAN_ORDER_DEPENDENT.value,
        )
        # The primary statement matches the spec template.
        self.assertIn(
            "T2_FWD has conflicting runtime paths: one writer assigns "
            "TRUE while another writer assigns FALSE. Final value may "
            "depend on execution order.",
            _primary_statement(result),
        )


# ---------------------------------------------------------------------------
# Incomplete due to missing values
# ---------------------------------------------------------------------------


class IncompleteDueToMissingValuesTests(unittest.TestCase):
    """No path satisfied, no path blocked, but missing values prevent
    a verdict -- the spec's example for ``Valve_Open``."""

    def test_lists_missing_tags_in_summary(self) -> None:
        target_id = "tag::PLC01/MainProgram/Valve_Open"
        concl = _writer_conditions(
            location="ValveRoutine/Rung[0]",
            instruction_type="OTE",
            conditions=[
                {
                    "tag": "AutoMode", "required_value": True,
                    "instruction_type": "XIC",
                    "natural_language": "AutoMode is TRUE",
                },
                {
                    "tag": "Faulted", "required_value": False,
                    "instruction_type": "XIO",
                    "natural_language": "Faulted is FALSE",
                },
            ],
            target_id=target_id,
            source_id="rung::PLC01/MainProgram/ValveRoutine/Rung[0]",
        )
        result = evaluate_trace_runtime_v2(
            _trace(concl, target_id=target_id),
            {},
        )
        self.assertEqual(_verdict(result), OverallVerdict.INCOMPLETE.value)
        self.assertIn(
            "Valve_Open cannot be fully evaluated because AutoMode and "
            "Faulted are missing from the runtime snapshot.",
            _primary_statement(result),
        )


# ---------------------------------------------------------------------------
# Unsupported conditions
# ---------------------------------------------------------------------------


class UnsupportedConditionTests(unittest.TestCase):
    """Conditions the evaluator can't interpret must not crash and
    must be preserved in the structured metadata."""

    def test_lim_three_operand_marked_unsupported(self) -> None:
        # LIM with 3 operands isn't supported in v1; we mark it
        # unsupported and continue evaluating the other condition.
        result = evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="LimRoutine/Rung[0]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "comparison_operator": "<=lim<=",
                            "compared_operands": ["Low", "Test", "High"],
                            "instruction_type": "LIM",
                            "natural_language": (
                                "Test must be between Low and High"
                            ),
                        },
                        {
                            "tag": "Permit", "required_value": True,
                            "instruction_type": "XIC",
                            "natural_language": "Permit is TRUE",
                        },
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            {"Permit": True},
        )
        # Permit satisfied -> path satisfied (LIM doesn't block).
        self.assertEqual(_verdict(result), OverallVerdict.TARGET_CAN_BE_ON.value)
        unsupported = result.platform_specific["unsupported_conditions"]
        self.assertEqual(len(unsupported), 1)
        self.assertEqual(unsupported[0]["comparison_operator"], "<=lim<=")
        # Per-path metadata reflects the satisfied outcome.
        path = result.platform_specific["writer_path_results"][0]
        self.assertEqual(path["status"], PathStatus.PATH_SATISFIED.value)

    def test_only_unsupported_condition_yields_path_unsupported(self) -> None:
        result = evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="UnknownRoutine/Rung[0]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "comparison_operator": "<=lim<=",
                            "compared_operands": ["Low", "Test", "High"],
                            "instruction_type": "LIM",
                            "natural_language": (
                                "Test must be between Low and High"
                            ),
                        }
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            {},
        )
        path = result.platform_specific["writer_path_results"][0]
        self.assertEqual(path["status"], PathStatus.PATH_UNSUPPORTED.value)
        # No satisfied or blocking paths -> blocked.
        self.assertEqual(_verdict(result), OverallVerdict.BLOCKED.value)

    def test_condition_without_tag_or_op_is_unsupported_not_crash(self) -> None:
        # Defensive: a condition row that has nothing actionable
        # should produce an unsupported result without raising.
        result = evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="Mystery/Rung[0]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "natural_language": "something complex",
                            "instruction_type": "FOO",
                        }
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            {},
        )
        unsupported = result.platform_specific["unsupported_conditions"]
        self.assertEqual(len(unsupported), 1)


# ---------------------------------------------------------------------------
# Structural / metadata sanity
# ---------------------------------------------------------------------------


class StructuredMetadataTests(unittest.TestCase):
    """Verify the platform_specific shape matches the v2 spec exactly."""

    def _build(self) -> TraceResult:
        return evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="MotorRoutine/Rung[3]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "tag": "StartPB", "required_value": True,
                            "instruction_type": "XIC",
                            "natural_language": "StartPB is TRUE",
                        }
                    ],
                    target_id=TARGET_ID,
                    source_id=RUNG_ID,
                ),
                target_id=TARGET_ID,
            ),
            {"StartPB": True},
        )

    def test_platform_specific_keys(self) -> None:
        ps = self._build().platform_specific
        self.assertEqual(ps["trace_version"], "runtime_v2")
        self.assertTrue(ps["runtime_snapshot_evaluated"])
        for key in (
            "overall_verdict",
            "writer_path_results",
            "blocking_conditions",
            "satisfied_conditions",
            "missing_conditions",
            "unsupported_conditions",
            "conflicts",
        ):
            self.assertIn(key, ps, f"missing key: {key}")

    def test_writer_path_results_carry_write_effect(self) -> None:
        ps = self._build().platform_specific
        path = ps["writer_path_results"][0]
        self.assertEqual(path["write_effect"], WriteEffect.SETS_TRUE.value)
        self.assertEqual(path["instruction_type"], "OTE")
        self.assertEqual(path["location"], "MotorRoutine/Rung[3]")
        self.assertEqual(path["status"], PathStatus.PATH_SATISFIED.value)
        self.assertEqual(len(path["conditions"]), 1)

    def test_primary_conclusion_is_first(self) -> None:
        # The runtime v2 verdict must be the first conclusion so a
        # consumer reading top-down sees the operational headline
        # before any design-time detail.
        result = self._build()
        first = result.conclusions[0]
        self.assertEqual(
            first.platform_specific.get("trace_v2_kind"),
            "runtime_v2_verdict",
        )

    def test_original_trace_v2_conclusions_preserved(self) -> None:
        # The writer_conditions conclusion must still be present after
        # runtime v2 augmentation.
        result = self._build()
        kinds = [
            c.platform_specific.get("trace_v2_kind")
            for c in result.conclusions
        ]
        self.assertIn("writer_conditions", kinds)


if __name__ == "__main__":
    unittest.main(verbosity=2)
