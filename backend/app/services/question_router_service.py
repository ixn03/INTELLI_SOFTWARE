"""Deterministic question router for INTELLI (v1).

Maps a free-text user question to an existing deterministic reasoning
tool (today: :func:`app.services.trace_v2_service.trace_object_v2`)
without any LLM, fuzzy matching, or runtime data.

This is intentionally a thin, rule-based first pass:

1. **Target detection.** Scan the question for a control-object id
   (substring match) or name (word-bounded regex match). Longer keys
   win, so dotted identifiers (``Pump_01.Run``) beat their root
   (``Pump_01``) when both exist in the project.

2. **Intent detection.** Keyword scan in priority order; the first
   matching family wins. Intents are recorded as metadata only -- the
   engine call (``trace_object_v2``) is the same regardless of intent.
   A future router can branch the engine call here.

3. **Trace.** Call :func:`trace_object_v2` against the detected target
   and decorate ``platform_specific`` with the question, the detected
   target id / name, the detected intent, and ``router_version``.

4. **No-target fallback.** If nothing matched, return a
   low-confidence :class:`TraceResult` with a clear summary and one
   recommended check, so the caller never has to handle ``None``.

Public API
----------

* :data:`ROUTER_VERSION`
* :func:`detect_intent`
* :func:`find_target_object`
* :func:`answer_question`
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ExecutionContext,
    Relationship,
    TraceResult,
    TruthConclusion,
    TruthContextType,
)
from app.services.trace_v2_service import trace_object_v2


ROUTER_VERSION = "v1"


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------
#
# Order matters. The first family that matches wins, so multi-keyword
# questions like "Why is X not running?" classify as ``why_off`` rather
# than ``what_writes`` even if a "writes" keyword sneaks in later.
#
# All patterns use ``\b`` word boundaries to avoid spurious substring
# hits (e.g. "soft" should not trigger "off").
# ---------------------------------------------------------------------------


_INTENT_PATTERNS: list[tuple[str, list[re.Pattern[str]]]] = [
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
        "what_writes",
        [
            re.compile(r"\bcontrols?\b", re.IGNORECASE),
            re.compile(r"\bwrites?\b", re.IGNORECASE),
            re.compile(r"\bdrives?\b", re.IGNORECASE),
        ],
    ),
    (
        "what_reads",
        [
            re.compile(r"\breads?\b", re.IGNORECASE),
            re.compile(r"\busage\b", re.IGNORECASE),
            re.compile(r"\bused\b", re.IGNORECASE),
            re.compile(r"\breferenced\b", re.IGNORECASE),
            re.compile(r"\bwhere\b", re.IGNORECASE),
        ],
    ),
]


def detect_intent(question: Optional[str]) -> str:
    """Classify the question into one of ``why_off`` / ``what_writes``
    / ``what_reads`` / ``unknown``.

    Pure keyword matching, deterministic, no LLM. Intent today is
    metadata only -- it is recorded in ``platform_specific`` but does
    not change which engine is invoked. A later router version can
    branch on it (e.g. trace_v3 for ``why_off``).
    """

    if not question:
        return "unknown"
    for intent, patterns in _INTENT_PATTERNS:
        if any(p.search(question) for p in patterns):
            return intent
    return "unknown"


# ---------------------------------------------------------------------------
# Target detection
# ---------------------------------------------------------------------------


# Match a control-object name with word-boundary semantics. ``\b``
# already treats dots as boundaries, which is what we want: it lets
# "Pump_01" still match in "Pump_01.Run" if and only if "Pump_01.Run"
# wasn't matched first (we sort candidates by length descending so
# the longer identifier always wins).
def _name_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)


def find_target_object(
    question: Optional[str],
    control_objects: Sequence[ControlObject],
) -> Optional[ControlObject]:
    """Return the first control object whose id or name appears in
    the question, preferring longer matches.

    Resolution order:

    1. **Exact id substring** -- ids are highly specific (they contain
       ``::`` and ``/``) so a substring match is nearly never a false
       positive. Longer ids are tried first.
    2. **Word-bounded name match** -- case-insensitive ``\\b<name>\\b``
       regex. Longer names are tried first, so ``Pump_01.Run`` is
       matched before ``Pump_01`` when both exist.

    Returns ``None`` if no object is identified.
    """

    if not question or not control_objects:
        return None

    # 1) Id substring match.
    by_id = sorted(
        (o for o in control_objects if o.id),
        key=lambda o: -len(o.id),
    )
    for obj in by_id:
        if obj.id in question:
            return obj

    # 2) Name word-bounded match.
    by_name = sorted(
        (o for o in control_objects if o.name),
        key=lambda o: -len(o.name or ""),
    )
    for obj in by_name:
        name = obj.name or ""
        # Skip 1-char names: they would be far too noisy in any
        # English sentence ("a", "I", "X").
        if len(name) < 2:
            continue
        if _name_pattern(name).search(question):
            return obj

    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def answer_question(
    question: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: Optional[list[ExecutionContext]] = None,
) -> TraceResult:
    """Route a free-text question to the deterministic trace engine.

    Today this always calls :func:`trace_object_v2` and decorates the
    result with router metadata::

        result.platform_specific["question"]
        result.platform_specific["detected_target_object_id"]
        result.platform_specific["detected_target_name"]
        result.platform_specific["detected_intent"]
        result.platform_specific["router_version"]

    When the question doesn't name a known control object, a
    low-confidence ``TraceResult`` is returned describing the
    failure and recommending a recovery step. The caller never has
    to handle ``None``.
    """

    intent = detect_intent(question)
    target = find_target_object(question, control_objects)

    if target is None:
        return _no_target_result(question=question, intent=intent)

    result = trace_object_v2(
        target_object_id=target.id,
        control_objects=control_objects,
        relationships=relationships,
        execution_contexts=execution_contexts or [],
    )

    result.platform_specific = dict(result.platform_specific or {})
    result.platform_specific.update(
        {
            "question": question,
            "detected_target_object_id": target.id,
            "detected_target_name": target.name,
            "detected_intent": intent,
            "router_version": ROUTER_VERSION,
            "target_resolution": "matched",
        }
    )
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _no_target_result(question: str, intent: str) -> TraceResult:
    """Construct the canonical "I couldn't find a target" result.

    Carries the question + detected intent so a UI can still show
    *something* useful even when the router gives up.
    """

    return TraceResult(
        target_object_id="",
        summary="I could not identify a target tag or object in the question.",
        confidence=ConfidenceLevel.LOW,
        recommended_checks=[
            "Try using the exact tag name or select an object from the "
            "normalized summary.",
        ],
        conclusions=[
            TruthConclusion(
                statement=(
                    "No tag or control object was recognized in the "
                    "question."
                ),
                subject_ids=[],
                truth_context=TruthContextType.UNKNOWN,
                confidence=ConfidenceLevel.LOW,
                recommended_checks=[
                    "Try using the exact tag name (e.g. "
                    "tag::PLC01/MainProgram/Motor_Run) or pick from "
                    "/api/normalized-summary.",
                ],
            ),
        ],
        platform_specific={
            "question": question,
            "detected_target_object_id": None,
            "detected_target_name": None,
            "detected_intent": intent,
            "router_version": ROUTER_VERSION,
            "target_resolution": "not_found",
        },
    )


__all__ = [
    "ROUTER_VERSION",
    "answer_question",
    "detect_intent",
    "find_target_object",
]
