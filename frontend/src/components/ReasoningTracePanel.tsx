"use client";

import axios from "axios";
import { useMemo, useState } from "react";

import type {
  NormalizedControlObjectSummary,
  NormalizedSummaryResponse,
  TraceConclusion,
  TraceRelationship,
  TraceV1Response,
} from "@/types/reasoning";

/**
 * Reasoning Trace v1 -- developer / debug panel.
 *
 * Exposes the new normalized-reasoning backend endpoints in the UI:
 *
 *   - GET  /api/normalized-summary  (counts + first 20 objects/rels)
 *   - POST /api/trace-v1            (deterministic Trace v1)
 *
 * Intentionally minimal: no graph visualization, no LLM, no
 * persistence. Designed to ride alongside the existing legacy
 * TracePanel without disturbing it.
 */

const API_BASE = "http://127.0.0.1:8000";

interface Props {
  projectUploaded: boolean;
}

export default function ReasoningTracePanel({ projectUploaded }: Props) {
  const [summary, setSummary] = useState<NormalizedSummaryResponse | null>(
    null,
  );
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const [search, setSearch] = useState("");
  const [targetId, setTargetId] = useState("");

  const [trace, setTrace] = useState<TraceV1Response | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);

  const filteredObjects = useMemo<NormalizedControlObjectSummary[]>(() => {
    if (!summary) return [];
    const q = search.trim().toLowerCase();
    if (!q) return summary.control_objects;
    return summary.control_objects.filter((o) => {
      const haystack = [
        o.id,
        o.name ?? "",
        o.object_type,
        o.source_location ?? "",
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [summary, search]);

  async function loadSummary() {
    if (!projectUploaded) {
      setSummaryError("No project uploaded yet.");
      return;
    }
    setSummaryError(null);
    setSummaryLoading(true);
    try {
      const res = await axios.get<NormalizedSummaryResponse>(
        `${API_BASE}/api/normalized-summary`,
      );
      setSummary(res.data);
    } catch (err) {
      setSummary(null);
      setSummaryError(extractError(err, "Could not load normalized summary"));
    } finally {
      setSummaryLoading(false);
    }
  }

  async function runTrace() {
    if (!projectUploaded) {
      setTraceError("No project uploaded yet.");
      return;
    }
    const id = targetId.trim();
    if (!id) {
      setTraceError("Enter or pick a target_object_id first.");
      return;
    }
    setTraceError(null);
    setTraceLoading(true);
    try {
      const res = await axios.post<TraceV1Response>(
        `${API_BASE}/api/trace-v1`,
        { target_object_id: id },
      );
      setTrace(res.data);
    } catch (err) {
      setTrace(null);
      setTraceError(extractError(err, "Could not trace selected object"));
    } finally {
      setTraceLoading(false);
    }
  }

  return (
    <div className="rounded-2xl border border-zinc-800 bg-zinc-900 p-6">
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-2xl font-bold">Reasoning Trace v1</h2>
        <p className="text-xs uppercase tracking-wider text-zinc-500">
          Developer / debug panel
        </p>
      </div>

      {!projectUploaded && (
        <p className="mb-4 rounded-lg border border-zinc-700 bg-zinc-800/60 px-3 py-2 text-sm text-zinc-300">
          No project uploaded yet. Upload an L5X file first to populate the
          reasoning graph.
        </p>
      )}

      {/* -- Summary controls ------------------------------------------------ */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={loadSummary}
          disabled={!projectUploaded || summaryLoading}
          className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {summaryLoading ? "Loading..." : "Load Normalized Summary"}
        </button>
        {summary && (
          <CountsRow
            controlObjects={summary.control_object_count}
            relationships={summary.relationship_count}
            executionContexts={summary.execution_context_count}
          />
        )}
      </div>

      {summaryError && (
        <p className="mb-4 rounded-lg border border-red-900/80 bg-red-950/50 px-3 py-2 text-sm text-red-200">
          {summaryError}
        </p>
      )}

      {/* -- Two-column body: object picker + trace result ------------------- */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ObjectPickerColumn
          summary={summary}
          filteredObjects={filteredObjects}
          search={search}
          onSearch={setSearch}
          targetId={targetId}
          onTargetIdChange={setTargetId}
          onRunTrace={runTrace}
          traceLoading={traceLoading}
          projectUploaded={projectUploaded}
        />
        <TraceResultColumn
          trace={trace}
          traceLoading={traceLoading}
          traceError={traceError}
        />
      </div>
    </div>
  );
}

// ===========================================================================
// Sub-components
// ===========================================================================

function CountsRow({
  controlObjects,
  relationships,
  executionContexts,
}: {
  controlObjects: number;
  relationships: number;
  executionContexts: number;
}) {
  return (
    <div className="flex flex-wrap gap-2 text-sm">
      <Stat label="control objects" value={controlObjects} />
      <Stat label="relationships" value={relationships} />
      <Stat label="execution contexts" value={executionContexts} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <span className="rounded-lg border border-zinc-700 bg-zinc-800 px-2 py-1">
      <span className="font-mono text-zinc-100">{value}</span>{" "}
      <span className="text-zinc-400">{label}</span>
    </span>
  );
}

function ObjectPickerColumn({
  summary,
  filteredObjects,
  search,
  onSearch,
  targetId,
  onTargetIdChange,
  onRunTrace,
  traceLoading,
  projectUploaded,
}: {
  summary: NormalizedSummaryResponse | null;
  filteredObjects: NormalizedControlObjectSummary[];
  search: string;
  onSearch: (s: string) => void;
  targetId: string;
  onTargetIdChange: (s: string) => void;
  onRunTrace: () => void;
  traceLoading: boolean;
  projectUploaded: boolean;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div>
        <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-zinc-500">
          Filter control objects
        </label>
        <input
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Search id / name / type / location"
          className="w-full rounded-xl border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-zinc-100 placeholder:text-zinc-500"
        />
        {summary && (
          <p className="mt-1 text-xs text-zinc-500">
            Showing {filteredObjects.length} of{" "}
            {summary.control_objects.length}
            {summary.control_objects.length < summary.control_object_count
              ? ` (first ${summary.control_objects.length} of ${summary.control_object_count} total)`
              : ""}
          </p>
        )}
      </div>

      <div className="max-h-72 overflow-auto rounded-xl border border-zinc-800 bg-zinc-950/60">
        {!summary && (
          <p className="px-3 py-4 text-sm text-zinc-500">
            Click <span className="text-zinc-300">Load Normalized Summary</span>{" "}
            to populate this list.
          </p>
        )}
        {summary && filteredObjects.length === 0 && (
          <p className="px-3 py-4 text-sm text-zinc-500">
            No control objects match the current filter.
          </p>
        )}
        {summary && filteredObjects.length > 0 && (
          <ul className="divide-y divide-zinc-800">
            {filteredObjects.map((o) => {
              const active = o.id === targetId;
              return (
                <li key={o.id}>
                  <button
                    type="button"
                    onClick={() => onTargetIdChange(o.id)}
                    className={`block w-full px-3 py-2 text-left transition ${
                      active
                        ? "bg-blue-950/60"
                        : "hover:bg-zinc-800/60"
                    }`}
                  >
                    <div className="flex items-baseline justify-between gap-2">
                      <span className="truncate font-mono text-sm text-zinc-100">
                        {o.id}
                      </span>
                      <span className="shrink-0 rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-zinc-300">
                        {o.object_type}
                      </span>
                    </div>
                    {o.name && (
                      <div className="truncate text-xs text-zinc-300">
                        {o.name}
                      </div>
                    )}
                    {o.source_location && (
                      <div className="truncate text-xs text-zinc-500">
                        {o.source_location}
                      </div>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div>
        <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-zinc-500">
          target_object_id
        </label>
        <div className="flex gap-2">
          <input
            value={targetId}
            onChange={(e) => onTargetIdChange(e.target.value)}
            placeholder="tag::Controller/Program/Tag"
            className="flex-1 rounded-xl border border-zinc-700 bg-zinc-800 px-3 py-2 font-mono text-sm text-zinc-100 placeholder:text-zinc-500"
          />
          <button
            type="button"
            onClick={onRunTrace}
            disabled={!projectUploaded || traceLoading || !targetId.trim()}
            className="rounded-xl bg-blue-600 px-4 py-2 text-sm font-medium hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {traceLoading ? "Tracing..." : "Trace Selected Object"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TraceResultColumn({
  trace,
  traceLoading,
  traceError,
}: {
  trace: TraceV1Response | null;
  traceLoading: boolean;
  traceError: string | null;
}) {
  return (
    <div className="flex min-h-[18rem] flex-col gap-3 rounded-xl border border-zinc-800 bg-zinc-950/40 p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
          Trace v1 result
        </h3>
        {trace?.confidence && <ConfidenceBadge value={trace.confidence} />}
      </div>

      {traceError && (
        <p className="rounded-lg border border-red-900/80 bg-red-950/50 px-3 py-2 text-sm text-red-200">
          {traceError}
        </p>
      )}

      {!trace && !traceError && !traceLoading && (
        <p className="text-sm text-zinc-500">
          Pick a control object and click{" "}
          <span className="text-zinc-300">Trace Selected Object</span> to run a
          deterministic trace.
        </p>
      )}

      {traceLoading && (
        <p className="text-sm text-zinc-400">Running trace...</p>
      )}

      {trace && (
        <>
          <Section title="Target">
            <p className="break-all font-mono text-xs text-zinc-200">
              {trace.target_object_id}
            </p>
          </Section>

          {trace.summary && (
            <Section title="Summary">
              <p className="text-sm text-zinc-200">{trace.summary}</p>
            </Section>
          )}

          <Section title="Counts">
            <div className="flex flex-wrap gap-2 text-xs">
              <Stat
                label="writers"
                value={trace.writer_relationships.length}
              />
              <Stat
                label="readers"
                value={trace.reader_relationships.length}
              />
              <Stat
                label="upstream"
                value={trace.upstream_object_ids.length}
              />
              <Stat
                label="downstream"
                value={trace.downstream_object_ids.length}
              />
            </div>
          </Section>

          {trace.upstream_object_ids.length > 0 && (
            <IdList title="Upstream objects" ids={trace.upstream_object_ids} />
          )}
          {trace.downstream_object_ids.length > 0 && (
            <IdList
              title="Downstream objects"
              ids={trace.downstream_object_ids}
            />
          )}

          {trace.writer_relationships.length > 0 && (
            <RelationshipList
              title="Writer relationships"
              relationships={trace.writer_relationships}
            />
          )}
          {trace.reader_relationships.length > 0 && (
            <RelationshipList
              title="Reader relationships"
              relationships={trace.reader_relationships}
            />
          )}

          {trace.conclusions.length > 0 && (
            <Section title="Conclusions">
              <ul className="flex flex-col gap-2">
                {trace.conclusions.map((c, i) => (
                  <ConclusionRow key={c.id ?? i} conclusion={c} />
                ))}
              </ul>
            </Section>
          )}

          {trace.recommended_checks.length > 0 && (
            <Section title="Recommended checks">
              <ul className="list-disc pl-5 text-sm text-zinc-200">
                {trace.recommended_checks.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </Section>
          )}

          {trace.failure_impact.length > 0 && (
            <Section title="Failure impact">
              <ul className="list-disc pl-5 text-sm text-zinc-200">
                {trace.failure_impact.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </Section>
          )}
        </>
      )}
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-zinc-500">
        {title}
      </div>
      {children}
    </div>
  );
}

function IdList({ title, ids }: { title: string; ids: string[] }) {
  return (
    <Section title={title}>
      <ul className="flex flex-col gap-1">
        {ids.map((id) => (
          <li
            key={id}
            className="break-all rounded border border-zinc-800 bg-zinc-900 px-2 py-1 font-mono text-xs text-zinc-200"
          >
            {id}
          </li>
        ))}
      </ul>
    </Section>
  );
}

function RelationshipList({
  title,
  relationships,
}: {
  title: string;
  relationships: TraceRelationship[];
}) {
  return (
    <Section title={`${title} (${relationships.length})`}>
      <ul className="flex flex-col gap-2">
        {relationships.map((r, i) => (
          <RelationshipRow key={r.id ?? `${r.source_id}->${r.target_id}-${i}`} relationship={r} />
        ))}
      </ul>
    </Section>
  );
}

function RelationshipRow({ relationship }: { relationship: TraceRelationship }) {
  const instr =
    typeof relationship.platform_specific?.instruction_type === "string"
      ? (relationship.platform_specific.instruction_type as string)
      : null;

  return (
    <li className="rounded-lg border border-zinc-800 bg-zinc-900 p-2 text-xs">
      <div className="mb-1 flex flex-wrap items-baseline gap-2">
        <span className="rounded border border-blue-900/70 bg-blue-950/40 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-blue-200">
          {relationship.relationship_type}
        </span>
        {instr && (
          <span className="rounded border border-zinc-700 bg-zinc-800 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-zinc-200">
            {instr}
          </span>
        )}
        {relationship.write_behavior && (
          <span className="rounded border border-amber-900/70 bg-amber-950/40 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-amber-200">
            {relationship.write_behavior}
          </span>
        )}
      </div>
      <KV k="source" v={relationship.source_id} mono />
      <KV k="target" v={relationship.target_id} mono />
      {relationship.source_location && (
        <KV k="location" v={relationship.source_location} mono />
      )}
      {relationship.logic_condition && (
        <KV k="condition" v={relationship.logic_condition} />
      )}
    </li>
  );
}

function KV({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <div className="flex gap-2">
      <span className="shrink-0 text-zinc-500">{k}</span>
      <span
        className={`break-all text-zinc-200 ${mono ? "font-mono" : ""}`}
      >
        {v}
      </span>
    </div>
  );
}

function ConclusionRow({ conclusion }: { conclusion: TraceConclusion }) {
  return (
    <li className="rounded-lg border border-zinc-800 bg-zinc-900 p-2 text-sm">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-zinc-100">{conclusion.statement}</p>
        {conclusion.confidence && (
          <ConfidenceBadge value={conclusion.confidence} />
        )}
      </div>
      {conclusion.recommended_checks &&
        conclusion.recommended_checks.length > 0 && (
          <ul className="mt-2 list-disc pl-5 text-xs text-zinc-400">
            {conclusion.recommended_checks.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        )}
    </li>
  );
}

function ConfidenceBadge({ value }: { value: string }) {
  const tone =
    value === "high" || value === "very_high"
      ? "border-emerald-900/70 bg-emerald-950/40 text-emerald-200"
      : value === "medium"
        ? "border-amber-900/70 bg-amber-950/40 text-amber-200"
        : value === "unknown"
          ? "border-zinc-700 bg-zinc-800 text-zinc-300"
          : "border-red-900/70 bg-red-950/40 text-red-200";
  return (
    <span
      className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${tone}`}
    >
      conf: {value}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function extractError(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string" && detail.length > 0) {
      return detail;
    }
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
