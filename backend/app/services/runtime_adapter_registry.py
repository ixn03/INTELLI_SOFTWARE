"""Registry of runtime adapters (extensible for OPC/historian later)."""

from __future__ import annotations

from typing import Callable, Optional

from app.services.live_data_adapter import (
    CsvRuntimeAdapter,
    ManualSnapshotAdapter,
    RuntimeDataAdapter,
    SimulatedRuntimeAdapter,
)

AdapterFactory = Callable[[], RuntimeDataAdapter]

_registry: dict[str, AdapterFactory] = {
    "manual": lambda: ManualSnapshotAdapter(),
    "simulated": lambda: SimulatedRuntimeAdapter(),
}


def register_adapter(name: str, factory: AdapterFactory) -> None:
    _registry[name] = factory


def get_adapter(name: str, **kwargs: object) -> RuntimeDataAdapter:
    if name == "csv":
        text = str(kwargs.get("csv_text") or "")
        return CsvRuntimeAdapter(text)
    if name == "manual":
        vals = kwargs.get("values")
        if isinstance(vals, dict):
            return ManualSnapshotAdapter(vals)
        return ManualSnapshotAdapter()
    if name == "simulated":
        return SimulatedRuntimeAdapter()
    factory = _registry.get(name)
    if factory is None:
        raise KeyError(f"Unknown runtime adapter: {name}")
    return factory()


def list_adapter_descriptors() -> list[dict[str, str]]:
    return [
        {"name": "manual", "source_type": "manual", "description": "Caller JSON values"},
        {"name": "simulated", "source_type": "simulated", "description": "Deterministic test values"},
        {"name": "csv", "source_type": "csv", "description": "CSV rows via normalize_csv_runtime_values"},
    ]


__all__ = ["get_adapter", "list_adapter_descriptors", "register_adapter"]
