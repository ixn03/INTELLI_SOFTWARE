"""Simple, deterministic Structured Text extraction for Trace v2.

This module is intentionally narrow: it does **not** evaluate ST code,
it does not handle full IEC-61131 grammar, and it never falls back to
an LLM. Trace v2 uses it to surface readable conditions for the most
common boolean-logic patterns; anything outside that envelope returns
``None`` and the caller renders a canonical "too complex" message.

Supported patterns
------------------

1. Direct boolean assignment::

       Motor_Run := StartPB AND AutoMode AND NOT Faulted;

   -> "Motor_Run is assigned TRUE when StartPB is TRUE, AutoMode is
      TRUE, and Faulted is FALSE."

2. Direct literal assignment (TRUE / FALSE)::

       Motor_Run := TRUE;

   -> "Motor_Run is assigned TRUE."

3. ``IF <cond> THEN <target> := TRUE; END_IF`` with a single body
   statement::

       IF StartPB AND AutoMode AND NOT Faulted THEN
           Motor_Run := TRUE;
       END_IF;

   -> "Motor_Run is assigned TRUE when StartPB is TRUE, AutoMode is
      TRUE, and Faulted is FALSE."

Boolean operators supported in the condition / RHS expression:
``AND``, ``NOT``. ``OR``, parentheses, comparisons, arithmetic, nested
IFs, ``ELSE`` / ``ELSIF``, multi-statement bodies, and function calls
all cause the extractor to return ``None``.

Public API
----------

* ``STCondition``           -- one parsed boolean operand.
* ``STExtractionResult``    -- a parsed assignment + its conditions.
* ``extract_simple_st_conditions(text, target_name=None)``
  -> ``Optional[STExtractionResult]``.

Determinism: same input -> same output. No I/O, no randomness, no LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class STCondition:
    """A single operand inside a parsed ST boolean conjunction.

    ``required_value`` is the boolean value the operand must take for
    the overall conjunction to be TRUE: ``True`` for a plain operand
    (``StartPB``), ``False`` for a ``NOT`` operand (``NOT Faulted``).
    """

    tag: str
    required_value: bool
    natural_language: str


@dataclass(frozen=True)
class STExtractionResult:
    """Successful parse of a supported ST pattern.

    Fields:
        assigned_target:   left-hand side of the ``:=``.
        assigned_value:    ``"TRUE"`` / ``"FALSE"`` for literal assigns;
                           ``"(boolean expression)"`` for conjunctions.
        conditions:        per-operand parse of the gating expression.
        natural_language:  pre-rendered Trace v2 statement.
        raw_text:          the original ST text that was parsed.
    """

    assigned_target: str
    assigned_value: str
    conditions: list[STCondition]
    natural_language: str
    raw_text: str


# ---------------------------------------------------------------------------
# Regexes (compiled once at import time)
# ---------------------------------------------------------------------------

# An ST identifier. Permits dotted member access (``Pump_01.Run``) so
# we don't reject very common tag names, but disallows array indexing
# and other punctuation that would land us in expression-parsing
# territory.
_IDENT_RE = r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*"

# IF <cond> THEN <target> := <rhs> [;] END_IF [;]
# DOTALL so multi-line bodies are matched. Non-greedy on the inner
# captures so the regex doesn't span past END_IF.
_IF_BLOCK_RE = re.compile(
    rf"""
    ^\s*
    IF\s+(?P<cond>.+?)\s+THEN\s+
    (?P<target>{_IDENT_RE})\s*:=\s*(?P<rhs>.+?)\s*;\s*
    END_IF\s*;?\s*$
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

# Bare assignment: <target> := <rhs>;
_ASSIGNMENT_RE = re.compile(
    rf"^\s*(?P<target>{_IDENT_RE})\s*:=\s*(?P<rhs>.+?)\s*;?\s*$",
    re.DOTALL,
)

