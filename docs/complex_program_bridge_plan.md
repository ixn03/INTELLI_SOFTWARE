# INTELLI — Bridging to Complex Industry-Standard Programs

A prioritized, step-by-step engineering plan to take INTELLI's ingestion +
normalization layer from "handles clean, mostly-combinational ladder" to
"faithfully understands a real plant's full Rockwell program."

This plan is written for a **controls/automation engineer** first, software
second. Each software concept is explained in controls terms. Effort is rated
so you know what you can drive yourself vs. what needs a software engineer
beside you.

---

## How to read the effort ratings

| Symbol | Meaning | Rough calendar effort (1 focused dev) |
|--------|---------|----------------------------------------|
| **S**  | Small — additive, low risk | 1–3 days |
| **M**  | Medium — new logic, contained | 1–2 weeks |
| **L**  | Large — touches the data model | 3–5 weeks |
| **XL** | Extra large — new subsystem | 6–10 weeks |

**Who-drives** tag:
- **You (controls)** — you can largely own this; it's domain knowledge + small edits.
- **Pair** — you supply the controls truth, a software engineer drives the code.
- **SW-led** — needs solid software design; you review for correctness.

---

## The core idea in controls terms

Today the parser is a **text tokenizer**. Think of it like reading a rung and
writing down "I see XIC(A), XIC(B), OTE(C)" — a *list* of instructions. It does
**not** reconstruct the **series/parallel network** (which contacts are in
series, which are in parallel). In software terms, that network is called a
**boolean expression tree** (or DAG). Without it, INTELLI knows *which tags a
rung touches* but not the *exact logic* of when the output energizes.

Almost everything valuable downstream ("why is this output false?") depends on
having that network. So the plan's center of gravity is: **stop throwing away
the rung's structure.**

---

## Guiding principle: universal-model-first (read before anything else)

This plan is **not** "make Rockwell perfect, then move to Siemens." It is
"make the *vendor-neutral universal model* mature, using Rockwell as the first
exercise of it."

```
   Rockwell ─┐
   Siemens   ├──►  Universal Model  ──►  One Reasoner
   DeltaV    ┤        (reasoning.py)
   Honeywell ┘
```

**One Reasoner, Many Connectors.** A connector's only job is to convert
vendor-specific exports into the universal model. The reasoner only ever sees
universal objects. Never build a `RockwellReasoner` / `SiemensReasoner`.

### Where the universal model actually lives (important nuance)

There are **two** data models in the pipeline, and only one is the universal
model:

```
Connector → ControlProject (intermediate) → normalize → reasoning schema (UNIVERSAL) → Reasoner
            control_model.py                              reasoning.py
```

- `ControlProject` (`backend/app/models/control_model.py`) is the **connector's
  private staging area**. Vendor flavor here is acceptable.
- The `reasoning.py` schema (`ControlObject` / `Relationship` /
  `ExecutionContext`) is **THE universal model**. This is what every connector
  must converge on and what the reasoner consumes.

**Rule:** When a new vendor forces a new concept, grow the **universal model**
(`reasoning.py`), not the shared intermediate. That way every existing vendor
benefits. Resist letting `ControlProject` become an accidental
"union-of-all-vendors" model (it already has `tia_*` literals creeping in — that
is the early warning sign to watch).

### Most of the universal vocabulary already exists

| Universal concept | In `reasoning.py` today? | Object |
|-------------------|--------------------------|--------|
| LogicObject | Yes | `ControlObject` |
| LogicRead / LogicWrite | Yes | `Relationship(READS / WRITES)` |
| LogicSequence | Yes | `Relationship(SEQUENCES)` |
| LogicBlock (AOI / Siemens FB) | **Yes** (Phase 2) | `ControlObject(FUNCTION_BLOCK)` |
| LogicCondition | Yes — text + structured tree | `Relationship.logic_condition` + `LogicExpression` |
| **LogicExpression (AND/OR/NOT tree)** | **Yes** (Phase 3, Jun 2026) | `LogicExpression` in `reasoning.py` |

The deliberate net-new universal object **`LogicExpression`** (Phase 3) is now
plus making **`LogicBlock`** a genuinely shared shape (Phase 2). Phase 3 is not
a Rockwell feature — it is the universal logic representation that Rockwell
Ladder, Siemens LAD/FBD, DeltaV, and Honeywell will all reuse. That is why it is
the moat.

### "Done enough" for Rockwell (then pivot to Siemens to stress the model)

