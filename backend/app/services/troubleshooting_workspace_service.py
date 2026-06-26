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
from app.services.logic_slice_service import (
    DERIVED_CALCULATION_INSTRUCTIONS,
    LogicPath,
    LogicPathSignalRef,
    LogicPathWrite,
    LogicSlice,
    build_program_slices,
    logic_paths_from_slices,
    slices_for_target,
    summary_for_target_slices,
)
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

BOOLEAN_CONDITION_INSTRUCTIONS: frozenset[str] = frozenset(
    {"XIC", "XIO", "ONS", "OSR", "OSF", "EQU", "NEQ", "GRT", "GEQ", "LES", "LEQ", "LIM", "CMP"}
)
DATA_SOURCE_INSTRUCTIONS: frozenset[str] = frozenset(
    {"SIZE", "MOV", "MOVE", "COP", "CPS", "FLL", "ADD", "SUB", "MUL", "DIV", "MOD", "CPT", "AND", "OR", "XOR", "TON", "TONR", "TOF", "RTO", "CTU", "CTD", "CTUD"}
)
DERIVED_CALCULATION_INSTRUCTIONS: frozenset[str] = frozenset(
    {"SIZE", "ADD", "SUB", "MUL", "DIV", "MOD", "CPT", "AND", "OR", "XOR"}
)

OperandSemanticRole = Literal[
    "boolean_condition_read",
    "data_source_read",
    "write_target",
    "index_or_reference_operand",
    "derived_calculation",
    "unknown",
]


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
    logic_slices: list[LogicSlice] = Field(default_factory=list)
    logic_paths: list[LogicPath] = Field(default_factory=list)
    upstream_required_conditions: list[EvidenceItem] = Field(default_factory=list)
    upstream_dependencies: list[EvidenceItem] = Field(default_factory=list)
    writer_conditions: list[EvidenceItem] = Field(default_factory=list)
    logic_line_groups: list["LogicLineGroup"] = Field(default_factory=list)
    data_source_reads: list[EvidenceItem] = Field(default_factory=list)
    write_operations: list[EvidenceItem] = Field(default_factory=list)
    derived_calculations: list[EvidenceItem] = Field(default_factory=list)
    derived_explanation: str | None = None
    unknown_direction_references: list[EvidenceItem] = Field(default_factory=list)


class LogicLineCondition(BaseModel):
    signal_id: str
    signal_name: str | None = None
    instruction_type: str | None = None
    required_value: bool | None = None
    relationship_id: str | None = None


class LogicLineGroup(BaseModel):
    """One writer line (rung, ST statement, FBD output, …) and its gating tags."""

    writer_relationship_id: str
    source_id: str
    source_name: str | None = None
    source_type: str | None = None
    language: str = "unknown"
    source_location: str | None = None
    routine: str | None = None
    rung_number: int | None = None
    statement_index: int | None = None
    instruction_type: str | None = None
    write_behavior: str | None = None
    logic_text: str | None = None
    output_summary: str | None = None
    conditions: list[LogicLineCondition] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


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
    quality: str | None = None
    source: str | None = None
    tag_id: str | None = None
    canonical_name: str | None = None
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


def collect_workspace_signal_refs(
    *,
    target_id: str,
    upstream_items: list[EvidenceItem],
    object_index: dict[str, ControlObject],
) -> list[SignalRef]:
    """Signals whose live values matter for current-state explanation."""
    refs: list[SignalRef] = []
    seen: set[str] = set()

    def add(obj_id: str) -> None:
        if obj_id in seen:
            return
        seen.add(obj_id)
        refs.append(_signal_ref(object_index.get(obj_id), obj_id))

    add(target_id)
    for item in upstream_items:
        add(item.target_id)
    return refs


