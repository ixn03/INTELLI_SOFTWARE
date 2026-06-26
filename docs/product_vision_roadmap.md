# INTELLI Product Vision & Roadmap

This document states what INTELLI is for, what it is not for, and how we get there in phases. It is written for product and engineering alignment — not as a UI spec or a code audit. For the step-by-step engineering plan to deepen Rockwell parsing and normalization, see **[complex_program_bridge_plan.md](./complex_program_bridge_plan.md)**. For parser/normalization technical scope, see **[parsing_normalization_roadmap.md](./parsing_normalization_roadmap.md)**. For current per-vendor import status, see **[platform_support_matrix.md](./platform_support_matrix.md)**.

---

## The product in one sentence

**INTELLI helps controls engineers answer: "Why is this output not doing what I expect?"** — by tracing backward through real PLC logic, showing evidence, and ranking confidence — not by asking an LLM to guess.

---

## The MVP spine

Everything else in this document supports one core workflow. If we lose sight of it, we lose the product.

1. **Upload** a Rockwell Studio 5000 export (`.L5X`).
2. **Select** a tag or output the engineer cares about (a valve, a motor run command, a permissive, an interlock).
3. **Ask** INTELLI what logic controls it and why it is true or false right now.

The answer must include:

- **What** drives the selected signal (rungs, ST blocks, AOI internals, sequence steps).
- **Why** it is in its current state — including permissives, interlocks, and mode gating.
- **Evidence** — rung text, structured conditions, tag references, and (later) live values.
- **Confidence** — how complete and trustworthy the explanation is.
- **What to check next** when the trace cannot fully resolve the cause.

That workflow is the **MVP spine**. We do not start with "AI for all controls." We do not start with multi-vendor breadth, historian integration, or a chatbot that improvises causality. We start with one vendor, one export format, one question — and we make that answer reliable enough that a plant engineer would trust it on a real program.

---

## Eleven pillars

The spine rests on eleven design commitments. Each is non-negotiable for the product vision; together they define what INTELLI becomes.

### 1. Lock the core use case

The product is a **troubleshooting lens on PLC logic**, not a general-purpose industrial AI assistant. Marketing, UI, and engineering priorities all flow from the spine above. Features that do not make "upload → select → explain with evidence" better are secondary until the spine works on representative plant programs.

### 2. Build the Rockwell foundation deeply

Rockwell is the first and primary connector. Priority order within that connector:

| Priority | Capability | Purpose |
|----------|------------|---------|
| 1 | L5X importer | Ingest real exports |
| 2 | Tag extraction | Identity every signal |
| 3 | Routine extraction | Locate logic by program/task |
| 4 | Ladder logic parsing | Reconstruct rung cause/effect |
| 5 | Structured Text parsing | Cover ST-heavy programs |
| 6 | FBD parsing | Process and batch logic |
| 7 | SFC parsing | Sequence and state-machine logic |
| 8 | AOI handling | See inside reusable blocks |
| 9 | UDT handling | Resolve structured tags and members |
| 10 | Cross-reference map | Who reads and writes each tag |

We do not chase Siemens, DeltaV, or Honeywell until Rockwell reaches **reliable understanding** on real programs — not demo fixtures alone. Depth on one vendor beats shallow coverage on five.

### 3. Vendor-neutral model

Every connector converts vendor-specific exports into one shared language. INTELLI's reasoner never branches on "is this Rockwell?" — it only sees universal objects.

Conceptual vocabulary (mapped to `backend/app/models/reasoning.py` today):

| Concept | Role |
|---------|------|
| `ControlProject` | Connector staging area (`control_model.py`); vendor flavor acceptable here |
| `ControlObject` / Tag | Any tag, rung, routine, block, alarm, step |
| Logic routine | A unit of executable logic |
| `LogicBlock` | AOI, function block, FB instance |
| `LogicExpression` | AND/OR/NOT boolean tree for conditions |
| Logic read / write | `Relationship(READS / WRITES)` edges |
| Logic condition | Gating on an edge or transition |
| Logic branch | Parallel path in ladder or FBD |
| Logic sequence | SFC or ordered step chain |
| `Relationship` | All typed edges between objects |

**Rule:** When a new vendor forces a new concept, grow the universal model in `reasoning.py`, not a vendor-specific reasoner. One reasoner, many connectors.

### 4. Logic graph

Parsed projects become a **directed graph of cause and effect**:

- Tag A controls Tag B.
- Tag B enables Output C.
- Interlock D blocks Output C.
- Mode E selects Sequence F.
- AOI X internally writes Status Y.

