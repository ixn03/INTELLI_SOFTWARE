"""Runtime Evaluation v2 -- deterministic operational verdict.

Runtime v2 upgrades v1's per-condition boolean diff into a stronger
*operational verdict* by evaluating, for each writer of a target:

1. **Boolean conditions** -- ``XIC`` / ``XIO`` reads carry a
   ``(tag, required_value)`` pair on Trace v2 ``writer_conditions``
   conclusions. Each pair is compared against the corresponding
   value in the caller-supplied runtime snapshot.

2. **Timer / counter member phrases** -- when a condition carries
   explicit ``member`` metadata (or the natural-language phrase
   identifies a known member like *"timer done bit"*, *"timer timing
   bit"*, *"timer enabled bit"*, *"accumulated value"* or *"preset
   value"*), the evaluator looks up ``<tag>.<member>`` in the
   snapshot. A plain ``<tag>`` key is accepted as a fallback.

3. **Comparison conditions** -- ``=``, ``<>``, ``>``, ``>=``, ``<``,
   ``<=`` against either a numeric / boolean literal or another tag
   from the snapshot. Other operators (e.g. ``LIM``'s 3-operand
   range check) are marked ``unsupported``.

4. **OR / AND-OR groups** -- conditions on the same conclusion are
   grouped by ``or_branch_index``. A single branch behaves as an AND
   group; multiple branches behave as an OR (any satisfied branch
   satisfies the whole path). Missing values in any branch surface
   as path-level ``incomplete``.

5. **Multiple writers** -- every writer (``OTE`` / ``OTL`` / ``OTU``
   / ``RES`` / ST assignment ...) is evaluated independently. Each
   writer's overall outcome is one of ``path_satisfied``,
   ``path_blocked``, ``path_incomplete`` or ``path_unsupported``.
   The target's overall verdict combines the per-writer outcomes
   with each writer's *write effect* (sets-true vs sets-false vs
   other) to produce one of:

      * ``target_can_be_on``
      * ``target_likely_off_or_reset``
      * ``conflict_or_scan_order_dependent``
      * ``blocked``
      * ``incomplete``

6. **Primary operational conclusion FIRST** -- runtime v2 prepends a
   single :class:`TruthConclusion` summarizing the verdict ahead of
   the existing Trace v2 / v1 conclusions, followed by one
   per-writer-path summary. The original conclusions are preserved
   verbatim.

7. **Structured metadata** in ``platform_specific``:

   * ``trace_version = "runtime_v2"``
   * ``runtime_snapshot_evaluated = True``
   * ``overall_verdict`` -- the verdict label.
   * ``writer_path_results`` -- one entry per writer with status,
     write_effect, conditions, etc.
   * ``satisfied_conditions`` / ``blocking_conditions`` /
     ``missing_conditions`` / ``unsupported_conditions`` -- flat
     lists across all writers, ready to render in a UI.
   * ``conflicts`` -- pairs of writer paths whose simultaneous
     satisfaction triggered a conflict verdict.

Runtime v2 does **not** call a PLC, does **not** call an LLM and does
**not** touch any database. It is a pure function of the input trace
and the snapshot. Runtime v1 (boolean only) continues to live
alongside it under :mod:`app.services.runtime_snapshot_service`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Union

from app.models.reasoning import (
    ConfidenceLevel,
    TraceResult,
    TruthConclusion,
    TruthContextType,
)
from app.models.runtime_value import RuntimeSnapshotModel
from app.services.runtime_ingestion_service import (
    normalize_runtime_snapshot,
    snapshot_data_type_map,
    snapshot_quality_map,
    snapshot_to_flat_values,
)


# ===========================================================================
# Status enums
# ===========================================================================


class ConditionStatus(str, Enum):
    """Outcome of evaluating one runtime condition."""

    SATISFIED = "satisfied"
    BLOCKING = "blocking"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"


class PathStatus(str, Enum):
    """Outcome of evaluating one writer's whole condition set."""

    PATH_SATISFIED = "path_satisfied"
    PATH_BLOCKED = "path_blocked"
    PATH_INCOMPLETE = "path_incomplete"
    PATH_UNSUPPORTED = "path_unsupported"


class OverallVerdict(str, Enum):
    """Final operational verdict for the traced target."""

    TARGET_CAN_BE_ON = "target_can_be_on"
    TARGET_LIKELY_OFF_OR_RESET = "target_likely_off_or_reset"
    CONFLICT_OR_SCAN_ORDER_DEPENDENT = "conflict_or_scan_order_dependent"
    BLOCKED = "blocked"
    INCOMPLETE = "incomplete"


class WriteEffect(str, Enum):
    """How a writer's effect rolls up into the overall verdict.

    * ``sets_true``  -- OTE / OTL / ST ``:= TRUE`` (energize / latch).
    * ``sets_false`` -- OTU / RES / ST ``:= FALSE`` (unlatch / reset).
    * ``other``      -- TON / MOV / math / unclassified ST assignments.
      "Other" satisfied paths still count toward
      ``target_can_be_on`` (the writer fires) but never trigger a
      true-vs-false conflict on their own.
    """

    SETS_TRUE = "sets_true"
    SETS_FALSE = "sets_false"
    OTHER = "other"


# ===========================================================================
# Internal constants
# ===========================================================================


_TRACE_VERSION = "runtime_v2"

