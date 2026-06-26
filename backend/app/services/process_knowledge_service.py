"""Service layer for process-unit control knowledge records."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.connectors.registry import get_connector
from app.db.models.process_knowledge import (
    ApprovalDecision,
    ApprovalHistory,
    ControlImportRun,
    ControlImportSource,
    ControlIssue,
    ControlNarrative,
    DocumentStatus,
    DocumentRevision,
    DocumentRevisionStatus,
    DocumentTemplate,
    EquipmentModule,
    EngineeringRecord,
    EngineeringRecordStatus,
    EngineeringRecordType,
    GovernanceArtifact,
    GovernanceArtifactType,
    ImportRunStatus,
    IssueStatus,
    LogicDiff,
    LogicSnapshot,
    ProposedDocumentUpdate,
    ProposedUpdateStatus,
    ProcessUnit,
    ReviewItem,
    ReviewItemStatus,
    SnapshotSourceType,
)
from app.services.graph_service import graph_summary
from app.services.project_store import project_store
from app.services.version_compare_service import compare_projects
from app.schemas.process_knowledge import (
    ControlImportSourceCreate,
    ControlIssueCreate,
    ControlNarrativeCreate,
    DocumentRevisionCreate,
    DocumentTemplateCreate,
    EquipmentModuleCreate,
    EngineeringRecordCreate,
    GovernanceArtifactCreate,
    LogicSnapshotCreate,
    ProcessUnitCreate,
)


class NotFoundError(ValueError):
    pass


class ConflictError(ValueError):
    pass


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        raise ConflictError("A record with the same unique identity already exists.") from exc


def create_process_unit(db: Session, payload: ProcessUnitCreate) -> ProcessUnit:
    item = ProcessUnit(**payload.model_dump())
    db.add(item)
    _flush(db)
    return item


def list_process_units(db: Session, *, limit: int = 100, offset: int = 0) -> tuple[list[ProcessUnit], int]:
    stmt = select(ProcessUnit).order_by(ProcessUnit.name).offset(offset).limit(limit)
    items = list(db.scalars(stmt))
    total = len(list(db.scalars(select(ProcessUnit.id))))
    return items, total


def get_process_unit(db: Session, process_unit_id: uuid.UUID) -> ProcessUnit:
    item = db.get(ProcessUnit, process_unit_id)
    if item is None:
        raise NotFoundError("Process unit not found.")
    return item


def create_equipment_module(
    db: Session,
    process_unit_id: uuid.UUID,
    payload: EquipmentModuleCreate,
) -> EquipmentModule:
    get_process_unit(db, process_unit_id)
    item = EquipmentModule(process_unit_id=process_unit_id, **payload.model_dump())
    db.add(item)
    _flush(db)
    return item


def list_equipment_modules(
    db: Session,
    process_unit_id: uuid.UUID,
    *,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[EquipmentModule], int]:
    get_process_unit(db, process_unit_id)
    base = select(EquipmentModule).where(EquipmentModule.process_unit_id == process_unit_id)
    items = list(db.scalars(base.order_by(EquipmentModule.name).offset(offset).limit(limit)))
    total = len(list(db.scalars(select(EquipmentModule.id).where(EquipmentModule.process_unit_id == process_unit_id))))
    return items, total


def get_equipment_module(db: Session, module_id: uuid.UUID) -> EquipmentModule:
    item = db.get(EquipmentModule, module_id)
    if item is None:
        raise NotFoundError("Equipment module not found.")
    return item


def create_logic_snapshot(
    db: Session,
    module_id: uuid.UUID,
    payload: LogicSnapshotCreate,
) -> LogicSnapshot:
    get_equipment_module(db, module_id)
    item = LogicSnapshot(module_id=module_id, **payload.model_dump())
    db.add(item)
    _flush(db)
    return item


def create_control_narrative(
    db: Session,
    module_id: uuid.UUID,
    payload: ControlNarrativeCreate,
) -> ControlNarrative:
    get_equipment_module(db, module_id)
    item = ControlNarrative(module_id=module_id, **payload.model_dump())
    db.add(item)
    _flush(db)
    return item


def create_governance_artifact(
    db: Session,
    module_id: uuid.UUID,
    payload: GovernanceArtifactCreate,
) -> GovernanceArtifact:
    get_equipment_module(db, module_id)
    item = GovernanceArtifact(module_id=module_id, **payload.model_dump())
    db.add(item)
    _flush(db)
    return item


def create_control_issue(
    db: Session,
    module_id: uuid.UUID,
    payload: ControlIssueCreate,
) -> ControlIssue:
    get_equipment_module(db, module_id)
    item = ControlIssue(module_id=module_id, **payload.model_dump())
    db.add(item)
    _flush(db)
    return item


def create_engineering_record(
    db: Session,
    payload: EngineeringRecordCreate,
) -> EngineeringRecord:
    get_process_unit(db, payload.process_unit_id)
    if payload.module_id is not None:
        module = get_equipment_module(db, payload.module_id)
        if module.process_unit_id != payload.process_unit_id:
            raise ConflictError("Engineering record module must belong to the process unit.")
    item = EngineeringRecord(**payload.model_dump())
    db.add(item)
    _flush(db)
    return item


def list_engineering_records(
    db: Session,
    *,
    process_unit_id: uuid.UUID | None = None,
    module_id: uuid.UUID | None = None,
    record_type: EngineeringRecordType | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[EngineeringRecord], int]:
    stmt = select(EngineeringRecord)
    count_stmt = select(EngineeringRecord.id)
    if process_unit_id is not None:
        stmt = stmt.where(EngineeringRecord.process_unit_id == process_unit_id)
        count_stmt = count_stmt.where(EngineeringRecord.process_unit_id == process_unit_id)
    if module_id is not None:
        stmt = stmt.where(EngineeringRecord.module_id == module_id)
        count_stmt = count_stmt.where(EngineeringRecord.module_id == module_id)
    if record_type is not None:
        stmt = stmt.where(EngineeringRecord.record_type == record_type)
        count_stmt = count_stmt.where(EngineeringRecord.record_type == record_type)
    items = list(db.scalars(stmt.order_by(EngineeringRecord.title).offset(offset).limit(limit)))
    total = len(list(db.scalars(count_stmt)))
    return items, total


def get_engineering_record(db: Session, record_id: uuid.UUID) -> EngineeringRecord:
    item = db.get(EngineeringRecord, record_id)
    if item is None:
        raise NotFoundError("Engineering record not found.")
    return item


def create_document_template(
    db: Session,
    payload: DocumentTemplateCreate,
) -> DocumentTemplate:
    item = DocumentTemplate(**payload.model_dump())
    db.add(item)
    _flush(db)
    return item


def create_document_revision(
    db: Session,
    engineering_record_id: uuid.UUID,
    payload: DocumentRevisionCreate,
) -> DocumentRevision:
    get_engineering_record(db, engineering_record_id)
    item = DocumentRevision(
        engineering_record_id=engineering_record_id,
        **payload.model_dump(),
    )
    db.add(item)
    _flush(db)
    return item


def list_document_revisions(
    db: Session,
    engineering_record_id: uuid.UUID,
) -> list[DocumentRevision]:
    get_engineering_record(db, engineering_record_id)
    return list(
        db.scalars(
            select(DocumentRevision)
            .where(DocumentRevision.engineering_record_id == engineering_record_id)
            .order_by(DocumentRevision.created_at.desc())
        )
    )


def latest_approved_revision(
    db: Session,
    engineering_record_id: uuid.UUID,
) -> DocumentRevision | None:
    return db.scalar(
        select(DocumentRevision)
        .where(
            DocumentRevision.engineering_record_id == engineering_record_id,
            DocumentRevision.status == DocumentRevisionStatus.APPROVED,
        )
        .order_by(DocumentRevision.created_at.desc())
        .limit(1)
    )


def create_import_source(
    db: Session,
    payload: ControlImportSourceCreate,
) -> ControlImportSource:
    get_process_unit(db, payload.process_unit_id)
    if payload.module_id is not None:
        module = get_equipment_module(db, payload.module_id)
        if module.process_unit_id != payload.process_unit_id:
            raise ConflictError("Import source module must belong to the process unit.")
    item = ControlImportSource(**payload.model_dump())
    db.add(item)
    _flush(db)
    return item


def list_import_sources(
    db: Session,
    *,
    process_unit_id: uuid.UUID | None = None,
    module_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ControlImportSource], int]:
    stmt = select(ControlImportSource)
    if process_unit_id is not None:
        stmt = stmt.where(ControlImportSource.process_unit_id == process_unit_id)
    if module_id is not None:
        stmt = stmt.where(ControlImportSource.module_id == module_id)
    stmt = stmt.order_by(ControlImportSource.name)
    items = list(db.scalars(stmt.offset(offset).limit(limit)))

    count_stmt = select(ControlImportSource.id)
    if process_unit_id is not None:
        count_stmt = count_stmt.where(ControlImportSource.process_unit_id == process_unit_id)
    if module_id is not None:
        count_stmt = count_stmt.where(ControlImportSource.module_id == module_id)
    total = len(list(db.scalars(count_stmt)))
    return items, total


def get_import_source(db: Session, source_id: uuid.UUID) -> ControlImportSource:
    item = db.get(ControlImportSource, source_id)
    if item is None:
        raise NotFoundError("Import source not found.")
    return item


def _source_type_from_filename(filename: str) -> SnapshotSourceType:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext == "fhx":
        return SnapshotSourceType.FHX
    if ext == "l5x":
        return SnapshotSourceType.L5X
    if ext == "xml":
        return SnapshotSourceType.XML
    return SnapshotSourceType.OTHER_EXPORT


def _latest_snapshot_for_module(
    db: Session,
    module_id: uuid.UUID,
) -> LogicSnapshot | None:
    return db.scalar(
        select(LogicSnapshot)
        .where(LogicSnapshot.module_id == module_id)
        .order_by(LogicSnapshot.created_at.desc())
        .limit(1)
    )


def _artifact_label(artifact_type: GovernanceArtifactType) -> str:
    if artifact_type == GovernanceArtifactType.IO_LIST:
        return "IO list"
    if artifact_type == GovernanceArtifactType.CEM:
        return "cause and effect matrix"
    if artifact_type == GovernanceArtifactType.NARRATIVE:
        return "control narrative"
    return artifact_type.value.replace("_", " ")


def _mark_impacted_records_for_review(
    db: Session,
    *,
    module_id: uuid.UUID,
    diff_summary: str,
) -> list[dict[str, Any]]:
    impacted: list[dict[str, Any]] = []

    narratives = list(
        db.scalars(select(ControlNarrative).where(ControlNarrative.module_id == module_id))
    )
    for narrative in narratives:
        if narrative.status in (DocumentStatus.APPROVED, DocumentStatus.IN_REVIEW):
            narrative.status = DocumentStatus.NEEDS_REVIEW
        impacted.append(
            {
                "record_type": "narrative",
                "record_id": str(narrative.id),
                "title": narrative.title,
                "reason": diff_summary,
                "recommended_action": "Review narrative against latest logic snapshot.",
            }
        )

    artifact_types = {
        GovernanceArtifactType.IO_LIST,
        GovernanceArtifactType.CEM,
        GovernanceArtifactType.ALARM_RATIONALIZATION,
    }
    artifacts = list(
        db.scalars(
            select(GovernanceArtifact).where(
                GovernanceArtifact.module_id == module_id,
                GovernanceArtifact.artifact_type.in_(artifact_types),
            )
        )
    )
    for artifact in artifacts:
        if artifact.status in (DocumentStatus.APPROVED, DocumentStatus.IN_REVIEW):
            artifact.status = DocumentStatus.NEEDS_REVIEW
        impacted.append(
            {
                "record_type": artifact.artifact_type.value,
                "record_id": str(artifact.id),
                "title": artifact.title,
                "reason": diff_summary,
                "recommended_action": (
                    f"Review {_artifact_label(artifact.artifact_type)} against latest "
                    "logic snapshot."
                ),
            }
        )

    issues = list(
        db.scalars(
            select(ControlIssue).where(
                ControlIssue.module_id == module_id,
                ControlIssue.status != IssueStatus.VERIFIED,
            )
        )
    )
    for issue in issues:
        impacted.append(
            {
                "record_type": "issue",
                "record_id": str(issue.id),
                "title": issue.title,
                "reason": diff_summary,
                "recommended_action": "Confirm whether this issue is still valid.",
            }
        )

    return impacted


def _diff_parts(diff_payload: dict[str, Any]) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    changed_objects = list(diff_payload.get("changed_objects") or [])
    changed_relationships = list(diff_payload.get("changed_relationships") or [])
    changed_routines = [
        item
        for item in changed_objects
        if "routine" in str(item.get("change", "")).lower()
    ]
    changed_tags = [
        item
        for item in changed_objects
        if "tag" in str(item.get("change", "")).lower()
    ]
    changed_reads_writes = [
        item
        for item in changed_relationships
        if item.get("change") in {
            "relationship_added",
            "relationship_removed",
            "writers_changed",
            "readers_or_conditions_changed",
        }
    ]
    return changed_routines, changed_tags, changed_reads_writes


def _create_logic_diff(
    db: Session,
    *,
    module_id: uuid.UUID,
    previous_snapshot_id: uuid.UUID,
    current_snapshot_id: uuid.UUID,
    diff_payload: dict[str, Any],
    changed: bool,
) -> LogicDiff:
    changed_routines, changed_tags, changed_reads_writes = _diff_parts(diff_payload)
    item = LogicDiff(
        module_id=module_id,
        previous_snapshot_id=previous_snapshot_id,
        current_snapshot_id=current_snapshot_id,
        changed=changed,
        changed_routines=changed_routines,
        changed_tags=changed_tags,
        changed_reads_writes=changed_reads_writes,
        summary_payload=diff_payload,
        impacted_record_ids=[],
    )
    db.add(item)
    _flush(db)
    return item


def _default_record_title(record_type: EngineeringRecordType, module: EquipmentModule) -> str:
    labels = {
        EngineeringRecordType.CONTROL_NARRATIVE: "Control Narrative",
        EngineeringRecordType.IO_LIST: "IO List",
        EngineeringRecordType.CAUSE_EFFECT_MATRIX: "Cause and Effect Matrix",
        EngineeringRecordType.ALARM_RATIONALIZATION: "Alarm Rationalization",
    }
    return f"{module.name} {labels.get(record_type, record_type.value.replace('_', ' ').title())}"


def _records_for_module(db: Session, module_id: uuid.UUID) -> list[EngineeringRecord]:
    return list(
        db.scalars(
            select(EngineeringRecord)
            .where(EngineeringRecord.module_id == module_id)
            .order_by(EngineeringRecord.record_type, EngineeringRecord.title)
        )
    )


def _ensure_placeholder_records_for_module(
    db: Session,
    module: EquipmentModule,
) -> list[EngineeringRecord]:
    existing = _records_for_module(db, module.id)
    if existing:
        return existing
    created: list[EngineeringRecord] = []
    for record_type in (
        EngineeringRecordType.CONTROL_NARRATIVE,
        EngineeringRecordType.IO_LIST,
        EngineeringRecordType.CAUSE_EFFECT_MATRIX,
        EngineeringRecordType.ALARM_RATIONALIZATION,
    ):
        record = EngineeringRecord(
            process_unit_id=module.process_unit_id,
            module_id=module.id,
            record_type=record_type,
            title=_default_record_title(record_type, module),
            status=EngineeringRecordStatus.NEEDING_SETUP,
            description="Placeholder created because a logic change needs governed documentation review.",
        )
        db.add(record)
        created.append(record)
    _flush(db)
    return created


def _create_review_items_for_logic_diff(
    db: Session,
    *,
    module_id: uuid.UUID,
    snapshot: LogicSnapshot,
    logic_diff: LogicDiff,
    diff_summary: str,
) -> list[dict[str, Any]]:
    module = get_equipment_module(db, module_id)
    records = _ensure_placeholder_records_for_module(db, module)
    impacted: list[dict[str, Any]] = []

    for record in records:
        if record.status == EngineeringRecordStatus.ACTIVE:
            record.status = EngineeringRecordStatus.NEEDS_REVIEW

        base_revision = latest_approved_revision(db, record.id)
        proposed = ProposedDocumentUpdate(
            engineering_record_id=record.id,
            source_logic_snapshot_id=snapshot.id,
            source_logic_diff_id=logic_diff.id,
            base_document_revision_id=base_revision.id if base_revision else None,
            proposed_body=None,
            proposed_structured_changes={
                "diff_summary": diff_summary,
                "changed_routines": logic_diff.changed_routines,
                "changed_tags": logic_diff.changed_tags,
                "changed_reads_writes": logic_diff.changed_reads_writes,
                "instruction": "Engineer review required before any document revision is accepted.",
            },
            confidence="deterministic",
            status=ProposedUpdateStatus.PROPOSED,
        )
        db.add(proposed)
        _flush(db)

        review = ReviewItem(
            process_unit_id=record.process_unit_id,
            module_id=record.module_id,
            engineering_record_id=record.id,
            logic_snapshot_id=snapshot.id,
            logic_diff_id=logic_diff.id,
            proposed_document_update_id=proposed.id,
            title=f"Review {record.title}",
            reason=diff_summary,
            status=(
                ReviewItemStatus.NEEDS_MANUAL_REVIEW
                if record.status == EngineeringRecordStatus.NEEDING_SETUP
                else ReviewItemStatus.OPEN
            ),
        )
        db.add(review)
        _flush(db)

        impacted.append(
            {
                "record_type": record.record_type.value,
                "record_id": str(record.id),
                "title": record.title,
                "review_item_id": str(review.id),
                "proposed_document_update_id": str(proposed.id),
                "reason": diff_summary,
                "recommended_action": "Review parser diff and approve, reject, or edit the governed record.",
            }
        )

    logic_diff.impacted_record_ids = [item["record_id"] for item in impacted]
    _flush(db)
    return impacted


def get_snapshot_diff(db: Session, snapshot_id: uuid.UUID) -> LogicDiff | None:
    return db.scalar(
        select(LogicDiff)
        .where(LogicDiff.current_snapshot_id == snapshot_id)
        .order_by(LogicDiff.created_at.desc())
        .limit(1)
    )


def list_review_items_for_process_unit(
    db: Session,
    process_unit_id: uuid.UUID,
    *,
    status: ReviewItemStatus | None = None,
) -> list[ReviewItem]:
    get_process_unit(db, process_unit_id)
    stmt = select(ReviewItem).where(ReviewItem.process_unit_id == process_unit_id)
    if status is not None:
        stmt = stmt.where(ReviewItem.status == status)
    return list(db.scalars(stmt.order_by(ReviewItem.created_at.desc())))


def get_proposed_document_update(
    db: Session,
    update_id: uuid.UUID,
) -> ProposedDocumentUpdate:
    item = db.get(ProposedDocumentUpdate, update_id)
    if item is None:
        raise NotFoundError("Proposed document update not found.")
    return item


def _decide_review_item(
    db: Session,
    review_item_id: uuid.UUID,
    *,
    actor: str,
    comments: str | None,
    approved: bool,
) -> tuple[ReviewItem, ApprovalHistory]:
    item = db.get(ReviewItem, review_item_id)
    if item is None:
        raise NotFoundError("Review item not found.")
    now = datetime.now(timezone.utc)
    item.status = ReviewItemStatus.APPROVED if approved else ReviewItemStatus.REJECTED
    item.resolved_at = now

    if item.proposed_document_update is not None:
        item.proposed_document_update.status = (
            ProposedUpdateStatus.ACCEPTED if approved else ProposedUpdateStatus.REJECTED
        )

    history = ApprovalHistory(
        review_item_id=item.id,
        engineering_record_id=item.engineering_record_id,
        proposed_document_update_id=item.proposed_document_update_id,
        decision=ApprovalDecision.APPROVED if approved else ApprovalDecision.REJECTED,
        actor=actor,
        comments=comments,
    )
    db.add(history)
    _flush(db)
    return item, history


def approve_review_item(
    db: Session,
    review_item_id: uuid.UUID,
    *,
    actor: str,
    comments: str | None = None,
) -> tuple[ReviewItem, ApprovalHistory]:
    return _decide_review_item(
        db,
        review_item_id,
        actor=actor,
        comments=comments,
        approved=True,
    )


def reject_review_item(
    db: Session,
    review_item_id: uuid.UUID,
    *,
    actor: str,
    comments: str | None = None,
) -> tuple[ReviewItem, ApprovalHistory]:
    return _decide_review_item(
        db,
        review_item_id,
        actor=actor,
        comments=comments,
        approved=False,
    )


def run_import_sync_from_bytes(
    db: Session,
    *,
    source_id: uuid.UUID,
    filename: str,
    content: bytes,
    actor: str | None = None,
) -> dict[str, Any]:
    source = get_import_source(db, source_id)
    if not source.is_enabled:
        raise ConflictError("Import source is disabled.")
    if source.module_id is None:
        raise ConflictError("Import source must be linked to a module for the first MVP.")

    module_id = source.module_id
    previous_snapshot = _latest_snapshot_for_module(db, module_id)
    previous_project_id = previous_snapshot.raw_project_id if previous_snapshot else None
    run: ControlImportRun | None = None

    try:
        connector = get_connector(filename, content)
        project = connector.parse(filename, content)
        project_store.save(project)
        project_id = project.file_hash or ""
        normalized = project_store.get_normalized(project_id)

        run = ControlImportRun(
            source_id=source.id,
            module_id=module_id,
            status=ImportRunStatus.PENDING,
            acquired_filename=filename,
            previous_project_id=previous_project_id,
        )
        db.add(run)
        _flush(db)

        diff_payload: dict[str, Any] | None = None
        diff_summary = "Baseline import; no previous logic snapshot for this module."
        has_changes = False
        if previous_project_id:
            try:
                previous_normalized = project_store.get_normalized(previous_project_id)
                diff = compare_projects(previous_normalized, normalized)
                diff_payload = asdict(diff)
                diff_summary = diff.summary
                has_changes = bool(diff.changed_objects or diff.changed_relationships)
            except Exception as exc:
                diff_summary = f"Imported latest snapshot; previous diff unavailable: {exc}"
                diff_payload = {"error": str(exc)}

        snapshot = LogicSnapshot(
            module_id=module_id,
            source_type=_source_type_from_filename(filename),
            source_filename=filename,
            file_hash=project_id,
            parsed_extract={
                "connector": connector.display_name,
                "project_name": project.project_name,
                "graph": graph_summary(project),
            },
            raw_project_id=project_id,
            notes=f"Imported from {source.name}",
            created_by=actor,
        )
        db.add(snapshot)
        _flush(db)

        logic_diff: LogicDiff | None = None
        impacted_records: list[dict[str, Any]] = []
        if previous_snapshot is not None and diff_payload is not None:
            logic_diff = _create_logic_diff(
                db,
                module_id=module_id,
                previous_snapshot_id=previous_snapshot.id,
                current_snapshot_id=snapshot.id,
                diff_payload=diff_payload,
                changed=has_changes,
            )
            if has_changes:
                impacted_records = _create_review_items_for_logic_diff(
                    db,
                    module_id=module_id,
                    snapshot=snapshot,
                    logic_diff=logic_diff,
                    diff_summary=diff_summary,
                )
                # Preserve compatibility with the earlier governance-artifact
                # MVP while EngineeringRecord replaces it.
                legacy_impacts = _mark_impacted_records_for_review(
                    db,
                    module_id=module_id,
                    diff_summary=diff_summary,
                )
                impacted_records.extend(legacy_impacts)

        now = datetime.now(timezone.utc)
        source.last_run_at = now
        run.status = ImportRunStatus.COMPLETED
        run.project_id = project_id
        run.snapshot_id = snapshot.id
        run.diff_summary = diff_summary
        run.impacted_records = impacted_records
        run.completed_at = now
        _flush(db)
        review_item_ids = [
            str(item["review_item_id"])
            for item in impacted_records
            if item.get("review_item_id")
        ]
        affected_record_ids = [
            str(item["record_id"])
            for item in impacted_records
            if item.get("record_id")
        ]
        return {
            "run": run,
            "snapshot": snapshot,
            "diff": diff_payload,
            "logic_diff": logic_diff,
            "impacted_records": impacted_records,
            "process_unit_id": source.process_unit_id,
            "module_id": module_id,
            "import_source_id": source.id,
            "snapshot_id": snapshot.id,
            "diff_id": logic_diff.id if logic_diff else None,
            "changed": has_changes,
            "review_item_ids": review_item_ids,
            "affected_record_ids": affected_record_ids,
        }
    except Exception as exc:
        if run is None:
            run = ControlImportRun(
                source_id=source.id,
                module_id=module_id,
                status=ImportRunStatus.FAILED,
                acquired_filename=filename,
                previous_project_id=previous_project_id,
            )
            db.add(run)
        now = datetime.now(timezone.utc)
        run.status = ImportRunStatus.FAILED
        run.error_message = str(exc)
        run.completed_at = now
        source.last_run_at = now
        _flush(db)
        raise


def module_record(db: Session, module_id: uuid.UUID) -> dict[str, object]:
    module = get_equipment_module(db, module_id)
    snapshots = list(
        db.scalars(
            select(LogicSnapshot)
            .where(LogicSnapshot.module_id == module_id)
            .order_by(LogicSnapshot.created_at.desc())
        )
    )
    narratives = list(
        db.scalars(
            select(ControlNarrative)
            .where(ControlNarrative.module_id == module_id)
            .order_by(ControlNarrative.updated_at.desc())
        )
    )
    artifacts = list(
        db.scalars(
            select(GovernanceArtifact)
            .where(GovernanceArtifact.module_id == module_id)
            .order_by(GovernanceArtifact.artifact_type, GovernanceArtifact.updated_at.desc())
        )
    )
    issues = list(
        db.scalars(
            select(ControlIssue)
            .where(ControlIssue.module_id == module_id)
            .order_by(ControlIssue.updated_at.desc())
        )
    )
    return {
        "module": module,
        "snapshots": snapshots,
        "narratives": narratives,
        "governance_artifacts": artifacts,
        "issues": issues,
    }
