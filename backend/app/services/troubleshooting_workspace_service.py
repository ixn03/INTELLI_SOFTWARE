"""Deterministic signal troubleshooting workspace service.

This layer turns a controls question into a UI-ready evidence workspace:
target signal, writers, upstream conditions, downstream readers, unknown
direction references, confidence, and a short deterministic explanation.

No LLMs are used here. Matching and conclusions are rule-based over the
normalized INTELLI model.
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)
from app.models.unified_evidence import UnifiedSignalEvidence
from app.models.knowledge import KnowledgeItem, KnowledgeType
from app.services.dependency_graph_service import build_control_dependency_graph
from app.services.knowledge_service import knowledge_rank_score, knowledge_service
from app.services.unified_evidence_service import get_signal_evidence


TroubleshootingIntent = Literal[
    "why_not_energized",
    "why_on",
    "where_written",
    "where_read",
    "trace_upstream",
    "trace_downstream",
    "general_signal_lookup",
]

WRITER_TYPES: frozenset[RelationshipType] = frozenset(
    {
        RelationshipType.WRITES,
        RelationshipType.LATCHES,
        RelationshipType.UNLATCHES,
        RelationshipType.RESETS,
        RelationshipType.CALCULATES,
        RelationshipType.SCALES,
    }
)


class SignalCandidate(BaseModel):
    id: str
    name: str | None = None
    source_location: str | None = None
    match_type: str
    score: float


class QuestionInterpretation(BaseModel):
    intent: TroubleshootingIntent
    target_signal_candidates: list[SignalCandidate] = Field(default_factory=list)
    selected_target_signal: SignalCandidate | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SignalRef(BaseModel):
    id: str
    name: str | None = None
    object_type: str | None = None
    source_location: str | None = None


class EvidenceItem(BaseModel):
    source_id: str
    source_name: str | None = None
    source_type: str | None = None
    target_id: str
    target_name: str | None = None
    relationship_type: str
    source_location: str | None = None
    instruction_type: str | None = None
    write_behavior: str | None = None
    condition_signal_ids: list[str] = Field(default_factory=list)
    condition_signal_names: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfidenceSummary(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    static_logic_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_live_state: bool = True
    missing_history: bool = True
    missing_engineer_knowledge: bool = True


class ScopedTagCandidate(BaseModel):
    id: str
    name: str | None = None
    controller: str | None = None
    program: str | None = None
    source_location: str | None = None
    match_type: str
    score: float


class ResolvedScope(BaseModel):
    controller: str | None = None
    program: str | None = None
    duplicate_name_status: Literal["unique", "duplicates_found", "unknown"] = "unknown"
    candidate_scoped_tags: list[ScopedTagCandidate] = Field(default_factory=list)


class SignalControlBreakdown(BaseModel):
    upstream_required_conditions: list[EvidenceItem] = Field(default_factory=list)
    upstream_dependencies: list[EvidenceItem] = Field(default_factory=list)
    writer_conditions: list[EvidenceItem] = Field(default_factory=list)
    unknown_direction_references: list[EvidenceItem] = Field(default_factory=list)


class DownstreamImpactBreakdown(BaseModel):
    downstream_readers: list[EvidenceItem] = Field(default_factory=list)
    downstream_writes_influenced: list[EvidenceItem] = Field(default_factory=list)
    downstream_routines: list[EvidenceItem] = Field(default_factory=list)
    downstream_aoi_blocks: list[EvidenceItem] = Field(default_factory=list)
    downstream_fbd_blocks: list[EvidenceItem] = Field(default_factory=list)
    downstream_st_statements: list[EvidenceItem] = Field(default_factory=list)


class WriterEvidenceGroups(BaseModel):
    ladder: list[EvidenceItem] = Field(default_factory=list)
    fbd: list[EvidenceItem] = Field(default_factory=list)
    aoi: list[EvidenceItem] = Field(default_factory=list)
    structured_text: list[EvidenceItem] = Field(default_factory=list)
    sfc: list[EvidenceItem] = Field(default_factory=list)
    unknown: list[EvidenceItem] = Field(default_factory=list)


class EvidenceProvenanceItem(BaseModel):
    relationship_id: str
    relationship_type: str
    language: str = "unknown"
    source_id: str
    source_name: str | None = None
    source_type: str | None = None
    target_id: str
    target_name: str | None = None
    routine: str | None = None
    rung: int | None = None
    block: str | None = None
    pin: str | None = None
    statement: str | None = None
    source_location: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    deterministic: bool = True


class KnowledgeContext(BaseModel):
    engineer_notes: list[KnowledgeItem] = Field(default_factory=list)
    control_narrative_facts: list[KnowledgeItem] = Field(default_factory=list)
    documentation_facts: list[KnowledgeItem] = Field(default_factory=list)
    upstream_dependency_facts: list[KnowledgeItem] = Field(default_factory=list)
    writer_facts: list[KnowledgeItem] = Field(default_factory=list)
    conflicts: list[KnowledgeItem] = Field(default_factory=list)
    confidence_contribution: float = Field(default=0.0, ge=0.0, le=1.0)
    status: Literal["available", "empty"] = "empty"


class LiveValue(BaseModel):
    signal_id: str
    signal_name: str | None = None
    value: Any = None
    timestamp: str | None = None
    stale: bool = False


class CurrentStateExplanation(BaseModel):
    status: Literal["live_data_available", "live_data_missing"] = "live_data_missing"
    target_current_value: LiveValue | None = None
    upstream_condition_current_values: list[LiveValue] = Field(default_factory=list)
    blocking_conditions: list[LiveValue] = Field(default_factory=list)
    satisfied_conditions: list[LiveValue] = Field(default_factory=list)
    stale_values: list[LiveValue] = Field(default_factory=list)
    missing_values: list[SignalRef] = Field(default_factory=list)
    confidence_contribution: float = Field(default=0.0, ge=0.0, le=1.0)


class HistoricalContext(BaseModel):
    recent_changes: list[Any] = Field(default_factory=list)
    repeated_patterns: list[Any] = Field(default_factory=list)
    related_alarms: list[Any] = Field(default_factory=list)
    last_transition: Any = None
    status: Literal["not_available"] = "not_available"


class SignalTroubleshootingWorkspace(BaseModel):
    question: str
    interpretation: QuestionInterpretation
    target_signal: SignalRef | None = None
    resolved_scope: ResolvedScope = Field(default_factory=ResolvedScope)
    unified_evidence: UnifiedSignalEvidence | None = None
    what_controls_this_signal: SignalControlBreakdown = Field(default_factory=SignalControlBreakdown)
    what_this_signal_controls: DownstreamImpactBreakdown = Field(default_factory=DownstreamImpactBreakdown)
    who_writes_this_signal: WriterEvidenceGroups = Field(default_factory=WriterEvidenceGroups)
    where_evidence_comes_from: list[EvidenceProvenanceItem] = Field(default_factory=list)
    knowledge_context: KnowledgeContext = Field(default_factory=KnowledgeContext)
    current_state_explanation: CurrentStateExplanation = Field(default_factory=CurrentStateExplanation)
    historical_context: HistoricalContext = Field(default_factory=HistoricalContext)
    writer_rungs: list[EvidenceItem] = Field(default_factory=list)
    upstream_required_conditions: list[EvidenceItem] = Field(default_factory=list)
    downstream_readers: list[EvidenceItem] = Field(default_factory=list)
    unknown_direction_blocks: list[EvidenceItem] = Field(default_factory=list)
    confidence_summary: ConfidenceSummary
    deterministic_explanation: str
    advanced_details: dict[str, Any] = Field(default_factory=dict)


def interpret_question(
    question: str,
    control_objects: list[ControlObject],
) -> QuestionInterpretation:
    intent = _detect_intent(question)
    candidates = _resolve_target_candidates(question, control_objects)
    selected = candidates[0] if candidates else None
    confidence = selected.score if selected else 0.0
    return QuestionInterpretation(
        intent=intent,
        target_signal_candidates=candidates[:5],
        selected_target_signal=selected,
        confidence=round(confidence, 3),
        metadata={"resolver_version": "signal_workspace_v1"},
    )


def build_signal_workspace(
    *,
    question: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    runtime_snapshot: dict[str, Any] | None = None,
) -> SignalTroubleshootingWorkspace:
    object_index = {obj.id: obj for obj in control_objects}
    interpretation = interpret_question(question, control_objects)
    selected = interpretation.selected_target_signal

    if selected is None:
        confidence = ConfidenceSummary(
            confidence=0.15,
            missing_evidence=["target_signal"],
            warnings=["No matching signal was found in the normalized model."],
        )
        return SignalTroubleshootingWorkspace(
            question=question,
            interpretation=interpretation,
            target_signal=None,
            confidence_summary=confidence,
            deterministic_explanation=(
                "I could not identify a target signal in the question. Try the exact "
                "tag name or a longer member path."
            ),
            advanced_details={"relationship_ids": []},
        )

    graph = build_control_dependency_graph(control_objects, relationships)
    target_id = selected.id
    target = object_index.get(target_id)
    writer_rels = [
        rel
        for rel in relationships
        if rel.target_id == target_id and rel.relationship_type in WRITER_TYPES
    ]
    reader_rels = [
        rel
        for rel in relationships
        if rel.target_id == target_id and rel.relationship_type == RelationshipType.READS
    ]
    unknown_rels = [
        rel
        for rel in relationships
        if rel.target_id == target_id
        and rel.relationship_type == RelationshipType.REFERENCES
        and (rel.platform_specific or {}).get("binding_status") == "direction_unknown"
    ]

    writer_items = [
        _evidence_item(rel, object_index, graph.provenance.get(target_id)) for rel in writer_rels
    ]
    downstream_items = [_evidence_item(rel, object_index, None) for rel in reader_rels]
    unknown_items = [_evidence_item(rel, object_index, None) for rel in unknown_rels]
    upstream_items = _upstream_items_for_target(
        target_id,
        writer_rels,
        relationships,
        object_index,
    )
    unified = get_signal_evidence(target_id, control_objects, relationships)
    explanation = unified.summary.answer
    confidence_summary = ConfidenceSummary(
        confidence=unified.confidence_summary.confidence,
        evidence=unified.confidence_summary.evidence,
        missing_evidence=unified.confidence_summary.missing_evidence,
        warnings=unified.confidence_summary.warnings,
        static_logic_confidence=unified.confidence_summary.confidence,
    )
    downstream_impact = _downstream_impact(
        target_id,
        downstream_items,
        relationships,
        object_index,
    )
    knowledge_context = _knowledge_context(
        target_id=target_id,
        upstream_items=upstream_items,
        writer_items=writer_items,
    )
    current_state = _current_state_explanation(
        target=_signal_ref(target, target_id),
        upstream_items=upstream_items,
        runtime_snapshot=runtime_snapshot,
        object_index=object_index,
    )
    confidence_summary.missing_live_state = current_state.status == "live_data_missing"
    confidence_summary.missing_engineer_knowledge = knowledge_context.status == "empty"

    return SignalTroubleshootingWorkspace(
        question=question,
        interpretation=interpretation,
        target_signal=_signal_ref(target, target_id),
        resolved_scope=_resolved_scope(selected, interpretation.target_signal_candidates),
        unified_evidence=unified,
        what_controls_this_signal=SignalControlBreakdown(
            upstream_required_conditions=upstream_items,
            upstream_dependencies=_dependency_items_for_target(
                target_id, relationships, object_index
            ),
            writer_conditions=upstream_items,
            unknown_direction_references=unknown_items,
        ),
        what_this_signal_controls=downstream_impact,
        who_writes_this_signal=_writer_groups(writer_items),
        where_evidence_comes_from=_evidence_provenance(
            [*writer_rels, *reader_rels, *unknown_rels],
            object_index,
        ),
        knowledge_context=knowledge_context,
        current_state_explanation=current_state,
        historical_context=HistoricalContext(),
        writer_rungs=writer_items,
        upstream_required_conditions=upstream_items,
        downstream_readers=downstream_items,
        unknown_direction_blocks=unknown_items,
        confidence_summary=confidence_summary,
        deterministic_explanation=explanation,
        advanced_details={
            **unified.advanced_details,
            "parser_metadata": {
                "target_resolution": interpretation.metadata,
                "target_match_type": selected.match_type,
            },
        },
    )


def _detect_intent(question: str) -> TroubleshootingIntent:
    q = question.lower()
    if (
        any(x in q for x in ("what writes", "who writes", "written by"))
        or ("where" in q and any(x in q for x in ("written", "write", "writes")))
    ):
        return "where_written"
    if (
        any(x in q for x in ("where read", "where used", "what reads", "who reads", "used downstream"))
        or ("where" in q and any(x in q for x in ("read", "reads", "used")))
    ):
        return "where_read"
    if any(x in q for x in ("downstream", "what does", "uses this", "used by")):
        return "trace_downstream"
    if any(x in q for x in ("upstream", "what must", "permissive", "interlock", "blocks", "blocking")):
        return "trace_upstream"
    if any(
        x in q
        for x in (
            "why not",
            "not ",
            " false",
            " off",
            "not energ",
            "not running",
            "won't",
            "wont",
            "not changing",
        )
    ):
        return "why_not_energized"
    if any(x in q for x in ("why on", "stuck on", "energized", " true", " running")):
        return "why_on"
    return "general_signal_lookup"


def _resolve_target_candidates(
    question: str,
    control_objects: list[ControlObject],
) -> list[SignalCandidate]:
    signals = [
        obj
        for obj in control_objects
        if obj.object_type
        in {
            ControlObjectType.TAG,
            ControlObjectType.SYSTEM_ATTRIBUTE,
            ControlObjectType.FUNCTION_BLOCK_PIN,
        }
        and obj.name
    ]
    if not signals:
        return []
    q = question.strip()
    q_lower = q.lower()

    tiers: list[tuple[str, float, list[ControlObject]]] = [
        ("exact_tag_match", 1.0, [obj for obj in signals if obj.name and obj.name in q]),
        (
            "case_insensitive_tag_match",
            0.96,
            [obj for obj in signals if obj.name and obj.name.lower() in q_lower],
        ),
        (
            "member_or_root_tag_match",
            0.9,
            [obj for obj in signals if obj.name and _member_or_root_match(obj.name, q_lower)],
        ),
    ]
    for match_type, score, matches in tiers:
        candidates = _dedupe_candidates(matches, match_type, score)
        if candidates:
            return candidates

    tokens = _question_tokens(q_lower)
    fuzzy: list[SignalCandidate] = []
    for obj in signals:
        name = obj.name or ""
        ratio = _best_fuzzy_ratio(name.lower(), tokens)
        if ratio >= 0.68:
            fuzzy.append(_candidate(obj, "fuzzy_match", 0.55 + ratio * 0.3))
    return sorted(fuzzy, key=lambda c: (-c.score, -(len(c.name or "")), c.id))[:5]


def _member_or_root_match(name: str, q_lower: str) -> bool:
    low = name.lower()
    root = low.split(".", 1)[0].split("[", 1)[0]
    members = [part for part in re.split(r"[.\[\]]+", low) if part]
    return root in q_lower or any(member in q_lower for member in members if len(member) >= 4)


def _question_tokens(q_lower: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z_][a-zA-Z0-9_.]*", q_lower)
    stop = {"why", "is", "the", "not", "where", "what", "read", "written", "by", "on", "off"}
    return [t for t in raw if t not in stop and len(t) >= 3]


def _best_fuzzy_ratio(name: str, tokens: list[str]) -> float:
    values = tokens + [".".join(tokens[i : i + 2]) for i in range(max(0, len(tokens) - 1))]
    if not values:
        return 0.0
    return max(difflib.SequenceMatcher(None, name, value).ratio() for value in values)


def _dedupe_candidates(
    objects: list[ControlObject],
    match_type: str,
    score: float,
) -> list[SignalCandidate]:
    out: dict[str, SignalCandidate] = {}
    for obj in sorted(objects, key=lambda o: (-(len(o.name or "")), o.id)):
        out.setdefault(obj.id, _candidate(obj, match_type, score))
    return list(out.values())[:5]


def _candidate(obj: ControlObject, match_type: str, score: float) -> SignalCandidate:
    return SignalCandidate(
        id=obj.id,
        name=obj.name,
        source_location=obj.source_location,
        match_type=match_type,
        score=round(min(0.99, max(0.0, score)), 3),
    )


def _upstream_items_for_target(
    target_id: str,
    writer_rels: list[Relationship],
    relationships: list[Relationship],
    object_index: dict[str, ControlObject],
) -> list[EvidenceItem]:
    by_source: dict[str, list[Relationship]] = {}
    for rel in relationships:
        by_source.setdefault(rel.source_id, []).append(rel)
    out: list[EvidenceItem] = []
    seen: set[tuple[str, str]] = set()
    for writer in writer_rels:
        for rel in by_source.get(writer.source_id, []):
            if rel.relationship_type != RelationshipType.READS or rel.target_id == target_id:
                continue
            key = (writer.id, rel.target_id)
            if key in seen:
                continue
            seen.add(key)
            item = _evidence_item(rel, object_index, None)
            item.metadata["writer_relationship_id"] = writer.id
            out.append(item)
    return out


def _dependency_items_for_target(
    target_id: str,
    relationships: list[Relationship],
    object_index: dict[str, ControlObject],
) -> list[EvidenceItem]:
    return [
        _evidence_item(rel, object_index, None)
        for rel in relationships
        if rel.target_id == target_id
        and rel.relationship_type
        in {
            RelationshipType.DEPENDS_ON,
            RelationshipType.CONDITION_FOR,
            RelationshipType.PERMITS,
            RelationshipType.INHIBITS,
        }
    ]


def _downstream_impact(
    target_id: str,
    downstream_items: list[EvidenceItem],
    relationships: list[Relationship],
    object_index: dict[str, ControlObject],
) -> DownstreamImpactBreakdown:
    reader_source_ids = {item.source_id for item in downstream_items}
    writes_influenced = [
        _evidence_item(rel, object_index, None)
        for rel in relationships
        if rel.source_id in reader_source_ids
        and rel.target_id != target_id
        and rel.relationship_type in WRITER_TYPES
    ]
    routines: list[EvidenceItem] = []
    aoi_blocks: list[EvidenceItem] = []
    fbd_blocks: list[EvidenceItem] = []
    st_statements: list[EvidenceItem] = []
    for item in downstream_items:
        language = _language_for_item(item)
        if language == "aoi":
            aoi_blocks.append(item)
        elif language == "fbd":
            fbd_blocks.append(item)
        elif language == "structured_text":
            st_statements.append(item)
        else:
            routines.append(item)
    return DownstreamImpactBreakdown(
        downstream_readers=downstream_items,
        downstream_writes_influenced=writes_influenced,
        downstream_routines=routines,
        downstream_aoi_blocks=aoi_blocks,
        downstream_fbd_blocks=fbd_blocks,
        downstream_st_statements=st_statements,
    )


def _writer_groups(writer_items: list[EvidenceItem]) -> WriterEvidenceGroups:
    groups = WriterEvidenceGroups()
    for item in writer_items:
        language = _language_for_item(item)
        if language == "ladder":
            groups.ladder.append(item)
        elif language == "fbd":
            groups.fbd.append(item)
        elif language == "aoi":
            groups.aoi.append(item)
        elif language == "structured_text":
            groups.structured_text.append(item)
        elif language == "sfc":
            groups.sfc.append(item)
        else:
            groups.unknown.append(item)
    return groups


def _evidence_provenance(
    relationships: list[Relationship],
    object_index: dict[str, ControlObject],
) -> list[EvidenceProvenanceItem]:
    out: list[EvidenceProvenanceItem] = []
    for rel in relationships:
        item = _evidence_item(rel, object_index, None)
        location = item.source_location or ""
        out.append(
            EvidenceProvenanceItem(
                relationship_id=rel.id,
                relationship_type=rel.relationship_type.value,
                language=_language_for_item(item),
                source_id=item.source_id,
                source_name=item.source_name,
                source_type=item.source_type,
                target_id=item.target_id,
                target_name=item.target_name,
                routine=_extract_location_part(location, "Routine"),
                rung=_extract_rung(location),
                block=_extract_location_part(location, "Block"),
                pin=_extract_location_part(location, "Pin"),
                statement=_extract_location_part(location, "Statement"),
                source_location=item.source_location,
                confidence=item.confidence,
                deterministic=rel.relationship_type != RelationshipType.REFERENCES
                or (rel.platform_specific or {}).get("binding_status") != "direction_unknown",
            )
        )
    return out


def _resolved_scope(
    selected: SignalCandidate,
    candidates: list[SignalCandidate],
) -> ResolvedScope:
    selected_scope = _scope_from_location(selected.source_location)
    same_name = [c for c in candidates if c.name == selected.name]
    duplicate_status: Literal["unique", "duplicates_found", "unknown"]
    if not selected.name:
        duplicate_status = "unknown"
    elif len(same_name) > 1:
        duplicate_status = "duplicates_found"
    else:
        duplicate_status = "unique"
    scoped = []
    for candidate in same_name or candidates:
        scope = _scope_from_location(candidate.source_location)
        scoped.append(
            ScopedTagCandidate(
                id=candidate.id,
                name=candidate.name,
                controller=scope.get("controller"),
                program=scope.get("program"),
                source_location=candidate.source_location,
                match_type=candidate.match_type,
                score=candidate.score,
            )
        )
    return ResolvedScope(
        controller=selected_scope.get("controller"),
        program=selected_scope.get("program"),
        duplicate_name_status=duplicate_status,
        candidate_scoped_tags=scoped,
    )


def _knowledge_context(
    *,
    target_id: str,
    upstream_items: list[EvidenceItem],
    writer_items: list[EvidenceItem],
) -> KnowledgeContext:
    target_items = _rank_knowledge(knowledge_service.list_by_target(target_id))
    upstream_ids = {item.target_id for item in upstream_items}
    writer_ids = {item.source_id for item in writer_items}
    upstream_items_knowledge = _rank_knowledge(
        [item for oid in upstream_ids for item in knowledge_service.list_by_target(oid)]
    )
    writer_items_knowledge = _rank_knowledge(
        [item for oid in writer_ids for item in knowledge_service.list_by_target(oid)]
    )
    conflicts = [
        item
        for item in [*target_items, *upstream_items_knowledge, *writer_items_knowledge]
        if item.knowledge_type
        in {
            KnowledgeType.KNOWN_FALSE_POSITIVE,
            KnowledgeType.REJECTED_FIX,
            KnowledgeType.ASSUMPTION,
        }
    ]
    context = KnowledgeContext(
        engineer_notes=[
            item
            for item in target_items
            if item.knowledge_type
            in {
                KnowledgeType.TROUBLESHOOTING_NOTE,
                KnowledgeType.ENGINEER_FEEDBACK,
                KnowledgeType.OPERATOR_GUIDANCE,
                KnowledgeType.VERIFIED_FIX,
            }
        ],
        control_narrative_facts=[
            item
            for item in target_items
            if item.knowledge_type
            in {
                KnowledgeType.CONTROL_NARRATIVE_NOTE,
                KnowledgeType.CONTROL_NARRATIVE_SECTION,
                KnowledgeType.SEQUENCE_EXPLANATION,
            }
        ],
        documentation_facts=[
            item
            for item in target_items
            if item.knowledge_type
            in {
                KnowledgeType.TAG_DESCRIPTION,
                KnowledgeType.STATE_DESCRIPTION,
                KnowledgeType.EQUIPMENT_DESCRIPTION,
                KnowledgeType.VERSION_SPECIFIC_BEHAVIOR,
                KnowledgeType.INSTRUMENTATION_CAVEAT,
            }
        ],
        upstream_dependency_facts=upstream_items_knowledge,
        writer_facts=writer_items_knowledge,
        conflicts=conflicts,
    )
    total = sum(
        len(getattr(context, field))
        for field in (
            "engineer_notes",
            "control_narrative_facts",
            "documentation_facts",
            "upstream_dependency_facts",
            "writer_facts",
            "conflicts",
        )
    )
    context.status = "available" if total else "empty"
    context.confidence_contribution = min(0.2, total * 0.04)
    return context


def _current_state_explanation(
    *,
    target: SignalRef,
    upstream_items: list[EvidenceItem],
    runtime_snapshot: dict[str, Any] | None,
    object_index: dict[str, ControlObject],
) -> CurrentStateExplanation:
    if not runtime_snapshot:
        missing = [target] + [
            _signal_ref(object_index.get(item.target_id), item.target_id)
            for item in upstream_items
        ]
        return CurrentStateExplanation(status="live_data_missing", missing_values=missing)

    target_value = _live_value(target, runtime_snapshot)
    upstream_values = [
        _live_value(_signal_ref(object_index.get(item.target_id), item.target_id), runtime_snapshot)
        for item in upstream_items
    ]
    missing = [
        value
        for value in [target_value, *upstream_values]
        if value.value is None
    ]
    if missing:
        return CurrentStateExplanation(
            status="live_data_missing",
            target_current_value=target_value if target_value.value is not None else None,
            upstream_condition_current_values=[
                value for value in upstream_values if value.value is not None
            ],
            missing_values=[
                SignalRef(id=value.signal_id, name=value.signal_name)
                for value in missing
            ],
        )

    blocking = [value for value in upstream_values if _is_blocking_value(value.value)]
    satisfied = [
        value
        for value in upstream_values
        if not _is_blocking_value(value.value)
    ]
    stale = [value for value in [target_value, *upstream_values] if value.stale]
    return CurrentStateExplanation(
        status="live_data_available",
        target_current_value=target_value,
        upstream_condition_current_values=upstream_values,
        blocking_conditions=blocking,
        satisfied_conditions=satisfied,
        stale_values=stale,
        confidence_contribution=0.18 if not stale else 0.1,
    )


def _evidence_item(
    rel: Relationship,
    object_index: dict[str, ControlObject],
    provenance: Any,
) -> EvidenceItem:
    source = object_index.get(rel.source_id)
    target = object_index.get(rel.target_id)
    meta = rel.platform_specific or {}
    condition_ids = []
    condition_names = []
    if provenance is not None:
        for writer in provenance.writers:
            if writer.relationship_id == rel.id:
                condition_ids = list(writer.condition_tag_ids)
                condition_names = [_name_for(cid, object_index) for cid in condition_ids]
                break
    return EvidenceItem(
        source_id=rel.source_id,
        source_name=source.name if source else None,
        source_type=source.object_type.value if source else None,
        target_id=rel.target_id,
        target_name=target.name if target else None,
        relationship_type=rel.relationship_type.value,
        source_location=rel.source_location,
        instruction_type=str(meta.get("instruction_type")) if meta.get("instruction_type") else None,
        write_behavior=_write_behavior(rel.write_behavior),
        condition_signal_ids=condition_ids,
        condition_signal_names=condition_names,
        confidence=_relationship_score(rel),
        metadata={
            "relationship_id": rel.id,
            "binding_status": meta.get("binding_status"),
            "operand_role": meta.get("operand_role"),
            "logic_expression_resolved": meta.get("logic_expression_resolved"),
        },
    )


def _language_for_item(item: EvidenceItem) -> str:
    source_type = (item.source_type or "").lower()
    location = (item.source_location or "").lower()
    instruction = (item.instruction_type or "").lower()
    if source_type == ControlObjectType.RUNG.value or "rung[" in location:
        return "ladder"
    if "structured" in source_type or "statement" in location or instruction == "st":
        return "structured_text"
    if "sfc" in source_type or "/sfc" in location:
        return "sfc"
    if "aoi" in source_type or "aoi" in location or "parameter_name" in item.metadata:
        return "aoi"
    if source_type in {
        ControlObjectType.FUNCTION_BLOCK.value,
        ControlObjectType.FBD_BLOCK_INSTANCE.value,
        ControlObjectType.FBD_INPUT_PIN.value,
        ControlObjectType.FBD_OUTPUT_PIN.value,
        ControlObjectType.FUNCTION_BLOCK_PIN.value,
    } or "block:" in location:
        return "fbd"
    return "unknown"


def _extract_location_part(source_location: str, key: str) -> str | None:
    match = re.search(rf"{re.escape(key)}:([^/]+)", source_location)
    return match.group(1) if match else None


def _extract_rung(source_location: str) -> int | None:
    match = re.search(r"Rung\[(\d+)\]", source_location)
    return int(match.group(1)) if match else None


def _scope_from_location(source_location: str | None) -> dict[str, str | None]:
    source_location = source_location or ""
    return {
        "controller": _extract_location_part(source_location, "Controller"),
        "program": _extract_location_part(source_location, "Program"),
    }


def _rank_knowledge(items: list[KnowledgeItem]) -> list[KnowledgeItem]:
    return sorted(items, key=knowledge_rank_score, reverse=True)


def _live_value(signal: SignalRef, runtime_snapshot: dict[str, Any]) -> LiveValue:
    raw = (
        runtime_snapshot.get(signal.id)
        or (runtime_snapshot.get(signal.name) if signal.name else None)
    )
    if isinstance(raw, dict):
        return LiveValue(
            signal_id=signal.id,
            signal_name=signal.name,
            value=raw.get("value"),
            timestamp=raw.get("timestamp") or raw.get("ts"),
            stale=bool(raw.get("stale", False)),
        )
    return LiveValue(signal_id=signal.id, signal_name=signal.name, value=raw)


def _is_blocking_value(value: Any) -> bool:
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in {"false", "0", "off", "no", "bad"}
    return value is None


def _relationship_score(rel: Relationship) -> float:
    if rel.confidence == ConfidenceLevel.VERY_HIGH:
        return 0.98
    if rel.confidence == ConfidenceLevel.HIGH:
        return 0.92
    if rel.confidence == ConfidenceLevel.MEDIUM:
        return 0.76
    if rel.confidence == ConfidenceLevel.LOW:
        return 0.48
    if rel.confidence == ConfidenceLevel.VERY_LOW:
        return 0.24
    return 0.6


def _write_behavior(behavior: WriteBehaviorType | str | None) -> str | None:
    if behavior is None:
        return None
    return behavior.value if hasattr(behavior, "value") else str(behavior)


def _signal_ref(obj: ControlObject | None, fallback_id: str) -> SignalRef:
    return SignalRef(
        id=obj.id if obj else fallback_id,
        name=obj.name if obj else None,
        object_type=obj.object_type.value if obj else None,
        source_location=obj.source_location if obj else None,
    )


def _name_for(object_id: str, object_index: dict[str, ControlObject]) -> str:
    obj = object_index.get(object_id)
    return (obj.name if obj else None) or object_id


def _confidence_summary(
    *,
    interpretation: QuestionInterpretation,
    writer_items: list[EvidenceItem],
    downstream_items: list[EvidenceItem],
    unknown_items: list[EvidenceItem],
    target: ControlObject | None,
) -> ConfidenceSummary:
    score = interpretation.confidence
    evidence = ["normalized_plc_logic"] if target else []
    missing = ["live_tag_state", "historical_occurrence", "updated_narrative"]
    warnings: list[str] = []
    if not writer_items:
        score = min(score, 0.58)
        warnings.append("No deterministic writer was found for this signal.")
    if unknown_items:
        score = min(score, 0.74)
        warnings.append("Direction-unknown block references exist and were not treated as causes.")
    if downstream_items:
        evidence.append("downstream_readers")
    if writer_items:
        evidence.append("writer_relationships")
    return ConfidenceSummary(
        confidence=round(max(0.05, min(0.98, score)), 3),
        evidence=evidence,
        missing_evidence=missing,
        warnings=warnings,
    )


def _explanation(
    *,
    target_name: str,
    writer_count: int,
    upstream_count: int,
    downstream_count: int,
    unknown_count: int,
) -> str:
    if writer_count == 0:
        return f"I found {target_name}, but no deterministic writer for it in the normalized model."
    text = (
        f"{target_name} is written in {writer_count} location"
        f"{'' if writer_count == 1 else 's'}."
    )
    if upstream_count:
        text += f" {upstream_count} upstream signal{'s' if upstream_count != 1 else ''} may gate those writes."
    if downstream_count:
        text += f" It is read downstream in {downstream_count} location{'s' if downstream_count != 1 else ''}."
    if unknown_count:
        text += " Direction-unknown references are shown separately and are not treated as causes."
    return text


__all__ = [
    "QuestionInterpretation",
    "SignalTroubleshootingWorkspace",
    "build_signal_workspace",
    "interpret_question",
]
