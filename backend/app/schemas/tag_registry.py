"""Pydantic schemas for the INTELLI tag registry API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.tag_registry import SourceSystem, TagDataType, TagRole


class TagCreate(BaseModel):
    plant_id: uuid.UUID
    area_id: uuid.UUID
    equipment_id: uuid.UUID | None = None
    data_source_id: uuid.UUID
    raw_name: str = Field(..., min_length=1, max_length=512)
    canonical_name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    data_type: TagDataType = TagDataType.UNKNOWN
    tag_role: TagRole = TagRole.UNKNOWN
    unit: str | None = None
    source_system: SourceSystem
    source_address: str | None = None
    node_id: str | None = None
    scan_rate_ms: int | None = Field(default=None, ge=0)
    is_enabled: bool = True


class TagUpdate(BaseModel):
    area_id: uuid.UUID | None = None
    equipment_id: uuid.UUID | None = None
    data_source_id: uuid.UUID | None = None
    raw_name: str | None = Field(default=None, min_length=1, max_length=512)
    canonical_name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    data_type: TagDataType | None = None
    tag_role: TagRole | None = None
    unit: str | None = None
    source_system: SourceSystem | None = None
    source_address: str | None = None
    node_id: str | None = None
    scan_rate_ms: int | None = Field(default=None, ge=0)
    is_enabled: bool | None = None


class TagRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plant_id: uuid.UUID
    area_id: uuid.UUID
    equipment_id: uuid.UUID | None
    data_source_id: uuid.UUID
    raw_name: str
    canonical_name: str
    description: str | None
    data_type: TagDataType
    tag_role: TagRole
    unit: str | None
    source_system: SourceSystem
    source_address: str | None
    node_id: str | None
    scan_rate_ms: int | None
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class TagListResponse(BaseModel):
    items: list[TagRead]
    total: int


class DataSourceCreate(BaseModel):
    plant_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=255)
    source_system: SourceSystem
    endpoint_url: str | None = None
    auth_type: str | None = None
    is_enabled: bool = True


class DataSourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plant_id: uuid.UUID
    name: str
    source_system: SourceSystem
    endpoint_url: str | None
    auth_type: str | None
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class DataSourceListResponse(BaseModel):
    items: list[DataSourceRead]
    total: int


class ConnectorConfigCreate(BaseModel):
    data_source_id: uuid.UUID
    config_json: dict[str, Any] = Field(default_factory=dict)
    polling_interval_ms: int | None = Field(default=None, ge=0)
    subscription_enabled: bool = False
