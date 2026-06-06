"""Deterministic control dependency graph over the neutral model.

The parser feeds INTELLI, but troubleshooting starts here: this module
turns normalized relationships into tag provenance and dependency chains
that answer:

* Who writes this tag?
* Who reads this tag?
* Which upstream tags/conditions gate a write?
* What deterministic chain can explain why a tag is false?

No LLMs, no vendor connectors, no raw source parsing.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    LogicExpression,
    LogicExpressionKind,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
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


class DependencyEvidence(BaseModel):
    plc_logic: bool = False
    live_tag_state: bool = False
    historical_occurrence: bool = False
    updated_narrative: bool = False
    missing: list[str] = Field(default_factory=list)


class DependencyConfidence(BaseModel):
    confidence_score: float = Field(ge=0.0, le=1.0)
    evidence: DependencyEvidence = Field(default_factory=DependencyEvidence)
    reasons: list[str] = Field(default_factory=list)


class DependencyEdge(BaseModel):
    upstream_tag_id: str
    upstream_tag_name: str | None = None
    downstream_tag_id: str
    downstream_tag_name: str | None = None
    writer_source_id: str
    writer_relationship_id: str
    source_location: str | None = None
    relationship_type: str
    write_behavior: str | None = None
    condition_tag_ids: list[str] = Field(default_factory=list)
    condition_summary: str | None = None
    confidence: DependencyConfidence
    metadata: dict[str, Any] = Field(default_factory=dict)


class TagAccess(BaseModel):
    source_id: str
    relationship_id: str
    relationship_type: str
    source_location: str | None = None
    instruction_type: str | None = None
    write_behavior: str | None = None
    condition_tag_ids: list[str] = Field(default_factory=list)
    condition_summary: str | None = None
    confidence: DependencyConfidence


class TagProvenance(BaseModel):
    tag_id: str
    tag_name: str | None = None
    writer_count: int = 0
    reader_count: int = 0
    writers: list[TagAccess] = Field(default_factory=list)
    readers: list[TagAccess] = Field(default_factory=list)
    referenced_by_unknown_blocks: list[TagAccess] = Field(default_factory=list)
    required_condition_tag_ids: list[str] = Field(default_factory=list)
    confidence: DependencyConfidence


class DependencyTrace(BaseModel):
    target_tag_id: str
    target_tag_name: str | None = None
    dependency_chains: list[list[str]] = Field(default_factory=list)
    dependency_chain_names: list[list[str]] = Field(default_factory=list)
    required_condition_tag_ids: list[str] = Field(default_factory=list)
    required_condition_tag_names: list[str] = Field(default_factory=list)
    writer_count: int = 0
    confidence: DependencyConfidence
    summary: str


class ControlDependencyGraph(BaseModel):
    tags: dict[str, str | None] = Field(default_factory=dict)
    name_to_tag_id: dict[str, str] = Field(default_factory=dict)
    edges: list[DependencyEdge] = Field(default_factory=list)
    provenance: dict[str, TagProvenance] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_control_dependency_graph(
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> ControlDependencyGraph:
    """Build tag provenance and data dependencies from normalized edges."""

    object_index = {obj.id: obj for obj in control_objects}
    tags = {
        obj.id: obj.name
        for obj in control_objects
        if _is_traceable_signal(obj)
    }
    name_to_tag_id = _build_name_index(tags)
    by_source: dict[str, list[Relationship]] = defaultdict(list)
    for rel in relationships:
        by_source[rel.source_id].append(rel)

    edges: list[DependencyEdge] = []
    provenance_build: dict[str, dict[str, Any]] = {
        tag_id: {
            "writers": [],
            "readers": [],
            "references": [],
            "conditions": [],
        }
        for tag_id in tags
    }

    for rel in relationships:
        if rel.relationship_type == RelationshipType.READS and rel.target_id in tags:
            provenance_build[rel.target_id]["readers"].append(
                _tag_access(rel, [], object_index)
            )
        elif rel.relationship_type == RelationshipType.REFERENCES and rel.target_id in tags:
            meta = rel.platform_specific or {}
            if meta.get("binding_status") == "direction_unknown":
                provenance_build[rel.target_id]["references"].append(
                    _tag_access(rel, [], object_index)
                )

    for writer in relationships:
        if writer.relationship_type not in WRITER_TYPES:
            continue
        if writer.target_id not in tags:
            continue

        condition_reads = [
            rel
            for rel in by_source.get(writer.source_id, [])
            if rel.relationship_type == RelationshipType.READS
            and rel.target_id in tags
            and rel.target_id != writer.target_id
        ]
        condition_tag_ids = _unique([r.target_id for r in condition_reads])
        condition_tag_ids = _unique(
            condition_tag_ids
            + _incoming_fbd_connection_sources(
                writer.source_id,
                by_source,
                relationships,
                object_index,
                tags,
            )
        )
        expr_condition_tag_ids = _logic_expression_tag_ids(writer.logic_expression, tags)
        condition_tag_ids = _unique(condition_tag_ids + expr_condition_tag_ids)
        condition_summary = _condition_summary(writer, condition_tag_ids, object_index)
        access = _tag_access(writer, condition_tag_ids, object_index)
        provenance_build[writer.target_id]["writers"].append(access)
        provenance_build[writer.target_id]["conditions"].extend(condition_tag_ids)

        for upstream_tag_id in condition_tag_ids:
            edges.append(
                DependencyEdge(
                    upstream_tag_id=upstream_tag_id,
                    upstream_tag_name=tags.get(upstream_tag_id),
                    downstream_tag_id=writer.target_id,
                    downstream_tag_name=tags.get(writer.target_id),
                    writer_source_id=writer.source_id,
                    writer_relationship_id=writer.id,
                    source_location=writer.source_location,
                    relationship_type=_rel_type(writer.relationship_type),
                    write_behavior=_write_behavior(writer.write_behavior),
                    condition_tag_ids=condition_tag_ids,
                    condition_summary=condition_summary,
                    confidence=_relationship_confidence(writer, object_index),
                    metadata={
                        "instruction_type": (writer.platform_specific or {}).get(
                            "instruction_type"
                        ),
                    },
                )
            )

    edges.extend(_fbd_connection_edges(relationships, object_index, tags))

    provenance: dict[str, TagProvenance] = {}
    for tag_id, name in tags.items():
        p = provenance_build[tag_id]
        condition_ids = _unique(p["conditions"])
        confidence = _tag_confidence(
            writers=p["writers"],
            readers=p["readers"],
            references=p["references"],
            tag_object=object_index.get(tag_id),
        )
        provenance[tag_id] = TagProvenance(
            tag_id=tag_id,
            tag_name=name,
            writer_count=len(p["writers"]),
            reader_count=len(p["readers"]),
            writers=p["writers"],
            readers=p["readers"],
            referenced_by_unknown_blocks=p["references"],
            required_condition_tag_ids=condition_ids,
            confidence=confidence,
        )

    return ControlDependencyGraph(
        tags=tags,
        name_to_tag_id=name_to_tag_id,
        edges=edges,
        provenance=provenance,
        metadata={
            "tag_count": len(tags),
            "dependency_edge_count": len(edges),
            "writer_relationship_count": sum(
                1
                for r in relationships
                if r.relationship_type in WRITER_TYPES and r.target_id in tags
            ),
            "reader_relationship_count": sum(
                1
                for r in relationships
                if r.relationship_type == RelationshipType.READS
                and r.target_id in tags
            ),
        },
    )


def trace_dependency_chain(
    graph: ControlDependencyGraph,
    target_tag_id_or_name: str,
    *,
    max_depth: int = 6,
) -> DependencyTrace:
    """Trace upstream tag dependency chains ending at a tag id or name."""

    target_tag_id = _resolve_graph_tag_id(graph, target_tag_id_or_name)

    incoming: dict[str, list[DependencyEdge]] = defaultdict(list)
    for edge in graph.edges:
        incoming[edge.downstream_tag_id].append(edge)

    chains: list[list[str]] = []

    def walk(tag_id: str, path: list[str], depth: int) -> None:
        if depth >= max_depth or tag_id not in incoming:
            chains.append(list(reversed(path)))
            return
        expanded = False
        for edge in incoming[tag_id]:
            upstream = edge.upstream_tag_id
            if upstream in path:
                chains.append(list(reversed(path + [upstream])))
                continue
            expanded = True
            walk(upstream, path + [upstream], depth + 1)
        if not expanded:
            chains.append(list(reversed(path)))

    walk(target_tag_id, [target_tag_id], 0)
    provenance = graph.provenance.get(target_tag_id)
    condition_ids = provenance.required_condition_tag_ids if provenance else []
    condition_names = [
        graph.tags.get(tag_id) or tag_id
        for tag_id in condition_ids
    ]
    writer_count = provenance.writer_count if provenance else 0
    confidence = provenance.confidence if provenance else _empty_confidence()
    name = graph.tags.get(target_tag_id)
    chain_names = [
        [graph.tags.get(tag_id) or tag_id for tag_id in chain]
        for chain in chains
    ]
    summary = (
        f"{name or target_tag_id} has {writer_count} writer(s) and "
        f"{len(condition_ids)} direct required condition tag(s)."
    )
    if not writer_count:
        summary = f"{name or target_tag_id} has no known writer in the dependency graph."

    return DependencyTrace(
        target_tag_id=target_tag_id,
        target_tag_name=name,
        dependency_chains=chains,
        dependency_chain_names=chain_names,
        required_condition_tag_ids=condition_ids,
        required_condition_tag_names=condition_names,
        writer_count=writer_count,
        confidence=confidence,
        summary=summary,
    )


def trace_dependency_chains(
    graph: ControlDependencyGraph,
    target_tag_id_or_name: str,
    *,
    max_depth: int = 6,
) -> DependencyTrace:
    """Alias with the plural name used by the troubleshooting API."""

    return trace_dependency_chain(
        graph,
        target_tag_id_or_name,
        max_depth=max_depth,
    )


def _tag_access(
    rel: Relationship,
    condition_tag_ids: list[str],
    object_index: dict[str, ControlObject],
) -> TagAccess:
    meta = rel.platform_specific or {}
    return TagAccess(
        source_id=rel.source_id,
        relationship_id=rel.id,
        relationship_type=_rel_type(rel.relationship_type),
        source_location=rel.source_location,
        instruction_type=(
            str(meta.get("instruction_type")) if meta.get("instruction_type") else None
        ),
        write_behavior=_write_behavior(rel.write_behavior),
        condition_tag_ids=list(condition_tag_ids),
        condition_summary=_condition_summary(rel, condition_tag_ids, object_index),
        confidence=_relationship_confidence(rel, object_index),
    )


def _condition_summary(
    rel: Relationship,
    condition_tag_ids: list[str],
    object_index: dict[str, ControlObject],
) -> str | None:
    if rel.logic_expression is not None:
        tags = [
            object_index[tag_id].name or tag_id
            for tag_id in condition_tag_ids
            if tag_id in object_index
        ]
        if not tags and rel.logic_expression.kind == LogicExpressionKind.CONSTANT:
            return "unconditional"
        if tags:
            return ", ".join(tags)
    return None


def _logic_expression_tag_ids(
    expr: LogicExpression | None,
    tag_index: dict[str, str | None],
) -> list[str]:
    if expr is None:
        return []
    tag_name_to_id = {
        name: tag_id for tag_id, name in tag_index.items() if name is not None
    }
    out: list[str] = []
    for name in _logic_expression_tag_names(expr):
        tag_id = tag_name_to_id.get(name)
        if tag_id:
            out.append(tag_id)
    return _unique(out)


def _is_traceable_signal(obj: ControlObject) -> bool:
    return obj.object_type in {
        ControlObjectType.TAG,
        ControlObjectType.SYSTEM_ATTRIBUTE,
        ControlObjectType.FUNCTION_BLOCK_PIN,
    }


def _build_name_index(tags: dict[str, str | None]) -> dict[str, str]:
    out: dict[str, str] = {}
    for tag_id, name in tags.items():
        out.setdefault(tag_id, tag_id)
        if name:
            out.setdefault(name, tag_id)
            out.setdefault(name.upper(), tag_id)
    return out


def _resolve_graph_tag_id(
    graph: ControlDependencyGraph,
    target_tag_id_or_name: str,
) -> str:
    return graph.name_to_tag_id.get(
        target_tag_id_or_name,
        graph.name_to_tag_id.get(target_tag_id_or_name.upper(), target_tag_id_or_name),
    )


def _incoming_fbd_connection_sources(
    writer_source_id: str,
    by_source: dict[str, list[Relationship]],
    relationships: list[Relationship],
    object_index: dict[str, ControlObject],
    tags: dict[str, str | None],
) -> list[str]:
    block_input_pins = [
        rel.target_id
        for rel in by_source.get(writer_source_id, [])
        if rel.relationship_type == RelationshipType.READS
        and rel.target_id in tags
        and _pin_direction(object_index.get(rel.target_id)) == "input"
    ]
    if not block_input_pins:
        return []
    input_pin_set = set(block_input_pins)
    upstream: list[str] = []
    for rel in relationships:
        if rel.relationship_type != RelationshipType.SIGNAL_CONNECTS:
            continue
        if rel.target_id not in input_pin_set:
            continue
        if rel.source_id not in tags:
            continue
        if not _is_deterministic_fbd_connection(rel, object_index):
            continue
        upstream.append(rel.source_id)
    return _unique(upstream)


def _fbd_connection_edges(
    relationships: list[Relationship],
    object_index: dict[str, ControlObject],
    tags: dict[str, str | None],
) -> list[DependencyEdge]:
    edges: list[DependencyEdge] = []
    for rel in relationships:
        if rel.relationship_type != RelationshipType.SIGNAL_CONNECTS:
            continue
        if rel.source_id not in tags or rel.target_id not in tags:
            continue
        if not _is_deterministic_fbd_connection(rel, object_index):
            continue
        edges.append(
            DependencyEdge(
                upstream_tag_id=rel.source_id,
                upstream_tag_name=tags.get(rel.source_id),
                downstream_tag_id=rel.target_id,
                downstream_tag_name=tags.get(rel.target_id),
                writer_source_id=rel.source_id,
                writer_relationship_id=rel.id,
                source_location=rel.source_location,
                relationship_type=_rel_type(rel.relationship_type),
                condition_tag_ids=[rel.source_id],
                condition_summary=tags.get(rel.source_id) or rel.source_id,
                confidence=_connection_confidence(rel),
                metadata={"fbd_relation": "wire"},
            )
        )
    return edges


def _is_deterministic_fbd_connection(
    rel: Relationship,
    object_index: dict[str, ControlObject],
) -> bool:
    source = object_index.get(rel.source_id)
    target = object_index.get(rel.target_id)
    return _pin_direction(source) == "output" and _pin_direction(target) == "input"


def _pin_direction(obj: ControlObject | None) -> str | None:
    if obj is None or obj.object_type != ControlObjectType.FUNCTION_BLOCK_PIN:
        return None
    return str((obj.attributes or {}).get("direction") or "")


def _logic_expression_tag_names(expr: LogicExpression) -> list[str]:
    if expr.kind in {
        LogicExpressionKind.AND,
        LogicExpressionKind.OR,
        LogicExpressionKind.NOT,
    }:
        names: list[str] = []
        for child in expr.children:
            names.extend(_logic_expression_tag_names(child))
        return names
    if expr.kind in {LogicExpressionKind.CONTACT, LogicExpressionKind.INSTRUCTION}:
        return [expr.tag] if expr.tag else []
    if expr.kind == LogicExpressionKind.COMPARE:
        return [
            op
            for op in expr.operands
            if op and op[0].isalpha()
        ]
    return []


def _relationship_confidence(
    rel: Relationship,
    object_index: dict[str, ControlObject],
) -> DependencyConfidence:
    score = _confidence_level_score(rel.confidence)
    reasons: list[str] = []
    evidence = DependencyEvidence(plc_logic=True)
    target = object_index.get(rel.target_id)
    if target and (target.current_state or {}).get("value") is not None:
        evidence.live_tag_state = True
        score = min(0.98, score + 0.02)
    if rel.logic_expression is None and rel.relationship_type in WRITER_TYPES:
        score = min(score, 0.86)
        reasons.append("writer has no structured logic condition")
    meta = rel.platform_specific or {}
    if meta.get("binding_status") == "direction_unknown":
        score = min(score, 0.55)
        reasons.append("generic block parameter direction is unknown")
    if target and (target.platform_specific or {}).get("unresolved"):
        score = min(score, 0.62)
        reasons.append("target tag is unresolved")
    if not evidence.live_tag_state:
        evidence.missing.append("live_tag_state")
    if not evidence.historical_occurrence:
        evidence.missing.append("historical_occurrence")
    if not evidence.updated_narrative:
        evidence.missing.append("updated_narrative")
    return DependencyConfidence(
        confidence_score=round(max(0.05, min(0.98, score)), 3),
        evidence=evidence,
        reasons=reasons,
    )


def _connection_confidence(rel: Relationship) -> DependencyConfidence:
    evidence = DependencyEvidence(plc_logic=True)
    evidence.missing.extend(
        ["live_tag_state", "historical_occurrence", "updated_narrative"]
    )
    score = min(0.86, _confidence_level_score(rel.confidence))
    return DependencyConfidence(
        confidence_score=round(score, 3),
        evidence=evidence,
        reasons=["FBD wire connection with explicit output-to-input endpoints"],
    )


def _tag_confidence(
    *,
    writers: list[TagAccess],
    readers: list[TagAccess],
    references: list[TagAccess],
    tag_object: ControlObject | None,
) -> DependencyConfidence:
    if writers:
        score = min(w.confidence.confidence_score for w in writers)
        reasons: list[str] = []
    elif references:
        score = 0.48
        reasons = ["tag appears only in direction-unknown block references"]
    else:
        score = 0.42
        reasons = ["no writer found"]
    evidence = DependencyEvidence(plc_logic=bool(writers or readers or references))
    if tag_object and (tag_object.current_state or {}).get("value") is not None:
        evidence.live_tag_state = True
        score = min(0.98, score + 0.02)
    if not evidence.live_tag_state:
        evidence.missing.append("live_tag_state")
    evidence.missing.append("historical_occurrence")
    evidence.missing.append("updated_narrative")
    return DependencyConfidence(
        confidence_score=round(score, 3),
        evidence=evidence,
        reasons=reasons,
    )


def _confidence_level_score(level: ConfidenceLevel) -> float:
    if level == ConfidenceLevel.VERY_HIGH:
        return 0.98
    if level == ConfidenceLevel.HIGH:
        return 0.96
    if level == ConfidenceLevel.MEDIUM:
        return 0.78
    if level == ConfidenceLevel.LOW:
        return 0.52
    if level == ConfidenceLevel.VERY_LOW:
        return 0.32
    return 0.6


def _empty_confidence() -> DependencyConfidence:
    return DependencyConfidence(
        confidence_score=0.2,
        evidence=DependencyEvidence(missing=["plc_logic"]),
        reasons=["target tag not found in dependency graph"],
    )


def _rel_type(rel_type: RelationshipType | str) -> str:
    return rel_type.value if hasattr(rel_type, "value") else str(rel_type)


def _write_behavior(behavior: WriteBehaviorType | str | None) -> str | None:
    if behavior is None:
        return None
    return behavior.value if hasattr(behavior, "value") else str(behavior)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


__all__ = [
    "ControlDependencyGraph",
    "DependencyConfidence",
    "DependencyEdge",
    "DependencyEvidence",
    "DependencyTrace",
    "TagAccess",
    "TagProvenance",
    "build_control_dependency_graph",
    "trace_dependency_chain",
    "trace_dependency_chains",
]
