"""Structured Text boolean expression parser (expanded envelope).

This is the more permissive sibling of
``app.services.structured_text_extraction.parse_boolean_conjunction``.
It is used by the block-aware parser (and from there, by the
normalization service) to extract READS / WRITES edges from ST
conditions that are richer than a flat ``A AND B AND NOT C``.

Supported grammar (conservative)
--------------------------------

::

    expr      ::= conj  ( "OR"  conj  )*
    conj      ::= term  ( "AND" term  )*
    term      ::= "NOT" term
                | "(" expr ")"             -- only when the parens
                                              wrap a balanced sub-expr
                | compare
                | identifier
    compare   ::= identifier compare_op compare_rhs
    compare_op ::= ">="  | "<="  | "<>"  | ">"  | "<"  | "="
    compare_rhs ::= identifier | number | string-literal | boolean
    identifier ::= [A-Za-z_][A-Za-z_0-9]*
                   ( "." identifier_segment | "[" integer "]" )*

What this parser **does not** do
--------------------------------

* Arithmetic operators (``+``, ``-``, ``*``, ``/`` outside compare RHS).
* Function calls (``MY_FUNC(x, y)``).
* Multi-level boolean negation (``NOT NOT A``) - only one ``NOT`` per
  term is accepted, and only as the first token of the term.
* Mixed AND/OR without explicit parens beyond a flat DNF
  (``A AND B OR C AND D`` is fine; ``A OR (B AND C OR D)`` is not).
* Comparisons whose LHS is a literal (``5 < A``) - we expect the
  LHS to be a tag so we can emit a sensible READS edge.

Anything outside this envelope sets ``STExpressionParse.too_complex``
to ``True``. The caller (typically the block parser or normalizer)
then records the relationship with ``st_parse_status="too_complex"``
and does not invent fake gating conditions.

Determinism
-----------

* Same input -> same output. No I/O, no randomness, no LLM.
* All regex is compiled once at import time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

from app.services.structured_text_extraction import STCondition


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


# An ST identifier with optional dotted members and integer array
# indexing. Examples: ``Motor_Run``, ``Tank.Level.SP``, ``Bits[3]``,
# ``MyArr[10].Value``. Conservative: array indices must be bare
# integers (no expressions); members are simple identifiers.
_IDENT_RE = re.compile(
    r"[A-Za-z_][A-Za-z_0-9]*"
    r"(?:"
    r"\.[A-Za-z_][A-Za-z_0-9]*"
    r"|\[\d+\]"
    r")*"
)

# A numeric literal. Permits a leading sign so ``> -5`` parses
# correctly.
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

# Boolean literal (case-insensitive).
_BOOL_RE = re.compile(r"TRUE|FALSE", re.IGNORECASE)

# Quoted string literal (single or double). Conservative: no
# embedded escape handling.
_STRING_RE = re.compile(r"""(?:"[^"]*"|'[^']*')""")

# Comparison operator. Order matters: 2-char operators MUST be
# listed before their 1-char prefixes so the longest match wins.
_COMPARE_OPS = ("<=", ">=", "<>", "<", ">", "=")


@dataclass(frozen=True)
class STComparisonTerm:
    """A condition like ``Tank.Level > 80`` or ``State <> Idle``.

    Either or both sides may be tags. Trace v2 should treat the
    *whole* term as opaque rather than try to evaluate it; the
    normalization layer only emits READS for the operands that look
    like tags.
    """

    lhs: str
    operator: str
    rhs: str
    lhs_is_tag: bool
    rhs_is_tag: bool
    natural_language: str

    @property
    def tag_operands(self) -> list[str]:
        """The operands that look like tags, in source order."""
        out: list[str] = []
        if self.lhs_is_tag:
            out.append(self.lhs)
        if self.rhs_is_tag:
            out.append(self.rhs)
        return out


# A single term inside an AND-conjunction is either an identifier
# read (``STCondition``) or a comparison (``STComparisonTerm``).
STTerm = Union[STCondition, STComparisonTerm]


@dataclass(frozen=True)
class STConjunction:
    """A list of AND-ed terms.

    The whole conjunction is true when every term is true.
    """

    terms: list[STTerm] = field(default_factory=list)

    @property
    def is_identifier_only(self) -> bool:
        """True iff every term is a plain identifier read.

        Used by the block parser to populate the legacy
        ``STAssignment.conditions`` / ``STIfBlock.condition_terms``
        fields for back-compat. Comparison terms break this -- they
        can't be flattened into a ``list[STCondition]``.
        """
        return all(isinstance(t, STCondition) for t in self.terms)


