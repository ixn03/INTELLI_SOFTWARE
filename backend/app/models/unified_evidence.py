"""Vendor-neutral unified signal evidence models.

Consumers reason about signals, conditions, writers, readers, and dependencies —
not parser-specific objects. Every evidence item preserves source provenance so
engineers can verify ladder rungs, FBD blocks/pins/wires, ST statements, and
AOI parameters without exposing parser implementation details.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

OriginatingLanguage = Literal[
    "ladder",
    "fbd",
    "structured_text",
    "sfc",
    "aoi",
    "unknown",
]

OriginatingPlatform = Literal[
    "rockwell",
    "siemens",
    "delta_v",
    "honeywell",
    "unknown",
]

EvidenceType = Literal[
    "ladder_write",
    "ladder_read",
    "ladder_condition",
    "fbd_input",
    "fbd_output",
    "fbd_connection",
    "st_write",
    "st_read",
    "st_condition",
    "aoi_parameter",
    "unknown_reference",
    "signal_dependency",
]

CausalityKind = Literal["deterministic", "direction_unknown", "structural_only"]

ConfidenceLevelLabel = Literal["very_low", "low", "medium", "high", "very_high"]


class SourceProvenance(BaseModel):
    """Engineer-verifiable source context for one evidence item."""

    originating_language: OriginatingLanguage = "unknown"
    originating_platform: OriginatingPlatform = "unknown"
    source_location: str | None = None
    routine: str | None = None
    rung_number: int | None = None
    block_id: str | None = None
    block_name: str | None = None
    block_type: str | None = None
    pin_name: str | None = None
    pin_direction: str | None = None
    statement_index: int | None = None
    instruction_type: str | None = None
    relationship_ids: list[str] = Field(default_factory=list)
    source_object_id: str | None = None
    causality: CausalityKind = "deterministic"
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceSource(BaseModel):
    """One piece of evidence with type, provenance, and confidence."""

    evidence_type: EvidenceType
    source_provenance: SourceProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    statement: str
    signal_id: str | None = None
    related_signal_ids: list[str] = Field(default_factory=list)
    deterministic: bool = True


class SignalUsage(BaseModel):
    signal_id: str
    signal_name: str | None = None
    usage_kind: Literal["read", "write", "reference", "condition"] = "read"
    source_provenance: SourceProvenance
    confidence: float = Field(ge=0.0, le=1.0)


class SignalWriter(BaseModel):
    writer_type: str
    signal_id: str
    signal_name: str | None = None
    source_provenance: SourceProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    causality: CausalityKind = "deterministic"
    condition_signal_ids: list[str] = Field(default_factory=list)
    condition_signal_names: list[str] = Field(default_factory=list)
    write_behavior: str | None = None


class SignalReader(BaseModel):
    reader_type: str
    signal_id: str
    signal_name: str | None = None
    source_provenance: SourceProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    reader_context: str | None = None


class RequiredCondition(BaseModel):
    signal_id: str
    signal_name: str | None = None
    required_for_writer_ids: list[str] = Field(default_factory=list)
    source_provenance: SourceProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    causality: CausalityKind = "deterministic"


class SignalDependency(BaseModel):
    upstream_signal_id: str
    upstream_signal_name: str | None = None
    downstream_signal_id: str
    downstream_signal_name: str | None = None
    dependency_kind: Literal[
        "required_condition",
        "enables",
        "wire_connection",
        "structural_reference",
    ] = "required_condition"
    source_provenance: SourceProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    causality: CausalityKind = "deterministic"


class UnknownDependency(BaseModel):
    signal_id: str
    signal_name: str | None = None
    reference_source_id: str
    reference_source_name: str | None = None
    source_provenance: SourceProvenance
    message: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.48)


class ConfidenceSummary(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_label: ConfidenceLevelLabel = "medium"
    evidence: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EvidenceSourceCounts(BaseModel):
    ladder: int = 0
    fbd: int = 0
    structured_text: int = 0
    sfc: int = 0
    aoi: int = 0
    unknown: int = 0


class LadderEvidenceGroup(BaseModel):
    routine: str | None = None
    rung_number: int | None = None
    instruction_type: str | None = None
    source_location: str | None = None
    role: Literal["writer", "reader", "condition"] = "writer"
    signal_name: str | None = None
    condition_signal_names: list[str] = Field(default_factory=list)
    relationship_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class FBDEvidenceGroup(BaseModel):
    routine: str | None = None
    block_name: str | None = None
    block_type: str | None = None
    pin_name: str | None = None
    pin_direction: str | None = None
    role: Literal["input", "output", "wire", "unknown"] = "input"
    signal_name: str | None = None
    connected_signal_name: str | None = None
    source_location: str | None = None
    relationship_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    structural_only: bool = False


class AOIEvidenceGroup(BaseModel):
    aoi_instance: str | None = None
    parameter_name: str | None = None
    parameter_direction: str | None = None
    signal_name: str | None = None
    source_location: str | None = None
    relationship_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class STEvidenceGroup(BaseModel):
    routine: str | None = None
    statement_index: int | None = None
    role: Literal["assignment", "read", "condition"] = "assignment"
    signal_name: str | None = None
    source_location: str | None = None
    relationship_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class UnknownEvidenceGroup(BaseModel):
    reference_source: str | None = None
    signal_name: str | None = None
    source_location: str | None = None
    message: str
    relationship_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class SFCEvidenceGroup(BaseModel):
    routine: str | None = None
    step_name: str | None = None
    transition_name: str | None = None
    role: Literal["step", "transition", "action", "condition"] = "step"
    signal_name: str | None = None
    source_location: str | None = None
    relationship_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    structural_only: bool = True


class VerificationByLanguage(BaseModel):
    ladder: list[LadderEvidenceGroup] = Field(default_factory=list)
    fbd: list[FBDEvidenceGroup] = Field(default_factory=list)
    aoi: list[AOIEvidenceGroup] = Field(default_factory=list)
    structured_text: list[STEvidenceGroup] = Field(default_factory=list)
    sfc: list[SFCEvidenceGroup] = Field(default_factory=list)
    unknown: list[UnknownEvidenceGroup] = Field(default_factory=list)


class UnifiedSignalSummary(BaseModel):
    answer: str
    confidence: ConfidenceLevelLabel


class UnifiedSignalEvidence(BaseModel):
    target_signal_id: str
    target_signal_name: str | None = None
    summary: UnifiedSignalSummary
    what_controls_this_signal: list[RequiredCondition] = Field(default_factory=list)
    who_writes_this_signal: list[SignalWriter] = Field(default_factory=list)
    where_is_it_used: list[SignalReader] = Field(default_factory=list)
    upstream_dependencies: list[SignalDependency] = Field(default_factory=list)
    downstream_impact: list[SignalReader] = Field(default_factory=list)
    unknowns: list[UnknownDependency] = Field(default_factory=list)
    evidence_sources: EvidenceSourceCounts = Field(default_factory=EvidenceSourceCounts)
    confidence_summary: ConfidenceSummary
    verification: VerificationByLanguage = Field(default_factory=VerificationByLanguage)
    advanced_details: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AOIEvidenceGroup",
    "ConfidenceSummary",
    "EvidenceSource",
    "EvidenceSourceCounts",
    "EvidenceType",
    "FBDEvidenceGroup",
    "LadderEvidenceGroup",
    "OriginatingLanguage",
    "OriginatingPlatform",
    "RequiredCondition",
    "SFCEvidenceGroup",
    "SignalDependency",
    "SignalReader",
    "SignalUsage",
    "SignalWriter",
    "SourceProvenance",
    "STEvidenceGroup",
    "UnifiedSignalEvidence",
    "UnifiedSignalSummary",
    "UnknownDependency",
    "UnknownEvidenceGroup",
    "VerificationByLanguage",
]
