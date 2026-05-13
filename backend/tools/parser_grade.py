#!/usr/bin/env python3
"""L5X parser grading benchmark (CLI).

Usage::

    cd backend
    PYTHONPATH=. python tools/parser_grade.py path/to/l5x_folder

Emits a per-file scorecard: parse success, structural counts, instruction
coverage vs :data:`app.services.normalization_service.INSTRUCTION_SEMANTICS`,
ST block health, branch hints, and a coarse letter grade (A–D).

This tool is **offline** and does not call LLMs or PLCs. It is meant for
CI or engineering review when comparing parser iterations.

Future extensions (not implemented here):

* Diff scorecards between two parser versions.
* Golden-file comparison against expected JSON extracts.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Allow running as ``python tools/parser_grade.py`` from ``backend/``.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.connectors.rockwell_l5x import RockwellL5XConnector  # noqa: E402
from app.parsers.structured_text_blocks import parse_structured_text_blocks  # noqa: E402
from app.services.normalization_service import (  # noqa: E402
    INSTRUCTION_SEMANTICS,
    normalize_l5x_project,
)


def _branch_markers(rung_text: str) -> int:
    if not rung_text:
        return 0
    u = rung_text.upper()
    return u.count("BST") + u.count("NXB") + u.count("BND") + rung_text.count("[")


def grade_file(path: Path) -> dict:
    conn = RockwellL5XConnector()
    raw = path.read_bytes()
    card: dict = {"file": str(path), "parse_success": False}
    try:
        project = conn.parse(path.name, raw)
        card["parse_success"] = True
    except Exception as exc:  # noqa: BLE001 — diagnostic tool
        card["error"] = str(exc)
        card["grade"] = "D"
        card["recommendations"] = ["Fix parse crash before other metrics matter."]
        return card

    norm = normalize_l5x_project(project)
    objs = norm["control_objects"]
    rels = norm["relationships"]
    exec_ctx = norm["execution_contexts"]

    card["controller_count"] = len(project.controllers)
    card["program_count"] = sum(len(c.programs) for c in project.controllers)
    routines: list[tuple[str, str, int, int, str | None]] = []
    inst_known = 0
    inst_unknown = 0
    inst_types: Counter[str] = Counter()
    unresolved_tags: Counter[str] = Counter()
    missing_raw: list[str] = []
    st_blocks_total = 0
    st_complex_total = 0
    branch_hits = 0
    tag_names = {t.name for c in project.controllers for p in c.programs for t in p.tags}
    tag_names |= {t.name for c in project.controllers for t in c.controller_tags}

    for c in project.controllers:
        for p in c.programs:
            for r in p.routines:
                n_instr = len(r.instructions)
                raw_len = len((r.raw_logic or "").strip())
                routines.append((p.name, r.name, n_instr, raw_len, r.language))
                if r.language == "ladder" and n_instr == 0 and raw_len > 0:
                    missing_raw.append(f"{p.name}/{r.name}")
                branch_hits += sum(
                    _branch_markers(i.metadata.get("rung_text", "") or "")
                    for i in r.instructions
                    if (i.metadata or {}).get("rung_text")
                )
                for ins in r.instructions:
                    it = (ins.instruction_type or "").upper()
                    inst_types[it] += 1
                    if it in INSTRUCTION_SEMANTICS or it in (
                        "IF",
                        "ELSE",
                        "ELSIF",
                        "END_IF",
                        "ASSIGN",
                    ):
                        inst_known += 1
                    else:
                        inst_unknown += 1
                    for op in ins.operands:
                        if _looks_unresolved(op, tag_names):
                            unresolved_tags[op] += 1
                if r.language == "structured_text" and r.raw_logic:
                    blocks = parse_structured_text_blocks(r.raw_logic)
                    st_blocks_total += len(blocks)
                    st_complex_total += sum(
                        1 for b in blocks if type(b).__name__ == "STComplexBlock"
                    )

    card["routine_count"] = len(routines)
    card["rung_count_proxy"] = sum(
        1 for _, _, _, rl, lang in routines if lang == "ladder" and rl > 0
    )
    card["instruction_count"] = sum(n for _, _, n, _, _ in routines)
    card["tag_extraction_count"] = len(tag_names)
    card["known_instruction_hits"] = inst_known
    card["unknown_instruction_count"] = inst_unknown
    known_set = set(INSTRUCTION_SEMANTICS) | {
        "IF",
        "ELSE",
        "ELSIF",
        "END_IF",
        "ASSIGN",
        "PARALLEL_BRANCH",
        "BST",
        "NXB",
        "BND",
    }
    denom = max(1, inst_known + inst_unknown)
    card["known_instruction_coverage_pct"] = round(100.0 * inst_known / denom, 1)
    card["st_block_count"] = st_blocks_total
    card["st_too_complex_pct"] = (
        round(100.0 * st_complex_total / st_blocks_total, 1) if st_blocks_total else 0.0
    )
    card["branch_detection_count"] = branch_hits
    card["relationship_density"] = round(len(rels) / max(1, len(objs)), 3)
    card["control_object_count"] = len(objs)
    card["relationship_count"] = len(rels)
    card["execution_context_count"] = len(exec_ctx)
    card["traceability_score"] = round(
        min(1.0, len(rels) / max(1, 5 * len(objs))),
        3,
    )
    card["top_unknown_instructions"] = [k for k, _ in inst_types.most_common(12)]
    card["top_unresolved_operands"] = [k for k, _ in unresolved_tags.most_common(12)]
    card["routines_missing_instructions"] = missing_raw
    card["recommendations"] = _recommendations(card)

    # Letter grade (heuristic rubric).
    unk_pct = 100.0 * inst_unknown / denom
    if not card["parse_success"]:
        card["grade"] = "D"
    elif unk_pct > 25 or st_complex_total > st_blocks_total * 0.5:
        card["grade"] = "C"
    elif unk_pct > 8 or card["st_too_complex_pct"] > 25:
        card["grade"] = "B"
    else:
        card["grade"] = "A"

    return card


def _looks_unresolved(operand: str, known: set[str]) -> bool:
    op = operand.strip()
    if not op or op.startswith('"'):
        return False
    head = op.split(".", 1)[0].split("[", 1)[0]
    return head not in known and op not in known


def _recommendations(card: dict) -> list[str]:
    out: list[str] = []
    if card.get("unknown_instruction_count", 0) > 0:
        out.append(
            "Register frequent unknown ladder/ST opcodes in "
            "INSTRUCTION_SEMANTICS or improve ladder tokenizer coverage."
        )
    if card.get("st_too_complex_pct", 0) > 15:
        out.append(
            "Expand structured_text_blocks grammar or preserve more "
            "context in STComplexBlock for common patterns."
        )
    if card.get("branch_detection_count", 0) == 0 and card.get("instruction_count", 0) > 50:
        out.append(
            "Verify branch markers (BST/NXB/BND, parallel brackets) "
            "are preserved in rung_text metadata for normalization."
        )
    if card.get("routines_missing_instructions", []):
        out.append(
            "Investigate routines with raw_logic but zero parsed "
            "instructions — tokenizer or export shape mismatch."
        )
    return out or ["Parser looks healthy for this file's surface metrics."]


def main() -> None:
    ap = argparse.ArgumentParser(description="Grade Rockwell L5X parser coverage.")
    ap.add_argument("folder", type=Path, help="Directory containing .l5x / .L5X files")
    ap.add_argument("--json", action="store_true", help="Emit JSON only")
    args = ap.parse_args()
    folder: Path = args.folder
    if not folder.is_dir():
        print(f"Not a directory: {folder}", file=sys.stderr)
        sys.exit(2)
    files = sorted(folder.glob("*.l5x")) + sorted(folder.glob("*.L5X"))
    if not files:
        print(f"No L5X files in {folder}", file=sys.stderr)
        sys.exit(1)
    scorecards = [grade_file(f) for f in files]
    if args.json:
        print(json.dumps(scorecards, indent=2))
        return
    for sc in scorecards:
        print("=" * 72)
        print(json.dumps(sc, indent=2))
    print("=" * 72)
    grades = Counter(s.get("grade", "?") for s in scorecards)
    print("Summary grades:", dict(grades))


if __name__ == "__main__":
    main()
