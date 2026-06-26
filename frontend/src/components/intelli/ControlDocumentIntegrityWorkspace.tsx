"use client";

import axios from "axios";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { extractIntelliError, useIntelliProject } from "@/context/IntelliProjectContext";
import type {
  AiDraftResponse,
  ApprovalHistory,
  ControlImportSource,
  DocumentRevision,
  EngineeringRecord,
  EquipmentModule,
  GenerationMode,
  ImportSyncResult,
  LogicDiff,
  LogicSnapshot,
  ModuleRecord,
  ProcessUnit,
  ProcessUnitListResponse,
  ProposedDocumentUpdate,
  RecordType,
  ReviewItem,
  SeedDemoResult,
} from "@/types/processKnowledge";

import {
  Badge,
  Banner,
  Button,
  Card,
  CardBody,
  CardHeader,
  Code,
  EmptyState,
  type FlowStep,
  InlineError,
  LoadingLine,
  Stat,
  StepIndicator,
  TextArea,
} from "./ui";

const RECORD_ORDER: RecordType[] = [
  "control_narrative",
  "io_list",
  "cause_effect_matrix",
  "alarm_rationalization",
  "moc",
  "knowledge_issue",
  "engineering_note",
];

const GENERATABLE_DOCUMENT_TYPES: RecordType[] = [
  "control_narrative",
  "io_list",
  "cause_effect_matrix",
  "alarm_rationalization",
];

const RECORD_LABELS: Record<RecordType, string> = {
  control_narrative: "Control Narrative",
  io_list: "IO List",
  cause_effect_matrix: "Cause & Effect Matrix",
  alarm_rationalization: "Alarm Rationalization",
  moc: "MOC",
  knowledge_issue: "Knowledge Base",
  engineering_note: "Engineering Note",
};

const ABSENCE_COPY: Record<RecordType, string> = {
  control_narrative: "No approved Control Narrative exists.",
  io_list: "No approved IO List exists.",
  cause_effect_matrix: "No approved C&E exists.",
  alarm_rationalization: "No approved Alarm Rationalization exists.",
  moc: "No approved MOC exists.",
  knowledge_issue: "No Knowledge Base entries exist.",
  engineering_note: "No approved Engineering Note exists.",
};

function fmtStatus(value: string | null | undefined): string {
  if (!value) return "Unknown";
  if (value === "needing_setup") return "Manual review required";
  return value.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
}

function statusTone(value: string | null | undefined): "neutral" | "info" | "success" | "warning" | "danger" | "outline" {
  if (value === "approved" || value === "active" || value === "resolved") return "success";
  if (value === "open" || value === "needs_review" || value === "proposed") return "warning";
  if (value === "needs_manual_review" || value === "needing_setup") return "info";
  if (value === "rejected") return "danger";
  return "neutral";
}

function latestSnapshot(snapshots: LogicSnapshot[]): LogicSnapshot | null {
  return snapshots[0] ?? null;
}

function latestApprovedRevision(revisions: DocumentRevision[]): DocumentRevision | null {
  return revisions.find((rev) => rev.status === "approved") ?? null;
}

function latestRevision(revisions: DocumentRevision[]): DocumentRevision | null {
  return revisions[0] ?? null;
}

