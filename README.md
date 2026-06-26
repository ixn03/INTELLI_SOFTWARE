# INTELLI_SOFTWARE
# This was created by Ian Gaic Nana Nzouakeu. We are currently 5/12/2026 and I finally decided to seriously start working on this.
# At my previous job at Georgia-Pacific and even now at Tesla, i have noticed often times how engineers struggle with wasted time on troubleshooting Controls systems
# documentations are not always accurate, sometimes they do not have good ways of capturing faults, or even parsing PLC or DCS programs to understand exactly what is happening
# Intelli will be a reasoning and intelligence engine
# This will include: Data ingestion layer , parsing layer, normalization layer, deterministic reasoning layer, LLM interpretation, knowledged storage  

# Copyright (c) 2026 INTELLI Software

# All rights reserved.

# This software and associated documentation are proprietary and confidential.
# Unauthorized copying, distribution, modification, or use is strictly prohibited.

## Live data stack (Docker)

Local InfluxDB, PostgreSQL, Neo4j, and FastAPI backend for OPC UA ingestion MVP:

```bash
cd infra
cp env.example .env
docker compose --env-file .env up -d --build
```

See [infra/README.md](infra/README.md) for ports, volumes, health checks, and stop/reset commands.

## Step 2 — Tag registry

INTELLI separates **raw source names** from **canonical tag identity**:

| Field | Example | Meaning |
|-------|---------|---------|
| `raw_name` | `Program:MainProgram.P101_RunningFB` | Address/name from OPC UA / PI / DeltaV |
| `canonical_name` | `P101_RunningFB` | INTELLI troubleshooting identity |
| `equipment_id` | P101 Pump | What the tag belongs to |
| `tag_role` | `FEEDBACK` | COMMAND, FEEDBACK, PERMISSIVE, etc. |

After starting the Docker stack:

```bash
curl http://localhost:8000/api/tags
curl http://localhost:8000/api/data-sources
```

Run registry API tests:

```bash
cd backend
pip install -r requirements.txt
python -m pytest tests/test_tag_registry_api.py -q
```

