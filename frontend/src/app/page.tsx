"use client";

import axios from "axios";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import {
  extractIntelliError,
  INTELLI_API_BASE,
  INTELLI_PROJECT_STORAGE_KEY,
} from "@/context/IntelliProjectContext";
import type { ControlProject } from "@/types/intelli";
import type { NormalizedSummaryResponse } from "@/types/reasoning";

import { Button, InlineError, LoadingLine } from "@/components/intelli/ui";

interface UploadResponse {
  project_id: string;
  connector: string;
  project: ControlProject;
  graph: Record<string, number>;
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

  async function upload() {
    setError(null);
    if (!file) {
      setError("Choose an L5X file first.");
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
      window.sessionStorage.setItem(INTELLI_PROJECT_STORAGE_KEY, res.data.project_id);
      router.push("/workspace");
    } catch (err) {
      setError(extractIntelliError(err, "Upload failed"));
    } finally {
      setUploading(false);
    }
  }

  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-16 text-zinc-100">
      <div className="mx-auto max-w-3xl">
        <p className="text-[11px] uppercase tracking-[0.22em] text-zinc-500">
          INTELLI
        </p>
        <h1 className="mt-3 text-4xl font-semibold tracking-tight text-zinc-50">
          Deterministic controls logic intelligence
        </h1>
        <p className="mt-4 max-w-xl text-base leading-relaxed text-zinc-400">
          Upload Rockwell L5X exports, trace logic, evaluate runtime snapshots,
          and review evidence-backed answers — without an LLM and without a
          live PLC connection.
        </p>

        <div className="mt-12 grid gap-8 lg:grid-cols-2">
          <section className="rounded-2xl border border-zinc-800/80 bg-zinc-900/40 p-6">
            <h2 className="text-sm font-medium text-zinc-100">Upload project</h2>
            <p className="mt-1 text-xs text-zinc-500">
              Rockwell L5X export. Stored in memory on the dev backend only.
            </p>
            <label className="mt-4 block cursor-pointer rounded-xl border border-dashed border-zinc-700/80 bg-zinc-950/40 px-4 py-8 text-center transition hover:border-zinc-600">
              <input
                type="file"
                accept=".l5x,.L5X,application/xml,text/xml"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                className="sr-only"
              />
              <p className="text-sm text-zinc-200">
                {file ? file.name : "Click to select an L5X file"}
              </p>
            </label>
            <Button
              tone="primary"
              className="mt-4 w-full"
              onClick={() => void upload()}
              disabled={uploading || !file}
            >
              {uploading ? "Uploading…" : "Upload"}
            </Button>
            {error ? <InlineError>{error}</InlineError> : null}
          </section>

          <section className="flex flex-col justify-between rounded-2xl border border-zinc-800/80 bg-zinc-900/30 p-6">
            <div>
              <h2 className="text-sm font-medium text-zinc-100">
                Recent / current project
              </h2>
              {countsLoading ? (
                <div className="mt-4">
                  <LoadingLine>Checking backend…</LoadingLine>
                </div>
              ) : counts ? (
                <dl className="mt-4 space-y-2 text-sm text-zinc-300">
                  <div className="flex justify-between gap-4">
                    <dt className="text-zinc-500">Control objects</dt>
                    <dd>{counts.control_object_count}</dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt className="text-zinc-500">Relationships</dt>
                    <dd>{counts.relationship_count}</dd>
                  </div>
                </dl>
              ) : (
                <p className="mt-4 text-sm text-zinc-500">
                  No project in this browser session yet. Upload an L5X or open
                  the workspace to attach to the backend&apos;s latest upload.
                </p>
              )}
            </div>
            <Link
              href="/workspace"
              className="mt-8 inline-flex w-full items-center justify-center rounded-lg border border-zinc-600 bg-zinc-100 px-4 py-3 text-sm font-medium text-zinc-900 transition hover:bg-white"
            >
              Open workspace
            </Link>
          </section>
        </div>

        <p className="mt-12 text-center text-[11px] text-zinc-600">
          INTELLI · industrial UI · evidence first
        </p>
      </div>
    </main>
  );
}
