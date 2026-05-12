"""Trace service: cause/effect tracing across control logic.

This module hosts two layers that coexist intentionally:

1. **Legacy trace** -- ``trace_tag(project, target_tag, question)`` works
   against the parsed ``ControlProject`` and returns the legacy
   ``app.models.control_model.TraceResult`` consumed by the current
   API routes and frontend. The implementation is preserved verbatim
   for backward compatibility; no behavior changes.

2. **Trace v1** -- ``trace_object(target_object_id, control_objects,
   relationships, execution_contexts=None)`` works against the
   normalized reasoning models (``ControlObject`` / ``Relationship`` /
   ``ExecutionContext``) and returns the reasoning ``TraceResult``
   from ``app.models.reasoning``. Deterministic, rule-based, no LLM.

The two entry points share no state. A future wiring step can switch
the API over to v1 (or expose both side-by-side) without further
changes to this module.

Relationship direction rules (v1)
---------------------------------

A relationship where ``target_id == target_object_id`` and whose type
is in ``WRITER_RELATIONSHIP_TYPES`` (``WRITES`` / ``LATCHES`` /
``UNLATCHES`` / ``RESETS`` / ``CALCULATES`` / ``SCALES``) is a
**writer / upstream** edge.

A relationship where ``target_id == target_object_id`` and whose type
is ``READS`` is a **reader / context** edge -- it is *not* counted as
a writer.

A relationship where ``source_id == target_object_id`` and whose type
is anything other than ``CONTAINS`` is a **downstream** edge: the
target object is the cause and the rel's target is the effect.

``CONTAINS`` relationships are kept as **context** (they show up in
``TraceResult.relationships`` but are excluded from writers / readers /
downstream / upstream counts so structural containment never inflates
cause/effect numbers).

Trace v1 is **location-aware**: conclusions and the summary cite the
actual routine / rung / instruction-type for each writer and reader
relationship, so a UI can render specific guidance like
``"MotorRoutine/Rung[12] using OTE"`` instead of a bare count.
"""

import re
from typing import Optional

# --- Legacy (unchanged) ----------------------------------------------------
from app.models.control_model import ControlProject, TraceCause, TraceResult
from app.services.graph_service import build_logic_graph

# --- v1 reasoning schema --------------------------------------------------
from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    Evidence,
    EvidenceType,
    ExecutionContext,
    Relationship,
    RelationshipType,
    TraceResult as ReasoningTraceResult,
    TruthConclusion,
    TruthContextType,
)


# ===========================================================================
# Legacy trace (preserved verbatim for backward compatibility)
# ===========================================================================


def trace_tag(
    project: ControlProject,
    target_tag: str,
    question: str = "why_false",
) -> TraceResult:
    """Legacy trace over the parsed-project graph. Do not modify."""

    graph = build_logic_graph(project)

    if target_tag not in graph:
        return TraceResult(
            target_tag=target_tag,
            question=question,
            status="not_found",
            summary=(
                f"{target_tag} was not found in the normalized project "
                "model."
            ),
        )

    drivers = [
        predecessor
        for predecessor in graph.predecessors(target_tag)
        if graph.edges[predecessor, target_tag].get("relationship")
        == "drives"
    ]

    if not drivers:
        return TraceResult(
            target_tag=target_tag,
            question=question,
            status="unsupported",
            summary=(
                f"No output instruction driving {target_tag} was found in "
                "the normalized logic graph."
            ),
            evidence={"graph_node": graph.nodes[target_tag]},
        )

    causes: list[TraceCause] = []

    for driver in drivers:
        driver_data = graph.nodes[driver]

        for condition in graph.predecessors(driver):
            edge = graph.edges[condition, driver]
            if edge.get("relationship") != "conditions":
                continue

            causes.append(
                TraceCause(
                    tag=condition,
                    relationship="conditions_output",
                    instruction_type=driver_data.get("instruction_type"),
                    routine=driver_data.get("routine"),
                    program=driver_data.get("program"),
                    raw_text=driver_data.get("raw_text"),
                )
            )

    return TraceResult(
        target_tag=target_tag,
        question=question,
        status="answered",
        summary=(
            f"{target_tag} is driven by {len(drivers)} instruction(s) and "
            f"depends on {len(causes)} upstream condition tag(s)."
        ),
        causes=causes,
        evidence={"driver_instruction_count": len(drivers)},
    )


# ===========================================================================
# Trace v1: reasoning-schema-driven
# ===========================================================================