More examples: [infra/README.md](infra/README.md#step-2--tag-registry-api-examples).

## Step 3 — Live tag ingestion (OPC UA → InfluxDB)

Production-style live data path:

```text
Prosys OPC UA Simulation Server
        ↓
INTELLI OPC UA Collector (backend/tools/opcua_collector.py)
        ↓
POST /api/ingest/tag-samples/batch
        ↓
Backend validation + normalization → InfluxDB
        ↓
GET /api/tags/{tag_id}/latest | /history
```

The collector is **stateless** and posts samples to the backend only — it never writes to InfluxDB directly.

### Configure Prosys OPC UA endpoint

Set `OPCUA_ENDPOINT_URL` in `infra/.env`. When the collector runs inside Docker and Prosys runs on your Windows host, use `host.docker.internal`:

```bash
OPCUA_ENDPOINT_URL=opc.tcp://host.docker.internal:53530/OPCUA/SimulationServer
OPCUA_SECURITY_POLICY=None
OPCUA_SECURITY_MODE=None
OPCUA_AUTH_MODE=Anonymous
INTELLI_BACKEND_URL=http://localhost:8000
```

### Create Prosys variables (Simulation/PumpSystem)

In **Prosys OPC UA Simulation Server**:

1. Open **Address Space** → **Objects** → create folder `Simulation` (if missing).
2. Under `Simulation`, create folder `PumpSystem`.
3. Add variables with these **Browse names** (BOOL unless noted):

| Variable | Type | Demo NodeId (ns=3) |
|----------|------|-------------------|
| P101_RunCmd | BOOL | `ns=3;i=1009` |
| P101_RunningFB | BOOL | `ns=3;i=1010` |
| P101_Fault | BOOL | `ns=3;i=1011` |
| XV101_OpenCmd | BOOL | `ns=3;i=1012` |
| XV101_OpenFB | BOOL | `ns=3;i=1013` |
| Tank101_Level | REAL | `ns=3;i=1014` |

Prosys assigns numeric NodeIds when you create variables. Copy the actual NodeId from **Browse** (right-click variable → copy NodeId) — it must match the registry.

### Discover NodeIds with list_opcua_nodes.py

```bash
cd backend
pip install -r requirements.txt
set OPCUA_ENDPOINT_URL=opc.tcp://localhost:53530/OPCUA/SimulationServer
python tools/list_opcua_nodes.py
```

The script browses `Simulation/PumpSystem` and prints DisplayName, BrowseName, NodeId, DataType, and AccessLevel. If PumpSystem is missing, it lists what it finds under Objects.

### Map NodeIds in the tag registry

Set **both** `node_id` and `source_address` on each tag (`source_system=OPC_UA`, `raw_name` = simple name like `P101_RunCmd`).

Demo seed (Docker init + `seed.py`) uses the numeric NodeIds above. If your Prosys install differs:

**Option A — PATCH via API:**

```bash
curl -X PATCH http://localhost:8000/api/tags/11111111-1111-1111-1111-111111111201 \
  -H "Content-Type: application/json" \
  -d '{"node_id": "ns=3;i=1009", "source_address": "ns=3;i=1009"}'
```

**Option B — re-seed:** update `infra/postgres/init/02-tag-registry.sql` and `backend/app/db/seed.py`, then recreate volumes (`docker compose down -v && up -d --build`).

### Option A: validate with Prosys OPC UA collector

On the host (backend running locally):

```bash
cd backend
pip install -r requirements.txt
set OPCUA_ENDPOINT_URL=opc.tcp://NANA:53530/OPCUA/SimulationServer
set OPCUA_SECURITY_POLICY=None
set OPCUA_SECURITY_MODE=None
set OPCUA_AUTH_MODE=Anonymous
set INTELLI_BACKEND_URL=http://localhost:8000
python tools/opcua_collector.py
```

Via Docker Compose (profile `opcua`):

```bash
cd infra
docker compose --env-file .env --profile opcua up -d opcua-collector
docker compose --env-file .env logs -f opcua-collector
```

### Option B: validate with demo live data publisher

If Prosys certificate/session settings block browsing or the custom Prosys variables are still `Null`, use the demo publisher. It reads the same six demo tags from `/api/tags`, generates changing values, and posts batches to `/api/ingest/tag-samples/batch`. The backend still validates the registry metadata and writes accepted samples to InfluxDB.

```bash
cd backend
pip install -r requirements.txt
set INTELLI_BACKEND_URL=http://localhost:8000
set DEMO_PUBLISH_INTERVAL_MS=1000
python tools/demo_live_data_publisher.py
```

For a finite smoke test:

```bash
set DEMO_PUBLISH_COUNT=20
python tools/demo_live_data_publisher.py
```

By default the publisher uses each tag's registry `source_system` (the demo seed is `OPC_UA`) so source validation is identical to the OPC UA collector path. To validate tags reconfigured as simulator tags, set `DEMO_SOURCE_OVERRIDE=SIMULATOR`.

### Ingestion examples

Single sample:

```bash
curl -X POST http://localhost:8000/api/ingest/tag-samples \
  -H "Content-Type: application/json" \
  -d '{
    "tag_id": "11111111-1111-1111-1111-111111111201",
    "raw_tag_name": "P101_RunCmd",
    "value": true,
    "timestamp": "2026-06-05T12:00:00Z",
    "quality": "good",
    "source": "OPC_UA"
  }'
```

Batch:

```bash
curl -X POST http://localhost:8000/api/ingest/tag-samples/batch \
  -H "Content-Type: application/json" \
  -d '{"samples": [{"tag_id": "11111111-1111-1111-1111-111111111201", "raw_tag_name": "P101_RunCmd", "value": true, "timestamp": "2026-06-05T12:00:00Z", "quality": "good", "source": "OPC_UA"}]}'
```

### Latest and history examples

```bash
curl http://localhost:8000/api/tags/11111111-1111-1111-1111-111111111201/latest
curl "http://localhost:8000/api/tags/11111111-1111-1111-1111-111111111201/history?limit=50"
```

After the collector or demo publisher has posted several batches, verify all six demo tags:

```bash
cd backend
set INTELLI_BACKEND_URL=http://localhost:8000
python tools/verify_live_ingestion.py
```

### Tests

```bash
cd backend
python -m pytest tests/test_tag_ingestion_api.py tests/test_tag_registry_api.py tests/test_live_snapshot_service.py tests/test_troubleshoot_live_data_api.py -q
```

## Step 4 — Live data in troubleshooting workspace

When you ask a troubleshooting question, INTELLI can auto-fetch latest values from **tag registry + InfluxDB** and merge them into the workspace `current_state_explanation`.

```text
L5X upload → normalized graph (in-memory)
        +
Tag registry canonical_name ↔ control-object name
        +
InfluxDB latest sample
        ↓
POST /api/troubleshoot/question  (use_live_data: true)
        ↓
Workspace shows target live value + blocking/satisfied conditions
```

### How linking works

1. Workspace collects the **target signal** and **upstream condition** tags from the parsed graph.
2. Each signal is matched to a registry tag by (in order):
   - `tag_logic_refs.reference_key` == control object id
   - `canonical_name` or `raw_name` == signal name
   - `tag_aliases.alias_name` == signal name
3. Latest values are read from InfluxDB and keyed by both control-object id and signal name.

### API example

```bash
curl -X POST http://localhost:8000/api/troubleshoot/question \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<your-project-id>",
    "question": "Why is P101_RunCmd not energizing?",
    "use_live_data": true
  }'
```

Check `current_state_explanation` and `advanced_details.live_data` in the response.

### Prerequisites

- Docker stack running (Postgres tag registry + InfluxDB)
- OPC UA collector or `demo_live_data_publisher.py` posting samples
- L5X tag names aligned with registry `canonical_name` (demo: `P101_RunCmd`, etc.)

The workspace UI sends `use_live_data: true` automatically.

### Conditions panel (Signal Intelligence UI)

In **What controls this? → Boolean control conditions**, each upstream condition shows a live badge when `current_state_explanation` includes matching values (`Permissive_A FALSE · OPC_UA`, etc.). The signal header shows the target's live value. Missing registry/Influx matches render `no live` on individual conditions.

## Phase 2 — Durable project persistence

Uploaded L5X projects and their normalized graphs are stored in Postgres `stored_projects` (JSONB blobs keyed by `file_hash`). The in-process cache is still used for speed; on restart or `GET /projects/{id}`, the store hydrates from the database when `DATABASE_URL` points at Postgres (or file-backed SQLite).

```text
POST /upload  →  project_store.save()  →  stored_projects row
GET /projects/{id}  →  hydrate from DB if not in memory
get_normalized()  →  lazy normalize + persist normalized_blob
```

Docker init: `infra/postgres/init/03-stored-projects.sql`. If the stack was created before this migration, recreate volumes (`docker compose down -v && docker compose up -d`).

Tests: `python -m unittest tests.test_project_persistence`

## Phase 1 — MVP spine complete (fixture corpus)

Product Phase 1 success criteria: upload L5X → ask about a tag → get deterministic trace with evidence and confidence on ladder, ST, AOI, and branched rungs.

Run the acceptance gate:

```bash
cd backend
python tools/phase1_gate.py
```

Gate checks: full pytest, troubleshooting eval harness (direct + route), ladder answer keys, and parser grade on 17 committed L5X fixtures.

**Plant-scale programs** (full multi-program controllers) are tracked separately via `traceability_score` and private L5X eval — see `docs/product_vision_roadmap.md` Phase 2.

## Internal Model Architecture

INTELLI treats vendor files as source inputs, not as the reasoning surface.
Connectors parse each vendor export into a deterministic intermediate model,
then the normalizer converts that model into vendor-neutral control objects,
relationships, and execution contexts. The LLM layer should only explain,
query, or summarize that normalized model; it should not reason directly over
raw controller exports.

The parsed intermediate model now has generic logic IR objects for common
controls concepts:

- `LogicObject`, `LogicBlock`, `LogicCondition`, `LogicRead`, `LogicWrite`,
  `LogicBranch`, and `LogicSequence`
- controller hierarchy objects: `Controller`, `Program`, `Routine`, and `Tag`
- Rockwell AOI staging objects exposed through generic names:
  `AOIDefinition` and `AOIInstance`
- ladder objects: `LadderRung` and `LadderBranch`
- FBD/SFC structural objects:
  `FBDBlock`, `FBDPin`, `SFCStep`, and `SFCTransition`

Vendor-specific fields belong in `metadata` or `platform_specific`; concepts
that are true across PLC/DCS platforms should be promoted to the neutral model.
Source locations are structural paths, so reads, writes, and blocks can be
audited without copying source logic into documentation or fixtures.

## Control Dependency Graph

The normalized model now feeds a deterministic dependency graph generator.
For each normalized write, INTELLI links the written tag to the upstream tags
and structured conditions read by the same logic object. The graph records
tag provenance: who writes a tag, who reads it, where those accesses occur,
which conditions gate the write, and which evidence is still missing for a
high-confidence troubleshooting conclusion.

The dependency graph is intentionally vendor-neutral and LLM-free. Unknown
logic blocks can be preserved as direction-unknown references for review, but
they do not create inferred cause-and-effect edges until their parameter
directions are known.

### FBD Phase 1

Function Block Diagram support is structural. Rockwell FBD exports that expose
blocks, pins, and wires are normalized into `FBDBlock`, `FBDPin`, and
connection relationships. Explicit input pins can read tags, explicit output
pins can write tags, and output-to-input wires can participate in dependency
tracing. Unknown-direction pins remain `REFERENCES` with direction-unknown
metadata and do not create cause/effect edges.

When an export represents block pins through compact attributes rather than
child elements, INTELLI preserves those visible pins as neutral `FBDPin`
objects. A visible pin starts as direction-unknown and is upgraded only when a
wire endpoint deterministically shows it acting as a source or target. Layout
coordinates are kept as vendor metadata for traceability, not used as logic
semantics.

Phase 1 does not infer vendor block behavior from block type names. If an FBD
routine is present but the export contains no block/pin/wire body, INTELLI
preserves the routine as unsupported structure rather than inventing a diagram.

## Unified Evidence Layer

The troubleshooting layer consumes a unified signal evidence model, not
parser-specific objects. `unified_evidence_service.py` merges normalized
ladder, FBD, ST, AOI, and unknown references into one answer shape:

- what controls this signal
- who writes this signal
- where it is used
- unknown-direction references (not treated as deterministic causes)

Each evidence item preserves `SourceProvenance` (routine, rung, block, pin,
statement, source location, originating language/platform) so engineers can
verify the answer without caring which parser produced the edge internally.

The Signal Troubleshooting Workspace now returns a static Signal Intelligence
view for each resolved signal:

1. `resolved_scope`: controller, program, duplicate-name status, and scoped tag
   candidates when the same tag name exists in multiple scopes.
2. `what_controls_this_signal`: upstream required conditions, dependency edges,
   writer conditions, and direction-unknown references kept separate from
   deterministic causes.
3. `what_this_signal_controls`: downstream readers, downstream writes
   influenced by this signal, and downstream routine/block/statement evidence.
4. `who_writes_this_signal` and `where_evidence_comes_from`: writer evidence
   grouped by Ladder, FBD, AOI, ST, SFC, or unknown, with provenance such as
   routine, rung, block, pin, statement, source location, and relationship ID.
5. `knowledge_context`, `current_state_explanation`, and `historical_context`:
   linked engineer/documentation facts supplement deterministic logic; live
   values explain blocking/satisfied conditions only when supplied; history is
   an explicit `not_available` stub until event reasoning is implemented.

The frontend presents this as five controls-engineering sections:

1. What controls this?
2. What does this control?
3. Current state explanation
4. Evidence sources
5. Engineer/documentation knowledge

Advanced relationship IDs remain collapsed by default.

Confidence is deterministic and evidence-based. No LLM scoring is used in this
layer. Missing live state, missing history, and missing engineer knowledge are
reported explicitly instead of inferred.

### Troubleshooting eval harness

Offline benchmark for the workspace endpoint (no LLM, no live PLC):

```bash
cd backend
PYTHONPATH=. python tools/troubleshoot_eval.py --pretty
```

The suite in `backend/tests/fixtures/troubleshoot_eval/suite.json` uses synthetic
graphs and committed INTELLI fixtures. Reports redact tag/rung names to opaque
signal keys and emit aggregate pass rates by check type, fixture, and intent.

Vendor AMP blocks (`AMP_*_INTRALOX`) register as AOI-style parameter bindings
with deterministic `Out` writes and `In_*` reads. Structured-text and AOI
fixture tests live under `backend/tests/test_unified_evidence_*.py`. Frontend
workspace rendering is covered by Vitest component tests.