# Source kinds that runtime v2 walks. Other ``trace_v2_kind`` values
# (writer_what, st_too_complex, branch_warning, ...) carry no
# evaluatable conditions and are ignored.
_KIND_WRITER_CONDITIONS = "writer_conditions"
_KIND_ST_ASSIGNMENT = "st_assignment"

# Marker kinds attached to runtime v2's own conclusions so consumers
# can tell them apart from Trace v2 design-time output.
_KIND_RUNTIME_V2_VERDICT = "runtime_v2_verdict"
_KIND_RUNTIME_V2_PATH = "runtime_v2_path"

# Instructions whose effect is *unambiguously* setting their target
# true / false. Anything else falls into the ``OTHER`` bucket and is
# rolled up conservatively in :func:`_compute_overall_verdict`.
_TRUE_SETTER_ITYPES = frozenset({"OTE", "OTL"})
_FALSE_SETTER_ITYPES = frozenset({"OTU", "RES"})

# Natural-language fragments that identify a timer/counter member
# access. Used as a fallback when the condition dict lacks explicit
# ``member`` metadata (e.g. legacy traces produced before the
# trace_v2_service enhancement landed).
_MEMBER_PHRASE_TO_MEMBER: dict[str, str] = {
    "timer done bit": "DN",
    "timer timing bit": "TT",
    "timer enabled bit": "EN",
    "accumulated value": "ACC",
    "preset value": "PRE",
}

# Comparison operators we know how to evaluate. ``LIM``-style 3-operand
# range checks are deliberately excluded -- they're surfaced as
# ``unsupported`` so the engineer still sees they exist.
_SUPPORTED_OPERATORS = frozenset({"=", "<>", ">", ">=", "<", "<="})

# Human-readable verb fragment for the comparison verb in narrative
# output (e.g. "must be greater than 80").
_OP_PHRASE: dict[str, str] = {
    "=": "equal",
    "<>": "not equal",
    ">": "be greater than",
    ">=": "be greater than or equal to",
    "<": "be less than",
    "<=": "be less than or equal to",
}


@dataclass(frozen=True)
class _RuntimeEvalContext:
    """Flat values plus per-tag quality / data-type for v2 evaluation."""

    flat: dict[str, Any]
    quality: dict[str, str]
    data_type: dict[str, str | None]


def _coerce_runtime_input(
    runtime_snapshot: Union[dict[str, Any], RuntimeSnapshotModel],
) -> _RuntimeEvalContext:
    if isinstance(runtime_snapshot, RuntimeSnapshotModel):
        model = runtime_snapshot
    else:
        model = normalize_runtime_snapshot(runtime_snapshot)
    return _RuntimeEvalContext(
        flat=snapshot_to_flat_values(model),
        quality=snapshot_quality_map(model),
        data_type=snapshot_data_type_map(model),
    )


def _quality_blocks_evaluation(q: str) -> bool:
    return q in ("bad", "missing", "uncertain")


def _dtype_allows_bool_condition(data_type: str | None) -> bool:
    """BOOL ladder conditions require BOOL-ish runtime tags."""

    if data_type is None or not str(data_type).strip():
        return True
    u = str(data_type).strip().upper()
    return u in ("BOOL", "BIT", "BOOLEAN")


# ===========================================================================
# Dataclasses
# ===========================================================================


@dataclass
class ConditionResult:
    """Evaluation of one runtime condition.

    Mirrors the structure of the input ``conditions`` row plus the
    runtime outcome. ``to_dict`` produces a JSON-serializable
    representation that is exactly what ends up in
    ``platform_specific["satisfied_conditions"]`` etc.
    """

    status: ConditionStatus
    natural_language: str
    instruction_type: Optional[str] = None
    or_branch_index: int = 0
    tag: Optional[str] = None
    member: Optional[str] = None
    snapshot_key: Optional[str] = None
    required_value: Any = None
    actual_value: Any = None
    comparison_operator: Optional[str] = None
    compared_with: Any = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "natural_language": self.natural_language,
            "instruction_type": self.instruction_type,
            "or_branch_index": self.or_branch_index,
            "tag": self.tag,
            "member": self.member,
            "snapshot_key": self.snapshot_key,
            "required_value": self.required_value,
            "actual_value": self.actual_value,
            "comparison_operator": self.comparison_operator,
            "compared_with": self.compared_with,
            "reason": self.reason,
        }


@dataclass
class WriterPathResult:
    """Evaluation of one writer's whole condition set."""

    status: PathStatus
    write_effect: WriteEffect
    location: str
    instruction_type: Optional[str]
    target_id: Optional[str]
    source_id: Optional[str]
    assigned_value: Optional[str] = None
    conditions: list[ConditionResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "write_effect": self.write_effect.value,
            "location": self.location,
            "instruction_type": self.instruction_type,
            "target_id": self.target_id,
            "source_id": self.source_id,
            "assigned_value": self.assigned_value,
            "conditions": [c.to_dict() for c in self.conditions],
        }


# ===========================================================================
# Public entry point
# ===========================================================================