The graph is the backbone. Without it, any LLM will hallucinate interlocks and permissives. Normalization (`normalization_service.py`) must emit explicit `READS`, `WRITES`, `CALLS`, `SEQUENCES`, and structured `LogicExpression` conditions — deterministically, with no model inference in the parse path.

Today the graph is built in memory on upload (`project_store.py`) and is lost on restart. Making it durable is a Phase 2 requirement, not a nice-to-have.

### 5. Deterministic tracing first

The canonical question: **"Why is Output_X not turning on?"**

The trace engine (`trace_v2_service.py`) walks backward from the target through writers and gating conditions. Every answer bundles:

| Field | Meaning |
|-------|---------|
| **Conclusion** | Plain-language statement of why the output is in its state |
| **Evidence** | Rung text, logic expressions, relationship IDs, source locations |
| **Confidence** | Score reflecting parser coverage and evidence completeness |
| **Unsupported assumptions** | Places where structure was ambiguous or unimplemented |
| **Conflicting evidence** | When logic and runtime (later) disagree |
| **Next tags to check** | Actionable follow-ups when the trace stops short |

Tracing is rule-based graph traversal. It is not prompt engineering.

### 6. Evidence and trust ranking

Not all evidence is equal. INTELLI ranks sources in strict priority:

1. **Live tag values** (highest — when available)
2. **Parsed PLC logic** (deterministic graph)
3. **Engineer-verified notes** (human attestation)
4. **Control narrative** (written loop/equipment description)
5. **Uploaded drawings and manuals** (P&ID, electrical, alarm docs)
6. **Historical fixes** (what worked before on this plant)
7. **LLM inference** (lowest — explanation only, never ground truth)

The LLM **explains** what the deterministic engine found. It does not invent tags, rungs, or causality. `llm_assist_service.py` exists as an assist layer; disabling it must leave full troubleshooting capability.

`evidence_service.py` and `trustworthiness_service.py` already shape evidence bundles and confidence scores toward this model. The product must surface source type and rank visibly so engineers know what they are trusting.

### 7. Knowledge layer

Logic alone is not enough. Plants accumulate context that no parser extracts:

- Control narratives and cause/effect matrices
- P&IDs and electrical drawings
- Alarm documentation
- Engineer notes, verified fixes, rejected fixes
- Assumptions and known plant-specific behavior

This layer sits **adjacent to** the deterministic graph — linked by tag and equipment identity, never merged into normalization. Engineers curate it over time; INTELLI uses it to enrich explanations and to record what the team has already validated.

### 8. UI around trust

The interface should feel like: **"Show me why this machine or loop is not working."**

Core surfaces (described by intent, not component names):

- **Selected tag** — what the engineer is investigating
- **Logic path** — backward trace from output to root causes
- **Blocking condition** — the specific gate that is false
- **Evidence cards** — each claim with source, location, and confidence
- **Engineer feedback** — "Mark as verified" / "Mark as wrong" to improve the knowledge layer

The UI is a trust instrument. Dense backend capability is worthless if the front end overwhelms the engineer. Current workspace flows are functional but cumbersome; future UI work should simplify the spine workflow before adding breadth.

### 9. Live data — after static logic works

Runtime truth comes **after** static parsing and tracing are credible:

- OPC UA
- PI / AF
- Seeq
- DeltaV historian
- Ignition
- Kafka
- InfluxDB / TimescaleDB

`runtime_snapshot_service.py` and `runtime_evaluation_v2_service.py` provide the integration shape; `runtime_adapter_registry.py` lists manual, simulated, and CSV adapters today. Live adapters merge runtime values into the same tag model the normalizer built, so trace can distinguish "logic says false" from "I/O reads false." This is Phase 3 — blocked on durable graph storage and trustworthy static trace.

### 10. Vendor expansion — Rockwell first, then stress the model

Expansion order:

```
Rockwell (deep) → Siemens TIA → DeltaV → Honeywell Experion → Omron / Schneider (later)
```

Each new vendor is a **connector** that maps into the same neutral model. Siemens is valuable not because we need breadth for marketing, but because it **stress-tests** whether `LogicBlock`, `LogicExpression`, and execution contexts are truly universal. See `platform_support_matrix.md` for today's per-vendor foundation status — Rockwell is furthest along; others are shell or preservation-only.

**Non-goal:** Starting Siemens before Rockwell is solid (~80–90% reliable understanding on real programs). The loop is: deepen Rockwell → strengthen universal model → add next vendor → repeat.

### 11. Clean UI + LLM explanation layer

Users should not feel overwhelmed. The happy path is simple:

1. Connect live data (when ready) or upload a project.
2. Ask a question about a tag or output.
3. Read a clear answer with evidence.