# Relationship types we count as "writers" (cause/effect upstream).
# ``READS`` is intentionally excluded -- readers are tracked separately.
# ``CONTAINS`` is excluded -- it is structural, not cause/effect.
WRITER_RELATIONSHIP_TYPES: frozenset[RelationshipType] = frozenset({
    RelationshipType.WRITES,
    RelationshipType.LATCHES,
    RelationshipType.UNLATCHES,
    RelationshipType.RESETS,
    RelationshipType.CALCULATES,
    RelationshipType.SCALES,
})


# ---------------------------------------------------------------------------
# Location-aware detail formatting
# ---------------------------------------------------------------------------


def format_relationship_detail(
    relationship: Relationship,
    source_object: Optional[ControlObject] = None,
    target_object: Optional[ControlObject] = None,
    execution_context: Optional[ExecutionContext] = None,
) -> str:
    """Render a relationship as ``"<routine>/<rung> using <INSTR>"``.

    Examples (produced from real fixtures):

        - ``"MotorRoutine/Rung[12] using OTE"``
        - ``"FaultRoutine/Rung[4] using OTU"``
        - ``"AlarmRoutine/Rung[8] using XIC"``

    Resolution order for the location component:
        1. ``relationship.source_location`` (cleaned to routine / rung
           short form via :func:`_extract_short_location`).
        2. ``source_object.source_location`` if the relationship itself
           has no location set (e.g. legacy or partial test fixtures).
        3. ``relationship.source_id`` as a last resort, also cleaned.

    The instruction-type suffix ``" using <TYPE>"`` is appended when
    ``relationship.platform_specific["instruction_type"]`` is present.

    ``target_object`` and ``execution_context`` are part of the public
    signature so callers can pre-resolve them once; they are reserved
    for future enrichment (e.g. annotating "(in MainTask scan)") and
    are not consumed by the current implementation.
    """

    # Suppress unused warnings while keeping the public signature stable
    # for future enrichment paths.
    _ = target_object, execution_context

    raw_location: Optional[str] = relationship.source_location
    if not raw_location and source_object is not None:
        raw_location = source_object.source_location
    if not raw_location:
        raw_location = relationship.source_id

    location = _extract_short_location(raw_location)
    instruction_type = relationship.platform_specific.get("instruction_type")
    if instruction_type:
        return f"{location} using {instruction_type}"
    return location


_RUNG_PATTERN = re.compile(r"([^/]+)/Rung\[(\d+)\]")
_ROUTINE_LABELLED = re.compile(r"Routine:([^/]+)")


def _extract_short_location(raw: Optional[str]) -> str:
    """Reduce a long source_location / source_id to a short, readable form.

    Handles both shapes our normalizer emits:

        - ``"Controller:PLC01/Program:MainProgram/Routine:MotorRoutine/Rung[12]"``
          -> ``"MotorRoutine/Rung[12]"``
        - ``"rung::PLC01/MainProgram/MotorRoutine/Rung[12]"``
          -> ``"MotorRoutine/Rung[12]"``
        - ``"Controller:PLC01/Program:MainProgram/Routine:CalcRoutine"``
          -> ``"CalcRoutine"``

    Anything that doesn't match these shapes is returned verbatim so
    the caller still gets *some* useful identifier.
    """

    if not raw:
        return ""

    match = _RUNG_PATTERN.search(raw)
    if match:
        routine_segment = match.group(1)
        # Strip "Routine:" / "Rung:" / etc. label prefixes if present.
        if ":" in routine_segment:
            routine_segment = routine_segment.split(":", 1)[1]
        return f"{routine_segment}/Rung[{match.group(2)}]"

    match = _ROUTINE_LABELLED.search(raw)
    if match:
        return match.group(1)

    return raw


def _detail_from_indices(
    relationship: Relationship,
    object_index: Optional[dict[str, ControlObject]],
    exec_ctx_index: Optional[dict[str, ExecutionContext]],
) -> str:
    """Build a relationship detail using pre-built lookup indices.

    Convenience wrapper around :func:`format_relationship_detail` so
    callers that already have ``{id: object}`` / ``{id: ExecutionContext}``
    maps don't have to do the resolution at every call-site.
    """

    source_object: Optional[ControlObject] = None
    target_object: Optional[ControlObject] = None
    execution_context: Optional[ExecutionContext] = None

    if object_index is not None:
        source_object = object_index.get(relationship.source_id)
        target_object = object_index.get(relationship.target_id)
    if exec_ctx_index is not None and relationship.execution_context_id:
        execution_context = exec_ctx_index.get(
            relationship.execution_context_id
        )

    return format_relationship_detail(
        relationship,
        source_object=source_object,
        target_object=target_object,
        execution_context=execution_context,
    )


