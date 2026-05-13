"""End-to-end tests for the Trace v1 backend pipeline.

Verifies the full chain that the new ``/api/`` endpoints exercise:

    parsed ControlProject
       -> normalize_l5x_project
       -> trace_object
       -> reasoning TraceResult

Tests are split into two flavors:

* **Service-level**: drives ``normalize_l5x_project`` and
  ``trace_object`` directly. No FastAPI or HTTP involved -- pure
  Python, fast, deterministic.
* **Route-function**: imports the FastAPI route handlers and invokes
  them as plain functions (no TestClient, so no new ``httpx``
  dependency). This proves the request/response models, store
  lookup, and error paths are wired correctly without requiring an
  HTTP layer in the test environment.

The shared ``project_store`` singleton is reset between tests via
``InMemoryProjectStore.reset()`` so tests don't observe each other.

Run with::

    python -m unittest discover -s backend/tests
"""

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi import HTTPException  # noqa: E402

from app.api.routes import (  # noqa: E402
    NormalizedSummaryResponse,
    TraceV1Request,
    normalized_summary_v1,
    trace_object_v1,
)
from app.models.control_model import (  # noqa: E402
    ControlController,
    ControlInstruction,
    ControlProgram,
    ControlProject,
    ControlRoutine,
    ControlTag,
)
from app.models.reasoning import (  # noqa: E402
    ConfidenceLevel,
    ControlObjectType,
    RelationshipType,
    TraceResult as ReasoningTraceResult,
)
from app.services.normalization_service import (  # noqa: E402
    normalize_l5x_project,
)
from app.services.project_store import project_store  # noqa: E402
from app.services.trace_service import trace_object  # noqa: E402


# ---------------------------------------------------------------------------
# Shared synthetic fixture
# ---------------------------------------------------------------------------


def _ladder_instr(
    iid: str,
    itype: str,
    operands: list[str],
    rung: int,
    output: str | None = None,
) -> ControlInstruction:
    return ControlInstruction(
        id=iid,
        instruction_type=itype,
        operands=list(operands),
        output=output,
        raw_text=f"{itype}({','.join(operands)})",
        language="ladder",
        rung_number=rung,
    )


def _make_pipeline_project() -> ControlProject:
    """Tiny synthetic project: one rung writes Motor_Run, one reads it.

    Resulting normalized graph contains a writer relationship and a
    reader relationship targeting ``tag::PLC01/MainProgram/Motor_Run``,
    which is what the pipeline tests trace against.
    """

    main_instructions = [
        _ladder_instr("r0_i0", "XIC", ["Start_PB"], rung=0),
        _ladder_instr("r0_i1", "OTE", ["Motor_Run"], rung=0,
                      output="Motor_Run"),

        _ladder_instr("r1_i0", "XIC", ["Motor_Run"], rung=1),
        _ladder_instr("r1_i1", "OTE", ["AlarmFlag"], rung=1,
                      output="AlarmFlag"),
    ]
    routine = ControlRoutine(
        name="MainRoutine",
        language="ladder",
        instructions=main_instructions,
        raw_logic="...",
        metadata={"rockwell_type": "RLL"},
    )
    program = ControlProgram(
        name="MainProgram",
        tags=[
            ControlTag(name=n, data_type="BOOL", scope="MainProgram",
                       platform_source="rockwell_l5x")
            for n in ["Start_PB", "Motor_Run", "AlarmFlag"]
        ],
        routines=[routine],
    )
    controller = ControlController(
        name="PLC01",
        platform="rockwell",
        controller_tags=[],
        programs=[program],
    )
    # file_hash is required by project_store.save(); we just need any
    # stable non-empty string for tests.
    return ControlProject(
        project_name="PLC01",
        source_file="pipeline_test.L5X",
        file_hash="pipeline-test-hash",
        controllers=[controller],
    )


MOTOR_RUN_ID = "tag::PLC01/MainProgram/Motor_Run"


# ---------------------------------------------------------------------------
# Service-level pipeline
# ---------------------------------------------------------------------------


class PipelineServiceLevelTests(unittest.TestCase):
    """Pure-Python pipeline: parsed -> normalized -> traced."""

    def test_normalize_then_trace_returns_reasoning_trace_result(
        self,
    ) -> None:
        project = _make_pipeline_project()

        normalized = normalize_l5x_project(project)
        result = trace_object(
            target_object_id=MOTOR_RUN_ID,
            control_objects=normalized["control_objects"],
            relationships=normalized["relationships"],
            execution_contexts=normalized["execution_contexts"],
        )

        self.assertIsInstance(result, ReasoningTraceResult)
        self.assertEqual(result.target_object_id, MOTOR_RUN_ID)

        # Exactly one writer (Rung[0]) and one reader (Rung[1]).
        self.assertEqual(len(result.writer_relationships), 1)
        self.assertEqual(len(result.reader_relationships), 1)
        self.assertEqual(
            result.writer_relationships[0].relationship_type,
            RelationshipType.WRITES,
        )
        self.assertEqual(
            result.reader_relationships[0].relationship_type,
            RelationshipType.READS,
        )

        # Direct cause/effect edges resolved, target is real -> HIGH.
        self.assertEqual(result.confidence, ConfidenceLevel.HIGH)

        # Summary and conclusions are present and non-empty.
        self.assertTrue(result.summary)
        self.assertGreater(len(result.conclusions), 0)
        statements = [c.statement for c in result.conclusions]
        self.assertTrue(
            any("Motor_Run is written in" in s for s in statements),
            f"missing writer conclusion: {statements}",
        )
        self.assertTrue(
            any("Motor_Run is read in" in s for s in statements),
            f"missing reader conclusion: {statements}",
        )