def evaluate_trace_runtime_v2(
    trace_result: TraceResult,
    runtime_snapshot: Union[dict[str, Any], RuntimeSnapshotModel],
) -> TraceResult:
    """Evaluate Trace v2 conditions against a runtime snapshot.

    ``runtime_snapshot`` may be a legacy plain dict (simple or rich
    per-tag shapes), or a pre-normalized :class:`RuntimeSnapshotModel`
    from :mod:`app.services.runtime_ingestion_service`. Tag
    ``quality`` of ``bad`` / ``missing`` / ``uncertain`` forces a
    ``missing``-style condition outcome so paths surface as
    ``incomplete`` rather than falsely satisfied.

    See module docstring for the full algorithm. The function mutates
    and returns ``trace_result`` in place: ``conclusions``,
    ``summary`` and ``platform_specific`` are augmented; the
    underlying writer / reader / upstream / downstream lists are not
    touched, and the original Trace v2 conclusions are preserved
    verbatim after the runtime ones.
    """

    target_name = _short_target_name(trace_result.target_object_id)

    ctx = _coerce_runtime_input(runtime_snapshot)

    paths: list[WriterPathResult] = []
    for conclusion in trace_result.conclusions:
        meta = conclusion.platform_specific or {}
        kind = meta.get("trace_v2_kind")
        if kind not in (_KIND_WRITER_CONDITIONS, _KIND_ST_ASSIGNMENT):
            continue
        paths.append(_evaluate_writer_path(conclusion, ctx, kind))

    verdict = _compute_overall_verdict(paths)
    conflicts = _detect_conflicts(paths)
    aggregated = _aggregate_conditions(paths)

    primary = _build_primary_conclusion(
        verdict=verdict,
        target_name=target_name,
        target_id=trace_result.target_object_id,
        aggregated=aggregated,
        conflicts=conflicts,
    )
    per_path_concls = [
        _build_per_path_conclusion(p, trace_result.target_object_id)
        for p in paths
    ]

    trace_result.conclusions = (
        [primary] + per_path_concls + list(trace_result.conclusions)
    )

    if trace_result.summary:
        trace_result.summary = f"{primary.statement} {trace_result.summary}"
    else:
        trace_result.summary = primary.statement

    trace_result.platform_specific = dict(trace_result.platform_specific or {})
    trace_result.platform_specific["trace_version"] = _TRACE_VERSION
    trace_result.platform_specific["runtime_snapshot_evaluated"] = True
    trace_result.platform_specific["overall_verdict"] = verdict.value
    trace_result.platform_specific["writer_path_results"] = [
        p.to_dict() for p in paths
    ]
    trace_result.platform_specific["satisfied_conditions"] = aggregated[
        "satisfied"
    ]
    trace_result.platform_specific["blocking_conditions"] = aggregated[
        "blocking"
    ]
    trace_result.platform_specific["missing_conditions"] = aggregated["missing"]
    trace_result.platform_specific["unsupported_conditions"] = aggregated[
        "unsupported"
    ]
    trace_result.platform_specific["conflicts"] = conflicts

    return trace_result


# ===========================================================================
# Per-writer-path evaluation
# ===========================================================================


def _evaluate_writer_path(
    conclusion: TruthConclusion,
    ctx: _RuntimeEvalContext,
    kind: str,
) -> WriterPathResult:
    """Evaluate one writer's conditions and produce a path verdict."""

    meta = conclusion.platform_specific or {}
    location = str(meta.get("location") or "")
    instruction_type_raw = meta.get("instruction_type")
    instruction_type = (
        str(instruction_type_raw) if instruction_type_raw else None
    )
    assigned_value = (
        meta.get("assigned_value") if kind == _KIND_ST_ASSIGNMENT else None
    )
    subject_ids = list(conclusion.subject_ids or [])
    target_id = subject_ids[0] if subject_ids else None
    source_id = subject_ids[1] if len(subject_ids) > 1 else None

    write_effect = _classify_write_effect(instruction_type, assigned_value)

    raw_conditions = meta.get("conditions") or []
    cond_results: list[ConditionResult] = []
    for raw in raw_conditions:
        if not isinstance(raw, dict):
            continue
        cond_results.append(_evaluate_condition(raw, ctx))

    path_status = _classify_path_status(cond_results)

    return WriterPathResult(
        status=path_status,
        write_effect=write_effect,
        location=location,
        instruction_type=instruction_type,
        target_id=target_id,
        source_id=source_id,
        assigned_value=(
            str(assigned_value) if assigned_value is not None else None
        ),
        conditions=cond_results,
    )


# ===========================================================================
# Per-condition evaluation
# ===========================================================================


