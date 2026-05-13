"""Tests for runtime ingestion and :class:`RuntimeSnapshotModel` integration."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.runtime_value import RuntimeSnapshotModel, RuntimeValue  # noqa: E402
from app.services.runtime_ingestion_service import (  # noqa: E402
    get_runtime_value,
    infer_runtime_data_type,
    normalize_csv_runtime_values,
    normalize_runtime_snapshot,
    snapshot_to_flat_values,
)
from app.services.runtime_evaluation_v2_service import (  # noqa: E402
    OverallVerdict,
    evaluate_trace_runtime_v2,
)
from tests.test_runtime_evaluation_v2_service import (  # noqa: E402
    TARGET_ID,
    _trace,
    _writer_conditions,
)


class NormalizeSimpleTests(unittest.TestCase):
    def test_simple_snapshot(self) -> None:
        m = normalize_runtime_snapshot({"Faulted": True, "Level": 3.5})
        self.assertEqual(m.values["Faulted"].value, True)
        self.assertEqual(m.values["Faulted"].data_type, "BOOL")
        self.assertEqual(m.values["Level"].data_type, "REAL")
        self.assertEqual(m.values["Level"].quality, "good")

    def test_null_scalar_quality_missing(self) -> None:
        m = normalize_runtime_snapshot({"X": None})
        self.assertEqual(m.values["X"].quality, "missing")

    def test_rich_named_set(self) -> None:
        m = normalize_runtime_snapshot(
            {
                "State": {
                    "value": "FILL",
                    "data_type": "NamedSet",
                    "quality": "good",
                    "source": "manual",
                }
            }
        )
        self.assertEqual(m.values["State"].value, "FILL")
        self.assertEqual(m.values["State"].data_type, "NamedSet")


class InferTypeTests(unittest.TestCase):
    def test_bool(self) -> None:
        self.assertEqual(infer_runtime_data_type(True), "BOOL")

    def test_int01_bool_heuristic(self) -> None:
        self.assertEqual(infer_runtime_data_type(0), "BOOL")
        self.assertEqual(infer_runtime_data_type(1), "BOOL")
        self.assertEqual(infer_runtime_data_type(42), "DINT")


class CsvTests(unittest.TestCase):
    def test_basic_csv(self) -> None:
        csv = "tag,value,data_type,quality\nA,1,BOOL,good\nB,2.5,REAL,bad\n"
        m = normalize_csv_runtime_values(csv)
        self.assertEqual(m.values["A"].value, True)
        self.assertEqual(m.values["B"].quality, "bad")

    def test_sparse_row_skipped(self) -> None:
        csv = "tag,value\nA,1\n,\nB,0\n"
        m = normalize_csv_runtime_values(csv)
        self.assertEqual(set(m.values), {"A", "B"})


class LookupTests(unittest.TestCase):
    def test_member_exact(self) -> None:
        m = normalize_runtime_snapshot({"Timer1.DN": True})
        v = get_runtime_value(m, "Timer1.DN")
        assert v is not None
        self.assertTrue(v.value)


class RuntimeV2QualityTests(unittest.TestCase):
    def test_bad_quality_bool_is_incomplete(self) -> None:
        snap = RuntimeSnapshotModel(
            values={
                "StartPB": RuntimeValue(
                    tag="StartPB",
                    value=True,
                    data_type="BOOL",
                    quality="bad",
                ),
                "Faulted": RuntimeValue(
                    tag="Faulted",
                    value=False,
                    data_type="BOOL",
                    quality="good",
                ),
            }
        )
        result = evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="Rung[0]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "tag": "StartPB",
                            "required_value": True,
                            "instruction_type": "XIC",
                            "natural_language": "StartPB TRUE",
                        },
                        {
                            "tag": "Faulted",
                            "required_value": False,
                            "instruction_type": "XIO",
                            "natural_language": "Faulted FALSE",
                        },
                    ],
                    target_id=TARGET_ID,
                    source_id="rung::PLC01/MainProgram/MotorRoutine/Rung[3]",
                ),
                target_id=TARGET_ID,
            ),
            snap,
        )
        self.assertEqual(
            result.platform_specific.get("overall_verdict"),
            OverallVerdict.INCOMPLETE.value,
        )

    def test_real_dtype_rejects_bool_ladder_condition(self) -> None:
        snap = normalize_runtime_snapshot(
            {
                "StartPB": {
                    "value": 1.0,
                    "data_type": "REAL",
                    "quality": "good",
                },
                "Faulted": False,
            }
        )
        flat = snapshot_to_flat_values(snap)
        self.assertEqual(flat["StartPB"], 1.0)
        result = evaluate_trace_runtime_v2(
            _trace(
                _writer_conditions(
                    location="Rung[0]",
                    instruction_type="OTE",
                    conditions=[
                        {
                            "tag": "StartPB",
                            "required_value": True,
                            "instruction_type": "XIC",
                            "natural_language": "StartPB TRUE",
                        },
                    ],
                    target_id=TARGET_ID,
                    source_id="rung::PLC01/MainProgram/MotorRoutine/Rung[3]",
                ),
                target_id=TARGET_ID,
            ),
            snap,
        )
        unsup = result.platform_specific.get("unsupported_conditions") or []
        reasons = " ".join(str(u.get("reason") or "") for u in unsup)
        self.assertIn("BOOL ladder", reasons)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
