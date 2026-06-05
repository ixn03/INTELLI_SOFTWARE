"use client";

import axios from "axios";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  extractIntelliError,
  INTELLI_API_BASE,
  INTELLI_PROJECT_STORAGE_KEY,
} from "@/context/IntelliProjectContext";
import type { ControlProject } from "@/types/intelli";
import type { NormalizedSummaryResponse } from "@/types/reasoning";

import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  InlineError,
  LoadingLine,
} from "@/components/intelli/ui";

interface UploadResponse {
  project_id: string;
  connector: string;
  project: ControlProject;
  graph: Record<string, number>;
}

const capabilityCards = [
  {
    eyebrow: "Ingest",
    title: "Parse the plant reality",
    body: "Rockwell L5X today, with Siemens TIA, DeltaV FHX, and Honeywell preservation paths already shaped around the same model.",
  },
  {
    eyebrow: "Reason",
    title: "Build evidence before language",
    body: "Normalize tags, routines, relationships, conditions, runtime values, and trust signals before any AI wording touches the answer.",
  },
  {
    eyebrow: "Diagnose",
    title: "Move from viewer to root cause",
    body: "Trace writers, readers, sequence state, runtime snapshots, and unsupported gaps so engineers know what INTELLI proved and what it did not.",
  },
];

const platformRows = [
  ["Rockwell Studio 5000", "L5X parser", "Useful"],
  ["Siemens TIA", "XML foundation", "Building"],
  ["Emerson DeltaV", "FHX foundation", "Building"],
  ["Honeywell Experion", "Preservation layer", "Building"],
];

function formatNumber(value: number | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "-";
  return Intl.NumberFormat("en-US").format(value);
}

