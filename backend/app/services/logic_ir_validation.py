"""Validation helpers for INTELLI's deterministic logic IR.

These checks are intentionally structural. They do not infer controls
semantics that the parser did not already emit, and they do not inspect
or preserve raw L5X logic text.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.control_model import (
    FBDBlock,
    LadderBranch,
    LadderRung,
    LogicRead,
    LogicWrite,
)
from app.models.reasoning import (
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
)


class LogicIRValidationIssue(BaseModel):
    code: str
    message: str
    severity: str = "error"
    source_location: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def validate_ladder_rung_ir(
    rung: LadderRung,
    *,
    known_tag_names: set[str] | None = None,
) -> list[LogicIRValidationIssue]:
    """Validate one parsed ladder rung IR object."""

    issues: list[LogicIRValidationIssue] = []
    for write in rung.writes:
        _validate_write_location(write, issues)
    for ref in [*rung.reads, *rung.writes]:
        _validate_tag_resolution(ref, known_tag_names, issues)
    if rung.root_branch is not None:
        _validate_branch(rung.root_branch, issues)
    return issues


def validate_fbd_block_ir(
    block: FBDBlock,
    *,
    known_pin_ids: set[str] | None = None,
) -> list[LogicIRValidationIssue]:
    """Validate a parsed FBD block skeleton without inferring signal flow."""

    issues: list[LogicIRValidationIssue] = []
    if not block.source_location or not block.source_location.path:
        issues.append(
            LogicIRValidationIssue(
                code="fbd_block_missing_source_location",
                message="FBD block has no structural source location.",
                metadata={"block_id": block.id},
            )
        )
    pin_ids = known_pin_ids or {pin.id for pin in block.pins}
    for wire in block.wires:
        source = wire.get("source_pin_id")
        target = wire.get("target_pin_id")
        if wire.get("missing_source_endpoint") or (source and source not in pin_ids):
            issues.append(
                LogicIRValidationIssue(
                    code="fbd_wire_source_pin_missing",
                    message="FBD wire references a missing source pin.",
                    source_location=(
                        block.source_location.path
                        if block.source_location
                        else None
                    ),
                    metadata={"pin_id": source, "wire_id": wire.get("id")},
                )
            )
        if wire.get("missing_target_endpoint") or (target and target not in pin_ids):
            issues.append(
                LogicIRValidationIssue(
                    code="fbd_wire_target_pin_missing",
                    message="FBD wire references a missing target pin.",
                    source_location=(
                        block.source_location.path
                        if block.source_location
                        else None
                    ),
                    metadata={"pin_id": target, "wire_id": wire.get("id")},
                )
            )
    return issues


def validate_fbd_blocks_ir(blocks: list[FBDBlock]) -> list[LogicIRValidationIssue]:
    """Validate FBD routine-level block/pin/wire structure."""

    pin_ids = {pin.id for block in blocks for pin in block.pins}
    issues: list[LogicIRValidationIssue] = []
    seen_wires: set[str] = set()
    for block in blocks:
        for issue in validate_fbd_block_ir(block, known_pin_ids=pin_ids):
            wire_id = str(issue.metadata.get("wire_id") or "")
            key = f"{issue.code}:{wire_id}:{issue.metadata.get('pin_id')}"
            if wire_id and key in seen_wires:
                continue
            if wire_id:
                seen_wires.add(key)
            issues.append(issue)
    return issues


def validate_normalized_output(output: dict[str, Any]) -> list[LogicIRValidationIssue]:
    """Validate normalized graph invariants used by trace/reasoning."""

    objects: list[ControlObject] = list(output.get("control_objects") or [])
    relationships: list[Relationship] = list(output.get("relationships") or [])
    object_ids = {obj.id for obj in objects}
    issues: list[LogicIRValidationIssue] = []

    for rel in relationships:
        rel_type = _relationship_type_value(rel.relationship_type)
        if rel_type in {"writes", "latches", "unlatches", "resets"}:
            if not rel.source_location:
                issues.append(
                    LogicIRValidationIssue(
                        code="write_missing_source_location",
                        message="Write-like relationship has no source location.",
                        source_location=None,
                        metadata={
                            "source_id": rel.source_id,
                            "target_id": rel.target_id,
                            "relationship_type": rel_type,
                        },
                    )
                )
        if rel_type in {
            "reads",
            "writes",
            "references",
            "resets",
            "latches",
            "unlatches",
            "binds_parameter",
            "signal_connects",
            "connects",
        }:
            if rel.target_id not in object_ids:
                issues.append(
                    LogicIRValidationIssue(
                        code="relationship_target_missing",
                        message="Relationship target does not resolve to a normalized object.",
                        source_location=rel.source_location,
                        metadata={
                            "source_id": rel.source_id,
                            "target_id": rel.target_id,
                            "relationship_type": rel_type,
                        },
                    )
                )
            if rel_type in {"signal_connects", "connects"} and rel.source_id not in object_ids:
                issues.append(
                    LogicIRValidationIssue(
                        code="relationship_source_missing",
                        message="Relationship source does not resolve to a normalized object.",
                        source_location=rel.source_location,
                        metadata={
                            "source_id": rel.source_id,
                            "target_id": rel.target_id,
                            "relationship_type": rel_type,
                        },
                    )
                )

        if rel_type in {"reads", "writes"}:
            source_obj = next((obj for obj in objects if obj.id == rel.source_id), None)
            if (
                source_obj
                and source_obj.object_type == ControlObjectType.FUNCTION_BLOCK
                and not rel.source_location
            ):
                issues.append(
                    LogicIRValidationIssue(
                        code="fbd_deterministic_access_missing_source_location",
                        message="Deterministic FBD READ/WRITE has no source location.",
                        metadata={
                            "source_id": rel.source_id,
                            "target_id": rel.target_id,
                            "relationship_type": rel_type,
                        },
                    )
                )

    for obj in objects:
        if obj.object_type == ControlObjectType.FUNCTION_BLOCK:
            attrs = obj.attributes or {}
            if attrs.get("language") == "fbd" and not obj.source_location:
                issues.append(
                    LogicIRValidationIssue(
                        code="fbd_block_missing_source_location",
                        message="Normalized FBD block has no source location.",
                        metadata={"block_id": obj.id},
                    )
                )

    return issues


def _validate_write_location(
    write: LogicWrite,
    issues: list[LogicIRValidationIssue],
) -> None:
    if write.source_location and write.source_location.path:
        return
    issues.append(
        LogicIRValidationIssue(
            code="logic_write_missing_source_location",
            message="LogicWrite has no source location path.",
            source_location=None,
            metadata={"target": write.target.raw},
        )
    )


def _validate_tag_resolution(
    ref: LogicRead | LogicWrite,
    known_tag_names: set[str] | None,
    issues: list[LogicIRValidationIssue],
) -> None:
    tag_ref = ref.tag if isinstance(ref, LogicRead) else ref.target
    if not known_tag_names:
        return
    if tag_ref.tag_name in known_tag_names and not tag_ref.normalized_tag_id:
        issues.append(
            LogicIRValidationIssue(
                code="tag_reference_not_normalized",
                message="Tag reference matches known tags but has no normalized id.",
                severity="warning",
                source_location=(
                    ref.source_location.path if ref.source_location else None
                ),
                metadata={"tag": tag_ref.raw},
            )
        )


def _validate_branch(
    branch: LadderBranch,
    issues: list[LogicIRValidationIssue],
) -> None:
    if branch.branch_type == "parallel" and len(branch.children) < 2:
        issues.append(
            LogicIRValidationIssue(
                code="ladder_parallel_branch_too_few_arms",
                message="Parallel ladder branch has fewer than two arms.",
                source_location=(
                    branch.source_location.path
                    if branch.source_location
                    else None
                ),
                metadata={"branch_id": branch.id},
            )
        )
    if branch.branch_type == "parallel":
        for child in branch.children:
            if child.branch_type != "parallel_arm":
                issues.append(
                    LogicIRValidationIssue(
                        code="ladder_parallel_child_not_arm",
                        message="Parallel ladder branch child is not marked as an arm.",
                        source_location=(
                            child.source_location.path
                            if child.source_location
                            else None
                        ),
                        metadata={"branch_id": child.id},
                    )
                )
    for child in branch.children:
        _validate_branch(child, issues)


def _relationship_type_value(value: RelationshipType | str) -> str:
    return value.value if hasattr(value, "value") else str(value)


__all__ = [
    "LogicIRValidationIssue",
    "validate_fbd_blocks_ir",
    "validate_fbd_block_ir",
    "validate_ladder_rung_ir",
    "validate_normalized_output",
]
