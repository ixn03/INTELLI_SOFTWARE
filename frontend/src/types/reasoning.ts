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
 *   - POST /api/trace-v1            -> TraceResponse
 *   - POST /api/trace-v2            -> TraceResponse (NL-augmented)
 *   - POST /api/ask-v1              -> AskResponse  (router metadata)
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
  control_object_count: number;
  relationship_count: number;
  execution_context_count: number;
  control_objects: NormalizedControlObjectSummary[];
  relationships: NormalizedRelationshipSummary[];
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
  | "branch_warning";

export function getTraceV2Kind(
  conclusion: TraceConclusion,
): TraceV2Kind | null {
  const k = conclusion.platform_specific?.["trace_v2_kind"];
  return typeof k === "string" ? (k as TraceV2Kind) : null;
}

export function getStringMeta(
  source: Record<string, unknown> | undefined,
  key: string,
): string | null {
  if (!source) return null;
  const v = source[key];
  return typeof v === "string" ? v : null;
}
