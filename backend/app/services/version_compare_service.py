"""Compare two normalized project snapshots (design-time, v1).

No live PLC or database — pure structural diff for tags, routines,
relationships, and coarse writer/condition changes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Optional

from app.models.reasoning import (
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
)


@dataclass
class VersionDiffResult:
    summary: str
    changed_objects: list[dict[str, Any]] = field(default_factory=list)
    changed_relationships: list[dict[str, Any]] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    recommended_checks: list[str] = field(default_factory=list)


def _routine_logic_key(o: ControlObject) -> Optional[str]:
    if o.object_type != ControlObjectType.ROUTINE:
        return None
    ps = o.platform_specific or {}
    meta = ps.get("rockwell_metadata") or {}
    h = meta.get("raw_logic_hash") or meta.get("raw_logic_sha256")
    if h:
        return str(h)
    raw = ps.get("raw_logic_present")
    if raw is True:
        return "__present_no_hash__"
    return None


def compare_projects(
    old_normalized: dict[str, Any],
    new_normalized: dict[str, Any],
) -> VersionDiffResult:
    old_cos: list[ControlObject] = old_normalized["control_objects"]
    new_cos: list[ControlObject] = new_normalized["control_objects"]
    old_rels: list[Relationship] = old_normalized["relationships"]
    new_rels: list[Relationship] = new_normalized["relationships"]

    old_tags = {o.id: o for o in old_cos if o.object_type == ControlObjectType.TAG}
    new_tags = {o.id: o for o in new_cos if o.object_type == ControlObjectType.TAG}
    old_routines = {
        o.id: o for o in old_cos if o.object_type == ControlObjectType.ROUTINE
    }
    new_routines = {
        o.id: o for o in new_cos if o.object_type == ControlObjectType.ROUTINE
    }

    changed_objects: list[dict[str, Any]] = []
    risk_flags: list[str] = []
    checks: list[str] = []

    added_tags = sorted(set(new_tags) - set(old_tags))
    removed_tags = sorted(set(old_tags) - set(new_tags))
    for tid in added_tags:
        changed_objects.append({"change": "tag_added", "id": tid, "name": new_tags[tid].name})
    for tid in removed_tags:
        changed_objects.append({"change": "tag_removed", "id": tid, "name": old_tags[tid].name})

    common_tags = set(old_tags) & set(new_tags)
    for tid in sorted(common_tags):
        o, n = old_tags[tid], new_tags[tid]
        odt = (o.attributes or {}).get("data_type")
        ndt = (n.attributes or {}).get("data_type")
        if odt != ndt:
            changed_objects.append(
                {
                    "change": "tag_data_type_changed",
                    "id": tid,
                    "old": odt,
                    "new": ndt,
                }
            )
            risk_flags.append(f"data_type_change:{tid}")

    added_routines = sorted(set(new_routines) - set(old_routines))
    removed_routines = sorted(set(old_routines) - set(new_routines))
    for rid in added_routines:
        changed_objects.append(
            {"change": "routine_added", "id": rid, "name": new_routines[rid].name}
        )
    for rid in removed_routines:
        changed_objects.append(
            {"change": "routine_removed", "id": rid, "name": old_routines[rid].name}
        )

    for rid in sorted(set(old_routines) & set(new_routines)):
        o, n = old_routines[rid], new_routines[rid]
        olang = (o.attributes or {}).get("language")
        nlang = (n.attributes or {}).get("language")
        if olang != nlang:
            changed_objects.append(
                {
                    "change": "routine_language_changed",
                    "id": rid,
                    "old": olang,
                    "new": nlang,
                }
            )
        oh = _routine_logic_key(o)
        nh = _routine_logic_key(n)
        if oh and nh and oh != nh:
            changed_objects.append(
                {"change": "routine_raw_logic_hash_changed", "id": rid}
            )
            risk_flags.append(f"logic_rewrite:{rid}")
            checks.append(f"Regression-test {rid} after logic change.")

    def rel_sig(r: Relationship) -> tuple:
        return (
            r.source_id,
            r.target_id,
            r.relationship_type.value,
            r.write_behavior.value if r.write_behavior else None,
        )

    old_rel_set = {rel_sig(r) for r in old_rels}
    new_rel_set = {rel_sig(r) for r in new_rels}
    added_rels = new_rel_set - old_rel_set
    removed_rels = old_rel_set - new_rel_set

    changed_relationships: list[dict[str, Any]] = []
    for sig in sorted(added_rels):
        changed_relationships.append({"change": "relationship_added", "edge": sig})
    for sig in sorted(removed_rels):
        changed_relationships.append({"change": "relationship_removed", "edge": sig})

    if added_rels or removed_rels:
        risk_flags.append("relationship_graph_changed")

    # Writers / conditions per target tag (WRITES + READS heuristic)
    writers_old = _writers_by_target(old_rels)
    writers_new = _writers_by_target(new_rels)
    readers_old = _readers_by_target(old_rels)
    readers_new = _readers_by_target(new_rels)

    all_targets = set(writers_old) | set(writers_new) | set(readers_old) | set(readers_new)
    for tid in sorted(all_targets):
        if writers_old.get(tid) != writers_new.get(tid):
            changed_relationships.append(
                {
                    "change": "writers_changed",
                    "target_id": tid,
                    "old_sources": sorted(writers_old.get(tid, set())),
                    "new_sources": sorted(writers_new.get(tid, set())),
                }
            )
            checks.append(f"Re-validate drivers of {tid}.")
        if readers_old.get(tid) != readers_new.get(tid):
            changed_relationships.append(
                {
                    "change": "readers_or_conditions_changed",
                    "target_id": tid,
                    "old_sources": sorted(readers_old.get(tid, set())),
                    "new_sources": sorted(readers_new.get(tid, set())),
                }
            )

    summary_parts = [
        f"tags +{len(added_tags)} -{len(removed_tags)}",
        f"routines +{len(added_routines)} -{len(removed_routines)}",
        f"relationships +{len(added_rels)} -{len(removed_rels)}",
        f"object_field_changes {len(changed_objects)}",
    ]
    summary = "; ".join(summary_parts)

    if not changed_objects and not changed_relationships:
        summary = "No structural differences detected (same ids and relationship set)."

    return VersionDiffResult(
        summary=summary,
        changed_objects=changed_objects,
        changed_relationships=changed_relationships,
        risk_flags=sorted(set(risk_flags)),
        recommended_checks=sorted(set(checks))[:20],
    )


def _writers_by_target(rels: list[Relationship]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for r in rels:
        if r.relationship_type == RelationshipType.WRITES:
            out[r.target_id].add(r.source_id)
    return {k: v for k, v in out.items()}


def _readers_by_target(rels: list[Relationship]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for r in rels:
        if r.relationship_type == RelationshipType.READS:
            out[r.target_id].add(r.source_id)
    return {k: v for k, v in out.items()}


__all__ = ["VersionDiffResult", "compare_projects"]
