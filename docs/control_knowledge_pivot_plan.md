# INTELLI Pivot Plan: Control Knowledge System of Record

This document captures the new product direction: INTELLI should not compete with Cursor-style tools on ad hoc FHX/L5X analysis. The product should become the governed system of record for plant control knowledge.

## Long-Term Vision

INTELLI is not a document management system.

INTELLI is the synchronization engine between:

- Control logic
- Engineering documentation
- Engineering knowledge
- Plant change history

The parser is the source of truth for engineering facts extracted from control exports. Engineering documents are living artifacts tied to versioned logic snapshots.

LLMs assist engineers by drafting updates and summarizing changes, but never determine engineering facts.

Every engineering fact should be traceable back to at least one of:

- a logic snapshot
- parser output
- engineer approval
- an imported engineering document

## Product Thesis

INTELLI is a control knowledge platform for controls engineers, reliability teams, quality teams, and commissioning teams.

It stores, versions, governs, and cross-checks the control knowledge that usually lives across Word documents, SharePoint folders, spreadsheets, MOC packets, tribal memory, and raw DCS/PLC exports.

Cursor and similar tools are useful for deep engineer analysis of a file. INTELLI owns the plant memory after that analysis:

- Approved narratives
- Known issues and fixes
- Logic export snapshots
- Cause and effect records
- IO lists
- Alarm rationalization records
- MOC evidence
- FAT/SAT and commissioning records
- Drift reports showing when code, documents, and field reality no longer agree

## Positioning

Do not position the product as:

- AI troubleshooting for DCS
- Chat with your plant
- Replacement for DCS tools
- Generic document chatbot

Position it as:

- Control knowledge system of record
- Keeps narratives, IO lists, alarms, and code in sync
- Captures and reuses controls engineering fixes across shifts and years
- Creates audit-ready evidence for MOC, FAT/SAT, commissioning, and reliability reviews

## Five Product Layers

### Layer 1: Data and Logic Layer

This is the import and normalization foundation.

Inputs:

- DeltaV FHX
- Rockwell L5X
- Siemens TIA exports
- IO lists
- Cause and effect matrices
- Alarm exports
- Control narratives
- Commissioning records

Outputs:

- Canonical equipment/module model
- Logic snapshots
- Extracted commands, steps, devices, setpoints, permissives, interlocks, alarms, and references
- Stable tag and module identity

Principle: parsers produce structured facts. They do not produce official truth by themselves.

### Layer 2: Knowledge Layer

This is the plant memory that survives turnover.

Core records:

- Equipment modules
- Known issues
- Root causes
- Fixes
- Affected tags/modules
- Evidence from logic exports, photos, trends, work orders, and engineer notes
- Verification status

Example:

`MIX_COMPLETED set before OAR confirms` should become a searchable issue linked to:

- `EM_COAT_PREP`
- MIX command
- affected tags
- FHX snapshot where it was found
- fix description
- engineer who verified it
- later snapshot where the fix was confirmed

### Layer 3: Governance Layer

This is the major product expansion. It turns the platform from a knowledge base into the governed record for controls engineering.

Governed artifacts:

- Narratives
- Cause and effect records
- IO lists
- Alarm rationalization
- MOC
- FAT/SAT
- Commissioning records

Each governed artifact should support:

- Version history
- Approval status
- Owner and reviewer
- Linked equipment/module
- Linked logic snapshot
- Linked issue or change request
- Evidence attachments
- Review due dates where applicable
- Waiver/exception notes
- Exportable audit package

Governance states:

- Draft
- In review
- Approved
- Superseded
- Needs review
- Waived with justification

The key product behavior is that logic changes trigger governance impact:

- If an FHX/L5X snapshot changes a setpoint, command, alarm, permissive, or IO reference, INTELLI should identify which narratives, cause and effect rows, IO list entries, alarm rationalization records, MOC items, FAT/SAT records, and commissioning records may need review.

### Layer 4: Drift Layer

This detects when reality diverges from the official record.

Initial drift types:

- Logic drift: snapshot N vs snapshot N-1
- Narrative drift: parser extract vs approved narrative
- IO drift: logic tag references vs IO list
- Cause and effect drift: interlocks/permissives in logic vs approved matrix
- Alarm drift: alarm references/limits vs rationalized alarm record
- MOC drift: logic changed without linked MOC or waiver
- Commissioning drift: commissioned state no longer matches current logic/documentation

