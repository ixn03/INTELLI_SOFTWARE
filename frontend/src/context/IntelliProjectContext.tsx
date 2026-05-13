"use client";

import axios from "axios";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import type { ControlProject } from "@/types/intelli";

export const INTELLI_API_BASE =
  process.env.NEXT_PUBLIC_INTELLI_API_BASE ?? "http://127.0.0.1:8000";

export const INTELLI_PROJECT_STORAGE_KEY = "intelli_latest_project_id";

export function extractIntelliError(err: unknown, fallback: string): string {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail;
    if (typeof detail === "string" && detail.length > 0) return detail;
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

type IntelliProjectContextValue = {
  apiBase: string;
  project: ControlProject | null;
  projectId: string;
  /** After POST /upload */
  setUploadedProject: (project: ControlProject, projectId: string) => void;
  clearProject: () => void;
  /** Restore from sessionStorage via GET /projects/{id} */
  restoreLatestProject: () => Promise<void>;
};

const IntelliProjectContext = createContext<IntelliProjectContextValue | null>(
  null,
);

export function IntelliProjectProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [project, setProject] = useState<ControlProject | null>(null);
  const [projectId, setProjectId] = useState("");

  const setUploadedProject = useCallback(
    (next: ControlProject, id: string) => {
      setProject(next);
      setProjectId(id);
      if (typeof window !== "undefined" && id) {
        window.sessionStorage.setItem(INTELLI_PROJECT_STORAGE_KEY, id);
      }
    },
    [],
  );

  const clearProject = useCallback(() => {
    setProject(null);
    setProjectId("");
    if (typeof window !== "undefined") {
      window.sessionStorage.removeItem(INTELLI_PROJECT_STORAGE_KEY);
    }
  }, []);

  const restoreLatestProject = useCallback(async () => {
    if (typeof window === "undefined") return;
    const id = window.sessionStorage.getItem(INTELLI_PROJECT_STORAGE_KEY);
    if (!id) return;
    try {
      const res = await axios.get<ControlProject>(
        `${INTELLI_API_BASE}/projects/${encodeURIComponent(id)}`,
      );
      setProject(res.data);
      const stableId =
        (typeof res.data.file_hash === "string" && res.data.file_hash.trim()) ||
        id;
      setProjectId(stableId);
    } catch {
      window.sessionStorage.removeItem(INTELLI_PROJECT_STORAGE_KEY);
    }
  }, []);

  useEffect(() => {
    const id = window.setTimeout(() => {
      void restoreLatestProject();
    }, 0);
    return () => window.clearTimeout(id);
  }, [restoreLatestProject]);

  const value = useMemo(
    () => ({
      apiBase: INTELLI_API_BASE,
      project,
      projectId,
      setUploadedProject,
      clearProject,
      restoreLatestProject,
    }),
    [
      project,
      projectId,
      setUploadedProject,
      clearProject,
      restoreLatestProject,
    ],
  );

  return (
    <IntelliProjectContext.Provider value={value}>
      {children}
    </IntelliProjectContext.Provider>
  );
}

export function useIntelliProject(): IntelliProjectContextValue {
  const ctx = useContext(IntelliProjectContext);
  if (!ctx) {
    throw new Error("useIntelliProject must be used within IntelliProjectProvider");
  }
  return ctx;
}
