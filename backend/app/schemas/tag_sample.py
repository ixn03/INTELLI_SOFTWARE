"""Pydantic schemas for live tag sample ingestion."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.db.models.tag_registry import SourceSystem


class SampleQuality(str, Enum):
    GOOD = "good"
    BAD = "bad"
    UNCERTAIN = "uncertain"
    STALE = "stale"


TagSampleValue = bool | int | float | str


class TagSampleCreate(BaseModel):
    tag_id: uuid.UUID
    raw_tag_name: str = Field(..., min_length=1, max_length=512)
    value: TagSampleValue
    timestamp: datetime
    quality: SampleQuality = SampleQuality.GOOD
    source: SourceSystem
    source_address: str | None = None
    unit: str | None = None


class TagSampleBatchCreate(BaseModel):
    samples: list[TagSampleCreate] = Field(..., min_length=1, max_length=500)


class TagSampleRejection(BaseModel):
    tag_id: uuid.UUID | None = None
    raw_tag_name: str | None = None
    reason: str


class TagSampleIngestResult(BaseModel):
    accepted_count: int
    rejected_count: int
    accepted_ids: list[uuid.UUID] = Field(default_factory=list)
    rejections: list[TagSampleRejection] = Field(default_factory=list)


class TagSampleRead(BaseModel):
    tag_id: uuid.UUID
    raw_tag_name: str
    canonical_name: str | None = None
    value: TagSampleValue
    timestamp: datetime
    quality: SampleQuality
    source: SourceSystem
    source_address: str | None = None
    unit: str | None = None
    plant_id: uuid.UUID | None = None
    equipment_id: uuid.UUID | None = None


class TagLatestResponse(BaseModel):
    tag: dict[str, Any]
    sample: TagSampleRead | None = None


class TagHistoryResponse(BaseModel):
    tag: dict[str, Any]
    samples: list[TagSampleRead]
    total: int
