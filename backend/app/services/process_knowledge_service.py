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
from app.models.control_model import ControlInstruction, ControlProject, ControlRoutine, ControlTag
from app.services.document_generation import (
    DocumentGenerationRequest,
    DocumentGenerationResult,
    GenerationMode,
    TemplateSpec,
    generate_document,
)
from app.services.document_generation.facts import build_source_facts
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


def delete_process_unit_if_empty(db: Session, process_unit_id: uuid.UUID) -> None:
    unit = get_process_unit(db, process_unit_id)
    has_children = any(
        [
            db.scalar(select(EquipmentModule.id).where(EquipmentModule.process_unit_id == process_unit_id).limit(1)),
            db.scalar(select(ControlImportSource.id).where(ControlImportSource.process_unit_id == process_unit_id).limit(1)),
            db.scalar(select(EngineeringRecord.id).where(EngineeringRecord.process_unit_id == process_unit_id).limit(1)),
            db.scalar(select(ReviewItem.id).where(ReviewItem.process_unit_id == process_unit_id).limit(1)),
        ]
    )
    if has_children:
        raise ConflictError("Process unit must be empty before it can be deleted.")
    db.delete(unit)
    _flush(db)


def delete_equipment_module(db: Session, module_id: uuid.UUID) -> None:
    module = get_equipment_module(db, module_id)
    records = list(module.engineering_records)
    approved = [
        revision
        for record in records
        for revision in record.revisions
        if revision.status == DocumentRevisionStatus.APPROVED
    ]
    if approved:
        raise ConflictError("Equipment with approved document revisions cannot be deleted.")
    for review in list(module.review_items):
        db.delete(review)
    for diff in list(module.logic_diffs):
        db.delete(diff)
    for source in list(module.import_sources):
        for run in list(source.runs):
            db.delete(run)
        db.delete(source)
    db.delete(module)
    _flush(db)


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


def delete_document_revision(db: Session, revision_id: uuid.UUID) -> None:
    revision = db.get(DocumentRevision, revision_id)
    if revision is None:
        raise NotFoundError("Document revision not found.")
    if revision.status == DocumentRevisionStatus.APPROVED:
        raise ConflictError("Approved revisions cannot be deleted from this workspace.")
    record = revision.engineering_record
    for review in list(record.review_items):
        if review.status in {ReviewItemStatus.OPEN, ReviewItemStatus.NEEDS_MANUAL_REVIEW}:
            db.delete(review)
    db.delete(revision)
    _flush(db)
    remaining = list_document_revisions(db, record.id)
    if not remaining:
        record.status = EngineeringRecordStatus.NEEDING_SETUP
    elif any(item.status == DocumentRevisionStatus.APPROVED for item in remaining):
        record.status = EngineeringRecordStatus.ACTIVE
    else:
        record.status = EngineeringRecordStatus.NEEDS_REVIEW
    _flush(db)


def reset_generated_workspace_data(db: Session) -> dict[str, int]:
    revisions_deleted = 0
    reviews_deleted = 0
    proposals_deleted = 0
    for review in list(
        db.scalars(
            select(ReviewItem).where(
                ReviewItem.status.in_(
                    [ReviewItemStatus.OPEN, ReviewItemStatus.NEEDS_MANUAL_REVIEW]
                )
            )
        )
    ):
        db.delete(review)
        reviews_deleted += 1
    for proposal in list(
        db.scalars(
            select(ProposedDocumentUpdate).where(
                ProposedDocumentUpdate.status.in_(
                    [ProposedUpdateStatus.PROPOSED, ProposedUpdateStatus.REJECTED]
                )
            )
        )
    ):
        db.delete(proposal)
        proposals_deleted += 1
    for revision in list(
        db.scalars(
            select(DocumentRevision).where(
                DocumentRevision.status.in_(
                    [DocumentRevisionStatus.DRAFT, DocumentRevisionStatus.REJECTED]
                )
            )
        )
    ):
        db.delete(revision)
        revisions_deleted += 1
    for record in list(db.scalars(select(EngineeringRecord))):
        remaining = list(record.revisions)
        if not remaining:
            record.status = EngineeringRecordStatus.NEEDING_SETUP
        elif any(item.status == DocumentRevisionStatus.APPROVED for item in remaining):
            record.status = EngineeringRecordStatus.ACTIVE
        else:
            record.status = EngineeringRecordStatus.NEEDS_REVIEW
    _flush(db)
    return {
        "revisions_deleted": revisions_deleted,
        "reviews_deleted": reviews_deleted,
        "proposals_deleted": proposals_deleted,
    }


DEMO_SEED_EXTRACT: dict[str, Any] = {
    "connector": "INTELLI demo seed",
    "project_name": "Demo_Converting",
    "controller_name": "DEMO_CTRL",
    "programs": ["BATCH"],
    "routines": ["MIX", "DISCHARGE"],
    "tags": ["VFD_AGIT", "XV_DISCH", "PREP_PMP", "LVL_HI_ALM", "MIX_TIME_SP", "PREP_READY"],
    "devices": ["VFD_AGIT", "XV_DISCH", "PREP_PMP"],
    "setpoints": ["MIX_TIME_SP"],
    "alarms": ["LVL_HI_ALM"],
    "permissives": ["PREP_READY"],
    "operator_prompts": ["OAR_CONFIRM"],
    "commands": {
        "MIX": {
            "steps": ["SETUP_MIX", "INIT_MIX", "MIXING", "MIXING_END"],
            "devices": ["VFD_AGIT", "PREP_PMP"],
            "setpoints": ["MIX_TIME_SP"],
            "tags": ["VFD_AGIT", "MIX_TIME_SP", "PREP_READY"],
            "routines": ["MIX"],
        },
        "DISCHARGE": {
            "steps": ["OPEN_XV_DISCH", "CONFIRM_EMPTY"],
            "devices": ["XV_DISCH"],
            "tags": ["XV_DISCH", "LVL_HI_ALM"],
            "routines": ["DISCHARGE"],
        },
    },
}


