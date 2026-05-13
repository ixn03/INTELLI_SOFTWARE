from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Optional

from app.models.evidence import EvidenceBundle, EvidenceItem
from app.models.reasoning import Relationship, TraceResult


def _confidence_from_penalties(
    base: float,
    *,
    unsupported: int = 0,
    conflicts: int = 0,
    missing_runtime: int = 0,
    runtime_present: bool = False,
) -> float:
    score = base
    score -= min(0.35, unsupported * 0.12)
    score -= min(0.30, conflicts * 0.15)
    score -= min(0.30, missing_runtime * 0.10)
    if runtime_present:
        score += 0.08
    return max(0.05, min(0.98, round(score, 3)))


def _relationship_id(rel: Relationship) -> str:
    return rel.id or f"{rel.source_id}->{rel.relationship_type.value}->{rel.target_id}"


def _first_sentence(trace_result: TraceResult) -> str:
    if trace_result.summary:
        return trace_result.summary.strip()
    if trace_result.conclusions:
        return trace_result.conclusions[0].statement
    return f"Trace result for {trace_result.target_object_id}."


def build_trace_evidence(trace_result: TraceResult) -> EvidenceBundle:
    supporting: list[EvidenceItem] = []
    unsupported: list[EvidenceItem] = []

    for rel in trace_result.writer_relationships:
        supporting.append(
            EvidenceItem(
                evidence_type="static_trace_writer",
                source_platform=rel.source_platform,
                source_location=rel.source_location,
                target_object_id=rel.target_id,
                statement=f"Writer relationship {rel.relationship_type.value} targets {rel.target_id}.",
                confidence=0.78,
                related_relationship_ids=[_relationship_id(rel)],
                metadata={
                    "relationship_type": rel.relationship_type.value,
                    "write_behavior": rel.write_behavior.value if rel.write_behavior else None,
                    "platform_specific": dict(rel.platform_specific or {}),
                },
            )
        )

    for rel in trace_result.reader_relationships:
        supporting.append(
            EvidenceItem(
                evidence_type="static_trace_reader",
                source_platform=rel.source_platform,
                source_location=rel.source_location,
                target_object_id=rel.target_id,
                statement=f"Reader/reference relationship {rel.relationship_type.value} uses {rel.target_id}.",
                confidence=0.66,
                related_relationship_ids=[_relationship_id(rel)],
                metadata={"relationship_type": rel.relationship_type.value},
            )
        )

    for conclusion in trace_result.conclusions:
        meta = conclusion.platform_specific or {}
        kind = str(meta.get("trace_v2_kind") or "")
        if "too_complex" in kind or "unsupported" in kind:
            unsupported.append(
                EvidenceItem(
                    evidence_type="unsupported_trace_pattern",
                    source_platform=conclusion.source_platform,
                    source_location=conclusion.source_location,
                    target_object_id=trace_result.target_object_id,
                    statement=conclusion.statement,
                    confidence=0.25,
                    unsupported=True,
                    metadata=dict(meta),
                )
            )

    conflicts = list((trace_result.platform_specific or {}).get("conflicts") or [])
    conflicting = [
        EvidenceItem(
            evidence_type="conflicting_writer",
            target_object_id=trace_result.target_object_id,
            statement="Multiple writer paths may conflict or depend on scan order.",
            confidence=0.35,
            deterministic=True,
            metadata={"conflict": conflict},
        )
        for conflict in conflicts
    ]

    confidence = _confidence_from_penalties(
        0.78 if supporting else 0.45,
        unsupported=len(unsupported),
        conflicts=len(conflicting),
    )
    warnings: list[str] = []
    if unsupported:
        warnings.append("unsupported_trace_evidence_present")
    if conflicting:
        warnings.append("conflicting_writer_evidence_present")
    return EvidenceBundle(
        conclusion=_first_sentence(trace_result),
        supporting_evidence=supporting,
        conflicting_evidence=conflicting,
        unsupported_evidence=unsupported,
        confidence=confidence,
        warnings=warnings,
    )


