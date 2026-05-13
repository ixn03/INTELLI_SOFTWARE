import re
from typing import Iterable

from app.models.control_model import ControlInstruction
from app.parsers.st_comments import strip_st_comments_for_parsing


ASSIGNMENT_PATTERN = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_\[\].:]*)\s*:=\s*(.+?)\s*;?\s*$",
    re.DOTALL,
)
FUNCTION_CALL_PATTERN = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\((.*?)\)", re.DOTALL)
COMPARISON_PATTERN = re.compile(r"(<>|>=|<=|=|>|<)")
TAG_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_\[\].:]*\b")

ST_KEYWORDS = {
    "IF",
    "THEN",
    "ELSIF",
    "ELSE",
    "END_IF",
    "CASE",
    "OF",
    "END_CASE",
    "AND",
    "OR",
    "NOT",
    "XOR",
    "TRUE",
    "FALSE",
    "MOD",
}

FUNCTION_OUTPUT_OPERAND_INDEX = {
    "MOV": 1,
    "COP": 1,
    "CPT": 0,
    "TON": 0,
    "TONR": 0,
    "TOF": 0,
    "RTO": 0,
    "CTU": 0,
    "CTD": 0,
    # JSR name only in typical ST call form; parameters are not modeled.
    "JSR": 0,
    # ABS is an expression helper — no structured output tag index.
}


def _split_args_respecting_parens(arg_text: str) -> list[str]:
    """Split function argument text on commas outside ``()`` and ``[]``."""

    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in arg_text:
        if char == "," and depth == 0:
            piece = "".join(current).strip()
            if piece:
                parts.append(piece)
            current = []
            continue
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth > 0:
            depth -= 1
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _closing_paren_index(text: str, open_paren: int) -> int:
    depth = 0
    j = open_paren
    n = len(text)
    while j < n:
        ch = text[j]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def parse_structured_text(
    routine_text: str,
    routine_name: str | None = None,
) -> list[ControlInstruction]:
    """Parse ST into flat ``ControlInstruction`` rows (legacy / heuristic).

    Control-flow topology for reasoning is taken from
    :func:`app.parsers.structured_text_blocks.parse_structured_text_blocks`;
    this function exists for tag discovery and coarse call inventory.
    Comments are stripped using the same rules as the block parser.
    """

    scrubbed = strip_st_comments_for_parsing(routine_text)
    instructions: list[ControlInstruction] = []

    for index, statement in enumerate(_split_statements(scrubbed)):
        normalized = statement.strip()
        if not normalized:
            continue

        control_instruction = _parse_control_flow(index, normalized, routine_name)
        if control_instruction:
            instructions.append(control_instruction)
            continue

        assignment = _parse_assignment(index, normalized, routine_name)
        if assignment:
            instructions.append(assignment)

        instructions.extend(_parse_function_calls(index, normalized, routine_name))

    return instructions


def extract_structured_text_tags(instructions: Iterable[ControlInstruction]) -> set[str]:
    tags: set[str] = set()

    for instruction in instructions:
        for operand in instruction.operands:
            if _looks_like_tag_reference(operand):
                tags.add(operand)

        if instruction.output and _looks_like_tag_reference(instruction.output):
            tags.add(instruction.output)

    return tags


def _parse_control_flow(
    index: int,
    statement: str,
    routine_name: str | None,
) -> ControlInstruction | None:
    upper_statement = statement.upper()

    if upper_statement.startswith("IF "):
        condition = _between_keyword(statement, "IF", "THEN")
        return _control_instruction(index, "IF", condition, routine_name)

    if upper_statement.startswith("ELSIF "):
        condition = _between_keyword(statement, "ELSIF", "THEN")
        return _control_instruction(index, "ELSIF", condition, routine_name)

    if upper_statement == "ELSE":
        return _control_instruction(index, "ELSE", "", routine_name)

    if upper_statement == "END_IF":
        return _control_instruction(index, "END_IF", "", routine_name)

    return None


def _control_instruction(
    index: int,
    instruction_type: str,
    condition: str,
    routine_name: str | None,
) -> ControlInstruction:
    operands = _extract_tags(condition)
    comparisons = COMPARISON_PATTERN.findall(condition)

    return ControlInstruction(
        id=f"st_{index}",
        instruction_type=instruction_type,
        operands=operands,
        raw_text=condition or instruction_type,
        language="structured_text",
        metadata={
            "vendor": "rockwell",
            "parser": "rockwell_structured_text_parser",
            "routine": routine_name,
            "instruction_role": "control_flow",
            "comparison_operators": comparisons,
        },
    )