function jsonBlock(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

interface UnitSummary {
  unit: ProcessUnit;
  modules: EquipmentModule[];
  reviewItems: ReviewItem[];
}

interface AiDraftSummary extends AiDraftResponse {
  record_type: RecordType;
}

type IntegrityTab =
  | "home"
  | "plant"
  | "import"
  | "documents"
  | "reviews"
  | "templates"
  | "knowledge";

const WORKFLOW_TABS: { id: IntegrityTab; label: string; hint: string }[] = [
  { id: "home", label: "Home", hint: "Start cleanly" },
  { id: "plant", label: "Plant Explorer", hint: "Choose area and equipment" },
  { id: "import", label: "Imports", hint: "Import a control export" },
  { id: "documents", label: "Documents", hint: "Draft and update records" },
  { id: "reviews", label: "Review Queue", hint: "Review changes" },
  { id: "templates", label: "Templates", hint: "Manage document patterns" },
  { id: "knowledge", label: "Knowledge Base", hint: "Capture MOC context" },
];

function isIntegrityTab(value: string | null): value is IntegrityTab {
  return WORKFLOW_TABS.some((tab) => tab.id === value);
}

function inferVendor(filename: string): string {
  const lower = filename.toLowerCase();
  if (lower.endsWith(".l5x")) return "Rockwell";
  if (lower.endsWith(".fhx")) return "DeltaV";
  if (lower.endsWith(".xml")) return "Siemens XML";
  return "Future vendor export";
}

function displayFileStem(filename: string): string {
  return filename.replace(/\.[^.]+$/, "").replaceAll("_", " ").replaceAll("-", " ");
}

export default function ControlDocumentIntegrityWorkspace() {
  const { apiBase } = useIntelliProject();
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedUnitId = searchParams.get("unitId") ?? "";
  const requestedModuleId = searchParams.get("moduleId") ?? "";
  const requestedTab = searchParams.get("tab");
  const [activeTab, setActiveTab] = useState<IntegrityTab>(
    isIntegrityTab(requestedTab) ? requestedTab : "home",
  );
  const [units, setUnits] = useState<UnitSummary[]>([]);
  const [importSources, setImportSources] = useState<ControlImportSource[]>([]);
  const [selectedUnitId, setSelectedUnitId] = useState("");
  const [selectedModuleId, setSelectedModuleId] = useState("");
  const [seedLoading, setSeedLoading] = useState(false);
  const [moduleRecord, setModuleRecord] = useState<ModuleRecord | null>(null);
  const [records, setRecords] = useState<EngineeringRecord[]>([]);
  const [revisionsByRecord, setRevisionsByRecord] = useState<Record<string, DocumentRevision[]>>({});
  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const [proposedById, setProposedById] = useState<Record<string, ProposedDocumentUpdate>>({});
  const [latestDiff, setLatestDiff] = useState<LogicDiff | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [generationLoading, setGenerationLoading] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [selectedDocumentTypes, setSelectedDocumentTypes] = useState<Record<RecordType, boolean>>({
    control_narrative: true,
    io_list: false,
    cause_effect_matrix: false,
    alarm_rationalization: false,
    moc: false,
    knowledge_issue: false,
    engineering_note: false,
  });
  const [generationMode, setGenerationMode] = useState<GenerationMode>("llm_assisted");
  const [generationNotes, setGenerationNotes] = useState("");
  const [aiDraftSummaries, setAiDraftSummaries] = useState<AiDraftSummary[]>([]);
  const [selectedDocumentFolder, setSelectedDocumentFolder] = useState<RecordType>("control_narrative");
  const [selectedDocumentRevisionId, setSelectedDocumentRevisionId] = useState("");

  const selectedUnit = units.find((entry) => entry.unit.id === selectedUnitId) ?? null;
  const selectedModule =
    selectedUnit?.modules.find((module) => module.id === selectedModuleId) ??
    moduleRecord?.module ??
    null;
  const currentSnapshot = latestSnapshot(moduleRecord?.snapshots ?? []);
  const pendingReviewCount = reviewItems.filter((item) =>
    ["open", "needs_manual_review"].includes(item.status),
  ).length;

  const loadUnits = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await axios.get<ProcessUnitListResponse>(`${apiBase}/api/process-units`);
      const summaries = await Promise.all(
        res.data.items.map(async (unit) => {
          const [modulesRes, reviewsRes] = await Promise.all([
            axios.get<EquipmentModule[]>(`${apiBase}/api/process-units/${unit.id}/modules`),
            axios.get<ReviewItem[]>(`${apiBase}/api/process-units/${unit.id}/review-items`),
          ]);
          return {
            unit,
            modules: modulesRes.data,
            reviewItems: reviewsRes.data,
          };
        }),
      );
      setUnits(summaries);
      const sourcesRes = await axios.get<ControlImportSource[]>(`${apiBase}/api/import-sources`, {
        params: { limit: 20 },
      });
      setImportSources(sourcesRes.data);
      const linkedUnit = requestedModuleId
        ? summaries.find((entry) =>
            entry.modules.some((module) => module.id === requestedModuleId),
          )
        : null;
      const requestedUnit =
        summaries.find((entry) => entry.unit.id === requestedUnitId) ?? linkedUnit;
      const firstUnit = requestedUnit ?? summaries[0];
      if (firstUnit) {
        setSelectedUnitId((current) => {
          if (requestedUnit?.unit.id) return requestedUnit.unit.id;
          if (current && summaries.some((entry) => entry.unit.id === current)) return current;
          return firstUnit.unit.id;
        });
        setSelectedModuleId((current) => {
          if (requestedModuleId) return requestedModuleId;
          if (current && summaries.some((entry) => entry.modules.some((m) => m.id === current))) {
            return current;
          }
          // Auto-select equipment only when the choice is unambiguous (a single
          // module); otherwise let the engineer pick in the Plant Explorer.
          return firstUnit.modules.length === 1 ? firstUnit.modules[0].id : "";
        });
      } else {
        setSelectedUnitId("");
        setSelectedModuleId("");
      }
    } catch (err) {
      setError(extractIntelliError(err, "Unable to load process units"));
    } finally {
      setLoading(false);
    }
  }, [apiBase, requestedModuleId, requestedUnitId]);

  const loadModule = useCallback(async () => {
    if (!selectedModuleId || !selectedUnitId) {
      setModuleRecord(null);
      setRecords([]);
      setReviewItems([]);
      setLatestDiff(null);
      return;
    }
    setDetailLoading(true);
    setError("");
    try {
      const [recordRes, recordsRes, reviewsRes] = await Promise.all([
        axios.get<ModuleRecord>(`${apiBase}/api/modules/${selectedModuleId}/record`),
        axios.get<EngineeringRecord[]>(`${apiBase}/api/engineering-records`, {
          params: { module_id: selectedModuleId },
        }),
        axios.get<ReviewItem[]>(`${apiBase}/api/process-units/${selectedUnitId}/review-items`),
      ]);
      const moduleReviews = reviewsRes.data.filter((item) => item.module_id === selectedModuleId);
      setModuleRecord(recordRes.data);
      setRecords(recordsRes.data);
      setReviewItems(moduleReviews);

      const revisionPairs = await Promise.all(
        recordsRes.data.map(async (record) => {
          const revRes = await axios.get<DocumentRevision[]>(
            `${apiBase}/api/engineering-records/${record.id}/revisions`,
          );
          return [record.id, revRes.data] as const;
        }),
      );
      setRevisionsByRecord(Object.fromEntries(revisionPairs));

      const snapshot = latestSnapshot(recordRes.data.snapshots);
      if (snapshot) {
        const diffRes = await axios.get<LogicDiff | null>(
          `${apiBase}/api/snapshots/${snapshot.id}/diff`,
        );
        setLatestDiff(diffRes.data);
      } else {
        setLatestDiff(null);
      }

      const proposedIds = moduleReviews
        .map((item) => item.proposed_document_update_id)
        .filter((id): id is string => Boolean(id));
      const proposedPairs = await Promise.all(
        proposedIds.map(async (id) => {
          const proposedRes = await axios.get<ProposedDocumentUpdate>(
            `${apiBase}/api/proposed-document-updates/${id}`,
          );
          return [id, proposedRes.data] as const;
        }),
      );
      setProposedById(Object.fromEntries(proposedPairs));
    } catch (err) {
      setError(extractIntelliError(err, "Unable to load module record"));
    } finally {
      setDetailLoading(false);
    }
  }, [apiBase, selectedModuleId, selectedUnitId]);

  useEffect(() => {
    void loadUnits();
  }, [loadUnits]);

  useEffect(() => {
    if (isIntegrityTab(requestedTab)) setActiveTab(requestedTab);
  }, [requestedTab]);

  useEffect(() => {
    void loadModule();
  }, [loadModule]);

  async function decideReview(item: ReviewItem, decision: "approve" | "reject") {
    setActionMessage("");
    try {
      const res = await axios.post<ApprovalHistory>(
        `${apiBase}/api/review-items/${item.id}/${decision}`,
        {
          actor: "controls.engineer",
          comments:
            decision === "approve"
              ? "Approved from Control Document Integrity workspace."
              : "Rejected from Control Document Integrity workspace.",
        },
      );
      setActionMessage(`${fmtStatus(res.data.decision)} recorded for ${item.title}.`);
      await loadModule();
      await loadUnits();
    } catch (err) {
      setError(extractIntelliError(err, `Unable to ${decision} review item`));
    }
  }

  async function handleImportComplete(result: ImportSyncResult) {
    const unitId = result.process_unit_id ?? "";
    const moduleId = result.module_id ?? "";
    await loadUnits();
    if (unitId) setSelectedUnitId(unitId);
    if (moduleId) setSelectedModuleId(moduleId);
    if (unitId && moduleId) {
      router.replace(`/workspace/integrity?unitId=${unitId}&moduleId=${moduleId}`);
    }
  }

  async function createAreaProcessUnit(payload: {
    name: string;
    area?: string | null;
    description?: string | null;
  }) {
    setError("");
    setActionMessage("");
    try {
      const res = await axios.post<ProcessUnit>(`${apiBase}/api/process-units`, payload);
      setSelectedUnitId(res.data.id);
      setActionMessage(`${res.data.name} created. Add equipment next.`);
      await loadUnits();
      setActiveTab("plant");
    } catch (err) {
      setError(extractIntelliError(err, "Unable to create area/process unit"));
    }
  }

  async function createEquipment(payload: {
    processUnitId: string;
    name: string;
    module_type?: string | null;
    description?: string | null;
  }) {
    setError("");
    setActionMessage("");
    try {
      const res = await axios.post<EquipmentModule>(
        `${apiBase}/api/process-units/${payload.processUnitId}/modules`,
        {
          name: payload.name,
          module_type: payload.module_type || null,
          description: payload.description || null,
          aliases: [],
          metadata_json: {},
        },
      );
      setSelectedUnitId(payload.processUnitId);
      setSelectedModuleId(res.data.id);
      setActionMessage(`${res.data.name} added. Import a control export next.`);
      await loadUnits();
      setActiveTab("import");
    } catch (err) {
      setError(extractIntelliError(err, "Unable to add equipment"));
    }
  }

  async function seedDemoProcessUnit() {
    setError("");
    setActionMessage("");
    setSeedLoading(true);
    try {
      const res = await axios.post<SeedDemoResult>(
        `${apiBase}/api/control-integrity/seed-demo`,
      );
      setSelectedUnitId(res.data.process_unit_id);
      setSelectedModuleId(res.data.module_id);
      setActionMessage(
        `Demo process unit "${res.data.process_unit_name}" created with ${res.data.module_name} and a logic snapshot. Pick documents and generate drafts to continue.`,
      );
      await loadUnits();
      setActiveTab("documents");
    } catch (err) {
      setError(extractIntelliError(err, "Unable to create demo process unit"));
    } finally {
      setSeedLoading(false);
    }
  }

  const recordsByType = useMemo(() => {
    const grouped = new Map<RecordType, EngineeringRecord[]>();
    for (const type of RECORD_ORDER) grouped.set(type, []);
    for (const record of records) {
      const bucket = grouped.get(record.record_type) ?? [];
      bucket.push(record);
      grouped.set(record.record_type, bucket);
    }
    return grouped;
  }, [records]);

  const missingDocumentCount = useMemo(() => {
    return RECORD_ORDER.filter((type) => {
      const recordsForType = recordsByType.get(type) ?? [];
      if (recordsForType.length === 0) return true;
      return recordsForType.every((record) => {
        const revisions = revisionsByRecord[record.id] ?? [];
        return !latestApprovedRevision(revisions) && !latestRevision(revisions);
      });
    }).length;
  }, [recordsByType, revisionsByRecord]);

  const flowSteps = useMemo<FlowStep[]>(() => {
    const hasUnits = units.length > 0;
    const hasEquipment = Boolean(selectedModule) || units.some((u) => u.modules.length > 0);
    const hasSnapshot = Boolean(currentSnapshot);
    const hasDrafts = records.some((record) => (revisionsByRecord[record.id] ?? []).length > 0);
    const hasApproved = records.some((record) =>
      latestApprovedRevision(revisionsByRecord[record.id] ?? []),
    );
    const definitions: { id: string; label: string; done: boolean }[] = [
      { id: "area", label: "Set up area", done: hasUnits },
      { id: "equipment", label: "Add equipment", done: hasEquipment },
      { id: "import", label: "Import logic", done: hasSnapshot },
      { id: "generate", label: "Generate drafts", done: hasDrafts },
      { id: "review", label: "Review", done: hasDrafts && pendingReviewCount === 0 },
      { id: "approve", label: "Approve", done: hasApproved },
    ];
    const currentIndex = definitions.findIndex((step) => !step.done);
    return definitions.map((step, index) => ({
      id: step.id,
      label: step.label,
      state:
        currentIndex === -1 || index < currentIndex
          ? "complete"
          : index === currentIndex
            ? "current"
            : "upcoming",
    }));
  }, [units, selectedModule, currentSnapshot, records, revisionsByRecord, pendingReviewCount]);

  async function generateDraft(recordType: RecordType, record?: EngineeringRecord) {
    if (!record && !selectedModuleId) {
      setError("Select equipment before generating a document draft.");
      setActiveTab("plant");
      return;
    }
    const key = record ? `draft:${record.id}` : `draft:${recordType}`;
    setGenerationLoading((current) => ({ ...current, [key]: true }));
    setError("");
    setActionMessage("");
    try {
      if (record) {
        await axios.post<DocumentRevision>(
          `${apiBase}/api/engineering-records/${record.id}/generate-draft`,
          null,
          { params: { actor: "controls.engineer" } },
        );
      } else {
        await axios.post<DocumentRevision>(
          `${apiBase}/api/modules/${selectedModuleId}/engineering-records/${recordType}/generate-draft`,
          null,
          { params: { actor: "controls.engineer" } },
        );
      }
      setActionMessage(`${RECORD_LABELS[recordType]} draft created. Review required.`);
      await loadModule();
      await loadUnits();
    } catch (err) {
      setError(extractIntelliError(err, `Unable to generate ${RECORD_LABELS[recordType]} draft`));
    } finally {
      setGenerationLoading((current) => ({ ...current, [key]: false }));
    }
  }

  async function generateSelectedAiDrafts() {
    if (!selectedModuleId) {
      setError("Select equipment before generating document drafts.");
      setActiveTab("plant");
      return;
    }
    const selectedTypes = GENERATABLE_DOCUMENT_TYPES.filter((type) => selectedDocumentTypes[type]);
    if (selectedTypes.length === 0) {
      setError("Choose at least one document type to generate.");
      return;
    }
    setGenerationLoading((current) => ({ ...current, selected: true }));
    setError("");
    setActionMessage("");
    setAiDraftSummaries([]);
    try {
      const summaries: AiDraftSummary[] = [];
      for (const type of selectedTypes) {
        const res = await axios.post<AiDraftResponse>(
          `${apiBase}/api/modules/${selectedModuleId}/engineering-records/${type}/generate-ai-draft`,
          {
            generation_mode: generationMode,
            user_notes: generationNotes.trim() || null,
            actor: "controls.engineer",
          },
        );
        summaries.push({ ...res.data, record_type: type });
      }
      setAiDraftSummaries(summaries);
      if (summaries[0]) {
        setSelectedDocumentFolder(summaries[0].record_type);
        setSelectedDocumentRevisionId(summaries[0].revision.id);
      }
      setActionMessage(`${selectedTypes.length} document draft${selectedTypes.length === 1 ? "" : "s"} generated. Review required.`);
      await loadModule();
      await loadUnits();
    } catch (err) {
      setError(extractIntelliError(err, "Unable to generate selected AI document drafts"));
    } finally {
      setGenerationLoading((current) => ({ ...current, selected: false }));
    }
  }

  async function generateProposedRevision(record: EngineeringRecord) {
    const key = `proposed:${record.id}`;
    setGenerationLoading((current) => ({ ...current, [key]: true }));
    setError("");
    setActionMessage("");
    try {
      await axios.post<DocumentRevision>(
        `${apiBase}/api/engineering-records/${record.id}/generate-proposed-revision`,
        null,
        { params: { actor: "controls.engineer" } },
      );
      setActionMessage(`Proposed revision generated for ${record.title}. Review required.`);
      await loadModule();
      await loadUnits();
    } catch (err) {
      setError(extractIntelliError(err, `Unable to generate proposed revision for ${record.title}`));
    } finally {
      setGenerationLoading((current) => ({ ...current, [key]: false }));
    }
  }

  async function deleteRevision(revision: DocumentRevision) {
    if (revision.status === "approved") {
      setError("Approved revisions cannot be deleted from this workspace.");
      return;
    }
    if (!window.confirm(`Delete revision ${revision.revision}? This only removes draft or rejected work.`)) return;
    setError("");
    setActionMessage("");
    try {
      await axios.delete(`${apiBase}/api/document-revisions/${revision.id}`);
      setActionMessage(`Revision ${revision.revision} deleted.`);
      await loadModule();
      await loadUnits();
    } catch (err) {
      setError(extractIntelliError(err, "Unable to delete document revision"));
    }
  }

  async function deleteSelectedModule() {
    if (!selectedModule) return;
    if (!window.confirm(`Delete equipment ${selectedModule.name}? Approved documents block this action.`)) return;
    setError("");
    setActionMessage("");
    try {
      await axios.delete(`${apiBase}/api/modules/${selectedModule.id}`);
      setActionMessage(`${selectedModule.name} deleted.`);
      setSelectedModuleId("");
      setModuleRecord(null);
      await loadUnits();
      setActiveTab("plant");
    } catch (err) {
      setError(extractIntelliError(err, "Unable to delete equipment"));
    }
  }

  async function deleteSelectedUnitIfEmpty() {
    if (!selectedUnit) return;
    if (!window.confirm(`Delete area/process unit ${selectedUnit.unit.name}? It must be empty.`)) return;
    setError("");
    setActionMessage("");
    try {
      await axios.delete(`${apiBase}/api/process-units/${selectedUnit.unit.id}`);
      setActionMessage(`${selectedUnit.unit.name} deleted.`);
      setSelectedUnitId("");
      setSelectedModuleId("");
      await loadUnits();
      setActiveTab("home");
    } catch (err) {
      setError(extractIntelliError(err, "Unable to delete empty area/process unit"));
    }
  }

  async function resetGeneratedWorkspaceData() {
    if (!window.confirm("Reset generated drafts, rejected drafts, and open reviews? Approved revisions are preserved.")) return;
    setError("");
    setActionMessage("");
    try {
      const res = await axios.post<Record<string, number>>(`${apiBase}/api/control-integrity/reset-generated`);
      setActionMessage(
        `Reset complete: ${res.data.revisions_deleted ?? 0} generated revision(s), ${res.data.reviews_deleted ?? 0} review item(s).`,
      );
      await loadModule();
      await loadUnits();
    } catch (err) {
      setError(extractIntelliError(err, "Unable to reset generated workspace data"));
    }
  }

  return (
    <main className="min-h-screen bg-[#050914] p-5 text-zinc-100 md:p-7">
      <div className="mx-auto flex max-w-7xl flex-col gap-5">
        <header className="flex flex-col gap-4 rounded-3xl border border-zinc-800/80 bg-gradient-to-br from-zinc-900/70 via-zinc-950/60 to-[#050914] p-5 lg:flex-row lg:items-end lg:justify-between lg:p-6">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-200/70">
              Control Integrity
            </p>
            <h1 className="mt-1 text-3xl font-semibold text-white">Control Integrity</h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-400">
              Keep logic, narratives, IO lists, C&E, alarms, and MOC records synchronized.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => setActiveTab("import")}>Import Control Export</Button>
            <Button
              tone="secondary"
              disabled={seedLoading}
              onClick={() => void seedDemoProcessUnit()}
              title="Demo / dev only: bootstrap a process unit, equipment, and logic snapshot."
            >
              {seedLoading ? "Seeding demo..." : "Create Demo Process Unit"}
            </Button>
            <Button tone="secondary" onClick={() => void loadUnits()}>
              Refresh
            </Button>
          </div>
        </header>

        <section className="rounded-2xl border border-zinc-800/70 bg-zinc-950/45 px-4 py-3">
          <p className="mb-2.5 text-[10px] font-semibold uppercase tracking-[0.18em] text-cyan-200/70">
            Guided workflow
          </p>
          <StepIndicator steps={flowSteps} />
        </section>

        <div className="grid gap-3 md:grid-cols-3">
          <StatusTile
            label="Latest import"
            value={currentSnapshot?.source_filename ?? "No import yet"}
            hint={currentSnapshot ? "Latest logic snapshot" : "Import a control export to begin."}
          />
          <StatusTile
            label="Documents needing review"
            value={String(pendingReviewCount)}
            hint={pendingReviewCount ? "Engineer review required." : "No open reviews for selected equipment."}
            tone={pendingReviewCount ? "warning" : "success"}
          />
          <StatusTile
            label="Missing documents"
            value={String(missingDocumentCount)}
            hint={missingDocumentCount ? "Generate first drafts when ready." : "All tracked document slots exist."}
            tone={missingDocumentCount ? "warning" : "success"}
          />
        </div>

        {error ? (
          <Banner tone="error" onDismiss={() => setError("")}>
            {error}
          </Banner>
        ) : null}
        {actionMessage ? (
          <Banner tone="success" onDismiss={() => setActionMessage("")}>
            {actionMessage}
          </Banner>
        ) : null}

        <WorkflowTabs activeTab={activeTab} onChange={setActiveTab} />

        {activeTab === "home" ? (
          <ControlIntegrityHome
            units={units}
            importSources={importSources}
            pendingReviewCount={pendingReviewCount}
            selectedModule={selectedModule}
            seedLoading={seedLoading}
            onCreate={() => setActiveTab("plant")}
            onAddEquipment={() => setActiveTab("plant")}
            onImport={() => setActiveTab("import")}
            onReview={() => setActiveTab("reviews")}
            onDocuments={() => setActiveTab("documents")}
            onSeedDemo={() => void seedDemoProcessUnit()}
          />
        ) : null}

        {activeTab === "import" ? (
          <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
            <ImportUploadPanel
              apiBase={apiBase}
              units={units}
              selectedUnitId={selectedUnitId}
              selectedModuleId={selectedModuleId}
              onImported={(result) => {
                void handleImportComplete(result);
              }}
            />
            <RecentImportsPanel importSources={importSources} />
          </div>
        ) : null}

        {activeTab === "plant" ? (
          <div className="grid gap-5 xl:grid-cols-[21rem_minmax(0,1fr)]">
            <div className="space-y-5">
              <GuidedSetupPanel
                units={units}
                selectedUnitId={selectedUnitId}
                onCreateUnit={(payload) => void createAreaProcessUnit(payload)}
                onCreateEquipment={(payload) => void createEquipment(payload)}
              />
              <PlantExplorer
                loading={loading}
                units={units}
                selectedUnitId={selectedUnitId}
                selectedModuleId={selectedModuleId}
                onSelectUnit={(unitId) => {
                  const next = units.find((entry) => entry.unit.id === unitId);
                  setSelectedUnitId(unitId);
                  setSelectedModuleId(next?.modules[0]?.id || "");
                }}
                onSelectModule={setSelectedModuleId}
              />
            </div>
            <div className="space-y-5">
              <ModuleHeader
                loading={detailLoading}
                module={selectedModule}
                unit={selectedUnit?.unit ?? null}
                snapshot={currentSnapshot}
                diff={latestDiff}
                reviewItems={reviewItems}
                missingDocumentCount={missingDocumentCount}
                onReview={() => setActiveTab("reviews")}
                onDocuments={() => setActiveTab("documents")}
                onImport={() => setActiveTab("import")}
              />
              <SafeActionsPanel
                unit={selectedUnit?.unit ?? null}
                module={selectedModule}
                canDeleteUnit={Boolean(selectedUnit && selectedUnit.modules.length === 0)}
                onDeleteModule={() => void deleteSelectedModule()}
                onDeleteUnit={() => void deleteSelectedUnitIfEmpty()}
                onReset={() => void resetGeneratedWorkspaceData()}
              />
            </div>
          </div>
        ) : null}

        {activeTab === "reviews" ? (
          <ReviewItemsSection
            reviewItems={reviewItems}
            records={records}
            module={selectedModule}
            revisionsByRecord={revisionsByRecord}
            proposedById={proposedById}
            diff={latestDiff}
            onApprove={(item) => void decideReview(item, "approve")}
            onReject={(item) => void decideReview(item, "reject")}
          />
        ) : null}

        {activeTab === "documents" ? (
          <div className="space-y-5">
            <DocumentSelectionPanel
              selectedDocumentTypes={selectedDocumentTypes}
              onChange={setSelectedDocumentTypes}
              selectedModule={selectedModule}
              generationMode={generationMode}
              onGenerationModeChange={setGenerationMode}
              userNotes={generationNotes}
              onUserNotesChange={setGenerationNotes}
              summaries={aiDraftSummaries}
              generationLoading={generationLoading.selected}
              onGenerate={() => void generateSelectedAiDrafts()}
              onOpenReview={() => setActiveTab("reviews")}
              onOpenLibrary={(recordType, revisionId) => {
                setSelectedDocumentFolder(recordType);
                setSelectedDocumentRevisionId(revisionId);
              }}
            />
            <DocumentLibraryPanel
              recordsByType={recordsByType}
              revisionsByRecord={revisionsByRecord}
              reviewItems={reviewItems}
              selectedFolder={selectedDocumentFolder}
              selectedRevisionId={selectedDocumentRevisionId}
              onSelectFolder={(recordType) => {
                setSelectedDocumentFolder(recordType);
                setSelectedDocumentRevisionId("");
              }}
              onSelectRevision={(recordType, revisionId) => {
                setSelectedDocumentFolder(recordType);
                setSelectedDocumentRevisionId(revisionId);
              }}
            />
            <EngineeringRecordsSection
              recordsByType={recordsByType}
              revisionsByRecord={revisionsByRecord}
              reviewItems={reviewItems}
              proposedById={proposedById}
              snapshot={currentSnapshot}
              diff={latestDiff}
              generationLoading={generationLoading}
              canGenerateDraft={Boolean(selectedModuleId) && !detailLoading}
              onGenerateDraft={(recordType, record) => void generateDraft(recordType, record)}
              onGenerateProposed={(record) => void generateProposedRevision(record)}
              onReviewDraft={() => setActiveTab("reviews")}
              onDeleteRevision={(revision) => void deleteRevision(revision)}
            />
          </div>
        ) : null}

        {activeTab === "templates" ? (
          <PlaceholderPanel
            title="Templates"
            hint="Template management will live here. Draft generation currently uses active backend templates or INTELLI defaults."
          />
        ) : null}

        {activeTab === "knowledge" ? (
          <PlaceholderPanel
            title="Knowledge Base"
            hint="Capture MOC notes, known issues, and engineering context linked to selected equipment."
          />
        ) : null}
      </div>
    </main>
  );
}