Knowledge building — narratives, verified fixes, drawings — lives in a **separate section**, not mixed into the troubleshooting flow. The backend can be rich; the frontend must be calm. Natural language makes answers readable; deterministic trace makes them true.

---

## Phased roadmap

Three product phases map to the eleven pillars. Engineering phases inside [complex_program_bridge_plan.md](./complex_program_bridge_plan.md) (Phases 0–11) are the execution detail beneath these.

### Phase 1 — MVP spine + Rockwell depth

**Goal:** A controls engineer can upload an L5X, select an output, and get a trustworthy backward trace with evidence on ladder and ST logic — including AOIs, UDTs, aliases, and branched rungs.

**In scope:**

- L5X import and tag/routine extraction (`rockwell_l5x.py`)
- Ladder and ST normalization to universal model (`normalization_service.py`, `ladder_logic.py`, `structured_text_blocks.py`)
- AOI / UDT / alias resolution as `LogicBlock` and member metadata
- Branch-aware `LogicExpression` on rungs
- Trace v2 with conclusions, evidence, confidence, next-check hints (`trace_v2_service.py`)
- Parser grading and regression (`parser_grade.py`, fixture corpus)
- Basic upload-and-trace UI

**Explicitly out of scope for Phase 1:**

- Live OPC / historian connections
- Siemens or other vendor connectors beyond shells
- FBD / SFC cause-effect (structural parse only)
- Durable graph persistence across restarts
- LLM as primary reasoning path

**Alignment today (Jun 2026):** Bridge Phases 0–4 are complete on the committed fixture corpus. The Phase 1 acceptance gate (`backend/tools/phase1_gate.py`) passes: eval harness (10/10 cases), ladder answer keys, parser grade (17 fixtures, ~95% opcode coverage), upload-and-trace workspace UI, and trace v2 with unified evidence. **Product Phase 1 is complete for the fixture corpus.** Plant-scale programs (private L5X exports) remain a measurement and depth target — fleet `traceability_score` is still low on full controllers; Phase 2 adds persistence, tasks, and FBD/SFC cause-effect depth.

### Phase 2 — Graph, tracing, knowledge

**Goal:** The logic graph is durable, causally correct, and enrichable with engineer knowledge. One engineer's verified finding helps the next shift.

**In scope:**

- **Execution / task model** — parse L5X tasks; respect scan order and periodic vs continuous context in trace metadata
- **FBD / SFC normalization** — wires and transitions become real READS/WRITES and SEQUENCE edges
- **Durable graph storage** — replace in-memory `project_store` with versioned persistence; upload-once, trace-many
- **Unified reasoner** — consolidate `trace_v2`, `ask_v2`, `sequence_reasoning`, and `runtime_evaluation_v2` into one graph walk and one explanation format
- **Knowledge layer** — narratives, cause/effect matrices, engineer notes, verified/rejected fixes linked to tags
- **Trust UI** — evidence cards with source rank, confidence, blocking conditions, engineer verify/reject actions
- **Project diff** — compare two export versions for regression review

**Principles carried forward:**

- Deterministic normalization; LLM never in the parse path
- Never fabricate edges for ambiguous structure — flag `unsupported` / `too_complex`
- Grade on `traceability_score`, not optimistic letter grades

### Phase 3 — Live data + vendor expansion

**Goal:** INTELLI answers with both **logic truth** and **plant truth**, and supports a second vendor without forking the reasoner.

**In scope:**

- **Live data adapters** — OPC UA, PI/AF, Seeq, DeltaV historian, Ignition, Kafka, InfluxDB/TimescaleDB
- **Runtime merge** — tag identity map (normalized id ↔ live node); evidence layer distinguishes logic vs I/O
- **Siemens TIA connector** — stress-test universal model with LAD/FBD/FB
- **DeltaV, Honeywell** — sequential expansion after Siemens validates the model
- **LLM explanation layer** — natural-language paraphrase of deterministic trace + evidence bundle; feature-flagged; citations required

**Non-goals for Phase 3:**

- LLM replacing trace or inventing causality
- Chasing Omron/Schneider before Rockwell + one second vendor are production-credible
- Feature breadth that obscures the spine workflow

---

## How the codebase aligns today

This is a directional map, not an audit. It shows where existing code already serves the vision and where gaps remain.

