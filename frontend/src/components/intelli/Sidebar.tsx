"use client";

import type { KeyboardEvent } from "react";
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
  TextArea,
  TextInput,
} from "./ui";

/**
 * Left sidebar — MVP operator surface: upload, tag search, ask.
 */

interface SidebarProps {
  project: ControlProject | null;
  uploadFile: File | null;
  onFileChange: (file: File | null) => void;
  onUploadSubmit: () => void;
  onResetUpload: () => void;
  uploadLoading: boolean;
  uploadError: string | null;

  summary: NormalizedSummaryResponse | null;
  summaryLoading: boolean;
  summaryError: string | null;
  onLoadSummary: () => void;

  objectList: NormalizedControlObjectSummary[];
  objectListTotal: number;
  objectListProjectTotal: number;
  objectListFetchSucceeded: boolean;
  objectListLoading: boolean;
  objectListError: string | null;
  hasActiveObjectFilter: boolean;
  objectListOffset: number;
  objectListHasPrev: boolean;
  objectListHasNext: boolean;
  showObjectPaging: boolean;
  onObjectListPrev: () => void;
  onObjectListNext: () => void;
  search: string;
  onSearch: (s: string) => void;
  selectedObjectId: string;
  onSelectObject: (id: string) => void;

  question: string;
  onQuestionChange: (s: string) => void;
  askLoading: boolean;
  onAsk: () => void;
  selectedTagLabel: string | null;
}

export default function Sidebar(props: SidebarProps) {
  return (
    <aside className="flex h-full w-full max-w-[410px] flex-col gap-4 border-r border-white/10 bg-[linear-gradient(180deg,rgba(9,9,11,0.92),rgba(3,7,18,0.98))] px-4 py-5 shadow-2xl shadow-black/20">
      <UploadSection {...props} />
      <TagFinderSection {...props} />
      <AskSection {...props} />
    </aside>
  );
}

// ---------------------------------------------------------------------------
// 1. Upload — prominent L5X drop zone
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
      <section className="rounded-2xl border border-zinc-800/80 bg-zinc-900/45 p-3">
        <div className="flex items-center justify-between gap-3">
          <Eyebrow>Project</Eyebrow>
          <Badge tone="success" uppercase>
            loaded
          </Badge>
        </div>
        <div className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-cyan-400/20 bg-cyan-400/[0.04] px-3 py-3">
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
    <section className="flex flex-col gap-3 rounded-2xl border border-cyan-400/25 bg-cyan-400/[0.05] p-4">
      <div>
        <Eyebrow>Step 1 — Import</Eyebrow>
        <p className="mt-1 text-xs text-zinc-400">
          Upload a Rockwell Studio 5000 L5X export to begin.
        </p>
      </div>
      <label className="block cursor-pointer rounded-xl border-2 border-dashed border-cyan-400/35 bg-cyan-400/[0.06] px-4 py-8 text-center transition hover:border-cyan-300/55 hover:bg-cyan-400/[0.09]">
        <span className="sr-only">L5X file</span>
        <input
          type="file"
          accept=".l5x,.L5X,application/xml,text/xml"
          onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
          className="sr-only"
        />
        <p className="text-sm font-medium text-zinc-100">
          {uploadFile ? uploadFile.name : "Drop L5X here or click to browse"}
        </p>
        <p className="mt-1.5 text-[11px] text-zinc-500">
          Studio 5000 export (.l5x)
        </p>
      </label>
      <Button
        tone="primary"
        onClick={onUploadSubmit}
        disabled={uploadLoading || !uploadFile}
      >
        {uploadLoading ? "Uploading..." : "Upload & analyze"}
      </Button>
      {uploadError ? <InlineError>{uploadError}</InlineError> : null}
    </section>
  );
}

// ---------------------------------------------------------------------------
// 2. Tag finder — tags only, friendly names
// ---------------------------------------------------------------------------

function tagDisplayName(o: NormalizedControlObjectSummary): string {
  if (o.name && o.name.trim()) return o.name.trim();
  const tail = o.id.split("/").pop();
  return tail ?? o.id;
}

function tagLocationHint(o: NormalizedControlObjectSummary): string | null {
  if (o.source_location?.trim()) return o.source_location.trim();
  const parts = o.id.split("/");
  if (parts.length >= 3) {
    return parts.slice(0, -1).join(" / ");
  }
  return null;
}