def _parse_assignment(
    index: int,
    statement: str,
    routine_name: str | None,
) -> ControlInstruction | None:
    match = ASSIGNMENT_PATTERN.match(statement)
    if not match:
        return None

    target = match.group(1).strip()
    expression = match.group(2).strip()
    expression_tags = _extract_tags(expression)

    operands = [target]
    operands.extend(tag for tag in expression_tags if tag != target)

    return ControlInstruction(
        id=f"st_{index}",
        instruction_type="ASSIGN",
        operands=operands,
        output=target,
        raw_text=statement,
        language="structured_text",
        metadata={
            "vendor": "rockwell",
            "parser": "rockwell_structured_text_parser",
            "routine": routine_name,
            "instruction_role": "assignment",
            "expression": expression,
            "comparison_operators": COMPARISON_PATTERN.findall(expression),
        },
    )


def _parse_function_calls(
    index: int,
    statement: str,
    routine_name: str | None,
) -> list[ControlInstruction]:

    instructions: list[ControlInstruction] = []
    i = 0
    n = len(statement)
    head = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\s*\(")

    while i < n:
        m = head.match(statement, i)
        if not m:
            i += 1
            continue

        name = m.group(1)
        open_paren = m.end(0) - 1
        close_p = _closing_paren_index(statement, open_paren)
        if close_p == -1:
            i += 1
            continue

        raw_args = statement[open_paren + 1 : close_p].strip()
        operands = _split_args_respecting_parens(raw_args)

        output = None
        output_index = FUNCTION_OUTPUT_OPERAND_INDEX.get(name)
        if output_index is not None and len(operands) > output_index:
            cand = operands[output_index]
            if name == "JSR":
                output = cand.strip()
            elif _looks_like_tag_reference(cand):
                output = cand

        instructions.append(
            ControlInstruction(
                id=f"st_{index}_{name}_{i}",
                instruction_type=name,
                operands=operands,
                output=output,
                raw_text=statement[i : close_p + 1],
                language="structured_text",
                metadata={
                    "vendor": "rockwell",
                    "parser": "rockwell_structured_text_parser",
                    "routine": routine_name,
                    "instruction_role": "function_call",
                },
            )
        )
        i = close_p + 1

    return instructions


def _split_statements(routine_text: str) -> list[str]:
    """Split ST into coarse statements for legacy instruction extraction.

    Multi-line ``IF`` / ``ELSIF`` / ``ELSE`` / ``END_IF`` groups are split
    so control-flow keywords remain visible to
    :func:`_parse_control_flow`. Full boolean topology is **not**
    reconstructed here — use :func:`parse_structured_text_blocks` for
    that. This splitter only needs to be good enough for tag / call
    heuristics on ``ControlInstruction`` lists.
    """

    statements: list[str] = []
    current: list[str] = []

    for line in routine_text.splitlines():

        stripped = line.strip()
        if not stripped:
            continue

        upper = stripped.upper()

        if upper.startswith("IF ") and re.search(r"\bTHEN\s*$", stripped, re.IGNORECASE):
            if current:
                statements.append(" ".join(current))
                current = []
            statements.append(stripped)
            continue

        if upper.startswith("ELSIF ") and re.search(r"\bTHEN\s*$", stripped, re.IGNORECASE):
            if current:
                statements.append(" ".join(current))
                current = []
            statements.append(stripped)
            continue

        if upper.startswith("CASE ") and re.search(r"\bOF\s*$", stripped, re.IGNORECASE):
            if current:
                statements.append(" ".join(current))
                current = []
            statements.append(stripped)
            continue

        if upper.startswith("END_CASE"):
            if current:
                statements.append(" ".join(current))
                current = []
            statements.append(stripped)
            continue

        if re.match(r"^\d+\s*:", stripped):
            if current:
                statements.append(" ".join(current))
                current = []
            statements.append(stripped)
            continue

        if upper == "ELSE":
            if current:
                statements.append(" ".join(current))
                current = []
            statements.append(stripped)
            continue

        if upper.startswith("END_IF"):
            if current:
                statements.append(" ".join(current))
                current = []
            statements.append(stripped)
            continue

        current.append(stripped)

        if stripped.endswith(";"):
            statements.append(" ".join(current))
            current = []

    if current:
        statements.append(" ".join(current))

    return statements


def _between_keyword(
    statement: str,
    start_keyword: str,
    end_keyword: str,
) -> str:

    upper_statement = statement.upper()

    start_index = upper_statement.find(start_keyword)

    end_index = upper_statement.find(end_keyword)

    if start_index == -1 or end_index == -1:
        return ""

    return statement[
        start_index + len(start_keyword): end_index
    ].strip()


def _extract_tags(expression: str) -> list[str]:

    tags: list[str] = []

    for token in TAG_PATTERN.findall(expression):

        upper_token = token.upper()

        if upper_token in ST_KEYWORDS:
            continue

        if token.isnumeric():
            continue

        if token not in tags:
            tags.append(token)

    return tags


def _looks_like_tag_reference(value: str) -> bool:
    normalized = value.strip()

    if not normalized:
        return False

    if normalized.upper() in ST_KEYWORDS:
        return False

    if normalized.startswith(('"', "'")):
        return False

    try:
        float(normalized)
        return False
    except ValueError:
        return True