def seed_demo_process_unit(db: Session) -> dict[str, Any]:
    """Bootstrap a demo process unit + equipment + logic snapshot for the MVP demo.

    Non-destructive: each call creates a new, uniquely named process unit so the
    full Control Integrity flow (generate -> review -> approve) can be exercised
    from a fresh database. This reuses the existing create helpers and stores a
    deterministic ``parsed_extract`` so deterministic and AI-assisted draft
    generation produce meaningful content. It does not touch the document
    generation architecture.
    """
    suffix = uuid.uuid4().hex[:6]
    unit = create_process_unit(
        db,
        ProcessUnitCreate(
            name=f"Demo Converting Area {suffix}",
            area="Demo Area",
            description="Demo process unit created by the Control Integrity seed helper.",
        ),
    )
    module = create_equipment_module(
        db,
        unit.id,
        EquipmentModuleCreate(
            name=f"Demo Filtrate Separator {suffix}",
            module_type="Equipment Module",
            description="Demo equipment created by the Control Integrity seed helper.",
        ),
    )
    snapshot = create_logic_snapshot(
        db,
        module.id,
        LogicSnapshotCreate(
            source_type=SnapshotSourceType.FHX,
            source_filename=f"DEMO_FILTRATE_SEPARATOR_{suffix}.fhx",
            file_hash=f"demo-{suffix}",
            raw_project_id=f"demo-{suffix}",
            parsed_extract=dict(DEMO_SEED_EXTRACT),
            notes="Seeded demo logic snapshot.",
            created_by="demo.seed",
        ),
    )
    _flush(db)
    return {
        "process_unit_id": unit.id,
        "module_id": module.id,
        "snapshot_id": snapshot.id,
        "process_unit_name": unit.name,
        "module_name": module.name,
    }


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


RECORD_TYPE_LABELS: dict[EngineeringRecordType, str] = {
    EngineeringRecordType.CONTROL_NARRATIVE: "Control Narrative",
    EngineeringRecordType.IO_LIST: "IO List",
    EngineeringRecordType.CAUSE_EFFECT_MATRIX: "Cause and Effect Matrix",
    EngineeringRecordType.ALARM_RATIONALIZATION: "Alarm Rationalization",
    EngineeringRecordType.MOC: "MOC",
    EngineeringRecordType.KNOWLEDGE_ISSUE: "Knowledge Base",
    EngineeringRecordType.ENGINEERING_NOTE: "Engineering Note",
}

DEFAULT_TEMPLATE_SECTIONS: dict[EngineeringRecordType, list[str]] = {
    EngineeringRecordType.CONTROL_NARRATIVE: [
        "Purpose",
        "Equipment controlled",
        "Commands",
        "Sequence description",
        "Permissives and interlocks",
        "Setpoints",
        "Alarms/operator prompts",
        "Related documents",
        "Revision history",
    ],
    EngineeringRecordType.IO_LIST: [
        "Tag",
        "Description",
        "Direction",
        "Data type",
        "Source",
        "Related module",
    ],
    EngineeringRecordType.CAUSE_EFFECT_MATRIX: [
        "Cause",
        "Effect",
        "Condition",
        "Action",
        "Related tag/module",
    ],
    EngineeringRecordType.ALARM_RATIONALIZATION: [
        "Alarm",
        "Priority",
        "Cause",
        "Operator response",
        "Consequence",
        "Related tag/module",
    ],
}


def _active_template_for_record_type(
    db: Session,
    record_type: EngineeringRecordType,
) -> DocumentTemplate | None:
    return db.scalar(
        select(DocumentTemplate)
        .where(
            DocumentTemplate.record_type == record_type,
            DocumentTemplate.active == True,  # noqa: E712 - SQLAlchemy comparison
        )
        .order_by(DocumentTemplate.updated_at.desc(), DocumentTemplate.created_at.desc())
        .limit(1)
    )


def _template_sections(
    record_type: EngineeringRecordType,
    template: DocumentTemplate | None,
) -> list[str]:
    schema_sections = []
    if template is not None:
        raw_sections = template.template_schema.get("sections")
        if isinstance(raw_sections, list):
            schema_sections = [str(item).strip() for item in raw_sections if str(item).strip()]
        if schema_sections:
            return schema_sections
        if template.template_body:
            headings = [
                line.lstrip("#").strip()
                for line in template.template_body.splitlines()
                if line.strip().startswith("#") and line.lstrip("#").strip()
            ]
            if headings:
                return headings
    return DEFAULT_TEMPLATE_SECTIONS.get(
        record_type,
        ["Summary", "Details", "Related tag/module", "Revision history"],
    )


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        items: list[str] = []
        for key, item in value.items():
            if isinstance(item, dict):
                label = item.get("name") or item.get("tag") or item.get("description") or key
                items.append(str(label))
            else:
                items.append(str(key if item in (None, True, "") else item))
        return [item for item in items if item]
    if isinstance(value, list):
        items = []
        for item in value:
            if isinstance(item, dict):
                label = item.get("name") or item.get("tag") or item.get("description") or item.get("id")
                if label:
                    items.append(str(label))
            elif item is not None:
                items.append(str(item))
        return [item for item in items if item]
    return [str(value)]


def _commands_from_extract(parsed_extract: dict[str, Any]) -> dict[str, Any]:
    commands = parsed_extract.get("commands")
    return commands if isinstance(commands, dict) else {}


