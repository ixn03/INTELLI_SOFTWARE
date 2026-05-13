"use client";

import axios from "axios";
import { useCallback, useMemo, useState } from "react";

import type { ControlProject } from "@/types/intelli";
import type {
  NormalizedSummaryResponse,
  TraceResponse,
} from "@/types/reasoning";

import AnswerView from "./AnswerView";
import Sidebar from "./Sidebar";

/**
 * Top-level layout for the INTELLI app.
 *
 * IntelliShell owns *all* application state and side effects:
 *
 *   - Upload lifecycle (file pick, POST /upload, reset).
 *   - Normalized summary lifecycle (lazy GET /api/normalized-summary
 *     on first request; cached for the session).
 *   - Object selection (synced with the sidebar list AND the
 *     /api/ask-v1 result's detected target).
 *   - Trace lifecycle (POST /api/trace-v1, /api/trace-v2,
 *     /api/ask-v1, /api/evaluate-runtime-v2).
 *
 * The :file:`Sidebar.tsx` and :file:`AnswerView.tsx` modules are
 * pure presentation: they receive props and emit events back through
 * the callbacks plumbed from here. This keeps the shell file the
 * single source of truth for "what is currently going on".
 */

const API_BASE =
  process.env.NEXT_PUBLIC_INTELLI_API_BASE ?? "http://127.0.0.1:8000";

interface UploadResponse {
  project_id: string;
  connector: string;
  project: ControlProject;
  graph: Record<string, number>;
}

type TraceVersion = "v1" | "v2";

