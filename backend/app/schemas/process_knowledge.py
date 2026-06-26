"""API schemas for the process-unit control knowledge system."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.db.models.process_knowledge import (
    ApprovalDecision,
    DocumentStatus,
    DocumentRevisionStatus,
    EngineeringRecordStatus,
    EngineeringRecordType,
    GovernanceArtifactType,
    ImportAcquisitionMode,
    ImportRunStatus,
    IssueStatus,
    ProposedUpdateStatus,
    ReviewItemStatus,
    SnapshotSourceType,
)


class ProcessUnitCreate(BaseModel):
    name: str
    area: str | None = None
    description: str | None = None


class ProcessUnitRead(ProcessUnitCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ProcessUnitListResponse(BaseModel):
    items: list[ProcessUnitRead]
    total: int


class EquipmentModuleCreate(BaseModel):
    name: str
    module_type: str | None = None
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class EquipmentModuleRead(EquipmentModuleCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    process_unit_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class LogicSnapshotCreate(BaseModel):
    source_type: SnapshotSourceType = SnapshotSourceType.XML
    source_filename: str
    file_hash: str | None = None
    export_timestamp: datetime | None = None
    parsed_extract: dict[str, Any] = Field(default_factory=dict)
    raw_project_id: str | None = None
    notes: str | None = None
    created_by: str | None = None


class LogicSnapshotRead(LogicSnapshotCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    module_id: uuid.UUID
    created_at: datetime


class ControlNarrativeCreate(BaseModel):
    snapshot_id: uuid.UUID | None = None
    title: str
    body_markdown: str
    status: DocumentStatus = DocumentStatus.DRAFT
    revision: str = "0.1"
    owner: str | None = None
    reviewer: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ControlNarrativeRead(ControlNarrativeCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    module_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class GovernanceArtifactCreate(BaseModel):
    snapshot_id: uuid.UUID | None = None
    artifact_type: GovernanceArtifactType
    title: str
    status: DocumentStatus = DocumentStatus.DRAFT
    content_json: dict[str, Any] = Field(default_factory=dict)
    source_filename: str | None = None
    owner: str | None = None
    reviewer: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class GovernanceArtifactRead(GovernanceArtifactCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    module_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ControlIssueCreate(BaseModel):
    found_in_snapshot_id: uuid.UUID | None = None
    fixed_in_snapshot_id: uuid.UUID | None = None
    title: str
    symptom: str | None = None
    root_cause: str | None = None
    fix_description: str | None = None
    severity: str | None = None
    status: IssueStatus = IssueStatus.OPEN
    tags: list[str] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    created_by: str | None = None
    verified_by: str | None = None


class ControlIssueRead(ControlIssueCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    module_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ControlImportSourceCreate(BaseModel):
    process_unit_id: uuid.UUID
    module_id: uuid.UUID | None = None
    name: str
    source_system: str
    acquisition_mode: ImportAcquisitionMode = ImportAcquisitionMode.MANUAL_UPLOAD
    connector_hint: str | None = None
    endpoint_url: str | None = None
    export_path: str | None = None
    schedule: str | None = None
    is_enabled: bool = True
    config_json: dict[str, Any] = Field(default_factory=dict)


class ControlImportSourceRead(ControlImportSourceCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    last_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ControlImportRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_id: uuid.UUID
    module_id: uuid.UUID | None = None
    status: ImportRunStatus
    acquired_filename: str | None = None
    project_id: str | None = None
    previous_project_id: str | None = None
    snapshot_id: uuid.UUID | None = None
    diff_summary: str | None = None
    impacted_records: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class EngineeringRecordCreate(BaseModel):
    process_unit_id: uuid.UUID
    module_id: uuid.UUID | None = None
    record_type: EngineeringRecordType
    title: str
    status: EngineeringRecordStatus = EngineeringRecordStatus.ACTIVE
    owner: str | None = None
    description: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class EngineeringRecordRead(EngineeringRecordCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DocumentTemplateCreate(BaseModel):
    name: str
    record_type: EngineeringRecordType
    scope_company: str | None = None
    scope_site: str | None = None
    template_body: str | None = None
    template_schema: dict[str, Any] = Field(default_factory=dict)
    active: bool = True
    version: str = "1.0"


class DocumentTemplateRead(DocumentTemplateCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class DocumentRevisionCreate(BaseModel):
    source_logic_snapshot_id: uuid.UUID | None = None
    template_id: uuid.UUID | None = None
    revision: str
    status: DocumentRevisionStatus = DocumentRevisionStatus.DRAFT
    body_markdown: str | None = None
    structured_content: dict[str, Any] = Field(default_factory=dict)
    created_by: str | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None


class DocumentRevisionRead(DocumentRevisionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    engineering_record_id: uuid.UUID
    created_at: datetime


class LogicDiffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    module_id: uuid.UUID
    previous_snapshot_id: uuid.UUID
    current_snapshot_id: uuid.UUID
    changed: bool
    changed_routines: list[dict[str, Any]] = Field(default_factory=list)
    changed_tags: list[dict[str, Any]] = Field(default_factory=list)
    changed_reads_writes: list[dict[str, Any]] = Field(default_factory=list)
    summary_payload: dict[str, Any] = Field(default_factory=dict)
    impacted_record_ids: list[str] = Field(default_factory=list)
    created_at: datetime


class ProposedDocumentUpdateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    engineering_record_id: uuid.UUID
    source_logic_snapshot_id: uuid.UUID
    source_logic_diff_id: uuid.UUID | None = None
    base_document_revision_id: uuid.UUID | None = None
    proposed_body: str | None = None
    proposed_structured_changes: dict[str, Any] = Field(default_factory=dict)
    confidence: str | None = None
    status: ProposedUpdateStatus
    created_at: datetime


class ReviewItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    process_unit_id: uuid.UUID
    module_id: uuid.UUID | None = None
    engineering_record_id: uuid.UUID | None = None
    logic_snapshot_id: uuid.UUID | None = None
    logic_diff_id: uuid.UUID | None = None
    proposed_document_update_id: uuid.UUID | None = None
    title: str
    reason: str | None = None
    status: ReviewItemStatus
    assigned_to: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None


class ReviewDecisionRequest(BaseModel):
    actor: str
    comments: str | None = None


class ApprovalHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    review_item_id: uuid.UUID | None = None
    engineering_record_id: uuid.UUID | None = None
    document_revision_id: uuid.UUID | None = None
    proposed_document_update_id: uuid.UUID | None = None
    decision: ApprovalDecision
    actor: str
    comments: str | None = None
    created_at: datetime


class ImportSyncResult(BaseModel):
    run: ControlImportRunRead
    snapshot: LogicSnapshotRead | None = None
    diff: dict[str, Any] | None = None
    logic_diff: LogicDiffRead | None = None
    impacted_records: list[dict[str, Any]] = Field(default_factory=list)
    process_unit_id: uuid.UUID | None = None
    module_id: uuid.UUID | None = None
    import_source_id: uuid.UUID | None = None
    snapshot_id: uuid.UUID | None = None
    diff_id: uuid.UUID | None = None
    changed: bool = False
    review_item_ids: list[str] = Field(default_factory=list)
    affected_record_ids: list[str] = Field(default_factory=list)


class ModuleRecordResponse(BaseModel):
    module: EquipmentModuleRead
    snapshots: list[LogicSnapshotRead]
    narratives: list[ControlNarrativeRead]
    governance_artifacts: list[GovernanceArtifactRead]
    issues: list[ControlIssueRead]
