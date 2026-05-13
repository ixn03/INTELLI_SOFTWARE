"use client";

import { useIntelliProject } from "@/context/IntelliProjectContext";
import { Eyebrow } from "@/components/intelli/ui";

export default function ProjectOverviewPage() {
  const { project } = useIntelliProject();

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

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto px-8 py-10">
      <Eyebrow>Project</Eyebrow>
      <h1 className="mt-2 text-2xl font-semibold text-zinc-50">
        {project.project_name || "Imported project"}
      </h1>
      <p className="mt-2 max-w-xl text-sm text-zinc-400">
        Parsed structure from the L5X connector. Parser grade and instruction
        coverage metrics can attach here later.
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
    </div>
  );
}