def _evaluate_condition(
    raw: dict, ctx: _RuntimeEvalContext
) -> ConditionResult:
    """Dispatch a single condition row to comparison / boolean evaluators."""

    natural_language = str(raw.get("natural_language") or "")
    instruction_type_raw = raw.get("instruction_type")
    instruction_type = (
        str(instruction_type_raw) if instruction_type_raw else None
    )
    or_idx = raw.get("or_branch_index")
    or_branch_index = int(or_idx) if isinstance(or_idx, int) else 0

    tag = raw.get("tag")
    member_raw = raw.get("member")
    member = member_raw if isinstance(member_raw, str) and member_raw else None
    op_raw = raw.get("comparison_operator")
    op = str(op_raw) if op_raw else None
    compared_with = raw.get("compared_with")
    compared_operands = raw.get("compared_operands")
    required = raw.get("required_value")

    if op is not None:
        # Prefer the explicit ST-style (tag, compared_with) shape.
        if compared_with is not None and isinstance(tag, str):
            return _eval_comparison(
                lhs_key=tag,
                operator=op,
                rhs=compared_with,
                ctx=ctx,
                natural_language=natural_language,
                instruction_type=instruction_type,
                or_branch_index=or_branch_index,
            )
        # Fall back to ladder-style (compared_operands list of 2).
        if (
            isinstance(compared_operands, list)
            and len(compared_operands) == 2
            and isinstance(compared_operands[0], str)
        ):
            return _eval_comparison(
                lhs_key=compared_operands[0],
                operator=op,
                rhs=compared_operands[1],
                ctx=ctx,
                natural_language=natural_language,
                instruction_type=instruction_type,
                or_branch_index=or_branch_index,
            )
        # Anything else (e.g. LIM with 3 operands, missing operands)
        # is reported as unsupported so the audit trail records it.
        return _unsupported(
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
            tag=tag if isinstance(tag, str) else None,
            comparison_operator=op,
            compared_with=compared_with,
            reason="Comparison metadata is incomplete or uses an unsupported shape.",
        )

    if isinstance(tag, str) and isinstance(required, bool):
        if member is None:
            member = _infer_member_from_natural_language(natural_language)
        return _eval_boolean(
            tag=tag,
            required=required,
            member=member,
            ctx=ctx,
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
        )

    return _unsupported(
        natural_language=natural_language,
        instruction_type=instruction_type,
        or_branch_index=or_branch_index,
        tag=tag if isinstance(tag, str) else None,
        reason=(
            "Condition lacks a (tag, required_value) pair or "
            "comparison metadata that runtime v2 can evaluate."
        ),
    )


def _eval_boolean(
    tag: str,
    required: bool,
    member: Optional[str],
    ctx: _RuntimeEvalContext,
    natural_language: str,
    instruction_type: Optional[str],
    or_branch_index: int,
) -> ConditionResult:
    """Evaluate a ``(tag, required: bool)`` condition.

    Lookup order:

    1. ``<tag>.<member>`` when a member is known (e.g. ``Timer1.DN``)
       -- this matches the canonical snapshot key convention.
    2. Plain ``<tag>`` when (1) is absent. Lets callers pass the bit
       value directly under the timer name when they don't model the
       member explicitly.

    Matching uses :func:`_values_match` so int / float snapshot
    values (e.g. accumulator counts) still evaluate truthily when no
    ``data_type`` is supplied (legacy snapshots).

    Non-BOOL ``data_type`` values (e.g. ``REAL``) with a boolean ladder
    condition are ``unsupported`` — BOOL semantics apply only to BOOL
    tags or untyped legacy scalars.

    ``quality`` not ``good`` yields ``missing`` so the path is
    ``incomplete``, never falsely satisfied.
    """

    primary_key = f"{tag}.{member}" if member else tag
    snapshot_key = primary_key
    if primary_key not in ctx.flat:
        if member is not None and tag in ctx.flat:
            snapshot_key = tag
        else:
            return ConditionResult(
                status=ConditionStatus.MISSING,
                natural_language=natural_language,
                instruction_type=instruction_type,
                or_branch_index=or_branch_index,
                tag=tag,
                member=member,
                snapshot_key=primary_key,
                required_value=required,
                actual_value=None,
                reason=(
                    f"{primary_key} is not in the runtime snapshot."
                ),
            )

    q = ctx.quality.get(snapshot_key, "good")
    if _quality_blocks_evaluation(q):
        return ConditionResult(
            status=ConditionStatus.MISSING,
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
            tag=tag,
            member=member,
            snapshot_key=snapshot_key,
            required_value=required,
            actual_value=ctx.flat.get(snapshot_key),
            reason=(
                f"{snapshot_key} has quality {q!r}; runtime condition "
                f"cannot be satisfied deterministically."
            ),
        )

    dtype = ctx.data_type.get(snapshot_key) or ctx.data_type.get(tag)
    timer_numeric_members = frozenset({"ACC", "PRE"})
    if (
        member not in timer_numeric_members
        and not _dtype_allows_bool_condition(dtype)
    ):
        return _unsupported(
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
            tag=tag,
            reason=(
                f"BOOL ladder/ST condition on {snapshot_key!r} but runtime "
                f"data_type is {dtype!r} (expected BOOL or legacy untyped)."
            ),
        )

    actual = ctx.flat[snapshot_key]
    matches = _values_match(actual, required)
    return ConditionResult(
        status=(
            ConditionStatus.SATISFIED if matches else ConditionStatus.BLOCKING
        ),
        natural_language=natural_language,
        instruction_type=instruction_type,
        or_branch_index=or_branch_index,
        tag=tag,
        member=member,
        snapshot_key=snapshot_key,
        required_value=required,
        actual_value=actual,
        reason=(
            f"{snapshot_key} is {_format_bool_value(actual)} as required."
            if matches
            else (
                f"{snapshot_key} is {_format_bool_value(actual)} "
                f"but must be {_format_bool(required)}."
            )
        ),
    )


