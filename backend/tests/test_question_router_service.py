"""Tests for :mod:`app.services.question_router_service`.

Covers:

* :func:`detect_intent` keyword classification (each family + unknown).
* :func:`find_target_object` exact-id substring + name word-boundary
  matching, case insensitivity, longest-match preference, and the
  no-match path.
* :func:`answer_question` end-to-end: returns a v2 ``TraceResult``,
  decorates ``platform_specific`` with router metadata, preserves
  the question text, and falls back gracefully when no target is in
  the question.
* ``POST /api/ask-v1`` route function: success, no-target, no-upload
  (404), and JSON serializability of the response payload.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi import HTTPException  # noqa: E402

from app.api.routes import AskV1Request, ask_v1  # noqa: E402
from app.models.reasoning import (  # noqa: E402
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
    TraceResult,
    WriteBehaviorType,
)
from app.services.project_store import project_store  # noqa: E402
from app.services.question_router_service import (  # noqa: E402
    ROUTER_VERSION,
    answer_question,
    detect_intent,
    find_target_object,
)

# Reuse the ladder fixture from the trace v1 pipeline tests so the
# router exercises the same parsed-project -> normalized -> trace
# chain that production hits.
from tests.test_trace_v1_pipeline import (  # noqa: E402
    MOTOR_RUN_ID,
    _make_pipeline_project,
)


# ---------------------------------------------------------------------------
# Fixture helpers (mirror the v2 service tests so test_object identity
# is stable across the suite).
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


def _writes_latch(
    rung_id: str,
    tag_id: str,
    routine: str,
    rung_number: int,
    logic_condition: str | None = None,
) -> Relationship:
    return Relationship(
        source_id=rung_id,
        target_id=tag_id,
        relationship_type=RelationshipType.WRITES,
        write_behavior=WriteBehaviorType.LATCHES,
        source_platform="rockwell",
        source_location=(
            f"Controller:PLC01/Program:MainProgram/Routine:{routine}"
            f"/Rung[{rung_number}]"
        ),
        logic_condition=logic_condition,
        platform_specific={"instruction_type": "OTL"},
        confidence=ConfidenceLevel.HIGH,
    )


def _build_router_graph() -> tuple[list[ControlObject], list[Relationship], ControlObject]:
    """Tiny graph: ``R02_Sequencer/Rung[1]`` latches ``State_Fill``.

    Returns ``(control_objects, relationships, state_fill_tag)`` so
    tests can assert on the canonical target id directly.
    """

    rung = _rung(
        "rung::PLC01/MainProgram/R02_Sequencer/Rung[1]",
        "R02_Sequencer",
        1,
    )
    state_fill = _tag(
        "tag::PLC01/MainProgram/State_Fill", "State_Fill"
    )
    start_pb = _tag("tag::PLC01/MainProgram/StartPB_OS", "StartPB_OS")
    objects = [rung, state_fill, start_pb]
    rels = [
        _writes_latch(
            rung.id,
            state_fill.id,
            "R02_Sequencer",
            1,
            logic_condition="XIC(StartPB_OS) OTL(State_Fill)",
        ),
    ]
    return objects, rels, state_fill


# ---------------------------------------------------------------------------
# Part 1 -- detect_intent
# ---------------------------------------------------------------------------


class DetectIntentTests(unittest.TestCase):
    def test_why_not_running_is_why_off(self) -> None:
        self.assertEqual(
            detect_intent("Why is PMP_LiOH_B_Run not running?"),
            "why_off",
        )

    def test_off_keyword_is_why_off(self) -> None:
        self.assertEqual(detect_intent("Why is State_Fill off?"), "why_off")

    def test_false_keyword_is_why_off(self) -> None:
        self.assertEqual(detect_intent("Why is Permit_OK false?"), "why_off")

    def test_what_writes_keywords(self) -> None:
        self.assertEqual(detect_intent("What writes State_Fill?"), "what_writes")
        self.assertEqual(
            detect_intent("What controls PMP_LiOH_B_Run?"), "what_writes"
        )
        self.assertEqual(detect_intent("What drives Pump_01?"), "what_writes")

    def test_what_reads_keywords(self) -> None:
        self.assertEqual(
            detect_intent("Where is StartPB_OS used?"), "what_reads"
        )
        self.assertEqual(detect_intent("What reads Faults_Any?"), "what_reads")
        self.assertEqual(
            detect_intent("Where is Permit_OK referenced?"), "what_reads"
        )

    def test_priority_why_off_beats_writes(self) -> None:
        # The question mentions "writes" but also "why" and "not" --
        # the why-off family wins because it appears first in the
        # priority list.
        self.assertEqual(
            detect_intent("Why does nothing write Motor_Run?"),
            "why_off",
        )

    def test_unknown_for_empty_or_none(self) -> None:
        self.assertEqual(detect_intent(""), "unknown")
        self.assertEqual(detect_intent(None), "unknown")

    def test_unknown_for_unrecognized_question(self) -> None:
        self.assertEqual(detect_intent("Hello there friend"), "unknown")

    def test_word_boundary_avoids_false_substring_match(self) -> None:
        # "Software" contains "off" but should NOT trigger why_off.
        self.assertEqual(detect_intent("Software inventory check"), "unknown")


# ---------------------------------------------------------------------------
# Part 2 -- find_target_object
# ---------------------------------------------------------------------------


class FindTargetObjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.motor = _tag("tag::PLC01/MainProgram/Motor_Run", "Motor_Run")
        self.pump = _tag("tag::PLC01/MainProgram/Pump_01", "Pump_01")
        self.pump_run = _tag(
            "tag::PLC01/MainProgram/Pump_01.Run", "Pump_01.Run"
        )
        self.short = _tag("tag::PLC01/MainProgram/On", "On")
        self.x = _tag("tag::PLC01/MainProgram/X", "X")
        self.objs = [self.motor, self.pump, self.pump_run, self.short, self.x]

    def test_exact_name_match(self) -> None:
        self.assertIs(
            find_target_object("Why is Motor_Run not running?", self.objs),
            self.motor,
        )

    def test_case_insensitive_name_match(self) -> None:
        self.assertIs(
            find_target_object("Why is MOTOR_RUN off?", self.objs),
            self.motor,
        )

    def test_longer_dotted_name_wins_over_root(self) -> None:
        self.assertIs(
            find_target_object("What controls Pump_01.Run?", self.objs),
            self.pump_run,
        )

    def test_id_substring_match(self) -> None:
        self.assertIs(
            find_target_object(
                "Please trace tag::PLC01/MainProgram/Motor_Run for me.",
                self.objs,
            ),
            self.motor,
        )

    def test_returns_none_when_no_object_named(self) -> None:
        self.assertIsNone(
            find_target_object("Why is nothing happening?", self.objs),
        )

    def test_empty_question_returns_none(self) -> None:
        self.assertIsNone(find_target_object("", self.objs))
        self.assertIsNone(find_target_object(None, self.objs))

    def test_one_char_names_are_ignored(self) -> None:
        # 'X' would otherwise match in any sentence containing an 'X'.
        self.assertIsNone(
            find_target_object("Where is X used as a variable?", self.objs),
        )

    def test_word_boundary_avoids_substring_collision(self) -> None:
        # "On" should not match "Motor_Run" (which ends in 'n' but
        # has no 'On' as a standalone word).
        self.assertIs(
            find_target_object("Why is Motor_Run not running?", self.objs),
            self.motor,
        )


# ---------------------------------------------------------------------------
# Part 3 -- answer_question end-to-end
# ---------------------------------------------------------------------------


class AnswerQuestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.objects, self.relationships, self.state_fill = _build_router_graph()

    def test_returns_trace_result_for_recognized_target(self) -> None:
        result = answer_question(
            "Why is State_Fill not running?",
            self.objects,
            self.relationships,
        )
        self.assertIsInstance(result, TraceResult)
        self.assertEqual(result.target_object_id, self.state_fill.id)

    def test_decorates_platform_specific_with_router_metadata(self) -> None:
        question = "Why is State_Fill not running?"
        result = answer_question(question, self.objects, self.relationships)
        ps = result.platform_specific
        self.assertEqual(ps["question"], question)
        self.assertEqual(ps["detected_target_object_id"], self.state_fill.id)
        self.assertEqual(ps["detected_target_name"], "State_Fill")
        self.assertEqual(ps["detected_intent"], "why_off")
        self.assertEqual(ps["router_version"], ROUTER_VERSION)
        self.assertEqual(ps["target_resolution"], "matched")
        # We routed through v2, so v2's marker must still be present.
        self.assertEqual(ps.get("trace_version"), "v2")

    def test_what_writes_intent_recorded(self) -> None:
        result = answer_question(
            "What controls State_Fill?", self.objects, self.relationships
        )
        self.assertEqual(
            result.platform_specific["detected_intent"], "what_writes"
        )

    def test_what_reads_intent_recorded(self) -> None:
        result = answer_question(
            "Where is StartPB_OS used?", self.objects, self.relationships
        )
        self.assertEqual(
            result.platform_specific["detected_intent"], "what_reads"
        )
        # Target detection should resolve to StartPB_OS, not State_Fill.
        self.assertEqual(
            result.platform_specific["detected_target_name"], "StartPB_OS"
        )

    def test_no_target_returns_low_confidence_result(self) -> None:
        result = answer_question(
            "Why is nothing happening today?",
            self.objects,
            self.relationships,
        )
        self.assertEqual(result.confidence, ConfidenceLevel.LOW)
        self.assertEqual(
            result.summary,
            "I could not identify a target tag or object in the question.",
        )
        self.assertIn(
            "Try using the exact tag name or select an object from the "
            "normalized summary.",
            result.recommended_checks,
        )
        # Metadata still carries the question + intent + null target.
        ps = result.platform_specific
        self.assertEqual(ps["question"], "Why is nothing happening today?")
        self.assertIsNone(ps["detected_target_object_id"])
        self.assertEqual(ps["detected_intent"], "why_off")
        self.assertEqual(ps["router_version"], ROUTER_VERSION)
        self.assertEqual(ps["target_resolution"], "not_found")

    def test_no_target_does_not_invoke_trace(self) -> None:
        # With no target the result must not carry the v2 marker, since
        # trace_object_v2 was never called.
        result = answer_question(
            "Hello", self.objects, self.relationships
        )
        self.assertNotIn("trace_version", result.platform_specific)
        self.assertEqual(result.target_object_id, "")

    def test_natural_language_v2_conclusion_present_for_writers(self) -> None:
        # Sanity check that we really delegated to v2: the writer
        # WHAT conclusion ("... is latched ON ...") should appear.
        result = answer_question(
            "Why is State_Fill not running?",
            self.objects,
            self.relationships,
        )
        statements = [c.statement for c in result.conclusions]
        self.assertTrue(
            any("State_Fill is latched ON" in s for s in statements),
            f"Expected v2 writer conclusion, got: {statements}",
        )


# ---------------------------------------------------------------------------
# Part 4 -- POST /api/ask-v1 route function
# ---------------------------------------------------------------------------


class AskV1RouteTests(unittest.TestCase):
    """Drive the FastAPI route handler as a plain Python function.

    Mirrors ``TraceV1RouteFunctionTests`` -- no ``httpx`` /
    ``TestClient`` dependency required.
    """

    def setUp(self) -> None:
        project_store.reset()

    def tearDown(self) -> None:
        project_store.reset()

    def test_ask_v1_returns_trace_result_for_recognized_target(self) -> None:
        project_store.save(_make_pipeline_project())
        result = ask_v1(
            AskV1Request(question="Why is Motor_Run not running?")
        )
        self.assertEqual(result.target_object_id, MOTOR_RUN_ID)
        self.assertEqual(
            result.platform_specific["detected_intent"], "why_off"
        )
        self.assertEqual(
            result.platform_specific["detected_target_name"], "Motor_Run"
        )
        self.assertEqual(
            result.platform_specific["router_version"], ROUTER_VERSION
        )

    def test_ask_v1_no_target_returns_low_confidence_payload(self) -> None:
        project_store.save(_make_pipeline_project())
        result = ask_v1(AskV1Request(question="Hello there friend"))
        self.assertEqual(result.confidence, ConfidenceLevel.LOW)
        self.assertEqual(
            result.summary,
            "I could not identify a target tag or object in the question.",
        )

    def test_ask_v1_with_no_upload_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            ask_v1(AskV1Request(question="Why is Motor_Run not running?"))
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("uploaded", ctx.exception.detail.lower())

    def test_ask_v1_serializes_cleanly_as_json(self) -> None:
        project_store.save(_make_pipeline_project())
        result = ask_v1(
            AskV1Request(question="What controls Motor_Run?")
        )
        payload = result.model_dump_json()
        self.assertIn('"target_object_id"', payload)
        self.assertIn(MOTOR_RUN_ID, payload)
        self.assertIn('"detected_intent"', payload)
        self.assertIn('"router_version"', payload)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