# ---------------------------------------------------------------------------
# Route-function pipeline (no HTTP / TestClient)
# ---------------------------------------------------------------------------


class TraceV1RouteFunctionTests(unittest.TestCase):
    """Drive the FastAPI route handlers as plain Python functions.

    This catches wiring issues (imports, request/response models,
    store lookup, 404 paths) without bringing in ``httpx`` /
    ``TestClient``.
    """

    def setUp(self) -> None:
        project_store.reset()

    def tearDown(self) -> None:
        project_store.reset()

    # -- /api/trace-v1 ------------------------------------------------

    def test_trace_v1_returns_trace_result_for_latest_project(
        self,
    ) -> None:
        project_store.save(_make_pipeline_project())

        result = trace_object_v1(
            TraceV1Request(target_object_id=MOTOR_RUN_ID)
        )

        self.assertIsInstance(result, ReasoningTraceResult)
        self.assertEqual(result.target_object_id, MOTOR_RUN_ID)
        self.assertEqual(len(result.writer_relationships), 1)
        self.assertEqual(len(result.reader_relationships), 1)

    def test_trace_v1_serializes_cleanly_as_json(self) -> None:
        """The route declares ``response_model=ReasoningTraceResult``,
        so the result must serialize without errors via Pydantic. We
        invoke ``model_dump_json`` directly to mirror what FastAPI
        would do on the wire."""
        project_store.save(_make_pipeline_project())
        result = trace_object_v1(
            TraceV1Request(target_object_id=MOTOR_RUN_ID)
        )
        payload = result.model_dump_json()
        self.assertIn('"target_object_id"', payload)
        self.assertIn(MOTOR_RUN_ID, payload)
        self.assertIn('"conclusions"', payload)

    def test_trace_v1_with_no_upload_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            trace_object_v1(
                TraceV1Request(target_object_id=MOTOR_RUN_ID)
            )
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("uploaded", ctx.exception.detail.lower())

    def test_trace_v1_unknown_target_id_still_returns_result(
        self,
    ) -> None:
        """``trace_object`` is defensive: an unknown target id should
        produce a TraceResult flagged as unresolved, not a 5xx."""
        project_store.save(_make_pipeline_project())

        result = trace_object_v1(
            TraceV1Request(target_object_id="tag::DefinitelyNotHere")
        )

        self.assertEqual(
            result.target_object_id, "tag::DefinitelyNotHere"
        )
        # No edges + missing target -> LOW confidence + unresolved
        # conclusion present.
        self.assertEqual(result.confidence, ConfidenceLevel.LOW)
        self.assertTrue(
            any(
                "unresolved or absent" in c.statement
                for c in result.conclusions
            ),
            f"missing unresolved conclusion: {result.conclusions}",
        )

    # -- /api/normalized-summary --------------------------------------

    def test_normalized_summary_returns_counts_and_first_twenty(
        self,
    ) -> None:
        project_store.save(_make_pipeline_project())

        summary = normalized_summary_v1()

        self.assertIsInstance(summary, NormalizedSummaryResponse)
        self.assertEqual(summary.project_id, "pipeline-test-hash")
        self.assertGreater(summary.control_object_count, 0)
        self.assertGreater(summary.relationship_count, 0)
        self.assertGreaterEqual(summary.execution_context_count, 1)

        # Pagination caps (default limit 100).
        self.assertLessEqual(len(summary.control_objects), 100)
        self.assertLessEqual(len(summary.relationships), 100)
        self.assertEqual(summary.returned_control_object_count, len(summary.control_objects))
        self.assertEqual(summary.returned_relationship_count, len(summary.relationships))

        # Each summary row has the spec'd shape.
        for o in summary.control_objects:
            self.assertTrue(o.id)
            self.assertTrue(o.object_type)
        for r in summary.relationships:
            self.assertTrue(r.source_id)
            self.assertTrue(r.target_id)
            self.assertTrue(r.relationship_type)

        # Sanity: at least one TAG object and one WRITES / READS
        # relationship must show up in the first 20 of each list.
        types = {o.object_type for o in summary.control_objects}
        self.assertIn(ControlObjectType.TAG.value, types)
        rel_types = {r.relationship_type for r in summary.relationships}
        self.assertIn(RelationshipType.WRITES.value, rel_types)

    def test_normalized_summary_without_upload_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            normalized_summary_v1()
        self.assertEqual(ctx.exception.status_code, 404)

    # -- Cache behavior ------------------------------------------------

    def test_normalization_is_cached_between_calls(self) -> None:
        """Two consecutive calls hit the same cached normalization
        dict, so identity is preserved (no recomputation)."""
        project_store.save(_make_pipeline_project())
        first = project_store.get_normalized("pipeline-test-hash")
        second = project_store.get_normalized("pipeline-test-hash")
        self.assertIs(first, second)

    def test_resave_invalidates_normalized_cache(self) -> None:
        """A re-upload with the same hash forces re-normalization
        (handy during dev iteration on the normalizer)."""
        project = _make_pipeline_project()
        project_store.save(project)
        first = project_store.get_normalized(project.file_hash)
        # Re-save -> cache invalidation.
        project_store.save(project)
        second = project_store.get_normalized(project.file_hash)
        self.assertIsNot(first, second)
        # But the content should still be equivalent.
        self.assertEqual(
            len(first["control_objects"]),
            len(second["control_objects"]),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
