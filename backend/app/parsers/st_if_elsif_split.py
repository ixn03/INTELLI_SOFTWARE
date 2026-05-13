"""Outer-level ``IF / ELSIF / ELSE / END_IF`` splitting for ST blocks."""

from __future__ import annotations

import re
from typing import Optional


def parse_outer_if_elsif_else(block: str) -> Optional[list[tuple[Optional[str], str]]]:
    """Return ordered ``(condition, body)`` tuples.

    ``condition`` is ``None`` only for the ``ELSE`` branch body.
    ``block`` must include the closing ``END_IF`` (trailing ``;`` optional).
    """

    s = block.strip()
    if not re.match(r"IF\b", s, re.IGNORECASE):
        return None
    if not re.search(r"\bELSIF\b", s, re.IGNORECASE):
        return None

    m_end = re.search(r"\bEND_IF\b", s, re.IGNORECASE)
    if not m_end:
        return None
    core = s[: m_end.start()]

    i = _skip_ws(core, 0)
    m_if0 = re.match(r"IF\b", core[i:], re.IGNORECASE)
    if not m_if0:
        return None
    i = _skip_ws(core, i + m_if0.end())
    c0_start = i
    t0 = _find_then_at_depth_zero(core, c0_start)
    if t0 == -1:
        return None
    cond0 = core[c0_start:t0].strip()
    pos = _skip_ws(core, t0 + 4)

    out: list[tuple[Optional[str], str]] = []
    current_cond: Optional[str] = cond0

    while pos <= len(core):
        split_at = _next_split_at_nesting_zero(core, pos)
        body = core[pos:split_at].strip()
        out.append((current_cond, body))
        if split_at >= len(core):
            break
        rest = _skip_ws(core, split_at)
        if rest >= len(core):
            break
        m_elsif = re.match(r"\bELSIF\b", core[rest:], re.IGNORECASE)
        if m_elsif:
            rest += m_elsif.end()
            rest = _skip_ws(core, rest)
            ct_start = rest
            tidx = _find_then_at_depth_zero(core, ct_start)
            if tidx == -1:
                return None
            current_cond = core[ct_start:tidx].strip()
            pos = _skip_ws(core, tidx + 4)
            continue
        if re.match(r"\bELSE\b", core[rest:], re.IGNORECASE):
            m_else = re.match(r"\bELSE\b", core[rest:], re.IGNORECASE)
            if not m_else:
                return None
            rest += m_else.end()
            current_cond = None
            pos = _skip_ws(core, rest)
            continue
        return None

    return out if out else None


def _skip_ws(s: str, i: int) -> int:
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    return i


def _find_then_at_depth_zero(s: str, start: int) -> int:
    depth_paren = 0
    i = start
    n = len(s)
    while i < n:
        if s[i] in "\"'":
            q = s[i]
            i += 1
            while i < n and s[i] != q:
                i += 1
            i = min(i + 1, n)
            continue
        if s[i] == "(":
            depth_paren += 1
            i += 1
            continue
        if s[i] == ")" and depth_paren > 0:
            depth_paren -= 1
            i += 1
            continue
        if depth_paren == 0:
            m = re.match(r"THEN\b", s[i:], re.IGNORECASE)
            if m:
                return i
        i += 1
    return -1


def _next_split_at_nesting_zero(s: str, start: int) -> int:
    """Index of next ``ELSIF``/``ELSE`` at inner-IF nest 0, or ``len(s)``."""

    nest = 0
    i = start
    n = len(s)
    while i < n:
        if s[i] in "\"'":
            q = s[i]
            i += 1
            while i < n and s[i] != q:
                i += 1
            i = min(i + 1, n)
            continue
        m_if = re.match(r"\bIF\b", s[i:], re.IGNORECASE)
        if m_if:
            nest += 1
            i += m_if.end()
            continue
        m_end = re.match(r"\bEND_IF\b", s[i:], re.IGNORECASE)
        if m_end:
            nest -= 1
            if nest < 0:
                return len(s)
            i += m_end.end()
            continue
        if nest == 0:
            if re.match(r"\bELSIF\b", s[i:], re.IGNORECASE):
                return i
            if re.match(r"\bELSE\b", s[i:], re.IGNORECASE):
                return i
        i += 1
    return len(s)


__all__ = ["parse_outer_if_elsif_else"]
