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
   may include nested ``IF … END_IF`` blocks and standalone FB calls;
   other shapes remain in ``raw_text`` only.
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

4. ``FOR`` / ``WHILE`` / ``REPEAT`` loops as :class:`STLoopBlock`
   (loop condition -> READS during normalization; body assignments
   extracted when they are simple ``:=`` statements).

5. Standalone function / FB invocations ``Name(...);`` as
   :class:`STFbInvocation` (``CALLS`` + parameter READS during
   normalization).

Everything else -- nested control flow inside IF bodies, unbalanced
parentheses, arithmetic mixed with boolean operators -- lands in
:class:`STComplexBlock` (top-level) or sets ``too_complex=True`` on
the relevant assignment / IF block, which the normalizer surfaces with
``st_parse_status="too_complex"``.

At parse entry, comments are removed via
:func:`app.parsers.st_comments.strip_st_comments_for_parsing` so
``IF`` / ``CASE`` keyword boundaries are not confused by ``(* ... *)``
or ``//`` markers. Block ``raw_text`` values are therefore **comment-
stripped** fragments; the connector still stores the verbatim export on
``ControlRoutine.raw_logic`` for audit.

``IF`` / ``ELSIF`` / ``ELSE`` chains are parsed into
:class:`STIfElsifChain` when the outer splitter succeeds; otherwise the
raw text is preserved as :class:`STComplexBlock`.

Determinism
-----------

* Same input -> same output. No I/O, no randomness, no LLM.
* All regex is compiled once at import time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union

from app.parsers.st_comments import strip_st_comments_for_parsing
from app.parsers.st_if_elsif_split import parse_outer_if_elsif_else
from app.parsers.st_arithmetic import (
    parse_st_arithmetic_operands,
    parse_st_function_call_operands,
)
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
_IDENT_RE = re.compile(_IDENT)

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

# ``IF`` / ``END_IF`` for balanced outer-IF scanning. ``ELSIF`` is not
# counted: any ``IF`` that contains ``ELSIF`` is emitted as
# :class:`STComplexBlock` (multi-branch ``IF`` is unsupported in
# :class:`STIfBlock` without a schema extension).
_IF_ENDIF_KW = re.compile(r"\b(?:IF|END_IF)\b", re.IGNORECASE)
_IF_THEN_ELSE_KW = re.compile(r"\b(IF|THEN|ELSE|END_IF)\b", re.IGNORECASE)


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
            boolean literal, a conjunction, a disjunction, a
            comparison-bearing expression, or a simple arithmetic
            formula.
        arithmetic_operands: Tag names referenced by a simple
            arithmetic RHS (e.g. ``Counter + 1`` -> ``["Counter"]``).
            Empty when the RHS is boolean-shaped or too complex.
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
    arithmetic_operands: list[str] = field(default_factory=list)
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
    then_nested_ifs: list["STIfBlock"] = field(default_factory=list)
    else_nested_ifs: list["STIfBlock"] = field(default_factory=list)
    then_fb_calls: list["STFbInvocation"] = field(default_factory=list)
    else_fb_calls: list["STFbInvocation"] = field(default_factory=list)
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
    nested_ifs: list["STIfBlock"] = field(default_factory=list)
    fb_calls: list["STFbInvocation"] = field(default_factory=list)
    condition_summary: Optional[str] = None


@dataclass(frozen=True)
class STReturnBlock:
    """A ``RETURN`` or ``RETURN <expr>;`` statement."""

    value_raw: Optional[str] = None
    value_operands: list[str] = field(default_factory=list)
    too_complex_value: bool = False
    raw_text: str = ""
    statement_index: int = 0


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
class STIfElsifChain:
    """``IF`` / ``ELSIF`` / optional ``ELSE`` with per-branch assignments."""

    branches: list[tuple[Optional[str], list[STAssignment]]]
    raw_text: str
    statement_index: int = 0


@dataclass(frozen=True)
class STForMetadata:
    """Parsed ``FOR`` header: ``FOR i := 0 TO Limit BY 1 DO``."""

    loop_var: str
    start_expr: str
    end_expr: str
    step_expr: str = "1"

    @property
    def display(self) -> str:
        step = self.step_expr.strip()
        if step in {"1", "1.0"}:
            return f"{self.loop_var} from {self.start_expr} to {self.end_expr}"
        return (
            f"{self.loop_var} from {self.start_expr} to {self.end_expr}"
            f" by {step}"
        )


