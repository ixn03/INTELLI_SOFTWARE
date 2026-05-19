"""Sequential Function Chart parser for L5X-style routine XML.

Parses step, transition, and action elements from synthetic fixture shapes
only. Connectivity is taken from explicit XML attributes when present.
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


def _parse_step(step_el: etree._Element) -> ControlInstruction:
    step_id = _attr(step_el, "ID") or _attr(step_el, "Name")
    name = _attr(step_el, "Name") or step_id
    sequences_to: list[str] = []
    for target in step_el.findall(".//Transition"):
        tid = _attr(target, "ID") or _attr(target, "Name")
        if tid:
            sequences_to.append(tid)
    next_step = _attr(step_el, "NextStep") or _attr(step_el, "TargetStep")
    if next_step:
        sequences_to.append(next_step)

    return ControlInstruction(
        id=f"sfc_step_{step_id}",
        instruction_type=name or "Step",
        operands=[],
        raw_text=etree.tostring(step_el, encoding="unicode", method="xml").strip(),
        language="sfc",
        metadata={
            "object_subtype": "sfc_step",
            "step_id": step_id,
            "step_name": name,
            "initial": _attr(step_el, "Initial").lower() in {"true", "1", "yes"},
            "sequences_to": sequences_to,
            "parse_status": "parsed",
            "parser": "intelli_l5x_sfc_v1",
        },
    )


def _parse_transition(trans_el: etree._Element) -> ControlInstruction:
    trans_id = _attr(trans_el, "ID") or _attr(trans_el, "Name")
    name = _attr(trans_el, "Name") or trans_id
    condition_el = trans_el.find(".//Condition")
    condition_text = _element_text(condition_el) if condition_el is not None else ""
    if not condition_text:
        condition_text = _attr(trans_el, "Condition")

    condition_for: list[str] = []
    target = _attr(trans_el, "TargetStep") or _attr(trans_el, "ToStep")
    if target:
        condition_for.append(target)
    for step in trans_el.findall(".//Step"):
        sid = _attr(step, "ID") or _attr(step, "Name")
        if sid:
            condition_for.append(sid)

    operands = [condition_text] if condition_text else []

    return ControlInstruction(
        id=f"sfc_trans_{trans_id}",
        instruction_type=name or "Transition",
        operands=operands,
        raw_text=etree.tostring(trans_el, encoding="unicode", method="xml").strip(),
        language="sfc",
        metadata={
            "object_subtype": "sfc_transition",
            "transition_id": trans_id,
            "condition_text": condition_text,
            "condition_for": condition_for,
            "parse_status": "parsed",
            "parser": "intelli_l5x_sfc_v1",
        },
    )


def _parse_action(action_el: etree._Element) -> ControlInstruction:
    action_id = _attr(action_el, "ID") or _attr(action_el, "Name")
    name = _attr(action_el, "Name") or action_id
    step_id = _attr(action_el, "StepID") or _attr(action_el, "Step")
    action_of = [step_id] if step_id else []
    for step in action_el.findall(".//Step"):
        sid = _attr(step, "ID") or _attr(step, "Name")
        if sid:
            action_of.append(sid)

    return ControlInstruction(
        id=f"sfc_action_{action_id}",
        instruction_type=name or "Action",
        operands=[],
        raw_text=etree.tostring(action_el, encoding="unicode", method="xml").strip(),
        language="sfc",
        metadata={
            "object_subtype": "sfc_action",
            "action_id": action_id,
            "qualifier": _attr(action_el, "Qualifier"),
            "action_of": action_of,
            "parse_status": "parsed",
            "parser": "intelli_l5x_sfc_v1",
        },
    )


def parse_l5x_sfc_routine(routine_element: etree._Element) -> list[ControlInstruction]:
    """Extract SFC steps, transitions, and actions from an L5X routine element."""

    instructions: list[ControlInstruction] = []
    content = routine_element.find(".//SFCContent")
    if content is None:
        content = routine_element

    for step in content.findall(".//Step"):
        instructions.append(_parse_step(step))
    for trans in content.findall(".//Transition"):
        instructions.append(_parse_transition(trans))
    for action in content.findall(".//Action"):
        instructions.append(_parse_action(action))

    return instructions


def extract_sfc_tags(instructions: Iterable[ControlInstruction]) -> set[str]:
    """Collect tag names referenced in transition conditions."""

    tags: set[str] = set()
    ident_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_\[\].:]*$")
    for inst in instructions:
        if (inst.metadata or {}).get("object_subtype") != "sfc_transition":
            continue
        for op in inst.operands:
            if op and ident_re.match(op.strip()):
                tags.add(op.strip())
    return tags


__all__ = ["parse_l5x_sfc_routine", "extract_sfc_tags"]
