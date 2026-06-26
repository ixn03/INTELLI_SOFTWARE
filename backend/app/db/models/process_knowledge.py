"""Process-unit centered control knowledge and governance ORM models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models.tag_registry import GUID, JSONType
from app.db.session import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(GUID(), primary_key=True, default=uuid.uuid4)


class DocumentStatus(str, enum.Enum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    NEEDS_REVIEW = "needs_review"
    WAIVED = "waived"


class EngineeringRecordType(str, enum.Enum):
    CONTROL_NARRATIVE = "control_narrative"
    IO_LIST = "io_list"
    CAUSE_EFFECT_MATRIX = "cause_effect_matrix"
    ALARM_RATIONALIZATION = "alarm_rationalization"
    MOC = "moc"
    KNOWLEDGE_ISSUE = "knowledge_issue"
    ENGINEERING_NOTE = "engineering_note"


class EngineeringRecordStatus(str, enum.Enum):
    ACTIVE = "active"
    NEEDING_SETUP = "needing_setup"
    NEEDS_REVIEW = "needs_review"
    ARCHIVED = "archived"


class DocumentRevisionStatus(str, enum.Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class ProposedUpdateStatus(str, enum.Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ReviewItemStatus(str, enum.Enum):
    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    RESOLVED = "resolved"


class ApprovalDecision(str, enum.Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    COMMENTED = "commented"


class SnapshotSourceType(str, enum.Enum):
    FHX = "fhx"
    L5X = "l5x"
    XML = "xml"
    OTHER_EXPORT = "other_export"


class GovernanceArtifactType(str, enum.Enum):
    IO_LIST = "io_list"
    CEM = "cem"
    NARRATIVE = "narrative"
    ALARM_RATIONALIZATION = "alarm_rationalization"
    MOC = "moc"
    FAT = "fat"
    SAT = "sat"
    COMMISSIONING = "commissioning"


class IssueStatus(str, enum.Enum):
    OPEN = "open"
    ROOT_CAUSED = "root_caused"
    FIX_DEPLOYED = "fix_deployed"
    VERIFIED = "verified"
    WONT_FIX = "wont_fix"


class ImportAcquisitionMode(str, enum.Enum):
    MANUAL_UPLOAD = "manual_upload"
    FILE_DROP = "file_drop"
    VENDOR_API = "vendor_api"
    SCHEDULED_EXPORT = "scheduled_export"


class ImportRunStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class ProcessUnit(Base):
    __tablename__ = "process_units"
    __table_args__ = (UniqueConstraint("name", name="uq_process_units_name"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    area: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    modules: Mapped[list[EquipmentModule]] = relationship(
        back_populates="process_unit", cascade="all, delete-orphan"
    )
    import_sources: Mapped[list[ControlImportSource]] = relationship(
        back_populates="process_unit", cascade="all, delete-orphan"
    )
    engineering_records: Mapped[list[EngineeringRecord]] = relationship(
        back_populates="process_unit", cascade="all, delete-orphan"
    )
    review_items: Mapped[list[ReviewItem]] = relationship(back_populates="process_unit")


class EquipmentModule(Base):
    __tablename__ = "equipment_modules"
    __table_args__ = (
        UniqueConstraint("process_unit_id", "name", name="uq_equipment_modules_unit_name"),
        Index("ix_equipment_modules_name", "name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    process_unit_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("process_units.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    module_type: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    process_unit: Mapped[ProcessUnit] = relationship(back_populates="modules")
    snapshots: Mapped[list[LogicSnapshot]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )
    narratives: Mapped[list[ControlNarrative]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )
    governance_artifacts: Mapped[list[GovernanceArtifact]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )
    issues: Mapped[list[ControlIssue]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )
    import_sources: Mapped[list[ControlImportSource]] = relationship(back_populates="module")
    engineering_records: Mapped[list[EngineeringRecord]] = relationship(
        back_populates="module", cascade="all, delete-orphan"
    )
    logic_diffs: Mapped[list[LogicDiff]] = relationship(back_populates="module")
    review_items: Mapped[list[ReviewItem]] = relationship(back_populates="module")


class ControlImportSource(Base):
    __tablename__ = "control_import_sources"
    __table_args__ = (
        UniqueConstraint("process_unit_id", "name", name="uq_import_sources_unit_name"),
        Index("ix_import_sources_module_enabled", "module_id", "is_enabled"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    process_unit_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("process_units.id"), nullable=False
    )
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("equipment_modules.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    acquisition_mode: Mapped[ImportAcquisitionMode] = mapped_column(
        Enum(ImportAcquisitionMode, name="import_acquisition_mode_enum", native_enum=False),
        nullable=False,
        default=ImportAcquisitionMode.MANUAL_UPLOAD,
    )
    connector_hint: Mapped[str | None] = mapped_column(String(128))
    endpoint_url: Mapped[str | None] = mapped_column(String(1024))
    export_path: Mapped[str | None] = mapped_column(String(1024))
    schedule: Mapped[str | None] = mapped_column(String(255))
    is_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    process_unit: Mapped[ProcessUnit] = relationship(back_populates="import_sources")
    module: Mapped[EquipmentModule | None] = relationship(back_populates="import_sources")
    runs: Mapped[list[ControlImportRun]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class ControlImportRun(Base):
    __tablename__ = "control_import_runs"
    __table_args__ = (Index("ix_import_runs_source_started", "source_id", "started_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("control_import_sources.id"), nullable=False
    )
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("equipment_modules.id"), nullable=True
    )
    status: Mapped[ImportRunStatus] = mapped_column(
        Enum(ImportRunStatus, name="import_run_status_enum", native_enum=False),
        nullable=False,
        default=ImportRunStatus.PENDING,
    )
    acquired_filename: Mapped[str | None] = mapped_column(String(512))
    project_id: Mapped[str | None] = mapped_column(String(128))
    previous_project_id: Mapped[str | None] = mapped_column(String(128))
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("logic_snapshots.id"))
    diff_summary: Mapped[str | None] = mapped_column(Text)
    impacted_records: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[ControlImportSource] = relationship(back_populates="runs")
    module: Mapped[EquipmentModule | None] = relationship()
    snapshot: Mapped[LogicSnapshot | None] = relationship()


class LogicSnapshot(Base):
    __tablename__ = "logic_snapshots"
    __table_args__ = (
        Index("ix_logic_snapshots_module_created", "module_id", "created_at"),
        Index("ix_logic_snapshots_file_hash", "file_hash"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    module_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("equipment_modules.id"), nullable=False
    )
    source_type: Mapped[SnapshotSourceType] = mapped_column(
        Enum(SnapshotSourceType, name="snapshot_source_type_enum", native_enum=False),
        nullable=False,
    )
    source_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(128))
    export_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parsed_extract: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    raw_project_id: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    module: Mapped[EquipmentModule] = relationship(back_populates="snapshots")
    diffs_as_previous: Mapped[list[LogicDiff]] = relationship(
        back_populates="previous_snapshot",
        foreign_keys="LogicDiff.previous_snapshot_id",
    )
    diffs_as_current: Mapped[list[LogicDiff]] = relationship(
        back_populates="current_snapshot",
        foreign_keys="LogicDiff.current_snapshot_id",
    )


class LogicDiff(Base):
    __tablename__ = "logic_diffs"
    __table_args__ = (
        UniqueConstraint(
            "previous_snapshot_id",
            "current_snapshot_id",
            name="uq_logic_diffs_snapshot_pair",
        ),
        Index("ix_logic_diffs_module_changed", "module_id", "changed"),
        Index("ix_logic_diffs_current_snapshot", "current_snapshot_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    module_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("equipment_modules.id"), nullable=False
    )
    previous_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("logic_snapshots.id"), nullable=False
    )
    current_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("logic_snapshots.id"), nullable=False
    )
    changed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    changed_routines: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    changed_tags: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    changed_reads_writes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    summary_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    impacted_record_ids: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    module: Mapped[EquipmentModule] = relationship(back_populates="logic_diffs")
    previous_snapshot: Mapped[LogicSnapshot] = relationship(
        back_populates="diffs_as_previous",
        foreign_keys=[previous_snapshot_id],
    )
    current_snapshot: Mapped[LogicSnapshot] = relationship(
        back_populates="diffs_as_current",
        foreign_keys=[current_snapshot_id],
    )
    proposed_updates: Mapped[list[ProposedDocumentUpdate]] = relationship(back_populates="source_logic_diff")
    review_items: Mapped[list[ReviewItem]] = relationship(back_populates="logic_diff")


class EngineeringRecord(Base):
    __tablename__ = "engineering_records"
    __table_args__ = (
        UniqueConstraint(
            "process_unit_id",
            "module_id",
            "record_type",
            "title",
            name="uq_engineering_records_scope_type_title",
        ),
        Index("ix_engineering_records_module_type", "module_id", "record_type"),
        Index("ix_engineering_records_unit_type", "process_unit_id", "record_type"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    process_unit_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("process_units.id"), nullable=False
    )
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("equipment_modules.id"), nullable=True
    )
    record_type: Mapped[EngineeringRecordType] = mapped_column(
        Enum(EngineeringRecordType, name="engineering_record_type_enum", native_enum=False),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[EngineeringRecordStatus] = mapped_column(
        Enum(EngineeringRecordStatus, name="engineering_record_status_enum", native_enum=False),
        nullable=False,
        default=EngineeringRecordStatus.ACTIVE,
    )
    owner: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    process_unit: Mapped[ProcessUnit] = relationship(back_populates="engineering_records")
    module: Mapped[EquipmentModule | None] = relationship(back_populates="engineering_records")
    revisions: Mapped[list[DocumentRevision]] = relationship(
        back_populates="engineering_record", cascade="all, delete-orphan"
    )
    proposed_updates: Mapped[list[ProposedDocumentUpdate]] = relationship(
        back_populates="engineering_record", cascade="all, delete-orphan"
    )
    review_items: Mapped[list[ReviewItem]] = relationship(back_populates="engineering_record")
    approval_history: Mapped[list[ApprovalHistory]] = relationship(back_populates="engineering_record")


class DocumentTemplate(Base):
    __tablename__ = "document_templates"
    __table_args__ = (
        Index("ix_document_templates_record_active", "record_type", "active"),
        UniqueConstraint("name", "record_type", "version", name="uq_document_templates_name_type_version"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    record_type: Mapped[EngineeringRecordType] = mapped_column(
        Enum(EngineeringRecordType, name="template_record_type_enum", native_enum=False),
        nullable=False,
    )
    scope_company: Mapped[str | None] = mapped_column(String(255))
    scope_site: Mapped[str | None] = mapped_column(String(255))
    template_body: Mapped[str | None] = mapped_column(Text)
    template_schema: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1.0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DocumentRevision(Base):
    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("engineering_record_id", "revision", name="uq_document_revisions_record_revision"),
        Index("ix_document_revisions_record_status", "engineering_record_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    engineering_record_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("engineering_records.id"), nullable=False
    )
    source_logic_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("logic_snapshots.id"), nullable=True
    )
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("document_templates.id"), nullable=True
    )
    revision: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[DocumentRevisionStatus] = mapped_column(
        Enum(DocumentRevisionStatus, name="document_revision_status_enum", native_enum=False),
        nullable=False,
        default=DocumentRevisionStatus.DRAFT,
    )
    body_markdown: Mapped[str | None] = mapped_column(Text)
    structured_content: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    engineering_record: Mapped[EngineeringRecord] = relationship(back_populates="revisions")
    source_logic_snapshot: Mapped[LogicSnapshot | None] = relationship()
    template: Mapped[DocumentTemplate | None] = relationship()
    proposed_updates_as_base: Mapped[list[ProposedDocumentUpdate]] = relationship(
        back_populates="base_document_revision"
    )


class ProposedDocumentUpdate(Base):
    __tablename__ = "proposed_document_updates"
    __table_args__ = (
        Index("ix_proposed_updates_record_status", "engineering_record_id", "status"),
        Index("ix_proposed_updates_diff", "source_logic_diff_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    engineering_record_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("engineering_records.id"), nullable=False
    )
    source_logic_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("logic_snapshots.id"), nullable=False
    )
    source_logic_diff_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("logic_diffs.id"), nullable=True
    )
    base_document_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("document_revisions.id"), nullable=True
    )
    proposed_body: Mapped[str | None] = mapped_column(Text)
    proposed_structured_changes: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict
    )
    confidence: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ProposedUpdateStatus] = mapped_column(
        Enum(ProposedUpdateStatus, name="proposed_update_status_enum", native_enum=False),
        nullable=False,
        default=ProposedUpdateStatus.PROPOSED,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    engineering_record: Mapped[EngineeringRecord] = relationship(back_populates="proposed_updates")
    source_logic_snapshot: Mapped[LogicSnapshot] = relationship()
    source_logic_diff: Mapped[LogicDiff | None] = relationship(back_populates="proposed_updates")
    base_document_revision: Mapped[DocumentRevision | None] = relationship(
        back_populates="proposed_updates_as_base"
    )
    review_items: Mapped[list[ReviewItem]] = relationship(back_populates="proposed_document_update")


class ReviewItem(Base):
    __tablename__ = "review_items"
    __table_args__ = (
        Index("ix_review_items_process_unit_status", "process_unit_id", "status"),
        Index("ix_review_items_module_status", "module_id", "status"),
        Index("ix_review_items_record_status", "engineering_record_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    process_unit_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("process_units.id"), nullable=False
    )
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("equipment_modules.id"), nullable=True
    )
    engineering_record_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("engineering_records.id"), nullable=True
    )
    logic_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("logic_snapshots.id"), nullable=True
    )
    logic_diff_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("logic_diffs.id"), nullable=True
    )
    proposed_document_update_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("proposed_document_updates.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ReviewItemStatus] = mapped_column(
        Enum(ReviewItemStatus, name="review_item_status_enum", native_enum=False),
        nullable=False,
        default=ReviewItemStatus.OPEN,
    )
    assigned_to: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    process_unit: Mapped[ProcessUnit] = relationship(back_populates="review_items")
    module: Mapped[EquipmentModule | None] = relationship(back_populates="review_items")
    engineering_record: Mapped[EngineeringRecord | None] = relationship(back_populates="review_items")
    logic_snapshot: Mapped[LogicSnapshot | None] = relationship()
    logic_diff: Mapped[LogicDiff | None] = relationship(back_populates="review_items")
    proposed_document_update: Mapped[ProposedDocumentUpdate | None] = relationship(
        back_populates="review_items"
    )
    approval_history: Mapped[list[ApprovalHistory]] = relationship(
        back_populates="review_item", cascade="all, delete-orphan"
    )


class ApprovalHistory(Base):
    __tablename__ = "approval_history"
    __table_args__ = (
        Index("ix_approval_history_review_item", "review_item_id"),
        Index("ix_approval_history_record", "engineering_record_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    review_item_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("review_items.id"), nullable=True
    )
    engineering_record_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("engineering_records.id"), nullable=True
    )
    document_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("document_revisions.id"), nullable=True
    )
    proposed_document_update_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("proposed_document_updates.id"), nullable=True
    )
    decision: Mapped[ApprovalDecision] = mapped_column(
        Enum(ApprovalDecision, name="approval_decision_enum", native_enum=False),
        nullable=False,
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    comments: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    review_item: Mapped[ReviewItem | None] = relationship(back_populates="approval_history")
    engineering_record: Mapped[EngineeringRecord | None] = relationship(back_populates="approval_history")
    document_revision: Mapped[DocumentRevision | None] = relationship()
    proposed_document_update: Mapped[ProposedDocumentUpdate | None] = relationship()


class ControlNarrative(Base):
    __tablename__ = "control_narratives"
    __table_args__ = (Index("ix_control_narratives_module_status", "module_id", "status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    module_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("equipment_modules.id"), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("logic_snapshots.id"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="document_status_enum", native_enum=False),
        nullable=False,
        default=DocumentStatus.DRAFT,
    )
    revision: Mapped[str] = mapped_column(String(64), nullable=False, default="0.1")
    owner: Mapped[str | None] = mapped_column(String(255))
    reviewer: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    module: Mapped[EquipmentModule] = relationship(back_populates="narratives")
    snapshot: Mapped[LogicSnapshot | None] = relationship()


class GovernanceArtifact(Base):
    __tablename__ = "governance_artifacts"
    __table_args__ = (
        Index("ix_governance_artifacts_module_type", "module_id", "artifact_type"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    module_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("equipment_modules.id"), nullable=False
    )
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), ForeignKey("logic_snapshots.id"))
    artifact_type: Mapped[GovernanceArtifactType] = mapped_column(
        Enum(GovernanceArtifactType, name="governance_artifact_type_enum", native_enum=False),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus, name="governance_artifact_status_enum", native_enum=False),
        nullable=False,
        default=DocumentStatus.DRAFT,
    )
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    source_filename: Mapped[str | None] = mapped_column(String(512))
    owner: Mapped[str | None] = mapped_column(String(255))
    reviewer: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    module: Mapped[EquipmentModule] = relationship(back_populates="governance_artifacts")
    snapshot: Mapped[LogicSnapshot | None] = relationship()


class ControlIssue(Base):
    __tablename__ = "control_issues"
    __table_args__ = (Index("ix_control_issues_module_status", "module_id", "status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    module_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("equipment_modules.id"), nullable=False
    )
    found_in_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("logic_snapshots.id")
    )
    fixed_in_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("logic_snapshots.id")
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    symptom: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    fix_description: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[IssueStatus] = mapped_column(
        Enum(IssueStatus, name="control_issue_status_enum", native_enum=False),
        nullable=False,
        default=IssueStatus.OPEN,
    )
    tags: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONType, nullable=False, default=list)
    created_by: Mapped[str | None] = mapped_column(String(255))
    verified_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    module: Mapped[EquipmentModule] = relationship(back_populates="issues")
    found_in_snapshot: Mapped[LogicSnapshot | None] = relationship(
        foreign_keys=[found_in_snapshot_id]
    )
    fixed_in_snapshot: Mapped[LogicSnapshot | None] = relationship(
        foreign_keys=[fixed_in_snapshot_id]
    )
