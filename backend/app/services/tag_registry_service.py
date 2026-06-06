"""Tag registry CRUD service."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models.tag_registry import DataSource, Tag
from app.schemas.tag_registry import DataSourceCreate, TagCreate, TagUpdate


class TagRegistryError(Exception):
    pass


class NotFoundError(TagRegistryError):
    pass


class ConflictError(TagRegistryError):
    pass


def list_tags(
    db: Session,
    *,
    plant_id: uuid.UUID | None = None,
    area_id: uuid.UUID | None = None,
    equipment_id: uuid.UUID | None = None,
    data_source_id: uuid.UUID | None = None,
    source_system: str | None = None,
    is_enabled: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Tag], int]:
    stmt = select(Tag)
    count_stmt = select(func.count()).select_from(Tag)

    filters = []
    if plant_id is not None:
        filters.append(Tag.plant_id == plant_id)
    if area_id is not None:
        filters.append(Tag.area_id == area_id)
    if equipment_id is not None:
        filters.append(Tag.equipment_id == equipment_id)
    if data_source_id is not None:
        filters.append(Tag.data_source_id == data_source_id)
    if source_system is not None:
        filters.append(Tag.source_system == source_system)
    if is_enabled is not None:
        filters.append(Tag.is_enabled == is_enabled)

    for clause in filters:
        stmt = stmt.where(clause)
        count_stmt = count_stmt.where(clause)

    stmt = stmt.order_by(Tag.canonical_name).limit(limit).offset(offset)
    items = list(db.scalars(stmt).all())
    total = db.scalar(count_stmt) or 0
    return items, int(total)


def get_tag(db: Session, tag_id: uuid.UUID) -> Tag:
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise NotFoundError(f"Tag {tag_id} not found.")
    return tag


def create_tag(db: Session, payload: TagCreate) -> Tag:
    tag = Tag(**payload.model_dump())
    db.add(tag)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Tag canonical_name must be unique within the plant."
        ) from exc
    db.refresh(tag)
    return tag


def update_tag(db: Session, tag_id: uuid.UUID, payload: TagUpdate) -> Tag:
    tag = get_tag(db, tag_id)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(tag, key, value)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Tag canonical_name must be unique within the plant."
        ) from exc
    db.refresh(tag)
    return tag


def list_data_sources(
    db: Session,
    *,
    plant_id: uuid.UUID | None = None,
    source_system: str | None = None,
    is_enabled: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[DataSource], int]:
    stmt = select(DataSource)
    count_stmt = select(func.count()).select_from(DataSource)

    if plant_id is not None:
        stmt = stmt.where(DataSource.plant_id == plant_id)
        count_stmt = count_stmt.where(DataSource.plant_id == plant_id)
    if source_system is not None:
        stmt = stmt.where(DataSource.source_system == source_system)
        count_stmt = count_stmt.where(DataSource.source_system == source_system)
    if is_enabled is not None:
        stmt = stmt.where(DataSource.is_enabled == is_enabled)
        count_stmt = count_stmt.where(DataSource.is_enabled == is_enabled)

    stmt = stmt.order_by(DataSource.name).limit(limit).offset(offset)
    items = list(db.scalars(stmt).all())
    total = db.scalar(count_stmt) or 0
    return items, int(total)


def create_data_source(db: Session, payload: DataSourceCreate) -> DataSource:
    source = DataSource(**payload.model_dump())
    db.add(source)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            "Data source name must be unique within the plant."
        ) from exc
    db.refresh(source)
    return source
