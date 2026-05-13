# INTELLI Parser and Normalization Roadmap

This document describes INTELLI’s design direction for parsing and normalizing industrial control logic across **Ladder Diagram (LD)**, **Structured Text (ST)**, **Function Block Diagram (FBD)**, and **Sequential Function Chart (SFC)**. It uses only generally known controls concepts (IEC-style languages, scan-based PLCs, tags, routines, transitions). It does not reproduce vendor-specific manuals or proprietary syntax definitions.

---

## 1. Current supported scope

| Language | Ingestion | Structural graph | Deterministic READS/WRITES / conditions |
|----------|-----------|-------------------|----------------------------------------|
| **Ladder** | Rockwell L5X-derived rung text and instruction tokens | Controllers, programs, routines, rungs, instructions, tags; branch markers preserved | XIC/XIO, OTE/OTL/OTU, timers/counters (structure-level writes), comparisons, math, MOV/COP, RES, JSR (routine target + parameter list metadata), one-shots, parallel bracket notation tokenization |
| **Structured Text** | Raw routine text + flat instruction list (legacy) | Same graph + synthetic per-block statement objects | Assignments, simple IF/ELSE, CASE with per-label assignments; richer boolean expressions where the ST expression parser supports them |
| **FBD** | Routine language tagged as FBD; optional flat instruction placeholders | Routine node + low-confidence placeholder metadata | **Not** parsed into blocks/wires yet |
| **SFC** | Routine language tagged as SFC | Routine node + low-confidence placeholder metadata | **Not** parsed into steps/transitions yet |

Anything outside the enrolled instruction registry or ST envelope is retained as **instruction-shaped objects** or **too_complex** blocks so downstream tools never assume completeness.

---

## 2. Desired normalized representation

### 2.1 Ladder

- **Routine** as `ControlObjectType.ROUTINE` with language, instruction counts, raw logic presence.
- **Rung** as `RUNG` with rung number, optional raw rung text, branch summary (has branches, branch count), not per-instruction branch mapping until a dedicated analyzer exists.
- **Instruction** as `INSTRUCTION` with operands, semantic family, implementation flag.
- **Edges**: `READS`, `WRITES`, `CALLS`, `RESETS`, etc., with `execution_context_id`, `logic_condition` (rung-level conservative string), and `platform_specific` carrying instruction identity, timer/counter member semantics where known, comparison metadata, JSR parameters, branch warnings.

### 2.2 Structured Text

- **Statement blocks** as `INSTRUCTION` nodes with `attributes["language"]="structured_text"` and stable `source_location` (`…/Statement[n]`).
- **Edges**: `WRITES` to assignment targets; `READS` from conditions, selectors, and expression operands that resolve to tags; no fabricated edges for unsupported shapes.
- **Complex / loop / call-only lines** preserved as blocks with `st_parse_status` and raw text only—no inferred control flow.

### 2.3 Function Block Diagram (target)

- **Block type** (`FUNCTION_BLOCK` or dedicated type) vs **block instance** (`FBD_BLOCK_INSTANCE`) as distinct objects where possible.
- **Pins** (`FBD_INPUT_PIN`, `FBD_OUTPUT_PIN`) as children or linked nodes of an instance.
- **Wires** as `RelationshipType` edges (e.g. signal / data flow) between pins or instances, never guessed from layout alone without parser support.
- **Parameter bindings** as explicit relationships or bound-parameter objects (`FBD_PARAMETER_BINDING`) referencing tags or literals in `platform_specific`.

### 2.4 Sequential Function Chart (target)

- **Steps** (`SFC_STEP`), **transitions** (`SFC_TRANSITION`), **actions** (`SFC_ACTION`), **transition conditions** (`SFC_CONDITION`) as first-class objects once parsing exists.
- **Active step** tracking via `ACTIVE_STEP_TAG` (or tag + role) and **sequence_order** (numeric / lexical order within chart) in attributes or `platform_specific`.
- **Edges**: `SEQUENCES`, `CONDITION_FOR`, and containment from chart → step → action.

---

## 3. Common controls concepts to normalize

