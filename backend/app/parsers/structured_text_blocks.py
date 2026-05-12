"""Block-aware Structured Text parser.

This module decomposes a Structured Text routine body into structured
*blocks* (one block per top-level statement) suitable for the
normalization service to convert into reasoning-schema relationships.
It is intentionally narrow and deterministic: anything outside the
small grammar below is preserved as :class:`STComplexBlock` so callers
can still record a "too_complex" marker without crashing.

Supported grammar (case-insensitive on keywords)
------------------------------------------------

1. Direct assignment::

       <target> := <expr> ;

   ``<expr>`` may be:
     * a literal ``TRUE`` or ``FALSE``
     * any boolean expression :func:`app.parsers.st_expression.parse_st_expression`
       can crack: ``AND`` / ``OR`` / ``NOT``, balanced parentheses,
       comparisons (``=`` / ``<>`` / ``<`` / ``<=`` / ``>`` /
       ``>=``), and identifiers with optional dotted member access
       or integer array indexing (``Tank.Level``, ``Bits[3]``).
     * anything else -> the assignment is still emitted, but flagged
       ``too_complex=True`` with no parsed conditions.

2. ``IF`` block (optional ``ELSE``)::

       IF <cond> THEN
           <target> := <expr> ;
           ...
       [ELSE
           <target> := <expr> ;
           ...]
       END_IF ;

   ``<cond>`` uses the same expression envelope as the assignment
   RHS (so ``IF A OR (B AND C) THEN ...`` parses). Body statements
   that aren't simple assignments are intentionally ignored here;
   the normalizer can still consult ``STIfBlock.raw_text``.
   Multiple sequential assignments in either branch are supported.

3. ``CASE`` block (integer or identifier labels, optional ``ELSE``)::

       CASE <selector> OF
           1: <target> := <expr> ;
           2: <target> := <expr> ;
           ELSE: <target> := <expr> ;
       END_CASE ;

   ``<selector>`` must be a bare identifier for the selector tag to
   contribute a READS edge during normalization. Each non-default
   branch carries a short ``condition_summary``
   (``"<selector> = <label>"``) the normalizer surfaces in
   ``platform_specific["case_condition_summary"]``. Each branch
   contains one or more :class:`STAssignment`.

Everything else -- ``WHILE``, ``FOR``, ``REPEAT``, function calls,
nested control flow, arithmetic outside comparison right-hand sides,
unbalanced parentheses -- lands in :class:`STComplexBlock` (top-level)
or sets ``too_complex=True`` on the relevant assignment / IF block,
which the normalizer surfaces with
``st_parse_status="too_complex"``.

The parser does not consume comments. The Rockwell L5X connector
already strips ST line comments via its ``<Line>`` extraction, so by
the time text reaches this parser comments are already gone in
practice; if some slip through they will simply be folded into the
adjacent statement's raw text, which is harmless for normalization.

Determinism
-----------

* Same input -> same output. No I/O, no randomness, no LLM.
* All regex is compiled once at import time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

from app.parsers.st_expression import (
    STComparisonTerm,
    STConjunction,
    STExpressionParse,
    STTerm,
    parse_st_expression,
)
from app.services.structured_text_extraction import STCondition

# ---------------------------------------------------------------------------
# Regex pieces (compiled once)
# ---------------------------------------------------------------------------

# A Structured Text identifier with optional dotted member access and
# integer array indexing. Matches names like ``Motor_Run``,
# ``Pump_01.Run``, ``Tank.Level.SP``, ``Bits[3]``, ``MyArr[5].Value``.
# Conservative: array indices must be bare integers (no expressions),
# and the bracket form may only appear after the head identifier or
# between member segments.
_IDENT = (
    r"[A-Za-z_][A-Za-z_0-9]*"
    r"(?:"
    r"\.[A-Za-z_][A-Za-z_0-9]*"
    r"|\[\d+\]"
    r")*"
)

# Match a single ``<target> := <rhs>;`` assignment. DOTALL so the RHS
# can span lines (rare for booleans but legal). The trailing ``;`` is
# required so we know where the statement ends; assignments without a
# semicolon (e.g. last statement of a body where the user forgot it)
# are handled by the body parser's fallback.
_ASSIGNMENT_RE = re.compile(
    rf"(?P<target>{_IDENT})\s*:=\s*(?P<rhs>.+?)\s*;",
    re.DOTALL,
)

# Match an IF block, with an optional ELSE clause, terminated by
# END_IF (with or without trailing semicolon). The inner captures are
# non-greedy so the regex never spans past END_IF.
_IF_BLOCK_RE = re.compile(
    rf"""
    IF\s+(?P<cond>.+?)\s+THEN\b
    (?P<then_body>.*?)
    (?:\bELSE\b(?P<else_body>.*?))?
    \bEND_IF\b\s*;?
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

