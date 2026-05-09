"use client";

import { useEffect, useMemo, useState } from "react";
import type {
  ControlProject,
  ControlRoutine,
  ExplanationResult,
} from "@/types/control";

const API_BASE = "http://127.0.0.1:8000";

interface ProjectDashboardProps {
  projectId: string;
  project: ControlProject;
  graph: Record<string, number>;
  onResetUpload: () => void;
}

function collectTagNames(project: ControlProject): string[] {
  const names = new Set<string>();
  for (const c of project.controllers) {
    for (const t of c.controller_tags) names.add(t.name);
    for (const p of c.programs) {
      for (const t of p.tags) names.add(t.name);
    }
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}

export function ProjectDashboard({
  projectId,
  project,
  graph,
  onResetUpload,
}: ProjectDashboardProps) {
  const [ci, setCi] = useState(0);
  const [pi, setPi] = useState(0);
  const [ri, setRi] = useState(0);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  const [explainResult, setExplainResult] = useState<ExplanationResult | null>(
    null,
  );
  const [explainLoading, setExplainLoading] = useState(false);
  const [explainError, setExplainError] = useState<string | null>(null);

  const tagNames = useMemo(() => collectTagNames(project), [project]);

  useEffect(() => {
    setCi(0);
    setPi(0);
    setRi(0);
    setSelectedTag(null);
    setExplainResult(null);
    setExplainError(null);
  }, [project]);

  const routine: ControlRoutine | null =
    project.controllers[ci]?.programs[pi]?.routines[ri] ?? null;

  const routinePath =
    project.controllers[ci] && project.controllers[ci].programs[pi]
      ? `${project.controllers[ci].name} · ${project.controllers[ci].programs[pi].name} · ${routine?.name ?? "—"}`
      : "—";

  async function runExplain(tag: string) {
    setSelectedTag(tag);
    setExplainLoading(true);
    setExplainError(null);
    setExplainResult(null);
    try {
      const res = await fetch(`${API_BASE}/explain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project_id: projectId,
          target_tag: tag,
          question: "why_false",
        }),
      });
      const data = (await res.json().catch(() => null)) as
        | ExplanationResult
        | { detail?: unknown }
        | null;
      if (!res.ok) {
        const detail =
          data && typeof data === "object" && "detail" in data
            ? (data as { detail: unknown }).detail
            : null;
        throw new Error(
          typeof detail === "string"
            ? detail
            : detail != null
              ? JSON.stringify(detail)
              : `${res.status} ${res.statusText}`,
        );
      }
      setExplainResult(data as ExplanationResult);
    } catch (e) {
      setExplainError(e instanceof Error ? e.message : "Explain request failed");
    } finally {
      setExplainLoading(false);
    }
  }

  return (
    <div className="flex min-h-[calc(100vh-2rem)] flex-col gap-3">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-zinc-800 pb-4">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
            INTELLI
          </p>
          <h2 className="text-xl font-semibold text-white">
            {project.project_name || "Imported project"}
          </h2>
          <div className="mt-2 flex flex-wrap gap-2 text-xs text-zinc-400">
            <span className="rounded-full bg-zinc-800 px-2 py-0.5">
              nodes {graph.nodes ?? "—"}
            </span>
            <span className="rounded-full bg-zinc-800 px-2 py-0.5">
              edges {graph.edges ?? "—"}
            </span>
            <span className="rounded-full bg-zinc-800 px-2 py-0.5">
              instructions {graph.instructions ?? "—"}
            </span>
            <span className="rounded-full bg-zinc-800 px-2 py-0.5">
              tags {graph.tags ?? "—"}
            </span>
          </div>
        </div>
        <button
          type="button"
          onClick={onResetUpload}
          className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-200 hover:bg-zinc-800"
        >
          Upload another file
        </button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[minmax(220px,280px)_minmax(0,1fr)_minmax(280px,360px)] lg:gap-0 lg:divide-x lg:divide-zinc-800">
        {/* Sidebar */}
        <aside className="flex min-h-[320px] flex-col gap-4 overflow-hidden lg:min-h-0 lg:pr-3">
          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
              Programs &amp; routines
            </h3>
            <div className="max-h-[38vh] overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/50 p-2 lg:max-h-[calc(100vh-14rem)]">
              {project.controllers.length === 0 ? (
                <p className="p-2 text-sm text-zinc-500">No controllers.</p>
              ) : (
                project.controllers.map((ctrl, cIdx) => (
                  <div key={ctrl.name} className="mb-3 last:mb-0">
                    <p className="px-2 py-1 text-xs font-medium text-zinc-500">
                      {ctrl.name}
                    </p>
                    {ctrl.programs.map((prog, pIdx) => (
                      <div key={`${ctrl.name}:${prog.name}`} className="mb-2">
                        <p className="px-2 py-0.5 text-sm font-medium text-zinc-300">
                          {prog.name}
                        </p>
                        <ul className="space-y-0.5 pl-2">
                          {prog.routines.map((r, rIdx) => {
                            const active =
                              cIdx === ci && pIdx === pi && rIdx === ri;
                            return (
                              <li key={`${prog.name}:${r.name}`}>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setCi(cIdx);
                                    setPi(pIdx);
                                    setRi(rIdx);
                                  }}
                                  className={`w-full rounded-md px-2 py-1.5 text-left text-sm transition-colors ${
                                    active
                                      ? "bg-blue-600 text-white"
                                      : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                                  }`}
                                >
                                  {r.name}
                                  {r.language ? (
                                    <span className="ml-1 text-[10px] uppercase opacity-70">
                                      ({r.language})
                                    </span>
                                  ) : null}
                                </button>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    ))}
                  </div>
                ))
              )}
            </div>
          </section>

          <section className="min-h-0 flex-1">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
              Tags
            </h3>
            <div className="max-h-[28vh] overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900/50 p-2 lg:max-h-[calc(100vh-24rem)]">
              {tagNames.length === 0 ? (
                <p className="p-2 text-sm text-zinc-500">No tags parsed.</p>
              ) : (
                <ul className="space-y-0.5">
                  {tagNames.map((name) => (
                    <li key={name}>
                      <button
                        type="button"
                        onClick={() => runExplain(name)}
                        className={`w-full truncate rounded-md px-2 py-1.5 text-left font-mono text-xs transition-colors ${
                          selectedTag === name
                            ? "bg-emerald-900/60 text-emerald-100 ring-1 ring-emerald-700"
                            : "text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100"
                        }`}
                      >
                        {name}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        </aside>

        {/* Routine viewer */}
        <section className="flex min-h-[360px] flex-col gap-4 overflow-hidden lg:min-h-0 lg:px-4">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
              Routine viewer
            </h3>
            <p className="mt-1 text-sm text-zinc-400">{routinePath}</p>
          </div>

          {!routine ? (
            <p className="text-sm text-zinc-500">Select a routine.</p>
          ) : (
            <>
              <div className="flex min-h-0 flex-1 flex-col gap-3">
                <div className="flex min-h-[140px] flex-1 flex-col rounded-lg border border-zinc-800 bg-zinc-900/40">
                  <div className="border-b border-zinc-800 px-3 py-2">
                    <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                      Raw logic
                    </span>
                  </div>
                  <pre className="max-h-[32vh] flex-1 overflow-auto whitespace-pre-wrap p-3 font-mono text-xs leading-relaxed text-zinc-300 lg:max-h-none">
                    {routine.raw_logic?.trim()
                      ? routine.raw_logic
                      : "No raw logic text was captured for this routine."}
                  </pre>
                </div>

                <div className="flex min-h-[160px] flex-1 flex-col rounded-lg border border-zinc-800 bg-zinc-900/40">
                  <div className="border-b border-zinc-800 px-3 py-2">
                    <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                      Parsed instructions
                    </span>
                    <span className="ml-2 text-xs text-zinc-600">
                      ({routine.instructions.length})
                    </span>
                  </div>
                  <div className="max-h-[40vh] overflow-auto lg:max-h-[calc(100vh-20rem)]">
                    {routine.instructions.length === 0 ? (
                      <p className="p-3 text-sm text-zinc-500">
                        No instructions in this routine.
                      </p>
                    ) : (
                      <ul className="divide-y divide-zinc-800">
                        {routine.instructions.map((inst, idx) => (
                          <li
                            key={inst.id ?? `${routine.name}-${idx}`}
                            className="px-3 py-2 text-xs"
                          >
                            <div className="flex flex-wrap items-baseline gap-2">
                              <span className="font-semibold text-blue-300">
                                {inst.instruction_type}
                              </span>
                              {inst.rung_number != null ? (
                                <span className="text-zinc-600">
                                  rung {inst.rung_number}
                                </span>
                              ) : null}
                            </div>
                            {inst.operands.length > 0 ? (
                              <p className="mt-1 font-mono text-zinc-400">
                                {inst.operands.join(", ")}
                              </p>
                            ) : null}
                            {inst.raw_text ? (
                              <p className="mt-1 font-mono text-zinc-500">
                                {inst.raw_text}
                              </p>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </div>
            </>
          )}
        </section>

        {/* Trace / Explain */}
        <aside className="flex min-h-[280px] flex-col gap-4 overflow-hidden lg:min-h-0 lg:pl-3">
          <div>
            <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
              Trace / Explain
            </h3>
            <p className="mt-1 text-sm text-zinc-400">
              Click a tag in the sidebar to run a deterministic trace and
              explanation.
            </p>
          </div>

          {explainError && (
            <p className="rounded-lg border border-red-900/80 bg-red-950/40 px-3 py-2 text-sm text-red-200">
              {explainError}
            </p>
          )}

          {!selectedTag && !explainLoading && (
            <p className="rounded-lg border border-dashed border-zinc-700 bg-zinc-900/30 p-4 text-sm text-zinc-500">
              No tag selected.
            </p>
          )}

          {explainLoading && (
            <p className="text-sm text-zinc-400">Loading trace…</p>
          )}

          {explainResult && !explainLoading && (
            <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto">
              <div className="rounded-lg border border-zinc-800 bg-zinc-900/40">
                <div className="border-b border-zinc-800 px-3 py-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Deterministic trace
                  </span>
                  <span className="ml-2 font-mono text-xs text-zinc-400">
                    {explainResult.trace.target_tag}
                  </span>
                </div>
                <div className="space-y-2 p-3 text-sm text-zinc-300">
                  {explainResult.trace.status ? (
                    <p className="text-xs uppercase text-zinc-500">
                      status: {explainResult.trace.status}
                    </p>
                  ) : null}
                  <p>{explainResult.trace.summary}</p>
                </div>
              </div>

              <div className="rounded-lg border border-zinc-800 bg-zinc-900/40">
                <div className="border-b border-zinc-800 px-3 py-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Explanation
                  </span>
                </div>
                <p className="p-3 text-sm leading-relaxed text-zinc-300">
                  {explainResult.explanation}
                </p>
              </div>

              <div className="rounded-lg border border-zinc-800 bg-zinc-900/40">
                <div className="border-b border-zinc-800 px-3 py-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Conditions
                  </span>
                  <span className="ml-2 text-xs text-zinc-600">
                    ({explainResult.trace.causes.length})
                  </span>
                </div>
                {explainResult.trace.causes.length === 0 ? (
                  <p className="p-3 text-sm text-zinc-500">
                    No upstream condition tags were linked for this output in
                    the graph.
                  </p>
                ) : (
                  <ul className="divide-y divide-zinc-800">
                    {explainResult.trace.causes.map((c, i) => (
                      <li key={`${c.tag}-${i}`} className="px-3 py-2 text-xs">
                        <p className="font-mono font-semibold text-emerald-300">
                          {c.tag}
                        </p>
                        <p className="mt-1 text-zinc-500">
                          {c.relationship}
                          {c.program ? ` · ${c.program}` : ""}
                          {c.routine ? ` · ${c.routine}` : ""}
                        </p>
                        {c.raw_text ? (
                          <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] text-zinc-500">
                            {c.raw_text}
                          </pre>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