def _all_command_values(parsed_extract: dict[str, Any], key: str) -> list[str]:
    values: list[str] = []
    for command in _commands_from_extract(parsed_extract).values():
        if isinstance(command, dict):
            values.extend(_as_string_list(command.get(key)))
    return sorted(set(values))


def _tags_from_extract(parsed_extract: dict[str, Any]) -> list[str]:
    tags = _as_string_list(parsed_extract.get("tags"))
    tags.extend(_as_string_list(parsed_extract.get("tag")))
    tags.extend(_all_command_values(parsed_extract, "tags"))
    tags.extend(_all_command_values(parsed_extract, "devices"))
    return sorted(set(tags))


def _access_entries_from_extract(parsed_extract: dict[str, Any], key: str) -> list[str]:
    entries: list[str] = []
    for item in parsed_extract.get(key) or []:
        if not isinstance(item, dict):
            continue
        tag = item.get("tag")
        if not tag:
            continue
        location = " / ".join(
            str(piece)
            for piece in (item.get("program"), item.get("routine"))
            if piece
        )
        entries.append(f"{tag} ({location})" if location else str(tag))
    return sorted(set(entries))


def _format_list(items: list[str]) -> str:
    if not items:
        return "Needs engineer input"
    return "\n".join(f"- {item}" for item in items)


def _section_content(
    *,
    section: str,
    record_type: EngineeringRecordType,
    module: EquipmentModule,
    snapshot: LogicSnapshot,
    parsed_extract: dict[str, Any],
) -> tuple[str, list[str], bool]:
    normalized = section.lower()
    commands = _commands_from_extract(parsed_extract)
    command_names = sorted(commands.keys())
    tags = _tags_from_extract(parsed_extract)
    routines = _as_string_list(parsed_extract.get("routines"))
    setpoints = _as_string_list(parsed_extract.get("setpoints"))
    setpoints.extend(_all_command_values(parsed_extract, "setpoints"))
    alarms = _as_string_list(parsed_extract.get("alarms"))
    prompts = _as_string_list(parsed_extract.get("operator_prompts"))
    permissives = _as_string_list(parsed_extract.get("permissives"))
    interlocks = _as_string_list(parsed_extract.get("interlocks"))
    conditions = _as_string_list(parsed_extract.get("conditions"))
    steps = _all_command_values(parsed_extract, "steps")
    outputs = _as_string_list(parsed_extract.get("outputs"))
    actions = _as_string_list(parsed_extract.get("actions"))
    reads = _access_entries_from_extract(parsed_extract, "reads")
    writes = _access_entries_from_extract(parsed_extract, "writes")

    if "purpose" in normalized:
        purpose_facts = _as_string_list(parsed_extract.get("programs")) or routines
        return (
            f"Describe the control intent for {module.name} using snapshot {snapshot.source_filename}.",
            [module.name, snapshot.source_filename, *purpose_facts],
            False,
        )
    if "equipment controlled" in normalized or "related module" in normalized:
        facts = [module.name, *tags, *outputs]
        return (_format_list(facts), facts, not tags)
    if "commands" in normalized:
        facts = [*command_names, *_all_command_values(parsed_extract, "devices"), *outputs, *actions]
        return (_format_list(facts), facts, not facts)
    if "sequence" in normalized:
        facts = [*routines, *steps, *reads, *writes, *outputs] or command_names
        return (_format_list(facts), facts, not facts)
    if "permissive" in normalized or "interlock" in normalized or "condition" in normalized:
        facts = [*permissives, *interlocks, *conditions]
        return (_format_list(facts), facts, not facts)
    if "setpoint" in normalized:
        return (_format_list(setpoints), setpoints, not setpoints)
    if "alarm" in normalized:
        facts = [*alarms, *prompts]
        return (_format_list(facts), facts, not facts)
    if record_type == EngineeringRecordType.IO_LIST and (normalized in {"tag", "tags"} or "tag" in normalized):
        facts = _as_string_list(parsed_extract.get("io_rows")) or tags
        return (_format_list(facts), facts, not facts)
    if normalized in {"tag", "related tag/module"} or "tag" in normalized:
        return (_format_list(tags), tags, not tags)
    if "description" in normalized:
        facts = _as_string_list(parsed_extract.get("descriptions")) or _as_string_list(
            parsed_extract.get("description")
        )
        return (_format_list(facts), facts, not facts)
    if "direction" in normalized:
        facts = _as_string_list(parsed_extract.get("directions")) or _as_string_list(
            parsed_extract.get("direction")
        )
        return (_format_list(facts), facts, not facts)
    if "data type" in normalized:
        facts = _as_string_list(parsed_extract.get("data_types")) or _as_string_list(
            parsed_extract.get("data_type")
        )
        return (_format_list(facts), facts, not facts)
    if "source" in normalized:
        facts = _as_string_list(parsed_extract.get("sources"))
        facts.extend([snapshot.source_filename])
        connector = parsed_extract.get("connector")
        if connector:
            facts.append(str(connector))
        return (_format_list(facts), facts, False)
    if "cause" in normalized:
        facts = _as_string_list(parsed_extract.get("causes")) or [*alarms, *conditions]
        return (_format_list(facts), facts, not facts)
    if "effect" in normalized or "action" in normalized:
        facts = _as_string_list(parsed_extract.get("effects")) or _as_string_list(
            parsed_extract.get("actions")
        )
        return (_format_list(facts), facts, not facts)
    if "priority" in normalized:
        facts = _as_string_list(parsed_extract.get("priorities")) or _as_string_list(
            parsed_extract.get("priority")
        )
        return (_format_list(facts), facts, not facts)
    if "operator response" in normalized:
        facts = _as_string_list(parsed_extract.get("operator_responses"))
        return (_format_list(facts), facts, not facts)
    if "consequence" in normalized:
        facts = _as_string_list(parsed_extract.get("consequences"))
        return (_format_list(facts), facts, not facts)
    if "revision history" in normalized:
        facts = [f"Initial deterministic draft generated from {snapshot.source_filename}."]
        return (_format_list(facts), facts, False)
    if "related document" in normalized:
        return ("Needs engineer input", [], True)
    return ("Needs engineer input", [], True)


