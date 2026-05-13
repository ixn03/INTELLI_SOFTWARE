/**
 * TypeScript mirrors of the backend reasoning schema
 * (`backend/app/models/reasoning.py`) -- only the fields the
 * INTELLI frontend currently consumes.
 *
 * The backend serializes string-enums as their string value, so all
 * enum-typed fields are typed as string unions (or `string` where the
 * surface is too wide to enumerate usefully -- e.g.
 * ``relationship_type``).
 *
 * Endpoints currently consumed:
 *   - GET  /api/normalized-summary  -> NormalizedSummaryResponse
 *   - GET  /api/control-objects       -> ControlObjectsPageResponse
 *   - GET  /api/sequence-summary      -> (dict, sequence reasoning v1)
 *   - POST /api/trace-v1            -> TraceResponse
 *   - POST /api/trace-v2            -> TraceResponse (NL-augmented)
 *   - POST /api/ask-v1              -> AskResponse  (router metadata)
 *   - POST /api/ask-v2              -> AskResponse  (orchestration + optional runtime)
 *   - POST /api/evaluate-runtime-v2 -> TraceResponse (runtime v2 overlay)
 */

export type ConfidenceLevel =
  | "very_low"
  | "low"
  | "medium"
  | "high"
  | "very_high"
  | "unknown";

// ---------------------------------------------------------------------------
// GET /api/normalized-summary
// ---------------------------------------------------------------------------

export interface NormalizedControlObjectSummary {
  id: string;
  name: string | null;
  object_type: string;
  source_location: string | null;
}

export interface NormalizedRelationshipSummary {
  source_id: string;
  target_id: string;
  relationship_type: string;
  source_location: string | null;
  platform_specific: Record<string, unknown>;
}

export interface NormalizedSummaryResponse {
  project_id: string | null;
  /** Total objects in project (unfiltered). */
  control_object_count: number;
  /** Objects matching current search / type filter. */
  total_control_object_count: number;
  returned_control_object_count: number;
  offset: number;
  limit: number;
  relationship_count: number;
  total_relationship_count: number;
  returned_relationship_count: number;
  rel_offset: number;
  rel_limit: number;
  execution_context_count: number;
  control_objects: NormalizedControlObjectSummary[];
  relationships: NormalizedRelationshipSummary[];
}

/** GET /api/control-objects */
export interface ControlObjectsPageResponse {
  project_id: string;
  project_control_object_count: number;
  total_control_object_count: number;
  returned_control_object_count: number;
  offset: number;
  limit: number;
  control_objects: NormalizedControlObjectSummary[];
}

// ---------------------------------------------------------------------------
// Trace v1 / v2 shared shape
// ---------------------------------------------------------------------------

/**
 * Subset of the backend ``Relationship`` model surfaced in the UI.
 * The backend may include additional fields (evidence, conflict
 * flags, timing_behavior, ...) -- we leave them off the type until
 * the UI actually renders them.
 */
export interface TraceRelationship {
  id?: string;
  source_id: string;
  target_id: string;
  relationship_type: string;
  source_location?: string | null;
  logic_condition?: string | null;
  write_behavior?: string | null;
  execution_context_id?: string | null;
  platform_specific?: Record<string, unknown>;
}

/**
 * Subset of the backend ``TruthConclusion`` model surfaced in the
 * UI. ``platform_specific.trace_v2_kind`` is the most useful tag
 * for rendering: it distinguishes Trace v2's natural-language
 * conclusions (``writer_what`` / ``writer_conditions`` /
 * ``st_assignment`` / ``st_too_complex`` / ``branch_warning``)
 * from legacy v1 conclusions (no ``trace_v2_kind``).
 */
export interface TraceConclusion {
  id?: string;
  statement: string;
  subject_ids?: string[];
  recommended_checks?: string[];
  confidence?: ConfidenceLevel;
  platform_specific?: Record<string, unknown>;
}

