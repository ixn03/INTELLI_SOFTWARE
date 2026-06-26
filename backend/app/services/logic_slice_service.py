"""Canonical program-slice layer for deterministic signal reasoning.

A ``LogicSlice`` is the unit of pre-LLM understanding: one rung, ST
statement, or FBD/AOI block with classified operands, writes, and a
reconstructed readable expression.  The troubleshooting workspace and
legacy evidence buckets are *projections* of slices — not independent
builders.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.reasoning import (
    ConfidenceLevel,
    ControlObject,
    ControlObjectType,
    Relationship,
    RelationshipType,
    WriteBehaviorType,
)

WRITER_TYPES: frozenset[RelationshipType] = frozenset(
    {
        RelationshipType.WRITES,
        RelationshipType.LATCHES,
        RelationshipType.UNLATCHES,
        RelationshipType.RESETS,
        RelationshipType.CALCULATES,
        RelationshipType.SCALES,
    }
)

BOOLEAN_CONDITION_INSTRUCTIONS: frozenset[str] = frozenset(
    {"XIC", "XIO", "ONS", "OSR", "OSF", "EQU", "NEQ", "GRT", "GEQ", "LES", "LEQ", "LIM", "CMP"}
)
DATA_SOURCE_INSTRUCTIONS: frozenset[str] = frozenset(
    {
        "SIZE",
        "MOV",
        "MOVE",
        "COP",
        "CPS",
        "FLL",
        "ADD",
        "SUB",
        "MUL",
        "DIV",
        "MOD",
        "CPT",
        "AND",
        "OR",
        "XOR",
        "TON",
        "TONR",
        "TOF",
        "RTO",
        "CTU",
        "CTD",
        "CTUD",
    }
)
DERIVED_CALCULATION_INSTRUCTIONS: frozenset[str] = frozenset(
    {"SIZE", "ADD", "SUB", "MUL", "DIV", "MOD", "CPT", "AND", "OR", "XOR"}
)

OperandRole = Literal[
    "boolean_gate",
    "comparison_gate",
    "calc_source",
    "calc_dest",
    "accumulator",
    "index_operand",
    "unknown",
]
SliceKind = Literal["boolean_control", "calculation", "direct_write"]


class LogicSliceOperand(BaseModel):
    signal_id: str
    signal_name: str | None = None
    instruction_type: str | None = None
    role: OperandRole = "unknown"
    relationship_id: str | None = None
    operand_index: int | None = None
    required_value: bool | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicSliceWrite(BaseModel):
    signal_id: str
    signal_name: str | None = None
    instruction_type: str | None = None
    write_behavior: str | None = None
    relationship_id: str
    operand_index: int | None = None


class LogicSlice(BaseModel):
    """One executable logic unit (rung / ST statement / FBD block)."""

    id: str
    source_id: str
    source_name: str | None = None
    source_type: str | None = None
    language: str = "unknown"
    source_location: str | None = None
    routine: str | None = None
    rung_number: int | None = None
    statement_index: int | None = None
    block_name: str | None = None
    instruction_sequence: list[str] = Field(default_factory=list)
    slice_kind: SliceKind = "direct_write"
    gate_operands: list[LogicSliceOperand] = Field(default_factory=list)
    data_operands: list[LogicSliceOperand] = Field(default_factory=list)
    writes: list[LogicSliceWrite] = Field(default_factory=list)
    affected_signal_ids: list[str] = Field(default_factory=list)
    readable_expression: str | None = None
    readable_summary: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicPathSignalRef(BaseModel):
    signal_id: str
    signal_name: str | None = None
    instruction_type: str | None = None
    relationship_id: str | None = None
    role: OperandRole | None = None


class LogicPathWrite(BaseModel):
    signal_id: str
    signal_name: str | None = None
    instruction_type: str | None = None
    write_behavior: str | None = None
    relationship_id: str | None = None


class LogicPath(BaseModel):
    """UI projection of a LogicSlice for one target signal."""

    id: str
    source_location: str | None = None
    routine: str | None = None
    rung_number: int | None = None
    statement_index: int | None = None
    block_name: str | None = None
    language: str = "unknown"
    instruction_sequence: list[str] = Field(default_factory=list)
    readable_expression: str | None = None
    input_signals: list[LogicPathSignalRef] = Field(default_factory=list)
    output_signals: list[LogicPathSignalRef] = Field(default_factory=list)
    write_operations: list[LogicPathWrite] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def build_program_slices(
    control_objects: list[ControlObject],
    relationships: list[Relationship],
) -> list[LogicSlice]:
    """Build canonical slices for every source location that writes a tag."""

    object_index = {obj.id: obj for obj in control_objects}
    by_source: dict[str, list[Relationship]] = {}
    for rel in relationships:
        by_source.setdefault(rel.source_id, []).append(rel)

    writer_rels = [
        rel for rel in relationships if rel.relationship_type in WRITER_TYPES
    ]
    writers_by_source: dict[str, list[Relationship]] = {}
    for writer in writer_rels:
        writers_by_source.setdefault(writer.source_id, []).append(writer)

    slices: list[LogicSlice] = []
    for source_id, source_writers in writers_by_source.items():
        source = object_index.get(source_id)
        reads = [
            rel
            for rel in by_source.get(source_id, [])
            if rel.relationship_type == RelationshipType.READS
        ]
        slice_obj = _build_slice(
            source_id=source_id,
            source=source,
            writers=source_writers,
            reads=reads,
            object_index=object_index,
        )
        slices.append(slice_obj)
    return slices


def slices_for_target(
    slices: list[LogicSlice],
    target_id: str,
) -> list[LogicSlice]:
    return [s for s in slices if target_id in s.affected_signal_ids]


def logic_paths_from_slices(
    slices: list[LogicSlice],
    target_id: str,
) -> list[LogicPath]:
    return [_slice_to_logic_path(s, target_id) for s in slices_for_target(slices, target_id)]


def summary_for_target_slices(
    *,
    target_name: str,
    slices: list[LogicSlice],
    reader_count: int = 0,
    unknown_count: int = 0,
) -> str:
    path_count = len(slices)
    calc_count = sum(1 for s in slices if s.slice_kind == "calculation")
    bool_count = path_count - calc_count
    if calc_count and not bool_count:
        text = (
            f"{target_name} is written by {path_count} calculation logic path"
            f"{'' if path_count == 1 else 's'}."
        )
    elif bool_count and not calc_count:
        text = (
            f"{target_name} is written by {path_count} logic path"
            f"{'' if path_count == 1 else 's'}."
        )
    elif path_count:
        text = (
            f"{target_name} is written by {path_count} logic path"
            f"{'' if path_count == 1 else 's'} "
            f"({calc_count} calculation, {bool_count} control)."
        )
    else:
        text = f"{target_name} has no deterministic write paths in the normalized model."
    if reader_count:
        text += (
            f" It is used downstream in {reader_count} location"
            f"{'' if reader_count == 1 else 's'}."
        )
    if unknown_count:
        text += (
            f" {unknown_count} unknown-direction reference"
            f"{'s' if unknown_count != 1 else ''} were preserved separately."
        )
    return text


# ---------------------------------------------------------------------------
# Slice construction
# ---------------------------------------------------------------------------


def _build_slice(
    *,
    source_id: str,
    source: ControlObject | None,
    writers: list[Relationship],
    reads: list[Relationship],
    object_index: dict[str, ControlObject],
) -> LogicSlice:
    write_targets = {w.target_id for w in writers}
    gate_operands: list[LogicSliceOperand] = []
    data_operands: list[LogicSliceOperand] = []
    slice_writes: list[LogicSliceWrite] = []

    for writer in sorted(
        writers,
        key=lambda w: (
            (w.platform_specific or {}).get("operand_index")
            if (w.platform_specific or {}).get("operand_index") is not None
            else 999,
            w.id,
        ),
    ):
        slice_writes.append(
            LogicSliceWrite(
                signal_id=writer.target_id,
                signal_name=_name_for(writer.target_id, object_index),
                instruction_type=str(
                    (writer.platform_specific or {}).get("instruction_type") or ""
                )
                or None,
                write_behavior=_write_behavior(writer.write_behavior),
                relationship_id=writer.id,
                operand_index=(writer.platform_specific or {}).get("operand_index"),
            )
        )

    for read in reads:
        if read.target_id in write_targets:
            role: OperandRole = "accumulator"
        else:
            role = _classify_read_role(read)
        operand = _read_to_operand(read, role, object_index)
        if role in {"boolean_gate", "comparison_gate"}:
            gate_operands.append(operand)
        elif role in {"calc_source", "accumulator", "index_operand"}:
            data_operands.append(operand)
        elif role == "unknown" and read.target_id not in write_targets:
            if _classify_read_role(read) == "boolean_gate":
                gate_operands.append(operand)
            else:
                data_operands.append(operand)

    gate_operands = _dedupe_operands(gate_operands)
    data_operands = _dedupe_operands(data_operands)

    sample_writer = writers[0]
    location = (
        sample_writer.source_location or (source.source_location if source else None)
    )
    language = _language_for_source(source, sample_writer)
    instructions = _ordered_instruction_sequence(writers)
    slice_kind = _slice_kind(gate_operands, data_operands, instructions)

    readable_expression, warnings = _build_readable_expression(
        slice_kind=slice_kind,
        language=language,
        gate_operands=gate_operands,
        data_operands=data_operands,
        writes=slice_writes,
        writers=writers,
        source=source,
        location=location,
    )
    readable_summary = _build_readable_summary(
        slice_kind=slice_kind,
        writes=slice_writes,
        gate_operands=gate_operands,
        data_operands=data_operands,
        readable_expression=readable_expression,
    )

    scores = [_relationship_score(w) for w in writers]
    scores.extend(_relationship_score(r) for r in reads)
    confidence = round(min(scores), 3) if scores else 0.0

    return LogicSlice(
        id=f"slice::{source_id}",
        source_id=source_id,
        source_name=source.name if source else None,
        source_type=source.object_type.value if source else None,
        language=language,
        source_location=location,
        routine=_extract_location_part(location or "", "Routine"),
        rung_number=_extract_rung(location or ""),
        statement_index=_extract_statement_index(location or ""),
        block_name=_extract_location_part(location or "", "Block")
        or (source.name if source else None),
        instruction_sequence=instructions,
        slice_kind=slice_kind,
        gate_operands=gate_operands,
        data_operands=data_operands,
        writes=slice_writes,
        affected_signal_ids=sorted(write_targets),
        readable_expression=readable_expression,
        readable_summary=readable_summary,
        confidence=confidence,
        warnings=warnings,
        metadata={
            "writer_relationship_ids": sorted({w.id for w in writers}),
        },
    )


def _slice_to_logic_path(slice_obj: LogicSlice, target_id: str) -> LogicPath:
    target_writes = [w for w in slice_obj.writes if w.signal_id == target_id]
    external_inputs = [
        o
        for o in [*slice_obj.gate_operands, *slice_obj.data_operands]
        if o.role != "accumulator" and o.signal_id != target_id
    ]
    input_signals = [
        LogicPathSignalRef(
            signal_id=o.signal_id,
            signal_name=o.signal_name,
            instruction_type=o.instruction_type,
            relationship_id=o.relationship_id,
            role=o.role,
        )
        for o in _dedupe_operands(external_inputs)
    ]
    write_ops = [
        LogicPathWrite(
            signal_id=w.signal_id,
            signal_name=w.signal_name,
            instruction_type=w.instruction_type,
            write_behavior=w.write_behavior,
            relationship_id=w.relationship_id,
        )
        for w in target_writes
    ]
    output_signals = [
        LogicPathSignalRef(
            signal_id=w.signal_id,
            signal_name=w.signal_name,
            instruction_type=w.instruction_type,
            relationship_id=w.relationship_id,
        )
        for w in target_writes
    ]
    return LogicPath(
        id=f"path::{slice_obj.source_id}::{target_id}",
        source_location=slice_obj.source_location,
        routine=slice_obj.routine,
        rung_number=slice_obj.rung_number,
        statement_index=slice_obj.statement_index,
        block_name=slice_obj.block_name,
        language=slice_obj.language,
        instruction_sequence=slice_obj.instruction_sequence,
        readable_expression=slice_obj.readable_expression,
        input_signals=input_signals,
        output_signals=output_signals,
        write_operations=write_ops,
        confidence=slice_obj.confidence,
        warnings=list(slice_obj.warnings),
        metadata={
            "path_kind": slice_obj.slice_kind,
            "source_id": slice_obj.source_id,
            "slice_id": slice_obj.id,
            "writer_relationship_ids": slice_obj.metadata.get("writer_relationship_ids", []),
            "readable_summary": slice_obj.readable_summary,
        },
    )


# ---------------------------------------------------------------------------
# Expression builders
# ---------------------------------------------------------------------------


def _build_readable_expression(
    *,
    slice_kind: SliceKind,
    language: str,
    gate_operands: list[LogicSliceOperand],
    data_operands: list[LogicSliceOperand],
    writes: list[LogicSliceWrite],
    writers: list[Relationship],
    source: ControlObject | None,
    location: str | None,
) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    if language == "structured_text":
        for writer in writers:
            meta = writer.platform_specific or {}
            statement_text = meta.get("statement_text") or meta.get("raw_text")
            if statement_text:
                return str(statement_text).strip(), warnings
            if writer.logic_condition:
                return writer.logic_condition.strip(), warnings
        warnings.append("ST statement text unavailable; reconstructed from operands.")
    elif language in {"fbd", "aoi"}:
        expr, fbd_warnings = _build_fbd_expression(
            source, location, writes, gate_operands
        )
        warnings.extend(fbd_warnings)
        if expr:
            return expr, warnings

    if slice_kind == "calculation":
        return _build_calculation_expression(data_operands, writes), warnings
    return _build_boolean_expression(gate_operands, writes), warnings


def _build_boolean_expression(
    gate_operands: list[LogicSliceOperand],
    writes: list[LogicSliceWrite],
) -> str | None:
    gate_parts = [_operand_to_readable(o) for o in gate_operands]
    gate = " AND ".join(gate_parts) if gate_parts else ""
    write_parts = [_write_clause(w) for w in writes]
    write_text = ", ".join(write_parts) if write_parts else ""
    if gate and write_text:
        return f"IF {gate} THEN {write_text}"
    if write_text:
        return write_text
    return gate or None


def _build_calculation_expression(
    data_operands: list[LogicSliceOperand],
    writes: list[LogicSliceWrite],
) -> str | None:
    if not writes:
        return None
    target = writes[-1].signal_name or writes[-1].signal_id
    instructions = [(w.instruction_type or "").upper() for w in writes]
    external = [
        o.signal_name or o.signal_id
        for o in data_operands
        if o.role != "accumulator" and o.signal_id != writes[-1].signal_id
    ]
    accumulators = [
        o.signal_name or o.signal_id
        for o in data_operands
        if o.role == "accumulator"
    ]
    upper = instructions
    if "SIZE" in upper and "SUB" in upper and external:
        return f"{target} = SIZE({external[0]}) - 1"
    if "SIZE" in upper and external:
        return f"{target} = SIZE({external[0]})"
    if "MOV" in upper or "MOVE" in upper:
        return f"{target} = {external[0]}" if external else f"{target} = MOV(...)"
    if "ADD" in upper:
        if accumulators and external:
            return f"{target} += {' + '.join(external)}"
        if external:
            return f"{target} = {external[0]} + ..."
    if "SUB" in upper and external:
        return f"{target} = {external[0]} - ..."
    joined = ", ".join(upper) if upper else "CALC"
    sources = ", ".join(external) if external else "..."
    return f"{target} = {joined}({sources})"


def _build_fbd_expression(
    source: ControlObject | None,
    location: str | None,
    writes: list[LogicSliceWrite],
    gate_operands: list[LogicSliceOperand],
) -> tuple[str | None, list[str]]:
    warnings: list[str] = []
    block_name = _extract_location_part(location or "", "Block") or (
        source.name if source else None
    )
    pin = _extract_location_part(location or "", "Pin")
    write_text = ", ".join(_write_clause(w) for w in writes)
    if gate_operands:
        gate = " AND ".join(_operand_to_readable(o) for o in gate_operands)
        expression = f"IF {gate} THEN {write_text}" if write_text else gate
    else:
        expression = write_text or None
    if block_name:
        prefix = f"{block_name}.{pin}" if pin else block_name
        expression = f"{prefix}: {expression}" if expression else prefix
    if not pin and writes:
        warnings.append("FBD/AOI pin direction inferred from write relationship only.")
    return expression, warnings


def _build_readable_summary(
    *,
    slice_kind: SliceKind,
    writes: list[LogicSliceWrite],
    gate_operands: list[LogicSliceOperand],
    data_operands: list[LogicSliceOperand],
    readable_expression: str | None,
) -> str | None:
    if not writes:
        return None
    targets = ", ".join({w.signal_name or w.signal_id for w in writes})
    if slice_kind == "calculation":
        external = [
            o.signal_name or o.signal_id
            for o in data_operands
            if o.role != "accumulator"
        ]
        if external and readable_expression:
            return readable_expression
        if external:
            return f"Calculates {targets} from {', '.join(external)}."
    if gate_operands:
        gates = ", ".join(o.signal_name or o.signal_id for o in gate_operands)
        return f"When {gates} gate the path, writes {targets}."
    return f"Writes {targets}."


def _operand_to_readable(operand: LogicSliceOperand) -> str:
    name = operand.signal_name or operand.signal_id
    itype = (operand.instruction_type or "XIC").upper()
    if itype == "XIO" or operand.required_value is False:
        return f"NOT {name}"
    if itype == "XIC" or operand.required_value is True:
        return name
    if itype in BOOLEAN_CONDITION_INSTRUCTIONS:
        return f"{itype}({name})"
    return f"{itype}({name})"


def _write_clause(write: LogicSliceWrite) -> str:
    itype = (write.instruction_type or "").upper()
    target = write.signal_name or write.signal_id
    behavior = (write.write_behavior or "").lower()
    if itype == "OTE" or behavior == "sets_true":
        return f"{target} = TRUE"
    if itype == "OTL" or behavior == "latches":
        return f"LATCH {target}"
    if itype == "OTU" or behavior == "unlatches":
        return f"UNLATCH {target}"
    if itype in DERIVED_CALCULATION_INSTRUCTIONS:
        return f"{target} := ..."
    return f"{itype}({target})"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_read_role(rel: Relationship) -> OperandRole:
    meta = rel.platform_specific or {}
    existing = str(meta.get("operand_semantic_role") or "").strip()
    if existing == "boolean_condition_read":
        return "boolean_gate"
    if existing == "data_source_read":
        return "calc_source"
    operand_role = str(meta.get("operand_role") or "").lower()
    instruction = str(meta.get("instruction_type") or "").upper()
    if operand_role in {"index", "dimension", "constant", "indirect_reference"}:
        return "index_operand"
    if operand_role in {"condition", "contact", "comparison_operand", "gating_operand"}:
        return "boolean_gate"
    if instruction in BOOLEAN_CONDITION_INSTRUCTIONS:
        if instruction in {"EQU", "NEQ", "GRT", "GEQ", "LES", "LEQ", "LIM", "CMP"}:
            return "comparison_gate"
        return "boolean_gate"
    if instruction in DATA_SOURCE_INSTRUCTIONS:
        return "calc_source"
    return "unknown"


def _read_to_operand(
    rel: Relationship,
    role: OperandRole,
    object_index: dict[str, ControlObject],
) -> LogicSliceOperand:
    meta = rel.platform_specific or {}
    target = object_index.get(rel.target_id)
    instruction = str(meta.get("instruction_type") or "") or None
    return LogicSliceOperand(
        signal_id=rel.target_id,
        signal_name=target.name if target else None,
        instruction_type=instruction,
        role=role,
        relationship_id=rel.id,
        operand_index=meta.get("operand_index"),
        required_value=_required_value_for_instruction(instruction),
        metadata=dict(meta),
    )


def _required_value_for_instruction(instruction_type: str | None) -> bool | None:
    upper = (instruction_type or "").upper()
    if upper == "XIC":
        return True
    if upper == "XIO":
        return False
    return None


def _dedupe_operands(operands: list[LogicSliceOperand]) -> list[LogicSliceOperand]:
    seen: set[tuple[str, str | None, str]] = set()
    out: list[LogicSliceOperand] = []
    for op in operands:
        key = (op.signal_id, op.relationship_id, op.role)
        if key in seen:
            continue
        seen.add(key)
        out.append(op)
    return out


def _slice_kind(
    gate_operands: list[LogicSliceOperand],
    data_operands: list[LogicSliceOperand],
    instructions: list[str],
) -> SliceKind:
    has_calc = any(inst in DERIVED_CALCULATION_INSTRUCTIONS for inst in instructions)
    if has_calc or (data_operands and not gate_operands):
        return "calculation"
    if gate_operands:
        return "boolean_control"
    return "direct_write"


def _ordered_instruction_sequence(writers: list[Relationship]) -> list[str]:
    ordered: list[tuple[int, str]] = []
    for writer in writers:
        meta = writer.platform_specific or {}
        instruction = str(meta.get("instruction_type") or "").upper()
        if not instruction:
            continue
        operand_index = meta.get("operand_index")
        sort_key = int(operand_index) if operand_index is not None else len(ordered)
        ordered.append((sort_key, instruction))
    seen: set[str] = set()
    sequence: list[str] = []
    for _, instruction in sorted(ordered, key=lambda row: row[0]):
        if instruction in seen:
            continue
        seen.add(instruction)
        sequence.append(instruction)
    return sequence


def _language_for_source(
    source: ControlObject | None,
    writer: Relationship,
) -> str:
    meta = writer.platform_specific or {}
    if str(meta.get("language") or "") == "structured_text":
        return "structured_text"
    location = (writer.source_location or (source.source_location if source else "") or "").lower()
    source_type = (source.object_type.value if source else "").lower()
    if source_type == ControlObjectType.RUNG.value or "rung[" in location:
        return "ladder"
    if "statement" in location or "structured" in source_type:
        return "structured_text"
    if "sfc" in source_type or "/sfc" in location:
        return "sfc"
    if "aoi" in source_type or "aoi" in location:
        return "aoi"
    if source_type in {
        ControlObjectType.FUNCTION_BLOCK.value,
        ControlObjectType.FBD_BLOCK_INSTANCE.value,
    } or "block:" in location:
        return "fbd"
    return "unknown"


def _name_for(tag_id: str, object_index: dict[str, ControlObject]) -> str | None:
    obj = object_index.get(tag_id)
    return obj.name if obj else tag_id.split("/")[-1] if "/" in tag_id else tag_id


def _write_behavior(behavior: WriteBehaviorType | None) -> str | None:
    return behavior.value if behavior is not None else None


def _relationship_score(rel: Relationship) -> float:
    if rel.confidence == ConfidenceLevel.HIGH:
        return 0.92
    if rel.confidence == ConfidenceLevel.MEDIUM:
        return 0.72
    if rel.confidence == ConfidenceLevel.LOW:
        return 0.48
    return 0.6


def _extract_location_part(source_location: str, key: str) -> str | None:
    match = re.search(rf"{re.escape(key)}:([^/]+)", source_location)
    return match.group(1) if match else None


def _extract_rung(source_location: str) -> int | None:
    match = re.search(r"Rung\[(\d+)\]", source_location)
    return int(match.group(1)) if match else None


def _extract_statement_index(source_location: str) -> int | None:
    match = re.search(r"Statement[:[](\d+)", source_location)
    return int(match.group(1)) if match else None
