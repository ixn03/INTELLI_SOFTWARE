"""Conservative Structured Text arithmetic operand extraction.

Used when an assignment RHS is not a boolean literal / expression but
is a simple arithmetic formula. The parser does **not** evaluate the
expression; it only collects tag-shaped identifiers so normalization
can emit READS edges.

Supported envelope (conservative)
---------------------------------

* Binary operators ``+``, ``-``, ``*``, ``/`` between operands.
* Operands: identifiers (with optional dotted members / ``[N]`` indices)
  and numeric literals.
* Balanced parentheses around sub-expressions.
* Unary ``+`` / ``-`` immediately before a numeric literal.

Anything else — boolean keywords, comparisons, function calls,
``:=``, unbalanced parentheses — sets ``too_complex=True``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_IDENT_RE = re.compile(
    r"[A-Za-z_][A-Za-z_0-9]*"
    r"(?:"
    r"\.[A-Za-z_][A-Za-z_0-9]*"
    r"|\[\d+\]"
    r")*"
)

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

_ST_KEYWORDS = frozenset(
    {
        "AND",
        "OR",
        "NOT",
        "XOR",
        "TRUE",
        "FALSE",
        "MOD",
        "TO",
        "BY",
        "DO",
    }
)


@dataclass(frozen=True)
class STArithmeticParse:
    """Tag operands referenced by a simple arithmetic expression."""

    operand_tags: list[str] = field(default_factory=list)
    too_complex: bool = False
    raw_text: str = ""


def parse_st_arithmetic_operands(text: str | None) -> STArithmeticParse:
    """Extract tag operands from a simple arithmetic RHS."""

    if not text:
        return STArithmeticParse(operand_tags=[], too_complex=False, raw_text="")

    raw = text.strip().rstrip(";").strip()
    if not raw:
        return STArithmeticParse(operand_tags=[], too_complex=False, raw_text="")

    if _has_unsupported_shape(raw):
        return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)

    tags: list[str] = []
    seen: set[str] = set()
    for m in _IDENT_RE.finditer(raw):
        ident = m.group(0)
        if ident.upper() in _ST_KEYWORDS:
            return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)
        if ident not in seen:
            seen.add(ident)
            tags.append(ident)

    if not tags:
        return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)

    return STArithmeticParse(operand_tags=tags, too_complex=False, raw_text=raw)


def _has_unsupported_shape(text: str) -> bool:
    """Return True when ``text`` is outside the arithmetic envelope."""

    if re.search(r"\b(?:AND|OR|NOT|XOR)\b", text, re.IGNORECASE):
        return True
    if re.search(r":=", text):
        return True
    if re.search(r"\w\s*\(", text):
        return True

    depth = 0
    i = 0
    compare_ops = ("<=", ">=", "<>", "<", ">", "=")
    while i < len(text):
        ch = text[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            if depth < 0:
                return True
            i += 1
            continue
        if depth == 0:
            for op in compare_ops:
                if text.startswith(op, i):
                    if op == "=" and i > 0 and text[i - 1] == ":":
                        i += 1
                        break
                    return True
        i += 1
    if depth != 0:
        return True

    stripped = text
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_[]().+-*/ \t")
    for ch in stripped:
        if ch not in allowed:
            return True

    if not re.search(r"[+\-*/]", stripped):
        if _IDENT_RE.fullmatch(stripped):
            return False
        if _NUMBER_RE.fullmatch(stripped):
            return True
        return True

    return False


def parse_st_function_call_operands(text: str | None) -> STArithmeticParse:
    """Extract tag operands from a single function-call or nested-call RHS.

    Conservative envelope: one outer call ``Name(arg, ...)`` or a simple
    nest ``Outer(Inner(x))``. Does not evaluate; only collects identifiers
    appearing inside argument lists.
    """

    if not text:
        return STArithmeticParse(operand_tags=[], too_complex=False, raw_text="")

    raw = text.strip().rstrip(";").strip()
    if not raw or not re.search(r"\w\s*\(", raw):
        return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)

    if re.search(r"\b(?:AND|OR|NOT|XOR)\b", raw, re.IGNORECASE):
        return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)
    if re.search(r":=", raw):
        return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)

    depth = 0
    i = 0
    compare_ops = ("<=", ">=", "<>", "<", ">", "=")
    while i < len(raw):
        ch = raw[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)
        elif depth == 0:
            if ch in "+-*/":
                return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)
            for op in compare_ops:
                if raw.startswith(op, i):
                    if op == "=" and i > 0 and raw[i - 1] == ":":
                        i += 1
                        break
                    return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)
        i += 1
    if depth != 0:
        return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)

    tags: list[str] = []
    seen: set[str] = set()
    for m in _IDENT_RE.finditer(raw):
        ident = m.group(0)
        if ident.upper() in _ST_KEYWORDS:
            continue
        end = m.end()
        rest = raw[end:].lstrip()
        if rest.startswith("("):
            continue
        if ident not in seen:
            seen.add(ident)
            tags.append(ident)

    if not tags:
        return STArithmeticParse(operand_tags=[], too_complex=True, raw_text=raw)

    return STArithmeticParse(operand_tags=tags, too_complex=False, raw_text=raw)


__all__ = ["STArithmeticParse", "parse_st_arithmetic_operands", "parse_st_function_call_operands"]
