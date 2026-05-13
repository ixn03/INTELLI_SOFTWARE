"""Engineer-verified knowledge items (supplemental to parsed logic).

Knowledge never replaces deterministic normalization; it ranks alongside
or above unverified notes in orchestration layers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.reasoning import ConfidenceLevel


class KnowledgeType(str, Enum):
    TAG_DESCRIPTION = "tag_description"
    STATE_DESCRIPTION = "state_description"
    EQUIPMENT_DESCRIPTION = "equipment_description"
    CONTROL_NARRATIVE_NOTE = "control_narrative_note"
    CONTROL_NARRATIVE_SECTION = "control_narrative_section"
    NUISANCE_ALARM = "nuisance_alarm"
    INSTRUMENTATION_CAVEAT = "instrumentation_caveat"
    OPERATOR_GUIDANCE = "operator_guidance"
    TROUBLESHOOTING_WORKFLOW = "troubleshooting_workflow"
    SEQUENCE_EXPLANATION = "sequence_explanation"
    STARTUP_SHUTDOWN_NOTE = "startup_shutdown_note"
    VERSION_SPECIFIC_BEHAVIOR = "version_specific_behavior"
    KNOWN_FALSE_POSITIVE = "known_false_positive"
    TROUBLESHOOTING_NOTE = "troubleshooting_note"
    VERIFIED_FIX = "verified_fix"
    REJECTED_FIX = "rejected_fix"
    ASSUMPTION = "assumption"
    ENGINEER_FEEDBACK = "engineer_feedback"


class KnowledgeStatus(str, Enum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class KnowledgeVerification(BaseModel):
    verified_by: Optional[str] = None
    rejected_by: Optional[str] = None
    superseded_by: Optional[str] = None
    verification_reason: Optional[str] = None
    plant_scope: Optional[str] = None
    equipment_scope: Optional[str] = None


class KnowledgeItem(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    target_object_id: Optional[str] = None
    target_name: Optional[str] = None
    knowledge_type: KnowledgeType
    statement: str
    source: str = "engineer"
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    verified_by: Optional[str] = None
    rejected_by: Optional[str] = None
    superseded_by: Optional[str] = None
    verification: KnowledgeVerification = Field(default_factory=KnowledgeVerification)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    status: KnowledgeStatus = KnowledgeStatus.PROPOSED
    evidence_links: list[str] = Field(default_factory=list)
    version_range: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "KnowledgeType",
    "KnowledgeStatus",
    "KnowledgeVerification",
    "KnowledgeItem",
]
