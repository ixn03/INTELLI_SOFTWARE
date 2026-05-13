"""Runtime Snapshot Evaluation v1.

Evaluates Trace v2 ``writer_conditions`` against a caller-supplied
runtime snapshot of tag values. No live PLC, no historian, no LLM, no
database -- the caller passes a plain ``{tag: value}`` dict and the
function deterministically compares each gating condition to the
provided value.

The runtime layer extends Trace v2 by:

* reading every conclusion whose ``platform_specific["trace_v2_kind"]``
  is ``"writer_conditions"`` and walking its ``conditions`` list,
* for each condition that carries a plain ``(tag, required_value)``
  pair (i.e. XIC/XIO style), comparing the snapshot value to the
  required value,
* prepending one :class:`TruthConclusion` per result -- satisfied,
  blocking, or missing -- so the natural-language story now reads
  "design says X must be FALSE, runtime says X is TRUE -> X is
  blocking the writer.",
* augmenting :pyattr:`TraceResult.summary` with a single-sentence
  English summary keyed off the worst observed bucket, and
* recording the structured per-bucket data on ``platform_specific``
  so UI / API consumers can render the result without re-parsing
  statements.

Conservative aggregation
------------------------

When a trace has multiple ``writer_conditions`` conclusions (e.g. an
``OTL`` latch *and* an ``OTU`` unlatch on the same target), every
condition is bucketed independently and no attempt is made to figure
out which writer "wins" at runtime. If any required value is blocked,
the target is reported as blocked. Engineers always see every
contributing condition, even when one path looks clean on its own.

Conditions that don't reduce to a plain boolean check -- comparison
gates (EQU/NEQ/GRT/...), one-shot storage reads, timer/counter member
reads -- are skipped silently. They remain in the underlying Trace v2
conclusion for inspection but are not surfaced as a runtime verdict.
"""

from __future__ import annotations

from typing import Any, Optional

from app.models.reasoning import (
    ConfidenceLevel,
    TraceResult,
    TruthConclusion,
    TruthContextType,
)


# ---------------------------------------------------------------------------
# Public type alias
# ---------------------------------------------------------------------------
#
# ``RuntimeSnapshot`` documents the expected shape of the snapshot a
# caller passes in. At the moment this layer only meaningfully consumes
# boolean values (because Trace v2 ``writer_conditions`` reduce to
# ``(tag, required_value: bool)`` pairs), but int / float are accepted
# and treated as truthiness so int-encoded BOOLs from a connector still
# match. Strings and other non-numeric types are conservatively flagged
# as blocking when used to satisfy a boolean condition.
RuntimeSnapshot = dict[str, bool | int | float | str]


# ---------------------------------------------------------------------------
# Markers attached to ``platform_specific`` so UI / API consumers can
# tell runtime conclusions apart from design-time Trace v2 ones.
# ---------------------------------------------------------------------------

_TRACE_VERSION = "runtime_v1"
_KIND_WRITER_CONDITIONS = "writer_conditions"

_RUNTIME_KIND_SATISFIED = "runtime_satisfied"
_RUNTIME_KIND_BLOCKING = "runtime_blocking"
_RUNTIME_KIND_MISSING = "runtime_missing"