function StatusTile({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint: string;
  tone?: "neutral" | "success" | "warning";
}) {
  const toneClass =
    tone === "success"
      ? "bg-emerald-950/20"
      : tone === "warning"
        ? "bg-amber-950/20"
        : "bg-zinc-950/45";
  return (
    <section className={`rounded-2xl ${toneClass} p-4`}>
      <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
        {label}
      </p>
      <p className="mt-2 truncate text-lg font-semibold text-white">{value}</p>
      <p className="mt-1 text-xs text-zinc-500">{hint}</p>
    </section>
  );
}

function WorkflowTabs({
  activeTab,
  onChange,
}: {
  activeTab: IntegrityTab;
  onChange: (tab: IntegrityTab) => void;
}) {
  return (
    <nav className="grid gap-2 md:grid-cols-4" aria-label="Control Integrity workflow">
      {WORKFLOW_TABS.map((tab) => (
        <button
          key={tab.id}
          type="button"
          onClick={() => onChange(tab.id)}
          className={`rounded-2xl px-4 py-3 text-left transition ${
            activeTab === tab.id
              ? "bg-cyan-300 text-cyan-950"
              : "bg-zinc-950/55 text-zinc-300 hover:bg-zinc-900"
          }`}
        >
          <span className="block text-sm font-semibold">{tab.label}</span>
          <span className={`mt-1 block text-xs ${activeTab === tab.id ? "text-cyan-900/75" : "text-zinc-500"}`}>
            {tab.hint}
          </span>
        </button>
      ))}
    </nav>
  );
}