Stop at **reliable understanding**, not perfection:

✅ Ladder ✅ Structured Text (depth) ✅ AOIs ✅ UDTs ✅ Aliases ✅ Branch logic
⬜ Tasks ⬜ FBD/SFC ⬜ Durable graph ⬜ Live runtime merge

Phases 0–4 are largely complete on the curated fixture corpus (commit
`95830d2`, branch `cursor/evidence-trust-ui`). **Tasks, FBD/SFC, persistence,
and runtime truth are still open** — do not treat grader "A" grades as plant
readiness. At **~80–90% Rockwell understanding** (Phases 1–6 + persistence),
**start the Siemens connector** — not because Rockwell is "finished," but
because every new vendor exposes weaknesses in the universal model. The loop is:

```
Rockwell 80–90% → strengthen universal model → Siemens connector →
strengthen universal model → DeltaV connector → ...
```

Each vendor makes the next one cheaper, because the intelligence already lives
in the shared model.

---

## Status (verified Jun 2026, commit `95830d2`)

| Phase | Status | Evidence |
|-------|--------|----------|
| **0** Corpus + scoring | ✅ Done | 17 L5X fixtures; `parser_grade.py` scorecard; `compare_grades.py` regression diff |
| **1** Instruction registry | ✅ Largely done | Fleet known-opcode coverage ~100% on fixtures; residual unknowns (`SQO`, `END`, vendor blocks like `MBTCP_CLIENT`) |
| **2** AOI / UDT / alias | ✅ Done | Connector defs + normalizer `LogicBlock`; alias/UDT member resolution; AOI body `CALLS` |
| **3** Branch-aware logic | ✅ Done (fixture-scoped) | `LogicExpression` in `reasoning.py`; builder in `ladder_logic.py`; answer keys + eval for `INTELLI_Ladder_Test` rungs 4+8; pf525 branched rungs 100% resolved |
| **4** Structured Text depth | ✅ Done (slices 1–3) | Loops, arithmetic, nested IF/CASE, RETURN, FB/JSR/AOI in ST, cross-program JSR, nested-IF gating AND, ladder AOI body normalization; LiOH ladder `st_too_complex` 0% |
| **5** Tasks / scan order | ⬜ Not started | L5X `<Tasks>` not parsed; one execution context per routine |
| **6** FBD / SFC | ⬜ Hollow | Structural parse only; no tag-level wire/step data flow |
| **7** Persistence / graph storage | ⬜ Not started | `project_store` in-memory only; graph lost on restart |
| **8** Runtime truth + live adapters | ⬜ Not started | Manual runtime eval on curated fixtures; no OPC/live merge |
| **9** Unified reasoner | ⬜ Not started | `trace_v2`, `ask_v2`, `sequence_reasoning`, `runtime_evaluation_v2` — separate services |
| **10** Siemens connector | ⬜ Shell only | Stress-test deferred until universal model + persistence ready |
| **11** NL / ML assist | ⬜ Explicitly last | `llm_assist_service` exists; not primary reasoning layer |

**Fleet reality check:** `traceability_score` averages **~0.32** across fixtures
with logic (grader still awards "A" on many files). Upload → trace v2 → manual
runtime eval works on curated programs; that is the credible demo path today.

---

## Current state vs vision (honest, controls-friendly)

**Vision:** Connectors → Universal Model → One Reasoner.

**Today:** A capable **Rockwell compiler** (L5X → normalized graph) plus
**trace microservices** and a **demo UI**. The universal model in
`reasoning.py` is real and growing, but intelligence is scattered across
services and the graph is **ephemeral** (`project_store`).

What a controls engineer can trust today:
- Upload an L5X, get trace v2 on ladder/ST with branch-level "why false"
  on tested rungs.
- AOI/UDT/alias resolution on real-ish fixtures (LiOH, pf525, synthetic AOI).
- Deterministic normalization — same file in, same graph out.

What you cannot trust yet for plant work:
- **Scan order / task causality** (Phase 5) — periodic vs continuous context missing.
- **FBD/SFC programs** — structure only, no cause/effect (Phase 6).
- **Durable knowledge** — no DB/KG; re-upload every session (Phase 7).
- **Live I/O truth** — no OPC/runtime merge to say "tag is actually false" (Phase 8).
- **One reasoner** — ask/trace/sequence/runtime are separate paths (Phase 9).
- **Grader optimism** — letter grades overstate readiness; use `traceability_score`
  and answer-key tests as the bar.