def build_signal_workspace(
    *,
    question: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    runtime_snapshot: dict[str, Any] | None = None,
    use_live_data: bool = False,
    db: Any | None = None,
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

    program_slices = build_program_slices(control_objects, relationships)
    target_slices = slices_for_target(program_slices, target_id)
    logic_paths = logic_paths_from_slices(program_slices, target_id)
    slice_projection = _project_evidence_from_slices(
        target_slices,
        target_id,
        object_index,
    )
    upstream_items = slice_projection["upstream_required_conditions"]
    data_source_items = slice_projection["data_source_reads"]
    writer_items = slice_projection["write_operations"] or writer_items
    derived_items = slice_projection["derived_calculations"]

    unified = get_signal_evidence(target_id, control_objects, relationships)
    logic_line_groups = _logic_line_groups_for_target(
        writer_rels,
        upstream_items,
        writer_items,
        object_index,
    )
    target_name = _name_for(target_id, object_index)
    explanation = unified.summary.answer
    if target_slices:
        explanation = summary_for_target_slices(
            target_name=target_name,
            slices=target_slices,
            reader_count=len(downstream_items),
            unknown_count=len(unknown_items),
        )
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
    live_data_meta: dict[str, Any] = {"enabled": False}
    if runtime_snapshot is None and use_live_data and db is not None:
        from app.services.live_snapshot_service import build_live_runtime_snapshot

        signal_refs = collect_workspace_signal_refs(
            target_id=target_id,
            upstream_items=upstream_items,
            object_index=object_index,
        )
        runtime_snapshot, live_data_meta = build_live_runtime_snapshot(db, signal_refs)
    elif use_live_data and db is None:
        live_data_meta = {
            "enabled": True,
            "source": "tag_registry_influx",
            "error": "database_unavailable",
        }
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
            logic_slices=target_slices,
            upstream_required_conditions=upstream_items,
            upstream_dependencies=_dependency_items_for_target(
                target_id, relationships, object_index
            ),
            writer_conditions=upstream_items,
            logic_paths=logic_paths,
            logic_line_groups=logic_line_groups,
            data_source_reads=data_source_items,
            write_operations=writer_items,
            derived_calculations=derived_items,
            derived_explanation=_derived_explanation_from_slices(
                target_name=target_name,
                target_slices=target_slices,
            ),
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
            "live_data": live_data_meta,
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


def _logic_line_groups_for_target(
    writer_rels: list[Relationship],
    upstream_items: list[EvidenceItem],
    writer_items: list[EvidenceItem],
    object_index: dict[str, ControlObject],
) -> list[LogicLineGroup]:
    upstream_by_writer: dict[str, list[EvidenceItem]] = {}
    for item in upstream_items:
        writer_id = str(item.metadata.get("writer_relationship_id") or "")
        if writer_id:
            upstream_by_writer.setdefault(writer_id, []).append(item)

    writer_item_by_id = {
        str(item.metadata.get("relationship_id")): item
        for item in writer_items
        if item.metadata.get("relationship_id")
    }

    groups: list[LogicLineGroup] = []
    for writer in writer_rels:
        writer_id = writer.id
        condition_items = upstream_by_writer.get(writer_id, [])
        writer_item = writer_item_by_id.get(writer_id)
        source = object_index.get(writer.source_id)
        conditions = [
            LogicLineCondition(
                signal_id=item.target_id,
                signal_name=item.target_name,
                instruction_type=item.instruction_type,
                required_value=_required_value_for_instruction(item.instruction_type),
                relationship_id=str(item.metadata.get("relationship_id"))
                if item.metadata.get("relationship_id")
                else None,
            )
            for item in sorted(
                condition_items,
                key=lambda row: (
                    row.metadata.get("operand_index")
                    if row.metadata.get("operand_index") is not None
                    else 999,
                    row.target_name or row.target_id,
                ),
            )
        ]
        logic_text = writer.logic_condition
        if not logic_text and conditions:
            logic_text = _synthesize_logic_line(conditions, writer_item)
        language = _language_for_item(writer_item) if writer_item else "unknown"
        location = writer.source_location or (
            writer_item.source_location if writer_item else None
        )
        groups.append(
            LogicLineGroup(
                writer_relationship_id=writer_id,
                source_id=writer.source_id,
                source_name=(
                    source.name
                    if source
                    else (writer_item.source_name if writer_item else None)
                ),
                source_type=(
                    source.object_type.value
                    if source
                    else (writer_item.source_type if writer_item else None)
                ),
                language=language,
                source_location=location,
                routine=_extract_location_part(location or "", "Routine"),
                rung_number=_extract_rung(location or ""),
                statement_index=_extract_statement_index(location or ""),
                instruction_type=(
                    writer_item.instruction_type
                    if writer_item
                    else str((writer.platform_specific or {}).get("instruction_type") or "")
                    or None
                ),
                write_behavior=(
                    writer_item.write_behavior
                    if writer_item
                    else _write_behavior(writer.write_behavior)
                ),
                logic_text=logic_text,
                output_summary=_output_summary_for_writer(writer, writer_item, object_index),
                conditions=conditions,
                confidence=_logic_line_confidence(writer, writer_item, condition_items),
            )
        )
    return groups


def _dedupe_evidence_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[tuple[str, str, str | None]] = set()
    out: list[EvidenceItem] = []
    for item in items:
        key = (
            item.target_id,
            str(item.metadata.get("relationship_id") or ""),
            item.instruction_type,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _project_evidence_from_slices(
    target_slices: list[LogicSlice],
    target_id: str,
    object_index: dict[str, ControlObject],
) -> dict[str, list[EvidenceItem]]:
    """Project legacy evidence buckets from canonical slices."""

    upstream: list[EvidenceItem] = []
    data_reads: list[EvidenceItem] = []
    writes: list[EvidenceItem] = []
    derived: list[EvidenceItem] = []
    writer_ids = {
        wid
        for slice_obj in target_slices
        for wid in slice_obj.metadata.get("writer_relationship_ids", [])
    }

    for slice_obj in target_slices:
        source_id = slice_obj.source_id
        for operand in slice_obj.gate_operands:
            upstream.append(
                _operand_to_evidence_item(
                    operand,
                    source_id=source_id,
                    slice_obj=slice_obj,
                    object_index=object_index,
                    writer_relationship_id=_writer_id_for_operand(
                        slice_obj, operand.relationship_id
                    ),
                )
            )
        for operand in slice_obj.data_operands:
            data_reads.append(
                _operand_to_evidence_item(
                    operand,
                    source_id=source_id,
                    slice_obj=slice_obj,
                    object_index=object_index,
                    writer_relationship_id=_writer_id_for_operand(
                        slice_obj, operand.relationship_id
                    ),
                    semantic_role=(
                        "data_source_read"
                        if operand.role != "accumulator"
                        else "derived_calculation"
                    ),
                )
            )
        for write in slice_obj.writes:
            if write.signal_id != target_id:
                continue
            item = _write_to_evidence_item(write, slice_obj, object_index)
            writes.append(item)
            if (write.instruction_type or "").upper() in DERIVED_CALCULATION_INSTRUCTIONS:
                derived.append(item)

    return {
        "upstream_required_conditions": _dedupe_evidence_items(upstream),
        "data_source_reads": _dedupe_evidence_items(data_reads),
        "write_operations": _dedupe_evidence_items(writes),
        "derived_calculations": _dedupe_evidence_items(derived),
    }


def _writer_id_for_operand(
    slice_obj: LogicSlice,
    operand_relationship_id: str | None,
) -> str | None:
    writer_ids = slice_obj.metadata.get("writer_relationship_ids") or []
    return writer_ids[0] if writer_ids else None


def _operand_to_evidence_item(
    operand: Any,
    *,
    source_id: str,
    slice_obj: LogicSlice,
    object_index: dict[str, ControlObject],
    writer_relationship_id: str | None,
    semantic_role: str = "boolean_condition_read",
) -> EvidenceItem:
    source = object_index.get(source_id)
    return EvidenceItem(
        source_id=source_id,
        source_name=slice_obj.source_name or (source.name if source else None),
        source_type=slice_obj.source_type or (source.object_type.value if source else None),
        target_id=operand.signal_id,
        target_name=operand.signal_name,
        relationship_type=RelationshipType.READS.value,
        source_location=slice_obj.source_location,
        instruction_type=operand.instruction_type,
        confidence=slice_obj.confidence,
        metadata={
            "relationship_id": operand.relationship_id,
            "writer_relationship_id": writer_relationship_id,
            "operand_semantic_role": semantic_role,
            "operand_role": operand.role,
            "operand_index": operand.operand_index,
            "slice_id": slice_obj.id,
        },
    )


def _write_to_evidence_item(
    write: Any,
    slice_obj: LogicSlice,
    object_index: dict[str, ControlObject],
) -> EvidenceItem:
    source = object_index.get(slice_obj.source_id)
    return EvidenceItem(
        source_id=slice_obj.source_id,
        source_name=slice_obj.source_name or (source.name if source else None),
        source_type=slice_obj.source_type or (source.object_type.value if source else None),
        target_id=write.signal_id,
        target_name=write.signal_name,
        relationship_type=RelationshipType.WRITES.value,
        source_location=slice_obj.source_location,
        instruction_type=write.instruction_type,
        write_behavior=write.write_behavior,
        confidence=slice_obj.confidence,
        metadata={
            "relationship_id": write.relationship_id,
            "writer_relationship_id": write.relationship_id,
            "operand_semantic_role": (
                "derived_calculation"
                if (write.instruction_type or "").upper() in DERIVED_CALCULATION_INSTRUCTIONS
                else "write_target"
            ),
            "operand_index": write.operand_index,
            "slice_id": slice_obj.id,
        },
    )


def _derived_explanation_from_slices(
    *,
    target_name: str,
    target_slices: list[LogicSlice],
) -> str | None:
    for slice_obj in target_slices:
        instructions = [i.upper() for i in slice_obj.instruction_sequence]
        external = [
            o.signal_name or o.signal_id
            for o in slice_obj.data_operands
            if o.role != "accumulator"
        ]
        if "SIZE" in instructions and "SUB" in instructions and external:
            return (
                f"{target_name} is calculated from {external[0]}. SIZE gets the array length, "
                f"then SUB subtracts 1, making {target_name} the highest valid zero-based index."
            )
    summaries = [
        s.readable_summary or s.readable_expression
        for s in target_slices
        if s.readable_summary or s.readable_expression
    ]
    if not summaries:
        return None
    if len(summaries) == 1:
        return summaries[0]
    return f"{target_name}: " + " ".join(summaries[:3])


def _logic_paths_for_target(
    target_id: str,
    writer_rels: list[Relationship],
    upstream_items: list[EvidenceItem],
    data_source_items: list[EvidenceItem],
    writer_items: list[EvidenceItem],
    object_index: dict[str, ControlObject],
) -> list[LogicPath]:
    upstream_by_writer: dict[str, list[EvidenceItem]] = {}
    for item in upstream_items:
        writer_id = str(item.metadata.get("writer_relationship_id") or "")
        if writer_id:
            upstream_by_writer.setdefault(writer_id, []).append(item)

    data_by_writer: dict[str, list[EvidenceItem]] = {}
    for item in data_source_items:
        writer_id = str(item.metadata.get("writer_relationship_id") or "")
        if writer_id:
            data_by_writer.setdefault(writer_id, []).append(item)

    writer_item_by_id = {
        str(item.metadata.get("relationship_id")): item
        for item in writer_items
        if item.metadata.get("relationship_id")
    }

    writers_by_source: dict[str, list[Relationship]] = {}
    for writer in writer_rels:
        writers_by_source.setdefault(writer.source_id, []).append(writer)

    paths: list[LogicPath] = []
    for source_id, source_writers in writers_by_source.items():
        writer_ids = {writer.id for writer in source_writers}
        bool_conditions: list[EvidenceItem] = []
        data_reads: list[EvidenceItem] = []
        for writer_id in writer_ids:
            bool_conditions.extend(upstream_by_writer.get(writer_id, []))
            data_reads.extend(data_by_writer.get(writer_id, []))

        bool_conditions = _dedupe_evidence_items(bool_conditions)
        data_reads = _dedupe_evidence_items(data_reads)

        writer_evidence = [
            writer_item_by_id[writer.id]
            for writer in source_writers
            if writer.id in writer_item_by_id
        ]
        sample_writer = source_writers[0]
        sample_item = writer_evidence[0] if writer_evidence else None
        source = object_index.get(source_id)
        location = (
            sample_writer.source_location
            or (sample_item.source_location if sample_item else None)
            or (source.source_location if source else None)
        )
        language = _language_for_item(sample_item) if sample_item else "unknown"
        instructions = _ordered_instruction_sequence(source_writers, writer_evidence)
        path_kind = _logic_path_kind(bool_conditions, data_reads, instructions)

        readable_expression: str | None = None
        warnings: list[str] = []
        if path_kind == "calculation":
            readable_expression = _build_calculation_expression(
                data_reads, writer_evidence, instructions
            )
        elif language == "structured_text":
            readable_expression, st_warnings = _build_st_expression(
                source_writers, writer_evidence, bool_conditions, data_reads
            )
            warnings.extend(st_warnings)
        elif language in {"fbd", "aoi"}:
            readable_expression, fbd_warnings = _build_fbd_expression(
                source, location, writer_evidence, bool_conditions
            )
            warnings.extend(fbd_warnings)
        else:
            readable_expression = _build_boolean_expression(
                bool_conditions, writer_evidence, sample_writer
            )

        if not readable_expression and writer_evidence:
            readable_expression = _build_direct_write_expression(writer_evidence)
            if not bool_conditions and not data_reads:
                warnings.append("Write path reconstructed without upstream operands.")

        input_signals = _logic_path_input_signals(bool_conditions, data_reads)
        write_ops = _logic_path_writes(source_writers, writer_evidence, object_index)
        output_signals = [
            LogicPathSignalRef(
                signal_id=write.signal_id,
                signal_name=write.signal_name,
                instruction_type=write.instruction_type,
                relationship_id=write.relationship_id,
            )
            for write in write_ops
        ]

        scores = [_relationship_score(writer) for writer in source_writers]
        scores.extend(item.confidence for item in writer_evidence)
        scores.extend(item.confidence for item in bool_conditions)
        scores.extend(item.confidence for item in data_reads)
        confidence = round(min(scores), 3) if scores else 0.0

        paths.append(
            LogicPath(
                id=f"path::{source_id}::{target_id}",
                source_location=location,
                routine=_extract_location_part(location or "", "Routine"),
                rung_number=_extract_rung(location or ""),
                statement_index=_extract_statement_index(location or ""),
                block_name=_extract_location_part(location or "", "Block")
                or (source.name if source else None),
                language=language,
                instruction_sequence=instructions,
                readable_expression=readable_expression,
                input_signals=input_signals,
                output_signals=output_signals,
                write_operations=write_ops,
                confidence=confidence,
                warnings=warnings,
                metadata={
                    "path_kind": path_kind,
                    "source_id": source_id,
                    "writer_relationship_ids": sorted(writer_ids),
                },
            )
        )
    return paths


def _dedupe_evidence_items(items: list[EvidenceItem]) -> list[EvidenceItem]:
    seen: set[tuple[str, str, str | None]] = set()
    out: list[EvidenceItem] = []
    for item in items:
        key = (
            item.target_id,
            str(item.metadata.get("relationship_id") or ""),
            item.instruction_type,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _ordered_instruction_sequence(
    writers: list[Relationship],
    writer_evidence: list[EvidenceItem],
) -> list[str]:
    ordered: list[tuple[int, str]] = []
    for writer in writers:
        meta = writer.platform_specific or {}
        instruction = str(meta.get("instruction_type") or "").upper()
        if not instruction:
            continue
        operand_index = meta.get("operand_index")
        sort_key = int(operand_index) if operand_index is not None else len(ordered)
        ordered.append((sort_key, instruction))
    if not ordered:
        for item in writer_evidence:
            instruction = (item.instruction_type or "").upper()
            if instruction:
                ordered.append((len(ordered), instruction))
    seen: set[str] = set()
    sequence: list[str] = []
    for _, instruction in sorted(ordered, key=lambda row: row[0]):
        if instruction in seen:
            continue
        seen.add(instruction)
        sequence.append(instruction)
    return sequence


def _logic_path_kind(
    bool_conditions: list[EvidenceItem],
    data_reads: list[EvidenceItem],
    instructions: list[str],
) -> str:
    has_calc = any(inst in DERIVED_CALCULATION_INSTRUCTIONS for inst in instructions)
    if has_calc or (data_reads and not bool_conditions):
        return "calculation"
    if bool_conditions:
        return "boolean_control"
    return "direct_write"


def _condition_to_readable(item: EvidenceItem) -> str:
    name = item.target_name or item.target_id
    itype = (item.instruction_type or "XIC").upper()
    resolved = item.metadata.get("logic_expression_resolved")
    if resolved:
        return str(resolved)
    if itype == "XIO":
        return f"NOT {name}"
    if itype == "XIC":
        return name
    if itype in {"EQU", "NEQ", "GRT", "GEQ", "LES", "LEQ", "LIM"}:
        return f"{itype}({name})"
    return f"{itype}({name})"


def _write_clause(item: EvidenceItem) -> str:
    itype = (item.instruction_type or "").upper()
    target = item.target_name or item.target_id
    behavior = (item.write_behavior or "").lower()
    if itype == "OTE" or behavior == "sets_true":
        return f"{target} = TRUE"
    if itype == "OTL" or behavior == "latches":
        return f"LATCH {target}"
    if itype == "OTU" or behavior == "unlatches":
        return f"UNLATCH {target}"
    if itype in DERIVED_CALCULATION_INSTRUCTIONS:
        return f"{target} := ..."
    return f"{itype}({target})"


def _build_boolean_expression(
    bool_conditions: list[EvidenceItem],
    writer_evidence: list[EvidenceItem],
    sample_writer: Relationship,
) -> str | None:
    if not bool_conditions and not writer_evidence:
        return None
    gate_parts = [
        _condition_to_readable(item)
        for item in sorted(
            bool_conditions,
            key=lambda row: (
                row.metadata.get("operand_index")
                if row.metadata.get("operand_index") is not None
                else 999,
                row.target_name or row.target_id,
            ),
        )
    ]
    gate = " AND ".join(gate_parts) if gate_parts else ""
    write_parts = [_write_clause(item) for item in writer_evidence]
    write_text = ", ".join(write_parts) if write_parts else ""
    if gate and write_text:
        return f"IF {gate} THEN {write_text}"
    if write_text:
        return write_text
    return gate or None


def _build_calculation_expression(
    data_reads: list[EvidenceItem],
    writer_evidence: list[EvidenceItem],
    instructions: list[str],
) -> str | None:
    if not writer_evidence:
        return None
    target = writer_evidence[-1].target_name or writer_evidence[-1].target_id
    source_names = [item.target_name or item.target_id for item in data_reads]
    upper = [inst.upper() for inst in instructions]
    if "SIZE" in upper and "SUB" in upper and source_names:
        return f"{target} = SIZE({source_names[0]}) - 1"
    if "SIZE" in upper and source_names:
        return f"{target} = SIZE({source_names[0]})"
    if "MOV" in upper or "MOVE" in upper:
        if source_names:
            return f"{target} = {source_names[0]}"
        return f"{target} = MOV(...)"
    if "ADD" in upper and source_names:
        return f"{target} = {source_names[0]} + ..."
    if "SUB" in upper and source_names:
        return f"{target} = {source_names[0]} - ..."
    joined = ", ".join(upper) if upper else "CALC"
    sources = ", ".join(source_names) if source_names else "..."
    return f"{target} = {joined}({sources})"


def _build_st_expression(
    writers: list[Relationship],
    writer_evidence: list[EvidenceItem],
    bool_conditions: list[EvidenceItem],
    data_reads: list[EvidenceItem],
) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    for writer in writers:
        meta = writer.platform_specific or {}
        statement_text = meta.get("statement_text") or meta.get("raw_text")
        if statement_text:
            return str(statement_text).strip(), warnings
        if writer.logic_condition:
            return writer.logic_condition.strip(), warnings
    reconstructed = _build_boolean_expression(bool_conditions, writer_evidence, writers[0])
    if reconstructed:
        warnings.append("ST statement text unavailable; reconstructed from reads and writes.")
        return reconstructed, warnings
    if data_reads or writer_evidence:
        warnings.append("ST statement text unavailable; partial reconstruction only.")
    return reconstructed, warnings


def _build_fbd_expression(
    source: ControlObject | None,
    location: str | None,
    writer_evidence: list[EvidenceItem],
    bool_conditions: list[EvidenceItem],
) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    block_name = _extract_location_part(location or "", "Block") or (
        source.name if source else None
    )
    pin = _extract_location_part(location or "", "Pin")
    write_text = ", ".join(_write_clause(item) for item in writer_evidence)
    if bool_conditions:
        gate = " AND ".join(_condition_to_readable(item) for item in bool_conditions)
        expression = f"IF {gate} THEN {write_text}" if write_text else gate
    else:
        expression = write_text or None
    if block_name:
        prefix = f"{block_name}"
        if pin:
            prefix = f"{block_name}.{pin}"
        expression = f"{prefix}: {expression}" if expression else prefix
    if not pin and writer_evidence:
        warnings.append("FBD/AOI pin direction inferred from write relationship only.")
    return expression, warnings


def _build_direct_write_expression(writer_evidence: list[EvidenceItem]) -> str | None:
    parts = [_write_clause(item) for item in writer_evidence]
    return ", ".join(parts) if parts else None


def _logic_path_input_signals(
    bool_conditions: list[EvidenceItem],
    data_reads: list[EvidenceItem],
) -> list[LogicPathSignalRef]:
    refs: list[LogicPathSignalRef] = []
    seen: set[str] = set()
    for item in [*bool_conditions, *data_reads]:
        if item.target_id in seen:
            continue
        seen.add(item.target_id)
        refs.append(
            LogicPathSignalRef(
                signal_id=item.target_id,
                signal_name=item.target_name,
                instruction_type=item.instruction_type,
                relationship_id=str(item.metadata.get("relationship_id"))
                if item.metadata.get("relationship_id")
                else None,
            )
        )
    return refs


def _logic_path_writes(
    writers: list[Relationship],
    writer_evidence: list[EvidenceItem],
    object_index: dict[str, ControlObject],
) -> list[LogicPathWrite]:
    writer_item_by_id = {
        str(item.metadata.get("relationship_id")): item
        for item in writer_evidence
        if item.metadata.get("relationship_id")
    }
    writes: list[LogicPathWrite] = []
    seen: set[str] = set()
    for writer in writers:
        if writer.id in seen:
            continue
        seen.add(writer.id)
        item = writer_item_by_id.get(writer.id)
        if item:
            writes.append(
                LogicPathWrite(
                    signal_id=item.target_id,
                    signal_name=item.target_name,
                    instruction_type=item.instruction_type,
                    write_behavior=item.write_behavior,
                    relationship_id=writer.id,
                )
            )
            continue
        writes.append(
            LogicPathWrite(
                signal_id=writer.target_id,
                signal_name=_name_for(writer.target_id, object_index),
                instruction_type=str((writer.platform_specific or {}).get("instruction_type") or "")
                or None,
                write_behavior=_write_behavior(writer.write_behavior),
                relationship_id=writer.id,
            )
        )
    return writes


def _explanation_from_logic_paths(
    *,
    target_name: str,
    logic_paths: list[LogicPath],
    reader_count: int,
    unknown_count: int,
) -> str:
    path_count = len(logic_paths)
    calc_count = sum(
        1 for path in logic_paths if path.metadata.get("path_kind") == "calculation"
    )
    bool_count = path_count - calc_count
    if calc_count and not bool_count:
        text = (
            f"{target_name} is written by {path_count} calculation logic path"
            f"{'' if path_count == 1 else 's'}."
        )
    elif bool_count and not calc_count:
        text = (
            f"{target_name} is written by {path_count} logic path"
            f"{'' if path_count == 1 else 's'}."
        )
    else:
        text = (
            f"{target_name} is written by {path_count} logic path"
            f"{'' if path_count == 1 else 's'} "
            f"({calc_count} calculation, {bool_count} control)."
        )
    if reader_count:
        text += (
            f" It is used downstream in {reader_count} location"
            f"{'' if reader_count == 1 else 's'}."
        )
    if unknown_count:
        text += (
            f" {unknown_count} unknown-direction reference"
            f"{'s' if unknown_count != 1 else ''} were preserved separately."
        )
    return text


def _required_value_for_instruction(instruction_type: str | None) -> bool | None:
    upper = (instruction_type or "").upper()
    if upper == "XIC":
        return True
    if upper == "XIO":
        return False
    return None


def _synthesize_logic_line(
    conditions: list[LogicLineCondition],
    writer_item: EvidenceItem | None,
) -> str:
    parts: list[str] = []
    for condition in conditions:
        name = condition.signal_name or condition.signal_id
        itype = (condition.instruction_type or "XIC").upper()
        if itype == "XIO" or condition.required_value is False:
            parts.append(f"NOT {name}")
        else:
            parts.append(f"{itype}({name})")
    gate = " AND ".join(parts) if parts else "TRUE"
    if writer_item:
        output = writer_item.instruction_type or "OUT"
        target = writer_item.target_name or writer_item.target_id
        return f"{gate} → {output}({target})"
    return gate


def _output_summary_for_writer(
    writer: Relationship,
    writer_item: EvidenceItem | None,
    object_index: dict[str, ControlObject],
) -> str | None:
    target = object_index.get(writer.target_id)
    target_name = (
        target.name if target else (writer_item.target_name if writer_item else writer.target_id)
    )
    itype = (
        writer_item.instruction_type
        if writer_item
        else str((writer.platform_specific or {}).get("instruction_type") or "")
    )
    behavior = (
        writer_item.write_behavior
        if writer_item
        else _write_behavior(writer.write_behavior)
    )
    if itype and target_name:
        return f"{itype} writes {target_name}" + (f" ({behavior})" if behavior else "")
    return None


def _logic_line_confidence(
    writer: Relationship,
    writer_item: EvidenceItem | None,
    condition_items: list[EvidenceItem],
) -> float:
    scores = [_relationship_score(writer)]
    if writer_item:
        scores.append(writer_item.confidence)
    scores.extend(item.confidence for item in condition_items)
    return round(min(scores), 3) if scores else 0.0


def _extract_statement_index(source_location: str) -> int | None:
    match = re.search(r"Statement[:[](\d+)", source_location)
    return int(match.group(1)) if match else None


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
            if _operand_semantic_role(rel) != "boolean_condition_read":
                continue
            key = (writer.id, rel.target_id)
            if key in seen:
                continue
            seen.add(key)
            item = _evidence_item(rel, object_index, None)
            item.metadata["writer_relationship_id"] = writer.id
            out.append(item)
    return out


def _data_source_items_for_target(
    target_id: str,
    writer_rels: list[Relationship],
    relationships: list[Relationship],
    object_index: dict[str, ControlObject],
) -> list[EvidenceItem]:
    by_source: dict[str, list[Relationship]] = {}
    for rel in relationships:
        by_source.setdefault(rel.source_id, []).append(rel)
    out: list[EvidenceItem] = []
    seen: set[tuple[str, str, str | None]] = set()
    for writer in writer_rels:
        for rel in by_source.get(writer.source_id, []):
            if rel.relationship_type != RelationshipType.READS:
                continue
            if _operand_semantic_role(rel) != "data_source_read":
                continue
            instruction_type = str((rel.platform_specific or {}).get("instruction_type") or "")
            key = (rel.source_id, rel.target_id, instruction_type.upper())
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
    semantic_role = _operand_semantic_role(rel)
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
            "operand_semantic_role": semantic_role,
            "operand_index": meta.get("operand_index"),
            "logic_expression_resolved": meta.get("logic_expression_resolved"),
        },
    )


def _operand_semantic_role(rel: Relationship) -> OperandSemanticRole:
    meta = rel.platform_specific or {}
    existing = str(meta.get("operand_semantic_role") or "").strip()
    if existing in {
        "boolean_condition_read",
        "data_source_read",
        "write_target",
        "index_or_reference_operand",
        "derived_calculation",
    }:
        return existing  # type: ignore[return-value]

    operand_role = str(meta.get("operand_role") or "").lower()
    instruction = str(meta.get("instruction_type") or "").upper()
    if rel.relationship_type in WRITER_TYPES:
        if instruction in DERIVED_CALCULATION_INSTRUCTIONS:
            return "derived_calculation"
        return "write_target"
    if rel.relationship_type != RelationshipType.READS:
        return "unknown"
    if operand_role in {"index", "dimension", "constant", "indirect_reference"}:
        return "index_or_reference_operand"
    if operand_role in {"move_source", "math_source", "data_source", "source", "array_source"}:
        return "data_source_read"
    if operand_role in {"condition", "contact", "comparison_operand", "gating_operand"}:
        return "boolean_condition_read"
    if instruction in BOOLEAN_CONDITION_INSTRUCTIONS:
        return "boolean_condition_read"
    if instruction in DATA_SOURCE_INSTRUCTIONS:
        return "data_source_read"
    return "unknown"


def _derived_explanation(
    *,
    target_name: str,
    data_sources: list[EvidenceItem],
    derived_writes: list[EvidenceItem],
) -> str | None:
    instructions = [(item.instruction_type or "").upper() for item in derived_writes]
    data_source_name = data_sources[0].target_name if data_sources else None
    if "SIZE" in instructions and "SUB" in instructions and data_source_name:
        return (
            f"{target_name} is calculated from {data_source_name}. SIZE gets the array length, "
            f"then SUB subtracts 1, making {target_name} the highest valid zero-based index."
        )
    if derived_writes and data_source_name:
        ordered = ", ".join(instruction for instruction in instructions if instruction)
        return f"{target_name} is calculated from {data_source_name} using {ordered}."
    if derived_writes:
        ordered = ", ".join(instruction for instruction in instructions if instruction)
        return f"{target_name} is produced by derived calculation instruction{'s' if len(derived_writes) != 1 else ''}: {ordered}."
    return None


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
        quality = raw.get("quality")
        stale = bool(raw.get("stale", False)) or quality == "stale"
        return LiveValue(
            signal_id=signal.id,
            signal_name=signal.name,
            value=raw.get("value"),
            timestamp=raw.get("timestamp") or raw.get("ts"),
            quality=str(quality) if quality is not None else None,
            source=str(raw.get("source")) if raw.get("source") is not None else None,
            tag_id=str(raw.get("tag_id")) if raw.get("tag_id") is not None else None,
            canonical_name=(
                str(raw.get("canonical_name"))
                if raw.get("canonical_name") is not None
                else None
            ),
            stale=stale,
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