def _format_locations_list(details: list[str]) -> str:
    """Join a list of detail strings with ``"; "`` for use inside a
    conclusion sentence.

    Returns an empty string for an empty list so callers can
    unconditionally embed the result.
    """

    return "; ".join(details)


def _format_locations_for_review(details: list[str]) -> str:
    """Join detail strings using natural-English connectors for the
    "Review X and Y" recommended check.

    - 0 items -> ``""``
    - 1 item  -> ``"X"``
    - 2 items -> ``"X and Y"``
    - 3+      -> ``"X, Y, and Z"`` (Oxford comma)
    """

    if not details:
        return ""
    if len(details) == 1:
        return details[0]
    if len(details) == 2:
        return f"{details[0]} and {details[1]}"
    return f"{', '.join(details[:-1])}, and {details[-1]}"


def trace_object(
    target_object_id: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: Optional[list[ExecutionContext]] = None,
) -> ReasoningTraceResult:
    """Deterministic cause/effect trace over normalized reasoning objects.

    Answers:
        * What writes this object/tag?
        * What reads this object/tag?
        * What is upstream of it?
        * What is downstream of it?
        * What execution contexts are involved?
        * Are there multiple writers?
        * Are there obvious conflict risks?

    The function is pure -- it does not mutate any input list -- and
    safe to call even when the target is unresolved (in which case
    confidence drops and a recommended check is emitted).

    Args:
        target_object_id: The ``ControlObject.id`` to trace.
        control_objects: All known normalized objects (so the target
            can be looked up by id; resolution is best-effort).
        relationships: All known relationships. Direction rules above.
        execution_contexts: Optional list of execution contexts; any
            referenced by the relevant relationships are surfaced.

    Returns:
        A ``TraceResult`` from ``app.models.reasoning``.
    """

    target = find_object_by_id(target_object_id, control_objects)
    is_unresolved = _is_unresolved_target(target)

    # Lookup indices built once for all per-relationship detail formatting.
    # Later occurrences win on duplicate ids, which mirrors the iteration
    # order callers (and the normalization service) expect.
    object_index: dict[str, ControlObject] = {
        obj.id: obj for obj in control_objects
    }
    exec_ctx_index: dict[str, ExecutionContext] = {
        ec.id: ec for ec in (execution_contexts or [])
    }

    writer_rels = get_writer_relationships(target_object_id, relationships)
    reader_rels = get_reader_relationships(target_object_id, relationships)
    upstream_ids = get_upstream_object_ids(target_object_id, relationships)
    downstream_ids = get_downstream_object_ids(target_object_id, relationships)
    multiple_writers = detect_multiple_writers(target_object_id, relationships)

    all_relationships = _gather_relevant_relationships(
        target_object_id=target_object_id,
        relationships=relationships,
        writer_rels=writer_rels,
        reader_rels=reader_rels,
    )

    # Resolve which execution contexts are referenced by relevant edges.
    relevant_exec_ctx_ids = {
        r.execution_context_id
        for r in (writer_rels + reader_rels)
        if r.execution_context_id
    }
    relevant_exec_ctx_ids.update(
        r.execution_context_id
        for r in all_relationships
        if r.execution_context_id
    )
    involved_exec_ctxs = [
        ec for ec in (execution_contexts or [])
        if ec.id in relevant_exec_ctx_ids
    ]

    conclusions = _build_conclusions(
        target_object_id=target_object_id,
        target=target,
        writer_rels=writer_rels,
        reader_rels=reader_rels,
        multiple_writers=multiple_writers,
        is_unresolved=is_unresolved,
        object_index=object_index,
        exec_ctx_index=exec_ctx_index,
    )

    recommended_checks = _aggregate_recommended_checks(conclusions)
    failure_impact = list(target.failure_impact) if target else []
    confidence = _overall_confidence(
        writer_rels=writer_rels,
        reader_rels=reader_rels,
        is_unresolved=is_unresolved,
    )
    summary = build_trace_summary(
        target_object_id=target_object_id,
        target=target,
        writer_relationships=writer_rels,
        reader_relationships=reader_rels,
        upstream_object_ids=upstream_ids,
        downstream_object_ids=downstream_ids,
        multiple_writers=multiple_writers,
        is_unresolved=is_unresolved,
        object_index=object_index,
        exec_ctx_index=exec_ctx_index,
    )

    target_name = (target.name if target else None) or target_object_id

    return ReasoningTraceResult(
        id=f"trace::{target_object_id}",
        name=f"Trace: {target_name}",
        source_platform=target.source_platform if target else None,
        confidence=confidence,
        target_object_id=target_object_id,
        upstream_object_ids=upstream_ids,
        downstream_object_ids=downstream_ids,
        writer_relationships=writer_rels,
        reader_relationships=reader_rels,
        relationships=all_relationships,
        conclusions=conclusions,
        recommended_checks=recommended_checks,
        failure_impact=failure_impact,
        summary=summary,
        platform_specific={
            "execution_context_ids": [ec.id for ec in involved_exec_ctxs],
            "multiple_writers": multiple_writers,
            "is_unresolved": is_unresolved,
        },
    )


