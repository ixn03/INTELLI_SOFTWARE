# INTELLI Repository Refactor Classification Report

This report is the required gate before destructive cleanup. It classifies the current repository against the new product direction:

> INTELLI continuously synchronizes control logic, engineering documentation, engineering knowledge, and plant change history.

INTELLI is not a document management system. It is the synchronization engine between versioned control logic snapshots and governed engineering records. Parser output, imported engineering documents, engineer approvals, and immutable snapshots are the evidence chain. LLMs may draft and summarize later, but they must never determine engineering facts.

## Category A: Keep

Keep these as core platform technology. They directly support the Control Document Integrity Platform.

### Backend Core

- `backend/app/connectors/`
  - Rockwell L5X, DeltaV FHX, Siemens XML/TIA, Honeywell preservation connectors.
  - Keep and evolve as vendor import adapters.
- `backend/app/parsers/`
  - Ladder, ST, FBD, SFC, expression parsing.
  - Keep as deterministic fact extraction.
- `backend/app/models/control_model.py`
  - Connector staging model.
- `backend/app/models/reasoning.py`
  - Existing normalized IR / relationship model.
  - Keep, but gradually rename or wrap around “logic snapshot / parser facts” language.
- `backend/app/services/normalization_service.py`
  - Converts parsed exports into canonical model.
- `backend/app/services/version_compare_service.py`
  - Existing deterministic diff engine.
  - Keep and expand into `LogicDiff`.
- `backend/app/services/project_store.py`
  - Useful snapshot/project cache and persistence hook.
- `backend/app/services/project_persistence_service.py`
  - Keep as durable raw project/normalized graph storage.
- `backend/app/db/models/stored_project.py`
  - Keep as raw imported control export storage.
- `backend/app/db/models/process_knowledge.py`
  - New process-unit centered product model.
- `backend/app/services/process_knowledge_service.py`
  - New import source / sync / review-trigger service.
- `backend/app/api/process_knowledge_routes.py`
  - New REST API spine for process units, modules, import sources, snapshots, governed records, issues.
- `backend/app/db/models/tag_registry.py`
  - Keep. Tag identity is required for IO list, CEM, alarm, and logic/document alignment.
- `backend/app/services/tag_registry_service.py`
  - Keep.
- `backend/app/api/tag_registry_routes.py`
  - Keep.
- `backend/app/services/dependency_graph_service.py`
  - Keep; useful for impact analysis.
- `backend/app/services/graph_service.py`
  - Keep; useful for summarizing parsed snapshots.
- `backend/app/services/logic_ir_validation.py`
  - Keep; parser facts need validation.
- `backend/app/services/logic_expression_eval.py`
  - Keep; useful for deterministic checks.
- `backend/app/services/logic_slice_service.py`
  - Keep; useful for extracting impacted logic around a process unit/module.

### Runtime / Live Infrastructure

Keep, but downgrade from product center to future evidence source:

- `backend/app/services/runtime_*`
- `backend/app/services/live_*`
- `backend/app/services/influx_*`
- `backend/app/api/ingest_routes.py`
- `backend/tools/opcua_*`
- `backend/tools/demo_live_data_publisher.py`

These are not MVP-center anymore, but they may support later validation, alarm correlation, and live evidence.

### Tests And Fixtures

Keep parser, connector, normalization, diff, persistence, and new process knowledge tests:

- `backend/tests/test_*connector*.py`
- `backend/tests/test_*parser*.py`
- `backend/tests/test_*normalization*.py`
- `backend/tests/test_version_compare_service.py`
- `backend/tests/test_project_persistence.py`
- `backend/tests/test_process_knowledge_service.py`
- `backend/tests/fixtures/`

### Infrastructure

- `backend/requirements.txt`
- `backend/Dockerfile`
- `infra/docker-compose.yml`
- `infra/postgres/init/`
- `frontend/package.json`
- `frontend/next.config.ts`
- `frontend/vitest.config.mts`

Keep and update migrations/init scripts as product schema stabilizes.

## Category B: Refactor

Valuable code, but currently named and organized around the old “AI troubleshooting / signal workspace” product.

### Backend Troubleshooting Services

Refactor these into the new architecture rather than deleting:

- `backend/app/services/trace_service.py`
- `backend/app/services/trace_v2_service.py`
- `backend/app/services/ask_v2_service.py`
- `backend/app/services/question_router_service.py`
- `backend/app/services/sequence_reasoning_service.py`
- `backend/app/services/sequence_semantics_service.py`
- `backend/app/services/runtime_evaluation_v2_service.py`
- `backend/app/services/troubleshooting_workspace_service.py`
- `backend/app/services/troubleshoot_eval_service.py`
- `backend/app/services/unified_evidence_service.py`
- `backend/app/services/evidence_service.py`
- `backend/app/services/trustworthiness_service.py`
- `backend/app/services/version_intelligence_service.py`

Target refactor:

- `trace_*` becomes logic fact traversal / impacted module analysis.
- `sequence_*` becomes parser facts for sequence sections in narratives and CEM checks.
- `runtime_*` becomes future live evidence comparison.
- `unified_evidence_*` becomes evidence provenance for engineering facts.
- `version_intelligence_*` becomes impact analysis and review item generation.

### LLM Services

Refactor and feature-flag. Do not expand yet:

- `backend/app/services/llm_assist_service.py`
- `backend/app/services/llm_orchestrator_service.py`
- `backend/app/services/llm_providers.py`
- `backend/app/services/explanation_service.py`

Target role:

- Draft narratives from parser facts.
- Draft proposed document revisions.
- Summarize deterministic diffs.
- Summarize review packages.