export default function HomePage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [counts, setCounts] = useState<NormalizedSummaryResponse | null>(null);
  const [countsLoading, setCountsLoading] = useState(false);

  const refreshCounts = useCallback(async () => {
    if (typeof window === "undefined") return;
    const id = window.sessionStorage.getItem(INTELLI_PROJECT_STORAGE_KEY);
    if (!id) {
      setCounts(null);
      return;
    }
    setCountsLoading(true);
    try {
      const res = await axios.get<NormalizedSummaryResponse>(
        `${INTELLI_API_BASE}/api/normalized-summary`,
        { params: { limit: 1, offset: 0, rel_limit: 1, rel_offset: 0 } },
      );
      setCounts(res.data);
    } catch {
      setCounts(null);
    } finally {
      setCountsLoading(false);
    }
  }, []);

  useEffect(() => {
    const id = window.setTimeout(() => {
      void refreshCounts();
    }, 0);
    return () => window.clearTimeout(id);
  }, [refreshCounts]);

  const graphStatus = useMemo(() => {
    if (!counts) return "No active graph";
    return `${formatNumber(counts.control_object_count)} objects / ${formatNumber(
      counts.relationship_count,
    )} relationships`;
  }, [counts]);

  async function upload() {
    setError(null);
    if (!file) {
      setError("Choose a supported control export first.");
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    setUploading(true);
    try {
      const res = await axios.post<UploadResponse>(
        `${INTELLI_API_BASE}/upload`,
        formData,
      );
      window.sessionStorage.setItem(
        INTELLI_PROJECT_STORAGE_KEY,
        res.data.project_id,
      );
      router.push("/workspace");
    } catch (err) {
      setError(extractIntelliError(err, "Upload failed"));
    } finally {
      setUploading(false);
    }
  }

  return (
    <main className="relative min-h-screen overflow-hidden bg-[radial-gradient(circle_at_top_left,#0f2a3f_0,#08111f_35%,#030712_75%)] text-zinc-100">
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(148,163,184,0.035)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,0.035)_1px,transparent_1px)] bg-[size:44px_44px]" />
      <div className="pointer-events-none absolute -right-40 top-24 h-96 w-96 rounded-full bg-cyan-500/10 blur-3xl" />
      <div className="pointer-events-none absolute bottom-0 left-1/4 h-80 w-80 rounded-full bg-violet-500/10 blur-3xl" />

      <div className="relative mx-auto flex min-h-screen max-w-7xl flex-col px-6 py-6 lg:px-8">
        <header className="flex items-center justify-between gap-4 rounded-2xl border border-white/10 bg-zinc-950/55 px-4 py-3 shadow-2xl shadow-black/20 backdrop-blur">
          <Link href="/" className="flex items-center gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl border border-cyan-400/30 bg-cyan-400/10 font-mono text-sm font-semibold text-cyan-100">
              IN
            </span>
            <span>
              <span className="block text-sm font-semibold tracking-tight text-white">
                INTELLI
              </span>
              <span className="block text-[10px] uppercase tracking-[0.24em] text-zinc-500">
                Controls intelligence
              </span>
            </span>
          </Link>
          <nav className="hidden items-center gap-6 text-xs font-medium uppercase tracking-[0.16em] text-zinc-500 md:flex">
            <a href="#platform" className="transition hover:text-zinc-200">
              Platform
            </a>
            <a href="#workflow" className="transition hover:text-zinc-200">
              Workflow
            </a>
            <a href="#workspace" className="transition hover:text-zinc-200">
              Workspace
            </a>
          </nav>
          <Link
            href="/workspace"
            className="rounded-xl border border-zinc-700/80 bg-zinc-100 px-4 py-2 text-sm font-semibold text-zinc-950 transition hover:bg-white"
          >
            Open workspace
          </Link>
        </header>

        <section className="grid flex-1 items-center gap-10 py-16 lg:grid-cols-[1.08fr_0.92fr] lg:py-20">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-400/25 bg-cyan-400/10 px-3 py-1 text-xs font-medium text-cyan-100">
              <span className="h-1.5 w-1.5 rounded-full bg-cyan-300" />
              Deterministic reasoning first. AI wording last.
            </div>
            <h1 className="mt-7 max-w-4xl text-5xl font-semibold tracking-[-0.05em] text-white md:text-7xl">
              Industrial controls intelligence that can prove its answer.
            </h1>
            <p className="mt-6 max-w-2xl text-lg leading-8 text-zinc-300">
              INTELLI is being built as the reasoning layer for PLC and DCS
              troubleshooting: parse control exports, normalize vendor logic,
              trace cause and effect, evaluate runtime snapshots, and show the
              evidence behind every conclusion.
            </p>
            <div className="mt-9 flex flex-col gap-3 sm:flex-row">
              <Link
                href="/workspace"
                className="inline-flex items-center justify-center rounded-xl bg-cyan-300 px-5 py-3 text-sm font-semibold text-cyan-950 shadow-lg shadow-cyan-950/30 transition hover:bg-cyan-200"
              >
                Launch command workspace
              </Link>
              <a
                href="#workspace"
                className="inline-flex items-center justify-center rounded-xl border border-white/10 bg-white/5 px-5 py-3 text-sm font-semibold text-zinc-100 transition hover:bg-white/10"
              >
                Upload a project
              </a>
            </div>
            <div className="mt-10 grid max-w-2xl grid-cols-3 gap-3">
              <HeroMetric value="4" label="vendor paths" />
              <HeroMetric value="0" label="guessed edges" />
              <HeroMetric value="v2" label="trace engine" />
            </div>
          </div>

          <section id="workspace" className="rounded-[2rem] border border-white/10 bg-zinc-950/70 p-3 shadow-2xl shadow-black/40 backdrop-blur">
            <div className="rounded-[1.5rem] border border-zinc-800/80 bg-[linear-gradient(180deg,rgba(24,24,27,0.96),rgba(9,9,11,0.96))] p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.24em] text-cyan-300">
                    Import console
                  </p>
                  <h2 className="mt-1 text-xl font-semibold tracking-tight text-white">
                    Start with a control export
                  </h2>
                </div>
                <Badge tone={counts ? "success" : "outline"} uppercase>
                  {counts ? "graph loaded" : "standby"}
                </Badge>
              </div>

              <label className="mt-6 block cursor-pointer rounded-2xl border border-dashed border-cyan-400/30 bg-cyan-400/[0.04] px-5 py-8 text-center transition hover:border-cyan-300/60 hover:bg-cyan-400/[0.07]">
                <input
                  type="file"
                  accept=".l5x,.L5X,.xml,.XML,.fhx,.FHX,.scl,.SCL,.txt,.csv,.cl,.hwl,.hwh,.hsc,.epr,application/xml,text/xml,text/plain"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  className="sr-only"
                />
                <p className="text-sm font-medium text-zinc-100">
                  {file ? file.name : "Click to select a PLC or DCS export"}
                </p>
                <p className="mt-2 text-xs leading-relaxed text-zinc-500">
                  L5X, Siemens XML, DeltaV FHX, Honeywell text/XML foundations.
                </p>
              </label>

              <Button
                tone="primary"
                className="mt-4 w-full bg-cyan-300 text-cyan-950 hover:bg-cyan-200"
                onClick={() => void upload()}
                disabled={uploading || !file}
              >
                {uploading ? "Uploading..." : "Analyze in workspace"}
              </Button>
              {error ? <div className="mt-3"><InlineError>{error}</InlineError></div> : null}

              <div className="mt-5 grid gap-3 sm:grid-cols-2">
                <StatusTile
                  label="Session graph"
                  value={countsLoading ? "Checking..." : graphStatus}
                  tone={counts ? "good" : "neutral"}
                />
                <StatusTile
                  label="API target"
                  value={INTELLI_API_BASE}
                  tone="neutral"
                />
              </div>

              {countsLoading ? (
                <div className="mt-4">
                  <LoadingLine>Checking current backend graph...</LoadingLine>
                </div>
              ) : null}
            </div>
          </section>
        </section>

        <section id="workflow" className="grid gap-4 py-4 lg:grid-cols-3">
          {capabilityCards.map((card) => (
            <Card key={card.title} className="bg-zinc-950/55 backdrop-blur">
              <CardHeader eyebrow={card.eyebrow} title={card.title} />
              <CardBody>
                <p className="text-sm leading-6 text-zinc-400">{card.body}</p>
              </CardBody>
            </Card>
          ))}
        </section>

        <section id="platform" className="grid gap-4 py-8 lg:grid-cols-[0.95fr_1.05fr]">
          <Card className="bg-zinc-950/55 backdrop-blur">
            <CardHeader
              eyebrow="Moat"
              title="Not just a PLC file viewer"
            />
            <CardBody className="space-y-4">
              <p className="text-sm leading-6 text-zinc-300">
                The product direction is a vendor-neutral reasoning system:
                one universal model, deterministic evidence, traceable runtime
                diagnosis, and explicit confidence when parser coverage is
                limited.
              </p>
              <div className="flex flex-wrap gap-2">
                <Badge tone="info">universal model</Badge>
                <Badge tone="success">evidence graph</Badge>
                <Badge tone="warning">runtime snapshots</Badge>
                <Badge tone="outline">multi-vendor</Badge>
              </div>
            </CardBody>
          </Card>

          <Card className="bg-zinc-950/55 backdrop-blur">
            <CardHeader eyebrow="Platform support" title="Connector posture" />
            <CardBody>
              <div className="overflow-hidden rounded-xl border border-zinc-800/80">
                <table className="w-full text-left text-sm">
                  <thead className="bg-zinc-900/70 text-[10px] uppercase tracking-[0.18em] text-zinc-500">
                    <tr>
                      <th className="px-4 py-3 font-semibold">System</th>
                      <th className="px-4 py-3 font-semibold">Current path</th>
                      <th className="px-4 py-3 font-semibold">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-800/80">
                    {platformRows.map(([system, path, status]) => (
                      <tr key={system} className="bg-zinc-950/35">
                        <td className="px-4 py-3 text-zinc-100">{system}</td>
                        <td className="px-4 py-3 text-zinc-400">{path}</td>
                        <td className="px-4 py-3">
                          <Badge tone={status === "Useful" ? "success" : "warning"}>
                            {status}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardBody>
          </Card>
        </section>
      </div>
    </main>
  );
}

function HeroMetric({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] px-4 py-3">
      <p className="font-mono text-2xl font-semibold text-white">{value}</p>
      <p className="mt-1 text-[10px] uppercase tracking-[0.18em] text-zinc-500">
        {label}
      </p>
    </div>
  );
}

function StatusTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "good" | "neutral";
}) {
  return (
    <div className="rounded-xl border border-zinc-800/80 bg-zinc-950/55 px-3 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-zinc-500">
        {label}
      </p>
      <p
        className={`mt-1 truncate text-sm ${
          tone === "good" ? "text-emerald-200" : "text-zinc-200"
        }`}
        title={value}
      >
        {value}
      </p>
    </div>
  );
}
