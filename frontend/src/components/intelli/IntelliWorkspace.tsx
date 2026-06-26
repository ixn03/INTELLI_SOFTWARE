"use client";

import axios from "axios";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  extractIntelliError,
  useIntelliProject,
} from "@/context/IntelliProjectContext";
import type { ControlProject } from "@/types/intelli";
import type {
  NormalizedControlObjectSummary,
  NormalizedSummaryResponse,
  SignalTroubleshootingWorkspace,
} from "@/types/reasoning";

import { SignalTroubleshootingWorkspaceView } from "./SignalTroubleshootingWorkspaceView";
import { Badge, Button, InlineError, LoadingLine } from "./ui";

interface UploadResponse {
  project_id: string;
  connector: string;
  project: ControlProject;
  graph: Record<string, number>;
}

function parseObjectSuggestions(data: unknown): NormalizedControlObjectSummary[] {
  const d =
    data && typeof data === "object"
      ? (data as Partial<NormalizedSummaryResponse>)
      : {};
  return Array.isArray(d.control_objects) ? d.control_objects : [];
}

function displayProjectName(projectName: string | null | undefined): string {
  const name = (projectName ?? "").trim();
  if (!name || name.toLowerCase().startsWith("unknown")) {
    return "Imported control project";
  }
  return name;
}