NL/ML and live-data-assisted troubleshooting stay **out of the critical path**
until Phases 5 + 7 + 8 land.

---

## Recommended order (dependency-aware, revised)

```
Phase 0  Foundation: corpus + scoring                 ✅ Done
Phase 1  Instruction registry                         ✅ Largely done
Phase 2  AOI + UDT/alias → LogicBlock                 ✅ Done
Phase 3  Branch-aware boolean logic (moat)            ✅ Done (fixture-scoped)
Phase 4  Structured Text depth                        ✅ Done (slices 1–3)
─── below here is the path to INTELLI vision ───
Phase 5  Execution / scan + task model                (causality correctness)
Phase 6  FBD / SFC normalization                      (breadth)
Phase 7  Persistence / durable graph storage          (was implicit; now explicit)
Phase 8  Runtime truth merge + live data adapters     (OPC / snapshot / historian)
Phase 9  Unified reasoner consolidation               (one entry point on universal model)
Phase 10 Siemens connector stress test                (after model + persistence)
Phase 11 NL / ML assist layer                         (last — never in normalization)
```

Phases 0–4 moved INTELLI from "demo tokenizer" to "credible on curated
Rockwell ladder + ST." Phases 5–9 are what separate **compiler + trace demo**
from **plant troubleshooting platform**. Phase 10 validates vendor-neutrality;
Phase 11 adds explanation assist only after deterministic truth exists.

> **Ordering note:** Persistence (7) can start in parallel with Phase 5 once the
> graph schema stabilizes — you need durable storage before live runtime merge (8).
> Do **not** prioritize NL/ML or Siemens until 5 + 7 + at least one live adapter (8)
> prove the loop: export → graph → runtime snapshot → trace.

---

## Phase 0 — Foundation: real-program corpus + scoring harness

**Goal (controls terms):** Before changing how we read logic, assemble a
library of *real* exported programs and an automated "grader" that scores how
much of each program we understand. This is your regression safety net — like a
loop-check sheet you re-run after every change.

**Why it matters:** Every later phase is only trustworthy if you can prove it
didn't break what already worked, measured against real files (not toy ones).

**What changes / where:**
- `backend/tests/fixtures/l5x/` — add a set of **real, de-identified** `.L5X`
  exports covering: branched rungs, AOIs, UDTs, sequencers, timers/counters,
  multi-routine programs, multi-task projects.
- `backend/tools/parser_grade.py` — extend the existing grader to print a
  per-file scorecard: % instructions recognized, % rungs with full logic, list
  of unknown opcodes, count of unresolved tag references.

**Steps:**
1. Collect 5–10 representative `.L5X` files. Sanitize tag/equipment names if
   confidential (find-and-replace is fine).
2. For each file, **hand-write the "answer key"** for 10–20 outputs: in plain
   English, what makes each output true. (This is pure controls work — you're
   the expert.)
3. Extend `parser_grade.py` to emit the scorecard above and a coverage % per
   file.
4. Commit files + scorecards as the baseline. Re-run after every later phase.

**Effort:** **M** · **Who:** **You (controls)** — the fixtures and answer keys
are your domain; a SW engineer wires the scorecard output.

**Done when:** Running one command prints a coverage scorecard for every
fixture, and you have written answer keys to compare trace output against.

**Status: ✅ Done (Jun 2026).** 17 fixtures in `backend/tests/fixtures/l5x/`;
`backend/tools/parser_grade.py` emits per-file scorecards;
`backend/tools/compare_grades.py` diffs baselines. Answer keys exist for
`INTELLI_Ladder_Test` (rungs 4+8); expand keys to more branched/ST fixtures
as regression targets.

---

## Phase 1 — Quick win: expand the instruction registry

**Goal:** Teach INTELLI the common Rockwell instructions it currently drops to
"unknown." This is **additive** and low-risk — the perfect first code task to
learn the codebase.

**Why it matters:** On real programs, instructions like `MSG`, `GSV/SSV`,
sequencers (`SQO/SQI/SQL`), `BTD`, `CLR`, `FLL` appear constantly. Today each
one becomes a structural node with **no cause/effect edges**, so coverage looks
worse than it should.

**What changes / where:**
- `backend/app/services/normalization_service.py` — the `INSTRUCTION_SEMANTICS`
  registry (around line 259+). Each instruction is one dictionary entry
  declaring which operands are read vs. written. The module is explicitly
  designed so "enabling new instructions is a registry edit, not a new code
  path."
