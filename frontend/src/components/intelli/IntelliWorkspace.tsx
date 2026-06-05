"use client";

import axios from "axios";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  extractIntelliError,
  useIntelliProject,
} from "@/context/IntelliProjectContext";
import type { ControlProject } from "@/types/intelli";
import type {
  AskAnswerStyle,
  LLMAssistResponse,
  NormalizedControlObjectSummary,
  NormalizedSummaryResponse,
  TraceResponse,
} from "@/types/reasoning";

import AnswerView from "./AnswerView";
import Sidebar from "./Sidebar";
import { Badge, Stat } from "./ui";

const IS_DEV = process.env.NODE_ENV === "development";

/** Page size for object finder (server limit/offset). */
const OBJECT_FINDER_PAGE_SIZE = 100;

function num(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

/** Normalize FastAPI / proxy JSON (snake_case or camelCase). */
function parseControlObjectsPayload(data: unknown): {
  objects: NormalizedControlObjectSummary[];
  totalMatching: number;
  projectTotal: number;
} {
  const d =
    data && typeof data === "object"
      ? (data as Record<string, unknown>)
      : {};
  const raw = d.control_objects ?? d.controlObjects;
  const objects = Array.isArray(raw)
    ? (raw as NormalizedControlObjectSummary[])
    : [];
  const totalMatching = num(
    d.total_control_object_count ?? d.totalControlObjectCount,
    objects.length,
  );
  const projectTotal = num(
    d.project_control_object_count ??
      d.projectControlObjectCount ??
      d.control_object_count ??
      d.controlObjectCount,
    totalMatching,
  );
  return { objects, totalMatching, projectTotal };
}

interface UploadResponse {
  project_id: string;
  connector: string;
  project: ControlProject;
  graph: Record<string, number>;
}

type TraceVersion = "v1" | "v2";

/**
 * Main engineering workspace: upload, object search, trace, ask,
 * runtime evaluation. Expects ``IntelliProjectProvider`` above in the tree.
 */
export default function IntelliWorkspace() {
  const { apiBase, project, projectId, setUploadedProject, clearProject } =
    useIntelliProject();

  const [file, setFile] = useState<File | null>(null);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [summary, setSummary] = useState<NormalizedSummaryResponse | null>(
    null,
  );
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [objectList, setObjectList] = useState<NormalizedControlObjectSummary[]>(
    [],
  );
  const [objectListTotal, setObjectListTotal] = useState(0);
  const [objectListProjectTotal, setObjectListProjectTotal] = useState(0);
  const [objectListFetchSucceeded, setObjectListFetchSucceeded] =
    useState(false);
  const [objectListLoading, setObjectListLoading] = useState(false);
  const [objectListError, setObjectListError] = useState<string | null>(null);
  const [objectTypeFilter, setObjectTypeFilter] = useState("");
  const [objectListOffset, setObjectListOffset] = useState(0);

  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");

  const [selectedObjectId, setSelectedObjectId] = useState("");

  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [traceVersion, setTraceVersion] = useState<TraceVersion | null>(null);
  const [traceLoading, setTraceLoading] = useState<TraceVersion | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);

  const [question, setQuestion] = useState("");
  const [answerStyle, setAnswerStyle] =
    useState<AskAnswerStyle>("controls_engineer");
  const [askLoading, setAskLoading] = useState(false);
  const [askedQuestion, setAskedQuestion] = useState<string | null>(null);
  const [llmAssist, setLlmAssist] = useState<LLMAssistResponse | null>(null);

  const [runtimeSnapshotText, setRuntimeSnapshotText] = useState("{}");
  const [runtimeEvaluating, setRuntimeEvaluating] = useState(false);
  const [runtimeEvalError, setRuntimeEvalError] = useState<string | null>(null);

  const lastObjectFinderFilterSig = useRef<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedSearch(search.trim()), 320);
    return () => window.clearTimeout(t);
  }, [search]);

  const projectApiKey = useMemo(
    () => (projectId || project?.file_hash || "").trim(),
    [projectId, project?.file_hash],
  );

  const hasActiveObjectFilter = useMemo(
    () =>
      Boolean(debouncedSearch) ||
      Boolean(
        objectTypeFilter.trim() &&
          objectTypeFilter.trim().toLowerCase() !== "all",
      ),
    [debouncedSearch, objectTypeFilter],
  );

  const loadLightSummary = useCallback(async () => {
    if (!projectApiKey) return;
    setSummaryError(null);
    setSummaryLoading(true);
    try {
      const res = await axios.get<NormalizedSummaryResponse>(
        `${apiBase.replace(/\/$/, "")}/api/normalized-summary`,
        {
          params: { limit: 1, offset: 0, rel_limit: 1, rel_offset: 0 },
        },
      );
      setSummary(res.data);
    } catch (err) {
      setSummary(null);
      setSummaryError(extractIntelliError(err, "Could not load summary"));
    } finally {
      setSummaryLoading(false);
    }
  }, [apiBase, projectApiKey]);

  const fetchObjectPage = useCallback(async () => {
    if (!projectApiKey) return;

    const filterSig = `${projectApiKey}\x00${debouncedSearch}\x00${objectTypeFilter}`;
    const filterChanged = lastObjectFinderFilterSig.current !== filterSig;

    if (filterChanged && objectListOffset !== 0) {
      lastObjectFinderFilterSig.current = filterSig;
      setObjectListOffset(0);
      return;
    }

    if (filterChanged) {
      lastObjectFinderFilterSig.current = filterSig;
    }

    const offsetForRequest = filterChanged ? 0 : objectListOffset;

    setObjectListError(null);
    setObjectListFetchSucceeded(false);
    setObjectListLoading(true);
    const params = new URLSearchParams();
    params.set("limit", String(OBJECT_FINDER_PAGE_SIZE));
    params.set("offset", String(offsetForRequest));
    params.set("rel_limit", "1");
    params.set("rel_offset", "0");
    const q = debouncedSearch.trim();
    if (q) params.set("search", q);
    const otRaw = objectTypeFilter.trim();
    const otLower = otRaw.toLowerCase();
    if (otRaw && otLower !== "all") params.set("object_type", otRaw);
    // Use normalized-summary for the object list so it cannot 404 while the
    // summary chips work (same handler, filters, and counts as control-objects).
    const url = `${apiBase.replace(/\/$/, "")}/api/normalized-summary?${params.toString()}`;
    try {
      const res = await axios.get(url);
      const parsed = parseControlObjectsPayload(res.data);
      setObjectList(parsed.objects);
      setObjectListTotal(parsed.totalMatching);
      setObjectListProjectTotal(parsed.projectTotal);
      setObjectListFetchSucceeded(true);
      if (IS_DEV) {
        console.info("[INTELLI] object-finder (normalized-summary)", {
          url,
          offset: offsetForRequest,
          totalMatching: parsed.totalMatching,
          projectTotal: parsed.projectTotal,
          returned: parsed.objects.length,
          sampleIds: parsed.objects.slice(0, 3).map((o) => o.id),
        });
      }
    } catch (err) {
      setObjectList([]);
      setObjectListTotal(0);
      setObjectListProjectTotal(0);
      setObjectListFetchSucceeded(false);
      setObjectListError(extractIntelliError(err, "Object search failed"));
      if (IS_DEV) {
        console.warn("[INTELLI] object-finder error", url, err);
      }
    } finally {
      setObjectListLoading(false);
    }
  }, [apiBase, projectApiKey, debouncedSearch, objectTypeFilter, objectListOffset]);

  useEffect(() => {
    if (!projectApiKey) return;
    const id = window.setTimeout(() => {
      void loadLightSummary();
    }, 0);
    return () => window.clearTimeout(id);
  }, [projectApiKey, loadLightSummary]);

  useEffect(() => {
    if (!projectApiKey) return;
    const id = window.setTimeout(() => {
      void fetchObjectPage();
    }, 0);
    return () => window.clearTimeout(id);
  }, [projectApiKey, fetchObjectPage]);

  const selectedObject = useMemo((): NormalizedControlObjectSummary | null => {
    if (!selectedObjectId) return null;
    return (
      objectList.find((o) => o.id === selectedObjectId) ?? {
        id: selectedObjectId,
        name: selectedObjectId.split("/").pop() ?? selectedObjectId,
        object_type: "unknown",
        source_location: null,
      }
    );
  }, [objectList, selectedObjectId]);

  const objectListHasPrev = objectListOffset > 0;
  const objectListHasNext =
    objectListFetchSucceeded &&
    objectListOffset + objectList.length < objectListTotal;

  const projectStats = useMemo(() => {
    if (!project) {
      return {
        controllers: 0,
        programs: 0,
        routines: 0,
        objects: 0,
        relationships: 0,
      };
    }
    const programs = project.controllers.reduce(
      (acc, controller) => acc + controller.programs.length,
      0,
    );
    const routines = project.controllers.reduce(
      (acc, controller) =>
        acc +
        controller.programs.reduce(
          (programAcc, program) => programAcc + program.routines.length,
          0,
        ),
      0,
    );
    return {
      controllers: project.controllers.length,
      programs,
      routines,
      objects: summary?.control_object_count ?? objectListProjectTotal,
      relationships: summary?.relationship_count ?? 0,
    };
  }, [project, summary, objectListProjectTotal]);

  async function uploadFile() {
    setUploadError(null);
    if (!file) {
      setUploadError("Choose an L5X file first.");
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    try {
      setUploadLoading(true);
      lastObjectFinderFilterSig.current = null;
      setSummary(null);
      setSummaryError(null);
      setObjectListOffset(0);
      setSelectedObjectId("");
      setTrace(null);
      setTraceVersion(null);
      setTraceError(null);
      setAskedQuestion(null);
      setLlmAssist(null);
      setQuestion("");
      setRuntimeSnapshotText("{}");
      setRuntimeEvaluating(false);
      setRuntimeEvalError(null);
      const res = await axios.post<UploadResponse>(
        `${apiBase.replace(/\/$/, "")}/upload`,
        formData,
      );
      const uploadKey =
        (res.data.project_id && res.data.project_id.trim()) ||
        (res.data.project.file_hash && res.data.project.file_hash.trim()) ||
        "";
      setUploadedProject(res.data.project, uploadKey);
    } catch (err) {
      setUploadError(extractIntelliError(err, "Upload failed"));
    } finally {
      setUploadLoading(false);
    }
  }

  const refreshSummary = useCallback(async () => {
    if (!projectApiKey) {
      setSummaryError("No active project.");
      return;
    }
    await loadLightSummary();
    await fetchObjectPage();
  }, [projectApiKey, loadLightSummary, fetchObjectPage]);

  async function runTrace(version: TraceVersion) {
    if (!project) {
      setTraceError("Upload a project first.");
      return;
    }
    const id = selectedObjectId.trim();
    if (!id) {
      setTraceError("Pick or enter a target object id first.");
      return;
    }
    setTraceError(null);
    setRuntimeEvalError(null);
    setTraceLoading(version);
    setAskedQuestion(null);
    setLlmAssist(null);
    try {
      const endpoint = version === "v2" ? "/api/trace-v2" : "/api/trace-v1";
      const res = await axios.post<TraceResponse>(
        `${apiBase.replace(/\/$/, "")}${endpoint}`,
        {
          target_object_id: id,
        },
      );
      setTrace(res.data);
      setTraceVersion(version);
    } catch (err) {
      setTrace(null);
      setTraceVersion(null);
      setTraceError(extractIntelliError(err, "Could not run trace"));
    } finally {
      setTraceLoading(null);
    }
  }

  function parseRuntimeSnapshotJson(): Record<string, unknown> | null {
    const trimmed = runtimeSnapshotText.trim();
    if (!trimmed || trimmed === "{}") return null;
    try {
      return JSON.parse(trimmed) as Record<string, unknown>;
    } catch {
      return null;
    }
  }

  async function ask() {
    if (!project) {
      setTraceError("Upload a project first.");
      return;
    }
    const q = question.trim();
    if (!q) return;
    setTraceError(null);
    setRuntimeEvalError(null);
    setAskLoading(true);
    setAskedQuestion(q);
    setLlmAssist(null);
    const runtimeSnapshot = parseRuntimeSnapshotJson();
    try {
      const res = await axios.post<LLMAssistResponse>(
        `${apiBase.replace(/\/$/, "")}/api/ask-v3`,
        {
          question: q,
          runtime_snapshot: runtimeSnapshot ?? undefined,
          current_selected_object: selectedObjectId || undefined,
          last_discussed_state:
            typeof trace?.platform_specific?.["last_discussed_state"] === "string"
              ? trace.platform_specific["last_discussed_state"]
              : undefined,
          prior_runtime_snapshot: runtimeSnapshot ?? undefined,
          prior_sequence_discussion:
            trace?.platform_specific?.["sequence_semantics"] ?? undefined,
          answer_style: answerStyle,
        },
      );
      setLlmAssist(res.data);
      setTrace(res.data.deterministic_result);
      setTraceVersion("v2");
      const tid = res.data.target_object_id;
      if (typeof tid === "string" && tid.length > 0) {
        setSelectedObjectId(tid);
      }
    } catch {
      try {
        const res = await axios.post<TraceResponse>(
          `${apiBase.replace(/\/$/, "")}/api/ask-v2`,
          {
            question: q,
            runtime_snapshot: runtimeSnapshot ?? undefined,
          },
        );
        setLlmAssist(null);
        setTrace(res.data);
        setTraceVersion("v2");
        const detected = res.data.platform_specific?.["detected_target_object_id"];
        if (typeof detected === "string" && detected.length > 0) {
          setSelectedObjectId(detected);
        }
      } catch {
        try {
          const res = await axios.post<TraceResponse>(
            `${apiBase.replace(/\/$/, "")}/api/ask-v1`,
            {
              question: q,
            },
          );
          setLlmAssist(null);
          setTrace(res.data);
          setTraceVersion("v2");
          const detected = res.data.platform_specific?.["detected_target_object_id"];
          if (typeof detected === "string" && detected.length > 0) {
            setSelectedObjectId(detected);
          }
        } catch (err) {
          setTrace(null);
          setTraceVersion(null);
          setLlmAssist(null);
          setTraceError(extractIntelliError(err, "Could not route question"));
        }
      }
    } finally {
      setAskLoading(false);
    }
  }

  const evaluateRuntimeV2 = useCallback(
    async (runtimeSnapshot: Record<string, unknown>) => {
      if (!project) {
        setRuntimeEvalError("Upload a project first.");
        return;
      }
      const id = selectedObjectId.trim();
      if (!id) {
        setRuntimeEvalError("No object selected.");
        return;
      }
      setRuntimeEvalError(null);
      setRuntimeEvaluating(true);
      try {
        const res = await axios.post<TraceResponse>(
          `${apiBase.replace(/\/$/, "")}/api/evaluate-runtime-v2`,
          {
            target_object_id: id,
            runtime_snapshot: runtimeSnapshot,
          },
        );
        setTrace(res.data);
        setTraceVersion("v2");
      } catch (err) {
        setRuntimeEvalError(
          extractIntelliError(err, "Runtime evaluation failed."),
        );
      } finally {
        setRuntimeEvaluating(false);
      }
    },
    [apiBase, project, selectedObjectId],
  );

  function resetWorkspaceUpload() {
    setFile(null);
    clearProject();
    lastObjectFinderFilterSig.current = null;
    setUploadError(null);
    setSummary(null);
    setSummaryError(null);
    setObjectList([]);
    setObjectListTotal(0);
    setObjectListProjectTotal(0);
    setObjectListFetchSucceeded(false);
    setObjectListError(null);
    setSelectedObjectId("");
    setSearch("");
    setObjectTypeFilter("");
    setObjectListOffset(0);
    setTrace(null);
    setTraceVersion(null);
    setTraceError(null);
    setAskedQuestion(null);
    setLlmAssist(null);
    setQuestion("");
    setAnswerStyle("controls_engineer");
    setRuntimeSnapshotText("{}");
    setRuntimeEvaluating(false);
    setRuntimeEvalError(null);
  }

  if (!project) {
    return (
      <WorkspaceNoProject
        file={file}
        onFileChange={setFile}
        onUpload={() => void uploadFile()}
        uploading={uploadLoading}
        uploadError={uploadError}
      />
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#050914] text-zinc-100">
      <header className="shrink-0 border-b border-white/10 bg-zinc-950/80 px-5 py-4 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="info" uppercase>
                reasoning workspace
              </Badge>
              <Badge tone={summaryError || objectListError ? "warning" : "success"} uppercase>
                {summaryError || objectListError ? "review graph" : "graph online"}
              </Badge>
              {traceVersion ? (
                <Badge tone="neutral" uppercase>
                  trace {traceVersion}
                </Badge>
              ) : null}
            </div>
            <h1 className="mt-2 truncate text-2xl font-semibold tracking-[-0.03em] text-white">
              {project.project_name || "Imported control project"}
            </h1>
            <p className="mt-1 max-w-3xl truncate text-sm text-zinc-500">
              Evidence-backed object tracing, runtime diagnosis, and controls
              question routing.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/"
              className="inline-flex rounded-xl border border-zinc-800 bg-zinc-900/70 px-3 py-2 text-sm text-zinc-300 transition hover:border-zinc-700 hover:text-white xl:hidden"
            >
              Home
            </Link>
            <button
              type="button"
              onClick={() => void refreshSummary()}
              disabled={summaryLoading || objectListLoading}
              className="rounded-xl border border-zinc-800 bg-zinc-900/70 px-3 py-2 text-sm text-zinc-300 transition hover:border-zinc-700 hover:text-white disabled:opacity-40"
            >
              {summaryLoading || objectListLoading ? "Refreshing..." : "Refresh graph"}
            </button>
            <button
              type="button"
              onClick={resetWorkspaceUpload}
              className="rounded-xl border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-sm font-medium text-cyan-100 transition hover:bg-cyan-400/15"
            >
              Switch project
            </button>
          </div>
        </div>
        <ProjectStatsBar
          stats={projectStats}
          selectedObjectName={
            selectedObject?.name ??
            (selectedObjectId ? selectedObjectId.split("/").pop() ?? selectedObjectId : "")
          }
        />
      </header>
      <div className="flex min-h-0 flex-1">
        <Sidebar
          project={project}
          uploadFile={file}
          onFileChange={setFile}
          onUploadSubmit={() => void uploadFile()}
          onResetUpload={resetWorkspaceUpload}
          uploadLoading={uploadLoading}
          uploadError={uploadError}
          summary={summary}
          summaryLoading={summaryLoading}
          summaryError={summaryError}
          onLoadSummary={() => void refreshSummary()}
          objectList={objectList}
          objectListTotal={objectListTotal}
          objectListLoading={objectListLoading}
          objectListError={objectListError}
          objectListProjectTotal={objectListProjectTotal}
          objectListFetchSucceeded={objectListFetchSucceeded}
          hasActiveObjectFilter={hasActiveObjectFilter}
          objectTypeFilter={objectTypeFilter}
          onObjectTypeFilter={setObjectTypeFilter}
          objectListOffset={objectListOffset}
          objectListHasPrev={objectListHasPrev}
          objectListHasNext={objectListHasNext}
          onObjectListPrev={() =>
            setObjectListOffset((o) => Math.max(0, o - OBJECT_FINDER_PAGE_SIZE))
          }
          onObjectListNext={() =>
            setObjectListOffset((o) => o + OBJECT_FINDER_PAGE_SIZE)
          }
          search={search}
          onSearch={setSearch}
          selectedObjectId={selectedObjectId}
          onSelectObject={(id) => {
            setSelectedObjectId(id);
            setTraceError(null);
          }}
          question={question}
          onQuestionChange={setQuestion}
          answerStyle={answerStyle}
          onAnswerStyleChange={setAnswerStyle}
          askLoading={askLoading}
          onAsk={() => void ask()}
        />
        <AnswerView
          selectedObject={selectedObject}
          selectedObjectId={selectedObjectId}
          trace={trace}
          traceVersion={traceVersion}
          traceLoading={traceLoading}
          traceError={traceError}
          askedQuestion={askedQuestion}
          onRunTrace={runTrace}
          runtimeSnapshotText={runtimeSnapshotText}
          onRuntimeSnapshotTextChange={(t) => {
            setRuntimeSnapshotText(t);
            setRuntimeEvalError(null);
          }}
          onEvaluateRuntimeV2={evaluateRuntimeV2}
          runtimeEvaluating={runtimeEvaluating}
          runtimeEvalError={runtimeEvalError}
          llmAssist={llmAssist}
        />
      </div>
    </div>
  );
}

function WorkspaceNoProject({
  file,
  onFileChange,
  onUpload,
  uploading,
  uploadError,
}: {
  file: File | null;
  onFileChange: (file: File | null) => void;
  onUpload: () => void;
  uploading: boolean;
  uploadError: string | null;
}) {
  return (
    <div className="relative grid min-h-screen place-items-center overflow-hidden bg-[radial-gradient(circle_at_top,#10223a_0,#050914_55%,#020617_100%)] px-6 py-12 text-center">
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(148,163,184,0.035)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,0.035)_1px,transparent_1px)] bg-[size:42px_42px]" />
      <div className="relative w-full max-w-3xl rounded-[2rem] border border-white/10 bg-zinc-950/75 p-6 shadow-2xl shadow-black/40 backdrop-blur">
        <div className="mx-auto max-w-2xl">
          <Badge tone="info" uppercase>
            workspace standby
          </Badge>
          <h1 className="mt-4 text-4xl font-semibold tracking-[-0.04em] text-white">
            Load a control project to start reasoning.
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-zinc-400">
            Upload a supported export and INTELLI will move into object search,
            trace, ask, and runtime diagnosis mode. The backend keeps the most
            recent project in memory during development.
          </p>
        </div>

        <div className="mx-auto mt-8 max-w-xl rounded-2xl border border-zinc-800/80 bg-zinc-900/45 p-4 text-left">
          <label className="block cursor-pointer rounded-2xl border border-dashed border-cyan-400/30 bg-cyan-400/[0.04] px-4 py-7 text-center transition hover:border-cyan-300/60">
            <input
              type="file"
              accept=".l5x,.L5X,.xml,.XML,.fhx,.FHX,.scl,.SCL,.txt,.csv,.cl,.hwl,.hwh,.hsc,.epr,application/xml,text/xml,text/plain"
              onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
              className="sr-only"
            />
            <p className="text-sm font-medium text-zinc-100">
              {file ? file.name : "Choose a PLC or DCS export"}
            </p>
            <p className="mt-2 text-xs text-zinc-500">
              Rockwell L5X, Siemens XML, DeltaV FHX, Honeywell text/XML.
            </p>
          </label>
          <button
            type="button"
            onClick={onUpload}
            disabled={uploading || !file}
            className="mt-4 w-full rounded-xl bg-cyan-300 py-3 text-sm font-semibold text-cyan-950 transition hover:bg-cyan-200 disabled:opacity-40"
          >
            {uploading ? "Uploading..." : "Analyze project"}
          </button>
          {uploadError ? (
            <p className="mt-3 rounded-lg border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-sm text-rose-100">
              {uploadError}
            </p>
          ) : null}
        </div>

        <Link
          href="/"
          className="mt-6 inline-flex text-sm text-zinc-400 underline-offset-4 hover:text-zinc-200 hover:underline"
        >
          Back to product home
        </Link>
      </div>
    </div>
  );
}

function ProjectStatsBar({
  stats,
  selectedObjectName,
}: {
  stats: {
    controllers: number;
    programs: number;
    routines: number;
    objects: number;
    relationships: number;
  };
  selectedObjectName: string;
}) {
  return (
    <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <div className="flex flex-wrap gap-2">
        <Stat value={stats.controllers} label="controllers" />
        <Stat value={stats.programs} label="programs" />
        <Stat value={stats.routines} label="routines" />
        <Stat value={stats.objects} label="objects" />
        <Stat value={stats.relationships} label="relationships" />
      </div>
      <div className="min-w-0 rounded-xl border border-zinc-800/80 bg-zinc-900/55 px-3 py-2 text-xs">
        <span className="text-zinc-500">Selected target: </span>
        <span className="text-zinc-200">
          {selectedObjectName || "none yet"}
        </span>
      </div>
    </div>
  );
}