@dataclass(frozen=True)
class STExpressionParse:
    """Parsed boolean expression as a disjunction of conjunctions.

    The expression is true when any branch is true; a branch is
    true when every term in it is true. This is DNF (sum of
    products). A simple ``A AND B AND C`` expression collapses to a
    single branch with three terms.

    Attributes:
        branches: One conjunction per top-level OR clause.
        too_complex: True when the parser hit something outside its
            supported envelope. ``branches`` may be empty in that
            case; never partially populated to avoid misleading
            consumers.
        raw_text: The original text that was parsed.
        gating_logic_type: A short tag indicating the shape:
            ``"and"`` (one branch, all identifier terms),
            ``"or"``  (multiple branches, all identifier terms),
            ``"and_or"`` (DNF with > 1 branch),
            ``"comparison"`` (at least one comparison term),
            ``"too_complex"`` (mirror of ``too_complex``).
    """

    branches: list[STConjunction] = field(default_factory=list)
    too_complex: bool = False
    raw_text: str = ""
    gating_logic_type: str = "and"

    @property
    def is_simple_conjunction(self) -> bool:
        """True iff exactly one branch and only identifier terms.

        This is the shape the legacy ``parse_boolean_conjunction``
        accepts. When true, the block parser can populate the
        legacy ``conditions`` / ``condition_terms`` fields directly
        from ``branches[0].terms``.
        """
        return (
            not self.too_complex
            and len(self.branches) == 1
            and self.branches[0].is_identifier_only
        )

    @property
    def all_terms(self) -> list[STTerm]:
        """Flat list of every term across every branch."""
        return [t for b in self.branches for t in b.terms]

    @property
    def all_identifier_tags(self) -> set[str]:
        """Unique identifier tag names from identifier terms only."""
        return {
            t.tag for t in self.all_terms if isinstance(t, STCondition)
        }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_st_expression(text: Optional[str]) -> STExpressionParse:
    """Parse an ST boolean expression into DNF.

    Returns an :class:`STExpressionParse` with ``too_complex=True``
    when the input falls outside the supported envelope. The
    function never raises on user input.
    """

    if not text:
        return STExpressionParse(
            branches=[], too_complex=False, raw_text="",
            gating_logic_type="and",
        )

    raw = text.strip().rstrip(";").strip()
    if not raw:
        return STExpressionParse(
            branches=[], too_complex=False, raw_text="",
            gating_logic_type="and",
        )

    stripped = _strip_balanced_outer_parens(raw)

    # Split on top-level OR; each piece becomes one branch.
    or_pieces = _split_top_level(stripped, "OR")
    if or_pieces is None:
        return STExpressionParse(
            branches=[], too_complex=True, raw_text=raw,
            gating_logic_type="too_complex",
        )

    branches: list[STConjunction] = []
    for piece in or_pieces:
        conj_or_none = _parse_conjunction(piece)
        if conj_or_none is None:
            return STExpressionParse(
                branches=[], too_complex=True, raw_text=raw,
                gating_logic_type="too_complex",
            )
        branches.append(conj_or_none)

    has_compare = any(
        any(isinstance(t, STComparisonTerm) for t in c.terms)
        for c in branches
    )
    if has_compare:
        gating = "comparison"
    elif len(branches) > 1 and any(len(c.terms) > 1 for c in branches):
        gating = "and_or"
    elif len(branches) > 1:
        gating = "or"
    else:
        gating = "and"

    return STExpressionParse(
        branches=branches,
        too_complex=False,
        raw_text=raw,
        gating_logic_type=gating,
    )


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------


def _strip_balanced_outer_parens(text: str) -> str:
    """Drop a *single* layer of outer parens iff the entire expression
    is wrapped by them. Conservative: refuses if the parens don't
    actually balance at the outermost level (``(A) OR (B)`` stays
    intact).
    """

    s = text.strip()
    while s.startswith("(") and s.endswith(")"):
        depth = 0
        matched = True
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    matched = False
                    break
        if not matched or depth != 0:
            break
        s = s[1:-1].strip()
    return s


def _split_top_level(text: str, keyword: str) -> Optional[list[str]]:
    """Split ``text`` on top-level (paren-depth 0) occurrences of
    the case-insensitive whitespace-bounded keyword.

    Returns ``None`` when parentheses are unbalanced, which is the
    signal the caller uses to bail out as ``too_complex``.
    """

    pieces: list[str] = []
    pattern = re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)
    depth = 0
    last = 0
    # Pre-mask the positions of every keyword match so we don't
    # accidentally split when one occurs inside parentheses (e.g.
    # ``A AND (B OR C)``).
    matches = list(pattern.finditer(text))
    for ch_index, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None
    if depth != 0:
        return None

    depth = 0
    cursor = 0
    for m in matches:
        depth = _paren_depth_at(text, m.start())
        if depth != 0:
            continue
        pieces.append(text[cursor:m.start()].strip())
        cursor = m.end()
    pieces.append(text[cursor:].strip())
    return [p for p in pieces if p != ""] or pieces


def _paren_depth_at(text: str, position: int) -> int:
    """Compute parenthesis depth immediately before ``position``."""

    depth = 0
    for ch in text[:position]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
    return depth


def _parse_conjunction(text: str) -> Optional[STConjunction]:
    """Parse a single AND-conjunction. Returns ``None`` on bail-out."""

    stripped = _strip_balanced_outer_parens(text)
    parts = _split_top_level(stripped, "AND")
    if parts is None:
        return None
    if not parts:
        return None
    terms: list[STTerm] = []
    for raw_part in parts:
        part = raw_part.strip()
        if not part:
            return None
        term = _parse_term(part)
        if term is None:
            return None
        terms.append(term)
    return STConjunction(terms=terms)


