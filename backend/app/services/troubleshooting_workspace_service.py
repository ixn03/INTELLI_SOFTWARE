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
from app.services.dependency_graph_service import build_control_dependency_graph
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


class SignalTroubleshootingWorkspace(BaseModel):
    question: str
    interpretation: QuestionInterpretation
    target_signal: SignalRef | None = None
    unified_evidence: UnifiedSignalEvidence | None = None
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
    )

    return SignalTroubleshootingWorkspace(
        question=question,
        interpretation=interpretation,
        target_signal=_signal_ref(target, target_id),
        unified_evidence=unified,
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
