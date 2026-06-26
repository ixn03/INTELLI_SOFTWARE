"""Resolve control-object signals to live values via tag registry + InfluxDB."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models.tag_registry import Tag, TagAlias, TagLogicRef
from app.schemas.tag_sample import SampleQuality
from app.services.influx_reader import InfluxReader, InfluxReaderError, get_influx_reader
from app.services.troubleshooting_workspace_service import SignalRef

log = logging.getLogger(__name__)

RuntimeSnapshot = dict[str, Any]


class RegistryIndex:
    """In-memory indexes for fast signal → registry tag resolution."""

    def __init__(self, tags: list[Tag]) -> None:
        self.by_canonical: dict[str, Tag] = {}
        self.by_alias: dict[str, Tag] = {}
        self.by_logic_ref: dict[str, Tag] = {}
        self.by_raw_name: dict[str, Tag] = {}
        for tag in tags:
            self.by_canonical[tag.canonical_name] = tag
            if tag.raw_name:
                self.by_raw_name[tag.raw_name] = tag
            for alias in tag.aliases:
                self.by_alias[alias.alias_name] = tag
            for ref in tag.logic_refs:
                if ref.reference_key:
                    self.by_logic_ref[ref.reference_key] = tag

    def resolve(self, signal: SignalRef) -> Tag | None:
        if signal.id in self.by_logic_ref:
            return self.by_logic_ref[signal.id]
        for key in _lookup_keys_for_signal(signal):
            if key in self.by_logic_ref:
                return self.by_logic_ref[key]
            if key in self.by_canonical:
                return self.by_canonical[key]
            if key in self.by_alias:
                return self.by_alias[key]
            if key in self.by_raw_name:
                return self.by_raw_name[key]
        return None


def _lookup_keys_for_signal(signal: SignalRef) -> list[str]:
    keys: list[str] = []
    if signal.name:
        keys.append(signal.name)
    if signal.id:
        if "/" in signal.id:
            tail = signal.id.rsplit("/", 1)[-1]
            if tail not in keys:
                keys.append(tail)
        if "::" in signal.id:
            tail = signal.id.split("::", 1)[-1]
            if "/" in tail:
                tail = tail.rsplit("/", 1)[-1]
            if tail not in keys:
                keys.append(tail)
    return keys


def load_registry_index(db: Session, *, enabled_only: bool = True) -> RegistryIndex:
    stmt = select(Tag).options(
        selectinload(Tag.aliases),
        selectinload(Tag.logic_refs),
    )
    if enabled_only:
        stmt = stmt.where(Tag.is_enabled.is_(True))
    tags = list(db.scalars(stmt).all())
    return RegistryIndex(tags)


def build_live_runtime_snapshot(
    db: Session,
    signals: list[SignalRef],
    *,
    reader: InfluxReader | None = None,
    registry_index: RegistryIndex | None = None,
) -> tuple[RuntimeSnapshot, dict[str, Any]]:
    """Build a runtime_snapshot dict keyed by control-object id and signal name.

    Values are dicts with keys: value, timestamp, quality, source, stale, tag_id,
    canonical_name — compatible with troubleshooting workspace _live_value().
  """
    if not signals:
        return {}, _empty_meta(enabled=True)

    reader = reader or get_influx_reader()
    index = registry_index or load_registry_index(db)

    snapshot: RuntimeSnapshot = {}
    resolved = 0
    missing_registry = 0
    missing_influx = 0
    influx_errors = 0
    resolutions: list[dict[str, str]] = []

    seen_tag_ids: set[str] = set()
    for signal in signals:
        tag = index.resolve(signal)
        if tag is None:
            missing_registry += 1
            continue

        tag_id_str = str(tag.id)
        if tag_id_str in seen_tag_ids:
            # Reuse already-fetched sample for duplicate tag mapping
            entry = _find_entry_for_tag(snapshot, tag_id_str)
            if entry:
                _attach_snapshot_keys(snapshot, signal, entry)
                resolved += 1
            continue
        seen_tag_ids.add(tag_id_str)

        try:
            sample = reader.get_latest_tag_value(tag_id_str)
        except InfluxReaderError as exc:
            log.warning("Influx read failed for tag %s: %s", tag.canonical_name, exc)
            influx_errors += 1
            continue

        if sample is None:
            missing_influx += 1
            continue

        stale = sample.quality == SampleQuality.STALE
        entry = {
            "value": sample.value,
            "timestamp": sample.timestamp.isoformat(),
            "quality": sample.quality.value,
            "source": sample.source.value,
            "stale": stale,
            "tag_id": tag_id_str,
            "canonical_name": tag.canonical_name,
        }
        _attach_snapshot_keys(snapshot, signal, entry)
        resolved += 1
        resolutions.append(
            {
                "signal_id": signal.id,
                "signal_name": signal.name or "",
                "canonical_name": tag.canonical_name,
                "tag_id": tag_id_str,
            }
        )

    meta = {
        "enabled": True,
        "source": "tag_registry_influx",
        "signals_requested": len(signals),
        "resolved": resolved,
        "missing_registry": missing_registry,
        "missing_influx": missing_influx,
        "influx_errors": influx_errors,
        "resolutions": resolutions,
    }
    return snapshot, meta


def _attach_snapshot_keys(
    snapshot: RuntimeSnapshot,
    signal: SignalRef,
    entry: dict[str, Any],
) -> None:
    snapshot[signal.id] = entry
    if signal.name:
        snapshot[signal.name] = entry


def _find_entry_for_tag(snapshot: RuntimeSnapshot, tag_id: str) -> dict[str, Any] | None:
    for value in snapshot.values():
        if isinstance(value, dict) and value.get("tag_id") == tag_id:
            return value
    return None


def _empty_meta(*, enabled: bool) -> dict[str, Any]:
    return {
        "enabled": enabled,
        "source": "tag_registry_influx",
        "signals_requested": 0,
        "resolved": 0,
        "missing_registry": 0,
        "missing_influx": 0,
        "influx_errors": 0,
        "resolutions": [],
    }


def find_tags_by_canonical_names(db: Session, names: list[str]) -> list[Tag]:
    if not names:
        return []
    stmt = (
        select(Tag)
        .where(
            or_(
                Tag.canonical_name.in_(names),
                Tag.raw_name.in_(names),
                Tag.id.in_(
                    select(TagAlias.tag_id).where(TagAlias.alias_name.in_(names))
                ),
            )
        )
        .options(selectinload(Tag.aliases), selectinload(Tag.logic_refs))
    )
    return list(db.scalars(stmt).all())
