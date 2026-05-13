#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.registry import get_connector  # noqa: E402
from app.models.reasoning import ControlObjectType, RelationshipType  # noqa: E402
from app.services.normalization_service import normalize_l5x_project  # noqa: E402


SUPPORTED_SUFFIXES = {
    ".l5x",
    ".xml",
    ".scl",
    ".fhx",
    ".txt",
    ".csv",
    ".cl",
    ".hwl",
    ".hwh",
    ".hsc",
    ".epr",
}


def _metadata_for(path: Path) -> dict[str, Any]:
    meta_path = path.with_suffix(path.suffix + ".metadata.json")
    if not meta_path.exists():
        return {
            "platform": None,
            "language": None,
            "expected_capabilities": [],
            "known_unsupported_features": [],
            "parser_notes": "",
        }
    return json.loads(meta_path.read_text(encoding="utf-8"))


def _inventory_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    metadata = _metadata_for(path)
    row: dict[str, Any] = {
        "file": str(path),
        "suffix": path.suffix.lower(),
        "metadata": metadata,
        "parser_crashed": False,
    }
    try:
        connector = get_connector(path.name, raw)
        row["connector_selected"] = connector.platform
        project = connector.parse(path.name, raw)
        row["parse_success"] = True
        normalized = normalize_l5x_project(project)
    except Exception as exc:  # noqa: BLE001 - coverage inventory should keep going
        row["parse_success"] = False
        row["parser_crashed"] = True
        row["error"] = str(exc)
        return row

    objs = normalized["control_objects"]
    rels = normalized["relationships"]
    instructions = [
        o
        for o in objs
        if o.object_type in {ControlObjectType.INSTRUCTION, ControlObjectType.FUNCTION_BLOCK}
    ]
    row.update(
        {
            "control_object_count": len(objs),
            "relationship_count": len(rels),
            "instruction_coverage": len(instructions),
            "branch_coverage": sum(
                1 for r in rels if (r.platform_specific or {}).get("rung_has_branches")
            ),
            "st_construct_coverage": sum(
                1
                for o in instructions
                if (o.attributes or {}).get("language") == "structured_text"
            ),
            "fbd_coverage": sum(
                1
                for o in objs
                if o.object_type
                in {
                    ControlObjectType.FUNCTION_BLOCK,
                    ControlObjectType.FUNCTION_BLOCK_PIN,
                    ControlObjectType.FBD_BLOCK_INSTANCE,
                    ControlObjectType.FBD_INPUT_PIN,
                    ControlObjectType.FBD_OUTPUT_PIN,
                }
                or (o.attributes or {}).get("language") == "fbd"
            ),
            "sfc_coverage": sum(
                1
                for o in objs
                if o.object_type
                in {
                    ControlObjectType.SFC_STEP,
                    ControlObjectType.SFC_TRANSITION,
                    ControlObjectType.SFC_ACTION,
                }
                or (o.attributes or {}).get("language") == "sfc"
            ),
            "unresolved_tags": sorted(
                {
                    r.target_id
                    for r in rels
                    if r.relationship_type == RelationshipType.REFERENCES
                    and str(r.target_id).startswith("unresolved::")
                }
            ),
            "unsupported_constructs": [
                {
                    "id": o.id,
                    "name": o.name,
                    "parse_status": (o.platform_specific or {}).get("parse_status"),
                }
                for o in objs
                if (o.platform_specific or {}).get("parse_status")
                in {"unsupported", "unsupported_language", "preserved_only"}
            ],
        }
    )
    row["coverage_gaps"] = _coverage_gaps(row)
    return row


def _coverage_gaps(row: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    meta = row.get("metadata") or {}
    expected = set(meta.get("expected_capabilities") or [])
    if "fbd" in expected and row.get("fbd_coverage", 0) == 0:
        gaps.append("expected_fbd_not_detected")
    if "sfc" in expected and row.get("sfc_coverage", 0) == 0:
        gaps.append("expected_sfc_not_detected")
    if row.get("parser_crashed"):
        gaps.append("parser_crash")
    if row.get("unsupported_constructs"):
        gaps.append("unsupported_constructs_present")
    return gaps


def build_inventory(fixtures_dir: Path) -> dict[str, Any]:
    files = sorted(
        p
        for p in fixtures_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_SUFFIXES
        and not p.name.endswith(".metadata.json")
    )
    rows = [_inventory_file(path) for path in files]
    return {
        "fixture_count": len(rows),
        "fixtures": rows,
        "summary": {
            "parser_crashes": sum(1 for r in rows if r.get("parser_crashed")),
            "coverage_gap_count": sum(len(r.get("coverage_gaps") or []) for r in rows),
            "platforms": sorted({str(r.get("connector_selected")) for r in rows if r.get("connector_selected")}),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory INTELLI parser fixtures.")
    parser.add_argument(
        "fixtures_dir",
        type=Path,
        nargs="?",
        default=_BACKEND_ROOT / "tests" / "fixtures",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_BACKEND_ROOT / "tests" / "fixtures" / "coverage_report.json",
    )
    args = parser.parse_args()
    report = build_inventory(args.fixtures_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