def _eval_comparison(
    lhs_key: str,
    operator: str,
    rhs: Any,
    ctx: _RuntimeEvalContext,
    natural_language: str,
    instruction_type: Optional[str],
    or_branch_index: int,
) -> ConditionResult:
    """Evaluate ``lhs <op> rhs`` against the runtime snapshot.

    ``rhs`` may be a boolean / numeric literal or another tag name.
    Tag-name RHS values are resolved against the snapshot; missing
    tags produce a ``missing`` outcome so the path can be reported
    as incomplete rather than silently blocked.
    """

    if operator not in _SUPPORTED_OPERATORS:
        return _unsupported(
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
            tag=lhs_key,
            comparison_operator=operator,
            compared_with=rhs,
            reason=f"Unsupported comparison operator {operator!r}.",
        )

    if lhs_key not in ctx.flat:
        return ConditionResult(
            status=ConditionStatus.MISSING,
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
            tag=lhs_key,
            snapshot_key=lhs_key,
            comparison_operator=operator,
            compared_with=rhs,
            reason=f"{lhs_key} is not in the runtime snapshot.",
        )

    if _quality_blocks_evaluation(ctx.quality.get(lhs_key, "good")):
        return ConditionResult(
            status=ConditionStatus.MISSING,
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
            tag=lhs_key,
            snapshot_key=lhs_key,
            comparison_operator=operator,
            compared_with=rhs,
            actual_value=ctx.flat.get(lhs_key),
            reason=(
                f"{lhs_key} has quality {ctx.quality.get(lhs_key)!r}; "
                f"cannot evaluate comparison."
            ),
        )

    lhs_value = ctx.flat[lhs_key]
    rhs_value, rhs_status, rhs_repr = _resolve_compared_with(rhs, ctx)

    if rhs_status == "missing":
        return ConditionResult(
            status=ConditionStatus.MISSING,
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
            tag=lhs_key,
            snapshot_key=lhs_key,
            comparison_operator=operator,
            compared_with=rhs,
            actual_value=lhs_value,
            reason=f"{rhs_repr} is not in the runtime snapshot.",
        )
    if rhs_status == "unsupported":
        return _unsupported(
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
            tag=lhs_key,
            comparison_operator=operator,
            compared_with=rhs,
            actual_value=lhs_value,
            reason=f"Cannot parse {rhs_repr!r} as a comparison operand.",
        )

    outcome = _apply_comparison(lhs_value, operator, rhs_value)
    if outcome is None:
        return _unsupported(
            natural_language=natural_language,
            instruction_type=instruction_type,
            or_branch_index=or_branch_index,
            tag=lhs_key,
            comparison_operator=operator,
            compared_with=rhs,
            actual_value=lhs_value,
            reason=(
                f"Cannot apply operator {operator!r} to "
                f"{_format_value(lhs_value)} and {_format_value(rhs_value)}."
            ),
        )

    return ConditionResult(
        status=(
            ConditionStatus.SATISFIED if outcome else ConditionStatus.BLOCKING
        ),
        natural_language=natural_language,
        instruction_type=instruction_type,
        or_branch_index=or_branch_index,
        tag=lhs_key,
        snapshot_key=lhs_key,
        comparison_operator=operator,
        compared_with=rhs,
        actual_value=lhs_value,
        reason=(
            f"{lhs_key} ({_format_value(lhs_value)}) {operator} "
            f"{_format_value(rhs_value)} -> "
            + ("TRUE." if outcome else "FALSE.")
        ),
    )


def _resolve_compared_with(
    compared_with: Any,
    ctx: _RuntimeEvalContext,
) -> tuple[Any, str, str]:
    """Resolve a comparison RHS to a Python value.

    Returns ``(value, status, repr)`` where ``status`` is one of
    ``"ok"`` / ``"missing"`` / ``"unsupported"``.

    * Booleans / numbers pass through unchanged.
    * String literals are parsed as ``TRUE`` / ``FALSE`` / int /
      float when possible.
    * Otherwise the string is treated as a tag reference and looked
      up in the snapshot; absent tags produce ``"missing"``.
    """

    if compared_with is None:
        return None, "unsupported", "None"
    if isinstance(compared_with, bool):
        return compared_with, "ok", repr(compared_with)
    if isinstance(compared_with, (int, float)):
        return compared_with, "ok", repr(compared_with)
    if isinstance(compared_with, str):
        s = compared_with.strip()
        if not s:
            return None, "unsupported", repr(s)
        upper = s.upper()
        if upper == "TRUE":
            return True, "ok", "TRUE"
        if upper == "FALSE":
            return False, "ok", "FALSE"
        # Numeric literal?
        try:
            if "." in s or "e" in s.lower():
                return float(s), "ok", s
            return int(s), "ok", s
        except ValueError:
            pass
        # Identifier-ish (Tag.Member or Tag[0]) -> snapshot lookup.
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.\[\]]*$", s):
            if s not in ctx.flat:
                return None, "missing", s
            if _quality_blocks_evaluation(ctx.quality.get(s, "good")):
                return None, "missing", s
            return ctx.flat[s], "ok", s
        return None, "unsupported", s
    return compared_with, "ok", repr(compared_with)


def _apply_comparison(
    lhs: Any, operator: str, rhs: Any
) -> Optional[bool]:
    """Apply a supported comparison operator. Returns ``None`` on
    type errors (e.g. comparing a string to a number) so the caller
    can surface an ``unsupported`` outcome.
    """

    def _numify(v: Any) -> Any:
        # Booleans are subclass of int in Python; coerce explicitly so
        # ``True > 0`` reads as ``1 > 0`` rather than identity-ish.
        if isinstance(v, bool):
            return int(v)
        return v

    try:
        if operator == "=":
            if isinstance(lhs, bool) or isinstance(rhs, bool):
                return bool(lhs) == bool(rhs)
            return lhs == rhs
        if operator == "<>":
            if isinstance(lhs, bool) or isinstance(rhs, bool):
                return bool(lhs) != bool(rhs)
            return lhs != rhs
        if operator == ">":
            return _numify(lhs) > _numify(rhs)
        if operator == ">=":
            return _numify(lhs) >= _numify(rhs)
        if operator == "<":
            return _numify(lhs) < _numify(rhs)
        if operator == "<=":
            return _numify(lhs) <= _numify(rhs)
    except (TypeError, ValueError):
        return None
    return None