| Concept | Ladder (today / near) | ST (today / near) | FBD / SFC (future) |
|--------|------------------------|-------------------|---------------------|
| **Reads** | XIC/XIO, compare inputs, math sources, JSR inputs (conservative) | Conditions, RHS identifiers | Wire into input pin |
| **Writes** | OTE/OTL/OTU, MOV, math dest, timer/counter structure | `:=` | Wire from output pin / block output |
| **Conditions** | Rung text + per-instruction gating metadata | Parsed boolean / compare | Transition expressions |
| **State transitions** | Implicit in coils; future state machines | CASE / assignments to state tags | SFC step → transition → step |
| **Timers / counters** | Structure-level writes; member reads (.DN, .PRE, …) | FB calls (TON, etc.) when modeled | FB instances |
| **Math** | ADD/SUB/MUL/DIV/CPT (partial) | Future expression lowering | Math FBs |
| **Comparisons** | EQU/NEQ/… | Compare terms in expressions | Comparator FBs |
| **Routine calls** | JSR + metadata | Future `()` calls | Nested charts / subcharts |
| **Branches** | BST/NXB/BND + bracket arms; warnings on edges | IF/CASE paths | Parallel divergence in SFC |
| **Function blocks** | AOI / vendor blocks as unknown instructions | Standalone FB call lines preserved | Core FBD model |
| **SFC steps/actions/transitions** | N/A | N/A | Dedicated object types + edges |

---

## 4. What should be deterministic now

- Stable IDs for tags, routines, rungs, and instructions derived from controller/program/routine paths and indices.
- Instruction registry classification: known opcode → known family; unknown → structural only.
- Rung-level branch **detection** counts and flags (not per-branch instruction assignment).
- ST block segmentation order and statement indices.
- Expression parser outcomes: same text → same parse tree or same `too_complex` flag.
- Normalization of supported ladder families and supported ST blocks into explicit relationships without LLM involvement.

---

## 5. What should be preserved as `unsupported` / `too_complex`

- **FBD/SFC** bodies until a real diagram parser exists: routine-level `parse_status=unsupported_language`, `confidence=LOW`, `raw_logic_present`, no invented pins or wires.
- **Ladder**: ASCII art, undocumented opcodes, AOI internals without definitions, PID internals, ambiguous nested branches beyond tokenization.
- **ST**: `ELSIF` chains (handled conservatively as structured text without inventing path conditions), `FOR`/`WHILE`/`REPEAT`, arbitrary arithmetic as sole RHS, function bodies, pointers, rich temporaries.
- **Policy**: never synthesize tags, rungs, or edges to “fill gaps”; surface warnings in metadata for trace and UI.

---

## 6. Parser grading criteria

Use these dimensions to score fixtures and regressions (see `backend/tools/parser_grade.py` where applicable):

1. **Coverage**: fraction of instructions/blocks recognized vs dropped to unknown/complex.
2. **Soundness**: no false READS/WRITES on literals or malformed operands.
3. **Stability**: byte-identical normalized output for the same input file hash and engine version.
4. **Diagnostics**: rich `platform_specific` (branch warnings, parse_status, inventory of unsupported opcodes).
5. **Trace usefulness**: downstream trace can explain supported paths without contradicting source text.

---

## 7. Prioritized implementation plan

1. **Cross-language baseline** — schema enums for FBD/SFC placeholders; routine-level unsupported handling; inventory metadata.
2. **FBD/SFC placeholders** — no graph fabrication; extend reasoning enums only.
3. **Ladder depth** — branch metadata, JSR parameter READS where tag-shaped, timer/counter member edges as needed, unsupported opcode inventory.
4. **ST depth** — ELSIF chains (conservative), numeric/enum-like assignments, FB call line preservation, loop blocks as complex.
5. **Knowledge layer** — engineer-verified facts adjacent to deterministic graph (separate service).
6. **Version compare** — diff two normalized projects for tags, routines, hashes, relationship changes.
7. **Live data adapters** — manual / CSV / simulated snapshots into `RuntimeSnapshotModel`.
8. **LLM assist (last)** — intent + target hints + evidence-only paraphrase; feature-flagged; never invent tags or logic.

---

## Revision

This roadmap is a living spec. Implementation details belong in module docstrings (`normalization_service`, parsers, services) and tests; this file tracks intent and priority only.