function TagFinderSection({
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
  objectListOffset,
  objectListHasPrev,
  objectListHasNext,
  showObjectPaging,
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
      return "No tags match your search.";
    }
    if (!hasActiveObjectFilter && pt > 0) {
      return "Tags failed to load. Try refreshing.";
    }
    return "No tags found in this project.";
  })();

  return (
    <section className="flex min-h-0 flex-1 flex-col gap-2 rounded-2xl border border-zinc-800/80 bg-zinc-900/35 p-3">
      <div className="flex items-baseline justify-between gap-2">
        <Eyebrow>Step 2 — Find a tag</Eyebrow>
        <span className="text-[10px] text-zinc-500">
          {objectListLoading
            ? "…"
            : objectListTotal > 0
              ? `${objectListTotal} tag${objectListTotal === 1 ? "" : "s"}`
              : ""}
        </span>
      </div>

      <TextInput
        value={search}
        onChange={onSearch}
        placeholder="Search tag name or location"
        ariaLabel="Search tags"
      />

      {showObjectPaging &&
      objectListFetchSucceeded &&
      objectListTotal > 0 &&
      (objectListHasPrev || objectListHasNext) ? (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-[10px] text-zinc-500">
            {objectList.length > 0
              ? `${objectListOffset + 1}–${objectListOffset + objectList.length} of ${objectListTotal}`
              : `${objectListTotal} matches`}
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
          Refresh tag list
        </Button>
      ) : null}
      {(summaryLoading && !summary) || objectListLoading ? (
        <LoadingLine>Loading tags…</LoadingLine>
      ) : null}

      <div className="min-h-[8rem] max-h-[min(22rem,50vh)] flex-1 overflow-y-auto overflow-x-hidden rounded-xl border border-zinc-800/80 bg-zinc-950/40">
        {emptyMessage ? (
          <p className="px-3 py-4 text-xs text-zinc-500">{emptyMessage}</p>
        ) : (
          <ul className="divide-y divide-zinc-800/70">
            {objectList.map((o) => {
              const active = o.id === selectedObjectId;
              const location = tagLocationHint(o);
              return (
                <li key={o.id}>
                  <button
                    type="button"
                    onClick={() => onSelectObject(o.id)}
                    className={`block w-full px-3 py-2.5 text-left transition ${
                      active
                        ? "bg-cyan-400/10 ring-1 ring-inset ring-cyan-400/25"
                        : "hover:bg-zinc-900/60"
                    }`}
                  >
                    <span className="block truncate text-sm font-medium text-zinc-100">
                      {tagDisplayName(o)}
                    </span>
                    {location ? (
                      <span className="mt-0.5 block truncate text-[11px] text-zinc-500">
                        {location}
                      </span>
                    ) : null}
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
// 3. Ask INTELLI — optional natural-language path
// ---------------------------------------------------------------------------

function AskSection({
  project,
  question,
  onQuestionChange,
  askLoading,
  onAsk,
  selectedTagLabel,
}: SidebarProps) {
  if (!project) return null;

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!askLoading && question.trim()) {
        onAsk();
      }
    }
  }

  return (
    <section className="flex flex-col gap-2.5 rounded-2xl border border-zinc-800/80 bg-zinc-900/35 p-3">
      <Eyebrow>Or ask a question</Eyebrow>
      {selectedTagLabel ? (
        <p className="text-[11px] text-zinc-500">
          Context:{" "}
          <span className="text-zinc-300">{selectedTagLabel}</span>
        </p>
      ) : (
        <p className="text-[11px] text-zinc-500">
          Select a tag above for automatic diagnosis, or ask in plain language.
        </p>
      )}
      <TextArea
        value={question}
        onChange={onQuestionChange}
        onKeyDown={onKeyDown}
        rows={3}
        placeholder={
          selectedTagLabel
            ? `Why is ${selectedTagLabel} not energizing?`
            : '"Why is Pump B not running?"'
        }
        ariaLabel="Ask INTELLI a question"
      />
      <Button
        tone="secondary"
        onClick={onAsk}
        disabled={askLoading || !question.trim()}
      >
        {askLoading ? "Thinking..." : "Ask"}
      </Button>
    </section>
  );
}

interface SimpleProjectSummaryRowProps {
  project: ControlProject;
}

export function SimpleProjectSummaryRow({
  project,
}: SimpleProjectSummaryRowProps) {
  return (
    <span className="text-xs text-zinc-400">
      {project.project_name}
    </span>
  );
}
