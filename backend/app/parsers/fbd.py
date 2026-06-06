"""Function Block Diagram parser for L5X-style routine XML.

Parses only element shapes exercised by this project's synthetic fixtures
and tests. Unknown elements are skipped; nothing is invented beyond what
appears in the export XML.
"""

from __future__ import annotations

import re
from typing import Iterable

from lxml import etree

from app.models.control_model import (
    ControlInstruction,
    FBDBlock,
    FBDPin,
    SourceLocation,
    TagReference,
)


def _attr(element: etree._Element, name: str, default: str = "") -> str:
    return str(element.get(name) or default)


def _element_text(element: etree._Element) -> str:
    return (element.text or "").strip()


def _lname(element: etree._Element) -> str:
    return etree.QName(element).localname


def _source_path(
    *,
    source_file: str | None = None,
    controller: str | None = None,
    program: str | None = None,
    routine: str | None = None,
    sheet: str | None = None,
    block: str | None = None,
    pin: str | None = None,
    wire: str | None = None,
) -> str:
    parts = []
    if source_file:
        parts.append(f"File:{source_file}")
    if controller:
        parts.append(f"Controller:{controller}")
    if program:
        parts.append(f"Program:{program}")
    if routine:
        parts.append(f"Routine:{routine}")
    if sheet:
        parts.append(f"Sheet:{sheet}")
    if block:
        parts.append(f"Block:{block}")
    if pin:
        parts.append(f"Pin:{pin}")
    if wire:
        parts.append(f"Wire:{wire}")
    return "/".join(parts)


def _safe_id_part(value: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip())
    return cleaned or "_"


def _tag_ref(value: str | None) -> TagReference | None:
    raw = (value or "").strip()
    if not raw or not _looks_like_tag(raw):
        return None
    base = raw.split(".", 1)[0].split("[", 1)[0]
    member_path: list[str] = []
    if "." in raw:
        member_path = [
            piece.split("[", 1)[0]
            for piece in raw.split(".", 1)[1].split(".")
            if piece.split("[", 1)[0]
        ]
    return TagReference(raw=raw, tag_name=base or raw, member_path=member_path)


def _looks_like_tag(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_\[\].:]*$", value.strip()))


def _normalize_direction(value: str | None, *, fallback: str = "unknown") -> str:
    low = (value or "").strip().lower()
    if low in {"input", "in", "read", "source"}:
        return "input"
    if low in {"output", "out", "write", "destination", "dest"}:
        return "output"
    if low in {"inout", "in/out", "inputoutput", "readwrite", "read/write"}:
        return "inout"
    return fallback


def _pin_value(pin_el: etree._Element) -> str:
    return (
        _attr(pin_el, "Tag")
        or _attr(pin_el, "Value")
        or _attr(pin_el, "Operand")
        or _element_text(pin_el)
    )


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
        raw_text=None,
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
        raw_text=None,
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
        raw_text=None,
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


