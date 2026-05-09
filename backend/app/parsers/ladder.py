import re
from typing import Iterable

from app.models.control_model import ControlInstruction


ROCKWELL_INSTRUCTION_PATTERN = re.compile(r"\b([A-Z][A-Z0-9_]+)\s*\(([^)]*)\)")

BOOLEAN_OUTPUT_INSTRUCTIONS = {"OTE", "OTL", "OTU"}
STATEFUL_OUTPUT_INSTRUCTIONS = {"TON", "TOF", "RTO", "CTU", "CTD"}
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


def parse_ladder_rung_text(
    rung_text: str,
    rung_number: int | None = None,
) -> list[ControlInstruction]:
    instructions: list[ControlInstruction] = []

    for index, match in enumerate(ROCKWELL_INSTRUCTION_PATTERN.finditer(rung_text)):
        instruction_type = match.group(1)
        operands = _parse_operands(match.group(2))
        output = _get_output_operand(instruction_type, operands)

        instructions.append(
            ControlInstruction(
                id=f"r{rung_number or 0}_i{index}",
                instruction_type=instruction_type,
                operands=operands,
                output=output,
                raw_text=match.group(0),
                language="ladder",
                rung_number=rung_number,
                metadata={
                    "vendor": "rockwell",
                    "parser": "rockwell_rung_text_tokenizer",
                    "instruction_role": _instruction_role(instruction_type),
                    "output_role": _output_role(instruction_type),
                    "source_span": {
                        "start": match.start(),
                        "end": match.end(),
                    },
                },
            )
        )

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
