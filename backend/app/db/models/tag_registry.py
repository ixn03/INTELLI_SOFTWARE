"""Tag registry ORM models — plants, equipment, data sources, and INTELLI tags."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator, Uuid

from app.db.session import Base


class GUID(TypeDecorator):
    """Platform-neutral UUID stored as CHAR(36) on SQLite and native UUID on Postgres."""

    impl = Uuid
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))


class JSONType(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB)
        return dialect.type_descriptor(JSON)


class TagDataType(str, enum.Enum):
    BOOL = "BOOL"
    INT = "INT"
    DINT = "DINT"
    REAL = "REAL"
    STRING = "STRING"
    UNKNOWN = "UNKNOWN"


class TagRole(str, enum.Enum):
    COMMAND = "COMMAND"
    FEEDBACK = "FEEDBACK"
    PERMISSIVE = "PERMISSIVE"
    INTERLOCK = "INTERLOCK"
    ALARM = "ALARM"
    ANALOG = "ANALOG"
    STATUS = "STATUS"
    SETPOINT = "SETPOINT"
    UNKNOWN = "UNKNOWN"


class SourceSystem(str, enum.Enum):
    OPC_UA = "OPC_UA"
    PI = "PI"
    DELTAV = "DELTAV"
    SEEQ = "SEEQ"
    CSV = "CSV"
    SIMULATOR = "SIMULATOR"


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(GUID(), primary_key=True, default=uuid.uuid4)


class Plant(Base):
    __tablename__ = "plants"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    areas: Mapped[list[Area]] = relationship(back_populates="plant")
    equipment: Mapped[list[Equipment]] = relationship(back_populates="plant")
    data_sources: Mapped[list[DataSource]] = relationship(back_populates="plant")
    tags: Mapped[list[Tag]] = relationship(back_populates="plant")


class Area(Base):
    __tablename__ = "areas"
    __table_args__ = (UniqueConstraint("plant_id", "name", name="uq_areas_plant_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    plant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("plants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    plant: Mapped[Plant] = relationship(back_populates="areas")
    equipment: Mapped[list[Equipment]] = relationship(back_populates="area")
    tags: Mapped[list[Tag]] = relationship(back_populates="area")


class Equipment(Base):
    __tablename__ = "equipment"
    __table_args__ = (UniqueConstraint("plant_id", "name", name="uq_equipment_plant_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    plant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("plants.id"), nullable=False)
    area_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("areas.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    equipment_type: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    plant: Mapped[Plant] = relationship(back_populates="equipment")
    area: Mapped[Area] = relationship(back_populates="equipment")
    tags: Mapped[list[Tag]] = relationship(back_populates="equipment")


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("plant_id", "name", name="uq_data_sources_plant_name"),
        Index("ix_data_sources_source_system", "source_system"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    plant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("plants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[SourceSystem] = mapped_column(
        Enum(SourceSystem, name="source_system_enum", native_enum=False), nullable=False
    )
    endpoint_url: Mapped[str | None] = mapped_column(String(1024))
    auth_type: Mapped[str | None] = mapped_column(String(64))
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    plant: Mapped[Plant] = relationship(back_populates="data_sources")
    connector_configs: Mapped[list[ConnectorConfig]] = relationship(back_populates="data_source")
    tags: Mapped[list[Tag]] = relationship(back_populates="data_source")


class ConnectorConfig(Base):
    __tablename__ = "connector_configs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("data_sources.id"), nullable=False
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    polling_interval_ms: Mapped[int | None] = mapped_column(Integer)
    subscription_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    data_source: Mapped[DataSource] = relationship(back_populates="connector_configs")


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("plant_id", "canonical_name", name="uq_tags_plant_canonical_name"),
        Index("ix_tags_raw_name", "raw_name"),
        Index("ix_tags_equipment_id", "equipment_id"),
        Index("ix_tags_data_source_id", "data_source_id"),
        Index("ix_tags_source_system", "source_system"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    plant_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("plants.id"), nullable=False)
    area_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("areas.id"), nullable=False)
    equipment_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("equipment.id"), nullable=True
    )
    data_source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("data_sources.id"), nullable=False
    )
    raw_name: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    data_type: Mapped[TagDataType] = mapped_column(
        Enum(TagDataType, name="tag_data_type_enum", native_enum=False),
        nullable=False,
        default=TagDataType.UNKNOWN,
    )
    tag_role: Mapped[TagRole] = mapped_column(
        Enum(TagRole, name="tag_role_enum", native_enum=False),
        nullable=False,
        default=TagRole.UNKNOWN,
    )
    unit: Mapped[str | None] = mapped_column(String(64))
    source_system: Mapped[SourceSystem] = mapped_column(
        Enum(SourceSystem, name="tag_source_system_enum", native_enum=False), nullable=False
    )
    source_address: Mapped[str | None] = mapped_column(String(1024))
    node_id: Mapped[str | None] = mapped_column(String(512))
    scan_rate_ms: Mapped[int | None] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    plant: Mapped[Plant] = relationship(back_populates="tags")
    area: Mapped[Area] = relationship(back_populates="tags")
    equipment: Mapped[Equipment | None] = relationship(back_populates="tags")
    data_source: Mapped[DataSource] = relationship(back_populates="tags")
    aliases: Mapped[list[TagAlias]] = relationship(back_populates="tag", cascade="all, delete-orphan")
    logic_refs: Mapped[list[TagLogicRef]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


class TagAlias(Base):
    __tablename__ = "tag_aliases"
    __table_args__ = (UniqueConstraint("tag_id", "alias_name", name="uq_tag_aliases_tag_alias"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    tag_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("tags.id"), nullable=False)
    alias_name: Mapped[str] = mapped_column(String(512), nullable=False)
    source_system: Mapped[SourceSystem | None] = mapped_column(
        Enum(SourceSystem, name="alias_source_system_enum", native_enum=False)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tag: Mapped[Tag] = relationship(back_populates="aliases")


class TagLogicRef(Base):
    __tablename__ = "tag_logic_refs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    tag_id: Mapped[uuid.UUID] = mapped_column(GUID(), ForeignKey("tags.id"), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reference_key: Mapped[str | None] = mapped_column(String(512))
    program_name: Mapped[str | None] = mapped_column(String(255))
    routine_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    tag: Mapped[Tag] = relationship(back_populates="logic_refs")
