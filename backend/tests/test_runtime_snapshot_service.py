"""Tests for :mod:`app.services.runtime_snapshot_service`.

Covers the v1 spec:

* all conditions satisfied -> "all known runtime conditions ... are
  satisfied" summary + per-tag runtime_satisfied conclusions.
* one blocking condition -> "X is blocked by ..." summary +
  runtime_blocking conclusion with the spec-mandated "is blocking
  because it is ... but must be ..." wording.
* missing runtime values -> "Runtime values are missing for ..."
  summary + runtime_missing conclusions.
* XIO required-FALSE condition blocked by a TRUE value -- the
  asymmetric case where the required value is FALSE but the snapshot
  supplies TRUE.
* multiple writer_conditions handled conservatively -- when both a
  latch and an unlatch contribute conditions, every contribution is
  bucketed, and any blocking is enough to mark the target as blocked.

The tests build :class:`TraceResult` instances directly (rather than
running them through ``trace_object_v2``) so the evaluator's contract
is exercised in isolation. End-to-end tests for the route are added
alongside the existing trace-v1 pipeline tests in a separate module.
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
from app.services.runtime_snapshot_service import (  # noqa: E402
    evaluate_trace_conditions,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


TARGET_ID = "tag::PLC01/MainProgram/Pump_Run"
ROUTINE_RUNG_ID = "rung::PLC01/MainProgram/MotorRoutine/Rung[3]"


def _writer_conditions_conclusion(
    location: str,
    instruction_type: str,
    conditions: list[dict],
    target_id: str = TARGET_ID,
    source_id: str = ROUTINE_RUNG_ID,
    statement: str = "Pump_Run is energized when ...",
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


def _trace(
    *conclusions: TruthConclusion,
    target_id: str = TARGET_ID,
    summary: str | None = None,
) -> TraceResult:
    return TraceResult(
        target_object_id=target_id,
        conclusions=list(conclusions),
        summary=summary,
        platform_specific={"trace_version": "v2"},
    )


def _kinds(result: TraceResult) -> list[str | None]:
    return [
        c.platform_specific.get("trace_v2_kind") for c in result.conclusions
    ]


# ---------------------------------------------------------------------------
# All conditions satisfied
# ---------------------------------------------------------------------------


class AllSatisfiedTests(unittest.TestCase):
    """All known runtime conditions match the design-time requirement."""

    def setUp(self) -> None:
        self.concl = _writer_conditions_conclusion(
            location="MotorRoutine/Rung[3]",
            instruction_type="OTE",
            conditions=[
                {
                    "tag": "StartPB",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
                {
                    "tag": "AutoMode",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
                {
                    "tag": "Faulted",
                    "required_value": False,
                    "instruction_type": "XIO",
                },
            ],
        )

    def test_three_runtime_satisfied_conclusions_are_prepended(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={
                "StartPB": True,
                "AutoMode": True,
                "Faulted": False,
            },
        )
        kinds = _kinds(result)
        self.assertEqual(kinds[:3], ["runtime_satisfied"] * 3)
        # The original writer_conditions conclusion is preserved.
        self.assertIn("writer_conditions", kinds)

    def test_platform_specific_lists_three_satisfied_no_others(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={
                "StartPB": True,
                "AutoMode": True,
                "Faulted": False,
            },
        )
        ps = result.platform_specific
        self.assertEqual(ps["trace_version"], "runtime_v1")
        self.assertTrue(ps["runtime_snapshot_evaluated"])
        self.assertEqual(len(ps["satisfied_conditions"]), 3)
        self.assertEqual(ps["blocking_conditions"], [])
        self.assertEqual(ps["missing_conditions"], [])
        tags = {row["tag"] for row in ps["satisfied_conditions"]}
        self.assertEqual(tags, {"StartPB", "AutoMode", "Faulted"})

    def test_summary_says_all_satisfied(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={
                "StartPB": True,
                "AutoMode": True,
                "Faulted": False,
            },
        )
        self.assertIsNotNone(result.summary)
        assert result.summary is not None
        self.assertIn(
            "All known runtime conditions for Pump_Run are satisfied.",
            result.summary,
        )

    def test_satisfied_statement_quotes_required_value(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={
                "StartPB": True,
                "AutoMode": True,
                "Faulted": False,
            },
        )
        satisfied_statements = [
            c.statement
            for c in result.conclusions
            if c.platform_specific.get("trace_v2_kind") == "runtime_satisfied"
        ]
        self.assertIn(
            "StartPB is TRUE as required (required by MotorRoutine/Rung[3]).",
            satisfied_statements,
        )
        self.assertIn(
            "Faulted is FALSE as required (required by MotorRoutine/Rung[3]).",
            satisfied_statements,
        )


# ---------------------------------------------------------------------------
# One blocking condition
# ---------------------------------------------------------------------------


class OneBlockingConditionTests(unittest.TestCase):
    """A single XIC tag that should be TRUE is FALSE on the live system."""

    def setUp(self) -> None:
        self.concl = _writer_conditions_conclusion(
            location="MotorRoutine/Rung[3]",
            instruction_type="OTE",
            conditions=[
                {
                    "tag": "StartPB",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
                {
                    "tag": "AutoMode",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
            ],
        )

    def test_blocking_record_captures_required_and_actual(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={"StartPB": True, "AutoMode": False},
        )
        ps = result.platform_specific
        self.assertEqual(len(ps["satisfied_conditions"]), 1)
        self.assertEqual(len(ps["blocking_conditions"]), 1)
        block = ps["blocking_conditions"][0]
        self.assertEqual(block["tag"], "AutoMode")
        self.assertEqual(block["required_value"], True)
        self.assertEqual(block["actual_value"], False)
        self.assertEqual(block["location"], "MotorRoutine/Rung[3]")

    def test_summary_says_target_is_blocked_by_tag(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={"StartPB": True, "AutoMode": False},
        )
        self.assertIsNotNone(result.summary)
        assert result.summary is not None
        self.assertIn("Pump_Run is blocked by AutoMode.", result.summary)
        self.assertNotIn(
            "All known runtime conditions", result.summary,
        )

    def test_blocking_statement_uses_spec_wording(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={"StartPB": True, "AutoMode": False},
        )
        blocking_statements = [
            c.statement
            for c in result.conclusions
            if c.platform_specific.get("trace_v2_kind") == "runtime_blocking"
        ]
        self.assertEqual(len(blocking_statements), 1)
        self.assertIn(
            "AutoMode is blocking because it is FALSE but must be TRUE",
            blocking_statements[0],
        )


# ---------------------------------------------------------------------------
# Missing runtime values
# ---------------------------------------------------------------------------


class MissingRuntimeValuesTests(unittest.TestCase):
    """Tags absent from the snapshot are flagged separately."""

    def setUp(self) -> None:
        self.concl = _writer_conditions_conclusion(
            location="MotorRoutine/Rung[3]",
            instruction_type="OTE",
            conditions=[
                {
                    "tag": "StartPB",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
                {
                    "tag": "AutoMode",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
                {
                    "tag": "Faulted",
                    "required_value": False,
                    "instruction_type": "XIO",
                },
            ],
        )

    def test_two_tags_are_bucketed_as_missing(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={"Faulted": False},
        )
        ps = result.platform_specific
        self.assertEqual(len(ps["satisfied_conditions"]), 1)
        self.assertEqual(len(ps["blocking_conditions"]), 0)
        self.assertEqual(len(ps["missing_conditions"]), 2)
        missing_tags = [m["tag"] for m in ps["missing_conditions"]]
        self.assertEqual(missing_tags, ["StartPB", "AutoMode"])

    def test_missing_summary_lists_tags(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={"Faulted": False},
        )
        self.assertIsNotNone(result.summary)
        assert result.summary is not None
        self.assertIn(
            "Runtime values are missing for StartPB and AutoMode.",
            result.summary,
        )

    def test_missing_conclusion_marked_low_confidence(self) -> None:
        result = evaluate_trace_conditions(
            _trace(self.concl),
            runtime_snapshot={"Faulted": False},
        )
        missing_concls = [
            c
            for c in result.conclusions
            if c.platform_specific.get("trace_v2_kind") == "runtime_missing"
        ]
        self.assertEqual(len(missing_concls), 2)
        for c in missing_concls:
            self.assertEqual(c.confidence, ConfidenceLevel.LOW)
            self.assertEqual(
                c.truth_context, TruthContextType.RUNTIME_TRUTH
            )
            self.assertTrue(c.recommended_checks)


# ---------------------------------------------------------------------------
# XIO required FALSE blocked by TRUE -- the asymmetric case
# ---------------------------------------------------------------------------


class XIORequiredFalseBlockedByTrueTests(unittest.TestCase):
    """A required-FALSE condition (XIO) reads TRUE on the live system."""

    def test_required_false_actual_true_is_blocking(self) -> None:
        concl = _writer_conditions_conclusion(
            location="MotorRoutine/Rung[3]",
            instruction_type="OTE",
            conditions=[
                {
                    "tag": "Faulted",
                    "required_value": False,
                    "instruction_type": "XIO",
                },
            ],
        )
        result = evaluate_trace_conditions(
            _trace(concl), runtime_snapshot={"Faulted": True},
        )

        ps = result.platform_specific
        self.assertEqual(len(ps["blocking_conditions"]), 1)
        block = ps["blocking_conditions"][0]
        self.assertEqual(block["tag"], "Faulted")
        self.assertEqual(block["required_value"], False)
        self.assertEqual(block["actual_value"], True)

        blocking_statements = [
            c.statement
            for c in result.conclusions
            if c.platform_specific.get("trace_v2_kind") == "runtime_blocking"
        ]
        # Mirrors the example wording in the spec.
        self.assertEqual(len(blocking_statements), 1)
        self.assertIn(
            "Faulted is blocking because it is TRUE but must be FALSE",
            blocking_statements[0],
        )

        self.assertIsNotNone(result.summary)
        assert result.summary is not None
        self.assertIn("Pump_Run is blocked by Faulted.", result.summary)


# ---------------------------------------------------------------------------
# Multiple writer conditions handled conservatively
# ---------------------------------------------------------------------------


class MultipleWriterConditionsConservativeTests(unittest.TestCase):
    """Two ``writer_conditions`` (latch + unlatch) on the same target.

    The evaluator must walk **every** writer's conditions and bucket
    them independently. If any condition is blocked, the target is
    reported as blocked even when the other writer's conditions look
    fine on their own.
    """

    def _build(self) -> TraceResult:
        latch = _writer_conditions_conclusion(
            location="R02_Sequencer/Rung[1]",
            instruction_type="OTL",
            statement="State_Fill is latched ON in R02_Sequencer/Rung[1].",
            conditions=[
                {
                    "tag": "StartPB",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
                {
                    "tag": "AutoMode",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
                {
                    "tag": "Faulted",
                    "required_value": False,
                    "instruction_type": "XIO",
                },
            ],
            target_id="tag::PLC01/MainProgram/State_Fill",
            source_id="rung::PLC01/MainProgram/R02_Sequencer/Rung[1]",
        )
        unlatch = _writer_conditions_conclusion(
            location="FaultRoutine/Rung[4]",
            instruction_type="OTU",
            statement="State_Fill is unlatched in FaultRoutine/Rung[4].",
            conditions=[
                {
                    "tag": "Faulted",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
            ],
            target_id="tag::PLC01/MainProgram/State_Fill",
            source_id="rung::PLC01/MainProgram/FaultRoutine/Rung[4]",
        )
        return _trace(
            latch,
            unlatch,
            target_id="tag::PLC01/MainProgram/State_Fill",
        )

    def test_both_writers_contribute_conditions(self) -> None:
        result = evaluate_trace_conditions(
            self._build(),
            runtime_snapshot={
                "StartPB": True,
                "AutoMode": True,
                "Faulted": False,
            },
        )
        ps = result.platform_specific
        # Latch: StartPB(satisfied), AutoMode(satisfied),
        # Faulted required FALSE -> satisfied. 3 satisfied total.
        # Unlatch: Faulted required TRUE but actual is FALSE ->
        # blocking. 1 blocking total.
        self.assertEqual(len(ps["satisfied_conditions"]), 3)
        self.assertEqual(len(ps["blocking_conditions"]), 1)
        self.assertEqual(len(ps["missing_conditions"]), 0)

    def test_blocking_in_any_writer_marks_target_blocked(self) -> None:
        result = evaluate_trace_conditions(
            self._build(),
            runtime_snapshot={
                "StartPB": True,
                "AutoMode": True,
                "Faulted": False,
            },
        )
        # Note: the latch path alone is "clean". The conservative
        # aggregation still surfaces the unlatch path's blocked
        # condition because we don't try to figure out which writer
        # "wins" at runtime.
        self.assertIsNotNone(result.summary)
        assert result.summary is not None
        self.assertIn(
            "State_Fill is blocked by Faulted.", result.summary,
        )

    def test_blocking_record_points_to_correct_rung(self) -> None:
        result = evaluate_trace_conditions(
            self._build(),
            runtime_snapshot={
                "StartPB": True,
                "AutoMode": True,
                "Faulted": False,
            },
        )
        block = result.platform_specific["blocking_conditions"][0]
        self.assertEqual(block["location"], "FaultRoutine/Rung[4]")
        self.assertEqual(block["required_value"], True)
        self.assertEqual(block["actual_value"], False)

    def test_missing_snapshot_value_lists_unlatch_tag(self) -> None:
        """Even with the latch path partially satisfied, a missing
        ``Faulted`` snapshot value is still reported -- and *only*
        reported once, deduplicated across writers."""

        # Provide values for the latch path only; omit Faulted.
        result = evaluate_trace_conditions(
            self._build(),
            runtime_snapshot={"StartPB": True, "AutoMode": True},
        )
        ps = result.platform_specific
        self.assertEqual(len(ps["missing_conditions"]), 2)
        missing_tags = {m["tag"] for m in ps["missing_conditions"]}
        self.assertEqual(missing_tags, {"Faulted"})
        # Two missing records (one per writer) -- but the summary
        # should mention Faulted only once.
        self.assertIsNotNone(result.summary)
        assert result.summary is not None
        self.assertEqual(
            result.summary.count("Faulted"), 1,
            f"Faulted should be deduped in summary; got: {result.summary}",
        )


# ---------------------------------------------------------------------------
# Defensive: non-writer_conditions conclusions are ignored
# ---------------------------------------------------------------------------


class IgnoresNonWriterConditionsTests(unittest.TestCase):
    def test_writer_what_conclusions_are_not_evaluated(self) -> None:
        # writer_what carries no conditions list -- the evaluator must
        # silently skip it instead of crashing.
        writer_what = TruthConclusion(
            statement="Pump_Run is energized in MotorRoutine/Rung[3].",
            subject_ids=[TARGET_ID, ROUTINE_RUNG_ID],
            truth_context=TruthContextType.DESIGN_TRUTH,
            confidence=ConfidenceLevel.HIGH,
            platform_specific={
                "trace_v2_kind": "writer_what",
                "instruction_type": "OTE",
                "location": "MotorRoutine/Rung[3]",
            },
        )
        result = evaluate_trace_conditions(
            _trace(writer_what), runtime_snapshot={"StartPB": True},
        )
        ps = result.platform_specific
        self.assertEqual(ps["satisfied_conditions"], [])
        self.assertEqual(ps["blocking_conditions"], [])
        self.assertEqual(ps["missing_conditions"], [])
        self.assertTrue(ps["runtime_snapshot_evaluated"])

    def test_conditions_without_tag_or_required_are_skipped(self) -> None:
        # Comparison / one-shot phrases land here in production.
        concl = _writer_conditions_conclusion(
            location="Calc/Rung[1]",
            instruction_type="OTE",
            conditions=[
                {
                    "natural_language": "A must equal B",
                    "instruction_type": "EQU",
                },
                {"tag": "X", "instruction_type": "XIC"},
                {
                    "tag": "Permit",
                    "required_value": True,
                    "instruction_type": "XIC",
                },
            ],
        )
        result = evaluate_trace_conditions(
            _trace(concl), runtime_snapshot={"Permit": True},
        )
        ps = result.platform_specific
        self.assertEqual(len(ps["satisfied_conditions"]), 1)
        self.assertEqual(ps["satisfied_conditions"][0]["tag"], "Permit")
        self.assertEqual(ps["blocking_conditions"], [])
        self.assertEqual(ps["missing_conditions"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
