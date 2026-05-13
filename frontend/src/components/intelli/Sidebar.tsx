"use client";

import type { ControlProject } from "@/types/intelli";
import type {
  NormalizedControlObjectSummary,
  NormalizedSummaryResponse,
} from "@/types/reasoning";

import {
  Badge,
  Button,
  Eyebrow,
  InlineError,
  LoadingLine,
  Stat,
  TextInput,
} from "./ui";

/**
 * Left sidebar -- the operator's control surface.
 *
 * Sections, top to bottom:
 *
 *   1. Upload status (file name + "swap" button when a project is
 *      loaded; full upload card when not).
 *   2. Project summary (counts + minimal program/routine list).
 *   3. Object finder (server-backed search via ``GET /api/normalized-summary``
 *      paging; same query semantics as ``/api/control-objects``).
 *      Selecting an object fills the trace target in
 *      :file:`IntelliWorkspace.tsx`.
 *   4. Ask INTELLI (``/api/ask-v2`` with optional runtime snapshot;
 *      falls back to ``/api/ask-v1``).
 *
 * The sidebar is pure presentation -- all loading, error handling,
 * and state lives in IntelliWorkspace. We only render what we're given.
 */

interface SidebarProps {
  // Upload status
  project: ControlProject | null;
  uploadFile: File | null;
  onFileChange: (file: File | null) => void;
  onUploadSubmit: () => void;
  onResetUpload: () => void;
  uploadLoading: boolean;
  uploadError: string | null;

  // Normalized summary (counts / light refresh)
  summary: NormalizedSummaryResponse | null;
  summaryLoading: boolean;
  summaryError: string | null;
  onLoadSummary: () => void;

  // Object finder (server-side search via /api/normalized-summary paging)
  objectList: NormalizedControlObjectSummary[];
  objectListTotal: number;
  objectListProjectTotal: number;
  objectListFetchSucceeded: boolean;
  objectListLoading: boolean;
  objectListError: string | null;
  hasActiveObjectFilter: boolean;
  objectTypeFilter: string;
  onObjectTypeFilter: (v: string) => void;
  objectListOffset: number;
  objectListHasPrev: boolean;
  objectListHasNext: boolean;
  onObjectListPrev: () => void;
  onObjectListNext: () => void;
  search: string;
  onSearch: (s: string) => void;
  selectedObjectId: string;
  onSelectObject: (id: string) => void;

  // Ask box
  question: string;
  onQuestionChange: (s: string) => void;
  askLoading: boolean;
  onAsk: () => void;
}