export default function IntelliShell() {
  // --- Upload --------------------------------------------------------
  const [file, setFile] = useState<File | null>(null);
  const [project, setProject] = useState<ControlProject | null>(null);
  const [projectId, setProjectId] = useState("");
  const [uploadLoading, setUploadLoading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  // --- Normalized summary --------------------------------------------
  const [summary, setSummary] = useState<NormalizedSummaryResponse | null>(
    null,
  );
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  // --- Object selection ----------------------------------------------
  const [search, setSearch] = useState("");
  const [selectedObjectId, setSelectedObjectId] = useState("");

  // --- Trace ---------------------------------------------------------
  const [trace, setTrace] = useState<TraceResponse | null>(null);
  const [traceVersion, setTraceVersion] = useState<TraceVersion | null>(null);
  const [traceLoading, setTraceLoading] =
    useState<TraceVersion | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);

  // --- Ask INTELLI ----------------------------------------------------
  const [question, setQuestion] = useState("");
  const [askLoading, setAskLoading] = useState(false);
  const [askedQuestion, setAskedQuestion] = useState<string | null>(null);

  // --- Runtime evaluation v2 -------------------------------------------
  const [runtimeSnapshotText, setRuntimeSnapshotText] = useState("{}");
  const [runtimeEvaluating, setRuntimeEvaluating] = useState(false);
  const [runtimeEvalError, setRuntimeEvalError] = useState<string | null>(
    null,
  );

  const selectedObject = useMemo(() => {
    if (!summary || !selectedObjectId) return null;
    return (
      summary.control_objects.find((o) => o.id === selectedObjectId) ?? null
    );
  }, [summary, selectedObjectId]);

  // ---------------------------------------------------------------------
  // Upload
  // ---------------------------------------------------------------------

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
      // Reset everything on a new upload.
      setProject(null);
      setProjectId("");
      setSummary(null);
      setSummaryError(null);
      setSelectedObjectId("");
      setTrace(null);
      setTraceVersion(null);
      setTraceError(null);
      setAskedQuestion(null);
      setQuestion("");
      setRuntimeSnapshotText("{}");
      setRuntimeEvaluating(false);
      setRuntimeEvalError(null);

      const res = await axios.post<UploadResponse>(
        `${API_BASE}/upload`,
        formData,
      );
      setProject(res.data.project);
      setProjectId(res.data.project_id);
      // Auto-fetch the normalized summary so the object finder is
      // immediately usable without an extra click. Failures here are
      // surfaced via summaryError; they don't block the upload.
      void loadSummaryFor(res.data.project_id);
    } catch (err) {
      setUploadError(extractError(err, "Upload failed"));
    } finally {
      setUploadLoading(false);
    }
  }

  async function loadSummaryFor(activeProjectId: string): Promise<void> {
    if (!activeProjectId) return;
    setSummaryError(null);
    setSummaryLoading(true);
    try {
      const res = await axios.get<NormalizedSummaryResponse>(
        `${API_BASE}/api/normalized-summary`,
      );
      setSummary(res.data);
    } catch (err) {
      setSummary(null);
      setSummaryError(
        extractError(err, "Could not load normalized summary"),
      );
    } finally {
      setSummaryLoading(false);
    }
  }

  function resetUpload() {
    setFile(null);
    setProject(null);
    setProjectId("");
    setUploadError(null);
    setSummary(null);
    setSummaryError(null);
    setSelectedObjectId("");
    setSearch("");
    setTrace(null);
    setTraceVersion(null);
    setTraceError(null);
    setAskedQuestion(null);
    setQuestion("");
    setRuntimeSnapshotText("{}");
    setRuntimeEvaluating(false);
    setRuntimeEvalError(null);
  }

  // ---------------------------------------------------------------------
  // Normalized summary -- auto-loaded after upload, refreshable via
  // the sidebar's "Load objects" button.
  // ---------------------------------------------------------------------

  const refreshSummary = useCallback(async () => {
    if (!projectId) {
      setSummaryError("Upload a project first.");
      return;
    }
    await loadSummaryFor(projectId);
  }, [projectId]);

  // ---------------------------------------------------------------------
  // Trace v1 / v2
  // ---------------------------------------------------------------------

  async function runTrace(version: TraceVersion) {
    if (!projectId) {
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
    try {
      const endpoint = version === "v2" ? "/api/trace-v2" : "/api/trace-v1";
      const res = await axios.post<TraceResponse>(`${API_BASE}${endpoint}`, {
        target_object_id: id,
      });
      setTrace(res.data);
      setTraceVersion(version);
    } catch (err) {
      setTrace(null);
      setTraceVersion(null);
      setTraceError(extractError(err, "Could not run trace"));
    } finally {
      setTraceLoading(null);
    }
  }

  // ---------------------------------------------------------------------
  // Ask INTELLI -- routes a free-text question through /api/ask-v1.
  // ---------------------------------------------------------------------

  async function ask() {
    if (!projectId) {
      setTraceError("Upload a project first.");
      return;
    }
    const q = question.trim();
    if (!q) return;
    setTraceError(null);
    setRuntimeEvalError(null);
    setAskLoading(true);
    setAskedQuestion(q);
    try {
      const res = await axios.post<TraceResponse>(`${API_BASE}/api/ask-v1`, {
        question: q,
      });
      setTrace(res.data);
      setTraceVersion("v2");
      const detected = res.data.platform_specific?.["detected_target_object_id"];
      if (typeof detected === "string" && detected.length > 0) {
        setSelectedObjectId(detected);
      }
    } catch (err) {
      setTrace(null);
      setTraceVersion(null);
      setTraceError(extractError(err, "Could not route question"));
    } finally {
      setAskLoading(false);
    }
  }

  const evaluateRuntimeV2 = useCallback(
    async (runtimeSnapshot: Record<string, unknown>) => {
      if (!projectId) {
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
          `${API_BASE}/api/evaluate-runtime-v2`,
          {
            target_object_id: id,
            runtime_snapshot: runtimeSnapshot,
          },
        );
        setTrace(res.data);
        setTraceVersion("v2");
      } catch (err) {
        setRuntimeEvalError(
          extractError(err, "Runtime evaluation failed."),
        );
      } finally {
        setRuntimeEvaluating(false);
      }
    },
    [projectId, selectedObjectId],
  );

  // ---------------------------------------------------------------------
  // Layout: landing screen if there's no project, app shell otherwise.
  // ---------------------------------------------------------------------

  if (!project) {
    return (
      <LandingScreen
        file={file}
        onFileChange={setFile}
        onUpload={uploadFile}
        uploadLoading={uploadLoading}
        uploadError={uploadError}
      />
    );
  }

  return (
    <div className="flex h-screen min-h-screen flex-col bg-[var(--background)] text-[var(--foreground)]">
      <TopHeader
        project={project}
        onResetUpload={resetUpload}
      />
      <div className="flex min-h-0 flex-1">
        <Sidebar
          project={project}
          uploadFile={file}
          onFileChange={setFile}
          onUploadSubmit={uploadFile}
          onResetUpload={resetUpload}
          uploadLoading={uploadLoading}
          uploadError={uploadError}
          summary={summary}
          summaryLoading={summaryLoading}
          summaryError={summaryError}
          onLoadSummary={() => void refreshSummary()}
          search={search}
          onSearch={setSearch}
          selectedObjectId={selectedObjectId}
          onSelectObject={(id) => {
            setSelectedObjectId(id);
            setTraceError(null);
          }}
          question={question}
          onQuestionChange={setQuestion}
          askLoading={askLoading}
          onAsk={ask}
        />
        <AnswerView
          selectedObject={selectedObject}
          selectedObjectId={selectedObjectId}
          summary={summary}
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
        />
      </div>
    </div>
  );
}

// ===========================================================================
// Top header -- slim brand + project status row.
// ===========================================================================