# ===========================================================================
# Aggregation / classification
# ===========================================================================


def _classify_path_status(
    conditions: list[ConditionResult],
) -> PathStatus:
    """Roll a writer's per-condition results into a path verdict.

    A writer with zero conditions is treated as ``path_satisfied`` (no
    gating means the writer always fires when scanned). Otherwise:

    * Single OR-branch -> classic AND group.
    * Multiple OR-branches -> OR aggregation across branches.
    """

    if not conditions:
        return PathStatus.PATH_SATISFIED

    branches: dict[int, list[ConditionResult]] = {}
    for c in conditions:
        branches.setdefault(c.or_branch_index, []).append(c)

    if len(branches) == 1:
        return _classify_and_terms(next(iter(branches.values())))
    return _classify_or_branches(branches)


def _classify_and_terms(terms: list[ConditionResult]) -> PathStatus:
    """Classify an AND group: any blocker dominates."""

    if any(t.status == ConditionStatus.BLOCKING for t in terms):
        return PathStatus.PATH_BLOCKED
    if all(t.status == ConditionStatus.UNSUPPORTED for t in terms):
        return PathStatus.PATH_UNSUPPORTED
    if any(t.status == ConditionStatus.MISSING for t in terms):
        return PathStatus.PATH_INCOMPLETE
    return PathStatus.PATH_SATISFIED


def _classify_or_branches(
    branches: dict[int, list[ConditionResult]],
) -> PathStatus:
    """Classify an OR group: any satisfied branch satisfies the path."""

    statuses = [_classify_and_terms(t) for t in branches.values()]
    if any(s == PathStatus.PATH_SATISFIED for s in statuses):
        return PathStatus.PATH_SATISFIED
    if any(s == PathStatus.PATH_INCOMPLETE for s in statuses):
        return PathStatus.PATH_INCOMPLETE
    if all(s == PathStatus.PATH_UNSUPPORTED for s in statuses):
        return PathStatus.PATH_UNSUPPORTED
    return PathStatus.PATH_BLOCKED


def _classify_write_effect(
    instruction_type: Optional[str],
    assigned_value: Optional[Any],
) -> WriteEffect:
    """Categorize a writer by whether it sets the target true / false.

    Ladder instruction types are the primary signal. ST writers don't
    carry an instruction type but they do carry ``assigned_value``
    (``"TRUE"`` / ``"FALSE"`` / something else) which we use as a
    secondary signal.
    """

    if instruction_type:
        itype = instruction_type.upper()
        if itype in _TRUE_SETTER_ITYPES:
            return WriteEffect.SETS_TRUE
        if itype in _FALSE_SETTER_ITYPES:
            return WriteEffect.SETS_FALSE
    if assigned_value is not None:
        av = str(assigned_value).strip().upper()
        if av == "TRUE":
            return WriteEffect.SETS_TRUE
        if av == "FALSE":
            return WriteEffect.SETS_FALSE
    return WriteEffect.OTHER


def _compute_overall_verdict(
    paths: list[WriterPathResult],
) -> OverallVerdict:
    """Combine per-path outcomes into the final operational verdict."""

    if not paths:
        return OverallVerdict.INCOMPLETE

    satisfied_true = [
        p
        for p in paths
        if p.status == PathStatus.PATH_SATISFIED
        and p.write_effect == WriteEffect.SETS_TRUE
    ]
    satisfied_false = [
        p
        for p in paths
        if p.status == PathStatus.PATH_SATISFIED
        and p.write_effect == WriteEffect.SETS_FALSE
    ]
    satisfied_other = [
        p
        for p in paths
        if p.status == PathStatus.PATH_SATISFIED
        and p.write_effect == WriteEffect.OTHER
    ]

    if satisfied_true and satisfied_false:
        return OverallVerdict.CONFLICT_OR_SCAN_ORDER_DEPENDENT

    if satisfied_true or satisfied_other:
        return OverallVerdict.TARGET_CAN_BE_ON

    if satisfied_false:
        return OverallVerdict.TARGET_LIKELY_OFF_OR_RESET

    if any(p.status == PathStatus.PATH_INCOMPLETE for p in paths):
        return OverallVerdict.INCOMPLETE

    return OverallVerdict.BLOCKED


def _detect_conflicts(
    paths: list[WriterPathResult],
) -> list[dict[str, Any]]:
    """List ``(true-setter, false-setter)`` writer-path pairs whose
    simultaneous satisfaction triggers
    ``conflict_or_scan_order_dependent``.

    Each entry carries enough metadata for a UI to render the conflict
    without re-walking ``writer_path_results``.
    """

    satisfied_true = [
        p
        for p in paths
        if p.status == PathStatus.PATH_SATISFIED
        and p.write_effect == WriteEffect.SETS_TRUE
    ]
    satisfied_false = [
        p
        for p in paths
        if p.status == PathStatus.PATH_SATISFIED
        and p.write_effect == WriteEffect.SETS_FALSE
    ]
    out: list[dict[str, Any]] = []
    for t_path in satisfied_true:
        for f_path in satisfied_false:
            out.append(
                {
                    "kind": "true_vs_false_setter",
                    "true_writer": {
                        "location": t_path.location,
                        "instruction_type": t_path.instruction_type,
                    },
                    "false_writer": {
                        "location": f_path.location,
                        "instruction_type": f_path.instruction_type,
                    },
                }
            )
    return out