def build_runtime_evidence(
    trace_result: TraceResult,
    runtime_snapshot: Optional[Any] = None,
) -> EvidenceBundle:
    ps = trace_result.platform_specific or {}
    supporting: list[EvidenceItem] = []
    unsupported: list[EvidenceItem] = []
    conflicts = list(ps.get("conflicts") or [])

    for bucket_name, confidence in (
        ("satisfied_conditions", 0.9),
        ("blocking_conditions", 0.88),
        ("missing_conditions", 0.35),
        ("unsupported_conditions", 0.22),
    ):
        rows = ps.get(bucket_name) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                keys = [str(row["snapshot_key"])] if row.get("snapshot_key") else []
                statement = str(row.get("natural_language") or row.get("reason") or bucket_name)
            else:
                keys = []
                statement = str(row)
            item = EvidenceItem(
                evidence_type=f"runtime_{bucket_name.removesuffix('_conditions')}",
                target_object_id=trace_result.target_object_id,
                statement=statement,
                confidence=confidence,
                deterministic=True,
                runtime_snapshot_keys=keys,
                unsupported=bucket_name == "unsupported_conditions",
                metadata={"bucket": bucket_name, "row": row},
            )
            if bucket_name == "unsupported_conditions":
                unsupported.append(item)
            else:
                supporting.append(item)

    conflicting = [
        EvidenceItem(
            evidence_type="runtime_conflicting_writer",
            target_object_id=trace_result.target_object_id,
            statement="Runtime evaluation found simultaneously satisfied opposing writer paths.",
            confidence=0.3,
            metadata={"conflict": conflict},
        )
        for conflict in conflicts
    ]

    missing = len(ps.get("missing_conditions") or [])
    confidence = _confidence_from_penalties(
        0.86 if ps.get("runtime_snapshot_evaluated") else 0.5,
        unsupported=len(unsupported),
        conflicts=len(conflicting),
        missing_runtime=missing,
        runtime_present=runtime_snapshot is not None or bool(supporting),
    )
    warnings: list[str] = []
    if missing:
        warnings.append("missing_runtime_values_present")
    if unsupported:
        warnings.append("unsupported_runtime_evidence_present")
    if conflicting:
        warnings.append("runtime_conflicts_present")
    return EvidenceBundle(
        conclusion=_first_sentence(trace_result),
        supporting_evidence=supporting,
        conflicting_evidence=conflicting,
        unsupported_evidence=unsupported,
        confidence=confidence,
        warnings=warnings,
    )


def build_sequence_evidence(sequence_summary: dict[str, Any]) -> EvidenceBundle:
    supporting: list[EvidenceItem] = []
    for transition in sequence_summary.get("state_transitions") or []:
        supporting.append(
            EvidenceItem(
                evidence_type="sequence_transition",
                source_location=transition.get("source_location"),
                target_object_id=transition.get("state_tag"),
                statement=(
                    f"{transition.get('state_tag_name') or transition.get('state_tag')} "
                    f"can transition to {transition.get('target_state')}."
                ),
                confidence=0.7 if transition.get("confidence") == "high" else 0.55,
                metadata=dict(transition),
            )
        )
    unsupported = [
        EvidenceItem(
            evidence_type="unsupported_sequence_pattern",
            target_object_id=row.get("state_tag_id"),
            source_location=row.get("source_location"),
            statement=str(row.get("detail") or row.get("kind") or "Unsupported sequence pattern."),
            confidence=0.25,
            unsupported=True,
            metadata=dict(row),
        )
        for row in sequence_summary.get("unsupported_sequence_patterns") or []
        if isinstance(row, dict)
    ]
    confidence = _confidence_from_penalties(
        0.7 if supporting else 0.45,
        unsupported=len(unsupported),
    )
    return EvidenceBundle(
        conclusion="Sequence evidence summarized deterministically.",
        supporting_evidence=supporting,
        unsupported_evidence=unsupported,
        confidence=confidence,
        warnings=["unsupported_sequence_evidence_present"] if unsupported else [],
    )


def build_version_diff_evidence(version_diff: Any) -> EvidenceBundle:
    data = asdict(version_diff) if is_dataclass(version_diff) else dict(version_diff)
    supporting: list[EvidenceItem] = []
    for row in data.get("changed_relationships") or []:
        supporting.append(
            EvidenceItem(
                evidence_type="version_relationship_change",
                statement=f"Relationship change detected: {row.get('change')}.",
                confidence=0.82,
                metadata=dict(row),
            )
        )
    for row in data.get("changed_objects") or []:
        supporting.append(
            EvidenceItem(
                evidence_type="version_object_change",
                target_object_id=row.get("id"),
                statement=f"Object change detected: {row.get('change')}.",
                confidence=0.78,
                metadata=dict(row),
            )
        )
    confidence = 0.82 if supporting else 0.95
    return EvidenceBundle(
        conclusion=str(data.get("summary") or "Version comparison complete."),
        supporting_evidence=supporting,
        confidence=confidence,
        warnings=list(data.get("risk_flags") or []),
    )


def combine_evidence_bundles(
    conclusion: str,
    bundles: Iterable[EvidenceBundle],
) -> EvidenceBundle:
    rows = list(bundles)
    supporting = [item for b in rows for item in b.supporting_evidence]
    conflicting = [item for b in rows for item in b.conflicting_evidence]
    unsupported = [item for b in rows for item in b.unsupported_evidence]
    warnings = sorted({w for b in rows for w in b.warnings})
    confidence = min((b.confidence for b in rows), default=0.5)
    if any(b.confidence > confidence for b in rows) and not conflicting and not unsupported:
        confidence = round(sum(b.confidence for b in rows) / max(1, len(rows)), 3)
    return EvidenceBundle(
        conclusion=conclusion,
        supporting_evidence=supporting,
        conflicting_evidence=conflicting,
        unsupported_evidence=unsupported,
        confidence=confidence,
        warnings=warnings,
    )


__all__ = [
    "build_trace_evidence",
    "build_runtime_evidence",
    "build_sequence_evidence",
    "build_version_diff_evidence",
    "combine_evidence_bundles",
]