def parse_l5x_fbd_blocks(
    routine_element: etree._Element,
    *,
    source_file: str | None = None,
    controller: str | None = None,
    program: str | None = None,
    routine: str | None = None,
) -> list[FBDBlock]:
    """Extract neutral FBD block/pin/wire IR from a Rockwell routine.

    The parser records only structure: block identities, pin directions when
    explicit or wire-endpoint deterministic, tag bindings, and connections.
    It does not infer vendor block behavior from block type names.
    """

    content = routine_element.find(".//FBDContent")
    if content is None:
        content = routine_element

    sheets = content.findall(".//Sheet")
    if not sheets:
        sheets = [content]

    blocks: list[FBDBlock] = []
    block_by_node: dict[str, FBDBlock] = {}
    pin_by_endpoint: dict[tuple[str, str], FBDPin] = {}
    counter = [0]

    for sheet in sheets:
        sheet_number = _attr(sheet, "Number") or _attr(sheet, "Name") or "0"
        for block_el in sheet.findall(".//Block"):
            block = _parse_fbd_block_ir(
                block_el,
                sheet_number=sheet_number,
                counter=counter,
                source_file=source_file,
                controller=controller,
                program=program,
                routine=routine,
            )
            blocks.append(block)
            node_id = str(block.metadata.get("node_id") or "")
            if node_id:
                block_by_node[node_id] = block
            for pin in block.pins:
                pin_by_endpoint[(node_id, pin.name)] = pin
        for ref_el, ref_kind in [
            *[(el, "input_ref") for el in sheet.findall(".//IRef")],
            *[(el, "output_ref") for el in sheet.findall(".//ORef")],
        ]:
            block = _parse_fbd_ref_block_ir(
                ref_el,
                ref_kind=ref_kind,
                sheet_number=sheet_number,
                source_file=source_file,
                controller=controller,
                program=program,
                routine=routine,
            )
            blocks.append(block)
            node_id = str(block.metadata.get("node_id") or "")
            if node_id:
                block_by_node[node_id] = block
            for pin in block.pins:
                pin_by_endpoint[(node_id, pin.name)] = pin

    wire_index = 0
    seen_wires: set[str] = set()
    for sheet in sheets:
        sheet_number = _attr(sheet, "Number") or _attr(sheet, "Name") or "0"
        for wire_el in sheet.findall(".//Wire"):
            wire_index += 1
            wire = _parse_fbd_wire_ir(
                wire_el,
                sheet_number=sheet_number,
                index=wire_index,
                block_by_node=block_by_node,
                pin_by_endpoint=pin_by_endpoint,
                source_file=source_file,
                controller=controller,
                program=program,
                routine=routine,
            )
            wire_id = str(wire["id"])
            if wire_id in seen_wires:
                continue
            seen_wires.add(wire_id)
            for block_id in (wire.get("source_block_id"), wire.get("target_block_id")):
                block = next((b for b in blocks if b.id == block_id), None)
                if block is not None:
                    block.wires.append(wire)

    return blocks


def _parse_fbd_block_ir(
    block_el: etree._Element,
    *,
    sheet_number: str,
    counter: list[int],
    source_file: str | None,
    controller: str | None,
    program: str | None,
    routine: str | None,
) -> FBDBlock:
    counter[0] += 1
    node_id = _attr(block_el, "ID") or str(counter[0])
    block_type = _block_type_name(block_el)
    block_name = _attr(block_el, "Name") or _attr(block_el, "Operand") or node_id
    block_id = (
        "fbd_block::"
        f"{_safe_id_part(program)}/{_safe_id_part(routine)}/{_safe_id_part(node_id)}"
    )
    source_location = SourceLocation(
        source_file=source_file,
        controller=controller,
        program=program,
        routine=routine,
        vendor="rockwell",
        path=_source_path(
            source_file=source_file,
            controller=controller,
            program=program,
            routine=routine,
            sheet=sheet_number,
            block=node_id,
        ),
    )
    block = FBDBlock(
        id=block_id,
        instance_name=block_name,
        definition_name=block_type,
        source_location=source_location,
        parameters=_collect_parameters(block_el),
        metadata={
            "parser": "intelli_l5x_fbd_ir_v1",
            "node_id": node_id,
            "sheet_number": sheet_number,
            "element_type": _lname(block_el),
            "block_type": block_type,
            "block_name": block_name,
        },
    )
    for pin_el in _iter_pin_elements(block_el):
        pin = _parse_fbd_pin_ir(
            pin_el,
            block=block,
            node_id=node_id,
            sheet_number=sheet_number,
            source_file=source_file,
            controller=controller,
            program=program,
            routine=routine,
        )
        if pin is not None and pin.id not in {p.id for p in block.pins}:
            block.pins.append(pin)
    operand = _attr(block_el, "Operand")
    if operand:
        pin = _make_fbd_pin(
            block=block,
            node_id=node_id,
            pin_name="Operand",
            direction="unknown",
            direction_source="operand_attribute",
            tag_value=operand,
            sheet_number=sheet_number,
            source_file=source_file,
            controller=controller,
            program=program,
            routine=routine,
        )
        if pin.id not in {p.id for p in block.pins}:
            block.pins.append(pin)
    return block


def _iter_pin_elements(block_el: etree._Element) -> list[etree._Element]:
    out: list[etree._Element] = []
    for child in block_el.iter():
        if child is block_el:
            continue
        name = _lname(child).lower()
        if name in {
            "parameter",
            "pin",
            "input",
            "output",
            "inout",
            "visiblepin",
        }:
            out.append(child)
    return out


