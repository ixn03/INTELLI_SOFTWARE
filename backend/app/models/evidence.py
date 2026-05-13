from __future__ import annotations

import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field


def _new_id() -> str:
    return str(uuid.uuid4())


class EvidenceItem(BaseModel):
    id: str = Field(default_factory=_new_id)
    evidence_type: str
    source_platform: Optional[str] = None
    source_location: Optional[str] = None
    target_object_id: Optional[str] = None
    statement: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    deterministic: bool = True
    related_relationship_ids: list[str] = Field(default_factory=list)
    runtime_snapshot_keys: list[str] = Field(default_factory=list)
    unsupported: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    conclusion: str
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    conflicting_evidence: list[EvidenceItem] = Field(default_factory=list)
    unsupported_evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


__all__ = ["EvidenceItem", "EvidenceBundle"]
