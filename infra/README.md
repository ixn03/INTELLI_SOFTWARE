# INTELLI Live Data Stack (Docker Compose)

Local infrastructure for the MVP live-data pipeline:

```text
Prosys OPC UA Simulation Server (host)
        ↓
INTELLI OPC UA Collector (optional profile: opcua)
        ↓
FastAPI backend — POST /api/ingest/tag-samples
        ↓
InfluxDB 3 Core (time-series) + PostgreSQL (tag registry)
        ↓
GET /api/tags/{tag_id}/latest | /history
```

The collector is stateless: it reads OPC UA values and POSTs batches to the backend.
The backend validates against the tag registry and writes accepted samples to InfluxDB.

This compose file does **not** include Kafka/MQTT. Add a broker later when you need
replay, buffering, or multiple consumers.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose v2)
- Ports available: `5432`, `8181`, `7474`, `7687`, `8000` (override via `.env`)

## Quick start

From the repository root:

```bash
cd infra
cp env.example .env
# Edit .env and change default passwords before first run.

docker compose --env-file .env up -d --build
```

Verify services:

```bash
docker compose --env-file .env ps
curl http://localhost:8000/health
```

Expected backend response:

```json
{"status": "INTELLI backend running"}
```

## Stop and reset

Stop containers (keep data volumes):

```bash
docker compose --env-file .env down
```

Stop and **delete all persisted data**:

```bash
docker compose --env-file .env down -v
```

Restart after code changes:

```bash
docker compose --env-file .env up -d --build backend
```

## Services

| Service | Image | Host port | Purpose |
|---------|-------|-----------|---------|
| `postgres` | `postgres:16-alpine` | 5432 | Projects, config, tag registry |
| `influxdb` | `quay.io/influxdb/influxdb3-core` | 8181 | Tag time-series (`TagSample`) |
| `neo4j` | `neo4j:5-community` | 7474 / 7687 | Logic + equipment relationships |
| `backend` | Built from `../backend` | 8000 | FastAPI API (ingest + query) |
| `opcua-collector` | Built from `../backend` | — | OPC UA poll → batch ingest (profile `opcua`) |

All services define health checks. The backend waits until databases report healthy
before starting.

## Environment variables

Copy `env.example` to `.env` and adjust:

| Variable | Default | Description |
|----------|---------|-------------|
| `POSTGRES_USER` | `intelli` | PostgreSQL username |
| `POSTGRES_PASSWORD` | *(change me)* | PostgreSQL password |
| `POSTGRES_DB` | `intelli` | Database name |
| `POSTGRES_PORT` | `5432` | Host port mapping |
| `INFLUXDB_PORT` | `8181` | InfluxDB 3 Core HTTP API |
| `INFLUXDB_NODE_ID` | `node0` | InfluxDB node identifier |
| `NEO4J_HTTP_PORT` | `7474` | Neo4j Browser |
| `NEO4J_BOLT_PORT` | `7687` | Neo4j Bolt driver port |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / *(change me)* | Neo4j auth |
| `BACKEND_PORT` | `8000` | FastAPI host port |
| `INFLUXDB_URL` | `http://localhost:8181` | InfluxDB HTTP API (host tools) |
| `INFLUXDB_TOKEN` | *(empty)* | Bearer token if auth enabled |
| `INFLUXDB_ORG` | `intelli` | Org label (InfluxDB 3 metadata) |
| `INFLUXDB_BUCKET` | `intelli_ts` | Database/bucket for `tag_samples` |
| `INTELLI_BACKEND_URL` | `http://localhost:8000` | Collector target API base URL |
| `OPCUA_ENDPOINT_URL` | Prosys default | OPC UA server endpoint |
| `COLLECTOR_POLL_MS` | `1000` | Collector poll interval |

The backend container receives connection URLs automatically:

- `DATABASE_URL` → PostgreSQL
- `INFLUXDB_URL` → InfluxDB
- `INFLUXDB_BUCKET` → `intelli_ts`
- `NEO4J_URI` → Neo4j Bolt

## Persistent volumes

| Volume | Mount | Data |
|--------|-------|------|
| `postgres_data` | PostgreSQL data dir | Projects + tag registry |
| `influxdb_data` | InfluxDB 3 data dir | Time-series samples |
| `neo4j_data` | Neo4j `/data` | Graph nodes and edges |

PostgreSQL runs init scripts on first boot:

- `postgres/init/01-init.sql` — legacy project stub tables
- `postgres/init/02-tag-registry.sql` — **Step 2 tag registry** (plants, equipment,
  data sources, tags) plus demo seed data
- `postgres/init/03-stored-projects.sql` — **Phase 2 project persistence**
  (`stored_projects` JSONB blobs for uploaded L5X + normalized graph)

If you already started the stack before Step 2 or before project persistence,
recreate volumes:

```bash
docker compose --env-file .env down -v
docker compose --env-file .env up -d --build
```

## Step 2 — Tag registry API examples

List demo tags:

```bash
curl http://localhost:8000/api/tags
```

Filter tags for pump equipment:

```bash
curl "http://localhost:8000/api/equipment/11111111-1111-1111-1111-111111111103/tags"
```

Create a data source:

```bash
curl -X POST http://localhost:8000/api/data-sources \
  -H "Content-Type: application/json" \
  -d '{
    "plant_id": "11111111-1111-1111-1111-111111111101",
    "name": "Line 1 OPC UA",
    "source_system": "OPC_UA",
    "endpoint_url": "opc.tcp://localhost:4840",
    "auth_type": "NONE"
  }'
```

Create a tag (raw name vs canonical INTELLI identity):

