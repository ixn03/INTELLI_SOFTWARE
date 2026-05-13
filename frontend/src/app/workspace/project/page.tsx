"use client";

import axios from "axios";
import { useState } from "react";

import {
  extractIntelliError,
  useIntelliProject,
} from "@/context/IntelliProjectContext";
import { Button, Code, Eyebrow, InlineError, TextInput } from "@/components/intelli/ui";
import type { VersionImpactSummary } from "@/types/reasoning";

export default function ProjectOverviewPage() {
  const { project, projectId, apiBase } = useIntelliProject();
  const [oldProjectId, setOldProjectId] = useState("");
  const [newProjectId, setNewProjectId] = useState(projectId);
  const [impact, setImpact] = useState<VersionImpactSummary | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [impactError, setImpactError] = useState<string | null>(null);

  if (!project) {
    return (
      <div className="p-10 text-sm text-zinc-500">
        No project loaded. Upload from home or the engineering workspace.
      </div>
    );
  }

  const programs = project.controllers.flatMap((c) =>
    c.programs.map((p) => ({
      controller: c.name,
      program: p.name,
      routines: p.routines.length,
      tags: p.tags?.length ?? 0,
    })),
  );

  const routineTotal = project.controllers.reduce(
    (a, c) => a + c.programs.reduce((b, p) => b + p.routines.length, 0),
    0,
  );
  const tagTotal = project.controllers.reduce(
    (a, c) =>
      a +
      c.programs.reduce(
        (b, p) => b + (p.tags?.length ?? 0),
        0,
      ) +
      (c.controller_tags?.length ?? 0),
    0,
  );

  let instr = 0;
  for (const c of project.controllers) {
    for (const p of c.programs) {
      for (const r of p.routines) {
        instr += r.instructions.length;
      }
    }
  }

  async function runVersionImpact() {
    const oldId = oldProjectId.trim();
    const newId = (newProjectId.trim() || projectId).trim();
    if (!oldId || !newId) {
      setImpactError("Enter both old and new project ids.");
      return;
    }
    setImpactLoading(true);
    setImpactError(null);
    setImpact(null);
    try {
      const res = await axios.post<VersionImpactSummary>(
        `${apiBase.replace(/\/$/, "")}/api/version-impact`,
        {
          old_project_id: oldId,
          new_project_id: newId,
        },
      );
      setImpact(res.data);
    } catch (err) {
      setImpactError(extractIntelliError(err, "Could not compare versions"));
    } finally {
      setImpactLoading(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto px-8 py-10">
      <Eyebrow>Project</Eyebrow>
      <h1 className="mt-2 text-2xl font-semibold text-zinc-50">
        {project.project_name || "Imported project"}
      </h1>
      <p className="mt-2 max-w-xl text-sm text-zinc-400">
        Parsed structure from the active connector. Rockwell L5X has the
        strongest logic parsing today; Siemens, DeltaV, and Honeywell imports
        are intentionally foundation/preservation oriented.
      </p>

      <dl className="mt-10 grid max-w-lg gap-4 text-sm sm:grid-cols-2">
        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 px-4 py-3">
          <dt className="text-xs uppercase tracking-[0.14em] text-zinc-500">
            Controllers
          </dt>
          <dd className="mt-1 text-lg text-zinc-100">
            {project.controllers.length}
          </dd>
        </div>
        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 px-4 py-3">
          <dt className="text-xs uppercase tracking-[0.14em] text-zinc-500">
            Programs
          </dt>
          <dd className="mt-1 text-lg text-zinc-100">{programs.length}</dd>
        </div>
        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 px-4 py-3">
          <dt className="text-xs uppercase tracking-[0.14em] text-zinc-500">
            Routines
          </dt>
          <dd className="mt-1 text-lg text-zinc-100">{routineTotal}</dd>
        </div>
        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 px-4 py-3">
          <dt className="text-xs uppercase tracking-[0.14em] text-zinc-500">
            Tags (approx.)
          </dt>
          <dd className="mt-1 text-lg text-zinc-100">{tagTotal}</dd>
        </div>
        <div className="rounded-xl border border-zinc-800/80 bg-zinc-900/30 px-4 py-3 sm:col-span-2">
          <dt className="text-xs uppercase tracking-[0.14em] text-zinc-500">
            Parsed ladder / ST instruction rows
          </dt>
          <dd className="mt-1 text-lg text-zinc-100">{instr}</dd>
        </div>
      </dl>

      <section className="mt-12 max-w-3xl">
        <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
          Programs
        </h2>
        <ul className="mt-3 divide-y divide-zinc-800/80 rounded-xl border border-zinc-800/80">
          {programs.map((row) => (
            <li
              key={`${row.controller}/${row.program}`}
              className="flex justify-between gap-4 px-4 py-2 text-sm text-zinc-300"
            >
              <span>
                {row.controller} / {row.program}
              </span>
              <span className="shrink-0 text-xs text-zinc-500">
                {row.routines} routines · {row.tags} program tags
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="mt-12 max-w-3xl">
        <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
          Version Intelligence
        </h2>
        <p className="mt-2 text-sm text-zinc-400">
          Compare two uploaded project IDs using deterministic relationship
          diff evidence. This does not infer process meaning beyond the graph.
        </p>
        <div className="mt-4 grid gap-3 rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4 sm:grid-cols-2">
          <label className="space-y-1 text-xs text-zinc-500">
            Old project id
            <TextInput
              value={oldProjectId}
              onChange={setOldProjectId}
              placeholder="Paste previous file_hash / project id"
              ariaLabel="Old project id"
            />
          </label>
          <label className="space-y-1 text-xs text-zinc-500">
            New project id
            <TextInput
              value={newProjectId}
              onChange={setNewProjectId}
              placeholder={projectId || "Current project id"}
              ariaLabel="New project id"
            />
          </label>
          <div className="sm:col-span-2">
            <Button
              tone="secondary"
              onClick={() => void runVersionImpact()}
              disabled={impactLoading}
            >
              {impactLoading ? "Comparing..." : "Analyze impact"}
            </Button>
          </div>
          {impactError ? (
            <div className="sm:col-span-2">
              <InlineError>{impactError}</InlineError>
            </div>
          ) : null}
        </div>
        {impact ? (
          <div className="mt-4 space-y-4 rounded-xl border border-zinc-800/80 bg-zinc-900/30 p-4">
            <div className="flex flex-wrap gap-2 text-xs text-zinc-300">
              <span className="rounded border border-zinc-700 px-2 py-1">
                Risk: {impact.risk_level}
              </span>
              <span className="rounded border border-zinc-700 px-2 py-1">
                Confidence: {Math.round(impact.confidence * 100)}%
              </span>
            </div>
            <VersionImpactList
              title="Operationally significant changes"
              rows={impact.operationally_significant_changes}
            />
            <VersionImpactList
              title="Possible runtime impacts"
              rows={impact.possible_runtime_impacts}
            />
            <VersionImpactList
              title="Changed fault behavior"
              rows={impact.changed_fault_behavior}
            />
            <details>
              <summary className="cursor-pointer text-xs text-zinc-500">
                Raw evidence
              </summary>
              <div className="mt-2">
                <Code>{JSON.stringify(impact.evidence, null, 2)}</Code>
              </div>
            </details>
          </div>
        ) : null}
      </section>
    </div>
  );
}

function VersionImpactList({
  title,
  rows,
}: {
  title: string;
  rows: string[];
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
        {title}
      </p>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-zinc-300">
        {rows.slice(0, 12).map((row, i) => (
          <li key={i}>{row}</li>
        ))}
      </ul>
    </div>
  );
}