export interface TraceResponse {
  target_object_id: string;
  summary?: string | null;
  confidence?: ConfidenceLevel;
  upstream_object_ids: string[];
  downstream_object_ids: string[];
  writer_relationships: TraceRelationship[];
  reader_relationships: TraceRelationship[];
  relationships: TraceRelationship[];
  conclusions: TraceConclusion[];
  recommended_checks: string[];
  failure_impact: string[];
  /**
   * Trace v2 sets ``platform_specific.trace_version`` to ``"v2"`` and
   * surfaces ``natural_conclusion_count``. Ask v1 surfaces the
   * router metadata (``question``, ``detected_target_object_id``,
   * ``detected_intent``, ``router_version``).
   */
  platform_specific?: Record<string, unknown>;
}

/**
 * Back-compatible alias. The two trace endpoints return the same
 * shape; the legacy name is kept so existing imports don't break.
 *
 * @deprecated Prefer ``TraceResponse``.
 */
export type TraceV1Response = TraceResponse;

// ---------------------------------------------------------------------------
// POST /api/ask-v1
// ---------------------------------------------------------------------------

/**
 * Router-level fields surfaced in the response's
 * ``platform_specific``. The ``answer_question`` service in the
 * backend always sets these even when no target is identified.
 */
export interface AskRouterMetadata {
  question?: string;
  detected_target_object_id?: string | null;
  detected_intent?: string;
  router_version?: string;
}

export type AskResponse = TraceResponse;

// ---------------------------------------------------------------------------
// Render-time helpers used by the UI layer. Kept here so the trace
// component(s) can pattern-match on them without re-deriving the
// string literals.
// ---------------------------------------------------------------------------

export type TraceV2Kind =
  | "writer_what"
  | "writer_conditions"
  | "st_assignment"
  | "st_too_complex"
  | "branch_warning"
  /** Prepended by ``/api/evaluate-runtime-v2`` — not design-time Trace v2. */
  | "runtime_v2_verdict"
  | "runtime_v2_path";

export function getTraceV2Kind(
  conclusion: TraceConclusion,
): TraceV2Kind | null {
  const k = conclusion.platform_specific?.["trace_v2_kind"];
  return typeof k === "string" ? (k as TraceV2Kind) : null;
}

// ---------------------------------------------------------------------------
// POST /api/evaluate-runtime-v2  (platform_specific on TraceResponse)
// ---------------------------------------------------------------------------

/** Overall operational verdict from ``runtime_evaluation_v2_service``. */
export type OverallVerdict =
  | "target_can_be_on"
  | "target_likely_off_or_reset"
  | "conflict_or_scan_order_dependent"
  | "blocked"
  | "incomplete";

/** Per-writer path status. */
export type PathStatus =
  | "path_satisfied"
  | "path_blocked"
  | "path_incomplete"
  | "path_unsupported";

/** One evaluated condition row in ``platform_specific.*_conditions``. */
export interface RuntimeConditionResult {
  status: string;
  natural_language?: string;
  instruction_type?: string | null;
  or_branch_index?: number;
  tag?: string | null;
  member?: string | null;
  snapshot_key?: string | null;
  required_value?: unknown;
  actual_value?: unknown;
  comparison_operator?: string | null;
  compared_with?: unknown;
  reason?: string;
  location?: string;
  writer_instruction_type?: string | null;
}

export interface RuntimeWriterPathResult {
  status: PathStatus | string;
  write_effect: string;
  location: string;
  instruction_type?: string | null;
  target_id?: string | null;
  source_id?: string | null;
  assigned_value?: string | null;
  conditions: RuntimeConditionResult[];
}

export interface RuntimeConflict {
  kind?: string;
  true_writer?: { location?: string; instruction_type?: string | null };
  false_writer?: { location?: string; instruction_type?: string | null };
}

/**
 * Typed view of ``TraceResponse.platform_specific`` after a successful
 * ``POST /api/evaluate-runtime-v2``. The backend still returns a loose
 * ``Record<string, unknown>`` on ``TraceResponse``; use
 * ``parseRuntimeV2PlatformSpecific`` for safe access.
 */
export interface RuntimeV2PlatformSpecific {
  trace_version: "runtime_v2";
  runtime_snapshot_evaluated: boolean;
  overall_verdict: OverallVerdict;
  writer_path_results: RuntimeWriterPathResult[];
  blocking_conditions: RuntimeConditionResult[];
  satisfied_conditions: RuntimeConditionResult[];
  missing_conditions: RuntimeConditionResult[];
  unsupported_conditions: RuntimeConditionResult[];
  conflicts: RuntimeConflict[];
}