# ---------------------------------------------------------------------------
# Public helpers (re-exported so other modules / tests can reuse them)
# ---------------------------------------------------------------------------


def find_object_by_id(
    object_id: str,
    control_objects: list[ControlObject],
) -> Optional[ControlObject]:
    """Linear lookup by id. Returns ``None`` if not present."""

    for obj in control_objects:
        if obj.id == object_id:
            return obj
    return None


def get_writer_relationships(
    target_object_id: str,
    relationships: list[Relationship],
) -> list[Relationship]:
    """Relationships whose ``target_id`` is the target and whose type
    is one of ``WRITER_RELATIONSHIP_TYPES``."""

    return [
        r for r in relationships
        if r.target_id == target_object_id
        and r.relationship_type in WRITER_RELATIONSHIP_TYPES
    ]


def get_reader_relationships(
    target_object_id: str,
    relationships: list[Relationship],
) -> list[Relationship]:
    """Relationships whose ``target_id`` is the target and whose type
    is ``READS``. Not counted as writers."""

    return [
        r for r in relationships
        if r.target_id == target_object_id
        and r.relationship_type == RelationshipType.READS
    ]


def get_upstream_object_ids(
    target_object_id: str,
    relationships: list[Relationship],
) -> list[str]:
    """Unique source-side ids from writer relationships.

    Order is preserved (first occurrence wins) so downstream consumers
    get a stable, scan-order-faithful list.
    """

    seen: set[str] = set()
    result: list[str] = []
    for r in get_writer_relationships(target_object_id, relationships):
        if r.source_id not in seen:
            seen.add(r.source_id)
            result.append(r.source_id)
    return result


def get_downstream_object_ids(
    target_object_id: str,
    relationships: list[Relationship],
) -> list[str]:
    """Unique target-side ids of source-outgoing edges from the target.

    "Downstream" follows the user-defined rule: relationships where
    ``source_id == target_object_id`` and the type is not ``CONTAINS``.
    Readers (relationships pointing *at* the target) are tracked
    separately via ``get_reader_relationships``.
    """

    seen: set[str] = set()
    result: list[str] = []
    for r in relationships:
        if r.source_id != target_object_id:
            continue
        if r.relationship_type == RelationshipType.CONTAINS:
            continue
        if r.target_id not in seen:
            seen.add(r.target_id)
            result.append(r.target_id)
    return result


def detect_multiple_writers(
    target_object_id: str,
    relationships: list[Relationship],
) -> bool:
    """True iff more than one writer relationship targets the object."""

    return (
        len(get_writer_relationships(target_object_id, relationships)) > 1
    )


