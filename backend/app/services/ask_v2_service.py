"""Deterministic ask orchestration (v2) — no LLM.

Routes natural-language questions to the right *deterministic* tool
chain:

* Always resolves a target from the question (same heuristics as v1).
* Classifies **intent** for analytics and future branching.
* When a ``runtime_snapshot`` is present and the question asks *why*
  something is off / not running, runs Trace v2 then
  :func:`app.services.runtime_evaluation_v2_service.evaluate_trace_runtime_v2`.
* Otherwise runs Trace v2 only.

``ask-v1`` remains the thin trace-only router; v2 is the orchestration
entry point for snapshot-aware diagnosis without adding an LLM.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.models.reasoning import (
    ControlObject,
    ExecutionContext,
    Relationship,
    TraceResult,
)
from app.models.runtime_value import RuntimeSnapshotModel
from app.services.question_router_service import (
    find_target_object,
    _no_target_result,
)
from app.services.runtime_evaluation_v2_service import evaluate_trace_runtime_v2
from app.services.runtime_ingestion_service import normalize_runtime_snapshot
from app.services.trace_v2_service import trace_object_v2

ROUTER_VERSION_V2 = "v2"


_INTENT_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = [
    (
        "runtime_diagnosis",
        [
            re.compile(r"\bruntime\b", re.IGNORECASE),
            re.compile(r"\bsnapshot\b", re.IGNORECASE),
            re.compile(r"\bplc\b", re.IGNORECASE),
            re.compile(r"\blive\b", re.IGNORECASE),
            re.compile(r"\bhistorian\b", re.IGNORECASE),
        ],
    ),
    (
        "why_off",
        [
            re.compile(r"\bnot\s+running\b", re.IGNORECASE),
            re.compile(r"\bwhy\b", re.IGNORECASE),
            re.compile(r"\bnot\b", re.IGNORECASE),
            re.compile(r"\bfalse\b", re.IGNORECASE),
            re.compile(r"\boff\b", re.IGNORECASE),
        ],
    ),
    (
        "what_controls",
        [
            re.compile(r"\bwhat\s+controls\b", re.IGNORECASE),
            re.compile(r"\bwho\s+controls\b", re.IGNORECASE),
        ],
    ),
    (
        "where_used",
        [
            re.compile(r"\bwhere\s+is\b.*\bused\b", re.IGNORECASE),
            re.compile(r"\bwhere\s+used\b", re.IGNORECASE),
            re.compile(r"\breferences?\b", re.IGNORECASE),
        ],
    ),
    (
        "what_writes",
        [
            re.compile(r"\bwrites?\b", re.IGNORECASE),
            re.compile(r"\boutputs?\b", re.IGNORECASE),
            re.compile(r"\bdrives?\b", re.IGNORECASE),
        ],
    ),
    (
        "what_reads",
        [
            re.compile(r"\breads?\b", re.IGNORECASE),
            re.compile(r"\binputs?\b", re.IGNORECASE),
        ],
    ),
]


def detect_intent_v2(question: Optional[str]) -> str:
    if not question:
        return "unknown"
    for intent, patterns in _INTENT_PATTERNS:
        if any(p.search(question) for p in patterns):
            return intent
    return "unknown"


def _snapshot_nonempty(snapshot: Any) -> bool:
    if snapshot is None:
        return False
    if isinstance(snapshot, RuntimeSnapshotModel):
        return bool(snapshot.values)
    if isinstance(snapshot, dict):
        return bool(snapshot)
    return True


def _should_run_runtime_v2(
    intent: str,
    question: str,
    snapshot: Any,
) -> bool:
    if not _snapshot_nonempty(snapshot):
        return False
    q = question.lower()
    if intent == "runtime_diagnosis":
        return True
    if intent == "why_off":
        return True
    if "not running" in q or "won't run" in q or "wont run" in q:
        return True
    if "why" in q and ("off" in q or "false" in q):
        return True
    return False


def answer_question_v2(
    question: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: Optional[list[ExecutionContext]] = None,
    runtime_snapshot: Any = None,
) -> TraceResult:
    intent = detect_intent_v2(question)
    target = find_target_object(question, control_objects)

    if target is None:
        return _no_target_v2(question=question, intent=intent)

    base = trace_object_v2(
        target_object_id=target.id,
        control_objects=control_objects,
        relationships=relationships,
        execution_contexts=execution_contexts or [],
    )

    runtime_used = False
    if _should_run_runtime_v2(intent, question, runtime_snapshot):
        snap_model = (
            runtime_snapshot
            if isinstance(runtime_snapshot, RuntimeSnapshotModel)
            else normalize_runtime_snapshot(runtime_snapshot)
        )
        evaluate_trace_runtime_v2(base, snap_model)
        runtime_used = True

    base.platform_specific = dict(base.platform_specific or {})
    base.platform_specific.update(
        {
            "question": question,
            "detected_target_object_id": target.id,
            "detected_target_name": target.name,
            "detected_intent": intent,
            "router_version": ROUTER_VERSION_V2,
            "target_resolution": "matched",
            "ask_v2_pipeline": (
                "trace_v2+runtime_v2" if runtime_used else "trace_v2_only"
            ),
            "runtime_evaluation_used": runtime_used,
        }
    )
    return base


def _no_target_v2(question: str, intent: str) -> TraceResult:
    r = _no_target_result(question=question, intent=intent)
    r.platform_specific = dict(r.platform_specific or {})
    r.platform_specific["router_version"] = ROUTER_VERSION_V2
    r.platform_specific["ask_v2_pipeline"] = "no_target"
    r.platform_specific["runtime_evaluation_used"] = False
    return r


__all__ = [
    "ROUTER_VERSION_V2",
    "answer_question_v2",
    "detect_intent_v2",
]
