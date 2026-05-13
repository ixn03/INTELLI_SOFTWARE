"""Trace v2 -- natural-language, condition-aware reasoning trace.

Trace v2 reuses :func:`app.services.trace_service.trace_object` (Trace
v1) for graph traversal and adds a richer **natural-language** layer
on top of it:

* per-writer "WHAT" statements (e.g. ``"State_Fill is latched ON in
  R02_Sequencer/Rung[1]."``) phrased per-instruction.
* per-writer "CONDITIONS" statements (e.g. ``"State_Fill is latched ON
  in R02_Sequencer/Rung[1] when StartPB_OS is TRUE, AutoMode is TRUE,
  and Faults_Any is FALSE."``).
* for Structured Text writers, a best-effort assignment summary via
  :mod:`app.services.structured_text_extraction`. Anything outside the
  supported envelope is reported as the canonical "too complex" line.

The returned :class:`TraceResult` keeps the same shape as Trace v1:
writers, readers, upstream/downstream id lists, recommended_checks,
and failure_impact are passed through unchanged. Only ``conclusions``
and ``summary`` are augmented (natural-language statements first,
followed by the original Trace v1 conclusions for transparency).

Determinism / scope
-------------------

* No LLM, no network, no I/O. Same input -> same output.
* Only the instruction families listed in :data:`_WHAT_PHRASE` /
  :data:`_CONDITIONS_VERB` get personalized phrasing; everything else
  falls back to a generic "is written in <loc> using <INSTR>." line.
* Ladder conditions come from the normalized READS edges first,
  falling back to a regex scan of :pyattr:`Relationship.logic_condition`
  via :func:`extract_simple_ladder_conditions`.

Public API
----------

* :func:`humanize_instruction_type`
* :func:`extract_simple_ladder_conditions`
* :func:`trace_object_v2`
* :class:`LadderConditionExtraction`
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    ExecutionContext,
    Relationship,
    RelationshipType,
    TraceResult,
    TruthConclusion,
    TruthContextType,
)
from app.services.structured_text_extraction import (
    STExtractionResult,
    extract_simple_st_conditions,
)
from app.services.trace_service import (
    format_relationship_detail,
    trace_object,
)


# ===========================================================================
# Part 1 -- Instruction-level natural-language phrasing
# ===========================================================================
#
# Two phrase tables per instruction:
#
#   _INSTR_GENERIC[itype]      -> sentence fragment with no target
#   _INSTR_WITH_TARGET[itype]  -> sentence fragment containing {target}
#
# Both phrase tables intentionally mirror the wording in the Trace v2
# spec. Keep them aligned: changing one without the other will produce
# inconsistent "with target" vs "without target" output.
# ===========================================================================

_INSTR_GENERIC: dict[str, str] = {
    "XIC": "requires the tag to be TRUE",
    "XIO": "requires the tag to be FALSE",
    "OTE": "turns the output ON while the rung is true",
    "OTL": "latches the output ON",
    "OTU": "unlatches or resets the output OFF",
    "TON": "runs an on-delay timer",
    "TOF": "runs an off-delay timer",
    "RTO": "runs a retentive timer",
    "CTU": "counts up",
    "CTD": "counts down",
    "RES": "resets the target",
    "JSR": "calls another routine",
    "ONS": "fires a one-scan pulse",
    "OSR": "fires a rising-edge one-shot",
    "OSF": "fires a falling-edge one-shot",
    "MOV": "copies a value to its destination",
    "COP": "copies an array of values to its destination",
    "ADD": "stores the sum of two operands",
    "SUB": "stores the difference of two operands",
    "MUL": "stores the product of two operands",
    "DIV": "stores the quotient of two operands",
    "CPT": "evaluates a compute expression",
    # Comparisons are reads-only, but the phrase is still useful when a
    # caller asks for a humanized form (e.g. summary rendering).
    "EQU": "requires the two operands to be equal",
    "NEQ": "requires the two operands to be unequal",
    "GRT": "requires the first operand to be greater than the second",
    "GEQ": (
        "requires the first operand to be greater than or equal to "
        "the second"
    ),
    "LES": "requires the first operand to be less than the second",
    "LEQ": (
        "requires the first operand to be less than or equal to the "
        "second"
    ),
    "LIM": "requires the test operand to be inside the low/high range",
}

_INSTR_WITH_TARGET: dict[str, str] = {
    "XIC": "requires {target} to be TRUE",
    "XIO": "requires {target} to be FALSE",
    "OTE": "turns {target} ON while the rung is true",
    "OTL": "latches {target} ON",
    "OTU": "unlatches or resets {target} OFF",
    "TON": "runs an on-delay timer on {target}",
    "TOF": "runs an off-delay timer on {target}",
    "RTO": "runs a retentive timer on {target}",
    "CTU": "counts up on {target}",
    "CTD": "counts down on {target}",
    "RES": "resets {target}",
    "JSR": "calls another routine ({target})",
    "ONS": "fires a one-scan pulse on {target}",
    "OSR": "fires a rising-edge one-shot on {target}",
    "OSF": "fires a falling-edge one-shot on {target}",
    "MOV": "loads {target} from a source value",
    "COP": "copies an array of values into {target}",
    "ADD": "stores the sum of two operands in {target}",
    "SUB": "stores the difference of two operands in {target}",
    "MUL": "stores the product of two operands in {target}",
    "DIV": "stores the quotient of two operands in {target}",
    "CPT": "stores the result of a compute expression in {target}",
}


# ---------------------------------------------------------------------------
# Translation tables consumed by the condition / writer phrasers
# ---------------------------------------------------------------------------


# Maps a Rockwell timer/counter member suffix (the value of
# ``platform_specific["member"]`` on a normalized relationship) to the
# noun phrase used in human-readable trace output. Mirrors the user
# spec for member rendering.
_MEMBER_PHRASES: dict[str, str] = {
    "DN": "timer done bit",
    "TT": "timer timing bit",
    "EN": "timer enabled bit",
    "ACC": "accumulated value",
    "PRE": "preset value",
}


# Comparison-operator -> conditional verb fragment used inside a
# "<lhs> <verb> <rhs>" sentence. Mirrors the spec phrasing.
_COMPARISON_VERB: dict[str, str] = {
    "=": "must equal",
    "<>": "must not equal",
    ">": "must be greater than",
    ">=": "must be greater than or equal to",
    "<": "must be less than",
    "<=": "must be less than or equal to",
}


# Math-operator -> verb fragment used inside a "<target> is calculated
# from <a> <op> <b>" sentence.
_MATH_OP_VERB: dict[str, str] = {
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "/",
    # ``expression`` is the placeholder operator we use for CPT
    # because its source expression isn't cracked by the parser.
    "expression": "expression",
}


def humanize_instruction_type(
    instruction_type: Optional[str],
    target_name: Optional[str] = None,
) -> str:
    """Return a plain-English phrase for a Rockwell instruction.

    Examples (with ``target_name=None``):

        >>> humanize_instruction_type("OTL")
        'latches the output ON'
        >>> humanize_instruction_type("XIC")
        'requires the tag to be TRUE'

    Examples (with a target):

        >>> humanize_instruction_type("OTL", "State_Fill")
        'latches State_Fill ON'
        >>> humanize_instruction_type("XIO", "Faults_Any")
        'requires Faults_Any to be FALSE'

    Unknown / unregistered instructions get a graceful fallback so
    callers can still emit something readable:

        >>> humanize_instruction_type("CMP", "X")
        'executes CMP on X'
        >>> humanize_instruction_type(None)
        ''
    """

    if not instruction_type:
        return ""
    itype = instruction_type.upper()

    if target_name:
        target_form = _INSTR_WITH_TARGET.get(itype)
        if target_form:
            return target_form.format(target=target_name)

    generic = _INSTR_GENERIC.get(itype)
    if generic:
        return generic

    if target_name:
        return f"executes {itype} on {target_name}"
    return f"executes {itype}"


# ===========================================================================
# Part 2 -- Simple ladder condition extraction
# ===========================================================================


@dataclass(frozen=True)
class LadderConditionExtraction:
    """A single XIC / XIO operand parsed from a raw rung string.

    ``required_value`` is the boolean value the operand must take for
    the rung condition to evaluate true: ``True`` for ``XIC``,
    ``False`` for ``XIO``.
    """

    tag: str
    required_value: bool
    instruction_type: str
    natural_language: str


# Match `XIC(Tag)` / `XIO(Tag)` with optional whitespace. The inner
# capture allows dots and brackets so member access (``Pump.Run``) and
# bit access (``Bits[3]``) survive. Anything with a literal ``)`` in
# the operand would be malformed Rockwell anyway.
_LADDER_INSTR_RE = re.compile(
    r"\b(?P<itype>XIC|XIO)\s*\(\s*(?P<tag>[^)]+?)\s*\)",
    re.IGNORECASE,
)


def extract_simple_ladder_conditions(
    logic_condition: Optional[str],
) -> list[LadderConditionExtraction]:
    """Pull XIC / XIO patterns out of a raw ladder condition string.

    Example::

        >>> extract_simple_ladder_conditions(
        ...     "XIC(StartPB_OS) XIC(AutoMode) XIO(Faults_Any) "
        ...     "OTL(State_Fill)"
        ... )
        [LadderConditionExtraction(tag='StartPB_OS', required_value=True,
                                   instruction_type='XIC',
                                   natural_language='StartPB_OS must be TRUE'),
         LadderConditionExtraction(tag='AutoMode', required_value=True,
                                   instruction_type='XIC',
                                   natural_language='AutoMode must be TRUE'),
         LadderConditionExtraction(tag='Faults_Any', required_value=False,
                                   instruction_type='XIO',
                                   natural_language='Faults_Any must be FALSE')]

    Output instructions (OTE / OTL / OTU / TON / ...) are intentionally
    NOT extracted: this function is about the gating conditions of a
    rung, not its effects. Order matches the order of occurrence in
    the input string.
    """

    if not logic_condition:
        return []

    out: list[LadderConditionExtraction] = []
    for match in _LADDER_INSTR_RE.finditer(logic_condition):
        itype = match.group("itype").upper()
        tag = match.group("tag").strip()
        if not tag:
            continue
        required = itype == "XIC"
        out.append(
            LadderConditionExtraction(
                tag=tag,
                required_value=required,
                instruction_type=itype,
                natural_language=(
                    f"{tag} must be {'TRUE' if required else 'FALSE'}"
                ),
            )
        )
    return out


# ===========================================================================
# Part 3 (continued) -- writer phrase tables
# ===========================================================================
#
# "WHAT" phrases describe the effect of a single writer relationship
# in isolation: ``<target> is <effect> in <loc>``. They are picked by
# instruction type and substituted with ``target`` / ``loc``.
#
# "CONDITIONS" verbs describe the same effect as a verb phrase that
# slots into ``<target> <verb> in <loc> when ...``: this is what the
# spec asks for in the gating-conditions conclusion.
# ===========================================================================


_WHAT_PHRASE: dict[str, str] = {
    "OTE": "{target} is energized while the rung is TRUE in {loc}.",
    "OTL": "{target} is latched ON in {loc}.",
    "OTU": "{target} is unlatched / reset OFF in {loc}.",
    "TON": "An on-delay timer is run on {target} in {loc}.",
    "TOF": "An off-delay timer is run on {target} in {loc}.",
    "RTO": "A retentive timer is run on {target} in {loc}.",
    "CTU": "A count-up on {target} is performed in {loc}.",
    "CTD": "A count-down on {target} is performed in {loc}.",
    "RES": "{target} is reset in {loc}.",
    "JSR": "{loc} calls routine {target}.",
    # One-shots: the WHAT phrase emphasizes the *pulse* effect; the
    # storage-bit details are surfaced via the CONDITIONS clause when
    # there are sibling READS.
    "ONS": "{target} is pulsed for one scan in {loc}.",
    "OSR": "{target} pulses on a rising edge in {loc}.",
    "OSF": "{target} pulses on a falling edge in {loc}.",
    # MOV / COP / math: the source-side wording is filled in
    # dynamically by ``_format_writer_what`` because it depends on
    # operands carried in ``platform_specific``.
}


_CONDITIONS_VERB: dict[str, str] = {
    "OTE": "is energized",
    "OTL": "is latched ON",
    "OTU": "is unlatched / reset OFF",
    "TON": "starts its on-delay timer",
    "TOF": "starts its off-delay timer",
    "RTO": "increments its retentive timer",
    "CTU": "increments its counter",
    "CTD": "decrements its counter",
    "RES": "is reset",
    "JSR": "is called",
    "ONS": "is pulsed for one scan",
    "OSR": "pulses on a rising edge",
    "OSF": "pulses on a falling edge",
    "MOV": "is loaded",
    "COP": "is copied into",
    "ADD": "is calculated",
    "SUB": "is calculated",
    "MUL": "is calculated",
    "DIV": "is calculated",
    "CPT": "is calculated",
}


# ===========================================================================
# Public entry point: trace_object_v2
# ===========================================================================


def trace_object_v2(
    target_object_id: str,
    control_objects: Sequence[ControlObject],
    relationships: Sequence[Relationship],
    execution_contexts: Optional[Sequence[ExecutionContext]] = None,
) -> TraceResult:
    """Run Trace v1, then enrich its conclusions with natural language.

    The returned :class:`TraceResult` has the same fields and the same
    writer / reader / upstream / downstream lists as v1. Only
    ``conclusions`` is augmented:

    1. Natural-language statements come **first** so a UI consumer
       reading the list top-down sees the human-friendly summary
       before the v1 detail.
    2. The original v1 conclusions are appended verbatim for
       transparency and so v1 callers that pattern-match on those
       statements keep working.

    ``platform_specific["trace_version"]`` is set to ``"v2"`` and
    ``platform_specific["natural_conclusion_count"]`` records how
    many statements v2 added on top of v1.
    """

    base = trace_object(
        target_object_id=target_object_id,
        control_objects=control_objects,
        relationships=relationships,
        execution_contexts=execution_contexts or [],
    )

    # Lookups used by every per-writer pass.
    obj_by_id: dict[str, ControlObject] = {o.id: o for o in control_objects}
    rels_by_source: dict[str, list[Relationship]] = {}
    for r in relationships:
        rels_by_source.setdefault(r.source_id, []).append(r)

    target_obj = obj_by_id.get(target_object_id)
    target_display_name = (
        (target_obj.name if target_obj else None) or target_object_id
    )

    natural_conclusions: list[TruthConclusion] = []
    for writer in base.writer_relationships:
        natural_conclusions.extend(
            _build_writer_conclusions(
                writer=writer,
                target_name=target_display_name,
                obj_by_id=obj_by_id,
                rels_by_source=rels_by_source,
            )
        )

    base.conclusions = natural_conclusions + list(base.conclusions)

    # Augment the summary with the first natural-language statement
    # so a UI that only renders ``summary`` still surfaces v2 wording.
    if natural_conclusions:
        first = natural_conclusions[0].statement
        if base.summary:
            base.summary = f"{base.summary} {first}"
        else:
            base.summary = first

    base.platform_specific = dict(base.platform_specific or {})
    base.platform_specific["trace_version"] = "v2"
    base.platform_specific["natural_conclusion_count"] = len(
        natural_conclusions
    )
    from app.services.evidence_service import build_trace_evidence
    from app.services.trustworthiness_service import assess_trace_confidence

    evidence = build_trace_evidence(base)
    trust = assess_trace_confidence(base, relationships)
    base.platform_specific["evidence_bundle"] = evidence.model_dump(mode="json")
    base.platform_specific["trust_assessment"] = trust.model_dump(mode="json")
    base.platform_specific["confidence_score"] = trust.confidence_score

    return base


# ===========================================================================
# Internals
# ===========================================================================


def _build_writer_conclusions(
    writer: Relationship,
    target_name: str,
    obj_by_id: dict[str, ControlObject],
    rels_by_source: dict[str, list[Relationship]],
) -> list[TruthConclusion]:
    """Produce 1..2 natural-language conclusions for a single writer."""

    source_obj = obj_by_id.get(writer.source_id)
    target_obj = obj_by_id.get(writer.target_id)

    short_location = _short_location_for(
        writer, source_object=source_obj, target_object=target_obj
    )
    itype = _instruction_type_from_relationship(writer)

    if _is_structured_text_writer(writer, source_obj):
        return _build_st_writer_conclusions(
            writer=writer,
            target_name=target_name,
            short_location=short_location,
            source_obj=source_obj,
        )

    return _build_ladder_writer_conclusions(
        writer=writer,
        target_name=target_name,
        itype=itype,
        short_location=short_location,
        rels_by_source=rels_by_source,
        obj_by_id=obj_by_id,
    )


def _build_ladder_writer_conclusions(
    writer: Relationship,
    target_name: str,
    itype: Optional[str],
    short_location: str,
    rels_by_source: dict[str, list[Relationship]],
    obj_by_id: dict[str, ControlObject],
) -> list[TruthConclusion]:
    out: list[TruthConclusion] = []

    # 1) WHAT does this writer do?
    out.append(
        _build_what_conclusion(
            writer=writer,
            target_name=target_name,
            itype=itype,
            short_location=short_location,
            rels_by_source=rels_by_source,
            obj_by_id=obj_by_id,
        )
    )

    # 2) Under what CONDITIONS does the writer fire?
    #
    # Preferred source: normalized READS edges from the same rung. The
    # collector handles XIC/XIO, comparison reads, one-shot reads, and
    # timer/counter member access. Fallback to a regex scan of the raw
    # rung text when the rung has no READS at all (very small rungs or
    # legacy parsed projects).
    phrases = _ladder_condition_phrases_for_rung(
        rung_id=writer.source_id,
        rels_by_source=rels_by_source,
        obj_by_id=obj_by_id,
    )
    if not phrases and writer.logic_condition:
        # Fallback uses the same "<tag> is TRUE/FALSE" wording as the
        # normalized-reads path so the test contract for the
        # conditions clause stays consistent regardless of source.
        # (``extract_simple_ladder_conditions`` itself uses
        # ``"must be"`` wording in its ``natural_language``, which
        # we deliberately don't propagate here.)
        phrases = [
            _LadderConditionPhrase(
                phrase=_xic_xio_phrase(
                    tag_name=c.tag,
                    required_value=c.required_value,
                    member=None,
                    member_semantic=None,
                ),
                instruction_type=c.instruction_type,
                tag=c.tag,
                required_value=c.required_value,
            )
            for c in extract_simple_ladder_conditions(writer.logic_condition)
        ]

    if phrases:
        out.append(
            _build_conditions_conclusion(
                writer=writer,
                target_name=target_name,
                itype=itype,
                short_location=short_location,
                phrases=phrases,
            )
        )

    # 3) Branch warning -- emitted last so it reads as a caveat
    # appended to the explanation of the writer.
    warning = _branch_warning_conclusion_for_writer(writer, short_location)
    if warning is not None:
        out.append(warning)
    return out


def _build_what_conclusion(
    writer: Relationship,
    target_name: str,
    itype: Optional[str],
    short_location: str,
    rels_by_source: Optional[dict[str, list[Relationship]]] = None,
    obj_by_id: Optional[dict[str, ControlObject]] = None,
) -> TruthConclusion:
    """Produce the per-writer "WHAT does it do?" conclusion.

    For math (ADD/SUB/MUL/DIV/CPT) and move (MOV/COP), the source
    operand(s) are looked up in the writer's ``platform_specific``
    (math) or in a sibling READS edge (move) so the conclusion can
    name them in natural language. One-shots and other registered
    families fall through to the static :data:`_WHAT_PHRASE`. Unknown
    instructions get a graceful "is written in <loc>" fallback.
    """

    upper_itype = (itype or "").upper()

    # Math: prefer dynamic phrasing when the WRITES carries source
    # operands. ADD/SUB/MUL/DIV all have two source operands; CPT has
    # none (the expression is opaque) so it just gets a static phrase.
    custom = _math_or_move_what_phrase(
        writer=writer,
        target_name=target_name,
        short_location=short_location,
        itype=upper_itype,
        rels_by_source=rels_by_source or {},
        obj_by_id=obj_by_id or {},
    )
    if custom:
        statement = custom
    else:
        phrase = _WHAT_PHRASE.get(upper_itype)
        if phrase:
            statement = phrase.format(target=target_name, loc=short_location)
        else:
            verb = humanize_instruction_type(itype, target_name=target_name)
            statement = (
                f"{target_name} is written in {short_location}"
                + (f"; it {verb}." if verb else ".")
            )

    return TruthConclusion(
        statement=statement,
        subject_ids=[writer.target_id, writer.source_id],
        truth_context=TruthContextType.DESIGN_TRUTH,
        confidence=ConfidenceLevel.HIGH,
        recommended_checks=[],
        platform_specific={
            "trace_v2_kind": "writer_what",
            "instruction_type": itype,
            "location": short_location,
        },
    )


def _build_conditions_conclusion(
    writer: Relationship,
    target_name: str,
    itype: Optional[str],
    short_location: str,
    phrases: list["_LadderConditionPhrase"],
) -> TruthConclusion:
    """Produce the per-writer "Under what CONDITIONS?" conclusion."""

    verb_phrase = _CONDITIONS_VERB.get((itype or "").upper(), "is affected")
    cond_phrases = [p.phrase for p in phrases]
    joined = _oxford_join(cond_phrases)

    statement = (
        f"{target_name} {verb_phrase} in {short_location} when {joined}."
    )

    return TruthConclusion(
        statement=statement,
        subject_ids=[writer.target_id, writer.source_id],
        truth_context=TruthContextType.DESIGN_TRUTH,
        confidence=ConfidenceLevel.HIGH,
        recommended_checks=[],
        platform_specific={
            "trace_v2_kind": "writer_conditions",
            "instruction_type": itype,
            "location": short_location,
            "conditions": [
                _condition_dict_for_phrase(p) for p in phrases
            ],
        },
    )


def _condition_dict_for_phrase(
    phrase: "_LadderConditionPhrase",
) -> dict:
    """Serialize a single condition row for the writer_conditions metadata.

    Only fields that meaningfully apply to the condition are added so
    legacy consumers (e.g. runtime v1) that scan for ``tag`` /
    ``required_value`` keep matching just those keys. Comparison
    conditions also gain ``comparison_operator`` / ``compared_operands``
    and timer-member XIC/XIO gain ``member`` -- runtime v2 reads these
    instead of re-parsing the natural-language phrase.
    """

    out: dict = {
        "natural_language": phrase.phrase,
        "instruction_type": phrase.instruction_type,
    }
    if phrase.tag is not None:
        out["tag"] = phrase.tag
    if phrase.required_value is not None:
        out["required_value"] = phrase.required_value
    if phrase.member:
        out["member"] = phrase.member
    if phrase.comparison_operator:
        out["comparison_operator"] = phrase.comparison_operator
    if phrase.compared_operands:
        out["compared_operands"] = list(phrase.compared_operands)
    return out


def _math_or_move_what_phrase(
    writer: Relationship,
    target_name: str,
    short_location: str,
    itype: str,
    rels_by_source: dict[str, list[Relationship]],
    obj_by_id: dict[str, ControlObject],
) -> Optional[str]:
    """Dynamically render the WHAT sentence for math / move writers.

    Returns ``None`` when ``itype`` doesn't need dynamic phrasing, so
    callers can fall back to the static :data:`_WHAT_PHRASE` table.
    """

    meta = writer.platform_specific or {}

    if itype in ("ADD", "SUB", "MUL", "DIV"):
        operator = meta.get("math_operator")
        sources = meta.get("source_operands") or []
        if (
            isinstance(sources, list)
            and len(sources) >= 2
            and operator
        ):
            return (
                f"{target_name} is calculated from "
                f"{sources[0]} {operator} {sources[1]} in {short_location}."
            )
        # Missing source operands -> fall through to the generic phrase.

    if itype == "CPT":
        return (
            f"{target_name} is calculated from a compute expression "
            f"in {short_location}."
        )

    if itype == "MOV":
        source = _find_move_source_name(writer, rels_by_source, obj_by_id)
        if source:
            return (
                f"{target_name} is loaded from {source} in {short_location}."
            )
        return f"{target_name} is loaded from a source value in {short_location}."

    if itype == "COP":
        source = _find_move_source_name(writer, rels_by_source, obj_by_id)
        if source:
            return (
                f"{target_name} is copied from {source} in {short_location}."
            )
        return (
            f"{target_name} is copied from a source array in {short_location}."
        )

    return None


def _find_move_source_name(
    writer: Relationship,
    rels_by_source: dict[str, list[Relationship]],
    obj_by_id: dict[str, ControlObject],
) -> Optional[str]:
    """Return the name of the source tag of a MOV / COP, when available.

    The normalizer emits the source as a sibling READS relationship
    on the same rung carrying ``operand_role="move_source"`` and the
    same ``instruction_id`` as the destination WRITES. We match on
    both so a rung containing multiple MOVs doesn't cross-pollinate.
    Resolves the source tag's display name via ``obj_by_id`` when the
    ControlObject is known, falling back to the trailing component of
    the target id when it isn't.
    """

    instr_id = (writer.platform_specific or {}).get("instruction_id")
    if not instr_id:
        return None
    for sibling in rels_by_source.get(writer.source_id, []):
        if sibling.relationship_type != RelationshipType.READS:
            continue
        sib_meta = sibling.platform_specific or {}
        if sib_meta.get("instruction_id") != instr_id:
            continue
        if sib_meta.get("operand_role") != "move_source":
            continue
        target = obj_by_id.get(sibling.target_id)
        if target and target.name:
            return target.name
        return sibling.target_id.rsplit("/", 1)[-1]
    return None


def _build_st_writer_conclusions(
    writer: Relationship,
    target_name: str,
    short_location: str,
    source_obj: Optional[ControlObject],
) -> list[TruthConclusion]:
    """Produce a Structured Text writer's natural-language conclusion.

    Dispatches by ``gating_logic_type`` on the writer's
    ``platform_specific``:

    * ``"too_complex"`` -> canonical "expression too complex" line.
    * ``"and"`` with identifier-only conditions and no CASE branch
      summary -> delegate to :func:`extract_simple_st_conditions`
      (preserves the long-standing wording for the v1 envelope).
    * Everything else (``"or"`` / ``"and_or"`` / ``"comparison"``,
      mixed shapes, or CASE branches) -> use the richer renderer
      that consumes the normalized ``extracted_conditions`` and
      ``case_condition_summary`` directly.
    """

    meta = writer.platform_specific or {}
    gating = str(meta.get("gating_logic_type") or "")
    parse_status = str(meta.get("st_parse_status") or "")

    if gating == "too_complex" or parse_status == "too_complex":
        return [_st_too_complex_conclusion(writer, target_name, short_location)]

    # Try the legacy simple-conjunction path first so existing
    # consumers see the exact same wording for ``A := B AND C`` etc.
    if _can_use_simple_st_path(meta):
        text = writer.logic_condition
        if not text and source_obj is not None:
            text = (source_obj.platform_specific or {}).get("raw_text") or None
        extraction: Optional[STExtractionResult] = (
            extract_simple_st_conditions(text or "") if text else None
        )
        if extraction is not None:
            return [
                _st_simple_conjunction_conclusion(
                    writer=writer,
                    short_location=short_location,
                    extraction=extraction,
                )
            ]

    # Richer paths: OR / AND-OR / comparison / CASE / multi-branch.
    rich = _st_rich_conclusion(
        writer=writer,
        target_name=target_name,
        short_location=short_location,
        meta=meta,
    )
    if rich is not None:
        return [rich]

    # Fall back to the canonical too-complex line so the UI still says
    # something useful.
    return [_st_too_complex_conclusion(writer, target_name, short_location)]


def _can_use_simple_st_path(meta: dict) -> bool:
    """True when the WRITES metadata describes the v1 envelope.

    The v1 envelope is "single AND-conjunction of identifier reads,
    no CASE branch summary". When any of those fail we want the
    richer renderer instead.
    """

    gating = str(meta.get("gating_logic_type") or "and")
    if gating != "and":
        return False
    if meta.get("case_condition_summary"):
        return False
    for entry in meta.get("extracted_conditions") or []:
        if entry.get("comparison_operator") is not None:
            return False
    return True


def _st_simple_conjunction_conclusion(
    writer: Relationship,
    short_location: str,
    extraction: STExtractionResult,
) -> TruthConclusion:
    """Preserve the long-standing v1 wording for simple ST conjunctions."""

    return TruthConclusion(
        statement=extraction.natural_language,
        subject_ids=[writer.target_id, writer.source_id],
        truth_context=TruthContextType.DESIGN_TRUTH,
        confidence=ConfidenceLevel.HIGH,
        recommended_checks=[],
        platform_specific={
            "trace_v2_kind": "st_assignment",
            "location": short_location,
            "assigned_target": extraction.assigned_target,
            "assigned_value": extraction.assigned_value,
            "conditions": [
                {
                    "tag": c.tag,
                    "required_value": c.required_value,
                    "natural_language": c.natural_language,
                }
                for c in extraction.conditions
            ],
        },
    )


def _st_too_complex_conclusion(
    writer: Relationship,
    target_name: str,
    short_location: str,
) -> TruthConclusion:
    statement = (
        "Structured Text logic was detected, but this expression is "
        "too complex for deterministic Trace v2 extraction."
    )
    return TruthConclusion(
        statement=statement,
        subject_ids=[writer.target_id, writer.source_id],
        truth_context=TruthContextType.DESIGN_TRUTH,
        confidence=ConfidenceLevel.LOW,
        recommended_checks=[
            f"Review the Structured Text logic at {short_location} "
            f"that drives {target_name}.",
        ],
        platform_specific={
            "trace_v2_kind": "st_too_complex",
            "location": short_location,
        },
    )


def _st_rich_conclusion(
    writer: Relationship,
    target_name: str,
    short_location: str,
    meta: dict,
) -> Optional[TruthConclusion]:
    """Render OR / AND-OR / comparison / CASE writers in natural language.

    Reads ``extracted_conditions`` (already populated by the
    normalizer) and groups them by ``or_branch_index``. Each branch
    is rendered as a conjunction of identifier-or-comparison
    phrases; branches are joined with "or". CASE branches get a
    prefix sentence pulled from ``case_condition_summary``.
    """

    assigned_value = meta.get("assigned_value")
    case_summary = meta.get("case_condition_summary")
    extracted = meta.get("extracted_conditions") or []

    branches_text = _format_st_branches_text(extracted)

    if not branches_text:
        # No gating conditions surfaced. If the assignment is a
        # literal (TRUE/FALSE) we can still say something useful;
        # otherwise let the caller fall through to the too-complex
        # path.
        if assigned_value in ("TRUE", "FALSE"):
            value_clause = f"is assigned {assigned_value}"
            sentence = (
                f"{target_name} {value_clause} in {short_location}."
            )
            if case_summary:
                sentence = (
                    f"This branch applies when {case_summary}. "
                    + sentence
                )
            return TruthConclusion(
                statement=sentence,
                subject_ids=[writer.target_id, writer.source_id],
                truth_context=TruthContextType.DESIGN_TRUTH,
                confidence=ConfidenceLevel.HIGH,
                recommended_checks=[],
                platform_specific=_st_rich_platform(
                    meta=meta,
                    short_location=short_location,
                ),
            )
        return None

    value_clause = _format_st_assigned_value_clause(assigned_value)
    sentence = (
        f"{target_name} {value_clause} in {short_location} "
        f"when {branches_text}."
    )
    if case_summary:
        sentence = f"This branch applies when {case_summary}. " + sentence

    return TruthConclusion(
        statement=sentence,
        subject_ids=[writer.target_id, writer.source_id],
        truth_context=TruthContextType.DESIGN_TRUTH,
        confidence=ConfidenceLevel.HIGH,
        recommended_checks=[],
        platform_specific=_st_rich_platform(
            meta=meta,
            short_location=short_location,
        ),
    )


def _format_st_assigned_value_clause(
    assigned_value: Optional[str],
) -> str:
    """Pick the verb phrase for the assignment summary."""

    if assigned_value in ("TRUE", "FALSE"):
        return f"is assigned {assigned_value}"
    return "is assigned"


def _format_st_branches_text(
    extracted: list[dict],
) -> str:
    """Group ``extracted_conditions`` by OR-branch index and render.

    Returns an empty string when no usable conditions exist (e.g.
    a CASE branch whose body is just ``Motor := TRUE`` with no
    other gating). The caller decides how to phrase the surrounding
    sentence in that case.
    """

    if not extracted:
        return ""

    branches: dict[int, list[dict]] = {}
    branch_order: list[int] = []
    for entry in extracted:
        idx = entry.get("or_branch_index")
        # Treat missing or_branch_index as branch 0 so simple
        # conjunctions / comparisons render correctly.
        bidx = int(idx) if isinstance(idx, int) else 0
        if bidx not in branches:
            branches[bidx] = []
            branch_order.append(bidx)
        branches[bidx].append(entry)

    rendered_branches: list[str] = []
    for bidx in branch_order:
        terms_text: list[str] = []
        for entry in branches[bidx]:
            op = entry.get("comparison_operator")
            if op:
                verb = _COMPARISON_VERB.get(op, op)
                lhs = entry.get("tag") or ""
                rhs = entry.get("compared_with") or ""
                # If lhs / rhs are missing, fall back to the
                # natural_language entry if present.
                if lhs and rhs:
                    terms_text.append(f"{lhs} {verb} {rhs}")
                else:
                    terms_text.append(
                        str(entry.get("natural_language") or "")
                    )
                continue
            tag = entry.get("tag")
            required = entry.get("required_value")
            if tag is None or required is None:
                continue
            terms_text.append(
                f"{tag} is {'TRUE' if required else 'FALSE'}"
            )
        terms_text = [t for t in terms_text if t]
        if not terms_text:
            continue
        if len(terms_text) == 1:
            rendered_branches.append(terms_text[0])
        else:
            rendered_branches.append(_oxford_join(terms_text))

    if not rendered_branches:
        return ""
    if len(rendered_branches) == 1:
        return rendered_branches[0]
    if len(rendered_branches) == 2:
        return f"either {rendered_branches[0]}, or {rendered_branches[1]}"
    head = ", or ".join(rendered_branches[:-1])
    return f"either {head}, or {rendered_branches[-1]}"


def _st_rich_platform(
    meta: dict,
    short_location: str,
) -> dict:
    """Compose ``platform_specific`` for a rich-ST conclusion.

    Mirrors the long-standing fields surfaced by
    :func:`_st_simple_conjunction_conclusion` plus the new metadata
    that drives the OR / comparison / CASE rendering.
    """

    return {
        "trace_v2_kind": "st_assignment",
        "location": short_location,
        "assigned_target": meta.get("assigned_target"),
        "assigned_value": meta.get("assigned_value"),
        "gating_logic_type": meta.get("gating_logic_type"),
        "case_condition_summary": meta.get("case_condition_summary"),
        "conditions": list(meta.get("extracted_conditions") or []),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LadderConditionPhrase:
    """One natural-language phrase for the gating-conditions clause.

    ``tag`` / ``required_value`` are kept for the simple XIC/XIO
    family so the WRITES platform_specific can still carry the
    legacy per-tag breakdown alongside the natural phrase. They are
    ``None`` for comparison and one-shot phrases, which don't reduce
    to a single ``(tag, boolean)`` pair.

    ``member`` / ``comparison_operator`` / ``compared_operands`` are
    propagated when the underlying READS carries the corresponding
    metadata. These fields are consumed by downstream runtime
    evaluators (see :mod:`app.services.runtime_evaluation_v2_service`)
    so they don't have to re-parse the natural-language phrase.
    """

    phrase: str
    instruction_type: str
    tag: Optional[str] = None
    required_value: Optional[bool] = None
    member: Optional[str] = None
    comparison_operator: Optional[str] = None
    compared_operands: Optional[tuple[str, ...]] = None


def _ladder_condition_phrases_for_rung(
    rung_id: str,
    rels_by_source: dict[str, list[Relationship]],
    obj_by_id: dict[str, ControlObject],
) -> list[_LadderConditionPhrase]:
    """Build natural-language phrases for the gating conditions of a
    rung.

    Walks the rung's READS edges (and, where useful, its sibling
    WRITES edges to detect one-shots) in source order and emits one
    phrase per distinct gating instruction. The collector handles:

    * XIC / XIO -> "X must be TRUE" / "X must be FALSE".
    * XIC / XIO on a timer/counter member -> "X's timer done bit is
      set" (and similar) via :data:`_MEMBER_PHRASES`.
    * Comparison instructions (EQU/NEQ/GRT/GEQ/LES/LEQ/LIM) -> one
      phrase per instruction (READS for both operands of EQU are
      collapsed into a single sentence).
    * One-shots (ONS/OSR/OSF) -> one phrase per instruction.

    Order is preserved per ``instruction_id`` first-seen position so
    the natural-language clause reads in source order.
    """

    rels = rels_by_source.get(rung_id, [])

    # Track which instruction_ids we've already produced a phrase
    # for, to avoid duplicating multi-operand instructions (e.g. LIM
    # has 3 READS for the same instruction_id; we only want one
    # sentence for it).
    seen_instructions: set[str] = set()
    phrases: list[_LadderConditionPhrase] = []

    for r in rels:
        if r.relationship_type != RelationshipType.READS:
            continue
        meta = r.platform_specific or {}
        itype = str(meta.get("instruction_type", "")).upper()
        instr_id = meta.get("instruction_id")

        # --- Comparisons --------------------------------------------
        if itype in _COMPARISON_INSTRUCTIONS:
            if instr_id and instr_id in seen_instructions:
                continue
            phrase_text = _comparison_phrase_from_read(r, meta, itype)
            if phrase_text is None:
                continue
            if instr_id:
                seen_instructions.add(instr_id)
            cmp_operator = meta.get("comparison_operator")
            cmp_operands = meta.get("compared_operands")
            # ``compared_operands`` may be a list[str] of length 2 for
            # binary comparisons (EQU/NEQ/...) or 3 for LIM. We carry
            # whatever shape the normalizer emits; downstream consumers
            # decide how to interpret it.
            operands_tuple = (
                tuple(str(o) for o in cmp_operands)
                if isinstance(cmp_operands, list) and cmp_operands
                else None
            )
            # For binary comparisons, surface the LHS operand as the
            # condition's ``tag`` so consumers that index by tag don't
            # need to crack ``compared_operands`` themselves.
            lhs_tag: Optional[str] = None
            if operands_tuple and len(operands_tuple) == 2:
                lhs_tag = operands_tuple[0]
            phrases.append(
                _LadderConditionPhrase(
                    phrase=phrase_text,
                    instruction_type=itype,
                    tag=lhs_tag,
                    comparison_operator=(
                        str(cmp_operator) if cmp_operator else None
                    ),
                    compared_operands=operands_tuple,
                )
            )
            continue

        # --- One-shots ---------------------------------------------
        if itype in ("ONS", "OSR", "OSF"):
            if instr_id and instr_id in seen_instructions:
                continue
            phrase_text = _one_shot_phrase_from_read(
                r, meta, itype, obj_by_id
            )
            if instr_id:
                seen_instructions.add(instr_id)
            phrases.append(
                _LadderConditionPhrase(
                    phrase=phrase_text,
                    instruction_type=itype,
                )
            )
            continue

        # --- XIC / XIO (the bulk of normal ladder logic) -----------
        if itype not in ("XIC", "XIO"):
            continue
        examined = meta.get("examined_value")
        if examined is None:
            examined = itype == "XIC"
        target_obj = obj_by_id.get(r.target_id)
        tag_name = (
            target_obj.name
            if (target_obj and target_obj.name)
            else r.target_id
        )
        member = meta.get("member")
        member_semantic = meta.get("member_semantic")
        phrase_text = _xic_xio_phrase(
            tag_name=tag_name,
            required_value=bool(examined),
            member=member if isinstance(member, str) else None,
            member_semantic=(
                member_semantic
                if isinstance(member_semantic, str)
                else None
            ),
        )
        phrases.append(
            _LadderConditionPhrase(
                phrase=phrase_text,
                instruction_type=itype,
                tag=tag_name,
                required_value=bool(examined),
                member=member if isinstance(member, str) else None,
            )
        )

    return phrases


# Set of instruction types treated as comparison gating.
_COMPARISON_INSTRUCTIONS = frozenset(
    {"EQU", "NEQ", "LES", "LEQ", "GRT", "GEQ", "LIM"}
)


def _xic_xio_phrase(
    tag_name: str,
    required_value: bool,
    member: Optional[str],
    member_semantic: Optional[str],
) -> str:
    """Render the natural language for a single XIC / XIO read.

    Plain XIC / XIO produces ``"<tag> is TRUE/FALSE"`` -- matching
    the long-standing Trace v2 wording in the conditions clause. The
    fallback regex extractor (used when there are no normalized READS
    edges) emits the same shape. When the operand accesses a known
    timer/counter member, the phrasing is upgraded to
    ``"<tag>'s <member-phrase> is set/clear"`` (DN/TT/EN -- boolean
    members) so the engineer reads the bit name instead of a raw
    ``Timer.DN`` tag suffix.
    """

    member_phrase = _MEMBER_PHRASES.get(member or "")
    if not member_phrase:
        return f"{tag_name} is {'TRUE' if required_value else 'FALSE'}"

    # Boolean member -> set / clear.
    if member in ("DN", "TT", "EN"):
        suffix = "is set" if required_value else "is clear"
        return f"{tag_name}'s {member_phrase} {suffix}"

    # Non-boolean member (ACC / PRE) examined as a boolean is unusual;
    # fall back to a safe, neutral phrasing rather than misrepresent
    # the comparison.
    suffix = "is non-zero" if required_value else "is zero"
    return f"{tag_name}'s {member_phrase} {suffix}"


def _comparison_phrase_from_read(
    relationship: Relationship,
    meta: dict,
    itype: str,
) -> Optional[str]:
    """Render the natural language for a comparison gating instruction.

    Pulls the operator and operand list from ``platform_specific``
    (the normalizer guarantees ``compared_operands`` is populated)
    so we get the same sentence regardless of which of the
    instruction's READS happens to be processed first.
    """

    operands = meta.get("compared_operands")
    operator = meta.get("comparison_operator")
    if not isinstance(operands, list) or not operator:
        return None

    if itype == "LIM" and len(operands) == 3:
        low, test, high = operands
        return f"{test} must be between {low} and {high}"

    if len(operands) < 2:
        return None

    verb = _COMPARISON_VERB.get(operator)
    if verb is None:
        # Unknown operator -- emit a defensive fallback rather than
        # raise. Should be unreachable today; covered for safety.
        return f"{operands[0]} {operator} {operands[1]}"
    return f"{operands[0]} {verb} {operands[1]}"


def _one_shot_phrase_from_read(
    relationship: Relationship,
    meta: dict,
    itype: str,
    obj_by_id: dict[str, ControlObject],
) -> str:
    """Render the natural language for an ONS / OSR / OSF storage read.

    The phrase names the storage bit (the READS target) plus the
    instruction-specific edge phrase from the spec.
    """

    target_obj = obj_by_id.get(relationship.target_id)
    tag_name = (
        target_obj.name
        if (target_obj and target_obj.name)
        else relationship.target_id
    )
    if itype == "ONS":
        return f"{tag_name} drives a one-scan pulse condition"
    if itype == "OSR":
        return f"{tag_name} drives a rising-edge one-shot"
    if itype == "OSF":
        return f"{tag_name} drives a falling-edge one-shot"
    return f"{tag_name} drives a one-shot"


# Canonical branch-warning message. Kept as a module constant so
# tests can pattern-match exactly without duplicating the wording.
_BRANCH_WARNING_TEXT = (
    "This rung contains parallel branches. INTELLI has detected "
    "branch structure, but branch-specific attribution is not fully "
    "resolved yet."
)


def _branch_warning_conclusion_for_writer(
    writer: Relationship,
    short_location: str,
) -> Optional[TruthConclusion]:
    """Return a branch-detection caveat when the writer's rung is
    branched, else ``None``.

    The caveat is intentionally conservative: it tells the engineer
    that parallel branches were detected and warns that the trace's
    per-instruction attribution still treats the rung as a single
    boolean condition.
    """

    meta = writer.platform_specific or {}
    if not meta.get("rung_has_branches"):
        return None
    branch_count = meta.get("rung_branch_count")
    return TruthConclusion(
        statement=_BRANCH_WARNING_TEXT,
        subject_ids=[writer.source_id],
        truth_context=TruthContextType.DESIGN_TRUTH,
        confidence=ConfidenceLevel.MEDIUM,
        recommended_checks=[
            f"Review the parallel branch structure at {short_location}.",
        ],
        platform_specific={
            "trace_v2_kind": "branch_warning",
            "location": short_location,
            "rung_has_branches": True,
            "rung_branch_count": branch_count,
        },
    )


def _is_structured_text_writer(
    writer: Relationship,
    source_obj: Optional[ControlObject],
) -> bool:
    """True when the writer originates from a Structured Text source.

    The normalizer today doesn't emit ST writers (it only structurally
    represents ST routines). This predicate is forward-looking: when
    ST normalization lands, ST WRITES will originate from INSTRUCTION
    or ROUTINE objects whose ``attributes["language"]`` is ST.
    """

    if source_obj is None:
        return False
    if source_obj.object_type == ControlObjectType.RUNG:
        return False
    language = ""
    if source_obj.attributes:
        language = str(source_obj.attributes.get("language", "")).lower()
    return language in ("structured_text", "st", "structuredtext", "stx")


def _instruction_type_from_relationship(r: Relationship) -> Optional[str]:
    if not r.platform_specific:
        return None
    itype = r.platform_specific.get("instruction_type")
    return str(itype).upper() if itype else None


# Re-use the same shortening logic that Trace v1 uses for its
# location-aware conclusions, but drop the trailing " using <INSTR>"
# because Trace v2's writer-WHAT conclusions name the instruction in a
# more natural way ("is latched ON" / "is energized" / ...).
_USING_SUFFIX_RE = re.compile(r"\s+using\s+[^.]*$", re.IGNORECASE)


def _short_location_for(
    relationship: Relationship,
    source_object: Optional[ControlObject] = None,
    target_object: Optional[ControlObject] = None,
) -> str:
    detail = format_relationship_detail(
        relationship,
        source_object=source_object,
        target_object=target_object,
        execution_context=None,
    )
    if not detail:
        return ""
    return _USING_SUFFIX_RE.sub("", detail).strip()


def _oxford_join(items: list[str]) -> str:
    """Join phrases with an Oxford comma for 3+ items."""

    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


__all__ = [
    "LadderConditionExtraction",
    "humanize_instruction_type",
    "extract_simple_ladder_conditions",
    "trace_object_v2",
]