def _aggregate_conditions(
    paths: list[WriterPathResult],
) -> dict[str, list[dict[str, Any]]]:
    """Flatten per-path condition lists into four overall buckets."""

    buckets: dict[str, list[dict[str, Any]]] = {
        "satisfied": [],
        "blocking": [],
        "missing": [],
        "unsupported": [],
    }
    for p in paths:
        for c in p.conditions:
            entry = c.to_dict()
            entry["location"] = p.location
            entry["writer_instruction_type"] = p.instruction_type
            if c.status == ConditionStatus.SATISFIED:
                buckets["satisfied"].append(entry)
            elif c.status == ConditionStatus.BLOCKING:
                buckets["blocking"].append(entry)
            elif c.status == ConditionStatus.MISSING:
                buckets["missing"].append(entry)
            elif c.status == ConditionStatus.UNSUPPORTED:
                buckets["unsupported"].append(entry)
    return buckets


# ===========================================================================
# Conclusion builders
# ===========================================================================


def _build_primary_conclusion(
    verdict: OverallVerdict,
    target_name: str,
    target_id: str,
    aggregated: dict[str, list[dict[str, Any]]],
    conflicts: list[dict[str, Any]],
) -> TruthConclusion:
    """Build the single-sentence operational verdict conclusion.

    Wording matches the spec examples wherever possible so downstream
    consumers (and tests) can pattern-match deterministically.
    """

    if verdict == OverallVerdict.TARGET_CAN_BE_ON:
        statement = (
            f"{target_name} can be energized: all known required "
            f"runtime conditions are satisfied."
        )
        confidence = ConfidenceLevel.HIGH
    elif verdict == OverallVerdict.TARGET_LIKELY_OFF_OR_RESET:
        statement = (
            f"{target_name} is likely OFF or held in reset: a reset or "
            f"unlatch writer path is currently satisfied while no "
            f"energizing writer path is."
        )
        confidence = ConfidenceLevel.HIGH
    elif verdict == OverallVerdict.CONFLICT_OR_SCAN_ORDER_DEPENDENT:
        statement = (
            f"{target_name} has conflicting runtime paths: one writer "
            f"assigns TRUE while another writer assigns FALSE. Final "
            f"value may depend on execution order."
        )
        confidence = ConfidenceLevel.MEDIUM
    elif verdict == OverallVerdict.INCOMPLETE:
        missing_tags = _unique_preserve_order(
            [m.get("tag") for m in aggregated["missing"] if m.get("tag")]
        )
        if missing_tags:
            verb = "is" if len(missing_tags) == 1 else "are"
            statement = (
                f"{target_name} cannot be fully evaluated because "
                f"{_oxford_join(missing_tags)} {verb} missing from the "
                f"runtime snapshot."
            )
        else:
            statement = (
                f"{target_name} cannot be fully evaluated from the "
                f"runtime snapshot."
            )
        confidence = ConfidenceLevel.LOW
    elif verdict == OverallVerdict.BLOCKED:
        statement = _blocked_statement(target_name, aggregated["blocking"])
        confidence = ConfidenceLevel.HIGH
    else:
        # Defensive: unreachable today, but never blow up the trace.
        statement = f"{target_name}: runtime verdict {verdict.value}."
        confidence = ConfidenceLevel.LOW

    return TruthConclusion(
        statement=statement,
        subject_ids=[target_id],
        truth_context=TruthContextType.COMPOSITE_TRUTH,
        confidence=confidence,
        platform_specific={
            "trace_v2_kind": _KIND_RUNTIME_V2_VERDICT,
            "overall_verdict": verdict.value,
            "blocking_count": len(aggregated["blocking"]),
            "missing_count": len(aggregated["missing"]),
            "satisfied_count": len(aggregated["satisfied"]),
            "unsupported_count": len(aggregated["unsupported"]),
            "conflict_count": len(conflicts),
        },
    )


def _blocked_statement(
    target_name: str, blocking: list[dict[str, Any]]
) -> str:
    """Render the blocked-verdict headline.

    Single-sentence form mirrors the spec example:
    *"PMP_LiOH_B_Run is blocked because Faults.PumpB_LiOH is TRUE."*
    For comparison conditions we extend the wording to surface the
    required relationship: *"... and must be greater than 80."*
    """

    if not blocking:
        return (
            f"{target_name} is blocked: no writer path is currently "
            f"satisfied for this target."
        )
    first = blocking[0]
    # Prefer the qualified snapshot key (e.g. ``Timer1.DN``) over the
    # bare tag so a member-access blocker reads correctly.
    display_key = (
        first.get("snapshot_key") or first.get("tag") or "an unknown tag"
    )
    op = first.get("comparison_operator")
    actual = first.get("actual_value")
    if op:
        rhs = first.get("compared_with")
        return (
            f"{target_name} is blocked because {display_key} is "
            f"{_format_value(actual)} and must {_op_phrase(op)} "
            f"{_format_value(rhs)}."
        )
    return (
        f"{target_name} is blocked because {display_key} is "
        f"{_format_bool_value(actual)}."
    )