# Tokens that, if present in a "boolean conjunction" expression, mean
# we are out of the supported envelope.
_DISALLOWED_TOKEN_RE = re.compile(
    r"""
    \bOR\b               # disjunction
    | \bXOR\b
    | [()]               # parentheses / grouping
    | [+\-*/]            # arithmetic
    | <=|>=|<>|=|<|>     # comparisons (= alone too, since := is gone by now)
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_simple_st_conditions(
    text: Optional[str],
    target_name: Optional[str] = None,
) -> Optional[STExtractionResult]:
    """Return an ``STExtractionResult`` for supported patterns, else None.

    ``target_name`` is currently unused; it is accepted for API
    parity with ``extract_simple_ladder_conditions`` so callers can
    pass it without branching.

    The function is intentionally conservative: when in doubt, it
    returns ``None`` so Trace v2 can render the canonical
    "too complex" message rather than fabricating a wrong sentence.
    """

    del target_name  # reserved for future personalization

    if not text:
        return None
    raw = text.strip()
    if not raw:
        return None

    # 1) Try IF-THEN block first. The bare-assignment regex would also
    #    match "IF ..." as garbage, so the IF check must run first.
    if_match = _IF_BLOCK_RE.match(raw)
    if if_match:
        return _try_parse_if_block(
            raw=raw,
            cond_text=if_match.group("cond"),
            target=if_match.group("target"),
            rhs=if_match.group("rhs"),
        )

    # 2) Bare assignment.
    assign_match = _ASSIGNMENT_RE.match(raw)
    if assign_match:
        return _try_parse_assignment(
            raw=raw,
            target=assign_match.group("target"),
            rhs=assign_match.group("rhs"),
        )

    return None


# ---------------------------------------------------------------------------
# Pattern handlers
# ---------------------------------------------------------------------------


def _try_parse_if_block(
    raw: str,
    cond_text: str,
    target: str,
    rhs: str,
) -> Optional[STExtractionResult]:
    """Parse ``IF <cond> THEN <target> := <rhs>; END_IF``.

    The body must be a single boolean literal assignment
    (``:= TRUE`` / ``:= FALSE``); anything else returns None.
    """

    target = target.strip()
    rhs = rhs.strip().rstrip(";").strip()
    rhs_upper = rhs.upper()
    if rhs_upper not in ("TRUE", "FALSE"):
        # IF cond THEN tag := <expression> END_IF: more than we want to
        # commit to in v2 because the expression can be anything.
        return None

    conditions = parse_boolean_conjunction(cond_text)
    if conditions is None:
        return None

    natural = _format_assignment_natural(
        target=target, assigned_value=rhs_upper, conditions=conditions
    )
    return STExtractionResult(
        assigned_target=target,
        assigned_value=rhs_upper,
        conditions=conditions,
        natural_language=natural,
        raw_text=raw,
    )


def _try_parse_assignment(
    raw: str,
    target: str,
    rhs: str,
) -> Optional[STExtractionResult]:
    """Parse ``<target> := <rhs>;``.

    Three accepted shapes:

    * RHS is a literal ``TRUE`` / ``FALSE`` -> emit a flat statement.
    * RHS is a conjunction of identifiers and ``NOT identifier`` ->
      emit "X is assigned TRUE when ...".
    * Anything else -> None.
    """

    target = target.strip()
    rhs = rhs.strip().rstrip(";").strip()
    rhs_upper = rhs.upper()

    if rhs_upper in ("TRUE", "FALSE"):
        return STExtractionResult(
            assigned_target=target,
            assigned_value=rhs_upper,
            conditions=[],
            natural_language=f"{target} is assigned {rhs_upper}.",
            raw_text=raw,
        )

    conditions = parse_boolean_conjunction(rhs)
    if conditions is None:
        return None

    natural = _format_assignment_natural(
        target=target, assigned_value="TRUE", conditions=conditions
    )
    return STExtractionResult(
        assigned_target=target,
        # The RHS itself is a boolean expression. For natural-language
        # purposes we report it as TRUE (the value the target takes
        # when the expression is satisfied), but we mark the
        # underlying value as "(boolean expression)" so a caller that
        # inspects ``assigned_value`` can tell it wasn't a literal.
        assigned_value="(boolean expression)",
        conditions=conditions,
        natural_language=natural,
        raw_text=raw,
    )


# ---------------------------------------------------------------------------
# Expression parsing
# ---------------------------------------------------------------------------


def parse_boolean_conjunction(expr: str) -> Optional[list[STCondition]]:
    """Parse ``A AND NOT B AND C`` style expressions.

    Returns ``None`` for anything outside that envelope (OR, parens,
    arithmetic, comparisons, function calls, empty operand, ...).

    Public so other ST tooling (the block-aware normalization parser
    in :mod:`app.parsers.structured_text_blocks`) can reuse the same
    deterministic envelope check without duplicating regex.
    """

    if not expr:
        return None
    e = expr.strip().rstrip(";").strip()
    if not e:
        return None

    if _DISALLOWED_TOKEN_RE.search(e):
        return None

    # Split on AND (case-insensitive, whitespace-bounded).
    parts = re.split(r"\s+AND\s+", e, flags=re.IGNORECASE)
    out: list[STCondition] = []
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            return None

        negate = False
        not_match = re.match(r"^NOT\s+(.+)$", part, re.IGNORECASE)
        if not_match:
            negate = True
            part = not_match.group(1).strip()

        if not re.fullmatch(_IDENT_RE, part):
            return None

        required = not negate
        out.append(
            STCondition(
                tag=part,
                required_value=required,
                natural_language=(
                    f"{part} is {'TRUE' if required else 'FALSE'}"
                ),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Natural-language formatting
# ---------------------------------------------------------------------------


def _format_assignment_natural(
    target: str,
    assigned_value: str,
    conditions: list[STCondition],
) -> str:
    if not conditions:
        return f"{target} is assigned {assigned_value}."
    joined = _oxford_join([c.natural_language for c in conditions])
    return f"{target} is assigned {assigned_value} when {joined}."


def _oxford_join(items: list[str]) -> str:
    """Join phrases as ``a``, ``a and b``, or ``a, b, and c``."""

    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


__all__ = [
    "STCondition",
    "STExtractionResult",
    "extract_simple_st_conditions",
    "parse_boolean_conjunction",
]
