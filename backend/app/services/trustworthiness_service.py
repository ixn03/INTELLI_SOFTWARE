from __future__ import annotations

from typing import Any, Sequence

from pydantic import BaseModel, Field

from app.models.reasoning import ControlObject, Relationship, RelationshipType, TraceResult


class TrustAssessment(BaseModel):
    confidence_score: float = Field(ge=0.0, le=1.0)
    uncertainty_reasons: list[str] = Field(default_factory=list)
    unsupported_reasons: list[str] = Field(default_factory=list)
    conflicting_reasons: list[str] = Field(default_factory=list)
    missing_runtime_reasons: list[str] = Field(default_factory=list)
    parser_coverage_reasons: list[str] = Field(default_factory=list)
    recommendation_level: str = "review"


def _level(score: float) -> str:
    if score >= 0.8:
        return "high_confidence"
    if score >= 0.55:
        return "review"
    return "needs_engineer_review"


def _score(
    base: float,
    *,
    unsupported: int = 0,
    conflicts: int = 0,
    missing_runtime: int = 0,
    parser_gaps: int = 0,
    runtime_bonus: bool = False,
) -> float:
    score = base
    score -= min(0.35, 0.11 * unsupported)
    score -= min(0.30, 0.15 * conflicts)
    score -= min(0.30, 0.10 * missing_runtime)
    score -= min(0.25, 0.08 * parser_gaps)
    if runtime_bonus:
        score += 0.08
    return max(0.05, min(0.98, round(score, 3)))


def assess_trace_confidence(
    trace_result: TraceResult,
    relationships: Sequence[Relationship] | None = None,
) -> TrustAssessment:
    ps = trace_result.platform_specific or {}
    unsupported_reasons: list[str] = []
    parser_reasons: list[str] = []
    for c in trace_result.conclusions or []:
        meta = c.platform_specific or {}
        kind = str(meta.get("trace_v2_kind") or "")
        if "too_complex" in kind or "unsupported" in kind:
            unsupported_reasons.append(c.statement)
    rels = list(relationships or trace_result.writer_relationships or [])
    for rel in rels:
        meta = rel.platform_specific or {}
        if meta.get("parse_status") in {"unsupported", "unsupported_language", "preserved_only"}:
            parser_reasons.append(f"Parser coverage gap near {rel.source_location or rel.source_id}.")
    conflicts = list(ps.get("conflicts") or [])
    score = _score(
        0.78 if trace_result.writer_relationships else 0.48,
        unsupported=len(unsupported_reasons),
        conflicts=len(conflicts),
        parser_gaps=len(parser_reasons),
    )
    return TrustAssessment(
        confidence_score=score,
        uncertainty_reasons=[] if score >= 0.8 else ["Static trace confidence is limited by available normalized relationships."],
        unsupported_reasons=unsupported_reasons,
        conflicting_reasons=[str(c) for c in conflicts],
        parser_coverage_reasons=parser_reasons,
        recommendation_level=_level(score),
    )


def assess_runtime_confidence(trace_result: TraceResult) -> TrustAssessment:
    ps = trace_result.platform_specific or {}
    missing = list(ps.get("missing_conditions") or [])
    unsupported = list(ps.get("unsupported_conditions") or [])
    conflicts = list(ps.get("conflicts") or [])
    runtime_used = bool(ps.get("runtime_snapshot_evaluated"))
    score = _score(
        0.84 if runtime_used else 0.5,
        unsupported=len(unsupported),
        conflicts=len(conflicts),
        missing_runtime=len(missing),
        runtime_bonus=runtime_used and not missing,
    )
    return TrustAssessment(
        confidence_score=score,
        uncertainty_reasons=[] if runtime_used else ["No runtime snapshot was evaluated."],
        unsupported_reasons=[str(x) for x in unsupported],
        conflicting_reasons=[str(x) for x in conflicts],
        missing_runtime_reasons=[str(x) for x in missing],
        recommendation_level=_level(score),
    )


def assess_sequence_confidence(sequence_summary: dict[str, Any]) -> TrustAssessment:
    unsupported = list(sequence_summary.get("unsupported_sequence_patterns") or [])
    transitions = list(sequence_summary.get("state_transitions") or [])
    inferred = [t for t in transitions if t.get("confidence") not in {"high", "very_high"}]
    score = _score(
        0.76 if transitions else 0.45,
        unsupported=len(unsupported),
        parser_gaps=len(inferred),
    )
    return TrustAssessment(
        confidence_score=score,
        uncertainty_reasons=["Some sequence state is inferred from deterministic patterns."] if inferred else [],
        unsupported_reasons=[str(x) for x in unsupported],
        parser_coverage_reasons=[
            "Transition confidence is below high for one or more state writes."
        ]
        if inferred
        else [],
        recommendation_level=_level(score),
    )


__all__ = [
    "TrustAssessment",
    "assess_trace_confidence",
    "assess_runtime_confidence",
    "assess_sequence_confidence",
]