def _render_document_body(
    *,
    record: EngineeringRecord,
    module: EquipmentModule,
    snapshot: LogicSnapshot,
    template: DocumentTemplate | None,
) -> tuple[str, dict[str, Any]]:
    parsed_extract = snapshot.parsed_extract or {}
    sections = []
    lines = [f"# {record.title}", ""]
    for section in _template_sections(record.record_type, template):
        content, facts, needs_input = _section_content(
            section=section,
            record_type=record.record_type,
            module=module,
            snapshot=snapshot,
            parsed_extract=parsed_extract,
        )
        lines.extend([f"## {section}", "", content, ""])
        sections.append(
            {
                "title": section,
                "content": content,
                "facts": facts,
                "needs_engineer_input": needs_input,
            }
        )
    structured = {
        "generation": "deterministic_template",
        "template": {
            "id": str(template.id) if template else None,
            "name": template.name if template else "INTELLI default template",
            "source": "company" if template else "intelli_default",
        },
        "source_snapshot_id": str(snapshot.id),
        "source_filename": snapshot.source_filename,
        "record_type": record.record_type.value,
        "sections": sections,
    }
    return "\n".join(lines).strip() + "\n", structured


def _next_revision_label(
    db: Session,
    record_id: uuid.UUID,
    *,
    proposed: bool = False,
) -> str:
    revisions = list(
        db.scalars(
            select(DocumentRevision)
            .where(DocumentRevision.engineering_record_id == record_id)
            .order_by(DocumentRevision.created_at.asc())
        )
    )
    if proposed:
        approved = [
            item
            for item in revisions
            if item.status == DocumentRevisionStatus.APPROVED and item.revision
        ]
        major = "1"
        if approved:
            major = approved[-1].revision.split(".")[0] or "1"
        return f"{major}.{len(revisions) + 1}"
    return f"0.{len(revisions) + 1}"


def _find_or_create_engineering_record_for_module(
    db: Session,
    *,
    module_id: uuid.UUID,
    record_type: EngineeringRecordType,
) -> EngineeringRecord:
    module = get_equipment_module(db, module_id)
    existing = db.scalar(
        select(EngineeringRecord)
        .where(
            EngineeringRecord.module_id == module_id,
            EngineeringRecord.record_type == record_type,
        )
        .order_by(EngineeringRecord.created_at.asc())
        .limit(1)
    )
    if existing is not None:
        return existing
    record = EngineeringRecord(
        process_unit_id=module.process_unit_id,
        module_id=module.id,
        record_type=record_type,
        title=_default_record_title(record_type, module),
        status=EngineeringRecordStatus.NEEDING_SETUP,
        description="Created by deterministic document draft generation.",
        metadata_json={"created_by": "deterministic_document_generation"},
    )
    db.add(record)
    _flush(db)
    return record


def _latest_logic_diff_for_snapshot(db: Session, snapshot_id: uuid.UUID) -> LogicDiff | None:
    return db.scalar(
        select(LogicDiff)
        .where(LogicDiff.current_snapshot_id == snapshot_id)
        .order_by(LogicDiff.created_at.desc())
        .limit(1)
    )


def _create_generation_review_item(
    db: Session,
    *,
    record: EngineeringRecord,
    snapshot: LogicSnapshot,
    title: str,
    reason: str,
    proposed_update: ProposedDocumentUpdate | None = None,
) -> ReviewItem:
    logic_diff = _latest_logic_diff_for_snapshot(db, snapshot.id)
    review = ReviewItem(
        process_unit_id=record.process_unit_id,
        module_id=record.module_id,
        engineering_record_id=record.id,
        logic_snapshot_id=snapshot.id,
        logic_diff_id=logic_diff.id if logic_diff else None,
        proposed_document_update_id=proposed_update.id if proposed_update else None,
        title=title,
        reason=reason,
        status=ReviewItemStatus.OPEN,
    )
    db.add(review)
    _flush(db)
    return review


def generate_document_draft(
    db: Session,
    *,
    record_id: uuid.UUID | None = None,
    module_id: uuid.UUID | None = None,
    record_type: EngineeringRecordType | None = None,
    actor: str | None = "controls.engineer",
) -> DocumentRevision:
    if record_id is not None:
        record = get_engineering_record(db, record_id)
    elif module_id is not None and record_type is not None:
        record = _find_or_create_engineering_record_for_module(
            db,
            module_id=module_id,
            record_type=record_type,
        )
    else:
        raise ConflictError("Draft generation requires record_id or module_id with record_type.")
    if record.module_id is None:
        raise ConflictError("Draft generation requires an equipment module.")
    module = get_equipment_module(db, record.module_id)
    snapshot = _latest_snapshot_for_module(db, module.id)
    if snapshot is None:
        raise ConflictError("Draft generation requires at least one logic snapshot.")

    template = _active_template_for_record_type(db, record.record_type)
    body_markdown, structured_content = _render_document_body(
        record=record,
        module=module,
        snapshot=snapshot,
        template=template,
    )
    revision = DocumentRevision(
        engineering_record_id=record.id,
        source_logic_snapshot_id=snapshot.id,
        template_id=template.id if template else None,
        revision=_next_revision_label(db, record.id),
        status=DocumentRevisionStatus.DRAFT,
        body_markdown=body_markdown,
        structured_content=structured_content,
        created_by=actor,
    )
    db.add(revision)
    record.status = EngineeringRecordStatus.NEEDS_REVIEW
    _flush(db)
    _create_generation_review_item(
        db,
        record=record,
        snapshot=snapshot,
        title=f"Review draft {record.title}",
        reason=f"Deterministic draft generated for {RECORD_TYPE_LABELS.get(record.record_type, record.record_type.value)}.",
    )
    return revision


