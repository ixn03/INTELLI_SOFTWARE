"""Tests for :mod:`app.services.ask_v2_service` and ``POST /api/ask-v2``."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi import HTTPException  # noqa: E402

from app.api.routes import AskV2Request, ask_v2  # noqa: E402
from app.services.normalization_service import normalize_l5x_project  # noqa: E402
from app.services.project_store import project_store  # noqa: E402
from app.services.ask_v2_service import (  # noqa: E402
    ROUTER_VERSION_V2,
    answer_question_v2,
    detect_intent_v2,
)
from tests.test_trace_v1_pipeline import (  # noqa: E402
    MOTOR_RUN_ID,
    _make_pipeline_project,
)


class IntentV2Tests(unittest.TestCase):
    def test_runtime_diagnosis(self) -> None:
        self.assertEqual(
            detect_intent_v2("What does the runtime snapshot show?"),
            "runtime_diagnosis",
        )

    def test_where_used(self) -> None:
        self.assertEqual(
            detect_intent_v2("Where is Motor_Run used?"),
            "where_used",
        )


class AskV2ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        proj = _make_pipeline_project()
        self.norm = normalize_l5x_project(proj)

    def test_trace_only_without_snapshot(self) -> None:
        r = answer_question_v2(
            question="Why is Motor_Run not running?",
            control_objects=self.norm["control_objects"],
            relationships=self.norm["relationships"],
            execution_contexts=self.norm["execution_contexts"],
            runtime_snapshot=None,
        )
        self.assertEqual(r.target_object_id, MOTOR_RUN_ID)
        self.assertEqual(r.platform_specific.get("ask_v2_pipeline"), "trace_v2_only")
        self.assertFalse(r.platform_specific.get("runtime_evaluation_used"))

    def test_runtime_v2_when_snapshot_and_why(self) -> None:
        r = answer_question_v2(
            question="Why is Motor_Run not running?",
            control_objects=self.norm["control_objects"],
            relationships=self.norm["relationships"],
            execution_contexts=self.norm["execution_contexts"],
            runtime_snapshot={"StartPB": True, "Faulted": False},
        )
        self.assertEqual(
            r.platform_specific.get("ask_v2_pipeline"), "trace_v2+runtime_v2"
        )
        self.assertTrue(r.platform_specific.get("runtime_evaluation_used"))
        self.assertEqual(
            r.platform_specific.get("trace_version"),
            "runtime_v2",
        )


class AskV2RouteTests(unittest.TestCase):
    def setUp(self) -> None:
        project_store.reset()

    def tearDown(self) -> None:
        project_store.reset()

    def test_ask_v2_route(self) -> None:
        project_store.save(_make_pipeline_project())
        r = ask_v2(
            AskV2Request(
                question="Why is Motor_Run not running?",
                runtime_snapshot={"StartPB": True, "Faulted": False},
            )
        )
        self.assertEqual(r.platform_specific.get("router_version"), ROUTER_VERSION_V2)
        self.assertTrue(r.platform_specific.get("runtime_evaluation_used"))

    def test_ask_v2_no_upload_404(self) -> None:
        with self.assertRaises(HTTPException) as ctx:
            ask_v2(AskV2Request(question="Why is Motor_Run not running?"))
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
