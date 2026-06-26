import type { SignalTroubleshootingWorkspace } from "@/types/reasoning";

export interface SignalLiveValue {
  signal_id: string;
  signal_name: string | null;
  canonical_name?: string | null;
  value: unknown;
  timestamp?: string | null;
  quality?: string | null;
  source?: string | null;
  stale?: boolean;
}

export type LiveValueIndex = Map<string, SignalLiveValue>;

function asLiveValue(raw: unknown): SignalLiveValue | null {
  if (!raw || typeof raw !== "object") return null;
  const v = raw as Partial<SignalLiveValue>;
  if (!v.signal_id && !v.signal_name && !v.canonical_name) return null;
  return {
    signal_id: v.signal_id ?? "",
    signal_name: v.signal_name ?? null,
    canonical_name: v.canonical_name ?? null,
    value: v.value,
    timestamp: v.timestamp ?? null,
    quality: v.quality ?? null,
    source: v.source ?? null,
    stale: Boolean(v.stale),
  };
}

function indexLiveValue(index: LiveValueIndex, value: SignalLiveValue): void {
  if (value.signal_id) index.set(value.signal_id, value);
  if (value.signal_name) index.set(value.signal_name, value);
  if (value.canonical_name) index.set(value.canonical_name, value);
}

export function buildLiveValueIndex(
  workspace: SignalTroubleshootingWorkspace,
): LiveValueIndex {
  const index: LiveValueIndex = new Map();
  const state = workspace.current_state_explanation;
  if (!state) return index;

  const candidates = [
    state.target_current_value,
    ...(state.upstream_condition_current_values ?? []),
    ...(state.blocking_conditions ?? []),
    ...(state.satisfied_conditions ?? []),
  ];

  for (const raw of candidates) {
    const parsed = asLiveValue(raw);
    if (parsed) indexLiveValue(index, parsed);
  }
  return index;
}

export function lookupLiveValue(
  index: LiveValueIndex,
  keys: Array<string | null | undefined>,
): SignalLiveValue | undefined {
  for (const key of keys) {
    if (key && index.has(key)) return index.get(key);
  }
  return undefined;
}

export function formatLiveValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "TRUE" : "FALSE";
  return String(value);
}

export function liveValueBadgeTone(
  live: SignalLiveValue,
): "success" | "warning" | "danger" | "info" | "neutral" {
  if (live.stale || live.quality === "stale") return "warning";
  if (live.quality === "bad") return "danger";
  if (typeof live.value === "boolean") return live.value ? "success" : "warning";
  return "info";
}