def evaluate_trace_conditions(
    trace_result: TraceResult,
    runtime_snapshot: dict[str, object],
) -> TraceResult:
    """Compare Trace v2 writer conditions to a runtime snapshot.

    For each conclusion in ``trace_result`` whose
    ``platform_specific["trace_v2_kind"] == "writer_conditions"``, walk
    the ``conditions`` list and compare every ``(tag, required_value)``
    pair to the value supplied in ``runtime_snapshot``. The result is
    bucketed into one of three groups:

    * **satisfied**: the snapshot value matches the required value.
    * **blocking**: the snapshot has a value, but it doesn't match the
      required value (e.g. ``XIO(Faulted)`` required FALSE but the
      snapshot says ``Faulted = True``).
    * **missing**: the tag is not present in the snapshot at all.

    Side effects on ``trace_result``:

    1. Prepends one :class:`TruthConclusion` per bucketed condition
       ahead of the existing Trace v2 / v1 conclusions. Existing
       conclusions are preserved verbatim.
    2. Augments ``summary`` with a single-sentence English summary --
       blocked-by, missing-values, and / or all-satisfied -- as
       appropriate. Any existing summary is appended after the
       runtime summary so v2 wording stays visible.
    3. Sets ``platform_specific["trace_version"] = "runtime_v1"``,
       ``platform_specific["runtime_snapshot_evaluated"] = True``,
       and stores the satisfied / blocking / missing lists for
       structured access.

    The function mutates ``trace_result`` in place and returns the
    same instance, matching the pattern used by
    :func:`app.services.trace_v2_service.trace_object_v2`.
    """

    target_name = _short_target_name(trace_result.target_object_id)

    satisfied: list[dict[str, Any]] = []
    blocking: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    runtime_conclusions: list[TruthConclusion] = []

    for conclusion in trace_result.conclusions:
        meta = conclusion.platform_specific or {}
        if meta.get("trace_v2_kind") != _KIND_WRITER_CONDITIONS:
            continue
        location = str(meta.get("location") or "")
        instruction_type = meta.get("instruction_type")
        conditions = meta.get("conditions") or []
        subject_ids = list(conclusion.subject_ids or [])

        for raw in conditions:
            if not isinstance(raw, dict):
                continue
            tag = raw.get("tag")
            required = raw.get("required_value")
            # Skip anything that isn't a plain ``(str tag, bool required)``
            # pair. Comparison phrases, one-shot reads, and timer/counter
            # member reads land here -- they don't reduce to a single
            # boolean check, so the v1 runtime layer leaves them for
            # future versions to interpret.
            if not isinstance(tag, str):
                continue
            if not isinstance(required, bool):
                continue

            record: dict[str, Any] = {
                "tag": tag,
                "required_value": required,
                "location": location,
                "instruction_type": instruction_type,
                "subject_ids": list(subject_ids),
            }

            if tag not in runtime_snapshot:
                record["actual_value"] = None
                missing.append(record)
                runtime_conclusions.append(_missing_conclusion(record))
                continue

            actual = runtime_snapshot[tag]
            record["actual_value"] = actual
            if _values_match(actual, required):
                satisfied.append(record)
                runtime_conclusions.append(_satisfied_conclusion(record))
            else:
                blocking.append(record)
                runtime_conclusions.append(_blocking_conclusion(record))

    summary_text = _build_runtime_summary(
        target_name=target_name,
        satisfied=satisfied,
        blocking=blocking,
        missing=missing,
    )

    trace_result.conclusions = runtime_conclusions + list(
        trace_result.conclusions
    )

    if summary_text:
        if trace_result.summary:
            trace_result.summary = f"{summary_text} {trace_result.summary}"
        else:
            trace_result.summary = summary_text

    trace_result.platform_specific = dict(trace_result.platform_specific or {})
    trace_result.platform_specific["trace_version"] = _TRACE_VERSION
    trace_result.platform_specific["runtime_snapshot_evaluated"] = True
    trace_result.platform_specific["satisfied_conditions"] = satisfied
    trace_result.platform_specific["blocking_conditions"] = blocking
    trace_result.platform_specific["missing_conditions"] = missing

    return trace_result


# ===========================================================================
# Conclusion factories
# ===========================================================================


def _satisfied_conclusion(record: dict[str, Any]) -> TruthConclusion:
    """Produce a HIGH-confidence runtime_satisfied conclusion."""

    tag = record["tag"]
    required = record["required_value"]
    location = record["location"]
    required_str = _format_bool(required)
    loc_clause = f" (required by {location})" if location else ""

    statement = f"{tag} is {required_str} as required{loc_clause}."

    return TruthConclusion(
        statement=statement,
        subject_ids=list(record.get("subject_ids") or []),
        truth_context=TruthContextType.COMPOSITE_TRUTH,
        confidence=ConfidenceLevel.HIGH,
        recommended_checks=[],
        platform_specific={
            "trace_v2_kind": _RUNTIME_KIND_SATISFIED,
            "tag": tag,
            "required_value": required,
            "actual_value": record["actual_value"],
            "location": location,
            "instruction_type": record.get("instruction_type"),
        },
    )


def _blocking_conclusion(record: dict[str, Any]) -> TruthConclusion:
    """Produce a HIGH-confidence runtime_blocking conclusion.

    The wording mirrors the spec example: "Faulted is blocking because
    it is TRUE but must be FALSE." Source location, when known, is
    appended in parentheses so the operator can find the rung that
    requires the value.
    """

    tag = record["tag"]
    required = record["required_value"]
    actual = record["actual_value"]
    location = record["location"]

    required_str = _format_bool(required)
    actual_str = _format_actual_value(actual)
    loc_clause = f" (required at {location})" if location else ""

    statement = (
        f"{tag} is blocking because it is {actual_str} but must be "
        f"{required_str}{loc_clause}."
    )

    return TruthConclusion(
        statement=statement,
        subject_ids=list(record.get("subject_ids") or []),
        truth_context=TruthContextType.COMPOSITE_TRUTH,
        confidence=ConfidenceLevel.HIGH,
        recommended_checks=[
            f"Verify why {tag} is {actual_str} on the live system.",
        ],
        platform_specific={
            "trace_v2_kind": _RUNTIME_KIND_BLOCKING,
            "tag": tag,
            "required_value": required,
            "actual_value": actual,
            "location": location,
            "instruction_type": record.get("instruction_type"),
        },
    )


