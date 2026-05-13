import re
from typing import Iterable

from app.models.control_model import ControlInstruction


# Legacy pattern kept for tests that might import it; the scanner below
# supersedes its behaviour for real rung text.
ROCKWELL_INSTRUCTION_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_]+)\s*\(([^)]*)\)")

BOOLEAN_OUTPUT_INSTRUCTIONS = {"OTE", "OTL", "OTU"}
STATEFUL_OUTPUT_INSTRUCTIONS = {"TON", "TONR", "TOF", "RTO", "CTU", "CTD"}
CONDITION_INSTRUCTIONS = {
    "XIC",
    "XIO",
    "ONS",
    "EQU",
    "NEQ",
    "GRT",
    "GEQ",
    "LES",
    "LEQ",
    "LIM",
}
WRITE_INSTRUCTIONS = BOOLEAN_OUTPUT_INSTRUCTIONS | STATEFUL_OUTPUT_INSTRUCTIONS

_BRANCH_KEYWORDS = frozenset({"BST", "NXB", "BND"})

_INSTR_HEAD_RE = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s*\(")


def _is_ident_char(c: str) -> bool:
    return c.isalnum() or c == "_"


def _branch_keyword_at(s: str, i: int) -> str | None:
    """Return BST/NXB/BND when a bare branch token starts at ``i``."""

    for kw in ("BST", "NXB", "BND"):
        ln = len(kw)
        if i + ln > len(s):
            continue
        if s[i : i + ln].upper() != kw:
            continue
        if i > 0 and _is_ident_char(s[i - 1]):
            continue
        after = i + ln
        if after < len(s) and _is_ident_char(s[after]):
            continue
        if after < len(s) and s[after] == "(":
            continue
        return kw
    return None


def _matching_close_paren(s: str, open_paren: int) -> int:
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


def _matching_close_bracket(s: str, open_bracket: int) -> int:
    depth_sq = 0
    depth_paren = 0
    j = open_bracket
    n = len(s)
    while j < n:
        ch = s[j]
        if ch == "[" and depth_paren == 0:
            depth_sq += 1
        elif ch == "]" and depth_paren == 0:
            depth_sq -= 1
            if depth_sq == 0:
                return j
        elif ch == "(":
            depth_paren += 1
        elif ch == ")" and depth_paren > 0:
            depth_paren -= 1
        j += 1
    return -1


def _split_operand_list_respecting_nesting(inner: str) -> list[str]:
    """Split ``inner`` on commas not inside ``()`` or ``[]``."""

    parts: list[str] = []
    cur: list[str] = []
    d_paren = d_bracket = 0
    for ch in inner:
        if ch == "(":
            d_paren += 1
        elif ch == ")" and d_paren > 0:
            d_paren -= 1
        elif ch == "[":
            d_bracket += 1
        elif ch == "]" and d_bracket > 0:
            d_bracket -= 1
        elif ch == "," and d_paren == 0 and d_bracket == 0:
            piece = "".join(cur).strip()
            if piece:
                parts.append(piece)
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_ladder_rung_text(
    rung_text: str,
    rung_number: int | None = None,
) -> list[ControlInstruction]:
    """Tokenize a single Rockwell ladder rung string.

    * Instructions ``NAME(...)`` use parenthesis-depth counting so
      nested ``()`` inside operands (e.g. UDT constructors in JSR
      parameter lists) do not truncate early.
    * Bare branch markers ``BST`` / ``NXB`` / ``BND`` (no parentheses)
      become synthetic instructions with ``metadata["branch_marker"]``.
      We do **not** infer which logical branch an operand instruction
      belongs to — that remains an unsupported branch-attribution
      problem for a future analyzer.
    * Square-bracket parallel notation ``[XIC(A),XIC(B)]`` emits a
      ``PARALLEL_BRANCH`` instruction (operands = raw arm strings) and
      **recursively** parses each arm so XIC/OTE/etc. still appear as
      first-class instructions for tag discovery. Recursive fragments
      inherit the same ``rung_number``; ``metadata["parallel_arm"]``
      marks instructions originating inside a bracket arm.
    * ``PARALLEL_BRANCH`` is not a real Rockwell opcode; it exists only
      as a structural placeholder with ``platform_specific`` metadata
      in the normalizer's generic unknown-instruction path.

    Unsupported / intentionally narrow:

    * ASCII-art rung graphics, /OT latch bars, and FBD-in-text exports.
    * Branch levels beyond one ``BST..BND`` group interpreted together
      (we only tokenize; we do not build a boolean DAG).
    """

    instructions: list[ControlInstruction] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        cid = f"r{rung_number or 0}_i{counter}"
        counter += 1
        return cid

    def append_instruction(
        instruction_type: str,
        operands: list[str],
        raw_span: str,
        *,
        output: str | None = None,
        extra_meta: dict | None = None,
    ) -> None:
        meta: dict = {
            "vendor": "rockwell",
            "parser": "rockwell_rung_text_tokenizer_v2",
            "instruction_role": _instruction_role(instruction_type),
            "output_role": _output_role(instruction_type),
            "source_span": {
                "start": 0,
                "end": len(raw_span),
            },
            "rung_text": rung_text,
        }
        if extra_meta:
            meta.update(extra_meta)
        instructions.append(
            ControlInstruction(
                id=next_id(),
                instruction_type=instruction_type,
                operands=operands,
                output=output if output is not None else _get_output_operand(instruction_type, operands),
                raw_text=raw_span,
                language="ladder",
                rung_number=rung_number,
                metadata=meta,
            )
        )

    def scan_fragment(fragment: str, *, parallel_arm: bool) -> None:
        nonlocal counter
        i = 0
        n = len(fragment)
        while i < n:
            while i < n and fragment[i].isspace():
                i += 1
            if i >= n:
                break

            kw = _branch_keyword_at(fragment, i)
            if kw:
                append_instruction(
                    kw,
                    [],
                    kw,
                    extra_meta={
                        "branch_marker": True,
                        "parallel_branch_notation": "bst_nxb_bnd",
                    },
                )
                i += len(kw)
                continue

            if fragment[i] == "[":
                close_b = _matching_close_bracket(fragment, i)
                if close_b == -1:
                    i += 1
                    continue
                inner = fragment[i + 1 : close_b]
                raw_bracket = fragment[i : close_b + 1]
                arms = _split_operand_list_respecting_nesting(inner)
                append_instruction(
                    "PARALLEL_BRANCH",
                    arms,
                    raw_bracket,
                    output=None,
                    extra_meta={
                        "parallel_branch_notation": "square_bracket",
                        "parallel_arm_count": len(arms),
                    },
                )
                for arm in arms:
                    scan_fragment(arm.strip(), parallel_arm=True)
                i = close_b + 1
                continue

            m = _INSTR_HEAD_RE.match(fragment, i)
            if not m:
                i += 1
                continue

            name = m.group(1)
            if name in _BRANCH_KEYWORDS:
                i += 1
                continue

            # ``m`` is anchored at ``i``; the opening ``(`` is always the
            # last character of the full regex match (``NAME\s*(``).
            open_paren = m.end(0) - 1
            if open_paren < 0 or open_paren >= n or fragment[open_paren] != "(":
                i += 1
                continue

            close_p = _matching_close_paren(fragment, open_paren)
            if close_p == -1:
                i += 1
                continue

            inner = fragment[open_paren + 1 : close_p]
            operands = _parse_operands(inner)
            raw_span = fragment[i : close_p + 1]
            span_start = m.start(0)
            span_end = close_p + 1
            extra: dict = {}
            if parallel_arm:
                extra["parallel_arm"] = True
            meta = {
                "vendor": "rockwell",
                "parser": "rockwell_rung_text_tokenizer_v2",
                "instruction_role": _instruction_role(name),
                "output_role": _output_role(name),
                "source_span": {"start": span_start, "end": span_end},
                "rung_text": rung_text,
                **extra,
            }
            instructions.append(
                ControlInstruction(
                    id=next_id(),
                    instruction_type=name,
                    operands=operands,
                    output=_get_output_operand(name, operands),
                    raw_text=raw_span,
                    language="ladder",
                    rung_number=rung_number,
                    metadata=meta,
                )
            )
            i = close_p + 1

    scan_fragment(rung_text, parallel_arm=False)

    for inst in instructions:
        span = inst.metadata.get("source_span")
        if isinstance(span, dict) and "start" in span:
            if inst.metadata.get("parallel_arm"):
                continue
            raw = inst.raw_text or ""
            if raw and rung_text.count(raw) == 1:
                real_start = rung_text.find(raw)
                if real_start != -1:
                    span["start"] = real_start
                    span["end"] = real_start + len(raw)

    return instructions


def extract_operand_tags(instructions: Iterable[ControlInstruction]) -> set[str]:
    tags: set[str] = set()

    for instruction in instructions:
        for operand in instruction.operands:
            if _looks_like_tag_reference(operand):
                tags.add(operand)

    return tags


def _parse_operands(operand_text: str) -> list[str]:
    operands: list[str] = []
    current = []
    depth = 0

    for char in operand_text:
        if char == "," and depth == 0:
            operand = "".join(current).strip()
            if operand:
                operands.append(operand)
            current = []
            continue

        if char in "([{":
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1

        current.append(char)

    operand = "".join(current).strip()
    if operand:
        operands.append(operand)

    return operands


def _get_output_operand(instruction_type: str, operands: list[str]) -> str | None:
    if instruction_type not in WRITE_INSTRUCTIONS or not operands:
        return None

    return operands[0]


def _instruction_role(instruction_type: str) -> str:
    if instruction_type in BOOLEAN_OUTPUT_INSTRUCTIONS:
        return "boolean_output"
    if instruction_type in STATEFUL_OUTPUT_INSTRUCTIONS:
        return "stateful_output"
    if instruction_type in CONDITION_INSTRUCTIONS:
        return "condition"
    return "unknown"


def _output_role(instruction_type: str) -> str | None:
    if instruction_type in BOOLEAN_OUTPUT_INSTRUCTIONS:
        return "drives_boolean_tag"
    if instruction_type in STATEFUL_OUTPUT_INSTRUCTIONS:
        return "writes_instruction_structure"
    return None


def _looks_like_tag_reference(value: str) -> bool:
    if not value:
        return False

    normalized = value.strip()

    if normalized.startswith(('"', "'")):
        return False

    if _is_number(normalized):
        return False

    if normalized.upper() in {"TRUE", "FALSE"}:
        return False

    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_\[\].:]*$", normalized))


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False
