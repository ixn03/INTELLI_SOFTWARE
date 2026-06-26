"""Serialize and persist ControlProject instances and normalized graphs."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models.stored_project import StoredProject
from app.db.session import get_database_url
from app.models.control_model import ControlProject
from app.models.reasoning import ControlObject, ExecutionContext, Relationship


def persistence_enabled() -> bool:
    """Return True when projects should be written to the configured database."""

    url = get_database_url()
    if url.startswith("sqlite"):
        return url not in ("sqlite://", "sqlite:///:memory:")
    return True


def serialize_project(project: ControlProject) -> dict[str, Any]:
    return project.model_dump(mode="json")


def deserialize_project(blob: dict[str, Any]) -> ControlProject:
    return ControlProject.model_validate(blob)


def serialize_normalized(normalized: dict[str, Any]) -> dict[str, Any]:
    return {
        "control_objects": [
            obj.model_dump(mode="json") for obj in normalized["control_objects"]
        ],
        "relationships": [
            rel.model_dump(mode="json") for rel in normalized["relationships"]
        ],
        "execution_contexts": [
            ctx.model_dump(mode="json") for ctx in normalized["execution_contexts"]
        ],
        "normalization_metadata": normalized.get("normalization_metadata", {}),
    }


def deserialize_normalized(blob: dict[str, Any]) -> dict[str, Any]:
    return {
        "control_objects": [
            ControlObject.model_validate(obj) for obj in blob.get("control_objects", [])
        ],
        "relationships": [
            Relationship.model_validate(rel) for rel in blob.get("relationships", [])
        ],
        "execution_contexts": [
            ExecutionContext.model_validate(ctx)
            for ctx in blob.get("execution_contexts", [])
        ],
        "normalization_metadata": blob.get("normalization_metadata", {}),
    }


def _project_connector(project: ControlProject) -> str | None:
    connector = (project.metadata or {}).get("connector")
    if isinstance(connector, str) and connector.strip():
        return connector
    return None


def save_project(
    db: Session,
    project: ControlProject,
    *,
    normalized: dict[str, Any] | None = None,
    clear_normalized: bool = False,
) -> None:
    if not project.file_hash:
        raise ValueError("Project must have a file hash before it can be persisted.")

    row = db.get(StoredProject, project.file_hash)
    project_blob = serialize_project(project)
    normalized_blob = (
        None
        if clear_normalized
        else serialize_normalized(normalized)
        if normalized is not None
        else None
    )

    if row is None:
        row = StoredProject(
            file_hash=project.file_hash,
            project_name=project.project_name,
            source_file=project.source_file,
            connector=_project_connector(project),
            project_blob=project_blob,
            normalized_blob=normalized_blob,
        )
        db.add(row)
        return

    row.project_name = project.project_name
    row.source_file = project.source_file
    row.connector = _project_connector(project)
    row.project_blob = project_blob
    if clear_normalized:
        row.normalized_blob = None
    elif normalized is not None:
        row.normalized_blob = normalized_blob


def load_project(db: Session, file_hash: str) -> ControlProject | None:
    row = db.get(StoredProject, file_hash)
    if row is None:
        return None
    return deserialize_project(row.project_blob)


def load_normalized(db: Session, file_hash: str) -> dict[str, Any] | None:
    row = db.get(StoredProject, file_hash)
    if row is None or row.normalized_blob is None:
        return None
    return deserialize_normalized(row.normalized_blob)
