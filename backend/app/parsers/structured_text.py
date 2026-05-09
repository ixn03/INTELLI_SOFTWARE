import re
from typing import Iterable

from app.models.control_model import ControlInstruction


ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_\[\].:]*)\s*:=\s*(.+?)\s*;?\s*$")
FUNCTION_CALL_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_]*)\s*\((.*?)\)", re.DOTALL)
COMPARISON_PATTERN = re.compile(r"(<>|>=|<=|=|>|<)")
TAG_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_\[\].:]*\b")

ST_KEYWORDS = {
    "IF",
    "THEN",
    "ELSIF",
    "ELSE",
    "END_IF",
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
    "TOF": 0,
    "RTO": 0,
    "CTU": 0,
    "CTD": 0,
}


def parse_structured_text(
    routine_text: str,
    routine_name: str | None = None,
) -> list[ControlInstruction]:
    instructions: list[ControlInstruction] = []

    for index, statement in enumerate(_split_statements(routine_text)):
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

    for match in FUNCTION_CALL_PATTERN.finditer(statement):

        function_name = match.group(1).strip()

        raw_args = match.group(2).strip()

        operands = [
            operand.strip()
            for operand in raw_args.split(",")
            if operand.strip()
        ]

        output = None

        output_index = FUNCTION_OUTPUT_OPERAND_INDEX.get(function_name)

        if (
            output_index is not None
            and len(operands) > output_index
        ):
            output = operands[output_index]

        instructions.append(
            ControlInstruction(
                id=f"st_{index}_{function_name}",
                instruction_type=function_name,
                operands=operands,
                output=output,
                raw_text=match.group(0),
                language="structured_text",
                metadata={
                    "vendor": "rockwell",
                    "parser": "rockwell_structured_text_parser",
                    "routine": routine_name,
                    "instruction_role": "function_call",
                },
            )
        )

    return instructions


def _split_statements(routine_text: str) -> list[str]:

    statements: list[str] = []

    current = []

    for line in routine_text.splitlines():

        stripped = line.strip()

        if not stripped:
            continue

        current.append(stripped)

        if stripped.endswith(";") or stripped.upper() in {
            "ELSE",
            "END_IF",
        }:
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
