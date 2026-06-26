"""API routes for the process-unit control knowledge product."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.process_knowledge import (
    AiDraftRequest,
    AiDraftResponse,
    ApprovalHistoryRead,
    ControlImportSourceCreate,
    ControlImportSourceRead,
    ControlIssueCreate,
    ControlIssueRead,
    ControlNarrativeCreate,
    ControlNarrativeRead,
    DocumentRevisionCreate,
    DocumentRevisionRead,
    DocumentTemplateCreate,
    DocumentTemplateRead,
    EquipmentModuleCreate,
    EquipmentModuleRead,
    EngineeringRecordCreate,
    EngineeringRecordRead,
    EngineeringRecordType,
    GovernanceArtifactCreate,
    GovernanceArtifactRead,
    ImportSyncResult,
    LogicDiffRead,
    LogicSnapshotCreate,
    LogicSnapshotRead,
    ModuleRecordResponse,
    ProcessUnitCreate,
    ProcessUnitListResponse,
    ProcessUnitRead,
    ProposedDocumentUpdateRead,
    ReviewDecisionRequest,
    ReviewItemRead,
    ReviewItemStatus,
    SeedDemoResult,
)
from app.services import process_knowledge_service as svc
from app.services.document_generation import DocumentGenerationResult, GenerationMode

router = APIRouter(prefix="/api", tags=["process-knowledge"])


def _not_found(exc: svc.NotFoundError) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _conflict(exc: svc.ConflictError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _ai_draft_response(revision, result: DocumentGenerationResult) -> AiDraftResponse:
    return AiDraftResponse(
        revision=DocumentRevisionRead.model_validate(revision),
        generation_mode=result.mode.value,
        provider_name=result.provider_name,
        confidence=result.confidence,
        source_snapshot_id=result.source_snapshot_id,
        template_id=result.template_id,
        facts_used=[fact.to_dict() for fact in result.facts_used],
        missing_facts=[fact.to_dict() for fact in result.missing_facts],
        assumptions=result.assumptions,
        warnings=result.warnings,
    )


@router.post("/process-units", response_model=ProcessUnitRead, status_code=201)
def create_process_unit(
    payload: ProcessUnitCreate,
    db: Session = Depends(get_db),
) -> ProcessUnitRead:
    try:
        return svc.create_process_unit(db, payload)
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/process-units", response_model=ProcessUnitListResponse)
def list_process_units(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> ProcessUnitListResponse:
    items, total = svc.list_process_units(db, limit=limit, offset=offset)
    return ProcessUnitListResponse(items=items, total=total)


@router.delete("/process-units/{process_unit_id}")
def delete_process_unit(
    process_unit_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    try:
        svc.delete_process_unit_if_empty(db, process_unit_id)
        return {"deleted": True}
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.post(
    "/process-units/{process_unit_id}/modules",
    response_model=EquipmentModuleRead,
    status_code=201,
)
def create_equipment_module(
    process_unit_id: uuid.UUID,
    payload: EquipmentModuleCreate,
    db: Session = Depends(get_db),
) -> EquipmentModuleRead:
    try:
        return svc.create_equipment_module(db, process_unit_id, payload)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.get(
    "/process-units/{process_unit_id}/modules",
    response_model=list[EquipmentModuleRead],
)
def list_equipment_modules(
    process_unit_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[EquipmentModuleRead]:
    try:
        items, _ = svc.list_equipment_modules(
            db, process_unit_id, limit=limit, offset=offset
        )
        return items
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.delete("/modules/{module_id}")
def delete_equipment_module(
    module_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    try:
        svc.delete_equipment_module(db, module_id)
        return {"deleted": True}
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/modules/{module_id}/record", response_model=ModuleRecordResponse)
def get_module_record(
    module_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ModuleRecordResponse:
    try:
        return ModuleRecordResponse(**svc.module_record(db, module_id))
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("/engineering-records", response_model=EngineeringRecordRead, status_code=201)
def create_engineering_record(
    payload: EngineeringRecordCreate,
    db: Session = Depends(get_db),
) -> EngineeringRecordRead:
    try:
        return svc.create_engineering_record(db, payload)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/engineering-records", response_model=list[EngineeringRecordRead])
def list_engineering_records(
    process_unit_id: uuid.UUID | None = None,
    module_id: uuid.UUID | None = None,
    record_type: EngineeringRecordType | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[EngineeringRecordRead]:
    items, _ = svc.list_engineering_records(
        db,
        process_unit_id=process_unit_id,
        module_id=module_id,
        record_type=record_type,
        limit=limit,
        offset=offset,
    )
    return items


@router.post("/document-templates", response_model=DocumentTemplateRead, status_code=201)
def create_document_template(
    payload: DocumentTemplateCreate,
    db: Session = Depends(get_db),
) -> DocumentTemplateRead:
    try:
        return svc.create_document_template(db, payload)
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.post(
    "/engineering-records/{record_id}/revisions",
    response_model=DocumentRevisionRead,
    status_code=201,
)
def create_document_revision(
    record_id: uuid.UUID,
    payload: DocumentRevisionCreate,
    db: Session = Depends(get_db),
) -> DocumentRevisionRead:
    try:
        return svc.create_document_revision(db, record_id, payload)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.get(
    "/engineering-records/{record_id}/revisions",
    response_model=list[DocumentRevisionRead],
)
def list_document_revisions(
    record_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> list[DocumentRevisionRead]:
    try:
        return svc.list_document_revisions(db, record_id)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.delete("/document-revisions/{revision_id}")
def delete_document_revision(
    revision_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    try:
        svc.delete_document_revision(db, revision_id)
        return {"deleted": True}
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.post("/control-integrity/reset-generated")
def reset_generated_workspace_data(db: Session = Depends(get_db)) -> dict[str, int]:
    return svc.reset_generated_workspace_data(db)


@router.post(
    "/control-integrity/seed-demo",
    response_model=SeedDemoResult,
    status_code=201,
)
def seed_demo_process_unit(db: Session = Depends(get_db)) -> SeedDemoResult:
    """Dev/demo helper: bootstrap a usable process unit + equipment + snapshot."""
    return SeedDemoResult(**svc.seed_demo_process_unit(db))


@router.post(
    "/engineering-records/{record_id}/generate-draft",
    response_model=DocumentRevisionRead,
    status_code=201,
)
def generate_document_draft(
    record_id: uuid.UUID,
    actor: str | None = "controls.engineer",
    db: Session = Depends(get_db),
) -> DocumentRevisionRead:
    try:
        return svc.generate_document_draft(db, record_id=record_id, actor=actor)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.post(
    "/engineering-records/{record_id}/generate-proposed-revision",
    response_model=DocumentRevisionRead,
    status_code=201,
)
def generate_proposed_revision(
    record_id: uuid.UUID,
    actor: str | None = "controls.engineer",
    db: Session = Depends(get_db),
) -> DocumentRevisionRead:
    try:
        return svc.generate_proposed_revision(db, record_id=record_id, actor=actor)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.post(
    "/modules/{module_id}/engineering-records/{record_type}/generate-draft",
    response_model=DocumentRevisionRead,
    status_code=201,
)
def generate_module_document_draft(
    module_id: uuid.UUID,
    record_type: EngineeringRecordType,
    actor: str | None = "controls.engineer",
    db: Session = Depends(get_db),
) -> DocumentRevisionRead:
    try:
        return svc.generate_document_draft(
            db,
            module_id=module_id,
            record_type=record_type,
            actor=actor,
        )
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.post(
    "/engineering-records/{record_id}/generate-ai-draft",
    response_model=AiDraftResponse,
    status_code=201,
)
def generate_ai_draft(
    record_id: uuid.UUID,
    payload: AiDraftRequest | None = None,
    db: Session = Depends(get_db),
) -> AiDraftResponse:
    body = payload or AiDraftRequest()
    try:
        revision, result = svc.generate_ai_document_draft(
            db,
            record_id=record_id,
            generation_mode=GenerationMode(body.generation_mode.value),
            selected_template_id=body.selected_template_id,
            user_notes=body.user_notes,
            selected_sections=body.selected_sections,
            actor=body.actor,
        )
        return _ai_draft_response(revision, result)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.post(
    "/modules/{module_id}/engineering-records/{record_type}/generate-ai-draft",
    response_model=AiDraftResponse,
    status_code=201,
)
def generate_module_ai_draft(
    module_id: uuid.UUID,
    record_type: EngineeringRecordType,
    payload: AiDraftRequest | None = None,
    db: Session = Depends(get_db),
) -> AiDraftResponse:
    body = payload or AiDraftRequest()
    try:
        revision, result = svc.generate_ai_document_draft(
            db,
            module_id=module_id,
            record_type=record_type,
            generation_mode=GenerationMode(body.generation_mode.value),
            selected_template_id=body.selected_template_id,
            user_notes=body.user_notes,
            selected_sections=body.selected_sections,
            actor=body.actor,
        )
        return _ai_draft_response(revision, result)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.get(
    "/process-units/{process_unit_id}/review-items",
    response_model=list[ReviewItemRead],
)
def list_process_unit_review_items(
    process_unit_id: uuid.UUID,
    status: ReviewItemStatus | None = None,
    db: Session = Depends(get_db),
) -> list[ReviewItemRead]:
    try:
        return svc.list_review_items_for_process_unit(db, process_unit_id, status=status)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.get("/snapshots/{snapshot_id}/diff", response_model=LogicDiffRead | None)
def get_snapshot_diff(
    snapshot_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> LogicDiffRead | None:
    return svc.get_snapshot_diff(db, snapshot_id)


@router.get("/snapshots/{snapshot_id}/parsed-extract")
def get_snapshot_parsed_extract(
    snapshot_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        snapshot = svc.get_logic_snapshot(db, snapshot_id)
        return snapshot.parsed_extract or {}
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/proposed-document-updates/{update_id}",
    response_model=ProposedDocumentUpdateRead,
)
def get_proposed_document_update(
    update_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> ProposedDocumentUpdateRead:
    try:
        return svc.get_proposed_document_update(db, update_id)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("/review-items/{review_item_id}/approve", response_model=ApprovalHistoryRead)
def approve_review_item(
    review_item_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    db: Session = Depends(get_db),
) -> ApprovalHistoryRead:
    try:
        _item, history = svc.approve_review_item(
            db,
            review_item_id,
            actor=payload.actor,
            comments=payload.comments,
        )
        return history
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("/review-items/{review_item_id}/reject", response_model=ApprovalHistoryRead)
def reject_review_item(
    review_item_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    db: Session = Depends(get_db),
) -> ApprovalHistoryRead:
    try:
        _item, history = svc.reject_review_item(
            db,
            review_item_id,
            actor=payload.actor,
            comments=payload.comments,
        )
        return history
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.post("/import-sources", response_model=ControlImportSourceRead, status_code=201)
def create_import_source(
    payload: ControlImportSourceCreate,
    db: Session = Depends(get_db),
) -> ControlImportSourceRead:
    try:
        return svc.create_import_source(db, payload)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc


@router.get("/import-sources", response_model=list[ControlImportSourceRead])
def list_import_sources(
    process_unit_id: uuid.UUID | None = None,
    module_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[ControlImportSourceRead]:
    items, _ = svc.list_import_sources(
        db,
        process_unit_id=process_unit_id,
        module_id=module_id,
        limit=limit,
        offset=offset,
    )
    return items


@router.post(
    "/import-sources/{source_id}/sync-upload",
    response_model=ImportSyncResult,
)
async def sync_import_source_from_upload(
    source_id: uuid.UUID,
    file: UploadFile = File(...),
    actor: str | None = None,
    db: Session = Depends(get_db),
) -> ImportSyncResult:
    content = await file.read()
    filename = file.filename or "uploaded_export.xml"
    try:
        return ImportSyncResult(
            **svc.run_import_sync_from_bytes(
                db,
                source_id=source_id,
                filename=filename,
                content=content,
                actor=actor,
            )
        )
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
    except svc.ConflictError as exc:
        raise _conflict(exc) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/modules/{module_id}/snapshots",
    response_model=LogicSnapshotRead,
    status_code=201,
)
def create_logic_snapshot(
    module_id: uuid.UUID,
    payload: LogicSnapshotCreate,
    db: Session = Depends(get_db),
) -> LogicSnapshotRead:
    try:
        return svc.create_logic_snapshot(db, module_id, payload)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/modules/{module_id}/narratives",
    response_model=ControlNarrativeRead,
    status_code=201,
)
def create_control_narrative(
    module_id: uuid.UUID,
    payload: ControlNarrativeCreate,
    db: Session = Depends(get_db),
) -> ControlNarrativeRead:
    try:
        return svc.create_control_narrative(db, module_id, payload)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/modules/{module_id}/governance-artifacts",
    response_model=GovernanceArtifactRead,
    status_code=201,
)
def create_governance_artifact(
    module_id: uuid.UUID,
    payload: GovernanceArtifactCreate,
    db: Session = Depends(get_db),
) -> GovernanceArtifactRead:
    try:
        return svc.create_governance_artifact(db, module_id, payload)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc


@router.post(
    "/modules/{module_id}/issues",
    response_model=ControlIssueRead,
    status_code=201,
)
def create_control_issue(
    module_id: uuid.UUID,
    payload: ControlIssueCreate,
    db: Session = Depends(get_db),
) -> ControlIssueRead:
    try:
        return svc.create_control_issue(db, module_id, payload)
    except svc.NotFoundError as exc:
        raise _not_found(exc) from exc
