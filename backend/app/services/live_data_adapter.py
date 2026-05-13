"""Runtime data adapters (manual, CSV, simulated) -> RuntimeSnapshotModel."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.models.runtime_value import RuntimeSnapshotModel, RuntimeValue
from app.services.runtime_ingestion_service import normalize_csv_runtime_values


class AdapterHealth(BaseModel):
    ok: bool = True
    message: str = ""
    adapter_name: str = ""
    source_type: str = ""


class RuntimeDataAdapter(ABC):
    """Interface for future OPC/historian/live drivers."""

    name: str = "base"
    source_type: str = "unknown"

    @abstractmethod
    def read_tags(self, tag_names: list[str]) -> RuntimeSnapshotModel:
        ...

    @abstractmethod
    def health_check(self) -> AdapterHealth:
        ...


class ManualSnapshotAdapter(RuntimeDataAdapter):
    """Caller supplies values dict tag -> raw value (expanded to RuntimeValue)."""

    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.name = "manual_snapshot"
        self.source_type = "manual"
        self._values = dict(values or {})

    def read_tags(self, tag_names: list[str]) -> RuntimeSnapshotModel:
        out: dict[str, RuntimeValue] = {}
        for name in tag_names:
            if name in self._values:
                raw = self._values[name]
                out[name] = RuntimeValue(
                    tag=name,
                    value=self._coerce_scalar(raw),
                    source="manual",
                    raw_value=raw,
                )
        return RuntimeSnapshotModel(values=out)

    def health_check(self) -> AdapterHealth:
        return AdapterHealth(
            ok=True,
            message="manual adapter",
            adapter_name=self.name,
            source_type=self.source_type,
        )

    @staticmethod
    def _coerce_scalar(raw: Any) -> bool | int | float | str | None:
        if isinstance(raw, (bool, int, float, str)) or raw is None:
            return raw
        if isinstance(raw, dict) and "value" in raw:
            v = raw["value"]
            if isinstance(v, (bool, int, float, str)) or v is None:
                return v
        return None


class SimulatedRuntimeAdapter(RuntimeDataAdapter):
    """Deterministic fake values for tests (cycles bools by tag name hash)."""

    def __init__(self) -> None:
        self.name = "simulated"
        self.source_type = "simulated"

    def read_tags(self, tag_names: list[str]) -> RuntimeSnapshotModel:
        out: dict[str, RuntimeValue] = {}
        for name in tag_names:
            flag = (sum(ord(c) for c in name) % 2) == 0
            out[name] = RuntimeValue(
                tag=name,
                value=flag,
                source="simulated",
                metadata={"pattern": "parity_of_name_chars"},
            )
        return RuntimeSnapshotModel(values=out)

    def health_check(self) -> AdapterHealth:
        return AdapterHealth(
            ok=True,
            message="simulated adapter",
            adapter_name=self.name,
            source_type=self.source_type,
        )


class CsvRuntimeAdapter(RuntimeDataAdapter):
    """Parse CSV text via existing ingestion helper."""

    def __init__(self, csv_text: str) -> None:
        self.name = "csv"
        self.source_type = "csv"
        self._snapshot = normalize_csv_runtime_values(csv_text)

    def read_tags(self, tag_names: list[str]) -> RuntimeSnapshotModel:
        out: dict[str, RuntimeValue] = {}
        for name in tag_names:
            if name in self._snapshot.values:
                out[name] = self._snapshot.values[name]
        return RuntimeSnapshotModel(values=out)

    def health_check(self) -> AdapterHealth:
        return AdapterHealth(
            ok=True,
            message="csv adapter",
            adapter_name=self.name,
            source_type=self.source_type,
        )


__all__ = [
    "AdapterHealth",
    "RuntimeDataAdapter",
    "ManualSnapshotAdapter",
    "SimulatedRuntimeAdapter",
    "CsvRuntimeAdapter",
]