def _parse_term(text: str) -> Optional[STTerm]:
    """Parse a single boolean term.

    Returns either an ``STCondition`` (identifier read, possibly
    negated) or an ``STComparisonTerm``. ``None`` signals a bail-out
    so the whole expression becomes ``too_complex``.
    """

    s = _strip_balanced_outer_parens(text)
    if not s:
        return None

    negate = False
    m = re.match(r"NOT\s+(.+)$", s, re.IGNORECASE)
    if m:
        negate = True
        s = m.group(1).strip()
        s = _strip_balanced_outer_parens(s)
        # Disallow ``NOT NOT X`` -- double negation would invert
        # back to positive, but the safer thing is to bail out so
        # the caller doesn't have to second-guess the parser.
        if re.match(r"NOT\s+", s, re.IGNORECASE):
            return None
        # Don't allow ``NOT (A AND B)`` etc. -- the parser only
        # negates a single identifier or comparison. Anything else
        # is out of scope.
        if " " in s and not _is_comparison(s):
            return None

    if _is_comparison(s):
        compare = _parse_comparison(s)
        if compare is None:
            return None
        if negate:
            # ``NOT (A > B)`` is allowed only when we can mechanically
            # invert the operator; otherwise bail out.
            inverted = _invert_comparison_operator(compare.operator)
            if inverted is None:
                return None
            return STComparisonTerm(
                lhs=compare.lhs,
                operator=inverted,
                rhs=compare.rhs,
                lhs_is_tag=compare.lhs_is_tag,
                rhs_is_tag=compare.rhs_is_tag,
                natural_language=(
                    f"{compare.lhs} {inverted} {compare.rhs}"
                ),
            )
        return compare

    if _IDENT_RE.fullmatch(s):
        required = not negate
        return STCondition(
            tag=s,
            required_value=required,
            natural_language=(
                f"{s} is {'TRUE' if required else 'FALSE'}"
            ),
        )

    return None


def _is_comparison(text: str) -> bool:
    """True iff ``text`` contains a top-level comparison operator."""

    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            continue
        if depth == 0:
            for op in _COMPARE_OPS:
                if text.startswith(op, i):
                    # Don't confuse ``:=`` with ``=``; ``:=`` should
                    # have been stripped out by the caller, but be
                    # defensive in case it slips through.
                    if op == "=" and i > 0 and text[i - 1] == ":":
                        i += 1
                        break
                    return True
        i += 1
    return False


def _parse_comparison(text: str) -> Optional[STComparisonTerm]:
    """Split a comparison ``<lhs> <op> <rhs>`` into its pieces."""

    for op in _COMPARE_OPS:
        # Find the *first* occurrence of this operator at depth 0.
        depth = 0
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "(":
                depth += 1
                i += 1
                continue
            if ch == ")":
                depth -= 1
                i += 1
                continue
            if depth == 0 and text.startswith(op, i):
                # Skip ``:=`` (assignment, not equality).
                if op == "=" and i > 0 and text[i - 1] == ":":
                    i += 1
                    continue
                lhs_raw = text[:i].strip()
                rhs_raw = text[i + len(op):].strip()
                # Strip optional outer parens from each side so
                # ``(A) > 5`` is the same as ``A > 5``.
                lhs_raw = _strip_balanced_outer_parens(lhs_raw)
                rhs_raw = _strip_balanced_outer_parens(rhs_raw)
                if not lhs_raw or not rhs_raw:
                    return None
                lhs_is_tag = bool(_IDENT_RE.fullmatch(lhs_raw))
                rhs_is_tag = bool(_IDENT_RE.fullmatch(rhs_raw))
                if not lhs_is_tag:
                    # Conservative: we require the LHS to be a tag so
                    # the resulting READS edge has a meaningful
                    # target.
                    return None
                if not rhs_is_tag:
                    # RHS may be a number, a quoted string, or a
                    # boolean literal. Reject anything else.
                    if not (
                        _NUMBER_RE.fullmatch(rhs_raw)
                        or _STRING_RE.fullmatch(rhs_raw)
                        or _BOOL_RE.fullmatch(rhs_raw)
                    ):
                        return None
                return STComparisonTerm(
                    lhs=lhs_raw,
                    operator=op,
                    rhs=rhs_raw,
                    lhs_is_tag=lhs_is_tag,
                    rhs_is_tag=rhs_is_tag,
                    natural_language=f"{lhs_raw} {op} {rhs_raw}",
                )
            i += 1
    return None


def _invert_comparison_operator(op: str) -> Optional[str]:
    """Return the operator that means "NOT (lhs op rhs)".

    Returns ``None`` for unknown operators so the caller can bail out.
    """

    return {
        "=":  "<>",
        "<>": "=",
        "<":  ">=",
        "<=": ">",
        ">":  "<=",
        ">=": "<",
    }.get(op)


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------


__all__ = [
    "STComparisonTerm",
    "STConjunction",
    "STExpressionParse",
    "STTerm",
    "parse_st_expression",
]
