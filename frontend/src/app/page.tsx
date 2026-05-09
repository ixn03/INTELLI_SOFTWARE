"use client";

import axios from "axios";
import { useState } from "react";

import ProjectExplorer from "@/components/ProjectExplorer";
import RoutineViewer from "@/components/RoutineViewer";
import TracePanel from "@/components/TracePanel";

import {
  ControlProject,
  ControlRoutine,
} from "@/types/intelli";

const UPLOAD_URL = "http://127.0.0.1:8000/upload";

interface UploadResponse {
  project_id: string;
  connector: string;
  project: ControlProject;
  graph: Record<string, number>;
}

export default function HomePage() {
  const [file, setFile] = useState<File | null>(null);
  const [project, setProject] = useState<ControlProject | null>(null);
  const [projectId, setProjectId] = useState("");
  const [selectedRoutine, setSelectedRoutine] =
    useState<ControlRoutine | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  async function uploadFile() {
    setUploadError(null);

    if (!file) {
      setUploadError("Choose an L5X file first (use the file picker above).");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);

    try {
      setLoading(true);
      setProject(null);
      setProjectId("");
      setSelectedRoutine(null);

      const res = await axios.post<UploadResponse>(UPLOAD_URL, formData);

      setProject(res.data.project);
      setProjectId(res.data.project_id);
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const detail = err.response?.data?.detail;
        const msg =
          typeof detail === "string"
            ? detail
            : detail != null
              ? JSON.stringify(detail)
              : err.message;
        setUploadError(msg || "Upload failed");
      } else {
        setUploadError("Upload failed");
      }
      console.error(err);
    } finally {
      setLoading(false);
    }
  }

  function resetUpload() {
    setProject(null);
    setProjectId("");
    setSelectedRoutine(null);
    setFile(null);
    setUploadError(null);
  }

  if (project) {
    return (
      <main className="min-h-screen bg-zinc-950 p-6 text-white">
        <div className="mx-auto mb-6 flex max-w-[1600px] flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500">
              INTELLI
            </p>
            <h1 className="text-2xl font-bold">{project.project_name}</h1>
          </div>
          <button
            type="button"
            onClick={resetUpload}
            className="rounded-lg border border-zinc-600 px-4 py-2 text-sm text-zinc-200 hover:bg-zinc-800"
          >
            Upload another file
          </button>
        </div>

        <div className="mx-auto grid max-w-[1600px] min-h-[calc(100vh-8rem)] grid-cols-1 gap-4 lg:grid-cols-3 lg:gap-6">
          <div className="min-h-[280px] lg:min-h-0">
            <ProjectExplorer
              project={project}
              selectedRoutine={selectedRoutine}
              onSelectRoutine={setSelectedRoutine}
            />
          </div>
          <div className="min-h-[280px] lg:min-h-0">
            <RoutineViewer routine={selectedRoutine} />
          </div>
          <div className="min-h-[280px] lg:min-h-0">
            <TracePanel projectId={projectId} />
          </div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-zinc-950 p-8 text-white">
      <div className="mx-auto max-w-5xl">
        <h1 className="mb-2 text-5xl font-bold">INTELLI</h1>

        <p className="mb-10 text-zinc-400">
          Industrial Logic Intelligence Platform
        </p>

        <div className="mb-8 rounded-2xl border border-zinc-800 bg-zinc-900 p-6">
          <h2 className="mb-4 text-2xl font-semibold">Upload Control Project</h2>

          <div className="mb-6 rounded-xl border-2 border-dashed border-zinc-600 bg-zinc-800/90 p-6 ring-1 ring-zinc-700/80">
            <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-zinc-500">
              Step 1 — Choose file
            </p>
            <p className="mb-4 text-lg font-medium text-zinc-100">
              Select an L5X export from Studio 5000
            </p>
            <label className="block cursor-pointer">
              <span className="sr-only">L5X file</span>
              <input
                type="file"
                accept=".l5x,.L5X,application/xml,text/xml"
                onChange={(e) => {
                  const next = e.target.files?.[0] ?? null;
                  setFile(next);
                  setUploadError(null);
                }}
                className="block w-full cursor-pointer text-sm text-zinc-300 file:mr-4 file:cursor-pointer file:rounded-lg file:border-0 file:bg-zinc-600 file:px-5 file:py-3 file:text-sm file:font-semibold file:text-white hover:file:bg-zinc-500"
              />
            </label>
            <p className="mt-3 text-sm text-zinc-500">
              Accepted:{" "}
              <span className="text-zinc-400">.l5x</span> (Rockwell Logix
              Designer export)
            </p>
            {file && (
              <div className="mt-4 rounded-lg border border-zinc-600 bg-zinc-900/80 px-4 py-3">
                <p className="text-xs font-medium uppercase tracking-wide text-zinc-500">
                  Ready to upload
                </p>
                <p className="mt-1 truncate font-mono text-sm text-zinc-200">
                  {file.name}
                </p>
              </div>
            )}
          </div>

          <button
            type="button"
            onClick={uploadFile}
            disabled={loading}
            className="rounded-xl bg-blue-600 px-5 py-3 font-medium hover:bg-blue-500 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? "Uploading..." : "Upload L5X"}
          </button>

          {uploadError && (
            <p className="mt-4 rounded-lg border border-red-900/80 bg-red-950/50 px-3 py-2 text-sm text-red-200">
              {uploadError}
            </p>
          )}
        </div>
      </div>
    </main>
  );
}