export default function IntelliWorkspace() {
  const { apiBase, project, projectId, setUploadedProject, clearProject } =
    useIntelliProject();

  const [file, setFile] = useState<File | null>(null);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const [question, setQuestion] = useState("");
  const [workspace, setWorkspace] =
    useState<SignalTroubleshootingWorkspace | null>(null);
  const [troubleshootLoading, setTroubleshootLoading] = useState(false);
  const [troubleshootError, setTroubleshootError] = useState<string | null>(
    null,
  );

  const [suggestions, setSuggestions] = useState<
    NormalizedControlObjectSummary[]
  >([]);
  const [suggestionLoading, setSuggestionLoading] = useState(false);

  const projectApiKey = useMemo(
    () => (projectId || project?.file_hash || "").trim(),
    [projectId, project?.file_hash],
  );

  const apiRoot = useMemo(() => apiBase.replace(/\/$/, ""), [apiBase]);

  useEffect(() => {
    if (!projectApiKey) return;
    const q = question.trim();
    if (q.length < 2) {
      return;
    }

    const id = window.setTimeout(async () => {
      setSuggestionLoading(true);
      try {
        const res = await axios.get(`${apiRoot}/api/normalized-summary`, {
          params: {
            limit: 8,
            offset: 0,
            rel_limit: 1,
            rel_offset: 0,
            object_type: "tag",
            search: q,
          },
        });
        setSuggestions(parseObjectSuggestions(res.data));
      } catch {
        setSuggestions([]);
      } finally {
        setSuggestionLoading(false);
      }
    }, 220);

    return () => window.clearTimeout(id);
  }, [apiRoot, projectApiKey, question]);

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
      setQuestion("");
      setWorkspace(null);
      setTroubleshootError(null);
      setSuggestions([]);
      const res = await axios.post<UploadResponse>(`${apiRoot}/upload`, formData);
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

  const askTroubleshootingQuestion = useCallback(async () => {
    const q = question.trim();
    if (!projectApiKey || !q) return;

    setTroubleshootError(null);
    setTroubleshootLoading(true);
    try {
      const res = await axios.post<SignalTroubleshootingWorkspace>(
        `${apiRoot}/api/troubleshoot/question`,
        {
          project_id: projectApiKey,
          question: q,
          use_live_data: true,
        },
      );
      setWorkspace(res.data);
    } catch (err) {
      setWorkspace(null);
      setTroubleshootError(
        extractIntelliError(err, "Could not build troubleshooting workspace"),
      );
    } finally {
      setTroubleshootLoading(false);
    }
  }, [apiRoot, projectApiKey, question]);

  function resetWorkspaceUpload() {
    setFile(null);
    clearProject();
    setUploadError(null);
    setQuestion("");
    setWorkspace(null);
    setTroubleshootError(null);
    setSuggestions([]);
  }

  function askForSuggestion(signal: NormalizedControlObjectSummary) {
    const name = signal.name ?? signal.id.split("/").pop() ?? signal.id;
    setQuestion(`Why is ${name} not energizing?`);
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
      <header className="shrink-0 border-b border-white/10 bg-zinc-950/85 px-5 py-4 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <Badge tone="info" uppercase>
              Signal troubleshooting
            </Badge>
            <h1 className="mt-2 truncate text-2xl font-semibold text-white">
              {displayProjectName(project.project_name)}
            </h1>
            <p className="mt-1 max-w-3xl truncate text-sm text-zinc-500">
              Ask a controls question, then jump straight to writer rungs,
              required conditions, downstream readers, and evidence.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Link
              href="/workspace/advanced"
              className="inline-flex rounded-lg border border-zinc-800 bg-zinc-900/70 px-3 py-2 text-sm text-zinc-400 transition hover:border-zinc-700 hover:text-zinc-200"
            >
              Advanced
            </Link>
            <button
              type="button"
              onClick={resetWorkspaceUpload}
              className="rounded-lg border border-cyan-400/30 bg-cyan-400/10 px-3 py-2 text-sm font-medium text-cyan-100 transition hover:bg-cyan-400/15"
            >
              Switch project
            </button>
          </div>
        </div>
      </header>

      <main className="flex min-h-0 flex-1 flex-col gap-4 p-5">
        <section className="rounded-lg border border-zinc-800/80 bg-zinc-950/55 p-4">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void askTroubleshootingQuestion();
            }}
            className="flex flex-col gap-3 lg:flex-row"
          >
            <input
              value={question}
              onChange={(e) => {
                const next = e.target.value;
                setQuestion(next);
                if (next.trim().length < 2) setSuggestions([]);
                setTroubleshootError(null);
              }}
              placeholder="Ask why a motor, valve, permissive, or alarm is not changing state..."
              className="min-h-14 flex-1 rounded-lg border border-zinc-800 bg-zinc-950/70 px-4 text-base text-zinc-100 placeholder:text-zinc-500 focus:border-cyan-300/50 focus:outline-none focus:ring-1 focus:ring-cyan-300/30"
            />
            <Button
              type="submit"
              disabled={troubleshootLoading || !question.trim()}
              className="min-h-14 px-6"
            >
              {troubleshootLoading ? "Tracing..." : "Troubleshoot"}
            </Button>
          </form>

          <div className="mt-3 min-h-8">
            {troubleshootError ? <InlineError>{troubleshootError}</InlineError> : null}
            {!troubleshootError && suggestionLoading ? (
              <LoadingLine>Checking tag suggestions...</LoadingLine>
            ) : null}
            {!troubleshootError && suggestions.length ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-zinc-500">Tag suggestions</span>
                {suggestions.map((signal) => (
                  <button
                    key={signal.id}
                    type="button"
                    onClick={() => askForSuggestion(signal)}
                    className="rounded-md border border-zinc-800 bg-zinc-900/70 px-2 py-1 text-xs text-zinc-300 transition hover:border-cyan-500/50 hover:text-cyan-100"
                  >
                    {signal.name ?? signal.id.split("/").pop() ?? signal.id}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </section>

        <SignalTroubleshootingWorkspaceView workspace={workspace} />
      </main>
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
      <div className="relative w-full max-w-3xl rounded-lg border border-white/10 bg-zinc-950/75 p-6 shadow-2xl shadow-black/40 backdrop-blur">
        <div className="mx-auto max-w-2xl">
          <Badge tone="info" uppercase>
            Step 1 - Import
          </Badge>
          <h1 className="mt-4 text-4xl font-semibold text-white">
            Upload your L5X to start diagnosing.
          </h1>
          <p className="mx-auto mt-3 max-w-xl text-sm leading-6 text-zinc-400">
            Import a Studio 5000 export, ask a controls question, and INTELLI
            traces writer rungs, required conditions, and downstream uses.
          </p>
        </div>

        <div className="mx-auto mt-8 max-w-xl rounded-lg border border-zinc-800/80 bg-zinc-900/45 p-4 text-left">
          <label className="block cursor-pointer rounded-lg border-2 border-dashed border-cyan-400/35 bg-cyan-400/[0.06] px-4 py-8 text-center transition hover:border-cyan-300/55">
            <input
              type="file"
              accept=".l5x,.L5X,application/xml,text/xml"
              onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
              className="sr-only"
            />
            <p className="text-sm font-medium text-zinc-100">
              {file ? file.name : "Drop L5X here or click to browse"}
            </p>
            <p className="mt-2 text-xs text-zinc-500">
              Studio 5000 export (.l5x)
            </p>
          </label>
          <button
            type="button"
            onClick={onUpload}
            disabled={uploading || !file}
            className="mt-4 w-full rounded-lg bg-cyan-300 py-3 text-sm font-semibold text-cyan-950 transition hover:bg-cyan-200 disabled:opacity-40"
          >
            {uploading ? "Uploading..." : "Upload and analyze"}
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