def _parse_fbd_pin_ir(
    pin_el: etree._Element,
    *,
    block: FBDBlock,
    node_id: str,
    sheet_number: str,
    source_file: str | None,
    controller: str | None,
    program: str | None,
    routine: str | None,
) -> FBDPin | None:
    pin_name = (
        _attr(pin_el, "Name")
        or _attr(pin_el, "Pin")
        or _attr(pin_el, "Parameter")
        or _lname(pin_el)
    )
    if not pin_name:
        return None
    element_name = _lname(pin_el).lower()
    fallback = "unknown"
    if element_name == "input":
        fallback = "input"
    elif element_name == "output":
        fallback = "output"
    elif element_name == "inout":
        fallback = "inout"
    direction = _normalize_direction(
        _attr(pin_el, "Direction") or _attr(pin_el, "Usage"),
        fallback=fallback,
    )
    direction_source = (
        "explicit"
        if (_attr(pin_el, "Direction") or _attr(pin_el, "Usage"))
        else f"element:{element_name}"
        if fallback != "unknown"
        else "unknown"
    )
    return _make_fbd_pin(
        block=block,
        node_id=node_id,
        pin_name=pin_name,
        direction=direction,
        direction_source=direction_source,
        tag_value=_pin_value(pin_el),
        sheet_number=sheet_number,
        source_file=source_file,
        controller=controller,
        program=program,
        routine=routine,
        metadata={"element_type": _lname(pin_el)},
    )


def _parse_fbd_ref_block_ir(
    ref_el: etree._Element,
    *,
    ref_kind: str,
    sheet_number: str,
    source_file: str | None,
    controller: str | None,
    program: str | None,
    routine: str | None,
) -> FBDBlock:
    node_id = _attr(ref_el, "ID") or _attr(ref_el, "Name") or ref_kind
    operand = _attr(ref_el, "Operand") or _attr(ref_el, "Name")
    block_id = (
        "fbd_block::"
        f"{_safe_id_part(program)}/{_safe_id_part(routine)}/{_safe_id_part(node_id)}"
    )
    source_location = SourceLocation(
        source_file=source_file,
        controller=controller,
        program=program,
        routine=routine,
        vendor="rockwell",
        path=_source_path(
            source_file=source_file,
            controller=controller,
            program=program,
            routine=routine,
            sheet=sheet_number,
            block=node_id,
        ),
    )
    pin_direction = "output" if ref_kind == "input_ref" else "input"
    tag_binding_role = "source_tag" if ref_kind == "input_ref" else "destination_tag"
    block = FBDBlock(
        id=block_id,
        instance_name=node_id,
        definition_name=ref_kind,
        source_location=source_location,
        metadata={
            "parser": "intelli_l5x_fbd_ir_v1",
            "node_id": node_id,
            "sheet_number": sheet_number,
            "element_type": _lname(ref_el),
            "block_type": ref_kind,
            "io_ref_kind": ref_kind,
        },
    )
    block.pins.append(
        _make_fbd_pin(
            block=block,
            node_id=node_id,
            pin_name="Out" if ref_kind == "input_ref" else "In",
            direction=pin_direction,
            direction_source="io_ref_element",
            tag_value=operand,
            sheet_number=sheet_number,
            source_file=source_file,
            controller=controller,
            program=program,
            routine=routine,
            metadata={"tag_binding_role": tag_binding_role},
        )
    )
    return block


def _make_fbd_pin(
    *,
    block: FBDBlock,
    node_id: str,
    pin_name: str,
    direction: str,
    direction_source: str,
    tag_value: str | None,
    sheet_number: str,
    source_file: str | None,
    controller: str | None,
    program: str | None,
    routine: str | None,
    metadata: dict[str, str] | None = None,
) -> FBDPin:
    pin_id = (
        "fbd_pin::"
        f"{_safe_id_part(program)}/{_safe_id_part(routine)}"
        f"/{_safe_id_part(node_id)}/{_safe_id_part(pin_name)}"
    )
    source_location = SourceLocation(
        source_file=source_file,
        controller=controller,
        program=program,
        routine=routine,
        vendor="rockwell",
        path=_source_path(
            source_file=source_file,
            controller=controller,
            program=program,
            routine=routine,
            sheet=sheet_number,
            block=node_id,
            pin=pin_name,
        ),
    )
    return FBDPin(
        id=pin_id,
        name=pin_name,
        direction=direction,  # type: ignore[arg-type]
        tag=_tag_ref(tag_value),
        source_location=source_location,
        metadata={
            "node_id": node_id,
            "block_id": block.id,
            "direction_source": direction_source,
            **(metadata or {}),
        },
    )


