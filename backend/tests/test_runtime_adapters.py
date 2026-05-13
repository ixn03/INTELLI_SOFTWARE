from app.services.live_data_adapter import (
    CsvRuntimeAdapter,
    ManualSnapshotAdapter,
    SimulatedRuntimeAdapter,
)
from app.services.runtime_adapter_registry import get_adapter, list_adapter_descriptors


def test_list_adapters() -> None:
    names = {d["name"] for d in list_adapter_descriptors()}
    assert {"manual", "simulated", "csv"} <= names


def test_simulated_adapter_reads() -> None:
    a = SimulatedRuntimeAdapter()
    snap = a.read_tags(["TagA", "TagB"])
    assert "TagA" in snap.values
    h = a.health_check()
    assert h.ok


def test_manual_adapter() -> None:
    a = ManualSnapshotAdapter({"X": True})
    snap = a.read_tags(["X", "Y"])
    assert snap.values["X"].value is True
    assert "Y" not in snap.values


def test_csv_adapter_via_registry() -> None:
    csv = "tag,value,quality\nA,1,good\n"
    a = get_adapter("csv", csv_text=csv)
    snap = a.read_tags(["A"])
    assert "A" in snap.values


def test_csv_runtime_adapter_direct() -> None:
    a = CsvRuntimeAdapter("tag,value\nB,FALSE\n")
    snap = a.read_tags(["B"])
    assert snap.values["B"].tag == "B"