function ControlIntegrityHome({
  units,
  importSources,
  pendingReviewCount,
  selectedModule,
  seedLoading,
  onCreate,
  onAddEquipment,
  onImport,
  onReview,
  onDocuments,
  onSeedDemo,
}: {
  units: UnitSummary[];
  importSources: ControlImportSource[];
  pendingReviewCount: number;
  selectedModule: EquipmentModule | null;
  seedLoading: boolean;
  onCreate: () => void;
  onAddEquipment: () => void;
  onImport: () => void;
  onReview: () => void;
  onDocuments: () => void;
  onSeedDemo: () => void;
}) {
  const isEmpty = units.length === 0;
  return (
    <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_24rem]">
      <div className="space-y-5">
        {isEmpty ? (
          <Card>
            <CardHeader title="Welcome to Control Integrity" eyebrow="Get started" />
            <CardBody className="space-y-4">
              <p className="text-sm leading-6 text-zinc-400">
                Your workspace is empty. Start by creating a process unit and adding
                equipment, then import a control export to generate and review documents.
                In a hurry? Use the demo helper to bootstrap everything at once.
              </p>
              <div className="flex flex-wrap gap-2">
                <Button onClick={onCreate}>Create new area/process unit</Button>
                <Button tone="secondary" disabled={seedLoading} onClick={onSeedDemo}>
                  {seedLoading ? "Seeding demo..." : "Create Demo Process Unit"}
                </Button>
              </div>
              <p className="rounded-lg border border-sky-800/50 bg-sky-950/30 px-3 py-2 text-xs leading-5 text-sky-100/80">
                Demo / dev-only: seeds one process unit, one piece of equipment, and a
                logic snapshot so you can exercise the full flow without a real export.
              </p>
            </CardBody>
          </Card>
        ) : null}
        <Card>
          <CardHeader title="Start Control Integrity" eyebrow="Home" />
          <CardBody className="grid gap-3 md:grid-cols-2">
            <HomeAction title="Create new area/process unit" hint="Start with a clean plant area." onClick={onCreate} />
            <HomeAction title="Add equipment" hint="Add equipment under the selected area." onClick={onAddEquipment} />
            <HomeAction title="Import control export" hint="Upload L5X, FHX, XML, or future exports." onClick={onImport} />
            <HomeAction title="Choose documents" hint="Generate only the documents you want." onClick={onDocuments} />
            <HomeAction title="Open review queue" hint={`${pendingReviewCount} review(s) waiting.`} onClick={onReview} />
          </CardBody>
        </Card>
      </div>
      <div className="space-y-5">
        <Card>
          <CardHeader title="Workspace status" eyebrow="Today" />
          <CardBody className="space-y-3 text-sm text-zinc-400">
            <StatusLine label="Areas / process units" value={String(units.length)} />
            <StatusLine
              label="Equipment selected"
              value={selectedModule?.name ?? "Select or add equipment"}
            />
            <StatusLine label="Pending reviews" value={String(pendingReviewCount)} />
          </CardBody>
        </Card>
        <RecentImportsPanel importSources={importSources} />
      </div>
    </div>
  );
}

function HomeAction({
  title,
  hint,
  onClick,
}: {
  title: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-2xl bg-zinc-900/45 p-4 text-left transition hover:bg-zinc-900"
    >
      <span className="block text-sm font-semibold text-zinc-100">{title}</span>
      <span className="mt-2 block text-xs leading-5 text-zinc-500">{hint}</span>
    </button>
  );
}

function GuidedSetupPanel({
  units,
  selectedUnitId,
  onCreateUnit,
  onCreateEquipment,
}: {
  units: UnitSummary[];
  selectedUnitId: string;
  onCreateUnit: (payload: { name: string; area?: string | null; description?: string | null }) => void;
  onCreateEquipment: (payload: {
    processUnitId: string;
    name: string;
    module_type?: string | null;
    description?: string | null;
  }) => void;
}) {
  const [unitName, setUnitName] = useState("");
  const [unitArea, setUnitArea] = useState("");
  const [equipmentUnitId, setEquipmentUnitId] = useState(selectedUnitId);
  const [equipmentName, setEquipmentName] = useState("");
  const [equipmentType, setEquipmentType] = useState("Equipment Module");

  useEffect(() => {
    if (!equipmentUnitId && selectedUnitId) setEquipmentUnitId(selectedUnitId);
    if (!equipmentUnitId && units[0]) setEquipmentUnitId(units[0].unit.id);
  }, [equipmentUnitId, selectedUnitId, units]);

  return (
    <Card>
      <CardHeader title="Create plant structure" eyebrow="Setup" />
      <CardBody className="space-y-5">
        <form
          className="space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!unitName.trim()) return;
            onCreateUnit({ name: unitName.trim(), area: unitArea.trim() || null, description: null });
            setUnitName("");
            setUnitArea("");
          }}
        >
          <p className="text-sm font-medium text-zinc-100">Create area / process unit</p>
          <TextField label="Area or process unit name" value={unitName} onChange={setUnitName} />
          <TextField label="Plant area" value={unitArea} onChange={setUnitArea} />
          <Button type="submit" disabled={!unitName.trim()}>Create area/process unit</Button>
        </form>

        <form
          className="space-y-3 border-t border-zinc-800 pt-5"
          onSubmit={(event) => {
            event.preventDefault();
            if (!equipmentUnitId || !equipmentName.trim()) return;
            onCreateEquipment({
              processUnitId: equipmentUnitId,
              name: equipmentName.trim(),
              module_type: equipmentType.trim() || null,
              description: null,
            });
            setEquipmentName("");
          }}
        >
          <p className="text-sm font-medium text-zinc-100">Add equipment</p>
          <label className="block text-xs text-zinc-400">
            Area / process unit
            <select
              aria-label="Equipment area"
              value={equipmentUnitId}
              onChange={(event) => setEquipmentUnitId(event.target.value)}
              className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
            >
              <option value="">Choose an area</option>
              {units.map((entry) => (
                <option key={entry.unit.id} value={entry.unit.id}>
                  {entry.unit.name}
                </option>
              ))}
            </select>
          </label>
          <TextField label="Equipment name" value={equipmentName} onChange={setEquipmentName} />
          <TextField label="Equipment type" value={equipmentType} onChange={setEquipmentType} />
          <Button type="submit" disabled={!equipmentUnitId || !equipmentName.trim()}>Add equipment</Button>
        </form>
      </CardBody>
    </Card>
  );
}

function RecentImportsPanel({ importSources }: { importSources: ControlImportSource[] }) {
  return (
    <Card>
      <CardHeader title="Recent imports" eyebrow="Imports" />
      <CardBody className="space-y-3">
        {importSources.length === 0 ? (
          <EmptyState title="No imports yet." hint="Import a control export after adding equipment." />
        ) : null}
        {importSources.slice(0, 5).map((source) => (
          <div key={source.id} className="rounded-xl bg-zinc-900/45 p-3">
            <p className="text-sm font-medium text-zinc-100">{source.name}</p>
            <p className="mt-1 text-xs text-zinc-500">{source.source_system}</p>
          </div>
        ))}
      </CardBody>
    </Card>
  );
}

