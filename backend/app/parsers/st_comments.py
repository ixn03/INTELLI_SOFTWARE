"""Structured Text comment handling for deterministic parsers.

Rockwell Logix exports ST with ``//`` line comments and ``(* ... *)``
block comments. The L5X connector preserves the *raw* routine body on
``ControlRoutine.raw_logic`` (including comments) for auditability.

The block parser (:mod:`app.parsers.structured_text_blocks`) strips
comments only from a *scratch copy* of the text used to locate
keywords and statement boundaries. Nested ``(* ... (* inner *) ... *)``
block comments are supported; string literals that happen to contain
``(*`` are intentionally **not** modeled (unsupported edge case — see
:func:`strip_st_comments_for_parsing` docstring).

This module is parser-only; it does not touch the reasoning schema.
"""

from __future__ import annotations

import re


def strip_st_comments_for_parsing(source: str) -> str:
    """Return ``source`` with ST comments removed, keeping newlines.

    Unhandled / intentionally unsupported:

    * Comment markers inside single- or double-quoted string literals
      are treated as real comment starts/ends, which can corrupt
      parsing for exotic generated code. Realistic PLC ST rarely
      embeds ``(*`` inside strings.

    * ``{* IEC-style *}`` block comments are not used by Rockwell ST
      exports we target; they are left untouched (would appear as
      stray characters in the scratch buffer).
    """

    without_blocks = _strip_block_comments_nested(source)
    return _strip_line_comments(without_blocks)


def _strip_block_comments_nested(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if i + 1 < n and text[i] == "(" and text[i + 1] == "*":
            depth = 1
            i += 2
            while i < n and depth:
                if i + 1 < n and text[i] == "(" and text[i + 1] == "*":
                    depth += 1
                    i += 2
                    continue
                if i + 1 < n and text[i] == "*" and text[i + 1] == ")":
                    depth -= 1
                    i += 2
                    continue
                i += 1
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


_LINE_COMMENT_RE = re.compile(r"//[^\n]*")


def _strip_line_comments(text: str) -> str:
    return _LINE_COMMENT_RE.sub("", text)