def _missing_conclusion(record: dict[str, Any]) -> TruthConclusion:
    """Produce a LOW-confidence runtime_missing conclusion.

    Confidence is LOW because the runtime layer can't say anything
    about the actual state of the tag -- the operator has to capture
    the value before this condition can be evaluated.
    """

    tag = record["tag"]
    required = record["required_value"]
    location = record["location"]
    required_str = _format_bool(required)
    loc_clause = f" (required at {location})" if location else ""

    statement = (
        f"Runtime value for {tag} is not provided in the snapshot; "
        f"it must be {required_str}{loc_clause} for the writer to fire."
    )

    return TruthConclusion(
        statement=statement,
        subject_ids=list(record.get("subject_ids") or []),
        truth_context=TruthContextType.RUNTIME_TRUTH,
        confidence=ConfidenceLevel.LOW,
        recommended_checks=[
            f"Capture the current value of {tag} from the live system.",
        ],
        platform_specific={
            "trace_v2_kind": _RUNTIME_KIND_MISSING,
            "tag": tag,
            "required_value": required,
            "actual_value": None,
            "location": location,
            "instruction_type": record.get("instruction_type"),
        },
    )


# ===========================================================================
# Helpers
# ===========================================================================


def _short_target_name(target_object_id: str) -> str:
    """Derive a human-friendly display name from a target id.

    Trace v2 produces ids like ``tag::PLC01/MainProgram/Pump_Run`` whose
    last path component is the tag name. We fall back to the part after
    ``::`` (and then to the raw id) so the helper is total.
    """

    if "/" in target_object_id:
        return target_object_id.rsplit("/", 1)[-1]
    if "::" in target_object_id:
        return target_object_id.split("::", 1)[-1]
    return target_object_id


def _values_match(actual: object, required: bool) -> bool:
    """Compare a runtime value to a required boolean.

    * ``bool`` -- direct identity comparison.
    * ``int`` / ``float`` -- treat as truthiness (``0`` -> FALSE, every
      other value -> TRUE). Some PLC connectors hand boolean tags back
      as ints; this keeps that path matching.
    * Anything else (``str``, ``None``, custom types) -- can't be
      safely interpreted as a boolean, so it's reported as a mismatch
      (i.e. blocking). The structured record still carries the raw
      value so a UI can show what was actually supplied.
    """

    if isinstance(actual, bool):
        return actual is required
    if isinstance(actual, (int, float)):
        return (actual != 0) is required
    return False


def _format_bool(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def _format_actual_value(actual: object) -> str:
    if isinstance(actual, bool):
        return _format_bool(actual)
    if actual is None:
        return "unknown"
    return repr(actual)


def _build_runtime_summary(
    target_name: str,
    satisfied: list[dict[str, Any]],
    blocking: list[dict[str, Any]],
    missing: list[dict[str, Any]],
) -> str:
    """Build the single-sentence English summary for the runtime pass.

    Order of clauses mirrors the spec:

    * blocking first -- the operator most needs to know what's
      actually wrong;
    * then "all known conditions satisfied" *only* when there's no
      blocking and no missing (so it isn't misleading);
    * then a "values are missing" clause when applicable.

    Each clause is independent: a trace with both blocking conditions
    and missing values yields a two-sentence summary, e.g.
    "Pump is blocked by Faulted. Runtime values are missing for
    StartPB."
    """

    parts: list[str] = []

    if blocking:
        names = _unique_preserve_order([c["tag"] for c in blocking])
        parts.append(f"{target_name} is blocked by {_oxford_join(names)}.")

    if not blocking and not missing and satisfied:
        parts.append(
            f"All known runtime conditions for {target_name} are satisfied."
        )

    if missing:
        names = _unique_preserve_order([c["tag"] for c in missing])
        parts.append(
            f"Runtime values are missing for {_oxford_join(names)}."
        )

    return " ".join(parts)


def _unique_preserve_order(items: list[str]) -> list[str]:
    """Deduplicate while preserving first-seen order."""

    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


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
    "RuntimeSnapshot",
    "evaluate_trace_conditions",
]