Rule: no LLM output becomes official without engineer approval and evidence links.

### Existing Knowledge Service

- `backend/app/models/knowledge.py`
- `backend/app/services/knowledge_service.py`
- `backend/tests/test_knowledge_service.py`

Refactor into the new `KnowledgeIssue` / `EngineeringKnowledge` model. Preserve verified/rejected/superseded semantics.

### API Aggregator

- `backend/app/api/routes.py`

Refactor into smaller routers:

- import sources
- process units/modules
- snapshots
- engineering records
- reviews
- parser/debug
- future runtime evidence

### Frontend

Refactor rather than delete:

- `frontend/src/components/intelli/IntelliWorkspace.tsx`
- `SignalTroubleshootingWorkspaceView.tsx`
- `AnswerView.tsx`
- `RuntimeDiagnosisView.tsx`
- `logicPaths.ts`
- `logicLineGroups.ts`
- `liveValueIndex.ts`
- `frontend/src/app/workspace/*`

Target UI:

- Process unit browser
- Module record page
- Import source settings
- Logic snapshot history
- Engineering records: narrative, IO list, CEM, alarm rationalization, MOC
- Review queue
- Proposed update diff view
- Approval / reject / edit / comment workflow

Existing signal troubleshooting UI can become a secondary “logic evidence/debug” panel, not the product home.

### Docs

Refactor:

- `docs/product_vision_roadmap.md`
- `docs/complex_program_bridge_plan.md`
- `docs/parsing_normalization_roadmap.md`
- `docs/platform_support_matrix.md`
- `README.md`
- `infra/README.md`

Target docs should use the new product terms:

- Control Document Integrity Platform
- import source
- logic snapshot
- parser facts
- engineering record
- proposed revision
- review item
- approval history
- drift / discrepancy

## Category C: Archive

Archive when cleanup begins. Do not delete unless later proven valueless.

### Old Product/Demo Surfaces

Likely archive after replacement UI exists:

- Old workspace pages that only serve the AI troubleshooting product.
- Old ask/trace demo endpoints if not used by new product.
- Old “Signal Intelligence” copy and demos.

Proposed archive path:

- `archive/old_troubleshooting_workspace/`
- `archive/old_signal_intelligence_api/`

### One-Off / Local Audit Artifacts

Move to archive or scratch outside production imports:

- `scratch_confidential/`
- `tmp_l5x_analysis/`
- `grade_before.json`
- `grade_after.json`
- `grade_phase4_slice2.json`
- `tdr_grade_fixtures.json`
- `vitest_out.txt`

Proposed archive path:

- `archive/parser_audit_artifacts/`

### Tools That Are Useful But Not Product Runtime

Some tools should stay available but not look like production app code:

- `backend/tools/parser_grade.py`
- `backend/tools/fixture_inventory.py`
- `backend/tools/troubleshoot_eval.py`
- `backend/tools/phase1_gate.py`
- `backend/tools/compare_grades.py`

Possible destination:

- `archive/eval_tools/` or `backend/tools/eval/`

Recommendation: refactor into `backend/tools/eval/` rather than archive if still used.

### Runtime OPC Demo Tools

Not archive yet, but consider moving later:

- `backend/tools/opcua_collector.py`
- `backend/tools/list_opcua_nodes.py`
- `backend/tools/demo_live_data_publisher.py`

Possible destination:

- `backend/tools/runtime/`

## Category D: Remove

Remove only after confirmation. Current safe candidates:

- Empty placeholder folders with only `.gitkeep` if the new structure no longer uses them:
  - top-level `connectors/`
  - top-level `parsers/`
  - top-level `shared/`
  - `sample_data/` if it remains empty
- Generated cache directories:
  - `.pytest_cache/`
  - `__pycache__/`
- Local SQLite DB artifact if not intended to be committed:
  - `backend/intelli_registry.db`
- Empty nested scaffold:
  - `INTELLI/backend` appears empty from current inventory.

Do not remove until the cleanup step is explicitly approved.

## Proposed Target Structure

Near-term structure without a risky full rewrite:

```text
backend/app/
  api/
    import_sources.py
    process_units.py
    snapshots.py
    engineering_records.py
    reviews.py
    parser_debug.py
    runtime_evidence.py
  connectors/
  parsers/
  models/
    control_model.py
    reasoning.py
    evidence.py
  db/models/
    process_knowledge.py
    stored_project.py
    tag_registry.py
  services/
    imports/
    snapshots/
    drift/
    engineering_records/
    reviews/
    parser_facts/
    ai_assist/
```

Avoid a big-bang move. First stabilize the product model and routes, then move services one group at a time with tests.

## Recommended Build Order

1. Complete data model for:
   - `EngineeringRecord`
   - `DocumentTemplate`
   - `DocumentRevision`
   - `ProposedDocumentUpdate`
   - `ReviewItem`
   - `ApprovalHistory`
   - `LogicDiff`
2. Make `LogicSnapshot` immutable and store raw-file metadata/checksum/export time.
3. Expand import source framework:
   - manual upload
   - watched folder
   - scheduled export
   - Siemens Openness adapter interface
4. Convert current `GovernanceArtifact` into first-class engineering records.
5. Build review workflow skeleton:
   - original
   - proposed
   - diff
   - approve/reject/edit/comment
6. Refactor old troubleshooting UI into a logic evidence panel.
7. Add AI only after the deterministic record/review workflow works.

## Non-Destructive Next Step

Before archiving/removing anything:

1. Add the missing engineering-record/revision/review models.
2. Add REST endpoints around the new workflow.
3. Create the new process-unit/module UI shell.
4. Only then archive old troubleshooting UI routes/components that are no longer imported.