def generate_proposed_revision(
    db: Session,
    *,
    record_id: uuid.UUID,
    actor: str | None = "controls.engineer",
) -> DocumentRevision:
    record = get_engineering_record(db, record_id)
    if record.module_id is None:
        raise ConflictError("Proposed revision generation requires an equipment module.")
    module = get_equipment_module(db, record.module_id)
    snapshot = _latest_snapshot_for_module(db, module.id)
    if snapshot is None:
        raise ConflictError("Proposed revision generation requires at least one logic snapshot.")
    approved_revision = latest_approved_revision(db, record.id)
    if approved_revision is None:
        raise ConflictError("Proposed revision generation requires an approved revision.")

    template = _active_template_for_record_type(db, record.record_type)
    body_markdown, structured_content = _render_document_body(
        record=record,
        module=module,
        snapshot=snapshot,
        template=template,
    )
    logic_diff = _latest_logic_diff_for_snapshot(db, snapshot.id)
    proposed_update = ProposedDocumentUpdate(
        engineering_record_id=record.id,
        source_logic_snapshot_id=snapshot.id,
        source_logic_diff_id=logic_diff.id if logic_diff else None,
        base_document_revision_id=approved_revision.id,
        proposed_body=body_markdown,
        proposed_structured_changes={
            **structured_content,
            "base_revision_id": str(approved_revision.id),
            "base_revision": approved_revision.revision,
        },
        confidence="deterministic",
        status=ProposedUpdateStatus.PROPOSED,
    )
    db.add(proposed_update)
    _flush(db)

    revision = DocumentRevision(
        engineering_record_id=record.id,
        source_logic_snapshot_id=snapshot.id,
        template_id=template.id if template else None,
        revision=_next_revision_label(db, record.id, proposed=True),
        status=DocumentRevisionStatus.PROPOSED,
        body_markdown=body_markdown,
        structured_content={
            **structured_content,
            "proposed_document_update_id": str(proposed_update.id),
            "base_revision_id": str(approved_revision.id),
        },
        created_by=actor,
    )
    db.add(revision)
    record.status = EngineeringRecordStatus.NEEDS_REVIEW
    _flush(db)
    _create_generation_review_item(
        db,
        record=record,
        snapshot=snapshot,
        proposed_update=proposed_update,
        title=f"Review proposed revision {record.title}",
        reason=f"Deterministic proposed revision generated from latest snapshot for {record.title}.",
    )
    return revision


def _resolve_record_for_generation(
    db: Session,
    *,
    record_id: uuid.UUID | None,
    module_id: uuid.UUID | None,
    record_type: EngineeringRecordType | None,
) -> EngineeringRecord:
    if record_id is not None:
        return get_engineering_record(db, record_id)
    if module_id is not None and record_type is not None:
        return _find_or_create_engineering_record_for_module(
            db,
            module_id=module_id,
            record_type=record_type,
        )
    raise ConflictError("Draft generation requires record_id or module_id with record_type.")


def generate_ai_document_draft(
    db: Session,
    *,
    record_id: uuid.UUID | None = None,
    module_id: uuid.UUID | None = None,
    record_type: EngineeringRecordType | None = None,
    generation_mode: GenerationMode = GenerationMode.LLM_ASSISTED,
    selected_template_id: uuid.UUID | None = None,
    user_notes: str | None = None,
    selected_sections: list[str] | None = None,
    actor: str | None = "controls.engineer",
    provider: Any = None,
) -> tuple[DocumentRevision, DocumentGenerationResult]:
    """Generate a first-draft engineering document and persist it as a DRAFT.

    Creates the EngineeringRecord if missing, always produces a DRAFT revision
    (never auto-approves), preserves any existing approved revision, and opens a
    ReviewItem. ``provider`` allows dependency injection for tests.
    """

    record = _resolve_record_for_generation(
        db,
        record_id=record_id,
        module_id=module_id,
        record_type=record_type,
    )
    if record.module_id is None:
        raise ConflictError("Draft generation requires an equipment module.")
    module = get_equipment_module(db, record.module_id)
    snapshot = _latest_snapshot_for_module(db, module.id)
    if snapshot is None:
        raise ConflictError("Draft generation requires at least one logic snapshot.")

    if selected_template_id is not None:
        template = db.get(DocumentTemplate, selected_template_id)
        if template is None:
            raise NotFoundError("Document template not found.")
        if template.record_type != record.record_type:
            raise ConflictError("Selected template does not match the record type.")
    else:
        template = _active_template_for_record_type(db, record.record_type)

    sections = _template_sections(record.record_type, template)
    parsed_extract = snapshot.parsed_extract or {}
    facts = build_source_facts(
        record_type=record.record_type.value,
        parsed_extract=parsed_extract,
        source_snapshot_id=str(snapshot.id),
    )

    if generation_mode == GenerationMode.DETERMINISTIC_TEMPLATE:
        # Reuse the proven deterministic renderer, then attach the AI output
        # contract (facts_used / missing_facts / etc.) on top of it.
        body_markdown, structured_content = _render_document_body(
            record=record,
            module=module,
            snapshot=snapshot,
            template=template,
        )
        facts_used = [fact for fact in facts if fact.present]
        missing_facts = [fact for fact in facts if not fact.present]
        warnings: list[str] = []
        if missing_facts:
            warnings.append(
                f"{len(missing_facts)} fact group(s) missing; marked as 'Needs engineer input'."
            )
        structured_content = {
            **structured_content,
            "provider_name": "deterministic_template",
            "facts_used": [fact.to_dict() for fact in facts_used],
            "missing_facts": [fact.to_dict() for fact in missing_facts],
            "assumptions": [],
            "confidence": "deterministic",
            "warnings": warnings,
            "user_notes_provided": bool(user_notes),
        }
        result = DocumentGenerationResult(
            body_markdown=body_markdown,
            structured_content=structured_content,
            source_snapshot_id=str(snapshot.id),
            template_id=str(template.id) if template else None,
            facts_used=facts_used,
            missing_facts=missing_facts,
            assumptions=[],
            confidence="deterministic",
            warnings=warnings,
            mode=GenerationMode.DETERMINISTIC_TEMPLATE,
            provider_name="deterministic_template",
        )
    else:
        approved_revision = latest_approved_revision(db, record.id)
        approved_excerpt = None
        approved_label = None
        if approved_revision is not None:
            approved_excerpt = (approved_revision.body_markdown or "")[:4000] or None
            approved_label = approved_revision.revision
        template_spec = TemplateSpec(
            id=str(template.id) if template else None,
            name=template.name if template else "INTELLI default template",
            source="company" if template else "intelli_default",
            sections=sections,
        )
        request = DocumentGenerationRequest(
            record_type=record.record_type.value,
            document_title=record.title,
            mode=GenerationMode.LLM_ASSISTED,
            module_name=module.name,
            source_snapshot_id=str(snapshot.id),
            source_filename=snapshot.source_filename,
            template=template_spec,
            facts=facts,
            selected_sections=selected_sections,
            user_notes=user_notes,
            approved_revision_excerpt=approved_excerpt,
            approved_revision_label=approved_label,
        )
        result = generate_document(request, provider=provider)

    revision = DocumentRevision(
        engineering_record_id=record.id,
        source_logic_snapshot_id=snapshot.id,
        template_id=template.id if template else None,
        revision=_next_revision_label(db, record.id),
        status=DocumentRevisionStatus.DRAFT,
        body_markdown=result.body_markdown,
        structured_content=result.structured_content,
        created_by=actor,
    )
    db.add(revision)
    record.status = EngineeringRecordStatus.NEEDS_REVIEW
    _flush(db)
    _create_generation_review_item(
        db,
        record=record,
        snapshot=snapshot,
        title=f"Review AI draft {record.title}",
        reason=(
            f"{result.mode.value} draft generated for "
            f"{RECORD_TYPE_LABELS.get(record.record_type, record.record_type.value)} "
            f"(provider: {result.provider_name})."
        ),
    )
    return revision, result


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