function DocumentLibraryPanel({
  recordsByType,
  revisionsByRecord,
  reviewItems,
  selectedFolder,
  selectedRevisionId,
  onSelectFolder,
  onSelectRevision,
}: {
  recordsByType: Map<RecordType, EngineeringRecord[]>;
  revisionsByRecord: Record<string, DocumentRevision[]>;
  reviewItems: ReviewItem[];
  selectedFolder: RecordType;
  selectedRevisionId: string;
  onSelectFolder: (recordType: RecordType) => void;
  onSelectRevision: (recordType: RecordType, revisionId: string) => void;
}) {
  const folders = RECORD_ORDER.map((type) => {
    const records = recordsByType.get(type) ?? [];
    const revisions = records.flatMap((record) => revisionsByRecord[record.id] ?? []);
    const openReviews = reviewItems.filter((item) =>
      records.some((record) => record.id === item.engineering_record_id) &&
      ["open", "needs_manual_review"].includes(item.status),
    );
    return { type, records, revisions, openReviews };
  });
  const selected = folders.find((folder) => folder.type === selectedFolder) ?? folders[0];
  const selectedType = selected?.type ?? selectedFolder;
  const selectedRecords = selected?.records ?? [];
  const selectedRevisions = selected?.revisions ?? [];
  const selectedRevision =
    selectedRevisions.find((revision) => revision.id === selectedRevisionId) ??
    selectedRevisions[0] ??
    null;
  const selectedRecord = selectedRevision
    ? selectedRecords.find((record) => record.id === selectedRevision.engineering_record_id)
    : selectedRecords[0] ?? null;

  return (
    <Card>
      <CardHeader
        title="Document Library"
        eyebrow="Controlled folders"
        trailing={<Badge tone="info">Module scoped</Badge>}
      />
      <CardBody className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <nav aria-label="Controlled document folders" className="space-y-2">
          {folders.map((folder) => {
            const latest = folder.revisions[0] ?? null;
            const missing = folder.records.length === 0 && folder.revisions.length === 0;
            const active = folder.type === selectedFolder;
            return (
              <button
                key={folder.type}
                type="button"
                onClick={() => onSelectFolder(folder.type)}
                className={`w-full rounded-xl border p-3 text-left transition ${
                  active
                    ? "border-cyan-400/60 bg-cyan-400/10"
                    : "border-zinc-800 bg-zinc-900/35 hover:border-zinc-700"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-zinc-100">
                    {RECORD_LABELS[folder.type]}
                  </span>
                  <Badge tone={missing ? "warning" : statusTone(latest?.status)}>
                    {missing ? "Missing" : fmtStatus(latest?.status)}
                  </Badge>
                </div>
                <p className="mt-1 text-xs text-zinc-500">
                  {folder.revisions.length} revision{folder.revisions.length === 1 ? "" : "s"}
                  {folder.openReviews.length ? ` · ${folder.openReviews.length} review` : ""}
                </p>
              </button>
            );
          })}
        </nav>

        <section className="rounded-2xl border border-zinc-800 bg-zinc-950/45 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
                Selected folder
              </p>
              <h3 className="mt-1 text-lg font-semibold text-zinc-50">
                {RECORD_LABELS[selectedType]}
              </h3>
              <p className="mt-1 text-sm text-zinc-400">
                {selectedRecord?.title ?? ABSENCE_COPY[selectedType]}
              </p>
            </div>
            <Badge tone={selectedRevision ? statusTone(selectedRevision.status) : "warning"}>
              {selectedRevision ? fmtStatus(selectedRevision.status) : "Needs setup"}
            </Badge>
          </div>

          {selectedRevisions.length > 0 ? (
            <div className="mt-4 flex flex-wrap gap-2">
              {selectedRevisions.map((revision) => (
                <button
                  key={revision.id}
                  type="button"
                  onClick={() => onSelectRevision(selectedType, revision.id)}
                  className={`rounded-lg border px-3 py-2 text-xs font-medium ${
                    revision.id === selectedRevision?.id
                      ? "border-cyan-400/70 bg-cyan-400/10 text-cyan-50"
                      : "border-zinc-800 bg-zinc-900/50 text-zinc-300 hover:border-zinc-700"
                  }`}
                >
                  Rev {revision.revision} · {fmtStatus(revision.status)}
                </button>
              ))}
            </div>
          ) : null}

          {selectedRevision ? (
            <div className="mt-4 grid gap-3">
              <div className="grid gap-2 md:grid-cols-3">
                <StatusLine label="Revision" value={selectedRevision.revision} />
                <StatusLine label="Status" value={fmtStatus(selectedRevision.status)} />
                <StatusLine
                  label="Source snapshot"
                  value={selectedRevision.source_logic_snapshot_id ?? "Not linked"}
                />
              </div>
              <div className="rounded-xl border border-zinc-800 bg-zinc-900/45 p-4">
                <p className="mb-3 text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
                  Document body
                </p>
                <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-lg bg-zinc-950/70 p-4 text-sm leading-6 text-zinc-100">
                  {selectedRevision.body_markdown || "No document body stored on this revision."}
                </pre>
              </div>
            </div>
          ) : (
            <EmptyState
              title="No document exists in this folder yet."
              hint="Generate a draft from parser facts, import an existing document, or create a placeholder record for engineer setup."
            />
          )}
        </section>
      </CardBody>
    </Card>
  );
}

function DocumentSelectionPanel({
  selectedDocumentTypes,
  onChange,
  selectedModule,
  generationMode,
  onGenerationModeChange,
  userNotes,
  onUserNotesChange,
  summaries,
  generationLoading,
  onGenerate,
  onOpenReview,
  onOpenLibrary,
}: {
  selectedDocumentTypes: Record<RecordType, boolean>;
  onChange: (next: Record<RecordType, boolean>) => void;
  selectedModule: EquipmentModule | null;
  generationMode: GenerationMode;
  onGenerationModeChange: (mode: GenerationMode) => void;
  userNotes: string;
  onUserNotesChange: (value: string) => void;
  summaries: AiDraftSummary[];
  generationLoading?: boolean;
  onGenerate: () => void;
  onOpenReview: () => void;
  onOpenLibrary: (recordType: RecordType, revisionId: string) => void;
}) {
  const selectedCount = GENERATABLE_DOCUMENT_TYPES.filter((type) => selectedDocumentTypes[type]).length;
  return (
    <Card>
      <CardHeader
        title="Generate Document"
        eyebrow="AI-assisted drafting"
        trailing={<Badge tone="info">Draft only</Badge>}
      />
      <CardBody className="space-y-5">
        <div>
          <p className="text-sm leading-6 text-zinc-400">
            Generate only the engineering documents you choose for{" "}
            {selectedModule?.name ?? "the selected equipment"}. INTELLI creates draft revisions and
            review items; nothing is approved automatically.
          </p>
          {!selectedModule ? (
            <p className="mt-3 rounded-lg border border-amber-800/60 bg-amber-950/30 px-3 py-2 text-sm text-amber-100">
              Select an equipment/module before generating document drafts.
            </p>
          ) : null}
          <p className="mt-3 rounded-lg border border-sky-800/60 bg-sky-950/30 px-3 py-2 text-xs leading-5 text-sky-100/85">
            Parser facts remain the source of truth. AI-assisted mode uses OpenAI only when
            backend LLM env settings and an API key are enabled; otherwise INTELLI stores a
            deterministic parser-backed draft.
          </p>
        </div>

        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Document types
          </p>
          <div className="mt-3 grid gap-2 md:grid-cols-2 lg:grid-cols-4">
            {GENERATABLE_DOCUMENT_TYPES.map((type) => (
              <label
                key={type}
                className="flex items-center gap-2 rounded-xl bg-zinc-900/45 px-3 py-2 text-sm text-zinc-200"
              >
              <input
                type="checkbox"
                checked={selectedDocumentTypes[type]}
                onChange={(event) => onChange({ ...selectedDocumentTypes, [type]: event.target.checked })}
              />
              {RECORD_LABELS[type]}
            </label>
          ))}
          </div>
        </div>

        <div className="grid gap-4 md:grid-cols-[18rem_minmax(0,1fr)]">
          <fieldset className="rounded-xl border border-zinc-800 bg-zinc-900/35 p-3">
            <legend className="px-1 text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Generation mode
            </legend>
            <div className="mt-2 space-y-2">
              <label className="flex items-center gap-2 text-sm text-zinc-200">
                <input
                  type="radio"
                  name="generation-mode"
                  value="deterministic_template"
                  checked={generationMode === "deterministic_template"}
                  onChange={() => onGenerationModeChange("deterministic_template")}
                />
                Deterministic
              </label>
              <label className="flex items-center gap-2 text-sm text-zinc-200">
                <input
                  type="radio"
                  name="generation-mode"
                  value="llm_assisted"
                  checked={generationMode === "llm_assisted"}
                  onChange={() => onGenerationModeChange("llm_assisted")}
                />
                AI-assisted
              </label>
            </div>
          </fieldset>
          <label className="block text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
            User notes
            <TextArea
              ariaLabel="User notes"
              rows={3}
              value={userNotes}
              onChange={onUserNotesChange}
              placeholder="Use Tesla-style concise narrative format, or focus on startup sequence."
              className="mt-2"
            />
          </label>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button disabled={!selectedModule || selectedCount === 0 || generationLoading} onClick={onGenerate}>
            {generationLoading
              ? "Generating selected..."
              : `Generate ${selectedCount || ""} selected document draft${selectedCount === 1 ? "" : "s"}`}
          </Button>
          <p className="text-xs text-zinc-500">Approved documents are preserved.</p>
        </div>

        {summaries.length > 0 ? (
          <AiDraftResultSummary
            summaries={summaries}
            onOpenReview={onOpenReview}
            onOpenLibrary={onOpenLibrary}
          />
        ) : null}
      </CardBody>
    </Card>
  );
}

function AiDraftResultSummary({
  summaries,
  onOpenReview,
  onOpenLibrary,
}: {
  summaries: AiDraftSummary[];
  onOpenReview: () => void;
  onOpenLibrary: (recordType: RecordType, revisionId: string) => void;
}) {
  return (
    <div className="rounded-xl border border-cyan-400/30 bg-cyan-400/10 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-cyan-100">Generated draft summary</p>
          <p className="mt-1 text-xs leading-5 text-cyan-100/75">
            Draft revision(s) were created and added to the review queue.
          </p>
        </div>
        <Button tone="secondary" onClick={onOpenReview}>
          Open Draft for Review
        </Button>
      </div>
      <div className="mt-4 grid gap-3">
        {summaries.map((summary) => (
          <div key={summary.revision.id} className="rounded-lg border border-cyan-300/15 bg-zinc-950/35 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-medium text-cyan-50">
                {RECORD_LABELS[summary.record_type]}
              </p>
              <div className="flex flex-wrap gap-1.5">
                <Badge tone={statusTone(summary.revision.status)}>
                  {fmtStatus(summary.revision.status)}
                </Badge>
                <Badge tone="info">Confidence: {summary.confidence}</Badge>
              </div>
            </div>
            <p className="mt-2 text-xs text-cyan-100/75">
              Revision {summary.revision.revision} - Provider: {summary.provider_name}
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                tone="secondary"
                onClick={() => onOpenLibrary(summary.record_type, summary.revision.id)}
              >
                Open in Document Library
              </Button>
            </div>
            {summary.warnings.length > 0 ? (
              <div className="mt-3">
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-cyan-100/60">
                  Warnings
                </p>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-cyan-50/85">
                  {summary.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {summary.missing_facts.length > 0 ? (
              <div className="mt-3">
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-cyan-100/60">
                  Missing facts
                </p>
                <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-cyan-50/85">
                  {summary.missing_facts.map((fact) => (
                    <li key={fact.key}>{fact.label}: Needs engineer input</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function SafeActionsPanel({
  unit,
  module,
  canDeleteUnit,
  onDeleteModule,
  onDeleteUnit,
  onReset,
}: {
  unit: ProcessUnit | null;
  module: EquipmentModule | null;
  canDeleteUnit: boolean;
  onDeleteModule: () => void;
  onDeleteUnit: () => void;
  onReset: () => void;
}) {
  return (
    <Card>
      <CardHeader title="Safe reset and delete" eyebrow="Cleanup" />
      <CardBody className="space-y-3">
        <Button tone="secondary" disabled={!module} onClick={onDeleteModule}>
          Delete equipment/module
        </Button>
        <Button tone="secondary" disabled={!unit || !canDeleteUnit} onClick={onDeleteUnit}>
          Delete empty process unit
        </Button>
        <Button tone="secondary" onClick={onReset}>
          Reset generated drafts/reviews
        </Button>
        <p className="text-xs leading-5 text-zinc-500">
          Approved revisions are protected. Deleting an area requires it to be empty.
        </p>
      </CardBody>
    </Card>
  );
}

function PlaceholderPanel({ title, hint }: { title: string; hint: string }) {
  return (
    <Card>
      <CardHeader title={title} eyebrow="Control Integrity" />
      <CardBody>
        <EmptyState title={title} hint={hint} />
      </CardBody>
    </Card>
  );
}

function PlantExplorer({
  loading,
  units,
  selectedUnitId,
  selectedModuleId,
  onSelectUnit,
  onSelectModule,
}: {
  loading: boolean;
  units: UnitSummary[];
  selectedUnitId: string;
  selectedModuleId: string;
  onSelectUnit: (id: string) => void;
  onSelectModule: (id: string) => void;
}) {
  return (
    <Card>
      <CardHeader title="Plant Explorer" eyebrow="Control system" />
      <CardBody className="space-y-3">
        {loading ? <LoadingLine>Loading plant explorer...</LoadingLine> : null}
        {!loading && units.length === 0 ? (
          <EmptyState
            title="No plant areas discovered yet."
            hint="Import a control project and INTELLI will build the plant explorer automatically."
          />
        ) : null}
        {units.map(({ unit, modules, reviewItems }) => {
          const pending = reviewItems.filter((item) =>
            ["open", "needs_manual_review"].includes(item.status),
          ).length;
          const selected = unit.id === selectedUnitId;
          return (
            <div
              key={unit.id}
              className={`rounded-xl border p-3 ${
                selected
                  ? "border-cyan-400/40 bg-cyan-400/10"
                  : "border-zinc-800 bg-zinc-900/35"
              }`}
            >
              <button
                type="button"
                onClick={() => onSelectUnit(unit.id)}
                className="w-full text-left"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-medium text-zinc-100">{unit.name}</p>
                    <p className="mt-1 text-xs text-zinc-500">
                      {unit.area || "Discovered plant area"}
                    </p>
                  </div>
                  <Badge tone={pending ? "warning" : "success"}>
                    {pending ? "Needs review" : "Up to date"}
                  </Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Stat value={modules.length} label="equipment" />
                  <Stat value={pending} label="reviews" />
                </div>
              </button>

              {selected && modules.length > 0 ? (
                <div className="mt-3 space-y-1 border-t border-zinc-800 pt-3">
                  {modules.map((module) => (
                    <button
                      key={module.id}
                      type="button"
                      onClick={() => onSelectModule(module.id)}
                      className={`block w-full rounded-lg px-3 py-2 text-left text-xs transition ${
                        module.id === selectedModuleId
                          ? "bg-cyan-300 text-cyan-950"
                          : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                      }`}
                    >
                      {module.name}
                    </button>
                  ))}
                  <div className="mt-3 rounded-lg border border-zinc-800 bg-zinc-950/45 p-3 text-xs text-zinc-500">
                    <p className="font-medium text-zinc-300">Documents</p>
                    <div className="mt-2 grid gap-1.5">
                      {RECORD_ORDER.map((type) => (
                        <span key={type}>{RECORD_LABELS[type]}</span>
                      ))}
                    </div>
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </CardBody>
    </Card>
  );
}

function ImportUploadPanel({
  apiBase,
  units,
  selectedUnitId,
  selectedModuleId,
  onImported,
}: {
  apiBase: string;
  units: UnitSummary[];
  selectedUnitId: string;
  selectedModuleId: string;
  onImported: (result: ImportSyncResult) => void;
}) {
  const [unitMode, setUnitMode] = useState<"existing" | "new">("existing");
  const [moduleMode, setModuleMode] = useState<"existing" | "new">("existing");
  const [unitId, setUnitId] = useState(selectedUnitId);
  const [moduleId, setModuleId] = useState(selectedModuleId);
  const [unitName, setUnitName] = useState("Converting Area");
  const [unitArea, setUnitArea] = useState("Converting Area");
  const [moduleName, setModuleName] = useState("Filtrate Separator");
  const [moduleType, setModuleType] = useState("Equipment Module");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportSyncResult | null>(null);

  const selectedUnit = units.find((entry) => entry.unit.id === unitId) ?? null;
  const selectedModule = selectedUnit?.modules.find((module) => module.id === moduleId) ?? null;
  const fileName = file?.name ?? "";
  const detectedVendor = fileName ? inferVendor(fileName) : "Waiting for export";
  const detectedController = fileName ? displayFileStem(fileName) : "Choose a file to detect";
  const detectedArea = selectedUnit?.unit.area || selectedUnit?.unit.name || unitArea;
  const detectedUnitName = selectedUnit?.unit.name || unitName;
  const detectedModules =
    selectedUnit?.modules.length ? selectedUnit.modules : moduleName ? [{ id: "new", name: moduleName }] : [];

  useEffect(() => {
    if (!unitId && selectedUnitId) setUnitId(selectedUnitId);
    if (!moduleId && selectedModuleId) setModuleId(selectedModuleId);
  }, [moduleId, selectedModuleId, selectedUnitId, unitId]);

  useEffect(() => {
    if (unitMode === "existing" && !unitId && units[0]) {
      setUnitId(units[0].unit.id);
    }
  }, [unitId, unitMode, units]);

  useEffect(() => {
    if (moduleMode === "existing" && selectedUnit?.modules.length) {
      const stillValid = selectedUnit.modules.some((module) => module.id === moduleId);
      if (!stillValid) setModuleId(selectedUnit.modules[0].id);
    }
    if (moduleMode === "existing" && selectedUnit && selectedUnit.modules.length === 0) {
      setModuleMode("new");
    }
  }, [moduleId, moduleMode, selectedUnit]);

  async function submitImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setResult(null);
    if (!file) {
      setError("Choose an export file before importing.");
      return;
    }
    setSubmitting(true);
    try {
      let targetUnitId = unitId;
      if (unitMode === "new" || !targetUnitId) {
        const unitRes = await axios.post<ProcessUnit>(`${apiBase}/api/process-units`, {
          name: unitName,
          area: unitArea || null,
          description: null,
        });
        targetUnitId = unitRes.data.id;
      }

      let targetModuleId = moduleId;
      if (moduleMode === "new" || !targetModuleId) {
        const moduleRes = await axios.post<EquipmentModule>(
          `${apiBase}/api/process-units/${targetUnitId}/modules`,
          {
            name: moduleName,
            module_type: moduleType || null,
            description: null,
            aliases: [],
            metadata_json: {},
          },
        );
        targetModuleId = moduleRes.data.id;
      }

      const sourceRes = await axios.post<ControlImportSource>(`${apiBase}/api/import-sources`, {
        process_unit_id: targetUnitId,
        module_id: targetModuleId,
        name: `${file.name} upload`,
        source_system: inferVendor(file.name).toLowerCase().replaceAll(" ", "_"),
        acquisition_mode: "manual_upload",
        connector_hint: null,
        endpoint_url: null,
        export_path: null,
        schedule: null,
        is_enabled: true,
        config_json: { discovered_from: "control_integrity_import" },
      });
      const targetSourceId = sourceRes.data.id;

      const form = new FormData();
      form.append("file", file);
      const uploadRes = await axios.post<ImportSyncResult>(
        `${apiBase}/api/import-sources/${targetSourceId}/sync-upload`,
        form,
        { params: { actor: "controls.engineer" } },
      );
      setResult(uploadRes.data);
      onImported(uploadRes.data);
    } catch (err) {
      setError(extractIntelliError(err, "Import failed"));
    } finally {
      setSubmitting(false);
    }
  }

  const reviewUrl =
    result?.process_unit_id && result?.module_id
      ? `/workspace/integrity?unitId=${result.process_unit_id}&moduleId=${result.module_id}`
      : "";

  return (
    <Card>
      <CardHeader
        title="Step 1 - Import Control Project"
        eyebrow="Engineer workflow"
        trailing={<Badge tone="info">L5X / FHX / Siemens XML</Badge>}
      />
      <CardBody>
        <form onSubmit={submitImport} className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-4">
            <p className="text-sm font-medium text-zinc-100">Import Control Project</p>
            <p className="mt-2 text-xs leading-5 text-zinc-500">
              Supported formats: Rockwell L5X, DeltaV FHX, Siemens XML, and future vendor exports.
            </p>
            <label className="block text-xs text-zinc-400">
              Export file
              <input
                aria-label="Export file"
                type="file"
                accept=".xml,.l5x,.L5X,.fhx,.FHX"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="mt-1 block w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100 file:mr-3 file:rounded-md file:border-0 file:bg-cyan-300 file:px-3 file:py-1.5 file:text-cyan-950"
              />
            </label>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={submitting}>
                {submitting ? "Importing..." : "Import Control Project"}
              </Button>
              <p className="text-xs text-zinc-500">
                INTELLI creates a snapshot, compares it to the previous snapshot,
                and routes affected documents for engineer review.
              </p>
            </div>
          </div>

          <div className="rounded-xl border border-cyan-400/20 bg-cyan-400/[0.04] p-4">
            <p className="text-sm font-medium text-cyan-100">Step 2 - Automatic Discovery</p>
            <div className="mt-3 grid gap-2 text-sm">
              <DiscoveryRow label="Vendor" value={detectedVendor} />
              <DiscoveryRow label="Controller" value={detectedController} />
              <DiscoveryRow label="Plant" value={detectedArea} />
              <DiscoveryRow label="Area" value={detectedArea} />
              <DiscoveryRow label="Process Unit" value={detectedUnitName} />
            </div>
            <div className="mt-4">
              <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-cyan-200/70">
                Equipment Modules
              </p>
              <div className="mt-2 grid gap-1.5 text-sm text-zinc-200">
                {detectedModules.map((module) => (
                  <span key={module.id}>✓ {module.name}</span>
                ))}
              </div>
            </div>

            <div className="mt-4 grid gap-3 rounded-xl border border-zinc-800 bg-zinc-950/45 p-3">
              <p className="text-xs font-medium text-zinc-300">
                {selectedUnit ? "Existing object found. Link to existing?" : "No existing match found. Create new."}
              </p>
              {units.length ? (
                <label className="block text-xs text-zinc-400">
                  Link discovered area
                  <select
                    aria-label="Link discovered area"
                    value={unitMode === "existing" ? unitId : ""}
                    onChange={(event) => {
                      setUnitMode("existing");
                      setUnitId(event.target.value);
                    }}
                    className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
                  >
                    {units.map((entry) => (
                      <option key={entry.unit.id} value={entry.unit.id}>
                        {entry.unit.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {units.length ? (
                  <ModeButton active={unitMode === "existing"} onClick={() => setUnitMode("existing")}>
                    Link to existing
                  </ModeButton>
                ) : null}
                <ModeButton active={unitMode === "new" || units.length === 0} onClick={() => setUnitMode("new")}>
                  Create new
                </ModeButton>
              </div>
              {unitMode === "new" || units.length === 0 ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  <TextField label="Discovered area name" value={unitName} onChange={setUnitName} />
                  <TextField label="Plant area" value={unitArea} onChange={setUnitArea} />
                </div>
              ) : null}

              {selectedUnit?.modules.length ? (
                <label className="block text-xs text-zinc-400">
                  Link discovered equipment
                  <select
                    aria-label="Link discovered equipment"
                    value={moduleMode === "existing" ? moduleId : ""}
                    onChange={(event) => {
                      setModuleMode("existing");
                      setModuleId(event.target.value);
                    }}
                    className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
                  >
                    {selectedUnit.modules.map((module) => (
                      <option key={module.id} value={module.id}>
                        {module.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {selectedUnit?.modules.length ? (
                  <ModeButton active={moduleMode === "existing"} onClick={() => setModuleMode("existing")}>
                    Link equipment
                  </ModeButton>
                ) : null}
                <ModeButton active={moduleMode === "new"} onClick={() => setModuleMode("new")}>
                  Create equipment
                </ModeButton>
              </div>
              {moduleMode === "new" || !selectedUnit?.modules.length ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  <TextField label="Discovered equipment name" value={moduleName} onChange={setModuleName} />
                  <TextField label="Equipment type" value={moduleType} onChange={setModuleType} />
                </div>
              ) : null}
            </div>
          </div>
        </form>

        {error ? <InlineError>{error}</InlineError> : null}
        {result ? (
          <ImportResultSummary result={result} reviewUrl={reviewUrl} moduleName={selectedModule?.name} />
        ) : null}
      </CardBody>
    </Card>
  );
}

function ModeButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md border px-2.5 py-1.5 text-xs ${
        active
          ? "border-cyan-300 bg-cyan-300 text-cyan-950"
          : "border-zinc-700 bg-zinc-950 text-zinc-300"
      }`}
    >
      {children}
    </button>
  );
}

function TextField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-xs text-zinc-400">
      {label}
      <input
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
      />
    </label>
  );
}

function DiscoveryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-zinc-800/70 pb-1.5 last:border-b-0">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="truncate text-right text-sm text-zinc-100">{value}</span>
    </div>
  );
}