export default function Sidebar(props: SidebarProps) {
  return (
    <aside className="flex h-full w-full max-w-[360px] flex-col gap-5 border-r border-zinc-800/80 bg-zinc-950/60 px-5 py-6">
      <UploadSection {...props} />
      <ProjectSummarySection {...props} />
      <ObjectFinderSection {...props} />
      <AskSection {...props} />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// 1. Upload
// ---------------------------------------------------------------------------

function UploadSection({
  project,
  uploadFile,
  onFileChange,
  onUploadSubmit,
  onResetUpload,
  uploadLoading,
  uploadError,
}: SidebarProps) {
  if (project) {
    return (
      <section>
        <Eyebrow>Project</Eyebrow>
        <div className="mt-2 flex items-center justify-between gap-2 rounded-xl border border-zinc-800/80 bg-zinc-900/60 px-3 py-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-zinc-100">
              {project.project_name || "Imported project"}
            </p>
            <p className="truncate text-[11px] text-zinc-500">
              {project.controllers.length} controller
              {project.controllers.length === 1 ? "" : "s"}
            </p>
          </div>
          <Button tone="ghost" onClick={onResetUpload} className="shrink-0">
            Swap
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-2">
      <Eyebrow>Upload L5X</Eyebrow>
      <label className="block cursor-pointer rounded-xl border border-dashed border-zinc-700/80 bg-zinc-900/40 px-4 py-5 text-center transition hover:border-zinc-600">
        <span className="sr-only">L5X file</span>
        <input
          type="file"
          accept=".l5x,.L5X,application/xml,text/xml"
          onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
          className="sr-only"
        />
        <p className="text-sm text-zinc-200">
          {uploadFile ? uploadFile.name : "Choose an L5X file"}
        </p>
        <p className="mt-1 text-[11px] text-zinc-500">
          Rockwell Logix Designer export
        </p>
      </label>
      <Button
        tone="primary"
        onClick={onUploadSubmit}
        disabled={uploadLoading || !uploadFile}
      >
        {uploadLoading ? "Uploading..." : "Upload"}
      </Button>
      {uploadError ? <InlineError>{uploadError}</InlineError> : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 2. Project summary
// ---------------------------------------------------------------------------

function ProjectSummarySection({
  project,
  summary,
}: SidebarProps) {
  if (!project) return null;

  const programs = project.controllers.flatMap((c) =>
    c.programs.map((p) => ({ controller: c.name, name: p.name })),
  );
  const routineCount = project.controllers.reduce(
    (acc, c) =>
      acc + c.programs.reduce((a, p) => a + p.routines.length, 0),
    0,
  );

  return (
    <section>
      <Eyebrow>Project summary</Eyebrow>
      <div className="mt-2 flex flex-wrap gap-1.5">
        <Stat
          value={project.controllers.length}
          label={`controller${project.controllers.length === 1 ? "" : "s"}`}
        />
        <Stat
          value={programs.length}
          label={`program${programs.length === 1 ? "" : "s"}`}
        />
        <Stat
          value={routineCount}
          label={`routine${routineCount === 1 ? "" : "s"}`}
        />
        {summary ? (
          <>
            <Stat
              value={summary.control_object_count}
              label="objects"
            />
            <Stat
              value={summary.relationship_count}
              label="relationships"
            />
          </>
        ) : null}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 3. Object finder
// ---------------------------------------------------------------------------

function ObjectFinderSection({
  project,
  summary,
  summaryLoading,
  summaryError,
  onLoadSummary,
  objectList,
  objectListTotal,
  objectListProjectTotal,
  objectListFetchSucceeded,
  objectListLoading,
  objectListError,
  hasActiveObjectFilter,
  objectTypeFilter,
  onObjectTypeFilter,
  objectListOffset,
  objectListHasPrev,
  objectListHasNext,
  onObjectListPrev,
  onObjectListNext,
  search,
  onSearch,
  selectedObjectId,
  onSelectObject,
}: SidebarProps) {
  if (!project) return null;

  const projectWide =
    summary?.control_object_count ?? objectListProjectTotal ?? 0;

  const emptyMessage = (() => {
    if (objectListLoading || objectListError) return null;
    if (!objectListFetchSucceeded) return null;
    if (objectList.length > 0) return null;

    const pt = Math.max(projectWide, objectListProjectTotal, objectListTotal);

    if (hasActiveObjectFilter && objectListTotal === 0 && pt > 0) {
      return "No objects match the current filter.";
    }
    if (!hasActiveObjectFilter && pt > 0) {
      return "Objects failed to load. Try refreshing.";
    }
    return "No control objects in the normalized graph.";
  })();

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-baseline justify-between gap-2">
        <Eyebrow>Find an object</Eyebrow>
        <span className="text-[10px] text-zinc-500">
          {objectListLoading
            ? "…"
            : `${objectList.length} shown · ${objectListTotal} match`}
          {projectWide > 0 && projectWide !== objectListTotal ? (
            <span className="text-zinc-600"> · {projectWide} total</span>
          ) : null}
        </span>
      </div>

      <label className="sr-only" htmlFor="intelli-object-type">
        Object type filter
      </label>
      <select
        id="intelli-object-type"
        value={objectTypeFilter}
        onChange={(e) => onObjectTypeFilter(e.target.value)}
        className="rounded-lg border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-xs text-zinc-200"
      >
        <option value="">All types</option>
        <option value="tag">Tags</option>
        <option value="routine">Routines</option>
        <option value="rung">Rungs</option>
        <option value="instruction">Instructions</option>
        <option value="controller">Controllers</option>
        <option value="program">Programs</option>
      </select>

      <TextInput
        value={search}
        onChange={onSearch}
        placeholder="Search id, name, type, location"
        ariaLabel="Search control objects"
      />

      {objectListFetchSucceeded &&
      objectListTotal > 0 &&
      (objectListHasPrev || objectListHasNext) ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[10px] text-zinc-500">
            {objectList.length > 0
              ? `Rows ${objectListOffset + 1}–${objectListOffset + objectList.length} of ${objectListTotal} match`
              : `Page · ${objectListTotal} match`}
          </p>
          <div className="flex gap-1.5">
            <Button
              tone="secondary"
              type="button"
              className="px-2.5 py-1 text-xs"
              disabled={!objectListHasPrev || objectListLoading}
              onClick={onObjectListPrev}
            >
              Previous
            </Button>
            <Button
              tone="secondary"
              type="button"
              className="px-2.5 py-1 text-xs"
              disabled={!objectListHasNext || objectListLoading}
              onClick={onObjectListNext}
            >
              Next
            </Button>
          </div>
        </div>
      ) : null}

      {summaryError || objectListError ? (
        <InlineError>{summaryError ?? objectListError}</InlineError>
      ) : null}

      {!summary && !summaryLoading && !summaryError ? (
        <Button tone="secondary" onClick={onLoadSummary}>
          Load graph counts
        </Button>
      ) : null}
      {(summaryLoading && !summary) || objectListLoading ? (
        <LoadingLine>Loading objects…</LoadingLine>
      ) : null}

      <div className="min-h-[8rem] max-h-[min(22rem,50vh)] flex-1 overflow-y-auto overflow-x-hidden rounded-xl border border-zinc-800/80 bg-zinc-950/40">
        {emptyMessage ? (
          <p className="px-3 py-4 text-xs text-zinc-500">{emptyMessage}</p>
        ) : (
          <ul className="divide-y divide-zinc-800/70">
            {objectList.map((o) => {
              const active = o.id === selectedObjectId;
              return (
                <li key={o.id}>
                  <button
                    type="button"
                    onClick={() => onSelectObject(o.id)}
                    className={`block w-full px-3 py-2 text-left transition ${
                      active
                        ? "bg-zinc-800/80"
                        : "hover:bg-zinc-900/60"
                    }`}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate text-sm text-zinc-100">
                        {o.name ?? o.id.split("/").pop()}
                      </span>
                      <Badge tone="outline" uppercase>
                        {o.object_type}
                      </Badge>
                    </div>
                    <p className="mt-0.5 truncate font-mono text-[10px] text-zinc-500">
                      {o.id}
                    </p>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// 4. Ask INTELLI
// ---------------------------------------------------------------------------

function AskSection({
  project,
  question,
  onQuestionChange,
  askLoading,
  onAsk,
}: SidebarProps) {
  if (!project) return null;

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" && !askLoading && question.trim()) {
      e.preventDefault();
      onAsk();
    }
  }

  return (
    <section className="flex flex-col gap-2">
      <Eyebrow>Ask INTELLI</Eyebrow>
      <TextInput
        value={question}
        onChange={onQuestionChange}
        onKeyDown={onKeyDown}
        placeholder='e.g. "Why is Motor_Run not running?"'
        ariaLabel="Ask INTELLI a question"
      />
      <Button
        tone="secondary"
        onClick={onAsk}
        disabled={askLoading || !question.trim()}
      >
        {askLoading ? "Thinking..." : "Ask"}
      </Button>
      <p className="text-[11px] leading-snug text-zinc-500">
        Uses ask-v2 (Trace v2, optional runtime diagnosis when a JSON
        snapshot is set in the main panel). Falls back to ask-v1 if v2
        fails. No LLM.
      </p>
    </section>
  );
}

interface SimpleProjectSummaryRowProps {
  project: ControlProject;
}

export function SimpleProjectSummaryRow({
  project,
}: SimpleProjectSummaryRowProps) {
  // (Kept exported so a future header can show a compact project
  // chip; not used by the default sidebar layout.)
  return (
    <span className="text-xs text-zinc-400">
      {project.project_name}
    </span>
  );
}