Principle: drift detection should be deterministic where possible. LLMs may summarize drift reports for humans, but the underlying comparison should be rule-based.

### Layer 5: AI and Engineer Assist Layer

AI is useful, but it is not the source of truth.

Use LLMs for:

- Draft narrative prose from structured parser output
- Summarize diffs between snapshots
- Summarize issue history for a module
- Suggest which governance artifacts may be affected
- Help engineers write clear MOC/FAT/SAT notes

Do not use LLMs for:

- Inventing tags, commands, interlocks, or setpoints
- Approving records
- Acting as the only parser
- Replacing deterministic drift checks

## MVP Scope

The first MVP should target one plant area, such as coating prep.

In scope:

1. Equipment/module registry
2. FHX/L5X upload and versioned logic snapshots
3. Structured extract for commands, devices, setpoints, permissives, interlocks, and alarms
4. Narrative draft from structured extract
5. Narrative review and approval
6. Issue log tied to module and snapshot
7. Governance records for narratives, IO lists, cause and effect, and MOC
8. Simple drift report between two snapshots
9. Search across modules, tags, issues, narratives, and governance artifacts
10. Audit package export for one module

Out of scope for MVP:

- Live OPC/PI integration
- Full multi-vendor semantics
- Generic plant chatbot
- Replacing SharePoint across the whole site on day one
- Fully automated approval

## Core Data Model

Minimum entities:

- `EquipmentModule`
- `LogicSnapshot`
- `ParsedModuleExtract`
- `ControlNarrative`
- `NarrativeRevision`
- `Issue`
- `IssueEvidence`
- `GovernedArtifact`
- `GovernedArtifactRevision`
- `ApprovalRecord`
- `DriftReport`
- `DriftFinding`
- `MocRecord`
- `AuditPackage`

Governed artifact types:

- `narrative`
- `cause_effect`
- `io_list`
- `alarm_rationalization`
- `moc`
- `fat`
- `sat`
- `commissioning`

## Execution Plan

### Phase 0: Product Refocus

- Freeze live troubleshooting as a later-stage capability.
- Update roadmap and UI language away from "live signal troubleshooting."
- Keep existing parser/evidence work as the extraction foundation.

### Phase 1: System of Record

- Add database tables for modules, snapshots, issues, narratives, governance artifacts, approvals, and drift reports.
- Add API endpoints for module registry, snapshot upload, issue creation, narrative revisions, and governance records.
- Build a module detail page that shows snapshots, approved narrative, issues, and governed artifacts.

### Phase 2: Narrative and Issue Workflow

- Generate draft narrative sections from structured parser output.
- Let controls engineers edit, review, approve, and supersede narratives.
- Let engineers log issues against modules and snapshots.
- Link evidence to issues and narrative sections.

### Phase 3: Governance Workflow

- Add governed artifact records for cause and effect, IO lists, alarm rationalization, MOC, FAT/SAT, and commissioning records.
- Add review states, approval records, waiver notes, and ownership.
- Add impact review when a new snapshot changes facts connected to governed artifacts.

### Phase 4: Drift Reports

- Compare snapshot-to-snapshot logic changes.
- Compare parser extract to approved narrative.
- Compare logic references to IO list.
- Compare alarm references to rationalized alarm records.
- Surface findings as review tasks, not automatic truth.

### Phase 5: Optional Live Layer

- Add live data only when a pilot proves a specific need that exports cannot solve.
- Keep live data read-only.
- Use live data as evidence, not as the product center.

## Success Criteria

The MVP is successful when a controls engineer can:

1. Open `EM_COAT_PREP`.
2. See the approved narrative, current logic snapshot, known issues, IO list, cause and effect records, alarm rationalization records, and MOC links.
3. Upload a new FHX/L5X export.
4. See what changed.
5. See which governed artifacts need review.
6. Log or resolve an issue with evidence.
7. Export an audit package showing narrative history, approvals, issues, drift findings, and linked MOC/commissioning records.

That is the product wedge: not another parser, and not another chatbot, but the control knowledge record that plants can trust.
