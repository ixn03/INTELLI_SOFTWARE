"""Normalized runtime tag values for INTELLI.

All future ingestion paths (manual UI, CSV, historian batch, OPC UA,
PLC online reads, MQTT edge, simulation harnesses) should funnel into
:class:`RuntimeValue` / :class:`RuntimeSnapshotModel` so runtime
evaluation stays source-agnostic.

Intentionally **not** wired yet: OPC client libraries, historian SQL,
or live PLC drivers — those belong in separate integration packages
that only produce ``RuntimeSnapshotModel``.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_serializer

RuntimeQuality = Literal["good", "bad", "uncertain", "missing"]

RuntimeSource = Literal[
    "manual",
    "csv",
    "historian",
    "live",
    "simulated",
]


class RuntimeValue(BaseModel):
    """One tag's value at a point in time, with quality and lineage."""

    tag: str
    value: bool | int | float | str | None = None
    data_type: Optional[str] = None
    quality: RuntimeQuality = "good"
    timestamp: Optional[str] = None
    source: RuntimeSource = "manual"
    raw_value: Any = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_serializer("raw_value", when_used="json")
    def _serialize_raw_value(self, v: Any) -> Any:
        if v is None or isinstance(v, (bool, int, float, str)):
            return v
        return repr(v)


class RuntimeSnapshotModel(BaseModel):
    """Full snapshot: map from tag name to :class:`RuntimeValue`."""

    values: dict[str, RuntimeValue] = Field(default_factory=dict)


__all__ = [
    "RuntimeQuality",
    "RuntimeSource",
    "RuntimeValue",
    "RuntimeSnapshotModel",
]
