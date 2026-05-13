"""Normalize external runtime snapshots into :class:`RuntimeSnapshotModel`.

Ingestion sources (today: JSON body, CSV text). Planned sources that
should *only* add builders here — not change
:class:`app.services.runtime_evaluation_v2_service` — include:

* OPC UA subscription / read services
* Historian / SQL / Parquet replay jobs
* PLC online drivers (CIP, Modbus, etc.)
* MQTT / Sparkplug edge adapters
* Deterministic simulation / digital twin feeds

The evaluation engine consumes :class:`RuntimeSnapshotModel` (or a
legacy plain dict that this module normalizes first).
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Mapping, Optional

from app.models.runtime_value import (
    RuntimeSnapshotModel,
    RuntimeSource,
    RuntimeValue,
)


def infer_runtime_data_type(value: Any) -> str | None:
    """Best-effort Rockwell-ish type label from a Python value.

    ``0`` / ``1`` integers are typed as ``BOOL`` because many PLC and
    historian exports represent BOOL columns as small integers; all
    other ints remain ``DINT``.
    """

    if value is None:
        return None
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int) and not isinstance(value, bool):
        if value in (0, 1):
            return "BOOL"
        return "DINT"
    if isinstance(value, float):
        return "REAL"
    if isinstance(value, str):
        return "STRING"
    return None


def _coerce_quality(raw: Any) -> str:
    if raw is None:
        return "good"
    s = str(raw).strip().lower()
    if s in ("good", "bad", "uncertain", "missing"):
        return s
    return "good"


def _coerce_source(raw: Any) -> RuntimeSource:
    if raw is None:
        return "manual"
    s = str(raw).strip().lower()
    mapping: dict[str, RuntimeSource] = {
        "manual": "manual",
        "csv": "csv",
        "historian": "historian",
        "live": "live",
        "simulated": "simulated",
    }
    return mapping.get(s, "manual")


def normalize_runtime_snapshot(snapshot: object) -> RuntimeSnapshotModel:
    """Normalize a caller-supplied snapshot to :class:`RuntimeSnapshotModel`.

    **Simple format** — bare scalars::

        {"Faulted": true, "Tank_Level": 82.5, "State": "FILL"}

    **Rich format** — per-tag dicts::

        {
          "Faulted": {
            "value": true,
            "data_type": "BOOL",
            "quality": "good",
            "source": "manual",
            "timestamp": "2026-01-01T00:00:00Z"
          }
        }

    Unknown keys inside a rich tag dict are preserved under
    :attr:`RuntimeValue.metadata`.
    """

    if isinstance(snapshot, RuntimeSnapshotModel):
        return snapshot.model_copy(deep=True)

    if snapshot is None:
        return RuntimeSnapshotModel(values={})

    if not isinstance(snapshot, Mapping):
        raise TypeError(
            f"runtime snapshot must be a mapping or RuntimeSnapshotModel, "
            f"not {type(snapshot).__name__}"
        )

    out: dict[str, RuntimeValue] = {}
    for tag, raw in snapshot.items():
        if not isinstance(tag, str) or not tag:
            continue
        if isinstance(raw, Mapping):
            d = dict(raw)
            val = d.pop("value", None)
            dt = d.pop("data_type", None)
            if dt is not None:
                dt = str(dt)
            q = _coerce_quality(d.pop("quality", None))
            ts = d.pop("timestamp", None)
            if ts is not None:
                ts = str(ts)
            src = _coerce_source(d.pop("source", None))
            raw_val = d.pop("raw_value", raw)
            meta_raw = d.pop("metadata", None)
            meta: dict[str, Any] = {}
            if isinstance(meta_raw, Mapping):
                meta = {str(k): v for k, v in meta_raw.items()}
            for k, v in d.items():
                meta[str(k)] = v
            if dt is None:
                dt = infer_runtime_data_type(val)
            if val is None and q == "good":
                q = "missing"
            out[tag] = RuntimeValue(
                tag=tag,
                value=_json_compat_scalar(val),
                data_type=dt,
                quality=q,  # type: ignore[arg-type]
                timestamp=ts,
                source=src,
                raw_value=raw_val,
                metadata=meta,
            )
        else:
            v = _json_compat_scalar(raw)
            q: str = "missing" if raw is None else "good"
            out[tag] = RuntimeValue(
                tag=tag,
                value=v,
                data_type=infer_runtime_data_type(v),
                quality=q,  # type: ignore[assignment]
                source="manual",
                raw_value=raw,
                metadata={},
            )
    return RuntimeSnapshotModel(values=out)


def _json_compat_scalar(val: Any) -> bool | int | float | str | None:
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, Mapping):
        return json.dumps(val, sort_keys=True)
    return str(val)


def normalize_csv_runtime_values(csv_text: str) -> RuntimeSnapshotModel:
    """Parse CSV with optional columns ``tag,value,data_type,quality,timestamp``.

    Extra columns are folded into each :class:`RuntimeValue.metadata`
    under the column header. Invalid / blank rows are skipped.
    """

    if not (csv_text or "").strip():
        return RuntimeSnapshotModel(values={})

    f = io.StringIO(csv_text)
    sample = csv_text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    f.seek(0)
    reader = csv.DictReader(f, dialect=dialect)
    if not reader.fieldnames:
        return RuntimeSnapshotModel(values={})

    fields = [h.strip().lower() if h else "" for h in reader.fieldnames]
    tag_col = next(
        (reader.fieldnames[i] for i, h in enumerate(fields) if h in ("tag", "name", "symbol")),
        reader.fieldnames[0],
    )
    value_col = next(
        (reader.fieldnames[i] for i, h in enumerate(fields) if h == "value"),
        None,
    )
    dt_col = next(
        (reader.fieldnames[i] for i, h in enumerate(fields) if h in ("data_type", "datatype", "type")),
        None,
    )
    qual_col = next(
        (reader.fieldnames[i] for i, h in enumerate(fields) if h == "quality"),
        None,
    )
    ts_col = next(
        (reader.fieldnames[i] for i, h in enumerate(fields) if h in ("timestamp", "time")),
        None,
    )

    reserved = {tag_col, value_col, dt_col, qual_col, ts_col}
    extras = [c for c in reader.fieldnames if c and c not in reserved]

    out: dict[str, RuntimeValue] = {}
    for row in reader:
        if not row:
            continue
        tag = (row.get(tag_col) or "").strip()
        if not tag:
            continue
        raw_val: Any = None
        if value_col and value_col in row:
            raw_val = _parse_csv_value_cell((row.get(value_col) or "").strip())
        dt = (row.get(dt_col) or "").strip() if dt_col else None
        if not dt:
            dt = infer_runtime_data_type(raw_val)
        q = _coerce_quality((row.get(qual_col) or "").strip() if qual_col else None)
        ts = (row.get(ts_col) or "").strip() or None if ts_col else None
        meta: dict[str, Any] = {}
        for c in extras:
            if c in row and row[c] is not None and str(row[c]).strip() != "":
                meta[c] = row[c]
        if raw_val is None and q == "good":
            q = "missing"
        out[tag] = RuntimeValue(
            tag=tag,
            value=raw_val if isinstance(raw_val, (bool, int, float, str, type(None))) else str(raw_val),
            data_type=dt or None,
            quality=q,  # type: ignore[arg-type]
            timestamp=ts,
            source="csv",
            raw_value=row,
            metadata=meta,
        )
    return RuntimeSnapshotModel(values=out)


def _parse_csv_value_cell(cell: str) -> bool | int | float | str | None:
    if not cell:
        return None
    u = cell.upper()
    if u in ("TRUE", "T", "1", "ON"):
        return True
    if u in ("FALSE", "F", "0", "OFF"):
        return False
    try:
        if "." in cell or "e" in cell.lower():
            return float(cell)
        return int(cell)
    except ValueError:
        return cell


def get_runtime_value(
    snapshot: RuntimeSnapshotModel,
    tag_name: str,
) -> RuntimeValue | None:
    """Exact, case-sensitive lookup (supports ``Timer1.DN`` style keys)."""

    return snapshot.values.get(tag_name)


def snapshot_to_flat_values(
    snapshot: RuntimeSnapshotModel,
) -> dict[str, Any]:
    """Build ``tag -> value`` dict for legacy code paths."""

    return {k: v.value for k, v in snapshot.values.items()}


def snapshot_quality_map(snapshot: RuntimeSnapshotModel) -> dict[str, str]:
    """``tag -> quality`` for runtime evaluators."""

    return {k: v.quality for k, v in snapshot.values.items()}


def snapshot_data_type_map(snapshot: RuntimeSnapshotModel) -> dict[str, str | None]:
    return {k: v.data_type for k, v in snapshot.values.items()}


__all__ = [
    "get_runtime_value",
    "infer_runtime_data_type",
    "normalize_csv_runtime_values",
    "normalize_runtime_snapshot",
    "snapshot_data_type_map",
    "snapshot_quality_map",
    "snapshot_to_flat_values",
]
