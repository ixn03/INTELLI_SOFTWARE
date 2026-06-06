"""Validate and ingest live tag samples into InfluxDB."""

from __future__ import annotations

import uuid
from datetime import timezone

from sqlalchemy.orm import Session

from app.db.models.tag_registry import Tag
from app.schemas.tag_sample import (
    SampleQuality,
    TagSampleBatchCreate,
    TagSampleCreate,
    TagSampleIngestResult,
    TagSampleRejection,
)
from app.services.influx_writer import InfluxSampleRecord, get_influx_writer


class TagIngestionError(Exception):
    pass


def _normalize_quality(quality: SampleQuality) -> SampleQuality:
    return SampleQuality(str(quality.value).lower())


def validate_sample(db: Session, sample: TagSampleCreate) -> tuple[Tag | None, str | None]:
    tag = db.get(Tag, sample.tag_id)
    if tag is None:
        return None, "Unknown tag_id — not found in registry."
    if not tag.is_enabled:
        return None, "Tag is disabled in registry."
    if tag.source_system != sample.source:
        return (
            None,
            f"Source mismatch: sample source={sample.source.value}, "
            f"registry source={tag.source_system.value}.",
        )
    if sample.raw_tag_name != tag.raw_name:
        return (
            None,
            "raw_tag_name does not match registry raw_name for this tag_id.",
        )
    return tag, None


def ingest_samples(
    db: Session,
    payload: TagSampleBatchCreate | TagSampleCreate,
    *,
    writer=None,
) -> TagSampleIngestResult:
    samples = (
        payload.samples if isinstance(payload, TagSampleBatchCreate) else [payload]
    )
    writer = writer or get_influx_writer()
    accepted_ids: list[uuid.UUID] = []
    rejections: list[TagSampleRejection] = []
    influx_records: list[InfluxSampleRecord] = []

    for sample in samples:
        sample.quality = _normalize_quality(sample.quality)
        tag, reason = validate_sample(db, sample)
        if tag is None:
            rejections.append(
                TagSampleRejection(
                    tag_id=sample.tag_id,
                    raw_tag_name=sample.raw_tag_name,
                    reason=reason or "Rejected.",
                )
            )
            continue

        ts = sample.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        influx_records.append(
            InfluxSampleRecord(
                tag_id=str(tag.id),
                raw_tag_name=tag.raw_name,
                source=sample.source.value,
                quality=sample.quality.value,
                plant_id=str(tag.plant_id),
                equipment_id=str(tag.equipment_id) if tag.equipment_id else None,
                value=sample.value,
                timestamp=ts,
            )
        )
        accepted_ids.append(tag.id)

    if influx_records:
        try:
            writer.write_samples(influx_records)
        except Exception as exc:
            raise TagIngestionError(f"InfluxDB write failed: {exc}") from exc

    return TagSampleIngestResult(
        accepted_count=len(accepted_ids),
        rejected_count=len(rejections),
        accepted_ids=accepted_ids,
        rejections=rejections,
    )
