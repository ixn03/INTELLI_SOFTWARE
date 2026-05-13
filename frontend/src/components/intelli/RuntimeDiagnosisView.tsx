"use client";

import type {
  OverallVerdict,
  RuntimeConditionResult,
  RuntimeConflict,
  RuntimeWriterPathResult,
  TraceResponse,
} from "@/types/reasoning";
import {
  getRuntimeV2VerdictConclusion,
  parseRuntimeV2PlatformSpecific,
} from "@/types/reasoning";

import { Accordion, Badge, Button, Card, CardBody, CardHeader } from "./ui";

// ===========================================================================
// Snapshot input — JSON textarea + evaluate (controlled by parent).
// ===========================================================================

export function RuntimeSnapshotPanel({
  value,
  onChange,
  onEvaluate,
  disabled,
  evaluating,
  parseError,
  apiError,
}: {
  value: string;
  onChange: (v: string) => void;
  onEvaluate: () => void;
  disabled: boolean;
  evaluating: boolean;
  parseError: string | null;
  apiError: string | null;
}) {
  return (
    <Card className="border-violet-900/30 bg-zinc-900/40">
      <CardHeader
        eyebrow="Diagnosis mode"
        title="Runtime snapshot"
        trailing={
          <Button
            type="button"
            tone="secondary"
            disabled={disabled || evaluating}
            onClick={onEvaluate}
          >
            {evaluating ? "Evaluating…" : "Evaluate runtime"}
          </Button>
        }
      />
      <CardBody className="space-y-3">
        <p className="text-xs leading-relaxed text-zinc-500">
          Paste tag values as JSON. This does not connect to a PLC yet.
        </p>
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
          placeholder='{"StartPB": true, "Faulted": false}'
          rows={5}
          aria-label="Runtime snapshot JSON"
          className="w-full resize-y rounded-lg border border-zinc-800 bg-zinc-950/70 px-3 py-2 font-mono text-xs leading-relaxed text-zinc-100 placeholder:text-zinc-600 focus:border-violet-700/60 focus:outline-none focus:ring-1 focus:ring-violet-700/40"
        />
        {parseError ? (
          <p className="rounded-lg border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-sm text-rose-100">
            {parseError}
          </p>
        ) : null}
        {apiError ? (
          <p className="rounded-lg border border-rose-900/60 bg-rose-950/30 px-3 py-2 text-sm text-rose-100">
            {apiError}
          </p>
        ) : null}
      </CardBody>
    </Card>
  );
}

// ===========================================================================
// Operational verdict — hero card when trace_version === runtime_v2
// ===========================================================================

function verdictBadge(verdict: OverallVerdict): { tone: "success" | "danger" | "warning"; label: string } {
  switch (verdict) {
    case "target_can_be_on":
      return { tone: "success", label: "Can be on" };
    case "blocked":
      return { tone: "danger", label: "Blocked" };
    case "incomplete":
      return { tone: "warning", label: "Incomplete" };
    case "conflict_or_scan_order_dependent":
      return { tone: "warning", label: "Conflict / scan order" };
    case "target_likely_off_or_reset":
      return { tone: "warning", label: "Likely off / reset" };
    default: {
      const _x: never = verdict;
      return { tone: "warning", label: String(_x) };
    }
  }
}

export function OperationalVerdictCard({ trace }: { trace: TraceResponse }) {
  const meta = parseRuntimeV2PlatformSpecific(trace.platform_specific);
  const verdictConcl = getRuntimeV2VerdictConclusion(trace);
  if (!meta || !verdictConcl) return null;

  const { tone, label } = verdictBadge(meta.overall_verdict);

  return (
    <Card className="border-emerald-900/25 bg-gradient-to-br from-zinc-900/80 to-emerald-950/10">
      <CardHeader
        eyebrow="Diagnosis mode"
        title="Operational verdict"
        trailing={<Badge tone={tone} uppercase>{label}</Badge>}
      />
      <CardBody>
        <p className="text-lg leading-relaxed tracking-tight text-zinc-100">
          {verdictConcl.statement}
        </p>
      </CardBody>
    </Card>
  );
}

// ===========================================================================
// Condition breakdown — compact tables inside one accordion
// ===========================================================================