@dataclass(frozen=True)
class STLoopBlock:
    """A ``FOR`` / ``WHILE`` / ``REPEAT`` loop with extracted body assignments."""

    loop_kind: str
    condition_raw: str = ""
    condition_expression: Optional[STExpressionParse] = None
    condition_operands: list[str] = field(default_factory=list)
    for_metadata: Optional[STForMetadata] = None
    too_complex_condition: bool = False
    body_assignments: list[STAssignment] = field(default_factory=list)
    too_complex_body: bool = False
    raw_text: str = ""
    statement_index: int = 0


@dataclass(frozen=True)
class STFbInvocation:
    """A standalone ``Callee(...);`` function or FB call line."""

    callee_name: str
    parameters: list[str] = field(default_factory=list)
    too_complex_parameters: bool = False
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
    fragment_kind: Optional[str] = None
    callee_name: Optional[str] = None


STBlock = Union[
    STAssignment,
    STIfBlock,
    STIfElsifChain,
    STCaseBlock,
    STLoopBlock,
    STFbInvocation,
    STReturnBlock,
    STComplexBlock,
]


def _matching_close_paren_st(s: str, open_paren: int) -> int:
    depth = 0
    j = open_paren
    n = len(s)
    while j < n:
        ch = s[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def _find_balanced_loop_end(text: str, pos: int, open_kw: str, close_kw: str) -> int:
    """Return index after optional ``;`` following ``close_kw``, or ``-1``."""

    m0 = re.match(rf"\s*\b{open_kw}\b", text[pos:], re.IGNORECASE)
    if not m0:
        return -1
    i = pos + m0.end()
    depth = 1
    n = len(text)
    open_rx = re.compile(rf"\b{open_kw}\b", re.IGNORECASE)
    close_rx = re.compile(rf"\b{close_kw}\b", re.IGNORECASE)
    while i < n:
        m_open = open_rx.search(text, i)
        m_close = close_rx.search(text, i)
        if m_close is None:
            return -1
        if m_open is not None and m_open.start() < m_close.start():
            depth += 1
            i = m_open.end()
            continue
        depth -= 1
        i = m_close.end()
        if depth == 0:
            while i < n and text[i] in " \t\r\n":
                i += 1
            if i < n and text[i] == ";":
                i += 1
            return i
    return -1


_FOR_HEADER_SKIP = frozenset({"TO", "BY"})

_FOR_HEADER_RE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:=\s*(.+?)\s+TO\s+(.+?)"
    r"(?:\s+BY\s+(.+?))?\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _parse_for_header(header: str) -> Optional[STForMetadata]:
    m = _FOR_HEADER_RE.match(header.strip())
    if not m:
        return None
    step = (m.group(4) or "1").strip()
    return STForMetadata(
        loop_var=m.group(1),
        start_expr=m.group(2).strip(),
        end_expr=m.group(3).strip(),
        step_expr=step,
    )


def _extract_for_header_operands(header: str) -> list[str]:
    """Collect tag-shaped identifiers from a ``FOR`` header."""

    tags: list[str] = []
    seen: set[str] = set()
    for m in _IDENT_RE.finditer(header):
        ident = m.group(0)
        if ident.upper() in _FOR_HEADER_SKIP:
            continue
        if ident not in seen:
            seen.add(ident)
            tags.append(ident)
    return tags


def _split_top_level_commas(text: str) -> list[str]:
    """Split ``text`` on commas at parenthesis depth zero."""

    parts: list[str] = []
    depth = 0
    last = 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return []
        elif ch == "," and depth == 0:
            parts.append(text[last:i].strip())
            last = i + 1
    if depth != 0:
        return []
    parts.append(text[last:].strip())
    return parts


def _extract_loop_condition(
    raw_loop: str, loop_kind: str
) -> tuple[str, Optional[STExpressionParse], list[str], bool]:
    """Return ``(condition_raw, expression, operands, too_complex)``."""

    if loop_kind == "WHILE":
        m = re.search(
            r"\bWHILE\s+(.+?)\s+DO\b", raw_loop, re.IGNORECASE | re.DOTALL
        )
        if not m:
            return "", None, [], True
        cond = m.group(1).strip()
        expr = parse_st_expression(cond)
        return cond, expr, [], expr.too_complex

    if loop_kind == "REPEAT":
        m = re.search(
            r"\bUNTIL\b(.+?)(?:\bEND_REPEAT\b)", raw_loop, re.IGNORECASE | re.DOTALL
        )
        if not m:
            return "", None, [], True
        cond = m.group(1).strip().rstrip(";").strip()
        expr = parse_st_expression(cond)
        return cond, expr, [], expr.too_complex

    if loop_kind == "FOR":
        m = re.search(
            r"\bFOR\s+(.+?)\s+DO\b", raw_loop, re.IGNORECASE | re.DOTALL
        )
        if not m:
            return "", None, [], True
        header = m.group(1).strip()
        operands = _extract_for_header_operands(header)
        if not operands:
            return header, None, [], True
        return header, None, operands, False

    return "", None, [], True


def _extract_loop_body(raw_loop: str, open_kw: str, close_kw: str) -> str:
    """Return the inner body text between loop header and ``close_kw``."""

    open_m = re.match(rf"\s*\b{open_kw}\b", raw_loop, re.IGNORECASE)
    if not open_m:
        return ""

    body_start = open_m.end()
    if open_kw in {"FOR", "WHILE"}:
        do_m = re.search(r"\bDO\b", raw_loop, re.IGNORECASE)
        if do_m:
            body_start = do_m.end()
        else:
            return ""
    elif open_kw == "REPEAT":
        body_start = open_m.end()

    close_m = re.search(rf"\b{close_kw}\b", raw_loop, re.IGNORECASE)
    if not close_m:
        return raw_loop[body_start:].strip()
    if open_kw == "REPEAT":
        until_m = re.search(r"\bUNTIL\b", raw_loop, re.IGNORECASE)
        if until_m and until_m.start() < close_m.start():
            return raw_loop[body_start : until_m.start()].strip()
    return raw_loop[body_start : close_m.start()].strip()


def _try_consume_loop_block(
    text: str, pos: int, stmt_idx: int
) -> tuple[Optional[STLoopBlock], int]:
    for open_kw, close_kw in (
        ("FOR", "END_FOR"),
        ("WHILE", "END_WHILE"),
        ("REPEAT", "END_REPEAT"),
    ):
        end_idx = _find_balanced_loop_end(text, pos, open_kw, close_kw)
        if end_idx == -1:
            continue
        raw = text[pos:end_idx].strip()
        body = _extract_loop_body(raw, open_kw, close_kw)
        cond_raw, cond_expr, cond_ops, cond_bad = _extract_loop_condition(
            raw, open_kw
        )
        for_meta = (
            _parse_for_header(cond_raw) if open_kw == "FOR" and cond_raw else None
        )
        body_fix = body.strip()
        if body_fix and not body_fix.endswith(";"):
            body_fix = body_fix + ";"
        assignments = _parse_body_assignments(body_fix)
        too_complex_body = bool(body.strip()) and not assignments
        if body.strip() and assignments:
            for assign in assignments:
                if assign.too_complex:
                    too_complex_body = True
                    break
        return (
            STLoopBlock(
                loop_kind=open_kw,
                condition_raw=cond_raw,
                condition_expression=cond_expr,
                condition_operands=cond_ops,
                for_metadata=for_meta,
                too_complex_condition=cond_bad,
                body_assignments=assignments,
                too_complex_body=too_complex_body,
                raw_text=raw,
                statement_index=stmt_idx,
            ),
            end_idx,
        )
    return None, pos


def _try_consume_fb_invocation(
    text: str, pos: int, stmt_idx: int
) -> tuple[Optional[STFbInvocation], int]:
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", text[pos:], re.IGNORECASE)
    if not m:
        return None, pos
    open_paren = pos + m.end() - 1
    close_paren = _matching_close_paren_st(text, open_paren)
    if close_paren < 0:
        return None, pos
    j = close_paren + 1
    while j < len(text) and text[j] in " \t\r\n":
        j += 1
    if j < len(text) and text[j] == ";":
        raw = text[pos : j + 1].strip()
        inner = text[open_paren + 1 : close_paren].strip()
        params = _split_top_level_commas(inner) if inner else []
        too_complex_params = False
        for param in params:
            p = param.strip()
            if not p:
                too_complex_params = True
                continue
            if _looks_like_tag_operand_text(p):
                continue
            if re.fullmatch(r"-?\d+(?:\.\d+)?", p):
                continue
            expr = parse_st_expression(p)
            if expr.too_complex:
                arith = parse_st_arithmetic_operands(p)
                fn = parse_st_function_call_operands(p)
                if arith.too_complex and fn.too_complex:
                    too_complex_params = True
        return (
            STFbInvocation(
                callee_name=m.group(1),
                parameters=params,
                too_complex_parameters=too_complex_params,
                raw_text=raw,
                statement_index=stmt_idx,
            ),
            j + 1,
        )
    return None, pos


def _looks_like_tag_operand_text(value: str) -> bool:
    return bool(_IDENT_RE.fullmatch((value or "").strip()))


def _find_matching_end_if(text: str, pos: int) -> int:
    """Return index just after the ``END_IF`` that closes the ``IF`` at ``pos``.

    ``pos`` must point at the start of an ``IF`` token. Returns ``-1``
    when no balanced ``END_IF`` exists (malformed source).
    """

    depth = 0
    for m in _IF_ENDIF_KW.finditer(text, pos):
        if m.group(0).upper() == "IF":
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                return m.end()
    return -1


def _consume_end_if_trailer(text: str, end_after_end_if: int) -> int:
    """Advance past optional whitespace and one ``;`` after ``END_IF``.

    ``_IF_BLOCK_RE`` ends with ``\\bEND_IF\\b\\s*;?``; ``_find_matching_end_if``
    returns the index immediately after the ``END_IF`` token. Align the two
    so valid blocks are not misclassified as :class:`STComplexBlock`.
    """

    i = end_after_end_if
    while i < len(text) and text[i] in " \t\r\n":
        i += 1
    if i < len(text) and text[i] == ";":
        i += 1
    return i


def _split_if_then_else(text: str) -> Optional[tuple[str, str, str]]:
    """Split a balanced ``IF … END_IF`` block into condition and bodies."""

    m0 = re.match(r"\s*IF\b", text, re.IGNORECASE)
    if not m0:
        return None

    after_if = m0.end()
    depth = 1
    then_kw_start = -1
    then_kw_end = -1
    else_kw_start = -1
    else_kw_end = -1
    end_if_start = -1

    for m in _IF_THEN_ELSE_KW.finditer(text, after_if):
        kw = m.group(1).upper()
        if kw == "IF":
            depth += 1
        elif kw == "END_IF":
            depth -= 1
            if depth == 0:
                end_if_start = m.start()
                break
        elif kw == "THEN" and depth == 1 and then_kw_start == -1:
            then_kw_start = m.start()
            then_kw_end = m.end()
        elif kw == "ELSE" and depth == 1 and else_kw_start == -1:
            else_kw_start = m.start()
            else_kw_end = m.end()

    if then_kw_start == -1 or end_if_start == -1:
        return None

    cond = text[after_if:then_kw_start].strip()
    if else_kw_start != -1:
        then_body = text[then_kw_end:else_kw_start].strip()
        else_body = text[else_kw_end:end_if_start].strip()
    else:
        then_body = text[then_kw_end:end_if_start].strip()
        else_body = ""

    return cond, then_body, else_body


@dataclass(frozen=True)
class _STBodyParse:
    assignments: list[STAssignment] = field(default_factory=list)
    nested_ifs: list[STIfBlock] = field(default_factory=list)
    fb_calls: list[STFbInvocation] = field(default_factory=list)


def _parse_body_statements(body: str) -> _STBodyParse:
    """Extract assignments, nested IF blocks, and FB calls from a body."""

    if not body.strip():
        return _STBodyParse()

    assignments: list[STAssignment] = []
    nested_ifs: list[STIfBlock] = []
    fb_calls: list[STFbInvocation] = []
    pos = 0
    assign_idx = 0

    while pos < len(body):
        ws = re.match(r"[\s;]+", body[pos:])
        if ws:
            pos += ws.end()
            if pos >= len(body):
                break

        if re.match(r"IF\b", body[pos:], re.IGNORECASE):
            end = _find_matching_end_if(body, pos)
            if end == -1:
                break
            end_consumed = _consume_end_if_trailer(body, end)
            block_src = body[pos:end_consumed].strip()
            nested_ifs.append(_parse_if_block_from_text(block_src, assign_idx))
            pos = end_consumed
            assign_idx += 1
            continue

        fb, new_pos = _try_consume_fb_invocation(body, pos, assign_idx)
        if fb is not None:
            fb_calls.append(fb)
            pos = new_pos
            assign_idx += 1
            continue

        m = _ASSIGNMENT_RE.match(body, pos)
        if m:
            assignments.append(_make_assignment(m, assign_idx))
            pos = m.end()
            assign_idx += 1
            continue

        end = body.find(";", pos)
        if end == -1:
            break
        pos = end + 1

    return _STBodyParse(
        assignments=assignments,
        nested_ifs=nested_ifs,
        fb_calls=fb_calls,
    )


def _try_consume_return(
    text: str, pos: int, stmt_idx: int
) -> tuple[Optional[STReturnBlock], int]:
    m = re.match(r"\s*RETURN\b", text[pos:], re.IGNORECASE)
    if not m:
        return None, pos
    start = pos + m.end()
    end = text.find(";", start)
    if end == -1:
        return None, pos
    raw = text[pos : end + 1].strip()
    value_raw = text[start:end].strip() or None
    operands: list[str] = []
    too_complex_value = False
    if value_raw:
        if _looks_like_tag_operand_text(value_raw):
            operands = [value_raw]
        elif re.fullmatch(r"-?\d+(?:\.\d+)?", value_raw):
            operands = []
        else:
            expr = parse_st_expression(value_raw)
            if not expr.too_complex:
                plans = _legacy_conditions_from(expr)
                operands = [c.tag for c in plans if hasattr(c, "tag")]
            else:
                arith = parse_st_arithmetic_operands(value_raw)
                fn = parse_st_function_call_operands(value_raw)
                if not arith.too_complex and arith.operand_tags:
                    operands = list(arith.operand_tags)
                elif not fn.too_complex and fn.operand_tags:
                    operands = list(fn.operand_tags)
                else:
                    too_complex_value = True
    return (
        STReturnBlock(
            value_raw=value_raw,
            value_operands=operands,
            too_complex_value=too_complex_value,
            raw_text=raw,
            statement_index=stmt_idx,
        ),
        end + 1,
    )


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

    text = strip_st_comments_for_parsing(routine_text).strip()
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

        # FOR / WHILE / REPEAT (preserve whole construct; no body edges).
        lb, new_pos = _try_consume_loop_block(text, pos, stmt_idx)
        if lb is not None:
            blocks.append(lb)
            pos = new_pos
            stmt_idx += 1
            continue

        # Standalone RETURN.
        ret, new_pos = _try_consume_return(text, pos, stmt_idx)
        if ret is not None:
            blocks.append(ret)
            pos = new_pos
            stmt_idx += 1
            continue

        # Standalone FB-style call ``Name(...);`` (not ``IF`` / ``CASE``).
        fb, new_pos = _try_consume_fb_invocation(text, pos, stmt_idx)
        if fb is not None:
            blocks.append(fb)
            pos = new_pos
            stmt_idx += 1
            continue

        # IF block (balanced ``IF`` … ``END_IF``; ``ELSIF`` -> chain or complex).
        if re.match(r"IF\b", text[pos:], re.IGNORECASE):
            end = _find_matching_end_if(text, pos)
            if end == -1:
                end_semi = text.find(";", pos)
                if end_semi == -1:
                    blocks.append(
                        STComplexBlock(
                            raw_text=text[pos:].strip(),
                            statement_index=stmt_idx,
                        )
                    )
                    break
                blocks.append(
                    STComplexBlock(
                        raw_text=text[pos : end_semi + 1].strip(),
                        statement_index=stmt_idx,
                    )
                )
                pos = end_semi + 1
                stmt_idx += 1
                continue

            end_consumed = _consume_end_if_trailer(text, end)
            block_src = text[pos:end_consumed].strip()
            if re.search(r"\bELSIF\b", block_src, re.IGNORECASE):
                segs = parse_outer_if_elsif_else(block_src)
                if segs:
                    built: list[tuple[Optional[str], list[STAssignment]]] = []
                    for cond, body in segs:
                        body_fix = body.strip()
                        if body_fix and not body_fix.endswith(";"):
                            body_fix = body_fix + ";"
                        built.append((cond, _parse_body_assignments(body_fix)))
                    blocks.append(
                        STIfElsifChain(
                            branches=built,
                            raw_text=block_src,
                            statement_index=stmt_idx,
                        )
                    )
                else:
                    blocks.append(
                        STComplexBlock(
                            raw_text=block_src,
                            statement_index=stmt_idx,
                        )
                    )
            else:
                blocks.append(_parse_if_block_from_text(block_src, stmt_idx))
            pos = end_consumed
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

    if re.fullmatch(r"-?\d+(?:\.\d+)?", rhs_clean):
        return STAssignment(
            target=target,
            raw_expression=rhs_clean,
            assigned_value=None,
            conditions=[],
            expression=None,
            too_complex=False,
            raw_text=raw_text,
            statement_index=statement_index,
        )

    expr = parse_st_expression(rhs_clean)
    if not expr.too_complex:
        legacy_conditions = _legacy_conditions_from(expr)
        return STAssignment(
            target=target,
            raw_expression=rhs_clean,
            assigned_value=None,
            conditions=legacy_conditions,
            expression=expr,
            too_complex=False,
            raw_text=raw_text,
            statement_index=statement_index,
        )

    arith = parse_st_arithmetic_operands(rhs_clean)
    if not arith.too_complex and arith.operand_tags:
        return STAssignment(
            target=target,
            raw_expression=rhs_clean,
            assigned_value=None,
            conditions=[],
            expression=None,
            arithmetic_operands=list(arith.operand_tags),
            too_complex=False,
            raw_text=raw_text,
            statement_index=statement_index,
        )

    fn = parse_st_function_call_operands(rhs_clean)
    if not fn.too_complex and fn.operand_tags:
        return STAssignment(
            target=target,
            raw_expression=rhs_clean,
            assigned_value=None,
            conditions=[],
            expression=None,
            arithmetic_operands=list(fn.operand_tags),
            too_complex=False,
            raw_text=raw_text,
            statement_index=statement_index,
        )

    return STAssignment(
        target=target,
        raw_expression=rhs_clean,
        assigned_value=None,
        conditions=[],
        expression=expr,
        too_complex=True,
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


def _parse_if_block_from_text(text: str, statement_index: int) -> STIfBlock:
    """Build an :class:`STIfBlock` from a balanced ``IF … END_IF`` snippet."""

    parts = _split_if_then_else(text)
    if parts is None:
        return STIfBlock(
            condition_raw="",
            too_complex_condition=True,
            else_too_complex=True,
            raw_text=text.strip(),
            statement_index=statement_index,
        )

    cond_text, then_body, else_body = parts
    expr = parse_st_expression(cond_text)
    too_complex_condition = expr.too_complex
    condition_terms = _legacy_conditions_from(expr)

    one_branch = not too_complex_condition and len(expr.branches) == 1
    one_term_in_only_branch = one_branch and len(expr.branches[0].terms) == 1
    else_too_complex = not one_term_in_only_branch

    then_parsed = _parse_body_statements(then_body)
    else_parsed = _parse_body_statements(else_body)

    return STIfBlock(
        condition_raw=cond_text,
        condition_terms=condition_terms,
        condition_expression=expr,
        too_complex_condition=too_complex_condition,
        else_too_complex=else_too_complex,
        then_assignments=then_parsed.assignments,
        else_assignments=else_parsed.assignments,
        then_nested_ifs=then_parsed.nested_ifs,
        else_nested_ifs=else_parsed.nested_ifs,
        then_fb_calls=then_parsed.fb_calls,
        else_fb_calls=else_parsed.fb_calls,
        raw_text=text.strip(),
        statement_index=statement_index,
    )


def _make_if_block(
    match: re.Match[str], statement_index: int
) -> STIfBlock:
    return _parse_if_block_from_text(match.group(0).strip(), statement_index)


def _make_case_block(
    match: re.Match[str], statement_index: int
) -> STCaseBlock:
    selector_raw = match.group("selector").strip()
    body = (match.group("body") or "").strip()

    # The selector is the tag whose value drives the switch. We only
    # treat it as a "real" tag (for READS) when it's a bare
    # identifier. Anything else (expression / arithmetic / function
    # call) is flagged too_complex_selector.
    if _IDENT_RE.fullmatch(selector_raw):
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

    Nested IF blocks and FB calls inside the body are ignored here;
    use :func:`_parse_body_statements` when those must be preserved.
    """

    return _parse_body_statements(body).assignments


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

        parsed = _parse_body_statements(branch_body)

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
                assignments=parsed.assignments,
                nested_ifs=parsed.nested_ifs,
                fb_calls=parsed.fb_calls,
                condition_summary=summary,
            )
        )
    return branches


__all__ = [
    "STBlock",
    "STAssignment",
    "STFbInvocation",
    "STIfElsifChain",
    "STForMetadata",
    "STLoopBlock",
    "STCaseBlock",
    "STCaseBranch",
    "STReturnBlock",
    "STComplexBlock",
    "STComparisonTerm",
    "STCondition",
    "STConjunction",
    "STExpressionParse",
    "STTerm",
    "parse_structured_text_blocks",
]
