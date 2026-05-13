from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.reasoning import ControlObject, Relationship, RelationshipType
from app.services.evidence_service import build_version_diff_evidence
from app.services.version_compare_service import compare_projects


@dataclass
class VersionImpactSummary:
    operationally_significant_changes: list[str] = field(default_factory=list)
    possible_runtime_impacts: list[str] = field(default_factory=list)
    affected_equipment: list[str] = field(default_factory=list)
    affected_sequences: list[str] = field(default_factory=list)
    changed_states: list[str] = field(default_factory=list)
    changed_fault_behavior: list[str] = field(default_factory=list)
    risk_level: str = "low"
    confidence: float = 0.5
    evidence: dict[str, Any] = field(default_factory=dict)


def _obj_name(index: dict[str, ControlObject], obj_id: str) -> str:
    obj = index.get(obj_id)
    return (obj.name if obj and obj.name else obj_id).split("::")[-1]


def _rel_key(rel: Relationship) -> tuple[str, str, str, str | None, str | None, str | None]:
    ps = rel.platform_specific or {}
    return (
        rel.source_id,
        rel.target_id,
        rel.relationship_type.value,
        rel.write_behavior.value if rel.write_behavior else None,
        str(ps.get("instruction_type") or ""),
        str(ps.get("comparison_operator") or ""),
    )


def _state_like(name: str) -> bool:
    low = name.lower()
    return any(part in low for part in ("state", "step", "phase", "mode", "seq"))


def _fault_like(name: str) -> bool:
    low = name.lower()
    return any(part in low for part in ("fault", "alarm", "trip", "interlock", "estop"))


def _equipment_hint(name: str) -> str | None:
    if "_" in name:
        return name.split("_", 1)[0]
    if "." in name:
        return name.split(".", 1)[0]
    return None


def analyze_version_impact(
    old_normalized: dict[str, Any],
    new_normalized: dict[str, Any],
) -> VersionImpactSummary:
    diff = compare_projects(old_normalized, new_normalized)
    old_objs = {o.id: o for o in old_normalized["control_objects"]}
    new_objs = {o.id: o for o in new_normalized["control_objects"]}
    new_rels: list[Relationship] = new_normalized["relationships"]
    old_rels: list[Relationship] = old_normalized["relationships"]
    old_set = {_rel_key(r): r for r in old_rels}
    new_set = {_rel_key(r): r for r in new_rels}
    added = [new_set[k] for k in sorted(set(new_set) - set(old_set))]
    removed = [old_set[k] for k in sorted(set(old_set) - set(new_set))]

    changes: list[str] = []
    impacts: list[str] = []
    equipment: set[str] = set()
    sequences: set[str] = set()
    states: set[str] = set()
    faults: list[str] = []

    for rel, verb in [(r, "now requires") for r in added] + [
        (r, "no longer requires") for r in removed
    ]:
        idx = new_objs if verb == "now requires" else old_objs
        target = _obj_name(idx, rel.target_id)
        source = _obj_name(idx, rel.source_id)
        rel_type = rel.relationship_type
        ps = rel.platform_specific or {}
        instr = str(ps.get("instruction_type") or "")
        if rel_type == RelationshipType.READS:
            msg = f"{target} {verb} {source} via {instr or 'read condition'}."
            changes.append(msg)
            impacts.append(f"Runtime behavior for {target} may change when {source} changes.")
        elif rel_type == RelationshipType.WRITES:
            msg = f"Writer for {target} changed at {rel.source_location or rel.source_id}."
            changes.append(msg)
            impacts.append(f"{target} may now be driven by a different logic path.")
        elif rel_type in {RelationshipType.SEQUENCES, RelationshipType.CONDITION_FOR}:
            msg = f"Sequence relationship changed for {target}."
            changes.append(msg)
            sequences.add(target)
        if instr in {"TON", "TOF", "RTO"} or "preset" in str(ps).lower():
            changes.append(f"Timer-related behavior changed near {target}.")
        if ps.get("comparison_operator"):
            changes.append(
                f"Comparison for {target} changed using operator {ps.get('comparison_operator')}."
            )
        if _state_like(target):
            states.add(target)
            sequences.add(target)
        if _fault_like(target) or _fault_like(source):
            faults.append(f"Fault/interlock path changed for {target} using {source}.")
        hint = _equipment_hint(target)
        if hint:
            equipment.add(hint)

    evidence = build_version_diff_evidence(diff).model_dump(mode="json")
    risk = "low"
    if faults or len(changes) >= 6:
        risk = "high"
    elif changes:
        risk = "medium"
    confidence = 0.82 if changes else 0.9
    if any("unsupported" in str(x).lower() for x in evidence.get("warnings", [])):
        confidence = min(confidence, 0.55)
    return VersionImpactSummary(
        operationally_significant_changes=changes,
        possible_runtime_impacts=impacts,
        affected_equipment=sorted(equipment),
        affected_sequences=sorted(sequences),
        changed_states=sorted(states),
        changed_fault_behavior=faults,
        risk_level=risk,
        confidence=confidence,
        evidence=evidence,
    )


__all__ = ["VersionImpactSummary", "analyze_version_impact"]