| Vision element | Where it lives | Status |
|----------------|----------------|--------|
| Rockwell L5X import | `backend/app/connectors/rockwell_l5x.py` | Operational for ladder/ST; tasks/FBD/SFC shallow |
| Connector staging model | `backend/app/models/control_model.py` | `ControlProject`, AOI defs, UDT defs, aliases |
| Universal reasoning schema | `backend/app/models/reasoning.py` | `ControlObject`, `Relationship`, `LogicExpression`, `TraceResult` |
| Ladder → graph | `backend/app/parsers/ladder_logic.py`, `normalization_service.py` | Branch-aware expressions on fixture corpus |
| ST → graph | `backend/app/parsers/structured_text_blocks.py`, `st_expression.py` | Loops, calls, nested IF; residual `too_complex` on exotic constructs |
| Backward trace | `backend/app/services/trace_v2_service.py` | Core spine demo path |
| Evidence & confidence | `backend/app/services/evidence_service.py`, `trustworthiness_service.py` | Bundles and scores; full 7-tier ranking not yet surfaced in UI |
| Project storage | `backend/app/services/project_store.py` | In-memory only — restart loses graph |
| Runtime (manual/CSV) | `backend/app/services/runtime_snapshot_service.py`, `live_data_adapter.py` | Fixture/manual path; no production OPC |
| LLM assist | `backend/app/services/llm_assist_service.py` | Stub/assist; not primary layer |
| API surface | `backend/app/api/routes.py` | Upload, trace, ask endpoints |
| Frontend types | `frontend/src/types/reasoning.ts` | Shared trace/evidence shapes |
| Workspace UI | `frontend/src/components/intelli/` | Signal Intelligence workspace — Phase 1 spine |
| Phase 1 gate | `backend/tools/phase1_gate.py` | Fixture-corpus acceptance (pytest + eval + parser grade) |

Fragmented reasoning services (`ask_v2_service.py`, `sequence_reasoning_service.py`, `runtime_evaluation_v2_service.py`) reflect today's architecture — Phase 2 consolidates them. Vendor shells (Siemens XML, DeltaV FHX, Honeywell preservation) exist per `platform_support_matrix.md` but must not distract from Rockwell depth.

---

## Principles and non-goals

**Principles we keep:**

1. **MVP spine first** — upload, select, explain with evidence.
2. **Deterministic before probabilistic** — same file in, same graph out.
3. **Graph before LLM** — no hallucinated interlocks.
4. **Evidence-ranked trust** — show source type; live logic beats narrative beats inference.
5. **Universal model, many connectors** — one reasoner, never `RockwellReasoner` / `SiemensReasoner`.
6. **Never fabricate** — ambiguous structure is flagged, not guessed.
7. **Controls engineer acceptance** — answer keys and real L5X fixtures are the bar.
8. **Clean front, rich back** — simplify the troubleshooting path; hide complexity.

**Non-goals:**

- "AI for all controls" as a positioning or architecture starting point
- Siemens (or any second vendor) before Rockwell is production-credible on real programs
- Live historian/OPC integration before static trace is trustworthy
- LLM inside normalization or as source of truth for causality
- UI refactors or feature sprawl that compete with the spine workflow
- Perfect parser coverage before useful trace — but also no claiming plant readiness from demo grades alone

---

## Relationship to other documents

| Document | Role |
|----------|------|
| **This file** | Product vision, pillars, phased roadmap, principles |
| **[complex_program_bridge_plan.md](./complex_program_bridge_plan.md)** | Engineering execution guide: Phases 0–11, effort ratings, 90-day milestones, fixture grading |
| **[parsing_normalization_roadmap.md](./parsing_normalization_roadmap.md)** | Technical normalization spec per language (LD, ST, FBD, SFC) |
| **[platform_support_matrix.md](./platform_support_matrix.md)** | Current import scope and confidence per vendor |

When engineering priorities conflict, **this vision document wins on *what* and *why***; the bridge plan wins on *how* and *in what order*. If a proposed feature does not strengthen the MVP spine or a named phase above, defer it.

---

## Success criteria

We will know INTELLI is on track when:

**Phase 1 complete:** An engineer uploads a de-identified plant L5X, selects a branched-rung output or AOI-driven permissive, and receives a trace whose failing condition matches a hand-written answer key — with evidence cards and an honest confidence score.

**Phase 2 complete:** That project persists across restarts; task context appears in trace metadata; engineer-verified notes attach to tags; FBD or SFC outputs trace to upstream blocks or transition conditions; one reasoner serves trace and ask with the same explanation shape.

**Phase 3 complete:** A pilot cell runs upload → stored graph → live OPC snapshot → trace that cites both logic and live value; a Siemens export traces through the same reasoner without Rockwell-specific branches in core logic; LLM narration cites deterministic evidence and can be disabled without losing capability.

Until then, every sprint should move at least one of these criteria measurably forward — graded by `traceability_score` and answer-key tests, not by feature count or model fluency.