# Match a CASE block. Body is captured raw; branches are extracted by
# scanning the body for label markers.
_CASE_BLOCK_RE = re.compile(
    rf"""
    CASE\s+(?P<selector>.+?)\s+OF\b
    (?P<body>.*?)
    \bEND_CASE\b\s*;?
    """,
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

# A case label: ``<int>:`` or ``<identifier>:`` or ``ELSE:``. The
# negative lookahead avoids matching ``:=`` (an assignment). ``ELSE``
# is matched FIRST in the alternation so the default branch isn't
# accidentally captured by the generic identifier alternative.
_CASE_LABEL_RE = re.compile(
    rf"""
    (?:^|;|\n)
    \s*
    (?:(?P<is_else>ELSE)\b|(?P<label>{_IDENT}|\d+))
    \s*:(?!=)
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ---------------------------------------------------------------------------
# Public block dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class STAssignment:
    """A single ``<target> := <expr>;`` assignment.

    Attributes:
        target: The bare identifier being assigned.
        raw_expression: The original RHS text (no trailing semicolon).
        assigned_value: ``True`` / ``False`` if the RHS was a literal
            boolean, else ``None``.
        conditions: Legacy view of the RHS as a flat boolean
            conjunction (identifier terms only). Populated **only**
            when the RHS is a simple conjunction of identifiers
            (``A AND NOT B``); empty for RHS shapes that include
            ``OR`` / comparisons / parens. Newer code should consume
            :attr:`expression` instead.
        expression: Full DNF parse of the RHS produced by
            :func:`app.parsers.st_expression.parse_st_expression`.
            ``None`` when the RHS was a literal ``TRUE`` / ``FALSE``
            (there is no condition to parse).
        too_complex: ``True`` when the RHS could not be parsed as a
            boolean literal, a conjunction, a disjunction, or a
            comparison-bearing expression.
        raw_text: The original ST snippet (incl. trailing ``;``).
        statement_index: Monotonic index within the containing scope
            (top-level routine, then THEN body, then ELSE body, then
            per-CASE-branch). Used to build stable ``Statement[N]``
            location strings.
    """

    target: str
    raw_expression: str
    assigned_value: Optional[bool] = None
    conditions: list[STCondition] = field(default_factory=list)
    expression: Optional[STExpressionParse] = None
    too_complex: bool = False
    raw_text: str = ""
    statement_index: int = 0


@dataclass(frozen=True)
class STIfBlock:
    """A parsed ``IF [ELSE] END_IF`` block.

    ``condition_terms`` is the legacy flat-conjunction view of the
    THEN-branch gating condition; populated only when the condition
    is a simple AND-conjunction of identifiers.
    ``condition_expression`` is the full DNF parse from
    :func:`app.parsers.st_expression.parse_st_expression` (also used
    for OR conditions, comparisons, and parens). The ELSE branch's
    effective condition is the boolean negation of THEN's; we can
    only mechanically invert it when THEN is a single identifier or
    a single comparison term. Anything else sets
    ``else_too_complex=True`` so the normalizer records ELSE writes
    without inventing gating conditions.
    """

    condition_raw: str
    condition_terms: list[STCondition] = field(default_factory=list)
    condition_expression: Optional[STExpressionParse] = None
    too_complex_condition: bool = False
    else_too_complex: bool = False
    then_assignments: list[STAssignment] = field(default_factory=list)
    else_assignments: list[STAssignment] = field(default_factory=list)
    raw_text: str = ""
    statement_index: int = 0


@dataclass(frozen=True)
class STCaseBranch:
    """One branch of a CASE statement.

    ``label`` is the literal text of the case label (``"1"``,
    ``"Idle"``, or ``"ELSE"``). ``is_default`` is ``True`` for the
    optional ``ELSE`` branch. ``condition_summary`` is a short,
    human-readable description of the branch's gating condition --
    e.g. ``"State = 1"`` for a numeric label or ``"State = Idle"``
    for an identifier label. ``None`` for the ``ELSE`` branch
    because its condition is "none of the above" (no compact
    summary).
    """

    label: str
    is_default: bool = False
    assignments: list[STAssignment] = field(default_factory=list)
    condition_summary: Optional[str] = None


@dataclass(frozen=True)
class STCaseBlock:
    """A parsed ``CASE ... OF ... END_CASE`` block.

    ``selector_tag`` is the tag whose value is being switched on, when
    the selector is a bare identifier. For expression selectors
    (``CASE state_a + state_b OF``), ``selector_tag`` is None and
    ``too_complex_selector=True``.
    """

    selector_raw: str
    selector_tag: Optional[str] = None
    too_complex_selector: bool = False
    branches: list[STCaseBranch] = field(default_factory=list)
    raw_text: str = ""
    statement_index: int = 0


@dataclass(frozen=True)
class STComplexBlock:
    """Anything the parser couldn't recognize.

    The normalization service uses this as a signal to flag
    ``st_parse_status="too_complex"`` while still recording the raw
    text on the routine.
    """

    raw_text: str
    statement_index: int = 0


STBlock = Union[STAssignment, STIfBlock, STCaseBlock, STComplexBlock]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_structured_text_blocks(routine_text: Optional[str]) -> list[STBlock]:
    """Decompose an ST routine into a flat list of top-level blocks.

    Returns an empty list for ``None`` / empty input.

    The parser is single-pass over the routine text: at each position
    it tries IF, then CASE, then assignment, and finally falls back
    to a "too complex" capture that consumes up to the next ``;``.
    Whitespace between blocks is skipped silently.
    """

    if not routine_text:
        return []

    text = routine_text.strip()
    if not text:
        return []

    blocks: list[STBlock] = []
    pos = 0
    stmt_idx = 0

    while pos < len(text):
        # Skip whitespace and stray semicolons between statements.
        ws = re.match(r"[\s;]+", text[pos:])
        if ws:
            pos += ws.end()
            if pos >= len(text):
                break

        # IF block.
        if re.match(r"IF\b", text[pos:], re.IGNORECASE):
            m = _IF_BLOCK_RE.match(text, pos)
            if m:
                blocks.append(_make_if_block(m, stmt_idx))
                pos = m.end()
                stmt_idx += 1
                continue

        # CASE block.
        if re.match(r"CASE\b", text[pos:], re.IGNORECASE):
            m = _CASE_BLOCK_RE.match(text, pos)
            if m:
                blocks.append(_make_case_block(m, stmt_idx))
                pos = m.end()
                stmt_idx += 1
                continue

        # Top-level assignment.
        m = _ASSIGNMENT_RE.match(text, pos)
        if m:
            blocks.append(_make_assignment(m, stmt_idx))
            pos = m.end()
            stmt_idx += 1
            continue

        # Anything else: consume up to the next ``;`` (or end of text)
        # and emit a too-complex marker. We never want the parser to
        # spin in place on garbage.
        end = text.find(";", pos)
        if end == -1:
            blocks.append(
                STComplexBlock(
                    raw_text=text[pos:].strip(),
                    statement_index=stmt_idx,
                )
            )
            break
        blocks.append(
            STComplexBlock(
                raw_text=text[pos:end + 1].strip(),
                statement_index=stmt_idx,
            )
        )
        pos = end + 1
        stmt_idx += 1

    return blocks


# ---------------------------------------------------------------------------
# Internal block constructors
# ---------------------------------------------------------------------------


def _make_assignment(
    match: re.Match[str], statement_index: int
) -> STAssignment:
    target = match.group("target").strip()
    rhs = match.group("rhs").strip()
    raw_text = match.group(0).strip()
    return _parse_assignment_text(
        target=target,
        rhs=rhs,
        raw_text=raw_text,
        statement_index=statement_index,
    )


def _parse_assignment_text(
    target: str,
    rhs: str,
    raw_text: str,
    statement_index: int,
) -> STAssignment:
    """Build an :class:`STAssignment` from already-extracted pieces.

    Order of attempts:

    1. Literal ``TRUE`` / ``FALSE``: trivially handled.
    2. Anything else: delegate to
       :func:`app.parsers.st_expression.parse_st_expression` and
       derive the legacy ``conditions`` view only when the result
       is a simple AND-conjunction of identifiers.
    """

    rhs_clean = rhs.strip().rstrip(";").strip()
    upper = rhs_clean.upper()

    if upper == "TRUE":
        return STAssignment(
            target=target,
            raw_expression=rhs_clean,
            assigned_value=True,
            conditions=[],
            expression=None,
            too_complex=False,
            raw_text=raw_text,
            statement_index=statement_index,
        )
    if upper == "FALSE":
        return STAssignment(
            target=target,
            raw_expression=rhs_clean,
            assigned_value=False,
            conditions=[],
            expression=None,
            too_complex=False,
            raw_text=raw_text,
            statement_index=statement_index,
        )

    expr = parse_st_expression(rhs_clean)
    legacy_conditions = _legacy_conditions_from(expr)
    return STAssignment(
        target=target,
        raw_expression=rhs_clean,
        assigned_value=None,
        conditions=legacy_conditions,
        expression=expr,
        too_complex=expr.too_complex,
        raw_text=raw_text,
        statement_index=statement_index,
    )


def _legacy_conditions_from(
    expr: STExpressionParse,
) -> list[STCondition]:
    """Return the legacy flat-conjunction view of an expression.

    Only populated for simple AND-conjunctions of identifier terms,
    so back-compat consumers (existing tests, Trace v2's
    ``logic_condition`` aggregator) never see comparison or
    disjunction shapes here. New consumers should read
    ``expression`` directly.
    """

    if expr.too_complex or not expr.is_simple_conjunction:
        return []
    return [t for t in expr.branches[0].terms if isinstance(t, STCondition)]


def _make_if_block(
    match: re.Match[str], statement_index: int
) -> STIfBlock:
    cond_text = match.group("cond").strip()
    then_body = (match.group("then_body") or "").strip()
    else_body = (match.group("else_body") or "").strip()

    expr = parse_st_expression(cond_text)
    too_complex_condition = expr.too_complex
    condition_terms = _legacy_conditions_from(expr)

    # The ELSE branch's gating condition is the negation of THEN's. We
    # can only represent that mechanically when THEN is exactly one
    # term *and* that term is invertible:
    #   * one identifier term  -> NOT it.
    #   * one comparison term  -> invert the operator (handled below
    #     by the normalizer reading ``condition_expression``).
    # Anything else (multi-term conjunctions, OR, mixed) becomes
    # ``else_too_complex=True`` so the normalizer doesn't fabricate
    # a wrong gating condition.
    one_branch = (
        not too_complex_condition
        and len(expr.branches) == 1
    )
    one_term_in_only_branch = (
        one_branch and len(expr.branches[0].terms) == 1
    )
    else_too_complex = not one_term_in_only_branch

    then_assignments = _parse_body_assignments(then_body)
    else_assignments = _parse_body_assignments(else_body)

    return STIfBlock(
        condition_raw=cond_text,
        condition_terms=condition_terms,
        condition_expression=expr,
        too_complex_condition=too_complex_condition,
        else_too_complex=else_too_complex,
        then_assignments=then_assignments,
        else_assignments=else_assignments,
        raw_text=match.group(0).strip(),
        statement_index=statement_index,
    )


def _make_case_block(
    match: re.Match[str], statement_index: int
) -> STCaseBlock:
    selector_raw = match.group("selector").strip()
    body = (match.group("body") or "").strip()

    # The selector is the tag whose value drives the switch. We only
    # treat it as a "real" tag (for READS) when it's a bare
    # identifier. Anything else (expression / arithmetic / function
    # call) is flagged too_complex_selector.
    if re.fullmatch(_IDENT, selector_raw):
        selector_tag: Optional[str] = selector_raw
        too_complex_selector = False
    else:
        selector_tag = None
        too_complex_selector = True

    branches = _parse_case_branches(body, selector_tag=selector_tag)
    return STCaseBlock(
        selector_raw=selector_raw,
        selector_tag=selector_tag,
        too_complex_selector=too_complex_selector,
        branches=branches,
        raw_text=match.group(0).strip(),
        statement_index=statement_index,
    )


# ---------------------------------------------------------------------------
# Body / branch parsing
# ---------------------------------------------------------------------------


def _parse_body_assignments(body: str) -> list[STAssignment]:
    """Extract assignments from a THEN / ELSE body in source order.

    Other shapes inside the body (nested IF, function calls, ...) are
    intentionally *not* preserved as STComplexBlock here: doing so
    would push complex-block semantics into the IF/CASE structure
    types. They're simply not extracted -- the normalizer can still
    see the raw body via ``STIfBlock.raw_text``.
    """

    if not body.strip():
        return []

    out: list[STAssignment] = []
    for idx, match in enumerate(_ASSIGNMENT_RE.finditer(body)):
        out.append(
            _parse_assignment_text(
                target=match.group("target").strip(),
                rhs=match.group("rhs").strip(),
                raw_text=match.group(0).strip(),
                statement_index=idx,
            )
        )
    return out


def _parse_case_branches(
    body: str,
    selector_tag: Optional[str] = None,
) -> list[STCaseBranch]:
    """Split a CASE body into per-label branches.

    Walks through ``<label>:`` markers in order, slicing the body
    between consecutive markers. Each slice is parsed as a list of
    assignments via :func:`_parse_body_assignments`.

    ``selector_tag`` is used to build a short
    ``condition_summary`` for each non-default branch (e.g.
    ``"State = 1"``). When the selector is too complex, the summary
    is left as ``None``.
    """

    if not body.strip():
        return []

    markers = list(_CASE_LABEL_RE.finditer(body))
    if not markers:
        return []

    branches: list[STCaseBranch] = []
    for i, marker in enumerate(markers):
        next_start = (
            markers[i + 1].start() if i + 1 < len(markers) else len(body)
        )
        branch_body = body[marker.end():next_start].strip()
        # Ensure the trailing assignment is terminated so the
        # assignment regex (which requires a trailing ``;``) matches.
        if branch_body and not branch_body.endswith(";"):
            branch_body = branch_body + ";"

        is_else = bool(marker.group("is_else"))
        label = "ELSE" if is_else else (marker.group("label") or "").strip()

        if is_else or selector_tag is None:
            summary: Optional[str] = None
        else:
            summary = f"{selector_tag} = {label}"

        branches.append(
            STCaseBranch(
                label=label,
                is_default=is_else,
                assignments=_parse_body_assignments(branch_body),
                condition_summary=summary,
            )
        )
    return branches


__all__ = [
    "STBlock",
    "STAssignment",
    "STIfBlock",
    "STCaseBlock",
    "STCaseBranch",
    "STComplexBlock",
    "STComparisonTerm",
    "STCondition",
    "STConjunction",
    "STExpressionParse",
    "STTerm",
    "parse_structured_text_blocks",
]
