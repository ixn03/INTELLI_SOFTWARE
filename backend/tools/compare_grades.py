#!/usr/bin/env python3
"""Compare parser grade baselines and emit Phase 3 logic-expression metrics."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.registry import get_connector  # noqa: E402
from app.models.reasoning import ControlObjectType, RelationshipType  # noqa: E402
from app.services.normalization_service import normalize_l5x_project  # noqa: E402


def load_cards(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {Path(item["file"]).name: item for item in data}


def aggregate_instruction_coverage(cards: dict[str, dict]) -> tuple[int, int, float]:
    known = sum(c.get("known_instruction_hits", 0) for c in cards.values())
    total = sum(c.get("instruction_count", 0) for c in cards.values())
    pct = round(100.0 * known / total, 1) if total else 0.0
    return known, total, pct


def phase3_metrics(folder: Path) -> dict:
    files = sorted(folder.glob("*.L5X"))
    total_rungs = 0
    branched_rungs = 0
    resolved_rungs = 0
    resolved_branched = 0
    edges_with_expr = 0
    cause_effect_edges = 0
    per_file: list[dict] = []

    ce_types = {
        RelationshipType.READS,
        RelationshipType.WRITES,
        RelationshipType.CALLS,
        RelationshipType.LATCHES,
        RelationshipType.UNLATCHES,
        RelationshipType.RESETS,
    }

    for path in files:
        raw = path.read_bytes()
        conn = get_connector(path.name, raw)
        project = conn.parse(path.name, raw)
        norm = normalize_l5x_project(project)
        rungs = [
            o
            for o in norm["control_objects"]
            if o.object_type == ControlObjectType.RUNG
        ]
        rels = norm["relationships"]
        ce = [r for r in rels if r.relationship_type in ce_types]
        tr = len(rungs)
        br = sum(1 for r in rungs if r.attributes.get("has_branches"))
        res = sum(1 for r in rungs if r.attributes.get("logic_expression_resolved"))
        rbr = sum(
            1
            for r in rungs
            if r.attributes.get("has_branches")
            and r.attributes.get("logic_expression_resolved")
        )
        ex = sum(1 for r in ce if r.logic_expression is not None)

        total_rungs += tr
        branched_rungs += br
        resolved_rungs += res
        resolved_branched += rbr
        edges_with_expr += ex
        cause_effect_edges += len(ce)

        if tr > 0:
            per_file.append(
                {
                    "file": path.name,
                    "rungs": tr,
                    "branched": br,
                    "resolved": res,
                    "branched_resolved": rbr,
                    "edges_with_expr": ex,
                    "cause_effect_edges": len(ce),
                }
            )

    return {
        "file_count": len(files),
        "total_rungs": total_rungs,
        "branched_rungs": branched_rungs,
        "resolved_rungs": resolved_rungs,
        "resolved_branched": resolved_branched,
        "edges_with_expr": edges_with_expr,
        "cause_effect_edges": cause_effect_edges,
        "per_file": per_file,
    }


def main() -> None:
    before_path = _REPO_ROOT / "grade_before.json"
    after_path = _REPO_ROOT / "grade_after.json"
    fixture_dir = _BACKEND_ROOT / "tests" / "fixtures" / "l5x"

    before = load_cards(before_path)
    after = load_cards(after_path)
    keys = sorted(set(before) | set(after))

    bk, bt, bp = aggregate_instruction_coverage(before)
    ak, at, ap = aggregate_instruction_coverage(after)

    print("=" * 72)
    print("PARSER GRADER COMPARISON (grade_before.json -> grade_after.json)")
    print("=" * 72)
    print(f"Files graded: {len(keys)}")
    print()
    print("Aggregate instruction coverage:")
    print(f"  Before: {bp}% ({bk}/{bt})")
    print(f"  After:  {ap}% ({ak}/{at})")
    print(f"  Delta:  {ap - bp:+.1f} percentage points")
    print()

    metric_names = [
        "branch_detection_count",
        "relationship_count",
        "supported_relationship_count",
        "traceability_score",
        "deterministic_relationship_score",
        "relationship_density",
    ]
    print("Aggregate structural metrics:")
    for metric in metric_names:
        bsum = sum(before[k].get(metric, 0) for k in keys if k in before)
        asum = sum(after[k].get(metric, 0) for k in keys if k in after)
        if isinstance(bsum, float) or isinstance(asum, float):
            bavg = sum(before[k].get(metric, 0) for k in keys if k in before) / max(
                1, len(keys)
            )
            aavg = sum(after[k].get(metric, 0) for k in keys if k in after) / max(
                1, len(keys)
            )
            print(f"  {metric} (avg/file): {bavg:.3f} -> {aavg:.3f} ({aavg - bavg:+.3f})")
        else:
            print(f"  {metric} (sum): {bsum} -> {asum} ({asum - bsum:+d})")

    grades_before = Counter(before[k].get("grade", "?") for k in keys if k in before)
    grades_after = Counter(after[k].get("grade", "?") for k in keys if k in after)
    print()
    print(f"Grade distribution before: {dict(sorted(grades_before.items()))}")
    print(f"Grade distribution after:  {dict(sorted(grades_after.items()))}")

    changed_files = []
    for k in keys:
        if k not in before or k not in after:
            continue
        diffs = []
        for metric in [
            "known_instruction_coverage_pct",
            "branch_detection_count",
            "relationship_count",
            "grade",
        ]:
            bv = before[k].get(metric)
            av = after[k].get(metric)
            if bv != av:
                diffs.append((metric, bv, av))
        if diffs:
            changed_files.append((k, diffs))

    print()
    if changed_files:
        print("Per-file changes vs baseline:")
        for name, diffs in changed_files:
            print(f"  {name}:")
            for metric, bv, av in diffs:
                print(f"    {metric}: {bv} -> {av}")
    else:
        print("Per-file grader metrics: unchanged vs grade_before.json")

    p3 = phase3_metrics(fixture_dir)
    print()
    print("=" * 72)
    print("PHASE 3 — LogicExpression coverage (new capability, not in old grader)")
    print("=" * 72)
    tr = p3["total_rungs"]
    br = p3["branched_rungs"]
    res = p3["resolved_rungs"]
    rbr = p3["resolved_branched"]
    ex = p3["edges_with_expr"]
    ce = p3["cause_effect_edges"]
    print(f"Ladder rungs total:                 {tr}")
    print(
        f"Branched rungs detected:            {br} "
        f"({100 * br / max(1, tr):.1f}% of rungs)"
    )
    print(
        f"LogicExpression resolved rungs:     {res} "
        f"({100 * res / max(1, tr):.1f}% of rungs)"
    )
    print(
        f"Branched rungs with resolved tree:  {rbr}/{br} "
        f"({100 * rbr / max(1, br):.1f}% of branched rungs)"
    )
    print(
        "Before Phase 3 this was 0% — branch structure was flagged but not "
        "represented as a boolean tree."
    )
    print(
        f"Cause/effect edges with logic tree: {ex}/{ce} "
        f"({100 * ex / max(1, ce):.1f}%)"
    )
    print()
    print("Per-file Phase 3 detail (files with rungs):")
    for row in p3["per_file"]:
        if row["branched"]:
            pct = 100 * row["branched_resolved"] / max(1, row["branched"])
            print(
                f"  {row['file']}: {row['rungs']} rungs, "
                f"{row['branched']} branched, "
                f"{row['branched_resolved']} branched+resolved ({pct:.0f}%)"
            )


if __name__ == "__main__":
    main()