def build_trace_summary(
    target_object_id: str,
    target: Optional[ControlObject],
    writer_relationships: list[Relationship],
    reader_relationships: list[Relationship],
    upstream_object_ids: list[str],
    downstream_object_ids: list[str],
    multiple_writers: bool,
    is_unresolved: bool,
    *,
    object_index: Optional[dict[str, ControlObject]] = None,
    exec_ctx_index: Optional[dict[str, ExecutionContext]] = None,
) -> str:
    """Build a short, human-readable trace summary with location detail.

    Writer locations are always inlined when at least one writer exists,
    so a UI can render the summary verbatim without re-formatting.
    Reader and downstream counts are summarized to keep the summary
    bounded; the full reader location list lives in the corresponding
    ``TruthConclusion`` statement.

    ``object_index`` and ``exec_ctx_index`` are optional pre-built
    lookup dicts. When omitted, formatting still works as long as the
    relationships themselves carry ``source_location`` /
    ``platform_specific["instruction_type"]`` (which the normalization
    service does by default).
    """

    name = (target.name if target else None) or target_object_id

    parts: list[str] = []

    if writer_relationships:
        writer_details = [
            _detail_from_indices(r, object_index, exec_ctx_index)
            for r in writer_relationships
        ]
        parts.append(
            f"{name} is written in {len(writer_relationships)} place(s): "
            f"{_format_locations_list(writer_details)}."
        )

    if reader_relationships:
        parts.append(
            f"Read in {len(reader_relationships)} place(s)."
        )

    if downstream_object_ids:
        parts.append(
            f"{len(downstream_object_ids)} downstream object(s) directly "
            "affected."
        )

    if multiple_writers:
        parts.append(
            "Multiple writers detected; final state may depend on "
            "execution context or scan order."
        )

    if is_unresolved:
        parts.append(
            "Target object is unresolved or absent from the normalized "
            "graph."
        )

    if not writer_relationships and not reader_relationships:
        parts.append(
            f"{name} has no direct cause/effect relationships in "
            "normalized logic."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_unresolved_target(target: Optional[ControlObject]) -> bool:
    """A target is unresolved if it isn't in the object list, or if its
    ``platform_specific.unresolved`` flag is True (stub from the
    normalization service)."""

    if target is None:
        return True
    return bool(target.platform_specific.get("unresolved"))


def _gather_relevant_relationships(
    target_object_id: str,
    relationships: list[Relationship],
    writer_rels: list[Relationship],
    reader_rels: list[Relationship],
) -> list[Relationship]:
    """Union of writer, reader, downstream (source-outgoing non-CONTAINS),
    and contextual CONTAINS relationships that involve the target.

    Order preserves the original ``relationships`` order for determinism.
    """

    keep_ids: set[int] = set()
    for r in writer_rels:
        keep_ids.add(id(r))
    for r in reader_rels:
        keep_ids.add(id(r))

    for r in relationships:
        if r.source_id == target_object_id and r.relationship_type != RelationshipType.CONTAINS:
            keep_ids.add(id(r))
        # Structural containment in either direction is kept as context.
        if r.relationship_type == RelationshipType.CONTAINS and (
            r.source_id == target_object_id
            or r.target_id == target_object_id
        ):
            keep_ids.add(id(r))

    return [r for r in relationships if id(r) in keep_ids]


def _build_conclusions(
    target_object_id: str,
    target: Optional[ControlObject],
    writer_rels: list[Relationship],
    reader_rels: list[Relationship],
    multiple_writers: bool,
    is_unresolved: bool,
    object_index: Optional[dict[str, ControlObject]] = None,
    exec_ctx_index: Optional[dict[str, ExecutionContext]] = None,
) -> list[TruthConclusion]:
    """Build a small set of deterministic TruthConclusion statements.

    Statements are location-aware: writer / reader / multiple-writer
    conclusions cite the actual routine / rung / instruction-type for
    each relationship so any UI can surface them verbatim with no
    further formatting.

    Wording mirrors the spec examples (parenthetical "place(s)",
    semicolon-separated detail lists, "Review X and Y" style for the
    multiple-writer recommended check).
    """

    name = (target.name if target else None) or target_object_id
    subject_ids = [target_object_id]
    conclusions: list[TruthConclusion] = []

    writer_details = [
        _detail_from_indices(r, object_index, exec_ctx_index)
        for r in writer_rels
    ]
    reader_details = [
        _detail_from_indices(r, object_index, exec_ctx_index)
        for r in reader_rels
    ]

    # Writers
    if writer_rels:
        conclusions.append(
            TruthConclusion(
                statement=(
                    f"{name} is written in {len(writer_rels)} place(s): "
                    f"{_format_locations_list(writer_details)}."
                ),
                subject_ids=list(subject_ids),
                truth_context=TruthContextType.DESIGN_TRUTH,
                confidence=ConfidenceLevel.HIGH,
                supporting_evidence=[
                    _evidence_for_relationship(
                        r, "Writes target object", target_object_id
                    )
                    for r in writer_rels
                ],
            )
        )
    else:
        conclusions.append(
            TruthConclusion(
                statement=(
                    f"No writer found for {name} in normalized logic."
                ),
                subject_ids=list(subject_ids),
                truth_context=TruthContextType.DESIGN_TRUTH,
                confidence=ConfidenceLevel.LOW,
                recommended_checks=[
                    "Verify the tag is written in another routine, "
                    "controller, HMI, external system, or online edit.",
                ],
            )
        )

    # Readers
    if reader_rels:
        conclusions.append(
            TruthConclusion(
                statement=(
                    f"{name} is read in {len(reader_rels)} place(s): "
                    f"{_format_locations_list(reader_details)}."
                ),
                subject_ids=list(subject_ids),
                truth_context=TruthContextType.DESIGN_TRUTH,
                confidence=ConfidenceLevel.HIGH,
                supporting_evidence=[
                    _evidence_for_relationship(
                        r, "Reads target object", target_object_id
                    )
                    for r in reader_rels
                ],
            )
        )

    # Conflict risk: multiple writers. Both the conclusion statement
    # and the recommended check name the writer locations so a
    # reviewer can jump straight to the offending rungs.
    if multiple_writers:
        review_list = _format_locations_for_review(writer_details)
        conclusions.append(
            TruthConclusion(
                statement=(
                    f"Multiple writers detected for {name}: "
                    f"{_format_locations_list(writer_details)}. "
                    "Final state may depend on execution context or "
                    "scan order."
                ),
                subject_ids=list(subject_ids),
                truth_context=TruthContextType.DESIGN_TRUTH,
                confidence=ConfidenceLevel.HIGH,
                recommended_checks=[
                    f"Review {review_list} to confirm intended "
                    "priority/scan behavior.",
                ],
            )
        )

    # Unresolved / stub target
    if is_unresolved:
        conclusions.append(
            TruthConclusion(
                statement=(
                    "Target object is unresolved or absent in the "
                    "normalized graph."
                ),
                subject_ids=list(subject_ids),
                truth_context=TruthContextType.DESIGN_TRUTH,
                confidence=ConfidenceLevel.MEDIUM,
                recommended_checks=[
                    "Verify tag mapping or parser coverage for this "
                    "operand.",
                ],
            )
        )

    return conclusions


def _evidence_for_relationship(
    relationship: Relationship,
    statement_prefix: str,
    target_object_id: str,
) -> Evidence:
    """Wrap a relationship in an ``Evidence`` record so conclusions can
    cite it directly."""

    return Evidence(
        evidence_type=EvidenceType.DERIVED_LOGIC,
        truth_context=TruthContextType.DESIGN_TRUTH,
        statement=(
            f"{statement_prefix}: {relationship.source_id} "
            f"--{relationship.relationship_type.value}--> "
            f"{relationship.target_id}"
        ),
        source_platform=relationship.source_platform,
        source_location=relationship.source_location,
        source_excerpt=relationship.logic_condition,
        confidence=relationship.confidence,
        platform_specific={
            "relationship_id": relationship.id,
            "target_object_id": target_object_id,
            "relationship_type": relationship.relationship_type.value,
            "write_behavior": (
                relationship.write_behavior.value
                if relationship.write_behavior
                else None
            ),
            "execution_context_id": relationship.execution_context_id,
        },
    )


def _aggregate_recommended_checks(
    conclusions: list[TruthConclusion],
) -> list[str]:
    """Collect unique recommended_checks from conclusions, preserving
    first-occurrence order."""

    seen: set[str] = set()
    result: list[str] = []
    for c in conclusions:
        for check in c.recommended_checks:
            if check in seen:
                continue
            seen.add(check)
            result.append(check)
    return result


def _overall_confidence(
    writer_rels: list[Relationship],
    reader_rels: list[Relationship],
    is_unresolved: bool,
) -> ConfidenceLevel:
    """HIGH when direct cause/effect edges exist and the target is
    resolved; MEDIUM when the target is unresolved (but we still have
    edges); LOW when no cause/effect edges exist."""

    has_cause_effect = bool(writer_rels or reader_rels)
    if not has_cause_effect:
        return ConfidenceLevel.LOW
    if is_unresolved:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.HIGH


__all__ = [
    # legacy
    "trace_tag",
    # v1 entry point
    "trace_object",
    # v1 helpers
    "find_object_by_id",
    "get_writer_relationships",
    "get_reader_relationships",
    "get_upstream_object_ids",
    "get_downstream_object_ids",
    "detect_multiple_writers",
    "build_trace_summary",
    "format_relationship_detail",
    # v1 constants
    "WRITER_RELATIONSHIP_TYPES",
]