function isOverallVerdict(v: unknown): v is OverallVerdict {
  return (
    v === "target_can_be_on" ||
    v === "target_likely_off_or_reset" ||
    v === "conflict_or_scan_order_dependent" ||
    v === "blocked" ||
    v === "incomplete"
  );
}

function asConditionArray(v: unknown): RuntimeConditionResult[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x) => x !== null && typeof x === "object") as RuntimeConditionResult[];
}

function asWriterPathArray(v: unknown): RuntimeWriterPathResult[] {
  if (!Array.isArray(v)) return [];
  return v
    .filter((x) => x !== null && typeof x === "object")
    .map((raw) => {
      const o = raw as Record<string, unknown>;
      return {
        status: typeof o.status === "string" ? o.status : "path_unsupported",
        write_effect: typeof o.write_effect === "string" ? o.write_effect : "other",
        location: typeof o.location === "string" ? o.location : "",
        instruction_type:
          typeof o.instruction_type === "string" ? o.instruction_type : null,
        target_id: typeof o.target_id === "string" ? o.target_id : null,
        source_id: typeof o.source_id === "string" ? o.source_id : null,
        assigned_value:
          typeof o.assigned_value === "string" ? o.assigned_value : null,
        conditions: asConditionArray(o.conditions),
      };
    });
}

function asConflictArray(v: unknown): RuntimeConflict[] {
  if (!Array.isArray(v)) return [];
  return v.filter((x) => x !== null && typeof x === "object") as RuntimeConflict[];
}

/**
 * Returns parsed runtime v2 metadata when ``trace_version`` is
 * ``runtime_v2``, otherwise ``null``.
 */
export function parseRuntimeV2PlatformSpecific(
  platformSpecific: Record<string, unknown> | undefined,
): RuntimeV2PlatformSpecific | null {
  if (!platformSpecific) return null;
  if (platformSpecific["trace_version"] !== "runtime_v2") return null;
  const ov = platformSpecific["overall_verdict"];
  if (!isOverallVerdict(ov)) return null;
  return {
    trace_version: "runtime_v2",
    runtime_snapshot_evaluated: Boolean(
      platformSpecific["runtime_snapshot_evaluated"],
    ),
    overall_verdict: ov,
    writer_path_results: asWriterPathArray(
      platformSpecific["writer_path_results"],
    ),
    blocking_conditions: asConditionArray(
      platformSpecific["blocking_conditions"],
    ),
    satisfied_conditions: asConditionArray(
      platformSpecific["satisfied_conditions"],
    ),
    missing_conditions: asConditionArray(
      platformSpecific["missing_conditions"],
    ),
    unsupported_conditions: asConditionArray(
      platformSpecific["unsupported_conditions"],
    ),
    conflicts: asConflictArray(platformSpecific["conflicts"]),
  };
}

/** True when the trace payload carries a runtime v2 evaluation overlay. */
export function isRuntimeV2Trace(trace: TraceResponse): boolean {
  return parseRuntimeV2PlatformSpecific(trace.platform_specific) !== null;
}

/** First conclusion with ``trace_v2_kind === "runtime_v2_verdict"``. */
export function getRuntimeV2VerdictConclusion(
  trace: TraceResponse,
): TraceConclusion | null {
  return (
    trace.conclusions.find(
      (c) => c.platform_specific?.["trace_v2_kind"] === "runtime_v2_verdict",
    ) ?? null
  );
}

const RUNTIME_V2_CONCLUSION_KINDS = new Set<string>([
  "runtime_v2_verdict",
  "runtime_v2_path",
]);

/** Design-time Trace v2 / v1 conclusions only (excludes runtime overlay rows). */
export function isDesignTraceConclusion(conclusion: TraceConclusion): boolean {
  const k = conclusion.platform_specific?.["trace_v2_kind"];
  if (typeof k === "string" && RUNTIME_V2_CONCLUSION_KINDS.has(k)) {
    return false;
  }
  return true;
}

export function getStringMeta(
  source: Record<string, unknown> | undefined,
  key: string,
): string | null {
  if (!source) return null;
  const v = source[key];
  return typeof v === "string" ? v : null;
}
