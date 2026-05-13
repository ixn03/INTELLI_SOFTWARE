"use client";

import axios from "axios";
import { useCallback, useEffect, useState } from "react";

import {
  extractIntelliError,
  useIntelliProject,
} from "@/context/IntelliProjectContext";
import { Button, Eyebrow, InlineError, LoadingLine } from "@/components/intelli/ui";

type SequenceSummaryPayload = {
  project_id?: string;
  state_candidates: Array<Record<string, unknown>>;
  state_transitions: Array<Record<string, unknown>>;
  case_branches: Array<Record<string, unknown>>;
  sequence_summary: string[];
  unsupported_sequence_patterns: Array<Record<string, unknown>>;
};

function normalizeSequenceSummary(raw: unknown): SequenceSummaryPayload {
  const d =
    raw && typeof raw === "object"
      ? (raw as Record<string, unknown>)
      : {};
  const arr = (k: string) =>
    Array.isArray(d[k]) ? (d[k] as Array<Record<string, unknown>>) : [];
  const strArr = (k: string) =>
    Array.isArray(d[k])
      ? (d[k] as unknown[]).filter((x): x is string => typeof x === "string")
      : [];
  return {
    project_id: typeof d.project_id === "string" ? d.project_id : undefined,
    state_candidates: arr("state_candidates"),
    state_transitions: arr("state_transitions"),
    case_branches: arr("case_branches"),
    sequence_summary: strArr("sequence_summary"),
    unsupported_sequence_patterns: arr("unsupported_sequence_patterns"),
  };
}

export default function SequencePage() {
  const { project, apiBase } = useIntelliProject();
  const [data, setData] = useState<SequenceSummaryPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showRaw, setShowRaw] = useState(false);

  const load = useCallback(async () => {
    if (!project) {
      setData(null);
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    const directBase = apiBase.replace(/\/$/, "");
    const directUrls = [
      `${directBase}/api/sequence-summary`,
      `${directBase}/sequence-summary`,
    ];
    try {
      let res: { data: unknown } | undefined;
      try {
        res = await axios.get("/api/intelli/sequence-summary");
      } catch (proxyErr) {
        if (!axios.isAxiosError(proxyErr)) throw proxyErr;
        const st = proxyErr.response?.status;
        if (st !== 404 && st !== 502 && st !== 503) throw proxyErr;
        let last: unknown = proxyErr;
        for (const url of directUrls) {
          try {
            res = await axios.get(url);
            last = null;
            break;
          } catch (e) {
            last = e;
          }
        }
        if (!res) throw last;
      }
      if (!res) {
        throw new Error("Sequence summary response missing.");
      }
      setData(normalizeSequenceSummary(res.data));
    } catch (err) {
      setData(null);
      let msg = extractIntelliError(err, "Could not load sequence summary");
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        msg =
          "Sequence reasoning returned Not Found (404). Start the FastAPI app from this repo (uvicorn) so GET /api/sequence-summary is registered, upload a project first, and set NEXT_PUBLIC_INTELLI_API_BASE to your API URL (e.g. http://127.0.0.1:8000).";
      }
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [project, apiBase]);

  useEffect(() => {
    const id = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(id);
  }, [load]);

  if (!project) {
    return (
      <div className="p-10 text-sm text-zinc-500">
        Load a project from the engineering workspace or home page first.
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto px-8 py-10">
      <header className="mb-10 flex flex-wrap items-end justify-between gap-4">
        <div>
          <Eyebrow>Sequence / State</Eyebrow>
          <h1 className="mt-2 text-2xl font-semibold tracking-tight text-zinc-50">
            Reasoning v1
          </h1>
          <p className="mt-2 max-w-xl text-sm text-zinc-400">
            Deterministic detection of state-like tags, writes, transitions, and
            ST CASE branches. Conservative confidence; no source-state inference.
          </p>
        </div>
        <Button tone="secondary" onClick={() => void load()} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </Button>
      </header>

      {loading && !data ? <LoadingLine>Loading…</LoadingLine> : null}
      {error ? <InlineError>{error}</InlineError> : null}

      {data ? (
        <div className="flex max-w-4xl flex-col gap-10">
          <section>
            <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Summary
            </h2>
            {data.sequence_summary.length === 0 ? (
              <p className="mt-2 text-sm text-zinc-500">No sequence lines yet.</p>
            ) : (
              <ul className="mt-3 list-inside list-disc space-y-2 text-sm text-zinc-300">
                {data.sequence_summary.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
              State candidates ({data.state_candidates.length})
            </h2>
            <ul className="mt-3 space-y-2 text-sm text-zinc-300">
              {data.state_candidates.map((c) => (
                <li
                  key={String(c.tag_id)}
                  className="rounded-lg border border-zinc-800/80 bg-zinc-900/30 px-3 py-2"
                >
                  <span className="font-medium text-zinc-100">
                    {String(c.tag_name ?? c.tag_id)}
                  </span>
                  <span className="ml-2 text-xs text-zinc-500">
                    {String(c.confidence ?? "")}
                  </span>
                  <p className="mt-1 text-xs text-zinc-400">{String(c.reason ?? "")}</p>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Transitions ({data.state_transitions.length})
            </h2>
            <ul className="mt-3 space-y-2 text-sm text-zinc-300">
              {data.state_transitions.map((t, i) => (
                <li
                  key={`${String(t.state_tag)}-${i}`}
                  className="rounded-lg border border-zinc-800/80 bg-zinc-900/30 px-3 py-2"
                >
                  <p className="text-zinc-100">
                    <span className="text-zinc-500">→</span>{" "}
                    {String(t.state_tag_name ?? t.state_tag)} :={" "}
                    <code className="text-emerald-200/90">
                      {String(t.target_state)}
                    </code>
                  </p>
                  {t.condition_summary ? (
                    <p className="mt-1 text-xs text-zinc-400">
                      {String(t.condition_summary)}
                    </p>
                  ) : null}
                  <p className="mt-1 font-mono text-[10px] text-zinc-600">
                    {String(t.source_location ?? "")}
                  </p>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
              CASE branches ({data.case_branches.length})
            </h2>
            <ul className="mt-3 space-y-2 text-sm text-zinc-300">
              {data.case_branches.map((b, i) => (
                <li
                  key={`${String(b.case_condition_summary)}-${i}`}
                  className="rounded-lg border border-zinc-800/80 bg-zinc-900/30 px-3 py-2 font-mono text-xs text-zinc-400"
                >
                  {String(b.case_condition_summary ?? "")}
                </li>
              ))}
            </ul>
          </section>

          <section>
            <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-zinc-500">
              Unsupported patterns ({data.unsupported_sequence_patterns.length})
            </h2>
            <ul className="mt-3 space-y-2 text-sm text-amber-200/80">
              {data.unsupported_sequence_patterns.map((u, i) => (
                <li key={i}>{String(u.detail ?? u.kind ?? "")}</li>
              ))}
            </ul>
          </section>

          <div>
            <button
              type="button"
              onClick={() => setShowRaw((v) => !v)}
              className="text-xs text-zinc-500 underline-offset-4 hover:text-zinc-300 hover:underline"
            >
              {showRaw ? "Hide" : "Show"} raw JSON
            </button>
            {showRaw ? (
              <pre className="mt-3 max-h-80 overflow-auto rounded-lg border border-zinc-800 bg-zinc-950 p-3 text-[11px] text-zinc-400">
                {JSON.stringify(data, null, 2)}
              </pre>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