def _parse_fbd_wire_ir(
    wire_el: etree._Element,
    *,
    sheet_number: str,
    index: int,
    block_by_node: dict[str, FBDBlock],
    pin_by_endpoint: dict[tuple[str, str], FBDPin],
    source_file: str | None,
    controller: str | None,
    program: str | None,
    routine: str | None,
) -> dict[str, object]:
    from_id = _attr(wire_el, "FromID")
    to_id = _attr(wire_el, "ToID")
    from_param = _attr(wire_el, "FromParam")
    to_param = _attr(wire_el, "ToParam")
    source_pin = _resolve_or_create_wire_pin(
        node_id=from_id,
        param=from_param,
        role_direction="output",
        block_by_node=block_by_node,
        pin_by_endpoint=pin_by_endpoint,
        sheet_number=sheet_number,
        source_file=source_file,
        controller=controller,
        program=program,
        routine=routine,
    )
    target_pin = _resolve_or_create_wire_pin(
        node_id=to_id,
        param=to_param,
        role_direction="input",
        block_by_node=block_by_node,
        pin_by_endpoint=pin_by_endpoint,
        sheet_number=sheet_number,
        source_file=source_file,
        controller=controller,
        program=program,
        routine=routine,
    )
    wire_id = (
        "fbd_wire::"
        f"{_safe_id_part(program)}/{_safe_id_part(routine)}/{_safe_id_part(str(index))}"
    )
    return {
        "id": wire_id,
        "source_block_id": source_pin.metadata.get("block_id") if source_pin else None,
        "source_pin_id": source_pin.id if source_pin else None,
        "target_block_id": target_pin.metadata.get("block_id") if target_pin else None,
        "target_pin_id": target_pin.id if target_pin else None,
        "source_node_id": from_id,
        "target_node_id": to_id,
        "source_param": from_param,
        "target_param": to_param,
        "source_location": _source_path(
            source_file=source_file,
            controller=controller,
            program=program,
            routine=routine,
            sheet=sheet_number,
            wire=str(index),
        ),
        "missing_source_endpoint": source_pin is None,
        "missing_target_endpoint": target_pin is None,
        "metadata": {"parser": "intelli_l5x_fbd_ir_v1"},
    }


def _resolve_or_create_wire_pin(
    *,
    node_id: str,
    param: str,
    role_direction: str,
    block_by_node: dict[str, FBDBlock],
    pin_by_endpoint: dict[tuple[str, str], FBDPin],
    sheet_number: str,
    source_file: str | None,
    controller: str | None,
    program: str | None,
    routine: str | None,
) -> FBDPin | None:
    block = block_by_node.get(node_id)
    if block is None:
        return None
    if not param and len(block.pins) == 1:
        return block.pins[0]
    pin_key = param or role_direction
    existing = pin_by_endpoint.get((node_id, pin_key))
    if existing is not None:
        return existing
    pin = _make_fbd_pin(
        block=block,
        node_id=node_id,
        pin_name=pin_key,
        direction=role_direction,
        direction_source="wire_endpoint",
        tag_value=None,
        sheet_number=sheet_number,
        source_file=source_file,
        controller=controller,
        program=program,
        routine=routine,
    )
    block.pins.append(pin)
    pin_by_endpoint[(node_id, pin_key)] = pin
    return pin


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


def extract_fbd_block_tags(blocks: Iterable[FBDBlock]) -> set[str]:
    """Collect tag-like references from structural FBD IR."""

    tags: set[str] = set()
    for block in blocks:
        for pin in block.pins:
            if pin.tag and pin.tag.raw:
                tags.add(pin.tag.raw)
    return tags


__all__ = [
    "parse_l5x_fbd_blocks",
    "parse_l5x_fbd_routine",
    "extract_fbd_block_tags",
    "extract_fbd_tags",
]
