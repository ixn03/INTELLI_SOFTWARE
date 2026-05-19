"""Function Block Diagram parser for L5X-style routine XML.

Parses only element shapes exercised by this project's synthetic fixtures
and tests. Unknown elements are skipped; nothing is invented beyond what
appears in the export XML.
"""

from __future__ import annotations

import re
from typing import Iterable

from lxml import etree

from app.models.control_model import ControlInstruction


def _attr(element: etree._Element, name: str, default: str = "") -> str:
    return str(element.get(name) or default)


def _element_text(element: etree._Element) -> str:
    return (element.text or "").strip()


def _collect_parameters(block_el: etree._Element) -> dict[str, str]:
    params: dict[str, str] = {}
    for param in block_el.findall(".//Parameter"):
        key = _attr(param, "Name") or _attr(param, "Tag")
        if not key:
            continue
        val = _attr(param, "Value") or _element_text(param)
        if val:
            params[key] = val
    return params


def _block_type_name(block_el: etree._Element) -> str:
    return (
        _attr(block_el, "BlockType")
        or _attr(block_el, "Type")
        or _attr(block_el, "Instruction")
        or "UNKNOWN"
    )


def _parse_block(
    block_el: etree._Element,
    *,
    sheet_number: str,
    counter: list[int],
) -> ControlInstruction:
    counter[0] += 1
    block_id = _attr(block_el, "ID") or str(counter[0])
    block_type = _block_type_name(block_el)
    operand = _attr(block_el, "Operand") or _attr(block_el, "Name")
    parameters = _collect_parameters(block_el)

    operands: list[str] = []
    if operand:
        operands.append(operand)
    operands.extend(parameters.values())

    connects_to: list[str] = []
    for wire in block_el.findall(".//Wire"):
        to_id = _attr(wire, "ToID")
        if to_id:
            connects_to.append(f"node::{to_id}")

    return ControlInstruction(
        id=f"fbd_b{block_id}",
        instruction_type=block_type,
        operands=operands,
        output=operand or None,
        raw_text=etree.tostring(block_el, encoding="unicode", method="xml").strip(),
        language="fbd",
        metadata={
            "object_subtype": "function_block",
            "block_type": block_type,
            "block_name": operand or block_id,
            "block_id": block_id,
            "sheet_number": sheet_number,
            "parameters": parameters,
            "connects_to": connects_to,
            "parse_status": "parsed",
            "parser": "intelli_l5x_fbd_v1",
        },
    )


def _parse_io_ref(
    ref_el: etree._Element,
    *,
    direction: str,
    sheet_number: str,
) -> ControlInstruction:
    ref_id = _attr(ref_el, "ID") or _attr(ref_el, "Name")
    operand = _attr(ref_el, "Operand") or _attr(ref_el, "Name")
    return ControlInstruction(
        id=f"fbd_{direction[0].lower()}ref_{ref_id}",
        instruction_type=f"{direction}_REF",
        operands=[operand] if operand else [],
        output=operand if direction == "OUTPUT" else None,
        raw_text=etree.tostring(ref_el, encoding="unicode", method="xml").strip(),
        language="fbd",
        metadata={
            "object_subtype": "function_block_pin",
            "pin": operand,
            "direction": direction.lower(),
            "ref_id": ref_id,
            "sheet_number": sheet_number,
            "parse_status": "parsed",
            "parser": "intelli_l5x_fbd_v1",
        },
    )


def _parse_wire(
    wire_el: etree._Element,
    *,
    sheet_number: str,
    index: int,
) -> ControlInstruction:
    from_id = _attr(wire_el, "FromID")
    to_id = _attr(wire_el, "ToID")
    from_param = _attr(wire_el, "FromParam")
    to_param = _attr(wire_el, "ToParam")
    operands = [p for p in (from_id, to_id, from_param, to_param) if p]
    connects_to: list[str] = []
    if to_id:
        connects_to.append(f"node::{to_id}")
    if from_id:
        connects_to.append(f"node::{from_id}")
    return ControlInstruction(
        id=f"fbd_wire_{index}",
        instruction_type="WIRE",
        operands=operands,
        raw_text=etree.tostring(wire_el, encoding="unicode", method="xml").strip(),
        language="fbd",
        metadata={
            "object_subtype": "fbd_parameter_binding",
            "from_id": from_id,
            "to_id": to_id,
            "from_param": from_param,
            "to_param": to_param,
            "sheet_number": sheet_number,
            "connects_to": connects_to,
            "parse_status": "parsed",
            "parser": "intelli_l5x_fbd_v1",
        },
    )


def parse_l5x_fbd_routine(routine_element: etree._Element) -> list[ControlInstruction]:
    """Extract FBD blocks, I/O refs, and wires from an L5X routine element."""

    instructions: list[ControlInstruction] = []
    counter = [0]
    content = routine_element.find(".//FBDContent")
    if content is None:
        content = routine_element

    sheets = content.findall(".//Sheet")
    if not sheets:
        sheets = [content]

    for sheet in sheets:
        sheet_number = _attr(sheet, "Number") or _attr(sheet, "Name") or "0"
        for block in sheet.findall(".//Block"):
            instructions.append(
                _parse_block(block, sheet_number=sheet_number, counter=counter)
            )
        for iref in sheet.findall(".//IRef"):
            instructions.append(
                _parse_io_ref(iref, direction="INPUT", sheet_number=sheet_number)
            )
        for oref in sheet.findall(".//ORef"):
            instructions.append(
                _parse_io_ref(oref, direction="OUTPUT", sheet_number=sheet_number)
            )
        for idx, wire in enumerate(sheet.findall(".//Wire")):
            instructions.append(
                _parse_wire(wire, sheet_number=sheet_number, index=idx)
            )

    return instructions


def extract_fbd_tags(instructions: Iterable[ControlInstruction]) -> set[str]:
    """Collect tag-like operand names from FBD instructions."""

    tags: set[str] = set()
    ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_\[\].:]*$")
    for inst in instructions:
        for op in inst.operands:
            if op and ident_re.match(op.strip()):
                tags.add(op.strip())
        params = (inst.metadata or {}).get("parameters") or {}
        if isinstance(params, dict):
            for val in params.values():
                if isinstance(val, str) and ident_re.match(val.strip()):
                    tags.add(val.strip())
        pin = (inst.metadata or {}).get("pin")
        if isinstance(pin, str) and ident_re.match(pin.strip()):
            tags.add(pin.strip())
    return tags


__all__ = ["parse_l5x_fbd_routine", "extract_fbd_tags"]
