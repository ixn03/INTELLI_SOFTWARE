"""LLM Assist v1 — deterministic-first orchestration.

Pipeline:
  1) Deterministic target resolution
  2) Deterministic intent detection
  3) If confidence is low and LLM is enabled, optional clarification pass
     (candidates only — never overrides unresolved target with invented ids)
  4) Deterministic tools: ask_v2 (trace_v2 + runtime_v2), sequence_reasoning,
     knowledge_service
  5) Structured evidence package (no raw ladder/ST/XML)
  6) LLM rewrites evidence to concise engineering language (or disabled path)

The LLM must never receive raw PLC logic for interpretation.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from app.models.knowledge import KnowledgeItem
from app.models.reasoning import ControlObject, ExecutionContext, Relationship, TraceResult
from app.models.runtime_value import RuntimeSnapshotModel
from app.services.ask_v2_service import answer_question_v2, detect_intent_v2
from app.services.knowledge_service import knowledge_rank_score, knowledge_service
from app.services.question_router_service import find_target_object
from app.services.runtime_ingestion_service import normalize_runtime_snapshot
from app.services.sequence_reasoning_service import (
    analyze_sequences,
    filter_sequence_result_for_tag,
)
from app.services.sequence_semantics_service import analyze_sequence_semantics
from app.services.trustworthiness_service import (
    assess_runtime_confidence,
    assess_trace_confidence,
)
from app.services.llm_providers import (
    LLMConfig,
    LLMProvider,
    DisabledLLMProvider,
    engineering_paragraph_from_evidence,
    load_llm_config_from_env,
    resolve_llm_provider,
)


INTELLI_LLM_SYSTEM_PROMPT = """You are INTELLI, a deterministic controls engineering assistant.