- `backend/app/parsers/ladder.py` — add the new opcodes to the role/family
  helper sets (e.g. `MATH_INSTRUCTIONS`, `MOVE_INSTRUCTIONS`) so they classify
  correctly.

**Steps (repeat per instruction):**
1. Pick an instruction (start with `CLR`, `FLL`, `BTD`, `SWPB` — simple
   read/write semantics).
2. Add a registry entry: which operand index is the destination (WRITES),
   which are sources (READS), and the write behavior.
3. Add a fixture rung using it; confirm the grader now shows edges for it.
4. Move to harder ones: `SQO/SQI` (sequencers), `MSG`, `GSV/SSV`.

**Effort:** **S per instruction** (a few hours each) · **Who:** **You (controls)**
— you know exactly what each instruction reads and writes; the registry pattern
is copy-paste.

**Done when:** The "unknown opcode" list in the Phase 0 scorecard shrinks to
genuinely exotic instructions only.

**Status: ✅ Largely done.** Fixture fleet shows ~100% known-opcode hits on
most files. Residual unknowns: `SQO` (synthetic AOI fixture), `END`, and
vendor/library blocks (`MBTCP_CLIENT`, `SYS_*`). `PID` registered but edges
deferred per plan.

**Explicitly defer:** `PID/PIDE` deep semantics (control-loop math) — register
them as recognized but leave full behavior for later; just capture the SP/PV/CV
tags as READS/WRITES.

---

## Phase 2 — AOI + UDT / alias resolution

**Goal:** Make INTELLI "see inside" the building blocks engineers actually use:
Add-On Instructions (AOIs), User-Defined Types (UDTs), and alias tags.

**Why it matters:** Modern Rockwell programs are built almost entirely from
AOIs and UDTs. Today an AOI instance is a black box and `Motor.Run` /
alias tags are opaque strings. This is likely the **single biggest coverage
gap** on real code — a program can look 70% "unknown" purely because it's
AOI-based.

This phase has two halves: **(A) connector exposes the definitions**, then
**(B) normalizer resolves them.**

### 2A — Connector: expose definitions & aliases

**What changes / where:**
- `backend/app/connectors/rockwell_l5x.py` — parse the L5X
  `<AddOnInstructionDefinitions>` section (parameters: Input/Output/InOut +
  the internal logic routine) and the `<Tags>` alias info (`AliasFor`) and UDT
  (`<DataTypes>`) member structure.
- `backend/app/models/control_model.py` — add lightweight models:
  `AddOnInstructionDef` (name, params with usage, internal routine),
  `DataTypeDef` (UDT members), and an `alias_for` field on `ControlTag`.

**Steps:**
1. In the L5X, locate `AddOnInstructionDefinitions`, `DataTypes`, and alias
   attributes (a controls engineer recognizes these instantly in the export).
2. Add the parsing to the connector; attach defs to the `ControlProject`.
3. Add fixtures: one AOI-based program, one with alias tags, one with UDTs.

**Effort:** **M** · **Who:** **Pair** — you point at the right XML sections and
explain parameter usage (Input vs InOut matters for read/write direction); SW
engineer writes the parsing.

### 2B — Normalizer: resolve them into edges

**What changes / where:**
- `backend/app/services/normalization_service.py` —
  - When an AOI **instance** appears in a rung, resolve its parameter list:
    Input params → `READS`, Output params → `WRITES`, InOut params →
    `READS` + `WRITES`, against the bound tags. (This is the TODO at line 123.)
  - Resolve alias tags to their base tag so trace follows the real signal.
  - Resolve UDT member access (`Motor.Run`) so the member and base tag are
    linked.

