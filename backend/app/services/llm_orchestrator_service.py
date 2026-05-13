"""LLM-assisted orchestration (v1) — evidence-bound; feature-flagged.

The LLM may classify intent, suggest target names from provided object
lists, choose deterministic tools, and rephrase evidence. It must not
invent tags, rungs, or relationships not present in the evidence package.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from app.models.reasoning import ControlObject, ExecutionContext, Relationship, TraceResult
from app.models.runtime_value import RuntimeSnapshotModel
from app.services.ask_v2_service import answer_question_v2
from app.services.knowledge_service import knowledge_rank_score, knowledge_service
from app.services.question_router_service import find_target_object
from app.services.runtime_ingestion_service import normalize_runtime_snapshot
from app.services.sequence_reasoning_service import analyze_sequences
from app.models.knowledge import KnowledgeItem


class LlmProvider(Protocol):
    def complete(self, system: str, user: str) -> str:
        ...


class MockLlmProvider:
    """Test provider: echoes structured JSON derived only from the user prompt."""

    def complete(self, system: str, user: str) -> str:
        # Deterministic stub: extract ANSWER: lines if present; else short ack.
        for line in user.splitlines():
            if line.strip().upper().startswith("ANSWER:"):
                return line.split(":", 1)[1].strip()
        return "No LLM paraphrase (mock)."


@dataclass
class EvidencePackage:
    question: str
    target_object_id: Optional[str] = None
    target_resolution: str = "none"
    trace: Optional[TraceResult] = None
    sequence_summary: Optional[dict[str, Any]] = None
    knowledge_hits: list[KnowledgeItem] = field(default_factory=list)
    runtime_snapshot_present: bool = False
    warnings: list[str] = field(default_factory=list)
    candidate_targets: list[str] = field(default_factory=list)


def _known_tag_names(control_objects: list[ControlObject]) -> set[str]:
    return {
        o.name
        for o in control_objects
        if o.object_type.value == "tag" and o.name
    }


def _forbidden_hallucination_check(answer: str, known_names: set[str]) -> list[str]:
    """Flag capitalized tag-like tokens in the answer that are not known tag names."""

    warnings: list[str] = []
    for m in re.finditer(
        r"\b[A-Z][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\b",
        answer,
    ):
        tok = m.group(0)
        if tok in known_names:
            continue
        if any(tok in k or k.endswith(tok) for k in known_names if "." in k):
            continue
        if tok in {"IF", "NOT", "AND", "OR", "TRUE", "FALSE", "PLC", "CPU"}:
            continue
        warnings.append(f"answer_mentions_unlisted_token:{tok}")
    return warnings[:8]


def build_evidence_package(
    question: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: list[ExecutionContext],
    runtime_snapshot: Any = None,
) -> EvidencePackage:
    warnings: list[str] = []
    target = find_target_object(question, control_objects)
    candidate_targets = sorted(
        {o.name for o in control_objects if o.object_type.value == "tag" and o.name} | set(),
    )[:200]

    trace = None
    seq = None
    knowledge_hits: list[KnowledgeItem] = []

    if target is None:
        warnings.append("target_not_resolved_deterministically")
        return EvidencePackage(
            question=question,
            target_resolution="unresolved",
            warnings=warnings,
            candidate_targets=candidate_targets,
        )

    trace = answer_question_v2(
        question=question,
        control_objects=control_objects,
        relationships=relationships,
        execution_contexts=execution_contexts,
        runtime_snapshot=runtime_snapshot,
    )
    seq = analyze_sequences(control_objects, relationships, execution_contexts)
    knowledge_hits = sorted(
        knowledge_service.list_by_target(target.id),
        key=knowledge_rank_score,
        reverse=True,
    )
    snap_ok = runtime_snapshot is not None and (
        isinstance(runtime_snapshot, RuntimeSnapshotModel)
        and bool(runtime_snapshot.values)
        or isinstance(runtime_snapshot, dict)
        and bool(runtime_snapshot)
    )
    if runtime_snapshot is not None and not snap_ok:
        warnings.append("runtime_snapshot_empty_or_invalid")

    return EvidencePackage(
        question=question,
        target_object_id=target.id,
        target_resolution="deterministic_match",
        trace=trace,
        sequence_summary=seq,
        knowledge_hits=knowledge_hits,
        runtime_snapshot_present=snap_ok,
        warnings=warnings,
        candidate_targets=candidate_targets,
    )


def answer_with_llm_assist(
    question: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: Optional[list[ExecutionContext]] = None,
    runtime_snapshot: Any = None,
    *,
    llm: Optional[LlmProvider] = None,
    enable_llm: Optional[bool] = None,
) -> dict[str, Any]:
    """Return answer dict with evidence_used, confidence, ids, warnings."""

    if enable_llm is None:
        enable_llm = os.environ.get("ENABLE_LLM_ASSIST", "false").lower() in (
            "1",
            "true",
            "yes",
        )

    ev = build_evidence_package(
        question,
        control_objects,
        relationships,
        execution_contexts or [],
        runtime_snapshot=runtime_snapshot,
    )

    trace_summary = ev.trace.summary if ev.trace and ev.trace.summary else ""
    if not trace_summary and ev.trace and ev.trace.conclusions:
        trace_summary = ev.trace.conclusions[0].statement

    allowed_names = _known_tag_names(control_objects)
    evidence_used: dict[str, Any] = {
        "target_object_id": ev.target_object_id,
        "target_resolution": ev.target_resolution,
        "trace_summary_excerpt": (trace_summary or "")[:2000],
        "sequence_keys": sorted((ev.sequence_summary or {}).keys())
        if isinstance(ev.sequence_summary, dict)
        else [],
        "knowledge_count": len(ev.knowledge_hits),
        "runtime_snapshot_present": ev.runtime_snapshot_present,
    }

    answer = trace_summary or "No deterministic trace summary available."
    confidence = "high" if ev.target_resolution == "deterministic_match" else "low"

    llm_warnings: list[str] = []
    if enable_llm and llm is not None:
        user = (
            f"QUESTION:\n{question}\n\nEVIDENCE_JSON:\n{repr(evidence_used)}\n"
            f"STRICT_RULES: Only restate facts from EVIDENCE_JSON. "
            f"ANSWER: <one paragraph>\n"
        )
        paraphrase = llm.complete(
            system="You rewrite controls evidence; never invent tags.",
            user=user,
        )
        if paraphrase:
            answer = paraphrase
        llm_warnings.extend(_forbidden_hallucination_check(answer, allowed_names))

    warnings = list(ev.warnings) + llm_warnings
    if not enable_llm:
        warnings.append("llm_assist_disabled")

    return {
        "answer": answer,
        "evidence_used": evidence_used,
        "confidence": confidence,
        "deterministic_trace_id": ev.target_object_id,
        "target_object_id": ev.target_object_id,
        "warnings": warnings,
        "trace_result": ev.trace,
    }


__all__ = [
    "EvidencePackage",
    "LlmProvider",
    "MockLlmProvider",
    "answer_with_llm_assist",
    "build_evidence_package",
]