function ImportResultSummary({
  result,
  reviewUrl,
  moduleName,
}: {
  result: ImportSyncResult;
  reviewUrl: string;
  moduleName?: string;
}) {
  const changed = result.changed || Boolean(result.logic_diff?.changed);
  const snapshotCreated = Boolean(result.snapshot_id ?? result.snapshot?.id);
  const previousSnapshotFound = Boolean(
    result.run.previous_project_id || result.logic_diff?.previous_snapshot_id,
  );
  const missingDocuments = Math.max(0, RECORD_ORDER.length - result.affected_record_ids.length);
  return (
    <div className="mt-5 rounded-xl border border-cyan-400/30 bg-cyan-400/10 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-cyan-100">Step 3 - Import Summary</p>
          <p className="mt-1 text-xs leading-5 text-cyan-100/75">
            Imported latest export{moduleName ? ` for ${moduleName}` : ""}. Created immutable
            snapshot {result.snapshot_id ?? result.snapshot?.id ?? "unknown"}.
          </p>
        </div>
        <Badge tone={changed ? "warning" : "success"}>
          {changed ? "Changes found" : "Baseline or no changes"}
        </Badge>
      </div>
      <div className="mt-4 grid gap-2 text-sm text-cyan-50 md:grid-cols-2">
        <ChecklistLine checked={snapshotCreated} text="Snapshot created" />
        <ChecklistLine checked={previousSnapshotFound} text="Previous snapshot found" />
        <ChecklistLine checked={Boolean(result.diff_id ?? result.logic_diff?.id)} text="Logic diff completed" />
        <ChecklistLine checked text={`${result.review_item_ids.length} engineering records require review`} />
        <ChecklistLine checked={missingDocuments > 0} text={`${missingDocuments} new engineering records missing`} />
      </div>
      <p className="mt-3 text-xs leading-5 text-cyan-100/75">
        {result.run.diff_summary ||
          "INTELLI compared this export with the previous snapshot and updated the review queue."}
      </p>
      {reviewUrl ? (
        <a
          href={reviewUrl}
          className="mt-4 inline-flex rounded-lg bg-cyan-300 px-3.5 py-2 text-sm font-medium text-cyan-950 hover:bg-cyan-200"
        >
          Open Review Workspace
        </a>
      ) : null}
    </div>
  );
}

