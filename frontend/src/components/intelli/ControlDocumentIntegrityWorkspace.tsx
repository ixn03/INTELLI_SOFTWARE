"use client";

import axios from "axios";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { extractIntelliError, useIntelliProject } from "@/context/IntelliProjectContext";
import type {
  ApprovalHistory,
  ControlImportSource,
  DocumentRevision,
  EngineeringRecord,
  EquipmentModule,
  ImportSyncResult,
  LogicDiff,
  LogicSnapshot,
  ModuleRecord,
  ProcessUnit,
  ProcessUnitListResponse,
  ProposedDocumentUpdate,
  RecordType,
  ReviewItem,
} from "@/types/processKnowledge";

import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  Code,
  EmptyState,
  InlineError,
  LoadingLine,
  Stat,
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

const RECORD_LABELS: Record<RecordType, string> = {
  control_narrative: "Control narrative",
  io_list: "IO list",
  cause_effect_matrix: "Cause/effect matrix",
  alarm_rationalization: "Alarm rationalization",
  moc: "MOC",
  knowledge_issue: "Knowledge issue",
  engineering_note: "Engineering note",
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

function jsonBlock(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

interface UnitSummary {
  unit: ProcessUnit;
  modules: EquipmentModule[];
  reviewItems: ReviewItem[];
}

export default function ControlDocumentIntegrityWorkspace() {
  const { apiBase } = useIntelliProject();
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedUnitId = searchParams.get("unitId") ?? "";
  const requestedModuleId = searchParams.get("moduleId") ?? "";
  const [units, setUnits] = useState<UnitSummary[]>([]);
  const [selectedUnitId, setSelectedUnitId] = useState("");
  const [selectedModuleId, setSelectedModuleId] = useState("");
  const [moduleRecord, setModuleRecord] = useState<ModuleRecord | null>(null);
  const [records, setRecords] = useState<EngineeringRecord[]>([]);
  const [revisionsByRecord, setRevisionsByRecord] = useState<Record<string, DocumentRevision[]>>({});
  const [reviewItems, setReviewItems] = useState<ReviewItem[]>([]);
  const [proposedById, setProposedById] = useState<Record<string, ProposedDocumentUpdate>>({});
  const [latestDiff, setLatestDiff] = useState<LogicDiff | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [actionMessage, setActionMessage] = useState("");

  const selectedUnit = units.find((entry) => entry.unit.id === selectedUnitId) ?? null;
  const selectedModule =
    selectedUnit?.modules.find((module) => module.id === selectedModuleId) ??
    moduleRecord?.module ??
    null;

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
      const linkedUnit = requestedModuleId
        ? summaries.find((entry) =>
            entry.modules.some((module) => module.id === requestedModuleId),
          )
        : null;
      const requestedUnit =
        summaries.find((entry) => entry.unit.id === requestedUnitId) ?? linkedUnit;
      const firstUnit = requestedUnit ?? summaries[0];
      if (firstUnit) {
        const nextModule =
          firstUnit.modules.find((module) => module.id === requestedModuleId) ??
          firstUnit.modules[0];
        setSelectedUnitId((current) => requestedUnit?.unit.id || current || firstUnit.unit.id);
        setSelectedModuleId((current) => nextModule?.id || current || "");
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

  return (
    <main className="min-h-screen bg-[#050914] p-5 text-zinc-100 md:p-7">
      <div className="mx-auto flex max-w-7xl flex-col gap-5">
        <header className="flex flex-col gap-3 border-b border-zinc-800 pb-5 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-cyan-200/70">
              Control Document Integrity
            </p>
            <h1 className="mt-1 text-2xl font-semibold text-white">
              Logic-document synchronization
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-zinc-400">
              Review logic snapshots, deterministic diffs, affected records, and
              proposed updates before anything becomes official.
            </p>
          </div>
          <Button tone="secondary" onClick={() => void loadUnits()}>
            Refresh
          </Button>
        </header>

        {error ? <InlineError>{error}</InlineError> : null}
        {actionMessage ? (
          <p className="rounded-lg border border-emerald-900/70 bg-emerald-950/30 px-3 py-2 text-sm text-emerald-100">
            {actionMessage}
          </p>
        ) : null}

        <ImportUploadPanel
          apiBase={apiBase}
          units={units}
          selectedUnitId={selectedUnitId}
          selectedModuleId={selectedModuleId}
          onImported={(result) => void handleImportComplete(result)}
        />

        <div className="grid gap-5 xl:grid-cols-[21rem_minmax(0,1fr)]">
          <ProcessUnitList
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

          <section className="flex min-w-0 flex-col gap-5">
            <ModuleHeader
              loading={detailLoading}
              module={selectedModule}
              unit={selectedUnit?.unit ?? null}
              snapshot={latestSnapshot(moduleRecord?.snapshots ?? [])}
              diff={latestDiff}
              reviewItems={reviewItems}
            />

            <div className="grid gap-5 lg:grid-cols-[minmax(0,1.1fr)_minmax(22rem,0.9fr)]">
              <EngineeringRecordsSection
                recordsByType={recordsByType}
                revisionsByRecord={revisionsByRecord}
                reviewItems={reviewItems}
                proposedById={proposedById}
              />
              <ReviewItemsSection
                reviewItems={reviewItems}
                records={records}
                revisionsByRecord={revisionsByRecord}
                proposedById={proposedById}
                diff={latestDiff}
                onApprove={(item) => void decideReview(item, "approve")}
                onReject={(item) => void decideReview(item, "reject")}
              />
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}

function ProcessUnitList({
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
      <CardHeader title="Process units" eyebrow="Plant record" />
      <CardBody className="space-y-3">
        {loading ? <LoadingLine>Loading process units...</LoadingLine> : null}
        {!loading && units.length === 0 ? (
          <EmptyState
            title="No process units yet."
            hint="Create process units through the API to begin the integrity workflow."
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
                    <p className="mt-1 text-xs text-zinc-500">{unit.area || "Unassigned area"}</p>
                  </div>
                  <Badge tone={pending ? "warning" : "success"}>
                    {pending ? "Needs review" : "Up to date"}
                  </Badge>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Stat value={modules.length} label="modules" />
                  <Stat value={pending} label="pending" />
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
  const [sourceMode, setSourceMode] = useState<"existing" | "new">("new");
  const [unitId, setUnitId] = useState(selectedUnitId);
  const [moduleId, setModuleId] = useState(selectedModuleId);
  const [sources, setSources] = useState<ControlImportSource[]>([]);
  const [sourceId, setSourceId] = useState("");
  const [unitName, setUnitName] = useState("Converting Area");
  const [unitArea, setUnitArea] = useState("600B");
  const [moduleName, setModuleName] = useState("Filtrate Separator");
  const [moduleType, setModuleType] = useState("Equipment Module");
  const [sourceName, setSourceName] = useState("Manual XML export");
  const [sourceSystem, setSourceSystem] = useState("xml_export");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportSyncResult | null>(null);

  const selectedUnit = units.find((entry) => entry.unit.id === unitId) ?? null;
  const selectedModule = selectedUnit?.modules.find((module) => module.id === moduleId) ?? null;

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
  }, [moduleId, moduleMode, selectedUnit]);

  useEffect(() => {
    async function loadSources() {
      if (!moduleId || sourceMode !== "existing") {
        setSources([]);
        return;
      }
      try {
        const res = await axios.get<ControlImportSource[]>(`${apiBase}/api/import-sources`, {
          params: { module_id: moduleId },
        });
        setSources(res.data);
        setSourceId((current) => current || res.data[0]?.id || "");
      } catch (err) {
        setError(extractIntelliError(err, "Unable to load import sources"));
      }
    }
    void loadSources();
  }, [apiBase, moduleId, sourceMode]);

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
      if (unitMode === "new") {
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

      let targetSourceId = sourceId;
      if (sourceMode === "new" || !targetSourceId) {
        const sourceRes = await axios.post<ControlImportSource>(`${apiBase}/api/import-sources`, {
          process_unit_id: targetUnitId,
          module_id: targetModuleId,
          name: sourceName,
          source_system: sourceSystem,
          acquisition_mode: "manual_upload",
          connector_hint: null,
          endpoint_url: null,
          export_path: null,
          schedule: null,
          is_enabled: true,
          config_json: {},
        });
        targetSourceId = sourceRes.data.id;
      }

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
        title="Import latest control export"
        eyebrow="Import-to-review demo"
        trailing={<Badge tone="info">DCS / PLC XML, L5X, FHX</Badge>}
      />
      <CardBody>
        <form onSubmit={submitImport} className="grid gap-4 lg:grid-cols-[1fr_1fr_1fr]">
          <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-3">
            <p className="text-sm font-medium text-zinc-100">Process unit</p>
            <div className="mt-3 flex gap-2">
              <ModeButton active={unitMode === "existing"} onClick={() => setUnitMode("existing")}>
                Select
              </ModeButton>
              <ModeButton active={unitMode === "new"} onClick={() => setUnitMode("new")}>
                Create
              </ModeButton>
            </div>
            {unitMode === "existing" ? (
              <label className="mt-3 block text-xs text-zinc-400">
                Unit
                <select
                  aria-label="Process unit"
                  value={unitId}
                  onChange={(event) => setUnitId(event.target.value)}
                  className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
                >
                  {units.map((entry) => (
                    <option key={entry.unit.id} value={entry.unit.id}>
                      {entry.unit.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <div className="mt-3 grid gap-2">
                <TextField label="New unit name" value={unitName} onChange={setUnitName} />
                <TextField label="Area" value={unitArea} onChange={setUnitArea} />
              </div>
            )}
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-3">
            <p className="text-sm font-medium text-zinc-100">Equipment module</p>
            <div className="mt-3 flex gap-2">
              <ModeButton active={moduleMode === "existing"} onClick={() => setModuleMode("existing")}>
                Select
              </ModeButton>
              <ModeButton active={moduleMode === "new"} onClick={() => setModuleMode("new")}>
                Create
              </ModeButton>
            </div>
            {moduleMode === "existing" && selectedUnit?.modules.length ? (
              <label className="mt-3 block text-xs text-zinc-400">
                Module
                <select
                  aria-label="Equipment module"
                  value={moduleId}
                  onChange={(event) => setModuleId(event.target.value)}
                  className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
                >
                  {selectedUnit.modules.map((module) => (
                    <option key={module.id} value={module.id}>
                      {module.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <div className="mt-3 grid gap-2">
                <TextField label="New module name" value={moduleName} onChange={setModuleName} />
                <TextField label="Module type" value={moduleType} onChange={setModuleType} />
              </div>
            )}
          </div>

          <div className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-3">
            <p className="text-sm font-medium text-zinc-100">Import source</p>
            <div className="mt-3 flex gap-2">
              <ModeButton active={sourceMode === "existing"} onClick={() => setSourceMode("existing")}>
                Select
              </ModeButton>
              <ModeButton active={sourceMode === "new"} onClick={() => setSourceMode("new")}>
                Create
              </ModeButton>
            </div>
            {sourceMode === "existing" && sources.length ? (
              <label className="mt-3 block text-xs text-zinc-400">
                Source
                <select
                  aria-label="Import source"
                  value={sourceId}
                  onChange={(event) => setSourceId(event.target.value)}
                  className="mt-1 w-full rounded-lg border border-zinc-800 bg-zinc-950 px-3 py-2 text-sm text-zinc-100"
                >
                  {sources.map((source) => (
                    <option key={source.id} value={source.id}>
                      {source.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <div className="mt-3 grid gap-2">
                <TextField label="New source name" value={sourceName} onChange={setSourceName} />
                <TextField label="Source system" value={sourceSystem} onChange={setSourceSystem} />
              </div>
            )}
          </div>

          <div className="lg:col-span-3">
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
                {submitting ? "Importing..." : "Import and create review"}
              </Button>
              <p className="text-xs text-zinc-500">
                INTELLI creates an immutable snapshot, compares it to the previous snapshot, and
                routes affected records for engineer review.
              </p>
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
  return (
    <div className="mt-5 rounded-xl border border-cyan-400/30 bg-cyan-400/10 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-cyan-100">Import complete</p>
          <p className="mt-1 text-xs leading-5 text-cyan-100/75">
            Imported latest export{moduleName ? ` for ${moduleName}` : ""}. Created immutable
            snapshot {result.snapshot_id ?? result.snapshot?.id ?? "unknown"}.
          </p>
        </div>
        <Badge tone={changed ? "warning" : "success"}>
          {changed ? "Changes found" : "Baseline or no changes"}
        </Badge>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-4">
        <SummaryTile label="Snapshot" value={result.snapshot_id ?? result.snapshot?.id ?? "Created"} />
        <SummaryTile label="Diff" value={result.diff_id ?? result.logic_diff?.id ?? "No previous diff"} />
        <SummaryTile label="Affected records" value={String(result.affected_record_ids.length)} />
        <SummaryTile label="Review items" value={String(result.review_item_ids.length)} />
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

function ModuleHeader({
  loading,
  module,
  unit,
  snapshot,
  diff,
  reviewItems,
}: {
  loading: boolean;
  module: EquipmentModule | null;
  unit: ProcessUnit | null;
  snapshot: LogicSnapshot | null;
  diff: LogicDiff | null;
  reviewItems: ReviewItem[];
}) {
  const pending = reviewItems.filter((item) =>
    ["open", "needs_manual_review"].includes(item.status),
  ).length;
  return (
    <Card>
      <CardHeader
        title={module ? module.name : "Select an equipment module"}
        eyebrow={unit?.name ?? "Module record"}
        trailing={
          <Badge tone={pending ? "warning" : "success"}>
            {pending ? "Needs review" : "Up to date"}
          </Badge>
        }
      />
      <CardBody>
        {loading ? <LoadingLine>Loading module record...</LoadingLine> : null}
        {!module ? (
          <EmptyState title="No module selected." />
        ) : (
          <div className="grid gap-3 md:grid-cols-4">
            <SummaryTile label="Type" value={module.module_type || "Equipment module"} />
            <SummaryTile
              label="Current logic snapshot"
              value={snapshot?.source_filename || "No snapshot"}
            />
            <SummaryTile
              label="Latest diff"
              value={diff ? (diff.changed ? "Changes detected" : "No changes") : "No diff yet"}
            />
            <SummaryTile label="Review items" value={String(pending)} />
          </div>
        )}
        {diff ? (
          <div className="mt-4 rounded-xl border border-zinc-800 bg-zinc-950/50 p-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium text-zinc-100">Latest LogicDiff summary</p>
              <Badge tone={diff.changed ? "warning" : "success"}>
                {diff.changed ? "Needs review" : "Up to date"}
              </Badge>
            </div>
            <p className="mt-2 text-xs leading-5 text-zinc-400">
              {String(diff.summary_payload.summary ?? "No summary available.")}
            </p>
          </div>
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
}: {
  recordsByType: Map<RecordType, EngineeringRecord[]>;
  revisionsByRecord: Record<string, DocumentRevision[]>;
  reviewItems: ReviewItem[];
  proposedById: Record<string, ProposedDocumentUpdate>;
}) {
  return (
    <Card>
      <CardHeader title="Engineering records" eyebrow="Governed documents" />
      <CardBody className="space-y-4">
        {RECORD_ORDER.map((type) => {
          const records = recordsByType.get(type) ?? [];
          return (
            <div key={type} className="rounded-xl border border-zinc-800 bg-zinc-900/30 p-3">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-sm font-medium text-zinc-100">{RECORD_LABELS[type]}</h3>
                <Badge tone={records.length ? "outline" : "neutral"}>{records.length}</Badge>
              </div>
              <div className="mt-3 space-y-2">
                {records.length === 0 ? (
                  <p className="text-xs text-zinc-500">No record linked.</p>
                ) : (
                  records.map((record) => {
                    const revisions = revisionsByRecord[record.id] ?? [];
                    const approved = latestApprovedRevision(revisions);
                    const review = reviewItems.find(
                      (item) => item.engineering_record_id === record.id,
                    );
                    const proposed = review?.proposed_document_update_id
                      ? proposedById[review.proposed_document_update_id]
                      : null;
                    return (
                      <div
                        key={record.id}
                        className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <p className="text-sm text-zinc-100">{record.title}</p>
                          <div className="flex flex-wrap gap-1.5">
                            <Badge tone={statusTone(record.status)}>
                              {fmtStatus(record.status)}
                            </Badge>
                            {review ? <Badge tone="warning">Review required</Badge> : null}
                            {proposed ? <Badge tone="info">Proposed update</Badge> : null}
                          </div>
                        </div>
                        <p className="mt-2 text-xs text-zinc-500">
                          Latest approved revision: {approved?.revision ?? "None"}
                        </p>
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

function ReviewItemsSection({
  reviewItems,
  records,
  revisionsByRecord,
  proposedById,
  diff,
  onApprove,
  onReject,
}: {
  reviewItems: ReviewItem[];
  records: EngineeringRecord[];
  revisionsByRecord: Record<string, DocumentRevision[]>;
  proposedById: Record<string, ProposedDocumentUpdate>;
  diff: LogicDiff | null;
  onApprove: (item: ReviewItem) => void;
  onReject: (item: ReviewItem) => void;
}) {
  return (
    <Card>
      <CardHeader title="Review items" eyebrow="Engineer approval" />
      <CardBody className="space-y-3">
        {reviewItems.length === 0 ? (
          <EmptyState title="No open review items for this module." />
        ) : null}
        {reviewItems.map((item) => {
          const record = records.find((entry) => entry.id === item.engineering_record_id);
          const revisions = record ? revisionsByRecord[record.id] ?? [] : [];
          const approved = latestApprovedRevision(revisions);
          const proposed = item.proposed_document_update_id
            ? proposedById[item.proposed_document_update_id]
            : null;
          return (
            <div key={item.id} className="rounded-xl border border-zinc-800 bg-zinc-900/35 p-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-zinc-100">{item.title}</p>
                  <p className="mt-1 text-xs text-zinc-500">
                    {record ? RECORD_LABELS[record.record_type] : "Unlinked record"}
                  </p>
                </div>
                <Badge tone={statusTone(item.status)}>{fmtStatus(item.status)}</Badge>
              </div>
              <p className="mt-3 text-xs leading-5 text-zinc-400">
                {item.reason || "Review required by latest import."}
              </p>
              <ProposedUpdateView approved={approved} proposed={proposed} diff={diff} />
              <div className="mt-4 flex flex-wrap gap-2">
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
            </div>
          );
        })}
      </CardBody>
    </Card>
  );
}

function ProposedUpdateView({
  approved,
  proposed,
  diff,
}: {
  approved: DocumentRevision | null;
  proposed: ProposedDocumentUpdate | null;
  diff: LogicDiff | null;
}) {
  return (
    <div className="mt-4 grid gap-3">
      <div>
        <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
          Current approved revision
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
            Proposed update
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
        ) : (
          <p className="rounded-lg border border-zinc-800 bg-zinc-950/50 p-3 text-xs text-zinc-500">
            No proposed update linked.
          </p>
        )}
      </div>
      {diff ? (
        <div>
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
            Diff payload
          </p>
          <Code>{jsonBlock(diff.summary_payload)}</Code>
        </div>
      ) : null}
    </div>
  );
}