def _build_per_path_conclusion(
    path: WriterPathResult,
    target_id: str,
) -> TruthConclusion:
    """Build a per-writer-path summary conclusion.

    Slots into ``conclusions`` after the primary verdict so an
    engineer reading top-down sees the verdict first, then the
    per-writer breakdown, then the existing Trace v2 / v1 detail.
    """

    instr = path.instruction_type or "writer"
    location = path.location or "an unknown location"

    if path.status == PathStatus.PATH_SATISFIED:
        statement = (
            f"Writer path {instr} in {location} is satisfied: all "
            f"runtime conditions match."
        )
        confidence = ConfidenceLevel.HIGH
    elif path.status == PathStatus.PATH_BLOCKED:
        blocking_descs = _list_blocked_term_tags(path)
        if blocking_descs:
            statement = (
                f"Writer path {instr} in {location} is blocked by "
                f"{_oxford_join(blocking_descs)}."
            )
        else:
            statement = (
                f"Writer path {instr} in {location} is blocked."
            )
        confidence = ConfidenceLevel.HIGH
    elif path.status == PathStatus.PATH_INCOMPLETE:
        missing_tags = _unique_preserve_order(
            [
                c.tag
                for c in path.conditions
                if c.status == ConditionStatus.MISSING and c.tag
            ]
        )
        if missing_tags:
            statement = (
                f"Writer path {instr} in {location} is incomplete: "
                f"missing runtime values for {_oxford_join(missing_tags)}."
            )
        else:
            statement = (
                f"Writer path {instr} in {location} is incomplete: "
                f"runtime data is missing."
            )
        confidence = ConfidenceLevel.LOW
    else:
        statement = (
            f"Writer path {instr} in {location} contains conditions "
            f"that runtime v2 cannot evaluate deterministically."
        )
        confidence = ConfidenceLevel.LOW

    subject_ids = [target_id]
    if path.source_id:
        subject_ids.append(path.source_id)

    return TruthConclusion(
        statement=statement,
        subject_ids=subject_ids,
        truth_context=TruthContextType.COMPOSITE_TRUTH,
        confidence=confidence,
        platform_specific={
            "trace_v2_kind": _KIND_RUNTIME_V2_PATH,
            "path_status": path.status.value,
            "write_effect": path.write_effect.value,
            "location": path.location,
            "instruction_type": path.instruction_type,
        },
    )


def _list_blocked_term_tags(path: WriterPathResult) -> list[str]:
    """Collect the blocking tag names for the per-path narrative."""

    out: list[str] = []
    for c in path.conditions:
        if c.status != ConditionStatus.BLOCKING:
            continue
        if c.tag:
            out.append(c.tag)
    return _unique_preserve_order(out)


# ===========================================================================
# Misc helpers
# ===========================================================================


def _unsupported(
    natural_language: str,
    instruction_type: Optional[str],
    or_branch_index: int,
    tag: Optional[str] = None,
    comparison_operator: Optional[str] = None,
    compared_with: Any = None,
    actual_value: Any = None,
    reason: str = "",
) -> ConditionResult:
    """Build an ``unsupported`` ConditionResult with a helpful reason."""

    return ConditionResult(
        status=ConditionStatus.UNSUPPORTED,
        natural_language=natural_language,
        instruction_type=instruction_type,
        or_branch_index=or_branch_index,
        tag=tag,
        comparison_operator=comparison_operator,
        compared_with=compared_with,
        actual_value=actual_value,
        reason=reason,
    )


def _infer_member_from_natural_language(text: str) -> Optional[str]:
    """Return DN / TT / EN / ACC / PRE when the phrase names a member.

    Used only as a fallback when the condition row has no explicit
    ``member`` field -- new traces should always carry the field
    directly (see :mod:`app.services.trace_v2_service`).
    """

    if not text:
        return None
    lowered = text.lower()
    for phrase, member in _MEMBER_PHRASE_TO_MEMBER.items():
        if phrase in lowered:
            return member
    return None


def _values_match(actual: object, required: bool) -> bool:
    """Compare a runtime value to a required boolean.

    * ``bool``  -- direct identity comparison.
    * ``int`` / ``float`` -- truthy semantics (``0`` -> FALSE, every
      other value -> TRUE). Lets ACC / PRE non-zero checks work and
      handles connectors that return BOOLs as ints.
    * Anything else -- conservative mismatch (i.e. blocking) so a
      stray ``"FALSE"`` string isn't silently coerced to truthy.
    """

    if isinstance(actual, bool):
        return actual is required
    if isinstance(actual, (int, float)):
        return (actual != 0) is required
    return False


def _format_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _format_bool_value(value: Any) -> str:
    if isinstance(value, bool):
        return _format_bool(value)
    if value is None:
        return "unknown"
    return repr(value)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return _format_bool(value)
    if value is None:
        return "unknown"
    if isinstance(value, str):
        return value
    return repr(value)


def _op_phrase(op: str) -> str:
    return _OP_PHRASE.get(op, op)


def _short_target_name(target_object_id: str) -> str:
    """Derive a human-friendly display name from a target id."""

    if "/" in target_object_id:
        return target_object_id.rsplit("/", 1)[-1]
    if "::" in target_object_id:
        return target_object_id.split("::", 1)[-1]
    return target_object_id


def _unique_preserve_order(items: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    out: list[Any] = []
    for x in items:
        if x is None or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _oxford_join(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


__all__ = [
    "ConditionStatus",
    "PathStatus",
    "OverallVerdict",
    "WriteEffect",
    "ConditionResult",
    "WriterPathResult",
    "evaluate_trace_runtime_v2",
]