function ConditionTable({
  title,
  rows,
  emptyHint,
}: {
  title: string;
  rows: RuntimeConditionResult[];
  emptyHint: string;
}) {
  if (rows.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800/60 bg-zinc-950/30 px-3 py-2">
        <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
          {title}
        </p>
        <p className="mt-1 text-xs text-zinc-600">{emptyHint}</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-800/70 bg-zinc-950/40">
      <p className="border-b border-zinc-800/70 px-3 py-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-zinc-500">
        {title}
      </p>
      <table className="w-full min-w-[520px] text-left text-xs text-zinc-300">
        <thead>
          <tr className="border-b border-zinc-800/60 text-[10px] uppercase tracking-wide text-zinc-500">
            <th className="px-3 py-1.5 font-medium">Tag / key</th>
            <th className="px-3 py-1.5 font-medium">Actual</th>
            <th className="px-3 py-1.5 font-medium">Expected</th>
            <th className="px-3 py-1.5 font-medium">Reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={i}
              className="border-b border-zinc-800/40 last:border-0 hover:bg-zinc-900/50"
            >
              <td className="px-3 py-2 font-mono text-[11px] text-zinc-200">
                {row.snapshot_key ?? row.tag ?? "—"}
              </td>
              <td className="px-3 py-2 font-mono text-[11px]">
                {formatCell(row.actual_value)}
              </td>
              <td className="px-3 py-2 font-mono text-[11px]">
                {formatExpected(row)}
              </td>
              <td className="max-w-[240px] px-3 py-2 text-zinc-400">
                {row.reason ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(v: unknown): string {
  if (v === undefined) return "—";
  if (v === null) return "null";
  if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
  return String(v);
}

function formatExpected(row: RuntimeConditionResult): string {
  if (row.comparison_operator != null && row.comparison_operator !== "") {
    const rhs = formatCell(row.compared_with);
    return `${row.comparison_operator} ${rhs}`;
  }
  if (row.required_value !== undefined && typeof row.required_value === "boolean") {
    return row.required_value ? "TRUE" : "FALSE";
  }
  if (row.required_value !== undefined) {
    return formatCell(row.required_value);
  }
  return "—";
}

export function RuntimeConditionBreakdown({ trace }: { trace: TraceResponse }) {
  const meta = parseRuntimeV2PlatformSpecific(trace.platform_specific);
  if (!meta) return null;

  const hasAny =
    meta.blocking_conditions.length > 0 ||
    meta.satisfied_conditions.length > 0 ||
    meta.missing_conditions.length > 0 ||
    meta.unsupported_conditions.length > 0;

  if (!hasAny) return null;

  return (
    <Accordion
      eyebrow="Runtime evaluation"
      title="Condition breakdown"
    >
      <div className="flex flex-col gap-3">
        <ConditionTable
          title="Blocking"
          rows={meta.blocking_conditions}
          emptyHint="No blocking conditions."
        />
        <ConditionTable
          title="Satisfied"
          rows={meta.satisfied_conditions}
          emptyHint="No satisfied conditions recorded."
        />
        <ConditionTable
          title="Missing"
          rows={meta.missing_conditions}
          emptyHint="No missing snapshot values."
        />
        <ConditionTable
          title="Unsupported"
          rows={meta.unsupported_conditions}
          emptyHint="No unsupported conditions."
        />
      </div>
    </Accordion>
  );
}

// ===========================================================================
// Writer path cards
// ===========================================================================

function pathStatusTone(
  status: string,
): "success" | "danger" | "warning" | "neutral" {
  if (status === "path_satisfied") return "success";
  if (status === "path_blocked") return "danger";
  if (status === "path_incomplete" || status === "path_unsupported") {
    return "warning";
  }
  return "neutral";
}

export function WriterPathResultsSection({ trace }: { trace: TraceResponse }) {
  const meta = parseRuntimeV2PlatformSpecific(trace.platform_specific);
  if (!meta || meta.writer_path_results.length === 0) return null;

  return (
    <Accordion eyebrow="Runtime evaluation" title="Writer paths">
      <ul className="flex flex-col gap-2">
        {meta.writer_path_results.map((p, i) => (
          <WriterPathCard key={`${p.location}-${i}`} path={p} />
        ))}
      </ul>
    </Accordion>
  );
}

function WriterPathCard({ path }: { path: RuntimeWriterPathResult }) {
  const n = path.conditions?.length ?? 0;
  return (
    <li className="rounded-xl border border-zinc-800/70 bg-zinc-900/50 px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={pathStatusTone(String(path.status))} uppercase>
          {path.status}
        </Badge>
        <Badge tone="outline" uppercase>
          {path.write_effect}
        </Badge>
        {path.instruction_type ? (
          <Badge tone="info" uppercase>
            {path.instruction_type}
          </Badge>
        ) : null}
        <span className="text-[10px] text-zinc-500">
          {n} condition{n === 1 ? "" : "s"}
        </span>
      </div>
      <p className="mt-2 font-mono text-xs text-zinc-300">{path.location || "—"}</p>
    </li>
  );
}

// ===========================================================================
// Conflicts
// ===========================================================================

export function RuntimeConflictsBanner({ trace }: { trace: TraceResponse }) {
  const meta = parseRuntimeV2PlatformSpecific(trace.platform_specific);
  if (!meta || meta.conflicts.length === 0) return null;

  return (
    <div className="rounded-xl border border-amber-800/50 bg-amber-950/25 px-4 py-3">
      <p className="text-sm font-medium text-amber-100">
        Conflicting writer paths detected. Final value may depend on execution
        order.
      </p>
      <ul className="mt-2 flex flex-col gap-1.5 text-xs text-amber-200/90">
        {meta.conflicts.map((c, i) => (
          <ConflictLine key={i} conflict={c} />
        ))}
      </ul>
    </div>
  );
}

function ConflictLine({ conflict }: { conflict: RuntimeConflict }) {
  const tw = conflict.true_writer;
  const fw = conflict.false_writer;
  return (
    <li className="rounded border border-amber-900/30 bg-amber-950/20 px-2 py-1.5">
      <span className="text-amber-100/80">
        {tw?.instruction_type ?? "?"} @ {tw?.location ?? "?"}
      </span>
      <span className="mx-1 text-amber-600">vs</span>
      <span className="text-amber-100/80">
        {fw?.instruction_type ?? "?"} @ {fw?.location ?? "?"}
      </span>
    </li>
  );
}
