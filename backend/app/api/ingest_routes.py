"""Live tag sample ingestion API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.tag_sample import (
    TagSampleBatchCreate,
    TagSampleCreate,
    TagSampleIngestResult,
)
from app.services.tag_ingestion_service import TagIngestionError, ingest_samples

router = APIRouter(prefix="/api/ingest", tags=["ingest"])


@router.post("/tag-samples", response_model=TagSampleIngestResult)
def ingest_tag_sample(
    payload: TagSampleCreate,
    db: Session = Depends(get_db),
) -> TagSampleIngestResult:
    try:
        return ingest_samples(db, payload)
    except TagIngestionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/tag-samples/batch", response_model=TagSampleIngestResult)
def ingest_tag_sample_batch(
    payload: TagSampleBatchCreate,
    db: Session = Depends(get_db),
) -> TagSampleIngestResult:
    try:
        return ingest_samples(db, payload)
    except TagIngestionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