```bash
curl -X POST http://localhost:8000/api/tags \
  -H "Content-Type: application/json" \
  -d '{
    "plant_id": "11111111-1111-1111-1111-111111111101",
    "area_id": "11111111-1111-1111-1111-111111111102",
    "equipment_id": "11111111-1111-1111-1111-111111111103",
    "data_source_id": "11111111-1111-1111-1111-111111111106",
    "raw_name": "Program:MainProgram.P101_RunningFB",
    "canonical_name": "P101_RunningFB",
    "description": "Confirms pump is actually running",
    "data_type": "BOOL",
    "tag_role": "FEEDBACK",
    "source_system": "SIMULATOR",
    "node_id": "ns=2;s=MainProgram.P101_RunningFB"
  }'
```

## Connect from host tools

| Tool | URL |
|------|-----|
| FastAPI | http://localhost:8000 |
| InfluxDB 3 | http://localhost:8181 |
| Neo4j Browser | http://localhost:7474 |
| PostgreSQL | `postgresql://intelli:<password>@localhost:5432/intelli` |

## Logs and troubleshooting

```bash
# All services
docker compose --env-file .env logs -f

# One service
docker compose --env-file .env logs -f influxdb

# Re-check health
docker compose --env-file .env ps
```

If InfluxDB 3 fails health checks on first pull, allow extra startup time
(`start_period` is 30s) or run `docker compose --env-file .env logs influxdb`.

If Neo4j fails to start, confirm `NEO4J_PASSWORD` is set in `.env` and is not
the reserved value `neo4j` alone (use something like `change_me_neo4j`).

## Step 3 — OPC UA collector and live ingestion

Start Prosys OPC UA Simulation Server on the host, then start the stack and collector:

```bash
docker compose --env-file .env up -d --build
docker compose --env-file .env --profile opcua up -d opcua-collector
docker compose --env-file .env logs -f opcua-collector
```

From inside Docker, point at the host machine:

```bash
OPCUA_ENDPOINT_URL=opc.tcp://host.docker.internal:53530/OPCUA/SimulationServer
```

### Prosys setup — Simulation/PumpSystem

A fresh Prosys install only ships default nodes (`Counter`, `Random`, `Sinusoid`, etc.). Create demo variables first:

1. **Objects** → folder `Simulation` → folder `PumpSystem`
2. Add variables: `P101_RunCmd`, `P101_RunningFB`, `P101_Fault`, `XV101_OpenCmd`, `XV101_OpenFB` (BOOL), `Tank101_Level` (REAL)
3. In Prosys **Browse**, copy each variable's **NodeId** (numeric form, e.g. `ns=3;i=1009`)

Demo seed maps:

| raw_name | node_id / source_address |
|----------|--------------------------|
| P101_RunCmd | `ns=3;i=1009` |
| P101_RunningFB | `ns=3;i=1010` |
| P101_Fault | `ns=3;i=1011` |
| XV101_OpenCmd | `ns=3;i=1012` |
| XV101_OpenFB | `ns=3;i=1013` |
| Tank101_Level | `ns=3;i=1014` |

Update registry if your NodeIds differ — `PATCH /api/tags/{tag_id}` with `node_id` and `source_address`, or edit seed SQL and recreate volumes.

### Browse NodeIds (list_opcua_nodes.py)

```bash
cd ../backend
set OPCUA_ENDPOINT_URL=opc.tcp://localhost:53530/OPCUA/SimulationServer
python tools/list_opcua_nodes.py
```

Prints DisplayName, BrowseName, NodeId, DataType, AccessLevel under `Simulation/PumpSystem`. If the folder is missing, lists top-level Objects children.

### Run collector (host)

```bash
cd ../backend
set OPCUA_ENDPOINT_URL=opc.tcp://localhost:53530/OPCUA/SimulationServer
set INTELLI_BACKEND_URL=http://localhost:8000
python tools/opcua_collector.py
```

Or via Docker profile `opcua` (uses `host.docker.internal` in `.env`):

```bash
docker compose --env-file .env --profile opcua up -d opcua-collector
docker compose --env-file .env logs -f opcua-collector
```

Until variables exist in Prosys, the collector connects but logs per-node read failures.

Ingest manually:

```bash
curl -X POST http://localhost:8000/api/ingest/tag-samples \
  -H "Content-Type: application/json" \
  -d '{"tag_id":"11111111-1111-1111-1111-111111111201","raw_tag_name":"P101_RunCmd","value":true,"timestamp":"2026-06-05T12:00:00Z","quality":"good","source":"OPC_UA"}'

curl http://localhost:8000/api/tags/11111111-1111-1111-1111-111111111201/latest
```

Run ingestion tests:

```bash
cd ../backend
python -m pytest tests/test_tag_ingestion_api.py -q
```

## Step 4 — Live data in troubleshooting workspace

The workspace API auto-fetches latest tag values when `use_live_data` is true (default):

```bash
curl -X POST http://localhost:8000/api/troubleshoot/question \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<id>","question":"Why is P101_RunCmd not energizing?","use_live_data":true}'
```

Response includes `current_state_explanation` (target value, blocking/satisfied conditions) and `advanced_details.live_data` (resolution counts).

**Prerequisites:** collector or `demo_live_data_publisher.py` running, registry tag `canonical_name` matching L5X tag name.

## Next steps (out of scope for Step 4)

1. **Reasoning Engine** — read live values from InfluxDB + logic graph from Neo4j.
2. **Additional connectors** — PI, DeltaV, Seeq, MQTT/Kafka buffering.

## Cost note

All images in this stack are free/self-hosted for development:

- InfluxDB 3 Core (open source)
- PostgreSQL
- Neo4j Community
- FastAPI backend (this repo)
