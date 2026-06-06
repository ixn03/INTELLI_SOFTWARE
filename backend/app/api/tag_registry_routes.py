"""Tag registry API routes."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.tag_registry import (
    DataSourceCreate,
    DataSourceListResponse,
    DataSourceRead,
    TagCreate,
    TagListResponse,
    TagRead,
    TagUpdate,
)
from app.schemas.tag_sample import TagHistoryResponse, TagLatestResponse, TagSampleRead
from app.services import tag_registry_service as svc
from app.services.influx_reader import InfluxReaderError, get_influx_reader

router = APIRouter(prefix="/api", tags=["tag-registry"])


@router.get("/tags", response_model=TagListResponse)
def list_tags(
    plant_id: uuid.UUID | None = None,
    area_id: uuid.UUID | None = None,
    equipment_id: uuid.UUID | None = None,
    data_source_id: uuid.UUID | None = None,
    source_system: str | None = None,
    is_enabled: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> TagListResponse:
    items, total = svc.list_tags(
        db,
        plant_id=plant_id,
        area_id=area_id,
        equipment_id=equipment_id,
        data_source_id=data_source_id,
        source_system=source_system,
        is_enabled=is_enabled,
        limit=limit,
        offset=offset,
    )
    return TagListResponse(items=items, total=total)


@router.post("/tags", response_model=TagRead, status_code=201)
def create_tag(payload: TagCreate, db: Session = Depends(get_db)) -> TagRead:
    try:
        return svc.create_tag(db, payload)
    except svc.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _tag_metadata(tag: TagRead | object) -> dict:
    data = TagRead.model_validate(tag).model_dump()
    return data


@router.get("/tags/{tag_id}/latest", response_model=TagLatestResponse)
def get_tag_latest(tag_id: uuid.UUID, db: Session = Depends(get_db)) -> TagLatestResponse:
    try:
        tag = svc.get_tag(db, tag_id)
    except svc.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        sample = get_influx_reader().get_latest_tag_value(str(tag_id))
    except InfluxReaderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if sample:
        sample.canonical_name = tag.canonical_name
        sample.unit = sample.unit or tag.unit
    return TagLatestResponse(tag=_tag_metadata(tag), sample=sample)


@router.get("/tags/{tag_id}/history", response_model=TagHistoryResponse)
def get_tag_history(
    tag_id: uuid.UUID,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(100, ge=1, le=5000),
    db: Session = Depends(get_db),
) -> TagHistoryResponse:
    try:
        tag = svc.get_tag(db, tag_id)
    except svc.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        rows = get_influx_reader().get_tag_history(
            str(tag_id), start=start, end=end, limit=limit
        )
    except InfluxReaderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    samples: list[TagSampleRead] = []
    for row in rows:
        row.canonical_name = tag.canonical_name
        row.unit = row.unit or tag.unit
        samples.append(row)
    return TagHistoryResponse(tag=_tag_metadata(tag), samples=samples, total=len(samples))


@router.get("/tags/{tag_id}", response_model=TagRead)
def get_tag(tag_id: uuid.UUID, db: Session = Depends(get_db)) -> TagRead:
    try:
        return svc.get_tag(db, tag_id)
    except svc.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/tags/{tag_id}", response_model=TagRead)
def patch_tag(
    tag_id: uuid.UUID, payload: TagUpdate, db: Session = Depends(get_db)
) -> TagRead:
    try:
        return svc.update_tag(db, tag_id, payload)
    except svc.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except svc.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/equipment/{equipment_id}/tags", response_model=TagListResponse)
def list_equipment_tags(
    equipment_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> TagListResponse:
    items, total = svc.list_tags(
        db, equipment_id=equipment_id, limit=limit, offset=offset
    )
    return TagListResponse(items=items, total=total)


@router.get("/data-sources", response_model=DataSourceListResponse)
def list_data_sources(
    plant_id: uuid.UUID | None = None,
    source_system: str | None = None,
    is_enabled: bool | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> DataSourceListResponse:
    items, total = svc.list_data_sources(
        db,
        plant_id=plant_id,
        source_system=source_system,
        is_enabled=is_enabled,
        limit=limit,
        offset=offset,
    )
    return DataSourceListResponse(items=items, total=total)


@router.post("/data-sources", response_model=DataSourceRead, status_code=201)
def create_data_source(
    payload: DataSourceCreate, db: Session = Depends(get_db)
) -> DataSourceRead:
    try:
        return svc.create_data_source(db, payload)
    except svc.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