**Steps:**
1. Map AOI parameter usage → relationship direction (you define the rule;
   it's controls semantics).
2. Emit edges from instance operands to bound tags.
3. Optionally: link the instance to its internal logic routine via `CALLS` so
   trace can descend into the AOI body.
4. Re-run the grader; AOI-heavy fixtures should jump in coverage.

**Effort:** **L** · **Who:** **SW-led**, you review — the resolution logic is
fiddly software, but every directional decision is yours to confirm.

> **Vendor-neutral design constraint (universal-model-first):** Do **not** model
> the AOI as a Rockwell-only structure. Design it as a universal **`LogicBlock`**
> = (block definition + typed parameter bindings with In / Out / InOut
> direction). This is the *same shape* a Siemens Function Block (FB) will map to
> later: `Rockwell AOI → LogicBlock` and `Siemens FB → LogicBlock`. Likewise
> `Rockwell UDT → LogicObject` and `Siemens DB → LogicObject`. If the model
> only makes sense for Rockwell, it's wrong — redesign it so the reasoner can't
> tell which vendor it came from.

**Done when:** A trace on an AOI output correctly lists the bound input tags as
causes, and alias/UDT references resolve to base tags in the graph.

**Status: ✅ Done (Jun 2026).** AOI instances normalize to universal
`LogicBlock` (`FUNCTION_BLOCK`); alias `alias_for` and UDT member paths
resolve in trace. LiOH full export exercises real AOI defs + ST instance
calls. **Remaining gap:** ladder AOI *body* logic is linked via `CALLS` but
deep in-body trace is still thinner than top-level rung trace.

---

## Phase 3 — Branch-aware boolean logic (the moat)

**Goal:** Reconstruct the **series/parallel network** of each rung as a real
boolean expression, so INTELLI can answer "why is this output false?" exactly,
not approximately.

**Why it matters:** This is the core of INTELLI's value. Today every branched
rung carries only a text string for its condition (see the TODO at
`normalization_service.py` line 127). Branched rungs are everywhere, so this is
the **biggest correctness gap.**

**The software concept, in controls terms:** We replace the flat instruction
list with a **boolean tree**:
- Contacts in **series** → an `AND` node.
- Contacts in **parallel** (a branch) → an `OR` node.
- `XIO` → a `NOT` around a contact.

The rung output is then a single boolean expression of tags. That's exactly the
mental model you already use reading a rung — we're just making the software
store it.

> **Vendor-neutral design constraint (universal-model-first):** `LogicExpression`
> is the single most important *universal* object — it is NOT a Rockwell feature.
> It must live in the **universal model** (`reasoning.py`), because Siemens
> LAD/FBD, DeltaV, and Honeywell logic all reduce to the same AND/OR/NOT tree.
> **Before writing code, do the two-vendor paper test:** take one Rockwell
> branched rung and one Siemens FBD network that express the same interlock, and
> confirm the *same* `LogicExpression` tree represents both (a Siemens FBD `AND`
> block and a Rockwell series contact group both collapse to
> `LogicExpression.AND([...])`). If they don't map cleanly, fix the design
> before it reaches code. (You supply the two examples; SW turns the agreed tree
> into the model.)

**What changes / where:**
- `backend/app/parsers/ladder_logic.py` — build the series/parallel tree from
  `BST`/`NXB`/`BND` nesting (extracted from `ladder.py` tokenizer output).
- `backend/app/models/reasoning.py` — `LogicExpression` type (AND/OR/NOT/contact
  leaves) on the universal model; rung/edge carries structured condition plus
  display text.
- `backend/app/services/trace_v2_service.py` / runtime eval — walk the tree for
  precise "this branch satisfied, that one didn't" answers.

**Steps:**
1. **Design the tree model** with a software engineer (AND/OR/NOT + leaf =
   contact + tag + examined value). Keep it small.
2. **Build it in the parser** from the `BST/NXB/BND` structure already tracked.
   Start with single-level branches, then nested.
3. **Carry it through normalization** onto the rung/edge as a structured
   condition (keep the old text string too, for display).
4. **Teach trace** to walk the tree: for a false output, identify which AND
   term / which OR branch failed.
5. Validate against your Phase 0 answer keys on branched rungs.

**Effort:** **L–XL** · **Who:** **SW-led**, you are the **acceptance authority**
— you confirm the reconstructed logic matches the rung as drawn.

**Done when:** For a branched rung, trace explains the exact failing term/branch
and it matches your hand-written answer key.

**Status: ✅ Done on fixture corpus (Jun 2026).** `LogicExpression` +
`evaluate_logic_expression` in place; `INTELLI_Ladder_Test` rungs 4+8 have
answer keys + automated acceptance tests; pf525 branched rungs 100% resolved.
**Remaining gaps:** not every fixture has answer keys; some files still show
~88–89% `logic_expression_resolved_pct` (unbranched/complex rungs fall back to
text). Expand keys before claiming plant-wide correctness.

**Scope discipline:** Keep the conservative policy — if a rung's structure is
ambiguous (ASCII art, exotic notation), fall back to the text-string condition
and flag it, rather than guessing. (Matches the existing "never fabricate"
policy in the roadmap.)

---

## Phase 4 — Structured Text depth

**Goal:** Handle the ST constructs real programs use beyond simple
assignments and `IF/CASE`.

**Why it matters:** Today `FOR`/`WHILE`/`REPEAT`, arithmetic-only RHS, and
function/FB calls are flagged `too_complex` (see
`backend/app/parsers/structured_text_blocks.py`). Modern programs use these
heavily, so ST coverage is currently shallow.

**What changes / where:**
- `backend/app/parsers/structured_text_blocks.py` — extend the block grammar:
  loops (as blocks with their condition + body reads/writes), arithmetic RHS,
  FB/function call lines.
- `backend/app/parsers/st_expression.py` — extend the expression parser
  (arithmetic, nested parens, `OR`) so more READS are extracted soundly.
- `backend/app/services/normalization_service.py` —
  `_normalize_structured_text_routine` to emit edges for the new shapes.

**Steps:**
1. Add `FOR/WHILE/REPEAT` as recognized blocks (condition → READS, body
   reads/writes). Don't unroll loops — just capture data flow.
2. Extend the expression parser for arithmetic + parentheses + `OR`.
3. Handle FB/function call lines as `CALLS` with parameter reads/writes.
4. Validate with ST fixtures.

**Effort:** **M–L** · **Who:** **Pair** — you define correct data-flow
semantics; SW engineer extends the parser.

**Done when:** The `too_complex` rate on real ST routines drops substantially
and loop/call data flow shows up in trace.

**Status: ✅ Done — slices 1–3 + closure (Jun 2026, `95830d2`).** Handles
`FOR`/`WHILE`/`REPEAT`, arithmetic RHS, nested IF/CASE/RETURN, FB/JSR/AOI
calls, cross-program JSR, nested-IF gating as AND, ladder AOI body
normalization. LiOH ladder fixture: `st_too_complex` 0%. **Residual:**
LiOH full export 5.9%; pf525 ST 2.7% — exotic constructs still flagged, not
guessed.

---

## Phase 5 — Execution / scan + task model

**Goal:** Represent the real controller execution model: multiple tasks
(continuous / periodic / event), programs within tasks, and scan order.

**Why it matters:** Today the normalizer emits **one execution context per
routine** and doesn't model tasks. Causality across the program (what runs
before what, what's in a periodic task) can be wrong without this. Real
projects have several tasks at different rates/priorities.

**What changes / where:**
- `backend/app/connectors/rockwell_l5x.py` — parse the `<Tasks>` section (task
  type, rate, priority, scheduled programs).
- `backend/app/models/control_model.py` — add `Task` (type, rate, priority,
  program list).
- `backend/app/models/reasoning.py` / normalization — model tasks as execution
  contexts and order programs/routines within them.

**Steps:**
1. Parse tasks and their scheduled programs from the L5X.
2. Build the task → program → routine execution hierarchy.
3. Let trace use scan order/task context when ordering causes.

**Effort:** **M** · **Who:** **Pair** — you explain task semantics (continuous
vs periodic vs event, priority), SW engineer models it.

**Done when:** The graph shows tasks with their rates and scheduled programs,
and trace respects scan/task context.

**Status: ⬜ Not started.** Connector still walks Programs only; fixtures
contain `<Tasks>` sections that are ignored.

---

## Phase 6 — FBD / SFC normalization

**Goal:** Turn the existing structural FBD/SFC parsing into real cause/effect:
wires → READS/WRITES, steps/transitions → sequence + condition edges.

**Why it matters:** Many process and batch applications are FBD/SFC. Today
these are parsed structurally only (blocks/steps exist, but no tag-level data
flow) — see the TODOs at `normalization_service.py` lines 116–122.

**What changes / where:**
- `backend/app/parsers/fbd.py` — resolve wires between pins to tag-level data
  flow.
- `backend/app/parsers/sfc.py` — resolve step/transition conditions to tag
  reads.
- `backend/app/services/normalization_service.py` — `_normalize_routine` FBD/SFC
  path (lines ~846–923): emit `READS`/`WRITES` from wires and
  `SEQUENCES`/`CONDITION_FOR` with tag conditions from transitions.

**Steps:**
1. FBD: map each wire from output pin → input pin into a data-flow edge; resolve
   pins bound to tags into READS/WRITES.
2. SFC: turn transition expressions into tag READS and `CONDITION_FOR` edges;
   order steps via `SEQUENCES`.
3. Validate with FBD/SFC fixtures.

**Effort:** **XL** (effectively two new normalizers) · **Who:** **SW-led**, you
define correct semantics.

**Done when:** A trace on an FBD output or SFC step lists the upstream blocks /
transition conditions as causes.

**Status: ⬜ Not started (hollow).** FBD/SFC objects preserved structurally;
normalizer TODOs at `normalization_service.py` lines 116–122 unchanged.

---

## Phase 7 — Persistence / durable graph storage

**Goal:** Store normalized graphs durably so projects, diffs, and traces survive
restarts — the foundation for a real knowledge graph, not an upload session.

**Why it matters:** `project_store` is in-process only. Without persistence,
there is no fleet history, no cross-session trace, and no serious live-data
correlation.

**What changes / where:**
- Replace or back `project_store` with a DB or graph store (SQLite/Postgres +
  optional graph layer).
- Version normalized output per `file_hash` + upload timestamp.
- API: list projects, diff two versions, trace by stable project id.

**Effort:** **L** · **Who:** **SW-led**, you define retention/versioning needs.

**Done when:** Re-upload is optional; trace v2 works against a stored project
after backend restart.

**Status: ⬜ Not started.**

---

## Phase 8 — Runtime truth merge + live data adapters

**Goal:** Merge **program logic truth** (graph) with **runtime truth** (live or
snapshotted tag values) so trace answers reflect what the plant is doing now.

**Why it matters:** Today runtime eval is manual/curated. Live troubleshooting
requires OPC UA, FactoryTalk, or historian snapshots feeding the same tag model
the normalizer built.

**What changes / where:**
- `runtime_snapshot_service` / `runtime_evaluation_v2_service` — wire to real
  adapters, not fixture-only snapshots.
- Tag identity map: normalized tag id ↔ live OPC node id.
- Trust/evidence layer: distinguish "logic says false" vs "I/O reads false."

**Effort:** **L–XL** · **Who:** **Pair** — you define tag↔I/O mapping rules;
SW builds adapters.

**Done when:** Upload L5X + connect OPC (or import snapshot) → trace v2 uses
live values on at least one pilot program.

**Status: ⬜ Not started** (blocked on Phase 7 for production use).

---

## Phase 9 — Unified reasoner consolidation

**Goal:** One reasoning entry point over the universal model — not separate
`trace_v2`, `ask_v2`, `sequence_reasoning`, and `runtime_evaluation_v2` paths.

**Why it matters:** Fragmented services duplicate graph walks and drift in
semantics. The vision is **One Reasoner, Many Connectors**.

**What changes / where:**
- Consolidate question routing into a single reasoner module consuming
  `ControlObject` / `Relationship` / `LogicExpression`.
- Keep services as thin API facades if needed; core logic lives once.

**Effort:** **L** · **Who:** **SW-led**, you validate trace answers unchanged.

**Done when:** "Why is X false?" and "What sequences before Y?" share one graph
walk and one explanation format.

**Status: ⬜ Not started.**

---

## Phase 10 — Siemens connector (universal-model stress test)

**Goal:** Second vendor connector to prove `LogicBlock`, `LogicExpression`, and
execution contexts are truly vendor-neutral.

**Why it matters:** Rockwell-first development hides model assumptions. Siemens
LAD/FBD/FB exposes different parameter binding and block shapes.

**Effort:** **XL** · **Who:** **Pair** — you supply TIA export examples; SW
maps to universal model.

**Done when:** Same reasoner (Phase 9) traces a Siemens program without
Rockwell-specific branches in core logic.

**Status: ⬜ Shell only** — defer until Phases 5–7 + reasoner sketch (9).

---

## Phase 11 — NL / ML assist layer (explicitly last)

**Goal:** Natural-language explanation and search **on top of** deterministic
trace — never inside normalization.

**Why it matters:** LLMs can phrase answers well but must not invent causality.
Normalization stays deterministic (Guardrail #2).

**What changes / where:**
- `llm_assist_service` — consume reasoner output + evidence bundle only.
- UI: assistive narrative, not authoritative trace.

**Effort:** **M** · **Who:** **SW-led**, you review for controls accuracy.

**Done when:** NL answers cite deterministic trace paths; disabling LLM leaves
full troubleshooting capability.

**Status: ⬜ Exists as assist stub; not primary layer.**

---

## At-a-glance summary

| Phase | Gap closed | Effort | Who drives | Status |
|-------|-----------|--------|-----------|--------|
| 0 | Test corpus + scoring | M | You (controls) | ✅ Done |
| 1 | Instruction coverage | S each | You (controls) | ✅ Largely done |
| 2 | AOI / UDT / alias → LogicBlock | M + L | Pair / SW-led | ✅ Done |
| 3 | Branch-aware boolean logic | L–XL | SW-led | ✅ Done (fixture-scoped) |
| 4 | Structured Text depth | M–L | Pair | ✅ Done (slices 1–3) |
| 5 | Execution / task model | M | Pair | ⬜ Next |
| 6 | FBD / SFC normalization | XL | SW-led | ⬜ |
| 7 | Persistence / graph storage | L | SW-led | ⬜ |
| 8 | Runtime truth + live adapters | L–XL | Pair | ⬜ |
| 9 | Unified reasoner | L | SW-led | ⬜ |
| 10 | Siemens stress test | XL | Pair | ⬜ |
| 11 | NL / ML assist | M | SW-led | ⬜ Last |

---

## Guardrails to keep throughout (already INTELLI's policy)

1. **Never fabricate.** If structure is ambiguous, flag it `unsupported` /
   `too_complex` — don't guess. (Roadmap section 5.)
2. **Deterministic, no LLM in normalization.** Same file in → same graph out.
3. **Re-run the Phase 0 grader after every change.** Coverage must go up and
   never silently regress.
4. **One instruction / one construct at a time.** Small, reviewable steps beat
   big rewrites — especially while you're building software fluency.
5. **Grade on `traceability_score`, not letter grade.** Fleet average ~0.32
   today; "A" on sparse fixtures is not plant readiness.

---

## Next steps — prioritized 90-day roadmap

Tied to vision milestones: **durable graph → correct scan order → live truth →
one reasoner**. NL/ML and Siemens stay off the critical path until then.

### Days 1–30 — Phase 5 kickoff + measurement honesty

| Milestone | Who | Outcome |
|-----------|-----|---------|
| Parse L5X `<Tasks>` (type, rate, priority, scheduled programs) | **Pair** | Task → program hierarchy in `ControlProject` + `ExecutionContext` |
| Grader: task coverage + scan-order fields | **Pair** | Scorecard shows task parse rate per fixture |
| Expand answer keys to 3 more branched/ST fixtures (e.g. pf525, LiOH ladder) | **You (controls)** | Regression bar beyond rungs 4+8 |
| Close residual Phase 1 unknowns (`SQO`, `END`) | **You (controls)** | Registry entries + fixture rungs |

**Vision checkpoint:** Trace can state *which task* owns a routine (even if
ordering logic is minimal).

### Days 31–60 — Phase 7 persistence + Phase 5 completion

| Milestone | Who | Outcome |
|-----------|-----|---------|
| DB-backed project store (survives restart) | **SW-led** | Stable project id; normalized graph cached on disk |
| Trace v2 reads from stored graph (no re-normalize) | **SW-led** | Upload-once, trace-many sessions |
| Trace respects task/scan context for causality ordering | **Pair** | Periodic vs continuous shown in trace metadata |
| Project diff API wired to stored versions | **SW-led** | Supports change review workflows |

**Vision checkpoint:** Graph is **durable**; causality includes **when** logic runs.

### Days 61–90 — Phase 8 pilot + reasoner sketch

| Milestone | Who | Outcome |
|-----------|-----|---------|
| One live adapter (OPC UA or snapshot CSV) for pilot tags | **Pair** | Runtime eval uses real values, not hand-entered |
| Tag map: normalized id ↔ live node | **Pair** | Evidence layer distinguishes logic vs I/O |
| Reasoner consolidation design doc + shared graph-walk module | **SW-led** | Single path for trace + ask; services become facades |
| Phase 6 scoping: pick one FBD or SFC fixture for first wire | **You (controls)** | Breadth work queued with clear acceptance test |

**Vision checkpoint:** **Upload → store → live snapshot → trace** works on one
pilot cell. Siemens connector (Phase 10) and NL assist (Phase 11) start only
after this loop is proven.

### Explicitly defer (90 days)

- **Phase 6 full FBD/SFC** — start scoping only; implementation after persistence.
- **Phase 10 Siemens** — until universal model survives second-vendor paper test.
- **Phase 11 NL/ML as primary** — assist narrative only, never in normalization.