function ChecklistLine({ checked, text }: { checked: boolean; text: string }) {
  return (
    <p className="rounded-lg border border-cyan-300/15 bg-zinc-950/35 px-3 py-2">
      <span className={checked ? "text-emerald-200" : "text-zinc-500"}>{checked ? "✓" : "•"}</span>{" "}
      {text}
    </p>
  );
}

function ModuleHeader({
  loading,
  module,
  unit,
  snapshot,
  diff,
  reviewItems,
  missingDocumentCount,
  onReview,
  onDocuments,
  onImport,
}: {
  loading: boolean;
  module: EquipmentModule | null;
  unit: ProcessUnit | null;
  snapshot: LogicSnapshot | null;
  diff: LogicDiff | null;
  reviewItems: ReviewItem[];
  missingDocumentCount: number;
  onReview: () => void;
  onDocuments: () => void;
  onImport: () => void;
}) {
  const pending = reviewItems.filter((item) =>
    ["open", "needs_manual_review"].includes(item.status),
  ).length;
  const recommendation = !snapshot
    ? "Import a control export for this equipment."
    : pending
      ? "Review proposed document changes."
      : missingDocumentCount
        ? "Generate first drafts for missing documents."
        : "Monitor future imports for logic/document drift.";
  return (
    <Card>
      <CardHeader
        title={module ? module.name : "Select an equipment module"}
        eyebrow={unit?.name ?? "Plant Explorer"}
        trailing={
          <Badge tone={pending ? "warning" : "success"}>
            {pending ? "Needs review" : "Up to date"}
          </Badge>
        }
      />
      <CardBody>
        {loading ? <LoadingLine>Loading module record...</LoadingLine> : null}
        {!module ? (
          <EmptyState title="Select equipment to see integrity status." />
        ) : (
          <div className="space-y-4">
            <div className="grid gap-3 md:grid-cols-3">
              <SummaryTile
                label="Latest logic snapshot"
                value={snapshot?.source_filename || "No snapshot"}
              />
              <SummaryTile label="Document health" value={`${missingDocumentCount} missing`} />
              <SummaryTile label="Reviews open" value={String(pending)} />
            </div>
            <div className="rounded-2xl bg-zinc-900/45 p-4">
              <p className="text-sm font-medium text-zinc-100">Recommended next action</p>
              <p className="mt-2 text-sm leading-6 text-zinc-400">{recommendation}</p>
              <div className="mt-4 flex flex-wrap gap-2">
                {!snapshot ? <Button onClick={onImport}>Import Control Export</Button> : null}
                {pending ? <Button onClick={onReview}>Open Review Queue</Button> : null}
                {missingDocumentCount ? (
                  <Button tone={pending ? "secondary" : "primary"} onClick={onDocuments}>
                    Open Documents
                  </Button>
                ) : null}
              </div>
            </div>
          </div>
        )}
        {diff ? (
          <details className="mt-4 rounded-xl bg-zinc-950/45 p-4">
            <summary className="cursor-pointer text-sm font-medium text-zinc-100">
              View technical details
            </summary>
            <p className="mt-3 text-xs leading-5 text-zinc-400">
              {String(diff.summary_payload.summary ?? "No summary available.")}
            </p>
          </details>
        ) : null}
      </CardBody>
    </Card>
  );
}

function SummaryTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/35 p-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
        {label}
      </p>
      <p className="mt-2 truncate text-sm text-zinc-100">{value}</p>
    </div>
  );
}

