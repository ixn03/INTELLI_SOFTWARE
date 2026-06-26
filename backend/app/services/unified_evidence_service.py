"""Unified evidence aggregation over the normalized control model.

Merges ladder, FBD, ST, AOI, and unknown references into one troubleshooting
graph while preserving engineer-verifiable source provenance. No LLMs.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)
from app.models.unified_evidence import (
    AOIEvidenceGroup,
    ConfidenceSummary,
    EvidenceSourceCounts,
    FBDEvidenceGroup,
    LadderEvidenceGroup,
    OriginatingLanguage,
    OriginatingPlatform,
    RequiredCondition,
    SignalDependency,
    SignalReader,
    SignalWriter,
    SourceProvenance,
    SFCEvidenceGroup,
    STEvidenceGroup,
    UnifiedSignalEvidence,
    UnifiedSignalSummary,
    UnknownDependency,
    UnknownEvidenceGroup,
    VerificationByLanguage,
)
from app.services.dependency_graph_service import (
    ControlDependencyGraph,
    DependencyEdge,
    TagAccess,
    build_control_dependency_graph,
    trace_dependency_chains,
)

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

_ROUTINE_RE = re.compile(r"Routine:([^/\]]+)")
_RUNG_RE = re.compile(r"Rung\[(\d+)\]")
_BLOCK_RE = re.compile(r"Block:([^/\]]+)")
_STATEMENT_RE = re.compile(r"Statement\[(\d+)\]")


def get_signal_evidence(
    signal_id: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> UnifiedSignalEvidence:
    """Build unified troubleshooting evidence for one signal."""

    graph = build_control_dependency_graph(control_objects, relationships)
    return _build_unified_evidence(signal_id, control_objects, relationships, graph)


def trace_upstream(
    signal_id: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    *,
    max_depth: int = 6,
) -> list[SignalDependency]:
    graph = build_control_dependency_graph(control_objects, relationships)
    trace = trace_dependency_chains(graph, signal_id, max_depth=max_depth)
    object_index = {obj.id: obj for obj in control_objects}
    out: list[SignalDependency] = []
    seen: set[str] = set()
    for edge in graph.edges:
        if edge.downstream_tag_id != signal_id:
            continue
        if edge.upstream_tag_id in seen:
            continue
        seen.add(edge.upstream_tag_id)
        rel = _find_relationship(relationships, edge.writer_relationship_id)
        prov = _provenance_from_relationship(rel, object_index) if rel else _empty_provenance()
        out.append(
            SignalDependency(
                upstream_signal_id=edge.upstream_tag_id,
                upstream_signal_name=edge.upstream_tag_name,
                downstream_signal_id=edge.downstream_tag_id,
                downstream_signal_name=edge.downstream_tag_name,
                dependency_kind="required_condition",
                source_provenance=prov,
                confidence=edge.confidence.confidence_score,
                causality=prov.causality,
            )
        )
    if not out and trace.required_condition_tag_ids:
        for cond_id in trace.required_condition_tag_ids:
            out.append(
                SignalDependency(
                    upstream_signal_id=cond_id,
                    upstream_signal_name=graph.tags.get(cond_id),
                    downstream_signal_id=signal_id,
                    downstream_signal_name=graph.tags.get(signal_id),
                    dependency_kind="required_condition",
                    source_provenance=_empty_provenance(),
                    confidence=trace.confidence.confidence_score,
                )
            )
    return out


def trace_downstream(
    signal_id: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> list[SignalReader]:
    return get_readers(signal_id, control_objects, relationships)


def get_writers(
    signal_id: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> list[SignalWriter]:
    evidence = get_signal_evidence(signal_id, control_objects, relationships)
    return evidence.who_writes_this_signal


def get_readers(
    signal_id: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> list[SignalReader]:
    object_index = {obj.id: obj for obj in control_objects}
    reader_rels = [
        rel
        for rel in relationships
        if rel.target_id == signal_id and rel.relationship_type == RelationshipType.READS
    ]
    return [
        _signal_reader_from_rel(rel, object_index, signal_id)
        for rel in reader_rels
    ]


def _build_unified_evidence(
    signal_id: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    graph: ControlDependencyGraph,
) -> UnifiedSignalEvidence:
    object_index = {obj.id: obj for obj in control_objects}
    target = object_index.get(signal_id)
    target_name = (target.name if target else None) or graph.tags.get(signal_id)

    writer_rels = [
        rel
        for rel in relationships
        if rel.target_id == signal_id and rel.relationship_type in WRITER_TYPES
    ]
    reader_rels = [
        rel
        for rel in relationships
        if rel.target_id == signal_id and rel.relationship_type == RelationshipType.READS
    ]
    unknown_rels = [
        rel
        for rel in relationships
        if rel.target_id == signal_id
        and rel.relationship_type == RelationshipType.REFERENCES
        and (rel.platform_specific or {}).get("binding_status") == "direction_unknown"
    ]

    provenance = graph.provenance.get(signal_id)
    writers = [
        _signal_writer_from_rel(rel, object_index, provenance, signal_id, target_name)
        for rel in writer_rels
    ]
    readers = [
        _signal_reader_from_rel(rel, object_index, signal_id) for rel in reader_rels
    ]
    unknowns = [
        _unknown_from_rel(rel, object_index, signal_id, target_name)
        for rel in unknown_rels
    ]
    upstream = trace_upstream(signal_id, control_objects, relationships)
    required_conditions = _required_conditions_from_upstream(
        upstream,
        writer_rels,
        relationships,
        object_index,
    )
    verification = _verification_groups(
        writers=writers,
        readers=readers,
        required_conditions=required_conditions,
        unknowns=unknowns,
        upstream=upstream,
        relationships=relationships,
        object_index=object_index,
    )
    evidence_counts = _count_evidence_sources(verification)
    confidence = _confidence_summary(
        writers=writers,
        readers=readers,
        unknowns=unknowns,
        target=target,
        provenance=provenance,
    )
    summary_text = _summary_answer(
        target_name=target_name or signal_id,
        writer_count=len(writers),
        condition_count=len(required_conditions),
        reader_count=len(readers),
        unknown_count=len(unknowns),
        calculated_writer_count=sum(
            1
            for writer in writers
            if (writer.source_provenance.instruction_type or "").upper()
            in DERIVED_CALCULATION_INSTRUCTIONS
        ),
    )

    return UnifiedSignalEvidence(
        target_signal_id=signal_id,
        target_signal_name=target_name,
        summary=UnifiedSignalSummary(
            answer=summary_text,
            confidence=confidence.confidence_label,
        ),
        what_controls_this_signal=required_conditions,
        who_writes_this_signal=writers,
        where_is_it_used=readers,
        upstream_dependencies=upstream,
        downstream_impact=readers,
        unknowns=unknowns,
        evidence_sources=evidence_counts,
        confidence_summary=confidence,
        verification=verification,
        advanced_details={
            "relationship_ids": [rel.id for rel in writer_rels + reader_rels + unknown_rels],
            "dependency_edge_count": graph.metadata.get("dependency_edge_count", 0),
            "dependency_edge_ids": [edge.writer_relationship_id for edge in graph.edges if edge.downstream_tag_id == signal_id],
            "source_object_ids": list(
                {
                    w.source_provenance.source_object_id
                    for w in writers
                    if w.source_provenance.source_object_id
                }
            ),
        },
    )


def _signal_writer_from_rel(
    rel: Relationship,
    object_index: dict[str, ControlObject],
    provenance: Any,
    signal_id: str,
    signal_name: str | None,
) -> SignalWriter:
    prov = _provenance_from_relationship(rel, object_index)
    writer_type = _writer_type(prov.originating_language, object_index.get(rel.source_id))
    condition_ids: list[str] = []
    condition_names: list[str] = []
    if provenance is not None:
        for writer in provenance.writers:
            if writer.relationship_id == rel.id:
                condition_ids = list(writer.condition_tag_ids)
                condition_names = [
                    (object_index[cid].name if cid in object_index else None) or cid
                    for cid in condition_ids
                ]
                break
    return SignalWriter(
        writer_type=writer_type,
        signal_id=signal_id,
        signal_name=signal_name,
        source_provenance=prov,
        confidence=_relationship_score(rel),
        causality=prov.causality,
        condition_signal_ids=condition_ids,
        condition_signal_names=[n for n in condition_names if n],
        write_behavior=_write_behavior(rel.write_behavior),
    )


def _signal_reader_from_rel(
    rel: Relationship,
    object_index: dict[str, ControlObject],
    signal_id: str,
) -> SignalReader:
    prov = _provenance_from_relationship(rel, object_index)
    reader_type = _reader_type(prov.originating_language, object_index.get(rel.source_id))
    target = object_index.get(signal_id)
    return SignalReader(
        reader_type=reader_type,
        signal_id=signal_id,
        signal_name=target.name if target else None,
        source_provenance=prov,
        confidence=_relationship_score(rel),
        reader_context=prov.routine,
    )


def _unknown_from_rel(
    rel: Relationship,
    object_index: dict[str, ControlObject],
    signal_id: str,
    signal_name: str | None,
) -> UnknownDependency:
    source = object_index.get(rel.source_id)
    prov = _provenance_from_relationship(rel, object_index)
    prov.causality = "direction_unknown"
    return UnknownDependency(
        signal_id=signal_id,
        signal_name=signal_name,
        reference_source_id=rel.source_id,
        reference_source_name=source.name if source else None,
        source_provenance=prov,
        message=(
            "Referenced by unknown-direction block. INTELLI preserved the "
            "relationship but did not infer causality."
        ),
        confidence=_relationship_score(rel),
    )


def _required_conditions_from_upstream(
    upstream: list[SignalDependency],
    writer_rels: list[Relationship],
    relationships: list[Relationship],
    object_index: dict[str, ControlObject],
) -> list[RequiredCondition]:
    by_source: dict[str, list[Relationship]] = {}
    for rel in relationships:
        by_source.setdefault(rel.source_id, []).append(rel)

    out: list[RequiredCondition] = []
    seen: set[str] = set()
    writer_ids = [rel.id for rel in writer_rels]

    for dep in upstream:
        if not _is_boolean_condition_instruction(dep.source_provenance.instruction_type):
            continue
        cond_id = dep.upstream_signal_id
        if cond_id in seen:
            continue
        seen.add(cond_id)
        cond_obj = object_index.get(cond_id)
        prov = dep.source_provenance
        out.append(
            RequiredCondition(
                signal_id=cond_id,
                signal_name=cond_obj.name if cond_obj else dep.upstream_signal_name,
                required_for_writer_ids=writer_ids,
                source_provenance=prov,
                confidence=dep.confidence,
                causality=dep.causality,
            )
        )

    for writer in writer_rels:
        for rel in by_source.get(writer.source_id, []):
            if rel.relationship_type != RelationshipType.READS:
                continue
            if not _is_boolean_condition_read(rel):
                continue
            if rel.target_id in seen or rel.target_id == writer.target_id:
                continue
            seen.add(rel.target_id)
            cond_obj = object_index.get(rel.target_id)
            out.append(
                RequiredCondition(
                    signal_id=rel.target_id,
                    signal_name=cond_obj.name if cond_obj else None,
                    required_for_writer_ids=[writer.id],
                    source_provenance=_provenance_from_relationship(rel, object_index),
                    confidence=_relationship_score(rel),
                )
            )
    return out


def _is_boolean_condition_read(rel: Relationship) -> bool:
    meta = rel.platform_specific or {}
    role = str(meta.get("operand_semantic_role") or meta.get("operand_role") or "").lower()
    if role in {"data_source_read", "data_source", "move_source", "math_source", "source", "array_source"}:
        return False
    if role in {"boolean_condition_read", "condition", "contact", "comparison_operand", "gating_operand"}:
        return True
    return _is_boolean_condition_instruction(
        str(meta.get("instruction_type")) if meta.get("instruction_type") else None
    )


def _is_boolean_condition_instruction(instruction_type: str | None) -> bool:
    if not instruction_type:
        return False
    instruction = instruction_type.upper()
    if instruction in DATA_SOURCE_INSTRUCTIONS:
        return False
    return instruction in BOOLEAN_CONDITION_INSTRUCTIONS


def _verification_groups(
    *,
    writers: list[SignalWriter],
    readers: list[SignalReader],
    required_conditions: list[RequiredCondition],
    unknowns: list[UnknownDependency],
    upstream: list[SignalDependency],
    relationships: list[Relationship],
    object_index: dict[str, ControlObject],
) -> VerificationByLanguage:
    ladder: list[LadderEvidenceGroup] = []
    fbd: list[FBDEvidenceGroup] = []
    aoi: list[AOIEvidenceGroup] = []
    st: list[STEvidenceGroup] = []
    sfc: list[SFCEvidenceGroup] = []
    unknown: list[UnknownEvidenceGroup] = []

    for writer in writers:
        prov = writer.source_provenance
        if prov.originating_language == "ladder":
            ladder.append(
                LadderEvidenceGroup(
                    routine=prov.routine,
                    rung_number=prov.rung_number,
                    instruction_type=prov.instruction_type,
                    source_location=prov.source_location,
                    role="writer",
                    signal_name=writer.signal_name,
                    condition_signal_names=writer.condition_signal_names,
                    relationship_ids=prov.relationship_ids,
                    confidence=writer.confidence,
                )
            )
        elif prov.originating_language == "fbd":
            fbd.append(
                FBDEvidenceGroup(
                    routine=prov.routine,
                    block_name=prov.block_name,
                    block_type=prov.block_type,
                    pin_name=prov.pin_name,
                    pin_direction=prov.pin_direction or "output",
                    role="output",
                    signal_name=writer.signal_name,
                    source_location=prov.source_location,
                    relationship_ids=prov.relationship_ids,
                    confidence=writer.confidence,
                )
            )
        elif prov.originating_language == "structured_text":
            st.append(
                _st_evidence_group(
                    prov=prov,
                    signal_name=writer.signal_name,
                    confidence=writer.confidence,
                    role="assignment",
                )
            )
        elif prov.originating_language == "aoi":
            aoi.append(
                _aoi_evidence_group(
                    prov=prov,
                    signal_name=writer.signal_name,
                    confidence=writer.confidence,
                    role="writer",
                )
            )
        elif prov.originating_language == "sfc":
            sfc.append(
                _sfc_evidence_group(prov, writer.signal_name, writer.confidence, "action")
            )

    for cond in required_conditions:
        prov = cond.source_provenance
        if prov.originating_language == "ladder":
            ladder.append(
                LadderEvidenceGroup(
                    routine=prov.routine,
                    rung_number=prov.rung_number,
                    instruction_type=prov.instruction_type,
                    source_location=prov.source_location,
                    role="condition",
                    signal_name=cond.signal_name,
                    relationship_ids=prov.relationship_ids,
                    confidence=cond.confidence,
                )
            )
        elif prov.originating_language == "fbd":
            fbd.append(
                FBDEvidenceGroup(
                    routine=prov.routine,
                    block_name=prov.block_name,
                    block_type=prov.block_type,
                    pin_name=prov.pin_name,
                    pin_direction=prov.pin_direction or "input",
                    role="input",
                    signal_name=cond.signal_name,
                    source_location=prov.source_location,
                    relationship_ids=prov.relationship_ids,
                    confidence=cond.confidence,
                )
            )
        elif prov.originating_language == "structured_text":
            st.append(
                _st_evidence_group(
                    prov=prov,
                    signal_name=cond.signal_name,
                    confidence=cond.confidence,
                    role="condition",
                )
            )
        elif prov.originating_language == "aoi":
            aoi.append(
                _aoi_evidence_group(
                    prov=prov,
                    signal_name=cond.signal_name,
                    confidence=cond.confidence,
                    role="condition",
                )
            )

    for reader in readers:
        prov = reader.source_provenance
        if prov.originating_language == "ladder":
            ladder.append(
                LadderEvidenceGroup(
                    routine=prov.routine,
                    rung_number=prov.rung_number,
                    instruction_type=prov.instruction_type,
                    source_location=prov.source_location,
                    role="reader",
                    signal_name=reader.signal_name,
                    relationship_ids=prov.relationship_ids,
                    confidence=reader.confidence,
                )
            )
        elif prov.originating_language == "fbd":
            fbd.append(
                FBDEvidenceGroup(
                    routine=prov.routine,
                    block_name=prov.block_name,
                    block_type=prov.block_type,
                    pin_name=prov.pin_name,
                    pin_direction=prov.pin_direction,
                    role="input" if prov.pin_direction == "input" else "output",
                    signal_name=reader.signal_name,
                    source_location=prov.source_location,
                    relationship_ids=prov.relationship_ids,
                    confidence=reader.confidence,
                )
            )
        elif prov.originating_language == "structured_text":
            st.append(
                _st_evidence_group(
                    prov=prov,
                    signal_name=reader.signal_name,
                    confidence=reader.confidence,
                    role="read",
                )
            )
        elif prov.originating_language == "aoi":
            aoi.append(
                _aoi_evidence_group(
                    prov=prov,
                    signal_name=reader.signal_name,
                    confidence=reader.confidence,
                    role="reader",
                )
            )
        elif prov.originating_language == "sfc":
            sfc.append(
                _sfc_evidence_group(prov, reader.signal_name, reader.confidence, "condition")
            )

    for rel in relationships:
        if rel.relationship_type != RelationshipType.SIGNAL_CONNECTS:
            continue
        prov = _provenance_from_relationship(rel, object_index)
        if prov.originating_language != "fbd":
            continue
        source = object_index.get(rel.source_id)
        target = object_index.get(rel.target_id)
        fbd.append(
            FBDEvidenceGroup(
                routine=prov.routine,
                block_name=prov.block_name,
                pin_name=prov.pin_name,
                role="wire",
                signal_name=source.name if source else None,
                connected_signal_name=target.name if target else None,
                source_location=prov.source_location,
                relationship_ids=[rel.id],
                confidence=_relationship_score(rel),
                structural_only=True,
            )
        )

    for unk in unknowns:
        prov = unk.source_provenance
        unknown.append(
            UnknownEvidenceGroup(
                reference_source=unk.reference_source_name,
                signal_name=unk.signal_name,
                source_location=prov.source_location,
                message=unk.message,
                relationship_ids=prov.relationship_ids,
                confidence=unk.confidence,
            )
        )

    return VerificationByLanguage(
        ladder=ladder,
        fbd=fbd,
        aoi=aoi,
        structured_text=st,
        sfc=sfc,
        unknown=unknown,
    )


def _aoi_evidence_group(
    *,
    prov: SourceProvenance,
    signal_name: str | None,
    confidence: float,
    role: str,
) -> AOIEvidenceGroup:
    meta = prov.metadata or {}
    parameter = prov.pin_name or meta.get("parameter_name")
    if role == "writer" and parameter and str(parameter).lower() in {"out", "output"}:
        display_param = str(parameter)
    elif parameter:
        display_param = str(parameter)
    else:
        display_param = None
    instance = meta.get("aoi_instance") or prov.block_name or meta.get("aoi_name")
    return AOIEvidenceGroup(
        aoi_instance=str(instance) if instance else None,
        parameter_name=display_param,
        parameter_direction=prov.pin_direction or meta.get("parameter_usage"),
        signal_name=signal_name,
        source_location=prov.source_location,
        relationship_ids=prov.relationship_ids,
        confidence=confidence,
    )


def _st_evidence_group(
    *,
    prov: SourceProvenance,
    signal_name: str | None,
    confidence: float,
    role: Literal["assignment", "read", "condition"],
) -> STEvidenceGroup:
    meta = prov.metadata or {}
    st_role: Literal["assignment", "read", "condition"] = role
    if meta.get("statement_type") in {"if", "if_elsif_chain"} and role == "condition":
        st_role = "condition"
    return STEvidenceGroup(
        routine=prov.routine,
        statement_index=prov.statement_index,
        role=st_role,
        signal_name=signal_name,
        source_location=prov.source_location,
        relationship_ids=prov.relationship_ids,
        confidence=confidence,
    )


def _sfc_evidence_group(
    prov: SourceProvenance,
    signal_name: str | None,
    confidence: float,
    role: Literal["step", "transition", "action", "condition"],
) -> SFCEvidenceGroup:
    meta = prov.metadata or {}
    subtype = str(meta.get("object_subtype") or "")
    return SFCEvidenceGroup(
        routine=prov.routine,
        step_name=prov.block_name if "step" in subtype else None,
        transition_name=prov.block_name if "transition" in subtype else None,
        role=role,
        signal_name=signal_name,
        source_location=prov.source_location,
        relationship_ids=prov.relationship_ids,
        confidence=confidence,
        structural_only=prov.causality != "deterministic",
    )


def _provenance_from_relationship(
    rel: Relationship,
    object_index: dict[str, ControlObject],
) -> SourceProvenance:
    meta = rel.platform_specific or {}
    source = object_index.get(rel.source_id)
    language = _resolve_language(rel, source, meta)
    platform = _resolve_platform(source, meta)
    location = rel.source_location or (source.source_location if source else None)
    routine, rung, block, statement = _parse_location(location)

    block_name = block
    block_type = None
    pin_name = None
    pin_direction = None
    if source is not None:
        if source.object_type == ControlObjectType.FUNCTION_BLOCK_PIN:
            pin_name = source.name
            pin_direction = str((source.attributes or {}).get("direction") or meta.get("pin_direction") or "")
            parent_block_id = (source.attributes or {}).get("block_id")
            if parent_block_id:
                block_obj = object_index.get(str(parent_block_id))
                if block_obj:
                    block_name = block_obj.name
                    block_type = (block_obj.attributes or {}).get("block_type")
        elif source.object_type == ControlObjectType.FUNCTION_BLOCK:
            block_name = source.name
            block_type = (source.attributes or {}).get("block_type")
        elif source.object_type == ControlObjectType.RUNG:
            if rung is None and source.name:
                m = re.search(r"(\d+)", source.name)
                if m:
                    rung = int(m.group(1))

    causality: str = "deterministic"
    if meta.get("binding_status") == "direction_unknown":
        causality = "direction_unknown"
    elif rel.relationship_type == RelationshipType.SIGNAL_CONNECTS:
        causality = "structural_only"

    return SourceProvenance(
        originating_language=language,
        originating_platform=platform,
        source_location=location,
        routine=routine or (source.name if source and source.object_type == ControlObjectType.ROUTINE else None),
        rung_number=rung,
        block_id=block,
        block_name=block_name,
        block_type=block_type,
        pin_name=pin_name or meta.get("pin_name") or meta.get("parameter_name"),
        pin_direction=pin_direction or meta.get("pin_direction") or meta.get("parameter_usage"),
        statement_index=(
            int(meta["statement_index"])
            if meta.get("statement_index") is not None
            else statement
        ),
        instruction_type=str(meta.get("instruction_type")) if meta.get("instruction_type") else None,
        relationship_ids=[rel.id],
        source_object_id=rel.source_id,
        causality=causality,  # type: ignore[arg-type]
        metadata={
            "relationship_type": rel.relationship_type.value,
            "fbd_relation": meta.get("fbd_relation"),
            "operand_role": meta.get("operand_role"),
            "aoi_name": meta.get("aoi_name"),
            "aoi_instance": meta.get("aoi_instance"),
            "parameter_name": meta.get("parameter_name"),
            "parameter_usage": meta.get("parameter_usage"),
            "binding_kind": meta.get("binding_kind"),
            "statement_type": meta.get("statement_type"),
            "object_subtype": (source.attributes or {}).get("object_subtype") if source else None,
        },
    )


def _resolve_language(
    rel: Relationship,
    source: ControlObject | None,
    meta: dict[str, Any],
) -> OriginatingLanguage:
    lang = meta.get("language")
    if lang == "fbd" or meta.get("fbd_relation"):
        return "fbd"
    if lang in {"structured_text", "st"}:
        return "structured_text"
    if lang == "sfc":
        return "sfc"
    if meta.get("binding_kind") in {"aoi_parameter", "vendor_amp_block"}:
        return "aoi"
    if meta.get("aoi_name") or meta.get("aoi_instance"):
        return "aoi"
    if meta.get("vendor_amp_block"):
        return "aoi"
    if rel.relationship_type == RelationshipType.CALLS:
        return "aoi"
    if source is not None:
        if source.object_type == ControlObjectType.FUNCTION_BLOCK_PIN:
            return "fbd"
        if source.object_type == ControlObjectType.FUNCTION_BLOCK:
            src_ps = source.platform_specific or {}
            src_attrs = source.attributes or {}
            if (meta.get("language") == "fbd") or src_ps.get("language") == "fbd":
                return "fbd"
            if src_attrs.get("is_vendor_amp_block") or src_ps.get("vendor_amp_block"):
                return "aoi"
            if src_attrs.get("is_aoi_instance") or src_ps.get("aoi_name"):
                return "aoi"
            return "aoi"
        if source.object_type == ControlObjectType.INSTRUCTION:
            src_lang = (source.attributes or {}).get("language") or (
                (source.platform_specific or {}).get("language")
            )
            if src_lang in {"structured_text", "st"}:
                return "structured_text"
            return "ladder"
        if source.object_type == ControlObjectType.RUNG:
            return "ladder"
        if source.object_type == ControlObjectType.INSTRUCTION:
            return "ladder"
    if meta.get("binding_status") == "direction_unknown":
        return "unknown"
    return "ladder"


def _resolve_platform(
    source: ControlObject | None,
    meta: dict[str, Any],
) -> OriginatingPlatform:
    raw = (meta.get("platform") or (source.platform_specific or {}).get("platform") if source else None) or "rockwell"
    normalized = str(raw).lower().replace("-", "_")
    if normalized in {"rockwell", "siemens", "delta_v", "honeywell"}:
        return normalized  # type: ignore[return-value]
    return "unknown"


def _parse_location(location: str | None) -> tuple[str | None, int | None, str | None, int | None]:
    if not location:
        return None, None, None, None
    routine_m = _ROUTINE_RE.search(location)
    rung_m = _RUNG_RE.search(location)
    block_m = _BLOCK_RE.search(location)
    stmt_m = _STATEMENT_RE.search(location)
    return (
        routine_m.group(1) if routine_m else None,
        int(rung_m.group(1)) if rung_m else None,
        block_m.group(1) if block_m else None,
        int(stmt_m.group(1)) if stmt_m else None,
    )


def _writer_type(language: OriginatingLanguage, source: ControlObject | None) -> str:
    if language == "aoi":
        return "aoi_parameter"
    if language == "fbd":
        return "fbd_output_pin"
    if language == "structured_text":
        return "st_assignment"
    if source and source.object_type == ControlObjectType.RUNG:
        return "ladder_rung"
    return "ladder_rung"


def _reader_type(language: OriginatingLanguage, source: ControlObject | None) -> str:
    if language == "fbd":
        return "fbd_input_pin"
    if language == "structured_text":
        return "st_read"
    if language == "aoi":
        return "aoi_parameter"
    return "ladder_rung"


def _confidence_summary(
    *,
    writers: list[SignalWriter],
    readers: list[SignalReader],
    unknowns: list[UnknownDependency],
    target: ControlObject | None,
    provenance: Any,
) -> ConfidenceSummary:
    score = 0.72 if writers else 0.42
    evidence = ["parsed_plc_logic"]
    missing = [
        "live_tag_values",
        "alarm_event_history",
        "updated_control_narrative",
        "engineer_confirmation",
    ]
    warnings: list[str] = []

    if writers:
        score = min(w.confidence for w in writers)
        evidence.append("deterministic_writer")
    else:
        warnings.append("No deterministic writer was found for this signal.")

    if any(w.condition_signal_names for w in writers):
        evidence.append("deterministic_condition")

    if readers:
        evidence.append("downstream_readers")

    if unknowns:
        score = min(score, 0.74)
        warnings.append(
            "Direction-unknown references exist and were not treated as deterministic causes."
        )

    if provenance and provenance.writer_count > 1:
        score = min(score, 0.82)
        warnings.append("Multiple writers found; review scan order and last-writer behavior.")

    if target and (target.current_state or {}).get("value") is not None:
        score = min(0.98, score + 0.03)
        evidence.append("live_tag_state")

    label = _score_to_label(score)
    return ConfidenceSummary(
        confidence=round(max(0.05, min(0.98, score)), 3),
        confidence_label=label,
        evidence=evidence,
        missing_evidence=missing,
        warnings=warnings,
    )


def _count_evidence_sources(verification: VerificationByLanguage) -> EvidenceSourceCounts:
    return EvidenceSourceCounts(
        ladder=len(verification.ladder),
        fbd=len(verification.fbd),
        structured_text=len(verification.structured_text),
        sfc=len(verification.sfc),
        aoi=len(verification.aoi),
        unknown=len(verification.unknown),
    )


def _summary_answer(
    *,
    target_name: str,
    writer_count: int,
    condition_count: int,
    reader_count: int,
    unknown_count: int,
    calculated_writer_count: int = 0,
) -> str:
    if writer_count == 0:
        return (
            f"{target_name} has no deterministic writer in the normalized model. "
            "Review unknown references and missing evidence."
        )
    if condition_count == 0:
        if calculated_writer_count:
            text = (
                f"{target_name} is calculated in {calculated_writer_count} location"
                f"{'' if calculated_writer_count == 1 else 's'}."
            )
        else:
            text = (
                f"{target_name} is written in {writer_count} location"
                f"{'' if writer_count == 1 else 's'} with no boolean upstream conditions identified."
            )
    else:
        text = (
            f"{target_name} is controlled by {condition_count} upstream condition"
            f"{'' if condition_count == 1 else 's'} and written in {writer_count} location"
            f"{'' if writer_count == 1 else 's'}."
        )
    if reader_count:
        text += (
            f" It is used downstream in {reader_count} location"
            f"{'' if reader_count == 1 else 's'}."
        )
    if unknown_count:
        text += f" {unknown_count} unknown-direction reference{'s' if unknown_count != 1 else ''} were preserved separately."
    return text


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


def _score_to_label(score: float) -> str:
    if score >= 0.9:
        return "very_high"
    if score >= 0.78:
        return "high"
    if score >= 0.58:
        return "medium"
    if score >= 0.38:
        return "low"
    return "very_low"


def _find_relationship(relationships: list[Relationship], rel_id: str) -> Relationship | None:
    for rel in relationships:
        if rel.id == rel_id:
            return rel
    return None


def _empty_provenance() -> SourceProvenance:
    return SourceProvenance()


__all__ = [
    "get_readers",
    "get_signal_evidence",
    "get_writers",
    "trace_downstream",
    "trace_upstream",
]
