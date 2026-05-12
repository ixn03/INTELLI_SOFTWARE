"""Unit tests for ``app.services.trace_service`` (Trace v1).

Covers the deterministic ``trace_object`` entry point against
hand-built normalized fixtures. Each test constructs the minimum set
of ``ControlObject`` / ``Relationship`` objects needed to exercise one
behavior, so a failure points directly at the rule that broke.

Legacy ``trace_tag`` is intentionally not exercised here; those code
paths are unchanged and covered by the existing API integration.

Run with::

    python -m unittest discover -s backend/tests
    # or, from backend/:
    python -m unittest tests.test_trace_service

No external test dependencies (stdlib ``unittest`` only).
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
)
from app.services.trace_service import (  # noqa: E402
    WRITER_RELATIONSHIP_TYPES,
    build_trace_summary,
    detect_multiple_writers,
    find_object_by_id,
    format_relationship_detail,
    get_downstream_object_ids,
    get_reader_relationships,
    get_upstream_object_ids,
    get_writer_relationships,
    trace_object,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _tag(tag_id: str, name: str, *, unresolved: bool = False) -> ControlObject:
    return ControlObject(
        id=tag_id,
        name=name,
        object_type=ControlObjectType.TAG,
        source_platform="rockwell",
        platform_specific=(
            {"unresolved": True} if unresolved else {}
        ),
    )


def _rung(rung_id: str, name: str) -> ControlObject:
    return ControlObject(
        id=rung_id,
        name=name,
        object_type=ControlObjectType.RUNG,
        source_platform="rockwell",
    )


def _routine(routine_id: str, name: str) -> ControlObject:
    return ControlObject(
        id=routine_id,
        name=name,
        object_type=ControlObjectType.ROUTINE,
        source_platform="rockwell",
    )


def _writes(source: str, target: str) -> Relationship:
    return Relationship(
        source_id=source,
        target_id=target,
        relationship_type=RelationshipType.WRITES,
        source_platform="rockwell",
    )


def _reads(source: str, target: str) -> Relationship:
    return Relationship(
        source_id=source,
        target_id=target,
        relationship_type=RelationshipType.READS,
        source_platform="rockwell",
    )


def _contains(source: str, target: str) -> Relationship:
    return Relationship(
        source_id=source,
        target_id=target,
        relationship_type=RelationshipType.CONTAINS,
        source_platform="rockwell",
    )


# -- Rich helpers for location-aware tests ----------------------------------


def _located_writes(
    source: str, target: str, *, routine: str, rung: int, instr: str,
) -> Relationship:
    """A WRITES relationship with the same source_location /
    platform_specific shape the normalization service emits."""
    return Relationship(
        source_id=source,
        target_id=target,
        relationship_type=RelationshipType.WRITES,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}/"
            f"Rung[{rung}]"
        ),
        platform_specific={"instruction_type": instr},
    )


def _located_reads(
    source: str, target: str, *, routine: str, rung: int, instr: str,
) -> Relationship:
    """A READS relationship with realistic location metadata."""
    return Relationship(
        source_id=source,
        target_id=target,
        relationship_type=RelationshipType.READS,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}/"
            f"Rung[{rung}]"
        ),
        platform_specific={
            "instruction_type": instr,
            "examined_value": instr == "XIC",
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TraceObjectTests(unittest.TestCase):
    """Behaviour tests for ``trace_object``."""

    # -- 1. One writer / one reader -----------------------------------

    def test_one_writer_one_reader(self) -> None:
        tag_id = "tag::Motor_Run"
        writer_id = "rung::Writer1"
        reader_id = "rung::Reader1"
        objects = [
            _tag(tag_id, "Motor_Run"),
            _rung(writer_id, "Rung[0]"),
            _rung(reader_id, "Rung[1]"),
        ]
        rels = [
            _writes(writer_id, tag_id),
            _reads(reader_id, tag_id),
        ]

        result = trace_object(tag_id, objects, rels)

        self.assertEqual(len(result.writer_relationships), 1)
        self.assertEqual(len(result.reader_relationships), 1)
        self.assertEqual(result.upstream_object_ids, [writer_id])
        self.assertEqual(result.confidence, ConfidenceLevel.HIGH)

        # Conclusions: one for writers, one for readers, no
        # multiple-writer/unresolved conclusions. The wording uses
        # "is written in N place(s)" / "is read in N place(s)" so
        # the same statements work for both bare and located fixtures.
        statements = [c.statement for c in result.conclusions]
        self.assertTrue(
            any(s.startswith("Motor_Run is written in 1 place(s)")
                for s in statements),
            f"missing writer conclusion: {statements}",
        )
        self.assertTrue(
            any(s.startswith("Motor_Run is read in 1 place(s)")
                for s in statements),
            f"missing reader conclusion: {statements}",
        )
        self.assertFalse(
            any("Multiple writers" in s for s in statements),
            f"unexpected multiple-writer conclusion: {statements}",
        )
        self.assertFalse(
            any("unresolved" in s for s in statements),
            f"unexpected unresolved conclusion: {statements}",
        )

    # -- 2. No writer found -------------------------------------------

    def test_no_writer_found(self) -> None:
        tag_id = "tag::OrphanTag"
        objects = [_tag(tag_id, "OrphanTag")]
        rels: list[Relationship] = []

        result = trace_object(tag_id, objects, rels)

        self.assertEqual(result.writer_relationships, [])
        self.assertEqual(result.reader_relationships, [])
        self.assertEqual(result.confidence, ConfidenceLevel.LOW)

        statements = [c.statement for c in result.conclusions]
        self.assertIn(
            "No writer found for OrphanTag in normalized logic.",
            statements,
        )
        # The matching recommended_check is surfaced at the top level.
        self.assertIn(
            "Verify the tag is written in another routine, "
            "controller, HMI, external system, or online edit.",
            result.recommended_checks,
        )

    # -- 3. Multiple writers ------------------------------------------

    def test_multiple_writers(self) -> None:
        tag_id = "tag::Conflict"
        writer_a = "rung::WriterA"
        writer_b = "rung::WriterB"
        objects = [
            _tag(tag_id, "Conflict"),
            _rung(writer_a, "Rung[0]"),
            _rung(writer_b, "Rung[5]"),
        ]
        rels = [
            _writes(writer_a, tag_id),
            _writes(writer_b, tag_id),
        ]

        result = trace_object(tag_id, objects, rels)

        self.assertEqual(len(result.writer_relationships), 2)
        self.assertTrue(detect_multiple_writers(tag_id, rels))
        self.assertEqual(
            sorted(result.upstream_object_ids),
            sorted([writer_a, writer_b]),
        )

        statements = [c.statement for c in result.conclusions]
        self.assertTrue(
            any("Multiple writers detected" in s for s in statements),
            f"missing multiple-writer conclusion: {statements}",
        )
        # The recommended check now names the offending locations
        # using the "Review X and Y" form. With bare relationships
        # (no source_location), the helper falls back to the
        # source_id and still produces an actionable string.
        self.assertTrue(
            any(c.startswith("Review ") and "to confirm intended "
                "priority/scan behavior." in c
                for c in result.recommended_checks),
            f"missing review check: {result.recommended_checks}",
        )
        self.assertIn(
            "Multiple writers detected; final state may depend on "
            "execution context or scan order.",
            result.summary,
        )

    # -- 4. Unresolved tag object -------------------------------------

    def test_unresolved_tag_object(self) -> None:
        tag_id = "tag::PLC01/MainProgram/Stub#unresolved"
        writer_id = "rung::WriterX"
        objects = [
            _tag(tag_id, "Stub", unresolved=True),
            _rung(writer_id, "Rung[0]"),
        ]
        rels = [_writes(writer_id, tag_id)]

        result = trace_object(tag_id, objects, rels)

        # We still have a real WRITES edge, so confidence is MEDIUM
        # (downgraded from HIGH because the target is a stub).
        self.assertEqual(result.confidence, ConfidenceLevel.MEDIUM)

        statements = [c.statement for c in result.conclusions]
        self.assertTrue(
            any("unresolved or absent" in s for s in statements),
            f"missing unresolved conclusion: {statements}",
        )
        self.assertIn(
            "Verify tag mapping or parser coverage for this operand.",
            result.recommended_checks,
        )

    def test_missing_tag_object_treated_as_unresolved(self) -> None:
        """Target id not present in control_objects at all."""
        tag_id = "tag::DefinitelyNotThere"
        result = trace_object(tag_id, control_objects=[], relationships=[])

        # No relationships AND not in objects -> LOW confidence,
        # plus the unresolved conclusion.
        self.assertEqual(result.confidence, ConfidenceLevel.LOW)
        statements = [c.statement for c in result.conclusions]
        self.assertTrue(
            any("unresolved or absent" in s for s in statements),
            f"missing unresolved conclusion: {statements}",
        )

    # -- 5. CONTAINS relationship ignored as cause/effect -------------

    def test_contains_not_counted_as_cause_or_effect(self) -> None:
        tag_id = "tag::Motor_Run"
        rung_id = "rung::OneWriter"
        routine_id = "routine::MainRoutine"
        objects = [
            _tag(tag_id, "Motor_Run"),
            _rung(rung_id, "Rung[0]"),
            _routine(routine_id, "MainRoutine"),
        ]
        rels = [
            _contains(routine_id, rung_id),  # structural, NOT a writer
            _writes(rung_id, tag_id),
        ]

        result = trace_object(tag_id, objects, rels)

        # Exactly one writer (the rung) -- the routine's CONTAINS edge
        # must not inflate the writer count or appear in upstream ids.
        self.assertEqual(len(result.writer_relationships), 1)
        self.assertEqual(result.upstream_object_ids, [rung_id])
        self.assertNotIn(routine_id, result.upstream_object_ids)

        # And confirm none of the writer/reader rels are CONTAINS.
        for r in result.writer_relationships:
            self.assertNotEqual(
                r.relationship_type, RelationshipType.CONTAINS
            )
        for r in result.reader_relationships:
            self.assertNotEqual(
                r.relationship_type, RelationshipType.CONTAINS
            )

    # -- 6. Downstream readers detected -------------------------------

    def test_downstream_readers_detected(self) -> None:
        tag_id = "tag::Motor_Run"
        writer_id = "rung::Writer"
        reader_ids = [f"rung::Reader_{i}" for i in range(3)]
        objects = (
            [_tag(tag_id, "Motor_Run"), _rung(writer_id, "Rung[0]")]
            + [_rung(rid, rid.split("::")[-1]) for rid in reader_ids]
        )
        rels = [_writes(writer_id, tag_id)] + [
            _reads(rid, tag_id) for rid in reader_ids
        ]

        result = trace_object(tag_id, objects, rels)

        self.assertEqual(len(result.reader_relationships), 3)
        reader_source_ids = {
            r.source_id for r in result.reader_relationships
        }
        self.assertEqual(reader_source_ids, set(reader_ids))

        statements = [c.statement for c in result.conclusions]
        self.assertTrue(
            any(s.startswith("Motor_Run is read in 3 place(s)")
                for s in statements),
            f"missing reader conclusion: {statements}",
        )
        self.assertIn("Read in 3 place(s)", result.summary)

    # -- Helper / sanity tests ----------------------------------------

    def test_writer_relationship_types_includes_expected(self) -> None:
        """The constant must include exactly the spec'd cause/effect
        types -- not READS, not CONTAINS, not CALLS."""
        expected = {
            RelationshipType.WRITES,
            RelationshipType.LATCHES,
            RelationshipType.UNLATCHES,
            RelationshipType.RESETS,
            RelationshipType.CALCULATES,
            RelationshipType.SCALES,
        }
        self.assertEqual(set(WRITER_RELATIONSHIP_TYPES), expected)
        self.assertNotIn(
            RelationshipType.READS, WRITER_RELATIONSHIP_TYPES
        )
        self.assertNotIn(
            RelationshipType.CONTAINS, WRITER_RELATIONSHIP_TYPES
        )

    def test_helpers_match_trace_object(self) -> None:
        """The standalone helpers and ``trace_object`` agree on the
        same fixture (no silent divergence between the two views)."""

        tag_id = "tag::Motor_Run"
        rung_a = "rung::A"
        rung_b = "rung::B"
        reader = "rung::R"
        objects = [
            _tag(tag_id, "Motor_Run"),
            _rung(rung_a, "A"),
            _rung(rung_b, "B"),
            _rung(reader, "R"),
        ]
        rels = [
            _writes(rung_a, tag_id),
            _writes(rung_b, tag_id),
            _reads(reader, tag_id),
        ]

        helper_writers = get_writer_relationships(tag_id, rels)
        helper_readers = get_reader_relationships(tag_id, rels)
        helper_upstream = get_upstream_object_ids(tag_id, rels)
        helper_downstream = get_downstream_object_ids(tag_id, rels)

        result = trace_object(tag_id, objects, rels)

        self.assertEqual(
            len(result.writer_relationships), len(helper_writers)
        )
        self.assertEqual(
            len(result.reader_relationships), len(helper_readers)
        )
        self.assertEqual(result.upstream_object_ids, helper_upstream)
        self.assertEqual(result.downstream_object_ids, helper_downstream)
        self.assertTrue(detect_multiple_writers(tag_id, rels))

        # And the standalone summary helper produces the same string,
        # provided callers build (or pass) the same indices that
        # trace_object builds internally.
        object_index = {o.id: o for o in objects}
        summary = build_trace_summary(
            target_object_id=tag_id,
            target=find_object_by_id(tag_id, objects),
            writer_relationships=helper_writers,
            reader_relationships=helper_readers,
            upstream_object_ids=helper_upstream,
            downstream_object_ids=helper_downstream,
            multiple_writers=detect_multiple_writers(tag_id, rels),
            is_unresolved=False,
            object_index=object_index,
            exec_ctx_index={},
        )
        self.assertEqual(result.summary, summary)


class LocationAwareConclusionTests(unittest.TestCase):
    """Trace v1 must cite real source_locations and instruction_types."""

    # -- format_relationship_detail unit tests ------------------------

    def test_format_relationship_detail_extracts_routine_and_rung(self) -> None:
        rel = _located_writes(
            "rung::PLC01/MainProgram/MotorRoutine/Rung[12]",
            "tag::Motor_Run",
            routine="MotorRoutine",
            rung=12,
            instr="OTE",
        )
        self.assertEqual(
            format_relationship_detail(rel),
            "MotorRoutine/Rung[12] using OTE",
        )

    def test_format_relationship_detail_falls_back_to_source_id(self) -> None:
        """When source_location is missing, fall back to source_id."""
        rel = Relationship(
            source_id="rung::PLC01/MainProgram/MotorRoutine/Rung[12]",
            target_id="tag::Motor_Run",
            relationship_type=RelationshipType.WRITES,
            source_platform="rockwell",
            platform_specific={"instruction_type": "OTE"},
        )
        # source_id has the same shape so the short-form extractor still
        # finds the rung; instruction_type is appended either way.
        self.assertEqual(
            format_relationship_detail(rel),
            "MotorRoutine/Rung[12] using OTE",
        )

    def test_format_relationship_detail_omits_instr_when_missing(self) -> None:
        rel = Relationship(
            source_id="rung::PLC01/MainProgram/MotorRoutine/Rung[0]",
            target_id="tag::T",
            relationship_type=RelationshipType.WRITES,
            source_platform="rockwell",
            source_location=(
                "Controller:PLC01/Program:MainProgram/Routine:MotorRoutine"
                "/Rung[0]"
            ),
            platform_specific={},  # no instruction_type
        )
        self.assertEqual(
            format_relationship_detail(rel),
            "MotorRoutine/Rung[0]",
        )

    # -- 1. Writer conclusion contains source_location + instruction_type

    def test_writer_conclusion_names_location_and_instruction(self) -> None:
        tag_id = "tag::Motor_Run"
        writer_id = "rung::PLC01/MainProgram/MotorRoutine/Rung[12]"
        objects = [_tag(tag_id, "Motor_Run"), _rung(writer_id, "Rung[12]")]
        rels = [
            _located_writes(
                writer_id, tag_id,
                routine="MotorRoutine", rung=12, instr="OTE",
            ),
        ]

        result = trace_object(tag_id, objects, rels)

        writer_statements = [
            c.statement for c in result.conclusions
            if c.statement.startswith("Motor_Run is written in")
        ]
        self.assertEqual(len(writer_statements), 1)
        statement = writer_statements[0]
        # Spec wording verbatim.
        self.assertEqual(
            statement,
            "Motor_Run is written in 1 place(s): MotorRoutine/Rung[12] "
            "using OTE.",
        )
        # And the cross-axis assertions the spec calls out explicitly.
        self.assertIn("MotorRoutine/Rung[12]", statement)  # location
        self.assertIn("OTE", statement)                    # instr_type

    # -- 2. Reader conclusion contains source_location ----------------

    def test_reader_conclusion_names_location_and_instruction(self) -> None:
        tag_id = "tag::Motor_Run"
        reader_a = "rung::PLC01/MainProgram/AlarmRoutine/Rung[8]"
        reader_b = "rung::PLC01/MainProgram/SequenceRoutine/Rung[22]"
        objects = [
            _tag(tag_id, "Motor_Run"),
            _rung(reader_a, "Rung[8]"),
            _rung(reader_b, "Rung[22]"),
        ]
        rels = [
            _located_reads(reader_a, tag_id,
                           routine="AlarmRoutine", rung=8, instr="XIC"),
            _located_reads(reader_b, tag_id,
                           routine="SequenceRoutine", rung=22, instr="XIO"),
        ]

        result = trace_object(tag_id, objects, rels)

        reader_statements = [
            c.statement for c in result.conclusions
            if c.statement.startswith("Motor_Run is read in")
        ]
        self.assertEqual(len(reader_statements), 1)
        statement = reader_statements[0]
        self.assertEqual(
            statement,
            "Motor_Run is read in 2 place(s): AlarmRoutine/Rung[8] "
            "using XIC; SequenceRoutine/Rung[22] using XIO.",
        )
        self.assertIn("AlarmRoutine/Rung[8]", statement)
        self.assertIn("SequenceRoutine/Rung[22]", statement)
        self.assertIn("XIC", statement)
        self.assertIn("XIO", statement)

    # -- 3. Multiple writer summary names both locations --------------

    def test_multiple_writer_summary_names_both_locations(self) -> None:
        tag_id = "tag::Motor_Run"
        writer_a = "rung::PLC01/MainProgram/MotorRoutine/Rung[12]"
        writer_b = "rung::PLC01/MainProgram/FaultRoutine/Rung[4]"
        objects = [
            _tag(tag_id, "Motor_Run"),
            _rung(writer_a, "Rung[12]"),
            _rung(writer_b, "Rung[4]"),
        ]
        rels = [
            _located_writes(writer_a, tag_id,
                            routine="MotorRoutine", rung=12, instr="OTE"),
            _located_writes(writer_b, tag_id,
                            routine="FaultRoutine", rung=4, instr="OTU"),
        ]

        result = trace_object(tag_id, objects, rels)

        # The multiple-writer conclusion names both locations.
        mw_conclusion = next(
            c for c in result.conclusions
            if c.statement.startswith("Multiple writers detected")
        )
        self.assertEqual(
            mw_conclusion.statement,
            "Multiple writers detected for Motor_Run: "
            "MotorRoutine/Rung[12] using OTE; "
            "FaultRoutine/Rung[4] using OTU. "
            "Final state may depend on execution context or scan order.",
        )

        # And the summary inlines the writer locations too.
        self.assertIn("MotorRoutine/Rung[12] using OTE", result.summary)
        self.assertIn("FaultRoutine/Rung[4] using OTU", result.summary)
        self.assertIn(
            "Multiple writers detected; final state may depend on "
            "execution context or scan order.",
            result.summary,
        )

    # -- 4. Multiple writer recommended check names both locations ----

    def test_multiple_writer_recommended_check_names_both_locations(
        self,
    ) -> None:
        tag_id = "tag::Motor_Run"
        writer_a = "rung::PLC01/MainProgram/MotorRoutine/Rung[12]"
        writer_b = "rung::PLC01/MainProgram/FaultRoutine/Rung[4]"
        objects = [
            _tag(tag_id, "Motor_Run"),
            _rung(writer_a, "Rung[12]"),
            _rung(writer_b, "Rung[4]"),
        ]
        rels = [
            _located_writes(writer_a, tag_id,
                            routine="MotorRoutine", rung=12, instr="OTE"),
            _located_writes(writer_b, tag_id,
                            routine="FaultRoutine", rung=4, instr="OTU"),
        ]

        result = trace_object(tag_id, objects, rels)

        review_check = next(
            c for c in result.recommended_checks if c.startswith("Review ")
        )
        self.assertEqual(
            review_check,
            "Review MotorRoutine/Rung[12] using OTE and "
            "FaultRoutine/Rung[4] using OTU to confirm intended "
            "priority/scan behavior.",
        )

    # -- 5. Three writers => Oxford-comma "X, Y, and Z" ---------------

    def test_three_writers_uses_oxford_comma_in_review_check(self) -> None:
        tag_id = "tag::Motor_Run"
        ids = [
            f"rung::PLC01/MainProgram/Routine{i}/Rung[{i}]"
            for i in (1, 2, 3)
        ]
        objects = [_tag(tag_id, "Motor_Run")] + [
            _rung(rid, f"Rung[{i}]") for i, rid in enumerate(ids, start=1)
        ]
        rels = [
            _located_writes(
                ids[0], tag_id, routine="Routine1", rung=1, instr="OTE"
            ),
            _located_writes(
                ids[1], tag_id, routine="Routine2", rung=2, instr="OTL"
            ),
            _located_writes(
                ids[2], tag_id, routine="Routine3", rung=3, instr="OTU"
            ),
        ]
        result = trace_object(tag_id, objects, rels)
        review_check = next(
            c for c in result.recommended_checks if c.startswith("Review ")
        )
        # Three items -> "A, B, and C"
        self.assertIn(
            "Routine1/Rung[1] using OTE, Routine2/Rung[2] using OTL, "
            "and Routine3/Rung[3] using OTU",
            review_check,
        )

    # -- 6. Execution-context plumbing is wired (no crash; ec_id seen) -

    def test_execution_context_index_is_built_and_used(self) -> None:
        tag_id = "tag::Motor_Run"
        writer_id = "rung::PLC01/MainProgram/MotorRoutine/Rung[12]"
        exec_ctx = ExecutionContext(
            id="exec::PLC01/MainProgram/MotorRoutine",
            name="MotorRoutine routine scan",
            context_type=ExecutionContextType.ROUTINE,
            controller_id="controller::PLC01",
        )
        writes = _located_writes(
            writer_id, tag_id,
            routine="MotorRoutine", rung=12, instr="OTE",
        )
        # Bind the writer relationship to the execution context.
        writes_with_ec = writes.model_copy(
            update={"execution_context_id": exec_ctx.id}
        )
        result = trace_object(
            tag_id,
            control_objects=[_tag(tag_id, "Motor_Run"),
                             _rung(writer_id, "Rung[12]")],
            relationships=[writes_with_ec],
            execution_contexts=[exec_ctx],
        )
        self.assertEqual(
            result.platform_specific.get("execution_context_ids"),
            [exec_ctx.id],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