function EngineeringRecordsSection({
  recordsByType,
  revisionsByRecord,
  reviewItems,
  proposedById,
  snapshot,
  diff,
  generationLoading,
  canGenerateDraft,
  onGenerateDraft,
  onGenerateProposed,
  onReviewDraft,
  onDeleteRevision,
}: {
  recordsByType: Map<RecordType, EngineeringRecord[]>;
  revisionsByRecord: Record<string, DocumentRevision[]>;
  reviewItems: ReviewItem[];
  proposedById: Record<string, ProposedDocumentUpdate>;
  snapshot: LogicSnapshot | null;
  diff: LogicDiff | null;
  generationLoading: Record<string, boolean>;
  canGenerateDraft: boolean;
  onGenerateDraft: (recordType: RecordType, record?: EngineeringRecord) => void;
  onGenerateProposed: (record: EngineeringRecord) => void;
  onReviewDraft: () => void;
  onDeleteRevision: (revision: DocumentRevision) => void;
}) {
  return (
    <Card>
      <CardHeader title="Documents" eyebrow="Generate and review" />
      <CardBody className="grid gap-3 lg:grid-cols-2">
        {RECORD_ORDER.map((type) => {
          const records = recordsByType.get(type) ?? [];
          const label = RECORD_LABELS[type];
          return (
            <div key={type} className="rounded-2xl bg-zinc-900/35 p-4">
              <div className="space-y-3">
                {records.length === 0 ? (
                  <>
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <h3 className="text-sm font-semibold text-zinc-100">{label}</h3>
                        <p className="mt-1 text-sm text-zinc-400">{ABSENCE_COPY[type]}</p>
                      </div>
                      <Badge tone="warning">Missing</Badge>
                    </div>
                    <Button
                      className="mt-3"
                      disabled={!canGenerateDraft || generationLoading[`draft:${type}`]}
                      onClick={() => onGenerateDraft(type)}
                    >
                      {generationLoading[`draft:${type}`]
                        ? "Generating..."
                        : type === "knowledge_issue"
                          ? "Create first entry"
                          : "Generate first draft"}
                    </Button>
                    {!canGenerateDraft ? (
                      <p className="mt-2 text-xs text-zinc-500">Select equipment first.</p>
                    ) : null}
                  </>
                ) : (
                  records.map((record) => {
                    const revisions = revisionsByRecord[record.id] ?? [];
                    const approved = latestApprovedRevision(revisions);
                    const latest = latestRevision(revisions);
                    const review = reviewItems.find(
                      (item) => item.engineering_record_id === record.id,
                    );
                    const activeReview = reviewItems.find(
                      (item) =>
                        item.engineering_record_id === record.id &&
                        ["open", "needs_manual_review"].includes(item.status),
                    );
                    const proposed = review?.proposed_document_update_id
                      ? proposedById[review.proposed_document_update_id]
                      : null;
                    const status = approved
                      ? "Approved"
                      : latest?.status === "draft"
                        ? "Draft awaiting review"
                        : latest?.status === "proposed"
                          ? "Proposed revision"
                          : latest?.status === "rejected"
                            ? "Rejected"
                            : fmtStatus(record.status);
                    const explanation = approved
                      ? "Approved document exists. Generate a proposed revision when logic changes."
                      : latest?.status === "rejected"
                        ? "Last draft was rejected. Generate a new draft when ready."
                        : latest
                          ? "Generated from latest logic snapshot."
                          : "No draft exists yet.";
                    return (
                      <div
                        key={record.id}
                        className="rounded-xl bg-zinc-950/45 p-3"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div>
                            <p className="text-sm font-semibold text-zinc-100">{label}</p>
                            <p className="mt-1 text-sm text-zinc-400">{explanation}</p>
                            <p className="mt-1 text-xs text-zinc-500">{record.title}</p>
                          </div>
                          <div className="flex flex-wrap gap-1.5">
                            <Badge tone={statusTone(latest?.status ?? record.status)}>{status}</Badge>
                            {activeReview ? <Badge tone="warning">Review required</Badge> : null}
                            {proposed ? <Badge tone="info">Proposed update</Badge> : null}
                          </div>
                        </div>
                        <p className="mt-3 text-xs text-zinc-500">
                          Latest revision: {latest?.revision ?? "None"}
                          {snapshot ? ` · Snapshot: ${snapshot.source_filename}` : ""}
                        </p>
                        <div className="mt-3 flex flex-wrap gap-2">
                          {approved ? (
                            <Button
                              tone="secondary"
                              disabled={generationLoading[`proposed:${record.id}`]}
                              onClick={() => onGenerateProposed(record)}
                            >
                              {generationLoading[`proposed:${record.id}`]
                                ? "Generating..."
                                : "Generate Proposed Revision"}
                            </Button>
                          ) : latest ? (
                            <>
                              <Button tone="secondary" disabled={!activeReview} onClick={onReviewDraft}>
                                {activeReview ? "Review Draft" : "Draft Created"}
                              </Button>
                              {["draft", "rejected"].includes(latest.status) ? (
                                <Button tone="secondary" onClick={() => onDeleteRevision(latest)}>
                                  Delete draft
                                </Button>
                              ) : null}
                            </>
                          ) : (
                            <Button
                              disabled={generationLoading[`draft:${record.id}`]}
                              onClick={() => onGenerateDraft(type, record)}
                            >
                              {generationLoading[`draft:${record.id}`]
                                ? "Generating..."
                                : "Generate Draft"}
                            </Button>
                          )}
                        </div>
                        <details className="mt-3 text-xs text-zinc-500">
                          <summary className="cursor-pointer text-zinc-400">View technical details</summary>
                          <div className="mt-2 grid gap-2">
                            <StatusLine label="Current Approved Version" value={approved?.revision ?? "None"} />
                            <StatusLine label="Revision History" value={`${revisions.length} revisions`} />
                            <StatusLine label="Revision Status" value={fmtStatus(latest?.status)} />
                            <StatusLine
                              label="Pending Proposed Revision"
                              value={proposed ? fmtStatus(proposed.status) : "None"}
                            />
                            <StatusLine
                              label="Diff Summary"
                              value={
                                diff
                                  ? String(diff.summary_payload.summary ?? (diff.changed ? "Changes detected" : "No changes"))
                                  : "No diff yet"
                              }
                            />
                          </div>
                        </details>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          );
        })}
      </CardBody>
    </Card>
  );
}

function StatusLine({ label, value }: { label: string; value: string }) {
  return (
    <p className="rounded-lg border border-zinc-800 bg-zinc-950/45 px-3 py-2">
      <span className="block text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-600">
        {label}
      </span>
      <span className="mt-1 block truncate text-zinc-200">{value}</span>
    </p>
  );
}

function ReviewItemsSection({
  reviewItems,
  records,
  module,
  revisionsByRecord,
  proposedById,
  diff,
  onApprove,
  onReject,
}: {
  reviewItems: ReviewItem[];
  records: EngineeringRecord[];
  module: EquipmentModule | null;
  revisionsByRecord: Record<string, DocumentRevision[]>;
  proposedById: Record<string, ProposedDocumentUpdate>;
  diff: LogicDiff | null;
  onApprove: (item: ReviewItem) => void;
  onReject: (item: ReviewItem) => void;
}) {
  const [openReviewId, setOpenReviewId] = useState<string | null>(null);
  return (
    <Card>
      <CardHeader title="Review Queue" eyebrow="Engineer approval" />
      <CardBody className="space-y-3">
        {reviewItems.length === 0 ? (
          <EmptyState
            title="No engineer reviews required."
            hint="Import a new snapshot to monitor drift and surface proposed revisions."
          />
        ) : null}
        {reviewItems.map((item) => {
          const record = records.find((entry) => entry.id === item.engineering_record_id);
          const revisions = record ? revisionsByRecord[record.id] ?? [] : [];
          const approved = latestApprovedRevision(revisions);
          const latest = latestRevision(revisions);
          const open = openReviewId === item.id;
          const proposed = item.proposed_document_update_id
            ? proposedById[item.proposed_document_update_id]
            : null;
          return (
            <div key={item.id} className="rounded-2xl bg-zinc-900/35 p-4">
              <div className="grid gap-3 md:grid-cols-[1fr_1fr_1.4fr_auto] md:items-center">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
                    Equipment
                  </p>
                  <p className="mt-1 text-sm font-medium text-zinc-100">{module?.name ?? "Selected equipment"}</p>
                </div>
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
                    Document
                  </p>
                  <p className="mt-1 text-sm text-zinc-100">
                    {record ? RECORD_LABELS[record.record_type] : "Unlinked record"}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
                    Reason
                  </p>
                  <p className="mt-1 text-sm text-zinc-400">
                    {item.reason || "Review required by latest import."}
                  </p>
                </div>
                <div className="flex flex-wrap gap-2 md:justify-end">
                  <Badge tone={statusTone(item.status)}>{fmtStatus(item.status)}</Badge>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap gap-2">
                <Button
                  tone="secondary"
                  onClick={() => setOpenReviewId(open ? null : item.id)}
                >
                  {open ? "Close Review" : "Open Review ->"}
                </Button>
                <Button
                  onClick={() => onApprove(item)}
                  disabled={item.status === "approved" || item.status === "rejected"}
                >
                  Approve
                </Button>
                <Button
                  tone="secondary"
                  onClick={() => onReject(item)}
                  disabled={item.status === "approved" || item.status === "rejected"}
                >
                  Reject
                </Button>
              </div>
              {open ? (
                <ReviewDocumentPanel
                  latest={latest}
                  approved={approved}
                  proposed={proposed}
                />
              ) : null}
              <details className="mt-4 text-xs text-zinc-500">
                <summary className="cursor-pointer text-zinc-400">View technical details</summary>
                <ProposedUpdateView
                  approved={approved}
                  latest={latest}
                  proposed={proposed}
                  diff={diff}
                />
              </details>
            </div>
          );
        })}
      </CardBody>
    </Card>
  );
}

function ReviewDocumentPanel({
  latest,
  approved,
  proposed,
}: {
  latest: DocumentRevision | null;
  approved: DocumentRevision | null;
  proposed: ProposedDocumentUpdate | null;
}) {
  const generatedBody =
    proposed?.proposed_body ??
    (latest && latest.status !== "approved" ? latest.body_markdown : null);
  const generatedStructured =
    proposed?.proposed_structured_changes ??
    (latest && latest.status !== "approved" ? latest.structured_content : null);

  return (
    <div className="mt-4 rounded-2xl bg-zinc-950/55 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-zinc-100">Generated document draft</p>
          <p className="mt-1 text-xs leading-5 text-zinc-500">
            Review this generated content before approving it.
          </p>
        </div>
        {latest ? <Badge tone={statusTone(latest.status)}>{fmtStatus(latest.status)}</Badge> : null}
      </div>
      <div className="mt-3">
        {generatedBody ? (
          <Code>{generatedBody}</Code>
        ) : generatedStructured ? (
          <Code>{jsonBlock(generatedStructured)}</Code>
        ) : approved?.body_markdown ? (
          <Code>{approved.body_markdown}</Code>
        ) : (
          <p className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3 text-xs text-zinc-500">
            No generated document body is linked to this review item yet.
          </p>
        )}
      </div>
    </div>
  );
}

function ProposedUpdateView({
  approved,
  latest,
  proposed,
  diff,
}: {
  approved: DocumentRevision | null;
  latest: DocumentRevision | null;
  proposed: ProposedDocumentUpdate | null;
  diff: LogicDiff | null;
}) {
  return (
    <div className="mt-4 grid gap-3">
      <div>
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
          Current Approved Version
        </p>
        {approved?.body_markdown ? (
          <Code>{approved.body_markdown}</Code>
        ) : approved ? (
          <Code>{jsonBlock(approved.structured_content)}</Code>
        ) : (
          <p className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3 text-xs text-zinc-500">
            No approved revision yet.
          </p>
        )}
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between gap-3">
          <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Generated Draft Or Proposed Revision
          </p>
          {proposed ? (
            <Badge tone={statusTone(proposed.status)}>
              {fmtStatus(proposed.status)}
              {proposed.confidence ? ` · ${proposed.confidence}` : ""}
            </Badge>
          ) : null}
        </div>
        {proposed?.proposed_body ? (
          <Code>{proposed.proposed_body}</Code>
        ) : proposed ? (
          <Code>{jsonBlock(proposed.proposed_structured_changes)}</Code>
        ) : latest && latest.status !== "approved" && latest.body_markdown ? (
          <Code>{latest.body_markdown}</Code>
        ) : latest && latest.status !== "approved" ? (
          <Code>{jsonBlock(latest.structured_content)}</Code>
        ) : (
          <p className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3 text-xs text-zinc-500">
            No generated draft or proposed update linked.
          </p>
        )}
      </div>
      {diff ? (
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Diff Summary
          </p>
          <Code>{jsonBlock(diff.summary_payload)}</Code>
        </div>
      ) : null}
    </div>
  );
}
