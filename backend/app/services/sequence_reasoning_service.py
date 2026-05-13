"""Deterministic sequence / state reasoning (v1).

Inspects the normalized reasoning graph for state-machine style tags,
writes, and transitions. Conservative: no source_state inference,
no LLM, no process semantics beyond tag names and Trace v2 wording.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    ExecutionContext,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)
from app.services.trace_v2_service import trace_object_v2

# ---------------------------------------------------------------------------
# Tag naming heuristics (conservative)
# ---------------------------------------------------------------------------

_STATE_EXACT_NAMES = frozenset(
    {
        "State",
        "Step",
        "Phase",
        "Mode",
        "Seq",
        "Sequence",
        "CurrentStep",
        "ActiveStep",
    }
)

_STATE_SUFFIXES = ("_State", "_Step", "_Phase", "_Mode")

_NUMERIC_STATE_TYPES = frozenset(
    {
        "DINT",
        "INT",
        "SINT",
        "LINT",
        "UDINT",
        "UINT",
        "USINT",
        "ULINT",
    }
)

_ENUM_LIKE_TYPES = frozenset({"NAMEDSET", "ENUM"})


def _tag_tail_name(tag_id: str) -> str:
    """Last path segment of a tag id (without scope noise)."""

    if "::" in tag_id:
        _, rest = tag_id.split("::", 1)
        return rest.rsplit("/", 1)[-1]
    return tag_id.rsplit("/", 1)[-1]


def _name_suggests_state(name: Optional[str]) -> bool:
    if not name:
        return False
    n = name.strip()
    if n in _STATE_EXACT_NAMES:
        return True
    for suf in _STATE_SUFFIXES:
        if n.endswith(suf):
            return True
    if re.search(
        r"(?<![A-Za-z0-9_])(?:State|Step|Phase|Mode)(?![A-Za-z0-9_])",
        n,
    ):
        return True
    if re.search(r"(?<![A-Za-z0-9_])Seq(?![A-Za-z0-9_])", n):
        return True
    if "Sequence" in n or "CurrentStep" in n or "ActiveStep" in n:
        return True
    return False


def _data_type_suggests_state(data_type: Optional[str]) -> bool:
    if not data_type:
        return False
    u = str(data_type).strip().upper()
    if u in _NUMERIC_STATE_TYPES:
        return True
    if "NAMEDSET" in u or u.startswith("ENUM") or "ENUM" in u:
        return True
    return False


def _confidence_value(level: ConfidenceLevel) -> str:
    if isinstance(level, ConfidenceLevel):
        return level.value
    return str(level)


def _serialize_relationship(rel: Relationship) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source_id": rel.source_id,
        "target_id": rel.target_id,
        "relationship_type": rel.relationship_type.value,
        "source_location": rel.source_location,
        "write_behavior": (
            rel.write_behavior.value if rel.write_behavior else None
        ),
        "logic_condition": rel.logic_condition,
        "platform_specific": dict(rel.platform_specific or {}),
    }
    if rel.id:
        out["id"] = rel.id
    return out


def _instruction_type_from_writer(writer: Relationship) -> Optional[str]:
    ps = writer.platform_specific or {}
    it = ps.get("instruction_type")
    if isinstance(it, str) and it:
        return it.upper()
    return None


def _find_mov_source_operand(
    writer: Relationship,
    rels_by_source: dict[str, list[Relationship]],
    obj_by_id: dict[str, ControlObject],
) -> Optional[str]:
    """Return MOV/COP source operand string when encoded as a tag READ."""

    ps = writer.platform_specific or {}
    instr_id = ps.get("instruction_id")
    rung_id = writer.source_id
    if not instr_id:
        return None
    for sib in rels_by_source.get(rung_id, []):
        if sib.relationship_type != RelationshipType.READS:
            continue
        sm = sib.platform_specific or {}
        if sm.get("instruction_id") != instr_id:
            continue
        if sm.get("operand_role") != "move_source":
            continue
        tgt = obj_by_id.get(sib.target_id)
        if tgt and tgt.name:
            return tgt.name
        return _tag_tail_name(sib.target_id)
    return None


def _ladder_instruction_operands_for_writer(
    writer: Relationship,
    obj_by_id: dict[str, ControlObject],
) -> Optional[list[str]]:
    """Operands from the ladder INSTRUCTION object for this writer, if any."""

    ps = writer.platform_specific or {}
    instr_id = ps.get("instruction_id")
    rung_id = writer.source_id
    if not instr_id or not rung_id:
        return None
    for obj in obj_by_id.values():
        if obj.object_type != ControlObjectType.INSTRUCTION:
            continue
        if rung_id not in (obj.parent_ids or []):
            continue
        o_ps = obj.platform_specific or {}
        if o_ps.get("instruction_local_id") != instr_id:
            continue
        ops = (obj.attributes or {}).get("operands")
        if isinstance(ops, list):
            return [str(x) for x in ops]
    return None


def _target_state_from_writer(
    writer: Relationship,
    rels_by_source: dict[str, list[Relationship]],
    obj_by_id: dict[str, ControlObject],
) -> tuple[Optional[str], str]:
    """Return (target_state_repr, confidence_bucket).

    ``confidence_bucket`` is ``high`` / ``medium`` / ``low`` for the
    *value* extraction (not the overall transition).
    """

    ps = writer.platform_specific or {}
    itype = (ps.get("instruction_type") or "").upper()
    wb = writer.write_behavior

    # Structured Text assignment / case body
    if ps.get("language") == "structured_text":
        assigned = ps.get("assigned_value")
        if isinstance(assigned, str) and assigned.strip():
            if ps.get("st_parse_status") == "too_complex":
                return assigned.strip(), "low"
            return assigned.strip(), "high"
        raw = ps.get("raw_text")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:120], "low"
        return None, "low"

    if itype == "MOV" or itype == "COP":
        src = _find_mov_source_operand(writer, rels_by_source, obj_by_id)
        if src is not None:
            return src, "high"
        ops = _ladder_instruction_operands_for_writer(writer, obj_by_id)
        if ops and len(ops) >= 1:
            # MOV(Source, Dest) — first operand may be a literal.
            return ops[0], "high"
        return None, "low"

    if wb == WriteBehaviorType.CALCULATES or itype in (
        "ADD",
        "SUB",
        "MUL",
        "DIV",
        "CPT",
    ):
        return "(calculated expression)", "low"

    if wb in (WriteBehaviorType.SETS_TRUE, WriteBehaviorType.SETS_FALSE):
        return "TRUE" if wb == WriteBehaviorType.SETS_TRUE else "FALSE", "medium"

    return None, "low"


def _build_rels_by_source(
    relationships: Sequence[Relationship],
) -> dict[str, list[Relationship]]:
    out: dict[str, list[Relationship]] = {}
    for r in relationships:
        out.setdefault(r.source_id, []).append(r)
    return out


def _trace_condition_by_writer(
    state_tag_id: str,
    writer: Relationship,
    control_objects: Sequence[ControlObject],
    relationships: Sequence[Relationship],
    execution_contexts: Sequence[ExecutionContext],
) -> Optional[str]:
    """Use Trace v2 natural-language for this writer when available."""

    try:
        trace = trace_object_v2(
            target_object_id=state_tag_id,
            control_objects=control_objects,
            relationships=relationships,
            execution_contexts=execution_contexts or [],
        )
    except Exception:
        return None
    ps_root = trace.platform_specific or {}
    try:
        n_natural = int(ps_root.get("natural_conclusion_count", 0))
    except (TypeError, ValueError):
        n_natural = 0
    for c in trace.conclusions[:n_natural]:
        cps = c.platform_specific or {}
        kind = cps.get("trace_v2_kind")
        if kind not in (
            "writer_conditions",
            "st_assignment",
            "st_too_complex",
        ):
            continue
        subs = c.subject_ids or []
        if writer.source_id in subs and writer.target_id in subs:
            # Prefer the conditions clause over ST assignment when both exist.
            if kind == "writer_conditions":
                return c.statement
    for c in trace.conclusions[:n_natural]:
        cps = c.platform_specific or {}
        kind = cps.get("trace_v2_kind")
        if kind == "st_assignment":
            subs = c.subject_ids or []
            if writer.source_id in subs and writer.target_id in subs:
                return c.statement
    for c in trace.conclusions[:n_natural]:
        cps = c.platform_specific or {}
        if cps.get("trace_v2_kind") == "st_too_complex":
            subs = c.subject_ids or []
            if writer.source_id in subs and writer.target_id in subs:
                return c.statement
    return None


def _transition_confidence(
    state_candidate_confidence: str,
    value_conf_bucket: str,
    has_trace_condition: bool,
) -> str:
    """Overall transition confidence (string enum)."""

    if value_conf_bucket == "low":
        return "low"
    if state_candidate_confidence in ("very_low", "low"):
        return "low"
    if (
        has_trace_condition
        and state_candidate_confidence in ("high", "very_high")
        and value_conf_bucket == "high"
    ):
        return "high"
    if has_trace_condition and value_conf_bucket == "high":
        return "medium"
    if not has_trace_condition and value_conf_bucket == "high":
        return "medium"
    return "low"


def analyze_sequences(
    control_objects: Sequence[ControlObject],
    relationships: Sequence[Relationship],
    execution_contexts: Optional[Sequence[ExecutionContext]] = None,
) -> dict[str, Any]:
    """Analyze normalized logic for sequence / state behavior.

    Returns JSON-serializable dict with keys:

    * ``state_candidates`` — inferred state-like tags
    * ``state_transitions`` — writes that assign a next value / state
    * ``case_branches`` — ST CASE branch metadata
    * ``sequence_summary`` — one-line summaries per state tag
    * ``unsupported_sequence_patterns`` — conservative unsupported notes
    """

    obj_by_id: dict[str, ControlObject] = {o.id: o for o in control_objects}
    rels_by_source = _build_rels_by_source(relationships)
    exec_ctx = execution_contexts or []

    candidates: dict[str, dict[str, Any]] = {}

    def add_candidate(
        tag_id: str,
        *,
        reason: str,
        confidence: ConfidenceLevel,
        evidence_loc: Optional[str],
    ) -> None:
        cur = candidates.get(tag_id)
        entry = {
            "tag_id": tag_id,
            "tag_name": (
                (obj_by_id[tag_id].name if tag_id in obj_by_id else None)
                or _tag_tail_name(tag_id)
            ),
            "reason": reason,
            "confidence": _confidence_value(confidence),
            "evidence_locations": [],
        }
        if evidence_loc:
            entry["evidence_locations"].append(evidence_loc)
        if cur is None:
            candidates[tag_id] = entry
            return
        # Merge: keep highest confidence, union evidence, combine reasons.
        rank = {
            "very_low": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
            "very_high": 4,
            "unknown": 1,
        }
        if rank.get(entry["confidence"], 1) > rank.get(cur["confidence"], 1):
            cur["confidence"] = entry["confidence"]
        if reason not in str(cur["reason"]):
            cur["reason"] = f"{cur['reason']}; {reason}"
        if evidence_loc and evidence_loc not in cur["evidence_locations"]:
            cur["evidence_locations"].append(evidence_loc)

    # --- Pass 1: tag objects (name + data type) -------------------------
    for obj in control_objects:
        if obj.object_type != ControlObjectType.TAG:
            continue
        dt = (obj.attributes or {}).get("data_type")
        name = obj.name or _tag_tail_name(obj.id)
        name_hit = _name_suggests_state(name)
        dt_hit = _data_type_suggests_state(dt)
        # Data types alone are too broad (e.g. any DINT); only combine
        # dtype evidence with a name hit here. Graph-driven passes still
        # promote CASE selectors / comparisons independently.
        if not name_hit:
            continue
        reasons: list[str] = [
            "tag name matches state/step/phase/mode/sequence heuristic",
        ]
        conf = ConfidenceLevel.MEDIUM
        if dt_hit:
            reasons.append(
                f"data type {dt!r} is common for discrete state values"
            )
            conf = ConfidenceLevel.HIGH
        add_candidate(
            obj.id,
            reason="; ".join(reasons),
            confidence=conf,
            evidence_loc=obj.source_location,
        )

    # --- Pass 2: graph signals (CASE selector, ladder comparison) -------
    for rel in relationships:
        ps = rel.platform_specific or {}
        loc = rel.source_location
        if rel.relationship_type == RelationshipType.READS:
            if ps.get("condition_source") == "case_selector" and ps.get(
                "instruction_type"
            ) == "CASE_SELECTOR":
                add_candidate(
                    rel.target_id,
                    reason="ST CASE selector reads this tag (strong state candidate)",
                    confidence=ConfidenceLevel.HIGH,
                    evidence_loc=loc,
                )
            gk = ps.get("gating_kind")
            it = (ps.get("instruction_type") or "").upper()
            if gk == "comparison" and it in (
                "EQU",
                "NEQ",
                "LES",
                "LEQ",
                "GRT",
                "GEQ",
                "LIM",
            ):
                tgt = obj_by_id.get(rel.target_id)
                tname = (tgt.name if tgt else None) or _tag_tail_name(
                    rel.target_id
                )
                if _name_suggests_state(tname):
                    add_candidate(
                        rel.target_id,
                        reason=(
                            f"ladder comparison ({it}) uses this tag — "
                            "consistent with state decoding"
                        ),
                        confidence=ConfidenceLevel.HIGH,
                        evidence_loc=loc,
                    )

    transitions: list[dict[str, Any]] = []
    case_branches: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []

    trace_cache: dict[str, dict[tuple[str, str, Any], str]] = {}

    def get_condition(state_tag: str, writer: Relationship) -> Optional[str]:
        key = (
            writer.source_id,
            str(_instruction_type_from_writer(writer)),
            (writer.platform_specific or {}).get("instruction_id"),
        )
        if state_tag not in trace_cache:
            trace_cache[state_tag] = {}
        bucket = trace_cache[state_tag]
        if key in bucket:
            return bucket[key]
        stmt = _trace_condition_by_writer(
            state_tag,
            writer,
            control_objects,
            relationships,
            exec_ctx,
        )
        bucket[key] = stmt
        return stmt

    # --- CASE branches (ST) ---------------------------------------------
    seen_branch: set[tuple[str, str, str]] = set()
    for rel in relationships:
        ps = rel.platform_specific or {}
        if ps.get("language") != "structured_text":
            continue
        if ps.get("statement_type") != "case":
            continue
        summary = ps.get("case_condition_summary")
        if not summary:
            continue
        stmt_id = rel.source_id
        branch_label = ps.get("branch_label")
        sel_reads = [
            r
            for r in relationships
            if r.source_id == stmt_id
            and r.relationship_type == RelationshipType.READS
            and (r.platform_specific or {}).get("instruction_type")
            == "CASE_SELECTOR"
            and (r.platform_specific or {}).get("case_condition_summary")
            == summary
        ]
        selector_id = sel_reads[0].target_id if sel_reads else None
        selector_name = (
            obj_by_id[selector_id].name
            if selector_id and selector_id in obj_by_id
            else None
        ) or (
            _tag_tail_name(selector_id) if selector_id else None
        )
        key = (stmt_id, str(summary), str(branch_label))
        if key in seen_branch:
            continue
        seen_branch.add(key)
        case_branches.append(
            {
                "statement_id": stmt_id,
                "selector_tag_id": selector_id,
                "selector_tag_name": selector_name,
                "branch_label": branch_label,
                "case_condition_summary": summary,
                "source_location": rel.source_location,
                "relationship_type": rel.relationship_type.value,
            }
        )

    # --- Transitions from WRITES to state candidates --------------------
    candidate_ids = set(candidates.keys())

    for rel in relationships:
        if rel.relationship_type != RelationshipType.WRITES:
            continue
        tgt_id = rel.target_id
        if tgt_id not in candidate_ids:
            continue
        ps = rel.platform_specific or {}
        itype = _instruction_type_from_writer(rel) or ""

        # OTE/OTL to a state-shaped BOOL is unusual; still record as unsupported
        # if it's not a value-style write.
        if ps.get("language") == "structured_text":
            if ps.get("st_parse_status") == "too_complex":
                unsupported.append(
                    {
                        "kind": "st_too_complex_write",
                        "state_tag_id": tgt_id,
                        "source_location": rel.source_location,
                        "detail": "Structured Text write to a state candidate is too complex to summarize.",
                    }
                )
        wb = rel.write_behavior
        is_calc = wb == WriteBehaviorType.CALCULATES or itype in (
            "CPT",
            "ADD",
            "SUB",
            "MUL",
            "DIV",
        )
        if is_calc:
            unsupported.append(
                {
                    "kind": "calculated_state_write",
                    "state_tag_id": tgt_id,
                    "source_location": rel.source_location,
                    "instruction_type": itype or None,
                    "detail": (
                        "Math / compute style write to a state candidate; "
                        "target state not extracted deterministically."
                    ),
                }
            )

        tgt_val, val_bucket = _target_state_from_writer(
            rel, rels_by_source, obj_by_id
        )
        if is_calc:
            val_bucket = "low"
        if tgt_val is None:
            unsupported.append(
                {
                    "kind": "unknown_state_write_value",
                    "state_tag_id": tgt_id,
                    "source_location": rel.source_location,
                    "instruction_type": itype or None,
                    "detail": "Could not determine the assigned state value.",
                }
            )
            continue

        # Non-state MOV (candidate matched only by weak signal): user asked
        # that arbitrary MOV to non-state not become a transition — enforced
        # by requiring membership in candidate_ids; weak candidates still
        # get transitions only if they're in the set. Optionally filter MOV
        # where destination is clearly not state: we only have candidates.

        cond = get_condition(tgt_id, rel)
        cand_conf = candidates[tgt_id]["confidence"]
        overall = _transition_confidence(cand_conf, val_bucket, bool(cond))

        meta_ps = dict(ps)
        case_summary = meta_ps.get("case_condition_summary")

        transitions.append(
            {
                "state_tag": tgt_id,
                "state_tag_name": candidates[tgt_id]["tag_name"],
                "source_state": None,
                "target_state": tgt_val,
                "condition_summary": cond,
                "source_location": rel.source_location,
                "writer_instruction_type": itype or ps.get("instruction_type"),
                "writer_relationship": _serialize_relationship(rel),
                "confidence": overall,
                "evidence": {
                    "case_condition_summary": case_summary,
                    "assigned_value_meta": meta_ps.get("assigned_value"),
                    "trace_v2_condition_available": bool(cond),
                    "value_extraction_confidence": val_bucket,
                },
            }
        )

    # --- Sequence summary lines -----------------------------------------
    summary_lines: list[str] = []
    by_tag: dict[str, list[dict[str, Any]]] = {}
    for t in transitions:
        by_tag.setdefault(t["state_tag"], []).append(t)
    for tag_id, cand in sorted(
        candidates.items(), key=lambda x: x[1]["tag_name"]
    ):
        n = len(by_tag.get(tag_id, []))
        if n:
            summary_lines.append(
                f"{cand['tag_name']}: {n} state transition(s) detected "
                f"(confidence {cand['confidence']})."
            )
        elif cand["confidence"] in ("high", "very_high"):
            summary_lines.append(
                f"{cand['tag_name']}: state-like tag; no qualifying writes found."
            )

    return {
        "state_candidates": list(candidates.values()),
        "state_transitions": transitions,
        "case_branches": case_branches,
        "sequence_summary": summary_lines,
        "unsupported_sequence_patterns": unsupported,
    }


def filter_sequence_result_for_tag(
    full: dict[str, Any],
    state_tag_id: str,
) -> dict[str, Any]:
    """Restrict a full ``analyze_sequences`` output to one state tag."""

    transitions = [
        t for t in full["state_transitions"] if t["state_tag"] == state_tag_id
    ]
    cands = [c for c in full["state_candidates"] if c["tag_id"] == state_tag_id]
    name = cands[0]["tag_name"] if cands else _tag_tail_name(state_tag_id)
    conf = cands[0]["confidence"] if cands else "unknown"
    summary_lines: list[str] = []
    if transitions:
        summary_lines.append(
            f"{name}: {len(transitions)} state transition(s) detected "
            f"(confidence {conf})."
        )
    elif cands and cands[0]["confidence"] in ("high", "very_high"):
        summary_lines.append(
            f"{name}: state-like tag; no qualifying writes found."
        )

    return {
        "state_tag_id": state_tag_id,
        "state_candidates": cands,
        "state_transitions": transitions,
        "case_branches": [
            b
            for b in full["case_branches"]
            if b.get("selector_tag_id") == state_tag_id
        ],
        "sequence_summary": summary_lines,
        "unsupported_sequence_patterns": [
            u
            for u in full["unsupported_sequence_patterns"]
            if u.get("state_tag_id") == state_tag_id
        ],
    }