def _model_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw) if raw is not None else ""


def _append_unique(items: list[Any], value: Any) -> None:
    if value in (None, "", [], {}):
        return
    if value not in items:
        items.append(value)


def _tag_ref_name(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("tag_name") or value.get("raw") or value.get("name") or value.get("id")
    return getattr(value, "tag_name", None) or getattr(value, "raw", None) or getattr(value, "name", None)


def _control_object_summary(obj: Any) -> dict[str, Any]:
    attributes = _model_value(obj, "attributes", {}) or {}
    return {
        "id": _model_value(obj, "id"),
        "name": _model_value(obj, "name"),
        "object_type": _enum_value(_model_value(obj, "object_type")),
        "role": _enum_value(_model_value(obj, "role")),
        "description": _model_value(obj, "description"),
        "source_location": _model_value(obj, "source_location"),
        "data_type": attributes.get("data_type") if isinstance(attributes, dict) else None,
        "attributes": attributes if isinstance(attributes, dict) else {},
    }


def _relationship_summary(rel: Any, object_names: dict[str, str]) -> dict[str, Any]:
    source_id = str(_model_value(rel, "source_id", ""))
    target_id = str(_model_value(rel, "target_id", ""))
    return {
        "source_id": source_id,
        "source": object_names.get(source_id, source_id),
        "target_id": target_id,
        "target": object_names.get(target_id, target_id),
        "relationship_type": _enum_value(_model_value(rel, "relationship_type")),
        "write_behavior": _enum_value(_model_value(rel, "write_behavior")),
        "logic_condition": _model_value(rel, "logic_condition"),
    }


def _tag_summary(tag: ControlTag, *, source: str, program: str | None = None) -> dict[str, Any]:
    return {
        "tag": tag.name,
        "name": tag.name,
        "description": tag.description,
        "data_type": tag.data_type,
        "source": source,
        "program": program,
        "scope": tag.scope,
        "alias_for": tag.alias_for,
    }


def _routine_outputs(routine: ControlRoutine) -> list[str]:
    outputs: list[str] = []
    for instruction in routine.instructions:
        _append_unique(outputs, instruction.output)
        for write in getattr(instruction, "writes", []) or []:
            _append_unique(outputs, _tag_ref_name(write))
    for rung in routine.ladder_rungs:
        for write in rung.writes:
            _append_unique(outputs, _tag_ref_name(write.target))
    for block in [*routine.logic_blocks, *routine.fbd_blocks]:
        for write in block.writes:
            _append_unique(outputs, _tag_ref_name(write.target))
    return outputs


def _routine_reads(routine: ControlRoutine) -> list[str]:
    reads: list[str] = []
    for instruction in routine.instructions:
        for operand in instruction.operands:
            if operand != instruction.output:
                _append_unique(reads, operand)
    for rung in routine.ladder_rungs:
        for read in rung.reads:
            _append_unique(reads, _tag_ref_name(read.tag))
    for block in [*routine.logic_blocks, *routine.fbd_blocks]:
        for read in block.reads:
            _append_unique(reads, _tag_ref_name(read.tag))
    return reads


def _infer_tag_direction(tag_name: str, reads: list[dict[str, Any]], writes: list[dict[str, Any]]) -> str | None:
    is_read = any(item.get("tag") == tag_name for item in reads)
    is_write = any(item.get("tag") == tag_name for item in writes)
    if is_read and is_write:
        return "read_write"
    if is_write:
        return "output"
    if is_read:
        return "input"
    return None


def _looks_like_alarm(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in ("ALM", "ALARM", "FAULT", "FLT", "WARN", "TRIP"))


def _looks_like_setpoint(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in ("_SP", "SP_", "SETPOINT", "PRESET"))


def _looks_like_permissive(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in ("PERM", "PERMISSIVE", "READY", "OK"))


def _looks_like_interlock(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in ("INTLK", "INTERLOCK", "ESTOP", "STOP", "TRIP"))


def _looks_like_operator_prompt(name: str) -> bool:
    upper = name.upper()
    return any(token in upper for token in ("PROMPT", "MSG", "MESSAGE", "OAR", "OPERATOR"))


def _looks_like_device(name: str) -> bool:
    upper = name.upper()
    return any(
        token in upper
        for token in ("PMP", "PUMP", "MTR", "MOTOR", "VFD", "XV", "VALVE", "AGIT", "FAN", "CONV")
    )


def _instruction_record(
    instruction: ControlInstruction,
    *,
    controller: str,
    program: str,
    routine: str,
) -> dict[str, Any]:
    return {
        "instruction_type": instruction.instruction_type,
        "operands": instruction.operands,
        "output": instruction.output,
        "raw_text": instruction.raw_text,
        "controller": controller,
        "program": program,
        "routine": routine,
        "rung_number": instruction.rung_number,
    }


def build_process_knowledge_extract(
    project: ControlProject,
    normalized: dict[str, Any],
) -> dict[str, Any]:
    """Build deterministic document-generation facts from parsed and normalized logic."""

    control_objects = list(normalized.get("control_objects") or [])
    relationships_raw = list(normalized.get("relationships") or [])
    object_summaries = [_control_object_summary(obj) for obj in control_objects]
    object_names = {
        str(item["id"]): str(item.get("name") or item["id"])
        for item in object_summaries
        if item.get("id")
    }
    normalized_relationships = [
        _relationship_summary(rel, object_names) for rel in relationships_raw
    ]

    programs: list[str] = []
    routines: list[str] = []
    routines_by_program: dict[str, list[str]] = {}
    tags: list[dict[str, Any]] = []
    reads: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []
    commands: dict[str, dict[str, Any]] = {}
    sequence_steps: list[str] = []
    devices: list[str] = []
    setpoints: list[str] = []
    permissives: list[str] = []
    interlocks: list[str] = []
    outputs: list[str] = []
    alarms: list[str] = []
    operator_prompts: list[str] = []
    conditions: list[str] = []
    actions: list[str] = []

    controller_name = project.controllers[0].name if project.controllers else None
    for controller in project.controllers:
        for tag in controller.controller_tags:
            tags.append(_tag_summary(tag, source="controller"))
        for program in controller.programs:
            _append_unique(programs, program.name)
            routines_by_program.setdefault(program.name, [])
            for tag in program.tags:
                tags.append(_tag_summary(tag, source="program", program=program.name))
            for routine in program.routines:
                _append_unique(routines, routine.name)
                _append_unique(routines_by_program[program.name], routine.name)
                routine_reads = _routine_reads(routine)
                routine_outputs = _routine_outputs(routine)
                for tag_name in routine_reads:
                    reads.append({"tag": tag_name, "program": program.name, "routine": routine.name})
                    if _looks_like_permissive(tag_name):
                        _append_unique(permissives, tag_name)
                    if _looks_like_interlock(tag_name):
                        _append_unique(interlocks, tag_name)
                    if _looks_like_alarm(tag_name):
                        _append_unique(alarms, tag_name)
                    _append_unique(conditions, tag_name)
                for tag_name in routine_outputs:
                    writes.append({"tag": tag_name, "program": program.name, "routine": routine.name})
                    _append_unique(outputs, tag_name)
                    _append_unique(actions, tag_name)
                    if _looks_like_device(tag_name):
                        _append_unique(devices, tag_name)
                    if _looks_like_setpoint(tag_name):
                        _append_unique(setpoints, tag_name)
                    if _looks_like_alarm(tag_name):
                        _append_unique(alarms, tag_name)
                    if _looks_like_operator_prompt(tag_name):
                        _append_unique(operator_prompts, tag_name)
                if routine.logic_sequence:
                    for item in routine.logic_sequence.items:
                        _append_unique(sequence_steps, item)
                for step in routine.sfc_steps:
                    _append_unique(sequence_steps, step.name or step.id)
                for instruction in routine.instructions:
                    output = instruction.output
                    if output:
                        command = commands.setdefault(
                            output,
                            {
                                "steps": [],
                                "devices": [],
                                "setpoints": [],
                                "tags": [],
                                "routines": [],
                            },
                        )
                        _append_unique(command["routines"], routine.name)
                        _append_unique(command["steps"], instruction.raw_text or instruction.instruction_type)
                        for operand in instruction.operands:
                            _append_unique(command["tags"], operand)
                            if _looks_like_device(operand):
                                _append_unique(command["devices"], operand)
                            if _looks_like_setpoint(operand):
                                _append_unique(command["setpoints"], operand)
                    if instruction.raw_text:
                        _append_unique(sequence_steps, instruction.raw_text)

    tag_descriptions: dict[str, str] = {}
    io_tags: list[dict[str, Any]] = []
    for tag in tags:
        name = str(tag.get("tag") or tag.get("name") or "")
        if not name:
            continue
        description = tag.get("description")
        if description:
            tag_descriptions[name] = str(description)
        direction = _infer_tag_direction(name, reads, writes)
        io_tags.append(
            {
                "tag": name,
                "description": description,
                "direction": direction,
                "read_write": direction,
                "input_output": direction,
                "data_type": tag.get("data_type"),
                "source": tag.get("source"),
                "program": tag.get("program"),
                "related_module": None,
            }
        )
        if _looks_like_device(name):
            _append_unique(devices, name)
        if _looks_like_setpoint(name):
            _append_unique(setpoints, name)
        if _looks_like_alarm(name):
            _append_unique(alarms, name)
        if _looks_like_operator_prompt(name):
            _append_unique(operator_prompts, name)

    io_rows = [
        " | ".join(
            [
                str(item.get("tag") or ""),
                f"direction={item.get('direction') or 'unknown'}",
                f"data_type={item.get('data_type') or 'unknown'}",
                f"description={item.get('description') or 'Needs engineer input'}",
                f"source={item.get('program') or item.get('source') or 'unknown'}",
            ]
        )
        for item in io_tags
        if item.get("tag")
    ]
    descriptions = [
        f"{item['tag']}: {item['description']}"
        for item in io_tags
        if item.get("tag") and item.get("description")
    ]
    directions = [
        f"{item['tag']}: {item['direction']}"
        for item in io_tags
        if item.get("tag") and item.get("direction")
    ]
    data_types = [
        f"{item['tag']}: {item['data_type']}"
        for item in io_tags
        if item.get("tag") and item.get("data_type")
    ]
    sources = [
        f"{item['tag']}: {item.get('program') or item.get('source')}"
        for item in io_tags
        if item.get("tag") and (item.get("program") or item.get("source"))
    ]

    relationship_types = {"reads", "writes", "commands", "starts", "stops", "permits", "inhibits"}
    relationships = [
        item
        for item in normalized_relationships
        if item.get("relationship_type") in relationship_types
    ]
    for item in relationships:
        rel_type = item.get("relationship_type")
        target = str(item.get("target") or "")
        source = str(item.get("source") or "")
        if rel_type in {"writes", "commands", "starts", "stops"}:
            _append_unique(outputs, target)
            _append_unique(actions, target)
        if rel_type in {"permits", "inhibits", "reads"}:
            _append_unique(conditions, source)

    equipment_modules = [
        item["name"]
        for item in object_summaries
        if item.get("object_type") == "equipment_module" and item.get("name")
    ]

    return {
        "project_name": project.project_name,
        "controller_name": controller_name,
        "programs": programs,
        "routines": routines,
        "equipment_modules": equipment_modules,
        "tags": io_tags or tags,
        "io_rows": io_rows,
        "tag_descriptions": tag_descriptions,
        "descriptions": descriptions,
        "directions": directions,
        "data_types": data_types,
        "sources": sources,
        "reads": reads,
        "writes": writes,
        "relationships": relationships,
        "graph": graph_summary(project),
        "graph_summary": graph_summary(project),
        "commands": commands,
        "sequence_steps": sequence_steps,
        "routines_by_program": routines_by_program,
        "devices": devices,
        "setpoints": setpoints,
        "permissives": permissives,
        "interlocks": interlocks,
        "outputs": outputs,
        "alarms": alarms,
        "alarm_conditions": conditions,
        "operator_prompts": operator_prompts,
        "causes": conditions,
        "effects": outputs,
        "conditions": conditions,
        "actions": actions,
        "related_tags": sorted({item.get("tag") for item in [*reads, *writes] if item.get("tag")}),
        "related_routines": routines,
        "io_tags": io_tags,
    }


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


def get_logic_snapshot(db: Session, snapshot_id: uuid.UUID) -> LogicSnapshot:
    snapshot = db.get(LogicSnapshot, snapshot_id)
    if snapshot is None:
        raise NotFoundError("Logic snapshot not found.")
    return snapshot


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


def _latest_pending_revision(
    db: Session,
    record_id: uuid.UUID,
) -> DocumentRevision | None:
    """Return the most recent draft or proposed revision for a record, if any."""
    return db.scalar(
        select(DocumentRevision)
        .where(
            DocumentRevision.engineering_record_id == record_id,
            DocumentRevision.status.in_(
                [DocumentRevisionStatus.DRAFT, DocumentRevisionStatus.PROPOSED]
            ),
        )
        .order_by(DocumentRevision.created_at.desc())
        .limit(1)
    )


def _apply_revision_decision(
    db: Session,
    record: EngineeringRecord | None,
    *,
    actor: str,
    approved: bool,
) -> None:
    """Promote or reject the pending revision so the document status reflects the decision.

    Approving resolves the latest draft/proposed revision into an approved
    revision; rejecting marks it rejected. The engineering record status is then
    recomputed so the document card shows Approved / Needs review / Missing
    consistently after a decision (no manual refresh required).
    """
    if record is None:
        return
    pending = _latest_pending_revision(db, record.id)
    if pending is not None:
        if approved:
            pending.status = DocumentRevisionStatus.APPROVED
            pending.approved_by = actor
            pending.approved_at = datetime.now(timezone.utc)
        else:
            pending.status = DocumentRevisionStatus.REJECTED
    remaining = list(record.revisions)
    if any(item.status == DocumentRevisionStatus.APPROVED for item in remaining):
        record.status = EngineeringRecordStatus.ACTIVE
    elif any(
        item.status in {DocumentRevisionStatus.DRAFT, DocumentRevisionStatus.PROPOSED}
        for item in remaining
    ):
        record.status = EngineeringRecordStatus.NEEDS_REVIEW
    else:
        record.status = EngineeringRecordStatus.NEEDING_SETUP


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

    record = (
        db.get(EngineeringRecord, item.engineering_record_id)
        if item.engineering_record_id
        else None
    )
    _apply_revision_decision(db, record, actor=actor, approved=approved)

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
                **build_process_knowledge_extract(project, normalized),
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