Rules:
- Use ONLY the supplied evidence JSON. Never invent tags, alarms, states, transitions, or troubleshooting facts.
- If evidence is missing, say so explicitly.
- Prefer concise controls-engineering wording.
- Mention blockers, satisfied conditions, missing runtime values, and next checks when the evidence lists them.
- Mention unsupported or too_complex items when present in evidence.
- Never claim certainty beyond what the evidence supports.
- Do not analyze raw ladder, structured text, or XML — you only see pre-digested fields.
"""


_INTENT_CANDIDATES = [
    "runtime_diagnosis",
    "why_off",
    "what_controls",
    "where_used",
    "what_writes",
    "what_reads",
    "unknown",
]


def _known_tag_names(control_objects: list[ControlObject]) -> set[str]:
    return {
        o.name
        for o in control_objects
        if o.object_type.value == "tag" and o.name
    }


def _forbidden_hallucination_check(answer: str, known_names: set[str]) -> list[str]:
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
        if tok in {
            "IF",
            "NOT",
            "AND",
            "OR",
            "TRUE",
            "FALSE",
            "PLC",
            "CPU",
            "INTELLI",
            "Assist",
            "No",
            "Operational",
            "Missing",
            "Unsupported",
            "Target",
            "Blocking",
            "Satisfied",
            "Knowledge",
            "Sequence",
            "Verdict",
            "Conditions",
            "Values",
            "Select",
            "Pick",
            "Tag",
            "Tags",
            "Possible",
            "Explicitly",
        }:
            continue
        warnings.append(f"answer_mentions_unlisted_token:{tok}")
    return warnings[:8]


def _snapshot_nonempty(snapshot: Any) -> bool:
    if snapshot is None:
        return False
    if isinstance(snapshot, RuntimeSnapshotModel):
        return bool(snapshot.values)
    if isinstance(snapshot, dict):
        return bool(snapshot)
    return True


def _deterministic_target_candidates(
    question: str, control_objects: list[ControlObject], limit: int = 24
) -> list[str]:
    """Substring match on tag names (deterministic, no LLM)."""

    q = question.lower()
    tokens = [t for t in re.split(r"[^\w]+", q) if len(t) >= 3]
    names = [o.name for o in control_objects if o.object_type.value == "tag" and o.name]
    scored: list[tuple[int, str]] = []
    for n in names:
        nl = n.lower()
        score = 0
        for tok in tokens:
            if tok in nl:
                score += len(tok)
        if score:
            scored.append((score, n))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: list[str] = []
    seen: set[str] = set()
    for _, n in scored:
        if n not in seen:
            seen.add(n)
            out.append(n)
        if len(out) >= limit:
            break
    return out


def _coerce_snapshot(runtime_snapshot: Any) -> Any:
    if runtime_snapshot is None:
        return None
    if isinstance(runtime_snapshot, RuntimeSnapshotModel):
        return runtime_snapshot
    if isinstance(runtime_snapshot, dict):
        return normalize_runtime_snapshot(runtime_snapshot)
    return runtime_snapshot


def build_structured_evidence_package(
    *,
    question: str,
    intent: str,
    target: Optional[ControlObject],
    target_resolution: str,
    trace: TraceResult,
    sequence_slice: dict[str, Any],
    knowledge_hits: list[KnowledgeItem],
    runtime_snapshot_present: bool,
    suggested_target_candidates: list[str],
    conversation_context: Optional[dict[str, Any]] = None,
    answer_style: str = "controls_engineer",
) -> dict[str, Any]:
    ps = trace.platform_specific or {}
    verdict = ps.get("overall_verdict")
    blocking = ps.get("blocking_conditions") or []
    satisfied = ps.get("satisfied_conditions") or []
    missing = ps.get("missing_conditions") or []
    unsupported = ps.get("unsupported_conditions") or []

    seq_lines = list(sequence_slice.get("sequence_summary") or [])
    if not seq_lines and sequence_slice.get("unsupported_sequence_patterns"):
        u = sequence_slice["unsupported_sequence_patterns"]
        if isinstance(u, list) and u:
            seq_lines.append(f"Sequence analysis notes: {len(u)} unsupported pattern(s) in evidence.")

    knowledge_notes = [k.statement for k in knowledge_hits[:12]]

    conclusions = [c.statement for c in (trace.conclusions or [])[:12]]

    ev: dict[str, Any] = {
        "question": question,
        "detected_intent": intent,
        "suggested_intent_candidates": list(_INTENT_CANDIDATES),
        "target_object_id": target.id if target else None,
        "target_name": target.name if target else None,
        "target_resolution": target_resolution,
        "runtime_snapshot_present": runtime_snapshot_present,
        "runtime_verdict": verdict,
        "blocking_conditions": list(blocking) if isinstance(blocking, list) else [],
        "satisfied_conditions": list(satisfied) if isinstance(satisfied, list) else [],
        "missing_conditions": list(missing) if isinstance(missing, list) else [],
        "unsupported_conditions": list(unsupported)
        if isinstance(unsupported, list)
        else [],
        "sequence_summary": seq_lines,
        "knowledge_notes": knowledge_notes,
        "deterministic_conclusions": conclusions,
        "trace_summary": (trace.summary or "").strip(),
        "suggested_target_candidates": suggested_target_candidates,
        "router_pipeline": ps.get("ask_v2_pipeline"),
        "runtime_evaluation_used": bool(ps.get("runtime_evaluation_used")),
        "conversation_context": dict(conversation_context or {}),
        "answer_style": answer_style,
        "evidence_bundle": ps.get("evidence_bundle") or ps.get("runtime_evidence_bundle"),
        "trust_assessment": ps.get("trust_assessment"),
    }
    return ev


def answer_with_llm_assist(
    question: str,
    control_objects: list[ControlObject],
    relationships: list[Relationship],
    execution_contexts: Optional[list[ExecutionContext]] = None,
    runtime_snapshot: Any = None,
    *,
    llm: Optional[LLMProvider] = None,
    llm_config: Optional[LLMConfig] = None,
    enable_llm: Optional[bool] = None,
    conversation_context: Optional[dict[str, Any]] = None,
    answer_style: str = "controls_engineer",
) -> dict[str, Any]:
    """Run deterministic-first assist; return dict matching ``LLMAssistResponse``."""

    cfg = llm_config or load_llm_config_from_env()
    if enable_llm is None:
        enable_llm = cfg.enabled

    provider = llm if llm is not None else resolve_llm_provider(cfg)
    if not enable_llm:
        provider = DisabledLLMProvider()

    ecs = execution_contexts or []
    snap = _coerce_snapshot(runtime_snapshot)

    intent = detect_intent_v2(question)

    target = find_target_object(question, control_objects)
    tag_candidates = _deterministic_target_candidates(question, control_objects)

    warnings: list[str] = []

    if target is None:
        trace = answer_question_v2(
            question=question,
            control_objects=control_objects,
            relationships=relationships,
            execution_contexts=ecs,
            runtime_snapshot=snap,
        )
        full_seq = analyze_sequences(control_objects, relationships, ecs)
        sequence_slice = {
            "sequence_summary": [],
            "unsupported_sequence_patterns": full_seq.get("unsupported_sequence_patterns") or [],
        }
        knowledge_hits = []
        target_resolution = "unresolved"
    else:
        trace = answer_question_v2(
            question=question,
            control_objects=control_objects,
            relationships=relationships,
            execution_contexts=ecs,
            runtime_snapshot=snap,
        )
        full_seq = analyze_sequences(control_objects, relationships, ecs)
        sequence_slice = filter_sequence_result_for_tag(full_seq, target.id)
        knowledge_hits = sorted(
            knowledge_service.list_by_target(target.id),
            key=knowledge_rank_score,
            reverse=True,
        )
        target_resolution = "deterministic_match"

    snap_ok = _snapshot_nonempty(snap)
    if snap is not None and not snap_ok:
        warnings.append("runtime_snapshot_empty_or_invalid")

    sequence_semantics = analyze_sequence_semantics(
        control_objects,
        relationships,
        ecs,
        runtime_snapshot=snap,
    ).model_dump(mode="json")

    evidence = build_structured_evidence_package(
        question=question,
        intent=intent,
        target=target,
        target_resolution=target_resolution,
        trace=trace,
        sequence_slice=sequence_slice,
        knowledge_hits=knowledge_hits,
        runtime_snapshot_present=snap_ok,
        suggested_target_candidates=tag_candidates,
        conversation_context=conversation_context,
        answer_style=answer_style,
    )
    evidence["sequence_semantics"] = sequence_semantics

    if not enable_llm:
        warnings.append("llm_assist_disabled")

    ps = trace.platform_specific or {}
    if ps.get("runtime_evaluation_used"):
        trust = assess_runtime_confidence(trace)
    else:
        trust = assess_trace_confidence(trace, relationships)
    evidence["trust_assessment"] = trust.model_dump(mode="json")
    if ps.get("runtime_evaluation_used") and evidence.get("runtime_verdict"):
        conf = "high" if trust.confidence_score >= 0.8 else "medium"
    elif target_resolution == "deterministic_match" and (trace.summary or trace.conclusions):
        conf = "high" if trust.confidence_score >= 0.8 else "medium"
    elif target_resolution == "deterministic_match":
        conf = "medium"
    else:
        conf = "low"

    if evidence.get("unsupported_conditions"):
        warnings.append("runtime_or_trace_contains_unsupported_conditions")

    answer = provider.generate_answer(INTELLI_LLM_SYSTEM_PROMPT, evidence)

    allowed = _known_tag_names(control_objects)
    extra_warnings = _forbidden_hallucination_check(answer, allowed)
    warnings.extend(extra_warnings)

    evidence_used = {k: v for k, v in evidence.items() if not str(k).startswith("_")}
    trace.platform_specific = dict(trace.platform_specific or {})
    trace.platform_specific["ask_v3_context"] = dict(conversation_context or {})
    trace.platform_specific["ask_v3_answer_style"] = answer_style
    trace.platform_specific["ask_v3_trust_assessment"] = trust.model_dump(mode="json")

    return {
        "answer": answer,
        "confidence": conf,
        "target_object_id": evidence.get("target_object_id"),
        "detected_intent": intent,
        "evidence_used": evidence_used,
        "warnings": warnings,
        "deterministic_result": trace,
    }


__all__ = [
    "INTELLI_LLM_SYSTEM_PROMPT",
    "answer_with_llm_assist",
    "build_structured_evidence_package",
]