function TopHeader({
  project,
  onResetUpload,
}: {
  project: ControlProject;
  onResetUpload: () => void;
}) {
  return (
    <header className="flex shrink-0 items-center justify-between gap-4 border-b border-[var(--border)] bg-[var(--surface)]/95 px-6 py-3 backdrop-blur">
      <div className="flex items-center gap-3">
        <BrandMark />
        <div>
          <p className="text-sm font-medium tracking-tight text-slate-50">
            INTELLI
          </p>
          <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">
            Controls Logic Intelligence
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <div className="hidden text-right sm:block">
          <p className="text-xs uppercase tracking-[0.18em] text-slate-500">
            Project
          </p>
          <p className="truncate text-sm text-slate-200">
            {project.project_name || "Imported project"}
          </p>
        </div>
        <button
          type="button"
          onClick={onResetUpload}
          className="rounded-lg border border-sky-500/40 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-200 transition hover:border-sky-400/60 hover:bg-sky-500/20"
        >
          Switch project
        </button>
      </div>
    </header>
  );
}

function BrandMark() {
  // A discreet square mark instead of a full logo. Keeps the chrome
  // industrial rather than playful.
  return (
    <span
      aria-hidden
      className="grid h-9 w-9 place-items-center rounded-lg border border-sky-500/30 bg-[var(--surface-elevated)] shadow-[0_0_20px_-8px_rgba(56,189,248,0.45)]"
    >
      <span className="font-mono text-sm tracking-tight text-sky-200">
        I.
      </span>
    </span>
  );
}

// ===========================================================================
// Landing screen -- shown until the first L5X is uploaded.
// ===========================================================================

function LandingScreen({
  file,
  onFileChange,
  onUpload,
  uploadLoading,
  uploadError,
}: {
  file: File | null;
  onFileChange: (file: File | null) => void;
  onUpload: () => void;
  uploadLoading: boolean;
  uploadError: string | null;
}) {
  return (
    <main className="grid min-h-screen place-items-center bg-[var(--background)] text-[var(--foreground)]">
      <div className="w-full max-w-xl px-6 py-10">
        <div className="mb-10">
          <p className="text-[11px] uppercase tracking-[0.22em] text-slate-500">
            INTELLI
          </p>
          <h1 className="mt-2 text-4xl font-semibold tracking-tight text-slate-50">
            Controls Logic Intelligence
          </h1>
          <p className="mt-3 max-w-md text-sm leading-relaxed text-slate-400">
            Upload a Rockwell L5X export to begin tracing logic, asking
            questions, and reviewing deterministic answers.
          </p>
        </div>

        <div className="rounded-2xl border border-[var(--border)] bg-[var(--surface)]/80 p-6 shadow-xl shadow-black/20">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">
            Step 1
          </p>
          <h2 className="mt-1 text-base font-medium text-slate-50">
            Choose your L5X file
          </h2>
          <label className="mt-4 block cursor-pointer rounded-xl border border-dashed border-slate-600/80 bg-[var(--surface-elevated)]/50 px-4 py-8 text-center transition hover:border-sky-500/40 hover:bg-[var(--surface-elevated)]/80">
            <input
              type="file"
              accept=".l5x,.L5X,application/xml,text/xml"
              onChange={(e) =>
                onFileChange(e.target.files?.[0] ?? null)
              }
              className="sr-only"
            />
            <p className="text-sm text-slate-200">
              {file ? file.name : "Click to select an L5X export"}
            </p>
            <p className="mt-1 text-[11px] text-slate-500">
              .l5x · Rockwell Logix Designer
            </p>
          </label>

          <button
            type="button"
            onClick={onUpload}
            disabled={uploadLoading || !file}
            className="mt-4 inline-flex w-full items-center justify-center rounded-lg bg-gradient-to-r from-sky-500 to-sky-600 px-4 py-2.5 text-sm font-semibold text-slate-950 shadow-lg shadow-sky-900/30 transition hover:from-sky-400 hover:to-sky-500 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {uploadLoading ? "Uploading..." : "Upload and continue"}
          </button>

          {uploadError ? (
            <p className="mt-3 rounded-lg border border-rose-900/70 bg-rose-950/40 px-3 py-2 text-sm text-rose-100">
              {uploadError}
            </p>
          ) : null}
        </div>

        <p className="mt-6 text-center text-[11px] text-slate-500">
          No data is persisted. INTELLI works on the most recently
          uploaded file.
        </p>
      </div>
    </main>
  );
}

// ===========================================================================
// Helpers
// ===========================================================================

function extractError(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string" && detail.length > 0) return detail;
    if (detail != null) {
      try {
        return JSON.stringify(detail);
      } catch {
        // fall through
      }
    }
    if (err.message) return `${fallback}: ${err.message}`;
  }
  return fallback;
}
