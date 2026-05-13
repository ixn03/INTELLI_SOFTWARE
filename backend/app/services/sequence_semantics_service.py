from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from pydantic import BaseModel, Field

from app.models.reasoning import ControlObject, ExecutionContext, Relationship
from app.services.runtime_ingestion_service import normalize_runtime_snapshot, snapshot_to_flat_values
from app.services.sequence_reasoning_service import analyze_sequences
from app.services.trustworthiness_service import assess_sequence_confidence


class SequenceSemanticSummary(BaseModel):
    current_possible_states: list[dict[str, Any]] = Field(default_factory=list)
    likely_waiting_conditions: list[dict[str, Any]] = Field(default_factory=list)
    transition_conditions: list[dict[str, Any]] = Field(default_factory=list)
    fault_conditions: list[dict[str, Any]] = Field(default_factory=list)
    manual_override_conditions: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = 0.5
    unsupported_patterns: list[dict[str, Any]] = Field(default_factory=list)


_WAIT_RE = re.compile(r"\b(wait|waiting|hold|ready|complete|done|dn|permit|permissive)\b", re.I)
_FAULT_RE = re.compile(r"\b(fault\w*|fail\w*|trip\w*|alarm\w*|interlock\w*|estop)\b", re.I)
_MANUAL_RE = re.compile(r"\b(manual|auto|operator|start|stop|reset|override|cmd|command)\b", re.I)
_TIMEOUT_RE = re.compile(r"\b(timeout|timer|\.dn|\.acc|\.pre|ton|tof|rto)\b", re.I)


def _runtime_values(runtime_snapshot: Optional[Any]) -> dict[str, Any]:
    if runtime_snapshot is None:
        return {}
    try:
        model = normalize_runtime_snapshot(runtime_snapshot)
        return snapshot_to_flat_values(model)
    except Exception:
        return {}


def _tag_name_from_id(tag_id: str) -> str:
    return tag_id.split("/")[-1].split(".")[-1].split("::")[-1]


def _condition_text(transition: dict[str, Any]) -> str:
    parts = [
        str(transition.get("condition_summary") or ""),
        str(transition.get("source_location") or ""),
        str(transition.get("writer_instruction_type") or ""),
    ]
    evidence = transition.get("evidence") or {}
    if isinstance(evidence, dict):
        parts.extend(str(v) for v in evidence.values() if v is not None)
    return " ".join(parts)


def analyze_sequence_semantics(
    control_objects: Sequence[ControlObject],
    relationships: Sequence[Relationship],
    execution_contexts: Optional[Sequence[ExecutionContext]] = None,
    runtime_snapshot: Optional[Any] = None,
) -> SequenceSemanticSummary:
    sequence = analyze_sequences(control_objects, relationships, execution_contexts or [])
    runtime = _runtime_values(runtime_snapshot)
    current_states: list[dict[str, Any]] = []
    for cand in sequence.get("state_candidates") or []:
        name = cand.get("tag_name") or _tag_name_from_id(str(cand.get("tag_id")))
        matched_key = None
        value = None
        for key, runtime_value in runtime.items():
            if key == name or key.endswith(f".{name}") or key.endswith(f"/{name}"):
                matched_key = key
                value = runtime_value
                break
        current_states.append(
            {
                "state_tag_id": cand.get("tag_id"),
                "state_tag_name": name,
                "runtime_snapshot_key": matched_key,
                "runtime_value": value,
                "confidence": "high" if matched_key is not None else cand.get("confidence", "medium"),
                "evidence": cand,
            }
        )

    waiting: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    faults: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    unsupported = list(sequence.get("unsupported_sequence_patterns") or [])

    for transition in sequence.get("state_transitions") or []:
        text = _condition_text(transition)
        row = {
            "state_tag_id": transition.get("state_tag"),
            "state_tag_name": transition.get("state_tag_name"),
            "target_state": transition.get("target_state"),
            "condition_summary": transition.get("condition_summary"),
            "source_location": transition.get("source_location"),
            "confidence": transition.get("confidence"),
            "deterministic": True,
        }
        transitions.append(row)
        if _WAIT_RE.search(text):
            waiting.append({**row, "reason": "condition text references waiting/completion/permissive pattern"})
        if _FAULT_RE.search(text):
            faults.append({**row, "reason": "condition text references fault/interlock/alarm pattern"})
        if _MANUAL_RE.search(text):
            manual.append({**row, "reason": "condition text references operator/auto/manual command pattern"})
        if _TIMEOUT_RE.search(text):
            waiting.append({**row, "reason": "condition text references timer/timeout/completion pattern"})

    trust = assess_sequence_confidence(sequence)
    return SequenceSemanticSummary(
        current_possible_states=current_states,
        likely_waiting_conditions=waiting,
        transition_conditions=transitions,
        fault_conditions=faults,
        manual_override_conditions=manual,
        confidence=trust.confidence_score,
        unsupported_patterns=unsupported,
    )


__all__ = ["SequenceSemanticSummary", "analyze_sequence_semantics"]
